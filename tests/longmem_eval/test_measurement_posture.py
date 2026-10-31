"""#8486 — the run's MEASUREMENT POSTURE (ingest mode + belief activation).

Hermetic: pure functions, no DB, no network. The load-bearing contract is
that a LongMemEval number can never be published with an IMPLIED posture —
the belief layer is EP-INERT on a v2 graph unless ``activate_beliefs`` ran
(#2598: every claim reads the Beta(1,1) ``0.50`` of a prior that was never
measured), so "was it activated" must be an explicit, recorded, non-unknown
fact wherever the number appears.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.longmem_eval import measurement_posture as mp


def _manifest(**over) -> dict:
    m = {"points_promoted": 10, "operators_promoted": 4, "ep_ran": True,
         "ep_engine": "dream:full", "points_with_ep": 10,
         "points_unmeasured": 0, "duration_ms": 12}
    m.update(over)
    return m


# ── summarize_belief_activation ───────────────────────────────────────────

def test_summary_sums_the_manifests_and_counts_ep_runs():
    outcomes = [{"belief_activation": _manifest()},
                {"belief_activation": _manifest(points_promoted=5,
                                                operators_promoted=2)}]
    summary = mp.summarize_belief_activation(outcomes, requested=True)
    assert summary["requested"] is True
    assert summary["questions"] == 2
    assert summary["ep_ran"] == 2
    # the POSITIVE-measurement cell: both questions' censuses found posteriors
    assert summary["questions_with_ep"] == 2
    assert summary["failed"] == 0
    assert summary["points_promoted"] == 15
    assert summary["operators_promoted"] == 6
    assert summary["engine"] == "dream:full"
    assert summary["errors"] == []


def test_summary_counts_a_fail_open_manifest_as_failed():
    """The runner records ``{"ep_ran": False, "error": ...}`` when activation
    raises — that question's belief layer is inert, so the run must not read
    as activated."""
    outcomes = [{"belief_activation": _manifest()},
                {"belief_activation": {"ep_ran": False,
                                       "error": "RuntimeError: boom"}}]
    summary = mp.summarize_belief_activation(outcomes, requested=True)
    assert summary["questions"] == 2
    assert summary["ep_ran"] == 1
    assert summary["failed"] == 1
    assert "boom" in summary["errors"][0]
    assert mp.build(ingest_mode="v2", activation=summary)["belief_layer"] == \
        mp.BELIEF_LAYER_PARTIAL


def test_summary_counts_every_question_and_only_the_measured_ones():
    """The arm-OFF path leaves the outcome shape untouched, and the
    denominator is EVERY outcome — not the manifest count.

    ``questions`` is what the number is computed over, so an outcome with no
    manifest must still be counted (it is an unmeasured question); the
    ``measured`` cell is what drops to zero.
    """
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1"}, {"belief_activation": "junk"}],
        requested=False)
    assert summary["requested"] is False
    assert summary["questions"] == 2
    assert summary["measured"] == 0
    assert summary["ep_ran"] == 0


def test_a_mostly_resumed_run_reads_partial_never_activated():
    """RED (fresh-context review, P1): a checkpoint written WITHOUT the arm
    and resumed WITH it re-appends the old outcomes verbatim — they carry no
    ``belief_activation`` manifest. Counting only manifests made 1 fresh
    question out of 3 read as ``activated``, while the two the number is
    mostly computed over are EP-inert.

    The checkpoint fingerprint now refuses that resume outright (see
    ``test_fingerprint_pins_the_belief_arm``); this denominator is the
    independent second guard, and it must report ``partial`` — with
    ``beliefs_activated`` False, never True.
    """
    resumed = [{"question_id": "old1", "label": True},
               {"question_id": "old2", "label": False}]
    fresh = [{"question_id": "new1", "label": True,
              "belief_activation": _manifest()}]
    summary = mp.summarize_belief_activation(resumed + fresh, requested=True)
    assert summary["questions"] == 3
    assert summary["measured"] == 1
    assert summary["ep_ran"] == 1
    posture = mp.build(ingest_mode="v2", activation=summary)
    assert posture["belief_layer"] == mp.BELIEF_LAYER_PARTIAL
    assert posture["beliefs_activated"] is False


def test_fingerprint_pins_the_belief_arm(tmp_path):
    """#8486: the arm is an ALWAYS-present checkpoint-fingerprint member (the
    ``session_workers`` / retry-constant convention). It changes graph content
    — draft points/operators promoted to ``live`` + persisted posteriors — so a
    checkpoint written under one side of the toggle must refuse the other via
    ``CheckpointStaleError`` rather than blend EP-inert and EP-measured
    questions into one published number."""
    from tools.longmem_eval import run as runner

    base = dict(reader_model="mock-reader", judge_model="mock-judge",
                ks=(5,), top_k=5, split="s", ingest_mode="v2",
                extractor_model=None, max_retries=3,
                dataset_fingerprint="x", rerank_config={})
    off = runner._build_fingerprint(**base)
    assert off["activate_beliefs"] is False  # ALWAYS present
    on = runner._build_fingerprint(**base, activate_beliefs=True)
    assert on["activate_beliefs"] is True
    cp = tmp_path / "cp.json"
    runner._save_checkpoint(str(cp), [], [], on)
    with pytest.raises(runner.CheckpointStaleError) as ei:
        runner._load_checkpoint(str(cp), off)
    assert "activate_beliefs" in str(ei.value)
    # the reverse direction is refused too — one number, one posture
    cp2 = tmp_path / "cp2.json"
    runner._save_checkpoint(str(cp2), [], [], off)
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp2), on)


# ── belief_layer_state ────────────────────────────────────────────────────

def test_inert_when_the_arm_was_not_requested():
    summary = mp.summarize_belief_activation([], requested=False)
    assert mp.belief_layer_state(requested=False, activation=summary) == \
        mp.BELIEF_LAYER_INERT


def test_armed_but_unproven_is_partial_never_activated():
    """A request is not consent: an armed run whose questions produced no
    manifest did not measure the belief layer."""
    empty = mp.summarize_belief_activation([], requested=True)
    assert mp.belief_layer_state(requested=True, activation=empty) == \
        mp.BELIEF_LAYER_PARTIAL
    assert mp.build(ingest_mode="v2", activation=empty)["belief_layer"] == \
        mp.BELIEF_LAYER_PARTIAL


def test_activated_requires_every_question_to_have_run_ep():
    ok = mp.summarize_belief_activation(
        [{"belief_activation": _manifest()}], requested=True)
    assert mp.belief_layer_state(requested=True, activation=ok) == \
        mp.BELIEF_LAYER_ACTIVATED
    # an EP pass that failed (ep_ran False, no exception) is NOT activated
    half = mp.summarize_belief_activation(
        [{"belief_activation": _manifest()},
         {"belief_activation": _manifest(ep_ran=False)}], requested=True)
    assert mp.belief_layer_state(requested=True, activation=half) == \
        mp.BELIEF_LAYER_PARTIAL


# ── POSITIVE measurement: "ran" is not "measured" ──────────────────────

def _unmeasured_manifest(**over) -> dict:
    """The manifest ``ep_activation.activate_beliefs`` returns when the EP
    pass ran and RETURNED NORMALLY but the post-pass census found no point
    carrying a posterior.

    Two documented, reachable states produce exactly this: the window holds no
    EP factors (``sdk.dream`` records ``no_op_reason = "no_ep_factors"`` and
    returns — a v2 question whose extraction minted claims but no IMPL/NAND
    operators, common on single-session questions), and the #1163 stale-run
    stand-down ("zero writes is correct here"). The shape is pinned by
    ``tests/longmem_eval/test_ep_activation.py::test_activate_counts_unmeasured``
    and ``::test_activate_no_drafts_still_runs_ep``.
    """
    m = {"points_promoted": 57, "operators_promoted": 0, "ep_ran": True,
         "ep_engine": "dream:full", "points_with_ep": 0,
         "points_unmeasured": 57, "duration_ms": 9}
    m.update(over)
    return m


def test_a_pass_that_measured_nothing_is_never_activated():
    """RED (fresh-context review, P1 — fail-open): ``ep_ran`` is set by a bare
    ``try`` around ``sdk.dream(...)``, which returns normally with ZERO
    belief-state writes in the two states documented above. Deciding the
    state from ``ep_ran``/``failed`` alone therefore let a run publish
    ``beliefs_activated: true`` for a graph in which not one claim carries a
    posterior, while its own ``activation.points_unmeasured`` recorded the
    opposite — the precise over-claim (#2598) this gate exists to stop, in the
    direction the gate must fail CLOSED. The manifest's own positive census
    (``points_with_ep``) is the evidence that a question was measured.
    """
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _unmeasured_manifest()}],
        requested=True)
    assert summary["ep_ran"] == 1            # the pass ran...
    assert summary["failed"] == 0
    assert summary["questions_with_ep"] == 0  # ...and measured nothing
    posture = mp.build(ingest_mode="v2", activation=summary)
    assert posture["belief_layer"] == mp.BELIEF_LAYER_PARTIAL
    assert posture["beliefs_activated"] is False


def test_one_unmeasured_question_makes_the_whole_run_partial():
    """The requirement is PER QUESTION: one question whose census found no
    posterior is an EP-inert question inside the same number.

    The summed census here is NON-zero (10), so only a per-question count can
    see the unmeasured one — this is what distinguishes the fix from a
    ``sum(points_with_ep) > 0`` check, which would still read ``activated``.
    """
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _manifest()},
         {"question_id": "q2", "belief_activation": _unmeasured_manifest()}],
        requested=True)
    assert summary["points_with_ep"] == 10
    assert summary["questions_with_ep"] == 1
    posture = mp.build(ingest_mode="v2", activation=summary)
    assert posture["belief_layer"] == mp.BELIEF_LAYER_PARTIAL
    assert posture["beliefs_activated"] is False


def test_unreachable_points_do_not_block_activation():
    """POLICY (recorded, not accidental): ``points_unmeasured > 0`` alone does
    NOT force ``partial``.

    A v2 graph holds non-operator points that belong to NO factor graph (its
    turn + raw-chunk points), so they can never be stamped and the census is
    non-zero for EVERY real run — requiring the whole census would make
    ``activated`` unreachable and the arm meaningless. A claim left without a
    posterior still reads ``unmeasured`` through
    ``ep_activation.read_confidence`` (#2598), so the per-point truth does not
    depend on this policy.
    """
    summary = mp.summarize_belief_activation(
        [{"belief_activation": _manifest(points_with_ep=10,
                                          points_unmeasured=47)}],
        requested=True)
    assert mp.build(ingest_mode="v2", activation=summary)["belief_layer"] == \
        mp.BELIEF_LAYER_ACTIVATED


# ── the arm-resolution refusals + the per-question fail-open wrapper ──────

def _one_instance() -> list[dict]:
    import json

    path = (Path(__file__).resolve().parents[1]
            / "fixtures" / "longmemeval_mini.json")
    return json.loads(path.read_text(encoding="utf-8"))[:1]


def test_run_evaluation_refuses_the_belief_arm_on_a_deterministic_graph():
    """The programmatic mirror of the CLI refusal: a deterministic graph has
    no operators/EP structure, so an "activated" posture claimed over it would
    describe a graph that cannot carry a belief layer. Refused at ARM
    RESOLUTION — before any question runs, no DB, outside every fail-open
    region — so it aborts instead of degrading per question."""
    from tools.longmem_eval import run as runner

    with pytest.raises(ValueError) as ei:
        runner.run_evaluation(_one_instance(), reader=None, judge=None,
                              ks=(5,), top_k=5, split="s",
                              activate_beliefs=True)
    assert "activate_beliefs requires ingest_mode='v2'" in str(ei.value)


def test_cli_refuses_the_belief_arm_on_a_deterministic_graph(capsys):
    """``--activate-beliefs --ingest-mode deterministic`` is a posture claim
    about a graph with nothing to activate: the CLI must reject it loudly
    (exit 2) rather than run an inert pass that then reports a posture."""
    from tools.longmem_eval import run as runner

    with pytest.raises(SystemExit) as ei:
        runner.run_main(["--activate-beliefs", "--ingest-mode",
                         "deterministic"])
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "--activate-beliefs requires --ingest-mode v2" in err


def test_a_failed_activation_records_the_fail_open_shape_and_reads_partial(
        capsys):
    """``_activate_beliefs_for_question`` is fail-open by design — a hard
    failure must not abort a mid-flight 500-Q run, but it must never be
    silent. It records ``{"ep_ran": False, "error": ...}`` on the outcome
    (which the run's posture then reads as ``partial``) and says so on stderr.
    """
    from tools.longmem_eval import run as runner

    class _Boom:
        def _get_proj(self):
            raise RuntimeError("graph unreachable")

    shape = runner._activate_beliefs_for_question(
        _Boom(), namespace="ns-1", qid="q1")
    assert shape["ep_ran"] is False
    assert "RuntimeError: graph unreachable" in shape["error"]
    assert "[beliefs] activation FAILED for q1" in capsys.readouterr().err

    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": shape},
         {"question_id": "q2", "belief_activation": _manifest()}],
        requested=True)
    assert summary["failed"] == 1
    assert summary["questions_with_ep"] == 1
    posture = mp.build(ingest_mode="v2", activation=summary)
    assert posture["belief_layer"] == mp.BELIEF_LAYER_PARTIAL
    assert posture["beliefs_activated"] is False


# ── build ────────────────────────────────────────────────────────────────

def test_build_records_both_halves_of_the_disclosure():
    inert = mp.build(ingest_mode="deterministic")
    assert inert["ingest_mode"] == "deterministic"
    # no decision reached this run → UNKNOWN, never an assumed "off"
    assert inert["beliefs_activated"] is None
    assert inert["belief_layer"] == mp.BELIEF_LAYER_UNKNOWN

    off = mp.build(ingest_mode="v2", activation=mp.summarize_belief_activation(
        [], requested=False))
    assert off["beliefs_activated"] is False
    assert off["belief_layer"] == mp.BELIEF_LAYER_INERT

    on = mp.build(ingest_mode="v2", activation=mp.summarize_belief_activation(
        [{"belief_activation": _manifest()}], requested=True))
    assert on["beliefs_activated"] is True
    assert on["belief_layer"] == mp.BELIEF_LAYER_ACTIVATED
    assert on["activation"]["points_promoted"] == 10


def test_of_report_reads_only_a_real_posture_block():
    assert mp.of_report(None) is None
    assert mp.of_report({}) is None
    assert mp.of_report({"methodology": {}}) is None
    assert mp.of_report({"methodology": {"measurement_posture": "junk"}}) is None
    posture = mp.build(ingest_mode="v2")
    assert mp.of_report(
        {"methodology": {"measurement_posture": posture}}) == posture


# ── whole-chain: report methodology → published receipt gate ─────────────

def test_completed_receipt_refuses_every_undisclosed_posture():
    """The gate: a completed number publishes ONLY with an explicit posture.

    RED mutations: drop ``measurement_posture`` → refused; ``beliefs_activated
    = None`` (a pre-#8486 producer) → refused; ``belief_layer = 'unknown'`` →
    refused; a posture whose ingest mode disagrees with the receipt's →
    refused.
    """
    from tools.longmem_eval.w7_publish import (
        JUDGE_PIN,
        build_receipt,
        validate_receipt,
    )

    def _receipt(**over):
        kwargs = dict(
            run_id="r", date="d", commit="c", corpus_hash="sha256:x",
            # #8486: the pin must belong to the recorded judge — an arbitrary
            # pin with an arbitrary judge is the pairing the validator refuses.
            judge_pin=JUDGE_PIN, judge_model="gpt-4o-2024-08-06",
            judge_rubric_id_hash="h",
            reader_model_spec="s", reader_pinned=True, ingest_mode="v2",
            run_status="completed", verdict="pass", failure_origin=None,
            cost_usd=1.0, notes=[], resolved_config={},
            metrics={
                "official_recall_all@5": 0.5,
                "official_recall_all@5_by_category": {
                    "rows": {c: {"n": 0} for c in (
                        "full_mix", "multi-session", "knowledge-update",
                        "single-session-preference")}},
            },
            measurement_posture=mp.build(
                ingest_mode="v2",
                activation=mp.summarize_belief_activation(
                    [{"belief_activation": _manifest()}], requested=True)),
        )
        kwargs.update(over)
        return build_receipt(**kwargs)

    assert validate_receipt(_receipt()) == []

    missing = _receipt()
    del missing["measurement_posture"]
    assert any("measurement_posture" in i for i in validate_receipt(missing))

    unrecorded = _receipt(measurement_posture=mp.build(ingest_mode="v2"))
    issues = validate_receipt(unrecorded)
    assert any("beliefs_activated" in i for i in issues)
    assert any("belief_layer" in i for i in issues)

    mismatch = _receipt(measurement_posture=mp.build(
        ingest_mode="deterministic"))
    assert any("disagrees" in i for i in validate_receipt(mismatch))

    # a FAILED run is not asked for the disclosure (nothing is published); its
    # verdict is 'inconclusive' — a failed run claiming 'pass' is refused by
    # the #8486 status/verdict clause (tests/test_w7_publish.py)
    assert validate_receipt(_receipt(run_status="failed",
                                     verdict="inconclusive",
                                     measurement_posture=None,
                                     metrics={"x": 1})) == []


# ── the COMPARISON surface: a delta across postures is flagged ────────────

def _cmp_report(tag: str, *, posture: dict | None) -> dict:
    """A minimal compare_reports input (#1528 shape) carrying/omitting the
    #8486 posture block."""
    methodology = {
        "dataset_source": "xiaowu0162/longmemeval-cleaned",
        "split": "s",
        "reader_model": "reader-x",
        "judge_model": "judge-gpt4o",
        "ingest_mode": "v2",
        "reader_prompt_hash": "deadbeefcafe1234",
        "git_sha": "c0ffee",
        "run_at_utc": "2026-08-20T00:00:00Z",
    }
    if posture is not None:
        methodology["measurement_posture"] = posture
    return {
        "benchmark": "LongMemEval",
        "dataset": "xiaowu0162/longmemeval-cleaned",
        "split": "s",
        "n_questions": 2,
        "outcomes": [
            {"question_id": "q1", "question_type": "multi-session",
             "label": True, "session_recall@k": {"20": 1.0},
             "context_tokens": 100, "n_ingest_errors": 0},
            {"question_id": "q2", "question_type": "multi-session",
             "label": False, "session_recall@k": {"20": 0.5},
             "context_tokens": 100, "n_ingest_errors": 0},
        ],
        "failures": [],
        "n_failed": 0,
        "methodology": methodology,
        "tag": tag,
    }


def _activated(ingest_mode: str = "v2") -> dict:
    return mp.build(ingest_mode=ingest_mode, activation=(
        mp.summarize_belief_activation(
            [{"belief_activation": _manifest()}], requested=True)))


def _not_activated(ingest_mode: str = "v2") -> dict:
    return mp.build(ingest_mode=ingest_mode, activation=(
        mp.summarize_belief_activation([], requested=False)))


def test_comparison_carries_both_sides_and_flags_a_belief_layer_drift():
    """A delta across an EP-measured graph and an EP-inert one is a belief
    -layer delta, never a memory delta — it must be visible in the
    comparability block AND in the warnings the console prints (#8486)."""
    from tools.longmem_eval.report import compare_reports

    cmp = compare_reports(_cmp_report("on", posture=_activated()),
                          _cmp_report("off", posture=_not_activated()))
    bl = cmp["comparability"]["belief_layer"]
    assert bl["a"]["beliefs_activated"] is True
    assert bl["b"]["beliefs_activated"] is False
    assert bl["a"]["ingest_mode"] == bl["b"]["ingest_mode"] == "v2"
    assert bl["match"] is False
    assert any("belief layer differs" in w
               and "unmeasured 0.50" in w
               for w in cmp["comparability"]["warnings"]), \
        cmp["comparability"]["warnings"]


def test_comparison_does_not_invent_a_drift_from_an_unrecorded_posture():
    """A pre-#8486 report carries no posture. UNKNOWN is not a mismatch —
    claiming drift there would state a fact about the report's age as a fact
    about the runs."""
    from tools.longmem_eval.report import compare_reports

    cmp = compare_reports(_cmp_report("on", posture=_activated()),
                          _cmp_report("old", posture=None))
    bl = cmp["comparability"]["belief_layer"]
    assert bl["a"]["beliefs_activated"] is True
    assert bl["b"]["beliefs_activated"] is None
    assert bl["match"] is None
    assert not any("belief layer differs" in w
                   for w in cmp["comparability"]["warnings"])


def test_comparison_flags_activated_against_partial():
    """RED (fresh-context review, P2): comparing the boolean alone made an
    ``activated`` run and a ``partial`` one — the latter mixing EP-inert
    questions into the same number — read as a match. The posture is compared
    whole."""
    from tools.longmem_eval.report import compare_reports

    partial = mp.build(ingest_mode="v2", activation=(
        mp.summarize_belief_activation(
            [{"question_id": "q1", "belief_activation": _manifest()},
             {"question_id": "q2"}], requested=True)))
    assert partial["belief_layer"] == mp.BELIEF_LAYER_PARTIAL
    cmp = compare_reports(_cmp_report("on", posture=_activated()),
                          _cmp_report("partial", posture=partial))
    bl = cmp["comparability"]["belief_layer"]
    assert bl["a"]["belief_layer"] == mp.BELIEF_LAYER_ACTIVATED
    assert bl["b"]["belief_layer"] == mp.BELIEF_LAYER_PARTIAL
    assert bl["match"] is False
    assert any("belief layer differs" in w
               for w in cmp["comparability"]["warnings"])


def test_comparison_flags_inert_against_partial_on_the_layer_alone():
    """Both sides report ``beliefs_activated=False`` (neither measured the
    whole belief layer) but they are not the same posture: one never attempted
    activation, the other attempted it and partly failed. ``match`` is on the
    resolved layer too, so this is flagged rather than reported as agreement."""
    from tools.longmem_eval.report import compare_reports

    partial = mp.build(ingest_mode="v2", activation=(
        mp.summarize_belief_activation(
            [{"question_id": "q1", "belief_activation": _manifest()},
             {"question_id": "q2"}], requested=True)))
    cmp = compare_reports(_cmp_report("off", posture=_not_activated()),
                          _cmp_report("partial", posture=partial))
    bl = cmp["comparability"]["belief_layer"]
    assert bl["a"]["beliefs_activated"] is bl["b"]["beliefs_activated"] is False
    assert bl["a"]["belief_layer"] == mp.BELIEF_LAYER_INERT
    assert bl["b"]["belief_layer"] == mp.BELIEF_LAYER_PARTIAL
    assert bl["match"] is False
    assert any("belief layer differs" in w
               for w in cmp["comparability"]["warnings"])


def test_comparison_of_matching_postures_is_clean_and_still_disclosed():
    from tools.longmem_eval.report import compare_reports, print_comparison

    cmp = compare_reports(_cmp_report("on-a", posture=_activated()),
                          _cmp_report("on-b", posture=_activated()))
    bl = cmp["comparability"]["belief_layer"]
    assert bl["match"] is True
    assert not any("belief layer" in w for w in cmp["comparability"]["warnings"])
    # the console surface states it even when the two sides agree
    import io
    buf = io.StringIO()
    print_comparison(cmp, file=buf)
    text = buf.getvalue()
    assert "belief layer: A ingest=v2 beliefs_activated=True" in text
    assert "B ingest=v2 beliefs_activated=True" in text


# ── the #2080 ingest cache: the arm is a REUSE gate, not a fingerprint ────

def test_cache_hit_refuses_a_belief_activated_graph_to_an_arm_off_run():
    """RED (fresh-context review round 2, P2): the ingest fingerprint cannot
    see that a prior arm-ON run promoted the cached graph's draft points and
    persisted posteriors. Serving that graph to an arm-OFF run would publish
    a number measured on an activated belief layer while its posture says
    ``inert`` — a false disclosure. The mirror direction stays a hit: an
    arm-ON run may reuse an inert graph and activate it (idempotent)."""
    from tools.longmem_eval.run import cache_hit_decision

    kw = dict(marker_fp="fp", graph_present=True, current_fp="fp")
    # arm-OFF run, graph already activated by an earlier arm-ON run -> MISS
    assert cache_hit_decision(**kw, marker_activated=True,
                              activate_beliefs=False) is False
    # arm-ON run, already-activated graph -> HIT (promotion/EP are idempotent)
    assert cache_hit_decision(**kw, marker_activated=True,
                              activate_beliefs=True) is True
    # arm-OFF run, inert graph -> HIT (the pre-#8486 behaviour, unchanged)
    assert cache_hit_decision(**kw, marker_activated=False,
                              activate_beliefs=False) is True
    # arm-ON run, inert graph -> HIT (activated after reuse)
    assert cache_hit_decision(**kw, marker_activated=False,
                              activate_beliefs=True) is True
    # a pre-arm marker carries no flag, so the default reads as not activated
    assert cache_hit_decision(**kw) is True


def test_marker_flag_never_reads_the_string_false_as_activated():
    """A graph DB that hands back 'false' as a string must not read TRUE
    (``bool('false')`` is True) — that would let an arm-OFF run reuse an
    activated graph, the one direction the reuse gate exists to refuse."""
    from tools.longmem_eval.run import _marker_activated

    assert _marker_activated(True) is True
    assert _marker_activated(False) is False
    assert _marker_activated("false") is False
    assert _marker_activated("FALSE") is False
    assert _marker_activated("true") is True
    assert _marker_activated(None) is False


def test_a_failed_marker_refresh_drops_the_marker_instead_of_lying():
    """#8486 (fresh-context review round 4, P2): the refresh is the only thing
    between an already-activated cached graph and a later arm-OFF run reusing
    it. The sibling ``_cache_marker_write`` is best-effort because a missing
    marker is a MISS — so a FAILED refresh must not be swallowed into a stale
    ``false``: it drops the marker (miss → re-ingest) and never raises, so this
    question's own run continues with a truthful posture."""
    from tools.longmem_eval.run import _cache_marker_activate

    class _G:
        def __init__(self):
            self.queries: list[tuple[str, dict]] = []
            self.fail_merge = True

        def query(self, cypher, params=None):
            self.queries.append((" ".join(cypher.split()), params or {}))
            if "MERGE" in cypher and self.fail_merge:
                raise RuntimeError("transient write hiccup")
            return type("R", (), {"result_set": []})()

    class _Proj:
        def __init__(self):
            self.g = _G()

    proj = _Proj()
    _cache_marker_activate(proj, namespace="ns-1")     # must not raise
    cyphers = [q for q, _ in proj.g.queries]
    assert any("MERGE" in q for q in cyphers), cyphers
    assert any("DETACH DELETE m" in q for q in cyphers), (
        f"a failed refresh must drop the marker (miss on the next run) — "
        f"got {cyphers}")
    assert proj.g.queries[-1][1] == {"ns": "ns-1"}

    # the happy path sets the flag and touches nothing else
    proj2 = _Proj()
    proj2.g.fail_merge = False
    _cache_marker_activate(proj2, namespace="ns-2")
    assert [q for q, _ in proj2.g.queries] == [
        "MERGE (m:lme_ingest_cache {namespace:$ns}) "
        "SET m.belief_activated=$activated"]
    assert proj2.g.queries[0][1] == {"ns": "ns-2", "activated": True}


# ── the DISCLOSURE document must agree with this module ──────────────────
#
# `docs/benchmarks/longmemeval-500q/run-report.md` is the surface that STATES
# the number, so it is part of the gate: a posture the report does not define,
# or a mechanism claim it gets backwards, is a disclosure defect even when the
# code is right (fresh-context review, P1/P2).

_REPORT = (Path(__file__).resolve().parents[2] / "docs" / "benchmarks"
           / "longmemeval-500q" / "run-report.md")


def _report() -> str:
    return _REPORT.read_text(encoding="utf-8")


def test_the_report_defines_every_state_this_module_can_return():
    """RED (fresh-context review, P2): the report defined ``inert`` and
    ``activated`` but ``partial`` — the state a correctly-armed run actually
    reports (``test_one_unmeasured_question_makes_the_whole_run_partial``,
    because single-session questions often carry no operators) — appeared
    NOWHERE in it, and the acceptance gate publishes ``partial``. So the row a
    reader would use to interpret the number had no defined meaning.

    Keyed on the module's own vocabulary: a state added here without a
    definition on the disclosure surface fails this test.
    """
    text = _report()
    missing = [s for s in sorted(mp.BELIEF_LAYER_VALUES)
               if f"`{s}`" not in text]
    assert missing == [], (
        f"the run report never names these belief-layer states: {missing}")


def test_the_report_states_the_bar_against_the_positive_measurement_cell():
    """The bar must be per QUESTION, not all-or-nothing: ``activated`` requires
    every question to carry a measured posterior (``questions_with_ep``), so an
    unqualified "must say activated" bar is unreachable on this benchmark and
    a ``partial`` run would have no defined description. Pinning the cell name
    keeps the doc's bar tied to the value the code computes."""
    text = _report()
    assert "questions_with_ep" in text, (
        "the bar must name the positive-measurement cell the code decides on")
    assert "beliefs_activated" in text, (
        "the receipt's portable boolean must be named where the bar is stated")


def test_the_report_does_not_claim_the_eval_graphs_have_no_operators():
    """RED (fresh-context review, P1): §3 said "no operators/NANDs exist in the
    eval graphs" and attributed it to a ``dataset_audit.py`` divergence record.
    Both are false for the ``v2`` step-5 configuration §1 records —
    ``ingest_v2`` writes IMPL/NAND operators (``sdk.create_operator``) and
    ``--activate-beliefs`` promotes and EPs exactly those, and
    ``dataset_audit.py`` never mentioned operators at all. A reader taking the
    bullet at face value concludes "the belief layer cannot matter here",
    which is the premise #8486 exists to correct."""
    text = _report()
    assert "no operators/NANDs exist" not in text, (
        "the report still claims the eval graphs have no operators — false "
        "for the v2 configuration it records")
    assert "dataset_audit.py` divergence record rides" not in text, (
        "the report still attributes the operators claim to dataset_audit.py")


# ── the INGEST-CACHE contract must describe the belief arm too ───────────
#
# `tools/longmem_eval/README.md` is the operator reference for the #2080 cache
# and for the runner's flags, and `run.py::cache_hit_decision` documents the
# gate's own invariant. Both are load-bearing prose: a maintainer reasons from
# them about whether a cache HIT can change the graph, which is exactly the
# question #8486 answered "yes, under --activate-beliefs".

_README = (Path(__file__).resolve().parents[2] / "tools" / "longmem_eval"
           / "README.md")


def _flat(text: str) -> str:
    """Whitespace-insensitive form — markdown re-wraps, claims do not."""
    return " ".join(text.split())


def test_the_ingest_cache_contract_documents_the_belief_arm():
    """RED (fresh-context review, P2 — downstream impact): the Contract
    section still described the pre-#8486 seam — the marker as written ONLY by
    an ingest, the seam as "INGEST-level only", and reuse as "only ever reused
    read-only" — while the code at this head refreshes the marker on the cache
    HIT path and (with ``--activate-beliefs``) mutates the persisted graph in
    place. ``--activate-beliefs`` was documented nowhere, although
    ``run_protocol`` injects it into every run step."""
    text = _flat(_README.read_text(encoding="utf-8"))
    assert "--activate-beliefs" in text, (
        "the operator reference never documents --activate-beliefs, which "
        "run_protocol injects into every run step")
    assert "belief_activated" in text, (
        "the cache marker's belief_activated flag must be documented where "
        "the marker lifecycle is")
    assert "only ever reused read-only" not in text, (
        "README still claims cached reuse is always read-only — a cache HIT "
        "under --activate-beliefs promotes points and persists posteriors "
        "in place")
    assert "this seam is INGEST-level only" not in text, (
        "README still claims the seam is ingest-level only — it now gates on "
        "the belief layer too (#8486)")
    assert "mutates the persisted graph in place" in text, (
        "README must state the HIT-path mutation, which is why the reuse gate "
        "and the marker refresh exist")


def test_the_cache_gate_docstring_names_the_hit_path_exception():
    """RED (fresh-context review, P2 — comment compliance):
    ``cache_hit_decision``'s docstring said "The marker is written ONLY after
    an ingest completes, so mid-write vs complete is distinguishable by
    construction" — an absolute this PR's ``_cache_marker_activate`` breaks on
    the cache HIT path. The sentence a maintainer would use to prove "marker
    present ⇒ ingest complete" must name both paths."""
    from tools.longmem_eval import run as lme_run

    doc = _flat(lme_run.cache_hit_decision.__doc__ or "")
    assert "The marker is written ONLY after an ingest completes" not in doc, (
        "the docstring still states the absolute _cache_marker_activate "
        "falsifies on the cache HIT path")
    assert "_cache_marker_write" in doc, (
        "the docstring must name the fresh-ingest write path")
    assert "_cache_marker_activate" in doc, (
        "the docstring must name the HIT-path flag refresh that is the "
        "exception to the marker invariant")


# ── #10137: the POPULATIONS, published beside the ratio ───────────────────
#
# The state says HOW MUCH is covered. These cells say WHY the rest is not,
# and how much of the covered part actually MOVED. Both were previously
# inferred from a green run: "single-session questions are the common case"
# was an assertion, and a fully covered graph whose every posterior is the
# neutral 0.50 read as belief-activated.

def _census(**over) -> dict:
    """A CURRENT-shape manifest: #10137's contribution cells present."""
    m = _manifest(operators=3, factors=3, points_off_neutral=10,
                  points_moved_off_neutral=0, no_op_reason=None)
    m.update(over)
    return m


def test_the_contribution_populations_are_published():
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "question_type": "multi-session",
          "belief_activation": _census(points_off_neutral=7)},
         {"question_id": "q2", "question_type": "multi-session",
          "belief_activation": _census(points_off_neutral=0)},
         {"question_id": "q3", "question_type": "single-session-user",
          "belief_activation": _unmeasured_manifest(factor_edges=0, operators=0)}],
        requested=True)
    # coverage (unchanged bar): 2 of 3 questions carry a measured posterior
    assert summary["questions"] == 3
    assert summary["questions_with_ep"] == 2
    # contribution: only ONE of the two covered questions moved anything
    assert summary["questions_off_neutral"] == 1
    assert summary["points_off_neutral"] == 7
    # ...and the covered-but-neutral one is enumerated, not hidden
    assert summary["questions_neutral"] == 1
    assert summary["neutral_questions"] == [
        {"question_id": "q2", "question_type": "multi-session"}]
    # the inert one is enumerated WITH its cause
    assert summary["inert_questions"] == [
        {"question_id": "q3", "question_type": "single-session-user",
         "reason": mp.INERT_NO_OPERATORS}]
    assert summary["inert_by_reason"] == {mp.INERT_NO_OPERATORS: 1}


def test_a_covered_but_neutral_run_still_reads_activated():
    """POLICY (recorded, deliberately NOT re-cut — #8486's disclosure bar and
    an OPEN OWNER DECISION): ``activated`` counts the questions whose claims
    carry ANY measured state. A run whose every posterior is the neutral
    fixed point is still ``activated``; the contribution cells beside it are
    what say it moved nothing."""
    summary = mp.summarize_belief_activation(
        [{"question_id": f"q{i}", "belief_activation":
          _census(points_off_neutral=0)} for i in range(3)],
        requested=True)
    posture = mp.build(ingest_mode="v2", activation=summary)
    assert posture["belief_layer"] == mp.BELIEF_LAYER_ACTIVATED
    assert summary["questions_off_neutral"] == 0
    assert summary["questions_neutral"] == 3
    assert summary["points_with_ep"] == 30


@pytest.mark.parametrize("manifest, reason", [
    # no manifest at all — a RESUMED outcome from before the arm existed
    (None, mp.INERT_NO_MANIFEST),
    # the runner's fail-open shape
    ({"ep_ran": False, "error": "RuntimeError: boom"},
     mp.INERT_ACTIVATION_FAILED),
    # the pass ran, the graph has no operator at all: STRUCTURAL
    (_unmeasured_manifest(factor_edges=0, operators=0, no_op_reason="no_ep_factors"),
     mp.INERT_NO_OPERATORS),
    # operators exist, none reached the window: the SDK's own diagnosis
    (_unmeasured_manifest(operators=3, no_op_reason="no_ep_factors"),
     mp.INERT_NO_EP_FACTORS),
    # #1163 concurrent-run stand-down
    (_unmeasured_manifest(operators=3, no_op_reason="stale_run_guard"),
     mp.INERT_STALE_RUN_GUARD),
    # the pass wrote nothing this census can see, no diagnosis recorded
    (_unmeasured_manifest(operators=3), mp.INERT_UNMEASURED),
])
def test_every_inert_reason_is_reachable(manifest, reason):
    outcome = {"question_id": "q1", "question_type": "single-session-user"}
    if manifest is not None:
        outcome["belief_activation"] = manifest
    summary = mp.summarize_belief_activation([outcome], requested=True)
    assert summary["inert_questions"][0]["reason"] == reason
    assert summary["questions_inert"] == 1
    assert summary["questions_off_neutral"] == 0
    assert reason in mp.INERT_REASONS


def test_unknown_operator_cell_never_fabricates_a_structural_cause():
    """An older SDK/stub omits ``operators``. UNKNOWN must not be read as
    ``no_operators``: a fabricated structural cause would point a fix at
    extraction when the real cause was never measured."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1",
          "belief_activation": _unmeasured_manifest()}],   # no `operators`
        requested=True)
    assert summary["inert_questions"][0]["reason"] == mp.INERT_UNMEASURED
    # the bucket publishes UNKNOWN for the census (no cell to sum), never a
    # fabricated 0 — and the reason does not follow the absent cell either
    assert summary["by_question_type"][""]["operators"] is None
    assert summary["by_question_type"][""]["operators_unread"] == 1
    assert summary["operators"] is None


def test_a_measured_question_is_measured_whatever_the_cause_cell_says():
    """An operator-LESS graph can still carry posteriors (a direct IMPL/NAND
    edge between two claims is a factor with no operator node, #888 W5). The
    census is the fact; the cause cell must not overrule it."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _census(
            operators=0, points_with_ep=4, points_off_neutral=4)}],
        requested=True)
    assert summary["inert_questions"] == []
    assert summary["questions_off_neutral"] == 1


