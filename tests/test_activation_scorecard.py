"""#B7 — the activation scorecard (``GET /v1/activation/scorecard``).

The scorecard's whole reason to exist is a rule that is easy to get wrong:

    a ZERO is only ever emitted with ``state == "measured"``.

An unreadable graph, an unreachable analytics store, an unconfigured writer, a
truncating page cap, or an org that has never produced memory must all read as
``unavailable`` / ``not_measurable`` — never as a confident 0. A scorecard that
cannot say "unmeasured" reproduces the exact bug it was built to fix (see
``tortoise/activation_scorecard.py``).

Four of these tests pin four ALTERNATIVE Cypher shapes that were measured as
broken on BOTH the embedded FalkorDBLite lane and the Docker FalkorDB lane, and
that all failed SILENTLY. They are pinned here so a future "simplification"
cannot reintroduce them:

  * ``test_window_predicate_is_not_absorbed_into_the_optional_match``
  * ``test_counts_are_distinct_not_cross_product``
  * ``test_event_only_session_survives_and_is_not_memory``
  * ``test_zero_point_session_is_not_memory_produced``
"""
from __future__ import annotations

import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tests._http_fixtures import patched_tortoise_sdk
from tortoise.hosted_api import (
    app,
    get_current_org_session_ungated,
)

ORG = {
    "org_id": "org-b7-activation",
    "key_id": "test-key-b7",
    "tier": "free",
    "graph_id": None,
    "scopes": [],
    "legacy_full_access": True,
    "delegation_depth": None,
    "created_by_key_id": None,
    "max_users": 1,
    "max_graphs": 1,
    "max_points": 10000,
    "max_api_keys": 2,
    "max_sessions": 1000,
}

WINDOW = {"since": "2026-09-16T00:00:00Z", "until": "2026-09-17T00:00:00Z"}


def _seed(sid: str, created_at: str, events: int, claims: int) -> None:
    """Seed one session into the ORG's graph — the same namespace the endpoint
    reads (``_data_sdk(org)`` -> ``_make_sdk(namespace=org_id)``). Seeding the
    process default graph instead would write to a different graph and the
    scorecard would legitimately report 0."""
    import tortoise.hosted_api as ha
    proj = ha._make_sdk(namespace=ORG["org_id"])._get_proj()
    proj.g.query("MERGE (s:Session {id:$i}) SET s.created_at=$c",
                 params={"i": sid, "c": created_at})
    for n in range(events):
        tid = f"{sid}_t{n}"
        proj.g.query("MERGE (t:Point {id:$i}) SET t.pointKind='event'",
                     params={"i": tid})
        proj.g.query(
            "MATCH (s:Session {id:$i}),(t:Point {id:$t}) MERGE (s)-[:CONTAINS]->(t)",
            params={"i": sid, "t": tid})
    for n in range(claims):
        pid = f"{sid}_p{n}"
        proj.g.query("MERGE (p:Point {id:$i}) SET p.pointKind='statement'",
                     params={"i": pid})
        proj.g.query(
            "MATCH (s:Session {id:$i}),(p:Point {id:$p}) MERGE (s)-[:CONTAINS]->(p)",
            params={"i": sid, "p": pid})


_UNSET = object()


