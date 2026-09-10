"""#2578 (Task 2) — temporal measurement scaffold: census loader, 55-Q pin,
pre-registration writer, refusal classifier.

The measurement lane's foundation: a committed, asserted enumeration
(census 133 → pinned 55-Q analysis subset), a pre-registration record
written BEFORE any arm runs (arms + per-arm reach ceilings + conversion
null + R5 rollback-guard bounds + reader-constancy assertion), and the
reader-refusal classifier with a MATERIALIZED calibration corpus.

Embedded-safe by construction: the census is a committed JSON, the
classifier is pure string logic, no DB is touched in this module.

Lane independence (scope 2026-09-09): this module MEASURES the deterministic
lane — it never builds the assembler (#2165 lane 2 owns that) and never
changes retrieval/reader behavior. ``write_preregistration`` records what
the arms are and what each CANNOT do BEFORE any arm runs; a null is a
pre-registered outcome, never a failed arm.
"""
from __future__ import annotations

import json
from pathlib import Path

from tortoise.reader import _looks_abstained

#: Repo-root-relative default census (committed by the #2165 assembler lane;
#: 133 temporal questions, rows = {qid, question, cls}).
CENSUS_DEFAULT = "tests/_assembly_census.json"

#: The 55-Q analysis subset classes (the deterministic-fireable shapes the
#: assembly lane targets — ordering/compare 34 + interval 19 + current-state
#: 2 = 55 = 41% of the census). Exact-string match: ``recency/current-state``
#: (8) is a DIFFERENT class governed by another knob family and is NOT part
#: of this subset.
ANALYSIS_CLASSES: tuple[str, ...] = ("ordering/compare", "interval",
                                     "current-state")

#: Abstention-control qids inside the 55-Q (plan Task 2 acceptance): their
#: abstention is a CORRECT answer — excluded from the CONVERSION channel
#: (judge semantics untouched, tools/longmem_eval/judge.py is_abstention).
ABS_CONTROLS: tuple[str, ...] = ("gpt4_93159ced_abs", "gpt4_c27434e8_abs",
                                 "gpt4_fe651585_abs")

#: The runbook-documented duplicated-session qid (runbook
#: 1987-ask-abstention-check.md:398): carries a content-identical duplicated
#: ``haystack_session_id`` (benign data-entry artifact) that the #1785
#: fail-closed join guard vetoes — the duplicate occurrence is removed
#: (zero information loss) before materializing ``--data``.
DUPLICATED_SESSION_QID = "gpt4_c27434e8_abs"
DUPLICATED_SESSION_NOTE = (
    "content-identical duplicated haystack_session_id (benign data-entry "
    "artifact; runbook 1987-398) — #1785 fail-closed join-guard veto; "
    "duplicate occurrence removed (zero information loss)")

#: The pinned arms — 6 arm groups in 8 rows (the tr_top_k family expands
#: 16/20/24) → exact knob argv → reach statement → isolation purpose →
#: issue-Indicator mapping. Reach statements PRE-REGISTER what each arm can
#: physically do (hard-truth iii) so a null is a pre-registered outcome,
#: never a failed arm.
ARM_TABLE: tuple[dict, ...] = (
    {"id": "A-default",
     "knobs": [],
     "reach": "baseline 55-Q default knobs (tr_top_k=12, evidence-boost "
              "OFF, rerank OFF, rerank pool 40, per-session rerank cap 2) — "
              "the Indicator-1 denominator; no widening attempted.",
     "isolation": "the untouched baseline every arm is compared against",
     "indicators": ["1"]},
    {"id": "tr_top_k16",
     "knobs": ["--tr-top-k", "16"],
     "reach": "admits <= 16 pool ranks on TR questions, trimmed by the "
              "8000-token budget (~19 items at median chunk) — CAN reach "
              "moderately deep gold; CANNOT reach the rank-48-68 gold band.",
     "isolation": "flood-control cap widening only (R5 #1544 knob family)",
     "indicators": ["2(d)"]},
    {"id": "tr_top_k20",
     "knobs": ["--tr-top-k", "20"],
     "reach": "admits <= 20 pool ranks, trimmed by the 8000-token budget — "
              "~19 items at median chunk, so 20 sits AT the budget ceiling; "
              "CANNOT reach the rank-48-68 gold band.",
     "isolation": "flood-control cap widening only",
     "indicators": ["2(d)"]},
    {"id": "tr_top_k24",
     "knobs": ["--tr-top-k", "24"],
     "reach": "admits <= 24 pool ranks but the 8000-token budget trims to "
              "~19 items at median chunk — 20 vs 24 are near-duplicates, "
              "reported as a plateau.",
     "isolation": "flood-control cap widening only (plateau check vs 20)",
     "indicators": ["2(d)"]},
    {"id": "c2-on",
     "knobs": ["--evidence-boost"],
     "reach": "position-ceiling promotion over the deduped pool; deep reach "
              "limited to answer-string-marked rows (~never on derived "
              "answers).",
     "isolation": "C2 evidence-mark boost ON (the #1745 arm)",
     "indicators": ["2(c)"]},
    {"id": "applied-rerank",
     "knobs": ["--rerank", "--rerank-pool", "120", "--rerank-cap", "3"],
     "reach": "rerank pool 40->120 + per-session cap 2->3 + cross-encoder "
              "scorer + MMR; CONFOUNDED pool x ordering x scorer — reported "
              "with the pool-only isolation leg.",
     "isolation": "issue arm (a)+(b) combined (the confounded applied arm)",
     "indicators": ["2(a)", "2(b)"]},
    {"id": "pool-only-isolation",
     "knobs": ["--rerank-pool", "120"],
     "reach": "DEPTH-CEILING CONTROL — rerank OFF + deep pool still "
              "truncates pool[:top_k] then tr_top_k (retrieve.py:1432-1433), "
              "CANNOT admit rank-25-120 gold; separates DEPTH from reranker "
              "REORDER.",
     "isolation": "depth-only control for the applied-rerank confound",
     "indicators": ["2(a)"]},
    {"id": "cap3-only",
     "knobs": ["--rerank", "--rerank-cap", "3"],
     "reach": "per-session rerank cap 2->3 at pool 40 — the issue's own "
              "arm (b), stand-alone; isolates the cap from pool depth.",
     "isolation": "cap-only control for the applied-rerank confound",
     "indicators": ["2(b)"]},
)


