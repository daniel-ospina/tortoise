#!/usr/bin/env python3
"""
bridge.py — Minutes markdown → Twenty CRM + Tortoise

Parses Minutes meeting markdown files from ~/meetings/,
matches speakers to Twenty CRM contacts, creates meeting notes,
and pushes meeting data to Tortoise/FalkorDB.

Idempotent: content_hash prevents duplicate notes on re-run.
Review queue: unresolved speakers are queued; on resolution, bridge applies matches.

Usage:
    python3 bridge.py ~/meetings/2026-07-29-client-call.md
    python3 bridge.py --watch ~/meetings/       # Watch directory mode
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests
import yaml

CAL_STATE_FILE = os.path.expanduser("~/.minutes/cal-trigger-state.json")


# --- Configuration ---

CONFIG = {
    "twenty_base_url": os.getenv("TWENTY_BASE_URL", "http://localhost:3001"),
    "twenty_api_key": os.getenv("TWENTY_API_KEY", ""),
    "review_queue_path": os.path.join(str(Path.home()), ".minutes", "review_queue.json"),
    "retry_max": 3,
    "retry_backoff_base": 5,
}


# --- Startup Validation ---

def validate_config():
    """Fail fast if critical config is missing."""
    if not CONFIG["twenty_api_key"]:
        print("FATAL: TWENTY_API_KEY not set. Export it or add to .env.")
        print("  Get your API key from Twenty UI: Settings → Developers → API Keys")
        sys.exit(1)


# --- Twenty CRM API ---

def _api(method: str, path: str, params: dict | None = None, json_data: dict | None = None) -> dict:
    """Call Twenty REST API with auth and retry."""
    url = f"{CONFIG['twenty_base_url']}/rest{path}"
    headers = {
        "Authorization": f"Bearer {CONFIG['twenty_api_key']}",
        "Content-Type": "application/json",
    }

    for attempt in range(CONFIG["retry_max"]):
        try:
            resp = requests.request(
                method, url, params=params, json=json_data,
                headers=headers, timeout=10
            )
            if resp.status_code in (200, 201):
                try:
                    return resp.json()
                except (json.JSONDecodeError, ValueError):
                    return {"error": "json_decode", "detail": resp.text[:200]}
            if resp.status_code == 401:
                print("FATAL: TWENTY_API_KEY is invalid. Check your .env file.", file=sys.stderr)
                sys.exit(1)
            if resp.status_code == 404:
                return {"error": "not_found"}
            if resp.status_code == 409:
                return {"error": "conflict", "detail": "Duplicate — already exists"}
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(CONFIG["retry_backoff_base"] * (2 ** attempt))
                continue
            return {"error": f"http_{resp.status_code}", "detail": resp.text}
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt == CONFIG["retry_max"] - 1:
                return {"error": "unreachable", "detail": str(e), "retry_after": 60}
            time.sleep(CONFIG["retry_backoff_base"] * (2 ** attempt))

    return {"error": "max_retries"}


def find_person_by_email(email: str) -> Optional[dict]:
    """Find a Twenty contact by email. Returns contact dict or None."""
    result = _api("GET", "/people", params={"filter": f"email[eq]={email}"})
    contacts = result.get("data", {}).get("contacts", {}).get("edges", [])
    if contacts:
        return contacts[0]["node"]
    return None


def create_person(name: str, email: str = "") -> dict:
    """Create a new person in Twenty."""
    return _api("POST", "/people", json_data={"name": name, "email": email})


def create_opportunity(person_id: str, name: str) -> dict:
    """Create an opportunity linked to a person."""
    from datetime import datetime
    return _api("POST", "/opportunities", json_data={
        "name": name,
        "pointOfContactId": person_id,
        "closeDate": datetime.now().strftime("%Y-%m-%d"),
    })


def find_company_by_domain(domain: str) -> Optional[dict]:
    """Find a Twenty company by domain."""
    result = _api("GET", "/companies", params={"filter": f"domain[eq]={domain}"})
    companies = result.get("data", {}).get("companies", {}).get("edges", [])
    if companies:
        return companies[0]["node"]
    return None


def create_company(name: str, domain: str = "") -> dict:
    """Create a new company in Twenty."""
    return _api("POST", "/companies", json_data={"name": name, "domain": domain})


def find_note_by_external_id(external_id: str) -> Optional[dict]:
    """Check if a note with this external_id already exists (idempotency)."""
    result = _api("GET", "/notes", params={"filter": f"externalId[eq]={external_id}"})
    notes = result.get("data", {}).get("notes", {}).get("edges", [])
    if notes:
        return notes[0]["node"]
    return None


def create_note(person_id: str, title: str, body: str, external_id: str) -> dict:
    """Create a meeting note in Twenty."""
    return _api("POST", "/notes", json_data={
        "personId": person_id,
        "title": title,
        "body": body,
        "externalId": external_id,
    })


# --- Review Queue ---

def _lock_queue(queue_path: Path):
    """Acquire exclusive lock on review queue file."""
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.touch(exist_ok=True)
    fd = open(queue_path, "r+")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _unlock_queue(fd):
    """Release lock on review queue file."""
    fcntl.flock(fd, fcntl.LOCK_UN)
    fd.close()


def load_resolved_speakers() -> list:
    """Load and clear resolved speakers from review queue."""
    queue_path = Path(CONFIG["review_queue_path"])
    if not queue_path.exists():
        return []

    fd = _lock_queue(queue_path)
    try:
        fd.seek(0)
        content = fd.read()
        if not content.strip():
            return []
        try:
            queue = json.loads(content)
        except json.JSONDecodeError:
            return []
        resolved = [item for item in queue if item.get("resolved_at")]
        remaining = [item for item in queue if not item.get("resolved_at")]
        fd.seek(0)
        fd.truncate()
        json.dump(remaining, fd, indent=2)
        return resolved
    finally:
        _unlock_queue(fd)


def queue_unmatched_speaker(meeting_id: str, speaker: dict) -> None:
    """Write unmatched speaker to review queue with file locking."""
    queue_path = Path(CONFIG["review_queue_path"])
    fd = _lock_queue(queue_path)
    try:
        fd.seek(0)
        content = fd.read()
        queue = json.loads(content) if content.strip() else []
        queue.append({
            "meeting_id": meeting_id,
            "speaker_name": speaker.get("name", "Unknown"),
            "segments": speaker.get("segments", 0),
            "queued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        fd.seek(0)
        fd.truncate()
        json.dump(queue, fd, indent=2)
    finally:
        _unlock_queue(fd)


# --- Tortoise Integration ---

def tortoise_available() -> bool:
    """Check if Tortoise MCP tools are available."""
    try:
        from tortoise.sdk import TortoiseSDK
        return TortoiseSDK() is not None
    except (ImportError, ValueError):
        return False


def push_to_tortoise(frontmatter: dict) -> dict:
    """Push meeting data to Tortoise/FalkorDB epistemic graph."""
    if not tortoise_available():
        return {"status": "skipped", "reason": "tortoise_unavailable"}

    try:
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()

        point_ids = []

        meeting_point = sdk.create_point(
            kind="event",
            content=f"Meeting: {frontmatter['title']} ({frontmatter['date']})",
            context="meeting-intelligence",
            authoredBy="minutes-bridge",
            dedup=True,
        )
        point_ids.append({"type": "meeting", "id": meeting_point.get("id")})

        for idx, decision in enumerate(frontmatter.get("decisions", [])):
            if not decision.get("text"):
                continue
            dp = sdk.create_point(
                kind="decision",
                content=decision["text"],
                context="meeting-intelligence",
                authoredBy="minutes-bridge",
                dedup=True,
            )
            point_ids.append({"type": "decision", "index": idx, "id": dp.get("id")})

        for idx, commitment in enumerate(frontmatter.get("commitments", [])):
            if not commitment.get("text"):
                continue
            cp = sdk.create_point(
                kind="commitment",
                content=commitment["text"],
                context="meeting-intelligence",
                authoredBy="minutes-bridge",
                dedup=True,
            )
            point_ids.append({"type": "commitment", "index": idx, "id": cp.get("id")})

        return {"status": "ok", "points": point_ids}

    except Exception:
        import traceback
        traceback.print_exc()
        return {"status": "error", "reason": "tortoise_push_failed"}


def get_calendar_attendees(meeting_date: str = None) -> list:
    """Get attendees from the most recent cal-trigger state file."""
    if not os.path.exists(CAL_STATE_FILE):
        return []
    try:
        with open(CAL_STATE_FILE) as f:
            state = json.load(f)
        return state.get("attendees", [])
    except (json.JSONDecodeError, IOError):
        return []


# --- Core Pipeline ---

def process_meeting(md_path: str) -> dict:
    """Process a Minutes meeting markdown file end-to-end."""
    path = Path(md_path)
    if not path.exists():
        return {"status": "error", "reason": "file_not_found"}

    text = path.read_text()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {"status": "error", "reason": "invalid_yaml", "detail": "No YAML frontmatter"}

    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        return {"status": "error", "reason": "invalid_yaml", "detail": str(e)}

    if not isinstance(frontmatter, dict):
        return {"status": "error", "reason": "invalid_yaml", "detail": "Frontmatter is not a dict"}

    meeting_id = frontmatter.get("id", path.stem)
    content_hash = frontmatter.get("content_hash", hashlib.sha256(text.encode()).hexdigest()[:16])

    # Apply resolved speakers from review queue
    resolved = load_resolved_speakers()
    resolved_map = {}
    for item in resolved:
        key = f"{item.get('meeting_id', '')}:{item.get('speaker_name', '')}"
        resolved_map[key] = item

    # Idempotency check
    existing = find_note_by_external_id(content_hash)
    if existing and "error" not in existing:
        return {"status": "skipped", "reason": "already_processed", "note_id": existing.get("id")}

    # Auto-create contacts from calendar attendees AND create opportunities
    cal_attendees = get_calendar_attendees()
    for att in cal_attendees:
        email = att.get("email", "")
        name = att.get("name", "")
        if email:
            existing_contact = find_person_by_email(email)
            if not existing_contact or "error" in existing_contact:
                create_person(name, email)
                # Auto-create opportunity at "Contacted" stage for new contacts
                new_person = find_person_by_email(email)
                if new_person and "error" not in new_person:
                    create_opportunity(new_person["id"], f"{name} — First call {frontmatter.get('date','')[:10]}")
                log(f"Auto-created contact: {name} <{email}>") if 'log' in dir() else None

    # Match speakers to contacts
    person_ids = []
    for speaker in frontmatter.get("speakers", []):
        name = speaker.get("name", "")
        email = speaker.get("email", "")

        # Check if this speaker was resolved in review queue
        resolved_key = f"{meeting_id}:{name}"
        if resolved_key in resolved_map:
            resolved_item = resolved_map[resolved_key]
            if resolved_item.get("email"):
                email = resolved_item["email"]
            if resolved_item.get("resolved_name"):
                name = resolved_item["resolved_name"]

        contact = None
        if email:
            contact = find_person_by_email(email)
        elif name:
            contact = None  # only match by email, not bare name

        if not contact or "error" in contact:
            queue_unmatched_speaker(meeting_id, speaker)
            continue

        if "error" not in contact and contact.get("id"):
            person_ids.append(contact["id"])

    # Create note (attached to first matched contact)
    if not person_ids:
        return {
            "status": "partial",
            "reason": "no_contacts_matched",
            "queued_speakers": len(frontmatter.get("speakers", [])),
        }

    title = frontmatter.get("title", path.stem)
    
    # Extract transcript from markdown (parts[2] is text after YAML frontmatter)
    transcript_text = parts[2].strip() if len(parts) > 2 else ""
    # Include transcript summary (first 50 lines) in CRM note
    transcript_lines = transcript_text.split("\n")[:50]
    transcript_excerpt = "\n".join(transcript_lines)
    if len(transcript_text.split("\n")) > 50:
        transcript_excerpt += f"\n\n... ({len(transcript_text.split(chr(10)))} lines total — full transcript in ~/meetings/)"
    
    body_parts = [
        f"📅 {frontmatter.get('date', 'unknown')} | ⏱ {frontmatter.get('duration_sec', 0)}s",
        "",
        "## Transcript",
        transcript_excerpt,
        "",
        "## Decisions",
        *[f"- {d.get('text', '')}" for d in frontmatter.get("decisions", []) if d.get("text")],
        "",
        "## Commitments",
        *[f"- {c.get('text', '')} ({c.get('person', '?')}, due: {c.get('deadline', '?')})"
          for c in frontmatter.get("commitments", []) if c.get("text")],
    ]
    body = "\n".join(body_parts)

    note = create_note(person_ids[0], title, body, content_hash)

    # Handle 409 conflict (concurrent processing race)
    if "error" in note and note.get("error") == "conflict":
        existing = find_note_by_external_id(content_hash)
        if existing and "error" not in existing:
            return {"status": "skipped", "reason": "already_processed", "note_id": existing.get("id")}

    # Only push to Tortoise if note creation succeeded
    tortoise_result = {"status": "skipped", "reason": "note_creation_failed"}
    if "error" not in note:
        tortoise_result = push_to_tortoise(frontmatter)

    return {
        "status": "ok" if "error" not in note else "partial",
        "person_ids": person_ids,
        "note_id": note.get("id") if "error" not in note else None,
        "note_error": note.get("error") if "error" in note else None,
        "tortoise": tortoise_result,
    }


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(description="Minutes → Twenty CRM + Tortoise bridge")
    parser.add_argument("meeting", nargs="?", help="Path to meeting markdown file")
    parser.add_argument("--watch", help="Watch directory for new meetings")
    args = parser.parse_args()

    validate_config()

    if args.watch:
        print(f"👀 Watching {args.watch} for new meetings...")
        watch_dir = Path(args.watch)
        processed = set()
        while True:
            for md_file in watch_dir.glob("*.md"):
                if md_file.name not in processed:
                    result = process_meeting(str(md_file))
                    print(f"  {md_file.name}: {result['status']}")
                    processed.add(md_file.name)
            time.sleep(5)
    elif args.meeting:
        result = process_meeting(args.meeting)
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
