"""GAP-21 #7009: Linear connector — GraphQL → issue/cycle → EventRecorded JSONL.

Polls Linear's GraphQL API for issues and cycles, maps to EventRecorded events.
Requires: LINEAR_API_KEY env var. Zero Python deps outside stdlib.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_LINEAR_API = "https://api.linear.app/graphql"


class LinearConnector:
    """Poll Linear issues/cycles via GraphQL API. No webhook support (yet)."""

    def __init__(self, config: dict[str, Any] | None = None, api=None):
        cfg = config or {}
        # Env-first (matches Slack/GitHub pattern #324): distinguish "unset"
        # from "set to empty" so the loader's stripped config ("api_key": "")
        # never shadows a real LINEAR_API_KEY env var.
        env_api_key = os.environ.get("LINEAR_API_KEY")
        self.api_key = env_api_key if env_api_key is not None else cfg.get("api_key", "")
        self.team_id = cfg.get("team_id", "")
        self.limit = int(cfg.get("limit", 100))
        self.days = int(cfg.get("days", 30))  # lookback window
        self.api = api

    def _query(self, query: str, variables: dict | None = None) -> dict:
        """Execute a Linear GraphQL query. Returns parsed JSON response."""
        if not self.api_key:
            return {}

        payload = json.dumps({"query": query, "variables": variables or {}}).encode()
        req = urllib.request.Request(
            _LINEAR_API,
            data=payload,
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                if "errors" in data:
                    # #331: GraphQL-level errors (e.g. undeclared variables,
                    # schema drift) must be visible — a silent {} looks like
                    # "no data" and hides broken queries.
                    logger.warning(
                        "Linear GraphQL errors: %s",
                        "; ".join(e.get("message", str(e)) for e in data["errors"]),
                    )
                    return {}
                return data
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            # #331: transport/HTTP failures must be logged, not swallowed.
            logger.warning("Linear GraphQL request failed: %s", e)
            return {}

    # ── Polling ────────────────────────────────────────────────────

    def poll(self) -> list[dict]:
        """Fetch issues + cycles → EventRecorded dicts."""
        if not self.api_key:
            return []
        events: list[dict] = []
        events.extend(self._poll_issues())
        events.extend(self._poll_cycles())
        return events

    def _poll_issues(self) -> list[dict]:
        query = """
        query Issues($first: Int!, $filter: IssueFilter) {
          issues(first: $first, filter: $filter) {
            nodes {
              id
              identifier
              title
              description
              state { name type }
              createdAt
              updatedAt
              completedAt
              canceledAt
              assignee { name email }
              team { name key }
              cycle { name }
              priority
              url
            }
          }
        }
        """
        variables = {"first": self.limit}
        filter_dict: dict[str, Any] = {}
        if self.team_id:
            filter_dict["team"] = {"id": {"eq": self.team_id}}
        if filter_dict:
            variables["filter"] = filter_dict

        result = self._query(query, variables)
        issues = result.get("data", {}).get("issues", {}).get("nodes", [])
        return [ev for issue in (issues or [])
                if (ev := self._issue_to_event(issue))]

    def _poll_cycles(self) -> list[dict]:
        query = """
        query Cycles($first: Int!, $teamId: String) {
          cycles(first: $first, teamId: $teamId) {
            nodes {
              id
              number
              name
              startsAt
              endsAt
              completedAt
              team { name key }
              progress
            }
          }
        }
        """
        variables: dict[str, Any] = {"first": min(self.limit, 50)}
        if self.team_id:
            variables["teamId"] = self.team_id
        result = self._query(query, variables)
        cycles = result.get("data", {}).get("cycles", {}).get("nodes", [])
        return [ev for cycle in (cycles or [])
                if (ev := self._cycle_to_event(cycle))]

    def ingest(self, proj) -> int:
        """Poll + apply to projection. Returns count of applied events."""
        events = self.poll()
        count = 0
        for ev in events:
            proj.apply(ev)
            count += 1
        return count

    # ── Event mapping ──────────────────────────────────────────────

    def _issue_to_event(self, issue: dict) -> dict | None:
        ident = issue.get("identifier", "")
        title = issue.get("title", "")
        if not ident or not title:
            return None

        state_info = issue.get("state", {}) or {}
        state = state_info.get("name", "unknown").lower()
        team = issue.get("team", {}) or {}
        team_key = team.get("key", "")
        assignee = issue.get("assignee", {}) or {}

        created = issue.get("createdAt", "")
        ended = issue.get("completedAt") or issue.get("canceledAt")

        participants = [assignee.get("email", "")] if assignee.get("email") else []

        normalized_state = state.replace(" ", "_").lower()
        return {
            "type": "EventRecorded",
            "eventId": f"linear-issue-{ident}",
            "eventKind": f"linear.issue.{'completed' if ended else normalized_state}",
            "subject": f"issue:linear:{ident}",
            "object": title[:200],
            "startedAt": created,
            "endedAt": ended,
            "source": f"linear:{team_key}",
            "sourceKind": "linear_card",
            "participants": participants,
        }

    def _cycle_to_event(self, cycle: dict) -> dict | None:
        number = cycle.get("number")
        name = cycle.get("name", "")
        if number is None or not name:
            return None

        team = cycle.get("team", {}) or {}
        team_key = team.get("key", "")

        starts = cycle.get("startsAt", "")
        ends = cycle.get("endsAt", "")
        completed = cycle.get("completedAt")

        return {
            "type": "EventRecorded",
            "eventId": f"linear-cycle-{team_key}-{number}",
            "eventKind": "linear.cycle.completed" if completed else "linear.cycle.active",
            "subject": f"cycle:linear:{team_key}#{number}",
            "object": name[:200],
            "startedAt": starts,
            "endedAt": completed or None,
            "source": f"linear:{team_key}",
            "sourceKind": "linear_card",
            "participants": [],
        }
