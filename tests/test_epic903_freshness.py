"""Epic 903-C2 (#1240) — freshness property ``lastDreamedAt`` + atomic
write-back + promote/zero-affected fixes.

DE2E splits (verify gate, issue #1240):
- DE2E-1 (this issue): full pass stamps reachable claims + scanned
  operator-less claims; atomicity sub-case (both-or-neither on partial
  failure of the single-UNWIND write-back). The return-shape + zero-operator
  negative is 903-C6's — NOT duplicated here.
- DE2E-3 operator-less-write sub-case: a freshly written isolated claim
  (no operators) gets trivially stamped by the local pass.
- DE2E-4 (this issue): lastDreamedAt present on reads; draft→promote
  re-enters dirty roots and the next pass re-stamps (promote→_mark_dirty
  fix); zero-affected retention (draft-only dirty roots + converged
  zero-affected pass → roots remain).
- Index: idempotent ``:Point(is_operator, lastDreamedAt)`` (embedded:
  plain ``:Point(lastDreamedAt)``) at init — replay-safe for AOF.

Hermetic embedded pattern per tests/test_epic903_fixtures.py (F2 builder +
fresh_sdk + _make_claim); no wall-clock staleness manufacturing.
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

from tests.epic903_fixtures import (
    FIXED_SEED,
    STAMP_OLD,
    f2_staleness_regions,
    fresh_sdk,
    _make_claim,
)
from tortoise.sdk import TortoiseSDK

ISO = "%Y-%m-%dT%H:%M:%S.%f%z"


def _read_stamp(proj, point_id: str) -> str | None:
    row = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.lastDreamedAt",
        params={"id": point_id},
    ).result_set
    return row[0][0] if row else None


def _read_confidence(proj, point_id: str) -> float | None:
    row = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence",
        params={"id": point_id},
    ).result_set
    return row[0][0] if row else None


def _read_updated_at(proj, point_id: str) -> str | None:
    row = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.updatedAt",
        params={"id": point_id},
    ).result_set
    return row[0][0] if row else None


def _stamps(proj, ids) -> dict[str, str | None]:
    return {pid: _read_stamp(proj, pid) for pid in ids}


# ── DE2E-1 — full pass stamps reachable + scanned operator-less ───────


class TestDe2e1FullPassFreshness:
    def test_full_pass_stamps_reachable_and_scanned_operator_less(self):
        """F2 fixture: dream(full=True) stamps every non-operator claim — the
        12 reachable region claims via the EP write-back (lastDreamedAt in
        [t0, t1], overwriting the fixed F2 ISO stamps) and the 1 isolated
        operator-less claim via the trivial-scan path (scanned_count=1).
        total_affected = reachable only; scanned_count reported separately."""
        f = f2_staleness_regions()
        try:
            proj = f.sdk._get_proj()
            all_claims = [pid for r in f.regions for pid in r.claims]
            all_claims.append(f.isolated_claim)
            # F2 manufactures fixed ISO stamps — snapshot pre-pass values.
            before = _stamps(proj, all_claims)
            assert before[f.isolated_claim] is None, "isolated claim starts unstamped"
            assert any(v is not None for v in before.values()), "F2 regions pre-stamped"

            t0 = datetime.now(timezone.utc)  # noqa: UP017
            random.seed(FIXED_SEED)
            result = f.sdk.dream(full=True)
            t1 = datetime.now(timezone.utc)  # noqa: UP017

            assert result["converged_all"] is True
            assert result["total_affected"] == 12, (
                f"total_affected = reachable only (12 region claims), "
                f"got {result['total_affected']}"
            )
            assert result["scanned_count"] == 1, (
                f"scanned_count = operator-less stamped (1 isolated claim), "
                f"got {result['scanned_count']}"
            )
            # Reachable claims stamped in [t0, t1] (window assertion — never
            # exact-equality to an implicit pass timestamp).
            for pid in all_claims:
                stamp = _read_stamp(proj, pid)
                assert stamp is not None, f"claim {pid} not stamped"
                parsed = datetime.fromisoformat(stamp)
                assert t0 <= parsed <= t1, (
                    f"stamp {stamp} outside pass window [{t0}, {t1}]"
                )
        finally:
            f.sdk.close()

    def test_atomicity_single_unwind_both_or_neither(self):
        """Injected partial failure of the write-back → the write-back's own
        fields (lastDreamedAt + updatedAt) are all-or-nothing: NEITHER applied
        to any claim (single-UNWIND atomicity, surface 5). A successful pass
        applies BOTH. Pins the no-N+1 property: exactly one write-back query
        per pass.

        Note: ``confidence`` is NOT a discriminator — ``ep.run`` flushes it
        independently via ``_flush_cache`` before the dream write-back runs
        (the epic plan calls the dream confidence write redundant). The
        atomicity guarantee is statement-local to the write-back, which is
        what this test proves."""
        f = f2_staleness_regions()
        try:
            proj = f.sdk._get_proj()
            all_claims = [pid for r in f.regions for pid in r.claims]
            all_claims.append(f.isolated_claim)
            # The write-back's UNIQUE fields are lastDreamedAt + updatedAt
            # (_flush_cache independently writes confidence during ep.run, so
            # the atomicity discriminators are the two write-back-only
            # fields). Snapshot pre-pass values.
            stamps_before = _stamps(proj, all_claims)
            updated_before = {pid: _read_updated_at(proj, pid) for pid in all_claims}

            # Inject a failure into the write-back UNWIND (the only query
            # containing lastDreamedAt) — it must abort the WHOLE statement.
            raw = proj.g._g
            orig = raw.query
            write_back_calls: list[str] = []

            def failing(cypher, params=None, **kw):
                if "UNWIND" in cypher and "lastDreamedAt" in cypher:
                    write_back_calls.append(cypher)
                    raise RuntimeError("injected write-back failure")
                return orig(cypher, params=params, **kw)

            raw.query = failing
            random.seed(FIXED_SEED)
            try:
                f.sdk.dream(full=True)
                pytest.fail("expected injected write-back failure to propagate")
            except RuntimeError as exc:
                assert "injected write-back failure" in str(exc)
            finally:
                raw.query = orig

            # Single statement — no N+1 loop, no partial writes.
            assert len(write_back_calls) == 1, (
                f"write-back must be ONE UNWIND statement, got "
                f"{len(write_back_calls)} calls"
            )
            # NEITHER applied: every claim's stamp AND updatedAt unchanged
            # (the two write-back-only fields — a per-claim loop would have
            # left earlier claims stamped/updated before the failure).
            # Confidence is deliberately NOT asserted here: ep.run's
            # _flush_cache writes it before the write-back regardless
            # (redundant rewrite per the epic plan) — the atomicity contract
            # is statement-local.
            for pid in all_claims:
                assert _read_stamp(proj, pid) == stamps_before[pid], (
                    f"partial stamp applied for {pid}: "
                    f"{stamps_before[pid]} -> {_read_stamp(proj, pid)}"
                )
                assert _read_updated_at(proj, pid) == updated_before[pid], (
                    f"partial updatedAt applied for {pid}"
                )

            # Success path: BOTH applied for every claim (stamp present, and
            # confidence present for EP-reachable claims — written together
            # by the same write-back / the EP flush). The isolated
            # operator-less claim has no confidence (no EP runs on it — the
            # trivial stamp is stamp-only by design).
            random.seed(FIXED_SEED)
            result = f.sdk.dream(full=True)
            assert result["converged_all"] is True
            reachable = [pid for r in f.regions for pid in r.claims]
            for pid in all_claims:
                assert _read_stamp(proj, pid) is not None, (
                    f"claim {pid} missing lastDreamedAt after success"
                )
            for pid in reachable:
                assert _read_confidence(proj, pid) is not None, (
                    f"claim {pid} missing confidence after success"
                )
        finally:
            f.sdk.close()


# ── DE2E-3 operator-less-write sub-case ───────────────────────────────


class TestDe2e3OperatorLessWrite:
    def test_local_pass_trivially_stamps_fresh_isolated_claim(self):
        """D3-3b: a freshly written isolated claim (no operators) → the local
        pass trivially stamps it. Pre-fix the seed-empty early-return at
        dream.py L92-93 left lastDreamedAt null forever."""
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_d3_")
        try:
            p = _make_claim(sdk, "fresh isolated claim")
            assert p["id"] in sdk._dirty_roots, "fresh claim is a dirty root"

            t0 = datetime.now(timezone.utc)  # noqa: UP017
            random.seed(FIXED_SEED)
            result = sdk.dream(dirty_only=True)
            t1 = datetime.now(timezone.utc)  # noqa: UP017

            assert result["converged"] is True
            assert p["id"] in result["affected_claims"], (
                "trivially stamped claim must be reported in affected_claims"
            )
            stamp = _read_stamp(sdk._get_proj(), p["id"])
            assert stamp is not None, "isolated claim must be trivially stamped"
            assert t0 <= datetime.fromisoformat(stamp) <= t1, (
                f"stamp {stamp} outside pass window"
            )
            # Stamped claims leave the dirty set (they were handled).
            assert p["id"] not in sdk._dirty_roots
        finally:
            sdk.close()


# ── DE2E-4 — freshness on reads; draft→promote; zero-affected ─────────


class TestDe2e4FreshnessLifecycle:
    def test_last_dreamed_at_present_on_reads(self):
        """J4: get_point returns lastDreamedAt (comes free via properties)."""
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_d4_")
        try:
            a = _make_claim(sdk, "a")
            b = _make_claim(sdk, "b")
            sdk.create_operator("IMPL", b["id"], [a["id"]])
            sdk.set_point_baseline(b["id"], 8.0, 2.0)
            random.seed(FIXED_SEED)
            sdk.dream(dirty_only=True)

            point = sdk.get_point(a["id"])
            assert "lastDreamedAt" in point, (
                f"get_point must expose lastDreamedAt, got keys "
                f"{sorted(point.keys())}"
            )
            assert point["lastDreamedAt"] is not None
        finally:
            sdk.close()

    def test_draft_promote_reenters_dirty_roots_and_restamps(self):
        """Draft→promote negative (fixed): after demote+promote the claim
        re-enters _dirty_roots (promote→_mark_dirty fix) and the next pass
        re-stamps/re-derives it. Simulated demotion via direct Cypher SET
        (there is no SDK demote — status transitions go through promote)."""
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_d4_")
        try:
            proj = sdk._get_proj()
            a = _make_claim(sdk, "a")
            b = _make_claim(sdk, "b")
            sdk.create_operator("IMPL", b["id"], [a["id"]])
            sdk.set_point_baseline(b["id"], 8.0, 2.0)

            # First pass: converges + stamps.
            random.seed(FIXED_SEED)
            sdk.dream(dirty_only=True)
            assert sdk._dirty_roots == set(), "converged pass clears dirty roots"
            stamp_first = _read_stamp(proj, a["id"])
            assert stamp_first is not None

            # Demote a to draft (direct Cypher — no SDK demote path).
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.status = 'draft', "
                "n.updatedAt = $now",
                params={"id": a["id"], "now":
                        datetime.now(timezone.utc).isoformat()},  # noqa: UP017
            )

            # Promote back → must call _mark_dirty (the fix): a re-enters
            # the dirty roots.
            res = sdk.promote_point(a["id"])
            assert res["promoted"] is True, res
            assert a["id"] in sdk._dirty_roots, (
                "promote_point must mark the promoted claim dirty "
                "(promote→_mark_dirty fix)"
            )
            # The operator neighborhood is dirty too (1-hop reverse BFS).
            assert b["id"] in sdk._dirty_roots, (
                "promote must mark both operator endpoints dirty"
            )

            # Next pass re-stamps + re-derives (dirty ≠ stale; the next pass
            # touches it because it re-entered the dirty set).
            random.seed(FIXED_SEED)
            result = sdk.dream(dirty_only=True)
            assert result["converged"] is True
            assert a["id"] in result["affected_claims"]
            stamp_second = _read_stamp(proj, a["id"])
            assert stamp_second is not None
            assert datetime.fromisoformat(stamp_second) >= \
                datetime.fromisoformat(stamp_first), (
                    "re-dreamed claim must be re-stamped"
                )
            assert sdk._dirty_roots == set(), (
                "re-dreamed promoted claim clears dirty roots"
            )
        finally:
            sdk.close()

    def test_zero_affected_converged_run_keeps_dirty_roots(self):
        """Zero-affected retention: draft-only dirty roots + a converged run
        with ZERO affected claims (#780 draft-excluded) → roots REMAIN dirty
        and are re-dreamed after promote."""
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_d4_")
        try:
            # create_point defaults to draft since #943.
            draft = sdk.create_point("statement", "draft-only claim", dedup=False)
            assert draft["id"] in sdk._dirty_roots

            random.seed(FIXED_SEED)
            result = sdk.dream(dirty_only=True)
            assert result["converged"] is True
            assert result["affected_claims"] == [], (
                "draft-excluded run produces zero affected claims"
            )
            assert draft["id"] in sdk._dirty_roots, (
                "zero-affected converged run must NOT clear dirty roots "
                "(#780 draft-excluded retention fix)"
            )

            # After promote the claim re-enters/remains dirty and is
            # re-dreamed (draft→promote path).
            res = sdk.promote_point(draft["id"])
            assert res["promoted"] is True, res
            assert draft["id"] in sdk._dirty_roots

            random.seed(FIXED_SEED)
            result2 = sdk.dream(dirty_only=True)
            assert result2["converged"] is True
            assert draft["id"] not in sdk._dirty_roots, (
                "post-promote pass re-dreams the claim and clears its root"
            )
            assert _read_stamp(sdk._get_proj(), draft["id"]) is not None
        finally:
            sdk.close()

    def test_non_converged_run_does_not_stamp(self):
        """W4/requirement 2: a failed run (converged=False) must NOT update
        lastDreamedAt — a claim's old stamp survives a non-converged pass."""
        from tests.epic903_fixtures import f3_nonconvergent

        f = f3_nonconvergent()
        try:
            proj = f.sdk._get_proj()
            a_id, b_id = f.a_id, f.b_id
            # Pre-manufacture old stamps (fixed ISO — never wall-clock).
            for pid in (a_id, b_id):
                proj.g.query(
                    "MATCH (n:Point {id:$id}) SET n.lastDreamedAt = $ts",
                    params={"id": pid, "ts": STAMP_OLD},
                )
            random.seed(FIXED_SEED)
            result = f.sdk.dream(dirty_only=True)
            assert result["converged"] is False, "F3 must fail convergence"
            # Stamps unchanged by the failed run (retention semantics).
            assert _read_stamp(proj, a_id) == STAMP_OLD
            assert _read_stamp(proj, b_id) == STAMP_OLD
        finally:
            f.sdk.close()

    def test_full_pass_does_not_scan_stamp_when_any_chunk_fails(self):
        """P3-review gate: dream_all's convergence gate — when any chunk
        fails to converge, the operator-less scan MUST NOT stamp (stamping
        partial results would report the graph fresh while regions are not).
        Uses F3, which fails across dream(full=True) (calibrated)."""
        from tests.epic903_fixtures import f3_nonconvergent

        f = f3_nonconvergent()
        try:
            proj = f.sdk._get_proj()
            result = f.sdk.dream(full=True)
            assert result["converged_all"] is False, "F3 must fail full pass"
            # No claim may carry a stamp from the failed full pass.
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.is_operator = false AND n.lastDreamedAt IS NOT NULL "
                "RETURN n.id"
            ).result_set
            assert rows == [], f"failed full pass stamped claims: {rows}"
        finally:
            f.sdk.close()

    def test_stamp_dreamed_at_false_suppresses_stamps_both_modes(self):
        """P3-review gate: stamp_dreamed_at=False must suppress stamps in
        both local and full modes — read-triggered lazy dreams (the fast path)
        must never move the freshness signal (903-C4 scheduler input).

        F2 pre-manufactures fixed stamps on the old/medium/fresh regions
        (the null region + isolated claim have none). The assertion is that
        the stamped SET is UNCHANGED by a stamp_dreamed_at=False pass — no
        new stamps appear (null region + isolated claim stay null) and no
        existing stamp moves."""
        f = f2_staleness_regions()
        try:
            proj = f.sdk._get_proj()

            def stamped_ids():
                rows = proj.g.query(
                    "MATCH (n:Point) WHERE n.is_operator = false "
                    "AND n.lastDreamedAt IS NOT NULL RETURN n.id"
                ).result_set
                return {r[0] for r in rows}

            before = stamped_ids()
            assert before, "F2 must pre-manufacture some stamps"
            # The null region + isolated claim must be stamp-free at start.
            assert f.isolated_claim not in before

            # Local mode: a live claim with a real operator neighborhood.
            res_local = f.sdk.dream(dirty_only=True, stamp_dreamed_at=False)
            assert res_local["converged"] is True
            # Full mode (includes the operator-less scan path).
            res_full = f.sdk.dream(full=True, stamp_dreamed_at=False)
            assert res_full["converged_all"] is True

            after = stamped_ids()
            assert after == before, (
                f"stamp_dreamed_at=False changed the stamped set: "
                f"added={after - before} removed={before - after}"
            )
            # The null region + isolated claim stayed null.
            assert f.isolated_claim not in after
        finally:
            f.sdk.close()


