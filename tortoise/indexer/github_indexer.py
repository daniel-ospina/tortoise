"""GitHub issues/PRs → Tortoise Points indexer (#499).

Fetches issues and PRs from a GitHub org via REST API and creates Points
(kind="observation", source="github") in the team's graph. Rate-limit aware
(exponential backoff on 429), paginated, idempotent (dedup by github_url),
and truncates oversized bodies.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_MAX_BODY_CHARS = 5000
_MAX_ITEMS_PER_RUN = 500
_RATE_LIMIT_REMAINING_THRESHOLD = 50


class GitHubIndexer:
    """Background indexer: org issues/PRs → Points in a team graph."""

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

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response | None:
        """GET with rate-limit aware retry (exponential backoff on 429)."""
        headers = {"Authorization": f"Bearer {self._token}",
                   "Accept": "application/vnd.github+json"}
        for attempt in range(4):
            try:
                r = await client.get(url, headers=headers)
            except httpx.HTTPError as e:
                logger.warning("GitHub request failed (%s): %s", url, e)
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(retry_after)
                continue
            if r.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
                continue
            return r
        return None

    async def _resolve_repos(self, client: httpx.AsyncClient, org: str) -> list[str]:
        """Resolve repo names for an org (try org, fall back to user)."""
        for kind in ("orgs", "users"):
            r = await self._get(client, f"{_GITHUB_API}/{kind}/{org}/repos?per_page=100")
            if r is not None and r.status_code == 200:
                return [repo["full_name"] for repo in r.json()]
        return []

    async def _fetch_items(self, client: httpx.AsyncClient, repo: str) -> list[dict]:
        """Fetch issues + PRs for a repo (paginated)."""
        items: list[dict] = []
        url = f"{_GITHUB_API}/repos/{repo}/issues?state=all&per_page=100"
        while url and len(items) < _MAX_ITEMS_PER_RUN:
            r = await self._get(client, url)
            if r is None or r.status_code != 200:
                break
            batch = r.json()
            # The issues endpoint includes PRs (pull_request key present)
            items.extend(batch)
            # Pagination via Link header rel="next"
            link = r.headers.get("Link", "")
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part[part.index("<") + 1:part.index(">")]
                    break
            url = next_url
        return items[: _MAX_ITEMS_PER_RUN]

    async def index_issues(self, sdk, org: str, repo: str | None = None) -> dict:
        """Index issues/PRs from an org into the SDK's team graph.

        Returns {points_created, repos_processed, errors, total_fetched}.
        Idempotent: SDK create_point dedup by github_url prop.
        """
        client = await self._get_client()
        errors: list[str] = []
        repos = [org + "/" + repo] if repo else await self._resolve_repos(client, org)
        points_created = 0
        total_fetched = 0
        for repo_name in repos:
            try:
                items = await self._fetch_items(client, repo_name)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{repo_name}: {e}")
                continue
            for item in items:
                body = (item.get("body") or "")[:_MAX_BODY_CHARS]
                title = (item.get("title") or "")[:_MAX_BODY_CHARS]
                url = item.get("html_url") or ""
                content = f"[{repo_name}] {title}\n\n{body}"[: _MAX_BODY_CHARS * 2]
                props = {"source": "github", "github_url": url,
                         "github_repo": repo_name,
                         "github_state": item.get("state") or "open",
                         "github_number": item.get("number")}
                try:
                    sdk.create_point(kind="observation", content=content,
                                     authoredBy="github-indexer", props=props)
                    points_created += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{repo_name}#{item.get('number')}: {e}")
                total_fetched += 1
        await self._close()
        return {"points_created": points_created, "repos_processed": len(repos),
                "errors": errors, "total_fetched": total_fetched}
