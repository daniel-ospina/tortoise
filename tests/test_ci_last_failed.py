"""#6142 — the last-failed fast-fail pre-phase in the ``test`` job.

A red PR re-pays up to 33-39 min per push to rediscover the SAME failure. The
pre-phase re-runs only the tests the PREVIOUS push of this PR left failing, so
a known failure that still fails reds in ~1 min instead of a full half.

The property that matters — and that ``tests/test_markers.py`` pins for every
pre-merge selection (#4164) — is that this MUST NOT narrow the GATING run. The
pre-phase is a pre-check only: when it passes (or when there is no last-failed
state, or the state names no test in this half) the FULL half runs unchanged.
Under a ROTATING flake set — a different handful of tests failing each push —
a reduced GATING run would skip a rotated-in failure and manufacture a false
green, so these tests pin the shape that prevents exactly that.
"""
from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "python-ci.yml"

# The flags from tests/test_markers.py::_NARROWING_FLAGS that can SUBTRACT from
# a selection (subtracting flags) or EMPTY it (the "execute nothing"
# collectors). NOT identical to that list: it drops the reorder-only
# `--failed-first`/`--new-first` (they cannot narrow) and adds the short `--sw`
# alias of `--stepwise`.
NARROWING = (
    "--last-failed", "--lf", "--stepwise", "--sw", "-k",
    "--deselect", "--ignore", "--ignore-glob", "--collect-only", "--co",
    "--setup-only", "--setup-plan", "--fixtures",
)

FULL_JUNIT = "/tmp/junit.xml"
PRE_JUNIT = "/tmp/junit-lf.xml"


def _test_job_steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["test"]["steps"]


def _run_script() -> str:
    for step in _test_job_steps():
        if step.get("name", "").startswith("Run fast test suite"):
            return step["run"]
    raise AssertionError("the `test` job has no 'Run fast test suite' step")


def _pytest_lines(script: str) -> list[str]:
    return [ln.strip() for ln in script.splitlines() if "python -m pytest" in ln]


def _pytest_args(line: str) -> list[str]:
    """The pytest argv ONLY — the shell wrapper's `timeout -s INT -k 10` is
    not a pytest flag (the same trap tests/test_markers.py documents)."""
    return line.split("python -m pytest", 1)[1].split()


def _is_narrowing(args: list[str]) -> list[str]:
    """Narrowing flags present in a pytest argv (empty list = none)."""
    hits = []
    for a in args:
        for flag in NARROWING:
            exact = a == flag or a.startswith(flag + "=")
            # attached short-option value: `-kslow` IS `-k slow` to argparse
            # (a bare `--keep…` must not match `-k`, hence the single-dash test)
            attached = (flag.startswith("-") and not flag.startswith("--")
                        and a.startswith(flag) and len(a) > len(flag))
            if exact or attached:
                hits.append(a)
                break
    return hits


def test_full_gating_run_is_not_narrowed():
    """The run that writes the canonical junit (the GATING run) must carry no
    narrowing flag — the #4164 property this change must preserve."""
    full = [ln for ln in _pytest_lines(_run_script()) if FULL_JUNIT in ln]
    assert len(full) == 1, full
    hits = _is_narrowing(_pytest_args(full[0]))
    assert not hits, f"the GATING run must not be narrowed by {hits}: {full[0]}"
    # ...and its file argument must be the whole half, not the pre-phase's
    # nodeid set: swapping `$FILES` for `"${LF_NODEIDS[@]}"` narrows the
    # gating run to the previous failure set without adding a flag.
    assert "$FILES" in _pytest_args(full[0]), full[0]
    assert "LF_NODEIDS" not in full[0], full[0]


