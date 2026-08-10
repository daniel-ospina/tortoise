"""GitHub Issues client — stdlib urllib only (zero new dependencies).

Files GitHub issues for the DR alert lifecycle (the agent-visible leg of the
dual-channel alert sink; the human leg is Telegram, see alert_store.py).
All functions take explicit ``repo``/``token`` so the caller (app or driver)
controls which credential is used — the app uses the Fly-only PAT, the driver
uses GITHUB_TOKEN with ``permissions: issues: write``.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
_DR_LABEL = "dr:backup"
_LABEL_COLOR = "B60205"  # red — alert/incident class


class GithubApiError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"github api {status}: {message}")
        self.status = status


def _request(method: str, url: str, token: str, payload: dict | None = None,
             timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    body: bytes | None = None
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(payload).encode("utf-8")
    try:
        with urllib.request.urlopen(req, body, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise GithubApiError(e.code, detail or e.reason) from e
    except urllib.error.URLError as e:
        raise GithubApiError(0, str(e.reason)) from e


def ensure_label(repo: str, token: str) -> None:
    """Idempotently create the ``dr:backup`` label (404 → create)."""
    label_url = f"{_API}/repos/{repo}/labels/{urllib.parse.quote(_DR_LABEL)}"
    try:
        _request("GET", label_url, token)
    except GithubApiError as e:
        if e.status == 404:
            try:
                _request(
                    "POST", f"{_API}/repos/{repo}/labels", token,
                    {"name": _DR_LABEL, "color": _LABEL_COLOR,
                     "description": "Disaster-recovery / backup alerts"},
                )
            except GithubApiError as e2:
                # 422 = already exists (race) — fine.
                if e2.status != 422:
                    raise
        else:
            raise


def create_issue(
    repo: str, token: str, *, title: str, body: str,
    assignee: str | None = None,
) -> int:
    """File an issue labeled ``dr:backup``; returns the issue number."""
    ensure_label(repo, token)
    payload: dict = {"title": title, "body": body, "labels": [_DR_LABEL]}
    if assignee:
        payload["assignees"] = [assignee]
    data = _request("POST", f"{_API}/repos/{repo}/issues", token, payload)
    return int(data["number"])


def close_issue(repo: str, token: str, number: int, comment: str | None = None) -> None:
    if comment:
        _request(
            "POST", f"{_API}/repos/{repo}/issues/{number}/comments", token,
            {"body": comment},
        )
    _request("PATCH", f"{_API}/repos/{repo}/issues/{number}", token, {"state": "closed"})


def search_open_incident(repo: str, token: str, kind: str) -> list[int]:
    """GH-search fallback: open ``dr:backup`` issues whose title carries ``kind``.

    Used when R2 is unreachable (no dedup object possible) or when a created
    dedup object is missing its issue_number (create-then-die window).
    """
    query = f'repo:{repo} is:issue is:open label:"{_DR_LABEL}" in:title "[DR] {kind}"'
    url = f"{_API}/search/issues?q={urllib.parse.quote(query)}"
    data = _request("GET", url, token)
    return [int(i["number"]) for i in data.get("items", [])]
