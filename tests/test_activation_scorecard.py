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


def _seed_memory_without_created_at(sid: str) -> None:
    """A Session holding a memory point but with NO ``created_at`` — the shape
    that makes ``min(s.created_at)`` NULL while ``count(DISTINCT s)`` is
    positive. Memory EXISTS; only its timestamp is missing."""
    import tortoise.hosted_api as ha
    proj = ha._make_sdk(namespace=ORG["org_id"])._get_proj()
    proj.g.query("MERGE (s:Session {id:$i})", params={"i": sid})
    pid = f"{sid}_p0"
    proj.g.query("MERGE (p:Point {id:$i}) SET p.pointKind='statement'",
                 params={"i": pid})
    proj.g.query(
        "MATCH (s:Session {id:$i}),(p:Point {id:$p}) MERGE (s)-[:CONTAINS]->(p)",
        params={"i": sid, "p": pid})


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


# ── Stage 4: conditioning, allowlist, and the zero/no-signal rule ──────────

def test_recall_is_conditioned_on_lifetime_memory(client, monkeypatch):
    """Stage 4 counts only retrievals AFTER the org first produced memory. A
    pre-memory retrieval cannot have been answered from memory. The
    unconditioned count stays visible as ``recall_attempted_any``."""
    import tortoise.supabase_control as sc
    from tortoise.sdk import TortoiseSDK  # noqa: F401  (namespace check)
    _seed("s1", "2026-09-16T12:00:00+00:00", 1, 1)  # first memory at 12:00

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


def test_unparseable_analytics_timestamp_refuses_the_recall_count(client, monkeypatch):
    """An allowlisted retrieval call whose timestamp will not parse cannot be
    placed relative to ``first_memory_at``, so it is excluded from the count —
    which makes the count a LOWER BOUND. Reporting a lower bound as
    ``measured`` is the same class of bug as counting an unverifiable row in
    the window guard, and is refused for the same reason."""
    import tortoise.supabase_control as sc
    _seed("s1", "2026-09-16T01:00:00+00:00", 2, 1)  # memory produced

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
    _seed("s1", "2026-09-16T01:00:00+00:00", 2, 1)  # memory produced

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
    _seed("s1", "2026-09-16T01:00:00+00:00", 2, 1)

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
    from tortoise.tool_registry import TOOL_REGISTRY

    names = {t.name for t in TOOL_REGISTRY}
    assert names >= RETRIEVAL_TOOL_ALLOWLIST, RETRIEVAL_TOOL_ALLOWLIST - names
    assert names >= RETRIEVAL_TOOL_ALLOWLIST_WIDE, RETRIEVAL_TOOL_ALLOWLIST_WIDE - names

    assert {
        "tortoise_search", "tortoise_recall", "tortoise_ask",
        "tortoise_search_sessions"} == RETRIEVAL_TOOL_ALLOWLIST
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
    _seed("s1", "2026-09-16T01:00:00+00:00", 1, 1)

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
    assert stage["reason"] == "analytics_page_cap_truncated", stage


def test_memory_without_a_timestamp_is_unavailable_not_no_memory(client, monkeypatch):
    """`first_memory_at` is NULL for two opposite reasons. Telling an org that
    HAS memory it has never produced any would be the wrong answer to a real
    question — so a positive memory-Session count with a NULL timestamp is a
    data gap (``unavailable``), not an absence."""
    import tortoise.supabase_control as sc
    _seed_memory_without_created_at("s1")

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
    _seed("s1", "2026-09-16T01:00:00+00:00", 2, 0)  # stored, no memory

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
    _seed("s1", "2026-09-16T01:00:00+00:00", 2, 0)
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
    _seed("s1", "2026-09-16T01:00:00+00:00", 2, 0)
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
    body = client.get("/v1/activation/scorecard").json()
    assert body["window"]["since"] < body["window"]["until"]


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
