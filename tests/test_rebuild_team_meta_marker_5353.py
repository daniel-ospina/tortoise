"""#5353 — `rebuild_all` must not wipe the `:TeamMeta` first-org marker.

`:TeamMeta` is written with a bare `CREATE (:TeamMeta {name, created})` and NO
identity property — by `sdk.org_create`, `__main__._cmd_key_create` and three
`hosted_api` provisioning lanes — so it rides no journal record and no replay
pass recreates it. Before this change `rg -n "TeamMeta" tortoise/projection/`
was ZERO hits: the unconditional wipe in `rebuild_all` destroyed the class
outright, and `rebuild()` (the journal-only engine) did the same.

Its **presence** is the whole `#2789` first-org guard: `_eager_provision_org_graph`
short-circuits on `MATCH (m:TeamMeta) RETURN count(m)`, so a rebuilt graph read
0 and the next provisioning call re-minted the marker with the control plane's
name and a fresh `created` — replacing the graph's own team identity on a
rebuild the operator asked for.

The class is CARRIED in the durable #2943 pre-wipe sidecar (`team_meta` section,
snapshot version 6) because it fits neither existing vehicle: the `#2814` config
registry keys a row on a single identity property (and `:TeamMeta` has none),
and the container/link sections key on `id` (also none). It is **at-most-one by
intent** — the guard probe IS the invariant — so the section carries at most one
marker, chosen by earliest `created`, and a collapse is reported rather than
silent.

Lane: either, mirroring `tests/test_rebuild_event_meta_watermark_4653.py`. The
fixtures build the SDK the same way the embedded carve-out suite does, and the
bulk-wipe guard passes on the redirected `test_*_tortoise` graph too, so this
file needs no `carve_out` classification.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tortoise.sdk import TortoiseSDK

# ── harness ──────────────────────────────────────────────────────────────────


def _mk_sdk(tmp_path, name="tm.db", namespace=None):
    events = tmp_path / "events"
    events.mkdir(parents=True, exist_ok=True)
    sdk = TortoiseSDK(
        db_path=str(tmp_path / name),
        # A `test_`-prefixed namespace keeps the bulk-wipe guard passing under
        # the docker redirect (graph name becomes `test_..._tortoise`).
        namespace=namespace or f"test_tm_{os.urandom(4).hex()}",
        event_log_path=str(events / "events.jsonl"),
    )
    return sdk, events


def _proj(sdk):
    return sdk._get_proj()


def _create_marker(sdk, name, created):
    _proj(sdk).g.query(
        "CREATE (m:TeamMeta {name: $name, created: $created})",
        params={"name": name, "created": created})


def _markers(sdk) -> list[dict]:
    rows = _proj(sdk).g.query(
        "MATCH (m:TeamMeta) RETURN properties(m)").result_set
    return [r[0] for r in rows]


def _marker_count(sdk) -> int:
    """The `#2789` guard's own probe — the exact expression it reads."""
    return int(_proj(sdk).g.query(
        "MATCH (m:TeamMeta) RETURN count(m)").result_set[0][0])


def _sidecar_path(events_dir) -> str:
    from tortoise.projection import prewipe_snapshot_path
    return prewipe_snapshot_path(str(events_dir))


def _plant(path, payload: dict) -> None:
    from tortoise.projection import _write_prewipe_snapshot
    _write_prewipe_snapshot(str(path), payload)


def _sidecar_payload(**overrides) -> dict:
    """A complete CURRENT-version shape with every section empty.

    Complete on purpose: an incomplete shape would fire #4641's / this change's
    state-UNKNOWN ERRORs and mask the pin under test.
    """
    from tortoise.projection import (
        _PREWIPE_SNAPSHOT_VERSION,
        _SNAPSHOT_SECTIONS,
    )
    payload = {
        "version": _PREWIPE_SNAPSHOT_VERSION,
        "created_at": "2026-01-01T00:00:00Z",
        **{section: [] for section in _SNAPSHOT_SECTIONS},
    }
    payload.update(overrides)
    return payload


