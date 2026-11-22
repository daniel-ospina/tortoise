"""#3653: the CI redislite cleanup must never reap a live peer's server.

The defect these pin: `python-ci.yml` reclaimed stale embedded-redis litter by
running a host-global, unconditional ``pkill -f "redis-server unixsocket"`` in
the setup of every embedded leg. `pkill -f` matches *every* process on the box,
and this repo's own orphan gate measured ten other live pytest suites on the
same host (`other_suites=10`), so a leg that started later killed a concurrent
leg's live instance. redislite unlinks its socket on that SIGTERM, so the
victim's next connect raised ``Error 2 connecting to <socket>. No such file or
directory`` — ENOENT, not ECONNREFUSED — and the victim leg reddened with
assertions that had nothing to do with its diff.

Two things are pinned here:

* the DECISION TABLE of the replacement (``.github/scripts/reap-redislite-orphans.py``):
  a server is reaped only on a proof that no running leg can own it, and every
  unmeasurable case keeps it;
* the WORKFLOW SHAPE — no leg may reap by process name again. That half fails
  on the pre-fix tree, which is what makes the guard non-vacuous rather than a
  restatement of the fix.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "reap-redislite-orphans.py"
PYTHON_CI = REPO_ROOT / ".github" / "workflows" / "python-ci.yml"

# The legs that install the package and then clean up before their suite — the
# four that carried the blind `pkill`.
EMBEDDED_LEGS = ("test", "test-slow", "test-concurrency-falkor", "test-carve-out")


def _load_module():
    spec = importlib.util.spec_from_file_location("reap_redislite_orphans", SCRIPT)
    assert spec is not None and spec.loader is not None, SCRIPT
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reaper():
    return _load_module()


def _age_bound_s(reaper) -> float:
    """The SHIPPED age bound in seconds — never a second literal for it.

    Round 15 of the PR review: every boundary test used to pass `45 * 60` by hand,
    so the suite stayed green while the shipped default disagreed with the repo's
    own watchdog policy. The bound is read from the module under test here, and
    its two SOURCES are pinned by `test_the_age_arm_clears_every_embedded_lane_job_outer_timeout`
    and `test_the_age_arm_clears_the_widest_permitted_leg_watchdog` below — the
    tests that actually fail when the constant drifts out of safety.
    """
    return reaper.DEFAULT_MAX_AGE_MINUTES * 60.0


# ── the decision table ────────────────────────────────────────────────────


def test_a_live_peers_young_server_is_kept(reaper, tmp_path):
    """THE #3653 REGRESSION: a young server with a live socket dir is a peer's."""
    row = {"pid": 1, "socket_dir": str(tmp_path), "age_seconds": 90}
    reap, reason = reaper.classify(row, max_age_s=_age_bound_s(reaper))
    assert reap is False, reason
    assert "concurrent leg" in reason


def test_an_aged_server_is_reaped(reaper, tmp_path):
    """Past the widest OUTER cancel bound nothing running can own it."""
    bound = _age_bound_s(reaper)
    row = {"pid": 1, "socket_dir": str(tmp_path), "age_seconds": bound + 60}
    reap, reason = reaper.classify(row, max_age_s=bound)
    assert reap is True, reason
    assert "no running leg can own it" in reason


def test_a_server_whose_socket_dir_is_gone_is_reaped(reaper, tmp_path):
    """The #1005 residue: the dir is gone, so the instance can serve nothing."""
    gone = tmp_path / "vanished"
    row = {"pid": 1, "socket_dir": str(gone), "age_seconds": 1}
    reap, reason = reaper.classify(row, max_age_s=_age_bound_s(reaper))
    assert reap is True, reason
    assert "is gone" in reason


def test_an_unmeasurable_age_on_a_live_dir_is_kept(reaper, tmp_path):
    """Fail CLOSED: no age is not evidence of abandonment."""
    row = {"pid": 1, "socket_dir": str(tmp_path), "age_seconds": None}
    reap, reason = reaper.classify(row, max_age_s=_age_bound_s(reaper))
    assert reap is False, reason
    assert "fail closed" in reason


def test_undeterminable_ownership_is_kept(reaper):
    """An argv this code cannot read must never authorise a kill."""
    row = {"pid": 1, "socket_dir": None, "age_seconds": 10 * 60 * 60}
    reap, reason = reaper.classify(row, max_age_s=_age_bound_s(reaper))
    assert reap is False, reason
    assert "undeterminable" in reason


# ── the age arm's two SOURCES, pinned ────────────────────────────────────────
#
# The age arm's whole claim is a safety proof: "older than this, no live leg can
# own it". Round 15 of the PR review found the shipped 45-minute default did not
# clear the repo's own numbers — the docstring measured a 27-minute widest
# watchdog while `tools/ci_selection.py::WATCHDOG_CEILING_MIN` permits 55, and
# the widest JOB cap among the legs that run this step is 90. Nothing failed when
# the numbers moved, because every boundary test passed `45 * 60` in by hand.
# These two pins bind the constant to its sources, so drift REDS instead of
# silently restoring the live-peer kill (#3653).
#
# ROUND 2 (#10327 review, ISSUE 1): the first population was "jobs that invoke
# the reaper", but the proof quantifies over "jobs that can OWN a redislite
# server" — the tool reads the HOST's process table, and ownership is argv +
# age, never which workflow started the server. `ci.yml::flip-gate` sits in the
# gap: it runs `verify-cutover`, which runs ten pytest files on FalkorDBLite
# from inside a shell script, on a host that `python-ci.yml`'s legs also use —
# and it had NO job-level cap, i.e. GitHub's 360-minute default, so no age arm
# could cover it. Both assertions below stayed green while the proof was false
# for that job. The population is now the embedded lanes (see
# `_embedded_lane_job_outer_timeouts`), and `flip-gate` is capped at 30 in the
# workflow and named in `expected` so it cannot lose that cap silently.

# Jobs that set the embedded opt-in for a suite that owns NO server. Both run
# `tests/e2e/…` request-only suites (no redislite import anywhere under
# `tests/e2e/` outside `tests/e2e/hosted`, which runs in its own 15-minute job)
# and say so in the workflow (`deploy-pages.yml`: "this suite is requests-only
# (no embedded DB server)"). Exempted BY NAME, not by a pattern: the exemption
# is a CLAIM, so it is written where a reviewer can check it, and a job that
# later grows an embedded server has to be taken off this list by hand.
_NO_EMBEDDED_SERVER_JOBS = frozenset(
    {
        "deploy-pages.yml:verify-legal",
        "deploy-pages.yml:verify-blog",
    }
)

# `tests/_embedded.py::_carve_out_opted_in` accepts these spellings (the #4097
# contract); a workflow that opts in with any of them runs URI-less embedded.
# Read from the YAML, not an indent scan: workflows set it at STEP level
# (`ci.yml::flip-gate`) AND at WORKFLOW level (`perf-lane.yml:68`,
# `evals-on-demand.yml:110`), where every job inherits it. An indent scan sees
# neither the inheritance nor the jobs underneath it — it reads the workflow
# `env:` block and the `on:` triggers as if they were JOBS.
_CARVE_OUT = "TORTOISE_TEST_CARVE_OUT"


