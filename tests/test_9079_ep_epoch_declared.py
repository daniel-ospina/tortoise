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
is graph-wide monotonic bookkeeping: no journal record carries it, a rebuild
legitimately resets it, and belief recomputation is idempotent from the record,
so nothing treats its value as recoverable state. What was missing is not a
journal append — it is the DECLARATION, so (a) the guard that closes the third
door does not silently refuse the create hot path, and (b) a future reader who
assumes "every cache write is a journal projection" meets the reason here
instead of either blessing the write silently or breaking creates trying to
journal it.

This suite pins the declaration, not the prose:

* the write runs inside a NAMED scope — observed from inside the handle, so a
  caller that merely succeeds today cannot pass;
* the scope wraps the RETRY, so every re-issue re-enters the same
  authorization instead of losing it (#7405/#7685's predicate untouched);
* the epoch still advances exactly once per advance, and a create still lands;
* the epoch is NOT journal-derived: no journal record mentions it, and a
  `rebuild` does not restore it — the "rebuilt-from-zero-safe" half of the
  declaration, pinned rather than asserted.

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
    """The scope wraps the RETRY, not one attempt inside it.

    A contended write refuses the first attempt and is re-issued by
    `_graph_write_with_retry` (#7405). If the scope were opened inside the
    retried lambda it would still pass a single-attempt assertion and lose the
    authorization on the re-issue — which, with `#9079`'s guard armed, refuses
    the SECOND write of a statement whose first attempt never landed.
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

def test_the_epoch_is_not_journaled(sdk_with_journal):
    """The declaration says "not a journal projection". Pin it: no journal
    record carries the epoch, so a rebuild cannot be expected to restore it.

    (`ep_version` appears NOWHERE in the journal — the write is graph
    bookkeeping, not a record of anything that happened to the corpus.)"""
    sdk, journal = sdk_with_journal
    sdk._advance_ep_version()
    sdk.create_point("fact", "a point whose create advances the epoch")

    text = _journal_text(journal)
    assert text.strip(), "the fixture must produce journal records"
    assert "EpMeta" not in text and "ep_version" not in text, (
        "the epoch must ride NO journal record; found one — either the "
        "declaration is wrong or something started journaling it, and a "
        "rebuild would then depend on it")
    for raw in text.splitlines():
        json.loads(raw)  # the log stays parseable, not just absent-of-a-word


def test_a_rebuild_does_not_restore_the_epoch(sdk_with_journal):
    """The other half of the declaration: the epoch is rebuilt-from-zero-safe.

    A `rebuild` folds only journal records; `:EpMeta` is not one of them, so
    the epoch is NOT restored — and nothing may reconcile over it. This is
    what makes "declare it" honest rather than a bypass: the state it holds is
    re-derivable (recomputation is idempotent from the record), so losing it to
    a rebuild costs nothing.
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
