"""#3797 — an installed-but-unconfigured hook must report that it RAN.

Before this change the Claude ``session-start.sh`` seam left NO observation
whenever the host had no ``.tortoise`` config:

* ``tortoise session probe`` is refused at the credential gate in
  ``tortoise.__main__._cmd_session`` *before* any request is dispatched;
* ``POST /v1/sessions/install-probe`` is ``get_current_org_gated``;
* the hook discarded the probe's exit status (``|| true``) and wrote nothing.

So ``captureStatusForHarness`` saw a falsy ``install_probe_claude`` and the
install was indistinguishable from *never installed*.  The fix is an
**observation**, not a new endpoint: the hook records that IT ran, locally, and
the credential-free ``tortoise hooks status`` reports it.

The evidence standard here is the one `tests/test_4314_inert_hooks.py` set: the
REAL shipped hook is executed (bash, subprocess) and the REAL CLI is invoked.
A grep of the script cannot see this regression — it lives in control flow.

Every test names the mutation that REDs it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tortoise.__main__ import _hook_run_file, _HookRunUnreadable, _read_hook_run
from tortoise.capture_install import install_capture

REPO = Path(__file__).resolve().parent.parent
SESSION_START = REPO / "tortoise" / "claude-hooks" / "session-start.sh"
LABEL = "Last hook run observed on this machine:"

pytestmark = pytest.mark.skipif(
    not SESSION_START.exists(), reason="claude-hooks script not present")


# ── helpers ──────────────────────────────────────────────────────────────

def _module_dir(root: Path) -> Path:
    """A directory that IS a resolvable module dir: it holds ``tortoise/``."""
    (root / "tortoise").mkdir(parents=True, exist_ok=True)
    (root / "tortoise" / "__init__.py").write_text("", encoding="utf-8")
    return root


def _write_mock_tortoise(bindir: Path, log: Path, probe_rc: int) -> None:
    """A fake ``tortoise`` on PATH that logs argv and exits ``probe_rc`` for
    ``session probe`` (and 0 otherwise)."""
    bindir.mkdir(parents=True, exist_ok=True)
    mock = bindir / "tortoise"
    mock.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        'if [ "$1" = "session" ] && [ "$2" = "probe" ]; then\n'
        f"  exit {probe_rc}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8")
    mock.chmod(0o755)


def _shell_tools_only(bindir: Path) -> Path:
    """A PATH carrying the shell plumbing the hook needs but NO ``python3`` and
    no ``tortoise`` — the branch reached *because* the interpreter is missing
    (the same harness `test_4314_inert_hooks.py` uses)."""
    bindir.mkdir(parents=True, exist_ok=True)
    for tool in ("cat", "tr", "head", "mkdir", "date", "dirname"):
        real = shutil.which(tool)
        assert real, tool
        (bindir / tool).symlink_to(real)
    return bindir


def _run_hook(home: Path, *, path: str, src: Path | None = None,
              receipt_dir: Path | str | None = None,
              cwd: Path | None = None,
              hook: Path | None = None) -> subprocess.CompletedProcess:
    """Drive the REAL shipped hook with a hermetic env.

    ``HOME`` is ALWAYS the caller's tmp dir: the hook now writes a hook-run
    observation, and a test must never let it land in the developer's real
    ``$HOME`` (#3721's trap; see `test_hook_src_dir_record_never_writes_the_real_home`).
    """
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    env = {"HOME": str(home), "PATH": path, "TMPDIR": str(home / "tmp")}
    if src is not None:
        env["TORTOISE_SRC_DIR"] = str(src)
    if receipt_dir is not None:
        env["TORTOISE_IMPORT_RECEIPT_DIR"] = str(receipt_dir)
    return subprocess.run(
        ["/bin/bash", str(hook or SESSION_START)], input="",
        capture_output=True, text=True, env=env,
        cwd=str(cwd) if cwd else None, timeout=120)


def _cli(argv: list[str], *, home: Path | str, receipt_dir: Path | str | None = None,
         cwd: Path | None = None, timeout: int = 180,
         extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """The REAL CLI, with the same HOME/receipt-dir derivation the hook used."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(REPO)
    env.pop("TORTOISE_SRC_DIR", None)
    env.pop("CODEX_HOME", None)
    # The developer's own consent/credentials must never leak into a hermetic
    # test: `_resolve_config_path` reads `TORTOISE_API_KEY` BEFORE the
    # credential gate, so an inherited key would turn these CLI runs into REAL
    # network probes (and make the manual-probe test pass for a reason it does
    # not document).  `extra_env` can re-add any of them deliberately.
    env.pop("TORTOISE_API_KEY", None)
    env.pop("TORTOISE_API_URL", None)
    env.pop("TORTOISE_CAPTURE", None)
    if receipt_dir is not None:
        env["TORTOISE_IMPORT_RECEIPT_DIR"] = str(receipt_dir)
    else:
        env.pop("TORTOISE_IMPORT_RECEIPT_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "tortoise", *argv], capture_output=True,
        text=True, env=env, cwd=str(cwd) if cwd else None, timeout=timeout)


def _record(home: Path) -> dict:
    """The default-path observation (no ``TORTOISE_IMPORT_RECEIPT_DIR``") —
    ``${HOME}/.tortoise/hook-runs/<harness>.json``."""
    path = home / ".tortoise" / "hook-runs" / "claude.json"
    assert path.is_file(), (
        f"the hook left no hook-run observation (looked at {path}) — an "
        "installed-but-unconfigured install is invisible again (#3797)")
    return json.loads(path.read_text(encoding="utf-8"))


def _status_line(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if line.startswith(LABEL):
            return line
    return None


def _assert_observation_is_honest(line: str) -> None:
    """A surface may only say what was OBSERVED: the run line may report a RUN
    and the probe's outcome, never an install-state claim."""
    assert line.startswith(LABEL), line
    assert "installed" not in line and "not installed" not in line, line


# ── the observation exists at all (the premise, now fixed) ───────────────

def test_unconfigured_hook_records_that_it_ran(tmp_path):
    """The REAL hook, installed but unconfigured, leaves a hook-run record.

    This is the defect itself: before #3797 the file did not exist, so
    "installed and ran" could not be told from "not installed".

    Mutation: drop the ``_record_hook_run`` call from the probe block — no
    record is written and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    log = tmp_path / "calls.log"
    # The probe is REFUSED (rc 1) — the unconfigured case.
    _write_mock_tortoise(bindir, log, probe_rc=1)

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin")

    assert proc.returncode == 0, proc.stderr
    assert "session probe --harness claude" in log.read_text(encoding="utf-8")
    body = _record(home)
    assert body["harness"] == "claude", body
    assert body["kind"] == "hook-run", body
    assert body["probe_recorded"] is False, body
    assert body["probe_rc"] == 1, body
    assert body["recorded_at"], body


def test_recorded_probe_is_reported_as_recorded(tmp_path):
    """The ``probe_recorded: true`` state — a probe the server ACCEPTED.

    Mutation: hard-code ``probe_recorded`` to ``false`` (or drop the
    ``then``-branch that sets it) — the record/status assertions RED."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    log = tmp_path / "calls.log"
    _write_mock_tortoise(bindir, log, probe_rc=0)

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin")

    assert proc.returncode == 0, proc.stderr
    body = _record(home)
    assert body["probe_recorded"] is True, body
    assert body["probe_rc"] == 0, body


def test_a_manual_probe_writes_no_hook_run_record(tmp_path):
    """The HOOK is the witness, not the CLI. A manual `session probe` proves
    only that a CLI ran — attributing that to the hook would be the
    "installed ≠ ran" collapse this change exists to prevent.

    Mutation: move the record write into ``_cmd_session_probe`` (the CLI-side
    writer this design rejects) — the manual probe creates the file and this
    REDs."""
    home = tmp_path / "home"
    home.mkdir()
    receipts = home / "receipts"

    proc = _cli(["session", "probe", "--harness", "claude"], home=home,
                receipt_dir=receipts, cwd=tmp_path)

    # The point of the test is the ABSENCE, so the CLI's own (failing) verdict
    # must not be mistaken for the assertion.  `_cli` strips the developer's
    # credentials, so the credential gate is what fails here.
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "No .tortoise config found" in proc.stderr, proc.stderr
    assert not (home / "hook-runs" / "claude.json").exists(), (
        "a manual `session probe` wrote a hook-run record — the record would "
        "no longer witness the HOOK")


@pytest.mark.parametrize("branch", ["no-module-dir", "no-interpreter"])
def test_inert_branches_record_the_run_with_a_null_probe_rc(tmp_path, branch):
    """The two INERT early exits must record the run too, with the probe
    never attempted — so ``hooks status`` cannot call a hook that ran
    "never ran". The third argument is the bare JSON token ``null``.

    Mutation: pass an empty string instead of ``null`` (the natural
    ``printf ... "$probe_rc"`` mistake) — the record becomes invalid JSON and
    ``_record`` REDs on ``json.loads``."""
    home = tmp_path / "home"
    home.mkdir()
    hook = home / ".claude" / "hooks" / "session-start.sh"
    hook.parent.mkdir(parents=True)
    shutil.copy(SESSION_START, hook)
    hook.chmod(0o755)
    # `../..` from the installed position is $HOME, deliberately not a checkout.
    assert not (home / "tortoise").exists()

    src = None
    if branch == "no-module-dir":
        path = "/usr/bin:/bin"          # no tortoise, no module dir anywhere
    else:
        src = _module_dir(tmp_path / "srcmod")
        path = str(_shell_tools_only(tmp_path / "bin"))  # no python3 either

    proc = _run_hook(home, path=path, src=src, hook=hook)

    assert proc.returncode == 0, proc.stderr
    body = _record(home)
    assert body["kind"] == "hook-run", body
    assert body["probe_recorded"] is False, body
    assert body["probe_rc"] is None, body


# ── the credential-free status surface reports it ────────────────────────

def test_hooks_status_reports_the_run_for_an_unconfigured_install(tmp_path):
    """`tortoise hooks status` — credential-free — says the hook RAN, and says
    it could not record the probe. It must NOT claim an install state.

    Mutation: delete the ``_print_hook_run`` call from ``_cmd_hooks`` — the
    line is absent and this REDs (and an unconfigured install is silently
    reported as not-installed again)."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"

    bindir = tmp_path / "bin"
    _write_mock_tortoise(bindir, tmp_path / "calls.log", probe_rc=1)
    assert _run_hook(home, path=f"{bindir}:/usr/bin:/bin",
                     receipt_dir=receipts).returncode == 0

    proc = _cli(["hooks", "status", "--harness", "claude", "--dir", str(root)],
                home=home, receipt_dir=receipts, cwd=tmp_path)

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "are current" in proc.stdout, proc.stdout
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    _assert_observation_is_honest(line)
    assert "ran at" in line and "NOT recorded" in line, line
    assert "exit 1" in line, line


def test_hooks_status_distinguishes_never_ran_from_not_installed(tmp_path):
    """The distinction the issue asks for, on ONE surface: an installed hook
    that has not run reports "no run recorded", which is NOT "not installed".

    Mutation: render the absent case as a bare `Last hook run: none` without
    the observation wording — or claim installation — and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"

    proc = _cli(["hooks", "status", "--harness", "claude", "--dir", str(root)],
                home=home, receipt_dir=receipts, cwd=tmp_path)

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    _assert_observation_is_honest(line)
    assert "none" in line and "no run recorded" in line, line
    assert (home / "hook-runs" / "claude.json").exists() is False


@pytest.mark.parametrize("payload,why", [
    (json.dumps({"harness": "claude", "kind": "capture-failure",
                 "recorded_at": "2026-09-26T00:00:00Z"}), "wrong kind"),
    (json.dumps({"harness": "codex", "kind": "hook-run",
                 "recorded_at": "2026-09-26T00:00:00Z"}), "wrong harness"),
    (json.dumps(["hook-run"]), "not an object"),
])
def test_a_foreign_record_reads_as_no_observation(tmp_path, payload, why):
    """The READ condition is the WRITE condition (#4314): a file that PARSES
    but is not a ``KIND_HOOK_RUN`` record for THIS harness must degrade to "no
    run recorded", never raise and never read as a run.

    Mutation: drop the ``kind``/``harness`` test in ``_read_hook_run`` — the
    wrong-kind case renders as a RUN and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    (home / "hook-runs").mkdir(parents=True)
    (home / "hook-runs" / "claude.json").write_text(payload, encoding="utf-8")

    proc = _cli(["hooks", "status", "--harness", "claude", "--dir", str(root)],
                home=home, receipt_dir=receipts, cwd=tmp_path)

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "Traceback" not in proc.stderr, proc.stderr
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    assert "none" in line and "no run recorded" in line, (why, line)


@pytest.mark.parametrize("payload,why", [
    ("", "an empty file (the writer truncates before writing)"),
    ('{"harness": "claude", "kind": "hook-ru', "a torn write"),
    ("{not json at all", "plain garbage"),
])
def test_an_unparseable_record_is_not_reported_as_no_record(tmp_path, payload, why):
    """A file that EXISTS but yields no record must not be rendered as an
    absence nobody observed.  The shipped writer truncates and rewrites with a
    plain `>` redirect, so a torn file is a real (if brief) state; reporting it
    as "no run recorded" would claim a run never happened.

    Mutation: return `None` instead of raising `_HookRunUnreadable` from the
    parse arm — the line claims nothing was recorded and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    (home / "hook-runs").mkdir(parents=True)
    (home / "hook-runs" / "claude.json").write_text(payload, encoding="utf-8")

    proc, _ = _status(home, receipts, tmp_path, root=root)
    line = _status_line(proc.stdout)
    assert line is not None, (why, proc.stdout)
    _assert_observation_is_honest(line)
    assert "cannot tell whether a run was recorded" in line, (why, line)
    assert "no run recorded" not in line, (why, line)

    _proc, jpayload = _status(home, receipts, tmp_path, root=root,
                              json_out=True)
    assert jpayload["hook_run"]["observed"] is None, (why, jpayload["hook_run"])
    assert jpayload["hook_run"]["reason"] == "record-unreadable", \
        (why, jpayload["hook_run"])


def test_an_unstatable_record_path_is_not_reported_as_no_record(tmp_path):
    """A record path that cannot even be STAT-ED — an unsearchable state
    directory — is not evidence that no run happened.  `Path.is_file()`
    swallows the `OSError` and returns `False`, so the old gate rendered a
    confident "no run recorded" about a file nobody read: the #3797 defect on
    the very reason set added for it (`record-unreadable`).

    Mutation: put the `is_file()` gate back — the stat failure collapses to
    `None`, the reason goes `null` and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    runs = home / "hook-runs"
    runs.mkdir(parents=True)
    (runs / "claude.json").write_text(
        json.dumps({"kind": "hook-run", "harness": "claude",
                    "recorded_at": "2026-09-26T00:00:00Z"}), encoding="utf-8")
    runs.chmod(0o000)
    try:
        proc = _cli(["hooks", "status", "--harness", "claude",
                     "--dir", str(root)], home=home, receipt_dir=receipts,
                    cwd=tmp_path, timeout=60)
        _proc, payload = _status(home, receipts, tmp_path, root=root,
                                 json_out=True)
    finally:
        runs.chmod(0o755)
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    assert "cannot tell whether a run was recorded" in line, line
    assert "no run recorded" not in line, line
    assert payload["hook_run"]["observed"] is None, payload["hook_run"]
    assert payload["hook_run"]["reason"] == "record-unreadable", \
        payload["hook_run"]


def test_the_read_arm_does_not_swallow_an_unenumerated_failure(monkeypatch):
    """The read arm's catch-all is deliberately NOT `(OSError, UnicodeError)`:
    the next unenumerated read failure is the same silent-suppression bug the
    parse arm was widened for, and an escape here to a bare `return None`
    reads as "no record", i.e. as a run that never happened.

    Mutation: `return None` in place of the read arm's catch-all raise — this
    REDs (nothing else can reach it: the stat gate above it refuses every
    non-regular file and `read_text` otherwise raises `OSError`)."""
    import stat as _stat

    import tortoise.__main__ as cli

    class _Boom:
        def stat(self):
            return type("S", (), {"st_mode": _stat.S_IFREG | 0o600})()

        def read_text(self, **_kw):
            raise RuntimeError("an unenumerated read failure")

    monkeypatch.setattr(cli, "_hook_run_file", lambda _h: _Boom())
    with pytest.raises(cli._HookRunUnreadable):
        cli._read_hook_run("claude")


def test_the_reader_raises_for_an_unparseable_record_but_not_a_foreign_one(
        tmp_path, monkeypatch):
    """The parse boundary, pinned at the FUNCTION it belongs to.

    The CLI-level test for the pathological record cannot see this: when the
    raise escapes, `_print_hook_run`'s outer catch renders the IDENTICAL honest
    line and `_hook_run_json` the identical `record-unreadable` reason, so both
    routes are byte-identical at the surface.  Only a direct assertion
    distinguishes them.

    Mutation: `return None` in place of the parse arm's raise — the `raises`
    assertions RED.  Conversely a raise in place of the foreign-record `return
    None` REDs the final assertion: the boundary is a boundary, not an
    over-broad net."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TORTOISE_IMPORT_RECEIPT_DIR", raising=False)
    path = _hook_run_file("claude")
    path.parent.mkdir(parents=True, exist_ok=True)

    for payload in ("", '{"kind": "hook-run", "harness": "cla',
                    "[" * 200000 + "]" * 200000):
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(_HookRunUnreadable):
            _read_hook_run("claude")

    # Parses, but is not THIS harness's record: no observation, not unreadable.
    path.write_text(json.dumps({"kind": "capture-failure"}), encoding="utf-8")
    assert _read_hook_run("claude") is None


def test_a_non_executable_writer_is_not_called_qualified(tmp_path):
    """A current version marker is not enough: `detect_install` also requires
    the OWNER's exec bit, and its `not-executable` finding is blocking.  A
    script the harness cannot execute is not a writer that could have recorded
    a run, so calling the absent record a real absence would contradict the
    finding printed beside it in the same payload.

    Mutation: drop the exec-bit check in `_hook_run_writer_gap` — the reason
    goes null while the finding says not-executable, and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    (root / ".claude" / "hooks" / "session-start.sh").chmod(0o644)

    proc, _ = _status(home, receipts, tmp_path, root=root)
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    assert "cannot tell whether a run was recorded" in line, line

    _proc, payload = _status(home, receipts, tmp_path, root=root, json_out=True)
    hr = payload["hook_run"]
    assert hr["observed"] is None, hr
    assert hr["reason"] == "hook-script-unqualified", hr
    kinds = {f["kind"] for f in payload["findings"]}
    assert kinds & {"not-executable", "not-executable-symlink"}, (kinds, hr)


def test_hooks_status_says_nothing_about_a_harness_whose_hooks_do_not_write(
        tmp_path):
    """codex/cursor are ``session-end.sh``-only: their hooks structurally never
    write a hook-run record, so rendering an absence-of-observation for them
    would assert something that was never observed — the defect class this
    change removes, on a new surface.

    Mutation: drop the ``layout.writes_hook_run`` gate — codex prints the
    line and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    for harness in ("codex", "cursor"):
        assert install_capture(harness, home=home).ok, harness

    for harness in ("codex", "cursor"):
        proc = _cli(["hooks", "status", "--harness", harness], home=home,
                    cwd=tmp_path)
        assert proc.returncode == 0, (harness, proc.stdout, proc.stderr)
        assert _status_line(proc.stdout) is None, (harness, proc.stdout)


def test_hooks_status_json_stays_a_pure_json_document(tmp_path):
    """The observation is human-readable output ONLY: `--json` is a machine
    contract, and a stray human line would corrupt every consumer.

    Mutation: move the ``_print_hook_run`` call outside the
    ``not args.json`` guard — ``json.loads`` fails and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    (home / "hook-runs").mkdir(parents=True)
    (home / "hook-runs" / "claude.json").write_text(json.dumps(
        {"harness": "claude", "kind": "hook-run",
         "recorded_at": "2026-09-26T00:00:00Z",
         "probe_recorded": False, "probe_rc": 1}), encoding="utf-8")

    proc = _cli(["hooks", "status", "--harness", "claude", "--dir", str(root),
                 "--json"], home=home, receipt_dir=receipts, cwd=tmp_path)

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    json.loads(proc.stdout)          # must not raise
    assert LABEL not in proc.stdout, proc.stdout


@pytest.mark.parametrize("bad_home", ["~", "~/x"])
def test_an_unresolvable_home_cannot_break_hooks_status(tmp_path, bad_home):
    """``Path.home()`` RAISES when ``$HOME`` is ``~``/``~/x``. The observation
    is best-effort and exit-code-neutral: it must report that it could not
    look, keep the currency verdict, and keep rc 0.

    Mutation: let the derivation raise (or mirror `_cmd_hooks`' other
    catch-alls with ``return 1``) — the rc/stdout assertions RED."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok

    proc = _cli(["hooks", "status", "--harness", "claude", "--dir", str(root)],
                home=bad_home, cwd=tmp_path)

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "are current" in proc.stdout, proc.stdout
    assert "Traceback" not in proc.stderr, proc.stderr
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    # The observation could not be MADE, so it says exactly that — never
    # "none", which would be an absence we did not observe.
    assert "cannot tell whether a run was recorded" in line, line
    _assert_observation_is_honest(line)
    # A literal `~` tree must not be created where the CLI ran.
    assert not (tmp_path / "~").exists(), "HOME=~ materialised a tree"

    # Same verdict on the machine surface.
    proc_json = _cli(["hooks", "status", "--harness", "claude", "--dir",
                      str(root), "--json"], home=bad_home, cwd=tmp_path)
    assert proc_json.returncode == 0, (proc_json.stdout, proc_json.stderr)
    hr = json.loads(proc_json.stdout)["hook_run"]
    assert hr["observed"] is None, hr
    assert hr["reason"] == "state-directory-unresolvable", hr


# ── the writer/reader path derivation must not drift ─────────────────────

@pytest.mark.parametrize("suffix", ["", "/", "///", "/.", "/./", "//."])
def test_hook_and_reader_agree_on_the_hook_run_dir(tmp_path, monkeypatch, suffix):
    """The hook writes with SHELL string surgery; the reader derives with
    pathlib. #4373 proved those two disagree on a TRAILING SLASH, and a
    disagreement here would make a real run invisible.  A trailing `/.` is
    the same class: pathlib drops it (`Path('/a/b/.')` is `/a/b`, so the
    reader's `.parent` is `/a`) while `${x%/*}` would keep `/a/b`.  The two
    derivations are therefore pinned EQUAL — for the NEW ``hook-runs`` leaf of
    the HOOK that owns the shared helper (``session-start.sh``), which the
    sibling-copy trailing-slash test in `test_4314_inert_hooks.py` does not
    cover.

    Mutation: drop the trailing-slash normalisation from
    ``_tortoise_state_dir`` — the hook writes ``…/receipts//hook-runs`` while
    the reader resolves a different dir and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _write_mock_tortoise(bindir, tmp_path / "calls.log", probe_rc=1)
    receipt_dir = str(tmp_path / "receipts") + suffix
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", receipt_dir)

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin",
                     receipt_dir=receipt_dir)
    assert proc.returncode == 0, proc.stderr

    looked_for = _hook_run_file("claude")
    # EQUALITY, not just `is_file()`: the reader must land on the SAME
    # absolute path the hook wrote, and a relative result (which a mutated
    # derivation produces) must not be satisfiable by a stray file in the
    # process cwd (see the empty-override test).
    expected = tmp_path / "hook-runs" / "claude.json"
    assert looked_for.is_absolute() and looked_for == expected, (
        f"the hook's record is invisible to the reader for "
        f"TORTOISE_IMPORT_RECEIPT_DIR={receipt_dir!r}: the reader looks at "
        f"{looked_for}, expected {expected}")
    assert looked_for.is_file(), (looked_for, tmp_path)
    assert json.loads(looked_for.read_text(encoding="utf-8"))["kind"] == "hook-run"


def test_hook_and_reader_agree_for_the_capture_errors_leaf_too(
        tmp_path, monkeypatch):
    """The SAME shared derivation feeds the pre-existing ``capture-errors``
    writer, so the refactor must not move it: the inert branch's breadcrumb
    still lands where ``session_verify`` looks for it.

    Mutation: point ``_record_breadcrumb`` at a different leaf (or revert it
    to its own ``${x%/*}`` surgery) — the paths diverge and this REDs."""
    from tortoise import session_verify as sv

    home = tmp_path / "home"
    home.mkdir()
    hook = home / ".claude" / "hooks" / "session-start.sh"
    hook.parent.mkdir(parents=True)
    shutil.copy(SESSION_START, hook)
    hook.chmod(0o755)
    receipt_dir = str(tmp_path / "receipts") + "/"
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", receipt_dir)

    proc = _run_hook(home, path="/usr/bin:/bin", receipt_dir=receipt_dir,
                     hook=hook)
    assert proc.returncode == 0, proc.stderr

    looked_for = sv._local_capture_error_file(
        "claude", {"HOME": str(home), "TORTOISE_IMPORT_RECEIPT_DIR": receipt_dir})
    assert looked_for.is_file(), (looked_for, sorted(
        str(p) for p in tmp_path.rglob("*.json")))
    assert json.loads(
        looked_for.read_text(encoding="utf-8"))["kind"] == "install-inert"


def test_hook_and_reader_agree_when_the_override_is_empty(tmp_path, monkeypatch):
    """`${VAR:-default}` treats an EMPTY override as unset, and the reader
    must read it the same way: `os.environ.get(name, default)` would take
    `""` as a real value, resolve it to the cwd, and look for the record
    somewhere the hook never wrote it.

    Mutation: resolve the override with `os.environ.get(name, default)` — the
    reader looks for `./hook-runs/claude.json` and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _write_mock_tortoise(bindir, tmp_path / "calls.log", probe_rc=1)
    # BOTH sides must resolve the same HOME: the shell's `${VAR:-…}` and the
    # reader's fallback both go to `$HOME/.tortoise/import-receipts`.
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", "")
    monkeypatch.setenv("HOME", str(home))

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", receipt_dir="")
    assert proc.returncode == 0, proc.stderr

    looked_for = _hook_run_file("claude")
    # EQUALITY, not just `is_file()`: a relative or otherwise-wrong derivation
    # could be satisfied by a stray file in the process cwd, which is how this
    # assertion was nearly vacuous (a mutated derivation created a
    # repo-relative `hook-runs/` and the next iteration passed on it).
    expected = home / ".tortoise" / "hook-runs" / "claude.json"
    assert looked_for.is_absolute() and looked_for == expected, (
        f"an EMPTY override made the reader look at {looked_for}, expected "
        f"{expected} (the hook wrote under {home}: "
        f"{sorted(str(p) for p in home.rglob('*.json'))})")
    assert looked_for.is_file(), looked_for
    assert json.loads(
        looked_for.read_text(encoding="utf-8"))["kind"] == "hook-run"


def test_an_unreadable_probe_outcome_is_not_a_fabricated_failure(tmp_path):
    """A record accepted on kind+harness but carrying no readable probe
    outcome must not be reported as a probe FAILURE: `exit None` is a verdict
    nobody observed, the same class of lie as reading an absent record as a
    run.

    Mutation: restore the blanket `else` that prints `exit {rc}` — the line
    says "exit None" and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    (home / "hook-runs").mkdir(parents=True)
    (home / "hook-runs" / "claude.json").write_text(json.dumps(
        {"harness": "claude", "kind": "hook-run",
         "recorded_at": "2026-09-26T00:00:00Z"}), encoding="utf-8")

    proc = _cli(["hooks", "status", "--harness", "claude", "--dir", str(root)],
                home=home, receipt_dir=receipts, cwd=tmp_path)

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    _assert_observation_is_honest(line)
    assert "does not say what the install probe did" in line, line
    assert "exit None" not in line, line


def test_a_pathologically_nested_record_cannot_silence_the_surface(tmp_path):
    """`json.loads` raises `RecursionError` (a `RuntimeError`) on a deeply
    nested document.  An `OSError`-only catch let it escape to a bare
    `except Exception: return` that printed NOTHING — and silence is the
    original defect: the surface must still say what it could not tell.

    What THIS test pins is that half — remove BOTH catches and the line
    disappears, and this REDs.  It does NOT pin the inner catch's breadth:
    when a narrowed parse arm lets `RecursionError` escape, the caller's outer
    catch renders the IDENTICAL line, so that narrowing is an EQUIVALENT
    mutant here.  The boundary itself is pinned directly, at the function,
    by `test_the_reader_raises_for_an_unparseable_record_but_not_a_foreign_one`."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    (home / "hook-runs").mkdir(parents=True)
    (home / "hook-runs" / "claude.json").write_text(
        "[" * 200000 + "]" * 200000, encoding="utf-8")

    proc = _cli(["hooks", "status", "--harness", "claude", "--dir", str(root)],
                home=home, receipt_dir=receipts, cwd=tmp_path)

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "Traceback" not in proc.stderr, proc.stderr
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    # A record that cannot be PARSED is an observation that could not be MADE,
    # never a claim that no run happened — asserting "no run recorded" here
    # would be the false absence #3797 exists to remove.
    assert "cannot tell whether a run was recorded" in line, line
    assert "no run recorded" not in line, line
    _assert_observation_is_honest(line)


def test_an_install_too_old_to_record_is_not_reported_as_never_ran(tmp_path):
    """A hook installed before the record existed cannot write one, so its
    silence is not evidence that it never ran.  The surface must say it could
    not tell — otherwise the new line re-creates the invisible-run defect for
    every install made before this change.

    Mutation: drop the installed-generation check — the line becomes a bare
    "none — no run recorded" and this REDs."""
    from tortoise.hook_install import HOOK_RUN_GENERATION, read_hook_version

    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    installed = root / ".claude" / "hooks" / "session-start.sh"
    body = installed.read_text(encoding="utf-8")
    # Derive the SHIPPED marker from the file: pinning it to the constant would
    # make an unrelated generation bump (which must NOT move the writer floor)
    # fail here, forcing the floor to track the shipped generation.
    shipped = read_hook_version(installed)
    assert shipped is not None and shipped > HOOK_RUN_GENERATION - 1
    marker = f"# tortoise-hook-version: {shipped}"
    assert marker in body, body.splitlines()[:4]
    installed.write_text(
        body.replace(marker,
                     f"# tortoise-hook-version: {HOOK_RUN_GENERATION - 1}", 1),
        encoding="utf-8")

    proc = _cli(["hooks", "status", "--harness", "claude", "--dir", str(root)],
                home=home, receipt_dir=receipts, cwd=tmp_path)

    assert "Traceback" not in proc.stderr, proc.stderr
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    _assert_observation_is_honest(line)
    assert f"generation {HOOK_RUN_GENERATION - 1}" in line, line
    assert f"starts at generation {HOOK_RUN_GENERATION}" in line, line
    assert "no run recorded" not in line, line

    _proc, payload = _status(home, receipts, tmp_path, root=root, json_out=True)
    hr = payload["hook_run"]
    assert hr["observed"] is None, hr
    assert hr["reason"] == "hook-scripts-too-old", hr
    assert hr["generation"] == HOOK_RUN_GENERATION - 1, hr


def test_hooks_status_json_carries_the_observation(tmp_path):
    """The credential-free MACHINE surface must make the same distinction the
    text one does: `--json` carries the observation as a FIELD, and `null` for
    a harness whose hooks never write a record (codex/cursor).

    Mutation: drop the `hook_run` key from the payload — the field is absent
    and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    assert install_capture("codex", home=home).ok
    receipts = home / "receipts"

    def status(harness, *extra):
        argv = ["hooks", "status", "--harness", harness]
        if harness == "claude":
            argv += ["--dir", str(root)]
        proc = _cli([*argv, "--json", *extra], home=home,
                    receipt_dir=receipts, cwd=tmp_path)
        assert "Traceback" not in proc.stderr, proc.stderr
        return proc, json.loads(proc.stdout)

    # No record yet: a real absence of a RUN (not of an install).
    proc, payload = status("claude")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert payload["hook_run"]["observed"] is False, payload["hook_run"]
    assert payload["hook_run"]["path"].endswith(
        "/hook-runs/claude.json"), payload["hook_run"]

    # A run, recorded by the REAL hook.
    bindir = tmp_path / "bin"
    _write_mock_tortoise(bindir, tmp_path / "calls.log", probe_rc=1)
    assert _run_hook(home, path=f"{bindir}:/usr/bin:/bin",
                     receipt_dir=receipts).returncode == 0
    proc, payload = status("claude")
    assert payload["hook_run"]["observed"] is True, payload["hook_run"]
    assert payload["hook_run"]["probe_recorded"] is False, payload["hook_run"]
    assert payload["hook_run"]["probe_rc"] == 1, payload["hook_run"]

    # codex's hooks never write one: `null`, never a fabricated absence.
    _proc, payload = status("codex")
    assert payload["hook_run"] is None, payload["hook_run"]


def _status(home, receipts, cwd, *, harness="claude", root=None, json_out=False):
    """Run the REAL `hooks status` and return (proc, payload-or-None)."""
    argv = ["hooks", "status", "--harness", harness]
    if root is not None:
        argv += ["--dir", str(root)]
    if json_out:
        argv.append("--json")
    proc = _cli(argv, home=home, receipt_dir=receipts, cwd=cwd,
                timeout=60)
    assert "Traceback" not in proc.stderr, proc.stderr
    return proc, (json.loads(proc.stdout) if json_out else None)


def test_an_unqualified_writer_is_not_reported_as_never_ran(tmp_path):
    """A pre-#3795 install carries NO version marker, and `read_hook_version`'s
    own contract calls that a first-class STALE signal — it is by definition
    pre-#3797 and cannot write a record.  Collapsing it into "no basis to say
    the writer is missing" reports an absence nobody observed.

    Mutation: treat an unreadable/marker-less script as "no opinion" — both
    surfaces report a real absence and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    installed = root / ".claude" / "hooks" / "session-start.sh"
    installed.write_text(
        "\n".join(ln for ln in installed.read_text(encoding="utf-8").splitlines()
                  if not ln.startswith("# tortoise-hook-version:")) + "\n",
        encoding="utf-8")

    proc, _ = _status(home, receipts, tmp_path, root=root)
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    _assert_observation_is_honest(line)
    assert "cannot tell whether a run was recorded" in line, line
    assert "could not be qualified" in line, line

    _proc, payload = _status(home, receipts, tmp_path, root=root, json_out=True)
    assert payload["hook_run"]["observed"] is None, payload["hook_run"]
    assert payload["hook_run"]["reason"] == "hook-script-unqualified", \
        payload["hook_run"]


@pytest.mark.parametrize("occupant,why", [
    ("dir", "a directory"),
    ("fifo", "a FIFO"),
    ("broken-symlink", "a broken symlink"),
    ("foreign", "a foreign script"),
])
def test_an_unusable_or_foreign_writer_is_never_called_missing(
        tmp_path, occupant, why):
    """`detect_install` reports these as `not-a-regular-file`,
    `symlinked-script` or `foreign-script` IN THE SAME PAYLOAD, so a reason of
    `hook-script-missing` would contradict the finding printed beside it.  The
    reason is deliberately coarse, and must match what was actually observed.

    Mutation: classify a non-regular session-start script as missing — the
    reason says `hook-script-missing` and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    script = root / ".claude" / "hooks" / "session-start.sh"
    script.unlink()
    if occupant == "dir":
        script.mkdir()
    elif occupant == "fifo":
        os.mkfifo(script)
    elif occupant == "broken-symlink":
        script.symlink_to(root / "nope.sh")
    else:
        script.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        script.chmod(0o755)

    proc, _ = _status(home, receipts, tmp_path, root=root)
    line = _status_line(proc.stdout)
    assert line is not None, (why, proc.stdout)
    _assert_observation_is_honest(line)
    assert "cannot tell whether a run was recorded" in line, (why, line)

    _proc, payload = _status(home, receipts, tmp_path, root=root, json_out=True)
    hr = payload["hook_run"]
    assert hr["observed"] is None, (why, hr)
    assert hr["reason"] != "hook-script-missing", (why, hr)
    # The reason must not contradict the finding the SAME document carries for
    # the same path.
    kinds = {f["kind"] for f in payload["findings"]}
    assert kinds & {"not-a-regular-file", "symlinked-script", "foreign-script"}, \
        (why, kinds, hr)


def test_an_unreadable_writer_script_is_not_called_marker_less(tmp_path):
    """`read_hook_version` swallows `OSError` and returns `None`, so a
    chmod-000 script is indistinguishable from a marker-less one AT THAT
    CALL.  Asserting "no version marker, so they predate the record" about a
    file nobody could read is an install claim made from a failed read.

    Mutation: map `installed is None` unconditionally to the marker-less
    reason — the text claims a missing marker and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    script = root / ".claude" / "hooks" / "session-start.sh"
    script.chmod(0o000)
    try:
        proc, _ = _status(home, receipts, tmp_path, root=root)
        _proc, payload = _status(home, receipts, tmp_path, root=root,
                                 json_out=True)
    finally:
        script.chmod(0o755)

    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    assert "cannot tell whether a run was recorded" in line, line
    assert "no version marker" not in line, line
    assert payload["hook_run"]["reason"] == "hook-script-unqualified", \
        payload["hook_run"]


def test_a_writer_at_the_floor_reports_a_real_absence(tmp_path):
    """HOOK_RUN_GENERATION is a FLOOR, not a mirror of the shipped marker: the
    SHIPPED install IS at the floor while the write contract is unchanged, so
    an absent record from it is a real absence of a run.  Raising the floor
    WITHOUT bumping the shipped script is the mutation this pins — that is the
    lockstep error (a floor that follows an unrelated script bump would call an
    install that can still record "too old").

    Mutation: `HOOK_RUN_GENERATION = 8` while the shipped script stays at 7 —
    the shipped install is called too old and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"

    proc, _ = _status(home, receipts, tmp_path, root=root)
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    assert "none" in line and "no run recorded" in line, line

    _proc, payload = _status(home, receipts, tmp_path, root=root, json_out=True)
    assert payload["hook_run"]["observed"] is False, payload["hook_run"]
    assert payload["hook_run"]["reason"] is None, payload["hook_run"]


def test_an_undecodable_record_is_not_reported_as_no_record(tmp_path):
    """A present record whose bytes are not UTF-8 is a READ failure, not a
    parse result — the shipped writer truncates and rewrites with a plain `>`
    redirect, so a concurrent reader can legitimately see a torn file.  With
    `read_text` failing outside `OSError`, that arm returned None and the
    surface reported an absence nobody observed.

    Mutation: catch only `OSError` in the read arm — the line claims nothing
    was recorded and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    (home / "hook-runs").mkdir(parents=True)
    (home / "hook-runs" / "claude.json").write_bytes(
        b'{"harness": "cla\xff\xfe')

    proc, _ = _status(home, receipts, tmp_path, root=root)
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    _assert_observation_is_honest(line)
    assert "cannot tell whether a run was recorded" in line, line
    assert "no run recorded" not in line, line

    _proc, payload = _status(home, receipts, tmp_path, root=root, json_out=True)
    assert payload["hook_run"]["observed"] is None, payload["hook_run"]
    assert payload["hook_run"]["reason"] == "record-unreadable", \
        payload["hook_run"]


def test_a_missing_writer_is_named_as_such_on_the_machine_surface(tmp_path):
    """Nothing installed at all: the absence of a record IS real, but a
    machine consumer must be able to tell it from "installed and never ran"
    without cross-reading the findings.

    Mutation: emit `reason: null` for the missing case — the field no longer
    distinguishes the two and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()          # deliberately NOT installed
    receipts = home / "receipts"

    proc, _ = _status(home, receipts, tmp_path, root=root)
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    assert "none" in line and "no run recorded" in line, line

    _proc, payload = _status(home, receipts, tmp_path, root=root, json_out=True)
    assert payload["hook_run"]["observed"] is False, payload["hook_run"]
    assert payload["hook_run"]["reason"] == "hook-script-missing", \
        payload["hook_run"]


def test_a_fifo_at_the_record_path_cannot_hang_hooks_status(tmp_path):
    """`open()` on a FIFO blocks FOREVER — strictly worse than a raise, in a
    command whose docstrings promise best-effort behaviour.  The repo already
    pins this rule for `settings.json`
    (`test_hook_upgrade.py::test_fifo_at_the_settings_path_does_not_hang`);
    this is a NEW path the credential-free surface reads.

    It is also an observation that could not be MADE, not an absence: the
    hook's own write could not have landed here as a record, so "no run
    recorded" would be a claim about a run nobody could observe.

    Mutation: drop the `S_ISREG` check (the `is_file()` gate it replaced) —
    the child never returns and this REDs on the timeout.  Or return `None`
    instead of raising — the line claims an absence and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    run_dir = home / "hook-runs"
    run_dir.mkdir(parents=True)
    os.mkfifo(run_dir / "claude.json")

    proc, _ = _status(home, receipts, tmp_path, root=root)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    assert "cannot tell whether a run was recorded" in line, line
    assert "no run recorded" not in line, line

    _proc, payload = _status(home, receipts, tmp_path, root=root, json_out=True)
    assert payload["hook_run"]["observed"] is None, payload["hook_run"]
    assert payload["hook_run"]["reason"] == "record-unreadable", \
        payload["hook_run"]


def test_an_unreadable_record_is_not_reported_as_no_record(tmp_path):
    """A record that EXISTS but cannot be READ must not be rendered as an
    absence nobody observed, which is what lumping it with "no record" does.

    Mutation: treat the `OSError` from `read_text` as "no record" — the line
    claims nothing was recorded and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    run_dir = home / "hook-runs"
    run_dir.mkdir(parents=True)
    record = run_dir / "claude.json"
    record.write_text(json.dumps({
        "harness": "claude", "kind": "hook-run",
        "recorded_at": "2026-09-26T00:00:00Z",
        "probe_recorded": False, "probe_rc": 1}), encoding="utf-8")
    record.chmod(0o000)
    try:
        proc, _ = _status(home, receipts, tmp_path, root=root)
        _proc, payload = _status(home, receipts, tmp_path, root=root,
                                 json_out=True)
    finally:
        record.chmod(0o600)

    line = _status_line(proc.stdout)
    assert line is not None, proc.stdout
    _assert_observation_is_honest(line)
    assert "cannot tell whether a run was recorded" in line, line
    assert "no run recorded" not in line, line
    assert payload["hook_run"]["observed"] is None, payload["hook_run"]
    assert payload["hook_run"]["reason"] == "record-unreadable", \
        payload["hook_run"]


@pytest.mark.parametrize("fields,why", [
    ({"probe_recorded": True, "probe_rc": None}, "recorded with no exit code"),
    ({"probe_recorded": "true", "probe_rc": 0}, "recorded is a string"),
    ({"probe_recorded": False, "probe_rc": True}, "exit code is a bool"),
    ({"recorded_at": "T"}, "no probe fields at all"),
])
def test_an_unreadable_probe_outcome_agrees_on_both_surfaces(tmp_path, fields, why):
    """The two surfaces are companions: where the text refuses to state a probe
    outcome, the JSON must not hand a consumer the raw contradictory pair (a
    consumer doing `if hr["probe_recorded"]` would read a result the text says
    is unreadable).

    Mutation: pass the raw fields through in `_hook_run_json` — the payload
    carries `probe_recorded` and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    (home / "hook-runs").mkdir(parents=True)
    (home / "hook-runs" / "claude.json").write_text(json.dumps(
        {"harness": "claude", "kind": "hook-run", **fields}),
        encoding="utf-8")

    proc, _ = _status(home, receipts, tmp_path, root=root)
    line = _status_line(proc.stdout)
    assert line is not None, (why, proc.stdout)
    assert "does not say what the install probe did" in line, (why, line)
    assert "exit True" not in line and "exit None" not in line, (why, line)

    _proc, payload = _status(home, receipts, tmp_path, root=root, json_out=True)
    hr = payload["hook_run"]
    assert hr["observed"] is True, (why, hr)
    assert hr["reason"] is None, (why, hr)
    assert hr["probe_outcome"] == "unreadable", (why, hr)
    assert "probe_recorded" not in hr and "probe_rc" not in hr, (why, hr)


# ── the clearer must resolve the WRITER's path, not a second one ─────────

def test_the_breadcrumb_clearer_resolves_the_writers_own_path(
        tmp_path, monkeypatch):
    """The clear is the other half of the write: ``sessions import`` removes a
    ``capture-failure`` breadcrumb on a 2xx (``_record_capture_error``'s
    documented contract).  Under an EMPTY override the writer and the reader
    both resolve ``$HOME``-relative, so a clearer still deriving the path with
    ``os.environ.get(name, default)`` looks in the CWD instead — the breadcrumb
    survives forever and the machine keeps reporting a capture failure that
    already succeeded.

    Mutation (VERIFIED RED): resolve the override in
    ``capture_spool._clear_breadcrumb_for`` with ``os.environ.get(name,
    default)`` — the clearer looks for ``./capture-errors/claude.json``, the
    file written under ``$HOME`` survives, and this REDs."""
    from tortoise import capture_spool
    from tortoise.__main__ import _capture_error_file
    from tortoise.hook_install import KIND_CAPTURE_FAILURE

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", "")
    monkeypatch.setenv("HOME", str(home))

    written = _capture_error_file("claude")
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(json.dumps({
        "harness": "claude",
        "kind": KIND_CAPTURE_FAILURE,
        "session_id": "imp_abc",
    }), encoding="utf-8")
    assert written == home / ".tortoise" / "capture-errors" / "claude.json", (
        written)

    capture_spool._clear_breadcrumb_for("claude", "imp_abc")

    assert not written.exists(), (
        "the breadcrumb the writer placed under $HOME survived its own clear: "
        "the clearer resolved a different tree")


@pytest.mark.parametrize("bad_home", ["~", "~/x"])
def test_an_unresolvable_home_cannot_break_the_capture_breadcrumb(
        monkeypatch, bad_home):
    """The derivation is FALLIBLE on purpose — ``Path.home()`` RAISES for
    ``$HOME=~``/``~/x`` — and both capture-breadcrumb callers run on the
    FAILURE paths of ``sessions import``, where a raise would replace a
    reportable capture failure with a traceback.  The breadcrumb is documented
    best-effort, so both must return quietly.

    Mutation (VERIFIED RED): hoist ``path = _capture_error_file(harness)`` back
    OUTSIDE the ``try`` in ``_record_capture_error``, or drop ``RuntimeError``
    from ``_clear_capture_error``'s suppression — this REDs with
    ``RuntimeError: Could not determine home directory.``"""
    from tortoise.__main__ import _clear_capture_error, _record_capture_error

    monkeypatch.setenv("HOME", bad_home)
    monkeypatch.delenv("TORTOISE_IMPORT_RECEIPT_DIR", raising=False)

    # Neither writes nor fails loudly: there is no HOME to write under.
    _record_capture_error("codex", "unreachable host", session_id="imp_x")
    _clear_capture_error("codex")
