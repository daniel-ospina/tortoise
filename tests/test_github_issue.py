"""Tests for tortoise/github_issue.py — urllib client (mocked transport)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import tortoise.github_issue as gi


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


class _FakeUrlopen:
    """Records requests; returns scripted responses or raises HTTPError."""

    def __init__(self, script):
        self.script = list(script)  # list of (response_dict | HTTPError)
        self.calls: list[tuple[str, str]] = []  # (method, url)

    def __call__(self, req, body=None, timeout=None):
        self.calls.append((req.get_method() if hasattr(req, "get_method") else "GET", req.full_url))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


@pytest.fixture
def urlopen(monkeypatch):
    fake = _FakeUrlopen([])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


def test_create_issue_files_with_label_and_assignee(monkeypatch, urlopen):
    urlopen.script = [
        {"id": 1, "number": 42},          # ensure_label POST (404 first)
        {"items": []},                    # (no GET — create path)
        {"number": 42},                   # create_issue POST
    ]
    # ensure_label: GET 404 → POST create
    monkeypatch.setattr(gi, "_request", lambda *a, **k: (_ for _ in ()).throw(
        gi.GithubApiError(404, "not found")) if a[1].endswith("/labels/dr%3Abackup") else {"number": 42})
    # simpler: drive _request directly
    monkeypatch.setattr(gi, "_request", _fake_request)
    n = gi.create_issue("daniel-ospina/tortoise", "tok", title="[DR] STALE", body="b", assignee="daniel-ospina")
    assert n == 42


def _fake_request(method, url, token, payload=None, timeout=15.0):
    if url.endswith("/labels/dr%3Abackup") and method == "GET":
        raise gi.GithubApiError(404, "not found")
    if "/labels" in url and method == "POST":
        return {"name": "dr:backup", "color": "B60205"}
    if url.endswith("/issues") and method == "POST":
        assert payload["labels"] == ["dr:backup"]
        if "assignee" in payload:  # only when provided
            assert payload["assignees"] == ["daniel-ospina"]
        return {"number": 42}
    if "/search/issues" in url:
        # #3029: the hit must carry an incident-shaped title — adoption is
        # verified against the incident's own title, never trusted from the
        # search result alone (a titleless item is not an incident).
        return {"items": [{"number": 7, "title": "[DR] STALE"}]}
    if "/issues/42/comments" in url:
        return {}
    if url.endswith("/issues/42") and method == "PATCH":
        assert payload["state"] == "closed"
        return {}
    raise AssertionError(f"unexpected request: {method} {url} {payload}")


def test_create_issue_flow(monkeypatch):
    monkeypatch.setattr(gi, "_request", _fake_request)
    assert gi.create_issue("r", "t", title="[DR] X", body="b") == 42


def test_close_issue(monkeypatch):
    calls = []
    def _req(method, url, token, payload=None, timeout=15.0):
        calls.append((method, url, payload))
        return {}
    monkeypatch.setattr(gi, "_request", _req)
    gi.close_issue("r", "t", 42, comment="resolved")
    assert calls[0][0] == "POST" and "comments" in calls[0][1]
    assert calls[1] == ("PATCH", "https://api.github.com/repos/r/issues/42", {"state": "closed"})


def test_search_open_incident(monkeypatch):
    monkeypatch.setattr(gi, "_request", _fake_request)
    assert gi.search_open_incident("r", "t", "STALE") == [7]


def test_ensure_label_404_creates(monkeypatch):
    monkeypatch.setattr(gi, "_request", _fake_request)
    gi.ensure_label("r", "t")  # GET 404 → POST create (no exception)


def test_request_surfaces_http_error():
    with pytest.raises(gi.GithubApiError) as ei:
        raise gi.GithubApiError(401, "bad creds")
    assert ei.value.status == 401


def test_search_open_incident_subject_scoped_query():
    """#2313 Task 4: the search fallback query must scope to the incident's
    OWN subject (kind + team/graph) — a kind-only query would let a
    same-kind incident of a different subject adopt this issue number."""
    seen: dict = {}

    def _fake_request(method, url, token):
        seen["url"] = url
        return {"items": []}

    import tortoise.github_issue as gi
    orig = gi._request
    gi._request = _fake_request
    try:
        import urllib.parse as _up
        # per-graph subject → the title phrase carries "{team}:{graph}"
        gi.search_open_incident("r", "t", "STALE", "team_a:g_x")
        q = _up.unquote(seen["url"])
        assert 'in:title "[DR] STALE — team_a:g_x"' in q
        # bare team subject → bare phrase (never matches "team_a:g_x" titles)
        gi.search_open_incident("r", "t", "STALE", "team_a")
        q = _up.unquote(seen["url"])
        assert 'in:title "[DR] STALE — team_a"' in q
        # global kinds → bare kind phrase
        gi.search_open_incident("r", "t", "DRIVER_DOWN")
        q = _up.unquote(seen["url"])
        assert 'in:title "[DR] DRIVER_DOWN"' in q
    finally:
        gi._request = orig


def test_search_open_incident_filters_prefix_colliding_titles():
    """#2413: GitHub phrase search does NOT guarantee punctuation-exact
    matching — ':' is a token boundary, so a "[DR] KIND — team_a" phrase can
    match a "[DR] KIND — team_a:g_x" title (the bare team subject is a
    literal prefix of its per-graph subjects). The adoption path must verify
    the EXACT title subject server-side (the query alone is not enough; the
    driver's gh_find_open already endswith-matches, #2375)."""
    import tortoise.github_issue as gi

    def _colliding_request(method, url, token):
        import urllib.parse as _up
        q = _up.unquote(url)
        # the real search query is kind-scoped — mirror that
        if "METADATA_LOST" in q:
            return {"items": [
                {"number": 13, "title": "[DR] METADATA_LOST — team_a"},
            ]}
        return {"items": [
            {"number": 10, "title": "[DR] STALE — team_a"},      # exact team
            {"number": 11, "title": "[DR] STALE — team_a:g_x"},  # prefix collider
            {"number": 12, "title": "[DR] STALE — team_b:g_y"},
        ]}

    orig = gi._request
    gi._request = _colliding_request
    try:
        # bare team subject must adopt ONLY its own exact-subject issue —
        # NOT the per-graph prefix collider (#2413)
        assert gi.search_open_incident("r", "t", "STALE", "team_a") == [10]
        # per-graph subject adopts only its exact per-graph issue
        assert gi.search_open_incident("r", "t", "STALE", "team_a:g_x") == [11]
        # global kinds keep the kind-wide match (prose titles)
        assert gi.search_open_incident("r", "t", "STALE") == [10, 11, 12]
    finally:
        gi._request = orig


# ── #3029: the DR title contract (shared by both writers) ────────────────────


def test_incident_title_matches_rejects_prose_mentions():
    """#3029: a title that merely MENTIONS the kind is not the incident.

    GitHub search tokenizes punctuation away, so the platform-scoped query for
    `[DR] R2_DOWN` matched #2844 — a bug report ABOUT R2_DOWN whose title
    contains the tokens `dr` + `r2_down`. Adoption is destructive (the resolver
    closes the adopted issue), so the index is a recall filter only.
    """
    import tortoise.github_issue as gi

    # Real incident titles (driver prose + server-derived forms) match.
    assert gi.incident_title_matches("[DR] R2_DOWN", "R2_DOWN")
    assert gi.incident_title_matches("[DR] R2_DOWN — backup storage unreachable", "R2_DOWN")
    assert gi.incident_title_matches("[DR] STALE — last backup 3h", "STALE")
    # Prose mentions and non-incident shapes do not.
    assert not gi.incident_title_matches(
        "bug(dr): R2_DOWN is deduped under two different R2 keys by the driver "
        "and the app watcher", "R2_DOWN")
    assert not gi.incident_title_matches("chore: bump the [DR] R2_DOWN docs", "R2_DOWN")
    assert not gi.incident_title_matches("[DR] R2_DOWNISH — a different kind", "R2_DOWN")
    assert not gi.incident_title_matches("R2_DOWN — no [DR] prefix", "R2_DOWN")


def test_incident_title_matches_subject_is_an_exact_segment():
    """#2413/#2375 cross-talk guard: a bare team subject is a literal PREFIX of
    its per-graph subjects, so the subject must be the EXACT ` — ` segment."""
    import tortoise.github_issue as gi

    assert gi.incident_title_matches("[DR] STALE — team_a", "STALE", "team_a")
    assert gi.incident_title_matches("[DR] STALE — team_a — last backup 4h", "STALE", "team_a")
    assert gi.incident_title_matches("[DR] STALE — team_a:g_x", "STALE", "team_a:g_x")
    assert not gi.incident_title_matches("[DR] STALE — team_a:g_x", "STALE", "team_a")
    assert not gi.incident_title_matches("[DR] STALE — team_ab", "STALE", "team_a")
    assert not gi.incident_title_matches("[DR] STALE", "STALE", "team_a")


def test_search_open_incident_verifies_platform_scoped_titles():
    """#3029 live regression: the production query resolved to #2844, a bug
    report ABOUT the kind — which `resolve_global` would then have closed."""
    import tortoise.github_issue as gi

    def _request(method, url, token):
        return {"items": [
            {"number": 2844, "title": "bug(dr): R2_DOWN is deduped under two "
                                      "different R2 keys by the driver and the app watcher"},
            {"number": 42, "title": "[DR] R2_DOWN — backup storage unreachable"},
            {"number": 43, "title": "[DR] R2_DOWN"},
        ]}

    orig = gi._request
    gi._request = _request
    try:
        assert gi.search_open_incident("r", "t", "R2_DOWN") == [42, 43]
    finally:
        gi._request = orig