def _seen_points(sdk) -> int:
    return int(_proj(sdk).g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0])


# ── 1. the defect: the marker survives a full rebuild (I1) ───────────────────


def test_rebuild_all_preserves_the_team_meta_marker(tmp_path):
    """The headline pin (issue #5353, I1).

    FAILING VALUE: after the rebuild exactly ONE `:TeamMeta` exists and it
    carries the captured `name`/`created` verbatim. Pre-fix the graph held ZERO
    — the unconditional wipe destroyed the class and no replay pass recreates a
    node it has no record of.

    REACHABLE IN THE FIXTURE: the marker is created as the product writes it
    (bare `CREATE`, same two properties), and the graph really is wiped and
    replayed (a journaled Point is asserted to come back, so this is a real
    `rebuild_all`, not a no-op).
    """
    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "journaled, so the replay is real")
    _create_marker(sdk, "Acme", "2026-01-01T00:00:00+00:00")
    assert _markers(sdk) == [
        {"name": "Acme", "created": "2026-01-01T00:00:00+00:00"}], \
        "the fixture must really start from one marker, or the pin is vacuous"

    counts = _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert _seen_points(sdk) == 1, "the replay must actually restore the Point"
    assert _markers(sdk) == [
        {"name": "Acme", "created": "2026-01-01T00:00:00+00:00"}], (
        "#5353/I1: the wipe is unconditional and only the journal is replayed, "
        "so the marker must be CARRIED — with the same name/created — not "
        "silently destroyed")
    assert counts["team_meta_expected"] == 1
    assert counts["team_meta_restored"] == 1
    assert counts["team_meta_gap"] == 0
    assert counts["team_meta_verified"] is True


def test_rebuild_all_leaves_the_2789_guard_reading_provisioned(tmp_path):
    """I2: the guard's own probe is non-zero on the rebuilt graph.

    FAILING VALUE: `MATCH (m:TeamMeta) RETURN count(m)` — the EXACT expression
    `_eager_provision_org_graph` reads — returns 1 after the rebuild (pre-fix:
    0, which is what let the next provisioning call re-mint the marker). The
    guard's short-circuit on a non-zero probe is pinned separately and
    hermetically by
    `test_2789_guard_short_circuits_on_the_rebuilt_marker_count` below; this pin
    is the INPUT half, driven against a graph that was really wiped+replayed.
    """
    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "point")
    _create_marker(sdk, "Acme", "2026-01-01T00:00:00+00:00")

    _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert _marker_count(sdk) == 1, (
        "#5353/I2: the first-org guard reads this count and returns early "
        "when it is non-zero — 0 is what re-mints a marker with the control "
        "plane's name")


def test_rebuild_all_leaves_no_marker_when_the_graph_had_none(tmp_path):
    """A graph that never held a marker must not gain one.

    FAILING VALUE: no `:TeamMeta` after the rebuild and a zero gap — inventing a
    marker from an empty section would make “never provisioned” indistinguishable
    from “provisioned”, which is the state the guard exists to read.
    """
    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "point")
    assert _markers(sdk) == []

    counts = _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert _markers(sdk) == []
    assert counts["team_meta_expected"] == 0
    assert counts["team_meta_restored"] == 0
    assert counts["team_meta_gap"] == 0


def test_rebuild_journal_only_engine_keeps_the_marker(tmp_path):
    """The sibling engine: `rebuild(log)` wipes the same node.

    FAILING VALUE: the marker is present with its captured properties after a
    journal-only rebuild (pre-fix: absent). This engine has no `log_dir` and so
    no durable sidecar — the carrier is in memory — which is still strictly
    better than the silent destruction it replaces.
    """
    from tortoise.log import EventLog

    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "only event")
    _create_marker(sdk, "Acme", "2026-01-01T00:00:00+00:00")

    _proj(sdk).rebuild(
        EventLog(str(events / "events.jsonl")), confirm_destructive=True)

    assert _seen_points(sdk) == 1
    assert _markers(sdk) == [
        {"name": "Acme", "created": "2026-01-01T00:00:00+00:00"}], (
        "#5353: `rebuild()` wipes the same class — the journal-only engine must "
        "carry it too")