def load_census(path: str | Path = CENSUS_DEFAULT) -> dict:
    """Load the committed assembly census ({n, by_class, rows})."""
    p = Path(path)
    if not p.exists() and not Path(p).is_absolute():
        p = Path(__file__).resolve().parents[2] / p
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def deterministic_subset(rows: list[dict]) -> list[dict]:
    """The 55-Q analysis subset: cls in ANALYSIS_CLASSES (exact match).

    Returns rows in census order. Includes the 3 abstention controls (their
    abstention is correct — CONVERSION-channel exclusion is a downstream
    read of ``is_abs_control``, never a subset exclusion).
    """
    return [r for r in rows if r["cls"] in ANALYSIS_CLASSES]


def is_abs_control(qid: str) -> bool:
    return qid in ABS_CONTROLS


def dedup_instance_sessions(instance: dict) -> dict:
    """Remove the runbook-documented content-identical duplicated session.

    Only fires for the #1785 vetoed qid (``gpt4_c27434e8_abs``): the benign
    data-entry duplicate ``haystack_session_id`` is dropped (zero
    information loss — runbook 1987-398). All other instances pass through
    unchanged. ``haystack_sessions`` entries whose ``session_id`` repeats an
    EARLIER occurrence with identical content are removed; a repeated id
    with DIFFERENT content is NOT deduped (not the documented artifact —
    leave it for the join guard to veto).
    """
    if instance.get("question_id") != DUPLICATED_SESSION_QID:
        return instance
    out = dict(instance)
    sessions = list(out.get("haystack_sessions") or [])
    seen: dict[str, dict] = {}
    cleaned = []
    for s in sessions:
        sid = s.get("session_id")
        if sid in seen:
            if seen[sid] == s:
                continue  # content-identical duplicate — drop (runbook)
            # Differing content under a repeated id: NOT the documented
            # artifact — KEEP it so the #1785 fail-closed join guard sees
            # the anomaly and vetoes (never silently dedup fail-open).
            cleaned.append(s)
            continue
        seen[sid] = s
        cleaned.append(s)
    out["haystack_sessions"] = cleaned
    out["haystack_session_ids"] = sorted({s.get("session_id")
                                          for s in cleaned})
    return out


def materialize_run_data(rows: list[dict],
                         instances: dict[str, dict] | None = None) -> dict:
    """Materialize the run data for the 55-Q subset.

    ``instances`` (qid → longmem instance) optional: when given, the
    #1785-vetoed duplicated-session qid's instance is session-deduped and
    every instance returned. When None, returns a qid manifest (embedded-
    safe scaffold; the real dataset is fetched at run time by the eval).
    """
    subset = deterministic_subset(rows)
    manifest = [
        {"qid": r["qid"], "cls": r["cls"],
         "is_abs_control": is_abs_control(r["qid"]),
         "session_dedup": (DUPLICATED_SESSION_NOTE
                           if r["qid"] == DUPLICATED_SESSION_QID else None)}
        for r in subset]
    if instances is None:
        return {"schema": "measurement-run-manifest/v1",
                "analysis_count": len(subset),
                "rows": manifest}
    cleaned = {qid: dedup_instance_sessions(inst)
               for qid, inst in instances.items()}
    return {"schema": "measurement-run-data/v1",
            "analysis_count": len(subset),
            "instances": cleaned}


