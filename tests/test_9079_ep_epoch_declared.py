"""#9123 §17.1 — the `:EpMeta` epoch is a DECLARED graph-metadata lane.

`docs/architecture/STORAGE-ARCHITECTURE.md` §17.1 (from the §2.0 owner ruling:
Postgres is the record, FalkorDB a derived cache) says every cache write is a
projection of the journal, and `#7951` enforced that at the fold
(`FalkorProjection.apply`) and at the public raw-query lane
(`FalkorProjection.query`). The third door — the guarded write handle the SDK
itself uses, `proj.g.query` — is still open, and `#9079` decomposes closing it
into the writers that legitimately reach it first.

`TortoiseSDK._advance_ep_version` issues one of those writes:

    MERGE (m:EpMeta) SET m:ep_version = coalesce(m.ep_version, 0) + 1

It is the MOST frequently refused statement in the §17.1 suite run on #9079's
lane (5 of 7 failures), because it runs outside every authorization scope:
`create_point` opens `_live_write_scope("create_point")` around its own CREATE
block, and this MERGE happens *before* it.

**It is not a journal projection, and journaling it would be wrong.** The epoch
is graph-wide monotonic bookkeeping: no journal record writes the COUNTER,
a rebuild legitimately resets it, and belief recomputation is idempotent from
the record, so nothing treats the counter as recoverable state. (The counter's
VALUE does ride the record, as each Point's own `ep_dirty_at` stamp — see
`test_the_epoch_is_not_journaled`, which pins that split rather than asserting
the value away.) What was missing is not a
journal append — it is the DECLARATION, so (a) the guard that closes the third
door does not silently refuse the create hot path, and (b) a future reader who
assumes "every cache write is a journal projection" meets the reason here
instead of either blessing the write silently or breaking creates trying to
journal it.

This suite pins the declaration, not the prose:

* the write runs inside a NAMED scope — observed from inside the handle, so a
  caller that merely succeeds today cannot pass;
* the scope wraps the RETRY, so both attempts observe the same declared
  reason and the refused attempt does not land (#7405/#7685's predicate
  untouched). It does NOT separate "around the retry" from "inside the
  re-entered lambda" — the retry's unit of re-issue is `fn()` itself, so an
  inner scope is re-entered on every attempt too and no behaviour differs;
  the test's docstring states what it actually proves;
* the epoch still advances exactly once per advance, and a create still lands;
* the epoch is NOT journal-derived as GRAPH METADATA: no record writes an
  `:EpMeta`-shaped node and no payload key carries the counter — while its
  VALUE does ride the record as each point's `ep_dirty_at` stamp — and a
  `rebuild` does not restore the counter, which leaves those replayed stamps
  AHEAD of it until it re-climbs (#5166: a delayed sweep, not corruption).
  Both halves of the declaration, pinned rather than asserted.

MUTATION for every test here: drop the `_graph_metadata_write_scope` wrapper in
`TortoiseSDK._advance_ep_version`. The declaration tests fail on
`_CACHE_WRITE_AUTHORIZED.get() is None` at the write; the semantics tests still
pass (they pin what the declaration CLAIMS, so the claim cannot drift).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import redis.exceptions

from tortoise.projection import _CACHE_WRITE_AUTHORIZED

#: The declared lane name. Pinned as a literal: a lane that renames itself
#: silently is a lane a future reader cannot find from the refusal message.
EPOCH_LANE = "graph-metadata:ep-epoch"

#: The journal's own ENVELOPE keys — record schema, not payload data. Excluded
#: from the "is the epoch's value in the payload?" walk because
#: `projection_version` is an envelope integer that can coincide with the
#: epoch's value by accident (it is 2 today), which would make the walk report
#: a payload occurrence that says nothing about the epoch.
_JOURNAL_ENVELOPE = frozenset({
    "event_id", "ts", "type", "initiated_by", "projection_version",
})

#: Payload key spellings that WOULD journal the counter itself. Case-folded.
_EPOCH_COUNTER_KEYS = frozenset({
    "ep_version", "ep_version_at", "ep_epoch", "epoch", "ep_meta_epoch",
})


@pytest.fixture
def sdk_with_journal(tmp_path):
    """A real SDK with a configured JSONL journal (so §17.1's guard is ARMED).

    Lane-agnostic, like the `#7951` fixture: the explicit `db_path` makes the
    projection embedded when no URI is configured, and a URI-set session is
    redirected to its server. `event_log_path` is what arms the guard.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "ep_epoch.db"),
                      event_log_path=str(journal))
    try:
        yield sdk, journal
    finally:
        sdk.close()


