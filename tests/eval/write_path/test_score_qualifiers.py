"""The decision rule, tested without a run.

`evaluate_branches` is the only place the pre-registered §7 rule is applied, so
these tests pin the two ways it can be wrong that a diff would not show: an
inequality decided by floating-point representation, and a branch certified on
an extract the extractor itself marked incomplete.

No database, no fixtures, no model — pure arithmetic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.eval.write_path import score_qualifiers as sq
from tests.eval.write_path.score_qualifiers import (
    BAND_PP,
    HOLDOUT_SESSIONS,
    PR_TOLERANCE_PP,
    TUNING_SESSIONS,
    check_protocol_pins,
    evaluate_branches,
    verdict_label,
)

CLEAN: list[str] = []


def test_exactly_on_the_band_does_not_fire_branch_1():
    """+10.0 pp is the branch-2 ceiling, NOT one step past the branch-1 trigger.

    The registration says branch 1 fires when Δ > +10 pp and branch 2 applies
    when |Δ| <= 10 pp. Computed in floats, the committed holdout is
    `100 * (7/10 - 6/10)` = 9.999999999999998 — so a raw `> 10.0` test and a raw
    `<= 10.0` test can disagree about the SAME cell depending on summation order.
    """
    verdict = evaluate_branches(100 * (7 / 10 - 6 / 10), 0.8, 3.0, contaminated=CLEAN)
    assert verdict["branch_1_fires"] is False
    assert verdict["branch_2_applies"] is True
    assert verdict["branch_2_certified"] is True


def test_both_boundaries_are_inclusive_and_symmetric():
    assert evaluate_branches(BAND_PP, 0.0, 0.0, contaminated=CLEAN)["branch_2_applies"]
    assert evaluate_branches(-BAND_PP, 0.0, 0.0, contaminated=CLEAN)["branch_2_applies"]
    assert evaluate_branches(PR_TOLERANCE_PP, 0.0, 0.0, contaminated=CLEAN)["branch_2_applies"]


def test_representation_noise_above_the_band_does_not_fire_branch_1():
    """10.000000000000002 pp is the band, not past it — the guard must be tested.

    `round(v, 9)` is what makes the boundary reproducible; without it this cell
    fires branch 1 (mutation-verified: dropping the rounding reddens this test).
    """
    verdict = evaluate_branches(10.000000000000002, 0.0, 0.0, contaminated=CLEAN)
    assert verdict["branch_1_fires"] is False
    assert verdict["branch_2_applies"] is True


def test_one_ulp_past_the_band_still_fires_branch_1():
    verdict = evaluate_branches(BAND_PP + 1e-6, 0.0, 0.0, contaminated=CLEAN)
    assert verdict["branch_1_fires"] is True
    assert verdict["branch_2_applies"] is False
    assert verdict["branch_2_certified"] is False


@pytest.mark.parametrize("prec,rec", [(-PR_TOLERANCE_PP - 0.1, 0.0),
                                      (0.0, -PR_TOLERANCE_PP - 0.1), (-99.0, 99.0)])
def test_a_precision_or_recall_drop_fires_branch_1_even_at_parity_loss(prec, rec):
    verdict = evaluate_branches(0.0, prec, rec, contaminated=CLEAN)
    assert verdict["branch_1_fires"] is True
    assert verdict["branch_2_applies"] is False


@pytest.mark.parametrize("prec,rec", [(-PR_TOLERANCE_PP, 0.0), (0.0, -PR_TOLERANCE_PP)])
def test_a_drop_exactly_at_the_tolerance_is_inside_the_band(prec, rec):
    """"> 5 pp" is strict: a −5.0 pp drop is the ceiling of acceptable, not past it."""
    verdict = evaluate_branches(0.0, prec, rec, contaminated=CLEAN)
    assert verdict["branch_1_fires"] is False
    assert verdict["branch_2_applies"] is True


def test_the_committed_run_is_provisional_because_its_receipt_is_incomplete():
    """The real cell: met the band, but `wp07` raw is `partial` (3 of 6 raw losses).

    Certifying here would record "D3 settles" off an extract whose tail the
    extractor dropped — and one qualifier in that cell crosses the band.
    """
    verdict = evaluate_branches(10.0, 0.8, 3.0,
                               contaminated=["wp07_bluepeak_followup:raw"])
    assert verdict["branch_1_fires"] is False
    assert verdict["branch_2_applies"] is True
    assert verdict["branch_2_certified"] is False, "an incomplete receipt must not certify"
    assert verdict["branch_3_stays_provisional"] is True


def test_contamination_does_not_rescue_the_falsifier_either():
    """A partial receipt cannot be used to *argue against* the arm either."""
    verdict = evaluate_branches(20.0, 0.0, 0.0, contaminated=["wp07_bluepeak_followup:raw"])
    assert verdict["branch_1_fires"] is True
    assert verdict["branch_2_applies"] is False


def test_missing_deltas_never_certify():
    assert evaluate_branches(None, None, None, contaminated=CLEAN)["branch_2_applies"] is False
    assert evaluate_branches(None, None, None, contaminated=CLEAN)["branch_3_stays_provisional"]


def test_a_missing_precision_or_recall_delta_is_not_inside_the_tolerance():
    """`d is None` used to satisfy the tolerance, so an ABSENT delta certified a branch.

    Both arms emitting ZERO claims gives precision `hits / 0` — None — and recall None
    with it, while Δloss is 0 because every qualifier is `absent` on both sides. So
    `evaluate_branches(0.0, None, None)` returned `branch_2_certified=True` and
    `verdict_label` recorded D3 as **SETTLED** off two empty extracts — the exact
    failure `certified = parity and not contaminated` exists to prevent, reached
    without tripping any contamination flag (`partial`/`truncated`/`s2_error` are all
    clean: the output is EMPTY, not incomplete).

    `test_missing_deltas_never_certify` passed `(None, None, None)`, which the
    `loss is not None` leg alone rejects, so the cell that matters — a PRESENT loss
    beside a MISSING P/R delta — had no test at all, and the fail-open survived
    five review cycles. The three tuples here cover the whole reachable shape.
    """
    for prec, rec in ((None, None), (None, 0.0), (0.0, None)):
        verdict = evaluate_branches(0.0, prec, rec, contaminated=CLEAN)
        assert verdict["branch_2_applies"] is False, (prec, rec)
        assert verdict["branch_2_certified"] is False, (prec, rec)
        assert verdict["scoring_input_incomplete"] is True, (prec, rec)
        label = verdict_label(verdict)
        assert label == "no branch matched — scoring input incomplete", label
        assert not label.startswith("settled"), label


def test_a_complete_parity_is_still_certified():
    """Control: the presence guard must reject MISSING deltas, not all deltas."""
    verdict = evaluate_branches(0.0, 0.0, 0.0, contaminated=CLEAN)
    assert verdict["scoring_input_incomplete"] is False
    assert verdict["branch_2_certified"] is True
    assert verdict_label(verdict) == "settled (branch 2 certified)"


# ── the recorded verdict string: ONE route per case ──────────────────────────────

def test_verdict_label_distinguishes_uncertifiable_parity_from_branch_3():
    """The two land on the same OUTCOME (provisional) by different ROUTES.

    A single `branch_3` flag cannot tell them apart, and collapsing them records a
    route the flags contradict — run 1 reaches branch 3 (branch 2 never applied),
    run 2 meets branch 2 and cannot certify it. The artifact a reader audits must
    say which.
    """
    run_2 = evaluate_branches(10.0, 0.8, 3.0, contaminated=["wp07_bluepeak_followup:raw"])
    assert run_2["branch_2_applies"] is True
    assert "branch 3 not reached" in verdict_label(run_2)

    run_1 = evaluate_branches(-60.0, -0.8, 24.2, contaminated=CLEAN)
    assert run_1["branch_2_applies"] is False
    assert "branch 3" in verdict_label(run_1)
    assert "not reached" not in verdict_label(run_1), "branch 3 WAS reached here"


def test_verdict_label_names_the_fire_and_the_settlement():
    assert verdict_label(evaluate_branches(20.0, 0.0, 0.0, contaminated=CLEAN)).startswith(
        "the falsifier FIRES")
    assert verdict_label(evaluate_branches(2.0, 0.0, 0.0, contaminated=CLEAN)).startswith(
        "settled (branch 2 certified)")


def test_the_committed_run2_routing_is_recorded_on_the_artifact() -> None:
    """End-to-end: the committed score file's verdict must be the run-2 route."""
    out = Path(__file__).parent / "results" / "7947-score-replication-2026-10-18.json"
    verdict = json.loads(out.read_text())["decision_input"]
    assert verdict["branch_2_applies"] is True
    assert verdict["branch_2_certified"] is False
    assert verdict["contaminated_receipts"] == ["wp07_bluepeak_followup:raw"]
    assert verdict["verdict"] == (
        "provisional (branch 2 met, not certifiable; branch 3 not reached)")


