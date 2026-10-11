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
from tortoise.activation_scorecard import (
    assert_no_activation_claim,
    roll_up_cohort,
)
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


def _seed(sid: str, created_at: str, events: int, claims: int,
          org_id: str = ORG["org_id"]) -> None:
    """Seed one session into ONE org's graph — the same namespace the endpoint
    reads (``_data_sdk(org)`` -> ``_make_sdk(namespace=org_id)``). Seeding the
    process default graph instead would write to a different graph and the
    scorecard would legitimately report 0.

    ``org_id`` is a parameter since #3676: the cross-tenant cohort endpoint
    reads several org namespaces in one request, so the fixture has to be able
    to seed more than the ambient ``ORG``.
    """
    import tortoise.hosted_api as ha
    proj = ha._make_sdk(namespace=org_id)._get_proj()
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


# ── Stage 5 (#3675): recall_returned — a RETURN is not an ATTEMPT ─────────

_ABSENT = object()  # "the prop was never written on this row"
_W = ("2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00")
_MEMORY_AT = "2026-09-16T01:00:00+00:00"


def _retrieval_row(stamp, count=None, tool="tortoise_search"):
    """One analytics row. ``count`` defaults to an explicit ``None`` (the
    post-#3675 "the result was not countable" shape); pass ``_ABSENT`` for a row
    that predates the prop, where the key is not there at all."""
    props = {"tool_name": tool}
    if count is not _ABSENT:
        props["result_count"] = count
    return {"event_name": "mcp_tool_call", "created_at": stamp,
            "properties": props}


def test_recall_returned_counts_only_calls_that_returned_content():
    """Stage 5 separates the attempt from the answer: a retrieval that returned
    items and one that returned nothing are the same row for stage 4 and
    different rows here. Everything else — a call BEFORE memory, a non-retrieval
    tool, a zero-result retrieval — must not be counted."""
    from tortoise.activation_scorecard import recall_stages
    rows = [
        _retrieval_row("2026-09-16T00:30:00+00:00", 8),   # before memory
        _retrieval_row("2026-09-16T02:00:00+00:00", 8),   # returned content
        _retrieval_row("2026-09-16T02:05:00+00:00", 0),   # ran, returned nothing
        _retrieval_row("2026-09-16T02:10:00+00:00", 5,
                       tool="tortoise_health"),           # not a retrieval tool
    ]
    stage, detail = recall_stages(rows, _MEMORY_AT, memory_sessions=1, window=_W)
    assert stage["state"] == "measured" and stage["value"] == 2, stage
    cell = detail["recall_returned_cell"]
    assert cell["state"] == "measured", cell
    assert cell["value"] == 1, cell
    assert cell["unit"] == "calls", cell
    assert detail["recall_returned_after_memory"] == 1, detail
    assert detail["rows_without_result_count"] == 0, detail


def test_recall_returned_zero_is_a_measured_zero_not_a_refusal():
    """The true-zero direction: retrievals that ran and returned NOTHING are a
    real, exact 0 — the finding the funnel exists to report. Refusing it would
    hide "the read side is answering nothing" behind "we could not read"""
    from tortoise.activation_scorecard import recall_stages
    rows = [_retrieval_row("2026-09-16T02:00:00+00:00", 0),
            _retrieval_row("2026-09-16T02:01:00+00:00", 0)]
    _, detail = recall_stages(rows, _MEMORY_AT, memory_sessions=1, window=_W)
    cell = detail["recall_returned_cell"]
    assert cell["state"] == "measured", cell
    assert cell["value"] == 0, cell
    assert cell["reason"] is None, cell


def test_a_retrieval_without_result_count_refuses_stage_5_never_zero():
    """THE #3675 core rule, at the consumer. ``result_count`` is forward-only:
    an allowlisted retrieval row written before the prop shipped cannot be read
    either way, so counting it as "returned nothing" would manufacture a
    confident zero from missing data. Stage 5 refuses and names the counter —
    while stage 4, which needs no result information, stays measured 2.
    Absence is not zero."""
    from tortoise.activation_scorecard import recall_stages
    rows = [_retrieval_row("2026-09-16T02:00:00+00:00", 8),
            _retrieval_row("2026-09-16T02:01:00+00:00", _ABSENT)]  # key absent
    stage, detail = recall_stages(rows, _MEMORY_AT, memory_sessions=1, window=_W)
    assert stage["state"] == "measured" and stage["value"] == 2, stage
    cell = detail["recall_returned_cell"]
    assert cell["state"] == "unavailable", cell
    assert cell["value"] is None, cell
    assert cell["reason"] == "result_count_not_recorded", cell
    assert detail["rows_without_result_count"] == 1, detail
    assert detail["rows_uncountable_result_count"] == 0, detail


@pytest.mark.parametrize("unreadable", [None, "8", 8.0, True, -1])
def test_a_present_but_unreadable_result_count_refuses_under_its_own_name(
        unreadable):
    """A PRESENT-but-not-a-count value is a different fact from an absent key,
    and it gets its own reason (``result_count_unreadable``) so an operator can
    tell a deploy-boundary window from a shape regression or a failed call.

    ``True`` is the sharp one: Python says ``isinstance(True, int)``, so a naive
    check would silently count a malformed row as one returned item."""
    from tortoise.activation_scorecard import recall_stages
    rows = [_retrieval_row("2026-09-16T02:00:00+00:00", unreadable)]
    _, detail = recall_stages(rows, _MEMORY_AT, memory_sessions=1, window=_W)
    cell = detail["recall_returned_cell"]
    assert cell["state"] == "unavailable", cell
    assert cell["reason"] == "result_count_unreadable", cell
    assert detail["rows_without_result_count"] == 0, detail
    assert detail["rows_uncountable_result_count"] == 1, detail


def test_stage_5_inherits_every_stage_4_refusal():
    """Stage 5 asks a strictly narrower question, so it must never be MORE
    readable than stage 4. A truncated page, an out-of-window row and an org
    with no lifetime memory all refuse BOTH stages with the SAME reason — two
    copies of that ladder would drift."""
    from tortoise.activation_scorecard import recall_stages
    rows = [_retrieval_row("2026-09-16T02:00:00+00:00", 3)]

    _, truncated = recall_stages(rows, _MEMORY_AT, memory_sessions=1,
                                 window=_W, truncated=True)
    assert truncated["recall_returned_cell"]["reason"] == \
        "analytics_page_cap_truncated", truncated["recall_returned_cell"]

    outside = [_retrieval_row("2026-09-15T02:00:00+00:00", 3)]
    stage, out = recall_stages(outside, _MEMORY_AT, memory_sessions=1, window=_W)
    assert stage["reason"] == "analytics_window_predicate_not_applied", stage
    assert out["recall_returned_cell"]["state"] == "unavailable", out
    assert out["recall_returned_cell"]["reason"] == stage["reason"], out

    stage, nomem = recall_stages([], None, memory_sessions=0, window=_W)
    assert stage["state"] == "not_measurable", stage
    assert nomem["recall_returned_cell"]["state"] == "not_measurable", nomem
    assert nomem["recall_returned_cell"]["value"] is None, nomem