def _record_authorizations(monkeypatch):
    """Patch `_GuardedGraph.query` so every statement records the §17.1
    authorization in force AT THE MOMENT IT IS ISSUED.

    The observation is at the handle, not around the caller: a scope opened
    somewhere the write never runs would pass a caller-side assertion while
    authorizing nothing. The class is patched rather than the instance because
    the handle is `__slots__`-restricted and read-only by construction.
    """
    from tortoise.projection import _GuardedGraph

    real = _GuardedGraph.query
    seen: list[tuple[str, str | None]] = []

    def query(self, cypher: str, params=None, timeout=None):
        seen.append((cypher, _CACHE_WRITE_AUTHORIZED.get()))
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", query)
    return seen


def _epoch(proj) -> int | None:
    rows = proj.g.query(
        "MATCH (m:EpMeta) RETURN m.ep_version").result_set
    return int(rows[0][0]) if rows and rows[0][0] is not None else None


def _journal_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── the declaration ──────────────────────────────────────────────────────

def test_the_epoch_write_runs_inside_a_named_scope(sdk_with_journal,
                                                  monkeypatch):
    """RED before #9123: the epoch MERGE is issued with NO authorization.

    `_CACHE_WRITE_AUTHORIZED` is `None` at the write today, which is exactly
    what `#9079`'s handle guard reads as "refuse", so closing that guard turns
    every create path into a `DirectCacheWriteRefused`. The declaration is what
    keeps that from being a silent regression.
    """
    sdk, _journal = sdk_with_journal
    seen = _record_authorizations(monkeypatch)

    sdk._advance_ep_version()

    epoch_writes = [(c, r) for c, r in seen if "EpMeta" in c]
    assert epoch_writes, (
        "the epoch write never reached `proj.g.query`; this test would be "
        f"vacuous. statements seen: {[c[:60] for c, _ in seen]}")
    assert [r for _c, r in epoch_writes] == [EPOCH_LANE], (
        "the epoch MERGE must be issued inside its named graph-metadata "
        f"declaration; observed authorizations={[r for _c, r in epoch_writes]}")


def test_the_declaration_survives_a_retry(sdk_with_journal, monkeypatch):
    """Every attempt of a retried write observes the same declaration, and the
    refused attempt does not land.

    A contended write refuses the first attempt and is re-issued by
    `_graph_write_with_retry` (#7405). What this pins is that the authorization
    is in force at the handle on EVERY attempt — a scope opened around a single
    attempt would fail here on the second one — and that the aborting attempt
    wrote nothing (the epoch advances exactly once).

    WHAT IT DOES NOT SEPARATE, stated so the claim cannot drift again: this
    test cannot tell "scope around the retry" from "scope inside the retried
    lambda", because the retry's unit of re-issue is `fn()` itself
    (`sdk.py::_graph_write_with_retry`), so an inner scope is re-entered on
    every attempt and yields `[EPOCH_LANE, EPOCH_LANE]` as well. The shipped
    shape is chosen for locality (one scope around the whole retried
    operation), not because an inner scope would lose the authorization.
    """
    from tortoise.projection import _GuardedGraph

    sdk, _journal = sdk_with_journal
    proj = sdk._get_proj()
    real = _GuardedGraph.query
    attempts: list[str | None] = []

    def query(self, cypher: str, params=None, timeout=None):
        if "EpMeta" not in cypher:
            return real(self, cypher, params=params, timeout=timeout)
        attempts.append(_CACHE_WRITE_AUTHORIZED.get())
        if len(attempts) == 1:
            # The engine's write-slot refusal: state-INDEPENDENT, so the retry
            # predicate authorizes it without an EXISTS probe (`retry.py`).
            raise redis.exceptions.ResponseError(
                "another write is in progress, retry the query")
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", query)

    assert sdk._advance_ep_version() == 1
    assert attempts == [EPOCH_LANE, EPOCH_LANE], (
        "every attempt must re-enter the same declaration; "
        f"observed={attempts}")
    assert _epoch(proj) == 1, (
        "the refused attempt must not land — the epoch advances exactly once, "
        f"observed={_epoch(proj)}")


