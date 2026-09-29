"""Hermetic tests for .github/scripts/check-required-set.py (#6144).

The guard exists because the same required-status-check set is declared in
THREE places — live branch protection, `.mergify.yml`'s `queue_conditions` /
`merge_conditions`, and `.github/settings.yml` — and until now nothing read two
of them. `.mergify.yml` stated the invariant in a COMMENT ("each required check
named in EXACTLY ONE of the two lists") and carried a hand-maintained
"Last reconciled with the live required set: 2026-09-26" line. A comment cannot
fail.

Both drift directions are defects and they are not symmetric:

* a required name in NEITHER mergify list stops the file DESCRIBING reality;
* a name in `merge_conditions` that the queue branch never reports on
  DEADLOCKS the queue for every PR.

The third check is the one this repo has already paid for: `python-ci-gate`
observes its legs through TWO machine-readable structures — its `needs:` list
and the `LEGS` heredoc table that decides what a non-`success` result MEANS. A
leg in `needs:` with no `LEGS` row still trips rule 1 (which greps the joined
results for `failure|cancelled`), but a leg that reports `skipped` has NO row to
fail closed on — so the required check would CERTIFY a shard the selector
selected and GitHub never ran. That is #5219 (a green required check over a tree
whose shard did not run) reached through the other door. `tests/test_ci_selection.py`
already asserts this same set equality at test time; the guard re-asserts it at
RUN time inside the aggregate job — defence in depth on the job that owns the
required context, not the discovery of a gap.

Hermetic: no network. The env seams (`MERGIFY_CONFIG`, `BRANCH_PROTECTION_DECLARATION`,
`WORKFLOWS_DIR`, `PYTHON_CI_WORKFLOW`) point the guard at fixtures. The `--live`
path shells out to `gh` and is exercised only when `REQUIRED_SET_SYNC_LIVE=1`.

Exit contract (fail-closed): 0 clean, 1 violation, 2 could-not-measure —
"nothing was compared" is never a pass.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-required-set.py"

# The repository's OWN inputs, as they are on disk when this module is imported.
# `_no_test_writes_a_real_file` below re-hashes them after every test.
_REAL_INPUTS = [
    REPO_ROOT / ".mergify.yml",
    REPO_ROOT / ".github" / "settings.yml",
    *sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml")),
]
_REAL_HASHES = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in _REAL_INPUTS}
# The LISTING as well as the bytes: a test that CREATES a new workflow file leaves
# the known files untouched, so a hash-only guard stays silent while the checkout
# gains a file. (That is the same class of accident as the truncation this guard
# was added for — a write the guard cannot see is a write it will not report.)
_REAL_WORKFLOW_NAMES = frozenset(
    p.name for p in (REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))


@pytest.fixture(autouse=True)
def _no_test_writes_a_real_file():
    """No test may modify the repository's own inputs.

    A GUARD, not a comment, and it exists because the accident already happened
    once in this file: a parametrised test lacked `tmp_guard_env` and so wrote its
    fixture `{mode: True}` into the REAL `.mergify.yml`, truncating 228 lines to
    3. Three unrelated tests then failed with a confusing "no check-success=*
    conditions in any queue rule" error, which named the symptom and not the
    cause. This names the file, at the test that did it.

    Cheap: hashes a handful of files that a unit test must never touch.
    """
    yield
    for path, expected in _REAL_HASHES.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected, (
            f"a test modified the repository's own {path.relative_to(REPO_ROOT)} — "
            f"every test must point the seams at `tmp_guard_env`, never at the real "
            f"tree, because writing here corrupts the checkout and makes unrelated "
            f"tests fail for an unrelated reason")
    now = frozenset(p.name for p in (REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert now == _REAL_WORKFLOW_NAMES, (
        f"a test created or deleted a workflow in the repository: "
        f"{sorted(now ^ _REAL_WORKFLOW_NAMES)} — a hash check over the files that "
        f"already existed cannot see a NEW one")

# Ambient seams a developer might have exported — popped for full hermeticity.
_AMBIENT = ("MERGIFY_CONFIG", "BRANCH_PROTECTION_DECLARATION", "WORKFLOWS_DIR",
            "PYTHON_CI_WORKFLOW", "REQUIRED_SET_SYNC_LIVE")


@pytest.fixture(scope="module")
def guard():
    """Import the guard script as a module (it is not on the package path)."""
    spec = importlib.util.spec_from_file_location("check_required_set", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tmp_guard_env(guard, monkeypatch):
    """Point every seam at a throwaway dir; return (dir, helper to write files)."""
    d = Path(tempfile.mkdtemp(prefix="required-set-"))
    monkeypatch.setattr(guard, "MERGIFY_PATH", d / ".mergify.yml")
    monkeypatch.setattr(guard, "SETTINGS_PATH", d / "settings.yml")
    monkeypatch.setattr(guard, "WORKFLOWS_DIR", d / "workflows")
    monkeypatch.setattr(guard, "PYTHON_CI_PATH", d / "python-ci.yml")
    (d / "workflows").mkdir()
    return d


def _run(env_extra: dict[str, str], args: list[str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key in _AMBIENT:
        env.pop(key, None)
    env.update(env_extra)
    return subprocess.run([sys.executable, str(SCRIPT), *(args or [])],
                          capture_output=True, text=True, env=env, cwd=REPO_ROOT)


# ── the real repo must pass (the durable pin) ──────────────────────────────


def test_the_real_repo_agrees_offline(guard):
    """`.mergify.yml`, the enumeration, the mirror and the gate's LEGS all agree.

    This is the pin: it fails the moment any one of the four drifts from the
    others, without needing credentials or network.
    """
    code, violations, _ = guard.run(live=False)
    assert code == 0, f"required-set drift on the live tree: {violations}"
    assert violations == []


def test_the_real_gate_observes_exactly_the_legs_its_table_names(guard):
    """`needs:` and the `LEGS` heredoc describe the SAME set in the real workflow."""
    needs, legs = guard.gate_legs()
    assert set(needs) == legs
    assert len(needs) == len(legs) == 10, (
        "python-ci-gate's claim changed shape — update the enumeration's "
        "coverage accounting for the new leg")
    for leg in ("test", "test-slow", "test-carve-out"):
        assert leg in legs, f"{leg} is a long leg and must stay observable (#5219)"


def test_the_real_enumeration_partitions_every_name(guard):
    """Exactly one bucket per name, and no name without a recorded guarantee."""
    queue, merge = guard.declared_lists()
    injected = guard.injected_names()
    assert not (queue & merge)
    assert not (injected & (queue | merge)), (
        "an `injected` name must not also be filed in a named bucket — the "
        "enumeration gives each name exactly ONE enforcement route")
    assert queue and merge
    for name, (where, why) in guard.REQUIRED_SET.items():
        assert where in ("queue", "merge", "injected"), f"{name}: unknown bucket {where!r}"
        assert why.strip(), f"{name}: coverage accounting is empty"
    assert "python-ci-gate" in merge, (
        "the aggregate must be the one merge condition — it is the check the "
        "queue branch actually reports on")
    # The live-required name that is enforced by INJECTION, not by a list entry.
    # Pinned so a later "tidy-up" cannot drop it and restore the six-context lie.
    assert injected == {"ai-review-gate"}, (
        "`ai-review-gate` is required on main and must stay enumerated; it is "
        "enforced at merge by branch-protection injection, so it belongs in "
        "NEITHER .mergify.yml list")
    assert set(guard.REQUIRED_SET) == queue | merge | injected, (
        "the enumeration is the LIVE-required set — every entry must be in one "
        "of the three buckets and every bucket entry in the enumeration")


# ── COMPOSITION: run() must actually CALL each check ───────────────────────
#
# The checks are pinned in isolation above, which is not enough: a refactor that
# drops a check call from `run()` keeps every isolation test green, because none
# of them drives `run()` at all. These drive it end-to-end through the env seams
# so the WIRING is pinned too, and so dropping a call reddens.


def _write_minimal_gate(guard, needs: list[str], legs: list[str]) -> None:
    """A consistent gate whose `needs:` and LEGS rows are given independently."""
    guard.PYTHON_CI_PATH.write_text(
        "jobs:\n  python-ci-gate:\n    needs: [" + ", ".join(needs) + "]\n"
        "    steps:\n      - run: |\n          done <<'LEGS'\n"
        + "".join(f"          {leg}|${{{{ needs.{leg}.result }}}}|-\n" for leg in legs)
        + "          LEGS\n")


def _write_minimal_mergify(guard, queue: list[str], merge: list[str]) -> None:
    guard.MERGIFY_PATH.write_text(
        "queue_rules:\n  - name: main\n"
        # The real file sets this, and `check_injection_mode` asserts it — a
        # fixture that omitted it would make every composition test fail for a
        # reason unrelated to what it tests.
        "    branch_protection_injection_mode: merge\n"
        "    queue_conditions:\n"
        + "".join(f"      - check-success={n}\n" for n in queue)
        + "    merge_conditions:\n"
        + "".join(f"      - check-success={n}\n" for n in merge))


def _write_workflow(guard, name: str, trigger: str, jobs: list[str]) -> None:
    guard.WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    (guard.WORKFLOWS_DIR / name).write_text(
        f"on:\n  {trigger}:\njobs:\n"
        + "".join(f"  {j}:\n    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"
                  for j in jobs))


def test_run_flags_a_legs_mismatch_end_to_end(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must call `check_gate_legs` (the #5219 door)."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["beta"])  # alpha has no row
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("alpha" in v and "skipped" in v for v in violations), violations


def test_run_flags_an_unproducible_queue_condition_end_to_end(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must call `check_deadlock` for the ENTRY bucket too."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "push.yml", "push", ["alpha"])  # alpha: push-only
    _write_workflow(guard, "pr.yml", "pull_request", ["beta"])
    _write_minimal_gate(guard, needs=["beta"], legs=["beta"])
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("ENTRY stalls" in v for v in violations), violations


def test_run_flags_a_stale_mirror_end_to_end(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must call `check_settings`."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["alpha", "beta"])
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - redis-guard\n")
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("stale declarative mirror" in v for v in violations), violations


# ── the mirror must speak for `main` ───────────────────────────────────────


def test_run_flags_an_unproducible_merge_condition_end_to_end(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must call `check_deadlock` for the MERGE bucket.

    The deadlock this guard exists to prevent is the MERGE one — a
    `merge_conditions` name the queue branch never reports. Pinning only the
    entry bucket left this call droppable with a green suite.
    """
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha"])
    _write_workflow(guard, "push.yml", "push", ["beta"])  # beta: push-only
    _write_minimal_gate(guard, needs=["alpha"], legs=["alpha"])
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("DEADLOCK" in v for v in violations), violations


