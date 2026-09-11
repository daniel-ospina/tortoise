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

import contextlib
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


#: Lane labels a measured cell may carry. "real"/"mock" are the released
#: full-context runner lanes; the "_tortoise" labels are the RETRIEVED-context
#: arm lanes (#2800). A Tortoise number carries its OWN label so it can never
#: be mistaken for the full-context baseline it is compared against (the
#: reason the lane field is keyword-required and validated, #2819).
LANES: tuple[str, ...] = ("real", "mock", "real_tortoise", "mock_tortoise")


@dataclass(frozen=True)
class ExecutedCell:
    """One measured benchmark cell: the official metric + the samples + the
    dataset revision the runner actually loaded."""

    benchmark: str
    accuracy: float | None
    samples: int
    revision: str
    #: Which lane produced the number: "real" (the released dataset with real
    #: reader/judge models), "mock" (a fixture with mocked reader/judge, no
    #: spend), or the retrieved-context arm variants "real_tortoise" /
    #: "mock_tortoise" (#2800 — MemoryAgentBench CR on the Tortoise arm).
    #: REQUIRED — no default (review P2 on #2819): a default would
    #: silently assert "real" for a cell that never said so, which is the
    #: fail-open version of the provenance bug this field exists to prevent.
    lane: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.lane not in LANES:
            raise ValueError(
                f"parity executor {self.benchmark}: lane={self.lane!r} must be "
                f"one of {LANES} — a cell that does not say which lane it came "
                f"from cannot be compared")
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
        # The runner's `dataset` field is its own hardcoded `dataset_id`
        # default, not proof of what was loaded — the VERIFIED identity is
        # `methodology.dataset_fingerprint` (sha256 of the loaded file,
        # report.py). Carry it, so a real revision is checkable against the
        # artifact rather than self-asserted (review P2, #2819).
        meta = report.get("methodology") or {}
        fingerprint = str(meta.get("dataset_fingerprint") or "unknown")
        revision = (f"{report.get('dataset', 'unknown')}"
                    f"@{report.get('split', '')}#{fingerprint[:16]}")
    return ExecutedCell(
        benchmark="longmemeval", accuracy=accuracy, samples=samples,
        revision=revision, lane=lane,
        detail={"task_averaged": (acc or {}).get("task_averaged")
                if isinstance(acc, dict) else None,
                "lane": lane, "output": str(out_path)})


class _MockReader:
    """Deterministic stand-in for the reader model (hermetic lanes only).

    It answers nothing useful ON PURPOSE: its job is to exercise the path
    (pool → prompt → answer → official metric → cell) in CI with no keys and
    no spend. A mock score is labelled ``lane="mock"`` and can never read as a
    comparable measurement — that is the whole reason the lane is recorded.
    """

    last_prompt_tokens = 0
    last_completion_tokens = 0

    def call(self, *, prompt: str) -> str:
        return "Answer: unknown"


def memoryagentbench_executor(*, mock: bool = False, limit: int | None = None,
                              out_dir: Path | None = None,
                              config: str = "factconsolidation_sh_6k"
                              ) -> ExecutedCell:
    """Run the MemoryAgentBench Conflict Resolution family (#2800).

    The lane is the benchmark's published **long-context** setup: the knowledge
    pool is injected once, each question is asked with the benchmark's own
    conflict-rule template, and the answers are scored by the benchmark's own
    ``substring_exact_match`` metric (no LLM judge).

    ``mock=True`` uses a deterministic stand-in reader; the real lane uses the
    pinned model caller (``battery.runner.model_calls``), which fails closed
    without ``OPENROUTER_API_KEY`` and meters every call's tokens. Every
    refusal below becomes ``ExecutorUnavailable`` — the parity leg then records
    an explicitly not-measured cell rather than a number.
    """
    from battery.parity.mabench import (
        MabenchError,
        load_cr,
    )
    from battery.parity.mabench_run import run_cr_lane

    try:
        cfg = load_cr(config)
    except MabenchError as e:
        raise ExecutorUnavailable(
            f"memoryagentbench: {type(e).__name__}: {e}") from e
    if mock:
        caller, lane = _MockReader(), "mock"
    else:
        try:
            from battery.runner.model_calls import RealModelCaller
            caller = RealModelCaller()
        except Exception as e:
            raise ExecutorUnavailable(
                f"memoryagentbench: real reader unavailable ({e})") from e
        lane = "real"
    try:
        cell, _run = run_cr_lane(cfg.items, caller, lane=lane, config=config,
                                 context=cfg.context, limit=limit)
    except Exception as e:
        # A reader that fails mid-run must not abort the whole parity leg
        # (other benchmarks still have a cell to record) — it becomes an
        # explicit not-measured cell carrying the reason.
        raise ExecutorUnavailable(
            f"memoryagentbench: run failed ({type(e).__name__}: {e})") from e
    return cell