def test_the_epoch_still_advances_and_a_create_still_lands(sdk_with_journal):
    """The declaration must not change the semantics it authorizes: one advance
    per call, and a create path that stamps `ep_dirty_at` from the same epoch."""
    sdk, _journal = sdk_with_journal
    proj = sdk._get_proj()

    assert sdk._advance_ep_version() == 1
    assert sdk._advance_ep_version() == 2

    point = sdk.create_point("fact", "the epoch advances per create")
    rows = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN count(p)",
        params={"id": point["id"]}).result_set
    assert int(rows[0][0]) == 1
    assert _epoch(proj) == 3, (
        "create_point advances the epoch once (its `ep_dirty_at` stamp), so "
        f"three advances in total; observed={_epoch(proj)}")


def test_a_create_path_is_not_refused_for_its_epoch_write(sdk_with_journal,
                                                         monkeypatch):
    """The indicator that matters for #9079: arm a guard over THIS write and a
    create path is not refused for it.

    #9124 is the child that arms `_GuardedGraph.query` for real. This test
    simulates the narrow slice of it that #9123 must answer for — an
    UNAUTHORIZED `:EpMeta` write is refused — so the declaration is measured
    against the guard it exists for instead of resting on prose. It is
    deliberately NOT a full guard simulation: the rest of the statement class is
    #9124's to declare (see the measurement in that issue's thread).
    """
    from tortoise.projection import (
        DirectCacheWriteRefused,
        _GuardedGraph,
        _statement_writes,
    )

    sdk, _journal = sdk_with_journal
    real = _GuardedGraph.query
    refused: list[str] = []

    def query(self, cypher: str, params=None, timeout=None):
        if ("EpMeta" in cypher and _statement_writes(cypher)
                and _CACHE_WRITE_AUTHORIZED.get() is None):
            refused.append(cypher)
            raise DirectCacheWriteRefused(
                "§17.1: an unauthorized `:EpMeta` write reached the derived "
                f"cache. statement={cypher[:120]}")
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", query)

    point = sdk.create_point("fact", "a create with the epoch guard armed")
    assert refused == [], (
        "the epoch write did not declare itself, so arming #9079's guard "
        "would refuse every create path: " + " | ".join(c[:120] for c in refused))
    assert point["id"]
    assert _epoch(sdk._get_proj()) == 1


# ── the semantics the declaration claims ─────────────────────────────────

