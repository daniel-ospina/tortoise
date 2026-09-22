"""P1-7 #6976 / GAP-11 #6998: GitHub connector — poll + webhook.

Zero Python deps. Uses `gh` CLI for polling + stdlib http.server for webhook.
Maps issues/PRs → 4-entity chain per ONTOLOGY_v2.5 §1.1:
  Source (github_issue) → Object (pm:issue) → Event (github.issue.*)
  + Subject (naturalPerson for author/assignees)

#1725 (Slice 0): all ontology mapping is DELEGATED to the shared stateless
`tortoise/github_map.py` — the SINGLE eventId/eventKind vocabulary (the
#1155 normalization; pm:cardCreated/pm:cardCompleted are retired in favor of
github.issue.open/github.issue.closed). These mappers are THIN WRAPPERS that
only adapt gh-CLI field naming (already handled by github_map's normalizer).
"""
from __future__ import annotations  # noqa: I001

import json
import hmac
import hashlib
import logging
import os
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable  # noqa: UP035

logger = logging.getLogger(__name__)

from tortoise import github_map  # noqa: E402 — the shared #1725 mapper


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


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
        # Env var takes precedence over config for webhook_secret (#324)
        env_secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
        self.webhook_secret = (
            env_secret if env_secret is not None else cfg.get("webhook_secret", "")
        )
        self.api = api
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._routing = self._load_routing()

    def _load_routing(self) -> dict:
        """Load entity routing config (#1395).

        Precedence: TORTOISE_ROUTING_CONFIG env var (user/customer file) >
        the packaged default (tortoise/config/routing.yaml, shipped in the
        wheel) > built-in {} fallback. A missing/unreadable/invalid user file
        falls back to the packaged default with a warning — never crashes.
        """
        import yaml

        def _load(path) -> dict | None:
            """Return the dict on success (even an empty dict), None on failure."""
            try:
                data = yaml.safe_load(path.read_text())
                return data if isinstance(data, dict) else None
            except Exception:
                return None

        # 1. User/customer override (#1395)
        override = os.environ.get("TORTOISE_ROUTING_CONFIG")
        if override:
            from pathlib import Path as _Path
            p = _Path(override)
            if p.is_file():
                loaded = _load(p)
                if loaded is not None:
                    return loaded  # even {} — an explicit "no routing" override
                logger.warning(
                    "TORTOISE_ROUTING_CONFIG=%s unreadable/invalid — falling back "
                    "to the packaged default", override,
                )
            else:
                logger.warning(
                    "TORTOISE_ROUTING_CONFIG=%s not found — falling back to the "
                    "packaged default", override,
                )

        # 2. Packaged default (wheel + editable installs; package-data)
        try:
            import importlib.resources as resources
            return _load(resources.files("tortoise").joinpath("config", "routing.yaml"))
        except Exception:
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
                if cfg.get("team"): team = cfg["team"]  # noqa: E701
                if cfg.get("role"): role = cfg["role"]  # noqa: E701
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
        """Map GitHub issue → entity chain (thin wrapper over github_map).

        Returns dict with: object (ObjectRegistered), event (EventRecorded),
        subjects (SubjectAdded list), about_subjects.
        Per ONTOLOGY_v2.5 §1.1: Object (pm:issue), Subject (naturalPerson).

        #1725: ALL mapping is delegated to `github_map` (the #1155 single
        vocabulary — eventKind github.issue.{state}, subject
        issue:{repo}#{n}); this method adds the connector's routing props
        (label-derived team/role/complexity) and the aboutSubject wiring.
        """
        number = issue.get("number")
        title = issue.get("title", "")
        if not title or not number:
            return None

        labels = [l.get("name", "") for l in (issue.get("labels") or [])]  # noqa: E741
        complexity = _extract_label_prefix(labels, "complexity:") or "standard"
        ux_rating = _extract_label_prefix(labels, "ux:") or ""
        route = self._route_issue(labels)

        obj = github_map.issue_to_object(
            issue, self.repo, routed_team=route["team"],
            routed_role=route["role"], routed_product=route["product"],
            complexity=complexity, ux_rating=ux_rating)
        if obj is None:
            return None
        event = github_map.issue_to_event(issue, self.repo, previous_state=None)
        if event is None:
            return None
        subjects, about_subjects = github_map.issue_to_subjects(issue)

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

        #1155: the entity path is the canonical producer for issue Events —
        poll-path issue events are DEDUPED (skipped) when the entity chain
        covered the same issue, so one issue yields exactly ONE Event node.
        Both producers share the deterministic eventId
        `github-issue-{repo}-{n}-created`; the poll path remains the
        fallback producer (raw-issues fetch failure) and the PR producer.
        Entity chains are applied BEFORE the poll path so the produces edge
        always lands on the real pm:issue Object (id = entity_id) — never a
        name-stub with a random id.
        """
        # Entity extraction for issues (ONTOLOGY_v2.5 §1.1, PM domain extension)
        try:
            raw_issues = self.poll_raw_issues()
        except Exception:
            raw_issues = []  # ponytail: gh CLI may fail, skip entity extraction

        count = 0
        covered: set[int] = set()
        for issue in raw_issues:
            entities = self._issue_to_entities(issue)
            if not entities:
                continue
            covered.add(issue.get("number"))

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

            # #388: explicit Source → Object references wiring (belt-and-
            # suspenders next to the event-level sourceObjectId materialization
            # in _upsert_event — idempotent MERGE, no-op when the event already
            # wired it).
            source_url = entities.get("event", {}).get("sourceUrl")
            if obj_id and source_url:
                proj.link_source_to_entity(
                    source_url, obj_id, "Object",
                    entities.get("event", {}).get("sourceKind", "github_issue"),
                )

            count += 1

        # Poll path — PRs always (entity path doesn't cover PRs); issue
        # events only when the entity path did not cover them (#1155 dedup).
        covered_event_ids = {
            f"github-issue-{self.repo}-{n}-created" for n in covered if n
        }
        for ev in self.poll():
            if ev.get("eventId") in covered_event_ids:
                continue  # absorbed by the entity path (one Event per issue)
            proj.apply(ev)
            count += 1
        return count

    # ── Webhook (GAP-11) ───────────────────────────────────────────

    def start_webhook(self, on_event: Callable[[dict], None] | None = None) -> int:
        """Start webhook server on configured port. Returns port number.
        Runs in a daemon thread; call stop_webhook() to shut down.
        """
        if not self.webhook_port:
            return 0

        # #331: double-start must be a no-op — a second HTTPServer on the
        # same port raises Address already in use and orphans the first
        # server + its thread (socket + thread leak).
        # #331 (review r2): a DEAD serve_forever thread is not a running
        # server — close its stale socket so the re-bind below succeeds
        # (previously: silent no-op with nothing serving).
        if self._server is not None:
            if self._thread is not None and self._thread.is_alive():
                return self.webhook_port
            try:  # noqa: SIM105
                self._server.server_close()
            except OSError:
                pass
            self._server = None
            self._thread = None

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
                try:
                    ev = connector._webhook_to_event(event_type, payload)
                    if ev:
                        if on_event:
                            on_event(ev)
                        if connector.api:
                            connector.api.get_proj().apply(ev)
                except Exception:
                    # #331: processing failures must be visible and answered
                    # with HTTP 500 — a silently dropped connection makes
                    # GitHub retry blindly with no server-side trace.
                    logger.exception(
                        "GitHub webhook processing failed (event=%s)",
                        event_type)
                    self.send_response(500)
                    self.end_headers()
                    return
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
            try:
                self._server.shutdown()
            finally:
                # #331: shutdown() stops serve_forever but does NOT release
                # the listening socket — without server_close() the port
                # stays bound and stop→restart fails with EADDRINUSE.
                self._server.server_close()
                self._server = None
                self._thread = None

    # ── Event mapping ──────────────────────────────────────────────

    def _issue_to_event(self, issue: dict) -> dict | None:
        """Poll-path issue event — thin wrapper over github_map.

        #1725: byte-identical to the shared vocabulary (previous_state=None
        ⇒ the `-created` event, kind github.issue.{state})."""
        return github_map.issue_to_event(issue, self.repo, previous_state=None)

    def _pr_to_event(self, pr: dict) -> dict | None:
        """PR event — thin wrapper over github_map (#1725)."""
        return github_map.pr_to_event(pr, self.repo)

    def _webhook_to_event(self, event_type: str, payload: dict) -> dict | None:
        """Map GitHub webhook event → EventRecorded (thin wrapper over
        github_map). Transitions mint transition ids (P2-7): action
        ``closed`` ⇒ ``-closed``, ``reopened`` ⇒ ``-reopened``; opening (no
        previous state) ⇒ ``-created``."""
        if event_type == "issues":
            issue = payload.get("issue", {})
            action = payload.get("action", "")
            if action in ("closed", "reopened"):
                return github_map.issue_to_event({
                    "number": issue.get("number"),
                    "title": issue.get("title", ""),
                    "state": "closed" if action == "closed" else "open",
                    "createdAt": issue.get("created_at", ""),
                    "closedAt": issue.get("closed_at"),
                    "html_url": issue.get("html_url", ""),
                }, self.repo,
                    previous_state="open" if action == "closed" else "closed")
            return self._issue_to_event({
                "number": issue.get("number"),
                "title": issue.get("title", ""),
                "state": "closed" if action == "closed" else "open",
                "createdAt": issue.get("created_at", ""),
                "closedAt": issue.get("closed_at"),
                "html_url": issue.get("html_url", ""),
            })
        if event_type == "pull_request":
            pr = payload.get("pull_request", {})
            return self._pr_to_event({
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "state": "closed" if payload.get("action") == "closed" else "open",
                "createdAt": pr.get("created_at", ""),
                "closedAt": pr.get("closed_at"),
                "mergedAt": pr.get("merged_at"),
                "html_url": pr.get("html_url", ""),
            })
        return None


def _verify_sig(secret: bytes, header: str, body: bytes) -> bool:
    """Constant-time HMAC-SHA256 verification of webhook signature."""
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", header)
