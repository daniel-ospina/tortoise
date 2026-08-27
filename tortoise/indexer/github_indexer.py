"""GitHub issues/PRs → Tortoise entities/statements indexer (#499, #1725).

Reworked in place for Slice 0 (#1725 / #1714): fetch+diff → project.

Phase 1 (fetch): httpx REST, pinned to ``sort=updated&direction=desc``
(cursor-correct AND stoppable at org scale — created-desc blinds the diff
beyond the window), composite ``(updated_at, number)`` cursor, parameterized
per-run cap (cost control, not correctness), honest truncation ("N issues
beyond window" surfaced in job status). Mid-walk 401/429 ⇒ raise
``GitHubFetchError`` — the caller marks the job failed with a readable error
and the cursor is NOT advanced past unprocessed items (resume without
gaps/dupes; idempotent writes make re-processing safe).

Phase 2 (write): entities + lifecycle Events via ``sdk._get_proj().apply()``
(the projection folds ``Object.status`` from lifecycle event kinds);
statement Points via ``create_point(kind="statement", id=…, dedup=False)``
TWO-PHASE (probe by externalId + status != terminal WITHOUT props → write
props only on a genuine create — no updatedAt churn on re-runs, P2-1).

Lifecycle decision table (THE rule):
- close/reopen ON TRANSITION → Event (``github.issue.closed``/``reopened``)
  + ``Object.status`` projection ONLY — statement points content/status-
  UNTOUCHED; ``invalidate_point`` is NEVER called (it always writes
  CORRECTS, sdk.py — the amnesia defect).
- content edit → new statement + ``supersede_point`` (bi-temporal
  validFrom/validTo, edge transfer).
- first ingest of an already-closed issue → ``-created`` ONLY (kind carries
  the closed state) — the one-time legacy backfill is the only other source
  of ``-closed`` events (marker-gated, T2-P3).

Writes ``statement`` ONLY — never ``observation`` (removed kind, §5).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Any

import httpx

from tortoise import github_map

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_MAX_ITEMS_PER_RUN = 500
_PAGE_SIZE = 100

# Terminal point statuses — the current-statement lookup is externalId +
# status != terminal (P1-2), NEVER content-hash dedup (a revert mints v+1,
# so a content-identical terminal point must never be resolved as current).
TERMINAL_STATUSES = ("retracted", "superseded", "archived")

# Object.status → issue-state projection (inverse of the lifecycle fold).
_STATUS_TO_STATE = {
    "completed": "closed",
    "in_progress": "open",
    "live": "open",
    "open": "open",
}


class GitHubFetchError(Exception):
    """GitHub API fetch failure — the run must fail honestly, cursor
    unadvanced past unprocessed items."""


class GitHubIndexer:
    """Background indexer: org issues/PRs → entities/events/statements."""

    def __init__(self, token: str, httpx_client: httpx.AsyncClient | None = None):
        self._token = token
        self._client = httpx_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20)
        return self._client

    async def _close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        """GET with bounded retry-with-backoff on 429/5xx (T1-P13).

        Terminal auth failures (401/403) raise immediately — the token is
        not going to heal within the run; retrying would waste the quota
        budget and mask the real error. After bounded retries on 429/5xx a
        GitHubFetchError is raised (honest fail — the caller's cursor stays
        put).
        """
        headers = {"Authorization": f"Bearer {self._token}",
                   "Accept": "application/vnd.github+json"}
        last_status = None
        for attempt in range(4):
            try:
                r = await client.get(url, headers=headers)
            except httpx.HTTPError as e:
                logger.warning("GitHub request failed (%s): %s", url, e)
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code in (401, 403):
                raise GitHubFetchError(
                    f"GitHub auth failed ({r.status_code}) for {url} — "
                    "token may be expired or lack repo scope")
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(retry_after)
                last_status = 429
                continue
            if r.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
                last_status = r.status_code
                continue
            return r
        raise GitHubFetchError(
            f"GitHub request failed after retries (last status {last_status or 'transport'}) "
            f"for {url}")

    async def resolve_repos(self, org: str) -> list[str]:
        """Resolve repo names for an org (try org, fall back to user)."""
        client = await self._get_client()
        for kind in ("orgs", "users"):
            r = await self._get(
                client, f"{_GITHUB_API}/{kind}/{org}/repos?per_page=100")
            if r.status_code == 200:
                return [repo["full_name"] for repo in r.json()]
        return []

    @staticmethod
    def _link_header_urls(link: str) -> dict[str, str]:
        """Parse a GitHub Link header into {rel: url}."""
        out: dict[str, str] = {}
        for part in link.split(","):
            if ">" not in part or "rel=" not in part:
                continue
            url = part[part.index("<") + 1:part.index(">")]
            rel = part.split('rel="')[1].rstrip('";')
            out[rel] = url
        return out

    @staticmethod
    def _last_page_estimate(link: str, per_page: int = _PAGE_SIZE) -> int | None:
        """Honest total estimate from the Link header's rel="last" page.

        GitHub includes rel="last" (page N) while more pages exist; the
        total is an upper bound (last page may be partial). None when no
        rel="last" (no more pages — nothing beyond the window).
        """
        urls = GitHubIndexer._link_header_urls(link)
        last = urls.get("last")
        if not last:
            return None
        from urllib.parse import parse_qs, urlparse
        page = parse_qs(urlparse(last).query).get("page", ["1"])[0]
        try:
            return int(page) * per_page
        except ValueError:
            return None

    async def _fetch_page(self, client: httpx.AsyncClient, repo: str,
                          cursor: dict | None) -> tuple[list[dict], str | None, int | None]:
        """Fetch one page of issues (updated desc) + next URL + last-page total estimate."""
        params = "state=all&per_page=100&sort=updated&direction=desc"
        since = ""
        if cursor and cursor.get("updated_at"):
            # `since` alone is inclusive-of-boundary ambiguous; the composite
            # cursor's number tiebreak below closes the same-second gap
            # (T2-P4). `updated_at − 1s` guarantees boundary-second items are
            # returned so the number tiebreak can decide them.
            from datetime import datetime, timedelta
            try:
                base = datetime.fromisoformat(cursor["updated_at"].replace("Z", "+00:00"))
            except ValueError:
                base = None
            if base is not None:
                since_dt = base - timedelta(seconds=1)
                since = "&since=" + since_dt.astimezone(UTC).isoformat()
        url = f"{_GITHUB_API}/repos/{repo}/issues?{params}{since}"
        r = await self._get(client, url)
        if r.status_code != 200:
            raise GitHubFetchError(
                f"GitHub issues fetch failed ({r.status_code}) for {repo}")
        batch = r.json()
        link = r.headers.get("Link", "")
        urls = self._link_header_urls(link)
        return batch, urls.get("next"), self._last_page_estimate(link)

    @staticmethod
    def _inside_cursor(issue: dict, cursor: dict | None) -> bool:
        """True when the issue is a BOUNDARY-SECOND item already indexed.

        Composite ``(updated_at, number)`` (T2-P4): the resume skip applies
        ONLY to items at the cursor's ``updated_at`` second with a number ≤
        the cursor's number — those were processed by the run that produced
        the cursor. Items at OLDER seconds are never skipped (they are the
        deferred backlog — capped runs must keep draining them, and
        idempotent probes make any overlap harmless).
        """
        if not cursor:
            return False
        n = github_map._norm_issue(issue)
        cur_updated = (cursor.get("updated_at") or "")
        cur_number = int(cursor.get("number") or 0)
        if not n["updated_at"] or not cur_updated:
            return False
        return (n["updated_at"] == cur_updated
                and n["number"] <= cur_number)

    async def _fetch_items(self, client: httpx.AsyncClient, repo: str,
                           cursor: dict | None,
                           cap: int) -> tuple[list[dict], int | None, bool]:
        """Fetch issues (updated desc, paginated) up to ``cap``.

        Returns (items, total_estimate, cap_hit).

        Two resume regimes (pinned by the plan's resume semantics):
        - DIFF (steady state — the previous run was NOT cap-truncated):
          first page ``since = cursor.updated_at − 1s``; the whole walk stays
          since-bounded (GitHub carries ``since`` in Link next URLs). Only
          items updated at/after the boundary second are fetched; boundary-
          second items with number ≤ cursor.number are skipped (T2-P4 —
          exactly once across the boundary).
        - DRAIN (previous run cap-truncated): no ``since`` — the walk
          refetches from the top so the deferred backlog (updated before the
          cursor) keeps draining up to ``cap`` per run. The boundary skip
          keeps it exact-once; idempotent probes make overlap harmless.
        """
        items: list[dict] = []
        total_estimate: int | None = None
        cap_hit = False
        # DRAIN mode when the persisted cursor carries the truncation flag.
        since_cursor = None if (cursor and cursor.get("truncated")) else cursor
        batch, next_url, total_estimate = await self._fetch_page(
            client, repo, since_cursor)
        while True:
            # Deterministic walk order: updated DESC, number ASC within the
            # same second (GitHub's within-second order is unspecified) — the
            # composite cursor's number tiebreak is only exact-once when the
            # walk ascends through a boundary second before truncating.
            batch = sorted(
                batch,
                key=lambda i: (github_map._norm_issue(i)["updated_at"],
                               -int(github_map._norm_issue(i)["number"] or 0)),
                reverse=True,
            )
            for item in batch:
                if self._inside_cursor(item, cursor):
                    continue
                items.append(item)
                if len(items) >= cap:
                    cap_hit = True
                    return items, total_estimate, cap_hit  # window full — stop
            if not next_url:
                return items, total_estimate, cap_hit
            if since_cursor is None:
                # DRAIN: GitHub's Link next-URL carries the `since` param —
                # strip it so the walk continues the FULL sorted fetch into
                # the deferred backlog.
                from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
                parts = urlsplit(next_url)
                q = parse_qs(parts.query)
                q.pop("since", None)
                next_url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                                       urlencode(q, doseq=True), parts.fragment))
            r = await self._get(client, next_url)
            if r.status_code != 200:
                raise GitHubFetchError(
                    f"GitHub issues fetch failed ({r.status_code})")
            batch = sorted(
                r.json(),
                key=lambda i: (github_map._norm_issue(i)["updated_at"],
                               -int(github_map._norm_issue(i)["number"] or 0)),
                reverse=True,
            )
            urls = self._link_header_urls(r.headers.get("Link", ""))
            next_url = urls.get("next")
            if total_estimate is None:
                total_estimate = self._last_page_estimate(
                    r.headers.get("Link", ""))

    # ── Phase 2: write path ─────────────────────────────────────────

    def _project_issue(self, sdk, proj, repo: str, issue: dict) -> dict:
        """Project one issue: Object + Subjects + Events + statement.

        Returns {"points_created", "statements_superseded", "events_minted",
        "quota_hit"} — the lifecycle decision table lives here.
        """
        stats = {"points_created": 0, "statements_superseded": 0,
                 "events_minted": 0}
        obj = github_map.issue_to_object(issue, repo)
        if obj is None:
            return stats
        obj_id = obj["id"]
        # Previous issue-state from the PRE-EXISTING Object.status — read
        # BEFORE applying the fresh Object (a new Object defaults to
        # status='live', which would otherwise be misread as a prior open
        # state and mint a spurious `-closed` on first ingest).
        prev_state = self._object_state_to_issue_state(proj, obj_id)
        proj.apply(obj)

        subjects, about_ids = github_map.issue_to_subjects(issue)
        for subj in subjects:
            proj.apply(subj)
        for sid in about_ids:
            proj.g.query(
                "MATCH (o:Object {id:$oid}) "
                "MATCH (s:Subject {id:$sid}) "
                "MERGE (o)-[:aboutSubject]->(s)",
                params={"oid": obj_id, "sid": sid},
            )

        # Creation event — ALWAYS (first ingest mints `-created` only, even
        # for an already-closed issue — kind carries the state).
        created = github_map.issue_to_event(issue, repo, previous_state=None)
        if created is not None:
            proj.apply(created)
            stats["events_minted"] += 1
        # Transition events — ONLY on a real state change (diff against the
        # persisted Object.status projection, never the fetch window).
        if prev_state is not None:
            trans = github_map.issue_to_event(issue, repo,
                                              previous_state=prev_state)
            if trans is not None:
                proj.apply(trans)
                stats["events_minted"] += 1

        # Statement point (content/status-untouched by lifecycle events).
        s_stats = self._upsert_statement(sdk, proj, repo, issue)
        stats["points_created"] += s_stats["points_created"]
        stats["statements_superseded"] += s_stats["statements_superseded"]
        return stats

    @staticmethod
    def _object_state_to_issue_state(proj, obj_id: str) -> str | None:
        """Previous issue-state derived from the persisted Object.status.

        None = first ingest (Object absent or never lifecycle-folded).
        """
        rows = proj.g.query(
            "MATCH (o:Object {id:$id}) RETURN o.status",
            params={"id": obj_id},
        ).result_set
        if not rows or rows[0][0] is None:
            return None
        return _STATUS_TO_STATE.get(str(rows[0][0]))

    def _upsert_statement(self, sdk, proj, repo: str, issue: dict) -> dict:
        """Two-phase statement write (P2-1): probe WITHOUT props → create
        with props only on a genuine miss. ``updatedAt`` byte-unchanged on
        re-runs of unchanged issues. Content edits → new statement +
        ``supersede_point`` (bi-temporal). NEVER ``invalidate_point``.
        """
        n = github_map._norm_issue(issue)
        eid = github_map.external_id(repo, n["number"])
        version = self._next_version(proj, eid)
        records = github_map.issue_to_statements(issue, repo, version=version)
        out = {"points_created": 0, "statements_superseded": 0}
        if not records:
            return out
        rec = records[0]

        # Phase A — probe: current (non-terminal) statement for this issue.
        rows = proj.g.query(
            "MATCH (n:Point {externalId:$eid}) "
            "WHERE n.status IS NULL OR NOT (n.status IN $terminal) "
            "RETURN n.id, n.content_hash, n.updatedAt "
            "ORDER BY n.createdAt DESC LIMIT 1",
            params={"eid": eid, "terminal": list(TERMINAL_STATUSES)},
        ).result_set
        # Bi-temporal window start: the statement is valid from the issue's
        # last update (E6 D3 — written at creation; the supersede's
        # valid_from closes the predecessor's window contiguously).
        valid_from = n["updated_at"] or None

        if rows:
            cur_id, cur_hash, _ = rows[0]
            if cur_hash == _content_hash(rec["content"]):
                # Unchanged — zero writes (no updatedAt churn, no EP churn).
                return out
            # Content edit → mint the next version + bi-temporal supersede.
            sdk.create_point(kind="statement", id=rec["id"], dedup=False,
                             content=rec["content"], **rec["props"],
                             **({"validFrom": valid_from}
                                if valid_from else {}))
            self._link_about_object(proj, rec["id"], repo, n["number"])
            sdk.supersede_point(cur_id, rec["id"],
                                **({"valid_from": valid_from}
                                   if valid_from else {}))
            out["points_created"] = 1
            out["statements_superseded"] = 1
            return out

        # Phase B — genuine create (probe missed): write props on create.
        sdk.create_point(kind="statement", id=rec["id"], dedup=False,
                         content=rec["content"], **rec["props"],
                         **({"validFrom": valid_from} if valid_from else {}))
        self._link_about_object(proj, rec["id"], repo, n["number"])
        out["points_created"] = 1
        return out

    @staticmethod
    def _next_version(proj, eid: str) -> int:
        """Monotonic per-issue version: count of ALL statements with the
        externalId (superseded included) + 1. A revert mints v+1 — never
        reuses a terminal id (P1-2)."""
        rows = proj.g.query(
            "MATCH (n:Point {externalId:$eid}) RETURN count(n)",
            params={"eid": eid},
        ).result_set
        return int(rows[0][0]) + 1

    @staticmethod
    def _link_about_object(proj, point_id: str, repo: str, number: int) -> None:
        """(Statement)-[:aboutObject]->(WorkItem Object) edge."""
        proj.g.query(
            "MATCH (p:Point {id:$pid}), (o:Object {id:$oid}) "
            "MERGE (p)-[:aboutObject]->(o)",
            params={"pid": point_id,
                    "oid": f"github-issue-{repo}-{number}"},
        )

    def _project_pr(self, proj, repo: str, pr: dict) -> dict:
        ev = github_map.pr_to_event(pr, repo)
        if ev is None:
            return {"events_minted": 0}
        proj.apply(ev)
        return {"events_minted": 1}

    # ── Backfill (T1-P1 + T2-P3) ────────────────────────────────────

    def backfill_legacy_closed(self, proj) -> int:
        """ONE-TIME legacy `-closed` backfill.

        Scans PRE-EXISTING ``-created`` Events that carry a closed kind
        (``github.issue.closed`` / legacy ``pm:cardCompleted``) + ``endedAt``
        and have no ``-closed`` sibling, and mints the ``-closed`` event.
        The caller gates this on the ``github_legacy_backfill_done`` marker
        (absent ⇒ run once, then set) and must call it BEFORE minting fresh
        events (no double-mint on fresh first-runs — T2-P3).
        """
        # NOTE: no `NOT EXISTS { MATCH ... }` subquery — FalkorDBLite
        # (embedded) rejects EXISTS blocks; the sibling check runs in
        # Python against a one-shot id set (idempotent, no double-mint).
        rows = proj.g.query(
            "MATCH (e:Event) "
            "WHERE e.eventId ENDS WITH '-created' "
            "AND (e.eventKind = 'github.issue.closed' "
            "     OR e.eventKind = 'pm:cardCompleted') "
            "AND e.endedAt IS NOT NULL AND e.endedAt <> '' "
            "RETURN e.eventId, e.subject, e.object, e.startedAt, e.endedAt, "
            "       e.source",
        ).result_set
        existing = {
            str(r[0]) for r in proj.g.query(
                "MATCH (x:Event) RETURN x.eventId").result_set
        }
        minted = 0
        for event_id, subject, obj, started_at, ended_at, source in rows:
            closed_id = str(event_id).replace("-created", "-closed")
            if closed_id in existing:
                continue  # sibling already minted (idempotent)
            proj.apply({
                "type": "EventRecorded",
                "eventId": closed_id,
                "eventKind": "github.issue.closed",
                "subject": subject or "",
                "object": obj or "",
                "startedAt": started_at or "",
                "endedAt": ended_at,
                "source": source or "",
                "sourceKind": "github_issue",
                "participants": [],
            })
            minted += 1
        return minted

    # ── Top-level entry ─────────────────────────────────────────────

    async def index_repo(self, sdk, repo: str, *, cursor: dict | None = None,
                         cap: int = _MAX_ITEMS_PER_RUN,
                         quota_check=None) -> dict:
        """Fetch + diff + project ONE repo. Returns per-repo stats.

        ``cursor``: composite (updated_at, number) from the previous run.
        ``quota_check``: callable re-checked per batch (raises
        QuotaExceededError); None = no quota gate (stdio/operator).
        """
        client = await self._get_client()
        proj = sdk._get_proj()
        stats: dict[str, Any] = {
            "repo": repo,
            "total_fetched": 0,
            "processed": 0,
            "issues_beyond_window": 0,
            "points_created": 0,
            "statements_superseded": 0,
            "events_minted": 0,
            "errors": [],
            "quota_hit": False,
            "cursor": cursor,
        }
        # A fetch failure (mid-walk 401/429 after bounded retries) RAISES
        # GitHubFetchError — the caller marks the job failed with a readable
        # error and the cursor is NOT advanced past unprocessed items
        # (T1-P13: honest fail; re-run resumes without gaps/dupes).
        items, total_estimate, cap_hit = await self._fetch_items(
            client, repo, cursor, cap)
        stats["total_fetched"] = len(items)
        last: dict | None = None
        for item in items:
            if quota_check is not None:
                try:
                    quota_check()
                except Exception as e:  # QuotaExceededError / QuotaCheckError
                    stats["quota_hit"] = True
                    stats["errors"].append(str(e))
                    break
            n = github_map._norm_issue(item)
            if "pull_request" in item:
                stats["events_minted"] += self._project_pr(proj, repo, item)["events_minted"]
            else:
                try:
                    s_stats = self._project_issue(sdk, proj, repo, item)
                except Exception as e:
                    stats["errors"].append(f"{repo}#{n['number']}: {e}")
                    continue
                stats["points_created"] += s_stats["points_created"]
                stats["statements_superseded"] += s_stats["statements_superseded"]
                stats["events_minted"] += s_stats["events_minted"]
            stats["processed"] += 1
            # Advance the composite cursor past this processed item (only
            # ever to PROCESSED items — a mid-walk failure leaves it put).
            if n["updated_at"]:
                last = {"updated_at": n["updated_at"], "number": n["number"]}
        # Honest truncation (P1-3): "N issues beyond window" from the
        # rel="last" upper-bound estimate — 0 when nothing is known to
        # remain beyond the cap window.
        if total_estimate is not None:
            stats["issues_beyond_window"] = max(
                0, total_estimate - stats["processed"])
        if last is not None:
            new_cursor: dict[str, Any] = {
                "updated_at": last["updated_at"], "number": last["number"]}
            # Cap-truncated runs stamp `truncated` so the next run enters
            # DRAIN mode and keeps draining the deferred backlog.
            if cap_hit:
                new_cursor["truncated"] = True
            stats["cursor"] = new_cursor
        await self._close()
        return stats


def _content_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
