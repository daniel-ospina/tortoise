"""`#13108` — the dense-leg probe's own pins (`tools/dense_leg_probe_13108.py`).

`#13108` reopens `STORAGE-ARCHITECTURE.md` §14.3 **V3** ("should `:Object` carry a
vector index?") on a measurement, so the probe is the deliverable and the numbers
it prints are the evidence. `tools/dense_leg_probe_13108.py:94` said those numbers
were *"pinned by the tests"* while the PR that added it added no test at all
(`#13121` review F4) — every assertion it made about `percentile`, `summarise`,
`over_budget` and the `None`-not-`0` rule had no row to redden, so any mutation of
them shipped green.

Each test below is a falsifier for ONE of the instrument's load-bearing claims:

| test | claim it falsifies | it goes RED when … |
|---|---|---|
| `test_percentile_answers_None_for_an_empty_sample_never_zero` | "UNMEASURED is never 0" | a percentile answers `0.0` (or any number) for a sample that does not exist |
| `test_percentile_is_nearest_rank` | the percentile is a PERCENTILE | the rank formula drifts (off-by-one, or a mean dressed as a p50) |
| `test_over_budget_includes_the_budget_itself` | `>=` in `summarise`'s `over_budget` | a read at exactly `timeout_ms` is counted as inside the budget |
| `test_summarise_publishes_None_for_every_absent_latency` | the same `None`-not-`0` rule, through `summarise` | an empty arm summarises as `p50: 0` / `mean: 0.0` |
| `test_summarise_counts_the_mechanism_the_trace_actually_took` | the mechanism/reason distribution | a mechanism is folded into another bucket, or dropped from the report the V3 reopen will quote |
| `test_an_unknown_mechanism_refuses_the_arm_instead_of_truncating_it` | the loud-guard claim (`#13121` F2) | the guard is raised from inside a worker thread, where `threading.excepthook` swallows it and the arm publishes the survivors |
| `test_an_arm_that_lost_a_read_is_refused_not_published_short` | `arm_integrity`, the backstop for a worker that dies without raising | `measure` summarises `37/100` as if it were a whole arm |
| `test_a_worker_failure_is_recorded_not_swallowed` | `measure`'s `except BaseException` | the failure escapes the thread again (the `#13121` F2 shape) |
| `test_probe_index_attempt_does_not_claim_both_signatures_when_one_ran` | the third outcome (`#13121` F3) | one signature aborts (a `--timeout-ms` hit on a loaded box) and the record still says `refused-both-signatures` |
| `test_probe_index_attempt_still_separates_a_real_double_refusal` | the same outcome, in the OTHER direction | a genuine double refusal stops being reported as one |
| `test_probe_index_attempt_reports_the_signature_that_answered` | the control case | a working signature is mislabelled or its attempt vanishes |
| `test_seed_carries_the_harvest_input_accounting` | `#13121` F5 — `harvest`'s `stats` reach the artifact | the report cannot distinguish "the corpus lost names" from "the harness stopped parsing records" |
| `test_the_closed_mechanism_set_is_the_engines_own_vocabulary` | `MECHANISMS` is `#4999`'s vocabulary, RESTATED | the engine grows a mechanism and the probe silently refuses every arm that takes it |
| `test_the_probe_ships_the_interpreter_guard` | the `#5128` guard is first, and attributable | the guard is removed or moved below an import — the corpus-wide rows in `test_entry_point_python_guard.py` also cover this (parametrized over `tools/`), pinned here so a `tools/`-only diff that selects THIS file carries its own copy |
| `test_a_tool_only_diff_selects_this_pin` | these falsifiers actually RUN in CI (`#13121` F4) | `tools/` stays swallowed by `NON_PYTHON_PREFIXES`, so a probe-only change selects tier-1 smoke and never runs this file |
"""

from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tools import dense_leg_probe_13108 as probe

# ── statistics: every number the V3 reopen will quote ───────────────────────


def test_percentile_answers_None_for_an_empty_sample_never_zero():
    """`#13121` F4: the `None`-not-`0` distinction is a BEHAVIOUR, not a comment.

    An empty sample and a zero-millisecond sample are different facts — the same
    distinction `tools/dod_5090_probe.py` draws between `UNMEASURED` and `0`. A
    `return 0.0` here reds this row.
    """
    assert probe.percentile([], 50) is None
    assert probe.percentile([], 95) is None
    assert probe.percentile([], 99) is None
    # …and a REAL zero is still a measurement, not "absent".
    assert probe.percentile([0.0], 50) == 0.0


