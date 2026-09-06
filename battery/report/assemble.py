"""Profile assembler + LIVE report writers (issue #1415 plan §4/§5 + #2284
Task 5).

``report.assemble(run_artifacts, thresholds) → Profile``: the full
differentiation matrix (14 families × arms, classified), the verdict, the
matched-recall record, and report_status (complete | incomplete_* — never
fabricated). Profile schema per plan §4 (value types: numeric | enum | n/a).

Task 5 (issue #2284) — REPORT_STATUS_* home (cycle-1: NOT classify.py, the
leaf cell classifier; only the per-cell ``insufficient_n`` rule lives
there) + the LIVE aggregation path: run end writes one JSON per scored
family (``family_<F>.json``) + ``recall.json`` into the attempt dir
(tmp+os.replace — atomic), and the CLI report/calibrate resolve the LATEST
attempt dir via ``attempt_dir_resolve`` (dirs are filtered on
``summary.json`` presence — the completion marker; a crashed/cap-stopped
dir never shadows a prior complete attempt).

Run-level report_status precedence (composed from run_mode + summary
exit_code + per-episode statuses — never conflated with the mock status):
- mock runs ALWAYS stay ``incomplete_missing_metrics`` (mock never
  false-flags an emitter gap);
- a non-empty post-derivation emitter_gap on a REAL artifact →
  ``incomplete_emitter_gap``;
- a REAL EXCLUDED episode whose exclusion snapshot shows expected -
  emitted non-empty (round-4 P2 ``excluded_gap``) ALSO →
  ``incomplete_emitter_gap`` (the artifact-level exemption is retained —
  excluded artifacts carry no emitter_gap — but the run-level emitter
  state is surfaced so an honest exclusion can never hide an emitter that
  stopped covering expected fields);
- an over-budget stop → ``incomplete_real_over_budget``;
- a real run with zero measured cells (all-excluded/cap-stopped) →
  ``incomplete_real_no_episodes``;
- a real partial run (some measured, the rest excluded/insufficient) →
  ``incomplete_real_partial``;
- otherwise the base missing-family rule applies (complete only when every
  expected family measured).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from battery.report.classify import CellClassification, classify_cell
from battery.report.verdict import Verdict, decide_verdict

REPORT_STATUS_OK = "complete"
REPORT_STATUS_INCOMPLETE = "incomplete_missing_metrics"
#: Full run-level set (Task 5; emitter-gap/real-* statuses land here, NOT
#: in classify.py — classify holds only the per-cell insufficient_n rule).
REPORT_STATUS_EMITTER_GAP = "incomplete_emitter_gap"
REPORT_STATUS_REAL_NO_EPISODES = "incomplete_real_no_episodes"
REPORT_STATUS_REAL_PARTIAL = "incomplete_real_partial"
REPORT_STATUS_REAL_OVER_BUDGET = "incomplete_real_over_budget"

REPORT_STATUSES = (
    REPORT_STATUS_OK, REPORT_STATUS_INCOMPLETE, REPORT_STATUS_EMITTER_GAP,
    REPORT_STATUS_REAL_NO_EPISODES, REPORT_STATUS_REAL_PARTIAL,
    REPORT_STATUS_REAL_OVER_BUDGET,
)

#: Writer file names (glob contract for the CLI readers).
FAMILY_FILE_PREFIX = "family_"
RECALL_FILE = "recall.json"
#: Live-writer family-file schema (v1.1 — arm-keyed CONTAINER shape
#: {"family", "arms": {arm_id: payload}}, round-4 P2; legacy flat
#: single-arm payloads + root-level v1.0 files stay readable via the
#: reader back-compat).
FAMILY_FILE_SCHEMA = "1.1"
SUMMARY_FILE = "summary.json"


@dataclass(frozen=True)
class Profile:
    matrix: dict[str, dict[str, dict[str, Any]]]  # family -> arm -> cell
    verdict: Verdict
    matched_recall: dict[str, Any]
    report_status: str
    families_measured: int
    families_expected: int
    #: Family control records (round-4 P2 metric-aware selection): measured
    #: SECONDARY/control metrics (e.g. R1's false-positive-rate) routed to
    #: their own record, keyed family -> arm -> {metric: mean}. The FP gate
    #: path (Task-9 executor + sibling-B cal re-lock) consumes these — a
    #: secondary mean never becomes the family headline.
    control_records: dict[str, Any] = field(default_factory=dict)


def assemble(run_artifacts: dict[str, dict[str, float | None]],
             expected_families: tuple[str, ...],
             mitigation_paths: dict[str, str],
             matched_recall: dict[str, Any] | None = None,
             delta_threshold: float = 0.10,
             run_status: str | None = None,
             control_records: dict[str, Any] | None = None) -> Profile:
    """run_artifacts: family -> arm -> value (from the run's family files;
    None = attempted-but-insufficient_n cell). ``run_status`` overrides the
    base missing-family rule when the CLI composed a run-level status
    (mock/emitter-gap/real-* branches)."""
    # Missing-metrics guard: never fabricate a classification for a family
    # that was not measured (E2E-6.2). Measured = a family with ≥1 numeric
    # arm value (an attempted-but-insufficient family is REPORTED as
    # insufficient_n cells, never counted measured, never vacuous).
    numeric = {f: {a: v for a, v in arms.items() if v is not None}
               for f, arms in run_artifacts.items()}
    measured = [f for f in expected_families
                if numeric.get(f, {})]
    missing = [f for f in expected_families if f not in measured]
    status = REPORT_STATUS_OK if not missing else REPORT_STATUS_INCOMPLETE
    if run_status is not None:
        status = run_status

    matrix: dict[str, dict[str, dict[str, Any]]] = {}
    classifications: list[CellClassification] = []
    for fam in sorted(run_artifacts):
        arms = run_artifacts[fam]
        matrix[fam] = {}
        for arm, value in arms.items():
            others = [v for a, v in arms.items() if a != arm and v is not None]
            best_comparator = max(others) if others else 0.0
            cell = classify_cell(fam, arm, value, best_comparator,
                                 delta_threshold)
            if value is not None:
                # An insufficient_n cell is reported but never scored — it
                # can not influence the verdict (no vacuous pass).
                classifications.append(cell)
                matrix[fam][arm] = {
                    "value": value,
                    "delta": value - best_comparator,
                    "classification": cell.classification,
                    "load_bearing": cell.load_bearing,
                }
            else:
                matrix[fam][arm] = {
                    "value": None,
                    "delta": None,
                    "classification": cell.classification,
                    "load_bearing": False,
                }

    verdict = decide_verdict(classifications, mitigation_paths,
                             matched_recall)
    return Profile(matrix=matrix, verdict=verdict,
                   matched_recall=matched_recall or {},
                   report_status=status,
                   families_measured=len(measured),
                   families_expected=len(expected_families),
                   control_records=control_records or {})


def compose_run_status(*, run_mode: str, exit_code: int,
                       measured_cells: int, insufficient_cells: int,
                       excluded_episodes: int,
                       emitter_gap: bool = False,
                       excluded_gap: bool = False,
                       over_budget: bool = False) -> str | None:
    """Run-level report_status precedence (Task 5 acceptance — each branch
    driven by run_mode + summary exit_code + per-episode statuses). Returns
    None when the base missing-family rule in ``assemble`` should decide
    (a real run whose measured families are simply incomplete).

    ``excluded_gap`` (round-4 P2): a REAL excluded episode whose exclusion
    snapshot (excluded.expected vs excluded.emitted — recorded by run.py)
    is non-empty ALSO composes ``incomplete_emitter_gap``: the artifact
    exemption is retained, but an exclusion can never hide an emitter that
    stopped covering the episode's expected fields."""
    if run_mode != "real":
        # Mock runs stay incomplete_missing_metrics even with a probe scorer
        # wired (all cells insufficient_n) — never the emitter-gap/real-*
        # statuses, never complete.
        return REPORT_STATUS_INCOMPLETE
    if emitter_gap or excluded_gap:
        # A real artifact with a non-empty post-derivation emitter gap —
        # probes never measured from an uncovered log. An excluded real
        # episode with a gapped expected-vs-emitted snapshot surfaces the
        # same run-level state (round-4 P2).
        return REPORT_STATUS_EMITTER_GAP
    if over_budget:
        return REPORT_STATUS_REAL_OVER_BUDGET
    if measured_cells == 0:
        # No-episodes is reserved for runs that ATTEMPTED episodes but
        # measured none: all-excluded / all-insufficient / cap-stopped /
        # ARM_FAILED (exit_code != 0). A real run with no family payloads,
        # no exclusions and a healthy exit code is a real HARNESS run (no
        # probe scorer wired — nothing attempted a family) — it falls
        # through to the base missing-family rule
        # (incomplete_missing_metrics), never mislabeled no-episodes.
        if (exit_code != 0 or insufficient_cells > 0
                or excluded_episodes > 0):
            return REPORT_STATUS_REAL_NO_EPISODES
        return None
    if insufficient_cells > 0 or excluded_episodes > 0:
        return REPORT_STATUS_REAL_PARTIAL
    return None


