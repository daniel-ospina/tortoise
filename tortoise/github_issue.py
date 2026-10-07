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
        try:  # noqa: SIM105
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


def incident_title_matches(title: str, kind: str,
                           subject: str = "") -> bool:
    """#3029: does ``title`` name THIS incident — or merely mention its kind?

    The DR title contract (both writers MUST conform):

    * global kind    -> ``[DR] {KIND}``   optionally followed by `` — {prose}``
    * subject-scoped -> ``[DR] {KIND} — {subject}`` optionally + `` — {prose}``

    GitHub issue search tokenizes punctuation away, so a query for
    ``in:title "[DR] R2_DOWN"`` also matches an ordinary bug report whose
    title happens to contain the tokens ``dr`` + ``r2_down`` (verified live:
    it matched issue #2844, a bug report ABOUT R2_DOWN). Adoption is
    destructive — the resolver closes the adopted issue — so a match must be
    verified against the incident's own title shape, never trusted from the
    search result alone.

    The check is a prefix test plus a kind-boundary rule: the text right
    after ``[DR] {KIND}`` must be empty or start a new `` — `` segment. That
    rejects both prose mentions (``bug(dr): R2_DOWN is deduped…``) and
    kind-prefix collisions (``[DR] R2_DOWN`` vs a hypothetical kind ``R2``).
    """
    prefix = f"[DR] {kind}"
    if not title.startswith(prefix):
        return False
    rest = title[len(prefix):]
    if subject:
        # The subject segment must be EXACT: skip the leading " — " and stop
        # at the next " — " or end of title. A bare team subject is a literal
        # prefix of its per-graph subjects ("team_a" vs "team_a:g_x"), so a
        # startswith/endswith test alone would cross-adopt (#2413).
        if not rest.startswith(" — "):
            return False
        rest = rest[len(" — "):]
        segment, _, _remainder = rest.partition(" — ")
        return segment == subject
    # Global kind: nothing may follow the kind token except a new segment.
    return rest == "" or rest.startswith(" — ")


def search_open_incident(repo: str, token: str, kind: str,
                          org_id: str = "") -> list[int]:
    """GH-search fallback: open ``dr:backup`` issues whose title carries
    ``kind`` AND the incident subject (``org_id`` — an org id or the
    per-graph "{org}:{graph}" subject, #2313 Task 4).

    Subject scoping matters: incidents are keyed per (kind, subject); an
    adoption search that matches only ``kind`` would bind a same-kind issue
    opened for a DIFFERENT subject — recovery of one would then close the
    other's issue (silent-loss cross-talk). Empty ``org_id`` (global kinds
    like DRIVER_DOWN) matches the bare ``[DR] {kind}`` title.

    Used when R2 is unreachable (no dedup object possible) or when a created
    dedup object is missing its issue_number (create-then-die window).

    #3029: EVERY hit is verified against the incident's own title shape
    (:func:`incident_title_matches`). The search index is a recall filter, not
    an identity proof — the pre-#3029 code only verified when ``org_id`` was
    truthy, so a platform-scoped kind (empty subject) adopted the first token
    match, which is how a bug report about R2_DOWN became closable as the
    R2_DOWN incident.
    """
    title = f"[DR] {kind}" + (f" — {org_id}" if org_id else "")
    query = f'repo:{repo} is:issue is:open label:"{_DR_LABEL}" in:title "{title}"'
    url = f"{_API}/search/issues?q={urllib.parse.quote(query)}"
    data = _request("GET", url, token)
    items = data.get("items", [])
    return [int(i["number"]) for i in items
            if incident_title_matches(str(i.get("title", "")), kind, org_id)]


def issue_is_open(repo: str, token: str, number: int) -> bool:
    """Authoritative open/closed state for ONE issue (#3127).

    Preferred over inferring liveness from a search result: the GH *search*
    endpoint is rate limited (~30/min) and matches titles heuristically, so
    "absent from the results" is NOT proof of closure. Reading the issue's own
    state is, and it is what lets the dedup store trust a sentinel only while
    the issue it names is still open — a sentinel naming a CLOSED issue would
    otherwise swallow every recurrence of a live fault.
    """
    data = _request("GET", f"{_API}/repos/{repo}/issues/{number}", token)
    return str(data.get("state", "open")) == "open"


def issue_is_open_checked(repo: str, token: str, number: int) -> bool:
    """``issue_is_open`` with 404/410 treated as CLOSED, not as an error.

    A deleted (or bogus) issue number is POSITIVE evidence that the incident is
    no longer tracked, and the bash driver's ``gh_issue_open`` already treats it
    that way (404 -> re-file). Letting it surface as a generic failure would let
    a sentinel naming a deleted issue swallow the recurrence forever — the
    silent-outage class this whole mechanism exists to stop. Every other failure
    (5xx, rate limit, transport) still raises, so a blip counts as "open" and
    never re-files a live incident.
    """
    try:
        data = _request("GET", f"{_API}/repos/{repo}/issues/{number}", token)
    except GithubApiError as e:
        if e.status in (404, 410):
            return False
        raise
    return str(data.get("state", "open")) == "open"
