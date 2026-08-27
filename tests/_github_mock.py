"""Shared mock GitHub REST transport for indexer tests (#1725 Slice 0).

Simulates the GitHub Issues API surface the indexer walks:
- repo resolution (orgs/users repos)
- issues list with ``sort=updated&direction=desc`` + ``since`` filter
- Link header pagination + rel="last" total estimation
- failure injection (401/429/5xx) for mid-walk honest-fail tests
"""
from __future__ import annotations

import httpx


def gh_issue(number: int, *, state: str = "open", title: str | None = None,
             updated_at: str = "2026-07-19T12:00:00Z",
             created_at: str = "2026-07-10T10:00:00Z",
             closed_at: str | None = None,
             body: str = "The bug is in the parser.",
             repo: str = "acme/repo1") -> dict:
    """REST-shaped issue fixture."""
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "state": state,
        "created_at": created_at,
        "updated_at": updated_at,
        "closed_at": closed_at,
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "body": body,
        "labels": [{"name": "complexity:micro"}],
        "assignees": [],
        "user": {"login": f"user{number}"},
    }


def gh_pr(number: int, *, state: str = "open",
          updated_at: str = "2026-07-19T12:00:00Z") -> dict:
    """REST-shaped PR fixture (the issues endpoint embeds PRs)."""
    d = gh_issue(number, state=state, title=f"PR {number}", updated_at=updated_at)
    d["pull_request"] = {"url": f"https://api.github.com/repos/acme/repo1/pulls/{number}"}
    d["merged_at"] = None
    return d


class MockGitHubTransport(httpx.AsyncBaseTransport):
    """Configurable GitHub REST mock.

    ``issues``: list of issue dicts served for every repo (or per-repo via
    ``issues_by_repo``). ``repos``: repo full_names returned by repo
    resolution. ``link_rel_last_page``: when set, the first issues response
    carries a Link header with rel=\"last\" at that page (honest-truncation
    estimation). ``failures``: list of (status_code, count) to inject in
    order on issues requests.
    """

    def __init__(self, *, issues: list[dict] | None = None,
                 issues_by_repo: dict[str, list[dict]] | None = None,
                 repos: list[str] | None = None,
                 link_rel_last_page: int | None = None,
                 failures: list[tuple[int, int]] | None = None,
                 respect_since: bool = True,
                 page_size: int | None = None,
                 resolve_repos_404: bool = False):
        self.issues_by_repo = issues_by_repo or {}
        if issues is not None:
            self.issues_by_repo["acme/repo1"] = issues
        self.repos = repos or ["acme/repo1"]
        self.link_rel_last_page = link_rel_last_page
        self.failures = list(failures or [])
        self.respect_since = respect_since
        # When set, the issues endpoint paginates (page_size per page) with
        # Link rel="next"/"prev"/"last" — multi-page DRAIN walks (P1-4).
        self.page_size = page_size
        # When set, BOTH orgs/ and users/ repo resolution return 404 —
        # resolve_repos must fail the job (P2, PR #1792).
        self.resolve_repos_404 = resolve_repos_404
        self.requests: list[httpx.Request] = []
        self._fail_idx = 0
        self._since_last: dict[str, str] = {}

    # ── request recording ──────────────────────────────────────────
    def issues_queries(self) -> list[str]:
        return [str(r.url.query) for r in self.requests if "/issues" in str(r.url)]

    def issue_query_params(self) -> list[dict]:
        from urllib.parse import parse_qs, urlsplit
        out = []
        for r in self.requests:
            if "/issues" in str(r.url):
                out.append({k: v[0] for k, v in
                            parse_qs(urlsplit(str(r.url)).query).items()})
        return out

    # ── handler ────────────────────────────────────────────────────
    def _next_failure(self) -> int | None:
        """Return a status code to inject, or None."""
        if self._fail_idx < len(self.failures):
            status, remaining = self.failures[self._fail_idx]
            if remaining > 0:
                self.failures[self._fail_idx] = (status, remaining - 1)
                return status
            self._fail_idx += 1
        return None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if "issues" in url and request.url.query:
            status = self._next_failure()
            if status is not None:
                return httpx.Response(status, json={"message": f"injected {status}"},
                                      request=request)
        if "/repos" in url and "/issues" not in url:
            if self.resolve_repos_404:
                return httpx.Response(404, json={}, request=request)
            return httpx.Response(200, json=[{"full_name": r} for r in self.repos],
                                  request=request)
        if "/issues" in url:
            # repo extraction: /repos/{owner}/{repo}/issues
            parts = url.split("/repos/")[1].split("/")
            repo = f"{parts[0]}/{parts[1]}"
            items = list(self.issues_by_repo.get(repo, []))
            # since filter (updated_at >= since) — GitHub semantics
            from urllib.parse import parse_qs, urlsplit
            params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
            since = params.get("since")
            if since and self.respect_since:
                items = [i for i in items if (i.get("updated_at") or "") >= since]
            # sort updated desc (mirror the pinned fetch params)
            items.sort(key=lambda i: (i.get("updated_at") or "", i.get("number") or 0),
                       reverse=True)
            headers: dict[str, str] = {}
            if self.page_size:
                total = len(items)
                last_page = max(1, -(-total // self.page_size))
                page = max(1, min(int(params.get("page", "1") or 1), last_page))
                page_items = items[(page - 1) * self.page_size:page * self.page_size]
                links = []
                if page < last_page:
                    links.append(
                        f'<https://api.github.com/repos/{repo}/issues?page={page + 1}>; '
                        'rel="next"')
                if page > 1:
                    links.append(
                        f'<https://api.github.com/repos/{repo}/issues?page={page - 1}>; '
                        'rel="prev"')
                if page < last_page:
                    links.append(
                        f'<https://api.github.com/repos/{repo}/issues?page={last_page}>; '
                        'rel="last"')
                if links:
                    headers["Link"] = ", ".join(links)
                return httpx.Response(200, json=page_items, headers=headers,
                                      request=request)
            if self.link_rel_last_page:
                headers["Link"] = (
                    f'<https://api.github.com/repos/{repo}/issues?page={self.link_rel_last_page}>; '
                    f'rel="last"')
            return httpx.Response(200, json=items, headers=headers, request=request)
        return httpx.Response(404, json={}, request=request)