def test_by_question_type_turns_the_assertion_into_a_measurement():
    # #10137 review 14 (P2): ``m2`` is MEASURED but its contribution census was
    # never read (``points_off_neutral`` absent → UNKNOWN). It was missing from
    # this fixture, which is exactly why the identity asserted at the bottom of
    # this test looked complete: without a contribution-unknown row, the
    # ``off_neutral + neutral`` sum happens to equal ``questions_measured``, so
    # the assertion could not fail on the population that makes it false — and
    # would red SPURIOUSLY against a correct implementation the moment someone
    # added this row. A supported, in-tree shape (`_census_ids` returns None
    # when the claim-state read raises, and a resumed pre-#10137 manifest is the
    # same shape), so it belongs in the fixture.
    summary = mp.summarize_belief_activation(
        [{"question_id": "s1", "question_type": "single-session-user",
          "belief_activation": _unmeasured_manifest(factor_edges=0, operators=0)},
         {"question_id": "s2", "question_type": "single-session-user",
          "belief_activation": _unmeasured_manifest(factor_edges=0, operators=0)},
         {"question_id": "s3", "question_type": "single-session-assistant",
          "belief_activation": _unmeasured_manifest(factor_edges=0, operators=0)},
         {"question_id": "m2", "question_type": "multi-session",
          "belief_activation": {"ep_ran": True, "points_with_ep": 4,
                                "points_unmeasured": 0, "operators": 3,
                                "factor_edges": 0}},
         {"question_id": "m1", "question_type": "multi-session",
          "belief_activation": _census(points_off_neutral=5)}],
        requested=True)
    by_type = summary["by_question_type"]
    assert set(by_type) == {"single-session-user",
                            "single-session-assistant", "multi-session"}
    assert by_type["single-session-user"]["questions"] == 2
    assert by_type["single-session-user"]["questions_inert"] == 2
    assert by_type["multi-session"]["questions_with_ep"] == 2
    assert by_type["multi-session"]["points_off_neutral"] == 5
    assert by_type["multi-session"]["questions_contribution_unknown"] == 1
    # the split reconciles with the run-level counters (one denominator)
    assert sum(b["questions"] for b in by_type.values()) == summary["questions"]
    assert sum(b["questions_inert"] for b in by_type.values()) == \
        summary["questions_inert"]
    assert sum(b["questions_with_ep"] for b in by_type.values()) == \
        summary["questions_with_ep"]
    assert sum(b["questions_off_neutral"] for b in by_type.values()) == \
        summary["questions_off_neutral"]
    assert sum(b["questions_neutral"] for b in by_type.values()) == \
        summary["questions_neutral"]
    # #10137 review (P1): the CONTRIBUTION cells are the ones that did NOT
    # reconcile — the run-level cell summed EVERY manifest while the buckets
    # counted only classified ``measured`` questions, so one report published
    # two different numbers for ``points_off_neutral``. Both are now derived
    # from the buckets, and this asserts the property rather than the fix.
    #
    # #10137 review 6 (P2): a bucket that measured NOTHING publishes ``None``,
    # not a fabricated ``0`` — so the reconciliation is over the buckets that
    # DID measure, and the inert-only types must be absent from the sum rather
    # than contributing a zero. Below, ``single-session-*`` measured no claim.
    assert by_type["single-session-user"]["points_off_neutral"] is None
    assert by_type["single-session-assistant"]["points_off_neutral"] is None
    assert by_type["single-session-user"]["points_moved_off_neutral"] is None
    assert sum(b["points_off_neutral"] for b in by_type.values()
               if b["points_off_neutral"] is not None) == \
        summary["points_off_neutral"]
    assert sum(b["points_moved_off_neutral"] for b in by_type.values()
               if b["points_moved_off_neutral"] is not None) == \
        summary["points_moved_off_neutral"]
    # the per-type counter is named apart from the run-level ``measured``
    # (which counts MANIFESTS, a different population — #10137 review)
    #
    # #10137 review 14 (P2): the third term is REQUIRED. A measured question
    # whose contribution census was never read belongs to NEITHER ``off_neutral``
    # nor ``neutral`` — it is ``questions_contribution_unknown`` — so the old
    # two-term form was an identity the module does not hold. Asserted over
    # ``questions_contribution_unknown`` here and as a full partition below, so
    # the fifth population the README names is pinned rather than assumed.
    assert sum(b["questions_measured"] for b in by_type.values()) == \
        summary["questions_off_neutral"] + summary["questions_neutral"] \
        + summary["questions_contribution_unknown"]
    assert sum(b["questions_contribution_unknown"] for b in by_type.values()) \
        == summary["questions_contribution_unknown"]
    # every question lands in exactly one of the four populations
    assert (summary["questions_inert"] + summary["questions_neutral"]
            + summary["questions_off_neutral"]
            + summary["questions_contribution_unknown"]) == summary["questions"]


