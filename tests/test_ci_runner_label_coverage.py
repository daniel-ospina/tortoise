"""Red-first: every `runs-on:` label set the workflows request must have a provider.

THE FAILURE THIS PREVENTS (measured 2026-11-04)
    ~98 workflow runs sat queued for hours while 15 x86_64 self-hosted runners idled.
    Every queued job asked for `ubuntu-latest`; the only three runners carrying that label
    were ARM64 — the fallback gate's arm is capped (`FALLBACK_ARM_MAX`, default 3) and
    ordered by ascending runner id, and the ARM pool registered first (ids 28-30 vs the
    x64 pool's 33-59). The fifteen x64 runners could not accept a label they did not
    carry, so they were unreachable capacity; `python-ci-gate` — the required aggregate
    behind every merge — sat `queued` on run after run, so no head could be tested and
    nothing could merge.

    A label nobody answers does not fail. It does not RUN: the job sits `pending` with
    zero materialised jobs, forever — no red, no green, nothing to merge on. That is why
    this is a test and not a note.

THE MODEL
    Each job maps to the label sets it REQUIRES — one per matrix row, because a matrix
    expands into one job instance per row and GitHub runs ALL of them. Within a set it is
    an AND over one runner; every set is independently required.

    ⛔ BOTH HALVES HAVE TO HOLD, in opposite directions:
      * collapse the rows into one UNION and the gate demands a single runner carrying
        `ubuntu-latest` AND `self-hosted` — impossible — so it reds the required suite on
        a correct repo, precisely when the pool is in its documented HEALTHY state (the
        false BLOCK);
      * treat the rows as alternatives where any one suffices and an unanswered row — a
        job that can never start — reports green (the false PASS).
    Each direction has its own test below, and each is written so the other reading fails
    it.

WHY A COMMITTED SNAPSHOT
    CI cannot read the Actions runners API. `config/ci-runner-labels.json` is the
    provider set, refreshed with `uv run python tools/ci_runner_labels.py --capture`.
    Reading it is a READ: no test here writes a runner's labels.

⚠️ A RED HERE MAY BE A STALE SNAPSHOT, NOT A REAL GAP — re-capture before believing it,
   because `ubuntu-latest` is attached/detached by `scripts/ci_runner_gate.sh` on the
   order of minutes. Re-capture is DIAGNOSIS, not a fix: if the live pool really does not
   answer the label, refreshing changes nothing and the red stands.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.ci_runner_labels import (
    MAX_SNAPSHOT_AGE_DAYS,
    SNAPSHOT_PATH,
    UnreadableRunsOn,
    hosted_covers,
    requested_labels,
    staleness,
    violations,
)

ARM = {
    "name": "arm-1",
    "status": "online",
    "labels": ["self-hosted", "Linux", "ARM64", "self-hosted-arm64", "ubuntu-latest"],
}
X64 = {
    "name": "x64-1",
    "status": "online",
    "labels": ["self-hosted", "Linux", "X64", "self-hosted-x64", "ubuntu-latest"],
}
X64_NO_POOL_LABEL = {
    "name": "x64-2",
    "status": "online",
    "labels": ["self-hosted", "Linux", "X64", "self-hosted-x64"],
}
X64_OFFLINE = {
    "name": "x64-3",
    "status": "offline",
    "labels": ["self-hosted", "Linux", "X64", "self-hosted-x64", "ubuntu-latest"],
}
POOL_POOL_SPLIT = [frozenset({"ubuntu-latest"}), frozenset({"self-hosted", "self-hosted-x64"})]


def _snapshot() -> dict:
    assert SNAPSHOT_PATH.is_file(), (
        f"the committed runner snapshot is missing at {SNAPSHOT_PATH}. It is the provider set "
        f"this gate checks against; create it with `uv run python tools/ci_runner_labels.py "
        f"--capture`."
    )
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _workflow(tmp: str, body: str) -> Path:
    (Path(tmp) / "wf.yml").write_text(body, encoding="utf-8")
    return Path(tmp)


def _all_labels(requested: dict[str, list[frozenset[str]]]) -> set[str]:
    return {label for rows in requested.values() for row in rows for label in row}


# ── the committed state ──────────────────────────────────────────────────────────────
def test_the_snapshot_is_recent_and_readable() -> None:
    """A snapshot that is never refreshed certifies a past pool forever — the fail-open."""
    message = staleness(_snapshot())
    assert message is None, message


def test_the_snapshot_actually_covers_a_pool() -> None:
    """An empty or truncated snapshot would pass every other assertion vacuously."""
    runners = _snapshot().get("runners", [])
    assert runners, "the runner snapshot lists no runners — it cannot certify coverage"
    assert any(r.get("status") == "online" for r in runners), (
        "the runner snapshot lists no ONLINE runner — an offline runner cannot be handed a job"
    )


def test_the_workflow_scan_really_finds_the_labels() -> None:
    """A scan that read no `runs-on:` would report 'covered' over work it never looked at."""
    requested = requested_labels()
    assert requested, "no runs-on: label was parsed out of .github/workflows — the scan is blind"
    labels = _all_labels(requested)
    # Named, so a parser regression that silently drops most jobs (leaving one obscure
    # label) is a RED rather than a narrower green.
    assert "ubuntu-latest" in labels, (
        "the scan found no `ubuntu-latest` job — this tree has ~77 of them, so the parser is "
        "missing jobs rather than the tree having changed"
    )
    assert len(requested) >= 50, (
        f"only {len(requested)} job(s) parsed; the tree has ~88 — the parser is dropping jobs"
    )


def test_every_requested_label_set_has_a_provider() -> None:
    """R1 + R2. Failing here means some job can never start on any runner this repo has."""
    problems = violations(_snapshot(), requested_labels())
    assert not problems, (
        "the runner pool cannot answer this tree's own `runs-on:` labels:\n  "
        + "\n  ".join(problems)
        + "\n\nFIRST re-capture (`uv run python tools/ci_runner_labels.py --capture`) to rule out "
        "a stale snapshot. If the live pool really does not answer the label, the fix is a LABEL "
        "on a runner (the gate's job, or a logged operator action — never a hand-written "
        "config.sh registration), or a `runs-on:` naming a label some runner really carries."
    )


# ── FALSE PASS: an unanswered row must red ──────────────────────────────────────────
def test_a_row_no_runner_answers_reds_even_when_another_row_is_served() -> None:
    """A matrix runs EVERY row, so a served row does not excuse an unanswered one.

    This is the 2026-11-04 mechanism: the pool-split row (`self-hosted` +
    `self-hosted-x64`) is a job instance in its own right. If no runner carries it, that
    instance sits `pending` forever and the required aggregate never reports — the whole
    outage — while `ubuntu-latest` (hosted) is perfectly fine.
    """
    problems = violations({"runners": [ARM]}, {"python-ci.yml:test": POOL_POOL_SPLIT})
    assert any("R1" in p and "self-hosted-x64" in p for p in problems), problems


def test_zero_runners_reds_on_the_pool_split_row() -> None:
    """With NO self-hosted runner at all, the pool-split row is unanswerable.

    A gate that treated the rows as alternatives would be green here (hosted answers
    `ubuntu-latest`), certifying every odd shard as schedulable when none can start.
    """
    assert violations({"runners": []}, {"python-ci.yml:test": POOL_POOL_SPLIT}), (
        "an empty pool cannot serve the self-hosted row — this must not be green"
    )


def test_a_matrix_that_is_unsatisfiable_on_every_row_reds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n"
            "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
            "    strategy:\n      matrix:\n        include:\n"
            '          - runner: \'["self-hosted", "self-hosted-arm64"]\'\n'
            '          - runner: \'["self-hosted", "no-such-label"]\'\n'
            "    steps: []\n",
        )
        problems = violations({"runners": [X64]}, requested_labels(Path(tmp)))
    assert any("R1" in p for p in problems), problems


# ── FALSE BLOCK: a correct repo must not red ───────────────────────────────────────
def test_rows_are_checked_as_separate_sets_not_one_union() -> None:
    """Unioning the two routings would demand one runner carrying

    `ubuntu-latest` AND `self-hosted`, which no runner can be — so a correct pool would
    red. Both rows are answerable here (hosted for one, the x64 pool for the other).
    """
    requested = {"python-ci.yml:test": POOL_POOL_SPLIT}
    assert not violations({"runners": [X64_NO_POOL_LABEL]}, requested), (
        "the pool does not claim ubuntu-latest (the documented HEALTHY state) and must not red: "
        "hosted serves that row, the x64 runner serves the other"
    )


def test_a_matrix_rows_are_read_per_row_not_union_into_one_set() -> None:
    """A hand-written two-row split matrix must not become one unsatisfiable conjunction."""
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n"
            "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
            "    strategy:\n      matrix:\n        include:\n"
            '          - runner: \'["self-hosted", "self-hosted-arm64"]\'\n'
            '          - runner: \'["self-hosted", "self-hosted-x64"]\'\n'
            "    steps: []\n",
        )
        requested = requested_labels(Path(tmp))
    assert not violations({"runners": [ARM, X64]}, requested), (
        "both rows are answerable — a union reading would red this correct pool"
    )
    assert len(requested["wf.yml:j"]) == 2, requested


def test_every_workflow_in_the_tree_parses_without_refusing() -> None:
    """A refusal fired by a workflow nobody edited would red the suite for the whole tree.

    `requested_labels()` on the real tree must complete; the read is what the committed
    test above then verdicted.
    """
    assert requested_labels(), "the tree's workflows did not parse"


# ── the provider side: ONLINE only ────────────────────────────────────────────────
def test_an_offline_runner_is_not_a_provider() -> None:
    """GitHub cannot hand a job to an offline runner.

    Without this filter a snapshot whose only carrier is offline reports the job
    schedulable — the "7 of 7 with every carrier offline" false pass the gate exists for.
    """
    assert not violations(
        {"runners": [X64_OFFLINE]}, {"wf.yml:j": [frozenset({"ubuntu-latest"})]}
    ), "a single-image row is still served by GitHub-hosted"
    problems = violations(
        {"runners": [X64_OFFLINE]},
        {"wf.yml:j": [frozenset({"self-hosted", "self-hosted-x64"})]},
    )
    assert any("R1" in p for p in problems), (
        "the only carrier is OFFLINE, so no runner can take this row — it must red"
    )


# ── refusals: unreadable input is refused, never cleared ─────────────────────────────
def test_an_unresolvable_runs_on_is_refused_not_cleared() -> None:
    """An expression this tool cannot read must RAISE, never count as covered."""
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n"
            "    runs-on: ${{ inputs.runner || 'ubuntu-latest' }}\n"
            "    steps: []\n",
        )
        with pytest.raises(UnreadableRunsOn):
            requested_labels(Path(tmp))


def test_a_computed_matrix_under_another_key_is_refused_not_substituted() -> None:
    """Only the shard selector's `matrix.runner` has rows this tool knows.

    Certifying `matrix.arch` against the shard routings would report green over labels the
    rows never ask for, and leave the labels they DO ask for unchecked.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n"
            "    runs-on: ${{ fromJSON(matrix.arch) }}\n"
            "    strategy:\n      matrix: ${{ fromJSON(needs.x.outputs.arch_matrix) }}\n"
            "    steps: []\n",
        )
        with pytest.raises(UnreadableRunsOn):
            requested_labels(Path(tmp))


