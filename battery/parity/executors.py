"""Released-benchmark executors for the parity leg (#2800).

The parity scaffold (`battery/parity/runner.py`) pins dataset versions,
refuses on mismatch, and verifies three methodology hashes against the #1144
baseline record — but it never executed a benchmark: `battery parity` handed
`run_parity` a literal accuracy that no run produced (#2797). This module is
the missing half: it runs a released benchmark through its OWN official
runner and returns the number that runner computed, with the sample count
and the dataset revision it actually loaded.

Design rules (they are what make the published parity table defensible):

* **The official runner computes the metric — never this module.** An
  executor adapts argv, invokes the released implementation, and reads the
  field that implementation published (for LongMemEval: the official anscheck
  judge's ``accuracy.overall`` with ``n_questions``). No re-scoring, no
  re-derivation, no metric of our own under a standard's name.
* **A metric is returned only with its samples.** ``ExecutedCell`` enforces
  the same invariant as ``ParityRun`` (#2797): a number with no sample count
  behind it is refused at construction.
* **Absence is a first-class outcome.** A missing dataset, a missing key, or
  a runner that cannot load raises ``ExecutorUnavailable`` — the caller then
  records an explicitly not-measured cell. Fail-closed, never a default.
* **The revision is reported, not assumed.** Each cell carries the dataset
  identity the runner actually used (LongMemEval: the report's ``dataset`` +
  ``split``), so a pinned-version claim can be checked against the artifact.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The repo root (battery/parity/executors.py → repo root), needed because the
#: released LongMemEval runner lives under ``tools/`` and is imported lazily.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class ExecutorUnavailable(RuntimeError):
    """The released runner for this benchmark cannot run here.

    Raised for a missing dataset/fixture, a missing vendor key, or an import
    failure — the caller records a not-measured cell (never a default).
    """


@dataclass(frozen=True)
class ExecutedCell:
    """One measured benchmark cell: the official metric + the samples + the
    dataset revision the runner actually loaded."""

    benchmark: str
    accuracy: float | None
    samples: int
    revision: str
    #: Which lane produced the number: "real" (the released dataset with real
    #: reader/judge models) or "mock" (a fixture with mocked reader/judge, no
    #: spend). Persisted so a mock number can never read as a comparable
    #: measurement (review P1 on #2819): the official runner hardcodes its
    #: dataset id, so without this a mock cell carried the REAL dataset's
    #: revision and was indistinguishable from a real one.
    lane: str = "real"
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.lane not in ("real", "mock"):
            raise ValueError(
                f"parity executor {self.benchmark}: lane={self.lane!r} must be "
                f"'real' or 'mock' — a cell that does not say which lane it "
                f"came from cannot be compared")
        if self.accuracy is not None and self.samples <= 0:
            raise ValueError(
                f"parity executor {self.benchmark}: accuracy={self.accuracy!r} "
                f"with samples={self.samples} is not a measurement (#2797)")
        if not self.revision:
            raise ValueError(
                f"parity executor {self.benchmark}: a measured cell must name "
                f"the dataset revision it ran (a number without its dataset "
                f"identity is not comparable)")


#: executor signature: (mock, limit, out_dir) -> ExecutedCell
Executor = Callable[..., ExecutedCell]


def longmemeval_executor(*, mock: bool = False, limit: int | None = None,
                         out_dir: Path | None = None,
                         fixture: Path | None = None) -> ExecutedCell:
    """Run the official LongMemEval runner (``tools/longmem_eval``, #1144).

    The metric is the benchmark's own: the answer-check judge's
    ``accuracy.overall`` over ``n_questions`` — the same numbers the paper
    reports. ``mock=True`` runs the committed mini fixture with a mocked
    reader+judge (no keys, no spend); the real lane downloads the split and
    calls the pinned reader/judge models.

    Refuses (``ExecutorUnavailable``) rather than fabricating: a missing
    fixture in mock mode, a missing ``OPENROUTER_API_KEY``/reader key on the
    real lane, or a runner that cannot be imported.
    """
    try:
        from tools.longmem_eval.run import run_main
    except Exception as e:  # pragma: no cover - import guard, not a metric
        raise ExecutorUnavailable(
            f"longmemeval: released runner not importable ({e})") from e

    out_dir = Path(out_dir) if out_dir is not None else Path("/tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "longmemeval_official.json"

    argv: list[str] = ["--output", str(out_path)]
    if mock:
        fx = Path(fixture) if fixture is not None else (
            _REPO_ROOT / "tests" / "fixtures" / "longmemeval_mini.json")
        if not fx.is_file():
            raise ExecutorUnavailable(
                f"longmemeval: mock lane needs the committed mini fixture at "
                f"{fx} (missing) — refusing to report a number without a run")
        argv += ["--data", str(fx), "--mock", "--skip-preflight"]
    else:
        argv += ["--split", "s"]
    if limit is not None:
        argv += ["--limit", str(limit)]

    try:
        report = run_main(argv)
    except SystemExit as e:  # argparse error / runner refusal
        raise ExecutorUnavailable(
            f"longmemeval: official runner exited ({e}) — no measurement") from e
    except Exception as e:
        raise ExecutorUnavailable(
            f"longmemeval: official runner failed ({type(e).__name__}: {e})"
        ) from e

    if not isinstance(report, dict):
        raise ExecutorUnavailable(
            f"longmemeval: runner returned {type(report).__name__}, expected "
            f"the report dict")
    acc = report.get("accuracy")
    # Retrieval-only runs publish accuracy=None by design — that is a
    # not-measured cell, never a 0.0.
    accuracy = (acc.get("overall")
                if isinstance(acc, dict) and "overall" in acc else None)
    samples = int(report.get("n_questions") or 0)
    # The revision names what was ACTUALLY loaded. In mock mode the runner
    # still reports its hardcoded dataset id, so trusting it would label a
    # fixture run with a real dataset's identity (review P1, #2819) — the
    # fixture is named instead.
    if mock:
        lane = "mock"
        revision = f"fixture:{fx.name}"
    else:
        lane = "real"
        revision = f"{report.get('dataset', 'unknown')}@{report.get('split', '')}"
    return ExecutedCell(
        benchmark="longmemeval", accuracy=accuracy, samples=samples,
        revision=revision, lane=lane,
        detail={"task_averaged": (acc or {}).get("task_averaged")
                if isinstance(acc, dict) else None,
                "lane": lane, "output": str(out_path)})


#: Registered execution seams, keyed by the pinned benchmark id. A benchmark
#: absent here is a NOT-MEASURED cell (no runner wired) — the parity leg says
#: so explicitly instead of implying a comparison.
EXECUTORS: dict[str, Executor] = {
    "longmemeval": longmemeval_executor,
}