def test_populations_are_order_independent():
    """A resumed/reshuffled outcome list must produce the same block — the
    enumeration is sorted, never insertion-ordered."""
    rows = [
        {"question_id": "q3", "question_type": "b",
         "belief_activation": _unmeasured_manifest(factor_edges=0, operators=0)},
        {"question_id": "q1", "question_type": "a",
         "belief_activation": _census(points_off_neutral=0)},
        {"question_id": "q2", "question_type": "a",
         "belief_activation": _census(points_off_neutral=2)},
        {"question_id": "q4", "question_type": "b"},          # no manifest
    ]
    forward = mp.summarize_belief_activation(rows, requested=True)
    backward = mp.summarize_belief_activation(rows[::-1], requested=True)
    assert forward == backward
    assert [q["question_id"]
            for q in forward["neutral_questions"]] == ["q1"]
    assert [q["question_id"]
            for q in forward["inert_questions"]] == ["q3", "q4"]


def test_the_new_cells_are_additive_and_ride_onto_the_posture():
    """``build()`` carries the whole summary, so the populations reach the
    report's ``methodology.measurement_posture`` without a second wiring."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation":
          _census(points_off_neutral=4)}], requested=True)
    posture = mp.build(ingest_mode="v2", activation=summary)
    activation = posture["activation"]
    for key in ("operators", "points_off_neutral", "questions_off_neutral",
                "questions_neutral", "neutral_questions", "questions_inert",
                "inert_questions", "inert_by_reason", "by_question_type"):
        assert key in activation
    assert activation["points_off_neutral"] == 4
    assert activation["operators"] == 3
    # the pre-existing keys are untouched
    assert activation["questions_with_ep"] == 1
    assert posture["beliefs_activated"] is True


def test_the_structural_causes_are_named_apart_from_the_arm_causes():
    """Indicator (c) of #10137 needs the distinction at a glance: which inert
    questions could be fixed by EXTRACTION (no operator exists at all — no
    arm, promotion or pass can help) and which are arm-side (the factors
    exist and the pass still wrote nothing). The reason vocabulary is closed
    and `INERT_REASONS_STRUCTURAL` names the structural subset, so the
    published population is consumable without re-deriving the mapping."""
    assert frozenset({mp.INERT_NO_OPERATORS}) == mp.INERT_REASONS_STRUCTURAL
    assert mp.INERT_REASONS_STRUCTURAL < mp.INERT_REASONS  # a strict subset

    structural = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation":
          _unmeasured_manifest(factor_edges=0, operators=0)}], requested=True)
    arm_side = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation":
          _unmeasured_manifest(operators=3, no_op_reason="no_ep_factors")}],
        requested=True)
    assert structural["inert_questions"][0]["reason"] in \
        mp.INERT_REASONS_STRUCTURAL
    assert arm_side["inert_questions"][0]["reason"] not in \
        mp.INERT_REASONS_STRUCTURAL


# ── #10137 review fixes ───────────────────────────────────────────────────

def test_an_unknown_edge_census_never_fabricates_a_structural_cause():
    """`operators == 0` alone does NOT make a graph structurally EP-inert: an
    IMPL/NAND straight between two claims is a factor with no operator node
    (#888 W5). The STRUCTURAL verdict needs BOTH censuses to be a MEASURED
    zero, so an UNKNOWN edge census must fall through, not assert it."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1",
          # operator count measured as 0, edge census never read
          "belief_activation": _unmeasured_manifest(operators=0)}],
        requested=True)
    assert summary["inert_questions"][0]["reason"] == mp.INERT_UNMEASURED