def test_the_dynamic_matrix_expression_resolves_to_both_routings() -> None:
    """This tree's `test` job: the matrix is emitted outside the workflow."""
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  test:\n"
            "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
            "    strategy:\n      matrix: ${{ fromJSON(needs.changes.outputs.fast_matrix) }}\n"
            "    steps: []\n",
        )
        requested = requested_labels(Path(tmp))
    rows = requested["wf.yml:test"]
    assert frozenset({"ubuntu-latest"}) in rows, rows
    assert frozenset({"self-hosted", "self-hosted-x64"}) in rows, rows
    assert len(rows) == 2, "the routings are SEPARATE rows, never one union"


def test_the_bare_matrix_idiom_is_read_not_refused() -> None:
    """`runs-on: ${{ matrix.os }}` is the canonical idiom and must not false-block.

    Refusing it would red the required suite on a tree whose every label is answerable,
    with a message blaming the label set rather than the reader.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n"
            "    runs-on: ${{ matrix.os }}\n"
            "    strategy:\n      matrix:\n        include:\n"
            "          - os: ubuntu-latest\n"
            "          - os: ubuntu-24.04\n"
            "    steps: []\n",
        )
        requested = requested_labels(Path(tmp))
    assert not violations({"runners": []}, requested), (
        "both rows are documented hosted images — no pool runner needed"
    )
    assert {frozenset({"ubuntu-latest"}), frozenset({"ubuntu-24.04"})} == set(requested["wf.yml:j"])


def test_a_spaced_matrix_expression_is_read_not_crashed() -> None:
    """`fromJSON( matrix.runner )` is valid YAML and must not crash the gate.

    A previous revision tested a space-stripped string to DETECT the expression but then
    split the ORIGINAL, so the spaced spelling raised `IndexError` and escaped `main()` —
    a traceback instead of a verdict.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n"
            "    runs-on: ${{ fromJSON( matrix.runner ) }}\n"
            "    strategy:\n      matrix: ${{ fromJSON(needs.changes.outputs.fast_matrix) }}\n"
            "    steps: []\n",
        )
        requested = requested_labels(Path(tmp))
    assert frozenset({"ubuntu-latest"}) in requested["wf.yml:j"], requested


