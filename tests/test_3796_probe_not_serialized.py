"""#3796 — the install-probe beacon must not be SERIALIZED behind the digest.

``tortoise/claude-hooks/session-start.sh`` does two independent things: the
memory digest (``tortoise context``, whose stdout Claude Code injects) and the
install-probe beacon (``tortoise session probe --harness claude`` — the
server-visible ``install_probe_claude`` signal the dashboard's onboarding state
reads).

#3755 closed the *exit-status* coupling (``... || exit 0`` → ``|| true``).  What
it did NOT close is the *latency* coupling: the digest still ran to completion
in the foreground and the probe started only after it returned, so the POST was
serialized behind a step whose runtime this script does not bound
(``tortoise/monitoring.py``: the embedded SDK-acquisition prefix is not covered
by ``PROBE_SDK_ACQUISITION_BUDGET``, the redis read timeout is 10 s clampable to
60 s, and a reviewer measured ~26.8 s for one such query).  A harness that kills
a slow SessionStart hook kills the beacon with it — the same "busy store reports
capture not installed" symptom family, through a channel ``|| true`` cannot
reach.

These tests EXECUTE the hook (bash, subprocess).  The regression lives in the
DISPATCH ORDER, so a grep of the script cannot see it — the hook must actually
run, and one of them must observe it WHILE it is still inside the digest.

Every test names the mutation that REDs it.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "tortoise" / "claude-hooks"
SESSION_START = HOOKS_DIR / "session-start.sh"

PROBE_CALL = "session probe --harness claude"
DIGEST_MARKER = "TORTOISE-DIGEST"
PROBE_MARKER = "TORTOISE-PROBE-BYTES"

pytestmark = pytest.mark.skipif(
    not SESSION_START.exists(), reason="claude-hooks script not present")


# ── helpers ──────────────────────────────────────────────────────────────

def _path_without_tortoise() -> str:
    """PATH with no ``tortoise`` executable, every other entry kept so
    ``python3``/``dirname``/``bash`` still resolve.  Used so the mock wins
    deterministically even on a machine with an installed ``tortoise``."""
    kept: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and (Path(entry) / "tortoise").exists():
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def _path_with_tortoise(bindir: Path) -> str:
    return os.pathsep.join([str(bindir), _path_without_tortoise()])


def _write_mock_tortoise(bindir: Path, log: Path, *, context_rc: int = 0,
                         probe_rc: int = 0, context_sleep: float = 0.0,
                         probe_noise: bool = False) -> None:
    """A fake ``tortoise`` on PATH.

    It appends its argv to ``log`` FIRST, then behaves as the caller asked:
    ``context`` optionally sleeps, prints ``DIGEST_MARKER`` on success, and
    exits ``context_rc``; anything else (i.e. the probe) exits ``probe_rc``,
    optionally writing ``PROBE_MARKER`` to BOTH stdout and stderr first.
    Logging first is what makes the dispatch-order tests observable: a line in
    ``log`` is proof the process RAN."""
    bindir.mkdir(parents=True, exist_ok=True)
    sleep_line = f"  sleep {context_sleep}\n" if context_sleep else ""
    noise = (f"echo '{PROBE_MARKER}'\n"
             f"echo '{PROBE_MARKER}' >&2\n") if probe_noise else ""
    mock = bindir / "tortoise"
    mock.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        'if [ "$1" = "context" ]; then\n'
        f"{sleep_line}"
        f"  [ {context_rc} -eq 0 ] && echo '{DIGEST_MARKER}'\n"
        '  echo "embedded store busy" >&2\n'
        f"  exit {context_rc}\n"
        "fi\n"
        f"{noise}"
        f"exit {probe_rc}\n",
        encoding="utf-8")
    mock.chmod(mock.stat().st_mode | stat.S_IEXEC)


def _write_fake_source_tree(src_dir: Path, log: Path, *, context_rc: int = 0,
                            probe_rc: int = 0) -> None:
    """A fake checkout for the source-tree fallback: ``<src>/tortoise/__main__.py``
    with a ``main(argv)`` that logs argv, returns ``context_rc`` for the digest
    and ``probe_rc`` otherwise."""
    pkg = src_dir / "tortoise"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(
        "import pathlib\n"
        f"_LOG = pathlib.Path({str(log)!r})\n"
        "def main(argv):\n"
        "    with _LOG.open('a') as f:\n"
        "        f.write(' '.join(argv) + chr(10))\n"
        "    return {ctx} if argv and argv[0] == 'context' else {prb}\n".format(
            ctx=context_rc, prb=probe_rc),
        encoding="utf-8")


def _env(path: str, home: Path, extra: dict | None = None) -> dict:
    """A hermetic env.  ``HOME`` is ALWAYS the caller's tmp dir: the shipped
    hook writes a hook-run observation and a test must never let it land in the
    developer's real ``$HOME`` (#3721's trap)."""
    env = {"HOME": str(home), "PATH": path, "TMPDIR": str(home / "tmp")}
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    if extra:
        env.update(extra)
    return env


def _run_hook(path: str, home: Path, extra: dict | None = None,
              hook: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(hook or SESSION_START)], input="",
        capture_output=True, text=True, env=_env(path, home, extra),
        timeout=120)


def _hook_run_record(home: Path) -> dict:
    """The observation the hook left for ``hooks status`` to read."""
    path = home / ".tortoise" / "hook-runs" / "claude.json"
    assert path.is_file(), (
        f"the hook left no hook-run observation (#3797 regressed): {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _shell_tools_only(bindir: Path) -> Path:
    """A PATH with the shell plumbing the hook needs but NO ``python3`` and no
    ``tortoise`` — the branch reached *because* the interpreter is missing."""
    bindir.mkdir(parents=True, exist_ok=True)
    for tool in ("cat", "tr", "head", "mkdir", "date", "dirname"):
        real = shutil.which(tool)
        assert real, tool
        (bindir / tool).symlink_to(real)
    return bindir


# ── the defect: the beacon must be DISPATCHED, not queued behind the digest ─

def test_probe_is_dispatched_while_the_digest_is_still_running(tmp_path):
    """#3796, the acceptance test: a digest that sleeps far past the harness
    bound must not delay the beacon's DISPATCH.

    The mock's ``context`` sleeps 30 s; the beacon logs its argv and returns
    immediately.  The hook is started, NOT waited on, and we poll for the
    beacon's log line.  Two assertions carry the whole test:

      * the line appears within the poll budget, and
      * the hook is STILL RUNNING when it does.

    The second is the one that distinguishes dispatch from sequence: if the
    probe had merely been dispatched first and then awaited, or left until
    after the digest, the line could only appear once the 30 s sleep finished —
    at which point ``proc.poll()`` is not ``None``.

    Mutation: move the ``_run_probe … &`` dispatch below the ``_run_digest``
    call (the pre-#3796 order) — the line never appears inside a 5 s window
    while a 30 s digest is in flight, and this REDs on the timeout."""
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    _write_mock_tortoise(bindir, log, context_sleep=30)

    proc = subprocess.Popen(
        ["/bin/bash", str(SESSION_START)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=_env(_path_with_tortoise(bindir), home),
        # Its own process group: the 30 s `sleep` grandchild must be killable
        # with the hook, or this test would leak a process per run.
        start_new_session=True)
    try:
        deadline = time.monotonic() + 5.0
        calls = ""
        while time.monotonic() < deadline:
            if log.exists():
                calls = log.read_text(encoding="utf-8")
                if PROBE_CALL in calls:
                    break
            time.sleep(0.05)
        assert PROBE_CALL in calls, (
            "the install-probe beacon was not dispatched while the digest was "
            "still running (#3796): after 5 s the mock had been invoked with "
            f"{calls!r} — with a 30 s digest that means the beacon is still "
            "QUEUED BEHIND it, which is the defect")
        assert "context" in calls, (
            f"the digest should also have been dispatched: {calls!r}")
        assert proc.poll() is None, (
            "the hook had already exited when the beacon line appeared, so "
            "this observed SEQUENCE, not concurrency — the assertion proves "
            "nothing about starvation")
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=30)


def test_probe_stdout_and_stderr_never_reach_the_digest_channel(tmp_path):
    """The digest's stdout IS the injection channel, so the concurrently
    running beacon must not be able to interleave bytes into it.

    The mock's probe writes ``PROBE_MARKER`` to BOTH streams; its digest writes
    ``DIGEST_MARKER`` to stdout.  The injected stream must carry the digest and
    none of the beacon, and the beacon's stderr must be suppressed too (a spike
    of probe noise on the hook's stderr is what ``#5919`` was about).

    Mutation: drop the ``>/dev/null 2>&1`` from the ``_run_probe`` dispatch
    site — ``PROBE_MARKER`` appears in stdout and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    _write_mock_tortoise(bindir, log, probe_noise=True)

    r = _run_hook(_path_with_tortoise(bindir), home)

    assert r.returncode == 0, (r.stdout, r.stderr)
    calls = log.read_text(encoding="utf-8")
    assert "context" in calls and PROBE_CALL in calls, calls
    assert DIGEST_MARKER in r.stdout, (
        f"the digest stdout must still be forwarded for injection: {r.stdout!r}")
    assert PROBE_MARKER not in r.stdout, (
        "beacon bytes reached the injection channel — the probe's stdout must "
        f"stay suppressed: {r.stdout!r}")
    assert PROBE_MARKER not in r.stderr, (
        f"beacon stderr leaked: {r.stderr!r}")


# ── the beacon's OUTCOME must survive the concurrent dispatch (#3797) ──────

@pytest.mark.parametrize("probe_rc,recorded,reported_rc", [
    (0, True, 0),
    (1, False, 1),
    (3, False, 3),
])
def test_the_reaped_probe_status_still_reaches_the_observation(
        tmp_path, probe_rc, recorded, reported_rc):
    """Concurrency must not lose the outcome that ``#3797`` records.

    The background subshell cannot assign to a parent variable, so the ONLY
    channel for the probe's result is its exit status through ``wait``.  If the
    reap is wrong, the ``hook-run`` record silently regresses to "no probe was
    attempted" for a probe that ran and failed — the exact "installed but
    unobserved" class #3797 exists to remove.

    Mutation: drop the ``else PROBE_RC=$?`` arm (or ``wait`` at all) — the
    failing cells report ``probe_rc`` null and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    _write_mock_tortoise(bindir, log, probe_rc=probe_rc)

    r = _run_hook(_path_with_tortoise(bindir), home)

    assert r.returncode == 0, r.stderr
    body = _hook_run_record(home)
    assert body["kind"] == "hook-run", body
    assert body["harness"] == "claude", body
    assert body["probe_recorded"] is recorded, body
    assert body["probe_rc"] == reported_rc, body


def test_a_source_tree_probe_is_reaped_too(tmp_path):
    """The same reap on the OTHER discovery branch — no ``tortoise`` on PATH,
    so the beacon runs as ``main([...])`` through the resolved module dir.

    Mutation: background only the bin arm and leave the module-dir arm serial
    — the probe's outcome is written by the wrong path and the rc assertion
    REDs (and the dispatch-order guarantee silently holds on one branch only)."""
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.log"
    src = tmp_path / "src"
    _write_fake_source_tree(src, log, probe_rc=4)

    r = _run_hook(_path_without_tortoise(), home,
                  extra={"TORTOISE_SRC_DIR": str(src)})

    assert r.returncode == 0, r.stderr
    calls = log.read_text(encoding="utf-8")
    assert "context" in calls and PROBE_CALL in calls, calls
    body = _hook_run_record(home)
    assert body["probe_recorded"] is False, body
    assert body["probe_rc"] == 4, body


# ── discovery is resolved once, and pre-initialised ───────────────────────

def test_discovery_is_resolved_exactly_once():
    """``TORTOISE_BIN`` must be derived from the PATH exactly once.

    Before #3796 the script ran the binary lookup twice — once for the digest
    and again in the probe block — so discovery could disagree with itself, and
    the probe's arm was reachable only because the digest branch had happened
    to assign ``PYTHON_BIN`` first.

    This is a STRUCTURAL pin, and it is deliberately narrow: it proves the
    lookup count, NOT that every dereference is safe (that is the runtime
    matrix below).  It is worth having because the duplication is invisible to
    every behavioural test — with one binary on the PATH both lookups return
    the same answer, so only the count can distinguish them.

    Mutation: restore ``TORTOISE_BIN="$(command -v tortoise || true)"`` in the
    probe block — the count becomes 2 and this REDs."""
    text = SESSION_START.read_text(encoding="utf-8")
    assert text.count("command -v tortoise") == 1, (
        "discovery must be resolved EXACTLY ONCE (#3796): a second binary "
        "lookup lets the digest and the beacon execute different binaries")
    # …and the one resolution is inside the named resolver, so a later reader
    # cannot mistake an incidental assignment for discovery.
    assert "_resolve_discovery() {" in text and "_resolve_discovery\n" in text, (
        "discovery must go through the single `_resolve_discovery` helper")


def test_all_three_discovery_variables_are_initialised_before_use():
    """``set -u`` safety must be INITIALISATION, not control-flow luck.

    Before #3796 ``PYTHON_BIN`` had no top-level initialiser — it was assigned
    only inside the digest branch — so a single-sided change to the discovery
    predicate would have dereferenced it unset and ended the script with
    ``unbound variable``, breaking the exit-0 contract.  ``TORTOISE_MODULE``
    was initialised but by accident of ordering rather than by declaration.

    Mutation: delete any of the three empty initialisers (or move one below
    ``_resolve_discovery``) — the ordering assertion REDs."""
    text = SESSION_START.read_text(encoding="utf-8")
    for name in ("TORTOISE_BIN", "TORTOISE_MODULE", "PYTHON_BIN"):
        decl = f'{name}=""'
        assert decl in text, (
            f"{name} must be declared before use so `set -u` cannot bite a "
            "branch that skips the assignment (#3796)")
        assert text.index(decl) < text.index("_resolve_discovery()"), (
            f"{name} is initialised AFTER the resolver — the safety ordering, "
            "not just the presence of the line, is the contract")
    assert 'PROBE_PID=""' in text, (
        "PROBE_PID must be declared before the `[ -n \"$PROBE_PID\" ]` gate")


# ── the matrix: exit 0 and no `unbound variable`, in every cell ───────────

def _stderr_is_clean(proc: subprocess.CompletedProcess) -> None:
    assert "unbound variable" not in proc.stderr, (
        f"`set -u` bit on a discovery variable: {proc.stderr!r}")
    assert "Traceback" not in proc.stderr, proc.stderr


@pytest.mark.parametrize("context_rc,probe_rc,cell", [
    (0, 0, "both succeed"),
    (0, 2, "probe fails"),
    (1, 0, "digest fails"),
    (1, 2, "both fail"),
    (127, 127, "both fail, badly"),
])
def test_both_steps_failing_in_any_combination_still_exits_zero(
        tmp_path, context_rc, probe_rc, cell):
    """The inviolable contract: session start is never blocked, and the beacon
    is attempted in every cell.

    Mutation: let the digest or the ``wait`` reap escape its guard — a non-zero
    cell returns non-zero and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    _write_mock_tortoise(bindir, log, context_rc=context_rc, probe_rc=probe_rc)

    r = _run_hook(_path_with_tortoise(bindir), home)

    assert r.returncode == 0, (cell, r.stdout, r.stderr)
    _stderr_is_clean(r)
    calls = log.read_text(encoding="utf-8")
    assert "context" in calls, (cell, calls)
    assert PROBE_CALL in calls, (cell, calls)


@pytest.mark.parametrize("path_key,extra,cell", [
    ("no_tortoise_at_all", {}, "no tortoise binary, no source tree"),
    ("shell_tools_only", {}, "no python3 and no tortoise binary"),
])
def test_the_inert_configurations_exit_zero_without_an_unbound_variable(
        tmp_path, path_key, extra, cell):
    """The inert cells the issue names explicitly: no ``tortoise`` on PATH,
    ``TORTOISE_SRC_DIR`` unset, no source tree ⇒ exit 0 with nothing on stderr.

    The hook is COPIED to an installed position so ``../..`` is ``$HOME`` — not
    a checkout (#4314) — which is what makes the cell genuinely inert.

    Mutation: dereference ``PYTHON_BIN``/``TORTOISE_MODULE`` without an
    initialiser on a path that skips their assignment — ``unbound variable``
    lands on stderr and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hook = home / ".claude" / "hooks" / "session-start.sh"
    hook.parent.mkdir(parents=True)
    shutil.copy(SESSION_START, hook)
    hook.chmod(0o755)
    assert not (home / "tortoise").exists(), (
        "the installed position's ../.. must not be a checkout for this cell")

    if path_key == "no_tortoise_at_all":
        path = "/usr/bin:/bin"
    else:
        path = str(_shell_tools_only(tmp_path / "bin"))

    r = _run_hook(path, home, extra=extra, hook=hook)

    assert r.returncode == 0, (cell, r.stdout, r.stderr)
    _stderr_is_clean(r)
    body = _hook_run_record(home)
    assert body["probe_recorded"] is False, (cell, body)
    assert body["probe_rc"] is None, (
        f"{cell}: a probe that was never attempted must leave the bare JSON "
        f"token `null`, not a fabricated status: {body}")


def test_the_source_tree_fallback_still_runs_both_steps(tmp_path):
    """The fallback branch is exercised end-to-end after the restructure: no
    ``tortoise`` on PATH, ``TORTOISE_SRC_DIR`` pointing at a fake checkout.

    It is the branch the new standalone interpreter guard could most easily
    have broken, because it is the ONLY one that needs ``PYTHON_BIN``.

    Mutation: gate the Python digest on ``TORTOISE_BIN`` (the pre-#3796 outer
    condition) — the fallback digest never runs and the ``context`` assertion
    REDs."""
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.log"
    src = tmp_path / "src"
    _write_fake_source_tree(src, log, context_rc=0, probe_rc=0)

    r = _run_hook(_path_without_tortoise(), home,
                  extra={"TORTOISE_SRC_DIR": str(src)})

    assert r.returncode == 0, r.stderr
    _stderr_is_clean(r)
    calls = log.read_text(encoding="utf-8")
    assert "context" in calls, f"fallback digest did not run: {calls!r}"
    assert PROBE_CALL in calls, f"fallback beacon did not run: {calls!r}"
    body = _hook_run_record(home)
    assert body["probe_recorded"] is True, body


def test_no_source_tree_anywhere_is_still_silent_and_exit_zero(tmp_path):
    """``TORTOISE_SRC_DIR`` unset and every candidate rejected, with the hook
    installed: the inert branch must still exit 0 and still record the run.

    Mutation: make the new standalone interpreter guard ``exit 1`` instead of
    ``exit 0`` — session start is blocked and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hook = home / ".claude" / "hooks" / "session-start.sh"
    hook.parent.mkdir(parents=True)
    shutil.copy(SESSION_START, hook)
    hook.chmod(0o755)

    r = _run_hook("/usr/bin:/bin", home, hook=hook)

    assert r.returncode == 0, (r.stdout, r.stderr)
    _stderr_is_clean(r)
    # The install-inert breadcrumb is the evidence the hook injected nothing.
    crumb = home / ".tortoise" / "capture-errors" / "claude-install.json"
    assert crumb.is_file(), sorted(str(p) for p in home.rglob("*.json"))


# ── the shipped generation must move with the behaviour (install contract) ─

def test_the_shipped_generation_was_bumped_for_this_behaviour_change():
    """A behavioural hook edit must move ``tortoise-hook-version``, or an
    already-installed copy is reported CURRENT and keeps the serialized order
    forever — ``tortoise hooks status`` / ``doctor`` would tell the user nothing
    is stale.

    The claude hooks share ONE contract generation (``contract_version``
    refuses a disagreement), so all of them must move together.  The set is
    read from the INSTALL LAYOUT, not from a directory glob: the directory also
    holds hooks that carry no contract marker at all (``volunteer-turn.sh``),
    and globbing it would assert a contract over a file that never agreed to
    one.

    Mutation: revert the bump — the marker no longer exceeds the generation
    that shipped the serialized order (10) and this REDs."""
    from tortoise.hook_install import HARNESS_LAYOUTS, read_hook_version

    layout = HARNESS_LAYOUTS["claude"]
    versions = {spec.name: read_hook_version(spec.source)
                for spec in layout.scripts}
    assert len(versions) == 3, versions
    assert all(v is not None for v in versions.values()), versions
    assert len(set(versions.values())) == 1, (
        f"the shipped claude hooks must declare ONE contract generation: "
        f"{versions}")
    shipped = next(iter(versions.values()))
    assert shipped > 10, (
        f"#3796 changes the hook's dispatch order, so the install-contract "
        f"generation must move past 10; it is {shipped}")
    assert read_hook_version(SESSION_START) == shipped, versions


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