def _truthy(value: object) -> bool:
    """The `is_truthy` spellings `tests/_embedded.py` accepts (#4097).

    Kept local rather than imported from `tests._embedded`: that module starts
    the redislite hygiene machinery on import, and this pin must stay a pure
    text/reasoning test.
    """
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _opts_in(value: object) -> bool:
    """Whether a `TORTOISE_TEST_CARVE_OUT` value says "URI-less embedded".

    TWO spellings count, because both are in the tree:

    * a literal truthy spelling — `_truthy` above, which is how `ci.yml`,
      `deploy-pages.yml`, `evals-on-demand.yml` and `perf-lane.yml` set it;
    * a non-empty EXPRESSION — `TORTOISE_TEST_CARVE_OUT: ${{ env.CARVE_OUT }}`,
      which is how `python-ci.yml`'s two matrix legs set it at step scope
      (`:1542` in `test`, `:2380` in `test-slow`): the variable is exported at
      step scope and is EMPTY on the docker lane, so the expression is all a text
      scan ever sees. (The JOIN-WIDEST embedded jobs are `test` and
      `evals-on-demand.yml::evals`, and `evals` opts in with a literal — so this
      is the form used by the python-ci matrix, not the form the widest uses.)

    An expression is admitted DELIBERATELY, and the direction is fail-closed: a
    job that sets one MAY run URI-less, so it joins the population whose cap
    must clear the age arm. Admission can only ADD jobs to the `uncapped` and
    `widest` assertions — never excuse one from them — so "maybe opted in" is
    strictly safer here than "no". (Review @ `1e9b96be5`, ISSUE 2: with
    `_truthy` alone those two jobs were admitted only through the
    reaper-invocation half, and a future expression-only job would have been
    admitted by nothing — invisible to `uncapped == []`, which is the direction
    that lets the proof go false silently.)
    """
    text = str(value).strip()
    return True if "${{" in text else _truthy(text)


def _workflow_files(workflows_dir: Path | None = None) -> list[Path]:
    """Every workflow file — BOTH `.yml` and `.yaml`.

    GitHub accepts either extension (workflow syntax: "Workflow files use YAML
    syntax, and must have either a `.yml` or `.yaml` file extension"), so a
    `.yml`-only glob makes every "EVERY workflow" claim in this file quietly
    false for the other half. This repo has no `.yaml` workflow today, which is
    exactly why the `.yaml` half needs its own test — the same trap, and the
    same remedy, as `tests/test_ci_selection.py::_workflow_files`. Handing every
    scan below through this one function is what makes dropping an extension
    visible instead of silent. `workflows_dir` is injectable so the extension
    pin can point it at a fixture without touching the real workflows.
    """
    wf_dir = workflows_dir if workflows_dir is not None else REPO_ROOT / ".github" / "workflows"
    return sorted({*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")})


def _job_strings(node: object) -> list[str]:
    """Every scalar under a job, so `run:` bodies are searched too."""
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (str(k), *_job_strings(v))]
    if isinstance(node, list):
        return [s for item in node for s in _job_strings(item)]
    return [] if node is None else [str(node)]


def _embedded_lane_job_outer_timeouts(
    workflows_dir: Path | None = None,
) -> dict[str, int | None]:
    """Job-level `timeout-minutes` for every job that can OWN a redislite server.

    Keyed `<workflow>:<job>`. GitHub cancels a job at its OWN `timeout-minutes`
    (workflow syntax: "the maximum number of minutes to let a job run before
    GitHub automatically cancels it", default 360), so that cap — not any inner
    pytest watchdog — bounds how long a live leg's redislite server can possibly
    exist.

    A job is admitted on either of the two signals this repo's CI uses to own
    one: it invokes this reaper, or it opts into the URI-less embedded lane — at
    workflow `env:` level (inherited by every job), at job level, or at step
    level, and in either spelling `_opts_in` accepts (a truthy literal, or a
    non-empty `${{ … }}` expression). The marker is the
    carve-out env var rather than the string "pytest" deliberately —
    `ci.yml::flip-gate` runs pytest from inside `verify-cutover`, so a `pytest`
    scan cannot see the exact job this population was widened for. The residual
    (a URI-set job whose suite constructs embedded anyway, through the
    `tests/_embedded.py` `TEST_NO_REDIRECT_STEMS` registry) is recorded in the
    module's derivation block, not silently absorbed: the reaper-invoking half
    covers today's such jobs, and every one of them is capped below the arm.
    """
    found: dict[str, int | None] = {}
    for path in _workflow_files(workflows_dir):
        workflow = yaml.safe_load(path.read_text()) or {}
        workflow_env = workflow.get("env") or {}
        for name, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, dict):  # e.g. `jobs: <id>:` with a null body
                continue
            key = f"{path.name}:{name}"
            if key in _NO_EMBEDDED_SERVER_JOBS:
                continue
            strings = _job_strings(job)
            invokes_reaper = any("reap-redislite-orphans.py" in s for s in strings)
            envs = [workflow_env, job.get("env")]
            envs.extend(
                step.get("env") for step in job.get("steps") or [] if isinstance(step, dict)
            )
            opted_in = any(_opts_in((env or {}).get(_CARVE_OUT)) for env in envs)
            if not (invokes_reaper or opted_in):
                continue
            cap = job.get("timeout-minutes")
            found[key] = None if cap is None else int(cap)
    return found


def test_the_age_arm_clears_every_embedded_lane_job_outer_timeout(reaper):
    """NON-VACUOUS, twice over.

    On the pre-fix arm (`DEFAULT_MAX_AGE_MINUTES = 45`) this reds: 45 clears
    neither `python-ci.yml::test`'s 90-minute outer cancel bound nor the 55-min
    watchdog ceiling the sibling pin covers.

    On the pre-fix POPULATION (`jobs that invoke the reaper` only) it also reds,
    which is the #10327 review's ISSUE 1: `ci.yml::flip-gate` carries no
    job-level `timeout-minutes`, so it inherits GitHub's 360-minute default and
    no age bound can prove ownership of a server it still owns. Widening the
    admission rule is exactly what makes that case visible instead of keeping
    both assertions green while the proof is false for that job.
    """
    caps = _embedded_lane_job_outer_timeouts()

    expected = {f"python-ci.yml:{leg}" for leg in EMBEDDED_LEGS}
    expected.add("post-merge-validation.yml:validate")
    # The carve-out half of the population: named so a job that loses its
    # discovery (a moved script, a renamed env var) reds instead of quietly
    # narrowing the proof. `flip-gate` is the one ISSUE 1 was filed for.
    expected.update(
        {
            "ci.yml:flip-gate",
            "python-ci.yml:test-carve-out",
            "python-ci.yml:test-d14-hosted-api",
            "evals-on-demand.yml:evals",
            "perf-lane.yml:perf-lane",
        }
    )
    assert expected <= set(caps), (
        "a job that can own an embedded redislite server was not discovered — this "
        f"pin would otherwise go vacuous: missing {sorted(expected - set(caps))}"
    )

    uncapped = sorted(job for job, cap in caps.items() if cap is None)
    assert uncapped == [], (
        "an embedded-lane job carries no job-level `timeout-minutes`, so it inherits "
        "GitHub's 360-minute default and no age bound can prove ownership: " + ", ".join(uncapped)
    )

    widest = max(c for c in caps.values() if c is not None)
    assert widest == reaper.WIDEST_EMBEDDED_JOB_TIMEOUT_MINUTES, (
        f"`WIDEST_EMBEDDED_JOB_TIMEOUT_MINUTES` mirrors a stale measurement: it says "
        f"{reaper.WIDEST_EMBEDDED_JOB_TIMEOUT_MINUTES} min, the workflows now cap the "
        f"widest embedded-lane job at {widest} min "
        f"({sorted(j for j, c in caps.items() if c == widest)}) — re-derive the "
        "constant and its comment instead of trusting the stored number"
    )
    assert widest < reaper.DEFAULT_MAX_AGE_MINUTES, (
        f"the age arm ({reaper.DEFAULT_MAX_AGE_MINUTES} min) does not clear the widest "
        f"job cancel bound ({widest} min): a leg can still be RUNNING with a server "
        "that old, so the SIGTERM is the #3653 live-peer kill, not a proof"
    )


