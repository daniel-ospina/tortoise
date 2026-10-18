"""#8870: the version anchor must advance on an ENGINE THAT CAN SEE THE BUG.

The defect this file guards is a WRITE-ORDER defect, and the engines disagree
about write order:

* an engine that applies the `SET` items of one clause LEFT TO RIGHT, each item
  observing the values the earlier items just wrote, makes a clause that
  installs `contentHash` BEFORE the fields guarded on it read "unchanged" and
  quietly skip the write — the production `TORTOISE_DB_URI` backend
  (`falkordb/falkordb:6.0.1`) is one of these;
* an engine that evaluates every right-hand side against the PRE-write record
  (`falkordblite`, and `falkordb-server v4.20.6` — the CI docker lane) behaves
  correctly on either order, so the defect is INVISIBLE there.

That asymmetry is why the bug survived: its pins ran on the second kind, could
not be red, and were green. A green bought from an engine that cannot observe
the failure is not evidence — and a LIVE engine is not automatically a CAPABLE
one, which is why reachability is not the gate below.

So this file does two things, and BOTH are load-bearing:

1. **It runs where the lane points it** — `TORTOISE_DB_URI` as-is, else the
   passworded docker TEST lane, never the canonical fleet store — and never
   decides to run by probing a DIFFERENT port than the one it connects to.
2. **It MEASURES the engine's `SET`-order semantics** with a one-clause probe
   (`_probe_set_order`) and SKIPS — visibly, naming the property — on an engine
   that reads pre-write values. A green from this file therefore means the two
   behavioural assertions below actually ran against an engine that can produce
   a red; a run on the CI docker lane is a SKIP, not a false green.

`tests/test_source_version_set_order_7362.py` is the sibling half: it pins the
ORDER of the emitted Cypher, needs no engine, and fails on the pre-fix
statements in every lane — including the lanes where this file skips.

THE SKIP REASONS BELOW DELIBERATELY DO NOT NAME THE BACKEND. `tools/skip-guard`
files a skip whose reason mentions it as an AVAILABILITY regression; an
order-blind engine is a PROPERTY of that engine, not a regression, so a reason
naming it would red every lane that legitimately cannot host this pin.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from tests import _live_utils
from tests.test_connector_source_version_5214 import GH_URL, _source, gh_issue_event
from tortoise.projection import FalkorProjection

URL = "https://example.test/8870-version-anchor"

#: The scratch label the order probe creates and deletes. Never a product label,
#: so it cannot collide with (or be counted by) a real read on the graph.
_ORDER_PROBE_LABEL = "SetOrderProbe8870"

#: The probe, in ONE clause. `a` is written BEFORE the `CASE` that reads it, so
#: the ordinal the `CASE` produces IS the engine's `SET`-order semantics: the
#: clause has the exact shape of the #8870 defect, with no `:Source` involved.
_ORDER_PROBE_STATEMENT = (
    f"MATCH (n:{_ORDER_PROBE_LABEL}) "
    "SET n.a = 'y', n.v = CASE WHEN n.a <> 'y' THEN n.v + 1 ELSE n.v END "
    "RETURN n.v AS v"
)

SET_ORDER_LEFT_TO_RIGHT = "left-to-right"
SET_ORDER_PRE_WRITE = "pre-write"
#: No confident answer: the probe could not be run, or returned something that
#: is not one of the two exhaustive ordinals. Never read as either verdict.
SET_ORDER_UNMEASURED = "unmeasured"

ORDER_BLIND_SKIP_REASON = (
    "not evidence on this engine: it evaluates every right-hand side against "
    "the PRE-write record, so a clause that writes contentHash before the "
    "fields guarded on it behaves correctly here and the #8870 write-order "
    "defect is INVISIBLE on it. Run this pin against an engine that applies "
    "the SET items left to right (the production backend); "
    "tests/test_source_version_set_order_7362.py pins the emitted ORDER in "
    "every lane, including this one."
)
ORDER_UNMEASURED_SKIP_REASON = (
    "the SET-order probe produced no usable ordinal, so this engine's write "
    "semantics are UNMEASURED — a green here would be evidence about nothing "
    "(#8870)."
)


def classify_set_order(observed) -> str:
    """Which `SET`-order semantics the probe's observed ordinal names.

    The probe node starts as ``{a:'x', v:0}`` and one clause writes ``a='y'``
    and then ``v = CASE WHEN a <> 'y' THEN v + 1 ELSE v END``. The two answers
    are exhaustive and mutually exclusive:

    * ``0`` — the `CASE` observed the ``a='y'`` the same clause had just
      written, so its guard was false: the engine applies the items LEFT TO
      RIGHT, each seeing the earlier writes. It CAN observe a write-order
      defect, so a behavioural assertion about one is meaningful on it.
    * ``1`` — the `CASE` observed the PRE-write ``a='x'``, so its guard was
      true: every right-hand side is evaluated against the pre-write record.
      The defect is invisible, and no behavioural assertion about it can ever be
      red here.

    Anything else (None, a bool, a non-numeric, an out-of-range ordinal) is
    `SET_ORDER_UNMEASURED`: an unreadable probe must never become a verdict — it
    is the difference between "this engine cannot show the defect" and "we never
    looked", and only the first is a legitimate reason to skip.
    """
    if isinstance(observed, bool) or not isinstance(observed, int):
        return SET_ORDER_UNMEASURED
    return {
        0: SET_ORDER_LEFT_TO_RIGHT,
        1: SET_ORDER_PRE_WRITE,
    }.get(observed, SET_ORDER_UNMEASURED)


def skip_reason_for_set_order(semantics: str) -> str | None:
    """The visible skip reason for an engine that cannot host this pin, else None.

    A ONE-source decision, so the gate cannot skip on one reading of the probe
    and run on another.
    """
    if semantics == SET_ORDER_LEFT_TO_RIGHT:
        return None
    if semantics == SET_ORDER_PRE_WRITE:
        return ORDER_BLIND_SKIP_REASON
    return ORDER_UNMEASURED_SKIP_REASON


def _probe_set_order(proj) -> str:
    """MEASURE the engine's `SET`-order semantics on a scratch node. Never raises.

    A probe, not a port check (#8939 review): reachability says nothing about
    whether an engine can observe the defect, and the reachable engine on every
    lane the repo runs is the order-blind one. The node is created and deleted
    inside the probe so it leaves no trace on the graph.
    """
    try:
        proj.g.query(f"CREATE (n:{_ORDER_PROBE_LABEL} {{a:'x', v:0}})")
        try:
            rows = proj.g.query(_ORDER_PROBE_STATEMENT).result_set
        finally:
            proj.g.query(f"MATCH (n:{_ORDER_PROBE_LABEL}) DELETE n")
    except Exception:
        # A failed probe is UNMEASURED, never a verdict — and never an error out
        # of the gate that would read as "the thing under test failed".
        return SET_ORDER_UNMEASURED
    if not rows or not rows[0]:
        return SET_ORDER_UNMEASURED
    return classify_set_order(rows[0][0])


def _live_uri() -> str:
    """The endpoint this pin runs against — never the canonical fleet store.

    Resolution, in this order:

    1. `TORTOISE_DB_URI` — the lane's own endpoint, used AS-IS. The pre-fix gate
       probed the LEGACY port and then CONNECTED to `TORTOISE_DB_URI`, so a lane
       with a live configured engine and nothing on the legacy port skipped with
       a perfectly good graph sitting right there (the fail-open skip of the
       #8939 review), and on a lane declaring `TORTOISE_TEST_EXPECT_URI=1` that
       skip is a VIOLATION per `tools/skip-guard.py`. Gating on the endpoint we
       are ABOUT to use is the only correct signal.
    2. The passworded docker TEST lane (`docker_uri`, 6379) when it answers and
       no URI is configured — NOT `legacy_uri`: the legacy default resolves to
       the canonical fleet store, and minting a graph there is exactly what the
       #7357 guard exists to stop (that store holds ~374 graphs against
       FalkorDB's default `CACHE_SIZE=25`, so each extra graph raises the
       eviction churn that aborts fleet writes with "graph was deleted or
       replaced").
    3. Otherwise SKIP, through `_live_utils.LIVE_URI_SKIP_REASON` — the ONE
       reason `tools/skip-guard` keys the URI-gate family on, and the reason a
       hand-spelled skip here would stop being recognised (a hand-written reason
       that mentions the backend is also the guard's AVAILABILITY-regression
       trigger, which is not what a missing test lane is).
    """
    uri = os.environ.get("TORTOISE_DB_URI")
    if uri:
        return uri
    if _live_utils.docker_reachable():
        return _live_utils.docker_uri("tortoise_test_8870")
    pytest.skip(_live_utils.LIVE_URI_SKIP_REASON)


def _live_proj() -> FalkorProjection:
    """A per-test graph on a live engine that can actually SEE the defect.

    A reachable PORT is not a usable SERVER: the connection can still be refused
    (a shared container at its memory ceiling answers the socket and then refuses
    writes — measured on this host: `DB refused writes on open … memory ceiling
    (2.0 GiB of 2.0 GiB)`). That is an ABSENT LANE, not a failing writer, and the
    gate must say so — raising here would read as "the thing under test failed".
    """
    try:
        proj = FalkorProjection.from_uri(
            _live_uri(), graph_name=f"test_8870_{os.urandom(4).hex()}")
        proj.g.query("MATCH (n) DETACH DELETE n")
    except Exception as exc:
        pytest.skip(
            f"{_live_utils.LIVE_URI_SKIP_REASON} (the lane is reachable but "
            f"unusable: {type(exc).__name__})"
        )
    reason = skip_reason_for_set_order(_probe_set_order(proj))
    if reason is not None:
        proj.close()
        pytest.skip(reason)
    return proj


def _props(proj: FalkorProjection, url: str) -> dict:
    rows = proj.g.query(
        f"MATCH (s:Source {{url:'{url}'}}) "
        "RETURN s.contentHash, s.version, s.title, s.updatedAt, s.validFrom, "
        "       s._searchText"
    ).result_set
    assert rows, f"no :Source at {url}"
    return dict(zip(
        ("contentHash", "version", "title", "updatedAt", "validFrom",
         "_searchText"), rows[0], strict=False))


def test_the_connector_writer_advances_the_ordinal_on_the_server_engine():
    """`_materialize_connector_source`: a content-changing repoll is a NEW
    version, so the ordinal beside the hash must move with it.

    FAILS IF the SET list writes `contentHash` before `version` — on an engine
    that applies the items left to right the version's `s.contentHash <> $ch`
    guard then reads the hash the same clause just installed, is always false,
    and measures `version` 1 → 1 while the hash moves. That is the exact shape
    #8870 was filed for.
    """
    proj = _live_proj()
    try:
        proj.apply(gh_issue_event())
        before = _source(proj, GH_URL)
        proj.apply(gh_issue_event(state="closed",
                                  closed_at="2026-07-11T00:00:00Z"))
        after = _source(proj, GH_URL)
    finally:
        proj.close()
    assert after[0] != before[0], "a state change is a new version"
    assert after[1] == before[1] + 1, (
        f"the ordinal must advance with the hash on a left-to-right engine: "
        f"{before[1]} -> {after[1]}"
    )


def test_upsert_source_advances_every_guarded_field_on_the_server_engine():
    """`_upsert_source`'s ON MATCH: EVERY field guarded on the hash diff must
    move with it — the ordinal, the title, both recorded instants, and
    `_searchText`.

    FAILS IF `s.contentHash` is written anywhere before the fields that test it:
    the hash lands, each guard reads "unchanged", and the whole version-anchor
    block is skipped while the hash silently moves on. `_searchText` is the LAST
    guard in the clause and therefore the one a partial re-ordering leaves
    behind, which is why it gets its own assertion rather than a mention.
    """
    proj = _live_proj()
    v1 = {
        "url": URL, "sourceKind": "document", "contentHash": "hash-v1",
        "title": "title-v1",
        "validFrom": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
    }
    v2 = dict(
        v1, contentHash="hash-v2", title="title-v2",
        validFrom="2026-02-02T00:00:00Z", updatedAt="2026-02-02T00:00:00Z")
    try:
        proj._upsert_source(dict(v1))
        first = _props(proj, URL)
        proj._upsert_source(dict(v2))
        second = _props(proj, URL)
    finally:
        proj.close()
    assert second["contentHash"] == "hash-v2", "the hash must move"
    assert second["version"] == first["version"] + 1, (
        f"the ordinal must advance with the hash: "
        f"{first['version']} -> {second['version']}")
    assert second["title"] == "title-v2", "the title must move with the hash"
    assert second["updatedAt"] == "2026-02-02T00:00:00Z", (
        "updatedAt must move with the hash")
    assert second["validFrom"] == "2026-02-02T00:00:00Z", (
        "validFrom must move with the hash")
    # `_searchText` is the LAST hash guard in the clause, so a partial
    # re-ordering that moves the hash write to just before it leaves this one
    # guard poisoned while every other assertion above still passes (#8870
    # review). `$st` derives from the title, so v2 must read `title-v2`.
    assert second["_searchText"] == "title-v2", (
        f"_searchText must move with the hash and derive from the NEW title, "
        f"got {second['_searchText']!r}")



# ── The gate itself. No engine, so these run in EVERY lane ────────────────
#
# Without these the behavioural pins above could silently become the false
# green they were added to remove: a gate that skips on the wrong signal (a
# port, an availability probe) leaves the assertions unrun and the suite green.


def test_the_probe_ordinal_names_the_two_set_order_semantics():
    """`0` and `1` are the only confident answers, and they are opposite."""
    assert classify_set_order(0) == SET_ORDER_LEFT_TO_RIGHT
    assert classify_set_order(1) == SET_ORDER_PRE_WRITE


@pytest.mark.parametrize("observed", [None, True, False, 2, -1, "1", 1.5, [], ()])
def test_an_unreadable_probe_is_unmeasured_never_a_verdict(observed):
    """An unmeasurable probe must not be read as "order-blind" (the reading that
    would silently skip) nor as "left-to-right" (the reading that would run the
    assertions against an engine nobody measured)."""
    assert classify_set_order(observed) == SET_ORDER_UNMEASURED


def test_an_order_blind_engine_skips_visibly_naming_the_property():
    """The gate's answer for a pre-write engine: SKIP, with a reason that names
    the property (and not the backend — see the module docstring)."""
    reason = skip_reason_for_set_order(SET_ORDER_PRE_WRITE)
    assert reason == ORDER_BLIND_SKIP_REASON
    assert "PRE-write" in reason
    assert "falkordb" not in reason.lower(), (
        "the reason must not name the backend: tools/skip-guard would file this "
        "property-based skip as an availability regression")
    assert not reason.startswith("requires TORTOISE_DB_URI"), (
        "this is not a URI-gate skip; the URI-gate family is lane-graded")


def test_a_left_to_right_engine_is_not_skipped():
    """The engine that CAN see the defect must run the assertions."""
    assert skip_reason_for_set_order(SET_ORDER_LEFT_TO_RIGHT) is None


def test_an_unmeasured_probe_skips_rather_than_running_blind():
    assert skip_reason_for_set_order(SET_ORDER_UNMEASURED) == (
        ORDER_UNMEASURED_SKIP_REASON)


@pytest.mark.parametrize(
    ("observed", "expected"),
    [(0, SET_ORDER_LEFT_TO_RIGHT), (1, SET_ORDER_PRE_WRITE)],
)
def test_the_probe_measures_the_engine_and_cleans_up(observed, expected):
    """`_probe_set_order` drives a real clause on the engine and reads its
    ordinal — it does not consult a port, and it removes its scratch node."""
    statements: list[str] = []

    class _Graph:
        def query(self, statement, *_args, **_kwargs):
            statements.append(statement)
            return SimpleNamespace(result_set=[(observed,)])

    assert _probe_set_order(SimpleNamespace(g=_Graph())) == expected
    assert any("CREATE" in s for s in statements), "the probe must create its node"
    assert any("DELETE" in s for s in statements), "the probe must clean up"
    probe = [s for s in statements if "CASE" in s]
    assert probe and "n.a = 'y'" in probe[0] and probe[0].index(
        "n.a = 'y'") < probe[0].index("CASE"), (
        "the probe clause must WRITE before it READS — the #8870 shape")


def test_an_empty_probe_result_is_unmeasured():
    class _Graph:
        def query(self, statement, *_args, **_kwargs):
            if "CREATE" in statement:
                return SimpleNamespace(result_set=[])
            if "RETURN n.v" in statement:
                return SimpleNamespace(result_set=[])
            return SimpleNamespace(result_set=[])

    assert _probe_set_order(SimpleNamespace(g=_Graph())) == SET_ORDER_UNMEASURED


def test_a_failing_probe_is_unmeasured_not_an_exception():
    class _Graph:
        def query(self, statement, *_args, **_kwargs):
            raise RuntimeError("engine said no")

    assert _probe_set_order(SimpleNamespace(g=_Graph())) == SET_ORDER_UNMEASURED


def _legacy_must_not_be_probed(**_kwargs):
    raise AssertionError(
        "a configured TORTOISE_DB_URI must be used as-is — probing a DIFFERENT "
        "endpoint to decide whether to run is how this pin skipped with a live "
        "engine configured (#8939 review)")


def test_a_configured_uri_is_used_without_probing_another_endpoint(monkeypatch):
    """ISSUE 2 of the #8939 review, pinned: the gate must follow the URI.

    Neither fallback probe may run when a URI is configured — the pre-fix gate
    probed the legacy port and then connected to the URI.
    """
    # The URI is BUILT, never spelled: a `tests/_live_utils` consumer that also
    # hardcodes a docker-lane port would pass its own probe while dialling a dead
    # endpoint (the #5049 guard, `test_runner_ports_6673`).
    configured = _live_utils.docker_uri("tortoise_test_8870_uri")
    monkeypatch.setenv("TORTOISE_DB_URI", configured)
    monkeypatch.setattr(_live_utils, "legacy_reachable", _legacy_must_not_be_probed)
    monkeypatch.setattr(_live_utils, "docker_reachable", _legacy_must_not_be_probed)
    assert _live_uri() == configured


def test_no_uri_falls_back_to_the_docker_test_lane_not_the_legacy_store(
        monkeypatch):
    """No URI + a reachable docker test lane ⇒ run there. The legacy port is
    NEVER consulted: it resolves to the canonical fleet store (#7357)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setattr(_live_utils, "docker_reachable", lambda **_kw: True)
    monkeypatch.setattr(_live_utils, "legacy_reachable", _legacy_must_not_be_probed)
    monkeypatch.setattr(_live_utils, "legacy_uri", _legacy_must_not_be_probed)
    assert _live_uri() == _live_utils.docker_uri("tortoise_test_8870")


def test_no_endpoint_at_all_skips_with_the_canonical_reason(monkeypatch):
    """Two properties in one: the skip goes through `LIVE_URI_SKIP_REASON`
    (a hand-written reason is how the URI-gate family stops being recognised by
    skip-guard), and a REACHABLE legacy port does not turn a URI-less lane into
    a run — that port is the canonical fleet store, which `tests/conftest.py`
    refuses the suite against (#7357)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setattr(_live_utils, "docker_reachable", lambda **_kw: False)
    monkeypatch.setattr(_live_utils, "legacy_reachable", lambda **_kw: True)
    with pytest.raises(pytest.skip.Exception) as exc:
        _live_uri()
    assert str(exc.value.args[0]).startswith(_live_utils.LIVE_URI_SKIP_REASON)