def test_a_suppressed_fire_is_recorded_as_suppressed_not_as_branch_3():
    """The suppression must not be laundered into "branch 3 applies".

    Branch 1's condition was MET; it was withheld because the arm that met it is
    incomplete. Recording that as branch 3 would assert a route the rule did not
    take — the same defect the route table exists to prevent.
    """
    verdict = evaluate_branches(20.0, 0.0, 0.0, contaminated=["wp06_quarry_rollout:narrative"])
    label = verdict_label(verdict)
    assert "SUPPRESSED" in label
    assert "branch 3" not in label


# ── a broken NARRATIVE arm must not be able to manufacture the falsifier ──────

def test_a_partial_narrative_receipt_cannot_fire_branch_1():
    """Truncation removes claims, so an incomplete narrative arm OVERSTATES Δ.

    Firing branch 1 off it would publish "the narrative loses qualifiers" on the
    strength of an extract the extractor itself calls incomplete — the mirror image
    of the raw-arm case, where truncation UNDERSTATES Δ and a fire is therefore
    conservative.
    """
    verdict = evaluate_branches(20.0, 0.0, 0.0,
                               contaminated=["wp06_quarry_rollout:narrative"])
    assert verdict["branch_1_fires"] is False
    assert verdict["falsifier_suppressed_by_contamination"] is True
    assert verdict["branch_3_stays_provisional"] is True
    # ...and the same delta on a CLEAN run does fire.
    assert evaluate_branches(20.0, 0.0, 0.0, contaminated=CLEAN)["branch_1_fires"] is True


