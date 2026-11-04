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

BOTH DIRECTIONS ARE PINNED, because either one makes the gate useless:
    * a FALSE PASS reports green over a job that can never start (R1/R2 must fire);
    * a FALSE BLOCK reds the required suite on a correct repo — e.g. by treating a
      matrix's ALTERNATIVE rows as one conjunction, or a run whose pool is in its
      documented healthy state (no self-hosted runner claiming `ubuntu-latest`).

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
    return {label for alts in requested.values() for alt in alts for label in alt}


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


# ── R1: AND semantics ───────────────────────────────────────────────────────────────
def test_r1_uses_and_semantics_not_per_label_coverage() -> None:
    """Every label has a provider individually, but no ONE runner carries the whole set.

    `runs-on` is an AND over a single runner, so per-label coverage would call this
    satisfiable — while the job can never be scheduled.
    """
    split = {
        "runners": [
            {"name": "arm", "status": "online", "labels": ["self-hosted", "self-hosted-arm64"]},
            {"name": "x64", "status": "online", "labels": ["self-hosted", "self-hosted-x64"]},
        ]
    }
    wanted = frozenset({"self-hosted", "self-hosted-x64", "self-hosted-arm64"})
    problems = violations(split, {"wf.yml:j": [wanted]})
    assert any("R1" in p and "AND" in p for p in problems), problems


def test_r1_reds_on_a_label_with_zero_providers() -> None:
    assert not violations({"runners": [X64]}, {"wf.yml:j": [frozenset({"ubuntu-latest"})]})
    assert not violations({"runners": []}, {"wf.yml:lint": [frozenset({"ubuntu-latest"})]}), (
        "a single documented hosted image needs no pool provider"
    )
    assert any(
        "R1" in p
        for p in violations({"runners": []}, {"wf.yml:l": [frozenset({"ubuntu-2204-tools"})]})
    ), "a custom label with zero providers must red"


# ── the false-block directions (this is what makes the gate usable) ─────────────────
def test_r1_does_not_red_when_the_pool_does_not_claim_ubuntu_latest() -> None:
    """The documented HEALTHY pool state must not red the required suite.

    `tools/ci_selection.fast_shard_runner()` returns `["ubuntu-latest"]` for some rows and
    `["self-hosted","self-hosted-x64"]` for others — ALTERNATIVES. Collapsing them into one
    set would demand a runner carrying `ubuntu-latest` AND `self-hosted`, which is
    impossible, so the gate would red exactly when the pool is healthy (#6897's state) and
    push an operator back into the outage-fallback state.
    """
    healthy_pool = {"runners": [X64_NO_POOL_LABEL]}
    alternatives = [frozenset({"ubuntu-latest"}), frozenset({"self-hosted", "self-hosted-x64"})]
    assert not violations(healthy_pool, {"python-ci.yml:test": alternatives})


def test_matrix_rows_are_read_as_alternatives_not_one_conjunction() -> None:
    """Two disjoint, individually-satisfiable rows must not be unioned into one AND set."""
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
            "two schedulable rows must not be read as one unsatisfiable conjunction"
        )
        # The alternatives rule stated the other way round, so neither reading can pass
        # both: one ANSWERABLE row is enough to schedule the leg, so a partially-served
        # matrix is correctly NOT a violation …
        assert violations({"runners": [X64]}, requested) == []


def test_a_matrix_that_is_unsatisfiable_on_every_row_reds() -> None:
    """…and the no-false-pass half: alternatives must not let an all-broken matrix through."""
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


def test_the_dynamic_matrix_expression_resolves_to_both_routings_as_alternatives() -> None:
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
    alternatives = requested["wf.yml:test"]
    assert frozenset({"ubuntu-latest"}) in alternatives, alternatives
    assert frozenset({"self-hosted", "self-hosted-x64"}) in alternatives, alternatives
    assert len(alternatives) == 2, "the two routings are ALTERNATIVES, never one union"


# ── refusals: unreadable input is refused, never cleared ─────────────────────────────
def test_an_unresolvable_runs_on_is_refused_not_cleared() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n    runs-on: ${{ matrix.whatever }}\n    steps: []\n",
        )
        with pytest.raises(UnreadableRunsOn):
            requested_labels(Path(tmp))


def test_a_spaced_matrix_expression_is_read_not_crashed() -> None:
    """`fromJSON( matrix.runner )` is valid YAML and must not crash the gate.

    A previous revision tested `text.replace(" ", "")` to DETECT the expression but then
    split the ORIGINAL string, so the spaced spelling raised `IndexError` and escaped
    `main()` — a traceback instead of a verdict. It must now be read like any other.
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
    """A static matrix that does not expose the key must NOT get the selector's union.

    Substituting it would fabricate a label set for a job whose real rows ask for something
    else, and the labels it really requests would go unchecked.
    """
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
    with tempfile.TemporaryDirectory() as tmp:
        _workflow(
            tmp,
            "name: t\non: push\njobs:\n  j:\n"
            "    runs-on: ${{ fromJSON(matrix.runner) }}\n"
            "    strategy:\n      matrix:\n        arch: [x64, arm64]\n"
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
    every other test green while the gate certified a ten-year-old pool. This pins a
    ceiling so that change has to be deliberate and argued for.
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
