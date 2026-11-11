"""#9752 — `rebuild_all` must not wipe the `:PackSelection` pack-set marker.

`:PackSelection` is the one node per graph whose **presence** says "this graph's
pack set was DELIBERATELY configured" (#2728 / PR #8648). `pack_state` writes it
with a bare ``MERGE (s:PackSelection)`` — no identity property, because the graph
is the isolation boundary — so it rides no journal record and no replay pass
recreates it. `rg -n "PackSelection" tortoise/projection/` was **ZERO hits**
before this change, which is why the unconditional wipe in `rebuild_all`
preserved the `:PackInstall` rows (a `#2814` config-registry class) and dropped
the marker that says the set was chosen.

The measured consequence is not bookkeeping (this worktree, embedded lane):

    before : configured=True   revision=1   namespaces=frozenset()
    after  : configured=False  revision=0   namespaces=frozenset({'venture'})

i.e. a pack an admin **REMOVED** comes back into the approved set, the
`revision` a client holds resets to 0, `get_tenant_packs`' self-heal becomes
live again on a graph that was pinned — and the Settings surface then states the
opposite of the truth ("the full catalog applies") over a set that holds the
removed namespace.

The class is CARRIED in the durable #2943 pre-wipe sidecar (`pack_selection`
section, snapshot version 7) because it fits neither existing vehicle: the
`#2814` config registry keys a row on a **single string identity property**, and
`_capture_config_snapshot` silently `continue`s past an instance whose identity
is not a string — so "enrol it as a config class" would capture NOTHING and pass
a green sweep while leaving both defects in place (the decision relay on
#9752). It is one-per-graph by construction, so the section carries at most one
marker, chosen by **highest `revision`** (the value an optimistic-concurrency
client holds), and a collapse is reported rather than silent.

Lane: either, mirroring `tests/test_rebuild_team_meta_marker_5353.py`. The
fixtures build the SDK the same way the embedded carve-out suite does, and the
bulk-wipe guard passes on the redirected `test_*_tortoise` graph too.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK

# ── harness ──────────────────────────────────────────────────────────────────


def _mk_sdk(tmp_path, name="ps.db", namespace=None):
    events = tmp_path / "events"
    events.mkdir(parents=True, exist_ok=True)
    sdk = TortoiseSDK(
        db_path=str(tmp_path / name),
        # A `test_`-prefixed namespace keeps the bulk-wipe guard passing under
        # the docker redirect (graph name becomes `test_..._tortoise`).
        namespace=namespace or f"test_ps_{os.urandom(4).hex()}",
        event_log_path=str(events / "events.jsonl"),
    )
    return sdk, events


def _proj(sdk):
    return sdk._get_proj()


def _write_journal(events_dir, records: list[dict] | None = None) -> None:
    (events_dir / "events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records or []))


def _markers(sdk) -> list[dict]:
    from tortoise.projection import _PACK_SELECTION_LABEL

    rows = _proj(sdk).g.query(
        f"MATCH (s:{_PACK_SELECTION_LABEL}) RETURN properties(s)").result_set
    return [r[0] for r in rows]


def _resolved(sdk):
    """The graph's own read core — what enforcement actually consumes."""
    from tortoise.pack_state import resolved_pack_set

    return resolved_pack_set(sdk)


def _configure(sdk, packs: list[dict], expected_revision=None):
    from tortoise.pack_state import set_graph_packs

    return set_graph_packs(
        sdk, packs=packs, expected_revision=expected_revision)


def _sidecar_path(events_dir) -> str:
    from tortoise.projection import prewipe_snapshot_path
    return prewipe_snapshot_path(str(events_dir))


def _plant(path, payload: dict) -> None:
    from tortoise.projection import _write_prewipe_snapshot
    _write_prewipe_snapshot(str(path), payload)