def test_a_partial_raw_receipt_cannot_fire_branch_1_off_the_precision_leg():
    """The loss leg's asymmetry does NOT carry over to the P/R legs.

    A dirty RAW arm is conservative for Δloss: a dropped tail only removes
    opportunities to count a loss. It is the OPPOSITE for precision, which is a
    ratio — dropping a tail of unmatched claims RAISES raw precision, which lowers
    Δprecision = prec_narr − prec_raw and can fire branch 1 off an extract the
    extractor itself marked incomplete. Precision on this run is exactly that
    shape: raw 78/119 = 65.5%, and dropping 10 unmatched tail claims gives
    78/109 = 71.6%, i.e. Δprecision = 63.1 − 71.6 = −8.5 pp → branch 1.

    `wp07_bluepeak_followup:raw` is `partial=True` in the committed holdout, so the
    dirty receipt and the fire channel are the same receipt.
    """
    dirty_raw = ["wp07_bluepeak_followup:raw"]
    for prec, rec in ((-15.0, 0.0), (0.0, -15.0)):
        verdict = evaluate_branches(2.0, prec, rec, contaminated=dirty_raw)
        assert verdict["branch_1_fires"] is False, (
            "a P/R-borne fire was published off an incomplete raw extract")
        assert verdict["falsifier_suppressed_by_contamination"] is True
        assert verdict["falsifier_suppressed_by"] == "raw"
        label = verdict_label(verdict)
        assert "SUPPRESSED" in label
        assert "raw receipt" in label, (
            "the label must name the arm that withheld the fire — the raw tail "
            f"inflates precision, not the delta: {label}")
        # ... and the same deltas on a CLEAN run do fire.
        assert evaluate_branches(2.0, prec, rec, contaminated=CLEAN)["branch_1_fires"] is True

    # The LOSS leg keeps the one-sided rule: a dirty raw arm does not withhold a
    # Δloss-borne fire, because there it can only under-detect.
    loss_fire = evaluate_branches(20.0, 0.0, 0.0, contaminated=dirty_raw)
    assert loss_fire["branch_1_fires"] is True
    assert loss_fire["falsifier_suppressed_by_contamination"] is False

    # ⛔ BOTH legs met, clean narrative, dirty raw: the Δloss fire is GENUINE and
    # must publish — the flag describes a fire that did NOT happen, so it must be
    # False here even though the P/R leg was withheld. Otherwise the label says
    # "SUPPRESSED … not a finding" under a console line that just printed "FIRES",
    # and `branch_1_fires: true` is persisted beside a suppressed verdict.
    both = evaluate_branches(20.0, -15.0, 0.0, contaminated=dirty_raw)
    assert both["branch_1_fires"] is True, "a conservative Δloss fire was discarded"
    assert both["falsifier_suppressed_by_contamination"] is False, (
        "the suppression flag is not disjoint from the fire")
    assert both["withheld_legs"] == ["precision_recall"], both["withheld_legs"]
    label = verdict_label(both)
    assert label.startswith("the falsifier FIRES"), label
    assert "SUPPRESSED" not in label
    log = "\n".join(sq.suppression_log(both))
    assert "WAS SUPPRESSED" not in log, log
    assert "withheld" in log and "the fire stands" in log, log


