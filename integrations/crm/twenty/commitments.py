#!/usr/bin/env python3
"""
commitments.py — Track and update meeting commitments across all meetings.

Usage:
    python3 commitments.py                    # List all open commitments
    python3 commitments.py --person "Alex"    # Filter by person
    python3 commitments.py --done <id>        # Mark commitment as done
    python3 commitments.py --cancel <id>      # Mark commitment as cancelled
"""
import os  # noqa: I001
import re
import sys
from pathlib import Path
from datetime import datetime

import yaml

MEETINGS_DIR = os.path.expanduser("~/meetings")


def load_all_commitments() -> list:
    """Parse all meeting markdown files and extract commitments."""
    all_commits = []
    meetings_path = Path(MEETINGS_DIR).resolve()
    if not meetings_path.exists():
        return []

    for md_file in sorted(meetings_path.rglob("*.md"), reverse=True):
        # Security: resolve symlinks and verify they stay within meetings_dir
        resolved = md_file.resolve()
        if not str(resolved).startswith(str(meetings_path)):
            continue
        if md_file.is_symlink():
            continue

        text = md_file.read_text()
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue

        try:
            fm = yaml.safe_load(parts[1])
        except yaml.YAMLError:
            continue

        if not isinstance(fm, dict):
            continue

        for idx, c in enumerate(fm.get("commitments", [])):
            if not c.get("text"):
                continue
            all_commits.append({
                "id": f"{fm.get('id', md_file.stem)}-commit-{idx}",
                "text": c.get("text", ""),
                "person": c.get("person", ""),
                "deadline": c.get("deadline", ""),
                "status": c.get("status", "open"),
                "meeting_id": fm.get("id", md_file.stem),
                "meeting_date": fm.get("date", ""),
                "source_file": str(md_file),
            })

    return all_commits


def list_commitments(person: str = None):  # noqa: RUF013
    commits = load_all_commitments()

    open_commits = [c for c in commits if c["status"] == "open"]
    if person:
        open_commits = [c for c in open_commits if person.lower() in c["person"].lower()]

    if not open_commits:
        print("✅ No open commitments." + (f" (filtered by: {person})" if person else ""))
        return

    print(f"📋 {len(open_commits)} open commitment(s):\n")

    overdue = []
    upcoming = []
    for c in open_commits:
        if c["deadline"] and datetime.strptime(c["deadline"][:10], "%Y-%m-%d").date() < datetime.now().date():
            overdue.append(c)
        else:
            upcoming.append(c)

    if overdue:
        print("🔴 Overdue:")
        for c in overdue:
            print(f"  [{c['id']}] {c['text']}")
            print(f"       Person: {c['person']} | Due: {c['deadline']} | Meeting: {c['meeting_date']}")
            print()

    if upcoming:
        print("🟡 Upcoming:")
        for c in upcoming:
            print(f"  [{c['id']}] {c['text']}")
            print(f"       Person: {c['person']} | Due: {c['deadline'] or 'no deadline'} | Meeting: {c['meeting_date']}")
            print()


def update_commitment(commit_id: str, new_status: str):
    """Update commitment status in source markdown file using regex to preserve YAML formatting."""
    commits = load_all_commitments()
    target = None
    for c in commits:
        if c["id"] == commit_id:
            target = c
            break

    if not target:
        print(f"❌ Commitment not found: {commit_id}")
        return

    source_path = Path(target["source_file"]).resolve()
    if not source_path.exists():
        print(f"❌ Source file not found: {source_path}")
        return

    text = source_path.read_text()

    # Use regex to replace only the status field, preserving YAML comments/formatting
    pattern = re.compile(
        rf"(\s*-\s+text:\s*{re.escape(target['text'])}[^\n]*\n(?:\s+\w+:\s+[^\n]*\n)*\s*status:\s*)\S+",
        re.MULTILINE
    )
    new_text = pattern.sub(rf"\g<1>{new_status}", text)

    if new_text == text:
        print(f"❌ Could not find commitment in file to update")  # noqa: F541
        return

    source_path.write_text(new_text)

    status_emoji = "✅" if new_status == "done" else "❌"
    print(f"{status_emoji} Commitment marked as {new_status}: {target['text'][:60]}")
    print(f"   File updated: {source_path.name}")
    print(f"   Bridge will sync to Twenty + Tortoise on next run.")  # noqa: F541


if __name__ == "__main__":
    if "--done" in sys.argv:
        idx = sys.argv.index("--done")
        if idx + 1 >= len(sys.argv):
            print("❌ Missing commitment ID after --done")
            sys.exit(1)
        update_commitment(sys.argv[idx + 1], "done")
    elif "--cancel" in sys.argv:
        idx = sys.argv.index("--cancel")
        if idx + 1 >= len(sys.argv):
            print("❌ Missing commitment ID after --cancel")
            sys.exit(1)
        update_commitment(sys.argv[idx + 1], "cancelled")
    elif "--person" in sys.argv:
        idx = sys.argv.index("--person")
        list_commitments(person=sys.argv[idx + 1])
    else:
        list_commitments()