def _sidecar_payload(**overrides) -> dict:
    """A complete CURRENT-version shape with every section empty.

    Complete on purpose: an incomplete shape fires the state-UNKNOWN ERRORs and
    would mask the pin under test.
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


# ── the defect, and its removal ─────────────────────────────────────────────


def test_rebuild_all_preserves_the_pack_selection_marker(tmp_path):
    """A configured graph stays configured, with the same revision and set.

    FAILING VALUE: before #9752's fix the marker is gone after the rebuild, so
    `resolved_pack_set` returns `configured=False, revision=0` and the
    records/union fallback arms decide the graph's approved packs — measured
    ``configured=True revision=1`` → ``configured=False revision=0``.

    REACHABLE: the marker is written by the real writer (`set_graph_packs`,
    which is the only path that moves the revision), and the rebuild is the real
    `rebuild_all` wipe+replay with an empty journal.
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _configure(sdk, [{"namespace": "dev", "status": "active"}])
    before = _resolved(sdk)
    before_markers = _markers(sdk)
    assert before.configured is True and before.revision == 1
    assert before.namespaces == frozenset({"dev"})

    _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    after = _resolved(sdk)
    assert after.configured is True, (
        "the marker was dropped by the rebuild — the graph now reads as never "
        "configured, so the self-heal and the union fallback are live (#9752)")
    assert after.revision == before.revision, (
        "the revision a client holds was reset — an optimistic-concurrency "
        "client would silently re-base onto a different decision")
    assert after.namespaces == before.namespaces
    assert _markers(sdk) == before_markers, (
        "the marker must survive byte-identically (the capture is faithful, and "
        "the restore is an idempotent MERGE of the captured property map)")


def test_rebuild_all_does_not_readmit_a_removed_pack(tmp_path):
    """#9752's acceptance: a tombstone-only graph does not get the pack back.

    FAILING VALUE: `venture` is removed (the desired set is EMPTY, which is
    expressible only because the marker exists), so the pre-rebuild resolved set
    is `frozenset()`. Without the marker after the rebuild, the
    records-present/union arm unions `recorded(any status)` — the `removed` row
    — into the approved set, so `visit`/`venture` kinds become mintable again on
    a graph whose admin removed that pack.

    REACHABLE: `set_graph_packs` with an empty desired set tombstones the
    namespace (`status='removed'`, kept for audit), which is the product path a
    removal takes.
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _configure(sdk, [{"namespace": "venture", "status": "active"}])
    _configure(sdk, [], expected_revision=1)
    before = _resolved(sdk)
    assert before.configured is True
    assert before.namespaces == frozenset(), (
        "the fixture must start from an explicitly EMPTY set — the state only "
        "the marker can express")
    rows = _proj(sdk).g.query(
        "MATCH (p:PackInstall) RETURN p.namespace, p.status").result_set
    assert ["venture", "removed"] in [list(r) for r in rows]

    _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    after = _resolved(sdk)
    assert "venture" not in (after.namespaces or frozenset()), (
        "the REMOVED pack was re-admitted into the approved set by the rebuild "
        "— an approval the admin revoked is silently back (#9752)")
    assert after.namespaces == before.namespaces
    assert after.configured is True and after.revision == before.revision


def test_rebuild_all_leaves_no_marker_when_the_graph_had_none(tmp_path):
    """Preservation must not MANUFACTURE a marker.

    FAILING VALUE: a graph that was never configured (`configured=False`,
    catalog-union arm) comes back configured, which would silently pin a set the
    user never chose and disable the self-heal they rely on.

    REACHABLE: a fresh graph with no `:PackInstall` records and no marker — the
    pre-#318 / self-hosted shape.
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    assert _resolved(sdk).configured is False
    assert _markers(sdk) == []

    _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert _markers(sdk) == [], (
        "the rebuild invented a pack-selection decision the graph never had")
    assert _resolved(sdk).configured is False