def test_the_console_printer_names_the_arm_that_suppressed_the_fire():
    """The LOG must agree with `verdict_label`, not just the returned dict.

    The printer is what an operator reads before the label, and it carried ONE
    sentence ("the NARRATIVE receipt is incomplete — a dropped tail removes claims
    and so OVERSTATES the delta") for both arms. Under the P/R rule the raw arm can
    be the suppressor, and there the mechanism is the opposite: a dropped tail of
    unmatched claims RAISES raw precision, which LOWERS Δprecision. A log that
    names the wrong mechanism is the same defect one consumer over — so this pins
    the text against the arm, in both directions.
    """
    for delta_kwargs, arm, expect, forbid in (
        ((2.0, -8.5, 0.0), "raw", ("RAW receipt", "precision"), ("NARRATIVE receipt",)),
        ((20.0, 0.0, 0.0), "narrative", ("NARRATIVE receipt", "OVERSTATES the delta"),
         ("RAW receipt", "precision")),
    ):
        contaminated = [f"wp07_bluepeak_followup:{arm}"]
        verdict = evaluate_branches(*delta_kwargs, contaminated=contaminated)
        assert verdict["falsifier_suppressed_by"] == arm
        out = "\n".join(sq.suppression_log(verdict))
        for needle in expect:
            assert needle in out, f"{arm} suppression log is missing {needle!r}:\n{out}"
        for needle in forbid:
            assert needle not in out, (
                f"the {arm} suppression log names the wrong arm ({needle!r}):\n{out}")
    # No suppression, no lines: the log must not invent a cause on a clean run.
    assert sq.suppression_log(evaluate_branches(2.0, 0.0, 0.0, contaminated=CLEAN)) == []


def test_a_dirty_narrative_receipt_is_not_called_a_suppressed_fire_when_the_condition_failed():
    """Suppression needs the CONDITION, not merely a dirty narrative receipt.

    A narrative receipt being incomplete says nothing about whether branch 1's
    condition was met. At Δ = +2.0 pp — plainly inside the ±10 pp band — the
    condition is NOT met, so recording ``falsifier_suppressed_by_contamination``
    and labelling the cell "branch 1's condition was MET but SUPPRESSED" asserts a
    fact about the rule that the rule's own inputs contradict, and it overwrites
    the route that IS available (branch 2 met, not certifiable). Direction alone
    cannot distinguish the two; the flag must gate on the condition itself.
    """
    dirty = ["wp06_quarry_rollout:narrative"]
    verdict = evaluate_branches(2.0, 0.0, 0.0, contaminated=dirty)
    assert verdict["branch_1_fires"] is False
    assert verdict["branch_2_applies"] is True
    assert verdict["branch_2_certified"] is False
    assert verdict["falsifier_suppressed_by_contamination"] is False
    label = verdict_label(verdict)
    assert label == "provisional (branch 2 met, not certifiable; branch 3 not reached)"
    assert "SUPPRESSED" not in label
    # The dirty receipt is still recorded — the run stays auditable.
    assert verdict["contaminated_receipts"] == dirty