def test_operators_zero_with_direct_factor_edges_is_not_structural():
    """The other half: an operator-less graph that DOES carry direct IMPL/NAND
    edges has factors, so calling it structural would be a false cause."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1",
          "belief_activation": _unmeasured_manifest(
              operators=0, factor_edges=3, no_op_reason="no_ep_factors")}],
        requested=True)
    assert summary["inert_questions"][0]["reason"] == mp.INERT_NO_EP_FACTORS
    assert summary["inert_questions"][0]["reason"] not in \
        mp.INERT_REASONS_STRUCTURAL


def test_a_failed_question_never_contributes_to_the_run_level_census():
    """The published ``points_off_neutral`` is derived from the CLASSIFIED
    population, so a question whose belief layer failed cannot inflate it —
    the enumerated views and the run-level cell must be one number."""
    failed = {"ep_ran": False, "error": "boom", "points_off_neutral": 7,
              "operators": 2, "factor_edges": 2}
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": failed},
         {"question_id": "q2", "belief_activation": _census(
             points_off_neutral=4, points_moved_off_neutral=4)}],
        requested=True)
    assert summary["points_off_neutral"] == 4       # NOT 7 + 4
    assert summary["points_moved_off_neutral"] == 4
    assert sum(b["points_off_neutral"]
               for b in summary["by_question_type"].values()) == 4


def test_an_unknown_graph_census_is_published_as_unknown_not_zero():
    """`operators`/`factor_edges` are `None` when no manifest could read one —
    a fabricated 0 would read as "this graph mints no operators"."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _unmeasured_manifest()}],
        requested=True)
    assert summary["operators"] is None
    assert summary["factor_edges"] is None