# ── 2. durability: the marker rides the sidecar, and recovers (I1) ───────────


def test_rebuild_all_carries_the_marker_in_the_prewipe_sidecar(
        tmp_path, monkeypatch):
    """The marker must be IN the sidecar, not merely read in memory.

    FAILING VALUE: the written payload's `team_meta` section is
    `[{"name": "Acme", "created": "..."}]`. A fix that only reads the live graph
    before the wipe writes no such section, and a crash between the write and
    the replay then loses the marker permanently — the exact #2943 window the
    sidecar exists to close.

    REACHABLE: the sidecar is written because the new `team_meta` section is
    itself non-empty (`snapshot_pending` is
    `any(merged[section] for section in _SNAPSHOT_SECTIONS)`), NOT because of the
    journaled Point — replaying that Point rebuilds it, so `synthetic_events`
    stays empty.
    """
    from tortoise import projection as pr

    written: list[dict] = []
    real = pr._write_prewipe_snapshot

    def spy(path, payload):
        written.append(payload)
        return real(path, payload)

    monkeypatch.setattr(pr, "_write_prewipe_snapshot", spy)

    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "journaled")
    _create_marker(sdk, "Acme", "2026-01-01T00:00:00+00:00")

    _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert written, "no pre-wipe sidecar was written — the spy missed"
    assert written[0]["team_meta"] == [
        {"name": "Acme", "created": "2026-01-01T00:00:00+00:00"}], (
        "#5353: the first-org marker must ride the durable pre-wipe sidecar")
    assert "team_meta_unknown" not in written[0], (
        "a rescue file this build wrote DID capture the class — it must not "
        "claim its own record is state-UNKNOWN")


def test_rebuild_all_recovers_the_marker_from_a_leftover_sidecar(tmp_path):
    """The sidecar-recovery path: the wipe already landed, the file is the only
    record.

    FAILING VALUE: with the live graph EMPTY (the interrupted run's wipe landed)
    and a leftover `team_meta` section carrying the marker, the retry restores
    it. Restoring from the pre-union local instead would restore nothing on
    exactly the path the sidecar exists for.

    REACHABLE: the sidecar is planted exactly as the interrupted run would have
    written it, and the batch entry keeps the file from being read as RETIRED
    (an entry-less file is a completed-rebuild artifact and is ignored).
    """
    sdk, events = _mk_sdk(tmp_path)
    assert _markers(sdk) == [], "the live graph must be the post-wipe, empty one"
    _plant(Path(_sidecar_path(events)),
           _sidecar_payload(
               batch_snapshot=[{"id": "b1"}],
               team_meta=[{"name": "Acme",
                           "created": "2026-01-01T00:00:00+00:00"}]))

    counts = _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert _markers(sdk) == [
        {"name": "Acme", "created": "2026-01-01T00:00:00+00:00"}], (
        "#5353: the durable carrier is the only record of the class once the "
        "wipe lands — dropping it leaves the guard reading 0")
    assert counts["team_meta_restored"] == 1
    assert counts["team_meta_state_unknown"] is False


