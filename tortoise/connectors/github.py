"""P1-7 #6976 / GAP-11 #6998: GitHub connector — poll + webhook.

Zero Python deps. Uses `gh` CLI for polling + stdlib http.server for webhook.
Maps issues/PRs → 4-entity chain per ONTOLOGY_v2.5 §1.1:
  Source (github_issue) → Object (pm:issue) → Event (pm:cardCreated)
  + Subject (naturalPerson for author/assignees)
PM domain extension kinds: pm:issue, pm:card, pm:cardCreated, pm:cardCompleted.
"""
from __future__ import annotations

import json
import hmac
import hashlib
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class GitHubConnector:
    """Poll GitHub issues/PRs via `gh` CLI + optional webhook server."""

    def __init__(self, config: dict[str, Any] | None = None, api=None):
        cfg = config or {}
        self.repo = cfg.get("repo", "")
        self.state = cfg.get("state", "closed")
        self.limit = int(cfg.get("limit", 100))
        self.webhook_port = int(cfg.get("webhook_port", 0))
        self.webhook_secret = cfg.get("webhook_secret", "")
        self.api = api
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── Polling (existing) ─────────────────────────────────────────

    def poll(self) -> list[dict]:
        """Fetch issues + PRs via `gh` CLI → EventRecorded dicts."""
        if not self.repo:
            return []
        events: list[dict] = []
        events.extend(self._poll_issues())
        events.extend(self._poll_prs())
        return events

    def poll_raw_issues(self) -> list[dict]:
        """Fetch raw issue dicts from GitHub API (for entity extraction)."""
        if not self.repo:
            return []
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo", self.repo,
                "--state", self.state,
                "--limit", str(self.limit),
                "--json", "number,title,state,createdAt,closedAt,url,"
                          "labels,assignees,author,milestone",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        return json.loads(result.stdout)

    def _poll_issues(self) -> list[dict]:
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo", self.repo,
                "--state", self.state,
                "--limit", str(self.limit),
                "--json", "number,title,state,createdAt,closedAt,url",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        return [ev for issue in json.loads(result.stdout)
                if (ev := self._issue_to_event(issue))]

    def _poll_prs(self) -> list[dict]:
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--repo", self.repo,
                "--state", self.state,
                "--limit", str(self.limit),
                "--json", "number,title,state,createdAt,closedAt,mergedAt,url",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        return [ev for pr in json.loads(result.stdout)
                if (ev := self._pr_to_event(pr))]

    # ── Entity extraction (ONTOLOGY_v2.5 §1.1, PM domain extension) ────

    def _issue_to_entities(self, issue: dict) -> dict | None:
        """Map GitHub issue → 4-entity chain.

        Returns dict with keys: source, object, event, subjects, about_edges.
        Per ONTOLOGY_v2.5 §1.1: Source (github_issue), Object (pm:issue),
        Event (pm:cardCreated/pm:cardCompleted).
        PM domain extension: packs/project-management/manifest.yaml.
        """
        number = issue.get("number")
        title = issue.get("title", "")
        if not title or not number:
            return None

        state = issue.get("state", "")
        created_at = issue.get("createdAt", "")
        closed_at = issue.get("closedAt", "")
        url = issue.get("url", "")
        labels = [l.get("name", "") for l in issue.get("labels", [])]
        assignees = [a.get("login", "") for a in issue.get("assignees", [])]
        author = issue.get("author", {}).get("login", "")

        source_id = f"github-issue-{self.repo}-{number}"

        # Source — provenance anchor (ONTOLOGY_v2.5 §3)
        source = {
            "type": "SourceCreated",
            "sourceId": source_id,
            "sourceKind": "github_issue",
            "externalId": str(number),
            "url": url,
            "label": f"{self.repo}#{number}: {title}",
        }

        # Object — persisted entity (PM domain extension)
        obj = {
            "type": "ObjectCreated",
            "objectId": source_id,
            "objectKind": "pm:issue",
            "name": f"{self.repo}#{number}",
            "title": title,
            "authoredBy": author,
            "url": url,
        }

        # Event — temporal occurrence (PM domain extension)
        event_kind = "pm:cardCompleted" if state == "closed" else "pm:cardCreated"
        event = {
            "type": "EventRecorded",
            "eventId": f"{source_id}-created",
            "eventKind": event_kind,
            "subject": f"github-user:{author}" if author else f"repo:{self.repo}",
            "object": source_id,
            "startedAt": created_at,
            "endedAt": closed_at if state == "closed" else None,
        }

        # Subjects — people involved (ONTOLOGY_v2.5 §1.1)
        subjects = []
        if author:
            subjects.append({
                "subjectId": f"github-user:{author}",
                "subjectKind": "naturalPerson",
                "name": author,
            })
        for a in assignees:
            if a != author:
                subjects.append({
                    "subjectId": f"github-user:{a}",
                    "subjectKind": "naturalPerson",
                    "name": a,
                })

        # aboutSubject edges — who this issue is about (ONTOLOGY_v2.5 §2.2)
        about_subjects = [s["subjectId"] for s in subjects]
        about_objects = [source_id]

        return {
            "source": source,
            "object": obj,
            "event": event,
            "subjects": subjects,
            "aboutSubjects": about_subjects,
            "aboutObjects": about_objects,
        }

    def ingest(self, proj) -> int:
        """Poll + apply to projection. Returns count of applied events.
        Issues get full 4-entity chain (Source → Object → Event → Subjects).
        PRs get event-only (existing behavior).
        """
        events = self.poll()
        count = 0
        for ev in events:
            proj.apply(ev)
            count += 1

        # Entity extraction for issues (ONTOLOGY_v2.5 §1.1, PM domain extension)
        raw_issues = self.poll_raw_issues()
        for issue in raw_issues:
            entities = self._issue_to_entities(issue)
            if entities:
                if entities.get("source"):
                    proj.apply(entities["source"])
                if entities.get("object"):
                    proj.apply(entities["object"])
                for subj in entities.get("subjects", []):
                    proj.apply(subj)
                # aboutSubject edges — who this issue relates to (ONTOLOGY_v2.5 §2.2)
                obj_id = entities.get("object", {}).get("objectId", "")
                for sid in entities.get("aboutSubjects", []):
                    proj.g.query(
                        "MERGE (s:Subject {id: $sid}) "
                        "MERGE (o:Object {id: $oid}) "
                        "MERGE (s)-[:aboutSubject]->(o)",
                        params={"sid": sid, "oid": obj_id},
                    )
                count += 1  # count the entity chain as one applied unit
        return count

    # ── Webhook (GAP-11) ───────────────────────────────────────────

    def start_webhook(self, on_event: Callable[[dict], None] | None = None) -> int:
        """Start webhook server on configured port. Returns port number.
        Runs in a daemon thread; call stop_webhook() to shut down.
        """
        if not self.webhook_port:
            return 0

        secret = self.webhook_secret.encode() if self.webhook_secret else None
        connector = self  # capture for handler closure

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                sig = self.headers.get("X-Hub-Signature-256", "")

                # Verify signature if secret configured
                if secret and not _verify_sig(secret, sig, body):
                    self.send_response(403)
                    self.end_headers()
                    return

                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return

                event_type = self.headers.get("X-GitHub-Event", "")
                ev = connector._webhook_to_event(event_type, payload)
                if ev:
                    if on_event:
                        on_event(ev)
                    if connector.api:
                        connector.api.get_proj().apply(ev)
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                pass  # ponytail: suppress HTTP log noise

        self._server = HTTPServer(("0.0.0.0", self.webhook_port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.webhook_port

    def stop_webhook(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
            self._thread = None

    # ── Event mapping ──────────────────────────────────────────────

    def _issue_to_event(self, issue: dict) -> dict | None:
        number = issue.get("number")
        title = issue.get("title", "")
        if not title or not number:
            return None

        state = issue.get("state", "")
        created_at = issue.get("createdAt", "")
        closed_at = issue.get("closedAt", "")

        return {
            "type": "EventRecorded",
            "eventId": f"github-issue-{self.repo}-{number}",
            "eventKind": f"github.issue.{state}",
            "subject": f"issue:{self.repo}#{number}",
            "object": title,
            "startedAt": created_at,
            "endedAt": closed_at if state == "closed" else None,
            "source": f"github:{self.repo}",
            "sourceKind": "github_issue", "sourceKind": "github_issue",
            "participants": [],
        }

    def _pr_to_event(self, pr: dict) -> dict | None:
        number = pr.get("number")
        title = pr.get("title", "")
        if not title or not number:
            return None

        state = pr.get("state", "")
        merged_at = pr.get("mergedAt")
        created_at = pr.get("createdAt", "")
        closed_at = pr.get("closedAt", "")

        kind = "github.pr.merged" if merged_at else f"github.pr.{state}"

        return {
            "type": "EventRecorded",
            "eventId": f"github-pr-{self.repo}-{number}",
            "eventKind": kind,
            "subject": f"pr:{self.repo}#{number}",
            "object": title,
            "startedAt": created_at,
            "endedAt": merged_at or (closed_at if state == "closed" else None),
            "source": f"github:{self.repo}",
            "sourceKind": "github_issue", "sourceKind": "github_issue",
            "participants": [],
        }

    def _webhook_to_event(self, event_type: str, payload: dict) -> dict | None:
        """Map GitHub webhook event → EventRecorded."""
        if event_type == "issues":
            issue = payload.get("issue", {})
            action = payload.get("action", "")
            return self._issue_to_event({
                "number": issue.get("number"),
                "title": issue.get("title", ""),
                "state": "closed" if action == "closed" else "open",
                "createdAt": issue.get("created_at", ""),
                "closedAt": issue.get("closed_at"),
                "url": issue.get("html_url", ""),
            })
        if event_type == "pull_request":
            pr = payload.get("pull_request", {})
            action = payload.get("action", "")
            return self._pr_to_event({
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "state": "closed" if action == "closed" else "open",
                "createdAt": pr.get("created_at", ""),
                "closedAt": pr.get("closed_at"),
                "mergedAt": pr.get("merged_at"),
                "url": pr.get("html_url", ""),
            })
        return None


def _verify_sig(secret: bytes, header: str, body: bytes) -> bool:
    """Constant-time HMAC-SHA256 verification of webhook signature."""
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", header)