def test_a_measured_run_publishes_both_off_neutral_cells_apart():
    """State and CONTRIBUTION are published separately: a claim that was
    already off the neutral mean before the pass is state, not contribution."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _census(
            points_off_neutral=6, points_moved_off_neutral=2)}],
        requested=True)
    assert summary["points_off_neutral"] == 6
    assert summary["points_moved_off_neutral"] == 2


def test_a_diagnosis_outside_the_closed_vocabulary_is_carried_not_dropped():
    """The reason vocabulary is a closed set a lane routes a fix on, but the
    SDK's own diagnosis must survive classification as `unmeasured` — it is
    carried verbatim on the enumerated entry, never silently discarded."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _unmeasured_manifest(
            operators=3, factor_edges=3, no_op_reason="budget_zero")}],
        requested=True)
    entry = summary["inert_questions"][0]
    assert entry["reason"] == mp.INERT_UNMEASURED
    assert entry["no_op_reason"] == "budget_zero"


# ── #10137 review round 2: UNKNOWN must not leak back into 0 ──────────────

def test_a_resumed_manifest_without_contribution_cells_is_never_neutral():
    """P0 regression. A checkpoint written BEFORE #10137 re-runs the arm and
    yields ``points_with_ep > 0`` with NO contribution cells — reachable
    without any exception, because the checkpoint fingerprint records only the
    ``activate_beliefs`` flag. Collapsing that UNKNOWN to 0 published the
    question in ``neutral_questions``, i.e. "the belief layer ran and moved
    nothing", for a census that was never read."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1",
          "belief_activation": {"ep_ran": True, "points_with_ep": 4}}],
        requested=True)
    assert summary["neutral_questions"] == []
    assert summary["questions_neutral"] == 0
    assert summary["points_off_neutral"] is None
    assert summary["points_moved_off_neutral"] is None
    assert summary["questions_contribution_unknown"] == 1
    assert summary["questions_with_ep"] == 1        # coverage IS measured


def test_an_unread_coverage_census_does_not_decide_the_posture():
    """A coverage census that could not be read is UNKNOWN — it must not be
    published as ``points_with_ep == 0``, which drives the per-question verdict
    and the run's ``partial`` posture.

    RED: the manifest below is the shape that MAKES THE ARM LOAD-BEARING. With
    ``operators`` and ``factor_edges`` both a MEASURED ``0``, the only thing
    standing between an unread coverage census and the STRUCTURAL verdict is
    the ``with_ep is None`` arm — collapse it to ``_as_int`` (so ``None`` reads
    as ``0``) and this question is published as ``no_operators``, "nothing EP
    can do will help", for a census that was never read. The earlier version of
    this test fed ``{"ep_ran": True}`` alone, which reaches ``unmeasured``
    through the operators-UNKNOWN fall-through under BOTH the real code and
    the mutant, so it could not fail (measured: the mutant stayed green).
    """
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": {
            "ep_ran": True, "operators": 0, "factor_edges": 0}}],
        requested=True)
    assert summary["inert_questions"][0]["reason"] == mp.INERT_UNMEASURED
    assert summary["neutral_questions"] == []
    # the coverage cell itself is UNKNOWN, not a fabricated 0
    assert summary["points_with_ep"] is None
    assert summary["points_with_ep_unread"] == 1


def test_an_all_inert_run_never_publishes_a_measured_zero_contribution():
    """RED: the run-level contribution cells read the SUMMED buckets, and an
    all-inert run has empty buckets — which ``bool(points) or not unread`` read
    as "measured, and it measured 0". So the exact run #10137 targets (every
    question EP-inert, nothing ever reads a contribution census) published
    ``points_off_neutral: 0``, a fabricated number printed by the very cell that
    exists to expose fabricated numbers.
    """
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _unmeasured_manifest(
            operators=0, factor_edges=0)},
         {"question_id": "q2", "belief_activation": {"ep_ran": False,
                                                     "error": "boom"}},
         {"question_id": "q3"}],               # no manifest at all
        requested=True)
    assert summary["questions_inert"] == 3
    assert summary["points_off_neutral"] is None
    assert summary["points_moved_off_neutral"] is None


def test_an_inert_only_question_type_never_publishes_a_measured_zero():
    """RED: the run-level cell was taught to publish ``None`` for a run that
    measured nothing, but the per-type bucket kept its INITIALISED ``0`` — so
    the type whose questions were never measured at all published
    ``points_off_neutral: 0, points_off_neutral_unread: 0`` (a measured zero,
    with nothing to say it was not one) while the run-level cell for the same
    evidence published ``null``. The per-type split is the deliverable #10137
    asks for ("split by question type"), so the same rule applies to it.
    """
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "question_type": "single-session-user",
          "belief_activation": _unmeasured_manifest(operators=0,
                                                    factor_edges=0)},
         {"question_id": "q2", "question_type": "single-session-user",
          "belief_activation": {"ep_ran": False, "error": "boom"}}],
        requested=True)
    bucket = summary["by_question_type"]["single-session-user"]
    assert bucket["questions_measured"] == 0
    assert bucket["points_off_neutral"] is None          # never measured
    assert bucket["points_moved_off_neutral"] is None    # never measured
    assert bucket["questions_contribution_unknown"] == 0  # nothing to read
    # ...and the run-level cell agrees, so the two views cannot disagree
    assert summary["points_off_neutral"] is None
    assert summary["points_moved_off_neutral"] is None


def test_a_measured_bucket_still_publishes_its_zero_beside_an_inert_one():
    """The control: a bucket that DID measure keeps its number, including a
    measured ``0`` — only the never-measured buckets are UNKNOWN, and the
    run-level sum covers the measured ones."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "i1", "question_type": "inert-type"},
         {"question_id": "m1", "question_type": "measured-type",
          "belief_activation": _census(points_off_neutral=0,
                                       points_moved_off_neutral=0)}],
        requested=True)
    by_type = summary["by_question_type"]
    assert by_type["inert-type"]["points_off_neutral"] is None
    assert by_type["measured-type"]["points_off_neutral"] == 0
    assert summary["points_off_neutral"] == 0
    assert summary["points_moved_off_neutral"] == 0


