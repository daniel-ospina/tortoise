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

WHAT IS CHECKED (see `tools/ci_runner_labels.py` for the rules and their reasoning)
    R1  no provider for a job's label SET          -> RED   (AND semantics, not per-label)
    R2  an x86_64 image claimed only by non-X64 hosts -> RED
    and the snapshot itself must be recent, non-empty and not future-dated, or it
    certifies nothing at all.

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

REPO_ROOT = Path(__file__).resolve().parent.parent


def _snapshot() -> dict:
    assert SNAPSHOT_PATH.is_file(), (
        f"the committed runner snapshot is missing at {SNAPSHOT_PATH}. It is the provider set "
        f"this gate checks against; create it with `uv run python tools/ci_runner_labels.py "
        f"--capture`."
    )
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _write_workflow(tmp: str, body: str) -> Path:
    path = Path(tmp) / "wf.yml"
    path.write_text(body, encoding="utf-8")
    return Path(tmp)


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
    labels = {label for labels in requested for label in labels}
    # Named, so a parser regression that silently drops most jobs (leaving one obscure
    # label) is a RED rather than a narrower green.
    assert "ubuntu-latest" in labels, (
        "the scan found no `ubuntu-latest` job — this tree has ~77 of them, so the parser is "
        "missing jobs rather than the tree having changed"
    )
    referenced = sum(len(jobs) for jobs in requested.values())
    assert referenced >= 50, f"only {referenced} job label references parsed; the tree has ~90"


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


# ── the rules themselves, on fixtures ───────────────────────────────────────────────
ARM_ONLY_POOL = {
    "runners": [
        {
            "name": "arm-1",
            "status": "online",
            "labels": ["self-hosted", "Linux", "ARM64", "self-hosted-arm64", "ubuntu-latest"],
        },
    ]
}
X64_PLUS_ARM_POOL = {
    "runners": ARM_ONLY_POOL["runners"]
    + [
        {
            "name": "x64-1",
            "status": "online",
            "labels": ["self-hosted", "Linux", "X64", "self-hosted-x64", "ubuntu-latest"],
        }
    ]
}


def test_r2_reds_when_an_x86_image_is_claimed_only_by_non_x64_hosts() -> None:
    """The 2026-11-04 state: the pool claims `ubuntu-latest`, no provider is X64."""
    problems = violations(ARM_ONLY_POOL, {frozenset({"ubuntu-latest"}): {"wf.yml:docs"}})
    assert any("R2" in p and "ubuntu-latest" in p for p in problems), problems


def test_r2_greens_once_an_x64_runner_answers_the_image() -> None:
    problems = violations(X64_PLUS_ARM_POOL, {frozenset({"ubuntu-latest"}): {"wf.yml:docs"}})
    assert not [p for p in problems if "R2" in p], problems


def test_r1_uses_and_semantics_not_per_label_coverage() -> None:
    """Every label has a provider individually, but no ONE runner carries the whole set.

    `runs-on` is an AND over a single runner, so per-label coverage would call this
    satisfiable — while the job can never be scheduled. This is the shape the 2026-11-04
    gate would have missed had it only counted carriers.
    """
    split = {
        "runners": [
            {"name": "arm", "status": "online", "labels": ["self-hosted", "self-hosted-arm64"]},
            {"name": "x64", "status": "online", "labels": ["self-hosted", "self-hosted-x64"]},
        ]
    }
    wanted = frozenset({"self-hosted", "self-hosted-x64", "self-hosted-arm64"})
    problems = violations(split, {wanted: {"wf.yml:j"}})
    assert any("R1" in p and "AND" in p for p in problems), problems


def test_r1_reds_on_a_label_with_zero_providers() -> None:
    problems = violations(
        X64_PLUS_ARM_POOL, {frozenset({"self-hosted", "self-hosted-x64"}): {"wf.yml:j"}}
    )
    assert not problems, problems  # satisfiable by x64-1: the fixture is not over-redding
    problems = violations({"runners": []}, {frozenset({"ubuntu-24.04"}): {"wf.yml:lint"}})
    assert not problems, "a single documented hosted image needs no pool provider"
    problems = violations({"runners": []}, {frozenset({"ubuntu-2204-tools"}): {"wf.yml:lint"}})
    assert any("R1" in p for p in problems), "a custom label with zero providers must red"


# ── refusals and the anti-fail-open guards ──────────────────────────────────────────
def test_an_unresolvable_runs_on_is_refused_not_cleared() -> None:
    """A `runs-on:` this tool cannot read must RAISE, never count as covered.

    Treating an unparsed expression as covered is how a coverage gate reports green over a
    job it never read. `scripts/ci_runner_gate.sh --status` refuses the same input (exit 4)
    for the same reason.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write_workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n    runs-on: ${{ matrix.whatever }}\n    steps: []\n",
        )
        with pytest.raises(UnreadableRunsOn):
            requested_labels(Path(tmp))


def test_a_group_only_runs_on_is_readable_and_contributes_no_label() -> None:
    """`runs-on: {group: X}` is a VALID form — a runner GROUP, not a label.

    It must not raise: one correctly-scheduled group-only job would otherwise red the
    whole tree and block every merge.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write_workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n    runs-on:\n      group: my-group\n    steps: []\n",
        )
        assert requested_labels(Path(tmp)) == {}


def test_the_dynamic_matrix_expression_resolves_to_its_label_union() -> None:
    """`${{ fromJSON(matrix.runner) }}` over a matrix emitted OUTSIDE the workflow.

    It resolves to the union over both routings, so whichever routing is configured is
    covered. This tree's `test` job is exactly this shape
    (`matrix: ${{ fromJSON(needs.changes.outputs.fast_matrix) }}`).
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write_workflow(
            tmp,
            "name: t\non: push\njobs:\n  test:\n"
            "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
            "    strategy:\n      matrix: ${{ fromJSON(needs.changes.outputs.fast_matrix) }}\n"
            "    steps: []\n",
        )
        requested = requested_labels(Path(tmp))
    labels = {label for labels in requested for label in labels}
    assert "ubuntu-latest" in labels, requested
    assert "self-hosted-x64" in labels, requested


def test_a_static_matrix_is_read_rather_than_assumed() -> None:
    """With a static `include`, the job's REAL label array is read — not a guessed union.

    Resolving the expression by substring alone would invent a label set for any future
    workflow that copies `fromJSON(matrix.runner)` over a hand-written matrix, and the
    labels that job really requests would go unchecked.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write_workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n"
            "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
            "    strategy:\n      matrix:\n        include:\n"
            "          - half: a\n"
            '            runner: \'["self-hosted", "self-hosted-arm64"]\'\n'
            "    steps: []\n",
        )
        requested = requested_labels(Path(tmp))
    labels = {label for labels in requested for label in labels}
    assert labels == {"self-hosted", "self-hosted-arm64"}, labels


def test_a_future_dated_snapshot_cannot_disable_the_staleness_control() -> None:
    """An unguarded negative age lets a clock-skewed capture certify for a year."""
    future = (datetime.now(UTC) + timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert staleness({"captured_at": future}) is not None


def test_a_snapshot_past_the_age_bound_reds() -> None:
    old = (datetime.now(UTC) - timedelta(days=MAX_SNAPSHOT_AGE_DAYS + 1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert staleness({"captured_at": old}) is not None


def test_hosted_images_are_named_explicitly_not_pattern_matched() -> None:
    """A `macos-*` pattern would silently cover a label nobody offers."""
    assert hosted_covers("ubuntu-latest")
    assert not hosted_covers("macos-99")
    assert not hosted_covers("my-gpu-box")
