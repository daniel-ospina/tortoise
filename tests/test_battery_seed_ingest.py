"""#2291 I-2 evented seed channel — ingest-lane acceptance tests.

Covers the plan's Task-2 acceptance: verify-at-scope calibration ordering
(no require_calibration=False anywhere; promote-then-compute satisfies the
fail-closed gate on the embedded lane), promote semantics (seed POINTS
promoted, operators ride, direct operator promotion blocked), the
ingest-lane warm guard firing BEFORE a batch_id is minted, and RAW-vs-SDK
lane equivalence (same content contract, no ¬A pre-k on either lane).
"""
from __future__ import annotations

import os

import pytest

from battery.testing import seeds
from battery.testing.seeds import setup_seed_mode, setup_seed_mode_raw

_CT = "ct-001"


@pytest.fixture(autouse=True, scope="module")
def _force_embedded_lane() -> None:
    """Hermetic per-run store tests materialize scenario graphs as named
    (battery_ct-001 …) — a TORTOISE_DB_URI redirect folds graphs per test
    and voids the assertions. Force the embedded lane for this module
    (embedded-file-contract; precedent: test_embedded_lifecycle)."""
    saved = os.environ.get("TORTOISE_DB_URI")
    saved_path = os.environ.get("TORTOISE_DB_PATH")
    os.environ.pop("TORTOISE_DB_URI", None)
    os.environ.pop("TORTOISE_DB_PATH", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["TORTOISE_DB_URI"] = saved
        if saved_path is not None:
            os.environ["TORTOISE_DB_PATH"] = saved_path


def _scenario(store) -> object:
    return store._scenario


def test_no_require_calibration_false_anywhere() -> None:
    """Audit: the harness never opts out of the calibration gate — the ban
    binds battery/, never the product's own seams."""
    from pathlib import Path as P
    root = P(__file__).resolve().parent.parent / "battery"
    offenders: list[str] = []
    for py in sorted(root.rglob("*.py")):
        src = py.read_text()
        # whitespace-agnostic: catches `= False`, ` =False`, split lines
        if __import__("re").search(r"require_calibration\s*=\s*False", src):
            offenders.append(str(py))
    assert not offenders, offenders


def test_verify_at_scope_promote_then_compute_calibrated(tmp_path) -> None:
    """E2E-5-style verify-at-scope: after the ingest-lane seed (credibility
    baselines) + explicit point promotion, compute_confidence's fail-closed
    gate PASSES on the embedded lane (calibrated summary — no live
    statement/evidence is uncalibrated). A pristine seed_mode store has NO
    operator edges (ct-* seeds claim_a + evidence only) ⇒ the honest EP
    read is an empty affected set (never decisive — the #2291 decisive-rule
    probe). After ONE closed-set agent write wires an operator, the same
    compute returns calibrated per-claim confidence + variance. A gate
    refuse WITH the baseline props in place would be a product gap → file,
    never flag-flip.
    """
    from battery.arms.base import AgentContext, Memory
    from tortoise.exceptions import CalibrationError
    store = setup_seed_mode(tmp_path, _CT)
    try:
        sdk = store._arm._sdk(store._scenario)
        mems = store.retrieve("")
        anchors = [m.id for m in mems if m.kind == "claim"][:2]
        assert anchors, "no claim anchors on the ingest-lane store"
        try:
            pristine = sdk.compute_confidence(factors=None, anchors=anchors)
        except CalibrationError as e:  # product gap → fail loudly, not flip
            pytest.fail(f"verify-at-scope: gate refused a calibrated store: {e}")
        assert not (pristine.get("confidences") or {}), (
            "pristine seed_mode store must have NO affected set (no operator "
            f"edges yet); got diagnostic={pristine.get('diagnostic')}")
        # Calibrated store (no live uncalibrated evidence):
        summary = sdk.calibrate_summary()
        uncal = [s for s in summary if not s.get("calibrated")
                 and s.get("pointKind") in ("statement", "evidence")
                 and s.get("status") != "draft"]
        assert not uncal, uncal
        # One closed-set agent write wires an operator → EP now propagates.
        store._arm.record(
            AgentContext(scenario=store._scenario, episode_seed=0,
                         user_message="go", prior_memories=tuple(mems)),
            Memory(id="e1", content="finding", kind="nand"))
        wired = sdk.compute_confidence(factors=None, anchors=anchors[:1])
        conf = wired.get("confidences") or {}
        assert conf, (f"wired store produced no confidences: "
                      f"{wired.get('diagnostic')}")
        cid = anchors[0]
        assert cid in conf, f"anchor {cid} missing from confidences"
        entry = conf[cid]
        assert entry.get("variance") is not None
        mean = entry.get("mean")
        assert mean is not None and 0.0 < float(mean) < 1.0
    finally:
        store.close()


def test_promote_semantics_seed_points_and_operators(tmp_path) -> None:
    """Seed POINTS are promoted (live, reviewed); a direct operator
    promotion is product-blocked {blocked: true, reason: is_operator}."""
    store = setup_seed_mode(tmp_path, _CT)
    try:
        sdk = store._arm._sdk(store._scenario)
        mems = store.retrieve("")
        assert mems
        # All retrievable claim points are LIVE (promoted from the seed).
        for m in mems:
            pt = sdk.get_point(m.id)
            assert pt.get("status") == "live", pt.get("status")
        # Build one real operator and assert direct promotion is blocked
        # (operator promotion is a product-level refusal, not a silent skip).
        if len(mems) < 2:
            pytest.skip("ct-001 seed must surface ≥2 memories for an operator")
        op = sdk.create_operator("IMPL", mems[0].id, [mems[1].id])
        oid = op.get("id")
        pr = sdk.promote_point(oid)
        assert pr.get("blocked") is True
        assert pr.get("reason") == "is_operator"
    finally:
        store.close()


def test_warm_guard_refuses_stale_pre_fix_before_batch(tmp_path) -> None:
    """Ingest-lane warm guard: a stale PRE-FIX legacy graph in the same
    namespace refuses with ConfigError (never silently retains ¬A), and the
    refusal happens at seed time (no new batch mints on the refuse path)."""
    from battery.exceptions import ConfigError
    ns = tmp_path / "warm"
    seeds.seed_full_legacy(ns, _CT)  # PRE-FIX full graph (claim_b pre-seeded)
    with pytest.raises(ConfigError, match="warm guard"):
        setup_seed_mode(ns, _CT, purge=False)  # observe → refuse


def test_lane_equivalence_raw_vs_sdk(tmp_path) -> None:
    """The RAW reference lane (projection+batch_setup) and the SDK ingest
    lane expose equivalent retrievable seed content: claim_a + evidence
    present, ¬A content absent pre-k, marker present — under the same
    seed_mode content contract (modulo server-minted ids + event streams)."""
    raw = setup_seed_mode_raw(tmp_path / "raw", _CT)
    sdk_lane = setup_seed_mode(tmp_path / "sdk", _CT)
    try:
        ra = raw.surface_text()
        sa = sdk_lane.surface_text()
        pair = _scenario(sdk_lane).contradiction_pairs[0]
        assert pair.claim_a[:40] in ra and pair.claim_a[:40] in sa
        assert pair.claim_b[:40] not in ra and pair.claim_b[:40] not in sa
        assert raw.find_content("seed_mode:v1") and sdk_lane.find_content(
            "seed_mode:v1")
    finally:
        raw.close()
        sdk_lane.close()


def test_seed_props_present_on_ingest_lane(tmp_path) -> None:
    """Seed points carry the additive provenance props (seed, source_harness,
    source_session) — the seeded-vs-agent tag seam (Task 5)."""
    store = setup_seed_mode(tmp_path, _CT)
    try:
        sdk = store._arm._sdk(store._scenario)
        mems = store.retrieve("")
        assert mems
        pt = sdk.get_point(mems[0].id)
        assert pt.get("seed") is True
        assert pt.get("source_harness") == "battery"
        assert pt.get("source_session") == _CT
    finally:
        store.close()
