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
    from tools.longmem_eval.w7_publish import build_receipt, validate_receipt

    def _receipt(**over):
        kwargs = dict(
            run_id="r", date="d", commit="c", corpus_hash="sha256:x",
            judge_pin="pin", judge_model="m", judge_rubric_id_hash="h",
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

    # a FAILED run is not asked for the disclosure (nothing is published)
    assert validate_receipt(_receipt(run_status="failed",
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