def test_the_embedded_lane_scan_reads_every_workflow_extension(tmp_path):
    """NON-VACUOUS: reds if either half of the glob goes missing.

    GitHub accepts `.yml` AND `.yaml`, so the population's claim to cover EVERY
    workflow is only true while the scan keeps both. The repo has no `.yaml`
    workflow today — which is precisely why this hole is invisible in the real
    tree, and why the fixture below carries one embedded job per extension and
    points the scan at it. `yaml-leg.yaml` is deliberately UNCAPPED: that is the
    state the age arm cannot prove ownership under (GitHub's 360-minute
    default), so a scan that missed it would keep reporting `uncapped == []`
    while a real workflow sat in the gap — the drift class of #10327 review
    round 2, one file-extension wide.
    """
    (tmp_path / "yml-leg.yml").write_text(
        "jobs:\n"
        "  yml-lane:\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 20\n"
        "    steps:\n"
        "      - run: .github/scripts/reap-redislite-orphans.py --apply\n"
    )
    (tmp_path / "yaml-leg.yaml").write_text(
        "env:\n"
        "  TORTOISE_TEST_CARVE_OUT: '1'\n"
        "jobs:\n"
        "  yaml-lane:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: pytest -q\n"
    )

    found = _embedded_lane_job_outer_timeouts(tmp_path)
    assert found == {"yml-leg.yml:yml-lane": 20, "yaml-leg.yaml:yaml-lane": None}, (
        "the population scan did not read every workflow extension, so a `.yaml` "
        "workflow that can own an embedded redislite server is invisible to the "
        f"age-arm proof: {found}"
    )


def test_an_expression_valued_carve_out_is_admitted(tmp_path):
    """NON-VACUOUS: reds if `_opts_in` falls back to literal spellings only.

    `python-ci.yml`'s two matrix legs opt in the way a text scan cannot spell:
    `TORTOISE_TEST_CARVE_OUT: ${{ env.CARVE_OUT }}` (`python-ci.yml:1542` in
    `test`, `:2380` in `test-slow`). They are admitted today by the
    reaper-invocation half, so this hole is INVISIBLE on the real tree — which is
    exactly why it needs its own fixture: a job that sets the expression and does
    not run the reaper would otherwise be admitted by nothing (or by a
    `TEST_NO_REDIRECT_STEMS` suite, which the scan cannot see either), and if it
    were uncapped the age-arm proof would go false while `uncapped == []` kept
    reporting green (review @ `1e9b96be5`, ISSUE 2).

    The fixture job is deliberately UNCAPPED and the expected value is `None`:
    admission is what this pins, and `None` is the state the assertions above
    refuse — so reading the expression as "not opted in" drops the job instead of
    mis-reading its cap.
    """
    (tmp_path / "expr-leg.yml").write_text(
        "jobs:\n"
        "  expr-lane:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: pytest -q\n"
        "        env:\n"
        "          TORTOISE_TEST_CARVE_OUT: ${{ env.CARVE_OUT }}\n"
    )

    found = _embedded_lane_job_outer_timeouts(tmp_path)
    assert found == {"expr-leg.yml:expr-lane": None}, (
        "an expression-valued `TORTOISE_TEST_CARVE_OUT` is not admitted, so a job "
        "that opts into the URI-less embedded lane this way — the form both "
        f"widest embedded jobs use — is invisible to the age-arm proof: {found}"
    )


