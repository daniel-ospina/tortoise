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
