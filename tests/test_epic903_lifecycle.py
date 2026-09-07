"""Epic 903-C9 (#1247) — lifecycle absorption: supersede/invalidate/
approve_merge feed dreaming + transfer invalidation (DE2E-5).

Hermetic harness per tests/epic903_fixtures.py (fresh_sdk disables the
calibration gate; live claims; fixed seeds — never wall-clock).
"""
from __future__ import annotations

import random

from tests.epic903_fixtures import FIXED_SEED, f1_corpus, fresh_sdk


def _msg_edges(proj) -> int:
    """Count edges still carrying graph-persisted message state."""
    rows = proj.g.query(
        "MATCH ()-[r:IMPL|NAND]->() "
        "WHERE r.msg_alpha IS NOT NULL OR r.back_msg_alpha IS NOT NULL "
        "RETURN count(r)"
    ).result_set
    return int(rows[0][0]) if rows else 0


def _dirty(sdk) -> set:
    return set(sdk._dirty_roots)


class TestLifecycleAbsorption:
    def _seed_messages(self, sdk):
        """Run a pass so graph-persisted messages exist (warm-start seeds)."""
        random.seed(FIXED_SEED)
        return sdk.dream(mode="full")

    def test_supersede_marks_dirty_and_invalidates_surviving_edges(self):
        f = f1_corpus()
        try:
            proj = f.sdk._get_proj()
            self._seed_messages(f.sdk)
            assert _msg_edges(proj) > 0, "seeds must exist pre-supersede"
            old_id = f.claims["p1"]
            new_id = f.claims["p2"]
            res = f.sdk.supersede_point(old_id, new_id)
            assert res["invalidated"] is True
            # #2422: the superseded old point is terminal for EP — a dirty
            # flag would strand forever (never swept). The LIVE successor
            # enters the dirty set (W1 lifecycle contract).
            assert old_id not in _dirty(f.sdk), (
                "terminal (superseded) point must not strand in dirty set (#2422)")
            assert new_id in _dirty(f.sdk)
            # Transfer creates NEW edges without msg_* (verified: zero msg_*
            # refs in supersede_point) — the C9 invalidation drops the
            # SURVIVING node's own edges' seeds (its context changed under
            # the transfer). Assert edges touching the surviving node carry
            # no message state; other claims' edges may keep theirs.
            rows = proj.g.query(
                "MATCH (p:Point {id:$id})-[r:IMPL|NAND]-() "
                "WHERE r.msg_alpha IS NOT NULL OR r.back_msg_alpha IS NOT NULL "
                "RETURN count(r)",
                params={"id": new_id},
            ).result_set
            assert int(rows[0][0]) == 0, (
                "surviving node's edges must be invalidated (C9 wiring)")
            # The dirty-absorption re-derives the surviving belief from the
            # post-event graph: a converged pass runs and the surviving node
            # is freshly stamped.
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="full")
            assert r["converged_all"] is True
            post = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.confidence, n.lastDreamedAt",
                params={"id": new_id},
            ).result_set
            assert post and post[0][1] is not None, (
                "surviving node must be re-derived + stamped post-supersede")
        finally:
            f.sdk.close()

    def test_invalidate_marks_pair_and_drops_seeds(self):
        f = f1_corpus()
        try:
            proj = f.sdk._get_proj()
            self._seed_messages(f.sdk)
            assert _msg_edges(proj) > 0
            res = f.sdk.invalidate_point(f.claims["p1"], f.claims["p2"])
            assert res["invalidated"] is True
            # #2422: p1 (flag-outdated, terminal for EP) must not strand in
            # the dirty set; the live successor p2 enters it (W1 contract).
            assert f.claims["p1"] not in _dirty(f.sdk), (
                "terminal (invalidated) point must not strand in dirty set (#2422)")
            assert f.claims["p2"] in _dirty(f.sdk)
            # invalidate_point drops both endpoints' edges' seeds (C9 wiring).
            rows = proj.g.query(
                "MATCH (p:Point)-[r:IMPL|NAND]-() "
                "WHERE p.id IN $ids "
                "AND (r.msg_alpha IS NOT NULL OR r.back_msg_alpha IS NOT NULL) "
                "RETURN count(r)",
                params={"ids": [f.claims["p1"], f.claims["p2"]]},
            ).result_set
            assert int(rows[0][0]) == 0, (
                "invalidate_point must drop both endpoints' seeds")
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="full")
            assert r["converged_all"] is True
        finally:
            f.sdk.close()

    def test_approve_merge_transitive_invalidation(self):
        """approve_merge wires the IMPL at promotion via create_operator —
        the C4 topology hook invalidates transitively; the surviving node's
        confidence is re-derived."""
        sdk, _ = fresh_sdk(prefix="tortoise_epic903_c9merge_")
        try:
            proj = sdk._get_proj()
            a = sdk.create_point("statement", "dup A", dedup=False,
                                 status="live")["id"]
            b = sdk.create_point("statement", "dup B", dedup=False,
                                 status="live")["id"]
            sdk.create_operator("IMPL", a, [b])
            # Mark b as the dedup candidate and approve the merge.
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.dedup_candidate = true",
                params={"id": b},
            )
            self._seed_messages(sdk)
            assert _msg_edges(proj) > 0
            res = sdk.approve_merge(b, action="merge")
            assert res["candidate_id"] == b
            # The merge cycle re-derived the surviving belief: a converged
            # pass runs without stale-seed crashes.
            random.seed(FIXED_SEED)
            r = sdk.dream(mode="full")
            assert r["converged_all"] is True
        finally:
            sdk.close()