def test_rebuild_all_reports_no_collapse_for_one_marker_in_both_legs(
        tmp_path, caplog):
    """The kill-between-the-sidecar-write-and-the-wipe window: ONE marker.

    FAILING VALUE: the interrupted run had already written the sidecar and was
    killed before `DETACH DELETE`, so the SAME marker is in the leftover file
    AND still live in the graph. The rebuild restores it, reports
    `team_meta_expected == 1`, and logs NO collapse warning. Counting ENTRIES
    across the two legs (1 + 1) instead of DISTINCT markers told the operator
    that "a graph holding several is already in violation" — about a graph
    holding exactly one — and every interrupted rebuild that got as far as the
    sidecar write would have accused the graph of the `#2789` violation.

    REACHABLE: the sidecar is planted exactly as that interrupted run would
    have written it, carrying the marker the live graph is holding.
    """
    sdk, events = _mk_sdk(tmp_path)
    marker = {"name": "Acme", "created": "2026-01-01T00:00:00+00:00"}
    _create_marker(sdk, marker["name"], marker["created"])
    assert _marker_count(sdk) == 1, "the fixture holds exactly one live marker"
    _plant(Path(_sidecar_path(events)),
           _sidecar_payload(batch_snapshot=[{"id": "b1"}], team_meta=[marker]))

    with caplog.at_level(logging.WARNING):
        counts = _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert _markers(sdk) == [marker], (
        "#5353: the collapse of one marker with itself restores that marker")
    assert counts["team_meta_expected"] == 1, (
        "one marker seen in BOTH legs is one marker, not two — counting the "
        "entries reports a collapse that never happened")
    assert counts["team_meta_restored"] == 1
    assert counts["team_meta_gap"] == 0
    assert not any("at-most-one" in r.getMessage() for r in caplog.records), (
        "#5353: a single marker present in the leftover sidecar and in the "
        "live graph is the DOCUMENTED interrupted-rebuild window — warning "
        "here accuses a compliant graph of the `#2789` violation")


# ── 3. the at-most-one merge rule, pinned directly ───────────────────────────


def test_team_meta_section_carries_at_most_one_marker():
    """The section's merge rule: earliest `created`, never a concatenation.

    FAILING VALUE: every union — either argument order, a hand-planted
    multi-entry section, an empty side — yields AT MOST ONE entry, and it is the
    one with the earliest `created`. A concatenating union yields two on the
    leftover+fresh case, which is the second `:TeamMeta` `#2789` exists to
    prevent: a rebuild would MANUFACTURE the very duplication the guard stops.

    REACHABLE: `_union_prewipe_snapshot` is the exact function the recovery path
    calls, driven here with the sections it receives.
    """
    from tortoise.projection import _SNAPSHOT_SECTIONS, _union_prewipe_snapshot

    def _sec(**kw):
        return {**{s: [] for s in _SNAPSHOT_SECTIONS}, **kw}

    early = {"name": "First", "created": "2026-01-01T00:00:00+00:00"}
    late = {"name": "Second", "created": "2026-06-01T00:00:00+00:00"}
    empty = _sec()

    def merged(left, right):
        return _union_prewipe_snapshot(left, right)["team_meta"]

    assert merged(_sec(team_meta=[early]), _sec(team_meta=[late])) == [early], (
        "the pre-wipe marker is the earlier one — the leftover leg must not "
        "have to win by ORDER, and the pair must never both come back")
    assert merged(_sec(team_meta=[late]), _sec(team_meta=[early])) == [early], (
        "the rule is earliest-created, not argument order")
    assert merged(empty, _sec(team_meta=[late])) == [late]
    assert merged(_sec(team_meta=[late]), empty) == [late]
    assert merged(None, empty) == []
    planted = _sec(team_meta=[late, early])
    assert merged(planted, empty) == [early], (
        "a hand-planted multi-entry section collapses to its EARLIEST too — "
        "and to exactly one entry")