def _leaves(node, path=()):
    """Every scalar leaf in a record, with the key path that reaches it."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaves(value, (*path, index))
    else:
        yield path, node


def test_the_epoch_is_not_journaled(sdk_with_journal):
    """The declaration says "not a journal projection". Pin BOTH halves of the
    precise claim, because only one of them is "the value is absent":

    * the COUNTER is not journaled — no record writes an `:EpMeta`-shaped node
      and no payload key carries it (obligation 1), so a rebuild cannot be
      expected to restore it;
    * the VALUE does ride the record — as each Point's own `ep_dirty_at` stamp,
      which the replay restores — which is why "no record carries the epoch"
      would be too strong, and why obligation 2 is a BOUND (see
      `test_a_rebuild_does_not_restore_the_epoch`).

    DISCRIMINATION: the first half is asserted on PARSED records and key paths,
    not on a substring of the file, so an epoch journaled under another event
    name or key still fails it (a `{"type": "EpochAdvanced", "epoch": N}`
    record trips the type and key checks) — the previous `"ep_version" not in
    text` spelling passed under any renamed key and was therefore vacuous about
    the claim it was captioned with. The second half is a POSITIVE assertion:
    drop the `ep_dirty_at` stamp from `create_point`'s CREATE map and it fails.
    """
    sdk, journal = sdk_with_journal
    sdk._advance_ep_version()
    sdk.create_point("fact", "a point whose create advances the epoch")
    epoch = sdk._ep_epoch()

    text = _journal_text(journal)
    assert text.strip(), "the fixture must produce journal records"
    records = [json.loads(raw) for raw in text.splitlines()]

    # (a) obligation 1: the counter is in NO record, under any key or event.
    assert {rec.get("type") for rec in records} == {"PointAdded"}, (
        "the journal must carry only corpus events; observed types="
        f"{sorted({rec.get('type') for rec in records})} — a graph-metadata "
        "event WOULD be a record describing the epoch, which obligation 1 "
        "forbids, and a rebuild would then depend on it")
    stamped: list[tuple] = []
    for rec in records:
        payload = {k: v for k, v in rec.items() if k not in _JOURNAL_ENVELOPE}
        for path, value in _leaves(payload):
            assert "EpMeta" not in path, (
                "a journal payload carries an `:EpMeta`-shaped node; the "
                f"epoch must not be journaled as graph metadata: {path}")
            assert not (isinstance(path[-1], str)
                        and path[-1].lower() in _EPOCH_COUNTER_KEYS), (
                f"a journal payload key carries the epoch counter: {path}={value!r}")
            if isinstance(value, bool):
                continue  # `ep_dirty: true` is a flag, not the epoch (1 == True)
            if isinstance(value, int) and value == epoch:
                stamped.append(path)

    # (b) the value IS in the record — only ever as the per-point stamp.
    assert stamped, (
        "no journal record carries the epoch's value at all, so the stamp half "
        "of this test would be vacuous: `ep_dirty_at` is supposed to ride the "
        "PointAdded payload")
    assert all(path[-1] == "ep_dirty_at" for path in stamped), (
        "the epoch value may appear in the record ONLY as a per-point "
        f"`ep_dirty_at` stamp; observed paths={stamped}")
    for raw in text.splitlines():
        json.loads(raw)  # the log stays parseable, not just absent-of-a-word


def test_a_rebuild_does_not_restore_the_epoch(sdk_with_journal):
    """The other half of the declaration: the epoch is rebuilt-from-zero-safe —
    and the reset really does invert the ordering bound, which is what makes
    obligation 2 a BOUND rather than an absence.

    A `rebuild` folds only journal records; `:EpMeta` is not one of them, so
    the epoch is NOT restored. What IS restored is each Point's `ep_dirty_at`
    stamp (obligation 1's value half) — so the reset leaves that stamp AHEAD
    of the counter, and `_sweep_dirty_roots`' own guard
    (`n.ep_dirty_at <= $run_ep`) refuses to clear the flag until the epoch
    re-climbs past it. That is #5166: a delayed sweep, not corruption, and the
    reason a caller may not claim "no comparison exists". Both halves are
    pinned here — the loss, and the comparison the loss inverts — so this test
    cannot pass by the bound being inert.
    """
    sdk, journal = sdk_with_journal
    proj = sdk._get_proj()
    point = sdk.create_point("fact", "a point that survives the rebuild")
    assert _epoch(proj) == 1

    proj.rebuild_all(str(journal.parent), confirm_destructive=True)

    assert _epoch(proj) is None, (
        "the epoch is graph metadata, not a journal projection: a rebuild "
        f"replays records and does not restore it; observed={_epoch(proj)}")
    rows = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN count(p)",
        params={"id": point["id"]}).result_set
    assert int(rows[0][0]) == 1, (
        "the REBUILD must still reproduce the point from the journal alone — "
        "if this fails the fixture, not the claim, is what broke")

    # THE BOUND, exercised rather than described: the replayed stamp outlives
    # the counter that minted it, and the sweep compares the two.
    rows = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.ep_dirty, p.ep_dirty_at",
        params={"id": point["id"]}).result_set
    dirty, stamp = rows[0][0], rows[0][1]
    assert dirty is True, (
        "the journaled `ep_dirty` flag must survive the replay — it is what "
        f"the sweep compares against the epoch; observed={dirty!r}")
    assert int(stamp) == 1, (
        "the replayed stamp is the epoch the create minted; "
        f"observed={stamp!r}")
    run_ep = sdk._ep_epoch()  # what dream.py::_snapshot_ep_epoch reads
    assert run_ep == 0, (
        f"a graph with no `:EpMeta` reads 0; observed={run_ep}")
    assert not int(stamp) <= run_ep, (
        "`_sweep_dirty_roots` clears a flag only under `ep_dirty_at <= "
        f"run_ep`; observed stamp={stamp} run_ep={run_ep}. If this ever holds, "
        "the reset stopped inverting the bound — update obligation 2 of "
        "`_graph_metadata_write_scope` rather than deleting this assertion")