def memoryagentbench_tortoise_executor(*, mock: bool = False,
                                       limit: int | None = None,
                                       out_dir: Path | None = None,
                                       config: str = "factconsolidation_sh_6k",
                                       k: int | None = None
                                       ) -> ExecutedCell:
    """Run the MemoryAgentBench Conflict Resolution family on the TORTOISE
    (retrieved-context) lane (#2800) — the treatment row of the baseline cell
    ``memoryagentbench_executor`` produces.

    Same pinned dataset, same benchmark prompts and the same official metric:
    the ONLY difference from the baseline is that the reader sees the arm's
    RETRIEVED facts instead of the whole knowledge pool. The cell is labelled
    ``lane="real_tortoise"`` / ``"mock_tortoise"`` so it can never be mistaken
    for the full-context baseline.

    ``mock=True`` is hermetic: an in-memory fake memory + the deterministic
    ``_MockReader`` — no parquet reader beyond the pinned config, no keys, no
    spend. The real lane builds the product-backed ``TortoiseCrMemory``
    (embedded store) and the pinned caller; both fail closed into
    ``ExecutorUnavailable`` — a missing key or an unavailable arm is never
    silently downgraded to the mock lane.
    """
    from battery.parity.mabench import MabenchError, load_cr
    from battery.parity.mabench_tortoise import (
        DEFAULT_K,
        LANE_MOCK,
        LANE_REAL,
        TortoiseCrMemory,
        _FakeCrMemory,
        run_cr_tortoise_lane,
    )

    try:
        cfg = load_cr(config)
    except MabenchError as e:
        raise ExecutorUnavailable(
            f"memoryagentbench_tortoise: {type(e).__name__}: {e}") from e

    kk = DEFAULT_K if k is None else k
    if mock:
        memory, caller, lane = _FakeCrMemory(), _MockReader(), LANE_MOCK
    else:
        try:
            from battery.runner.model_calls import RealModelCaller
            caller = RealModelCaller()
        except Exception as e:
            raise ExecutorUnavailable(
                f"memoryagentbench_tortoise: real reader unavailable "
                f"({e})") from e
        try:
            memory = TortoiseCrMemory()
        except Exception as e:
            raise ExecutorUnavailable(
                f"memoryagentbench_tortoise: tortoise arm unavailable "
                f"({e})") from e
        lane = LANE_REAL

    try:
        cell, _run = run_cr_tortoise_lane(
            cfg.items, memory, caller, lane=lane, config=config,
            context=cfg.context, limit=limit, k=kk)
    except ExecutorUnavailable:
        raise
    except Exception as e:
        # A reader/arm that fails mid-run must not abort the whole parity leg:
        # it becomes an explicit not-measured cell carrying the reason.
        raise ExecutorUnavailable(
            f"memoryagentbench_tortoise: run failed "
            f"({type(e).__name__}: {e})") from e
    finally:
        # Close the real embedded store (the fake has none). Non-masking: a
        # teardown error must never replace the propagating result/refusal —
        # the CLI catches only ``ExecutorUnavailable``, so an escaping close
        # error would abort the whole parity leg instead of recording a
        # not-measured cell.
        close = getattr(memory, "close", None)
        if lane == LANE_REAL and callable(close):
            with contextlib.suppress(Exception):
                close()
    return cell


#: Registered execution seams, keyed by the pinned benchmark id. A benchmark
#: absent here is a NOT-MEASURED cell (no runner wired) — the parity leg says
#: so explicitly instead of implying a comparison.
EXECUTORS: dict[str, Executor] = {
    "longmemeval": longmemeval_executor,
    "memoryagentbench": memoryagentbench_executor,
    "memoryagentbench_tortoise": memoryagentbench_tortoise_executor,
}
