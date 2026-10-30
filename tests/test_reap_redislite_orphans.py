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
import re
import subprocess
import sys
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
      which is how the repo's two WIDEST embedded jobs set it
      (`python-ci.yml:1542` in `test`, `:2380` in `test-slow`: the variable is
      exported at step scope and can be EMPTY on the docker lane, so the
      expression is all a text scan ever sees).

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

    A job is admitted on either signal it cannot own a server without: it
    invokes this reaper, or it opts into the URI-less embedded lane — at workflow
    `env:` level (inherited by every job), at job level, or at step level, and in
    either spelling `_opts_in` accepts (a truthy literal, or a non-empty
    `${{ … }}` expression). The marker is the
    carve-out env var rather than the string "pytest" deliberately —
    `ci.yml::flip-gate` runs pytest from inside `verify-cutover`, so a `pytest`
    scan cannot see the exact job this population was widened for. The residual
    (a URI-set job whose suite constructs embedded anyway, through the
    `tests/_embedded.py` no-redirect registry) is recorded in the module's
    derivation block, not silently absorbed: the reaper-invoking half covers
    today's such jobs, and every one of them is capped below the arm.
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

    The repo's two widest embedded jobs opt in the way a text scan cannot spell:
    `TORTOISE_TEST_CARVE_OUT: ${{ env.CARVE_OUT }}` (`python-ci.yml:1542` in
    `test`, `:2380` in `test-slow`). They are admitted today by the
    reaper-invocation half, so this hole is INVISIBLE on the real tree — which is
    exactly why it needs its own fixture: a job that sets the expression and does
    not run the reaper would otherwise be admitted by nothing, and if it were
    uncapped the age-arm proof would go false while `uncapped == []` kept
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


def test_a_measured_empty_host_is_still_exit_0(reaper, tmp_path):
    """The other direction: an inventory that WAS read and held no litter is exit 0."""
    inventory = tmp_path / "proc.json"
    inventory.write_text("[]")
    assert reaper.main(["--proc-json", str(inventory)]) == 0
    assert reaper.run([], max_age_s=_age_bound_s(reaper), apply=True) == 0


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