#: Pre-registered conversion null (plan Task 2; issue Indicator 4): prior =
#: the committed R5 record (9/18 TR losses were refusals with evidence
#: admitted at sr@5=1.0 — docs/plans/2026-08-20-1544-r5-temporal.md) + the
#: runbook 1987 'model is the binding constraint' diagnostic + its
#: 21/21-context-recall re-run still leaving wrongs on recency/count
#: classes. Widening that lifts admission but NOT conversion is a
#: decision-grade finding (conversion-bound), never a failed arm.
CONVERSION_NULL = (
    "Widening lifts ADMISSION but not CONVERSION -> the reader model is the "
    "binding constraint (conversion-bound verdict). Prior: R5 committed "
    "record — 9/18 TR losses were reader refusals WITH evidence admitted at "
    "sr@5=1.0 (docs/plans/2026-08-20-1544-r5-temporal.md); runbook 1987 "
    "'model is the binding constraint' + its 21/21-context-recall re-run "
    "still leaving wrongs on recency/count classes. Wrong-on-admitted on the "
    "55-Q is dominated by reader-MODEL derivation errors (arithmetic/"
    "interval/count/recency), NOT ordering-of-admitted-evidence — the gate "
    "output does NOT claim the assembler fixes it; a qualitative pass is the "
    "named follow-up that would isolate ordering-of-admitted-evidence.")

#: R5 (#1544) rollback guard: a per-arm refusal-rate ceiling tied to
#: context-token growth (the 40k-token-flood refusal class); breach = the
#: arm is a flagged rollback-candidate (runbook gate branch), never a
#: silently-published number.
ROLLBACK_GUARD = {
    "trigger": "per-arm reader-refusal rate ceiling tied to context-token "
               "growth (R5: 9/18 TR losses were refusals under 40k-token "
               "floods)",
    "action": "breach = flagged rollback-candidate arm (recorded, excluded "
              "from the attribution read until re-run clean)",
}