def test_a_visible_matrix_without_the_key_is_refused_not_substituted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n"
            "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
            "    strategy:\n      matrix:\n        include:\n"
            '          - other: \'["self-hosted", "no-such-label"]\'\n'
            "    steps: []\n",
        )
        with pytest.raises(UnreadableRunsOn):
            requested_labels(Path(tmp))


def test_an_axes_matrix_is_refused_rather_than_guessed() -> None:
    """Axis-generated combinations are not in the file, and an `include` entry adds a
    further combination rather than merging — so the real rows cannot be recovered."""
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n"
            "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
            "    strategy:\n      matrix:\n        runner: ['[\"self-hosted\"]']\n"
            "        include:\n          - runner: '[\"ubuntu-latest\"]'\n"
            "    steps: []\n",
        )
        with pytest.raises(UnreadableRunsOn):
            requested_labels(Path(tmp))


def test_a_group_only_runs_on_is_readable_and_contributes_no_label() -> None:
    """`runs-on: {group: X}` is a VALID form — a runner GROUP, not a label.

    It must not raise: one correctly-scheduled group-only job would otherwise red the
    whole tree and block every merge.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n    runs-on:\n      group: my-group\n    steps: []\n",
        )
        assert requested_labels(Path(tmp)) == {}


# ── R2: architecture ───────────────────────────────────────────────────────────────
def test_r2_reds_when_an_x86_image_is_claimed_only_by_non_x64_hosts() -> None:
    """The 2026-11-04 state: the pool claims `ubuntu-latest`, no provider is X64."""
    problems = violations({"runners": [ARM]}, {"wf.yml:docs": [frozenset({"ubuntu-latest"})]})
    assert any("R2" in p and "ubuntu-latest" in p for p in problems), problems


def test_r2_greens_once_an_x64_runner_answers_the_image() -> None:
    problems = violations({"runners": [ARM, X64]}, {"wf.yml:docs": [frozenset({"ubuntu-latest"})]})
    assert not [p for p in problems if "R2" in p], problems


# ── the snapshot's own anti-fail-open guards ───────────────────────────────────────
def test_a_future_dated_snapshot_cannot_disable_the_staleness_control() -> None:
    """An unguarded negative age lets a clock-skewed capture certify for a year."""
    future = (datetime.now(UTC) + timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert staleness({"captured_at": future}) is not None


def test_a_snapshot_past_the_age_bound_reds() -> None:
    old = (datetime.now(UTC) - timedelta(days=MAX_SNAPSHOT_AGE_DAYS + 1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert staleness({"captured_at": old}) is not None


def test_the_age_bound_stays_meaningful() -> None:
    """The bound is the ONLY revalidation this gate has.

    A fixture built from the constant cannot pin it, so raising it to a decade would keep
    every other test green while the gate certified a ten-year-old pool.
    """
    assert MAX_SNAPSHOT_AGE_DAYS <= 180, (
        f"MAX_SNAPSHOT_AGE_DAYS={MAX_SNAPSHOT_AGE_DAYS} is too long to revalidate a label that "
        f"scripts/ci_runner_gate.sh attaches and detaches on the order of minutes"
    )
    assert MAX_SNAPSHOT_AGE_DAYS >= 7, "a bound under a week would red a quiet repo constantly"


def test_hosted_images_are_named_explicitly_not_pattern_matched() -> None:
    """A `macos-*` pattern would silently cover a label nobody offers."""
    assert hosted_covers("ubuntu-latest")
    assert not hosted_covers("macos-99")
    assert not hosted_covers("my-gpu-box")
