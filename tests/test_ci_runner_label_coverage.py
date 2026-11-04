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
    Each direction has its own fixtures below, each written so the other reading fails it.

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
    reusable_workflow_jobs,
    staleness,
    violations,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

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
POOL_SPLIT_ROWS = [frozenset({"ubuntu-latest"}), frozenset({"self-hosted", "self-hosted-x64"})]


def _snapshot() -> dict:
    assert SNAPSHOT_PATH.is_file(), (
        f"the committed runner snapshot is missing at {SNAPSHOT_PATH}. It is the provider set "
        f"this gate checks against; create it with `uv run python tools/ci_runner_labels.py "
        f"--capture`."
    )
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _workflow(tmp: str, name: str, body: str) -> Path:
    (Path(tmp) / name).write_text(body, encoding="utf-8")
    return Path(tmp)


def _shard_workflow(tmp: str, matrix: str) -> Path:
    """The ONLY job whose computed matrix this gate may resolve: python-ci.yml:test."""
    body = (
        "name: t\n"
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
        "    strategy:\n"
        f"      matrix: {matrix}\n"
        "    steps: []\n"
    )
    return _workflow(tmp, "python-ci.yml", body)


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
    assert "self-hosted-x64" in labels, (
        "the scan found no `self-hosted-x64` job — ci.yml's repo-harnesses AND row is the tree's "
        "only list-form runs-on, and dropping it would silently leave the AND semantics unread"
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
    problems = violations({"runners": [ARM]}, {"python-ci.yml:test": POOL_SPLIT_ROWS})
    assert any("R1" in p and "self-hosted-x64" in p for p in problems), problems


def test_zero_runners_reds_on_the_pool_split_row() -> None:
    """With NO self-hosted runner at all, the pool-split row is unanswerable.

    A gate that treated the rows as alternatives would be green here (hosted answers
    `ubuntu-latest`), certifying every odd shard as schedulable when none can start.
    """
    assert violations({"runners": []}, {"python-ci.yml:test": POOL_SPLIT_ROWS}), (
        "an empty pool cannot serve the self-hosted row — this must not be green"
    )


def test_one_unanswerable_row_reds_while_the_other_row_is_served() -> None:
    """The DISCRIMINATING fixture: one row answerable, one not.

    Written this way on purpose. With BOTH rows unanswerable the test would pass under the
    correct reading, the alternatives reading AND a first-row-only reading alike — so it
    would pin nothing. Here only "every row is required" reds.
    """
    body = (
        "name: t\n"
        "on: push\n"
        "jobs:\n"
        "  j:\n"
        "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
        "    strategy:\n"
        "      matrix:\n"
        "        include:\n"
        '          - runner: \'["self-hosted", "self-hosted-x64"]\'\n'
        '          - runner: \'["self-hosted", "no-such-label"]\'\n'
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        problems = violations({"runners": [X64]}, requested_labels(_workflow(tmp, "wf.yml", body)))
    assert any("R1" in p and "no-such-label" in p for p in problems), problems


# ── FALSE BLOCK: a correct repo must not red ───────────────────────────────────────
def test_rows_are_checked_as_separate_sets_not_one_union() -> None:
    """Unioning the two routings would demand one runner carrying

    `ubuntu-latest` AND `self-hosted`, which no runner can be — so a correct pool would
    red. Both rows are answerable here (hosted for one, the x64 pool for the other).
    """
    requested = {"python-ci.yml:test": POOL_SPLIT_ROWS}
    assert not violations({"runners": [X64_NO_POOL_LABEL]}, requested), (
        "the pool does not claim ubuntu-latest (the documented HEALTHY state) and must not red: "
        "hosted serves that row, the x64 runner serves the other"
    )


def test_the_list_form_of_runs_on_is_read_as_one_and_set() -> None:
    """`runs-on: [a, b]` is the AND semantics R1 is built on, and this tree uses it.

    Without this fixture the list branch could return [] (silently treated as group-only):
    `ci.yml:repo-harnesses` — the tree's only AND row — would vanish from the scan and
    every test here would stay green.
    """
    body = "name: t\non: push\njobs:\n  j:\n    runs-on: [self-hosted, self-hosted-x64]\n    steps: []\n"
    with tempfile.TemporaryDirectory() as tmp:
        requested = requested_labels(_workflow(tmp, "wf.yml", body))
    assert requested == {"wf.yml:j": [frozenset({"self-hosted", "self-hosted-x64"})]}, requested
    assert not violations({"runners": [X64_NO_POOL_LABEL]}, requested)
    assert violations({"runners": [ARM]}, requested), "the ARM runner lacks self-hosted-x64"


def test_a_hand_written_two_row_matrix_is_read_per_row() -> None:
    body = (
        "name: t\n"
        "on: push\n"
        "jobs:\n"
        "  j:\n"
        "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
        "    strategy:\n"
        "      matrix:\n"
        "        include:\n"
        '          - runner: \'["self-hosted", "self-hosted-arm64"]\'\n'
        '          - runner: \'["self-hosted", "self-hosted-x64"]\'\n'
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        requested = requested_labels(_workflow(tmp, "wf.yml", body))
    assert not violations({"runners": [ARM, X64]}, requested), (
        "both rows are answerable — a union reading would red this correct pool"
    )
    assert len(requested["wf.yml:j"]) == 2, requested


def test_every_workflow_in_the_tree_parses_without_refusing() -> None:
    """A refusal fired by a workflow nobody edited would red the suite for the whole tree."""
    assert requested_labels(), "the tree's workflows did not parse"


# ── the provider side ──────────────────────────────────────────────────────────────
def test_an_offline_runner_is_not_a_provider() -> None:
    """GitHub cannot hand a job to an offline runner.

    Without this filter a snapshot whose only carrier is offline reports the job
    schedulable — the "7 of 7 with every carrier offline" false pass the gate exists for.
    """
    problems = violations(
        {"runners": [X64_OFFLINE]},
        {"wf.yml:j": [frozenset({"self-hosted", "self-hosted-x64"})]},
    )
    assert any("R1" in p for p in problems), (
        "the only carrier is OFFLINE, so no runner can take this row — it must red"
    )


def test_label_matching_is_case_insensitive() -> None:
    """GitHub matches labels case-insensitively (this repo pins that in runner_autoscale).

    Comparing exactly would red a pool that CAN schedule the job, for a spelling
    difference, and the message would blame the label set.
    """
    weird = {
        "name": "x64-odd",
        "status": "online",
        "labels": ["Self-Hosted", "LINUX", "x64", "Self-Hosted-X64"],
    }
    requested = {"wf.yml:j": [frozenset({"self-hosted", "self-hosted-x64"})]}
    assert not violations({"runners": [weird]}, requested)


# ── refusals: unreadable input is refused, never cleared ─────────────────────────────
def test_an_unresolvable_runs_on_is_refused_not_cleared() -> None:
    """An expression this tool cannot read must RAISE, never count as covered."""
    body = (
        "name: t\non: push\njobs:\n  j:\n"
        "    runs-on: ${{ inputs.runner || 'ubuntu-latest' }}\n    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(UnreadableRunsOn):
        requested_labels(_workflow(tmp, "wf.yml", body))


def test_a_partial_expression_is_refused_rather_than_stripped() -> None:
    """A PARTIAL expression must be refused, never resolved.

    Resolving only the expression discards the literal prefix, so the gate would certify a
    label nobody asked for while the real one has no provider.
    """
    body = (
        "name: t\non: push\njobs:\n  j:\n"
        "    runs-on: 'pool-${{ matrix.os }}'\n"
        "    strategy:\n      matrix:\n        include:\n          - os: ubuntu-latest\n"
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(UnreadableRunsOn):
        requested_labels(_workflow(tmp, "wf.yml", body))


def test_a_computed_matrix_under_another_key_is_refused_not_substituted() -> None:
    """Only the shard selector's `matrix.runner` (in its own job) has known rows.

    Certifying `matrix.arch` against the shard routings would report green over labels the
    rows never ask for, and leave the labels they DO ask for unchecked.
    """
    body = (
        "name: t\non: push\njobs:\n  j:\n"
        "    runs-on: ${{ fromJSON(matrix.arch) }}\n"
        "    strategy:\n      matrix: ${{ fromJSON(needs.x.outputs.arch_matrix) }}\n"
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(UnreadableRunsOn):
        requested_labels(_workflow(tmp, "wf.yml", body))


def test_a_computed_matrix_is_not_resolved_by_key_name_in_a_foreign_job() -> None:
    """A key named `runner` in ANOTHER workflow must not be certified as the shard rows."""
    body = (
        "name: t\non: push\njobs:\n  test:\n"
        "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
        "    strategy:\n      matrix: ${{ fromJSON(needs.plan.outputs.rows) }}\n"
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(UnreadableRunsOn):
        requested_labels(_workflow(tmp, "other.yml", body))


def test_a_visible_matrix_without_the_key_is_refused_not_substituted() -> None:
    body = (
        "name: t\non: push\njobs:\n  j:\n"
        "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
        "    strategy:\n      matrix:\n        include:\n"
        '          - other: \'["self-hosted", "no-such-label"]\'\n'
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(UnreadableRunsOn):
        requested_labels(_workflow(tmp, "wf.yml", body))


def test_axes_mixed_with_include_are_refused() -> None:
    """An `include` entry that cannot merge into an axis combination ADDS one, so the real
    row set cannot be recovered from the file."""
    body = (
        "name: t\non: push\njobs:\n  j:\n"
        "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
        "    strategy:\n      matrix:\n"
        "        runner: ['[\"self-hosted\"]']\n"
        "        include:\n          - runner: '[\"ubuntu-latest\"]'\n"
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(UnreadableRunsOn):
        requested_labels(_workflow(tmp, "wf.yml", body))


# ── the readable forms ─────────────────────────────────────────────────────────────
def test_the_dynamic_matrix_expression_resolves_to_both_routings() -> None:
    """This tree's `test` job: the matrix is emitted outside the workflow."""
    with tempfile.TemporaryDirectory() as tmp:
        requested = requested_labels(
            _shard_workflow(tmp, "${{ fromJSON(needs.changes.outputs.fast_matrix) }}")
        )
    rows = requested["python-ci.yml:test"]
    assert frozenset({"ubuntu-latest"}) in rows, rows
    assert frozenset({"self-hosted", "self-hosted-x64"}) in rows, rows
    assert len(rows) == 2, "the routings are SEPARATE rows, never one union"


def test_the_bare_matrix_idiom_is_read_not_refused() -> None:
    """`runs-on: ${{ matrix.os }}` is the canonical idiom and must not false-block.

    Refusing it would red the required suite on a tree whose every label is answerable,
    with a message blaming the label set rather than the reader.
    """
    body = (
        "name: t\non: push\njobs:\n  j:\n"
        "    runs-on: ${{ matrix.os }}\n"
        "    strategy:\n      matrix:\n        include:\n"
        "          - os: ubuntu-latest\n"
        "          - os: ubuntu-24.04\n"
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        requested = requested_labels(_workflow(tmp, "wf.yml", body))
    assert not violations({"runners": []}, requested), (
        "both rows are documented hosted images — no pool runner needed"
    )
    assert set(requested["wf.yml:j"]) == {
        frozenset({"ubuntu-latest"}),
        frozenset({"ubuntu-24.04"}),
    }, requested


def test_an_axes_only_matrix_is_read_from_its_own_axis() -> None:
    """The axis values are literal lists IN THE FILE, so refusing them would be a false block."""
    body = (
        "name: t\non: push\njobs:\n  j:\n"
        "    runs-on: ${{ matrix.os }}\n"
        "    strategy:\n      matrix:\n        os: [ubuntu-latest, ubuntu-24.04]\n"
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        requested = requested_labels(_workflow(tmp, "wf.yml", body))
    assert set(requested["wf.yml:j"]) == {
        frozenset({"ubuntu-latest"}),
        frozenset({"ubuntu-24.04"}),
    }, requested
    assert not violations({"runners": []}, requested), "both are documented hosted images"


def test_a_group_only_runs_on_is_readable_and_contributes_no_label() -> None:
    """`runs-on: {group: X}` is a VALID form — a runner GROUP, not a label.

    It must not raise: one correctly-scheduled group-only job would otherwise red the
    whole tree and block every merge.
    """
    body = "name: t\non: push\njobs:\n  j:\n    runs-on:\n      group: my-group\n    steps: []\n"
    with tempfile.TemporaryDirectory() as tmp:
        assert requested_labels(_workflow(tmp, "wf.yml", body)) == {}


# ── R2: architecture ───────────────────────────────────────────────────────────────
def test_r2_reds_when_an_x86_image_is_claimed_only_by_non_x64_hosts() -> None:
    """The 2026-11-04 state: the pool claims `ubuntu-latest`, no provider is X64."""
    problems = violations({"runners": [ARM]}, {"wf.yml:docs": [frozenset({"ubuntu-latest"})]})
    assert any("R2" in p and "ubuntu-latest" in p for p in problems), problems


def test_r2_greens_once_an_x64_runner_answers_the_image() -> None:
    problems = violations({"runners": [ARM, X64]}, {"wf.yml:docs": [frozenset({"ubuntu-latest"})]})
    assert not [p for p in problems if "R2" in p], problems


def test_r2_reds_on_a_mixed_case_x86_image_too() -> None:
    """RED-FIRST (pre-fix: `[]`). R2's intersection must go through `_norm`.

    `every_label` holds the spelling the WORKFLOW used; `X86_IMAGE_LABELS` is lowercase. A
    raw `X86_IMAGE_LABELS & every_label` drops `runs-on: Ubuntu-Latest`, so R2 is never
    evaluated for it — while R1 answers the SAME row case-insensitively, so the row counts
    as served and the 2026-11-04 state (an x86_64 image claimed only by ARM hosts) reports
    GREEN. GitHub matches labels case-insensitively, so the spelling is not something the
    workflow author can be expected to get exactly right.
    """
    problems = violations({"runners": [ARM]}, {"wf.yml:d": [frozenset({"Ubuntu-Latest"})]})
    assert any("R2" in p for p in problems), (
        "an x86_64 image spelled in mixed case is still an x86_64 image — R1 served the row "
        f"case-insensitively, so R2 must judge it too; got {problems}"
    )
    assert any("Ubuntu-Latest" in p for p in problems), (
        f"the message must quote the tree's own spelling, not the constant's; got {problems}"
    )


def test_r2_reports_one_verdict_per_image_not_per_spelling() -> None:
    """Two spellings of ONE image are one architecture question, not two violations."""
    problems = violations(
        {"runners": [ARM]},
        {"wf.yml:d": [frozenset({"ubuntu-latest"}), frozenset({"Ubuntu-Latest"})]},
    )
    assert len([p for p in problems if "R2" in p]) == 1, problems


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


def test_every_reusable_workflow_job_is_acknowledged() -> None:
    """A job-level `uses:` takes its `runs-on:` from ANOTHER repo, so this gate cannot read it.

    Skipping those jobs in silence makes "every label set is covered" a claim about an
    un-enumerated population: `agent-infra` moving one of its reusable workflows onto a
    label this pool cannot answer would leave those jobs `pending` forever while this suite
    stayed green. The set is therefore PINNED — a new reusable-workflow job reds here until
    someone either records its labels or records why it is out of scope.
    """
    expected = {
        "ci.yml:dashboard-js-tests",
        "ci.yml:agent-infra-ci",
        "ci.yml:drift-check",
        "main-health-nightly.yml:main-health",
    }
    actual = set(reusable_workflow_jobs())
    assert actual == expected, (
        "the set of reusable-workflow jobs changed:\n"
        f"  added:   {sorted(actual - expected)}\n"
        f"  removed: {sorted(expected - actual)}\n"
        "Those jobs' `runs-on:` lives in another repo and this gate cannot read it. Add the "
        "labels by hand or record why the job is out of scope, then update this set."
    )


def test_two_matrix_expressions_in_one_runs_on_are_refused_not_multiplied() -> None:
    """A matrix runs ONE combination per row — cross-producting invents phantom rows.

    `runs-on: ['${{ matrix.os }}', '${{ matrix.arch }}']` over a 2x2 matrix has as many
    rows as the matrix, not the product of the two expressions'. Multiplying produces label
    PAIRS no row asks for, and those phantom rows can red a correct repo.
    """
    body = (
        "name: t\non: push\njobs:\n  j:\n"
        "    runs-on: ['${{ matrix.os }}', '${{ matrix.arch }}']\n"
        "    strategy:\n      matrix:\n        os: [ubuntu-latest, macos-latest]\n"
        "        arch: [x64-hosted, arm64-hosted]\n"
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(UnreadableRunsOn):
        requested_labels(_workflow(tmp, "wf.yml", body))


def test_a_row_missing_the_key_is_refused_not_dropped() -> None:
    """Dropping such a row would report "every row answers" for rows never read.

    Same verdict as the all-rows-missing case, so one input cannot get two answers.
    """
    body = (
        "name: t\non: push\njobs:\n  j:\n"
        "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
        "    strategy:\n      matrix:\n        include:\n"
        "          - runner: '[\"ubuntu-latest\"]'\n"
        '          - other: \'["self-hosted", "no-such-label"]\'\n'
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(UnreadableRunsOn):
        requested_labels(_workflow(tmp, "wf.yml", body))


def test_an_empty_label_set_is_refused_not_answerable() -> None:
    """An empty set would be satisfied by ANY online runner (`wanted <= labels`)."""
    body = (
        "name: t\non: push\njobs:\n  j:\n"
        "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
        "    strategy:\n      matrix:\n        include:\n"
        "          - runner: '[]'\n"
        "    steps: []\n"
    )
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(UnreadableRunsOn):
        requested_labels(_workflow(tmp, "wf.yml", body))