def test_team_meta_collapse_tie_break_is_deterministic():
    """A tie on `created` is broken by the property map, not graph order.

    FAILING VALUE (pre-fix): with two markers sharing a `created`, the key
    `(0, created)` made `min()` return whichever the graph listed first, so the
    survivor was graph-answer-order dependent — the docstring claimed
    REPRODUCIBLE while the *input* order was arbitrary. The key now carries the
    marker's full property map, so both input orders yield the SAME survivor.
    """
    from tortoise.projection import _team_meta_marker

    a = {"name": "Acme", "created": "2026-01-01T00:00:00+00:00"}
    b = {"name": "Beta", "created": "2026-01-01T00:00:00+00:00"}
    assert _team_meta_marker([a, b]) == _team_meta_marker([b, a]), (
        "a tie on `created` must not depend on the capture order — the "
        "secondary key is what makes the collapse reproducible")
    assert _team_meta_marker([a, b]) == a, (
        "the property-map tie-break keeps the lexicographically first marker")


def test_team_meta_section_and_validator_are_wired():
    """The section cannot silently fall out of the sidecar plumbing.

    FAILING VALUE: `team_meta` is in `_SNAPSHOT_SECTIONS`, has an entry check,
    and the read/write version is 6 — one increment past #4653's v5, so a
    pre-#5353 binary REFUSES a v6 file instead of ignoring the section and
    wiping. Removing any one of these silently de-carries the class.

    REACHABLE: pure import-level assertions on the module constants.
    """
    from tortoise.projection import (
        _PREWIPE_SNAPSHOT_READABLE_VERSIONS,
        _PREWIPE_SNAPSHOT_VERSION,
        _SNAPSHOT_ENTRY_CHECK,
        _SNAPSHOT_SECTIONS,
        _TEAM_META_LABEL,
        _union_prewipe_snapshot,
        _validate_team_meta_entry,
    )

    assert _TEAM_META_LABEL == "TeamMeta", (
        "the one home for the label — a rename must not silently de-enrol the "
        "class from the capture, the restore and the tests")
    assert "team_meta" in _SNAPSHOT_SECTIONS
    assert _SNAPSHOT_ENTRY_CHECK["team_meta"] is _validate_team_meta_entry
    assert _PREWIPE_SNAPSHOT_VERSION == 6
    assert set(_PREWIPE_SNAPSHOT_READABLE_VERSIONS) >= {1, 2, 3, 4, 5, 6}
    # The union's return literal is hand-written per section, so pin its key
    # set against the tuple: a section added to `_SNAPSHOT_SECTIONS` and
    # forgotten in the literal would be silently dropped from the merged
    # snapshot that the write payload and the restore leg both read.
    assert set(_union_prewipe_snapshot(
        None, {s: [] for s in _SNAPSHOT_SECTIONS})) == set(_SNAPSHOT_SECTIONS)
    # The entry check is the untrusted boundary. There is deliberately NO
    # required key (the class has no identity property), but a non-storable
    # value must be refused BEFORE the wipe — it would otherwise die inside the
    # driver after `DETACH DELETE`.
    assert _validate_team_meta_entry({}) is None
    assert _validate_team_meta_entry({"name": "Acme"}) is None
    assert _validate_team_meta_entry(
        {"name": "Acme", "created": "t", "tags": ["a", "b"]}) is None
    for bad in ("Acme", 3, None, ["name"], {"nested": {"a": 1}},
                {"name": {"a": 1}}, {"ok": 1, "bad": {"a": 1}}):
        assert _validate_team_meta_entry(bad) is not None, bad


def test_rebuild_all_collapses_several_markers_and_reports_it(tmp_path, caplog):
    """A graph that already violated the at-most-one invariant is repaired and
    the repair is REPORTED.

    FAILING VALUE: the graph ends with exactly ONE marker — the earliest
    `created` — and the run logs a WARNING naming both counts. Restoring both
    would leave the graph in the state `#2789` exists to prevent, and restoring
    one silently would make a pre-existing invariant violation
    indistinguishable from a marker that was never there.
    """
    sdk, events = _mk_sdk(tmp_path)
    _create_marker(sdk, "Second", "2026-06-01T00:00:00+00:00")
    _create_marker(sdk, "First", "2026-01-01T00:00:00+00:00")
    assert _marker_count(sdk) == 2, "the fixture must really hold two markers"

    with caplog.at_level(logging.WARNING):
        counts = _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert _markers(sdk) == [
        {"name": "First", "created": "2026-01-01T00:00:00+00:00"}], (
        "#5353: the class is at-most-one — the earliest marker is the one the "
        "`#2789` retry rule would keep, and the others must NOT be recreated")
    assert counts["team_meta_expected"] == 1
    assert any("at-most-one" in r.getMessage() for r in caplog.records), (
        "the collapse must be reported, not silent")