def test_run_flags_an_unaccounted_required_check_end_to_end(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must call `check_partition`."""
    _write_minimal_mergify(guard, ["alpha", "ghost"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "ghost", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["alpha", "beta"])
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("unaccounted" in v for v in violations), violations


def test_run_flags_a_strict_mismatch_on_the_live_path(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run(live=True)` must call `check_declared_strict`.

    Offline `live_strict` is None, so that call is a NO-OP there — only a live
    run can pin it. The real live and mirror values are both `false`, so the
    `--live` test on the real tree cannot pin it either.
    """
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["alpha", "beta"])
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        strict: true\n"
        "        contexts:\n          - alpha\n          - beta\n")
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    monkeypatch.setattr(guard, "read_live_protection", lambda: ({"alpha", "beta"}, False))
    code, violations, _ = guard.run(live=True)
    assert code == 1, violations
    assert any("#4764" in v for v in violations), violations


def test_run_flags_a_mirror_that_also_speaks_for_another_branch(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must call `check_settings_branches`.

    The `main` entry here is CORRECT and complete, so `check_settings` does NOT
    fire — only the off-main check can redden. That is what pins THIS call:
    without it this test would be satisfied by the other mirror check.
    """
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["alpha", "beta"])
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n"
        "    - branch: main\n      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - alpha\n          - beta\n"
        "    - branch: develop\n      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - ghost\n")
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("rather than `main`" in v for v in violations), violations


def test_a_mirror_for_another_branch_is_a_violation(guard, tmp_guard_env):
    """.github/settings.yml is a mirror of `main`; contexts for `develop` are mis-filed."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: develop\n"
        "      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - docs\n          - python-ci-gate\n")
    assert guard.declared_settings_contexts() == set()
    assert guard.declared_settings_off_main() == ["develop"]
    assert guard.check_settings_branches(["develop"]), "must be flagged"


def test_a_second_entry_for_another_branch_contributes_no_contexts(guard, tmp_guard_env):
    """The main entry is read; a sibling branch entry neither adds nor hides names."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n"
        "    - branch: main\n      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - docs\n"
        "    - branch: develop\n      required_status_checks:\n"
        "        contexts:\n          - ghost\n")
    assert guard.declared_settings_contexts() == {"docs"}
    assert guard.declared_settings_off_main() == ["develop"]


def test_an_entry_without_a_branch_is_not_treated_as_main(guard, tmp_guard_env):
    """A missing `branch:` must not be silently accepted as `main`."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - required_status_checks:\n"
        "        contexts:\n          - docs\n")
    assert guard.declared_settings_contexts() == set()
    assert guard.declared_settings_off_main() == ["<missing branch>"]


def test_declared_settings_strict_reads_only_the_main_entry(guard, tmp_guard_env):
    """The reader must pick the `main` entry's `strict`, not a sibling's."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n"
        "    - branch: develop\n      required_status_checks:\n        strict: true\n"
        "    - branch: main\n      required_status_checks:\n        strict: false\n")
    assert guard.declared_settings_strict() is False
    guard.SETTINGS_PATH.write_text("repository:\n  branch-protection: []\n")
    assert guard.declared_settings_strict() is None


@pytest.mark.parametrize("body", [
    "repository:\n  branch-protection: []\n",
    "repository:\n  branch-protection:\n",
    "repository:\n  branch-protection:\n    - branch: develop\n",
])
def test_a_present_but_empty_declaration_is_a_violation_not_a_pass(guard, tmp_guard_env, body):
    """Present-but-empty is NOT "the mirror does not exist".

    Returning None here would skip `check_settings` entirely — a fail-open in the
    one place that exists to fail closed.
    """
    guard.SETTINGS_PATH.write_text(body)
    contexts = guard.declared_settings_contexts()
    assert contexts is not None, "present-but-empty must not read as 'absent'"
    assert contexts == set()
    assert guard.check_settings(contexts, {"alpha"}) != []


def test_a_misshaped_declaration_cannot_be_measured(guard, tmp_guard_env):
    """A mapping where a list belongs is exit 2, not a silent pass."""
    guard.SETTINGS_PATH.write_text("repository:\n  branch-protection:\n    branch: main\n")
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_contexts()
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_off_main()


def test_an_off_main_entry_declaring_only_strict_is_still_mis_filed(guard, tmp_guard_env):
    """Off-main `strict` with no `contexts:` is still a mis-filed mirror."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n"
        "    - branch: main\n      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - alpha\n"
        "    - branch: develop\n      required_status_checks:\n        strict: true\n")
    assert guard.declared_settings_off_main() == ["develop"]