def _stub_graph(monkeypatch, *, created_at="2026-09-16T01:00:00+00:00",
                turn_points=2, extracted=1, sessions=1,
                first_memory_at=_UNSET) -> None:
    """Bind the endpoint's graph leg to EXPLICIT rows, bypassing the graph.

    The recall/stage-4 tests are about the ANALYTICS leg; they only need the
    graph leg to report memory so stage 4 runs at all. Seeding the shared
    embedded DB for that made them inherit the #1497/#1950/#2090 redislite
    keepalive-anchor flake (non-green roughly one run in four under load,
    failing as `not_measurable / no_memory_produced_in_lifetime`). The tests
    that exercise the real Cypher still seed — this is only for the ones whose
    subject is elsewhere.

    ``first_memory_at`` models the lifetime row and defaults to
    ``created_at``. It is kept separate from the funnel rows because the two
    shapes are different questions: ``first_memory_at=None`` gives (NULL,
    positive count) — memory exists, its timestamp is missing — which must read
    as ``unavailable``/``first_memory_at_missing``, never "never produced".

    (A NULL-``created_at`` funnel row is not a shape this helper should produce
    either — ``FUNNEL_QUERY``'s range predicate excludes NULL — but note it is
    NOT impossible: the module's measured failure mode 1 is that predicate being
    absorbed into the ``OPTIONAL MATCH``, and that is exactly the case
    ``stage_counts``' Python re-check exists to catch. Do not "tidy away" the
    NULL handling on the strength of this helper.)

    ``extracted=0`` means the org has produced no memory at all, so the
    lifetime query returns nothing (a distinct, load-bearing state).
    """
    import tortoise.hosted_api as ha
    from tortoise.activation_scorecard import FUNNEL_QUERY as _FUNNEL

    rows = [(f"s{i}", created_at, turn_points, extracted)
            for i in range(sessions)]
    first = created_at if first_memory_at is _UNSET else first_memory_at
    life = [(first, sessions)] if extracted >= 1 else []

    class _Res:
        def __init__(self, r):
            self.result_set = r

    class _G:
        def query(self, q, params=None):
            return _Res(rows if q is _FUNNEL else life)

    class _SDK:
        def _get_proj(self):
            class _Proj:
                g = _G()
            return _Proj()

    monkeypatch.setattr(ha, "_data_sdk", lambda org: _SDK())


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Authed TestClient over a temp embedded graph, with the analytics store
    UNSET by default (so stage 4 is deterministically ``unavailable``).

    Uses pytest's ``tmp_path`` + ``monkeypatch.setenv`` — the
    ``test_action_endpoints_dual_auth`` idiom — rather than a manual
    ``TemporaryDirectory``. The manual form tears the directory down BEFORE
    redislite's atexit shutdown runs, which emits ``AttributeError: 'Redis'
    object has no attribute 'connection_pool'`` teardown noise and
    intermittently an OSError under load.
    """
    db_path = str(tmp_path / "b7.db")
    app.dependency_overrides[get_current_org_session_ungated] = lambda: dict(ORG)
    monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
                "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)
    with patched_tortoise_sdk(db_path):
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            app.dependency_overrides.clear()


def _stages(resp) -> dict:
    assert resp.status_code == 200, resp.text
    return resp.json()["stages"]


# ── The four measured broken shapes ────────────────────────────────────────

def test_window_predicate_is_not_absorbed_into_the_optional_match(client):
    """Broken shape 1: a time window placed in the ``WHERE`` after an
    ``OPTIONAL MATCH`` is absorbed into the optional pattern and silently
    ignored — the query returns EVERY session ever, with out-of-window rows
    reading as 0. The window must sit directly after ``MATCH``."""
    from tortoise.sdk import TortoiseSDK  # noqa: F401
    _seed("in-window", "2026-09-16T10:00:00+00:00", 2, 1)
    _seed("before-window", "2026-09-15T10:00:00+00:00", 2, 1)
    _seed("after-window", "2026-09-18T10:00:00+00:00", 2, 1)

    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stages = body["stages"]
    assert stages["captured"]["value"] == 1, body
    assert stages["memory_produced"]["value"] == 1, body
    assert body["integrity"] == []
    assert "window_predicate_not_applied" not in body["integrity"]


def test_counts_are_distinct_not_cross_product(client):
    """Broken shape 2: ``count(p)`` across two optional matches inflates via
    the cross-product (3 turns x 2 claims would report 6)."""
    from tortoise.sdk import TortoiseSDK  # noqa: F401
    _seed("s-cross", "2026-09-16T10:00:00+00:00", 3, 2)

    detail = client.get("/v1/activation/scorecard", params=WINDOW).json()["detail"]
    assert detail["turn_points_total"] == 3, detail
    assert detail["extracted_points_total"] == 2, detail


def test_event_only_session_survives_and_is_not_memory(client):
    """Broken shape 3: an interposed ``WITH s, p WHERE <optional predicate>``
    DELETES the sessions that have only turn points — exactly the population
    this scorecard exists to count (the measured production shape: stored
    transcript, zero extraction)."""
    from tortoise.sdk import TortoiseSDK  # noqa: F401
    _seed("events-only", "2026-09-16T10:00:00+00:00", 4, 0)

    stages = _stages(client.get("/v1/activation/scorecard", params=WINDOW))
    assert stages["captured"]["value"] == 1, stages
    assert stages["stored"]["value"] == 1, stages
    assert stages["memory_produced"]["value"] == 0, stages
    assert stages["memory_produced"]["state"] == "measured"


def test_zero_point_session_is_not_memory_produced(client):
    """Broken shape 4: ``sum(CASE WHEN p.pointKind IS NULL OR ... THEN 1 ELSE 0
    END)`` counts a session with NO points as ``memory_produced``, because a
    null ``p`` makes ``p.pointKind IS NULL`` true."""
    from tortoise.sdk import TortoiseSDK  # noqa: F401
    _seed("bare", "2026-09-16T10:00:00+00:00", 0, 0)

    stages = _stages(client.get("/v1/activation/scorecard", params=WINDOW))
    assert stages["captured"]["value"] == 1, stages
    assert stages["stored"]["value"] == 0, stages
    assert stages["memory_produced"]["value"] == 0, stages


# ── The window guard must fail CLOSED for every row shape ─────────────────

def test_unparseable_created_at_refuses_the_count():
    """A timestamp the guard cannot place on the timeline must NOT be counted.
    Keeping the row asserts it is in-window with no evidence — a plausible
    wrong number, which is the exact failure the guard exists to prevent."""
    from tortoise.activation_scorecard import normalize_window, stage_counts
    since, until = normalize_window(WINDOW["since"], WINDOW["until"])
    stages, detail = stage_counts([("s1", "not-a-date", 2, 1)], since, until)
    assert stages["captured"]["state"] == "unavailable", stages
    assert stages["captured"]["value"] is None, stages
    assert stages["stored"]["value"] is None, stages
    assert "unparseable_created_at" in detail["integrity"], detail


def test_datetime_stamps_are_window_checked_not_counted_blind():
    """A driver returning a native datetime must not bypass the guard. The
    check previously ran only when the stamp was a ``str``, so a datetime was
    counted as in-window without ever being examined."""
    from datetime import UTC, datetime

    from tortoise.activation_scorecard import normalize_window, stage_counts
    since, until = normalize_window(WINDOW["since"], WINDOW["until"])

    stages, detail = stage_counts(
        [("s1", datetime(2026, 9, 16, 12, tzinfo=UTC), 2, 1)], since, until)
    assert stages["captured"]["value"] == 1, stages
    assert detail["integrity"] == [], detail

    stages, detail = stage_counts(
        [("s1", datetime(2020, 1, 1, tzinfo=UTC), 2, 1)], since, until)
    assert stages["captured"]["state"] == "unavailable", stages
    assert stages["captured"]["value"] is None, stages
    assert "window_predicate_not_applied" in detail["integrity"], detail


def test_naive_datetime_is_refused_rather_than_assumed_utc():
    """Assuming a zone would silently shift the window — the bug the whole
    normalizer exists to prevent."""
    from datetime import datetime

    from tortoise.activation_scorecard import normalize_window, stage_counts
    since, until = normalize_window(WINDOW["since"], WINDOW["until"])
    stages, detail = stage_counts(
        [("s1", datetime(2026, 9, 16, 12), 2, 1)], since, until)
    assert stages["captured"]["state"] == "unavailable", stages
    assert "unparseable_created_at" in detail["integrity"], detail


def test_point_with_no_pointkind_is_memory_produced(client):
    """Stage 3's predicate is `pointKind IS NULL OR pointKind <> 'event'`, and
    the NULL branch is the shape extraction actually writes. Without a test for
    it, replacing the predicate with the known-defective
    `pointKind IN ['decision','statement']` (the #3555 bug) passed the whole
    suite — green-lighting the exact defect the code documents."""
    import tortoise.hosted_api as ha
    proj = ha._make_sdk(namespace=ORG["org_id"])._get_proj()
    proj.g.query("MERGE (s:Session {id:'nullkind'}) SET s.created_at=$c",
                 params={"c": "2026-09-16T10:00:00+00:00"})
    proj.g.query("MERGE (t:Point {id:'nullkind_t'}) SET t.pointKind='event'")
    proj.g.query(
        "MATCH (s:Session {id:'nullkind'}),(t:Point {id:'nullkind_t'}) "
        "MERGE (s)-[:CONTAINS]->(t)")
    # A memory point with NO pointKind at all — the production shape.
    proj.g.query("MERGE (p:Point {id:'nullkind_p'})")
    proj.g.query(
        "MATCH (s:Session {id:'nullkind'}),(p:Point {id:'nullkind_p'}) "
        "MERGE (s)-[:CONTAINS]->(p)")

    stages = _stages(client.get("/v1/activation/scorecard", params=WINDOW))
    assert stages["captured"]["value"] == 1, stages
    assert stages["stored"]["value"] == 1, stages
    assert stages["memory_produced"]["value"] == 1, stages


# ── Stage 4: conditioning, allowlist, and the zero/no-signal rule ──────────

def test_recall_is_conditioned_on_lifetime_memory(client, monkeypatch):
    """Stage 4 counts only retrievals AFTER the org first produced memory. A
    pre-memory retrieval cannot have been answered from memory. The
    unconditioned count stays visible as ``recall_attempted_any``."""
    import tortoise.supabase_control as sc
    from tortoise.sdk import TortoiseSDK  # noqa: F401  (namespace check)
    _stub_graph(monkeypatch, created_at="2026-09-16T12:00:00+00:00", turn_points=1, extracted=1)  # first memory at 12:00

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    rows = [
        {"event_name": "mcp_tool_call", "created_at": "2026-09-16T11:00:00+00:00",
         "properties": {"tool_name": "tortoise_search"}},   # before memory
        {"event_name": "mcp_tool_call", "created_at": "2026-09-16T13:00:00+00:00",
         "properties": {"tool_name": "tortoise_search"}},   # after memory
        {"event_name": "mcp_tool_call", "created_at": "2026-09-16T13:05:00+00:00",
         "properties": {"tool_name": "tortoise_health"}},   # not a retrieval tool
    ]

    class _CP:
        def query(self, *a, **kw):
            return rows

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["state"] == "measured", body
    assert stage["value"] == 1, stage
    assert body["detail"]["recall_attempted_any"] == 2, body["detail"]


def test_recall_measured_zero_is_reported_as_zero(client, monkeypatch):
    """The suite must exercise the TRUE-ZERO direction too, not only the
    false-zero direction. Patching `recall_stages` to refuse every zero passed
    all prior tests, so a regression that treated a real zero as unmeasurable
    (hiding the actual finding) would have shipped."""
    import tortoise.supabase_control as sc
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00", turn_points=2, extracted=1)  # memory produced

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    class _CP:
        def query(self, *a, **kw):
            # A tool call, but not a RETRIEVAL tool — so no recall happened.
            return [{"properties": {"tool_name": "tortoise_health"},
                     "created_at": "2026-09-16T13:00:00+00:00"}]

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["state"] == "measured", stage
    assert stage["value"] == 0, stage
    assert stage["reason"] is None, stage


def test_analytics_leg_interval_matches_the_graph_legs(client, monkeypatch):
    """The graph legs and the analytics leg are SEPARATE queries, so their
    windows can drift. Both must be [since, until): a boundary instant has to
    be treated identically by each, or the funnel disagrees with itself. This
    regressed once — the analytics leg used `gt`, so a tool call landing
    exactly ON `since` was dropped while a session created at that same instant
    was counted (review cycle 5).

    The graph is STUBBED rather than seeded: the subject here is the analytics
    query's bounds, and the graph leg is what makes the analytics leg run at
    all. Seeding it made the test depend on the shared embedded-DB fixture and
    it flaked (~2/9 under load, the #1497/#1950/#2090 anchor class).
    """
    import tortoise.hosted_api as ha
    import tortoise.supabase_control as sc
    from tortoise.activation_scorecard import FUNNEL_QUERY as _FUNNEL

    class _Res:
        def __init__(self, rows):
            self.result_set = rows

    class _G:
        def query(self, q, params=None):
            if q is _FUNNEL:
                return _Res([("s1", "2026-09-16T01:00:00+00:00", 2, 1)])
            # LIFETIME_MEMORY_QUERY: (first_memory_at, sessions_with_memory)
            return _Res([("2026-09-16T01:00:00+00:00", 1)])

    class _SDK:
        def _get_proj(self):
            class _Proj:
                g = _G()
            return _Proj()

    monkeypatch.setattr(ha, "_data_sdk", lambda org: _SDK())

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    # The read path makes ONE `get_control_plane().query(...)` call — narrow and
    # wide are two in-memory passes over those same rows, not two queries — but
    # collect every call anyway rather than let a single overwritten dict make
    # the assertion depend on incidental call order.
    #
    # ⚠️ NOT every call to the control plane comes from this test's subject. The
    # app's lifespan starts background boot sweeps on a daemon thread
    # (`_sweep_events` -> `_iter_registered_orgs` -> `query("organizations", …)`,
    # plus `_purge_deleted_orgs`), and `_sweep_events` tests `is_supabase_enabled()`
    # at RUN time — so once this test sets the Supabase env, a sweep that happens
    # to land in the test body adds a window-less call. Selecting on the table AND
    # on the presence of a `created_at` bound keeps background traffic out
    # (review cycle 7: this made the test intermittently red).
    calls: list[tuple[str, dict]] = []

    class _CP:
        def query(self, table, **kw):
            calls.append((table, kw))
            return []

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    windowed = [kw for table, kw in calls
                if table == "analytics_events"
                and any(col == "created_at" for col, _, _ in kw["filters"])]
    assert windowed, (f"no windowed analytics query was made; calls="
                      f"{[(t, [c for c, _, _ in k['filters']]) for t, k in calls]}")
    windows = {
        tuple((op, v) for col, op, v in kw["filters"] if col == "created_at")
        for kw in windowed
    }
    # Every windowed analytics query carries exactly one half-open [since, until).
    assert windows == {(("gte", body["window"]["since"]),
                        ("lt", body["window"]["until"]))}, windows
    assert body["stages"]["recall_attempted"]["state"] == "measured", body


def test_graph_legs_window_operators_are_half_open():
    """F9 (review cycle 6): the analytics leg's bounds were pinned by a test,
    but the GRAPH legs' were not — nothing asserted that `FUNNEL_QUERY` uses
    `>=` on `since` and `<` on `until`. The runbook calls the matching
    intervals a "guarantee"; for the graph side that was only an assertion in
    prose. Pin it, so a later `>=`→`>` edit fails a test instead of silently
    dropping a session created exactly on the boundary."""
    from tortoise.activation_scorecard import FUNNEL_QUERY, LIFETIME_MEMORY_QUERY

    assert "s.created_at >= $since" in FUNNEL_QUERY, FUNNEL_QUERY
    assert "s.created_at < $until" in FUNNEL_QUERY, FUNNEL_QUERY
    # ...and the lifetime query is window-free on purpose (it is the baseline
    # stage 4 conditions on, so restricting it to the window would defeat it).
    assert "$since" not in LIFETIME_MEMORY_QUERY, LIFETIME_MEMORY_QUERY
    assert "$until" not in LIFETIME_MEMORY_QUERY, LIFETIME_MEMORY_QUERY


def test_window_boundaries_match_the_analytics_leg():
    """The other half: a Session exactly ON `since` counts, exactly on `until`
    does not — the same half-open rule the analytics leg now uses."""
    from tortoise.activation_scorecard import stage_counts

    since, until = "2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00"
    # Exactly ON `since` — INCLUDED (`>=`): the leg is closed on the left.
    stages, _ = stage_counts([("at-since", since, 1, 1)], since, until)
    assert stages["captured"]["state"] == "measured", stages
    assert stages["captured"]["value"] == 1, stages
    # Exactly ON `until` — EXCLUDED (`<`): open on the right. And because ANY
    # out-of-window row withholds ALL THREE graph stages (fail closed), a
    # session on `until` is not "counted as 0" — the number is refused.
    stages, detail = stage_counts([("at-until", until, 1, 1)], since, until)
    assert stages["captured"]["state"] == "unavailable", stages
    assert stages["captured"]["value"] is None, stages
    assert stages["captured"]["reason"] == "window_predicate_not_applied", stages
    assert "window_predicate_not_applied" in detail["integrity"], detail
    # One bad row poisons the batch rather than silently adjusting the count.
    stages, _ = stage_counts([("at-since", since, 1, 1),
                              ("at-until", until, 1, 1)], since, until)
    assert stages["captured"]["value"] is None, stages


def test_first_memory_at_unparseable_is_unavailable():
    """A lifetime row whose min(created_at) will not parse must refuse, not
    fall back to a lower bound."""
    from tortoise.activation_scorecard import recall_stages
    stage, detail = recall_stages([], "not-a-date", memory_sessions=1)
    assert stage["state"] == "unavailable", stage
    assert stage["value"] is None, stage
    assert stage["reason"] == "first_memory_at_unparseable", stage
    assert detail["recall_rows_fetched"] == 0, detail


def test_truncated_page_does_not_mask_a_no_memory_org():
    """The no-lifetime-memory fact comes from the GRAPH, so it is independent of
    how many analytics rows were fetched. Checking truncation first reported an
    org with no memory as `unavailable` (a recoverable reporting failure) —
    putting it in a cohort's `orgs_unavailable` list."""
    from tortoise.activation_scorecard import recall_stages
    stage, _ = recall_stages([{}] * 1000, None, truncated=True, memory_sessions=0)
    assert stage["state"] == "not_measurable", stage
    assert stage["reason"] == "no_memory_produced_in_lifetime", stage
    # ...but truncation still refuses when the org DOES have memory.
    stage, _ = recall_stages([{}] * 1000, "2026-09-16T01:00:00+00:00",
                             truncated=True, memory_sessions=1)
    # `state` is the load-bearing field — `not_measurable` and `unavailable`
    # are classified differently downstream. Omitting it let a production
    # change to `STATE_NOT_MEASURABLE` pass all 58 tests (review cycle 6).
    assert stage["state"] == "unavailable", stage
    assert stage["reason"] == "analytics_page_cap_truncated", stage


def test_properties_as_a_json_string_is_counted(client, monkeypatch):
    """PostgREST can hand `properties` back as a JSON-encoded string. The
    `isinstance(props, str)` branch was never exercised."""
    import tortoise.supabase_control as sc
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00", turn_points=2, extracted=1)

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    class _CP:
        def query(self, *a, **kw):
            return [{"properties": '{"tool_name": "tortoise_recall"}',
                     "created_at": "2026-09-16T13:00:00+00:00"}]

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    stage = client.get("/v1/activation/scorecard", params=WINDOW).json()[
        "stages"]["recall_attempted"]
    assert stage["state"] == "measured", stage
    assert stage["value"] == 1, stage


def test_unparseable_analytics_timestamp_refuses_the_recall_count(client, monkeypatch):
    """An allowlisted retrieval call whose timestamp will not parse cannot be
    placed relative to ``first_memory_at``, so it is excluded from the count —
    which makes the count a LOWER BOUND. Reporting a lower bound as
    ``measured`` is the same class of bug as counting an unverifiable row in
    the window guard, and is refused for the same reason."""
    import tortoise.supabase_control as sc
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00", turn_points=2, extracted=1)  # memory produced

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    rows = [
        {"event_name": "mcp_tool_call", "created_at": "2026-09-16T13:00:00+00:00",
         "properties": {"tool_name": "tortoise_search"}},
        {"event_name": "mcp_tool_call", "created_at": "not-a-timestamp",
         "properties": {"tool_name": "tortoise_recall"}},
    ]

    class _CP:
        def query(self, *a, **kw):
            return rows

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["state"] == "unavailable", stage
    assert stage["value"] is None, stage
    assert stage["reason"] == "unparseable_analytic_rows", stage
    # The doubt is still visible in detail rather than swallowed.
    assert body["detail"]["unparseable_analytic_rows"] == 1, body["detail"]


def test_unclassifiable_analytic_row_refuses_the_recall_count(client, monkeypatch):
    """The store holds ``mcp_tool_call`` rows only (the read filters on
    ``event_name``), so a call whose ``tool_name`` cannot be read is a call we
    cannot rule OUT of the retrieval set. Dropping it silently would make the
    count a lower bound that looks exact."""
    import tortoise.supabase_control as sc
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00", turn_points=2, extracted=1)  # memory produced

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    rows = [
        {"event_name": "mcp_tool_call", "created_at": "2026-09-16T13:00:00+00:00",
         "properties": {"tool_name": "tortoise_search"}},
        # properties present but carrying no tool_name — unreadable
        {"event_name": "mcp_tool_call", "created_at": "2026-09-16T13:01:00+00:00",
         "properties": {"status": "ok"}},
    ]

    class _CP:
        def query(self, *a, **kw):
            return rows

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["state"] == "unavailable", stage
    assert stage["value"] is None, stage
    assert stage["reason"] == "unclassifiable_analytic_rows", stage
    assert body["detail"]["unclassifiable_analytic_rows"] == 1, body["detail"]


def test_wide_leg_exclusions_are_tracked_too(client, monkeypatch):
    """The wide allowlist is the sensitivity BOUND. A wide-only row with an
    unreadable timestamp must not let the bound under-count silently while the
    stage still reports ``measured``."""
    import tortoise.supabase_control as sc
    from tortoise.activation_scorecard import RETRIEVAL_TOOL_ALLOWLIST_WIDE
    assert "tortoise_query" in RETRIEVAL_TOOL_ALLOWLIST_WIDE
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00", turn_points=2, extracted=1)

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    rows = [{"event_name": "mcp_tool_call", "created_at": "not-a-timestamp",
             "properties": {"tool_name": "tortoise_query"}}]

    class _CP:
        def query(self, *a, **kw):
            return rows

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    assert body["stages"]["recall_attempted"]["state"] == "unavailable", body
    assert body["detail"]["unparseable_analytic_rows_wide"] == 1, body["detail"]
    # The reason must name the counter that is actually dirty: a wide-only row
    # is skipped by the NARROW pass, so `unparseable_analytic_rows` reads 0 and
    # reporting the exclusion under that name would contradict it.
    assert body["detail"]["unparseable_analytic_rows"] == 0, body["detail"]
    assert (body["stages"]["recall_attempted"]["reason"]
            == "unparseable_analytic_rows_wide"), body["stages"]
    # A refused count is never published as a lower bound.
    assert body["detail"]["recall_attempted_after_memory"] is None, body["detail"]


def test_retrieval_allowlist_is_pinned_by_name_and_cannot_be_silently_widened():
    """The allowlist must be pinned BY NAME, never derived from
    ``readOnlyHint``/``_ro()`` — that covers 52 tools including
    ``tortoise_health``, ``tortoise_status`` and every ``tortoise_list_*``,
    which agents call automatically at connect time (which would mark an org
    activated on connection). Also pins that every name still EXISTS, so a
    tool rename cannot silently change the number."""
    from tortoise.activation_scorecard import (
        RETRIEVAL_TOOL_ALLOWLIST,
        RETRIEVAL_TOOL_ALLOWLIST_WIDE,
    )
    from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY

    # Every name must still RESOLVE — live, or retired through the #3883 warning
    # shim. A retired name is deliberately KEPT in the allowlist: it still answers
    # and still emits `mcp_tool_call`, and the retirement warning's purpose is to
    # MEASURE who still calls the old name. Dropping it here would stop counting
    # exactly the legacy traffic the retirement is meant to expose.
    names = {t.name for t in (*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY)}
    assert names >= RETRIEVAL_TOOL_ALLOWLIST, RETRIEVAL_TOOL_ALLOWLIST - names
    assert names >= RETRIEVAL_TOOL_ALLOWLIST_WIDE, RETRIEVAL_TOOL_ALLOWLIST_WIDE - names

    # `tortoise_ask` is deliberately NOT allowlisted: it is eval-only (#3849,
    # surface removed by #3929) and the scorecard must not count ask traffic.
    assert {
        "tortoise_search", "tortoise_recall",
        "tortoise_search_sessions"} == RETRIEVAL_TOOL_ALLOWLIST
    assert "tortoise_ask" not in RETRIEVAL_TOOL_ALLOWLIST
    for connect_time in ("tortoise_health", "tortoise_status",
                         "tortoise_list_pointkinds", "tortoise_overview",
                         "tortoise_session_context"):
        assert connect_time not in RETRIEVAL_TOOL_ALLOWLIST


def test_graph_unavailable_is_unavailable_never_zero(client, monkeypatch):
    """An unreadable graph and an empty graph are different facts."""
    import tortoise.hosted_api as ha

    def _boom(_org):
        raise RuntimeError("graph down")

    monkeypatch.setattr(ha, "_data_sdk", _boom)
    resp = client.get("/v1/activation/scorecard", params=WINDOW)
    assert resp.status_code == 200, resp.text
    stages = resp.json()["stages"]
    for name in ("captured", "stored", "memory_produced"):
        assert stages[name]["state"] == "unavailable", stages
        assert stages[name]["value"] is None, stages
        assert stages[name]["reason"] == "org_graph_unavailable"
    assert resp.json()["stages"]["recall_attempted"]["value"] is None
    # The doubt must reach the RESPONSE surface, not just detail. A regression
    # that always returned integrity: [] would otherwise pass every test.
    assert "org_graph_unavailable" in resp.json()["integrity"], resp.json()


def test_memory_without_transcript_flagged_at_the_unit_level():
    """Turn points are written BEFORE extraction, so a session with memory and
    ZERO event points is impossible in production. If it appears, the funnel's
    premise is broken and the flag must surface — a regression that dropped it
    would otherwise be silent.

    Unit-level on purpose: an earlier version seeded the graph through the app
    fixture and was FLAKY (`integrity: []` on a run where the seeded rows were
    not visible in the fixture's shared temp DB — the #1497/#1950/#2090
    keepalive-anchor class, caught by review cycle 3's VGATE). The transform is
    pure, so it is pinned where it is deterministic.
    """
    from tortoise.activation_scorecard import stage_counts

    since, until = "2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00"
    # (session_id, created_at, turn_points, extracted) — memory but no turns.
    stages, detail = stage_counts([("inv", "2026-09-16T10:00:00+00:00", 0, 1)],
                                  since, until)
    assert "memory_without_transcript" in detail["integrity"], detail
    # The counts still stand — the row is in-window; the flag carries the doubt.
    assert stages["captured"]["state"] == "measured", stages
    assert stages["captured"]["value"] == 1, stages
    # `stored` counts sessions with a TRANSCRIPT (turn points), `memory_produced`
    # counts sessions with memory — so this row IS the inversion: memory with no
    # transcript. That is the anomaly, stated explicitly rather than assumed.
    assert stages["stored"]["value"] == 0, stages
    assert stages["memory_produced"]["value"] == 1, stages
    # And it is NOT a window failure, so no stage is withheld.
    assert stages["memory_produced"]["reason"] is None, stages

    # The healthy shape must NOT raise the flag, or the flag is noise.
    _, clean = stage_counts([("ok", "2026-09-16T10:00:00+00:00", 3, 1)],
                            since, until)
    assert "memory_without_transcript" not in clean["integrity"], clean
    # ...and a transcript-only session (extracted 0) is also not the inversion.
    _, no_mem = stage_counts([("nm", "2026-09-16T10:00:00+00:00", 3, 0)],
                             since, until)
    assert "memory_without_transcript" not in no_mem["integrity"], no_mem


def test_memory_without_transcript_reaches_the_endpoint(client, monkeypatch):
    """The unit test above pins the flag; this pins that it SURVIVES to the
    response surface. Graph rows come from a stubbed `_data_sdk`, so there is
    no shared-DB seeding to race (the flake review cycle 3 caught)."""
    import tortoise.hosted_api as ha
    from tortoise.activation_scorecard import FUNNEL_QUERY as _FUNNEL

    class _Res:
        def __init__(self, rows):
            self.result_set = rows

    class _G:
        def query(self, q, params=None):
            if q is _FUNNEL:
                # (session_id, created_at, turn_points, extracted): memory, no
                # transcript — the impossible inversion.
                return _Res([("inv", "2026-09-16T10:00:00+00:00", 0, 1)])
            return _Res([("2026-09-16T10:00:00+00:00", 1)])

    class _SDK:
        def _get_proj(self):
            class _Proj:
                g = _G()
            return _Proj()

    monkeypatch.setattr(ha, "_data_sdk", lambda org: _SDK())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    assert "memory_without_transcript" in body["integrity"], body["integrity"]
    assert body["stages"]["captured"]["value"] == 1, body
    assert body["stages"]["captured"]["state"] == "measured", body


def test_analytics_unreachable_is_unavailable_never_zero(client, monkeypatch):
    import tortoise.supabase_control as sc

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    class _Down:
        def query(self, *a, **kw):
            raise RuntimeError("supabase down")

    monkeypatch.setattr(sc, "get_control_plane", lambda: _Down())
    stage = client.get("/v1/activation/scorecard",
                       params=WINDOW).json()["stages"]["recall_attempted"]
    assert stage["state"] == "unavailable", stage
    assert stage["value"] is None, stage
    assert stage["reason"] == "analytics_store_unreachable"


def test_analytics_write_path_unset_is_unavailable_never_zero(client):
    """The hosted deployment once provided only ``SUPABASE_SERVICE_ROLE_KEY``
    while the writer read only the legacy name — so every event was dropped to
    ephemeral disk. An unconfigured writer must never read as an observed 0."""
    stage = client.get("/v1/activation/scorecard",
                       params=WINDOW).json()["stages"]["recall_attempted"]
    assert stage["state"] == "unavailable", stage
    assert stage["value"] is None, stage
    assert stage["reason"] == "analytics_write_path_unconfigured"


def test_analytics_page_cap_is_unavailable_never_a_lower_bound(client, monkeypatch):
    """The read helper has no offset/Range, so a full page is a LOWER BOUND.
    A lower bound is not a count."""
    import tortoise.supabase_control as sc
    from tortoise.activation_scorecard import RECALL_PAGE_CAP
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00", turn_points=1, extracted=1)

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    class _Full:
        def query(self, *a, **kw):
            return [{"properties": {"tool_name": "tortoise_search"},
                     "created_at": "2026-09-16T13:00:00+00:00"}] * RECALL_PAGE_CAP

    monkeypatch.setattr(sc, "get_control_plane", lambda: _Full())
    stage = client.get("/v1/activation/scorecard",
                       params=WINDOW).json()["stages"]["recall_attempted"]
    assert stage["value"] is None, stage
    assert stage["state"] == "unavailable", stage
    assert stage["reason"] == "analytics_page_cap_truncated", stage


def test_memory_without_a_timestamp_is_unavailable_not_no_memory(client, monkeypatch):
    """`first_memory_at` is NULL for two opposite reasons. Telling an org that
    HAS memory it has never produced any would be the wrong answer to a real
    question — so a positive memory-Session count with a NULL timestamp is a
    data gap (``unavailable``), not an absence."""
    import tortoise.supabase_control as sc
    # The funnel rows keep a real in-window timestamp (FUNNEL_QUERY cannot
    # return a NULL one); only the LIFETIME value is NULL.
    _stub_graph(monkeypatch, first_memory_at=None, turn_points=2, extracted=1)

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    class _CP:
        def query(self, *a, **kw):
            return []

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["state"] == "unavailable", stage
    assert stage["value"] is None, stage
    assert stage["reason"] == "first_memory_at_missing", stage


def test_no_memory_in_lifetime_means_recall_is_unmeasurable(client, monkeypatch):
    import tortoise.supabase_control as sc
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00", turn_points=2, extracted=0)  # stored, no memory

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    class _CP:
        def query(self, *a, **kw):
            return [{"properties": {"tool_name": "tortoise_search"},
                     "created_at": "2026-09-16T13:00:00+00:00"}]

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["value"] is None, stage
    assert stage["reason"] == "no_memory_produced_in_lifetime", stage
    # `not_measurable`, NOT `unavailable`: the org has never produced memory, so
    # there is nothing to recall FROM and no retry changes that. Asserting the
    # state is what keeps the two vocabularies from collapsing into each other.
    assert stage["state"] == "not_measurable", stage


def test_unavailable_is_reserved_for_the_recoverable_failures(client, monkeypatch):
    """The two non-measured states must stay distinct: `unavailable` = the
    metric exists but could not be read now (retry/repair fixes it);
    `not_measurable` = there is nothing to measure. A store that is merely
    unconfigured is the former."""
    # The graph is deliberately NOT seeded: `_read_recall` checks the write-path
    # configuration BEFORE it looks at the graph, so the subject never depends
    # on it — and seeding kept this test on the shared embedded DB for nothing
    # (review cycle 6, F6, mutation-proven: making `_data_sdk` raise leaves it
    # green).
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
                "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["state"] == "unavailable", stage
    assert stage["reason"] == "analytics_write_path_unconfigured", stage


def test_only_value_confirmed_and_no_lifetime_memory_are_not_measurable(client, monkeypatch):
    """Pin the vocabulary: exactly two of the five stages may report
    `not_measurable` on a healthy run. If a third appears, a recoverable
    failure has been mislabelled as permanent (or vice versa)."""
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    class _CP:
        def query(self, *a, **kw):
            return []

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00", turn_points=2, extracted=0)
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    states = {name: cell["state"] for name, cell in body["stages"].items()}
    assert states["captured"] == "measured", states
    assert states["stored"] == "measured", states
    assert states["memory_produced"] == "measured", states
    assert states["recall_attempted"] == "not_measurable", states
    assert states["value_confirmed"] == "not_measurable", states
    # And the docstring's claim, enforced: a stage with a value is never
    # not_measurable, and a not_measurable stage never carries a value.
    for name, cell in body["stages"].items():
        if cell["state"] == "not_measurable":
            assert cell["value"] is None, (name, cell)
        if cell["value"] is not None:
            assert cell["state"] == "measured", (name, cell)


# ── The refusal: no fabricated activation, at any depth ───────────────────

def test_value_confirmed_is_a_refusal_and_no_activated_key_exists(client):
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    value = body["stages"]["value_confirmed"]
    assert value["state"] == "not_measurable", value
    assert value["value"] is None, value
    assert "not_measurable" in value["reason"]
    assert "result_count" in value["reason"]
    assert "#3518" in value["reason"]

    # No key anywhere claims activation. A surface that emitted an
    # ``activated`` field would reintroduce exactly the mislabelling this lane
    # exists to remove (the dashboard already calls a STORED session "active").
    flat = _flatten_keys(body)
    offenders = [k for k in flat if "activat" in k.lower()]
    assert not offenders, offenders
    assert "value_confirmed" in body["stages"]


def _flatten_keys(obj, out=None):
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(k)
            _flatten_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _flatten_keys(v, out)
    return out


def test_every_stage_carries_a_state_and_reason(client):
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    for name, cell in body["stages"].items():
        assert cell["state"] in {"measured", "unavailable", "not_measurable"}, name
        assert (cell["reason"] is None) == (cell["state"] == "measured"), (name, cell)
        assert body["detail"]["stages_1_3_granularity"] == "session"


def test_mixed_granularity_is_disclosed(client):
    """Stages 1-3 are session-scoped; stage 4 is org/time-scoped (the telemetry
    carries no session_id). The payload must say so."""
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    assert body["detail"]["stage_4_granularity"] == "org_time"
    assert body["detail"]["graph_scope"] == "org_default_graph"
    assert any("session_id" in lim for lim in body["limitations"])


# ── Guards ────────────────────────────────────────────────────────────────

def test_graph_bound_key_is_rejected_before_any_query(client):
    graph_key = dict(ORG, graph_id="g-1", graph_namespace="org_x_g_1")
    app.dependency_overrides[get_current_org_session_ungated] = lambda: graph_key
    resp = client.get("/v1/activation/scorecard", params=WINDOW)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error_code"] == "GRAPH_SCOPED_TEAM_SURFACE"


def test_key_without_graphs_read_scope_is_rejected(client):
    scoped = dict(ORG, key_id="k-1", legacy_full_access=False, scopes=[])
    app.dependency_overrides[get_current_org_session_ungated] = lambda: scoped
    resp = client.get("/v1/activation/scorecard", params=WINDOW)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error_code"] == "INSUFFICIENT_SCOPE"


@pytest.mark.parametrize("params", [
    {"since": "nonsense", "until": "2026-09-17T00:00:00Z"},
    {"since": "2026-09-17T00:00:00Z", "until": "2026-09-16T00:00:00Z"},
    {"since": "2026-09-16T00:00:00", "until": "2026-09-17T00:00:00Z"},  # naive
    {"since": "2020-01-01T00:00:00Z", "until": "2026-09-17T00:00:00Z"},  # >90d
])
def test_bad_window_is_422(client, params):
    assert client.get("/v1/activation/scorecard", params=params).status_code == 422


def test_default_window_is_24h(client):
    """The name has to be true: `since < until` holds for ANY positive span, so
    asserting only that would pass with a 7-day default (mutation-verified)."""
    from datetime import datetime

    from tortoise.activation_scorecard import DEFAULT_WINDOW, MAX_WINDOW

    assert timedelta(hours=24) == DEFAULT_WINDOW
    assert timedelta(days=90) == MAX_WINDOW
    body = client.get("/v1/activation/scorecard").json()
    since = datetime.fromisoformat(body["window"]["since"])
    until = datetime.fromisoformat(body["window"]["until"])
    assert until - since == timedelta(hours=24), (since, until)


# ── The analytics write-path repair ───────────────────────────────────────

def test_analytics_writer_accepts_either_service_key_name(monkeypatch, tmp_path):
    """The hosted deployment provides ``SUPABASE_SERVICE_ROLE_KEY``. Before the
    repair the writer read ONLY the legacy ``SUPABASE_SERVICE_KEY``, found
    nothing, and appended every event to a JSONL file on ephemeral disk — the
    whole funnel surface was dark. Positive test: with only the ROLE key set,
    the Supabase branch is TAKEN."""
    import tortoise.hosted_api as ha

    posted: list = []

    class _Resp:
        status_code = 201

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            posted.append((url, json, headers))
            return _Resp()

    monkeypatch.setattr("httpx.Client", _Client)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "role-key")
    fallback = tmp_path / "analytics_fallback.jsonl"
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", fallback)

    ha._track_analytics_event("org-1", "mcp_tool_call",
                              {"tool_name": "tortoise_search"})

    assert posted, "the Supabase branch was not taken with only the ROLE key set"
    assert posted[0][0].endswith("/rest/v1/analytics_events")
    assert not fallback.exists(), "fell through to the ephemeral JSONL fallback"


def test_analytics_writer_still_falls_back_when_unconfigured(monkeypatch, tmp_path):
    import tortoise.hosted_api as ha

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    fallback = tmp_path / "analytics_fallback.jsonl"
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", fallback)

    ha._track_analytics_event("org-1", "mcp_tool_call", {"tool_name": "x"})
    assert fallback.exists()


def test_analytics_props_allowlist_is_respected(monkeypatch, tmp_path):
    """Unknown props are silently dropped — a new prop must be registered
    before it can ever appear in the store."""
    import tortoise.hosted_api as ha

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    for var in ("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)
    fallback = tmp_path / "analytics_fallback.jsonl"
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", fallback)

    ha._track_analytics_event("org-1", "mcp_tool_call",
                              {"tool_name": "t", "not_registered": "dropped"})
    import json
    event = json.loads(fallback.read_text().strip())
    assert event["properties"] == {"tool_name": "t"}


# ── Cohort roll-up (tools/activation_cohort.py) ──────────────────────────

def _cohort_module():
    import importlib.util
    import pathlib
    path = (pathlib.Path(__file__).resolve().parents[1]
            / "tools" / "activation_cohort.py")
    spec = importlib.util.spec_from_file_location("_b7_activation_cohort", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _payload(captured, stored, memory, recall_state="unavailable",
             recall_value=None, value_confirmed_state="not_measurable"):
    return {"stages": {
        "captured": {"state": "measured", "value": captured},
        "stored": {"state": "measured", "value": stored},
        "memory_produced": {"state": "measured", "value": memory},
        "recall_attempted": {"state": recall_state, "value": recall_value},
        "value_confirmed": {"state": value_confirmed_state, "value": None},
    }}


def test_cohort_does_not_call_a_not_measurable_stage_a_reporting_failure():
    """F1. ``value_confirmed`` is ``not_measurable`` for EVERY org by design.
    Collapsing that into ``unavailable`` and listing the orgs as unreadable
    would assert that healthy orgs failed to report — the state inversion this
    lane exists to prevent. The cohort stage must be ``not_measurable``, with no
    org accused."""
    mod = _cohort_module()
    report = mod.roll_up(["a", "b"], [_payload(5, 5, 1), _payload(7, 6, 0)])
    cell = report["stages"]["value_confirmed"]
    assert cell["state"] == "not_measurable", cell
    assert cell["value"] is None, cell
    assert cell["orgs_unavailable"] == [], cell
    assert cell["orgs_not_measurable"] == ["a", "b"], cell
    # The healthy _measured_ stages must be untouched by that.
    assert report["stages"]["captured"]["state"] == "measured", report


def test_cohort_partial_when_some_orgs_measured_and_others_had_nothing():
    """Mixed cohort: one org measured recall, the other has never produced
    memory (so genuinely has nothing to measure). That is `partial`, and the
    org with nothing to measure is NOT accused of failing to report."""
    mod = _cohort_module()
    report = mod.roll_up(
        ["a", "b"],
        [_payload(5, 5, 1, "measured", 4),
         _payload(7, 6, 0, "not_measurable", None)])
    cell = report["stages"]["recall_attempted"]
    assert cell["state"] == "partial", cell
    assert cell["value"] == 4, cell
    assert cell["orgs_unavailable"] == [], cell
    assert cell["orgs_not_measurable"] == ["b"], cell


def test_cohort_still_accuses_an_org_that_really_failed_to_report():
    """The distinction must not swing the other way: a genuinely unreadable
    org is still named, and never lets the cohort read `not_measurable`."""
    mod = _cohort_module()
    report = mod.roll_up(
        ["a", "b"],
        [_payload(5, 5, 1, "not_measurable", None), None],
        [None, "HTTP 500"])
    cell = report["stages"]["recall_attempted"]
    assert cell["state"] == "unavailable", cell
    assert cell["orgs_unavailable"] == ["b"], cell
    assert cell["orgs_not_measurable"] == ["a"], cell


def test_cohort_rollup_sums_measured_and_echoes_its_denominator():
    mod = _cohort_module()
    report = mod.roll_up(
        ["org-a", "org-b"],
        [_payload(5, 5, 1), _payload(7, 6, 0)],
    )
    assert report["cohort_definition"] == {
        "orgs": ["org-a", "org-b"], "size": 2,
        "source": "operator_supplied"}
    assert report["dogfood_only"] is False
    assert report["stages"]["captured"]["value"] == 12
    assert report["stages"]["memory_produced"]["value"] == 1
    # recall was unavailable for both -> the cohort stage must not read 0
    assert report["stages"]["recall_attempted"]["value"] is None
    assert report["stages"]["recall_attempted"]["state"] == "unavailable"


def test_cohort_rollup_refuses_to_sum_an_unreadable_org_as_zero():
    mod = _cohort_module()
    report = mod.roll_up(
        ["org-a", "org-b"],
        [_payload(5, 5, 1), None],
        [None, "HTTP 500"],
    )
    cell = report["stages"]["captured"]
    assert cell["state"] == "partial", cell
    assert cell["value"] == 5, cell
    assert cell["orgs_unavailable"] == ["org-b"], cell
    assert report["org_errors"] == {"org-b": "HTTP 500"}


def test_cohort_rollup_does_not_invent_a_value_for_a_malformed_measured_cell():
    """A `measured` cell with no value is a malformed upstream payload. Counting
    it as 0 would invent a measurement the org never reported."""
    mod = _cohort_module()
    payload = _payload(5, 5, 1)
    payload["stages"]["captured"] = {"state": "measured", "value": None}
    report = mod.roll_up(["a", "b"], [payload, _payload(7, 6, 0)])
    cell = report["stages"]["captured"]
    assert cell["state"] == "partial", cell
    assert cell["value"] == 7, cell
    assert cell["orgs_unavailable"] == ["a"], cell


def test_single_org_cohort_is_flagged_dogfood_only():
    mod = _cohort_module()
    assert mod.roll_up(["solo"], [_payload(1, 1, 1)])["dogfood_only"] is True


def test_cohort_output_never_derives_an_activation_rate():
    """``recall_attempted`` is an attempt, not an answer. A rate derived from
    it would be exactly the mislabelling the scorecard exists to remove.
    Checked on KEYS, not prose — the note is allowed to name the thing it
    refuses to compute."""
    mod = _cohort_module()
    report = mod.roll_up(["a", "b"], [_payload(1, 1, 1, "measured", 4),
                                        _payload(1, 1, 0, "measured", 2)])
    keys: list[str] = []
    stack = [report]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            keys.extend(str(k) for k in node)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    assert not [k for k in keys if "activat" in k.lower()], keys
    assert report["stages"]["recall_attempted"]["value"] == 6
    assert mod.roll_up(["a"], [_payload(1, 1, 1, "measured", 4)])["stages"][
        "recall_attempted"]["state"] == "measured"


def test_cohort_refuses_a_payload_that_claims_activation():
    mod = _cohort_module()
    with pytest.raises(SystemExit):
        mod._assert_no_activation_claim({"stages": {"activated": {"value": 1}}})


# ── The cohort tool's guards (re-review P2-4: they had zero coverage) ───────

class TestCohortGuards:
    """`tools/activation_cohort.py` transmits full tenant API keys, so its
    pre-flight guards are security controls. They behave correctly but nothing
    pinned them — a regression would have been silent."""

    def test_https_is_required(self):
        """Every entry below is load-bearing: review cycle 4 reverted the guard
        to the earlier `netloc`-prefix form and the whole suite stayed green,
        because the original list only held cases BOTH forms reject. These are
        the cases the hardening was written for.

        `https://:443` is the dangerous one — netloc is `:443` with an EMPTY
        hostname, so the request goes to localhost:443 with the tenant key."""
        from tools.activation_cohort import _assert_https
        for bad in ("http://api.example", "ftp://h", "api.example",
                    "https://", "https://h?x=", "https://h#f", "",
                    # netloc non-empty but NO host:
                    "https://@", "https://:443",
                    # EMPTY query/fragment (urlsplit reports query=''), which
                    # still swallow the joined scorecard path:
                    "https://host?", "https://host#",
                    # credentials in the URL, and a non-numeric port:
                    "https://user:pass@host", "https://host:abc", "https://h:99999"):
            with pytest.raises(SystemExit):
                _assert_https(bad)
        for good in ("https://api.premiselabs.co", "https://host:8443",
                     "HTTPS://host", "https://[::1]:8443", "https://host/path"):
            _assert_https(good)

    def test_header_illegal_key_is_refused_without_echoing_it(self):
        from tools.activation_cohort import _validate_key
        secret = "tt_" + "A" * 40
        _validate_key("org-1", secret)  # a real key passes
        for bad in (secret + "\n", secret + "\t", "with space", "ünicode",
                    "tab\there", ""):
            with pytest.raises(SystemExit) as exc:
                _validate_key("org-1", bad)
            # The whole point: the message names the ORG, never the key. A key
            # that reaches `putheader` raises a ValueError embedding it whole.
            assert secret not in str(exc.value), "the key leaked into the error"

    def test_redirects_are_not_followed(self):
        """CPython's redirect handler copies request headers to the new origin,
        so following one would hand `Authorization: Bearer <key>` to whatever
        host answered. Asserted against the HANDLER rather than a live socket:
        deterministic, and no background thread to perturb neighbouring
        fixtures (review cycle 3 flagged a `dictionary changed size during
        iteration` flake in a co-run module)."""
        import urllib.error

        from tools.activation_cohort import _no_redirect_opener

        opener = _no_redirect_opener()
        # `build_opener` keeps only the LAST handler for a protocol, so our
        # subclass must be the one that answers — not the stdlib default.
        handlers = [h for h in opener.handlers
                    if type(h).__name__ == "_RefuseRedirects"]
        assert handlers, "the refusing handler was not installed"
        req = urllib.request.Request(
            "https://api.example/v1/activation/scorecard",
            headers={"Authorization": "Bearer SECRET"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            handlers[0].redirect_request(
                req, None, 302, "Found", {}, "https://evil.example/x")
        assert exc.value.code == 302, exc.value
        assert "evil.example" in str(exc.value), exc.value

    def test_fetch_scorecard_uses_the_refusing_opener(self):
        """The handler being correct is not enough — `fetch_scorecard` must
        actually route through it. Swapping it for `urlopen` would restore the
        header-copying default."""
        import inspect

        from tools import activation_cohort as ac

        src = inspect.getsource(ac.fetch_scorecard)
        assert "_no_redirect_opener().open(" in src, src
        assert "urllib.request.urlopen(" not in src, src

    def test_duplicate_org_is_rejected(self):
        """A copy-paste duplicate would inflate both the cohort number and its
        denominator."""
        from tools.activation_cohort import main
        argv = ["--api-base", "https://api.example",
                "--org", "org-a=k1", "--org", "org-a=k2"]
        with pytest.raises(SystemExit):
            main(argv)

    def test_empty_org_id_is_rejected(self):
        from tools.activation_cohort import main
        with pytest.raises(SystemExit):
            main(["--api-base", "https://api.example", "--org", "=k1"])

    def test_roll_up_arity_mismatch_raises(self):
        from tools.activation_cohort import roll_up
        # `match=` is load-bearing: `zip(..., strict=True)` ALREADY raised a bare
        # ValueError, so asserting only the type would pass with this guard
        # reverted (mutation-verified by review cycle 3).
        with pytest.raises(ValueError, match="one entry per org"):
            roll_up(["a", "b"], [None])

    def test_duplicate_orgs_cannot_double_count(self):
        """Belt and braces: even if a caller bypasses the CLI, the
        operator-supplied list is echoed verbatim so the inflated cohort is
        visible rather than silent."""
        from tools.activation_cohort import roll_up
        body = {"stages": {n: {"state": "measured", "value": 1, "unit": "x",
                               "reason": None} for n in (
            "captured", "stored", "memory_produced", "recall_attempted",
            "value_confirmed")}}
        report = roll_up(["a", "a"], [body, body])
        assert report["cohort_definition"]["orgs"] == ["a", "a"]
        assert report["cohort_definition"]["size"] == 2
        # The tool MUST NOT invent a rate; a duplicate stays visible as size=2.
        keys = []
        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    keys.append(str(k))
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(report)
        assert not [k for k in keys if "activat" in k.lower()], keys
        # A bare `rate`/`ratio` key would be the same defect wearing a
        # different name — cycle 5 noted the old assertion walked only for
        # "activat" and would have missed it.
        assert not [k for k in keys if k.lower() in ("rate", "ratio",
                                                    "conversion")], keys


# ── Round-7 fixes: the stage-4 window guard, key-set stability, shape safety ──

def test_stage4_out_of_window_row_refuses_the_count():
    """The analytics leg's `created_at` predicate is the ONLY thing confining
    the fetched rows to [since, until). If a bound were silently dropped (the
    exact class this PR fixed one layer down, in `SupabaseControlPlane.query`),
    an out-of-window tool call would be counted as a measured recall attempt.
    The Python re-check must refuse the number, mirroring `stage_counts`."""
    from tortoise.activation_scorecard import recall_stages
    rows = [{"properties": {"tool_name": "tortoise_search"},
             "created_at": "2027-01-01T00:00:00+00:00"}]
    stage, detail = recall_stages(
        rows, "2026-09-16T01:00:00+00:00", memory_sessions=1,
        window=("2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00"))
    assert stage["state"] == "unavailable", stage
    assert stage["value"] is None, stage
    assert stage["reason"] == "analytics_window_predicate_not_applied", stage
    assert detail["analytics_window_out_of_range"] == 1, detail


def test_stage4_in_window_row_still_counts():
    """Legitimate form: the guard must not refuse everything — an in-window row
    still yields a measured count."""
    from tortoise.activation_scorecard import recall_stages
    rows = [{"properties": {"tool_name": "tortoise_search"},
             "created_at": "2026-09-16T12:00:00+00:00"}]
    stage, _ = recall_stages(
        rows, "2026-09-16T01:00:00+00:00", memory_sessions=1,
        window=("2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00"))
    assert stage["state"] == "measured", stage
    assert stage["value"] == 1, stage


def test_stage4_detail_key_set_is_identical_on_every_return_path():
    """Every counter must be present on EVERY return path — the exclusion
    counters and the window counter were previously set only on one path, i.e.
    absent exactly when a consumer reads them unconditionally."""
    from tortoise.activation_scorecard import recall_stages
    window = ("2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00")
    _, measured = recall_stages(
        [{"properties": {"tool_name": "tortoise_search"},
          "created_at": "2026-09-16T12:00:00+00:00"}],
        "2026-09-16T01:00:00+00:00", memory_sessions=1, window=window)
    stage, unread = recall_stages(None, None,
                                  reason="analytics_store_unreachable")
    assert stage["state"] == "unavailable"
    _, out_of_range = recall_stages(
        [{"properties": {"tool_name": "tortoise_search"},
          "created_at": "2027-01-01T00:00:00+00:00"}],
        "2026-09-16T01:00:00+00:00", memory_sessions=1, window=window)
    for key in ("unparseable_analytic_rows", "unclassifiable_analytic_rows",
                "unparseable_analytic_rows_wide",
                "unclassifiable_analytic_rows_wide",
                "analytics_window_out_of_range"):
        assert key in unread, (key, unread)
        assert key in out_of_range, (key, out_of_range)
    assert set(measured) == set(unread) == set(out_of_range)


def test_stage4_exclusion_counters_are_absent_not_zero_when_the_fold_did_not_run():
    """Exact-or-absent: a ``0`` asserts "we counted and found none". Where the
    fold that computes the stage-4 exclusion counters never ran, the counter is
    ``None`` ("we did not count"), never a literal ``0``. The old literal-zero
    init reported ``unparseable_analytic_rows == 0`` on the truncation and
    window-failure refusal paths while the count was withheld BECAUSE a row was
    unplaceable — the false zero the fold's own comment forbids."""
    from tortoise.activation_scorecard import recall_stages
    window = ("2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00")
    clean = [{"properties": {"tool_name": "tortoise_search"},
              "created_at": "2026-09-16T12:00:00+00:00"}]
    counters = ("unparseable_analytic_rows", "unclassifiable_analytic_rows",
                "unparseable_analytic_rows_wide",
                "unclassifiable_analytic_rows_wide")

    # The fold RAN on the clean read: each exclusion counter is an exact 0.
    _, measured = recall_stages(clean, "2026-09-16T01:00:00+00:00",
                                memory_sessions=1, window=window)
    for key in counters:
        assert measured[key] == 0, (key, measured)

    # Truncated page: the exclusion fold is gated off -> absent, never 0.
    _, truncated = recall_stages(clean * 1000, "2026-09-16T01:00:00+00:00",
                                 memory_sessions=1, window=window,
                                 truncated=True)
    for key in counters:
        assert truncated[key] is None, (key, truncated)

    # Window failure: absent too, and the window counter names the reason.
    _, failed = recall_stages(
        [*clean, {"properties": {"tool_name": "tortoise_recall"},
                  "created_at": "2027-01-01T00:00:00+00:00"}],
        "2026-09-16T01:00:00+00:00", memory_sessions=1, window=window)
    for key in counters:
        assert failed[key] is None, (key, failed)
    assert failed["analytics_window_out_of_range"] == 1, failed

    # No window: neither the exclusion fold nor the window scan ran -> every
    # staged counter is absent, including the window counter.
    _, no_window = recall_stages(clean, "2026-09-16T01:00:00+00:00",
                                 memory_sessions=1)
    for key in (*counters, "analytics_window_out_of_range"):
        assert no_window[key] is None, (key, no_window)

    # A store that was never read: absent, not a false zero.
    _, unread = recall_stages(None, None, reason="analytics_store_unreachable")
    for key in (*counters, "analytics_window_out_of_range"):
        assert unread[key] is None, (key, unread)


def test_counting_rows_without_a_window_fails_closed():
    """The window re-check is not optional. Reading rows without verifying
    their window is the silent-dropped-bound class this PR fixed one layer
    down, so a missing window must refuse the count rather than count rows it
    never placed on the timeline."""
    from tortoise.activation_scorecard import recall_stages
    stage, _ = recall_stages(
        [{"properties": {"tool_name": "tortoise_search"},
          "created_at": "2026-09-16T12:00:00+00:00"}],
        "2026-09-16T01:00:00+00:00", memory_sessions=1)
    assert stage["state"] == "unavailable", stage
    assert stage["value"] is None, stage
    assert stage["reason"] == "analytics_window_not_supplied", stage


def test_a_non_mapping_analytic_row_makes_the_count_a_lower_bound():
    """A store/proxy returning an array of non-mapping elements must be refused
    (the count becomes a lower bound), not raise out of the fold as a 500."""
    from tortoise.activation_scorecard import recall_stages
    stage, detail = recall_stages(
        [42, {"properties": {"tool_name": "tortoise_search"},
              "created_at": "2026-09-16T12:00:00+00:00"}],
        "2026-09-16T01:00:00+00:00", memory_sessions=1,
        window=("2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00"))
    assert stage["state"] == "unavailable", stage
    assert stage["value"] is None, stage
    assert stage["reason"] == "unclassifiable_analytic_rows", stage
    assert detail["unclassifiable_analytic_rows"] == 1, detail


def test_analytics_write_path_probe_uses_the_writers_key_resolver(monkeypatch):
    """The probe must resolve the key through the SAME seam the writer uses
    (`supabase_control._service_key()`), not a second hand-rolled lookup — a
    second resolver could report "unset" while the writer is working."""
    from tortoise import activation_scorecard as sc
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert sc.analytics_write_path_configured() is False
    # The LEGACY name is accepted by the writer, so the probe must accept it too.
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    assert sc.analytics_write_path_configured() is True


def test_stage3_predicate_in_the_payload_is_the_query_predicate(client):
    """The payload's own claim about what stage 3 counted must be the predicate
    actually interpolated into BOTH queries — stage 3's `memory_produced` and
    stage 4's gate come from different queries, so a hand-copied literal in one
    of them would let the two disagree silently."""
    from tortoise.activation_scorecard import (
        FUNNEL_QUERY,
        LIFETIME_MEMORY_QUERY,
        STAGE3_PREDICATE,
    )
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    assert body["detail"]["stage3_predicate"] == STAGE3_PREDICATE
    assert STAGE3_PREDICATE in FUNNEL_QUERY
    assert STAGE3_PREDICATE in LIFETIME_MEMORY_QUERY


def test_cohort_partial_zero_from_an_unreadable_org_is_refused():
    """A partial cohort's sum is a LOWER BOUND — the unreadable orgs are exactly
    the ones that might carry the activity. A lower bound of 0 must not be
    emitted as a confident 0."""
    mod = _cohort_module()
    report = mod.roll_up(
        ["a", "b"],
        [_payload(0, 0, 0, "measured", 0), None],
        [None, "HTTP 500"])
    cell = report["stages"]["captured"]
    assert cell["state"] == "partial", cell
    assert cell["value"] is None, cell
    assert cell["reason"] == "partial_zero_from_an_unreadable_cohort", cell
    assert cell["orgs_unavailable"] == ["b"], cell


def test_cohort_partial_zero_from_a_not_measurable_org_is_a_real_zero():
    """A 0 from `not_measurable` orgs alone IS a fact (they genuinely have
    nothing), so only `unavailable` triggers the refusal."""
    mod = _cohort_module()
    report = mod.roll_up(
        ["a", "b"],
        [_payload(3, 3, 1, "measured", 0),
         _payload(0, 0, 0, "not_measurable", None)])
    cell = report["stages"]["recall_attempted"]
    assert cell["state"] == "partial", cell
    assert cell["value"] == 0, cell
    assert cell["orgs_unavailable"] == [], cell


def test_cohort_refuses_a_payload_whose_org_id_is_not_the_org_asked_for(
        monkeypatch, capsys):
    """A mistyped `--org A=key_of_B` must not produce a report whose
    `cohort_definition` names A while the summed numbers are B's."""
    import json
    mod = _cohort_module()
    payload = _payload(5, 5, 1)
    payload["org_id"] = "b"
    monkeypatch.setattr(mod, "fetch_scorecard", lambda *a, **k: payload)
    monkeypatch.setattr(mod, "_assert_https", lambda *a, **k: None)
    assert mod.main(["--api-base", "https://api.example",
                     "--org", "a=KEY"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["org_errors"] == {"a": "org_id_mismatch"}, report
    assert report["stages"]["captured"]["state"] == "unavailable", report
    assert report["stages"]["captured"]["value"] is None, report


def test_an_out_of_window_analytics_row_refuses_the_recall_count(client, monkeypatch):
    """The `window=` wiring must be pinned END-TO-END: a store that returns a
    row outside [since, until) — the PostgREST predicate silently dropped, the
    class this PR fixed one layer down — must make stage 4 `unavailable`, not
    count it. Deleting the `window=` kwarg from `_read_recall` used to leave
    the entire suite green."""
    import tortoise.supabase_control as sc
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00",
                turn_points=2, extracted=1)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    rows = [
        {"event_name": "mcp_tool_call",
         "created_at": "2026-09-16T13:00:00+00:00",
         "properties": {"tool_name": "tortoise_search"}},
        # OUTSIDE the requested window — the read must not count it.
        {"event_name": "mcp_tool_call",
         "created_at": "2027-01-01T00:00:00+00:00",
         "properties": {"tool_name": "tortoise_recall"}},
    ]

    class _CP:
        def query(self, *a, **kw):
            return rows

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["state"] == "unavailable", stage
    assert stage["value"] is None, stage
    assert stage["reason"] == "analytics_window_predicate_not_applied", stage
    assert body["detail"]["analytics_window_out_of_range"] == 1, body["detail"]


def test_a_malformed_funnel_row_yields_unavailable_not_a_500(client, monkeypatch):
    """The graph fold sits INSIDE the fail-soft guard, exactly like the
    analytics fold: a driver/version/proxy that returns a short row must yield
    `unavailable` + an integrity flag, not an IndexError out of the handler —
    the endpoint's contract is "never a 500"."""
    import tortoise.hosted_api as ha
    from tortoise.activation_scorecard import FUNNEL_QUERY as _FUNNEL

    class _Res:
        def __init__(self, r):
            self.result_set = r

    class _G:
        def query(self, q, params=None):
            # A shortened row: `stage_counts` unpacks four fields.
            short = [["s1", "2026-09-16T01:00:00+00:00"]]
            return _Res(short if q is _FUNNEL else [])

    class _SDK:
        def _get_proj(self):
            class _Proj:
                g = _G()
            return _Proj()

    monkeypatch.setattr(ha, "_data_sdk", lambda org: _SDK())
    resp = client.get("/v1/activation/scorecard", params=WINDOW)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for name in ("captured", "stored", "memory_produced"):
        assert body["stages"][name]["state"] == "unavailable", body["stages"]
    assert "org_graph_unavailable" in body["integrity"], body["integrity"]


def test_recall_any_is_exact_or_absent_on_every_path():
    """`detail.recall_attempted_any` must mean the same thing everywhere: the
    allowlisted count over the WINDOW, emitted only when it is EXACT (window
    verified, page not truncated, every row placeable/classifiable).
    Otherwise it is withheld AND the exclusion counters name the reason. The
    STAGE precedence is unchanged: an org with no memory stays `not_measurable`,
    it does not become `unavailable`."""
    from tortoise.activation_scorecard import recall_stages
    window = ("2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00")
    clean = [{"properties": {"tool_name": "tortoise_search"},
              "created_at": "2026-09-16T13:00:00+00:00"}]
    rows = [*clean, {"properties": {"tool_name": "tortoise_recall"},
                     "created_at": "2027-01-01T00:00:00+00:00"}]

    # Out-of-window row: the window failure is named, the count withheld.
    stage, detail = recall_stages(rows, None, memory_sessions=0, window=window)
    assert stage["state"] == "not_measurable", stage
    assert stage["reason"] == "no_memory_produced_in_lifetime", stage
    assert detail["analytics_window_out_of_range"] == 1, detail
    assert detail["recall_attempted_any"] is None, detail

    # Exact read: the raw count IS reported.
    _, ok = recall_stages(clean, None, memory_sessions=0, window=window)
    assert ok["recall_attempted_any"] == 1, ok

    # Unplaceable rows: withheld, and the COUNTER names why.
    bad_stamp = [{"properties": {"tool_name": "tortoise_search"},
                  "created_at": "not-a-timestamp"}]
    _, s1 = recall_stages(bad_stamp, None, memory_sessions=0, window=window)
    assert s1["recall_attempted_any"] is None, s1
    assert s1["unparseable_analytic_rows"] == 1, s1
    _, s2 = recall_stages([42, *clean], None, memory_sessions=0, window=window)
    assert s2["recall_attempted_any"] is None, s2
    assert s2["unclassifiable_analytic_rows"] == 1, s2
    # A WIDE-only unplaceable row withholds the count on the NO-MEMORY path
    # too — the narrow pass skips it before reading its timestamp, so without
    # the wide fold the key would be present here and absent on the counting
    # path for the same input.
    wide_bad = [{"properties": {"tool_name": "tortoise_query"},
                 "created_at": "not-a-timestamp"}]
    _, s2b = recall_stages(wide_bad, None, memory_sessions=0, window=window)
    assert s2b["recall_attempted_any"] is None, s2b
    assert s2b["unparseable_analytic_rows"] == 0, s2b
    assert s2b["unparseable_analytic_rows_wide"] == 1, s2b

    # A truncated page is a lower bound, not a count.
    _, s3 = recall_stages(clean * 1000, None, memory_sessions=0,
                          window=window, truncated=True)
    assert s3["recall_attempted_any"] is None, s3
    # A missing window is never a count.
    _, s4 = recall_stages(clean, None, memory_sessions=0)
    assert s4["recall_attempted_any"] is None, s4

    # The COUNTING path holds the same rule: an unplaceable row refuses the
    # stage AND withholds the unconditioned count (it used to report it).
    stage5, s5 = recall_stages(bad_stamp, "2026-09-16T01:00:00+00:00",
                               memory_sessions=1, window=window)
    assert stage5["state"] == "unavailable", stage5
    assert stage5["reason"] == "unparseable_analytic_rows", stage5
    assert s5["recall_attempted_any"] is None, s5
    # ...and a WIDE-only unplaceable row names the WIDE counter, never the
    # narrow one (which reads 0).
    stage5b, s5b = recall_stages(wide_bad, "2026-09-16T01:00:00+00:00",
                                 memory_sessions=1, window=window)
    assert stage5b["reason"] == "unparseable_analytic_rows_wide", stage5b
    assert s5b["unparseable_analytic_rows_wide"] == 1, s5b
    assert s5b["unparseable_analytic_rows"] == 0, s5b
    assert s5b["recall_attempted_after_memory"] is None, s5b
    # ...and the measured path still reports it.
    stage6, s6 = recall_stages(clean, "2026-09-16T01:00:00+00:00",
                               memory_sessions=1, window=window)
    assert stage6["state"] == "measured", stage6
    assert s6["recall_attempted_any"] == 1, s6


def test_the_graph_read_is_dispatched_off_the_shared_default_executor(client,
                                                                     monkeypatch):
    """The graph hand-off must go through the scorecard's OWN pool, not the
    loop's shared default executor (#3060/#3718 doctrine) — reverting it to
    `asyncio.to_thread` used to leave the suite green."""
    import tortoise.hosted_api as ha
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00",
                turn_points=2, extracted=1)
    calls: list[object] = []
    real = ha._run_off_loop

    async def _spy(executor, fn, /, *args, **kwargs):
        calls.append(executor)
        return await real(executor, fn, *args, **kwargs)

    monkeypatch.setattr(ha, "_run_off_loop", _spy)
    resp = client.get("/v1/activation/scorecard", params=WINDOW)
    assert resp.status_code == 200, resp.text
    assert calls == [ha._SCORECARD_EXECUTOR], calls