def test_suppression_still_fires_when_the_precision_leg_is_what_met_the_condition():
    """The condition is a disjunction; the fix must not narrow it to Δloss only.

    Branch 1 fires on ``Δloss > +10 pp`` OR a precision/recall drop > 5 pp. A
    condition-aware suppression gate that only looked at ``loss`` would silently
    stop reporting a precision-borne fire that a dirty narrative arm withheld.
    """
    dirty = ["wp06_quarry_rollout:narrative"]
    for prec, rec in ((-15.0, 0.0), (0.0, -15.0)):
        verdict = evaluate_branches(2.0, prec, rec, contaminated=dirty)
        assert verdict["branch_1_fires"] is False
        assert verdict["falsifier_suppressed_by_contamination"] is True
        assert "SUPPRESSED" in verdict_label(verdict)


# ── the protocol-pins guard ───────────────────────────────────────────────

SESSIONS = (*TUNING_SESSIONS, *HOLDOUT_SESSIONS)


def _write_receipts(tmp_path, **overrides):
    base = {"model_id": "deepseek/deepseek-v4-flash", "chunk_size": 50,
            "session_date": "2026-10-10", "env_pins": {}, "embed_list": {}, "s2_stats": {}}
    for sid in SESSIONS:
        rec = {**base, **{k: (v[sid] if isinstance(v, dict) else v)
                          for k, v in overrides.items()}}
        (tmp_path / f"{sid}.arms.json").write_text(json.dumps(rec))
    return tmp_path


def test_consistent_receipts_pass_the_protocol_guard(tmp_path):
    assert check_protocol_pins(_write_receipts(tmp_path), SESSIONS) == []


def test_a_different_model_in_one_receipt_is_refused(tmp_path):
    d = _write_receipts(tmp_path, model_id={s: "deepseek/deepseek-v4-flash" for s in SESSIONS}
                        | {"wp05_retro_writeup": "other/model"})
    errors = check_protocol_pins(d, SESSIONS)
    assert errors and "model_id" in errors[0]


def test_a_different_environment_pin_is_refused(tmp_path):
    """The decisive pins: `TORTOISE_LABEL_ORDER=shuffle` reseeds the label order FROM
    THE STORY, so it differs between arms at identical prompt length — exactly the
    difference §10's token arithmetic is blind to. A guard that ignores it would
    score a shuffle run as if it were this one."""
    d = _write_receipts(tmp_path, env_pins={s: {} for s in SESSIONS}
                        | {"wp03_ember_design": {"TORTOISE_LABEL_ORDER": "shuffle"}})
    errors = check_protocol_pins(d, SESSIONS)
    assert errors and "ENVIRONMENT" in errors[0]
    assert "TORTOISE_LABEL_ORDER" in errors[0]


def test_unset_environment_pins_are_consistent_with_each_other(tmp_path):
    """The 7 committed receipts carry NO `env_pins` — absence is not a disagreement."""
    assert check_protocol_pins(_write_receipts(tmp_path, env_pins={s: {} for s in SESSIONS}),
                               SESSIONS) == []


# ── the reproducibility claim, pinned ──────────────────────────────────────

WRITE_PATH = Path(__file__).parent
COMMITTED_SCORE = WRITE_PATH / "results" / "7947-score-replication-2026-10-18.json"
COMMITTED_ARMS = WRITE_PATH / "receipts" / "7947-arms-2026-10-18"