# ── Index idempotency at init (replay-safe, AOF) ──────────────────────


class TestLastDreamedAtIndex:
    def test_index_idempotent_at_init_embedded(self):
        """Surface 5: index creation at init is idempotent + replay-safe. On
        embedded (hermetic harness) the plain :Point(lastDreamedAt) index is
        created (the is_operator composite is #522-unsafe on redislite and is
        non-embedded-only). Reopening the same DB (AOF replay context) must
        not crash and must keep the index; the #522 load-bearing is_operator
        sweep must still return the full set."""
        import tempfile
        db_path = os.path.join(tempfile.mkdtemp(prefix="tt_dreamidx_"), "t.db")
        sdk1 = TortoiseSDK(db_path)
        try:
            proj1 = sdk1._get_proj()
            a = _make_claim(sdk1, "idx-a")
            b = _make_claim(sdk1, "idx-b")
            sdk1.create_operator("IMPL", a["id"], [b["id"]], label="op1")
            # The embedded index exists after init.
            rows = proj1.g.query("CALL db.indexes()").result_set
            point_idx = {
                r[0]: r[1] for r in rows if r[0] == "Point"
            }
            assert "lastDreamedAt" in str(point_idx), (
                f"embedded must create :Point(lastDreamedAt) index: "
                f"{point_idx}"
            )
        finally:
            sdk1.close()

        # Reopen (AOF-replay equivalent) → idempotent init, no crash, index
        # still present, and the #522 `= false` sweep returns the full set.
        sdk2 = TortoiseSDK(db_path)
        try:
            proj2 = sdk2._get_proj()
            rows = proj2.g.query("CALL db.indexes()").result_set
            point_idx = {r[0]: r[1] for r in rows if r[0] == "Point"}
            assert "lastDreamedAt" in str(point_idx), (
                f"index must survive reopen: {point_idx}"
            )
            assert proj2.g.query(
                "MATCH (n:Point) WHERE n.is_operator = false "
                "RETURN count(n)"
            ).result_set[0][0] == 2, (
                "#522 load-bearing form must return the full non-operator set"
            )
            # Second explicit _ensure_indexes is a no-op (idempotent).
            proj2._ensure_indexes()
        finally:
            sdk2.close()

    def test_full_pass_scanned_count_key_present(self):
        """The full-mode return exposes scanned_count (I1 full key-set
        additive; the full return-shape assertion is 903-C6's)."""
        f = f2_staleness_regions()
        try:
            random.seed(FIXED_SEED)
            result = f.sdk.dream(full=True)
            assert "scanned_count" in result
            assert result["scanned_count"] >= 0
        finally:
            f.sdk.close()

    def test_composite_index_created_non_embedded(self):
        """P2-5: the composite :Point(is_operator, lastDreamedAt) is created
        on docker/server FalkorDB (the #522-safe path). Docker-gated — the
        embedded hermetic runner creates the plain index instead (see
        test_index_idempotent_at_init_embedded). Mirrors the test_indexes.py
        probe pattern."""
        from tests.test_indexes import FALKORDB_AVAILABLE, _current_uri  # noqa: I001
        from urllib.parse import urlparse

        if not FALKORDB_AVAILABLE:
            pytest.skip("no live non-embedded FalkorDB available")
        uri = _current_uri()
        if not urlparse(uri).path.lstrip("/").startswith(
                ("test_", "tortoise_test_")):
            pytest.skip(f"resolved URI {uri!r} is not a test graph "
                        "(graph name must start with 'test_')")
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection.from_uri(uri)
        try:
            proj.g.query("MATCH (n) DETACH DELETE n")
            proj._ensure_indexes()
            rows = proj.g.query("CALL db.indexes()").result_set
            composite = [
                r for r in rows
                if r[0] == "Point"
                and "is_operator" in str(r[1])
                and "lastDreamedAt" in str(r[1])
            ]
            assert composite, (
                f"non-embedded must create :Point(is_operator, lastDreamedAt) "
                f"composite index, got {rows}"
            )
        finally:
            proj.close()