def test_percentile_is_nearest_rank():
    """Nearest-rank, not interpolation: 1..10 must give the 5th/10th/10th value."""
    values = [float(v) for v in range(1, 11)]
    assert probe.percentile(values, 50) == 5.0
    assert probe.percentile(values, 95) == 10.0
    assert probe.percentile(values, 99) == 10.0
    # The single-value sample is the value, at every rank (the `len == 1` branch).
    assert probe.percentile([7.5], 99) == 7.5
    # Order of the INPUT cannot matter — the rank is over the sorted sample.
    assert probe.percentile([10.0, 1.0, 5.0], 50) == 5.0


def _sample(elapsed_ms: float, timeout_ms: int = 500, **kw) -> dict:
    row = {"elapsed_ms": elapsed_ms, "timeout_ms": timeout_ms, "rows": 1,
           "mechanism": "scan", "reason": "ok", "degraded": False}
    row.update(kw)
    return row


def test_over_budget_includes_the_budget_itself():
    """`>=`, boundary INCLUSIVE — the read the collector's deadline abandons.

    `>=` → `>` reds this row: a read that took exactly `timeout_ms` is the one the
    deadline fires on, and counting it as inside the budget understates the very
    number the issue is about.
    """
    arm = probe.summarise([
        _sample(499.0), _sample(500.0), _sample(500.01), _sample(1.0),
    ])
    assert arm["over_budget"] == 2, arm
    assert arm["n"] == 4


def test_summarise_publishes_None_for_every_absent_latency():
    """An arm that ran nothing is `None` in every latency cell, never `0`."""
    arm = probe.summarise([])
    assert arm["n"] == 0
    assert arm["latency_ms"] == {"p50": None, "p95": None, "p99": None,
                                "max": None, "mean": None}
    assert arm["count"]["rows_mean"] is None
    assert arm["over_budget"] == 0
    assert arm["degraded"] == 0


def test_summarise_counts_the_mechanism_the_trace_actually_took():
    arm = probe.summarise([
        _sample(10.0, mechanism="index", reason="ok"),
        _sample(20.0, mechanism="scan_fallback", reason="timeout"),
        _sample(30.0, mechanism="scan_fallback", reason="timeout"),
        _sample(40.0, mechanism=None, reason="query_failed", rows=0),
    ])
    assert arm["mechanism"] == {"None": 1, "index": 1, "scan_fallback": 2}
    assert arm["reason"] == {"ok": 1, "query_failed": 1, "timeout": 2}
    assert arm["count"]["zero_rows"] == 1
    assert arm["degraded"] == 0


# ── the loud guard must not die inside a worker thread (#13121 F2) ──────────


def _measure_with(monkeypatch, run_vector_query, *, reads: int = 4,
                  concurrency: int = 2):
    """`measure` driven against a SCRIPTED `run_vector_query` — no graph, no DB."""
    import tortoise.search_engine as engine

    monkeypatch.setattr(engine, "run_vector_query", run_vector_query)
    return probe.measure(object(), [0.0], "object", reads=reads,
                         concurrency=concurrency, limit=3, timeout_ms=500)


def test_an_unknown_mechanism_refuses_the_arm_instead_of_truncating_it(monkeypatch):
    """`#13121` F2: the pre-fix guard raised `SystemExit` INSIDE a thread.

    CPython's default `threading.excepthook()` silently ignores an uncaught
    `SystemExit` raised by a thread (`docs.python.org/3/library/threading.html`,
    reproduced on 3.12.3), so the pre-fix shape was: the thread dies quietly, the
    samples it had already collected are discarded with it, and `measure` returns
    an arm summarised from the survivors — `over 500ms: 0/0` on an arm that ran.

    RED on the pre-fix code: no refusal is raised at all, the call returns a
    (short) arm.
    """
    from tortoise.search_engine import VECTOR_MECHANISM_KEY

    def scripted(graph, vec, *, leg_trace, **_kw):
        leg_trace.append({VECTOR_MECHANISM_KEY: "mystery"})
        return []

    with pytest.raises(SystemExit) as refusal:
        _measure_with(monkeypatch, scripted)
    message = str(refusal.value)
    assert "did not complete" in message, message
    assert "mystery" in message, message
    assert "object@2" in message, message