def test_the_committed_score_artifact_regenerates_byte_identically(tmp_path, capsys):
    """The PR body and §10 both publish this command as the reproducer.

    Nothing else in CI ran it. §10's decisive numbers — holdout Δloss +10.0 pp,
    Δprecision +0.8, Δrecall +3.0, the two-tenths-of-a-qualifier band, the
    recorded route string — are computed by `main()`, so without this test an
    edit to `MARKERS`, to the 0.6 claim-match threshold or to `classify()`
    changes every published figure while every other gate stays green. That is
    the exact failure mode the corpus-count pin exists for (a generator edit
    re-renders the gold, keeps the gates green, and silently falsifies the
    write-up), left open on the half that CONSUMES the gold.

    Hermetic: the scorer is stdlib-only (`json`/`re`/`argparse`/`pathlib`), and
    both inputs are the committed receipts and gold. No DB, network or model.
    """
    out = tmp_path / "score.json"
    rc = sq.main(["--sessions", *SESSIONS,
                  "--arms-dir", str(COMMITTED_ARMS),
                  "--out", str(out)])
    capsys.readouterr()  # the console table is not the artifact
    assert rc == 0
    assert out.read_bytes() == COMMITTED_SCORE.read_bytes(), (
        "a fresh score over the committed receipts no longer reproduces "
        f"{COMMITTED_SCORE.name} — every number §10 publishes was derived from "
        "this file, so either the scorer changed the measurement (re-run the "
        "registered protocol and update §10), or the committed artifact was "
        "hand-edited")


# ── the lexical instrument's error is two-sided (and used to claim otherwise) ─

def test_the_marker_match_over_credits_a_mid_word_hit():
    """The registered instrument's over-credit is real, and must stay documented.

    `marker_present` is an UNBOUNDED SUBSTRING test over a table that mixes
    space-suffixed markers with bare stems, so `"commonly"` satisfies scope's
    `"only"` and a FLATTENED cell reads as `preserved` — an error that
    UNDERSTATES loss, the opposite direction from the one the module docstring
    and the emitted `instrument.note` claimed until this change.

    Word-bounding the table would remove the class, but the table is the
    REGISTERED instrument: doing it silently would make the two runs and any
    future run non-comparable, so it belongs in a newly registered run. This
    test therefore pins the CURRENT behaviour AND the two-sided description; if
    someone word-bounds the match, this test must be updated together with a new
    registration, which is the point of pinning it rather than leaving prose.
    """
    unit = {"u1": {"id": "u1", "verbatim_anchor": "the deploy runs on the staging cluster"}}
    qualifier = {"id": "q1", "class": "scope", "bound_claim": "u1",
                 "verbatim_qualifier": "only on Tuesdays"}
    plain = "the deploy runs on the staging cluster"
    assert sq.classify(qualifier, unit, [plain])["outcome"] == "flattened"
    hit = sq.classify(qualifier, unit, [f"{plain} commonly"])["outcome"]
    assert hit == "preserved", (
        "the mid-word over-credit the module now documents has been removed — "
        "that is a REGISTERED-instrument change: re-run the protocol and update "
        "§10 rather than editing this expectation")


# ── the S1 half of the contamination guard ──────────────────────────────

def _receipts_copy(tmp_path):
    import shutil

    dst = tmp_path / "arms"
    shutil.copytree(COMMITTED_ARMS, dst)
    return dst


def _score_with(arms_dir, out):
    return sq.main(["--sessions", *SESSIONS, "--arms-dir", str(arms_dir),
                    "--out", str(out)])