# ── 4. the guard rails: I3, the refusal message, the pre-#5353 UNKNOWN ───────


def test_rebuild_all_refuses_before_the_wipe_when_the_marker_capture_fails(
        tmp_path, monkeypatch):
    """I3: a failed marker read aborts BEFORE the destructive wipe.

    FAILING VALUE: `rebuild_all` raises and the graph is untouched (same marker
    count, same properties, same Points). Without the `capture_failed` leg a
    failed read falls through to the wipe and the class is destroyed with no
    durable record — the #2943 rule that a graph which cannot answer a read is
    not evidence the wipe is safe.
    """
    from tortoise import projection as pr

    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "keep me")
    _create_marker(sdk, "Acme", "2026-01-01T00:00:00+00:00")
    before_points = _seen_points(sdk)
    before_markers = _markers(sdk)
    assert before_markers

    def boom(_proj):
        raise RuntimeError("simulated unreadable :TeamMeta")

    monkeypatch.setattr(pr, "_capture_team_meta", boom)
    with pytest.raises(RuntimeError, match="graph wipe"):
        _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert _seen_points(sdk) == before_points, "the wipe must not have run"
    assert _markers(sdk) == before_markers, "the marker must be untouched"


def test_team_meta_only_write_failure_names_the_marker(tmp_path, monkeypatch):
    """A marker-only refusal must name the class, not "0 of everything".

    FAILING VALUE: the pre-wipe write fails while the ONLY section pending is
    the new `team_meta` one (nothing is journaled, so `synthetic_events` stays
    empty) — and the refusal must say so. Without the note the message reads
    "Wiping now would destroy 0 graph-only Point event(s), 0 :Batch marker(s),
    ... and the 0 captured authoritative config entr(y/ies)", i.e. it claims to
    protect nothing while refusing the rebuild.
    """
    from tortoise import projection as pr

    sdk, events = _mk_sdk(tmp_path)
    _create_marker(sdk, "Acme", "2026-01-01T00:00:00+00:00")

    def boom(path, payload):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(pr, "_write_prewipe_snapshot", boom)
    with pytest.raises(RuntimeError) as exc:
        _proj(sdk).rebuild_all(str(events), confirm_destructive=True)
    msg = str(exc.value)
    assert "aborted BEFORE the graph wipe" in msg
    assert ":TeamMeta first-org marker" in msg, (
        "the refusal must name the one section that made the snapshot pending "
        "(#5353), or it reads as protecting 0 of everything")
    assert _markers(sdk) == [
        {"name": "Acme", "created": "2026-01-01T00:00:00+00:00"}], \
        "the wipe must not have run"