def test_recall_returned_uses_the_narrow_allowlist_only():
    """The wide set exists as a sensitivity bound on the ATTEMPT (stage 4). Its
    known-id reads return a single object with no item collection, so they are
    uncountable BY SHAPE forever — folding them into stage 5 would refuse the
    stage for any org that ever read a point by id. Stage 4 still reports both.
    """
    from tortoise.activation_scorecard import recall_stages
    rows = [_retrieval_row("2026-09-16T02:00:00+00:00", 4),
            _retrieval_row("2026-09-16T02:01:00+00:00", None,
                           tool="tortoise_get_point")]  # wide-only, uncountable
    stage, detail = recall_stages(rows, _MEMORY_AT, memory_sessions=1, window=_W)
    # Stage 4's cell is the NARROW count; the wide sensitivity bound is in
    # `detail`, and it is the wide one that sees both rows.
    assert stage["value"] == 1, stage
    assert detail["recall_attempted_after_memory"] == 1, detail
    assert detail["recall_attempted_after_memory_wide"] == 2, detail
    cell = detail["recall_returned_cell"]
    assert cell["state"] == "measured", cell
    assert cell["value"] == 1, cell
    assert "recall_returned_after_memory_wide" not in detail, detail


def test_recall_returned_stage_rides_in_the_payload_not_in_detail(client, monkeypatch):
    """The cell is produced by ``recall_stages`` (it shares stage 4's
    precedence) but it is a STAGE, so it must be lifted out of ``detail`` into
    ``stages`` by ``assemble`` — and ``detail`` must not carry the transport
    key. The predicate travels with the number, as ``stage3_predicate``
    already does for stage 3, so a consumer cannot read the count without its
    definition."""
    import tortoise.supabase_control as sc
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00",
                turn_points=2, extracted=1)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    rows = [
        _retrieval_row("2026-09-16T02:00:00+00:00", 4),
        _retrieval_row("2026-09-16T02:01:00+00:00", 0),
    ]

    class _CP:
        def query(self, *a, **kw):
            return rows

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    assert "recall_returned" in body["stages"], body["stages"]
    cell = body["stages"]["recall_returned"]
    assert cell["state"] == "measured", cell
    assert cell["value"] == 1, cell
    assert "recall_returned_cell" not in body["detail"], body["detail"]
    assert "result_count" in body["detail"]["recall_returned_predicate"], body
    assert body["detail"]["result_count_prop"] == "result_count", body["detail"]
    # The five/six-stage granularity disclosure moved with the funnel.
    assert body["detail"]["stage_5_granularity"] == "org_time", body["detail"]
    # Nothing in this stage claims activation.
    flat = _flatten_keys(body)
    assert not [k for k in flat if "activat" in k.lower()], flat


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
    """A seam that CANNOT page (no ``query_all``) still gets the pre-#4038
    single-page contract: a full page is a LOWER BOUND, and a lower bound is not
    a count. #4038 pages the leg to completion, so this refusal is now scoped to
    a store that offers no way to advance — dropping the fallback would let such
    a seam silently under-report."""
    import tortoise.supabase_control as sc
    from tortoise.activation_scorecard import RECALL_PAGE_CAP
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00", turn_points=1, extracted=1)

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    class _Full:
        # `query` only — no `query_all`, so the walk is unavailable to the read.
        def query(self, *a, **kw):
            return [{"properties": {"tool_name": "tortoise_search"},
                     "created_at": "2026-09-16T13:00:00+00:00"}] * RECALL_PAGE_CAP

    monkeypatch.setattr(sc, "get_control_plane", lambda: _Full())
    stage = client.get("/v1/activation/scorecard",
                       params=WINDOW).json()["stages"]["recall_attempted"]
    assert stage["value"] is None, stage
    assert stage["state"] == "unavailable", stage
    assert stage["reason"] == "analytics_page_cap_truncated", stage


def _window_rows(n: int) -> list[dict]:
    """``n`` in-window, allowlisted ``mcp_tool_call`` rows as the seam returns
    them (``id`` included — it is the walk's cursor)."""
    return [{"id": f"ev-{i:06d}", "event_name": "mcp_tool_call",
             "created_at": "2026-09-16T13:00:00+00:00",
             "properties": {"tool_name": "tortoise_search"}}
            for i in range(n)]


def test_a_busy_org_pages_to_completion_and_reports_a_measured_count(client, monkeypatch):
    """#4038, THE regression. Every hosted MCP tool call emits a row, so an
    actively-used org fills the 1000-row page by mid-morning; the read then
    reported `unavailable` (`analytics_page_cap_truncated`) for being BUSY —
    the blind spot landing on exactly the most interesting orgs. Paged to
    completion, the same window is a MEASURED count."""
    import tortoise.supabase_control as sc
    from tortoise.activation_scorecard import RECALL_PAGE_CAP
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00",
                turn_points=2, extracted=1)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    n = RECALL_PAGE_CAP + 5
    rows = _window_rows(n)

    class _Paging:
        def __init__(self):
            self.calls: list[dict] = []

        def query_all(self, table, **kw):
            self.calls.append(kw)
            # Two pages, then the empty one that CERTIFIES — `complete=True`.
            return rows, True

        def query(self, table, **kw):
            # The pre-#4038 `query` shape, kept HERE as the mutation probe: on
            # the old single-request read this is a FULL page, so the stage
            # reads `unavailable` — the failure #4038 names.
            return rows[:RECALL_PAGE_CAP]

    seam = _Paging()
    monkeypatch.setattr(sc, "get_control_plane", lambda: seam)
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["state"] == "measured", stage
    assert stage["value"] == n, stage
    assert body["detail"]["recall_rows_fetched"] == n, body["detail"]
    assert body["detail"]["recall_truncated"] is False, body["detail"]
    # The payload states the BOUND it read under, so a reader can tell "counted"
    # from "counted up to a limit".
    from tortoise.activation_scorecard import RECALL_MAX_PAGES
    assert body["detail"]["recall_page_cap"] == RECALL_PAGE_CAP, body["detail"]
    assert body["detail"]["recall_page_limit"] == RECALL_MAX_PAGES, body["detail"]
    # The walk is asked with the module's OWN page size and bound, and — the
    # half that keeps the paging from quietly changing the question — with the
    # SAME window predicate the single-page read used, cursor column included.
    assert seam.calls and len(seam.calls) == 1, seam.calls
    call = seam.calls[0]
    assert call["page_size"] == RECALL_PAGE_CAP, call
    assert call["max_pages"] == RECALL_MAX_PAGES, call
    assert "id" in call["select"], call
    assert ("created_at", "gte", "2026-09-16T00:00:00+00:00") in call["filters"], call
    assert ("created_at", "lt", "2026-09-17T00:00:00+00:00") in call["filters"], call
    assert ("org_id", "eq", ORG["org_id"]) in call["filters"], call