def test_prephase_is_pr_only_and_never_uses_pytest_lf_fallback():
    """The pre-phase must be (a) PR-only and (b) driven by EXPLICIT nodeids.

    pytest's `--lf` FALLS BACK to running the whole selection when the restored
    keys intersect nothing collected. On the tier-2 shape the half is re-split
    per push, so that fallback would run the full half in the pre-phase AND
    again as phase 2. The workflow therefore intersects the restored keys with
    `$FILES` itself and passes the nodeids explicitly.
    """
    script = _run_script()
    lines = script.splitlines()
    pre = [ln for ln in _pytest_lines(script) if PRE_JUNIT in ln]
    assert len(pre) == 1, pre
    # no narrowing flag at all — not just `--lf`: `--last-failed`, `--sw` and
    # friends trigger the SAME LFPlugin behaviour, and `-k` would subtract too.
    hits = _is_narrowing(_pytest_args(pre[0]))
    assert not hits, (
        f"the pre-phase must not narrow the selection ({hits}); its explicit "
        f"nodeids are the only subtraction allowed: {pre[0]}")
    # explicit nodeids come from a real intersection, not prose: the CODE line
    # must read lastfailed and split on '::'.
    assert 'json.load(open(".pytest_cache/v/cache/lastfailed"))' in script
    assert 'n.split("::",1)' in script
    # the predicate itself: `not in files` would invert the per-half scoping
    # (run the complement — a dead optimization, or another half's tests).
    assert "[0] in files" in script
    # and the whole pre-phase is gated on the PR event + the restored state.
    guard = next(i for i, ln in enumerate(lines)
                 if "github.event_name" in ln and ln.strip().startswith("if "))
    # The POLARITY is the pin, not the token: `!in` would leave every other
    # assertion here green while the pre-phase ran on every push EXCEPT a PR —
    # i.e. the feature silently off where it is meant to run (mutation-checked).
    assert lines[guard].strip().startswith(
        'if [ "${{ github.event_name }}" = "pull_request" ]'), lines[guard]
    assert "!=" not in lines[guard], lines[guard]
    assert "[ -s .pytest_cache/v/cache/lastfailed ]" in lines[guard], lines[guard]