def test_a_failed_s1_chunk_contaminates_the_narrative_arm_only(tmp_path, capsys):
    """The narrative arm's INPUT is built by S1, and nothing read `errors`.

    `build_inputs()` swallows a per-chunk S1 failure into a top-level `errors`
    list and carries on, so the narrative story is missing a chunk — a shortened
    story can drop claims (raising narrative loss, i.e. MANUFACTURING a branch-1
    fire) or move the cell across the band, while `branch_2_certified` stayed
    true. That list sat in the receipt, read by nothing: the same "the flag was
    in the receipt, unread" defect this measurement had just fixed for S2, one
    stage earlier, and it contradicted the artifact's own `certification_note`
    ("a branch is certified only on clean receipts").

    `raw` must NOT be contaminated by it: the raw arm's input is the transcript,
    which S1 never touches, and refusing the raw arm there would let a broken
    narrative hide a real fire.
    """
    arms = _receipts_copy(tmp_path)
    receipt = arms / "wp06_quarry_rollout.arms.json"
    rec = json.loads(receipt.read_text())
    rec["errors"] = ["S1 chunk failed: RuntimeError: boom"]
    receipt.write_text(json.dumps(rec))

    out = tmp_path / "score.json"
    assert _score_with(arms, out) == 0
    capsys.readouterr()
    cont = json.loads(out.read_text())["decision_input"]["contaminated_receipts"]
    assert "wp06_quarry_rollout:narrative" in cont, cont
    assert "wp06_quarry_rollout:raw" not in cont, (
        "an S1 failure cannot contaminate the raw arm — its input never goes "
        f"through S1; got {cont}")


def test_a_length_capped_s1_summary_contaminates_the_narrative_arm(tmp_path, capsys):
    """S1 truncation is a silent tail-drop (`finish_reason == "length"`), never a raise.

    `run_s1` was called WITHOUT `stats`, so this documented path left no trace in
    the receipt at all. The harness now passes `stats` and records `s1_stats`.
    """
    arms = _receipts_copy(tmp_path)
    receipt = arms / "wp06_quarry_rollout.arms.json"
    rec = json.loads(receipt.read_text())
    rec["s1_stats"] = [{"ok": True}, {"ok": True, "finish_reason": "length"}]
    receipt.write_text(json.dumps(rec))

    out = tmp_path / "score.json"
    assert _score_with(arms, out) == 0
    capsys.readouterr()
    cont = json.loads(out.read_text())["decision_input"]["contaminated_receipts"]
    assert "wp06_quarry_rollout:narrative" in cont, cont
    assert "wp06_quarry_rollout:raw" not in cont, cont


def test_the_clean_committed_receipts_carry_no_s1_flag():
    """The committed receipts carry `errors: []` — the S1 guard must not fire on them."""
    rec = json.loads((COMMITTED_ARMS / "wp05_retro_writeup.arms.json").read_text())
    flags = sq.score_session("wp05_retro_writeup", "narrative", COMMITTED_ARMS)["receipt_flags"]
    assert rec["errors"] == []
    assert flags["s1_error"] is False
    assert flags["s1_truncated"] is False


def test_every_committed_preserved_cell_rests_on_a_word_bounded_marker():
    """The over-credit is LATENT on the two committed runs — pinned, not asserted.

    Recomputed from the committed receipts + gold (never from a console claim):
    for every qualifier whose claim survived extraction, either a WORD-BOUNDED
    marker is present in the matched claim text (genuine preservation), or no
    marker is present at all (`flattened`). No cell in either run is carried by
    a mid-word hit, which is what lets §10 say the error is real but did not move
    a number. An edit to the gold, the receipts or `MARKERS` that introduces one
    reds this instead of silently revising the write-up.
    """
    checked = 0
    for sid in SESSIONS:
        gold = json.loads((WRITE_PATH / "gold" / f"{sid}.gold.json").read_text())
        units = {u["id"]: u for u in gold.get("planted_units", [])}
        arms = json.loads((COMMITTED_ARMS / f"{sid}.arms.json").read_text())
        for arm in sq.ARMS:
            claims = sq.render_claims(arms["embed_list"].get(arm) or {})
            for q in gold.get("planted_qualifiers", []):
                bound = q.get("bound_claim")
                unit = units.get(bound) if bound else None
                anchor = (unit or {}).get("verbatim_anchor") or q["verbatim_qualifier"]
                matched = sq._claims_matching(sq._content_words(anchor), claims)
                if not matched:
                    continue  # `absent` never reads a marker
                checked += 1
                text = " \n ".join(matched)
                markers = sq.MARKERS[q["class"]]
                word_bounded = any(re.search(rf"\b{re.escape(m.strip())}", text)
                                   for m in markers)
                if word_bounded:
                    continue
                mid_word = [m for m in markers if m in text]
                assert not mid_word, (
                    f"{sid}:{arm} {q['id']} ({q['class']}) reads `preserved` only "
                    f"through a mid-word substring hit {mid_word} — the "
                    "over-credit the module documents is now moving a cell in a "
                    "committed run, so §10's 'latent, not a wrong number' no longer "
                    "holds and the runs need re-scoring under a word-bounded table")
    assert checked >= 20, f"only {checked} surviving-claim cells inspected — the pin is vacuous"