def test_rebuild_journal_only_engine_keeps_the_marker(tmp_path):
    """The IN-MEMORY `rebuild()` path carries it too (no sidecar there).

    FAILING VALUE: the marker is held in memory and restored after the wipe; with
    no restore it is destroyed on this path as well, and this engine has no
    `log_dir`, so nothing else can recover it. A destroyed marker is not neutral:
    it makes the graph read as UNCONFIGURED, which is the fallback arm that
    re-admits a pack the admin removed.

    SCOPE, stated so this test cannot be read as claiming more: `rebuild()` has
    never carried the `#2814` config registry (no sidecar → no `:PackInstall`
    rows), and that is a PRE-EXISTING property of this engine, not of this
    change. So the assertion here is the marker's survival (`configured`,
    `revision`) and that the result is not the unrestricted union — the resolved
    set on this path comes from the marker's arm plus the data leg, and the
    caller who needs the config rows back routes through `rebuild_all(log_dir)`.

    REACHABLE: `FalkorProjection.rebuild()` is the journal-events engine (the
    `consistency`/CLI callers route through `rebuild_all`, which carries the
    marker durably — this test pins the strictly-narrower in-memory case).
    """
    from tortoise.log import EventLog

    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _configure(sdk, [{"namespace": "dev", "status": "active"}])
    before = _resolved(sdk)

    _proj(sdk).rebuild(
        EventLog(str(events / "events.jsonl")), confirm_destructive=True)

    after = _resolved(sdk)
    assert after.configured is True, (
        "`rebuild()` wipes the same class — the journal-only engine must carry "
        "the marker too, or the graph reads as never configured")
    assert after.revision == before.revision
    assert after.namespaces is not None, (
        "a marker-bearing graph must NOT resolve to the catalog-union fallback "
        "— that is the arm that re-admits a removed pack (#9752)")


# ── the carrier: capture, union, restore, report ────────────────────────────


def test_rebuild_all_carries_the_marker_in_the_prewipe_sidecar(
        tmp_path, monkeypatch):
    """The marker must be in the DURABLE artifact, not only in memory.

    FAILING VALUE: the sidecar is written because the `pack_selection` section
    is itself non-empty (`snapshot_pending` is
    `any(merged[section] for section in _SNAPSHOT_SECTIONS)`). Without the
    section, a config-only graph takes the `elif` branch, writes NO sidecar, and
    a kill between the write and the wipe loses the marker with no record.

    REACHABLE: a graph whose only graph-resident state is the marker.
    """
    from tortoise import projection as pr

    written: list[dict] = []
    real = pr._write_prewipe_snapshot

    def spy(path, payload):
        written.append(payload)
        return real(path, payload)

    monkeypatch.setattr(pr, "_write_prewipe_snapshot", spy)

    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _configure(sdk, [{"namespace": "dev", "status": "active"}])
    captured = _markers(sdk)

    _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert written, "no pre-wipe sidecar was written — the spy missed"
    assert written[0]["pack_selection"] == captured, (
        "#9752: the marker must ride the durable pre-wipe sidecar, or a kill "
        "between the capture and the wipe loses the graph's pack decision")
    assert "pack_selection_unknown" not in written[0], (
        "a rescue file this build wrote DID capture the class — it must not "
        "claim its own record is state-UNKNOWN")


def test_rebuild_all_recovers_the_marker_from_a_leftover_sidecar(tmp_path):
    """The sidecar-recovery path: the wipe already landed, the file is the only
    record.

    FAILING VALUE: with the live graph EMPTY (the interrupted run's wipe landed)
    and a leftover `pack_selection` section carrying the marker, the retry
    restores it. Restoring from the pre-union local instead restores nothing on
    exactly the path the sidecar exists for.

    REACHABLE: the sidecar is planted exactly as the interrupted run would have
    written it, and a batch entry keeps the file from being read as RETIRED (an
    entry-less file is a completed-rebuild artifact and is ignored).
    """
    from tortoise.projection import _PACK_SELECTION_LABEL

    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    marker = {"revision": 4, "configured_at": "2026-01-01T00:00:00Z",
              "updated_by": "owner"}
    _plant(Path(_sidecar_path(events)), _sidecar_payload(
        batch_snapshot=[{"id": "b1"}], pack_selection=[marker]))

    _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert _markers(sdk) == [marker], (
        "the recovered marker did not reach the rebuilt graph — the union's "
        "`pack_selection` leg or the restore write is missing")
    assert _proj(sdk).g.query(
        f"MATCH (s:{_PACK_SELECTION_LABEL}) RETURN count(s)"
    ).result_set[0][0] == 1, (
        "the restore must be an idempotent MERGE of ONE marker, never a "
        "duplicate — two markers make `_read_selection`'s single-row read "
        "ambiguous")