def test_an_incomplete_walk_is_unavailable_never_a_lower_bound(client, monkeypatch):
    """A walk that could not CERTIFY completion (a page cap, a server that
    ignored `order`, a store that will not advance past the cursor) is a lower
    bound however many rows it did return. It must read `unavailable`, exactly
    as the pre-#4038 single full page did — the fix widens what CAN be counted,
    it does not loosen what may be called a count."""
    import tortoise.supabase_control as sc
    from tortoise.activation_scorecard import RECALL_PAGE_CAP
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00",
                turn_points=2, extracted=1)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    rows = _window_rows(RECALL_PAGE_CAP + 5)

    class _Uncertified:
        def query_all(self, table, **kw):
            return rows, False

        def query(self, table, **kw):
            return rows[:RECALL_PAGE_CAP]

    monkeypatch.setattr(sc, "get_control_plane", lambda: _Uncertified())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["value"] is None, stage
    assert stage["state"] == "unavailable", stage
    assert stage["reason"] == "analytics_page_cap_truncated", stage
    # The rows that WERE read are still reported for visibility — the refusal is
    # the count, not the observation.
    assert body["detail"]["recall_rows_fetched"] == len(rows), body["detail"]
    assert body["detail"]["recall_truncated"] is True, body["detail"]
    # ...and no lower bound is published under the exact-count name.
    assert body["detail"]["recall_attempted_after_memory"] is None, body["detail"]


def test_a_walk_that_raises_is_unavailable_not_a_partial_count(client, monkeypatch):
    """A read FAILURE mid-walk is not an incomplete result: the seam raises, the
    read's fail-soft guard turns it into `analytics_store_unreachable`, and no
    prefix of the rows reaches any count. (A short page followed by an error is
    the case the fix direction names.)"""
    import tortoise.supabase_control as sc
    _stub_graph(monkeypatch, created_at="2026-09-16T01:00:00+00:00",
                turn_points=2, extracted=1)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

    class _DiesMidWalk:
        def query_all(self, table, **kw):
            raise RuntimeError("supabase down after page 1")

        def query(self, table, **kw):
            # A failure is a failure whichever seam shape the store offers: the
            # read must not get a count out of either.
            raise RuntimeError("supabase down after page 1")

    monkeypatch.setattr(sc, "get_control_plane", lambda: _DiesMidWalk())
    body = client.get("/v1/activation/scorecard", params=WINDOW).json()
    stage = body["stages"]["recall_attempted"]
    assert stage["value"] is None, stage
    assert stage["state"] == "unavailable", stage
    assert stage["reason"] == "analytics_store_unreachable", stage
    assert body["detail"]["recall_rows_fetched"] is None, body["detail"]


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
    # #3675 narrowed this refusal: the result-bearing instrument now EXISTS, so
    # the string must name the surviving gap (a returned item was not observed
    # to be USED) and must NOT keep claiming the two stale blockers it used to
    # name — `result_count`/`abstained` (built here) and #3518/#3519 (closed).
    # A test that kept asserting the old text would pin a false statement into
    # the payload.
    assert "USED" in value["reason"]
    assert "not zero" in value["reason"]
    assert "(result_count/abstained)" not in value["reason"]
    assert "#3518" not in value["reason"]
    assert "#3519" not in value["reason"]
    # And it points at the stage that replaced the missing instrument.
    assert "recall_returned" in value["reason"]

    # No key anywhere claims activation. A surface that emitted an
    # ``activated`` field would reintroduce exactly the mislabelling this lane
    # exists to remove (the dashboard already calls a STORED session "active").
    flat = _flatten_keys(body)
    offenders = [k for k in flat if "activat" in k.lower()]
    assert not offenders, offenders
    assert "value_confirmed" in body["stages"]
    # The full stage set, pinned: the funnel gained stage 5 in #3675 and the
    # refusal moved to 6. A silent rename/drop would otherwise be invisible.
    assert list(body["stages"]) == [
        "captured", "stored", "memory_produced", "recall_attempted",
        "recall_returned", "value_confirmed"], list(body["stages"])


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


# ── Cohort roll-up (tortoise/activation_scorecard.roll_up_cohort) ───────
# #3676 moved the fold OUT of `tools/activation_cohort.py` and INTO the module:
# the cross-tenant endpoint is now a caller, so there is ONE implementation of
# the cohort number. When the script owned it, the scripted cohort and the
# served cohort were two code paths that could silently disagree about the same
# orgs — which is what these tests exist to prevent, in either home.


def _payload(captured, stored, memory, recall_state="unavailable",
             recall_value=None, value_confirmed_state="not_measurable",
             returned_state="unavailable", returned_value=None):
    return {"stages": {
        "captured": {"state": "measured", "value": captured},
        "stored": {"state": "measured", "value": stored},
        "memory_produced": {"state": "measured", "value": memory},
        "recall_attempted": {"state": recall_state, "value": recall_value},
        # #3675: the served payload carries six stages, and this fixture must
        # mirror it — a stage the fixture omits folds to `unavailable` for every
        # org, which would quietly make the cohort tests exercise a shape the
        # product never returns.
        "recall_returned": {"state": returned_state, "value": returned_value},
        "value_confirmed": {"state": value_confirmed_state, "value": None},
    }}


def test_cohort_rolls_up_the_result_bearing_stage():
    """#3675: `recall_returned` is a STAGE on the served payload, so the cohort
    fold must carry it. `COHORT_STAGE_NAMES` is the roll-up's only stage list,
    and a stage missing from it disappears from the cohort silently — the
    per-org endpoint would report it while the operator's number did not."""
    report = roll_up_cohort(
        ["a", "b"],
        [_payload(5, 5, 1, "measured", 4, returned_state="measured",
                  returned_value=3),
         _payload(7, 6, 1, "measured", 2, returned_state="measured",
                  returned_value=1)])
    cell = report["stages"]["recall_returned"]
    assert cell["state"] == "measured", cell
    assert cell["value"] == 4, cell
    assert cell["orgs_unavailable"] == [], cell