def test_a_worker_failure_is_recorded_not_swallowed(monkeypatch):
    """Any escaping failure is RECORDED and refuses the arm — not dropped.

    ⚠️ Driven at `_vector_query_once`, not at `run_vector_query`: a leg that
    RAISES is a legitimate SAMPLE here (``"a raised leg is a SAMPLE, not a
    crash"``), so a `RuntimeError` from the store never leaves the worker. What
    must not escape is a failure of the instrument itself — the pre-fix code had
    no `except` in `worker()` at all, so such a failure was swallowed by
    `threading.excepthook` and a truncated arm was published as a whole one.
    """
    def exploding(*_args, **_kwargs):
        raise RuntimeError("store went away")

    monkeypatch.setattr(probe, "_vector_query_once", exploding)
    with pytest.raises(SystemExit) as refusal:
        probe.measure(object(), [0.0], "object", reads=2, concurrency=1,
                      limit=3, timeout_ms=500)
    assert "RuntimeError: store went away" in str(refusal.value)


def test_an_arm_that_lost_a_read_is_refused_not_published_short():
    """`arm_integrity` is the backstop for a worker that dies WITHOUT raising.

    Pure, so the rule itself is pinned without a thread — `measure` is this
    function plus the workers. No refusals and a full count is the only clean
    answer; anything else refuses, and the message says which.
    """
    assert probe.arm_integrity("object", 2, 100, 100, []) is None
    short = probe.arm_integrity("object", 2, 100, 37, [])
    assert short is not None and "37/100 reads arrived" in short, short
    raised = probe.arm_integrity("object", 2, 100, 100, ["Boom: nope"])
    assert raised is not None and "Boom: nope" in raised, raised
    # A full count with ZERO reads is vacuously complete; `reads == arrived`
    # alone must not be the rule, or an arm of no work reads as measured.
    assert probe.arm_integrity("object", 1, 0, 0, []) is None


# ── the index-attempt record must not contradict itself (#13121 F3) ─────────