def test_a_job_gated_to_push_inside_a_pr_workflow_is_not_producible(guard, tmp_guard_env):
    """A `pull_request` workflow can still hold a job that only ever runs on push.

    Counting that job as producible is a false negative in the deadlock check —
    the condition can never become true on the PR head or the queue branch.
    """
    (tmp_guard_env / "workflows" / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n"
        "  always-ok:\n    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"
        "  push-only:\n    if: always() && github.event_name == 'push'\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    producible = guard.producible_on_pull_request()
    assert "always-ok" in producible
    assert "push-only" not in producible
    assert guard.check_deadlock({"push-only"}, producible, "merge_conditions") != []


def test_a_job_with_an_unclassifiable_if_is_treated_as_producible(guard, tmp_guard_env):
    """THE LIMIT, pinned deliberately.

    An `if:` we cannot classify must count as producible: the opposite default
    would turn an unrecognised expression into a false DEADLOCK on a check that
    is fine, which is worse than the false negative being closed.
    """
    (tmp_guard_env / "workflows" / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n  weird:\n"
        "    if: needs.changes.outputs.python == 'true'\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    assert "weird" in guard.producible_on_pull_request()


def test_run_flags_a_required_check_gated_to_push_inside_a_pr_workflow(
        guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: the push-gated job must reach a violation through run()."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    guard.WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    (guard.WORKFLOWS_DIR / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n"
        "  alpha:\n    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"
        "  beta:\n    if: always() && github.event_name == 'push'\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    _write_minimal_gate(guard, needs=["alpha"], legs=["alpha"])
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("DEADLOCK" in v for v in violations), violations


# ── shape guards: unusable YAML is exit 2, never a traceback ───────────────


def test_a_non_mapping_mergify_top_level_cannot_be_measured(guard, tmp_guard_env):
    """A top-level list must exit 2, not raise AttributeError with no ::error::."""
    guard.MERGIFY_PATH.write_text("- check-success=docs\n")
    with pytest.raises(guard.CannotMeasure):
        guard.load_mergify()


def test_a_workflow_with_jobs_as_a_scalar_cannot_be_measured(guard, tmp_guard_env):
    (tmp_guard_env / "workflows" / "bad.yml").write_text(
        "on:\n  pull_request:\njobs: hello\n")
    with pytest.raises(guard.CannotMeasure):
        guard.producible_on_pull_request()


def test_a_non_mapping_required_status_checks_cannot_be_measured(guard, tmp_guard_env):
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks: nope\n")
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_contexts()


def test_a_non_mapping_workflow_top_level_cannot_be_measured(guard, tmp_guard_env):
    (tmp_guard_env / "python-ci.yml").write_text("- a\n- b\n")
    with pytest.raises(guard.CannotMeasure):
        guard.gate_legs()


def test_a_non_iterable_needs_cannot_be_measured(guard, tmp_guard_env):
    # A VALID heredoc, so the ONLY defect is the `needs:` shape. Without it this
    # test passed for the wrong reason (the "no LEGS table" guard fired), and
    # deleting the needs guard left it green.
    _write_gate_with_a_bad_shape(guard, needs="5")
    with pytest.raises(guard.CannotMeasure, match="must be a string or list"):
        guard.gate_legs()


@pytest.mark.parametrize("value", ["[{a: b}]", "[[a]]"])
def test_unhashable_needs_elements_cannot_be_measured(guard, tmp_guard_env, value):
    """A dict/list element used to reach `set(needs)` -> TypeError, exit 1, no annotation."""
    _write_gate_with_a_bad_shape(guard, needs=value)
    with pytest.raises(guard.CannotMeasure, match="entries must be non-blank strings"):
        guard.gate_legs()


def test_a_blank_needs_entry_cannot_be_measured(guard, tmp_guard_env):
    _write_gate_with_a_bad_shape(guard, needs="['']")
    with pytest.raises(guard.CannotMeasure, match="non-blank strings"):
        guard.gate_legs()


def test_a_non_string_settings_context_cannot_be_measured(guard, tmp_guard_env):
    """The mirror must shape-validate contexts, as the live path does — no `str()`.

    The anchor is this guard's OWN message. An earlier version anchored on the
    bare `"must be a list"` and passed for the wrong reason: the parser's
    `required_status_checks` guard raised first and matched, so removing the
    element check entirely left the test green.
    """
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - 5\n          - docs\n")
    with pytest.raises(guard.CannotMeasure, match="contexts must be strings"):
        guard.declared_settings_contexts()


def test_a_non_list_settings_contexts_cannot_be_measured(guard, tmp_guard_env):
    """Pins the PARSER's list-shape guard, reached through this entry point.

    Anchored on the parser's message because that is the guard that fires: the
    list shape is enforced in `_required_status_checks`, not in
    `_settings_contexts`. This asserts the end-to-end behaviour, not a guard in
    `_settings_contexts` (which deliberately does not re-check the shape).
    """
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        strict: false\n"
        "        contexts: false\n")
    with pytest.raises(guard.CannotMeasure,
                       match="required_status_checks\\.contexts must be a list"):
        guard.declared_settings_contexts()


def test_a_dangling_symlink_at_the_mirror_path_is_not_read_as_absent(guard, tmp_guard_env):
    """A BROKEN SYMLINK is a present directory entry, not a missing mirror.

    `Path.exists()` is False for a dangling link, so this used to classify the
    mirror as absent and SKIP the comparison (exit 0) — while the same shape at
    the mergify/python-ci seams was fail-closed exit 2.
    """
    if guard.SETTINGS_PATH.exists() or guard.SETTINGS_PATH.is_symlink():
        guard.SETTINGS_PATH.unlink()
    guard.SETTINGS_PATH.symlink_to(tmp_guard_env / "nonexistent-target.yml")
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_contexts()


def test_a_non_mapping_strategy_cannot_be_measured(guard, tmp_guard_env):
    (tmp_guard_env / "workflows" / "bad.yml").write_text(
        "on:\n  pull_request:\njobs:\n  g:\n    name: my-check\n"
        "    strategy: [a, b]\n    runs-on: ubuntu-latest\n")
    with pytest.raises(guard.CannotMeasure):
        guard.producible_on_pull_request()


def test_a_non_iterable_queue_conditions_cannot_be_measured(guard, tmp_guard_env):
    guard.MERGIFY_PATH.write_text(
        "queue_rules:\n  - name: main\n    queue_conditions: 5\n"
        "    merge_conditions:\n      - check-success=python-ci-gate\n")
    with pytest.raises(guard.CannotMeasure):
        guard.load_mergify()


@pytest.mark.parametrize("body", [
    "repository: [a, b]\n",
    "repository: hello\n",
    "repository: 5\n",
])
def test_a_non_mapping_repository_is_not_treated_as_absent(guard, tmp_guard_env, body):
    """PRESENT-but-unusable `repository:` must not silently disable the mirror.

    Returning "absent" here makes `declared_settings_contexts()` None, which
    `check_settings` SKIPS — an exit 0 with one of the three surfaces uncompared.
    """
    guard.SETTINGS_PATH.write_text(body)
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_contexts()


@pytest.mark.parametrize("body", [
    "repository:\n  branch-protection: 5\n",
    "repository:\n  branch-protection: hello\n",
])
def test_a_non_list_branch_protection_cannot_be_measured(guard, tmp_guard_env, body):
    guard.SETTINGS_PATH.write_text(body)
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_contexts()


def test_a_non_iterable_contexts_cannot_be_measured(guard, tmp_guard_env):
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        contexts: 5\n")
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_contexts()


def test_a_string_contexts_is_refused_not_iterated(guard, tmp_guard_env):
    """`contexts: docs` must not silently become {'d','o','c','s'}."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        contexts: docs\n")
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_contexts()


@pytest.mark.parametrize("cond,gated_off", [
    ("always() && github.event_name == 'push'", True),
    ('github.event_name == "push"', True),
    ("github.event_name=='push'", True),
    ("github.event_name != 'pull_request'", True),
    ("github.event_name != 'pull_request_target'", True),
    ("github.event_name == 'push' || github.event_name == 'pull_request'", False),
    # A POSITIVE comparison against any other non-PR event is as decisive as
    # `== 'push'`. Matching only `push` classified these PRODUCIBLE — a false GREEN.
    ("github.event_name == 'schedule'", True),
    ("github.event_name == 'workflow_dispatch'", True),
    ("github.event_name == 'issues'", True),
    ("github.event_name == 'pull_request_target'", False),
    ("github.event_name == 'pull_request'", False),
    ("needs.changes.outputs.python == 'true'", False),
])
def test_the_push_gate_classification(guard, cond, gated_off):
    """Classify the COMPARISON, not a substring.

    A brute `"pull_request" in condition` test swallowed the NEGATED form too, so
    a job gated `!= 'pull_request'` — just as push-only as `== 'push'` — was
    counted as producible, and the stall went undetected.
    """
    assert guard._gated_off_pr_refs({"if": cond}) is gated_off, cond


def test_run_flags_a_job_gated_by_a_negated_pull_request(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: the negated push-only gate must reach a violation through run()."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    guard.WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    (guard.WORKFLOWS_DIR / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n"
        "  alpha:\n    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"
        "  beta:\n    if: github.event_name != 'pull_request'\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    _write_minimal_gate(guard, needs=["alpha"], legs=["alpha"])
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("DEADLOCK" in v for v in violations), violations


@pytest.mark.parametrize("body", ["[]\n", "[a, b]\n", "false\n", "0\n", "hello\n"])
def test_a_falsy_but_non_mapping_top_level_is_not_read_as_absent(guard, tmp_guard_env, body):
    """THE CARDINAL SIN: exit 0 while the mirror was never compared.

    `read_yaml` used to coerce EVERY falsy parse to `{}` via `or {}`, so
    `settings.yml = []` became `{}` -> "no `repository` key" -> `_KEY_ABSENT` ->
    `check_settings` SKIPPED -> exit 0. The SAME shape made truthy (`[a, b]`)
    exited 2 — a fail-open that depended on the value's truthiness.
    """
    guard.SETTINGS_PATH.write_text(body)
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_contexts()


@pytest.mark.parametrize("body", [
    "",                       # empty file
    "null\n",                 # explicit null document
    "{}\n",                   # empty mapping
    "# only a comment\n",
    "repository:\n",          # bare repository key
    "repository: null\n",
    "repository:\n  branch-protection:\n",
    "repository:\n  branch-protection: null\n",
])
def test_a_present_file_declaring_nothing_is_a_violation_not_a_skip(guard, tmp_guard_env, body):
    """A PRESENT but empty declaration must FLAG, not skip.

    Only a MISSING file is "the mirror does not exist". Otherwise an empty (or
    `null`) settings.yml — or a bare `[]` BEFORE the `or {}` fix — disabled the
    mirror check and exited 0 with one of the three surfaces never compared.
    """
    guard.SETTINGS_PATH.write_text(body)
    contexts = guard.declared_settings_contexts()
    assert contexts is not None, "a present file must not read as 'absent'"
    assert contexts == set()
    assert guard.check_settings(contexts, {"alpha"}) != []


def test_a_missing_file_is_still_absent(guard, tmp_guard_env):
    """The ONE legitimate skip: the mirror file does not exist."""
    guard.SETTINGS_PATH.unlink(missing_ok=True)
    assert guard.declared_settings_contexts() is None


@pytest.mark.parametrize("value", ['"false"', "5", "[]", "null"])
def test_a_non_boolean_strict_is_not_coerced(guard, tmp_guard_env, value):
    """`strict: "false"` is a STRING, and `bool("false")` is True.

    Coercing it would manufacture a spurious #4764 mismatch against a live
    `strict=false` — a false red on a correct mirror.
    """
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        f"      required_status_checks:\n        strict: {value}\n")
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_strict()


def test_an_undecodable_config_is_exit_2_not_a_traceback(guard, tmp_guard_env):
    """An undecodable file is UNPARSABLE, so it must reach the exit-2 contract."""
    guard.MERGIFY_PATH.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(guard.CannotMeasure):
        guard.load_mergify()


def test_an_undecodable_settings_file_is_exit_2(guard, tmp_guard_env):
    guard.SETTINGS_PATH.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(guard.CannotMeasure):
        guard.declared_settings_contexts()


def test_an_undecodable_workflow_is_skipped_not_fatal(guard, tmp_guard_env):
    """A broken workflow may only ever cause a false RED, never a false GREEN."""
    (tmp_guard_env / "workflows" / "binary.yml").write_bytes(b"\xff\xfe\x00bad")
    (tmp_guard_env / "workflows" / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n  mycheck:\n    name: my-check\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    assert guard.producible_on_pull_request() == {"my-check"}


def test_a_broken_symlink_in_the_workflows_dir_cannot_be_measured(guard, tmp_guard_env):
    """The stat in `read_yaml` is a filesystem read too — it must not traceback."""
    link = tmp_guard_env / "workflows" / "zz-broken.yml"
    link.symlink_to(tmp_guard_env / "nonexistent-target.yml")
    with pytest.raises(guard.CannotMeasure):
        guard.read_yaml(link)


def _write_gate_with_a_bad_shape(guard, *, needs: str = "[alpha]", steps: str | None = None) -> None:
    """A gate with a VALID LEGS table, so only `needs`/`steps` shape is under test.

    Without the valid heredoc a shape test passes for the WRONG reason — it trips
    the "no LEGS table" guard instead of the shape validation it names.
    """
    step_block = steps if steps is not None else (
        "    steps:\n      - run: |\n          done <<'LEGS'\n"
        "          alpha|success|-\n          LEGS\n")
    guard.PYTHON_CI_PATH.write_text(
        "jobs:\n  python-ci-gate:\n    needs: " + needs + "\n" + step_block)


@pytest.mark.parametrize("value", ["5", "true", "[a, b]", "{a: b}"])
def test_a_non_string_step_run_cannot_be_measured(guard, tmp_guard_env, value):
    """A truthy non-string `run:` used to reach `.splitlines()` and traceback."""
    _write_gate_with_a_bad_shape(guard, steps=(
        f"    steps:\n      - run: {value}\n"
        "      - run: |\n          done <<'LEGS'\n          alpha|success|-\n          LEGS\n"))
    with pytest.raises(guard.CannotMeasure, match="run"):
        guard.gate_legs()


@pytest.mark.parametrize("value", ["0", "false"])
def test_a_falsy_non_list_needs_cannot_be_measured(guard, tmp_guard_env, value):
    """`needs: 0` must not be read as `needs: []` — the same shape as `needs: 5`.

    `''` is deliberately NOT a row here: it is a STRING, so the scalar branch
    accepts it as `[""]` and the raise comes from the blank-element guard, not the
    shape guard this test exists to pin. It is covered by
    `test_a_blank_needs_entry_cannot_be_measured`.
    """
    _write_gate_with_a_bad_shape(guard, needs=value)
    with pytest.raises(guard.CannotMeasure):
        guard.gate_legs()


def test_a_non_list_steps_cannot_be_measured(guard, tmp_guard_env):
    # Anchored on the MESSAGE: a scalar `steps:` fixture cannot also carry a
    # heredoc, so an unanchored `pytest.raises(CannotMeasure)` was satisfied by
    # the "no LEGS table" guard once the steps guard's raise was neutralised.
    _write_gate_with_a_bad_shape(guard, steps="    steps: 5\n")
    with pytest.raises(guard.CannotMeasure, match="steps must be a list"):
        guard.gate_legs()


def test_a_scalar_needs_is_accepted(guard, tmp_guard_env):
    """`needs: alpha` is the scalar form of a one-element list, not a shape error."""
    _write_gate_with_a_bad_shape(guard, needs="alpha")
    assert guard.gate_legs() == (["alpha"], {"alpha"})


def test_a_gate_without_needs_defaults_to_an_empty_list(guard, tmp_guard_env):
    """No `needs:` key at all means the gate depends on nothing — not unmeasurable."""
    guard.PYTHON_CI_PATH.write_text(
        "jobs:\n  python-ci-gate:\n    steps:\n      - run: |\n"
        "          done <<'LEGS'\n          alpha|success|-\n          LEGS\n")
    assert guard.gate_legs() == ([], {"alpha"})


@pytest.mark.parametrize("value", [r"\\1", r"\\g<name>", r"\\d", r"\\n", "\\1", "\\g<name>"])
def test_a_matrix_value_with_a_backslash_is_not_a_replacement_template(guard, value):
    r"""A matrix value must not be treated as a regex replacement TEMPLATE.

    The failure modes differ by input, and BOTH are covered:
      * a DOUBLE-backslash value is emitted literally by the buggy template path,
        silently producing a wrong check name (no exception at all);
      * a SINGLE-backslash value makes the buggy path RAISE — `re.error: invalid
        group reference` for `\1`, and `IndexError: unknown group name` for
        `\g<name>` on CPython 3.12.
    Either way the value must come back unchanged.
    """
    assert guard._render_matrix("test (${{ matrix.a }})", {"a": [value]}) == {
        f"test ({value})"
    }


@pytest.mark.parametrize("payload", [
    {"contexts": 5, "strict": False},
    {"contexts": [["a"]], "strict": False},
    {"contexts": "docs", "strict": False},
    {"contexts": ["a"], "strict": "false"},
    ["not-an-object"],
])
def test_a_malformed_live_payload_cannot_be_measured(guard, monkeypatch, payload):
    """The last unguarded parsed-payload access: `set(payload['contexts'])`."""
    import json
    import subprocess

    class Done:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(guard.CannotMeasure):
        guard.read_live_protection()


def test_a_non_mapping_jobs_in_python_ci_cannot_be_measured(guard, tmp_guard_env):
    """The `jobs:` guard in `gate_legs`.

    The near-identical guard in `producible_on_pull_request` was pinned; this one
    was not, so deleting it left the whole suite green.
    """
    guard.PYTHON_CI_PATH.write_text("jobs: 5\n")
    with pytest.raises(guard.CannotMeasure, match="`jobs:` must be a mapping"):
        guard.gate_legs()


@pytest.mark.parametrize("value", ["5", "[a, b]"])
def test_a_non_mapping_matrix_cannot_be_measured(guard, tmp_guard_env, value):
    """A scalar `strategy.matrix` used to reach `.get()` -> AttributeError."""
    (tmp_guard_env / "workflows" / "bad.yml").write_text(
        "on:\n  pull_request:\njobs:\n  g:\n    name: 't (${{ matrix.a }})'\n"
        f"    strategy:\n      matrix: {value}\n    runs-on: ubuntu-latest\n")
    with pytest.raises(guard.CannotMeasure, match=r"strategy\.matrix must be a mapping"):
        guard.producible_on_pull_request()


@pytest.mark.parametrize("value", ["[5]", "[[a]]"])
def test_a_non_mapping_branch_protection_entry_cannot_be_measured(guard, tmp_guard_env, value):
    """An entry that is not a mapping used to reach `entry.get` -> AttributeError."""
    guard.SETTINGS_PATH.write_text(f"repository:\n  branch-protection: {value}\n")
    with pytest.raises(guard.CannotMeasure, match="entries must be mappings"):
        guard.declared_settings_contexts()


@pytest.mark.parametrize("value", ["5", "false"])
def test_a_non_list_queue_rules_cannot_be_measured(guard, tmp_guard_env, value):
    """A scalar `queue_rules` used to reach `enumerate(5)` -> TypeError."""
    guard.MERGIFY_PATH.write_text(f"queue_rules: {value}\n")
    with pytest.raises(guard.CannotMeasure, match="no queue_rules"):
        guard.load_mergify()


@pytest.mark.parametrize("live,missing,extra", [
    ({"alpha"}, ["beta"], []),
    ({"alpha", "beta", "gamma"}, [], ["gamma"]),
])
def test_run_compares_the_live_context_set(guard, tmp_guard_env, monkeypatch,
                                          live, missing, extra):
    """COMPOSITION: `run(live=True)` must compare LIVE contexts to the enumeration.

    The other live-path test returns contexts EQUAL to the enumeration, so
    `missing`/`extra` were both empty there and this whole block was a no-op -
    deleting it left the suite green.
    """
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["alpha", "beta"])
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        contexts:\n"
        "          - alpha\n          - beta\n")
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    monkeypatch.setattr(guard, "read_live_protection", lambda: (live, False))
    code, violations, _ = guard.run(live=True)
    assert code == 1, violations
    for name in missing:
        assert any("NOT required on main" in v and name in v for v in violations), violations
    for name in extra:
        assert any("unaccounted required check" in v and name in v for v in violations), violations


# ── the remaining guard branches, pinned by OUTCOME ─────────────────────────
#
# A guard nobody deletes is a guard nobody is testing, so this block pins the
# branches that change the EXIT CODE or the VERDICT.
#
# It is NOT a claim that every branch is pinned, and it asserts NO survivor list:
# an enumeration of unpinned branches is a claim about the code that goes stale on
# every edit, and it was deleted rather than re-derived. The mutation evidence
# lives in the commit log; this file pins BEHAVIOUR.


@pytest.mark.parametrize("seam", ["mergify", "settings", "python-ci"])
def test_malformed_yaml_at_each_seam_cannot_be_measured(guard, tmp_guard_env, seam):
    """The three `except yaml.YAMLError -> CannotMeasure` guards (exit 2, per contract)."""
    body = {"mergify": "queue_rules: [\n", "settings": "repository: [\n",
            "python-ci": "jobs: [\n"}[seam]
    path = {"mergify": guard.MERGIFY_PATH, "settings": guard.SETTINGS_PATH,
            "python-ci": guard.PYTHON_CI_PATH}[seam]
    call = {"mergify": guard.load_mergify, "settings": guard.declared_settings_contexts,
            "python-ci": guard.gate_legs}[seam]
    path.write_text(body)
    with pytest.raises(guard.CannotMeasure, match="unparsable"):
        call()


@pytest.mark.parametrize("seam,needle", [
    ("mergify", "mergify config not found"),
    ("python-ci", "workflow not found"),
])
def test_a_missing_config_file_cannot_be_measured(guard, tmp_guard_env, seam, needle):
    """Anchored on the MESSAGE: without it the `exists()` check is unpinned.

    `read_yaml`'s stat guard raises CannotMeasure for a missing file too, so an
    unanchored assertion was satisfied by the sibling guard and deleting this one
    left the suite green.
    """
    path = {"mergify": guard.MERGIFY_PATH, "python-ci": guard.PYTHON_CI_PATH}[seam]
    call = {"mergify": guard.load_mergify, "python-ci": guard.gate_legs}[seam]
    path.unlink(missing_ok=True)
    with pytest.raises(guard.CannotMeasure, match=needle):
        call()


def test_a_non_mapping_queue_rule_cannot_be_measured(guard, tmp_guard_env):
    guard.MERGIFY_PATH.write_text("queue_rules: [5]\n")
    with pytest.raises(guard.CannotMeasure, match=r"queue_rules\[0\] must be a mapping"):
        guard.load_mergify()


def test_a_non_mapping_job_in_a_workflow_cannot_be_measured(guard, tmp_guard_env):
    (tmp_guard_env / "workflows" / "bad.yml").write_text(
        "on:\n  pull_request:\njobs:\n  g: 5\n")
    with pytest.raises(guard.CannotMeasure, match=r"jobs\.g must be a mapping"):
        guard.producible_on_pull_request()


def test_a_non_mapping_workflow_file_is_skipped_not_fatal(guard, tmp_guard_env):
    """A scalar top-level workflow contributes no names; it must not `.get` a scalar."""
    (tmp_guard_env / "workflows" / "scalar.yml").write_text("hello\n")
    (tmp_guard_env / "workflows" / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n  mycheck:\n    name: my-check\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    assert guard.producible_on_pull_request() == {"my-check"}


def test_a_non_mapping_gate_job_cannot_be_measured(guard, tmp_guard_env):
    guard.PYTHON_CI_PATH.write_text("jobs:\n  python-ci-gate: 5\n")
    with pytest.raises(guard.CannotMeasure, match=r"jobs\.python-ci-gate must be a mapping"):
        guard.gate_legs()


def test_a_gate_job_that_does_not_exist_cannot_be_measured(guard, tmp_guard_env):
    guard.PYTHON_CI_PATH.write_text("jobs:\n  something-else:\n    runs-on: ubuntu-latest\n")
    with pytest.raises(guard.CannotMeasure, match="no 'python-ci-gate' job"):
        guard.gate_legs()


def test_a_gate_job_without_steps_cannot_be_measured(guard, tmp_guard_env):
    """No `steps:` means no LEGS table — `steps is None -> []` must not crash first."""
    guard.PYTHON_CI_PATH.write_text("jobs:\n  python-ci-gate:\n    needs: [a]\n")
    with pytest.raises(guard.CannotMeasure, match="no <<'LEGS' table"):
        guard.gate_legs()


def test_a_non_mapping_step_is_skipped(guard, tmp_guard_env):
    """`steps: [5]` contributes nothing; it must be skipped, not `.get`-ed."""
    guard.PYTHON_CI_PATH.write_text(
        "jobs:\n  python-ci-gate:\n    needs: [alpha]\n    steps:\n      - 5\n"
        "      - run: |\n          done <<'LEGS'\n          alpha|success|-\n          LEGS\n")
    assert guard.gate_legs() == (["alpha"], {"alpha"})


def test_a_blank_legs_row_is_skipped(guard, tmp_guard_env):
    guard.PYTHON_CI_PATH.write_text(
        "jobs:\n  python-ci-gate:\n    needs: [alpha, beta]\n    steps:\n      - run: |\n"
        "          done <<'LEGS'\n          alpha|success|-\n\n"
        "          beta|success|-\n          LEGS\n")
    assert guard.gate_legs() == (["alpha", "beta"], {"alpha", "beta"})


def test_a_missing_workflows_dir_cannot_be_measured(guard, tmp_guard_env):
    for leftover in (tmp_guard_env / "workflows").glob("*"):
        leftover.unlink()
    (tmp_guard_env / "workflows").rmdir()
    with pytest.raises(guard.CannotMeasure, match="workflows dir not found"):
        guard.producible_on_pull_request()


@pytest.mark.parametrize("on_block,expect_producible", [
    ("on: push\n", False),                      # scalar trigger
    ("on: pull_request\n", True),                # scalar PR trigger
    ("on: [push, pull_request]\n", True),        # list trigger
    ("", False),                                 # no trigger at all
    ("on:\n  pull_request:\n", True),            # mapping trigger
])
def test_trigger_shapes_decide_producibility(guard, tmp_guard_env, on_block, expect_producible):
    for leftover in (tmp_guard_env / "workflows").glob("*"):
        leftover.unlink()
    body = on_block + ("jobs:\n  mycheck:\n    name: my-check\n"
                       "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    (tmp_guard_env / "workflows" / "pr.yml").write_text(body)
    assert (guard.producible_on_pull_request() == {"my-check"}) is expect_producible


def test_a_scalar_matrix_value_leaves_the_template_unrendered(guard):
    """A non-list matrix value is returned AS-IS — a name that cannot match.

    Fail-closed by design: the caller then holds a name that will not match, so a
    required check it would have produced reads as unproducible.
    """
    assert guard._render_matrix("test (${{ matrix.a }})", {"a": 5}) == {
        "test (${{ matrix.a }})"
    }


@pytest.mark.parametrize("declared,needle", [
    ({"only-queue": ("queue", "x")}, "EMPTY merge bucket"),
    ({"only-merge": ("merge", "x")}, "EMPTY queue bucket"),
    ({"weird": ("bogus", "x")}, "unknown bucket"),
    ({"blank": ("queue", "   ")}, "no recorded guarantee"),
])
def test_partition_flags_a_broken_enumeration(guard, monkeypatch, declared, needle):
    """The empty-bucket / unknown-bucket / no-guarantee branches of `check_partition`."""
    monkeypatch.setattr(guard, "REQUIRED_SET", declared)
    problems = guard.check_partition({"queue": set(), "merge": set()})
    assert any(needle in p for p in problems), problems


@pytest.mark.parametrize("mode,needle", [
    ("raises", "could not run gh"),
    ("returncode", "could not read branch protection"),
    ("not-json", "non-JSON"),
])
def test_live_read_failures_cannot_be_measured(guard, monkeypatch, mode, needle):
    """The three gh-invocation guards, each ANCHORED on its own message.

    The `returncode` case must return VALID JSON: with a shared `stdout="not json"`
    fixture the sibling JSONDecodeError guard satisfied the assertion, so deleting
    the returncode guard left the suite green — and a FAILED `gh` call whose stdout
    happened to be JSON would have been accepted as a measurement.
    """
    import subprocess

    if mode == "raises":
        def fake(*a, **k):
            raise OSError("gh not found")
    elif mode == "returncode":
        def fake(*a, **k):
            class Fail:
                returncode = 1
                stdout = '{"contexts": ["ghost"], "strict": false}'
                stderr = "gh: Not Found (HTTP 404)"
            return Fail()
    else:
        def fake(*a, **k):
            class Bad:
                returncode = 0
                stdout = "not json"
                stderr = ""
            return Bad()
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(guard.CannotMeasure, match=needle):
        guard.read_live_protection()


def test_a_non_string_mergify_condition_cannot_be_measured(guard, tmp_guard_env):
    """A non-string condition used to reach `.startswith` -> AttributeError."""
    guard.MERGIFY_PATH.write_text(
        "queue_rules:\n  - name: main\n    queue_conditions:\n      - 5\n"
        "    merge_conditions:\n      - check-success=python-ci-gate\n")
    with pytest.raises(guard.CannotMeasure, match="entries must be strings"):
        guard.load_mergify()


def test_a_non_string_job_name_cannot_be_measured(guard, tmp_guard_env):
    """`name: 5` used to reach `.strip()` -> AttributeError."""
    (tmp_guard_env / "workflows" / "bad.yml").write_text(
        "on:\n  pull_request:\njobs:\n  g:\n    name: 5\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    with pytest.raises(guard.CannotMeasure, match=r"jobs\.g\.name must be a string"):
        guard.producible_on_pull_request()


def test_a_main_entry_without_required_status_checks_is_usable(guard, tmp_guard_env):
    """`rsc is None -> {}`: an entry with no `required_status_checks` declares nothing."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n")
    assert guard.declared_settings_contexts() == set()


def test_a_bare_required_status_checks_is_not_iterated(guard, tmp_guard_env):
    """`required_status_checks:` with no `contexts` used to hit `None` iteration."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        strict: false\n")
    assert guard.declared_settings_contexts() == set()
    assert guard.declared_settings_strict() is False


def test_check_deadlock_names_the_bucket_it_was_given(guard):
    """`bucket` is REQUIRED — there is no default arm to fall through to.

    The default used to be `"merge_conditions"`, unreachable from both call
    sites, which is why it was deleted rather than kept as a fallback.
    """
    problems = guard.check_deadlock({"alpha"}, set(), "merge_conditions")
    assert any("merge_conditions" in p for p in problems), problems
    entry = guard.check_deadlock({"alpha"}, set(), "queue_conditions")
    assert any("queue_conditions" in p for p in entry), entry


def test_an_unreadable_mirror_is_not_read_as_absent(guard, tmp_guard_env, monkeypatch):
    """PRESENT-but-UNREADABLE must not skip the comparison (the cardinal sin).

    `os.path.lexists` swallows PermissionError and returned False, so a locked
    settings.yml was classified absent and the guard exited 0 with a surface
    never compared.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    locked = tmp_guard_env / "locked"
    locked.mkdir()
    (locked / "settings.yml").write_text("repository: {}\n")
    locked.chmod(0o000)
    monkeypatch.setattr(guard, "SETTINGS_PATH", locked / "settings.yml")
    try:
        with pytest.raises(guard.CannotMeasure):
            guard.declared_settings_contexts()
    finally:
        locked.chmod(0o700)


def test_a_rule_with_only_merge_conditions_is_usable(guard, tmp_guard_env):
    """`conds is None -> continue`: a rule may declare only ONE of the two lists.

    Deleting that `continue` makes `require_list(None, ...)` raise — turning a
    legitimate one-sided rule into a false exit 2.
    """
    guard.MERGIFY_PATH.write_text(
        "queue_rules:\n  - name: main\n    merge_conditions:\n"
        "      - check-success=python-ci-gate\n")
    assert guard.load_mergify() == {"queue": set(), "merge": {"python-ci-gate"}}


def test_a_step_without_a_run_is_skipped(guard, tmp_guard_env):
    """A `uses:` step has no `run:` — it must be skipped, not treated as unusable."""
    guard.PYTHON_CI_PATH.write_text(
        "jobs:\n  python-ci-gate:\n    needs: [alpha]\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - run: |\n          done <<'LEGS'\n          alpha|success|-\n          LEGS\n")
    assert guard.gate_legs() == (["alpha"], {"alpha"})


def test_a_blank_job_name_falls_back_to_the_job_id(guard, tmp_guard_env):
    """A whitespace-only `name:` is not a check name; the job id is."""
    (tmp_guard_env / "workflows" / "w.yml").write_text(
        "on:\n  pull_request:\njobs:\n  mycheck:\n    name: '   '\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    assert guard.producible_on_pull_request() == {"mycheck"}


def test_an_absent_jobs_key_names_the_missing_gate_job(guard, tmp_guard_env):
    """`jobs or {}` keeps the error message about the GATE, not about `jobs:`."""
    guard.PYTHON_CI_PATH.write_text("name: ci\n")
    with pytest.raises(guard.CannotMeasure, match="no 'python-ci-gate' job"):
        guard.gate_legs()


def test_a_null_gate_job_cannot_be_measured(guard, tmp_guard_env):
    """`python-ci-gate:` with no body: `gate or {}` turns it into "no LEGS table".

    Both the shipped path and the un-coerced one exit 2, so only the MESSAGE
    distinguishes them — an unanchored assertion would pass either way.
    """
    guard.PYTHON_CI_PATH.write_text("jobs:\n  python-ci-gate:\n")
    with pytest.raises(guard.CannotMeasure, match="no <<'LEGS' table"):
        guard.gate_legs()


DEEP_NESTING = "[" * 3000 + "]" * 3000


@pytest.mark.parametrize("seam", ["mergify", "settings", "python-ci"])
def test_a_deeply_nested_config_cannot_be_measured(guard, tmp_guard_env, seam):
    """A deep document makes PyYAML raise RecursionError — not YAMLError, not OSError.

    Uncaught it escaped as an unannotated traceback (exit 1) at every seam.
    """
    path = {"mergify": guard.MERGIFY_PATH, "settings": guard.SETTINGS_PATH,
            "python-ci": guard.PYTHON_CI_PATH}[seam]
    call = {"mergify": guard.load_mergify, "settings": guard.declared_settings_contexts,
            "python-ci": guard.gate_legs}[seam]
    path.write_text(DEEP_NESTING)
    with pytest.raises(guard.CannotMeasure, match="nesting too deep"):
        call()


def test_a_deeply_nested_workflow_cannot_be_measured(guard, tmp_guard_env):
    deep = tmp_guard_env / "workflows" / "deep.yml"
    deep.write_text(DEEP_NESTING)
    with pytest.raises(guard.CannotMeasure, match="nesting too deep"):
        guard.read_yaml(deep)


def test_a_null_branch_is_not_main(guard, tmp_guard_env):
    """`branch: null` must not traceback on `.strip()` — it simply is not `main`."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch:\n"
        "      required_status_checks:\n        contexts:\n          - alpha\n")
    assert guard.declared_settings_contexts() == set()
    assert guard.declared_settings_off_main() == ["None"]


def test_a_padded_branch_is_still_main(guard, tmp_guard_env):
    """`branch: ' main '` is main — `.strip()` is load-bearing, not cosmetic."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: ' main '\n"
        "      required_status_checks:\n        contexts:\n          - alpha\n")
    assert guard.declared_settings_contexts() == {"alpha"}


def test_a_live_payload_without_contexts_reads_as_empty(guard, monkeypatch):
    """`[] if ... is None else ...` — an ABSENT field means 'no live contexts'.

    The real expression is `[] if payload.get("contexts") is None else
    payload["contexts"]`, NOT `payload.get("contexts") or []`: the latter coerced
    `0`/`false`/`""` to an empty required set, which is the fail-open that
    `test_a_falsy_non_list_live_contexts_cannot_be_measured` exists to prevent.
    """
    import json
    import subprocess

    class Done:
        returncode = 0
        stdout = json.dumps({"strict": False})
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    assert guard.read_live_protection() == (set(), False)


def test_a_config_too_large_to_read_cannot_be_measured(guard, tmp_guard_env, monkeypatch):
    """`read_text` allocates BEFORE the parser, so it needs its own MemoryError guard.

    Guarding only `yaml.safe_load` left the >RAM case escaping as an unannotated
    traceback (exit 1).
    """
    import pathlib

    guard.SETTINGS_PATH.write_text("repository: {}\n")
    real = pathlib.Path.read_text

    def fake(self, *a, **k):
        if self == guard.SETTINGS_PATH:
            raise MemoryError("cannot allocate")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", fake)
    with pytest.raises(guard.CannotMeasure, match="unreadable"):
        guard.declared_settings_contexts()


def test_a_live_run_reports_the_live_note(guard, tmp_guard_env, monkeypatch):
    """The `live_contexts is not None` NOTES branch (not the violations one)."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["alpha", "beta"])
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        contexts:\n"
        "          - alpha\n          - beta\n")
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    monkeypatch.setattr(guard, "read_live_protection", lambda: ({"alpha", "beta"}, False))
    _, _, notes = guard.run(live=True)
    assert any(n.startswith("LIVE required    :") and "alpha" in n for n in notes), notes
    _, _, offline = guard.run(live=False)
    assert any("not read (offline mode" in n for n in offline), offline


def test_an_unparsable_document_too_large_for_memory_cannot_be_measured(guard, tmp_guard_env, monkeypatch):
    """The parser needs its OWN MemoryError guard, separate from the read's.

    A document small enough to READ can still fail to PARSE into an object graph
    that fits; that MemoryError escaped as an unannotated traceback.
    """
    guard.SETTINGS_PATH.write_text("repository: {}\n")

    def fake_load(*a, **k):
        raise MemoryError("cannot allocate")

    monkeypatch.setattr(guard.yaml, "safe_load", fake_load)
    with pytest.raises(guard.CannotMeasure, match="too large to parse"):
        guard.declared_settings_contexts()


def test_a_mirror_path_whose_parent_is_a_file_is_absent(guard, tmp_guard_env, monkeypatch):
    """`os.lstat`'s `NotADirectoryError` arm — constructible on POSIX and Windows.

    `parent/settings.yml` where `parent` is a FILE raises NotADirectoryError; that
    is a path with no directory entry, so the mirror is absent (skip), not a crash.
    """
    blocker = tmp_guard_env / "afile"
    blocker.write_text("not a directory\n")
    monkeypatch.setattr(guard, "SETTINGS_PATH", blocker / "settings.yml")
    assert guard.declared_settings_contexts() is None


def test_a_malformed_but_decodable_workflow_is_skipped(guard, tmp_guard_env):
    """The `yaml.YAMLError` arm of `producible_on_pull_request`'s except.

    Documented as a deliberate skip: a broken workflow can only make a name look
    unproducible (a false RED), never manufacture a false GREEN.
    """
    for leftover in (tmp_guard_env / "workflows").glob("*"):
        leftover.unlink()
    (tmp_guard_env / "workflows" / "broken.yml").write_text("on: [pull_request\n")
    (tmp_guard_env / "workflows" / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n  mycheck:\n    name: my-check\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    assert guard.producible_on_pull_request() == {"my-check"}


def test_a_directory_where_a_config_file_belongs_cannot_be_measured(guard, tmp_guard_env):
    """The `OSError` arm of `_read_yaml_cached`'s read guard.

    `stat()` succeeds on a directory, so the failure lands on `read_text`.
    """
    target = tmp_guard_env / "python-ci.yml"
    target.unlink(missing_ok=True)
    target.mkdir()
    with pytest.raises(guard.CannotMeasure, match="unreadable"):
        guard.gate_legs()


def test_a_gh_timeout_cannot_be_measured(guard, monkeypatch):
    """The `subprocess.SubprocessError` arm — a 60 s timeout raises TimeoutExpired."""
    import subprocess

    def fake(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=60)

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(guard.CannotMeasure, match="could not run gh"):
        guard.read_live_protection()


@pytest.mark.parametrize("seam", ["mergify", "settings", "python-ci"])
def test_an_unbuildable_yaml_value_cannot_be_measured(guard, tmp_guard_env, seam):
    """PyYAML raises ValueError for a resolved-but-unbuildable value.

    `x: 2001-02-31` is a valid timestamp SHAPE but an impossible date. It is not a
    YAMLError, so it escaped as an unannotated traceback, offline, in CI.
    """
    path = {"mergify": guard.MERGIFY_PATH, "settings": guard.SETTINGS_PATH,
            "python-ci": guard.PYTHON_CI_PATH}[seam]
    call = {"mergify": guard.load_mergify, "settings": guard.declared_settings_contexts,
            "python-ci": guard.gate_legs}[seam]
    path.write_text("last_reconciled: 2001-02-31\n")
    with pytest.raises(guard.CannotMeasure, match="unparsable"):
        call()


def test_an_unbuildable_value_in_a_workflow_cannot_be_measured(guard, tmp_guard_env):
    deep = tmp_guard_env / "workflows" / "bad-date.yml"
    deep.write_text("on:\n  pull_request:\nlast_reconciled: 2001-02-31\n")
    with pytest.raises(guard.CannotMeasure, match="unparsable"):
        guard.read_yaml(deep)


def test_undecodable_gh_output_cannot_be_measured(guard, monkeypatch):
    """`text=True` decoded gh's output with the locale encoding.

    A malformed byte raised UnicodeDecodeError inside `subprocess`, which is a
    ValueError — caught by neither OSError nor SubprocessError.
    """
    import subprocess

    def fake(*a, **k):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(guard.CannotMeasure, match="could not run gh"):
        guard.read_live_protection()


@pytest.mark.parametrize("value", [False, 0, ""])
def test_a_falsy_non_list_live_contexts_cannot_be_measured(guard, monkeypatch, value):
    """PRESENT-but-falsy must not be coerced to `[]`, as at `needs:` and `read_yaml`."""
    import json
    import subprocess

    class Done:
        returncode = 0
        stdout = json.dumps({"contexts": value, "strict": False})
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(guard.CannotMeasure, match="must be a list"):
        guard.read_live_protection()


def test_an_unreadable_workflows_dir_cannot_be_measured(guard, tmp_guard_env):
    """An unreadable workflows directory must NOT read as an EMPTY one.

    `Path.glob` silently swallows EACCES and yields no names, so this used to
    FABRICATE a measurement: "NO pull_request-triggered workflow produces it" for
    every required check — six false deadlock violations, exit 1 — instead of 2.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    locked = tmp_guard_env / "lockedwf"
    locked.mkdir()
    (locked / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n  mycheck:\n    name: my-check\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    locked.chmod(0o000)
    try:
        with pytest.raises(guard.CannotMeasure):
            guard.producible_on_pull_request(locked)
    finally:
        locked.chmod(0o700)


def test_an_unreadable_parent_dir_cannot_be_measured(guard, tmp_guard_env):
    """`is_dir()` re-raises EACCES — it must not escape as a traceback."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    locked = tmp_guard_env / "lockedparent"
    (locked / "workflows").mkdir(parents=True)
    locked.chmod(0o000)
    try:
        with pytest.raises(guard.CannotMeasure):
            guard.producible_on_pull_request(locked / "workflows")
    finally:
        locked.chmod(0o700)


def test_an_unreadable_config_path_cannot_be_measured(guard, tmp_guard_env, monkeypatch):
    """`Path.exists()` re-raises EACCES; this is `run()`'s FIRST filesystem touch."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    locked = tmp_guard_env / "lockedcfg"
    locked.mkdir()
    (locked / ".mergify.yml").write_text("queue_rules: []\n")
    locked.chmod(0o000)
    try:
        monkeypatch.setattr(guard, "MERGIFY_PATH", locked / ".mergify.yml")
        with pytest.raises(guard.CannotMeasure):
            guard.load_mergify()
        monkeypatch.setattr(guard, "PYTHON_CI_PATH", locked / "python-ci.yml")
        with pytest.raises(guard.CannotMeasure):
            guard.gate_legs()
    finally:
        locked.chmod(0o700)


def test_an_absent_mirror_is_reported_as_not_compared(guard, tmp_guard_env, monkeypatch):
    """An absent mirror must not be reported as having AGREED."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta", "gamma"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["alpha", "beta"])
    guard.SETTINGS_PATH.unlink(missing_ok=True)
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y"),
                         "gamma": ("injected", "enforced by branch-protection injection")})
    code, _, notes = guard.run()
    assert code == 0, notes
    assert any("ABSENT" in n and "not compared" in n for n in notes), notes


@pytest.mark.parametrize("mode,flagged", [
    ("merge", False),
    ("queue", True),      # the default: injected contexts gate ENTRY too
    (None, True),         # absent == Mergify's `queue` default
    ("sort", True),      # a typo is not a recognised mode
    (True, True),
])
def test_the_injection_mode_must_be_merge(guard, tmp_guard_env, mode, flagged):
    """`queue`/absent makes an `injected` name an ENTRY gate — a false claim.

    `tmp_guard_env` is REQUIRED here, not decoration: without it this test writes
    its fixture into the real `.mergify.yml` (see the autouse
    `_no_test_writes_a_real_file` guard, which now catches that).
    """
    if mode is None:
        guard.MERGIFY_PATH.write_text("queue_rules:\n  - name: main\n")
    else:
        guard.MERGIFY_PATH.write_text(
            "queue_rules:\n  - name: main\n"
            f"    branch_protection_injection_mode: {mode!r}\n")
    assert bool(guard.check_injection_mode(guard.read_injection_modes())) is flagged


def test_a_second_queue_rule_is_also_measured(guard, tmp_guard_env):
    """EVERY queue rule injects independently — reading only the first is a hole.

    With two rules, `[{merge}, {queue}]`, the second still injects the
    branch-protection contexts for QUEUING, so the enumeration's "deliberately not
    an entry gate" claim is false of it. A first-rule-only read returned `merge`
    for the whole file and reported clean.
    """
    guard.MERGIFY_PATH.write_text(
        "queue_rules:\n"
        "  - name: first\n    branch_protection_injection_mode: merge\n"
        "  - name: second\n")
    modes = guard.read_injection_modes()
    assert modes == ["merge", None], modes
    problems = guard.check_injection_mode(modes)
    assert len(problems) == 1 and "queue_rules[1]" in problems[0], problems


def test_a_non_list_queue_rules_is_a_cannot_measure_not_an_empty_list(guard, tmp_guard_env):
    """PRESENT-but-unusable `queue_rules` is exit 2, not "declared nothing".

    Returning `[]` reported an unreadable value as an absent one: the violation
    that followed was exit 1 rather than exit 2, and a direct caller comparing
    modes could read the empty list as success — a fail-open. Raising matches
    `require_list`'s sibling behaviour and the module's own distinction between
    absent and unusable.
    """
    for bad in ("queue_rules: 0\n", "queue_rules: {}\n", "queue_rules: nothing\n"):
        guard.MERGIFY_PATH.write_text(bad)
        with pytest.raises(guard.CannotMeasure, match="absent or not a list"):
            guard.read_injection_modes()


def test_an_absent_queue_rules_also_cannot_be_measured(guard, tmp_guard_env):
    """The ABSENT key reaches the same raise, and the message must not deny it.

    `cfg.get("queue_rules")` returns None for an absent key exactly as it does for
    an explicit null, so the message said "present but not a list" about a key that
    was not present — the very absent/unusable distinction this branch draws.
    """
    guard.MERGIFY_PATH.write_text("pull_request_rules: []\n")
    with pytest.raises(guard.CannotMeasure, match="absent or not a list"):
        guard.read_injection_modes()


def test_no_queue_rule_at_all_is_a_violation(guard):
    """The mode justifies every `injected` name, so it cannot simply be assumed."""
    assert guard.check_injection_mode([]) != []


def test_an_injected_name_named_in_mergify_is_a_violation(guard, tmp_guard_env, monkeypatch):
    """Naming an `injected` name in a list turns it into an ENTRY gate too."""
    _write_minimal_mergify(guard, ["alpha"], ["beta", "gamma"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta", "gamma"])
    _write_minimal_gate(guard, needs=["alpha"], legs=["alpha"])
    guard.SETTINGS_PATH.unlink(missing_ok=True)
    monkeypatch.setattr(guard, "REQUIRED_SET", {
        "alpha": ("queue", "x"), "beta": ("merge", "y"),
        "gamma": ("injected", "enforced by injection only")})
    code, violations, _ = guard.run()
    assert code == 1
    assert any("declared 'injected' but IS named" in v for v in violations), violations


def test_an_unproducible_injected_name_is_a_deadlock_through_the_injection_door(
        guard, tmp_guard_env, monkeypatch):
    """A live-required check no PR-like workflow produces deadlocks the merge.

    It is never named in `.mergify.yml`, so `check_deadlock` never sees it — this
    is the only guard that can catch it.
    """
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha"], legs=["alpha"])
    guard.SETTINGS_PATH.unlink(missing_ok=True)
    monkeypatch.setattr(guard, "REQUIRED_SET", {
        "alpha": ("queue", "x"), "beta": ("merge", "y"),
        "ghost": ("injected", "required on main, enforced by injection")})
    code, violations, _ = guard.run()
    assert code == 1
    assert any("deadlock through the injection door" in v for v in violations), violations


def test_the_injection_mode_is_checked_through_run(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must actually CALL check_injection_mode."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["alpha", "beta"])
    guard.SETTINGS_PATH.unlink(missing_ok=True)
    # Flip the mode to the default in the fixture file itself.
    guard.MERGIFY_PATH.write_text(
        guard.MERGIFY_PATH.read_text().replace(
            "    branch_protection_injection_mode: merge\n", ""))
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("branch_protection_injection_mode" in v for v in violations), violations


@pytest.mark.parametrize("cond", [
    False,          # YAML `if: false`
    "false",        # string spelling GitHub evaluates as falsy
    "FALSE",
    "${{ false }}",
    "${{false}}",     # no spaces — one character away from the caught form
    "${{  FALSE  }}",
    "${{ 0 }}",
    "${{ null }}",
    "${{ '' }}",      # the EMPTY constant, wrapped
    "null",
    "0",
    0,
    0.0,              # YAML `if: 0.0` arrives as a FLOAT, not the string "0.0"
    -0.0,
    "",
    '""',             # two characters — a DIFFERENT input from the empty string
    "''",
    "-0",
    "0.0",
    # Exponent forms. GitHub numbers are "any number format supported by JSON", so
    # these are ALL the number zero — but YAML 1.1 resolves none of them, because
    # PyYAML's float pattern REQUIRES a dot and its optional exponent group is itself
    # sign-REQUIRING (`[eE][-+][0-9]+`): `0e+0` has the sign and no dot, `0.0e0` has
    # the dot and no sign. PyYAML hands every one over as `str`, so the numeric arm
    # never sees them, and undecoded each was called PRODUCIBLE.
    # These rows are the ONLY thing pinning `_is_a_zero_number`: deleting it leaves
    # them green, because every OTHER falsy row is caught by `_FALSY_IF_STRINGS` or
    # by the `(int, float)` arm without it.
    "0e0",
    "0E0",
    "-0e0",
    "0.0e0",
    "0e+0",
    "${{ 0e0 }}",
    "${{ 0.0e0 }}",
])
def test_a_literally_falsy_if_is_gated_off(guard, cond):
    """A LITERAL falsy `if:` is the ONE decidable case: the job can never run.

    It used to fall into the `not isinstance(condition, str)` early-return and be
    counted PRODUCIBLE, so a required check whose only producer was `if: false`
    read as arriving forever — a false GREEN in the direction the deadlock checks
    must never fail in.

    The `${{ }}` spellings are the point: GitHub sees `${{false}}`, `${{ 0 }}` and
    `${{ null }}` as the SAME constants as their bare forms, so matching only exact
    strings left them producible. Those rows are why the comparison normalises.
    """
    assert guard._gated_off_pr_refs({"if": cond}) is True, cond


@pytest.mark.parametrize("job", [
    {},                      # the `if:` KEY IS ABSENT -> the job runs
    {"if": True},
    {"if": "always()"},
    {"if": "${{ true }}"},
    {"if": 1},
    {"if": "${{ 1 }}"},
    {"if": "${{ !cancelled() }}"},
    # NON-zero numbers in exponent form must stay PRODUCIBLE. `float()` accepts every
    # JSON number form, so the guard has to distinguish the VALUE from the spelling —
    # the same reason `nan` and `inf` (which DO parse) stay producible below.
    {"if": "1e0"},
    {"if": "1e5"},
    {"if": "2.5e-1"},
    {"if": "nan"},
    {"if": ".inf"},
    # A YAML-1.1 octal zero is not a JSON number, so it is not decoded — the same
    # deliberate reading as any expression this module cannot decide.
    {"if": "0o0"},
])
def test_an_absent_or_truthy_if_is_producible(guard, job):
    """`{}` and truthy conditions are producible; an ABSENT `if:` means the job runs.

    The first row is the one that matters: `{}` has NO `if` key. A row passing
    `{"if": None}` does NOT exercise absence — it is a key present with a null
    value, which is the falsy CONSTANT and is asserted gated off elsewhere. The
    merged `job.get("if")` could not tell those apart, so the old row pinned the
    wrong behaviour under an absence rationale.
    """
    assert guard._gated_off_pr_refs(job) is False, job


@pytest.mark.parametrize("job", [{"if": None}, {"if": "null"},
                                  {"if": "${{ null }}"}])
def test_an_explicit_null_if_is_gated_off(guard, job):
    """The null CONSTANT is falsy in every spelling, including the bare one.

    `if: null` used to be counted producible (it arrives as `None`) while
    `${{ null }}` was gated off — the guard contradicting itself on one constant,
    with the NATURAL spelling taking the unsafe branch.
    """
    assert guard._gated_off_pr_refs(job) is True, job


def test_an_empty_string_if_is_gated_off_through_yaml(guard, tmp_guard_env):
    """`if: ''` must reach the falsy set through a real YAML parse.

    The direct-`dict` test above cannot see the transformation that matters:
    `if: ''` parses to the ZERO-character string, not to the two-character `'""'`.
    Listing only the two-character forms left this case producible while looking
    covered, so it is pinned end-to-end here.
    """
    guard.WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    (guard.WORKFLOWS_DIR / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n"
        "  alive:\n    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"
        "  blank:\n    if: ''\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: 'true'\n"
        "  dquoted:\n    if: \"\"\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: 'true'\n"
        "  wrapped:\n    if: \"${{ '' }}\"\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: 'true'\n")
    names = guard.producible_on_pull_request()
    assert "alive" in names
    for never in ("blank", "dquoted", "wrapped"):
        assert never not in names, (
            f"{never!r} is gated on a falsy constant, so it never produces a check "
            f"run; counting it producible is a false GREEN in the deadlock checks")


def test_a_never_running_job_is_not_producible(guard, tmp_guard_env):
    """COMPOSITION: `if: false` must remove the name from the producible set."""
    guard.WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    (guard.WORKFLOWS_DIR / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n"
        "  alive:\n    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"
        "  dead:\n    if: false\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: 'true'\n")
    names = guard.producible_on_pull_request()
    assert "alive" in names
    assert "dead" not in names, (
        "a job gated `if: false` never produces a check run, so counting it "
        "producible is a false GREEN in the deadlock checks")


def test_an_empty_injected_bucket_is_fail_closed(guard, tmp_guard_env, monkeypatch):
    """The `injected` bucket has no second file, so empty must fail closed.

    Unlike `queue`/`merge`, nothing outside the guard records that a
    live-required name is enforced by injection, so an emptied bucket is
    UNVERIFIABLE offline rather than merely unusual.
    """
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha"], legs=["alpha"])
    guard.SETTINGS_PATH.unlink(missing_ok=True)
    monkeypatch.setattr(guard, "REQUIRED_SET", {
        "alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("EMPTY injected bucket" in v for v in violations), violations


def test_the_real_queue_lists_are_all_produced_on_pr_refs(guard):
    """Every condition the queue waits on must exist on a PR-like ref — BOTH lists.

    `queue_conditions` gate entry against the PR head and `merge_conditions`
    gate the merge against the queue branch; both refs are PR-like, so an
    unproducible name in EITHER stalls every PR.
    """
    parsed = guard.load_mergify()
    producible = guard.producible_on_pull_request()
    for bucket, names in parsed.items():
        assert not guard.check_deadlock(names, producible, f"{bucket}_conditions"), bucket


def test_the_real_declarative_mirror_is_not_stale(guard):
    """`.github/settings.yml` must agree with the enumeration (or not exist).

    Compared against the ENUMERATION, not `queue | merge`: the mirror is a mirror
    of live branch protection, which includes the `injected` name.
    """
    contexts = guard.declared_settings_contexts()
    assert guard.check_settings(contexts, set(guard.REQUIRED_SET)) == []
    assert contexts is not None and "ai-review-gate" in contexts


# ── partition mutations: a dropped or mis-filed name must fail ─────────────


def test_a_name_missing_from_the_enumeration_fails(guard):
    """A `check-success=` in `.mergify.yml` with no enumeration entry fails."""
    parsed = {"queue": {"docs"}, "merge": {"python-ci-gate", "smuggled-check"}}
    violations = guard.check_partition(parsed)
    assert any("smuggled-check" in v and "unaccounted" in v for v in violations), violations


def test_a_declared_name_dropped_from_mergify_fails(guard):
    """A declared name absent from `.mergify.yml` fails (a dropped condition)."""
    parsed = {"queue": {"docs"}, "merge": {"python-ci-gate"}}
    violations = guard.check_partition(parsed)
    assert any("missing from .mergify.yml" in v for v in violations), violations


def test_a_name_in_both_lists_fails(guard):
    """The invariant is EXACTLY ONE list — an overlap is a mis-filing."""
    parsed = {"queue": {"docs", "python-ci-gate"}, "merge": {"python-ci-gate"}}
    violations = guard.check_partition(parsed)
    assert any("BOTH" in v for v in violations), violations


def test_an_empty_guarantee_fails(guard, monkeypatch):
    """Coverage accounting cannot be blank — that is the #2656 failure shape."""
    monkeypatch.setitem(guard.REQUIRED_SET, "docs", ("queue", "   "))
    parsed = {"queue": {"docs"}, "merge": set()}
    violations = guard.check_partition(parsed)
    assert any("no recorded guarantee" in v for v in violations), violations


# ── the deadlock mutation ──────────────────────────────────────────────────


def test_a_merge_condition_only_a_push_workflow_produces_is_a_deadlock(guard, tmp_guard_env):
    """A push-only check is never reported on the queue branch → deadlock."""
    (tmp_guard_env / "workflows" / "push-only.yml").write_text(
        "on:\n  push:\n    branches: [main]\njobs:\n  push-only-gate:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    producible = guard.producible_on_pull_request()
    assert "push-only-gate" not in producible
    violations = guard.check_deadlock({"push-only-gate"}, producible,
                                      "merge_conditions")
    assert any("DEADLOCK" in v for v in violations), violations


def test_a_queue_condition_only_a_push_workflow_produces_stalls_entry(guard, tmp_guard_env):
    """The ENTRY half: an unproducible queue_condition stalls entry forever."""
    (tmp_guard_env / "workflows" / "push-only-entry.yml").write_text(
        "on:\n  push:\n    branches: [main]\njobs:\n  entry-gate:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    producible = guard.producible_on_pull_request()
    assert "entry-gate" not in producible
    violations = guard.check_deadlock({"entry-gate"}, producible, "queue_conditions")
    assert any("ENTRY stalls" in v for v in violations), violations


def test_a_pull_request_check_is_not_a_deadlock(guard, tmp_guard_env):
    """The same check on a pull_request trigger is fine."""
    (tmp_guard_env / "workflows" / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n  pr-gate:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    producible = guard.producible_on_pull_request()
    assert "pr-gate" in producible
    assert guard.check_deadlock({"pr-gate"}, producible,
                                "merge_conditions") == []


def test_a_workflow_parse_uses_the_boolean_on_key(guard, tmp_guard_env):
    """PyYAML resolves a bare `on:` key to the BOOLEAN True — it must still read."""
    import yaml as _yaml
    doc = _yaml.safe_load("on:\n  pull_request:\njobs:\n  g:\n    runs-on: ubuntu-latest\n")
    assert True in doc and "on" not in doc, "PyYAML changed its `on:` handling"
    (tmp_guard_env / "workflows" / "b.yml").write_text(
        "on:\n  pull_request:\njobs:\n  boolean-on-gate:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    assert "boolean-on-gate" in guard.producible_on_pull_request()


# ── the gate-legs mutation: the #5219 door ─────────────────────────────────


def test_a_leg_in_needs_with_no_legs_row_would_certify_a_skipped_shard(guard):
    """The mutation that matters: drop a LEGS row and the leg escapes fail-closed."""
    needs = ["changes", "test", "test-slow"]
    legs = {"changes", "test"}  # `test-slow` row deleted
    violations = guard.check_gate_legs(needs, legs)
    assert any("test-slow" in v and "skipped" in v for v in violations), violations


def test_a_legs_row_for_a_leg_not_in_needs_is_dead(guard):
    """The other direction: a row whose result can never be read."""
    violations = guard.check_gate_legs(["changes"], {"changes", "ghost-leg"})
    assert any("ghost-leg" in v and "dead" in v for v in violations), violations


def test_an_empty_needs_observes_nothing(guard):
    """An aggregate that needs nothing certifies nothing."""
    violations = guard.check_gate_legs([], set())
    assert any("observes nothing" in v for v in violations), violations


def test_a_multi_ref_job_name_pairs_each_key_with_its_own_values(guard):
    """Each `matrix.<key>` placeholder takes THAT key's value, not the leftmost.

    Substituting the first regex match instead of the named key's placeholder
    cross-pairs the values and invents check names that do not exist — which
    would read as a deadlock once a job name carries two matrix refs.
    """
    rendered = guard._render_matrix(
        "test (${{ matrix.b }}-${{ matrix.a }})", {"a": ["A1", "A2"], "b": ["B1", "B2"]})
    # A two-key matrix expands to the full CROSS PRODUCT (as GitHub does); the
    # defect this pins is PAIRING — before the fix, every value landed in the
    # leftmost slot, so b's values never appeared in b's position.
    assert rendered == {
        "test (B1-A1)", "test (B1-A2)", "test (B2-A1)", "test (B2-A2)"}, rendered
    assert all(name.startswith("test (B") for name in rendered), rendered


def test_a_repeated_ref_of_the_same_key_renders_every_occurrence(guard):
    """`${{ matrix.a }}` twice must fill BOTH slots with the same value.

    A first-match-only substitution leaves the second occurrence LITERAL, which
    is a check name that can never match a real check — a spurious deadlock —
    and it survived the cross-key test above.
    """
    rendered = guard._render_matrix(
        "test (${{ matrix.a }}-${{ matrix.a }})", {"a": ["A1", "A2"]})
    assert rendered == {"test (A1-A1)", "test (A2-A2)"}, rendered
    assert all("${{" not in name for name in rendered), rendered


def test_a_whitespace_varied_placeholder_still_renders(guard):
    """`${{matrix.a}}` (no spaces) is still a placeholder, not a literal."""
    assert guard._render_matrix("t (${{matrix.a}})", {"a": ["A1"]}) == {"t (A1)"}
    assert guard._render_matrix("t (${{  matrix.a  }})", {"a": ["A1"]}) == {"t (A1)"}


def test_a_single_ref_job_name_renders_every_value(guard):
    """The live shape: `test (${{ matrix.half }})` over half=[a,b]."""
    assert guard._render_matrix("test (${{ matrix.half }})", {"half": ["a", "b"]}) == {
        "test (a)", "test (b)"}


def test_gate_legs_parses_the_heredoc_not_a_bare_marker(guard, tmp_guard_env):
    """The opener is the tail of a command (`done <<'LEGS'`), not a bare token."""
    (tmp_guard_env / "python-ci.yml").write_text(
        "jobs:\n"
        "  python-ci-gate:\n"
        "    needs: [alpha, beta]\n"
        "    steps:\n"
        "      - run: |\n"
        "          while IFS='|' read -r leg result selected; do\n"
        "            echo \"$leg\"\n"
        "          done <<'LEGS'\n"
        "          alpha|${{ needs.alpha.result }}|-\n"
        "          beta|${{ needs.beta.result }}|${{ needs.changes.outputs.python }}\n"
        "          LEGS\n")
    needs, legs = guard.gate_legs()
    assert needs == ["alpha", "beta"] and legs == {"alpha", "beta"}


def test_a_gate_without_a_legs_table_cannot_be_measured(guard, tmp_guard_env):
    """No table → cannot measure (exit 2), never a silent pass."""
    (tmp_guard_env / "python-ci.yml").write_text(
        "jobs:\n  python-ci-gate:\n    needs: [alpha]\n    steps:\n      - run: echo hi\n")
    with pytest.raises(guard.CannotMeasure):
        guard.gate_legs()


# ── the mirror and the fail-closed paths ───────────────────────────────────


def test_a_stale_mirror_fails(guard, tmp_guard_env):
    """A third list that disagrees is the defect this guard was written for."""
    (tmp_guard_env / "settings.yml").write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        strict: true\n"
        "        contexts:\n          - redis-guard\n")
    violations = guard.check_settings({"redis-guard"}, {"docs", "python-ci-gate"})
    assert any("stale declarative mirror" in v for v in violations), violations


def test_an_absent_mirror_is_not_a_violation(guard):
    """Deleting the inert mirror is a legitimate fix."""
    assert guard.check_settings(None, {"docs"}) == []


def test_a_strict_mismatch_fails(guard):
    """.github/settings.yml's `strict: true` is the origin of #4764's stale premise."""
    violations = guard.check_declared_strict(False, True)
    assert any("strict" in v and "#4764" in v for v in violations), violations
    assert guard.check_declared_strict(False, False) == []
    assert guard.check_declared_strict(None, True) == [], "unknown live is not a mismatch"


def test_mergify_with_no_conditions_cannot_be_measured(guard, tmp_guard_env):
    """"Nothing to compare" is not a pass — exit 2."""
    guard.MERGIFY_PATH.write_text("queue_rules:\n  - name: main\n    queue_conditions:\n      - base=main\n")
    with pytest.raises(guard.CannotMeasure):
        guard.load_mergify()


def test_missing_mergify_cannot_be_measured(guard, tmp_guard_env):
    with pytest.raises(guard.CannotMeasure):
        guard.load_mergify()


def test_the_cli_exits_two_when_it_cannot_measure():
    """End-to-end fail-closed: a nonexistent config must not exit 0.

    Asserted on the ANNOTATION, not the bare text: `main()` prints every note
    before the annotation loop, so `"CANNOT MEASURE" in stdout` was satisfied by
    the note alone and deleting the `::error::` branch left the suite green.
    """
    d = Path(tempfile.mkdtemp(prefix="required-set-missing-"))
    r = _run({"MERGIFY_CONFIG": str(d / "absent.yml")})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "::error::CANNOT MEASURE" in r.stdout


def test_the_cli_exits_zero_on_the_real_repo(guard, capsys):
    """`main()` maps a clean run to exit 0 (in-process: no extra interpreter)."""
    assert guard.main([]) == 0
    out = capsys.readouterr().out
    assert "required-set sync" in out and "python-ci-gate" in out
    assert "LEGS row" in out


def test_the_cli_exits_one_when_a_violation_is_found(guard, capsys, monkeypatch):
    """`main()` maps a violation to exit 1 and emits an ::error:: annotation.

    `monkeypatch` (not a bare assignment) so the module-global enumeration is
    restored — otherwise the mutated set leaks into every later test.
    """
    monkeypatch.setattr(
        guard, "REQUIRED_SET", {"ghost": ("merge", "a name nothing produces")})
    assert guard.main([]) == 1
    assert "::error::required-set drift" in capsys.readouterr().out


@pytest.mark.skipif(os.environ.get("REQUIRED_SET_SYNC_LIVE") != "1",
                    reason="needs admin-scoped gh credentials (set REQUIRED_SET_SYNC_LIVE=1)")
def test_live_branch_protection_matches_the_enumeration():
    """The `--live` path: live required contexts == the enumeration.

    Env-gated because it shells out to `gh` with admin scope. `--live` is the
    owner/rail check; the offline assertions above are the CI contract.
    """
    r = _run({}, args=["--live"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "LIVE required" in r.stdout and "LIVE strict" in r.stdout