def test_pack_selection_section_carries_at_most_one_marker():
    """The union collapses to one marker, HIGHEST revision, deterministically.

    FAILING VALUE: a concatenating union would carry both markers, so the
    restored graph holds two nodes and `pack_state._read_selection` (a
    `MATCH (s:PackSelection) RETURN …` with no key) reads whichever row the
    graph answers first — the enforced pack set would depend on row order.
    Highest-revision rather than first-encountered is the safe rule: `revision`
    is the token an optimistic-concurrency client holds.

    REACHABLE: the legs genuinely differ on the interrupted-rebuild path (a
    post-wipe `set_graph_packs` creates a fresh low-revision marker beside the
    leftover's).
    """
    from tortoise.projection import _SNAPSHOT_SECTIONS, _union_prewipe_snapshot

    def fresh(**kw):
        return {**{s: [] for s in _SNAPSHOT_SECTIONS}, **kw}

    old = {"revision": 5, "updated_by": "owner"}
    new = {"revision": 1, "updated_by": "admin"}
    merged = _union_prewipe_snapshot(
        fresh(pack_selection=[old]), fresh(pack_selection=[new]))
    assert merged["pack_selection"] == [old], (
        "the highest revision must win — restoring the lower one would move the "
        "graph's concurrency token backwards")
    # Argument order must not decide it.
    assert _union_prewipe_snapshot(
        fresh(pack_selection=[new]), fresh(pack_selection=[old])
    )["pack_selection"] == [old]
    # One marker in both legs (a kill between the sidecar write and the DELETE)
    # is ONE marker, not a collision.
    assert _union_prewipe_snapshot(
        fresh(pack_selection=[old]), fresh(pack_selection=[old])
    )["pack_selection"] == [old]
    # No marker anywhere stays empty — preservation never invents a decision.
    assert _union_prewipe_snapshot(None, fresh())["pack_selection"] == []


def test_pack_selection_collapse_tie_break_is_deterministic():
    """Two markers at the SAME revision still collapse reproducibly."""
    from tortoise.projection import _pack_selection_marker

    a = {"revision": 2, "updated_by": "aaa"}
    b = {"revision": 2, "updated_by": "bbb"}
    assert _pack_selection_marker([a, b]) == _pack_selection_marker([b, a]), (
        "a revision tie must not depend on the capture order — the property-map "
        "tie-break is what makes the collapse reproducible")
    # A marker with a usable revision beats a degenerate one.
    assert _pack_selection_marker(
        [{"updated_by": "no-revision"}, a]) == a
    assert _pack_selection_marker([]) is None


def test_pack_selection_section_and_validator_are_wired():
    """The section cannot silently fall out of the sidecar plumbing.

    FAILING VALUE: `pack_selection` is in `_SNAPSHOT_SECTIONS`, has an entry
    check, and the read/write version is at least 7 — one increment past
    #5353's v6, so a v6 build REFUSES the file instead of ignoring the section
    and wiping. Removing any one of these silently de-carries the class.

    REACHABLE: pure import-level assertions on the module constants.
    """
    from tortoise.pack_state import PACK_SELECTION_LABEL
    from tortoise.projection import (
        _PACK_SELECTION_LABEL,
        _PREWIPE_SNAPSHOT_READABLE_VERSIONS,
        _PREWIPE_SNAPSHOT_VERSION,
        _SNAPSHOT_ENTRY_CHECK,
        _SNAPSHOT_SECTIONS,
        _union_prewipe_snapshot,
        _validate_pack_selection_entry,
    )

    assert _PACK_SELECTION_LABEL == PACK_SELECTION_LABEL == "PackSelection", (
        "the projection's copy of the label must match `pack_state`'s — the "
        "capture, the restore and the writer must name the same node")
    assert "pack_selection" in _SNAPSHOT_SECTIONS
    assert _SNAPSHOT_ENTRY_CHECK["pack_selection"] is \
        _validate_pack_selection_entry
    assert _PREWIPE_SNAPSHOT_VERSION >= 7, (
        "`:PackSelection` landed at v7; a LOWER read/write version would let a "
        "pre-#9752 build accept the file and ignore the section")
    assert set(_PREWIPE_SNAPSHOT_READABLE_VERSIONS) >= {1, 2, 3, 4, 5, 6, 7}
    # The union's return literal is hand-written per section, so pin its key set
    # against the tuple: a section added to `_SNAPSHOT_SECTIONS` and forgotten
    # in the literal would be silently dropped from the merged snapshot that the
    # write payload and the restore leg both read.
    assert set(_union_prewipe_snapshot(
        None, {s: [] for s in _SNAPSHOT_SECTIONS})) == set(_SNAPSHOT_SECTIONS)
    # The entry check is the untrusted boundary. There is deliberately NO
    # required key (the class has no identity property and `_read_selection`
    # already tolerates a malformed one); a non-storable value must be refused
    # BEFORE the wipe, because it would otherwise die inside the driver after
    # `DETACH DELETE`.
    assert _validate_pack_selection_entry({}) is None
    assert _validate_pack_selection_entry({"revision": 3}) is None
    assert _validate_pack_selection_entry(
        {"revision": 3, "updated_by": "o", "tags": ["a", "b"]}) is None
    for bad in ("marker", 3, None, ["revision"], {"nested": {"a": 1}},
                {"revision": {"a": 1}}, {"ok": 1, "bad": {"a": 1}}):
        assert _validate_pack_selection_entry(bad) is not None, bad