class _ScriptedGraph:
    """A graph handle whose `query` raises/answers per call, in order."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def query(self, cypher, params=None, timeout=None):
        self.calls.append({"cypher": cypher, "params": params, "timeout": timeout})
        outcome = self.outcomes.pop(0) if self.outcomes else Exception("exhausted")
        if isinstance(outcome, BaseException):
            raise outcome

        class _Result:
            result_set = outcome

        return _Result()


def test_probe_index_attempt_does_not_claim_both_signatures_when_one_ran():
    """`#13121` F3 — the likely trigger is the probe query ITSELF timing out.

    At `--timeout-ms 500` on a loaded box, signature A raises a `TimeoutError`
    that is not one of the four recognised signature phrases, the loop breaks,
    and B was NEVER TRIED. Reporting `refused-both-signatures` there asserts a
    measurement the run did not make — and it is precisely the "how 'the index is
    missing' becomes 'the driver call is wrong'" conflation the function exists
    to prevent. RED on the pre-fix code, which returns `refused-both-signatures`.
    """
    graph = _ScriptedGraph(TimeoutError("Query timed out"))
    record = probe.probe_index_attempt(graph, [0.1], "Object", 3, 500)

    assert record["outcome"] == "aborted_at_signature_a", record
    assert record["answered_signature"] is None
    assert len(record["attempts"]) == 1, record["attempts"]
    assert len(graph.calls) == 1, "signature B must not be recorded as tried"
    assert "TimeoutError" in record["aborted_reason"], record
    assert "never tried" in record["aborted_reason"], record


def test_probe_index_attempt_still_separates_a_real_double_refusal():
    """The other direction: TWO recognised refusals are still `refused-both-…`.

    Without this row a fix could delete the branch and call every failure an
    abort, losing the one outcome the census is meant to corroborate.
    """
    graph = _ScriptedGraph(
        Exception("Invalid arguments for procedure 'db.idx.vector.queryNodes'"),
        Exception("Invalid arguments for procedure 'db.idx.vector.queryNodes'"),
    )
    record = probe.probe_index_attempt(graph, [0.1], "Object", 3, 500)

    assert record["outcome"] == "refused-both-signatures", record
    assert record["answered_signature"] is None
    assert record["aborted_reason"] is None
    assert [a["signature"] for a in record["attempts"]] == ["A", "B"]


def test_probe_index_attempt_reports_the_signature_that_answered():
    """The control: A refuses by signature, B answers — `ok`, and both attempts."""
    graph = _ScriptedGraph(
        Exception("Invalid arguments for procedure 'db.idx.vector.queryNodes'"),
        [["pt-000001"]],
    )
    record = probe.probe_index_attempt(graph, [0.1], "Point", 3, 500)

    assert record["outcome"] == "ok", record
    assert record["answered_signature"] == "B"
    assert record["aborted_reason"] is None
    assert [a["outcome"] for a in record["attempts"]] == ["refused", "ok"]
    assert graph.calls[0]["timeout"] == 500, "the probe must carry --timeout-ms"


# ── the seed must account for its own input (#13121 F5) ────────────────────


def test_seed_carries_the_harvest_input_accounting(monkeypatch):
    """`harvest` fills `stats` precisely so an artifact can tell "the corpus lost
    names" from "the harness stopped parsing records"; `seed` threw it away.

    RED on the pre-fix code: `seed` calls `harvest(sessions)` with no `stats`, so
    the report has no `harvest_stats` key at all.
    """
    from tools.candidate_recall_11901 import build_population

    seen: list[dict] = []

    def fake_harvest(sessions_dir, stats=None):
        if stats is not None:
            stats.update(files=3, lines=120, parse_errors=2,
                         records_without_text=1)
        seen.append(stats)
        return {"definite": collections.Counter({"the verdict": 5}),
                "ident": collections.Counter({"#13108": 4}),
                "path": collections.Counter({"tools/x.py": 3})}

    monkeypatch.setattr(probe, "_import_instrument",
                        lambda: (fake_harvest, build_population))

    class _Result:
        def __init__(self):
            self.result_set = [("obj-13108000000", "the verdict", [0.5, 0.25])]

    class _Graph:
        def query(self, cypher, params=None):
            return _Result()

    class _Proj:
        def __init__(self):
            self.g = _Graph()
            self.upserted: list[dict] = []

        def _upsert_object(self, row):
            self.upserted.append(row)

    class _SDK:
        def __init__(self):
            self.proj = _Proj()

        def _get_proj(self):
            return self.proj

    seeded = probe.seed(_SDK(), 3, "~/nowhere")

    assert seeded["harvest_stats"] == {"files": 3, "lines": 120,
                                       "parse_errors": 2,
                                       "records_without_text": 1}, seeded
    assert seen and seen[0] is not None, "harvest was not handed a stats dict"
    # …and the mirror still reports what landed beside it.
    assert seeded["objects"] == 1 and seeded["points"] == 1
    assert seeded["vectors"] == 1


# ── the closed set is the ENGINE's vocabulary, restated ────────────────────


def test_the_closed_mechanism_set_is_the_engines_own_vocabulary():
    """`MECHANISMS` is `#4999`'s vocabulary restated — a restatement can drift.

    The probe REFUSES an arm whose trace reports a mechanism outside this set
    (by design), so an engine that grows a fifth mechanism would make the tool
    report "refused" for a shipped path. That is loud, but it stops the
    instrument; this row turns it into a red test that names the drift instead.
    """
    from tortoise import search_engine as engine

    declared = {engine.MECHANISM_INDEX, engine.MECHANISM_SCAN,
                engine.MECHANISM_SCAN_FALLBACK, engine.MECHANISM_PGVECTOR}
    assert set(probe.MECHANISMS) == declared, (
        "the probe's closed mechanism set drifted from the engine's own "
        f"constants: probe={sorted(probe.MECHANISMS)} engine={sorted(declared)}"
    )


# ── the file is a compliant `tools/` entry point, and its pins RUN ─────────


def test_the_probe_ships_the_interpreter_guard():
    """`#5128`: an attributable refusal BEFORE the imports (see the corpus rows).

    Pinned here as well because `test_entry_point_python_guard.py` is the
    corpus-wide sweep: this row is what a `tools/dense_leg_probe_13108.py`-only
    diff carries if the sweep's own selection ever changes.
    """
    import ast

    path = Path(probe.__file__).resolve()
    body = ast.parse(path.read_text(encoding="utf-8")).body
    # [docstring] [__future__ imports…] `import sys` <guard> — the preamble the
    # corpus sweep's `_guard_index` requires, restated here for this one file.
    if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant):
        body = body[1:]
    while isinstance(body[0], ast.ImportFrom) and body[0].module == "__future__":
        body = body[1:]
    assert isinstance(body[0], ast.Import) and body[0].names[0].name == "sys", (
        "the #5128 guard must be preceded only by `import sys` — the first "
        f"statement here is {ast.dump(body[0])[:80]}"
    )
    guard = body[1]
    assert isinstance(guard, ast.If), f"no guard after `import sys`: {guard!r}"
    source = ast.get_source_segment(path.read_text(encoding="utf-8"), guard) or ""
    assert "sys.version_info < (3, 12)" in source, source
    assert "3.12" in source and ">= 3.12" in source, source
    assert "uv run python tools/dense_leg_probe_13108.py" in source, source


def test_a_tool_only_diff_selects_this_pin():
    """`#13121` F4: registration is CLASSIFICATION — it does not make a diff RUN this.

    `tools/` is a flat `NON_PYTHON_PREFIXES` entry, so a probe-only change filters
    to `changed == []`, takes the docs-only early return and runs tier-1 smoke —
    where this file is not registered. Every falsifier above would then be asleep
    on the one diff that can break them (the `tools/dod_5090_probe.py` /
    `#10465` shape), which is why the probe is a `CORE_ALSO` entry.
    """
    from tools import ci_selection as cs

    manifest = cs.load_manifest()
    selected = cs.select(["tools/dense_leg_probe_13108.py"],
                         "pull_request", manifest)
    assert "test_dense_leg_probe_13108.py" in selected["test_files"], (
        "a probe-only diff does not select this file — its falsifiers never "
        f"run: surfaces={selected['surfaces']} full={selected['full']}"
    )


# ── the report cannot mislabel the population it measured (round 5 F1) ──────

def test_population_measured_resolves_ENTITY_TYPES_through_the_label_map():
    """The census is keyed by LABEL; `--labels` names ENTITY TYPES.

    Round 4 added `population_measured` to stop four artifacts saying
    `population: 1000` beside their own 8,000-node census — and looked the
    entity type up directly in a census keyed by label, so the field answered
    `None` for every arm. A field that reads as measured and is empty is the
    class this file refuses.
    """
    census = {"labels": {"Object": {"stored": 8000, "embedded": 8000},
                         "Point": {"stored": 8000, "embedded": 8000}}}
    got = probe.population_measured(census, ["object", "point"])

    assert got == {"Object": 8000, "Point": 8000}
    # The mapping is the one the probe already uses for the index attempt.
    assert probe.LABEL_BY_ENTITY["object"] == "Object"
    assert probe.LABEL_BY_ENTITY["document"] == "Source"


def test_an_UNREADABLE_census_label_is_None_never_zero():
    """UNMEASURED ≠ 0, here as everywhere else in this file."""
    got = probe.population_measured({"labels": {}}, ["object"])

    assert got == {"Object": None}


# ── the hint override must touch the attribute the shipped read consults ───

def test_the_hint_override_writes_the_BEHAVIOURAL_inventory():
    """`--compare-hint` is only a pair if it moves the attribute the read reads.

    `#13121` round 5 F2: the probe forced and read `_vector_indexed_labels` (the
    REPORTING pair), while every shipped call site forwards
    `_vector_indexed_labels_for_reads` — so both arms ran the identical path and
    printed an "index_hint" that changed nothing.
    """
    import re
    from pathlib import Path

    source = Path("tools/dense_leg_probe_13108.py").read_text(encoding="utf-8")
    written = re.findall(r"proj\.(_vector_indexed_labels[A-Za-z_]*)\s*=", source)
    assert written, "the probe no longer overrides the inventory at all"
    assert set(written) == {"_vector_indexed_labels_for_reads"}, (
        "the hint override writes the REPORTED pair, which the shipped read "
        f"does not consult — the comparison is a no-op: {sorted(set(written))}")
    # …and it READS the same half, so the arms cannot both start from `None`.
    assert "getattr(sdk._get_proj(), \"_vector_indexed_labels_for_reads\", None)" \
        in source, "the probe's `indexed` must come from the behavioural half"