def test_nodeid_intersection_actually_selects_this_halfs_failures(tmp_path):
    """Execute the workflow's OWN intersection one-liner over a fixture.

    A substring check cannot tell `in files` from `not in files`, and the
    pre-phase's whole safety rests on selecting only THIS half's previous
    failures. Run the real python payload (path substituted) against a cache
    holding a this-half failure, another half's failure, and a spaced nodeid.
    """
    script = _run_script()
    one_liner = re.search(r"python3 -c '([^']*json\.load[^']*)'", script)
    assert one_liner, script
    cache = tmp_path / "lastfailed"
    cache.write_text(json.dumps({
        "tests/test_a.py::test_one[c 1]": True,
        "tests/test_b.py::test_two": True,
        "tests/test_other.py::test_three": True,
    }))
    code = one_liner.group(1).replace(
        '.pytest_cache/v/cache/lastfailed', str(cache))
    out = subprocess.run(
        ["python3", "-c", code], input="tests/test_a.py tests/test_b.py",
        capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == ["tests/test_a.py::test_one[c 1]",
                                "tests/test_b.py::test_two"], repr(out)


def test_a_vanished_nodeid_is_pruned_so_the_prephase_cannot_lock_itself_out(tmp_path):
    """Execute the workflow's OWN prune payload over a fixture (#6142 P2).

    rc 4 means pytest could not find one of the nodeids and aborted the WHOLE
    pre-phase (zero tests ran) — and LFPlugin keeps a cache entry it never
    collected, so an unpruned vanished nodeid (a renamed or re-parametrized
    test) would disable the fast-fail for that half on EVERY later push. The
    prune is what makes that self-healing; without it this test reds because
    the vanished entry survives in the cache.
    """
    script = _run_script()
    block = re.search(r"<<'LF_PRUNE'\n(.*?)\n *LF_PRUNE", script, re.S)
    assert block, script
    code = textwrap.dedent(block.group(1))
    cache = tmp_path / "lastfailed"
    cache.write_text(json.dumps({
        "tests/test_a.py::test_gone": True,
        "tests/test_a.py::test_live": True,
    }))
    log = tmp_path / "pytest.log"
    # pytest's real shape for a nodeid it cannot find
    log.write_text(
        "ERROR: not found: tests/test_a.py::test_gone\n"
        "(no name 'tests/test_a.py::test_gone' in any of [<Module test_a.py>])\n")
    code = code.replace(".pytest_cache/v/cache/lastfailed", str(cache))
    code = code.replace("/tmp/pytest.log", str(log))
    out = subprocess.run(["python3", "-c", code], capture_output=True,
                         text=True, check=True).stdout
    assert "pruned 1 vanished nodeid(s)" in out, out
    assert json.loads(cache.read_text()) == {
        "tests/test_a.py::test_live": True}, cache.read_text()
    # ...and a log naming nothing is a NO-OP, not a wipe: the entries a real
    # failure left must survive an rc-4 run that found no missing nodeid.
    log.write_text("1 failed, 2 passed\n")
    subprocess.run(["python3", "-c", code], capture_output=True, text=True,
                   check=True)
    assert json.loads(cache.read_text()) == {
        "tests/test_a.py::test_live": True}, cache.read_text()


def test_full_run_is_outside_the_guard_and_unconditional():
    """Fail-OPEN: a missing/empty cache must run the FULL set, and a PASSING
    pre-phase must fall through to it. So the full invocation sits OUTSIDE the
    `if ... lastfailed ... fi` guard, with no early exit between them."""
    script = _run_script()
    lines = script.splitlines()
    guard = next(i for i, ln in enumerate(lines)
                 if "github.event_name" in ln and ln.strip().startswith("if "))
    full = next(i for i, ln in enumerate(lines)
                if FULL_JUNIT in ln and "python -m pytest" in ln)
    assert full > guard, "the full run must fall through OUTSIDE the guard"
    # the LAST base-level `fi` before the full run closes the pre-phase block;
    # nothing executable may sit between it and the full run. The regression
    # this catches: an `exit` short-circuit inserted there (which would make the
    # gate conditional on the pre-phase FAILING — no longer fail-open).
    base = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
    last_fi = max(i for i in range(guard, full) if lines[i] == " " * base + "fi")
    between = [ln.strip() for ln in lines[last_fi + 1:full] if ln.strip()]
    # NOTHING executable may sit between the pre-phase's closing fi and the
    # full run: a first-token check would miss `false || exit 1`,
    # `[ -n "$X" ] && exit 1` or an `if …; then exit; fi` short-circuit.
    assert not between, between
    # the ONLY exit inside [guard, full) is the pinned red branch — an `exit 0`
    # added INSIDE the pre-phase block (bare, `|| exit 0`, or a `trap … EXIT`)
    # would let a PASSING pre-phase skip the full half GREEN, and `between`
    # cannot see it (it is before the last fi). Token-based, not first-token.
    region = lines[guard:full]
    exits = [ln.strip() for ln in region
             if re.search(r"(^|[;&|]\s*|\bthen\s+)exit(\s|$)", ln.strip())]
    assert exits == ["exit $LF_RC"], exits
    assert "trap" not in "\n".join(region), region
    # ...nor may the pre-phase invocation itself short-circuit.
    pre_line = next(ln for ln in region if PRE_JUNIT in ln)
    assert not re.search(r"\|\||&&|;", pre_line), pre_line
    # ...and the gating line carries no shell condition prefix, and uses the
    # GATING budget — per-shard since #6135 — never the pre-phase's 10m.
    assert lines[full].strip().startswith(
        "timeout -s INT -k 10 ${{ matrix.watchdog_minutes }}m"), lines[full]
    assert " -k 10 10m " not in lines[full], lines[full]


def test_prephase_marker_equals_gating_marker():
    """#6142: `-m` is NOT in test_markers._NARROWING_FLAGS — it is the seam's
    deliberately-identical filter, pinned against the skip-guard manifest's
    `--marker` by tests/test_ci_selection.py. Adding a second pytest
    invocation retargeted that scan to the pre-phase; now that the scan is
    pinned to the gating run, pin the other direction too: the pre-phase must
    filter with EXACTLY the gating marker, or pre-phase and gating run would
    select different sets."""
    script = _run_script()

    def marker(line: str) -> str:
        return re.search(r"-m '([^']+)'", line).group(1)

    pre = next(ln for ln in _pytest_lines(script) if PRE_JUNIT in ln)
    full = next(ln for ln in _pytest_lines(script) if FULL_JUNIT in ln)
    assert marker(pre) == marker(full)


def test_lastfailed_cache_is_keyed_per_job_half_and_ref():
    """State must never bleed between the two matrix halves or between PRs."""
    cache_steps = [
        s for s in _test_job_steps()
        if "last-failed state" in s.get("name", "")]
    assert len(cache_steps) == 2, [s.get("name") for s in cache_steps]
    for step in cache_steps:
        key = step["with"]["key"]
        assert "github.job" in key
        assert "matrix.half" in key
        assert "github.ref_name" in key
        # per-push uniqueness: without github.sha the key is constant per PR,
        # so the first save reserves it and every later save is dropped — the
        # lastfailed state freezes at the first push (silent, no red).
        assert "github.sha" in key
        assert step["with"]["path"] == ".pytest_cache"

    # The exact key is per-sha, so it ALWAYS misses on a new push: only the
    # restore step's `restore-keys` makes the previous push's entry reachable.
    # It must be a real prefix of the key and carry the ref (else state
    # silently never restores, or bleeds across PRs).
    restore = next(s for s in cache_steps
                   if "Restore pytest last-failed" in s.get("name", ""))
    save = next(s for s in cache_steps
                if "Save pytest last-failed" in s.get("name", ""))
    key = restore["with"]["key"]
    # the SAVED key must be the one the restore step's prefix matches: a
    # reordered field (still containing every part) would break the prefix and
    # the pre-phase would never fire, with every field-presence assert green.
    assert key == save["with"]["key"], (key, save["with"]["key"])
    restore_keys = restore["with"]["restore-keys"].rstrip()
    assert restore_keys.endswith("-"), restore_keys
    assert key.startswith(restore_keys), (key, restore_keys)
    assert "github.sha" not in restore_keys, restore_keys
    for part in ("github.job", "matrix.half", "github.ref_name"):
        assert part in restore_keys, restore_keys


def test_cache_provider_enabled_so_lastfailed_is_written():
    """The feature rides on pytest WRITING `.pytest_cache/v/cache/lastfailed`.

    Every OTHER pytest invocation in this workflow disables the cache provider
    (`-p no:cacheprovider` — 5 sites here, 15 repo-wide, the population #6142
    documents), so re-adding
    it to this job's two invocations is a plausible "restore consistency"
    edit — and it would silently kill the optimization (no cache file, no
    pre-phase, no red, ever). Pin the absence.
    """
    script = _run_script()
    lines = _pytest_lines(script)
    assert len(lines) == 2, lines
    for line in lines:
        assert "cacheprovider" not in line, line


def test_cache_restore_precedes_run_and_save_follows_even_on_red():
    steps = _test_job_steps()
    names = [s.get("name", "") for s in steps]
    restore = next(i for i, n in enumerate(names) if "Restore pytest last-failed" in n)
    run = next(i for i, n in enumerate(names) if n.startswith("Run fast test suite"))
    save = next(i for i, n in enumerate(names) if "Save pytest last-failed" in n)
    assert restore < run < save
    # a RED run must still save its failures — that is the state the next
    # push's pre-phase reads; a re-run of the same sha is a harmless no-op.
    assert "always()" in steps[save].get("if", "")
    # and BOTH cache steps are PR-only: `main` must always run the full set.
    for i in (restore, save):
        assert "pull_request" in steps[i].get("if", ""), names[i]
    # exact polarity: `!=` (push-only: PRs never restore/save) and `||` both
    # pass a substring check while killing the feature.
    assert steps[restore]["if"] == "github.event_name == 'pull_request'", \
        steps[restore]["if"]
    assert steps[save]["if"] == \
        "always() && github.event_name == 'pull_request'", steps[save]["if"]
    # both cache steps are explicitly NON-GATING: a cache-service error (or a
    # re-run that cannot reserve an existing key) must never red the job.
    for i in (restore, save):
        assert steps[i].get("continue-on-error") is True, names[i]


def test_prephase_failopen_codes_are_pinned():
    """FAIL-OPEN (#6142): the pre-phase reds ONLY on a real failing run.

    A vanished nodeid (rc 4 usage / rc 5 nothing collected), a passing run (0)
    and an interrupted or watchdog-killed run (2 external SIGINT / in-test
    KeyboardInterrupt, 124/137 the pre-phase's OWN watchdog — 10m on a handful
    of tests is not a verdict) must all fall through to the FULL half. Deleting
    the `-ne 4`/`-ne 5` clause would turn a deselected or vanished nodeid into
    a job red — fail-CLOSED, the wrong direction for a fail-open pre-check.
    """
    script = _run_script()
    cond = next(
        ln.strip() for ln in script.splitlines() if '"$LF_RC" -ne 0' in ln)
    excluded = {int(m) for m in re.findall(r'\$LF_RC" -ne (\d+)', cond)}
    assert {0, 2, 4, 5, 124, 137} <= excluded, cond
    assert 1 not in excluded, (
        "a real failing pre-phase MUST red the job: " + cond)
    assert 3 not in excluded, (
        "an internal pytest error MUST red the job: " + cond)
    # ...and the chain must be a CONJUNCTION of those clauses. With `||`, an
    # rc of 0 would satisfy the branch → `exit 0` → the full run is SKIPPED
    # and the job is GREEN — the #4164 false green this change exists to
    # prevent. The token set alone survives that mutation.
    assert "||" not in cond, cond
    assert cond.startswith('if [ "$LF_RC" -ne 0 ]'), cond
    assert script.count('] && [ "$LF_RC" -ne') == 5, (
        script.count('] && [ "$LF_RC" -ne'))
    # the red path must EXIT with that code, not fall through to the full run.
    assert "exit $LF_RC" in script
    # ...and the capture itself: without it LF_RC stays 0 and neither the red
    # branch nor the watchdog banner can ever fire (pre-phase AND full always run).
    assert "LF_RC=$?" in script


def test_prephase_has_its_own_bounded_watchdog():
    """#6142 P1: the pre-phase must not borrow the GATING watchdog.

    Setup + pre-phase + gating must fit the job cap; if the pre-phase could
    spend the gating budget, a *passing* pre-phase would leave the gating run to
    be killed by the runner cap mid-suite — the #798 death mode this job's own
    cap comment says is impossible.

    #6135 makes that budget per-shard (`matrix.watchdog_minutes`) instead of the
    literal 55m, so the pin is that the gating run uses the SHARD budget and NOT
    the pre-phase's 10m: the two must never collapse into one value, or the
    pre-phase stops being the cheap pre-phase.
    """
    script = _run_script()
    pre = [ln for ln in _pytest_lines(script) if PRE_JUNIT in ln]
    assert len(pre) == 1, pre
    assert "timeout -s INT -k 10 10m" in pre[0], pre[0]
    full = [ln for ln in _pytest_lines(script) if FULL_JUNIT in ln]
    assert "timeout -s INT -k 10 ${{ matrix.watchdog_minutes }}m" in full[0], full[0]
    # the pre-phase's 10m must NOT be the gating budget
    assert "timeout -s INT -k 10 10m" not in full[0], full[0]
    # the job cap holds setup + pre-phase watchdog + gating watchdog (>=75m).
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["test"]
    assert job["timeout-minutes"] >= 75, job["timeout-minutes"]


def test_nodeids_are_passed_as_an_array_not_word_split():
    """#6142 P3: parametrized nodeids contain spaces.

    An unquoted `$LF_NODEIDS` word-splits `test_x[case one]` into two argv
    entries -> pytest UsageError (rc 4) -> the optimization silently never
    applies to spaced ids. The pre-phase must read them into a bash array.
    """
    script = _run_script()
    # one nodeid per line, read into an array with a portable read loop
    # (bash 3.2 has no `mapfile`; an unquoted $LF_NODEIDS word-splits).
    assert "while IFS= read -r LF_NID; do LF_NODEIDS+=(\"$LF_NID\")" in script
    # ...and it must be FED: without the process substitution the array is
    # always empty and the pre-phase never runs.
    assert "done < <(printf" in script
    # one nodeid per line, so the loop yields one element each.
    assert 'print("\\n".join(' in script
    assert '"${LF_NODEIDS[@]}"' in script