def test_the_age_arm_clears_the_widest_permitted_leg_watchdog(reaper):
    """NON-VACUOUS: this reds on the pre-fix arm (45 < `WATCHDOG_CEILING_MIN`=55).

    `tools/ci_selection.py` derives a `test` leg's pytest watchdog as
    `min(max(ceil(est * WATCHDOG_HEADROOM), WATCHDOG_FLOOR_MIN), WATCHDOG_CEILING_MIN)`
    and emits it per leg, so 55 is a budget the policy PERMITS a live leg to run
    with. An age arm at or below it can fire on a leg that is still inside its own
    sanctioned budget — the case that stayed green before this pin existed.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from tools.ci_selection import WATCHDOG_CEILING_MIN, shard_watchdog_minutes

    assert shard_watchdog_minutes(10 * 60 * 60) == WATCHDOG_CEILING_MIN, (
        "the ceiling is no longer reachable through the producer this pin reasons "
        f"about (got {shard_watchdog_minutes(10 * 60 * 60)}, want {WATCHDOG_CEILING_MIN}); "
        "re-derive the age arm against whatever replaces `shard_watchdog_minutes`"
    )
    assert reaper.DEFAULT_MAX_AGE_MINUTES > WATCHDOG_CEILING_MIN, (
        f"the age arm ({reaper.DEFAULT_MAX_AGE_MINUTES} min) does not clear the widest "
        f"leg watchdog the policy permits ({WATCHDOG_CEILING_MIN} min) — a leg inside "
        "its sanctioned budget can own a server this old"
    )


#: What the derivation block must SAY about the sibling 30-minute dir-litter
#: `find` floor, and the sentence it must no longer say.
_DIR_FLOOR_RISK_PROSE = ("3653", "CAN unlink a running peer's socket directory")
_DIR_FLOOR_REFUTED_PROSE = "no live-process risk"


def _derivation_block() -> str:
    """The comment block that derives the age arm, as shipped in the script."""
    text = SCRIPT.read_text()
    return text[text.index("the age arm: DERIVED") : text.index("WIDEST_EMBEDDED_JOB_TIMEOUT")]


def test_the_derivation_block_names_the_dir_floor_as_open_risk():
    """NON-VACUOUS: reds on the block the pre-fix diff shipped.

    That block stated the 30-minute dir-litter floor is safe because "a directory
    carries no live-process risk, a SIGTERM to a live server does" — the one claim
    the block's OWN arithmetic two paragraphs above disproves. The sibling `find
    ... -mmin +30 -exec rm -rf` deletes by mtime alone, with no liveness check, so
    its 30 minutes sit below both the 55-minute watchdog ceiling and the
    90-minute job cap this arm is derived from, and it can unlink a running
    peer's `mkdtemp` socket directory — which is exactly arm 1's "socket dir
    gone" input, not harmless litter. It is #3653's remaining half (the issue's
    own "Candidates for the deleter" names that `find`). The block is the
    authority a maintainer recomputes the arm from, so a sentence calling that
    floor harmless can get the floor raised — or this arm lowered back toward it
    — on the strength of a claim the same block refutes (#10327 review, ISSUE 1
    at `3d0f0ce06`).
    """
    block = _derivation_block()
    assert _DIR_FLOOR_REFUTED_PROSE not in block, (
        "the derivation block calls the 30-minute dir-litter floor safe again — its "
        "own arithmetic shows that floor is BELOW both the 55-minute watchdog "
        "ceiling and the 90-minute job cap, so it can unlink a live peer's socket "
        "directory; state it as #3653's remaining half instead"
    )
    missing = [marker for marker in _DIR_FLOOR_RISK_PROSE if marker not in block]
    assert missing == [], (
        "the derivation block no longer names what the sibling `find` floor costs "
        f"(missing {missing}) — a maintainer who meets that `find` with no warning "
        "here has only the false reading this pin replaced"
    )


@pytest.mark.parametrize(
    "argv,expected",
    [
        # inline colon form (macOS / redislite config directive)
        (["redis-server", "unixsocket:/tmp/tt_ab/tmpcd/redis.socket"], "/tmp/tt_ab/tmpcd"),
        # long-form flag with the path as the next argv element
        (["redis-server", "--unixsocket", "/tmp/tt_ab/tmpcd/redis.socket"], "/tmp/tt_ab/tmpcd"),
        # space-separated directive (no dashes)
        (["redis-server", "unixsocket", "/tmp/tt_ab/tmpcd/redis.socket"], "/tmp/tt_ab/tmpcd"),
        # config-file form redislite uses on macOS
        (["redis-server", "/tmp/tt_ab/tmpcd/redis.config", "--port", "0"], "/tmp/tt_ab/tmpcd"),
        # no socket at all -> unknown, never a kill
        (["redis-server", "/etc/redis/redis.conf"], None),
        ([], None),
    ],
)
def test_socket_dir_from_argv_covers_every_emitter_shape(reaper, argv, expected):
    assert reaper.socket_dir_from_argv(argv) == expected


# ── the action: dry run signals nothing, apply signals only the reaped rows ──


def test_a_dry_run_never_signals(reaper, tmp_path, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(reaper.os, "kill", lambda pid, sig: killed.append(pid))
    rows = [{"pid": 4242, "socket_dir": str(tmp_path / "gone"), "age_seconds": 1}]
    assert reaper.run(rows, max_age_s=_age_bound_s(reaper), apply=False) == 0
    assert killed == [], "a dry run must never signal"


def test_apply_signals_exactly_the_reapable_rows(reaper, tmp_path, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(reaper.os, "kill", lambda pid, sig: killed.append(pid))
    bound = _age_bound_s(reaper)
    rows = [
        {"pid": 11, "socket_dir": str(tmp_path), "age_seconds": 60},  # live peer
        {"pid": 22, "socket_dir": str(tmp_path / "gone"), "age_seconds": 60},  # litter
        {"pid": 33, "socket_dir": str(tmp_path), "age_seconds": bound + 60},  # aged
    ]
    assert reaper.run(rows, max_age_s=bound, apply=True) == 0
    assert killed == [22, 33], killed


def test_the_cli_reads_its_inventory_from_the_proc_json_seam(tmp_path):
    """End-to-end through `main()`: the seam is what CI-side tests can drive."""
    gone = tmp_path / "gone"
    inventory = tmp_path / "proc.json"
    inventory.write_text(
        json.dumps(
            [
                {"pid": 7, "socket_dir": str(tmp_path), "age_seconds": 60},
                {"pid": 8, "socket_dir": str(gone), "age_seconds": 60},
            ]
        )
    )
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--proc-json", str(inventory)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert re.search(r"^KEEP pid=7\b", out.stdout, re.M), out.stdout
    assert re.search(r"^REAP pid=8\b", out.stdout, re.M), out.stdout
    assert "dry-run" in out.stdout


def test_an_unreadable_inventory_is_exit_2_not_a_silent_zero(reaper, monkeypatch):
    """NON-VACUOUS: this reds on the pre-fix code, which returned `[]` here.

    The module documents exactly one failure signal — exit 2, "the inventory could not
    be read at all" — and it could not fire on the host path: `_iter_proc_inventory()`
    returned an empty list both when it had looked and found no redis-server and when
    it had never been able to look at all, so `run()` printed `0 candidate(s), 0
    reaped` and exited 0. "Measured the host, no litter" and "never saw the host" are
    different facts about a cleanup step, and only one of them is safe to call success
    (PR #10254 review, round 13).
    """
    # no /proc (the `ps` branch), and `ps` itself unavailable
    monkeypatch.setattr(reaper.os.path, "isdir", lambda _p: False)

    def _no_ps(*_a, **_k):
        raise OSError("ps: not found")

    monkeypatch.setattr(subprocess, "run", _no_ps)
    assert reaper._iter_proc_inventory() is None, (
        "an unreadable inventory must be None (exit 2), never an empty list — the "
        "empty list is what let a blind run report success"
    )
    assert reaper.main([]) == 2, "the documented failure signal must reach the exit code"


def test_an_unlistable_proc_is_exit_2_too(reaper, monkeypatch):
    """`/proc` present but unlistable is the same "never saw the host" case."""
    monkeypatch.setattr(reaper.os.path, "isdir", lambda _p: True)

    def _no_listdir(_p):
        raise OSError("Permission denied")

    monkeypatch.setattr(reaper.os, "listdir", _no_listdir)
    assert reaper._iter_proc_inventory() is None


def test_a_ps_that_runs_and_fails_is_exit_2_too(reaper, monkeypatch):
    """NON-VACUOUS: this reds on the pre-fix `ps` branch, which read stdout only.

    `ps` EXISTS and EXITS NON-ZERO — denied, a broken binary, an unsupported
    `-eo` — is the third shape of "never saw the host", and it was the one arm
    the round-13 fix left open: the branch passed `check=False` and read
    `.stdout` without ever inspecting `returncode`, so a failing `ps` produced
    `rows == []`, `run()` printed `0 candidate(s), 0 reaped`, and `main()` exited
    **0** — the exact `[]`-for-both-cases confusion that the None/exit-2
    distinction was introduced to remove (PR #10254 review, P3).
    """
    monkeypatch.setattr(reaper.os.path, "isdir", lambda _p: False)

    def _ps_fails(*_a, **_k):
        return subprocess.CompletedProcess(
            args=["ps", "-eo", "pid=,args="],
            returncode=1,
            stdout="",
            stderr="ps: illegal option -- e",
        )

    monkeypatch.setattr(subprocess, "run", _ps_fails)
    assert reaper._iter_proc_inventory() is None, (
        "a `ps` that ran and FAILED measured nothing, so the inventory must be "
        "None (exit 2) — never an empty list, which reports as 'measured, no litter'"
    )
    assert reaper.main([]) == 2, "the documented failure signal must reach the exit code"


def test_the_ps_branch_identifies_by_comm_and_parses_every_shape(reaper, monkeypatch):
    """The non-`/proc` branch (macOS) has the SAME identity rule and the same census.

    No CI job runs on macOS, so this branch can only be pinned at the seam: the
    real `/proc` probe is forced off and `ps` is answered with a synthetic
    snapshot. What is pinned here is exactly what the branch's own parsing decides
    — a wrapper's `comm` is the identity, a plain TCP server is an excluded census
    member, a process with no args is KEPT and accounted for, and (for `ps`
    snapshots whose `comm` field contains a space, which shifts the argv boundary)
    nothing crashes and no gap is invented.
    """
    snapshot = (
        "\n".join(
            [
                # 1: wrapper-exec'd server — argv[0] is the WRAPPER, so only
                # the kernel's `comm` field can identify it
                f"101 redis-server /opt/gh-runner/bin/python3 {REDIS_BIN} "
                "unixsocket:/tmp/tt_ab/tmpcd/redis.socket",
                # 2: plain TCP server — excluded, counted
                "102 redis-server redis-server --port 6379",
                # 3: a server with no args at all — kept and accounted for
                "103 redis-server",
                # 4: not a server, and its argv merely NAMES the binary
                f"104 python3 /usr/bin/python3 -m pytest {REDIS_BIN}",
                # 5: a `setproctitle` server — `ps` prints its `comm` with a space
                # in it, so the argv boundary shifts by one token; must not crash,
                # must stay a census member, and must not invent a gap
                "105 redis-server *: redis-server *:6379",
            ]
        )
        + "\n"
    )

    real_isdir = os.path.isdir
    monkeypatch.setattr(
        reaper.os.path,
        "isdir",
        lambda p: False if str(p) == "/proc/self" else real_isdir(p),
    )

    def _fake_ps(*_a, **_k):
        return subprocess.CompletedProcess(
            args=["ps", "-eo", "pid=,comm=,args="], returncode=0, stdout=snapshot, stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_ps)
    monkeypatch.setattr(reaper, "_process_age", lambda _pid: 60.0)

    inventory = reaper._iter_proc_inventory()
    assert inventory is not None, "a `ps` that answered must never read as unmeasured"
    assert inventory.source == "ps"
    rows = {row["pid"]: row for row in inventory.rows}
    assert set(rows) == {101, 103}, sorted(rows)
    assert rows[101]["identified_by"] == "comm"
    assert rows[101]["socket_dir"] == "/tmp/tt_ab/tmpcd"
    assert rows[103]["socket_dir"] is None, "no argv is not authority to kill"
    assert inventory.census == 4, (
        "101, 102, 103 and 105 are named redis-server by the kernel (105 through "
        "the first field of its space-containing `comm`, even though the shifted "
        "argv boundary loses it as a candidate); 104 is not"
    )
    assert inventory.unaccounted == 0, (
        "every census member must be a row or a stated exclusion — a non-zero here "
        "on a well-formed snapshot would be a false census gap"
    )


def test_a_measured_empty_host_is_still_exit_0(reaper, tmp_path):
    """The other direction: an inventory that WAS read and held no litter is exit 0."""
    inventory = tmp_path / "proc.json"
    inventory.write_text("[]")
    assert reaper.main(["--proc-json", str(inventory)]) == 0
    assert reaper.run([], max_age_s=_age_bound_s(reaper), apply=True) == 0


# ── #10502: identity is the kernel's name, never the wrapper's argv[0] ──────
#
# The measured defect: a wrapper-exec'd server has `argv[0]` pointing at the
# WRAPPER, so `_IS_REDIS.search(argv[0])` matched no row, `_iter_proc_inventory()`
# returned an empty list, and the tool printed `applied: 0 candidate(s), 0 reaped`
# and exited 0 on a cell carrying 1,555 `redis-server` processes, 1,537 of them
# orphaned. Zero `REAP`/`KEEP` lines is the tell: it classified nothing at all.
#
# The scan is driven through its three `/proc` seams (`_read_proc_cmdline`,
# `_read_proc_name`, `_read_proc_exe_name`) so the exact shapes are pinned
# deterministically on every platform; the shapes themselves are the ones MEASURED
# on the runner, not invented here.

#: `<bin>/redis-server` — the binary the runner actually executes, at a
#: `redislite` site-packages path, so a wrapper-shaped argv is realistic.
REDIS_BIN = "/opt/gh-runner/_work/_tool/Python/3.12.15/x64/lib/python3.12/site-packages/redislite/bin/redis-server"


def _seed_proc_scan(
    reaper,
    monkeypatch,
    procs: dict[int, tuple[list[str] | None, str | None, str | None]],
    age_seconds: float = 60.0,
) -> None:
    """Seed `_iter_proc_inventory()` with `{pid: (argv, comm, exe)}`.

    `argv` is what `/proc/<pid>/cmdline` yields — the kernel hands back exactly
    the argv the process was exec'd with, which for a wrapper-exec'd server is
    NOT the server's binary. `comm`/`exe` are the kernel's own answers.
    """
    real_isdir = os.path.isdir
    # Only `/proc` is faked: `classify()` must still see the REAL filesystem, or
    # a socket-dir arm would silently pass here and fail in CI.
    monkeypatch.setattr(
        reaper.os.path, "isdir", lambda p: True if str(p) == "/proc/self" else real_isdir(p)
    )
    monkeypatch.setattr(reaper.os, "listdir", lambda _p: [str(pid) for pid in procs] + ["self"])
    monkeypatch.setattr(reaper, "_read_proc_cmdline", lambda pid: procs[pid][0])
    monkeypatch.setattr(reaper, "_read_proc_name", lambda pid: procs[pid][1])
    monkeypatch.setattr(reaper, "_read_proc_exe_name", lambda pid: procs[pid][2])
    monkeypatch.setattr(reaper, "_process_age", lambda _pid: age_seconds)


@pytest.mark.parametrize(
    "argv,comm,exe,expected_socket_dir,identified_by,census",
    [
        # THE MEASURED SHAPE (issue #10502, `pi-runner-x64-2`): ONE argv element
        # carrying the whole command line, so `redis-server` is not at end-of-
        # string and the pre-fix `_IS_REDIS` filter matches nothing. Pre-fix this
        # row does not exist at all.
        (
            [f"{REDIS_BIN} unixsocket:/tmp/tt_pcfpx8kg/tmpjo77yr86/redis.socket"],
            "redis-server",
            REDIS_BIN,
            "/tmp/tt_pcfpx8kg/tmpjo77yr86",
            # Post-fix identity is `argv[0]` — because the ONE element has been
            # tokenised, so `argv[0]` IS the binary. Before the fix the element
            # was never split, and no row existed at all.
            "argv[0]",
            1,
        ),
        # The wrapper-shape the brief names: `argv[0]` IS the wrapper's path.
        (
            [
                "/opt/gh-runner/_work/_tool/Python/3.12.15/x64/bin/python3",
                REDIS_BIN,
                "unixsocket:/tmp/tt_ab/tmpcd/redis.socket",
            ],
            "redis-server",
            None,
            "/tmp/tt_ab/tmpcd",
            "comm",
            1,
        ),
        # Comm renamed away (`prctl`), binary on disk still the server.
        (
            ["redis-server@wrapper", "unixsocket:/tmp/tt_ab/tmpcd/redis.socket"],
            "redis-server:6379",
            REDIS_BIN,
            "/tmp/tt_ab/tmpcd",
            "comm",
            1,
        ),
        # The fast path is UNCHANGED: a directly-started server still resolves
        # from argv[0] without a /proc/<pid>/comm read.
        (
            [REDIS_BIN, "unixsocket:/tmp/tt_ab/tmpcd/redis.socket"],
            None,
            None,
            "/tmp/tt_ab/tmpcd",
            "argv[0]",
            # The census is the KERNEL-Named population (`comm`), read before any
            # filter. This process is identified from argv alone, so it is a row
            # that exists outside the census arithmetic — by design.
            0,
        ),
        # `comm` renamed AND unreadable → the resolved exe is the last authority.
        (
            ["/usr/bin/env", "unixsocket:/tmp/tt_ab/tmpcd/redis.socket"],
            "some-renamed-name",
            REDIS_BIN,
            "/tmp/tt_ab/tmpcd",
            "exe",
            0,
        ),
    ],
    # ⛔ EXPLICIT ids, not the auto-generated ones (PR #14182 CI red). The auto-id
    # for a row embeds the PARAM VALUES, so it embedded `REDIS_BIN` — and what
    # pytest renders for a long string parameter is a PYTEST-VERSION decision, not
    # a fact about this repo: pytest 9.1.1 printed the whole path, pytest 9.2.0
    # (what CI installs) prints `exe{idx}`. That silently changed 3 nodeids between
    # the lane's `--collect-only` manifest and the run's junit, and
    # `config/ci-expected-nodeids/platform-gated.txt` is a FROZEN set of nodeids —
    # so the mismatch reads as "these tests never ran" (which is exactly what the
    # #4215 guard exists to catch, and what it caught here). Literal ids make the
    # nodeids a property of THIS file, stable across pytest upgrades.
    ids=[
        "argc1-measured",  # the measured wrapper shape: ONE argv element
        "wrapper-comm",  # argv[0] is the wrapper; only `comm` identifies
        "renamed-comm",  # `comm` renamed to a setproctitle form
        "direct-argv0",  # fast path unchanged: argv[0] IS the binary
        "renamed-exe",  # `comm` renamed and unreadable -> resolved `exe`
    ],
)
def test_a_wrapper_execd_server_is_enumerated(
    reaper, monkeypatch, argv, comm, exe, expected_socket_dir, identified_by, census
):
    """THE #10502 REGRESSION: every one of these counts ZERO on the pre-fix code.

    Non-vacuous in the strict sense: with `_IS_REDIS`-on-`argv[0]` for identity,
    cases 1-3 and 5 are dropped before `classify()` is ever reached, so the
    inventory is empty and `main()` prints `0 candidate(s), 0 reaped` and exits 0
    — the exact output that read as a clean host over 1,537 orphans.
    """
    _seed_proc_scan(reaper, monkeypatch, {4242: (argv, comm, exe)})
    inventory = reaper._iter_proc_inventory()
    assert inventory is not None, "a readable host must never be None"
    rows = {row["pid"]: row for row in inventory.rows}
    assert 4242 in rows, (
        "a server whose argv[0] is not the binary must still be enumerated — "
        f"identity {identified_by!r} is the kernel's, not the wrapper's"
    )
    assert rows[4242]["socket_dir"] == expected_socket_dir
    assert rows[4242]["identified_by"] == identified_by
    assert inventory.census == census, (
        "the census counts processes the KERNEL names redis-server (comm), "
        "independently of how this row was identified"
    )


def test_the_seeded_wrapper_row_reaches_the_decision_table(reaper, monkeypatch, capsys):
    """End-to-end: enumeration → classify → a printed REAP line, not a silent zero."""
    # `socket_dir` names a directory that does not exist, i.e. the #1005 residue.
    _seed_proc_scan(
        reaper,
        monkeypatch,
        {
            4242: (
                [f"{REDIS_BIN} unixsocket:/nonexistent-tt-10502/redis.socket"],
                "redis-server",
                None,
            )
        },
    )
    assert reaper.main([]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^REAP pid=4242\b", out, re.M), out
    assert "0 candidate(s)" not in out, (
        "the pre-fix output — and the one that read as a clean host over 1,537 "
        f"orphans — must be impossible here:\n{out}"
    )
    assert "1 candidate(s)" in out, out


@pytest.mark.parametrize(
    "name,is_server",
    [
        # Accepted: the kernel name of a redis server, in the forms redis and the
        # kernel actually produce — including the `setproctitle` title and the
        # 15-byte `comm` truncation of it.
        ("redis-server", True),
        ("redis-server *:6379", True),
        ("redis-server *:", True),
        ("redis-server:6379", True),
        # Refused: other Redis binaries, and names that merely START the same.
        # A widening here would make the reaper classify `redis-cli` /
        # `redis-sentinel` as servers and hand them to the kill decision.
        ("redis-cli", False),
        ("redis-sentinel", False),
        ("redis-serverish", False),
        ("redis-server-x", False),
        ("redis-serve", False),
        ("not-redis-server", False),
        ("Redis-Server", False),
        ("", False),
    ],
)
def test_the_kernel_name_predicate_accepts_only_redis_server(reaper, name, is_server):
    """A widening here is a WRONG-KILL class, so it is pinned in both directions.

    Identity is now taken from `comm`/`exe`, which means this predicate decides
    which processes reach `classify()`. Peer review flagged that only the positive
    shapes were pinned: a future `^redis` would start feeding `redis-cli` and
    `redis-sentinel` to the kill decision with no test going red. Same class as
    the pre-fix bug — an identity predicate that admits the wrong population —
    just in the other direction.
    """
    assert bool(reaper._REDIS_KERNEL_NAME.search(name)) is is_server, name


def test_the_pre_fix_identity_filter_is_what_dropped_the_measured_shape(reaper):
    """The root cause, pinned as a FACT about the two predicates.

    The measured `argv[0]` on the runner was ONE element holding the whole
    command line, so the pre-fix identity filter could not match it — not
    "classified and declined", but never matched. If a future edit loosens
    `_IS_REDIS` in a way that would silently change this, the mechanism the fix
    describes stops being true and this reds.
    """
    measured_argv0 = f"{REDIS_BIN} unixsocket:/tmp/tt_pcfpx8kg/tmpjo77yr86/redis.socket"
    assert reaper._IS_REDIS.search(measured_argv0) is None, (
        "the pre-fix filter matched this element — then #10502's root cause was "
        "something else and this fix describes it wrongly"
    )
    assert reaper._split_one_arg_line([measured_argv0])[0] == REDIS_BIN
    assert reaper.socket_dir_from_argv([measured_argv0]) == "/tmp/tt_pcfpx8kg/tmpjo77yr86"


def test_a_process_that_only_NAMES_the_binary_is_not_a_server(reaper, monkeypatch):
    """Identity is about the EXECUTED binary, never a string in someone's args.

    A client, a test helper, or a launcher whose CHILD is the server is not the
    server. Reaping one on "its argv mentions redis-server" would be the
    live-peer kill #3653 removed, so `_redis_identity` deliberately does not scan
    argv for the name — this pins that narrowing.
    """
    _seed_proc_scan(
        reaper,
        monkeypatch,
        {
            900: (
                ["/usr/bin/python3", "-m", "pytest", REDIS_BIN],
                "python3",
                "/usr/bin/python3.12",
            ),
            901: (
                ["/bin/sh", "-c", f"{REDIS_BIN} unixsocket:/tmp/tt_ab/tmpcd/redis.socket"],
                "sh",
                "/usr/bin/dash",
            ),
        },
    )
    inventory = reaper._iter_proc_inventory()
    assert inventory is not None
    assert inventory.rows == [], inventory.rows
    assert inventory.census == 0, "neither process IS a redis server, so neither is census"


def test_a_plain_tcp_redis_is_excluded_with_a_stated_reason(reaper, monkeypatch, capsys):
    """A measured zero of REDISLITE litter: a redis server with no unix socket.

    This is the case a naive "the census is non-zero, so refuse to call it a
    measured zero" would turn into a false red on any host that runs a plain
    TCP `redis-server` for something else. It is EXCLUDED and COUNTED, and the
    run is still a measured zero — exit 0 with the census line saying so.
    """
    _seed_proc_scan(
        reaper, monkeypatch, {777: (["redis-server", "--port", "6379"], "redis-server", None)}
    )
    assert reaper.main([]) == 0
    out = capsys.readouterr().out
    assert "census: 1 redis-server process(es)" in out, out
    assert "0 accounted for as candidate(s); 1 excluded: no unix socket in argv" in out, out
    assert "0 not accounted for" in out, out
    assert "0 candidate(s)" in out, out


def test_a_census_the_inventory_cannot_account_for_is_exit_3(reaper, monkeypatch, capsys):
    """NON-VACUOUS: the pre-fix run printed `0 candidate(s)` and exited 0 here.

    `census=1555, accounted=0, excluded=0` is the measured shape of #10502: 1,555
    processes named `redis-server`, none of them enumerated. A parse/filter gap
    must say so and exit non-zero; only a genuine measured zero may exit 0.
    """
    inventory = reaper.ProcInventory(
        rows=[], census=1555, census_accounted=0, census_excluded=0, source="/proc"
    )
    assert inventory.unaccounted == 1555
    monkeypatch.setattr(reaper, "_iter_proc_inventory", lambda: inventory)
    assert reaper.main([]) == 3, "a gap must not exit 0 — that is the whole defect"
    err = capsys.readouterr().err
    assert "census gap: 1555" in err, err
    assert "NOT a measured zero" in err, err


def test_the_census_fires_when_the_identity_gate_matches_nothing(reaper, monkeypatch, capsys):
    """THE GUARD IS NOT INERT: a gate that matches nothing now REDS.

    This is the shape of #10502 itself — the identity gate drops every server —
    and it is the reason the census is read from the kernel's name BEFORE any
    filter, rather than counted from the rows: a census derived from the gate
    would collapse to 0 with it and report the same clean-looking zero. The
    servers here are real `comm` census members, so an identity gate that returns
    nothing leaves them unaccounted.
    """
    _seed_proc_scan(
        reaper,
        monkeypatch,
        {
            4242: ([REDIS_BIN, "unixsocket:/tmp/tt_ab/tmpcd/redis.socket"], "redis-server", None),
            4243: ([f"{REDIS_BIN} unixsocket:/tmp/tt_ab/tmpcd/redis.socket"], "redis-server", None),
        },
    )
    monkeypatch.setattr(reaper, "_redis_identity", lambda _pid, _argv, _comm=None: None)
    assert reaper.main([]) == 3, (
        "a gate that matches nothing must NOT report a measured zero — that is "
        "exactly the output that read as a clean host over 1,537 orphans"
    )
    out = capsys.readouterr()
    assert "census gap: 2" in out.err, (out.out, out.err)
    assert "census: 2 redis-server process(es)" in out.out, out.out


def test_an_accounted_census_is_not_a_gap(reaper):
    """The other direction, so the guard cannot fire on a healthy measurement."""
    assert (
        reaper.ProcInventory(
            rows=[{"pid": 1}], census=1, census_accounted=1, census_excluded=0, source="/proc"
        ).unaccounted
        == 0
    )
    assert (
        reaper.ProcInventory(
            rows=[], census=2, census_accounted=0, census_excluded=2, source="/proc"
        ).unaccounted
        == 0
    )
    assert (
        reaper.ProcInventory(
            rows=[], census=0, census_accounted=0, census_excluded=0, source="/proc"
        ).unaccounted
        == 0
    )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux-only")
def test_a_non_utf8_process_name_cannot_kill_the_run(reaper):
    """Peer-review finding: any process can set a non-UTF-8 name, and that must
    not abort the scan.

    `_read_proc_name()` read `/proc/<pid>/comm` in text mode, so a name carrying
    a non-UTF-8 byte (`prctl(PR_SET_NAME)`, unprivileged) raised
    `UnicodeDecodeError` — a ValueError, not an OSError — out of
    `_iter_proc_inventory()`: exit 1, no census line, nothing reaped, and (under
    the shipped `... --apply || true` call sites) a GREEN cleanup step that did
    nothing. Reading it with `errors="replace"` makes an undecodable name simply
    "not redis-server".
    """
    setter = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import ctypes,time; "
            "libc=ctypes.CDLL(None, use_errno=True); "
            "libc.prctl(15, ctypes.c_char_p(b'\\xff\\xfe-bad'), 0, 0, 0); "
            "time.sleep(60)",
        ]
    )
    try:
        raw = b""
        for _ in range(200):
            try:
                with open(f"/proc/{setter.pid}/comm", "rb") as fh:
                    raw = fh.read()
            except OSError:
                raw = b""
            if raw.startswith(b"\xff\xfe"):
                break
            time.sleep(0.02)
        assert raw.startswith(b"\xff\xfe"), (
            "the fixture never carried a non-UTF-8 name, so this test proves nothing"
        )
        # The reader must return a string instead of raising...
        assert isinstance(reaper._read_proc_name(setter.pid), str)
        # ...and the whole run must survive it: a real measurement (exit 0, or 3
        # if this host's census genuinely cannot be accounted for), never a crash.
        assert reaper.main([]) in (0, 3)
    finally:
        setter.kill()
        setter.wait(timeout=30)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux-only")
def test_a_real_argv0_wrapper_process_is_enumerated(reaper, tmp_path):
    """The kernel's own answer, no seams: a live `argc == 1` server shape.

    A real process is exec'd with ONE argv element carrying the whole command
    line (the shape measured on the runner) and a binary file named
    `redis-server`, so `/proc/<pid>/comm` is `redis-server` while
    `/proc/<pid>/cmdline` holds a string that does not end in it. The pre-fix
    filter drops this process; the inventory must list it.
    """
    binary = tmp_path / "redis-server"
    # ⛔ NOT a renamed copy of `cat` (#14335). On a runner whose coreutils is the
    # uutils MULTICALL binary (`ubuntu-26.04`: `/usr/bin/cat` is a hardlink into
    # `/usr/bin/coreutils`), a copy refuses to run — the multicall dispatches on the
    # basename of `argv[0]`, prints `coreutils: unknown program 'redis-server'` and
    # EXITS. `comm` survives the zombie, so the test then graded a corpse: an empty
    # `/proc/<pid>/cmdline` made `socket_dir` None, and `test-carve-out` false-redded on
    # every PR that ran this shard. CPython does not dispatch on `argv[0]`: exec'd with
    # `argc == 1` and a pipe on stdin it reads a program from stdin and BLOCKS, which is
    # exactly the live wrapper shape this test needs.
    binary.write_bytes(Path(sys.executable).read_bytes())
    binary.chmod(0o755)
    # ⛔ THE COPY NEEDS ITS STDLIB (#14335 follow-up, MEASURED on the `uv` standalone build):
    # a RELOCATABLE interpreter (`python-build-standalone` — what `uv` installs and what
    # `actions/setup-python` provisions) resolves its prefix from the EXECUTABLE'S OWN PATH,
    # so a copy in `tmp_path` finds no `lib/pythonX.Y` and exits immediately:
    #     cp <uv-python>/bin/python3.9 /tmp/x/redis-server; (sleep 20) | timeout 3 /tmp/x/redis-server
    #     -> rc=1   (and, with stderr shown: "Could not find platform independent libraries")
    #     -> rc=124 with `PYTHONHOME=<base_prefix>`   (blocks on stdin, which is the shape
    #        this test needs)
    # A Debian-style interpreter has a compiled-in `/usr` fallback and hides the defect, which
    # is why this only reds on SOME runners — the exact "reds on every PR that runs this
    # shard" class #14335 was filed to remove. `PYTHONHOME` is the portable override and is
    # derived from the interpreter we are already running under, so it names the prefix the
    # copied bytes would have used in place.
    child_env = {**os.environ, "PYTHONHOME": sys.base_prefix}
    gone = tmp_path / "gone-socket-dir"
    line = f"{binary} unixsocket:{gone}/redis.socket"
    child = subprocess.Popen(
        [line], executable=str(binary), stdin=subprocess.PIPE, env=child_env,
        stdout=subprocess.DEVNULL,
    )
    try:
        seen = False
        for _ in range(200):
            try:
                with open(f"/proc/{child.pid}/comm") as fh:
                    seen = fh.read().strip() == "redis-server"
            except OSError:
                seen = False
            if seen:
                break
            time.sleep(0.02)
        assert seen, "the fixture process never came up with comm == redis-server"
        # ⛔ AND IT MUST STAY LIVE THROUGH A SETTLE (#14335). `comm` survives a zombie,
        # and an instantly-exiting stand-in can be observed BEFORE it dies — so a single
        # `poll()` races, and the test then fails as `assert None == '/tmp/…'`, a message
        # that names nothing. A binary that cannot hold this shape cannot exercise it.
        settle_deadline = time.monotonic() + 1.0
        while child.poll() is None and time.monotonic() < settle_deadline:
            time.sleep(0.05)
        assert child.poll() is None, (
            "the fixture child exited instead of blocking on stdin "
            f"(returncode={child.returncode}) — the stand-in binary did not reach its "
            "input loop, so this test would grade a zombie rather than a live "
            "wrapper-exec'd server. On a RELOCATABLE interpreter this is usually a missing "
            "stdlib for the copied binary (see PYTHONHOME above), not an `argv[0]` refusal; "
            "reproduce it by running the copy by hand and reading its stderr"
        )
        inventory = reaper._iter_proc_inventory()
        assert inventory is not None
        # Written against the rows ALONE, so the pre-fix tree fails on the FACT
        # (the process is missing) rather than on the newer return type.
        rows_list = inventory.rows if hasattr(inventory, "rows") else inventory
        rows = {row["pid"]: row for row in rows_list}
        assert child.pid in rows, (
            "a live wrapper-exec'd server must be enumerated from the kernel's "
            f"name, not from argv[0] — inventory held {sorted(rows)}"
        )
        assert rows[child.pid]["socket_dir"] == str(gone)
    finally:
        child.kill()
        child.wait(timeout=30)


# ── the workflow shape: the blind, host-global reap must not come back ──────


def test_no_ci_step_reaps_embedded_redis_by_process_name():
    """NON-VACUOUS: this reds on the pre-fix workflow.

    `pkill -f "redis-server unixsocket"` cannot distinguish a leaked server
    from a concurrent leg's live one — that indistinguishability IS #3653. It is
    scanned across EVERY workflow, because the same step was parity-ported
    (`post-merge-validation.yml`, #1439) and a fix to one copy alone would leave
    the other killing peers.
    """
    offenders: list[str] = []
    for path in _workflow_files():
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"pkill\b.*redis-server", line):
                offenders.append(f"{path.name}:{i}")
    assert offenders == [], (
        'the host-global `pkill -f "redis-server unixsocket"` reaps a concurrent '
        "leg's live instance (#3653); use .github/scripts/reap-redislite-orphans.py: "
        + ", ".join(offenders)
    )


def test_every_cleaning_job_invokes_the_scoped_reaper():
    """Every workflow that reaps redislite litter must call the scoped tool."""
    for name in ("python-ci.yml", "post-merge-validation.yml"):
        text = (REPO_ROOT / ".github" / "workflows" / name).read_text()
        assert "reap-redislite-orphans.py --apply" in text, (
            f"{name} no longer reclaims redislite litter at all — the #1005 "
            "accumulation guard is gone with the blind pkill"
        )


#: The population the age proof quantifies over, in the two halves the shipped
#: diff states it in: the WIDE set ("jobs that can OWN one") and the part of it a
#: call site cannot see (every job that opts into the URI-less embedded lane).
#: Read off `reap-redislite-orphans.py:104-112`.
_POPULATION_PROSE = ("can OWN one", "URI-less embedded lane")

#: The narrowed reading the call-site comments carried before this pin existed —
#: "the jobs that run it", i.e. the five callers, which round 2 of #10327's own
#: review rejected: this tool enumerates the HOST's process table, so it can meet
#: a server belonging to a job that never installs it.
_NARROW_POPULATION_PROSE = "jobs that run it"


def _reaper_call_site_comments() -> list[tuple[str, str]]:
    """`(where, comment block)` for every step that actually RUNS the reaper.

    The block is the contiguous comment run immediately above the step's `run:`
    key (one non-comment line back, because `run: |` sits between them). It is
    anchored on the INVOCATION, not on a step name or a heading: a heading scan
    goes vacuous the moment the heading is reworded — the very failure this pin
    exists to catch one line over.
    """
    sites: list[tuple[str, str]] = []
    for path in _workflow_files():
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if "reap-redislite-orphans.py --apply" not in line:
                continue
            if line.lstrip().startswith("#"):  # prose ABOUT the call, not the call
                continue
            block: list[str] = []
            j = i - 1
            if j >= 0 and not lines[j].lstrip().startswith("#"):
                j -= 1  # step over the `run:` key
            while j >= 0 and lines[j].lstrip().startswith("#"):
                block.append(lines[j])
                j -= 1
            sites.append((f"{path.name}:{i + 1}", "\n".join(reversed(block))))
    return sites


def test_every_reaper_call_site_states_the_real_population():
    """NON-VACUOUS: this reds on all five comments the pre-fix diff shipped.

    The module says the opposite of "the jobs that run it" in two places
    (`reap-redislite-orphans.py:44-45`, `:104-105`), and `ci.yml`'s `flip-gate`
    comment gets it right — so a tree that says both ships two contradictory
    statements of one fact, and the prose ones sit on the DISPROVED side. It is
    not cosmetic: a maintainer recomputing the arm from the shipped comment
    derives it over the five callers, which happens to yield the same 90 today
    only because `python-ci.yml::test` is in both sets — while omitting
    `evals-on-demand.yml::evals` (90) and `ci.yml::flip-gate` (30), the jobs this
    PR's widening exists for. The next job to move would then derive a false arm
    from a green tree (#10327 review, ISSUE 1 — raised at `32b56f99e` and again
    at `0bc5529c3`, and dropped by a fixer lane that read the head as clean).
    """
    sites = _reaper_call_site_comments()
    assert len(sites) >= 5, (
        "the call-site scan found fewer steps than the five that run the reaper "
        f"({[where for where, _ in sites]}) — a scan that reads nothing would "
        "pass over the comments it exists to check, so fail instead"
    )
    narrow = [where for where, block in sites if _NARROW_POPULATION_PROSE in block]
    thin = [
        where for where, block in sites if not all(marker in block for marker in _POPULATION_PROSE)
    ]
    assert narrow == [], (
        'a call-site comment still names "the jobs that run it", the population '
        "round 2 of #10327's review rejected (the reaper reads the HOST's process "
        "table, so a job that never installs it can own a server):\n  " + "\n  ".join(narrow)
    )
    assert thin == [], (
        "a call-site comment does not state the population the age proof quantifies "
        'over — every one must name a job that "can OWN one" and the URI-less '
        "embedded opt-in that widens it past the call sites "
        "(`reap-redislite-orphans.py:104-112`):\n  " + "\n  ".join(thin)
    )


#: the three logical lines of the tempdir cleanup that follows the scoped reaper
_CLEANUP_MARKERS = ("gettempdir())", "-mmin +30", 'echo "tempdir cleaned"')


def _cleanup_runs(text):
    """Every maximal run of consecutive tempdir-cleanup lines in `text`."""
    runs, cur = [], []
    for line in text.splitlines():
        if any(m in line for m in _CLEANUP_MARKERS) and not line.lstrip().startswith("#"):
            cur.append(line.strip())
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def test_no_ci_step_duplicates_its_tempdir_cleanup():
    """The tempdir trio is ONE cleanup; a second copy is a mechanical artifact.

    NON-VACUOUS: this reds on the pre-fix tree, naming all five steps that carried
    the trio twice — `python-ci.yml` x4 and `post-merge-validation.yml` x1. That is
    the shape `0e32f415c` produced by inserting the scoped-reaper lines ABOVE the
    existing cleanup instead of replacing it: the commands are idempotent, so
    nothing fails, but the duplicate reads as intentional to the next editor and
    the step's body then claims two cleanups it does not need (#10254 round-13
    note). The invariant is per STEP, which is why it is asserted over runs of
    consecutive cleanup lines rather than over the file's total count.
    """
    offenders: list[str] = []
    for path in _workflow_files():
        for run in _cleanup_runs(path.read_text()):
            if len(run) != 3:
                offenders.append(
                    f"{path.name}: {len(run)} cleanup lines starting at {run[0][:52]!r}"
                )
    assert offenders == [], (
        "the tempdir cleanup is emitted more than once in a single step — the trio "
        '(`TMPD=…`, `find … -mmin +30`, `echo "tempdir cleaned"`) is one cleanup, '
        "and a second copy is a mechanical-edit artifact that reads as intentional:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("leg", EMBEDDED_LEGS)
def test_every_embedded_leg_invokes_the_scoped_reaper(leg):
    """Each of the four cleaning legs must call the scoped tool, not nothing."""
    text = PYTHON_CI.read_text()
    start = text.index(f"\n  {leg}:\n")
    # The next top-level job key, or end of file.
    nxt = re.search(r"\n  [A-Za-z0-9_-]+:\n", text[start + 1 :])
    block = text[start : start + 1 + nxt.start()] if nxt else text[start:]
    assert "reap-redislite-orphans.py" in block, (
        f"{leg} no longer reclaims redislite litter at all — the #1005 "
        "accumulation guard is gone with the blind pkill"
    )