def test_rebuild_all_reports_a_pre_5353_sidecar_as_state_unknown(
        tmp_path, monkeypatch, caplog):
    """A rescue file written before the class had a vehicle cannot say whether
    the earlier wipe destroyed a marker.

    FAILING VALUE: the run logs an ERROR naming the UNKNOWN, returns
    `team_meta_state_unknown: True` and `team_meta_gap: 1`, and CARRIES the flag
    into the rescue file this run writes — so a second interruption cannot lose
    the evidence behind a present-but-empty section. Silently proceeding would
    report a rebuilt graph as complete while whether its `#2789` marker was
    destroyed is unknowable, which is the state-UNKNOWN this signal exists for.

    REACHABLE: the payload is a genuine v5 file (this build's own previous
    format) with NO `team_meta` key at all, and the live graph is genuinely
    marker-free — one of the states the recovery path can be in, and the one in
    which the marker is lost.
    """
    from tortoise import projection as pr

    written: list[dict] = []
    real = pr._write_prewipe_snapshot

    def spy(path, payload):
        written.append(payload)
        return real(path, payload)

    monkeypatch.setattr(pr, "_write_prewipe_snapshot", spy)

    sdk, events = _mk_sdk(tmp_path)
    payload = _sidecar_payload(version=5, batch_snapshot=[{"id": "b1"}])
    payload.pop("team_meta")
    _plant(Path(_sidecar_path(events)), payload)

    with caplog.at_level(logging.ERROR):
        counts = _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert _markers(sdk) == [], "a v5 file cannot fabricate a marker"
    assert counts["team_meta_expected"] == 0
    assert counts["team_meta_state_unknown"] is True
    assert counts["team_meta_gap"] == 1
    assert any("CANNOT be determined" in r.getMessage()
               for r in caplog.records), (
        "the pre-preservation UNKNOWN must be an ERROR naming its consequence "
        "— not a silent clean rebuild")
    # The spy sees THREE writes: the planted v5 file (`_plant` goes through the
    # same writer), this run's own current-version rescue file, and the
    # entry-less retirement artifact. Pin the middle one.
    own = [p for p in written
           if p.get("version") == 6 and not p.get("completed")]
    assert own and own[-1].get("team_meta_unknown") is True, (
        "#5353: the UNKNOWN must ride THIS run's sidecar, or a second "
        "interruption leaves a present-but-empty section that reads as "
        "'captured and empty'")


def test_2789_guard_short_circuits_on_the_rebuilt_marker_count(monkeypatch):
    """I2 at the decision seam: the guard mints on 0 and does NOT on 1.

    FAILING VALUE: with the probe answering 1 — the post-rebuild shape this
    change produces — `_eager_provision_org_graph` issues NO TeamMeta CREATE at
    all. With the probe answering 0 (the pre-fix post-rebuild shape) it DOES,
    which is the re-mint this issue is about. Asserted on the Cypher actually
    issued, so a passing comment cannot satisfy it.

    HERMETIC by construction: the graph handle is a recording stand-in, so no
    control plane, DB or `org_*` graph is touched.
    """
    import tortoise.hosted_api as ha_mod

    issued: list[str] = []

    class _Graph:
        def __init__(self, count):
            self._count = count

        def query(self, cypher, params=None):
            issued.append(cypher)
            if "MATCH (m:TeamMeta)" in cypher:
                return SimpleNamespace(result_set=[[self._count]])
            return SimpleNamespace(result_set=[])

    def sdk_for(count):
        graph = _Graph(count)
        proj = SimpleNamespace(
            db=SimpleNamespace(select_graph=lambda _name: graph))
        return SimpleNamespace(_get_proj=lambda: proj)

    probe = [0]
    monkeypatch.setattr(ha_mod, "_make_sdk", lambda **kw: sdk_for(probe[0]))

    def run(count):
        probe[0] = count
        issued.clear()
        return ha_mod._eager_provision_org_graph(
            object(), "tid1", "Renamed", "u1", prior_org_ids=[])

    assert run(0) == "org_tid1"
    assert any(":TeamMeta" in c and "CREATE" in c for c in issued), (
        "the fixture must be able to mint — otherwise the 1-probe pin below "
        "cannot distinguish 'short-circuited' from 'could not mint'")

    assert run(1) == "org_tid1"
    assert not [c for c in issued if ":TeamMeta" in c and "CREATE" in c], (
        "#2789/I2: a non-zero probe IS the post-rebuild shape — the guard must "
        "return early, not mint a second marker with the control plane's name")


