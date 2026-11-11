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


def test_rebuild_all_preserves_the_pack_selection_marker(tmp_path, caplog):
    """A configured graph stays configured, with the same revision and set.

    FAILING VALUE: before #9752's fix the marker is gone after the rebuild, so
    `resolved_pack_set` returns `configured=False, revision=0` and the
    records/union fallback arms decide the graph's approved packs — measured
    ``configured=True revision=1`` → ``configured=False revision=0``.

    REACHABLE: the marker is written by the real writer (`set_graph_packs`,
    which is the only path that moves the revision), and the rebuild is the real
    `rebuild_all` wipe+replay with an empty journal.

    BOTH SIDES OF THE SIGNAL (review P2): every other gap assertion in this file
    pins the ``== 1`` shape, so a FALSE POSITIVE on the happy path — a rebuild
    that preserved the marker perfectly and reported a gap anyway — would leave
    the suite green. That is the "cries wolf on every old sidecar" failure the
    producer's own docstring guards against (`tortoise/projection/__init__.py`,
    the `pack_selection_state_unknown` pre-preservation window), and an operator
    who learns to ignore the warning reads the real wipe as noise. So this test
    asserts the counts are CLEAN here and that no `:PackSelection` ERROR was
    logged.
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _configure(sdk, [{"namespace": "dev", "status": "active"}])
    before = _resolved(sdk)
    before_markers = _markers(sdk)
    assert before.configured is True and before.revision == 1
    assert before.namespaces == frozenset({"dev"})

    with caplog.at_level(logging.ERROR, logger="tortoise.projection"):
        counts = _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

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

    # The NEGATIVE side: a preserving rebuild must report NO gap. Asserted on
    # the counts `rebuild_all` returns (what every recovery consumer reads) and
    # on the log, so neither half can silently invert.
    assert counts["pack_selection_expected"] == 1, (
        f"the pre-wipe capture found no marker to preserve: {counts}")
    assert counts["pack_selection_restored"] == 1, (
        f"the marker was captured but not restored: {counts}")
    assert counts["pack_selection_verified"] is True, (
        f"the post-restore verification did not confirm the property map: "
        f"{counts}")
    assert counts["pack_selection_restore_failures"] == 0, counts
    assert counts["pack_selection_gap"] == 0, (
        "a rebuild that preserved the marker reported a gap — a false positive "
        f"here trains the operator to ignore the warning that means data loss: {counts}")
    assert counts["pack_selection_state_unknown"] is False, (
        "this sidecar carries a usable `pack_selection` section, so the state "
        f"is KNOWN — reporting it unknown is the pre-v7 reading: {counts}")
    assert not [r for r in caplog.records
                if r.levelno >= logging.ERROR and ":PackSelection" in r.getMessage()], (
        "no :PackSelection ERROR is expected on a clean preserving rebuild: "
        f"{[r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]}")


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


def test_rebuild_all_reports_a_marker_collapse(tmp_path, caplog):
    """Two `:PackSelection` markers collapse to one — and the rebuild SAYS so.

    FAILING VALUE: the collapse warning in `rebuild_all` (the
    ``len(pack_selection_snapshot) < pack_selection_entries_merged`` branch) is
    the ONLY thing that makes the discard non-silent, and nothing pinned it —
    the collapse is covered at the UNION level only
    (`test_pack_selection_section_carries_at_most_one_marker`,
    `test_pack_selection_collapse_tie_break_is_deterministic`), so deleting the
    warning left every test green. Both the module's own docstring ("A discard
    is REPORTED (`rebuild_all` warns with both counts), never silent") and
    `docs/durability-posture.md` state the report as a contract, and a silent
    collapse is the ambiguity the class exists to make visible.

    REACHABLE: two markers reach `_capture_pack_selection` as two distinct
    property maps (it reads EVERY `:PackSelection` node faithfully — one per
    graph is the writer's `MERGE` convention, not a constraint), so the merged
    count is 2 while the section carries 1.
    """
    from tortoise.projection import _PACK_SELECTION_LABEL

    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    proj = _proj(sdk)
    proj.g.query("MATCH (n) DETACH DELETE n")
    # The invariant `pack_state` maintains is one marker per graph; a graph
    # holding two is already in violation of it, which is exactly the case the
    # warning exists to surface to the operator.
    proj.g.query(
        f"MERGE (s:{_PACK_SELECTION_LABEL} "
        f"{{revision: 5, updated_by: 'owner'}})")
    proj.g.query(
        f"MERGE (s:{_PACK_SELECTION_LABEL} "
        f"{{revision: 1, updated_by: 'admin'}})")
    assert len(_markers(sdk)) == 2, "the fixture must plant two markers"

    with caplog.at_level(logging.WARNING, logger="tortoise.projection"):
        counts = proj.rebuild_all(str(events), confirm_destructive=True)

    assert any(
        "2 :PackSelection marker(s) were captured but the" in r.getMessage()
        and "exactly 1" in r.getMessage()
        for r in caplog.records), (
        "the collapse was SILENT — the module docstring and "
        "docs/durability-posture.md both claim `rebuild_all` reports it with "
        "both counts: "
        f"{[r.getMessage()[:140] for r in caplog.records]}")

    markers = _markers(sdk)
    assert len(markers) == 1, (
        f"exactly one marker is recreated (highest revision), got {markers}")
    assert markers[0]["revision"] == 5, (
        "the HIGHEST revision must be the one restored — restoring the lower "
        f"one moves the client's concurrency token backwards: {markers[0]}")
    assert counts["pack_selection_restored"] == 1, counts


def test_unverifiable_post_restore_read_is_a_gap_not_a_success(
        tmp_path, caplog, monkeypatch):
    """A verification read that RAISES must not return a success-shaped result.

    FAILING VALUE: `pack_selection_verified` is set and consumed but exercised
    by no test on the FALSE rung — every injection passes `True`
    (`tests/test_rebuild_pack_selection_marker_9752.py`, the migrate/recover
    consumer tests). If the `except` that sets it left the flag `True` on a
    failed read, `pack_selection_missing` also stays `False`, and the run
    returns `pack_selection_restored == 1, pack_selection_gap == 0` — a FALSE
    PASS on the one signal that means "not confirmed intact". A post-restore
    read is exactly the thing that can fail after the wipe, so this is the
    rung that decides whether "could not confirm" is reported or swallowed.

    REACHABLE: `rebuild_all` really runs, and only the SECOND
    `MATCH (s:PackSelection) RETURN properties(s)` — the post-restore verify;
    the first is the pre-wipe capture — is made to raise, so the capture path
    is unaffected and the failure lands where the product would see it.
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _configure(sdk, [{"namespace": "dev", "status": "active"}])
    proj = _proj(sdk)
    real_g = proj.g
    reads = {"n": 0}

    class _FailingVerifyRead:
        """Passes the capture read through; fails the post-restore one."""

        def __init__(self, inner):
            self._inner = inner

        def query(self, q, *a, **kw):
            if "PackSelection" in q and "RETURN properties" in q:
                reads["n"] += 1
                if reads["n"] >= 2:      # 1 = pre-wipe capture, 2 = verify
                    raise RuntimeError("post-restore read failed")
            return self._inner.query(q, *a, **kw)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(proj, "g", _FailingVerifyRead(real_g))

    with caplog.at_level(logging.ERROR, logger="tortoise.projection"):
        counts = proj.rebuild_all(str(events), confirm_destructive=True)

    assert reads["n"] >= 2, (
        "the post-restore verification read must actually be attempted")
    assert counts["pack_selection_verified"] is False, (
        f"an unconfirmable read must not report as verified: {counts}")
    assert counts["pack_selection_restored"] == 0, (
        "an UNCONFIRMED marker must never be counted as restored — that is "
        f"the false PASS this rung exists to prevent: {counts}")
    assert counts["pack_selection_gap"] == 1, (
        f"the gap must be reported when confirmation could not run: {counts}")
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("UNVERIFIED" in m and ":PackSelection" in m for m in msgs), (
        "the operator must be told the marker is UNVERIFIED (not confirmed "
        f"intact, not observed gone) rather than shown a success: {msgs}")


def test_a_different_live_marker_is_reported_as_a_conflict(
        tmp_path, caplog, monkeypatch):
    """A live marker that DIFFERS is a CONFLICT — never an asserted loss.

    FAILING VALUE: `pack_selection_missing` is true for both "no marker" and "a
    DIFFERENT marker", so a single absence message claims the graph "reads as
    UNCONFIGURED, its `revision` is 0, a pack the admin REMOVED is back" and
    tells the operator to RE-PIN — over a graph that IS configured and whose
    marker may be NEWER than the captured one. Re-pinning on that advice
    overwrites a decision the capture never saw. The conflict arm exists to say
    so, and nothing pinned it: deleting it leaves the suite green and restores
    the absence wording.

    REACHABLE: `set_graph_packs` serialises only per worker, so a concurrent
    write can land between the wipe and the restore; the `MERGE` then matches
    ITS node and our `$props` are skipped — emulated here by pointing the
    restore leg at different props, which is exactly what that race leaves
    behind.
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _configure(sdk, [{"namespace": "dev", "status": "active"}])
    proj = _proj(sdk)
    real_restore = proj._restore_write

    def _restore_a_different_marker(cypher, *, params=None, reason):
        # The concurrent writer's marker: same label, DIFFERENT properties.
        # Scoped to THIS leg — the other legs share `_restore_write` and must
        # keep their own props (substituting them breaks the config restore and
        # makes an unrelated #2814 "TRUE POSITIVE" the thing under test).
        if "PackSelection" in cypher:
            params = {"props": {"revision": 99, "updated_by": "concurrent"}}
        return real_restore(cypher, params=params, reason=reason)

    monkeypatch.setattr(proj, "_restore_write", _restore_a_different_marker)

    with caplog.at_level(logging.ERROR, logger="tortoise.projection"):
        counts = proj.rebuild_all(str(events), confirm_destructive=True)

    assert counts["pack_selection_conflict"] is True, (
        "the conflict shape must be machine-visible, not inferred from "
        f"`expected and not restored`: {counts}")
    assert counts["pack_selection_restored"] == 0, counts
    assert counts["pack_selection_gap"] == 1, counts
    assert counts["pack_selection_verified"] is True, (
        "the read SUCCEEDED — this is a mismatch, not an unverified read: "
        f"{counts}")

    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("reported CONFLICT, not a loss" in m for m in msgs), (
        f"a DIFFERENT live marker must be reported as a conflict: {msgs}")
    assert not any("TRUE POSITIVE" in m and ":PackSelection" in m for m in msgs), (
        "the absence consequence must NOT be asserted over a graph that HAS a "
        f"marker: {msgs}")


def test_recover_from_log_reports_a_conflict_not_a_loss(tmp_path, monkeypatch):
    """The recovery `reason` must not carry the absence consequence on CONFLICT.

    FAILING VALUE: `recover_from_log`'s reason arm branched on
    `expected and not restored`, which is true for a CONFLICT too, so the
    operator record asserted "the graph reads as UNCONFIGURED and a pack an
    admin had REMOVED can be re-admitted" over a graph that HAS a marker —
    possibly a NEWER one written by a concurrent `set_graph_packs` (review
    round 3 fixed the producer; round 4 found this consumer).

    REACHABLE: the real rescue path (a v7 sidecar carrying a marker, an empty
    graph, the capture supplied by the sidecar) with only the restore leg
    pointed at different props — exactly what the competing write leaves behind.
    """
    from tortoise.consistency import recover_from_log

    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _plant(Path(_sidecar_path(events)),
           _sidecar_payload(pack_selection=[{"revision": 1,
                                             "updated_by": "admin"}]))
    proj = _proj(sdk)
    proj.g.query("MATCH (n) DETACH DELETE n")
    real_restore = proj._restore_write

    def _restore_a_different_marker(cypher, *, params=None, reason):
        if "PackSelection" in cypher:
            params = {"props": {"revision": 99, "updated_by": "concurrent"}}
        return real_restore(cypher, params=params, reason=reason)

    monkeypatch.setattr(proj, "_restore_write", _restore_a_different_marker)

    rec = recover_from_log(str(events), proj)

    assert rec.get("pack_selection_conflict") is True, (
        f"the conflict discriminator must reach the caller's dict: {rec}")
    assert "reported CONFLICT, not a loss" in rec["reason"], rec["reason"]
    assert "re-admitted" not in rec["reason"], (
        "the reason must not carry the absence consequence for a graph that "
        f"HAS a marker: {rec['reason']}")


def test_recover_from_log_forwards_the_unverified_discriminator(
        tmp_path, monkeypatch):
    """UNVERIFIED must survive into the dict, or BOTH callers mislabel the gap.

    FAILING VALUE: `recover_from_log` read `pack_selection_verified` only to
    append a `reason` clause and never copied it into `result`. Both recovery
    consumers label the gap by calling `pack_selection_shape(result)`, so the
    UNVERIFIED arm of that helper was DEAD for them: on a real run whose
    post-restore read raised, the dict produced `('restore gap', '… reads as
    UNCONFIGURED … a pack an admin REMOVED can be re-admitted …')` while the
    same line's `reason` said "COULD NOT RUN … UNVERIFIED" — the false loss
    assertion rounds 2-4 removed, on the two surfaces where no other reporter
    exists (#9752 review round 5, P1).

    REACHABLE: the real rescue path (v7 sidecar carrying a marker, empty graph)
    with only the post-restore `MATCH (s:PackSelection) RETURN properties(s)`
    made to raise — a failure that can only happen after the wipe, which is
    exactly why it must not be reported as a loss.
    """
    from tortoise.consistency import recover_from_log
    from tortoise.projection import pack_selection_shape

    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _plant(Path(_sidecar_path(events)),
           _sidecar_payload(pack_selection=[{"revision": 1,
                                             "updated_by": "admin"}]))
    proj = _proj(sdk)
    proj.g.query("MATCH (n) DETACH DELETE n")
    real_g = proj.g
    reads = {"n": 0}

    class _FailingVerifyRead:
        def __init__(self, inner):
            self._inner = inner

        def query(self, q, *a, **kw):
            if "PackSelection" in q and "RETURN properties" in q:
                reads["n"] += 1
                if reads["n"] >= 2:      # 1 = capture, 2 = post-restore verify
                    raise RuntimeError("post-restore read failed")
            return self._inner.query(q, *a, **kw)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(proj, "g", _FailingVerifyRead(real_g))

    rec = recover_from_log(str(events), proj)

    assert reads["n"] >= 2, "the verification read must actually be attempted"
    assert rec.get("pack_selection_verified") is False, (
        "the UNVERIFIED discriminator must travel in the returned dict — both "
        f"recovery callers label the gap from it: {rec}")
    label, _consequence = pack_selection_shape(rec)
    assert "UNVERIFIED" in label, (
        "the shape derived from this dict must read UNVERIFIED, not the "
        f"absence/restore-gap claim: {label}")
    assert "re-admitted" not in _consequence, (
        f"an unverified read must not assert a loss: {_consequence}")


def test_migrate_route_names_the_conflict_shape(tmp_path, caplog, monkeypatch):
    """The migration route must not print the loss wording on a CONFLICT.

    FAILING VALUE: both `migrate` warning sites read only the gap (and
    `state_unknown`), so a CONFLICT printed "the migrated graph reads as
    UNCONFIGURED, so a pack an admin REMOVED can be re-admitted" — over a graph
    that holds a marker and may hold a NEWER decision (review round 4).
    """
    counts = {"nodes": 0, "edges": 0, "events": 0,
              "pack_selection_expected": 1, "pack_selection_verified": True,
              "pack_selection_restored": 0,
              "pack_selection_gap": 1, "pack_selection_state_unknown": False,
              "pack_selection_conflict": True}

    with caplog.at_level(logging.WARNING, logger="tortoise.migrate_db"):
        result = _migrate_with_rebuild_counts(tmp_path, monkeypatch, counts)

    assert result["pack_selection_conflict"] is True, (
        f"the returned dict must carry the discriminator: {result}")
    assert result["pack_selection_verified"] is True, result
    msgs = [r.getMessage() for r in caplog.records]
    assert any("reported CONFLICT, not a loss" in m for m in msgs), msgs
    assert not any("reads as UNCONFIGURED" in m for m in msgs), (
        f"the absence consequence must not be printed on a conflict: {msgs}")


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
    # ⛔ EXACT, in the NEWEST section's own test, and the pin claims only what
    # it delivers. The two older sections' pins are `>=` on purpose (a later
    # bump must not red a test about another class), but the sum of three `>=`
    # pins would be that NO test ever SEES the constant — so the newest section
    # owns the current value, and a bump must move it with the section that
    # caused it. ⛔ What this pin does NOT do, measured: land a fully-wired
    # section without bumping and it stays GREEN (it compares a constant to a
    # literal, and nothing maps section → version). Do not read it as that
    # tripwire. The reader-side protection against an un-bumped section is
    # separate and already there twice: `_validate_prewipe_snapshot` REFUSES an
    # unknown section, and the readable-versions check refuses an unreadable
    # version — either one makes an older build refuse the file rather than
    # accept it and wipe on it.
    assert _PREWIPE_SNAPSHOT_VERSION == 7, (
        "`:PackSelection` landed at v7 — if you bumped the version for a NEW "
        "section, move THIS pin with the section you added (this pin cannot "
        "detect a section landed without a bump; the unknown-section refusal "
        "is what protects the reader). A LOWER read/write version lets a "
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
    with the discovery seam pointed at the test's events dir and the embedded
    reading the branch is gated on forced on (see the `_is_embedded` note
    below — without it this pin is unreachable on the docker lane).
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _plant(Path(_sidecar_path(events)), _pack_selection_only_gap_sidecar())
    _proj(sdk).g.query("MATCH (n) DETACH DELETE n")
    proj = _proj(sdk)
    monkeypatch.setattr(type(proj), "_find_local_jsonl_dir",
                        lambda self: str(events))
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    # The lost-graph branch is gated on `self._is_embedded and not is_prod`. On
    # the suite's DOCKER lane `TORTOISE_DB_URI` arms the redirect, so the SDK
    # builds host-mode, `_path` is None and `_is_embedded` reads False (the
    # reading tests/test_tripwire.py:253 pins) — which makes the whole branch,
    # and this consumer with it, UNREACHABLE, so the pin could never fire. A
    # test that only passes on the embedded carve-out lane is a test the fast
    # shard never exercises: it shipped unmeasured exactly that way (#11839).
    # Force the reading the branch is written for, the lane-independent seam
    # `test_turn_embedding_write_path_4194.py` already uses (#4280).
    monkeypatch.setattr(proj, "_is_embedded", True)
    assert proj._is_embedded is True, (
        "the seam must land, or this test silently reverts to asserting a "
        "branch it never entered")

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
    # ⛔ Assert the RENDERED SHAPE, not just the shared template. Every arm uses
    # the same "… NOT confirmed intact" frame, so the substring checks above stay
    # green if `pack_selection_shape` is replaced by the pre-round-4 hardcoded
    # absence tail — the exact collapse that was removed here (review round 6).
    # The fixture plants a v6 sidecar with NO `pack_selection` section, i.e. the
    # UNKNOWN shape, whose consequence must not claim a loss.
    _msg = next(r.getMessage() for r in caplog.records
                if ":PackSelection pack-set marker" in r.getMessage())
    assert "pre-preservation/UNKNOWN" in _msg, (
        "the rendered shape must be UNKNOWN for a sidecar that cannot say "
        f"whether the marker existed: {_msg}")
    assert "re-admitted" not in _msg, (
        f"an UNKNOWN sidecar must not assert an observed loss: {_msg}")


def test_auto_health_recover_does_not_mask_the_team_meta_gap(
        tmp_path, caplog, monkeypatch):
    """The :TeamMeta leg is not suppressed by the onboarding leg (#9752 r6).

    FAILING VALUE: `_auto_health_recover`'s gap legs are one additive block —
    the shape the two other reporters use (`_recover_or_raise`, and the CLI at
    `tortoise/__main__.py`), and the rule the producer states verbatim in
    `tortoise/consistency.py` ("a confirmed loss and a pre-preservation
    UNKNOWN can coexist, and one must not suppress the other"). Round 6's fix
    made `pack_selection_gap` and `corrupt_lines` independent but left
    `team_meta_gap` as `elif` on the head of that chain, so whichever class
    matched FIRST still suppressed it.

    The masking is NOT hypothetical and is the SAME rescue-file shape round 6
    fixed for the pack-set leg: a sidecar predating BOTH `:TeamMeta`
    preservation (v6) and the onboarding sections carries neither, so it sets
    `onboarding_gap` AND `team_meta_gap` together. The operator then got only
    the onboarding remedy and never "the #2789 first-org guard may read 0 and
    a later provisioning call can re-mint the marker with the control plane's
    name" — the one consequence that stops a destroyed first-org marker being
    silently replaced with the WRONG org.

    REACHABLE: the real `_auto_health_recover` on the probe-OK lost-graph path,
    with the discovery seam pointed at the test's events dir and the embedded
    reading forced on (see the sibling pin above for why the docker lane makes
    the branch unreachable without it).
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    payload = _sidecar_payload(batch_snapshot=[{"id": "b1"}], version=6)
    # Neither preservation section exists in a pre-v6 rescue file: the
    # `team_meta` KEY gate fires on the KEY (a file written before the section
    # landed cannot carry it), and the onboarding gate on its two keys.
    payload.pop("team_meta")
    payload.pop("onboarding_snapshot")
    payload.pop("onboarding_step_links")
    _plant(Path(_sidecar_path(events)), payload)
    _proj(sdk).g.query("MATCH (n) DETACH DELETE n")
    proj = _proj(sdk)
    monkeypatch.setattr(type(proj), "_find_local_jsonl_dir",
                        lambda self: str(events))
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setattr(proj, "_is_embedded", True)

    with caplog.at_level(logging.WARNING, logger="tortoise.projection"):
        proj._auto_health_recover()

    messages = [r.getMessage() for r in caplog.records]
    assert any("onboarding state is NOT confirmed intact" in m
               for m in messages), (
        f"the onboarding leg did not fire on this rescue file: {messages}")
    assert any(":TeamMeta first-org marker" in m
               and "NOT confirmed intact" in m for m in messages), (
        "the :TeamMeta leg was MASKED by the onboarding leg — that is a "
        "chain, not additive legs, and it withholds the only remedy that "
        "stops a destroyed first-org marker being re-minted with the "
        f"control plane's name: {messages}")


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
    # Assert the RENDERED SHAPE too (review round 6): the template frame is
    # shared by every arm, so the check above cannot tell the helper's answer
    # from the pre-round-4 hardcoded absence tail. The fixture's v6 sidecar
    # makes this the UNKNOWN shape.
    _msg = next(r.getMessage() for r in caplog.records
                if "recovery completed but the rebuilt graph's :PackSelection"
                in r.getMessage())
    assert "pre-preservation/UNKNOWN" in _msg, (
        f"the rendered shape must be UNKNOWN here: {_msg}")
    assert "re-admitted" not in _msg, (
        f"an UNKNOWN sidecar must not assert an observed loss: {_msg}")


def test_rebuild_all_honours_a_carried_pack_selection_unknown_flag(
        tmp_path, caplog, monkeypatch):
    """The carried UNKNOWN flag must LOAD and keep reporting the UNKNOWN.

    FAILING VALUE: a CURRENT-version sidecar whose `pack_selection` section is
    present-but-empty, carrying `pack_selection_unknown: True`, must be accepted
    by the loader and must keep reporting the UNKNOWN. Both cheaper gates are
    satisfied and cannot supply the signal (`leftover_version` is current, and
    the section key IS present), so the inherited flag is the only carrier — and
    without it a SECOND interruption reports a clean rebuild over a graph the
    earlier wipe may have stripped, which is the silent "reads as never
    configured" outcome #9752 exists to prevent.

    ⚠️ CORRECTION to the round-6 finding that asked for this pin: it claimed
    deleting the explicit re-ride (`if pack_selection_unknown:
    payload["pack_selection_unknown"] = True`) leaves every suite green and
    loses the signal on a third interruption. MEASURED FALSE on this head —
    with that line removed the spy still sees
    `{'pack_selection_unknown': True}` in the first payload written, because
    the leftover payload is copied into the file this run writes. The carry is
    therefore REDUNDANT (two carriers, not one), which is why no mutation of the
    single line can red any test, and this pin asserts the OUTCOME — the flag is
    present in the sidecar written by the retry — rather than pretending to
    discriminate one of the two redundant carriers.
    """
    from tortoise import projection as pr
    from tortoise.projection import (
        _PREWIPE_SNAPSHOT_META_KEYS,
        _PREWIPE_SNAPSHOT_VERSION,
    )

    written: list[dict] = []
    _real_write = pr._write_prewipe_snapshot

    def _spy(path, payload):
        written.append(payload)
        return _real_write(path, payload)

    monkeypatch.setattr(pr, "_write_prewipe_snapshot", _spy)

    sdk, events = _mk_sdk(tmp_path)
    payload = _sidecar_payload(
        batch_snapshot=[{"id": "b1"}], pack_selection=[])
    payload["pack_selection_unknown"] = True
    assert (payload["version"] == _PREWIPE_SNAPSHOT_VERSION
            and payload["pack_selection"] == []), (
        "the fixture must plant the CURRENT version with a present-but-empty "
        "section, so only the inherited flag can produce the UNKNOWN")
    assert "pack_selection_unknown" in _PREWIPE_SNAPSHOT_META_KEYS, (
        "the flag must be a registered META key, or the loader refuses the file "
        "as carrying a foreign section and a second interruption is "
        "unrecoverable")
    _plant(Path(_sidecar_path(events)), payload)

    with caplog.at_level(logging.ERROR):
        counts = _proj(sdk).rebuild_all(str(events), confirm_destructive=True)

    assert counts["pack_selection_state_unknown"] is True, (
        "the flag staged by an earlier interrupted run must be inherited — "
        f"this run's sections cannot re-derive it: {counts}")
    assert counts["pack_selection_gap"] == 1, counts
    # ⛔ And it must be present in this run's own sidecar, or a THIRD
    # interruption reads a v7 file whose section is present-but-empty →
    # `pack_selection_unknown` False → no gap → a clean success over a graph the
    # first wipe may have stripped. The carry is carried by TWO mechanisms (the
    # copied leftover payload AND the explicit re-ride), so deleting either alone
    # stays green — asserted here as an OUTCOME, deliberately.
    assert written, "no pre-wipe sidecar was written — the spy missed"
    assert written[0].get("pack_selection_unknown") is True, (
        "the inherited UNKNOWN must travel into the sidecar THIS run writes, "
        "so a second interruption is still honest about the marker's fate: "
        f"{written[0].get('pack_selection_unknown')!r}")


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

    # The CONFLICT rung (review round 4): a DIFFERENT, possibly NEWER marker is
    # live, so neither the absence wording nor its "Re-set ... deliberately"
    # remedy may be printed — that remedy would overwrite the newer decision.
    counts.update({"pack_selection_expected": 1, "pack_selection_restored": 0,
                   "pack_selection_state_unknown": False,
                   "pack_selection_conflict": True})
    rc = _cmd_rebuild(argparse.Namespace(dir=str(events),
                                        db=str(tmp_path / "cli4.db")))
    out = capsys.readouterr()

    assert rc in (None, 0)
    assert "reported CONFLICT, not a loss" in out.err, out.err
    assert "was NOT restored" not in out.err, out.err
    assert "Re-set the graph's packs deliberately" not in out.err, out.err


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
        # ⛔ NEGATIVE CONTROL, and its message claims only that: this guards the
        # "declared twice" shape, it does NOT record a move. Measured against
        # `origin/main`, `:PackSelection` never appeared in this block either —
        # so nothing was removed from it by #9752, and a message saying "it is
        # no longer 'unenrolled'" would describe a state change that did not
        # happen (the class was never an unenrolled registry row; it is a
        # SECTION, like `:TeamMeta`). The clause reds if a future edit adds it
        # here as well as to `preserved-sections`.
        "the class must not be declared in BOTH blocks — it is a preserved "
        "SECTION, never an unenrolled registry row (#9752)")
    assert ":PackSelection" in block("audit-query"), (
        "the operator audit must enumerate the class, or it under-reports what "
        "survived a rebuild")


# ── #9752: the THIRD `rebuild_all` caller — `tortoise migrate-db` ───────────


def _migrate_with_rebuild_counts(tmp_path, monkeypatch, counts):
    """Run the real `migrate_db.migrate()` with `rebuild_all`'s counts injected.

    REACHABLE: the counts ARE `rebuild_all`'s own return value (the end-to-end
    producer of a non-zero gap is pinned by
    `test_rebuild_all_reports_a_pre_9752_sidecar_as_state_unknown`); they are
    injected here so each reporting rung can be asserted positively without
    minting a second embedded graph. `HOME` is redirected so `_tortoise_dir()`
    resolves inside the test, and the JSONL dir is created so `migrate()` takes
    the replay route rather than the RDB-copy fallback (which replays nothing
    and therefore has no counts to report).
    """
    from tortoise.projection import FalkorProjection

    monkeypatch.setenv("HOME", str(tmp_path))
    tortoise_dir = tmp_path / ".tortoise"
    (tortoise_dir / "events").mkdir(parents=True)
    # Source for backup-first; its content is never read as a graph because
    # `rebuild_all` is stubbed (the wipe is therefore never reached).
    (tortoise_dir / "embedded.db").write_bytes(b"not-a-real-rdb")
    monkeypatch.setattr(FalkorProjection, "rebuild_all",
                        lambda self, _dir, **kw: dict(counts))

    from tortoise.migrate_db import migrate

    return migrate(force=True)


def test_pack_selection_gap_warns_in_the_migrate_db_caller(
        tmp_path, caplog, monkeypatch):
    """`rebuild_all` has THREE callers; the third must consume the gap (#9752).

    FAILING VALUE: `tortoise/migrate_db.py` already carried the identical
    warning leg for `onboarding_gap` (#4641) and `team_meta_gap` (#5353) and had
    none for `pack_selection_gap` — `git grep -c pack_selection -- <this file>`
    was 0. So a migration-triggered rebuild whose pack-set marker was not
    confirmed intact reported a clean `migrated`, wrote the terminal migration
    marker (no later open re-tries it), and left a graph that reads as
    UNCONFIGURED with a REMOVED pack re-admitted and its `revision` reset.

    REACHABLE: the real `migrate()` replay route, with `rebuild_all`'s counts
    injected; the returned dict is asserted too, because that is the only thing
    a caller of `migrate()` can branch on.
    """
    counts = {"nodes": 0, "edges": 0, "events": 0,
              "pack_selection_expected": 1, "pack_selection_verified": True,
              "pack_selection_restored": 0,
              "pack_selection_restore_failures": 1,
              "pack_selection_gap": 1, "pack_selection_state_unknown": False}

    with caplog.at_level(logging.WARNING, logger="tortoise.migrate_db"):
        result = _migrate_with_rebuild_counts(tmp_path, monkeypatch, counts)

    assert result["status"] == "migrated", result
    assert result["pack_selection_gap"] == 1, (
        "the migration's own returned dict must carry the count — otherwise a "
        "caller has nothing to branch on")
    assert result["pack_selection_state_unknown"] is False

    msgs = [r.getMessage() for r in caplog.records]
    assert any(":PackSelection" in m and "NOT confirmed intact" in m
               and "restore gap" in m and "REMOVED" in m for m in msgs), (
        f"the migrate route discarded the pack-set gap: {msgs}")


def test_migrate_route_does_not_report_the_empty_target_as_verified(
        tmp_path, caplog, monkeypatch):
    """The replay route's capture describes an EMPTY graph, so it cannot verify.

    FAILING VALUE: `rebuild_all` captures each preservation section from the
    graph it is ABOUT TO WIPE, and on this route that graph is the freshly
    created `target` — not `backup_path`, the legacy graph the journal came
    from. The capture is therefore empty by construction: nothing is restored,
    the post-restore comparison is skipped, and the rebuild returns
    `pack_selection_gap: 0` with `pack_selection_verified: True`. Reporting
    that as a verification is a false PASS over a graph that really did lose
    the marker — measured end-to-end (#11866): a source with
    `configured=True revision=2` and an EMPTY approved set migrates to
    `configured=False revision=0` (the catalog union, so the pack an admin
    REMOVED is re-admitted) under `pack_selection_gap: 0,
    pack_selection_verified: True` and no CLI line at all.

    REACHABLE: the counts below are exactly what `rebuild_all` returns for a
    fresh target, injected through the real `migrate()` via the same helper the
    sibling rungs use. The assertion is on the ROUTE's answer — UNKNOWN, named
    rather than silent — because that is the only state this route can honestly
    support until the capture source is fixed (#11866).
    """
    counts = {"nodes": 1, "edges": 0, "events": 1,
              "pack_selection_expected": 0, "pack_selection_verified": True,
              "pack_selection_restored": 0,
              "pack_selection_restore_failures": 0,
              "pack_selection_gap": 0, "pack_selection_state_unknown": False,
              "pack_selection_conflict": False}

    with caplog.at_level(logging.WARNING, logger="tortoise.migrate_db"):
        result = _migrate_with_rebuild_counts(tmp_path, monkeypatch, counts)

    assert result["pack_selection_verified"] is False, (
        "the route cannot verify the migrated graph's marker: its capture read "
        "the fresh target, not the source (#11866)")
    assert result["pack_selection_state_unknown"] is True
    assert result["pack_selection_gap"] == 1
    msgs = [r.getMessage() for r in caplog.records]
    assert any(":PackSelection" in m and "pre-preservation/UNKNOWN" in m
               for m in msgs), (
        "the route must NAME the state it cannot observe, or the only surface a "
        f"human runs stays silent: {msgs}")
    assert not any("restore gap" in m for m in msgs), (
        "nothing observed a loss, so the absence consequence and its re-pin "
        "remedy must not be asserted")


def test_migrate_route_does_not_override_a_stronger_rebuild_report(
        tmp_path, caplog, monkeypatch):
    """The route's UNKNOWN must never MASK an observation the rebuild made.

    The override exists for one shape only: a rebuild that reported a clean
    verification over an empty capture. An OBSERVED state outranks it — here a
    CONFLICT (a live marker that is not the captured one), which is the shape
    whose consequence is the OPPOSITE of "reads as UNCONFIGURED": the graph IS
    configured and may hold a newer set, so the re-pin remedy would overwrite a
    decision. `pack_selection_expected` is 0 so the empty-capture clause is
    active and this bracket is the only thing standing between the route and a
    relabelled conflict.
    """
    counts = {"nodes": 1, "edges": 0, "events": 1,
              "pack_selection_expected": 0, "pack_selection_verified": True,
              "pack_selection_restored": 0,
              "pack_selection_restore_failures": 0,
              "pack_selection_gap": 1, "pack_selection_state_unknown": False,
              "pack_selection_conflict": True}

    with caplog.at_level(logging.WARNING, logger="tortoise.migrate_db"):
        result = _migrate_with_rebuild_counts(tmp_path, monkeypatch, counts)

    assert result["pack_selection_conflict"] is True
    assert result["pack_selection_state_unknown"] is False, (
        "an observed CONFLICT was relabelled UNKNOWN by the route")
    assert result["pack_selection_gap"] == 1
    msgs = [r.getMessage() for r in caplog.records]
    assert any("CONFLICT" in m for m in msgs), msgs
    assert not any("restore gap" in m for m in msgs), msgs
    assert not any("re-pin" in m.lower() and "UNCONFIGURED" in m
                   for m in msgs), msgs


def test_migrate_route_does_not_override_a_verified_non_empty_capture(
        tmp_path, caplog, monkeypatch):
    """The UNKNOWN rule RETIRES itself once the route can capture (#11866).

    The override exists for one signature: an EMPTY capture reported as a clean
    verification. The moment the capture source is fixed (#11866) `expected` is
    non-zero and a real carry is verified — and a rule written for the empty
    target must not relabel that success as `pre-preservation/UNKNOWN` with a
    gap, which would print "the graph may read as UNCONFIGURED" over a marker
    that was just restored.
    """
    counts = {"nodes": 1, "edges": 0, "events": 1,
              "pack_selection_expected": 1, "pack_selection_restored": 1,
              "pack_selection_verified": True,
              "pack_selection_restore_failures": 0,
              "pack_selection_gap": 0, "pack_selection_state_unknown": False,
              "pack_selection_conflict": False}

    with caplog.at_level(logging.WARNING, logger="tortoise.migrate_db"):
        result = _migrate_with_rebuild_counts(tmp_path, monkeypatch, counts)

    assert result["pack_selection_verified"] is True, (
        "a verified, non-empty capture was relabelled UNKNOWN by a rule that "
        "only ever described an empty target (#11866)")
    assert result["pack_selection_state_unknown"] is False
    assert result["pack_selection_gap"] == 0
    msgs = [r.getMessage() for r in caplog.records]
    assert not any(":PackSelection" in m for m in msgs), msgs


def test_migrate_db_caller_distinguishes_the_unknown_shape(
        tmp_path, caplog, monkeypatch):
    """UNKNOWN must not be reported as an observed loss (#4641 round 5, #9752).

    A pre-v7 rescue file cannot say whether the marker was destroyed, so the
    migrate route must name `pre-preservation/UNKNOWN` rather than assert a
    loss nobody observed — and the returned dict must carry the discriminator
    for the same reason.
    """
    counts = {"nodes": 0, "edges": 0, "events": 0,
              "pack_selection_expected": 0, "pack_selection_verified": True,
              "pack_selection_restored": 0,
              "pack_selection_gap": 1, "pack_selection_state_unknown": True}

    with caplog.at_level(logging.WARNING, logger="tortoise.migrate_db"):
        result = _migrate_with_rebuild_counts(tmp_path, monkeypatch, counts)

    assert result["pack_selection_state_unknown"] is True
    assert result["pack_selection_verified"] is False, (
        "nothing was verified on this route, so the UNKNOWN shape must not also "
        "carry `verified: True` for a caller that reads that field")
    msgs = [r.getMessage() for r in caplog.records]
    matching = [m for m in msgs if ":PackSelection" in m]
    assert matching, f"no pack-set line at all: {msgs}"
    assert any("pre-preservation/UNKNOWN" in m for m in matching), matching
    assert not any("restore gap" in m for m in matching), (
        "an UNKNOWN rescue file was described as an observed restore gap")


def test_migrate_cli_reports_the_pack_selection_gap(caplog, monkeypatch):
    """The operator surface of `tortoise migrate-db` must name the class.

    FAILING VALUE: even with the count in the returned dict, `main()` printed
    only "migrated embedded.db -> tortoise.db (N nodes)" — the pack-set loss was
    invisible on the one surface a human runs. This is the same "written and
    never surfaced on the very path an operator reads" defect the CLI rebuild
    block closes (`__main__.py`).
    """
    import tortoise.migrate_db as mdb

    gap = {"status": "migrated", "nodes": 3, "pack_selection_gap": 1,
           "pack_selection_state_unknown": False}
    monkeypatch.setattr(mdb, "migrate", lambda force=False: dict(gap))

    with caplog.at_level(logging.WARNING, logger="tortoise.migrate_db"):
        assert mdb.main(["--force"]) == 0
    msgs = " | ".join(r.getMessage() for r in caplog.records)
    assert "migrated graph's :PackSelection pack-set marker is NOT confirmed" \
        in msgs, msgs
    assert "restore gap" in msgs, msgs
    assert "REMOVED" in msgs, msgs

    # The UNKNOWN rung is a different shape and must be nameable on its own.
    caplog.clear()
    gap.update({"pack_selection_state_unknown": True})
    monkeypatch.setattr(mdb, "migrate", lambda force=False: dict(gap))
    with caplog.at_level(logging.WARNING, logger="tortoise.migrate_db"):
        assert mdb.main(["--force"]) == 0
    msgs = " | ".join(r.getMessage() for r in caplog.records)
    assert "pre-preservation/UNKNOWN" in msgs, msgs
    assert "restore gap" not in msgs, msgs


def test_recover_from_log_documents_the_pack_selection_keys():
    """The return contract must name the keys it returns (#9752 review, P3).

    `recover_from_log`'s docstring enumerates the returned dict and was
    extended for each prior class; a caller branching on the shape reads it, so
    a key that exists only in the code is a contract nobody documented.
    """
    from tortoise.consistency import recover_from_log

    doc = recover_from_log.__doc__ or ""
    assert "pack_selection_gap" in doc
    assert "pack_selection_state_unknown" in doc
    assert "pack_selection_conflict" in doc, (
        "the conflict discriminator is returned but not documented, so a "
        "caller reading the contract cannot tell a conflict from a loss")
    assert "pack_selection_verified" in doc, (
        "the UNVERIFIED discriminator is returned but not documented, so a "
        "caller cannot tell 'could not confirm' from an observed loss")
    assert "#9752" in doc


def test_recover_or_raise_documents_the_pack_selection_leg():
    """The third gap leg must be named where the method's contract is (#9752).

    FAILING VALUE: `_recover_or_raise`'s docstring answered "which classes does
    this caller report?" with the onboarding gap and the `:TeamMeta` leg
    (#5353), and stopped there while the method grew its `:PackSelection`
    consumer. That is the same prose/code disagreement the round-1 finding on
    THIS PR was made of — a class the caller reports, described by nothing —
    and it is pinned here rather than left to the next reader, exactly as
    `recover_from_log`'s own contract is pinned above.

    The sibling assertions are not decoration: an "add the new one" edit that
    REPLACED the list would pass the `:PackSelection` assertions while dropping
    a class the method still reports.
    """
    from tortoise.projection import FalkorProjection

    doc = FalkorProjection._recover_or_raise.__doc__ or ""
    assert ":PackSelection" in doc and "#9752" in doc
    assert ":TeamMeta" in doc and "#5353" in doc
    assert "#4641" in doc


def test_replay_log_reports_the_marker_shape_it_observed(tmp_path, caplog,
                                                         monkeypatch):
    """The journal-replay restore must report the shape it READ BACK (#9752).

    FAILING VALUE: `_replay_log` used to restore the marker with a bare
    `MERGE … ON CREATE SET` and then log "the graph's approved pack set and
    `revision` survive the wipe" WITHOUT reading anything back — and, on a
    raised write, log "the graph now reads as UNCONFIGURED". Both are
    assertions about state this code never observed:

    * `MERGE … ON CREATE SET` SKIPS its props when a marker already exists, so a
      concurrent `set_graph_packs` landing in the wipe→write window leaves a
      DIFFERENT marker — the rebuild then claims the CAPTURED revision survived
      while the graph holds someone else's (possibly NEWER) pack set. The
      accurate report is a CONFLICT, the shape `rebuild_all` reconciles for.
    * a raised write is UNVERIFIED, not absent: the same MERGE would have
      matched an existing marker, so advising a re-pin there can OVERWRITE a
      newer decision.

    This path is the ONE reporter with no sidecar and no reconciliation step, so
    the log line is its entire report — and it was the last place asserting
    unobserved state.

    MUTATION-PROVEN: replacing the read-back with the old unconditional success
    `logger.info` leaves the CONFLICT arm RED; replacing the UNVERIFIED wording
    with the old "reads as UNCONFIGURED" leaves the raise arm RED.
    """
    from tortoise import projection as pr
    from tortoise.log import EventLog

    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)

    captured = {"revision": 1, "packs": ["a"]}
    monkeypatch.setattr(pr, "_capture_pack_selection",
                        lambda _proj: [dict(captured)])
    proj = _proj(sdk)
    _G = type(proj.g)
    _orig_query = _G.query
    mode = {"raise": False}

    def _patched(obj, cypher, params=None, timeout=None):
        if "MERGE (s:" in cypher:
            if mode["raise"]:
                raise RuntimeError("write refused")
            # The concurrent write wins: the MERGE matches an existing marker,
            # so its ON CREATE props are skipped and a DIFFERENT marker is live.
            if isinstance(params, dict):
                props = dict(params.get("props") or {})
                props["revision"] = 7
                params = {**params, "props": props}
        return _orig_query(obj, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_G, "query", _patched)

    # Arm 1 — a concurrent write wins, so the captured marker did NOT land.
    with caplog.at_level(logging.INFO, logger="tortoise.projection"):
        proj._replay_log(EventLog(str(events / "events.jsonl")),
                         confirm_destructive=True)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("DIFFERENT :PackSelection marker is present" in m
               and "revision 7" in m and "revision 1" in m for m in msgs), (
        f"the replay reporter must name the CONFLICT it can see: {msgs}")
    assert not any("survive the wipe" in m for m in msgs), (
        "the replay reporter claimed the captured pack set survived while the "
        f"graph holds a different marker: {msgs}")

    # Arm 2 — the write raises: UNVERIFIED, never "reads as UNCONFIGURED".
    mode["raise"] = True
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="tortoise.projection"):
        proj._replay_log(EventLog(str(events / "events.jsonl")),
                         confirm_destructive=True)
    errs = [r.getMessage() for r in caplog.records]
    assert any("UNVERIFIED" in m and "NOT observed gone" in m for m in errs), (
        f"a raised restore write must be reported as UNVERIFIED: {errs}")
    assert not any("now reads as UNCONFIGURED" in m for m in errs), (
        f"a raised write asserted an absence it never observed: {errs}")


def test_pack_selection_gap_is_not_masked_by_a_co_occurring_gap(
        tmp_path, caplog, monkeypatch):
    """Co-occurring gap classes each get their own line (#9752, review round 6 P2).

    FAILING VALUE: the legs were an `elif` chain, so whichever class matched
    FIRST suppressed every later one. A pre-preservation rescue file carries
    NEITHER marker, so it sets `team_meta_state_unknown` AND
    `pack_selection_state_unknown` together — the ordinary shape after one
    interrupted rebuild. The operator then got only the TeamMeta remedy
    ("re-mint the marker") and never "check the approved pack set before
    trusting it", which is the one consequence that stops a pack an admin had
    REMOVED being re-admitted with its `revision` reset. The two other
    reporters of this class (`_recover_or_raise`, and the CLI at
    `tortoise/__main__.py`) already emit one line per class with independent
    `if`s; an `elif` here also contradicts the rule the producer states
    verbatim in `tortoise/consistency.py` — "Additive, not a chain … one must
    not suppress the other".

    REACHABLE: both sections are absent from a pre-preservation payload, which
    is exactly what the pending sidecar holds for a snapshot written before
    marker preservation existed (version < 7).
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    payload = _sidecar_payload(batch_snapshot=[{"id": "b1"}], version=6)
    payload.pop("pack_selection")
    payload.pop("team_meta")
    _plant(Path(_sidecar_path(events)), payload)
    _proj(sdk).g.query("MATCH (n) DETACH DELETE n")
    proj = _proj(sdk)
    monkeypatch.setattr(type(proj), "_find_local_jsonl_dir",
                        lambda self: str(events))
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    # Same lane-independent seam as the sibling pin above (#4280).
    monkeypatch.setattr(proj, "_is_embedded", True)

    with caplog.at_level(logging.WARNING, logger="tortoise.projection"):
        proj._auto_health_recover()

    messages = [r.getMessage() for r in caplog.records]
    assert any(":TeamMeta first-org marker" in m
               and "NOT confirmed intact" in m for m in messages), (
        f"the :TeamMeta leg did not fire on a co-occurring gap: {messages}")
    assert any(":PackSelection pack-set marker" in m
               and "NOT confirmed intact" in m for m in messages), (
        "the pack-set leg was MASKED by the earlier :TeamMeta leg — that is "
        "a chain, not additive legs, and it withholds the only remedy that "
        f"stops a removed pack being re-admitted: {messages}")


class _NoMarkerRestore:
    """Drops the `:PackSelection` restore write, passes every other query through.

    Models the shape the ABSENT arms exist for: the restore wrote nothing (the
    wipe removed the node and no replay pass recreates it), the read-back then
    runs and HONESTLY observes nothing. The absence is therefore a measurement,
    not an assumption — which is the only condition under which either reporter
    may say "the graph now reads as UNCONFIGURED".
    """

    def __init__(self, inner):
        self._inner = inner

    def query(self, q, *a, **kw):
        if "MERGE (s:" in q and "PackSelection" in q:
            return _EMPTY
        return self._inner.query(q, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _EmptyResult:
    """The `.result_set` shape a projection query returns, with no rows."""

    def __init__(self):
        self.result_set: list = []


_EMPTY = _EmptyResult()


def test_rebuild_all_reports_the_true_absence_it_observed(tmp_path, caplog,
                                                          monkeypatch):
    """The `rebuild_all` ABSENT arm must fire on an OBSERVED absence (#9752).

    FAILING VALUE: `elif pack_selection_missing:` — the one branch that states
    the actual #9752 harm ("the graph now reads as UNCONFIGURED, a pack the
    admin had REMOVED is back in the approved set") — was pinned by NO test.
    Measured at this head: rewriting it to `elif False:` leaves the whole file
    GREEN (89 passed across the four rebuild suites, 34 in this one). A report
    that can be deleted without reddening anything is not a report; it is dead
    code with a log line, which is the class this PR exists to remove.

    REACHABLE, not simulated: `rebuild_all` really runs. Only the restore
    `MERGE (s:PackSelection)` is dropped, so the wipe's effect stands — the
    marker is genuinely gone — and the post-restore verification really reads
    the graph and really finds nothing. The two injected failures (the write
    done for us, the read raising) both stay out of the way, which is what makes
    this the TRUE-POSITIVE rung: an observed absence, distinguished from
    UNVERIFIED by the read having SUCCEEDED.

    MUTATION: `elif pack_selection_missing:` → `elif False:` reds this test and
    nothing else; the wording assertion also pins the remedy an operator needs.
    """
    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)
    _configure(sdk, [{"namespace": "dev", "status": "active"}])
    assert _resolved(sdk).configured is True, (
        "fixture: the graph must start configured, or there is no marker to "
        "lose and the arm under test is not reachable")

    proj = _proj(sdk)
    monkeypatch.setattr(proj, "g", _NoMarkerRestore(proj.g))

    with caplog.at_level(logging.ERROR, logger="tortoise.projection"):
        counts = proj.rebuild_all(str(events), confirm_destructive=True)

    assert counts["pack_selection_expected"] == 1, (
        f"the pre-wipe capture must have found the marker: {counts}")
    assert counts["pack_selection_verified"] is True, (
        "the verification READ must have succeeded — this rung is the observed "
        f"absence, not the unconfirmable one: {counts}")
    assert counts["pack_selection_restored"] == 0, (
        f"a marker that was not observed must not count as restored: {counts}")
    assert counts["pack_selection_gap"] == 1, (
        f"the loss must be reported as a gap: {counts}")
    assert counts["pack_selection_conflict"] is False, (
        "the shape discriminator must separate a loss from a CONFLICT — a "
        "consumer that asserts the absence consequence on the CONFLICT shape "
        f"claims a loss nobody observed: {counts}")
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("ABSENT" in m and "TRUE POSITIVE" in m for m in msgs), (
        "the operator must be told this is an observed loss, not a silent "
        f"success: {msgs}")
    assert any("re-pin" in m.lower() for m in msgs), (
        f"the report must carry the remedy (re-pin the pack set): {msgs}")


def test_replay_log_reports_the_absence_arm_it_observed(tmp_path, caplog,
                                                        monkeypatch):
    """`_replay_log`'s ABSENT arm must fire on an OBSERVED absence (#9752).

    FAILING VALUE: the `else:` under the read-back — the journal-replay path's
    only statement that a lost marker reads as UNCONFIGURED — is unpinned.
    Measured at this head: `logger.error(` → `logger.debug(` on that arm leaves
    this file GREEN (34 passed), so the single operator-visible signal on the
    journal-only path can be silenced by an edit nothing notices.

    REACHABLE: `_replay_log` really runs over a real journal, `_capture_pack_selection`
    really returns the marker, and only the restore `MERGE` is dropped — the
    read-back then runs against a graph that truly has no marker, which is the
    observed absence the arm's wording is allowed to describe.

    MUTATION: silencing the arm (`logger.debug`) or removing the `else:` reds
    this test; the "no success wording" assertion also pins that the run does
    not claim the marker survived.
    """
    from tortoise import projection as pr
    from tortoise.log import EventLog

    sdk, events = _mk_sdk(tmp_path)
    _write_journal(events)

    captured = {"revision": 1, "packs": ["a"]}
    monkeypatch.setattr(pr, "_capture_pack_selection",
                        lambda _proj: [dict(captured)])
    proj = _proj(sdk)
    monkeypatch.setattr(proj, "g", _NoMarkerRestore(proj.g))

    with caplog.at_level(logging.INFO, logger="tortoise.projection"):
        proj._replay_log(EventLog(str(events / "events.jsonl")),
                         confirm_destructive=True)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("ABSENT" in m and "UNCONFIGURED" in m for m in msgs), (
        "the replay path must report the absence it observed, and say what it "
        f"means for the approved set: {msgs}")
    assert any("re-pin" in m.lower() for m in msgs), (
        f"the report must carry the remedy (re-pin the pack set): {msgs}")
    assert not any("survive the wipe" in m for m in msgs), (
        f"nothing may claim the marker survived: {msgs}")