def test_the_run_level_cell_does_not_depend_on_the_question_type_split():
    """RED: an inert-only bucket arrived at the old predicate as
    ``points_off_neutral or not points_off_neutral_unread`` → ``0 or not 0`` →
    ``True``, so a bucket that measured NOTHING cast a vote for "this run was
    measured". One inert question was therefore enough to publish
    ``points_off_neutral: 0`` — beside its own
    ``questions_contribution_unknown: 1`` — for the same evidence that
    publishes ``null`` when the two questions happen to share a type.

    The published contribution figure must depend on the EVIDENCE, never on
    the shape of the ``by_question_type`` split (measured: every LongMemEval
    run carries several types).
    """
    resumed = {"question_id": "q1",
               "belief_activation": {"ep_ran": True, "points_with_ep": 4}}
    inert = {"question_id": "q2"}                     # no manifest at all
    one_type = mp.summarize_belief_activation(
        [{**resumed, "question_type": "single-session-user"},
         {**inert, "question_type": "single-session-user"}],
        requested=True)
    two_types = mp.summarize_belief_activation(
        [{**resumed, "question_type": "single-session-user"},
         {**inert, "question_type": "multi-session"}],
        requested=True)
    for summary in (one_type, two_types):
        assert summary["points_off_neutral"] is None
        assert summary["points_moved_off_neutral"] is None
        assert summary["points_off_neutral_unread"] == 1
        assert summary["questions_contribution_unknown"] == 1
    assert one_type["points_off_neutral"] == two_types["points_off_neutral"]