# ---------------------------------------------------------------------------
# LIVE writers (tmp+os.replace — a partial/corrupt family file is never
# readable as a measured cell) + attempt-dir resolution
# ---------------------------------------------------------------------------

def attempt_dir_resolve(out_dir: str | Path) -> Path | None:
    """Resolve the LATEST completed attempt dir under ``out_dir``.

    Completion marker = ``summary.json`` presence (written LAST by the
    runner); crashed/cap-stopped dirs (episode artifacts but no summary)
    NEVER shadow a prior complete attempt. ``--out`` pointing AT an attempt
    dir itself resolves to it (legacy root fallback when no attempt dir
    exists). Deterministic: attempt dir names are timestamped
    (sub-second stamp — two sequential runs never collide) so the lexical
    maximum is the latest.
    """
    base = Path(out_dir)
    if not base.is_dir():
        return None
    if (base / SUMMARY_FILE).is_file():
        return base
    candidates = sorted(
        p for p in base.iterdir()
        if p.is_dir() and (p / SUMMARY_FILE).is_file())
    return candidates[-1] if candidates else None


def _atomic_write(path: Path, payload: Any) -> None:
    """tmp + os.replace — readers never observe a partially-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def write_family_files(attempt_dir: str | Path,
                       payloads: list[dict[str, Any]]) -> list[Path]:
    """Aggregate the run's per-arm family payloads into ONE container file
    per family (round-4 P2 — the round-3 writer named ``family_<F>.json``
    from the family alone while the reader keyed on ``payload["arm"]``, so a
    second arm's payload silently OVERWROTE the first). Container shape:

    ``family_<F>.json`` = ``{"schema_version": "1.1", "family": F,
    "arms": {arm_id: payload}}`` — arm-keyed, collision-free. run.py still
    refuses multi-arm probe runs pre-flight (unchanged); the file contract
    is now safe for the Task-9 multi-arm executor. Each per-arm payload is
    the pinned Task-5 payload (family, primary, arm, n, values, cells).
    Written atomically into the attempt dir. Legacy flat single-arm files
    (pre-round files) remain readable via the reader back-compat."""
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for payload in payloads:
        family = payload.get("family")
        if not family:
            raise ValueError(f"family payload missing 'family': {payload}")
        arm = payload.get("arm")
        if not arm:
            raise ValueError(f"family payload missing 'arm': {payload}")
        arms = grouped.setdefault(family, {})
        arms[arm] = payload  # same-arm duplicates: last write wins
    written: list[Path] = []
    for family, arms in grouped.items():
        container = {
            "schema_version": FAMILY_FILE_SCHEMA,
            "family": family,
            "arms": arms,
        }
        path = Path(attempt_dir) / f"{FAMILY_FILE_PREFIX}{family}.json"
        _atomic_write(path, container)
        written.append(path)
    return written


def write_recall_file(attempt_dir: str | Path, recall: dict[str, Any]) -> Path:
    """Per-episode retrieved Memories + EP markers (atomic)."""
    path = Path(attempt_dir) / RECALL_FILE
    _atomic_write(path, recall)
    return path


def read_family_file(path: str | Path) -> dict[str, Any] | None:
    """Read one family_<F>.json; None when the file is corrupt/partial (a
    torn write or manual tamper is NEVER readable as a measured cell). Two
    readable shapes (round-4 P2): the live CONTAINER
    (``{"family", "arms": {arm_id: payload}}`` — arm-keyed aggregation) and
    the LEGACY flat single-arm payload (``{family, cells, ...}`` — pre-round
    files ``{family,n,values,cells,arm,primary}`` stay readable). A
    versioned file whose schema_version does not match the live-writer
    schema is refused (a future-shape family file is never misread as the
    current no-data shape); unversioned legacy files (root-level v1.0
    fallback) remain readable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "family" not in data:
        return None
    arms = data.get("arms")
    is_container = isinstance(arms, dict) and bool(arms)
    if not is_container and ("cells" not in data or "values" not in data):
        return None
    if ("schema_version" in data
            and data["schema_version"] != FAMILY_FILE_SCHEMA):
        return None
    return data


def read_recall_file(attempt_dir: str | Path) -> dict[str, Any] | None:
    path = Path(attempt_dir) / RECALL_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_profile(profile: Profile, path: str | Path) -> Path:
    """Serialize the profile to profile.json (plan §4 schema)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "matrix": profile.matrix,
        "verdict": {
            "outcome": profile.verdict.outcome,
            "differentiators": list(profile.verdict.differentiators),
            "weaknesses": list(profile.verdict.weaknesses),
            "mitigation_paths": profile.verdict.mitigation_paths,
            "artifacts_changed": list(profile.verdict.artifacts_changed),
        },
        "matched_recall": profile.matched_recall,
        #: Family control records (round-4 P2): measured secondary/control
        #: metrics routed to their own records (never the family headline) —
        #: the FP gate path consumes these once Task 9 emits bct verdicts.
        "control_records": profile.control_records,
        "report_status": profile.report_status,
        "families": {"measured": profile.families_measured,
                     "expected": profile.families_expected},
    }
    p.write_text(json.dumps(payload, indent=2))
    return p
