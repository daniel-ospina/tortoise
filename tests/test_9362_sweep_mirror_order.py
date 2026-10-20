"""#9362 — the dirty-root MIRROR must not outrun the graph write it describes.

`TortoiseSDK._sweep_dirty_roots` keeps two copies of the same fact: the
graph's `n.ep_dirty` flag (cross-process truth since #1163) and the in-process
`_dirty_roots` mirror (the hot path). The sweep's contract is that a root it
clears is removed from both — but the landed order removed it from the mirror
FIRST, so for the whole duration of the write (and permanently, if that write
fails) the mirror claimed a root was swept that the graph still holds.

Two symptoms, one mechanism, both measured here:

* **a stale-epoch sweep** drops a root the epoch guard deliberately KEPT —
  the graph half of this is already pinned by
  `tests/test_ep_dirty_persist.py::test_sweep_preserves_newer_dirty_marking`;
  the mirror half was not, and lost the root (#1163's hazard, mirror side);
* **a failed sweep write** loses the root for the lifetime of the process:
  the graph keeps the flag, the mirror forgets it, and `_hydrate_dirty_roots`
  only reads the graph while the mirror is EMPTY — so that process retries
  only if its mirror later drains.

The fix must not overshoot in the other direction: a root whose graph flag was
already cleared by a peer process is NOT dirty any more, and it must leave the
mirror (otherwise the mirror pins a phantom, `dream_health_check`'s
`stale_backlog` never drains, and the same empty-mirror hydration gate is
blocked forever).
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK

# ── Fixtures ─────────────────────────────────────────────────────


@contextmanager
def _db(tmp_prefix: str = "tt_9362_"):
    td = tempfile.mkdtemp(prefix=tmp_prefix)
    yield os.path.join(td, "t.db")


def _chain(sdk: TortoiseSDK, n: int, prefix: str = "c") -> list[str]:
    """Calibrated chain of n claims linked by n-1 operators (SDK-level writes,
    so `_mark_dirty` runs and the flags + epoch persist)."""
    ids = []
    for i in range(n):
        pid = sdk.create_point("statement", f"{prefix}{i}",
                               dedup=False, status="live")["id"]
        sdk.set_point_baseline(pid, 1, 1)
        ids.append(pid)
    for i in range(n - 1):
        sdk.create_operator("IMPL", ids[i], [ids[i + 1]],
                            direction="bidirectional")
    return ids


def _ep_dirty_ids(sdk: TortoiseSDK) -> set[str]:
    return {r[0] for r in sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.ep_dirty = true RETURN n.id").result_set}


def _ep_dirty_at(sdk: TortoiseSDK, cid: str):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.ep_dirty_at",
        params={"id": cid}).result_set
    return rows[0][0] if rows else None


class _FailingSweepWrite:
    """Stands in for the projection while the sweep's SET aborts.

    Only the flag-clearing statement fails: every other query the SDK issues
    while the fixture is installed (including the ones the test itself runs to
    read the graph through the REAL projection) passes through untouched.
    """

    def __init__(self, real, *, already_cleared: set[str] | None = None):
        self._real = real
        self._already_cleared = set(already_cleared or ())
        self.attempts: list[str] = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def g(self):
        real_g = self._real.g
        outer = self

        class _G:
            def __getattr__(self, name):
                return getattr(real_g, name)

            def query(self, cypher, params=None, **kw):
                if "SET n.ep_dirty" in " ".join(cypher.split()):
                    outer.attempts.append(cypher)
                    raise RuntimeError("sweep write aborted (engine timeout)")
                return real_g.query(cypher, params=params, **kw)

        return _G()


# ── Symptom 1: a stale-epoch sweep must not drop the mirror root ──


def test_stale_epoch_sweep_keeps_the_root_in_the_mirror():
    """The graph keeps a newer marking; the mirror must keep it too.

    Mirrors `test_sweep_preserves_newer_dirty_marking`'s graph assertion — the
    mirror is the half that was unguarded (#9362 symptom 1).
    """
    with _db() as db:
        sdk = TortoiseSDK(db)
        try:
            ids = _chain(sdk, 2)
            sdk.dream(mode="local")  # converged → mirrors and flags swept
            assert ids[1] not in sdk._dirty_roots

            sdk._mark_dirty([ids[1]])          # newer marking at epoch e
            e = sdk._ep_epoch()
            assert ids[1] in sdk._dirty_roots

            sdk._sweep_dirty_roots({ids[1]}, run_ep=e - 1)

            assert _ep_dirty_at(sdk, ids[1]) == e, (
                "the epoch guard must keep the newer marking in the graph"
            )
            assert ids[1] in sdk._dirty_roots, (
                "the sweep removed a root from the in-memory mirror that its "
                "own epoch guard refused to clear in the graph — the mirror "
                "no longer describes the graph (#9362 symptom 1)"
            )
        finally:
            sdk.close()


# ── Symptom 2: a failed sweep write must not mutate the mirror ────


def test_failed_sweep_write_leaves_the_mirror_untouched():
    """A sweep whose SET aborts keeps both the graph flag and the mirror root.

    The mirror used to be subtracted BEFORE the write, so a failed write left
    the root in the graph and gone from the mirror — unreachable for the life
    of the process, because `_hydrate_dirty_roots` only reads the graph while
    the mirror is empty (#9362 symptom 2).
    """
    with _db() as db:
        sdk = TortoiseSDK(db)
        try:
            ids = _chain(sdk, 2)
            sdk.dream(mode="local")
            sdk._mark_dirty([ids[1]])
            before = set(sdk._dirty_roots)
            assert ids[1] in before
            assert ids[1] in _ep_dirty_ids(sdk)

            real_proj = sdk._get_proj()
            failing = _FailingSweepWrite(real_proj)
            sdk._get_proj = lambda: failing  # type: ignore[method-assign]
            try:
                try:
                    sdk._sweep_dirty_roots({ids[1]}, run_ep=sdk._ep_epoch())
                except RuntimeError:
                    pass  # the write abort is the point of the fixture
                else:  # pragma: no cover - the fixture must abort the write
                    raise AssertionError("the failing sweep did not abort")
            finally:
                sdk._get_proj = lambda: real_proj  # type: ignore[method-assign]

            assert failing.attempts, "the fixture never intercepted the SET"
            assert ids[1] in _ep_dirty_ids(sdk), "the graph still holds the flag"
            assert ids[1] in sdk._dirty_roots, (
                "a failed sweep write dropped the root from the in-memory "
                "mirror: the graph keeps it, the mirror forgot it, and this "
                "process can only retry it once the mirror drains "
                "(#9362 symptom 2)"
            )
            assert sdk._dirty_roots == before, (
                f"the failed sweep mutated the mirror: {before} -> {sdk._dirty_roots}"
            )
        finally:
            sdk.close()


# ── The epoch predicate itself is NOT part of the fix ───────────


def test_epoch_guarded_sweep_still_spares_an_unstamped_flag():
    """An epoch-guarded sweep clears only flags stamped at or before `run_ep`.

    The landed predicate is `n.ep_dirty_at <= $run_ep`, which excludes a flag
    carrying NO stamp as well as a newer one. #9362 changes WHEN the mirror
    learns the verdict, not WHICH flags the guard clears — a "helpful"
    `ep_dirty_at IS NULL OR …` here would clear a marking whose age this run
    cannot prove, which is the false-PASS direction (#3139/#1163).
    """
    with _db() as db:
        sdk = TortoiseSDK(db)
        try:
            ids = _chain(sdk, 2)
            sdk.dream(mode="local")
            # A flag with no ordering stamp at all.
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) SET n.ep_dirty = true",
                params={"id": ids[1]},
            )
            sdk._dirty_roots.add(ids[1])

            sdk._sweep_dirty_roots({ids[1]}, run_ep=sdk._ep_epoch())

            assert ids[1] in _ep_dirty_ids(sdk), (
                "an epoch-guarded sweep cleared a flag whose stamp it cannot "
                "read — the guard must stay the landed `<=` predicate"
            )
            assert ids[1] in sdk._dirty_roots, (
                "the mirror and the graph disagree: the graph kept the flag, "
                "so the mirror must keep the root"
            )
        finally:
            sdk.close()


# ── The other direction: a root the graph no longer holds ────────


def test_sweep_drops_a_root_a_peer_already_cleared():
    """A root whose graph flag a peer cleared must leave the mirror.

    The exact form of the fix subtracts only what the graph actually cleared,
    so the mirror would otherwise pin a phantom forever — the mirror gates
    `_hydrate_dirty_roots` (it only reads the graph while the mirror is
    EMPTY), so a phantom root blocks hydration and pins `stale_backlog`.
    """
    with _db() as db:
        sdk = TortoiseSDK(db)
        try:
            ids = _chain(sdk, 2)
            sdk.dream(mode="local")
            sdk._mark_dirty([ids[1]])
            assert ids[1] in sdk._dirty_roots

            # A peer process clears the graph flag; this process's mirror
            # still holds the root (it has not hydrated).
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) "
                "SET n.ep_dirty = null, n.ep_dirty_at = null",
                params={"id": ids[1]},
            )
            assert ids[1] in sdk._dirty_roots
            # Retry state is per-mirror-root; give the phantom some before the
            # sweep so the cleanup is observable.
            sdk._retry_attempts[ids[1]] = 3
            sdk._stale_unresolved[ids[1]] = 1

            sdk._sweep_dirty_roots({ids[1]}, run_ep=sdk._ep_epoch())

            assert ids[1] not in sdk._dirty_roots, (
                "the sweep kept a root whose graph flag no longer exists — "
                "the mirror pinned a phantom and can never hydrate again"
            )
            assert ids[1] not in sdk._retry_attempts, (
                "retry state survived a root that left the mirror — it can "
                "never be retried through the mirror again, so the entries "
                "are dead weight (the landed code reset the whole swept set)"
            )
            assert ids[1] not in sdk._stale_unresolved
        finally:
            sdk.close()
