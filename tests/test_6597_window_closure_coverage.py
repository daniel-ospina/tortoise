"""#6597 — the dream coverage denominator must never trail the EP affected set.

`_window_closure` is the denominator of `dream()["coverage"]`
(``affected / closure``). It must be a SUPERSET of the pass's
``affected_claims``, and the docstring's contract — ``affected ⊆ closure``,
so ``coverage <= 1.0`` — must hold for every window and pass.

The pass does NOT seed EP from the raw window: ``dream.py`` seeds it from
``_bfs_select_operators(window, max_hops)``, which itself expands the window
by up to ``max_hops`` graph hops, and ``TortoiseEP._affected_claims`` then
adds its own one-hop seed expansion plus a ``max_hops`` BFS. The pass can
therefore reach up to ``2*max_hops + 1`` claim-hops from the window. Before
the fix ``_window_closure`` expanded only ``max_hops`` levels from a 0-hop
window, so it trailed the pass and ``coverage`` exceeded 1.0 on chains of
4+ claims (``1.333`` on a 4-claim chain, ``1.667`` on a 5-claim chain).

The fix mirrors ``_affected_claims``' seed expansion, uses ``_live_only`` on
every traversal predicate, and expands ``2*max_hops + 1`` claim-hops.

Hermetic embedded harness (``tests/test_dream_noop_3139.py`` pattern).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.sdk import TortoiseSDK


@contextmanager
def _fresh_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_6597_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        yield sdk
    finally:
        sdk.close()
        shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _claim(sdk: TortoiseSDK, content: str) -> str:
    # #992/#943: EP tests model live claims (create_point defaults to draft).
    return sdk.create_point("statement", content, dedup=False,
                            status="live")["id"]


def _chain(sdk: TortoiseSDK, length: int) -> list[str]:
    ids = [_claim(sdk, f"c{i}") for i in range(length)]
    for i in range(length - 1):
        sdk.create_direct_edge("IMPL", ids[i], ids[i + 1])
    return ids


def _pin_single_root(sdk: TortoiseSDK, root: str) -> None:
    """Clear graph-persisted dirty flags so `dream` hydrates ONLY `root`.

    `create_direct_edge` marks both endpoints dirty; `dream()` calls
    `_hydrate_dirty_roots()` and would otherwise run the whole chain as the
    window (which happens to look fine even when broken). The bug needs the
    single dirty root the issue measured with.
    """
    sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.ep_dirty = true "
        "SET n.ep_dirty = null, n.ep_dirty_at = null")
    sdk._dirty_roots.clear()
    sdk._dirty_roots.add(root)


@pytest.mark.parametrize("length", [4, 5, 6, 7, 8])
@pytest.mark.parametrize("max_hops", [1, 2, 3, None])
def test_coverage_never_exceeds_one_on_long_direct_chains(length, max_hops):
    """End-to-end `dream()` coverage on the exact ≥4-claim chains #6597
    measured — `coverage > 1.0` is the bug (1.333 / 1.667 pre-fix)."""
    with _fresh_sdk() as sdk:
        ids = _chain(sdk, length)
        _pin_single_root(sdk, ids[0])
        result = sdk.dream(mode="local", require_calibration=False,
                           max_hops=max_hops)
        assert 0.0 <= result["coverage"] <= 1.0, (
            f"coverage={result['coverage']} on a {length}-claim chain "
            f"(max_hops={max_hops}): the closure trails the affected set")
        # A converged direct chain covers its whole reachable set exactly.
        assert result["coverage"] == 1.0


@pytest.mark.parametrize("max_hops", [1, 2, 3, None])
def test_affected_claims_subset_of_window_closure(max_hops):
    """The denominator contract: every claim EP can reach from the window is
    in `_window_closure`."""
    with _fresh_sdk() as sdk:
        ids = _chain(sdk, 7)
        affected = sdk._get_ep()._affected_claims([ids[0]], max_hops)
        reachable = sdk._window_closure([ids[0]], max_hops)
        assert affected <= reachable, (
            f"max_hops={max_hops}: {len(affected - reachable)} claim(s) "
            "outside the closure")


def test_closure_seed_expands_window_neighbourhood():
    """The one-hop seed expansion: a 4-chain window's closure must include
    the far endpoint the pass reaches (the hop the pre-fix closure missed)."""
    with _fresh_sdk() as sdk:
        ids = _chain(sdk, 4)
        reachable = sdk._window_closure([ids[0]], 2)
        # 0-hop window + 1 seed hop + 2*max_hops BFS = all four claims.
        assert set(ids) <= reachable


def test_terminal_bridge_is_not_counted_in_closure():
    """Liveness alignment (#2422): a terminal/outdated point is never
    reachable by EP, so it must not inflate the denominator."""
    with _fresh_sdk() as sdk:
        a = _claim(sdk, "a")
        b = _claim(sdk, "b")
        c = _claim(sdk, "c")
        sdk.create_direct_edge("IMPL", a, b)
        sdk.create_direct_edge("IMPL", b, c)
        sdk.retract_point(c)
        reachable = sdk._window_closure([a], 2)
        assert c not in reachable
        assert b in reachable


def test_operator_window_closure_covers_affected():
    """A window carrying an operator (`_mark_dirty` seeds `[mitigation,
    operator]`) must keep the operator's inputs in the denominator."""
    with _fresh_sdk() as sdk:
        src = _claim(sdk, "src")
        tgt = _claim(sdk, "tgt")
        sdk.set_point_baseline(src, 10, 1)
        sdk.set_point_baseline(tgt, 1, 1)
        op = sdk.create_operator("IMPL", src, [tgt])
        mit = sdk.mitigate_operator(op["id"], "caveat", strength=0.3)
        window = [mit["id"], op["id"]]
        affected = sdk._get_ep()._affected_claims(window, 2)
        reachable = sdk._window_closure(window, 2)
        assert affected <= reachable, (
            f"operator window denominator under-counts: affected={len(affected)}"
            f" reachable={len(reachable)}")


def test_stale_first_coverage_is_bounded():
    """The same bound must hold on the scheduler path, where a budget makes
    `affected` a strict subset of the pre-pass window closure."""
    with _fresh_sdk() as sdk:
        _chain(sdk, 6)
        # Drain the write-triggered dirty roots so the scheduler selects.
        sdk.dream(mode="local", require_calibration=False)
        result = sdk.dream(mode="stale-first", require_calibration=False,
                           budget=2)
        assert 0.0 <= result["coverage"] <= 1.0
