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


def _extract_label_prefix(labels: list[str], prefix: str) -> str | None:
    """Extract value after a label prefix. E.g., _extract_label_prefix(['complexity:micro'], 'complexity:') -> 'micro'."""
    for label in labels:
        if label.startswith(prefix):
            return label[len(prefix):]
    return None


class GitHubConnector:
    """Poll GitHub issues/PRs via `gh` CLI + optional webhook server."""

    def __init__(self, config: dict[str, Any] | None = None, api=None):
        cfg = config or {}
        self.repo = cfg.get("repo", "")
        self.state = cfg.get("state", "open")
        self.limit = int(cfg.get("limit", 100))
        self.webhook_port = int(cfg.get("webhook_port", 0))
        self.webhook_secret = cfg.get("webhook_secret", "")
        self.api = api
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._routing = self._load_routing()

    def _load_routing(self) -> dict:
        """Load entity routing config (config/routing.yaml)."""
        import yaml
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent.parent / "config" / "routing.yaml"
        if path.exists():
            return yaml.safe_load(path.read_text()) or {}
        return {}

    def _route_issue(self, labels: list[str]) -> dict:
        """Determine team + role for an issue based on routing config."""
        routing = self._routing
        label_map = routing.get("label_routing", {})
        repo_routing = routing.get("repo_routing", {}).get(self.repo, {})
        default_team = repo_routing.get("default_team", "")
        product = repo_routing.get("product", "")
        fallback = routing.get("attribution_fallback", "default_team")
        team = ""
        role = "product-implementer"
        for label in labels:
            if label in label_map:
                cfg = label_map[label]
                if cfg.get("team"): team = cfg["team"]
                if cfg.get("role"): role = cfg["role"]
        if not team:
            team = default_team
        if not team and fallback != "skip":
            team = "default" if fallback == "default_team" else ""
        return {"team": team, "role": role, "product": product}

    # ── Polling ─────────────────────────────────────────────────

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
        """Map GitHub issue → entity chain.

        Returns dict with: object (ObjectRegistered), event (EventRecorded),
        subjects (SubjectAdded list), about_edges.
        Per ONTOLOGY_v2.5 §1.1: Object (pm:issue), Subject (naturalPerson).
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
        labels = [l.get("name", "") for l in (issue.get("labels") or [])]
        complexity = _extract_label_prefix(labels, "complexity:") or "standard"
        ux_rating = _extract_label_prefix(labels, "ux:") or ""
        assignees = [a.get("login", "") for a in (issue.get("assignees") or [])]
        author = (issue.get("author") or {}).get("login", "")  # P0: handle null author
        milestone = (issue.get("milestone") or {}).get("title", "")

        entity_id = f"github-issue-{self.repo}-{number}"

        # Route to team + role based on labels + repo config
        route = self._route_issue(labels)

        # Object — persisted entity (PM domain extension, ONTOLOGY_v2.5 §1.1)
        obj = {
            "type": "ObjectRegistered",
            "id": entity_id,
            "name": f"{self.repo}#{number}",
            "object_kind": "pm:issue",
            "title": title,
            "url": url,
            "createdAt": created_at,
            "routed_team": route["team"],
            "routed_role": route["role"],
            "routed_product": route["product"],
            "complexity": complexity,
            "ux_rating": ux_rating,
        }

        # Event — temporal occurrence (PM domain extension)
        event_kind = "pm:cardCompleted" if state == "closed" else "pm:cardCreated"
        event = {
            "type": "EventRecorded",
            "eventId": f"{entity_id}-created",
            "eventKind": event_kind,
            "subject": f"github-user:{author}" if author else f"repo:{self.repo}",
            "object": entity_id,
            "startedAt": created_at,
            "endedAt": closed_at if state == "closed" else None,
        }

        # Subjects — people involved (ONTOLOGY_v2.5 §1.1 subjectKind)
        subjects = []
        seen = set()
        for login in [author] + assignees:
            if login and login not in seen:
                seen.add(login)
                subjects.append({
                    "type": "SubjectAdded",
                    "id": f"github-user:{login}",
                    "name": login,
                    "subject_kind": "naturalPerson",
                })

        # aboutSubject edges — Object → Subject (ONTOLOGY_v2.5 §2.2)
        about_subjects = [s["id"] for s in subjects]

        return {
            "object": obj,
            "event": event,
            "subjects": subjects,
            "aboutSubjects": about_subjects,
        }

    def ingest(self, proj) -> int:
        """Poll + apply to projection. Returns count of applied events.
        Issues get entity chain: Object (pm:issue) + Event + Subjects + aboutSubject edges.
        PRs get event-only (existing behavior).
        """
        events = self.poll()
        count = 0
        for ev in events:
            proj.apply(ev)
            count += 1

        # Entity extraction for issues (ONTOLOGY_v2.5 §1.1, PM domain extension)
        try:
            raw_issues = self.poll_raw_issues()
        except Exception:
            raw_issues = []  # ponytail: gh CLI may fail, skip entity extraction

        for issue in raw_issues:
            entities = self._issue_to_entities(issue)
            if not entities:
                continue

            # Apply Object (ObjectRegistered → FalkorDB)
            if entities.get("object"):
                proj.apply(entities["object"])
                # Set routing properties (not handled by projection's _upsert_object)
                obj = entities["object"]
                proj.g.query(
                    "MATCH (o:Object {id: $id}) "
                    "SET o.routed_team = $team, o.routed_role = $role, o.routed_product = $product, "
                    "o.complexity = $complexity, o.ux_rating = $ux_rating",
                    params={
                        "id": obj.get("id"),
                        "team": obj.get("routed_team", ""),
                        "role": obj.get("routed_role", ""),
                        "product": obj.get("routed_product", ""),
                        "complexity": obj.get("complexity", "standard"),
                        "ux_rating": obj.get("ux_rating", ""),
                    },
                )

            # Apply Event (EventRecorded → FalkorDB)
            if entities.get("event"):
                proj.apply(entities["event"])

            # Apply Subjects (SubjectAdded → FalkorDB)
            for subj in entities.get("subjects", []):
                proj.apply(subj)

            # aboutSubject edges — Object → Subject (ONTOLOGY_v2.5 §2.2)
            obj_id = entities.get("object", {}).get("id", "")
            for sid in entities.get("aboutSubjects", []):
                if obj_id and sid:
                    proj.g.query(
                        "MATCH (o:Object {id: $oid}) "
                        "MATCH (s:Subject {id: $sid}) "
                        "MERGE (o)-[:aboutSubject]->(s)",
                        params={"oid": obj_id, "sid": sid},
                    )

            count += 1
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

        self._server = HTTPServer(("127.0.0.1", self.webhook_port), Handler)
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