# ── an arm that emits NOTHING: refused at the CLI, never crashed ────────────────

def test_an_arm_that_emits_no_claims_is_refused_not_crashed(tmp_path, capsys):
    """An empty extract made `main()` die with a `TypeError`, writing no artifact.

    The decision-input print formatted the three deltas unconditionally, and
    `_d()` returns None whenever an arm has no reading for a key — which is what
    precision is for an arm that emitted zero claims (`hits / 0`). So the run
    that most needs the "cannot certify" record produced NO record at all: the
    traceback landed before `out_path.write_text()`.

    Fail-closed now, two ways: the deltas render `n/a` (see the sibling test), and
    a split with no precision delta is REFUSED with a message instead of being
    scored as a measurement. An empty extract sets no contamination flag either
    (`partial`/`truncated`/`s2_error` are clean — the output is empty, not
    incomplete), so this is the one input the receipt guards cannot see.
    """
    arms = _receipts_copy(tmp_path)
    for sid in HOLDOUT_SESSIONS:
        receipt = arms / f"{sid}.arms.json"
        rec = json.loads(receipt.read_text())
        rec["embed_list"] = {arm: {"points": [], "operators": []} for arm in sq.ARMS}
        receipt.write_text(json.dumps(rec))

    out = tmp_path / "score.json"
    with pytest.raises(SystemExit) as exc:
        _score_with(arms, out)
    assert exc.value.code == 2, "ap.error must REFUSE the run (exit 2), not crash"
    assert "no precision delta" in capsys.readouterr().err
    assert not out.exists(), (
        "a refused run must not write a score artifact — an artifact whose decision "
        "input is an empty extract is the 'settled off nothing' failure this guards")


def test_an_unscorable_delta_is_labelled_incomplete_and_the_artifact_still_writes(
        tmp_path, capsys, monkeypatch):
    """A delta that does not EXIST renders `n/a`; the run is recorded, not settled.

    Same crash, different leg: recall is None when the split has no planted units,
    and the old format string died on THAT too (`TypeError: unsupported format
    string passed to NoneType.__format__`) before the artifact was written. Here
    the precision legs are present, so the run is not refused — it is scored, and
    the route is recorded honestly: no branch was evaluated, so the label says
    "scoring input incomplete" rather than "settled (branch 2 certified)", which
    is what a missing delta used to certify.
    """
    import shutil

    gold = tmp_path / "gold"
    shutil.copytree(sq.GOLD_DIR, gold)
    for sid in HOLDOUT_SESSIONS:
        path = gold / f"{sid}.gold.json"
        doc = json.loads(path.read_text())
        doc["planted_units"] = []          # no planted facts → no recall reading
        path.write_text(json.dumps(doc))
    monkeypatch.setattr(sq, "GOLD_DIR", gold)

    out = tmp_path / "score.json"
    assert _score_with(COMMITTED_ARMS, out) == 0
    console = capsys.readouterr().out
    assert "n/a pp" in console, (
        "the missing recall delta must render as n/a — formatting it crashed the "
        f"scorer before the artifact was written; console was:\n{console}")

    di = json.loads(out.read_text())["decision_input"]
    assert di["branches"]["holdout"]["delta_recall_pp"] is None
    assert di["branch_2_applies"] is False
    assert di["branch_2_certified"] is False
    assert di["verdict"] == "no branch matched — scoring input incomplete", di["verdict"]
    assert "SCORING INPUT INCOMPLETE" in console