def test_a_measured_all_neutral_run_still_publishes_a_zero():
    """The control for the fix above: never publish ``0`` when nothing was
    read, but do not turn every zero into UNKNOWN either. A covered graph whose
    every posterior is the neutral mean DID measure zero contribution — that is
    the number #10137 is about, and it must stay a number."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _census(
            points_off_neutral=0, points_moved_off_neutral=0)}],
        requested=True)
    assert summary["questions_neutral"] == 1
    assert summary["points_off_neutral"] == 0
    assert summary["points_moved_off_neutral"] == 0


def test_an_unknown_coverage_census_is_never_summed_as_a_measured_zero():
    """RED: ``points_with_ep`` / ``points_unmeasured`` degrade to UNKNOWN when
    their read fails, and they are the cells the per-question verdict is built
    from. The summed family treated only the GRAPH census as UNKNOWN-able, so a
    failed coverage read was published as ``0`` — "0 claims carry EP state" —
    while the very same report classified that question ``unmeasured``.
    """
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _census(
            points_with_ep=None, points_unmeasured=None)},
         {"question_id": "q2", "belief_activation": _census(
            points_with_ep=4, points_unmeasured=1)}],
        requested=True)
    assert summary["points_with_ep"] == 4          # the MEASURED cells only
    assert summary["points_with_ep_unread"] == 1   # ...and it says how partial
    assert summary["points_unmeasured"] == 1
    assert summary["points_unmeasured_unread"] == 1
    assert summary["inert_questions"] == [
        {"question_id": "q1", "question_type": "",
         "reason": mp.INERT_UNMEASURED}]
    # nothing measured at all → UNKNOWN, not a complete-looking 0
    only_unknown = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _census(
            points_with_ep=None, points_unmeasured=None)}],
        requested=True)
    assert only_unknown["points_with_ep"] is None
    assert only_unknown["points_with_ep_unread"] == 1
    # ...and an ABSENT cell is UNKNOWN for the same reason as a ``None`` one:
    # the fail-open manifest carries no census because none was read, so
    # reading the omission as a measured ``0`` would be the same fabrication.
    fail_open = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": {"ep_ran": False,
                                                     "error": "boom"}}],
        requested=True)
    assert fail_open["points_with_ep"] is None
    assert fail_open["points_with_ep_unread"] == 1
    assert fail_open["points_unmeasured"] is None
    assert fail_open["points_unmeasured_unread"] == 1


def test_a_manifest_less_question_is_unread_not_absent_from_the_denominator():
    """RED: the ``*_unread`` counters iterated the MANIFEST-bearing subset, so a
    question with NO manifest — the ``no_manifest`` shape this module names
    itself (a checkpoint written before the arm existed, or under another arm)
    — was neither KNOWN nor UNREAD. A 3-question run whose census was read for
    ONE of them published ``operators: 7, operators_unread: 0``: a partial sum
    that claims to be complete, which is the invariant the block exists to
    keep. A missing manifest is an unread census, full stop.
    """
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _census(
            operators=7, factor_edges=2)},
         {"question_id": "q2"},               # RESUMED: no manifest at all
         {"question_id": "q3"}],
        requested=True)
    assert summary["operators"] == 7           # the measured cell only
    assert summary["operators_unread"] == 2    # ...and it says how partial
    assert summary["factor_edges_unread"] == 2
    assert summary["points_with_ep_unread"] == 2
    # a run with NO manifest anywhere is UNKNOWN beside a NON-ZERO unread
    # count — never "UNKNOWN and nothing unread", which reads as a whole
    # measurement that happened to be empty.
    empty = mp.summarize_belief_activation(
        [{"question_id": "q1"}, {"question_id": "q2"}], requested=True)
    assert empty["operators"] is None
    assert empty["operators_unread"] == 2
    assert empty["points_with_ep"] is None
    assert empty["points_with_ep_unread"] == 2


def test_a_manifest_less_question_is_unread_inside_its_question_type_too():
    """RED: the round-9 denominator fix reached the run-level loop only. The
    per-type bucket still guarded its ``*_unread`` counters on
    ``manifest is not None``, so a manifest-less question was counted in
    neither side INSIDE its type: a 3-question type whose census was read for 1
    published ``operators: 3, operators_unread: 0`` while the run-level cell for
    the same key said ``operators_unread: 2`` — the split stopped reconciling,
    and the type figure claimed a completeness it did not have.
    """
    manifest = _census(operators=3, factor_edges=0, points_with_ep=0,
                       points_unmeasured=3)
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "question_type": "A",
          "belief_activation": manifest},
         {"question_id": "q2", "question_type": "A"},   # RESUMED: no manifest
         {"question_id": "q3", "question_type": "A"}],
        requested=True)
    bucket = summary["by_question_type"]["A"]
    assert bucket["operators"] == 3
    assert bucket["operators_unread"] == 2
    assert bucket["factor_edges_unread"] == 2
    # ...and the split reconciles with the run-level cells
    assert summary["operators_unread"] == bucket["operators_unread"]
    assert summary["factor_edges_unread"] == bucket["factor_edges_unread"]


def test_a_partial_census_sum_says_how_partial_it_is():
    """A sum over the MEASURED cells is published, but never without the count
    of cells that were unread — otherwise a partial sum reads as a complete
    measurement."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": _unmeasured_manifest(
            operators=0, factor_edges=0)},
         {"question_id": "q2", "belief_activation": _unmeasured_manifest(
            no_op_reason="no_ep_factors")}],      # no census cells at all
        requested=True)
    assert summary["operators"] == 0
    assert summary["operators_unread"] == 1
    assert summary["factor_edges"] == 0
    assert summary["factor_edges_unread"] == 1