def test_rebuild_all_honours_a_carried_team_meta_unknown_flag(tmp_path, caplog):
    """The carried flag must LOAD, and must keep reporting the UNKNOWN.

    FAILING VALUE: a **v6** file — this build's own current format, so BOTH
    cheaper gates are satisfied and cannot supply the signal
    (`leftover_version < 6` is False, and `"team_meta" not in leftover` is
    False because the section is present-but-empty) — carrying
    `team_meta_unknown: True` is accepted by the loader and the flag is carried
    to the reporting leg: the rebuild completes (no RuntimeError from the
    unknown-section refusal) and reports `team_meta_state_unknown: True`. This
    is the SECOND interruption of an already-interrupted rebuild; without the
    inherited-flag clause the retry reports a clean rebuild while the marker's
    fate is unknown. `team_meta_unknown` is a METADATA key, not a section:
    without that registration the LOADER would REFUSE the file as carrying a
    foreign section, which would make a second interruption unrecoverable — the
    worst possible failure for the artifact whose whole job is recovery.

    MUTATION-PROVEN: deleting the inherited-flag clause from the state-UNKNOWN
    predicate (`bool((leftover or {}).get("team_meta_unknown"))`) leaves this
    test RED; a v5 version-gate fixture would stay green, because only the
    version gate fired. The version gate has its own pin,
    `test_rebuild_all_reports_a_pre_5353_sidecar_as_state_unknown`.
    """
    sdk, events = _mk_sdk(tmp_path)
    payload = _sidecar_payload(
        batch_snapshot=[{"id": "b1"}], team_meta_unknown=True)
    assert payload["version"] == 6 and payload["team_meta"] == [], (
        "the fixture must plant the CURRENT version with a present-but-empty "
        "section, so only the inherited flag can produce the UNKNOWN")
    _plant(Path(_sidecar_path(events)), payload)

    with caplog.at_level(logging.ERROR):
        counts = _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert counts["team_meta_state_unknown"] is True, (
        "the flag staged by an earlier interrupted run must be inherited — "
        "this run's sections cannot re-derive it")
    assert counts["team_meta_gap"] == 1
    assert counts["nodes"] >= 0, "the rebuild completed, not a refusal"


# ── 5. the declaration (T2) ──────────────────────────────────────────────────

_DOC = (Path(__file__).resolve().parent.parent
        / "docs" / "durability-posture.md")


def _doc_block(name: str) -> str:
    """The text between `<!-- config-registry:<name> -->` and the next `:end`."""
    doc = _DOC.read_text(encoding="utf-8")
    open_tag = f"<!-- config-registry:{name} -->"
    assert open_tag in doc, f"docs/durability-posture.md has no {open_tag}"
    body = doc.split(open_tag, 1)[1]
    assert "<!-- config-registry:end -->" in body, (
        f"the {open_tag} block is unterminated")
    return body.split("<!-- config-registry:end -->", 1)[0]


def test_marker_declared_in_the_durability_posture():
    """T2: the class has a DECLARED disposition, not an inference.

    FAILING VALUE: `:TeamMeta` appears in the `preserved-sections` block with
    its section, its restore rule and #5353, and NOT in `unenrolled` — the block
    that means "no vehicle in this change", which is exactly what the doc said
    about it before this fix.

    REACHABLE: the doc is the repo's canonical durability authority and the
    blocks are literals in it.
    """
    block = _doc_block("preserved-sections")
    labels = set(re.findall(r"`:(\w+)`", block))
    assert "TeamMeta" in labels, (
        "the section-preserved block must name :TeamMeta")
    assert "team_meta" in block, "the block must name the carrier section"
    assert "#5353" in block
    assert "2789" in block, (
        "the declaration must state the load-bearing consequence — the "
        "first-org guard reads exactly this class")
    unenrolled = _doc_block("unenrolled")
    assert "TeamMeta" not in unenrolled, (
        "#5353 put a vehicle behind the class — it is no longer 'unenrolled'")
    audit = _doc_block("audit-query")
    assert ":TeamMeta" in audit, (
        "the operator audit must enumerate the class, or it under-reports what "
        "survived a rebuild")