def test_cohort_calls_a_refused_result_stage_partial_not_a_zero():
    """A pre-#3675 window (or an unreadable result) refuses the stage PER ORG.
    That org must be NAMED as unavailable — folding it to 0 would publish a
    lower bound as the cohort's answer, which is the failure the per-org
    refusal exists to prevent."""
    report = roll_up_cohort(
        ["a", "b"],
        [_payload(5, 5, 1, "measured", 4, returned_state="measured",
                  returned_value=3),
         _payload(7, 6, 1, "measured", 2,
                  returned_state="unavailable", returned_value=None)])
    cell = report["stages"]["recall_returned"]
    assert cell["state"] == "partial", cell
    assert cell["value"] == 3, cell
    assert cell["orgs_unavailable"] == ["b"], cell
    assert cell["orgs_measured"] == 1, cell


def test_cohort_does_not_call_a_not_measurable_stage_a_reporting_failure():
    """F1. ``value_confirmed`` is ``not_measurable`` for EVERY org by design.
    Collapsing that into ``unavailable`` and listing the orgs as unreadable
    would assert that healthy orgs failed to report — the state inversion this
    lane exists to prevent. The cohort stage must be ``not_measurable``, with no
    org accused."""
    report = roll_up_cohort(["a", "b"], [_payload(5, 5, 1), _payload(7, 6, 0)])
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
    report = roll_up_cohort(
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
    report = roll_up_cohort(
        ["a", "b"],
        [_payload(5, 5, 1, "not_measurable", None), None],
        [None, "HTTP 500"])
    cell = report["stages"]["recall_attempted"]
    assert cell["state"] == "unavailable", cell
    assert cell["orgs_unavailable"] == ["b"], cell
    assert cell["orgs_not_measurable"] == ["a"], cell


def test_cohort_rollup_sums_measured_and_echoes_its_denominator():
    report = roll_up_cohort(
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
    report = roll_up_cohort(
        ["org-a", "org-b"],
        [_payload(5, 5, 1), None],
        [None, "HTTP 500"],
    )
    cell = report["stages"]["captured"]
    assert cell["state"] == "partial", cell
    assert cell["value"] == 5, cell
    assert cell["orgs_unavailable"] == ["org-b"], cell
    assert report["org_errors"] == [{"org_id": "org-b", "reason": "HTTP 500"}]
    # `org_errors` is a LIST of records, never a map keyed by org id: an org id
    # is caller-supplied text and a key containing "activat" would trip the
    # claim guard (see `roll_up_cohort`'s docstring).
    assert isinstance(report["org_errors"], list)
    assert not [k for k in _all_keys(report) if "activat" in k.lower()], report


def test_cohort_rollup_does_not_invent_a_value_for_a_malformed_measured_cell():
    """A `measured` cell with no value is a malformed upstream payload. Counting
    it as 0 would invent a measurement the org never reported."""
    payload = _payload(5, 5, 1)
    payload["stages"]["captured"] = {"state": "measured", "value": None}
    report = roll_up_cohort(["a", "b"], [payload, _payload(7, 6, 0)])
    cell = report["stages"]["captured"]
    assert cell["state"] == "partial", cell
    assert cell["value"] == 7, cell
    assert cell["orgs_unavailable"] == ["a"], cell


def test_single_org_cohort_is_flagged_dogfood_only():
    assert roll_up_cohort(["solo"], [_payload(1, 1, 1)])["dogfood_only"] is True


def test_cohort_output_never_derives_an_activation_rate():
    """``recall_attempted`` is an attempt, not an answer. A rate derived from
    it would be exactly the mislabelling the scorecard exists to remove.
    Checked on KEYS, not prose — the note is allowed to name the thing it
    refuses to compute."""
    report = roll_up_cohort(["a", "b"], [_payload(1, 1, 1, "measured", 4),
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
    assert roll_up_cohort(["a"], [_payload(1, 1, 1, "measured", 4)])["stages"][
        "recall_attempted"]["state"] == "measured"


def test_cohort_refuses_a_payload_that_claims_activation():
    """The MODULE's guard raises `ValueError` (not SystemExit): it runs inside a
    request handler, where exiting the process would be absurd. The operator
    CLIENT's own copy raises SystemExit (see `TestCohortGuards`)."""
    with pytest.raises(ValueError, match="claims activation"):
        assert_no_activation_claim({"stages": {"activated": {"value": 1}}})
    # The guard is recursive and KEY-based — a nested claim is caught.
    with pytest.raises(ValueError, match="claims activation"):
        assert_no_activation_claim(
            {"cohort_definition": {"orgs": [{"activation_rate": 0.5}]}})
    # ...but the prose that NAMES the refusal is allowed through.
    assert_no_activation_claim(
        {"note": "no activation rate is derivable from this output"})


# ── The cohort tool's guards (re-review P2-4: they had zero coverage) ───────

class TestCohortGuards:
    """`tools/activation_cohort.py` transmits the platform's MOST privileged
    credential (`FASTAPI_INTERNAL_KEY`, the operator/internal plane), so its
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
        from tools.activation_cohort import _validate_credential
        secret = "tt_" + "A" * 40
        _validate_credential("--operator-key", secret)  # a real key passes
        for bad in (secret + "\n", secret + "\t", "with space", "ünicode",
                    "tab\there", ""):
            with pytest.raises(SystemExit) as exc:
                _validate_credential("--operator-key", bad)
            # The whole point: the message names the CREDENTIAL'S LABEL, never
            # its value. A key that reaches `putheader` raises a ValueError
            # embedding it whole.
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
            "https://api.example/v1/internal/activation/cohort",
            headers={"Authorization": "Bearer SECRET"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            handlers[0].redirect_request(
                req, None, 302, "Found", {}, "https://evil.example/x")
        assert exc.value.code == 302, exc.value
        assert "evil.example" in str(exc.value), exc.value

    def test_fetch_cohort_uses_the_refusing_opener(self):
        """The handler being correct is not enough — `fetch_cohort` must
        actually route through it. Swapping it for `urlopen` would restore the
        header-copying default."""
        import inspect

        from tools import activation_cohort as ac

        src = inspect.getsource(ac.fetch_cohort)
        assert "_no_redirect_opener().open(" in src, src
        assert "urllib.request.urlopen(" not in src, src

    def test_duplicate_org_is_rejected(self):
        """A copy-paste duplicate would inflate both the cohort number and its
        denominator."""
        from tools.activation_cohort import main
        argv = ["--api-base", "https://api.example",
                "--org", "org-a", "--org", "org-a"]
        with pytest.raises(SystemExit):
            main(argv)

    def test_the_old_org_key_syntax_is_refused_with_a_reason(self):
        """#3676 replaced `--org <org_id>=<tenant_key>` with ONE operator
        credential. Silently reading the left half of an old command would send
        a tenant key to the server as an org id — refuse it, and say why."""
        from tools.activation_cohort import main
        with pytest.raises(SystemExit) as exc:
            main(["--api-base", "https://api.example", "--org", "org-a=tt_live"])
        assert "operator" in str(exc.value)
        assert "tt_live" not in str(exc.value)

    def test_empty_org_id_is_rejected(self):
        from tools.activation_cohort import main
        with pytest.raises(SystemExit):
            main(["--api-base", "https://api.example", "--org", ""])

    def test_a_missing_operator_credential_is_refused_before_any_request(
            self, monkeypatch):
        """An org-scoped `tt_` key cannot authenticate against the cohort
        endpoint, so the tool must fail loudly rather than send nothing."""
        from tools import activation_cohort as ac
        monkeypatch.delenv(ac.OPERATOR_KEY_ENV, raising=False)
        monkeypatch.setattr(ac, "_assert_https", lambda *a, **k: None)
        with pytest.raises(SystemExit) as exc:
            ac.main(["--api-base", "https://api.example", "--org", "org-a"])
        assert ac.OPERATOR_KEY_ENV in str(exc.value)

    def test_roll_up_arity_mismatch_raises(self):
        # `match=` is load-bearing: `zip(..., strict=True)` ALREADY raised a bare
        # ValueError, so asserting only the type would pass with this guard
        # reverted (mutation-verified by review cycle 3).
        with pytest.raises(ValueError, match="one entry per org"):
            roll_up_cohort(["a", "b"], [None])

    def test_duplicate_orgs_cannot_double_count(self):
        """Belt and braces: even if a caller bypasses the CLI, the
        operator-supplied list is echoed verbatim so the inflated cohort is
        visible rather than silent."""
        body = {"stages": {n: {"state": "measured", "value": 1, "unit": "x",
                               "reason": None} for n in (
            "captured", "stored", "memory_produced", "recall_attempted",
            "value_confirmed")}}
        report = roll_up_cohort(["a", "a"], [body, body])
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
    report = roll_up_cohort(
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
    report = roll_up_cohort(
        ["a", "b"],
        [_payload(3, 3, 1, "measured", 0),
         _payload(0, 0, 0, "not_measurable", None)])
    cell = report["stages"]["recall_attempted"]
    assert cell["state"] == "partial", cell
    assert cell["value"] == 0, cell
    assert cell["orgs_unavailable"] == [], cell


def test_cohort_client_refuses_a_response_bound_to_another_org_list(
        monkeypatch, capsys):
    """The client binds the report to the orgs it ASKED FOR. The server echoes
    `cohort_definition` from the request, so a mismatch means the response did
    not come from the requested cohort (a wrong/old endpoint, or a proxy) — and
    an operator would otherwise read a denominator that does not describe the
    numbers beneath it. The credential must never appear in the error."""
    from tools import activation_cohort as ac

    monkeypatch.setattr(
        ac, "fetch_cohort",
        lambda *a, **k: {"cohort_definition": {"orgs": ["b"], "size": 1,
                                              "source": "operator_supplied"},
                         "stages": {}, "org_errors": {}})
    monkeypatch.setattr(ac, "_assert_https", lambda *a, **k: None)
    monkeypatch.setenv(ac.OPERATOR_KEY_ENV, "operator-secret")
    with pytest.raises(SystemExit) as exc:
        ac.main(["--api-base", "https://api.example", "--org", "a"])
    assert "cohort_definition" in str(exc.value)
    assert "operator-secret" not in str(exc.value)
    assert capsys.readouterr().out == ""


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


# ══ #3676: the cross-tenant cohort endpoint ═══════════════════════════════════
# `GET /v1/internal/activation/cohort` — operator-only, EXPLICIT org list. These
# are the tenancy review's executable half: the credential, the enumeration
# refusal, the per-org blast radius, the no-activation-claim rule and the audit
# decision are each pinned, because each fails SILENTLY if it regresses.

COHORT_PATH = "/v1/internal/activation/cohort"
_INTERNAL_KEY = "test-internal-cohort-secret"
INTERNAL_HEADERS = {"Authorization": f"Bearer {_INTERNAL_KEY}"}
COHORT_ORGS = ["org-cohort-a", "org-cohort-b"]


@pytest.fixture
def internal_key(monkeypatch):
    """The cohort endpoint's operator credential. `_check_internal` reads it at
    CALL time (#880), so setting it inside the test is enough."""
    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
    return dict(INTERNAL_HEADERS)


def _cohort(client, headers, orgs=None, window=None, **kwargs):
    params = [("orgs", o) for o in (COHORT_ORGS if orgs is None else orgs)]
    for key, value in (window if window is not None else WINDOW).items():
        params.append((key, value))
    return client.get(COHORT_PATH, params=params, headers=headers, **kwargs)


def _all_keys(payload) -> list[str]:
    keys: list[str] = []
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            keys.extend(str(k) for k in node)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return keys


def _all_strings(payload) -> list[str]:
    out: list[str] = []
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str):
            out.append(node)
    return out


# ── The credential (acceptance criterion 1) ────────────────────────────────

def test_cohort_requires_the_internal_operator_credential(client, monkeypatch):
    """An org-scoped `tt_` key — every tenant's credential — must NOT open this
    surface. `_check_internal` compares against `FASTAPI_INTERNAL_KEY`, a value
    an org key cannot obtain, so the separation is structural rather than a
    scope check."""
    monkeypatch.delenv("FASTAPI_INTERNAL_KEY", raising=False)
    # Unconfigured server: fail closed (503), never "no key needed".
    assert _cohort(client, {}).status_code == 503

    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
    # No credential at all.
    assert _cohort(client, {}).status_code == 401
    # A well-formed ORG key (`tt_`, the tenant plane).
    org_key = {"Authorization": "Bearer tt_" + "A" * 40}
    assert _cohort(client, org_key).status_code == 401
    # A wrong internal key.
    assert _cohort(client, {"Authorization": "Bearer nope"}).status_code == 401
    # The right one.
    assert _cohort(client, INTERNAL_HEADERS).status_code == 200


def test_cohort_a_near_miss_credential_is_refused(client, internal_key):
    """Prefix/suffix near-misses must not authenticate — the comparison is on
    the FULL bearer value (`hmac.compare_digest`), not a prefix test."""
    for bad in (_INTERNAL_KEY[:-1], _INTERNAL_KEY + "x", _INTERNAL_KEY.upper()):
        resp = _cohort(client, {"Authorization": f"Bearer {bad}"})
        assert resp.status_code == 401, (bad, resp.text)


# ── Enumeration refusal (acceptance criterion 2) ────────────────────────────

def test_cohort_refuses_wildcard_tokens(client, internal_key):
    """A wildcard is how an explicit list silently becomes "every tenant on the
    platform" — refused by NAME, case-insensitively, and the message says so."""
    for token in ("all", "ALL", "any", "every", "*", "%", "-1"):
        resp = _cohort(client, internal_key, orgs=[token])
        assert resp.status_code == 422, (token, resp.text)
        assert "wildcard" in resp.text, (token, resp.text)


def test_cohort_refuses_an_empty_org_list(client, internal_key):
    """No list, and no "default to everything": an absent cohort is not a
    cohort."""
    assert client.get(COHORT_PATH, params=WINDOW,
                      headers=internal_key).status_code == 422
    assert _cohort(client, internal_key, orgs=[]).status_code == 422


def test_cohort_refuses_a_malformed_org_id(client, internal_key):
    """The id becomes a graph namespace, so the accepted shape is the same one
    the provisioning path validates. `")/"`-style input must never reach the
    namespace seam as an opaque connect error."""
    for bad in ("org a", "../etc", "a/b", "-leading", "x" * 65, "org;drop"):
        resp = _cohort(client, internal_key, orgs=[bad])
        assert resp.status_code == 422, (bad, resp.text)


def test_cohort_refuses_a_duplicate_org(client, internal_key):
    """Refused rather than deduped: a copy-paste duplicate silently inflates
    the cohort number AND its denominator."""
    resp = _cohort(client, internal_key, orgs=["org-cohort-a", "org-cohort-a"])
    assert resp.status_code == 422, resp.text
    assert "more than once" in resp.text, resp.text


def test_cohort_org_cap_is_a_blast_radius_bound():
    """The cap is enforced at the boundary and is an OPERATIONAL limit, not a
    cohort-size requirement (the owner withdrew the minimum-N bar). Asserted
    against the validator so the boundary costs no I/O."""
    from tortoise.activation_scorecard import MAX_COHORT_ORGS
    from tortoise.hosted_api import _validate_cohort_orgs

    at_cap = [f"org-{n:03d}" for n in range(MAX_COHORT_ORGS)]
    assert _validate_cohort_orgs(at_cap, MAX_COHORT_ORGS) == at_cap
    with pytest.raises(Exception) as exc:
        _validate_cohort_orgs([*at_cap, "org-one-too-many"], MAX_COHORT_ORGS)
    assert "at most" in str(getattr(exc.value, "detail", exc.value))


def test_cohort_never_returns_an_org_the_caller_did_not_name(client, internal_key):
    """The endpoint is not a registry probe: a seeded org that was NOT asked
    for appears nowhere in the response, at any depth."""
    _seed("s-unasked", "2026-09-16T10:00:00+00:00", 2, 1,
          org_id="org-cohort-unasked")
    _seed("s-asked", "2026-09-16T10:00:00+00:00", 1, 0,
          org_id="org-cohort-a")
    body = _cohort(client, internal_key, orgs=["org-cohort-a"]).json()
    assert body["cohort_definition"]["orgs"] == ["org-cohort-a"]
    assert not [s for s in _all_strings(body) if "unasked" in s], body


# ── The fold, end to end ────────────────────────────────────────────────────

def test_cohort_rolls_up_the_named_orgs_and_echoes_its_denominator(
        client, internal_key):
    _seed("c-a", "2026-09-16T10:00:00+00:00", 2, 1, org_id="org-cohort-a")
    _seed("c-b", "2026-09-16T11:00:00+00:00", 1, 0, org_id="org-cohort-b")
    body = _cohort(client, internal_key).json()

    assert body["surface"] == "activation_cohort"
    assert body["cohort_definition"] == {
        "orgs": COHORT_ORGS, "size": 2, "source": "operator_supplied"}
    assert body["dogfood_only"] is False
    assert body["window"] == {"since": "2026-09-16T00:00:00+00:00",
                              "until": "2026-09-17T00:00:00+00:00"}
    assert body["org_errors"] == []
    # The shared analytics stage is unmeasured here (no SUPABASE_URL in the test
    # env), so the cause is hoisted ONCE at the cohort level instead of being
    # repeated as a per-org failure.
    assert body["notes"] == [
        "recall_attempted is unavailable for 2 org(s): "
        "analytics_write_path_unconfigured — this stage reads ONE shared "
        "analytics store, so it is a server-level condition rather than a "
        "per-org read failure"], body["notes"]
    assert body["stages"]["captured"] == {
        "state": "measured", "value": 2, "reason": None,
        "orgs_measured": 2, "orgs_unavailable": [], "orgs_not_measurable": []}
    assert body["stages"]["stored"]["value"] == 2, body["stages"]
    assert body["stages"]["memory_produced"]["value"] == 1, body["stages"]
    # `value_confirmed` is a refusal for every org, so the cohort state is
    # `not_measurable` — that is the HEALTHY answer, not a failure.
    assert body["stages"]["value_confirmed"]["state"] == "not_measurable"
    assert body["stages"]["value_confirmed"]["value"] is None


def test_cohort_payload_never_claims_activation(client, internal_key):
    """Acceptance criterion 5: no `activated` field and no rate, at any depth.
    `assert_no_activation_claim` in the module makes this a RUNTIME refusal on
    the endpoint too, not only a test."""
    _seed("c-a2", "2026-09-16T10:00:00+00:00", 1, 1, org_id="org-cohort-a")
    body = _cohort(client, internal_key).json()
    keys = _all_keys(body)
    assert not [k for k in keys if "activat" in k.lower()], keys
    assert not [k for k in keys if k.lower() in ("rate", "ratio",
                                                "conversion")], keys


def test_cohort_single_org_is_flagged_dogfood_only(client, internal_key):
    _seed("c-solo", "2026-09-16T10:00:00+00:00", 1, 1, org_id="org-cohort-a")
    body = _cohort(client, internal_key, orgs=["org-cohort-a"]).json()
    assert body["dogfood_only"] is True
    assert body["cohort_definition"]["size"] == 1


def test_cohort_window_is_validated_once_and_echoed(client, internal_key):
    """One window for the whole cohort — validated once, so every per-org read
    sees the same interval and the boundary instant is treated identically."""
    resp = _cohort(client, internal_key,
                   window={"since": "2026-09-16T00:00:00",
                           "until": "2026-09-17T00:00:00Z"})
    assert resp.status_code == 422, resp.text
    for bad in ({"since": "2026-09-17T00:00:00Z", "until": "2026-09-16T00:00:00Z"},
                {"since": "2026-01-01T00:00:00Z", "until": "2027-01-01T00:00:00Z"}):
        assert _cohort(client, internal_key, window=bad).status_code == 422


def test_cohort_agrees_with_the_org_scoped_endpoint_for_the_same_org(
        client, internal_key):
    """ONE code path (#3676): the cohort's per-org number must equal what that
    org reads through `GET /v1/activation/scorecard`. A second implementation
    would let the two surfaces quietly disagree about the same org."""
    _seed("c-agree", "2026-09-16T10:00:00+00:00", 3, 2)
    scoped = client.get("/v1/activation/scorecard", params=WINDOW).json()
    body = _cohort(client, internal_key, orgs=[ORG["org_id"]]).json()
    for name in ("captured", "stored", "memory_produced", "recall_attempted",
                 "value_confirmed"):
        cohort_cell, scoped_cell = body["stages"][name], scoped["stages"][name]
        # The per-org VERDICT must be identical. The cohort cell additionally
        # carries the roll-up's own bookkeeping (`orgs_measured` / the two
        # org-name lists) and does not repeat `unit` — that difference is the
        # fold's, and it is asserted so it stays deliberate.
        assert (cohort_cell["state"], cohort_cell["value"]) == (
            scoped_cell["state"], scoped_cell["value"]), name
        assert set(cohort_cell) - set(scoped_cell) == {
            "orgs_measured", "orgs_unavailable", "orgs_not_measurable"}, name
    # The REASON differs exactly where the read does: stages 1-3 are read per
    # org and keep the org's own reason, while stage 4's is the roll-up's
    # umbrella reason — its cause is server-level and is hoisted once into
    # `notes` instead of being repeated for every org.
    for name in ("captured", "stored", "memory_produced"):
        assert body["stages"][name]["reason"] == scoped["stages"][name]["reason"]
    assert body["stages"]["recall_attempted"]["reason"] == "no_org_measurable"
    assert any("analytics_write_path_unconfigured" in n for n in body["notes"]), \
        body["notes"]


# ── Blast radius (acceptance criterion 3) ───────────────────────────────────

def test_cohort_an_unreadable_org_is_named_and_never_summed_as_zero(
        client, internal_key, monkeypatch):
    """org-b's graph is unreachable while org-a's reads fine. org-a's number
    must stand, org-b must be NAMED, and the cohort must never present the
    unreadable org as a 0."""
    import tortoise.hosted_api as ha
    _seed("c-ok", "2026-09-16T10:00:00+00:00", 2, 1, org_id="org-cohort-a")
    real = ha._data_sdk

    def _sdk(org):
        if org["org_id"] == "org-cohort-b":
            raise RuntimeError("graph unreachable")
        return real(org)

    monkeypatch.setattr(ha, "_data_sdk", _sdk)
    body = _cohort(client, internal_key).json()

    cell = body["stages"]["captured"]
    assert cell["state"] == "partial", cell
    assert cell["value"] == 1, cell          # org-a's 1, NOT 0 and NOT 2
    assert cell["orgs_unavailable"] == ["org-cohort-b"], cell
    assert cell["orgs_measured"] == 1, cell
    # The error map explains the roll-up's own verdict rather than leaving a
    # bare name the operator has to re-read the org to explain.
    assert body["org_errors"] == [
        {"org_id": "org-cohort-b", "reason": "org_graph_unavailable"}], body
    # A partial cohort with a non-zero sum keeps its value; the lower-bound
    # refusal only fires at 0 (a 0 it never established).
    assert body["stages"]["memory_produced"]["value"] == 1, body["stages"]


def test_cohort_names_an_org_whose_read_raised(client, internal_key, monkeypatch):
    """A per-org read that RAISES (a bug, or a shape the fold does not absorb)
    must cost that org's cells only. The exception class is reported; the
    exception MESSAGE never is (it can carry a DSN/host/credential)."""
    import tortoise.hosted_api as ha
    _seed("c-raise", "2026-09-16T10:00:00+00:00", 1, 1, org_id="org-cohort-a")
    real = ha._scorecard_for_org

    async def _one(org, since_iso, until_iso, log):
        if org["org_id"] == "org-cohort-b":
            raise RuntimeError("postgres://user:pw@host/db")
        return await real(org, since_iso, until_iso, log)

    monkeypatch.setattr(ha, "_scorecard_for_org", _one)
    body = _cohort(client, internal_key).json()
    assert body["org_errors"] == [
        {"org_id": "org-cohort-b", "reason": "org_read_failed:RuntimeError"}], body
    assert body["stages"]["captured"]["value"] == 1, body["stages"]
    assert not [s for s in _all_strings(body) if "postgres://" in s], body


def _canned_cell(state, value, reason=None):
    return {"state": state, "value": value, "reason": reason}


def _canned_payload(captured: int) -> dict:
    """A well-formed per-org scorecard payload, for tests whose subject is the
    BLAST RADIUS rather than the read (so they cost no graph/store latency)."""
    return {
        "stages": {
            "captured": _canned_cell("measured", captured),
            "stored": _canned_cell("measured", captured),
            "memory_produced": _canned_cell("measured", captured),
            "recall_attempted": _canned_cell(
                "unavailable", None, "analytics_store_unreachable"),
            "value_confirmed": _canned_cell("not_measurable", None),
        },
        "integrity": [],
    }


def test_cohort_a_hung_org_read_becomes_a_named_unavailable(client, internal_key,
                                                           monkeypatch):
    """A HANG is the blast radius that matters most: without the per-org bound
    one dead org would hold the whole cohort. The hung org is named, and the
    healthy org still reports."""
    import asyncio

    import tortoise.hosted_api as ha
    monkeypatch.setattr(ha, "_COHORT_PER_ORG_TIMEOUT_S", 1.0)

    async def _one(org, since_iso, until_iso, log):
        if org["org_id"] == "org-cohort-b":
            await asyncio.sleep(60)
        return _canned_payload(1)

    monkeypatch.setattr(ha, "_scorecard_for_org", _one)
    body = _cohort(client, internal_key).json()
    assert body["org_errors"] == [
        {"org_id": "org-cohort-b", "reason": "org_read_timeout"}], body
    assert body["stages"]["captured"]["value"] == 1, body["stages"]
    assert body["stages"]["captured"]["orgs_unavailable"] == ["org-cohort-b"]


def test_cohort_a_partial_zero_from_an_unreadable_org_is_refused(
        client, internal_key, monkeypatch):
    """The sharpest edge: org-a genuinely measured 0 captured sessions and
    org-b could not be read. `0` there would be a LOWER BOUND presented as a
    fact — the exact misreading this lane exists to remove."""
    import tortoise.hosted_api as ha
    real = ha._data_sdk

    def _sdk(org):
        if org["org_id"] == "org-cohort-b":
            raise RuntimeError("graph unreachable")
        return real(org)

    monkeypatch.setattr(ha, "_data_sdk", _sdk)
    body = _cohort(client, internal_key).json()
    cell = body["stages"]["captured"]
    assert cell["state"] == "partial", cell
    assert cell["value"] is None, cell
    assert cell["reason"] == "partial_zero_from_an_unreadable_cohort", cell


def test_cohort_every_org_unreadable_reads_unavailable_not_not_measurable(
        client, internal_key, monkeypatch):
    """If nothing could be read, the cohort is `unavailable` — never
    `not_measurable`, which would claim the orgs answered "nothing to measure".
    That distinction is the state inversion the lane exists to prevent."""
    import tortoise.hosted_api as ha

    def _sdk(org):
        raise RuntimeError("graph unreachable")

    monkeypatch.setattr(ha, "_data_sdk", _sdk)
    body = _cohort(client, internal_key).json()
    cell = body["stages"]["captured"]
    assert cell["state"] == "unavailable", cell
    assert cell["value"] is None, cell
    assert cell["reason"] == "no_org_measurable", cell
    assert sorted(cell["orgs_unavailable"]) == sorted(COHORT_ORGS), cell


# ── The audit decision ──────────────────────────────────────────────────────

def test_cohort_audits_once_on_the_operator_plane(client, internal_key,
                                                 monkeypatch):
    """A cross-tenant read is recorded: ONE append-only `audit_events` row per
    request, with `org_id=""` (the operator plane — the non-org-scoped shape
    `identity_link` already uses), naming every target org in `detail`."""
    import tortoise.hosted_api as ha
    seen: list[dict] = []

    async def _audit(request, org_id, operation, **kwargs):
        seen.append({"org_id": org_id, "operation": operation, **kwargs})

    monkeypatch.setattr(ha, "_async_audit", _audit)
    _seed("c-audit", "2026-09-16T10:00:00+00:00", 1, 1, org_id="org-cohort-a")
    resp = _cohort(client, internal_key)
    assert resp.status_code == 200, resp.text

    assert len(seen) == 1, seen
    row = seen[0]
    assert row["org_id"] == "", row
    assert row["operation"] == "activation_cohort_read", row
    assert row["resource_type"] == "activation_cohort", row
    assert row["detail"]["orgs"] == COHORT_ORGS, row
    assert row["detail"]["window"] == {"since": "2026-09-16T00:00:00+00:00",
                                      "until": "2026-09-17T00:00:00+00:00"}, row


def test_cohort_a_failing_audit_sink_does_not_fail_the_read(
        client, internal_key, monkeypatch):
    """Fail-OPEN on the audit write (#1529's ruling: bookkeeping must not 500 a
    serving path). Blocking the read would not stop an abuser either — an
    abuser holds the key."""
    import tortoise.hosted_api as ha

    async def _boom(*a, **k):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(ha, "_async_audit", _boom)
    _seed("c-audit2", "2026-09-16T10:00:00+00:00", 1, 1, org_id="org-cohort-a")
    resp = _cohort(client, internal_key)
    assert resp.status_code == 200, resp.text
    assert resp.json()["stages"]["captured"]["value"] == 1, resp.text


# ── The operator client ────────────────────────────────────────────────────

def test_operator_client_sends_one_authenticated_request(monkeypatch, capsys):
    """The thin client: ONE GET to the operator endpoint, bearer credential,
    every org as a repeated `orgs=` parameter — and it echoes the server's
    payload unchanged (the roll-up is the server's, not the client's)."""
    import json

    from tools import activation_cohort as ac
    captured: dict = {}
    payload = {"cohort_definition": {"orgs": COHORT_ORGS, "size": 2,
                                     "source": "operator_supplied"},
               "dogfood_only": False, "stages": {}, "org_errors": {},
               "window": {"since": "s", "until": "u"}}

    def _fetch(api_base, key, orgs, since, until, timeout):
        captured.update(api_base=api_base, key=key, orgs=orgs, since=since,
                        until=until, timeout=timeout)
        return payload

    monkeypatch.setattr(ac, "fetch_cohort", _fetch)
    monkeypatch.delenv(ac.OPERATOR_KEY_ENV, raising=False)
    assert ac.main(["--api-base", "https://api.example",
                    "--org", "org-cohort-a", "--org", "org-cohort-b",
                    "--operator-key", "operator-secret",
                    "--since", "2026-09-16T00:00:00Z",
                    "--until", "2026-09-17T00:00:00Z"]) == 0
    assert captured == {"api_base": "https://api.example",
                        "key": "operator-secret",
                        "orgs": COHORT_ORGS,
                        "since": "2026-09-16T00:00:00Z",
                        "until": "2026-09-17T00:00:00Z",
                        "timeout": 900.0}, captured
    out = capsys.readouterr().out
    assert json.loads(out) == payload
    assert "operator-secret" not in out
    assert ac.COHORT_PATH == COHORT_PATH
    # The client's path must be a route the server actually registers — a
    # renamed route would otherwise leave the committed client pointing at a
    # 404, and only an operator would find out.
    assert COHORT_PATH in {r.path for r in app.routes}, [
        r.path for r in app.routes if "cohort" in r.path]


def test_operator_client_prefers_flag_over_env_and_writes_out(
        monkeypatch, tmp_path):
    """`--operator-key` wins over the env (so a rotation can be tested without
    exporting a secret into the shell), and `--out` writes the payload."""
    import json

    from tools import activation_cohort as ac
    payload = {"cohort_definition": {"orgs": ["org-cohort-a"], "size": 1,
                                     "source": "operator_supplied"},
               "dogfood_only": True, "stages": {}, "org_errors": {}}
    seen: dict = {}
    monkeypatch.setattr(ac, "fetch_cohort",
                        lambda api_base, key, orgs, since, until, timeout: (
                            seen.update(key=key) or payload))
    monkeypatch.setenv(ac.OPERATOR_KEY_ENV, "from-env")
    out = tmp_path / "cohort.json"
    ac.main(["--api-base", "https://api.example", "--org", "org-cohort-a",
             "--operator-key", "from-flag", "--out", str(out)])
    assert seen["key"] == "from-flag"
    assert json.loads(out.read_text()) == payload