def test_a_boolean_coverage_cell_is_not_a_measured_question():
    """RED: `questions_with_ep` read the coverage cell with `_as_int`, which
    coerces a boolean to 1, while `_classify_question` reads the SAME manifest
    cell as UNKNOWN (the round-11 rule). One report therefore published
    `questions_with_ep: 1` and `belief_layer: activated` — `beliefs_activated:
    true`, the value the receipt gate reads — while also listing the question as
    inert with reason `unmeasured` and a bucket `questions_with_ep` of 0.

    A bool is not a count here either: the run measured nothing.
    """
    manifest = {"ep_ran": True, "points_with_ep": True, "points_unmeasured": 0,
                "operators": 2, "factor_edges": 0}
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "question_type": "multi-session",
          "belief_activation": manifest}], requested=True)

    assert summary["questions_with_ep"] == 0
    assert summary["by_question_type"]["multi-session"]["questions_with_ep"] == 0
    assert summary["inert_questions"][0]["reason"] == "unmeasured"
    block = mp.build(ingest_mode="v2", activation=summary)["belief_layer"]
    assert block != mp.BELIEF_LAYER_ACTIVATED


def test_a_pass_that_ran_AND_errored_is_not_a_measured_question_either():
    """RED: the run-level cell and the buckets spelled the ``measured``
    predicate SEPARATELY, and they disagreed on one shape.

    `_classify_question` checks `error` BEFORE the census, so a manifest
    carrying both `ep_ran: True` and `error` is `activation_failed` and never
    measured. The run-level cell checked only `ep_ran is True` plus the
    coverage cell, so the SAME manifest counted 1 there and 0 in the bucket —
    `sum(bucket) == run-level` (the invariant this block is built on) broken,
    and the inflated coverage cell is the number `belief_layer_state` derives
    `activated` from.

    No in-tree producer emits this shape (`run.py::_activate_beliefs_for_question`
    always pairs `error` with `ep_ran: False`), which is exactly why the pin has
    to be a fixture: the hazard is a FUTURE producer, and the fix is one
    predicate instead of two.
    """
    manifest = {"ep_ran": True, "points_with_ep": 2, "points_unmeasured": 0,
                "operators": 2, "factor_edges": 0, "error": "dream raised"}
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "question_type": "multi-session",
          "belief_activation": manifest}], requested=True)

    assert summary["questions_with_ep"] == 0
    assert summary["by_question_type"]["multi-session"]["questions_with_ep"] == 0
    assert sum(b["questions_with_ep"] for b in summary["by_question_type"].values()) \
        == summary["questions_with_ep"]
    assert summary["inert_questions"][0]["reason"] == "activation_failed"
    assert summary["failed"] == 1
    block = mp.build(ingest_mode="v2", activation=summary)["belief_layer"]
    assert block != mp.BELIEF_LAYER_ACTIVATED


def test_a_failed_pass_that_names_no_diagnosis_still_counts_as_failed():
    """RED: the run-level `failed` cell keyed on `error` alone, and the shape
    that key was blind to is the COMMON one.

    `ep_activation.activate_beliefs` used to return `ep_ran: False` with no
    `error` key when `dream` raised (review 14 adds the diagnosis there), while
    `_classify_question` resolved the same manifest from the `ep_ran` arm and
    called it `activation_failed`. So a run in which EVERY pass raised published
    `failed: 0`, `errors: []` — the one number a reader has for "did anything
    break" said nothing did — while `inert_by_reason` simultaneously reported
    every question as `activation_failed`.

    `failed`/`ep_ran` are now derived from the classifier's own arm, so the
    cells agree on this shape whether or not a producer fills `error` in, and
    `errors` states the shape instead of inventing a cause.
    """
    outcomes = [{"question_id": "q1", "belief_activation": {"ep_ran": False}},
                {"question_id": "q2", "belief_activation": {"ep_ran": False}},
                {"question_id": "q3", "belief_activation": _manifest()}]
    summary = mp.summarize_belief_activation(outcomes, requested=True)

    assert summary["measured"] == 3
    assert summary["failed"] == 2
    assert summary["ep_ran"] == 1
    # the partition closes by construction, not by two spellings agreeing
    assert summary["ep_ran"] + summary["failed"] == summary["measured"]
    assert summary["inert_by_reason"]["activation_failed"] == 2
    assert len(summary["errors"]) == 2
    assert all("no diagnosis was reported" in e for e in summary["errors"])
    # the failure is VISIBLE in the posture, and cannot read as activated
    posture = mp.build(ingest_mode="v2", activation=summary)
    assert posture["belief_layer"] == mp.BELIEF_LAYER_PARTIAL
    assert posture["beliefs_activated"] is False


def test_a_diagnosis_carrying_failure_reports_the_message_verbatim():
    """The other half: when `error` IS set, `errors` quotes it, not the shape."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1",
          "belief_activation": {"ep_ran": False,
                                "error": "DreamNoOpError: no ep factors"}}],
        requested=True)
    assert summary["failed"] == 1
    assert summary["errors"] == ["DreamNoOpError: no ep factors"]
