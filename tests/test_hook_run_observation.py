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

from tortoise.__main__ import _hook_run_file
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
         cwd: Path | None = None,
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
        text=True, env=env, cwd=str(cwd) if cwd else None, timeout=180)


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
    ("{not json at all", "malformed"),
    (json.dumps({"harness": "codex", "kind": "hook-run",
                 "recorded_at": "2026-09-26T00:00:00Z"}), "wrong harness"),
    (json.dumps(["hook-run"]), "not an object"),
])
def test_a_foreign_or_corrupt_record_reads_as_no_observation(
        tmp_path, payload, why):
    """The READ condition is the WRITE condition (#4314): a file that is not a
    ``KIND_HOOK_RUN`` record for THIS harness — foreign, malformed, or
    corrupt — must degrade to "no run recorded", never raise and never read
    as a run.

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
    assert looked_for.is_file(), (
        f"the hook's record is invisible to the reader for "
        f"TORTOISE_IMPORT_RECEIPT_DIR={receipt_dir!r}: the reader looks at "
        f"{looked_for}, the hook wrote under {tmp_path}")
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
    assert looked_for.is_file(), (
        f"an EMPTY override made the reader look at {looked_for} while the "
        f"hook wrote under {home} (/hook-runs/*: "
        f"{sorted(str(p) for p in home.rglob('*.json'))})")
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

    Mutation: narrow the catch in `_read_hook_run` back to
    `(OSError, ValueError)` — the escape lands in the outer arm, which says
    "could not be read" instead of "no run recorded", and this REDs.  (With
    BOTH layers removed the line disappears entirely.)"""
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
    # A record that cannot be PARSED is malformed, and the read/write
    # condition says a malformed record is indistinguishable from no
    # observation — so the honest line is the same "no run recorded" the
    # foreign/corrupt test asserts.  Asserting merely "cannot tell" would let
    # this pass with the INNER catch removed, because the outer arm also
    # prints something; silence (the original defect) is the only other
    # failure mode the line can have.
    assert "no run recorded" in line, line
    _assert_observation_is_honest(line)


def test_an_install_too_old_to_record_is_not_reported_as_never_ran(tmp_path):
    """A hook installed before the record existed cannot write one, so its
    silence is not evidence that it never ran.  The surface must say it could
    not tell — otherwise the new line re-creates the invisible-run defect for
    every install made before this change.

    Mutation: drop the installed-generation check — the line becomes a bare
    "none — no run recorded" and this REDs."""
    from tortoise.hook_install import HOOK_RUN_GENERATION

    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    receipts = home / "receipts"
    installed = root / ".claude" / "hooks" / "session-start.sh"
    body = installed.read_text(encoding="utf-8")
    marker = "# tortoise-hook-version: "
    old_marker = f"{marker}{HOOK_RUN_GENERATION}"
    assert old_marker in body, body.splitlines()[:4]
    installed.write_text(
        body.replace(old_marker, f"{marker}{HOOK_RUN_GENERATION - 1}", 1),
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