def test_rebuild_all_reports_a_pre_9752_sidecar_as_state_unknown(
        tmp_path, caplog):
    """A rescue file written before the class had a vehicle is reported UNKNOWN.

    FAILING VALUE: the run logs an ERROR naming the UNKNOWN, returns
    `pack_selection_state_unknown: True` and `pack_selection_gap: 1`. Treating
    the absent section as "captured and empty" would conclude the graph was
    NEVER configured — i.e. silently adopt the union fallback that re-admits a
    removed pack — over a file that simply could not record the class.

    REACHABLE: a genuine v6 file (this build's own previous format) with no
    `pack_selection` key at all, and a live graph that is marker-free.
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    payload = _sidecar_payload(batch_snapshot=[{"id": "b1"}], version=6)
    payload.pop("pack_selection")
    _plant(Path(_sidecar_path(events)), payload)

    with caplog.at_level(logging.ERROR):
        counts = _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert counts["pack_selection_expected"] == 0
    assert counts["pack_selection_state_unknown"] is True
    assert counts["pack_selection_gap"] == 1
    assert any("CANNOT be determined" in r.getMessage()
               and ":PackSelection" in r.getMessage()
               for r in caplog.records), (
        "the pre-preservation UNKNOWN must be an ERROR naming its consequence — "
        "not a silent clean rebuild")


def test_rebuild_refuses_before_the_wipe_when_the_marker_capture_fails(
        tmp_path, monkeypatch):
    """A failed marker read must not fall through to the wipe (#2943, I3).

    FAILING VALUE: with `_capture_pack_selection` raising, the rebuild aborts
    with "aborted BEFORE the graph wipe" and the marker is still there. A
    best-effort `except: pass` would wipe a graph it could not read, destroying
    the marker with no durable record — the exact loss class the sidecar exists
    to prevent.

    REACHABLE: monkeypatch the capture to raise (a corrupt/heavy read is the
    real cause; the discipline is what is under test).
    """
    from tortoise import projection as pr

    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _configure(sdk, [{"namespace": "dev", "status": "active"}])
    before = _markers(sdk)

    def boom(_proj_):
        raise OSError("simulated capture failure")

    monkeypatch.setattr(pr, "_capture_pack_selection", boom)
    with pytest.raises(RuntimeError) as exc:
        _proj(sdk).rebuild_all(str(events), confirm_destructive=True)
    assert "aborted BEFORE the graph wipe" in str(exc.value)
    assert _markers(sdk) == before, "the wipe must not have run"
    assert not os.path.exists(_sidecar_path(events))


# ── #9752: the gap has the same producer → consumer contract as #5353 ───────


def _pack_selection_only_gap_sidecar() -> dict:
    """A rescue file whose `pack_selection` record is UNKNOWN and nothing else is.

    Version 6 (this build's previous format, i.e. pre-#9752) with no
    `pack_selection` key at all: every other section is present and empty, so
    the ONLY gap this file produces is the pack-set marker's — the shape the
    recovery consumers had no *reachable* branch for.
    """
    payload = _sidecar_payload(batch_snapshot=[{"id": "b1"}], version=6)
    payload.pop("pack_selection")
    return payload


def test_pack_selection_gap_is_visible_to_recover_from_log(tmp_path):
    """The automatic-recovery ENTRY POINT must not return a clean success (#9752).

    FAILING VALUE: `recover_from_log` built its result dict from a WHITELIST —
    it copied `onboarding_*` and `team_meta_*` and nothing else — so
    `pack_selection_gap` was dropped on the floor here even though
    `rebuild_all` had computed it. Both consumers below
    (`_auto_health_recover`, `_recover_or_raise`) read the key off THIS dict, so
    without this hop they were unreachable dead code and the class the change
    exists to make durable was still silent on the auto-recovery path.

    REACHABLE: the pending sidecar is retired after the replay, so these keys
    plus the appended `reason` clause are the only record the entry point can
    hand its caller. `recovered` stays True — the rebuild DID complete — exactly
    as the onboarding leg does.
    """
    from tortoise.consistency import recover_from_log

    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _plant(Path(_sidecar_path(events)), _pack_selection_only_gap_sidecar())
    _proj(sdk).g.query("MATCH (n) DETACH DELETE n")

    rec = recover_from_log(str(events), _proj(sdk))

    assert rec["recovered"] is True, "the rebuild itself did complete"
    assert rec.get("pack_selection_gap") == 1, (
        "a missing/unconfirmable pack-set marker must be machine-visible to "
        "the auto-recovery caller — otherwise both warning legs are dead code")
    assert rec.get("pack_selection_state_unknown") is True, (
        "a pre-preservation sidecar cannot say whether the marker was "
        "destroyed")
    assert ":PackSelection" in rec["reason"], (
        "`reason` is the only record in-repo callers log for a completed "
        "recovery; it must name the class it could not confirm")


def test_auto_health_recover_warns_on_the_pack_selection_gap(
        tmp_path, caplog, monkeypatch):
    """The embedded auto-recovery consumer of `pack_selection_gap` acts on it.

    FAILING VALUE: `_auto_health_recover` is an `elif` chain whose clean
    "auto-recovered" line DROPS `reason`, so a pack-set-only gap produced a
    success-shaped log over a graph that now reads as UNCONFIGURED. Deleting
    the branch reddened nothing before this pin (mutation-verified in the
    review: "NO ROW REDDENS").

    REACHABLE: the real `_auto_health_recover` on the probe-OK lost-graph path,
    with the discovery seam pointed at the test's events dir.
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _plant(Path(_sidecar_path(events)), _pack_selection_only_gap_sidecar())
    _proj(sdk).g.query("MATCH (n) DETACH DELETE n")
    proj = _proj(sdk)
    monkeypatch.setattr(type(proj), "_find_local_jsonl_dir",
                        lambda self: str(events))
    monkeypatch.delenv("FLY_APP_NAME", raising=False)

    with caplog.at_level(logging.WARNING, logger="tortoise.projection"):
        proj._auto_health_recover()

    # Assert the CALLER's own message, not a bare `:PackSelection` substring:
    # `rebuild_all` already logs an ERROR naming the class on this path, so a
    # looser match would pass with the consumer removed.
    assert any("auto-recovered empty embedded DB" in r.getMessage()
               and ":PackSelection pack-set marker" in r.getMessage()
               and "NOT confirmed intact" in r.getMessage()
               for r in caplog.records), (
        f"the auto-recovery consumer discarded the marker gap: "
        f"{[r.getMessage() for r in caplog.records]}")


def test_pack_selection_gap_warns_in_the_recover_or_raise_caller(
        tmp_path, caplog):
    """A producer signal with no consumer is the same log-only defect (#9752).

    FAILING VALUE: `_recover_or_raise` is the real caller on the
    unresponsive-graph path, the one where NO other surface reports the gap. It
    must warn on a pack-set gap and must NOT raise — the store is usable, so
    refusing to open would be strictly worse.
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _plant(Path(_sidecar_path(events)), _pack_selection_only_gap_sidecar())
    _proj(sdk).g.query("MATCH (n) DETACH DELETE n")

    with caplog.at_level(logging.WARNING, logger="tortoise.projection"):
        _proj(sdk)._recover_or_raise(str(events))  # must not raise

    assert any("recovery completed but the rebuilt graph's :PackSelection"
               in r.getMessage()
               and "NOT confirmed intact" in r.getMessage()
               for r in caplog.records), (
        f"the recovery caller discarded the pack-set gap: "
        f"{[r.getMessage() for r in caplog.records]}")


def test_cmd_rebuild_reports_the_pack_selection_marker(tmp_path, capsys,
                                                       monkeypatch):
    """The CLI operator surface must name the class (#9752).

    FAILING VALUE: `_cmd_rebuild` printed a dedicated line per preserved class
    for Config, Onboarding and TeamMeta and NONE for `:PackSelection`, so a
    rebuild whose marker restore failed printed a clean `Done:` with
    `pack_selection_gap == 1` sitting in the counts — the "written and never
    surfaced on the very path an operator reads" defect the config line was
    added to close (#2814). `docs/durability-posture.md` declares the class
    "reported (`pack_selection_gap`, never a silent success)", which was true of
    the dict and false of this surface.

    REACHABLE: the counts are `rebuild_all`'s own return value; they are
    injected so each rung can be asserted positively (the end-to-end producer
    of a non-zero gap is `test_rebuild_all_reports_a_pre_9752_sidecar_as_
    state_unknown`).
    """
    import argparse

    from tortoise.__main__ import _cmd_rebuild
    from tortoise.projection import FalkorProjection

    counts = {"nodes": 0, "edges": 0, "events": 0,
              "pack_selection_expected": 1, "pack_selection_verified": True,
              "pack_selection_restored": 0, "pack_selection_restore_failures": 1,
              "pack_selection_gap": 1, "pack_selection_state_unknown": False}
    monkeypatch.setattr(FalkorProjection, "rebuild_all",
                        lambda self, _dir, **kw: dict(counts))
    sdk, events = _mk_sdk(tmp_path, name="cli.db")
    sdk.close()
    _write_journal(events)

    rc = _cmd_rebuild(argparse.Namespace(dir=str(events),
                                        db=str(tmp_path / "cli2.db")))
    out = capsys.readouterr()

    assert rc in (None, 0)
    assert "PackSelection: 0 of 1 pack-set marker(s) restored" in out.out, out.out
    assert "the pre-wipe pack-set marker was NOT restored" in out.err, out.err
    assert "REMOVED" in out.err, out.err

    # The UNKNOWN rung is a different shape and must be nameable on its own: a
    # pre-v7 rescue file cannot say whether the marker was destroyed, and
    # printing the loss wording for it would assert a loss nobody observed.
    counts.update({"pack_selection_expected": 0, "pack_selection_restored": 0,
                   "pack_selection_state_unknown": True})
    rc = _cmd_rebuild(argparse.Namespace(dir=str(events),
                                        db=str(tmp_path / "cli3.db")))
    out = capsys.readouterr()

    assert rc in (None, 0)
    assert "CANNOT be determined" in out.err, out.err
    assert "was NOT restored" not in out.err, out.err


def test_marker_declared_in_the_durability_posture():
    """T2: the class has a DECLARED disposition, not an inference.

    FAILING VALUE: `:PackSelection` appears in the `preserved-sections` block
    with its section, its restore rule and #9752, in the operator audit query,
    and NOT in `unenrolled`.

    REACHABLE: `docs/durability-posture.md` is the repo's canonical durability
    authority and the blocks are literals in it.
    """
    doc = Path(__file__).resolve().parent.parent / "docs" / "durability-posture.md"
    text = doc.read_text()

    def block(name: str) -> str:
        start = text.index(f"<!-- config-registry:{name} -->")
        end = text.index("<!-- config-registry:end -->", start)
        return text[start:end]

    preserved = block("preserved-sections")
    assert "`:PackSelection`" in preserved, (
        "the section-preserved block must name :PackSelection")
    assert "pack_selection" in preserved, (
        "the block must name the carrier section")
    assert "#9752" in preserved
    assert "removed" in preserved, (
        "the declaration must state the load-bearing consequence — an absent "
        "marker re-admits a pack the admin removed")
    assert "PackSelection" not in block("unenrolled"), (
        "#9752 put a vehicle behind the class — it is no longer 'unenrolled'")
    assert ":PackSelection" in block("audit-query"), (
        "the operator audit must enumerate the class, or it under-reports what "
        "survived a rebuild")