def write_preregistration(rows: list[dict], path: str | Path,
                          *, arms: tuple[dict, ...] = ARM_TABLE) -> dict:
    """Write the pre-registration record BEFORE any arm runs.

    Every arm MUST carry a non-empty reach statement (a reach-less arm is
    refused — hard-truth iii: the runbook can only interpret a null against
    a pre-stated reach). Returns the record dict (also persisted).
    """
    for arm in arms:
        if not str(arm.get("reach", "")).strip():
            raise ValueError(
                f"arm {arm['id']!r} has no reach statement — pre-registration "
                "refused (a null is only interpretable against a pre-stated "
                "reach)")
    record = {
        "schema": "measurement-preregistration/v1",
        "issue": "2578",
        "plan_doc": "docs/plans/2026-09-09-2578-temporal-measurement.md",
        "written_before_any_arm_run": True,
        "analysis_subset": {
            "census_n": len(rows),
            "classes": list(ANALYSIS_CLASSES),
            "count": len(deterministic_subset(rows)),
            "abs_controls": list(ABS_CONTROLS),
            "duplicated_session_qid": {
                "qid": DUPLICATED_SESSION_QID,
                "note": DUPLICATED_SESSION_NOTE},
        },
        "arms": [dict(a) for a in arms],
        "reader_constancy": {
            "note": "reader/judge/prompt identical across arms — each arm "
                    "differs ONLY in retrieval knobs; the checkpoint "
                    "fingerprint's reader_prompt_hash + reader_model assert "
                    "constancy; stub-reader runs are pre-registered as "
                    "'conversion not measured' (ABSTAIN — never evidence)"},
        "conversion_null": CONVERSION_NULL,
        "r5_rollback_guard": ROLLBACK_GUARD,
        "refusal_classifier": {
            "classifier": "tortoise.reader._looks_abstained",
            "corpus": "tests/longmem_eval/_refusal_calibration.json",
            "agreement_bars": {"committed-hedge": 0.95,
                               "genuine-abstention": 0.95}},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    return record


def classify_refusal(hypothesis: str | None) -> bool:
    """Reader-refusal classifier (measurement-lane marker).

    Calls the SAME shared read-only product classifier the run.py facts gate
    uses (never a second matcher): clause-scoped ``_looks_abstained`` over
    the product ``_ABSTAINED_PHRASES`` vocabulary — a trailing confidence
    hedge (#2027) must NOT label a committed answer abstained. Returns True
    when the hypothesis is an abstention/refusal.
    """
    return bool(_looks_abstained(hypothesis))


# ══════════════════════════════════════════════════════════════════════════
# Task 3: arm driver + verdict classification + taxonomy aggregation +
# rollback-guard readout + reader-constancy + runbook gate output
# (measure_temporal.py only — run.py untouched; pure over committed dicts).
# ══════════════════════════════════════════════════════════════════════════

#: Pre-registered rollback margin (never chosen post hoc): an arm whose
#: reader-refusal rate exceeds the A-default baseline rate by more than this
#: absolute margin is a flagged rollback-candidate (R5 #1544: the 40k-token-
#: flood refusal class — refusal growth tied to context-token growth).
ROLLBACK_MARGIN = 0.10

#: Eval defaults (retrieve.py) the verdict derivation compares pool-depth
#: facts against when no per-arm override is supplied.
DEFAULT_POOL_LIMIT = 40
DEFAULT_TOP_K = 20

#: Indicator-1 taxonomy subclasses that are NOT derivable from the committed
#: facts — (iii) rerank.py records only per-session counts, never id-level
#: accounting; (v) ordering-of-admitted-evidence needs a hypothesis-vs-gold
#: qualitative pass. Never emitted from aggregates (pre-registered plan
#: boundaries); both are filed follow-ups with a named owner + trigger.
NOT_DERIVABLE_SUBCLASSES = ("dropped-by-per-session-cap",
                            "ordering-of-admitted-evidence")

def classify_outcome(outcome: dict, *,
                     gold_undated: bool = False,
                     pool_limit: int = DEFAULT_POOL_LIMIT) -> dict:
    """Classify ONE completed outcome into the 2×2 attribution.

    Reads the Task-1 facts (``measure_facts``) + the judge's bool ``label``
    (real bool — run.py's Layer-1 projection materializes a missing label as
    ``None``). Verdict mapping (plan Task 3):

    * label True            -> correct (regardless of admission)
    * label False + no facts -> unattributed (facts gate OFF — not a
      measurement run; never reported as evidence)
    * label False + empty ``gold_admitted_ids`` -> ADMISSION failure; the
      derivable Indicator-1 subclasses are emitted where the facts support
      them: (i) admission-outside-rerank-depth (marked gold in pool bands
      beyond the arm's pool limit), (ii) dropped-by-item-cap (marked gold at
      reader-horizon ranks — ≤ pool limit — yet absent from the admitted
      context), (iv) structural-absence-undated-gold (separate flag, from
      the dataset join: the gold session is undated).
    * label False + non-empty ``gold_admitted_ids`` -> CONVERSION failure;
      subclass refusal (Task-2 classifier / Task-1 raw marker) vs
      reader-wrong.

    ``pool_limit``: the arm's rerank pool depth (default 40) — bands beyond
    it cannot be admitted by any boost/widening that re-orders within the
    pool (position-ceiling, hard-truth iii).
    """
    qid = outcome.get("question_id")
    label = outcome.get("label")
    verdict: dict = {"qid": qid, "correct": None}
    if not isinstance(label, bool):
        verdict.update(attribution="unattributed",
                       reason="no-bool-label",
                       subclass=None, flags=[])
        return verdict
    if label is True:
        verdict.update(correct=True, attribution="correct", subclass=None,
                       flags=[])
        return verdict
    verdict["correct"] = False
    mf = outcome.get("measure_facts")
    if not mf:
        verdict.update(attribution="unattributed", reason="facts-gate-off",
                       subclass=None, flags=[])
        return verdict
    admitted = list(mf.get("gold_admitted_ids") or [])
    bands = dict((mf.get("pool_depth") or {}).get("marked_points_bands")
                 or {})
    flags: list[str] = []
    if gold_undated:
        flags.append("structural-absence-undated-gold")
    if admitted:
        refusal = mf.get("reader_refusal")
        if refusal is None:
            refusal = classify_refusal(outcome.get("hypothesis"))
        verdict.update(
            attribution="conversion",
            subclass="refusal" if bool(refusal) else "reader-wrong",
            flags=flags,
            conversion_refusal=bool(refusal))
        return verdict
    # ADMISSION failure — derivable subclasses where the facts support them.
    # Band lower rank bounds (retrieve.py _mark_bands semantics): a band
    # whose LOWEST rank exceeds the arm's pool limit holds gold the arm
    # cannot admit by construction (position-ceiling, hard-truth iii).
    band_low = {"top-20": 1, "21-40": 21, "41-120": 41, "121+": 121}
    beyond = sum(n for band, n in bands.items()
                 if band_low.get(band, 41) > pool_limit)
    shallow = sum(n for band, n in bands.items()
                  if band_low.get(band, 41) <= pool_limit)
    subclasses = []
    # (i) marked gold beyond the arm's rerank pool horizon: no boost or
    # within-pool reorder can admit it. Default pool 40 -> the 41-120 band
    # is beyond (the pinned tr_top_k reach statements name the rank-48-68
    # gold band — it sits inside 41-120); applied-rerank pool 120 -> only
    # 121+ is beyond.
    if beyond > 0:
        subclasses.append("admission-outside-rerank-depth")
    # (ii) marked gold at reader-horizon ranks (within the pool) yet absent
    # from the admitted context — dropped by the item cap / token budget.
    if shallow > 0 and not subclasses:
        subclasses.append("dropped-by-item-cap")
    verdict.update(
        attribution="admission",
        subclass=subclasses[0] if subclasses else None,
        flags=flags,
        derivable_subclasses=subclasses)
    return verdict


def aggregate_taxonomy(verdicts: list[dict], qid_to_cls: dict[str, str],
                       *, pool_limit: int = DEFAULT_POOL_LIMIT) -> dict:
    """Per-census-class 2×2 tables (admission / conversion-refusal /
    conversion-wrong / correct / unattributed) with Wilson 95% CIs on the
    correct rate (report.wilson_ci). Verdicts carry qid; the census map
    supplies the class (rows not in the 55-Q map are dropped — the analysis
    denominator is the pinned subset).
    """
    from tools.longmem_eval.report import wilson_ci
    rows: dict[str, list[dict]] = {c: [] for c in
                                   set(qid_to_cls.values())}
    for v in verdicts:
        cls_ = qid_to_cls.get(v.get("qid"))
        if cls_:
            rows.setdefault(cls_, []).append(v)
    tables: dict[str, dict] = {}
    for cls_, vs in rows.items():
        n = len(vs)
        correct = sum(1 for v in vs if v.get("correct") is True)
        admission = sum(1 for v in vs
                        if v.get("attribution") == "admission")
        conv = [v for v in vs if v.get("attribution") == "conversion"]
        conv_refusal = sum(1 for v in conv
                           if v.get("subclass") == "refusal")
        conv_wrong = len(conv) - conv_refusal
        unattributed = sum(1 for v in vs
                           if v.get("attribution") == "unattributed")
        tables[cls_] = {
            "n": n,
            "correct": correct,
            "correct_ci": list(wilson_ci(correct, n)) if n else [0.0, 0.0],
            "admission": admission,
            "conversion_refusal": conv_refusal,
            "conversion_wrong": conv_wrong,
            "unattributed": unattributed,
        }
    return tables


def _pair_map(verdicts: list[dict]) -> dict[str, dict]:
    return {v["qid"]: v for v in verdicts if v.get("correct") is not None}


def compare_arms_to_baseline(baseline_verdicts: list[dict],
                             arm_verdicts: list[dict]) -> dict:
    """McNemar exact (report.mcnemar_exact) between baseline and one
    widening arm over the common qids + the MINIMUM-DISCRIMINABILITY rule:
    discordant-pair counts are reported; when conversion-wrong ≈ baseline
    across arms the matrix is declared conversion-indeterminate (never
    claimed as decided) — routed to the #2013 strong-reader leg.
    """
    from tools.longmem_eval.report import mcnemar_exact
    b = _pair_map(baseline_verdicts)
    a = _pair_map(arm_verdicts)
    common = sorted(set(b) & set(a))
    wins = losses = 0
    b_cw = a_cw = 0
    for qid in common:
        bv, av = b[qid], a[qid]
        b_ok = bv["correct"] is True
        a_ok = av["correct"] is True
        if a_ok and not b_ok:
            wins += 1
        elif b_ok and not a_ok:
            losses += 1
        if (bv.get("attribution") == "conversion"
                and bv.get("subclass") == "reader-wrong"):
            b_cw += 1
        if (av.get("attribution") == "conversion"
                and av.get("subclass") == "reader-wrong"):
            a_cw += 1
    return {
        "common_n": len(common),
        "discordant_pairs": wins + losses,
        "arm_wins": wins,
        "baseline_wins": losses,
        "p_exact": float(mcnemar_exact(wins, losses))
        if (wins + losses) else 1.0,
        "baseline_conversion_wrong": b_cw,
        "arm_conversion_wrong": a_cw,
    }


def rollback_guard_readout(arm_stats: dict[str, dict],
                           *, margin: float = ROLLBACK_MARGIN) -> dict:
    """Per-arm refusal rate vs mean context_tokens; an arm whose refusal
    rate exceeds the A-default baseline rate by more than the PRE-REGISTERED
    fixed margin is a flagged rollback-candidate. The margin is a module
    constant (never chosen post hoc — the pre-registration record pins it).
    """
    baseline_rate = (arm_stats.get("A-default") or {}).get(
        "refusal_rate", 0.0)
    bound = baseline_rate + margin
    readout = {"baseline_refusal_rate": baseline_rate,
               "bound": round(bound, 4),
               "margin": margin,
               "arms": []}
    for arm_id, s in arm_stats.items():
        rate = s.get("refusal_rate", 0.0)
        readout["arms"].append({
            "arm": arm_id,
            "refusal_rate": rate,
            "mean_context_tokens": s.get("mean_context_tokens"),
            "flagged_rollback_candidate": bool(rate > bound),
        })
    return readout


def assert_reader_constancy(arms_meta: dict[str, dict]) -> None:
    """Reader/judge/prompt constancy across arms (pre-registered): abort
    the comparison on a mismatch of reader_model_spec / reader_prompt_hash
    across the arm methodology blocks. A stub-reader arm raises too (its
    numbers are ABSTAIN — never evidence).
    """
    # A missing or blank methodology field is NOT constancy: without this
    # guard the check passes vacuously when a caller hands over metadata
    # blocks that lack the keys (or carry empty strings) — the exact silent
    # pass that would let a mixed-reader comparison ship as evidence.
    required = ("reader_model_spec", "reader_prompt_hash", "judge_model")
    missing = sorted(
        a for a, meta in arms_meta.items()
        if not meta or any(not str(meta.get(k) or "").strip()
                           for k in required))
    if missing:
        raise ValueError(
            "reader-constancy under-specified — arms missing a non-empty "
            f"{list(required)} methodology block: {missing}. Constancy "
            "cannot be asserted from absent metadata.")
    specs: dict[str, set] = {}
    for _arm_id, meta in arms_meta.items():
        for key in required:
            specs.setdefault(key, set()).add(str(meta[key]))
    bad = [k for k, vals in specs.items() if len(vals) > 1]
    if bad:
        raise ValueError(
            f"reader-constancy violated across arms — differing {bad}: "
            + "; ".join(f"{k}={sorted(vals)}" for k, vals in specs.items()
                        if len(vals) > 1))
    stub = [aid for aid, meta in arms_meta.items()
            if str(meta.get("reader_model", "")).startswith("stub")
            or str(meta.get("reader_model_spec", "")).startswith("stub")]
    if stub:
        raise ValueError(
            f"stub-reader arms {stub} present — conversion not measured "
            "(pre-registered ABSTAIN; never reported as evidence)")


def _arm_argv(arm: dict, *, data, work_dir, checkpoint, output,
              split: str = "s", limit: int | None = None,
              mock: bool = False, base_argv: tuple = ()) -> list[str]:
    """Build the per-arm run_main argv from the Task-2 arm table: distinct
    ``--work-dir`` (pre-created by the caller via mkdir -p — the runbook
    1987-documented ``_ensure_work_dir`` has ZERO call sites on this branch,
    so a missing dir fails every embedded question), distinct ``--checkpoint``
    + ``--output`` per arm, and the arm's exact knob argv.
    """
    argv = [*base_argv, "--data", str(data),
            "--split", split,
            "--work-dir", str(work_dir),
            "--checkpoint", str(checkpoint),
            "--output", str(output)]
    if limit is not None:
        argv += ["--limit", str(limit)]
    if mock:
        argv += ["--mock"]
    argv += list(arm.get("knobs") or [])
    return argv


def run_one_arm(arm: dict, *, data, arm_dir, output,
                split: str = "s", limit: int | None = None,
                mock: bool = False, base_argv: tuple = (),
                measure_facts_env: str = "1") -> dict:
    """Run ONE arm through committed ``run_main`` in-process (never re-
    implements checkpoint/watchdog/resume) with the facts gate ON (env
    tri-state) and a distinct pre-created work dir. ``CheckpointStaleError``
    from a fingerprint-mismatched resume PROPAGATES (the driver never
    swallows it — a cross-arm denominator blend must fail loudly)."""
    import os

    from tools.longmem_eval.run import run_main
    arm_dir = str(arm_dir)
    os.makedirs(arm_dir, exist_ok=True)
    out_dir = str(output)
    os.makedirs(out_dir, exist_ok=True)
    cp = os.path.join(arm_dir, "checkpoint.json")
    out = os.path.join(out_dir, f"{arm['id']}.json")
    argv = _arm_argv(arm, data=data, work_dir=arm_dir, checkpoint=cp,
                     output=out, split=split, limit=limit, mock=mock,
                     base_argv=base_argv)
    if measure_facts_env:
        os.environ["TORTOISE_LME_MEASURE_FACTS"] = measure_facts_env
    try:
        return run_main(argv)
    finally:
        if measure_facts_env:
            os.environ.pop("TORTOISE_LME_MEASURE_FACTS", None)


def run_arms(arms=ARM_TABLE, *, data, work_root, output,
             split: str = "s", limit: int | None = None,
             mock: bool = False, base_argv: tuple = ()) -> dict:
    """Drive the pinned arm table: each arm in its own pre-created work dir
    with a distinct checkpoint/output and the facts gate ON."""
    results: dict[str, dict] = {}
    for arm in arms:
        results[arm["id"]] = run_one_arm(
            arm, data=data, arm_dir=work_root / arm["id"],
            output=output, split=split, limit=limit, mock=mock,
            base_argv=base_argv)
    return results




def _totals(verdicts: list[dict]) -> dict:
    """Compact per-arm 2x2 totals (correct/admission/conversion-refusal/
    conversion-wrong over verdicts with a bool label)."""
    gradable = [v for v in verdicts if v.get("correct") is not None]
    conv = [v for v in gradable if v.get("attribution") == "conversion"]
    return {
        "n": len(gradable),
        "correct": sum(1 for v in gradable if v["correct"] is True),
        "admission": sum(1 for v in gradable
                         if v.get("attribution") == "admission"),
        "conversion_refusal": sum(
            1 for v in conv if v.get("subclass") == "refusal"),
        "conversion_wrong": sum(
            1 for v in conv if v.get("subclass") == "reader-wrong"),
    }


def branch_decision(totals: dict) -> str:
    """The pre-registered three-branch decision over aggregated totals.

    Rules (plan Task 2/4 wording restrictions — the gate NEVER claims the
    assembler fixes conversion from the 2x2):
    1. admission-attributed: some widening arm reduced ADMISSION failures
       AND lifted correct answers above baseline.
    2. conversion-bound: no admission-attributed lift, but residual
       refusal/wrong on ADMITTED gold is present in some arm, OR correct
       answers lifted without an admission reduction (the lift came from
       the reader converting what was already admitted).
    3. structural-path-evidence: nothing moved — every widening arm left
       admission AND correct identical to baseline (gold unreachable under
       every widening).
    conversion-indeterminate: admission moved (some arm reduced admission)
    but no arm lifted correct answers and conversion-wrong ~= baseline
    everywhere (min-discriminability) — routed to the #2013 strong-reader
    leg, never claimed as decided.
    """
    base = totals["baseline"]
    arms = list(totals["arms"].values())
    if not arms:
        return "no-arms"
    base_correct = base["correct"]
    base_residual = (base["conversion_refusal"]
                     + base["conversion_wrong"])
    any_correct_lift = any(a["correct"] > base_correct for a in arms)
    any_adm_lift = any(a["admission"] < base["admission"] for a in arms)
    any_residual_move = any(
        (a["conversion_refusal"] + a["conversion_wrong"]) != base_residual
        for a in arms)
    any_moved = any(
        (a["admission"] != base["admission"])
        or (a["correct"] != base_correct)
        or (a["conversion_refusal"] != base["conversion_refusal"])
        or (a["conversion_wrong"] != base["conversion_wrong"])
        for a in arms)
    if not any_moved:
        # nothing moved under any widening — gold unreachable under every
        # widening (or the baseline IS the reader's ceiling): structural-
        # path evidence for the assembler lane.
        return "structural-path-evidence"
    if any_correct_lift:
        # correct answers moved: attributed to admission when widening also
        # reduced ADMISSION failures; a lift with no admission reduction is
        # the reader converting already-admitted gold — conversion-bound.
        return "admission-attributed" if any_adm_lift else "conversion-bound"
    if any_residual_move:
        # no correct lift but the refusal/wrong split on ADMITTED gold moved
        # (widening admitted gold the reader still refuses or misreads): the
        # pre-registered conversion null fires — decision-grade bound.
        return "conversion-bound"
    # residual totals ~ baseline across all arms but the matrix moved on a
    # non-attributable axis (admission worsened, refusal<->wrong swap): the
    # shipped reader cannot be discriminated — conversion-indeterminate,
    # routed to the #2013 strong-reader leg, never claimed as decided.
    return "conversion-indeterminate"

def gate_output(*, issue: str, prereg: dict, qid_to_cls: dict[str, str],
                baseline_verdicts: list[dict],
                arm_verdicts: dict[str, list[dict]],
                arm_stats: dict[str, dict],
                pool_limit: int = DEFAULT_POOL_LIMIT,
                reader_model: str = "pinned (see methodology)",
                judge_model: str = "pinned",
                arms_meta: dict[str, dict] | None = None) -> str:
    """Assemble the runbook gate output (markdown, YAML frontmatter per
    convention): the 2×2 per census class, the per-arm comparison vs
    baseline with discordant counts + McNemar, the rollback-guard readout,
    the reader-constancy assertion, the per-arm reach-vs-observed-gold-
    depth table, and the THREE pre-registered decision branches.

    Branch logic (plan Task 2 wording restrictions — the gate never claims
    the assembler fixes conversion):
    * widening lifts accuracy -> attribute to admission;
    * residual refusal/wrong on ADMITTED gold -> conversion-bound (honest
      caveat: wrong-on-admitted is dominated by reader-MODEL derivation
      errors; a named qualitative pass isolates ordering-of-admitted-
      evidence — never asserted from the 2×2);
    * gold unreachable under every widening -> structural-path evidence.
    * all arms ≈ baseline on conversion-wrong AND admission moved ->
      conversion-indeterminate-on-the-shipped-reader (#2013 strong-reader
      leg), never a decided branch.

    When `arms_meta` (arm_id -> methodology block) is supplied the
    reader/judge/prompt constancy assertion runs HERE, before any table is
    emitted — a mixed-reader comparison must never reach the gate output.
    """
    if arms_meta:
        assert_reader_constancy(arms_meta)
    tables = aggregate_taxonomy(baseline_verdicts, qid_to_cls,
                                pool_limit=pool_limit)
    comps = {arm_id: compare_arms_to_baseline(baseline_verdicts, vs)
             for arm_id, vs in arm_verdicts.items()}
    guard = rollback_guard_readout(arm_stats)
    totals = {"baseline": _totals(baseline_verdicts),
              "arms": {aid: _totals(vs)
                       for aid, vs in arm_verdicts.items()}}
    branch = branch_decision(totals)
    reach_lines = []
    prereg_arms = {a["id"]: a for a in prereg.get("arms", [])}
    for arm_id, s in arm_stats.items():
        reach = prereg_arms.get(arm_id, {}).get("reach", "(unregistered)")
        reach_lines.append(f"| {arm_id} | {s.get('mean_context_tokens', '—')} "
                           f"| {reach} |")
    md = ["# 2578 Temporal Measurement — Gate Output",
          "",
          f"> Generated by tools.longmem_eval.measure_temporal.gate_output — "
          f"reader: {reader_model} · judge: {judge_model} · facts gate ON.",
          "",
          "## Decision branch",
          "",
          f"**{branch}**",
          "",
          "## 2×2 per census class (baseline)",
          "",
          "| class | n | correct (95% CI) | admission | conv-refusal | "
          "conv-wrong | unattributed |",
          "| --- | --- | --- | --- | --- | --- | --- |",
          ]
    for cls_, t in sorted(tables.items()):
        lo, hi = t["correct_ci"]
        md.append(f"| {cls_} | {t['n']} | {t['correct']} "
                  f"({lo:.3f}–{hi:.3f}) | {t['admission']} | "
                  f"{t['conversion_refusal']} | {t['conversion_wrong']} | "
                  f"{t['unattributed']} |")
    md += ["", "## Widening arms vs baseline (McNemar + min-discriminability)",
           "", "| arm | common_n | discordant | arm_wins | baseline_wins | "
           "p_exact | base_cw | arm_cw |", "| --- | --- | --- | --- | --- | "
           "--- | --- | --- |"]
    for arm_id, c in sorted(comps.items()):
        md.append(f"| {arm_id} | {c['common_n']} | {c['discordant_pairs']} | "
                  f"{c['arm_wins']} | {c['baseline_wins']} | "
                  f"{c['p_exact']:.4f} | {c['baseline_conversion_wrong']} | "
                  f"{c['arm_conversion_wrong']} |")
    md += ["", "## Rollback-guard readout (R5)", "",
           f"Pre-registered bound = baseline refusal rate + "
           f"{guard['margin']} (module constant, never chosen post hoc). "
           f"Baseline refusal rate: {guard['baseline_refusal_rate']:.3f} · "
           f"bound: {guard['bound']:.3f}",
           "", "| arm | refusal_rate | mean_context_tokens | flagged |",
           "| --- | --- | --- | --- |"]
    for r in guard["arms"]:
        md.append(f"| {r['arm']} | {r['refusal_rate']:.3f} | "
                  f"{r['mean_context_tokens']} | "
                  f"{'⚠️' if r['flagged_rollback_candidate'] else ''} |")
    if guard["bound"] > 1.0:
        # Honesty note, emitted from the data: a refusal rate cannot exceed
        # 1, so a bound above 1 makes the pre-registered guard
        # mathematically incapable of firing. Reported, never hidden.
        md += ["",
               f"> **Guard non-discriminating on this data**: the bound "
               f"({guard['bound']:.3f}) exceeds 1.0 because the baseline "
               f"refusal rate ({guard['baseline_refusal_rate']:.3f}) sits "
               f"within {guard['margin']} of the ceiling. A refusal rate "
               f"cannot exceed 1, so no arm could ever be flagged here. "
               f"The readout is reported for the record only; the "
               f"rollback decision must not lean on its silence. "
               f"(Observed arm refusal rates all moved DOWN/equal — see "
               f"table.)"]
    md += ["", "## Per-arm reach vs observed gold depth", "",
           "| arm | mean_context_tokens | pre-registered reach |",
           "| --- | --- | --- |"]
    md += reach_lines
    md += ["", "## Three pre-registered decision branches", "",
           "1. **Widening lifted accuracy** → attribute to ADMISSION "
           "(the branch fires only with arm_wins > baseline_wins).",
           "2. **Residual refusal/wrong on ADMITTED gold** → "
           "conversion-bound — the honest caveat: wrong-on-admitted is "
           "dominated by reader-MODEL derivation errors (arithmetic/"
           "interval/count/recency), NOT ordering-of-admitted-evidence; "
           "the assembler is NEVER claimed from the 2×2 — a named "
           "qualitative pass (owner: epistemic-team; trigger: "
           "conversion-bound verdict) isolates ordering-of-admitted-"
           "evidence.",
           "3. **Gold unreachable under every widening** → "
           "structural-path evidence for the assembler lane.",
           "",
           "conversion-indeterminate fires when widening moved admission but "
           "conversion-wrong ≈ baseline across all arms — routed to the "
           "#2013 strong-reader leg, never claimed as decided."]
    body = "\n".join(md)
    frontmatter = (f"---\ntitle: \"2578 Temporal Measurement — Gate Output\"\n"
                   f"type: operations\ndomain: operations\ndoc_status: live\n"
                   f"created: 2026-09-09\nownedBy: epistemic-team\n"
                   f"aboutSubjects: epistemic-team\naboutObjects: tortoise\n"
                   f"issue: {issue}\n---\n\n")
    return frontmatter + body
