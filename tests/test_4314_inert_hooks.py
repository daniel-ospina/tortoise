"""#4314 — an inert harness install must not read as health.

The shipped capture hooks resolved their module dir as
``$(dirname "$0")/../..`` ALONE.  Installed to ``~/.codex/hooks`` / ``~/.cursor/
hooks`` — where that is ``$HOME``, not a checkout — the hook took its own
silent ``exit 0`` and captured nothing, yet ``tortoise session verify``
reported ``installed: PROVEN`` because the hook exited rc=0.  rc=0 is an
instrument reading, not the effect it was taken to prove.

These guards drive the REAL shipped hook and the REAL verifier; each docstring
names the mutation that turns it RED.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tortoise import hook_install as hi
from tortoise.capture_install import install_capture

REPO_ROOT = Path(__file__).resolve().parent.parent
CODEX_HOOK = REPO_ROOT / "tortoise" / "codex-hooks" / "session-end.sh"
CLAUDE_HOOK = REPO_ROOT / "tortoise" / "claude-hooks" / "session-end.sh"


def _fake_capture_bin(bindir: Path, log: Path) -> Path:
    """A `tortoise` that records its argv and appends DONE when it finishes."""
    bindir.mkdir(parents=True, exist_ok=True)
    script = bindir / "tortoise"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" >> {log}\n'
        f'echo DONE >> {log}\n',
        encoding="utf-8")
    script.chmod(0o755)
    return script


def _module_dir(root: Path) -> Path:
    """A directory that IS a valid module dir: it holds ``tortoise/``."""
    (root / "tortoise").mkdir(parents=True, exist_ok=True)
    (root / "tortoise" / "__init__.py").write_text("", encoding="utf-8")
    return root


def _write_claude_transcript(path: Path) -> Path:
    """One parseable Claude turn, so the hook reaches module resolution."""
    path.write_text(
        json.dumps({"type": "user",
                    "message": {"role": "user", "content": "hi"}}) + "\n",
        encoding="utf-8")
    return path


def _run_claude_hook(hook: Path, home: Path, *, transcript: Path,
                     path: str, extra_env: dict[str, str] | None = None,
                     cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Drive the real Claude session-end hook with a controlled env."""
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    # #3615: session-end.sh's capture step is consent-gated, and these tests
    # exercise the CAPTURE invocation (the resolved module dir, the CWE-427
    # path hygiene). Explicit consent keeps the capture leg live for them; no
    # test in this file pins the refusal path.
    env = {"HOME": str(home), "PATH": path, "TMPDIR": str(home / "tmp"),
           "TORTOISE_CAPTURE": "1"}
    env.update(extra_env or {})
    payload = json.dumps({
        "session_id": "sid-4314", "transcript_path": str(transcript),
        "cwd": str(home)})
    return subprocess.run(
        ["/bin/bash", str(hook)], input=payload, text=True,
        capture_output=True, env=env, cwd=str(cwd) if cwd else None,
        timeout=20)


def _run_codex_hook(hook: Path, home: Path, *, transcript: Path,
                    path: str) -> subprocess.CompletedProcess:
    """Drive the real hook with a controlled HOME and PATH."""
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    env = {"HOME": str(home), "PATH": path, "TMPDIR": str(home / "tmp")}
    payload = json.dumps({
        "session_id": "sid-4314", "transcript_path": str(transcript),
        "cwd": str(home), "hook_event_name": "SessionEnd", "reason": "other"})
    return subprocess.run(
        ["/bin/bash", str(hook)], input=payload, text=True,
        capture_output=True, env=env, timeout=20)


def _wait_for(predicate, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _breadcrumb(home: Path, harness: str) -> dict:
    """Wait for a COMPLETE breadcrumb (the writer truncates then writes) and
    return its parsed body."""
    path = home / ".tortoise" / "capture-errors" / f"{harness}.json"
    assert _wait_for(lambda: path.is_file()
                     and path.read_text(encoding="utf-8").strip() != ""), (
        "an inert install left no breadcrumb — the failure is invisible")
    return json.loads(path.read_text(encoding="utf-8"))


def _argv(log: Path) -> list[str]:
    return [t for t in log.read_text(encoding="utf-8").split() if t != "DONE"]


def test_hook_resolves_the_installer_recorded_src_dir(tmp_path):
    """An install under ``~/.codex/hooks`` has no ``../..`` checkout; the
    installer's recorded module dir is what lets it resolve the CLI.

    Mutation: drop the ``cat $HOME/.tortoise/hook-src-dir`` candidate from the
    shipped hook — the module dir stays unresolved, the worker records a
    breadcrumb instead of capturing, and the argv assertion REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hooks = home / ".codex" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "session-end.sh"
    shutil.copy(CODEX_HOOK, hook)
    hook.chmod(0o755)
    # `../..` from the installed position is $HOME — deliberately not a
    # checkout, which is the whole defect.
    assert not (home / "tortoise").exists()

    src = _module_dir(tmp_path / "srcmod")
    log = tmp_path / "argv.log"
    _fake_capture_bin(src / ".venv" / "bin", log)
    (home / ".tortoise").mkdir(parents=True, exist_ok=True)
    (home / ".tortoise" / "hook-src-dir").write_text(str(src) + "\n")

    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    proc = _run_codex_hook(hook, home, transcript=transcript,
                           path=f"{tmp_path / 'bin'}:/usr/bin:/bin")

    assert proc.returncode == 0, proc.stderr
    assert _wait_for(lambda: log.is_file()
                     and "DONE" in log.read_text(encoding="utf-8")), (
        "the recorded module dir did not resolve — the hook captured nothing")
    assert _argv(log)[:2] == ["sessions", "import"]


def test_hook_rejects_a_recorded_dir_without_the_package(tmp_path):
    """A recorded path that does not actually hold ``tortoise/`` must be
    REJECTED, and the ``../..`` fallback must then win.

    Mutation: drop the ``[ -d "$CANDIDATE/tortoise" ]`` test — the bogus
    recorded dir is accepted, ``python3 -m tortoise`` runs against a tree with
    no package, and the argv assertion REDs."""
    root = tmp_path / "checkout"
    (root / "tortoise" / "codex-hooks").mkdir(parents=True)
    hook = root / "tortoise" / "codex-hooks" / "session-end.sh"
    shutil.copy(CODEX_HOOK, hook)
    hook.chmod(0o755)
    _module_dir(root)  # root/tortoise exists, so `../..` = root is a checkout
    log = tmp_path / "argv.log"
    _fake_capture_bin(root / ".venv" / "bin", log)

    home = tmp_path / "home"
    home.mkdir()
    bogus = tmp_path / "not-a-module"
    bogus.mkdir()
    (home / ".tortoise").mkdir(parents=True, exist_ok=True)
    (home / ".tortoise" / "hook-src-dir").write_text(str(bogus) + "\n")

    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    proc = _run_codex_hook(hook, home, transcript=transcript,
                           path=f"{tmp_path / 'bin'}:/usr/bin:/bin")

    assert proc.returncode == 0, proc.stderr
    assert _wait_for(lambda: log.is_file()
                     and "DONE" in log.read_text(encoding="utf-8")), (
        "the ../.. fallback did not run after the bogus record was rejected")
    assert _argv(log)[:2] == ["sessions", "import"]


def test_hook_records_a_breadcrumb_when_nothing_resolves(tmp_path):
    """An install that can resolve neither a binary nor a module dir must
    leave EVIDENCE, not silence — and must still exit 0.

    Mutation: restore the old ``exit 0  # clean silence`` on the unresolved
    branch — no breadcrumb is written and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hooks = home / ".codex" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "session-end.sh"
    shutil.copy(CODEX_HOOK, hook)
    hook.chmod(0o755)
    assert not (home / "tortoise").exists()
    assert not (home / ".tortoise" / "hook-src-dir").exists()

    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    proc = _run_codex_hook(hook, home, transcript=transcript,
                           path=f"{tmp_path / 'bin'}:/usr/bin:/bin")

    assert proc.returncode == 0, "the fail-open exit-0 contract must hold"
    body = _breadcrumb(home, "codex")
    assert body["harness"] == "codex"
    assert body["kind"] == "install-inert", body
    assert "could not resolve" in body["detail"], body


def test_install_capture_records_the_module_dir(tmp_path):
    """The real install path records where the hooks came FROM.

    Mutation: drop the ``_record_hook_src_dir`` call from
    ``install_capture`` (or from ``upgrade_install``) — the record is absent
    and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    result = install_capture("codex", home=home)
    assert result.ok, result.error

    record = home / ".tortoise" / "hook-src-dir"
    assert record.is_file(), (
        "install_capture did not record the module dir it installed FROM")
    recorded = Path(record.read_text(encoding="utf-8").strip())
    assert (recorded / "tortoise").is_dir(), recorded


def test_verify_reports_inert_for_an_rc0_but_effectless_install(
        tmp_path, monkeypatch):
    """`installed` may only be PROVEN on a DOWNSTREAM EFFECT. A seam that
    fires rc=0 and files nothing must read INERT, never PROVEN.

    Mutation: restore ``STATUS_PROVEN`` immediately after ``fired.succeeded``
    — the INERT assertion REDs and an inert install reads as health again."""
    from tortoise import session_verify as sv

    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "proj"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    # The breadcrumb the inert hook leaves is written DURING the fire, with the
    # install-inert kind (P1-A) — a pre-seeded record would be cleared by the
    # before-fire clear (P1-B).
    _stub_verify_fire(sv, monkeypatch, home=home, kind="install-inert")

    report = sv.verify_session_capture(
        "claude", api_key="tt_test", api_url="http://127.0.0.1:1",
        home=home, install_dir=root, timeout=1.0,
        env={"HOME": str(home)})

    assert report["fire"]["returncode"] == 0, report["fire"]
    installed = report["links"]["installed"]
    assert installed["status"] == "INERT", installed
    assert installed["status"] != "PROVEN"
    assert installed["breadcrumb"]["harness"] == "claude", installed
    assert report["exit_code"] == sv.EXIT_BROKEN, report


def _stub_verify_fire(sv, monkeypatch, *, home: Path,
                      kind: str | None = None):
    """Make ``verify_session_capture`` run without a real seam or API.

    When ``kind`` is given, the stubbed fire WRITES that breadcrumb — as the
    real hook (``install-inert``) or a failed ``sessions import``
    (``capture-failure``) would DURING the fire.
    """
    def _fake_fire(*_a, **_k):
        if kind is not None:
            path = home / ".tortoise" / "capture-errors" / "claude.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "harness": "claude", "detail": f"stub {kind}",
                "kind": kind}), encoding="utf-8")
        return sv.FireResult(
            sv.LaunchOutcome.EXITED, "executed '…' (rc=0)", returncode=0)

    monkeypatch.setattr(sv, "_fire", _fake_fire)
    monkeypatch.setattr(sv, "_read_receipt", lambda *a, **k: None)
    monkeypatch.setattr(sv, "_session_detail", lambda *a, **k: None)

    def _api(*_a, **_k):
        raise sv._ApiError("no api (test)", status=404)

    monkeypatch.setattr(sv, "_api", _api)


def test_verify_ignores_a_capture_failure_breadcrumb(tmp_path, monkeypatch):
    """The SAME ``capture-errors`` file is written by TWO writers, and the
    install leg must key on the install-inert marker: a ``sessions import``
    capture failure (an API outage) must read PROVEN, never INERT.

    Mutation: drop the ``kind == KIND_INSTALL_INERT`` test in ``_install_link``
    — the capture-failure record is accepted as install-inert evidence and the
    PROVEN assertion REDs (the exact defect P1-A)."""
    from tortoise import session_verify as sv

    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "proj"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok

    crumbs = home / ".tortoise" / "capture-errors"
    crumbs.mkdir(parents=True)
    _stub_verify_fire(sv, monkeypatch, home=home, kind="capture-failure")
    report = sv.verify_session_capture(
        "claude", api_key="tt_test", api_url="http://127.0.0.1:1",
        home=home, install_dir=root, timeout=1.0,
        env={"HOME": str(home)})

    installed = report["links"]["installed"]
    assert installed["status"] == "PROVEN", installed


def test_verify_clears_a_stale_install_inert_breadcrumb(tmp_path, monkeypatch):
    """A hook that was inert ONCE must not fail every later verify forever:
    the install-inert evidence is cleared immediately BEFORE the fire, so only
    a breadcrumb THIS fire produced can be read.

    Mutation: drop the ``_clear_install_inert_breadcrumb`` call in
    ``verify_session_capture`` — the stale record is read after a fire that
    wrote nothing and ``installed`` reads INERT, REDding this."""
    from tortoise import session_verify as sv

    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "proj"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok

    crumbs = home / ".tortoise" / "capture-errors"
    crumbs.mkdir(parents=True)
    (crumbs / "claude.json").write_text(json.dumps({
        "harness": "claude", "detail": "stale — a previous fire was inert",
        "kind": "install-inert",
        "recorded_at": "2026-09-19T00:00:00Z"}), encoding="utf-8")

    _stub_verify_fire(sv, monkeypatch, home=home)
    report = sv.verify_session_capture(
        "claude", api_key="tt_test", api_url="http://127.0.0.1:1",
        home=home, install_dir=root, timeout=1.0,
        env={"HOME": str(home)})

    installed = report["links"]["installed"]
    assert installed["status"] == "PROVEN", installed
    assert not (crumbs / "claude.json").exists(), (
        "the stale install-inert record survived the fire")


def test_local_capture_error_resolves_from_the_fire_env(tmp_path, monkeypatch):
    """P2.2: the breadcrumb is read under the env the hook was FIRED with,
    not this process's ``os.environ`` — a verify run with a non-default HOME
    must read what the hook wrote under that HOME.

    Mutation: restore ``_local_capture_error`` to read ``os.environ``/
    ``Path.home()`` — it resolves the process home instead of the fire env and
    this REDs."""
    from tortoise import session_verify as sv

    process_home = tmp_path / "process-home"
    process_home.mkdir()
    fire_home = tmp_path / "fire-home"
    fire_home.mkdir()
    monkeypatch.setenv("HOME", str(process_home))
    monkeypatch.delenv("TORTOISE_IMPORT_RECEIPT_DIR", raising=False)

    path = sv._local_capture_error_file("claude", {"HOME": str(fire_home)})
    assert path == (fire_home / ".tortoise" / "capture-errors" / "claude.json"), path
    assert str(process_home) not in str(path), path

    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "harness": "claude", "detail": "inert",
        "kind": "install-inert"}), encoding="utf-8")
    found = sv._local_capture_error("claude", {"HOME": str(fire_home)})
    assert found is not None and found["kind"] == "install-inert", found
    # A different fire HOME sees nothing.
    assert sv._local_capture_error(
        "claude", {"HOME": str(process_home)}) is None


def test_hook_src_dir_record_never_writes_the_real_home(tmp_path, monkeypatch):
    """The record must never land on the developer's machine from a test.

    #3721's trap: an earlier cut of #4314 used ``Path.home()`` unconditionally
    and a single test run created ``~/.tortoise/hook-src-dir`` for real.
    The fail-safe is keyed on a HOME-scoped home NOT having been resolved
    (``_record_hook_src_dir`` only records for a resolved HOME-scoped root),
    with the pytest-derived base as the last-resort guard.

    Mutation: revert ``_hook_src_dir_base`` to ``Path.home()`` → RED.
    LEGITIMATE GREEN: an explicit ``home`` is honoured verbatim, which the
    installer relies on when it has resolved a harness home.
    """
    assert os.environ.get("PYTEST_CURRENT_TEST"), "not running under pytest"
    real = Path.home()
    derived = hi._hook_src_dir_base(None)
    assert derived != real and real not in derived.parents, derived
    assert "tortoise-hook-src-tests" in str(derived), derived
    # PYTEST_CURRENT_TEST carries the phase; one test must map to ONE dir.
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/x.py::test_y (call)")
    call_dir = hi._hook_src_dir_base(None)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/x.py::test_y (setup)")
    assert hi._hook_src_dir_base(None) == call_dir
    explicit = tmp_path / "explicit-home"
    assert hi._hook_src_dir_base(explicit) == explicit


def test_claude_hook_records_a_breadcrumb_when_nothing_resolves(tmp_path):
    """The CLAUDE capture seam is the seam the installer's record feeds: an
    install under ``~/.claude/hooks`` whose ``../..`` is ``$HOME`` (not a
    checkout) and with no installer record must leave EVIDENCE and exit 0.

    Mutation: drop the ``cat $HOME/.tortoise/hook-src-dir`` candidate (or the
    breadcrumb) from claude ``session-end.sh`` — the seam exits 0 in silence
    and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hooks = home / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "session-end.sh"
    shutil.copy(CLAUDE_HOOK, hook)
    hook.chmod(0o755)
    assert not (home / "tortoise").exists()
    assert not (home / ".tortoise" / "hook-src-dir").exists()

    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = _run_claude_hook(hook, home, transcript=transcript,
                            path=f"{tmp_path / 'bin'}:/usr/bin:/bin")

    assert proc.returncode == 0, proc.stderr
    body = _breadcrumb(home, "claude")
    assert body["harness"] == "claude"
    assert body["kind"] == "install-inert", body
    assert "could not resolve" in body["detail"], body


def test_claude_hook_resolves_the_installer_recorded_src_dir(tmp_path):
    """The claude hook reads the installer's record via the SAME candidate
    loop the codex/cursor hooks use. The fake interpreter logs the module dir
    the hook prepended, proving the record (not ``../..``) won.

    Mutation: drop the record candidate from claude ``session-end.sh`` — the
    module stays unresolved, the fake interpreter is never called, and this
    REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hooks = home / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "session-end.sh"
    shutil.copy(CLAUDE_HOOK, hook)
    hook.chmod(0o755)
    assert not (home / "tortoise").exists()

    src = _module_dir(tmp_path / "srcmod")
    log = tmp_path / "python.log"
    fake_py = tmp_path / "bin" / "fakepython"
    fake_py.parent.mkdir(parents=True, exist_ok=True)
    fake_py.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "MODULE=$TORTOISE_MODULE_DIR" >> {log}\n'
        f'echo "ARGV=$*" >> {log}\n'
        f'echo DONE >> {log}\n',
        encoding="utf-8")
    fake_py.chmod(0o755)
    (home / ".tortoise").mkdir(parents=True, exist_ok=True)
    (home / ".tortoise" / "hook-src-dir").write_text(str(src) + "\n")

    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = _run_claude_hook(
        hook, home, transcript=transcript,
        path=f"{tmp_path / 'bin'}:/usr/bin:/bin",
        extra_env={"PYTHON_BIN": str(fake_py)})

    assert proc.returncode == 0, proc.stderr
    assert log.is_file() and "DONE" in log.read_text(encoding="utf-8"), (
        "the recorded module dir did not resolve — the hook captured nothing")
    text = log.read_text(encoding="utf-8")
    # The module dir travels as argv[1] (never via ``-m``/PYTHONPATH); its
    # presence in the logged argv proves it was the RECORD, not ``../..``
    # (which is $HOME here and holds no checkout).
    assert str(src) in text, text
    assert '"capture"' in text, text


def test_hook_never_executes_a_tortoise_package_in_the_cwd(tmp_path):
    """CWE-427: the resolved module dir is prepended INSIDE ``-c``, never
    reached via ``-m`` — for which CPython puts the process CWD AHEAD of
    PYTHONPATH, executing a ``tortoise/`` package planted in the agent's
    workspace (whose stdout is injected into the model context).

    Mutation: restore ``PYTHONPATH=… python -m tortoise`` — the planted
    package's ``__init__`` runs, the sentinel appears, and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hooks = home / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "session-end.sh"
    shutil.copy(CLAUDE_HOOK, hook)
    hook.chmod(0o755)

    src = tmp_path / "srcmod"
    (src / "tortoise").mkdir(parents=True)
    (src / "tortoise" / "__init__.py").write_text("", encoding="utf-8")
    (src / "tortoise" / "__main__.py").write_text(
        "import os\n"
        "def main(argv):\n"
        "    with open(os.environ['LEGIT_LOG'], 'a') as f:\n"
        "        f.write(' '.join(argv) + '\\n')\n"
        "    return 0\n",
        encoding="utf-8")

    workdir = tmp_path / "workdir"
    (workdir / "tortoise").mkdir(parents=True)
    (workdir / "tortoise" / "__init__.py").write_text(
        "import os\n"
        "open(os.environ['SENTINEL'], 'w').write('pwned')\n",
        encoding="utf-8")

    (home / ".tortoise").mkdir(parents=True, exist_ok=True)
    (home / ".tortoise" / "hook-src-dir").write_text(str(src) + "\n")

    legit = tmp_path / "legit.log"
    sentinel = tmp_path / "sentinel"
    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = _run_claude_hook(
        hook, home, transcript=transcript,
        path=f"{tmp_path / 'bin'}:/usr/bin:/bin",
        extra_env={"PYTHON_BIN": sys.executable,
                   "LEGIT_LOG": str(legit), "SENTINEL": str(sentinel)},
        cwd=workdir)

    assert proc.returncode == 0, proc.stderr
    assert _wait_for(lambda: legit.is_file()
                     and "capture" in legit.read_text(encoding="utf-8")), (
        "the resolved module never ran — the test proved nothing")
    assert not sentinel.exists(), (
        "a tortoise/ package in $PWD executed — the cwd is attacker-influenced")


def test_verify_reads_inert_when_the_hook_cannot_resolve(tmp_path, monkeypatch):
    """With ``TORTOISE_SRC_DIR`` no longer seeded, verify exercises the
    install's OWN resolution: no record, no ``tortoise`` on PATH, and a
    ``../..`` that is not a checkout leave the hook inert, and ``installed``
    reads INERT from the hook's own breadcrumb.

    Mutation: re-seed ``TORTOISE_SRC_DIR`` in ``_fire_env`` — the hook
    resolves the real package, files no breadcrumb, and ``installed`` reads
    PROVEN, REDding this test."""
    from tortoise import session_verify as sv

    home = tmp_path / "home"
    home.mkdir()
    root = home / "proj"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok
    # The installer recorded where the hooks came FROM; remove it so the
    # resolution genuinely fails, as it does for a home-scoped install whose
    # record was never written.
    (home / ".tortoise" / "hook-src-dir").unlink()

    receipts = tmp_path / "receipts"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(receipts))
    monkeypatch.setattr(sv, "_read_receipt", lambda *a, **k: None)
    monkeypatch.setattr(sv, "_session_detail", lambda *a, **k: None)

    def _api(*_a, **_k):
        raise sv._ApiError("no api (test)", status=404)

    monkeypatch.setattr(sv, "_api", _api)

    report = sv.verify_session_capture(
        "claude", api_key="tt_test", api_url="http://127.0.0.1:1",
        home=home, install_dir=root, timeout=20.0,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin",
             "TORTOISE_IMPORT_RECEIPT_DIR": str(receipts)})

    assert report["fire"]["returncode"] == 0, report["fire"]
    installed = report["links"]["installed"]
    assert installed["status"] == "INERT", installed
    assert installed["breadcrumb"]["harness"] == "claude", installed
    assert report["exit_code"] == sv.EXIT_BROKEN, report


def _upgrade_root(harness: str, root: Path) -> None:
    """Create a minimal, upgradeable install at ``root`` for ``harness``."""
    layout = hi.get_layout(harness)
    hooks = layout.hooks_root(root)
    hooks.mkdir(parents=True, exist_ok=True)
    for spec in layout.scripts:
        (hooks / spec.name).write_text(
            f"# {hi.HOOK_VERSION_TOKEN}: 0\n# tortoise\n", encoding="utf-8")


def test_upgrade_records_only_when_the_hook_cannot_resolve(
        tmp_path, monkeypatch):
    """The write condition is the READ condition: the record is written iff
    the installed hook's own ``../..`` does not hold a ``tortoise/`` package.

    * a repo-scoped ``--dir`` whose ``../..`` IS a checkout -> nothing (#4110);
    * a Claude project install whose ``../..`` is NOT a checkout -> written
      (the documented `tortoise hooks upgrade --dir` repair path, P1-C);
    * a HOME-scoped Codex root (``~/.codex``, ``../..`` is ``$HOME``) ->
      written.

    Mutation: gate the write on the layout being HOME-scoped (the old
    ``_is_home_scoped_root``) — the Claude project install records nothing and
    this REDs."""
    monkeypatch.delenv("CODEX_HOME", raising=False)
    calls: list = []
    monkeypatch.setattr(
        hi, "_record_hook_src_dir", lambda home=None: calls.append(home))

    # (1) repo-scoped --dir whose ../.. IS a checkout: writes nothing.
    checkout = tmp_path / "checkout"
    (checkout / "tortoise").mkdir(parents=True)
    _upgrade_root("claude", checkout)
    result = hi.upgrade_install(checkout, "claude", home=tmp_path / "home")
    assert result.ok, result.refused
    assert calls == [], "a --dir upgrade whose ../.. is a checkout wrote HOME state"

    # (2) Claude project install whose ../.. is NOT a checkout: the repair
    # path (tortoise/__main__.py prints `tortoise hooks upgrade --dir {root}`)
    # MUST write the record or the hook stays inert.
    project = tmp_path / "project"
    _upgrade_root("claude", project)
    result = hi.upgrade_install(project, "claude", home=tmp_path / "home")
    assert result.ok, result.refused
    assert calls == [tmp_path / "home"], calls

    # (3) HOME-scoped Codex root: ../.. is $HOME, not a checkout.
    calls.clear()
    home = tmp_path / "home"
    home.mkdir()
    codex_root = hi.default_root(hi.get_layout("codex"), home)
    result = hi.upgrade_install(codex_root, "codex", home=home)
    assert result.ok, result.refused
    assert calls == [home], calls


def test_install_capture_and_upgrade_share_the_need_rule(
        tmp_path, monkeypatch):
    """P2.4: the sibling ``install_capture`` call site uses the SAME shared
    need-based helper as ``upgrade_install`` — a Claude project install whose
    ``../..`` is not a checkout writes the record; one whose ``../..`` IS a
    checkout writes nothing.

    Mutation: restore the unconditional
    ``_record_hook_src_dir_best_effort`` at the end of ``install_capture`` —
    the checkout case writes and the assertion REDs."""
    calls: list = []
    monkeypatch.setattr(
        hi, "_record_hook_src_dir", lambda home=None: calls.append(home))

    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    result = install_capture("claude", root=project, home=home)
    assert result.ok, result.error
    assert calls == [home], (
        "a Claude project install whose ../.. is not a checkout recorded nothing")

    calls.clear()
    checkout = tmp_path / "checkout"
    (checkout / "tortoise").mkdir(parents=True)
    result = install_capture("claude", root=checkout, home=home)
    assert result.ok, result.error
    assert calls == [], (
        "a repo-scoped install whose ../.. is a checkout wrote HOME state")


def test_hook_never_imports_a_planted_prepath_os_module(tmp_path):
    """CWE-427, the narrower vector: ``python -c`` puts the process cwd at
    ``sys.path[0]``, so a planted ``./os.py`` in the agent workspace must not
    execute. The bootstrap drops cwd from ``sys.path`` BEFORE importing any
    non-builtin module. ``-S`` is used so the interpreter does not preload
    ``os`` — otherwise the vector is invisible on modern CPython.

    Mutation: restore ``import os, sys; sys.path.insert(0,
    os.environ["TORTOISE_MODULE_DIR"])`` — the planted ``./os.py`` executes and
    the sentinel assertion REDs."""
    import shlex

    home = tmp_path / "home"
    home.mkdir()
    hooks = home / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "session-end.sh"
    shutil.copy(CLAUDE_HOOK, hook)
    hook.chmod(0o755)

    src = _module_dir(tmp_path / "srcmod")
    (src / "tortoise" / "__main__.py").write_text(
        "import os\n"
        "def main(argv):\n"
        "    with open(os.environ['LEGIT_LOG'], 'a') as f:\n"
        "        f.write(' '.join(argv) + '\\n')\n"
        "    return 0\n",
        encoding="utf-8")

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    # ``os`` is shadowed by the planted module, so the module cannot use
    # ``os.environ`` — it writes a fixed cwd-relative marker instead.
    sentinel = workdir / "PLANTED-OS-EXECUTED"
    (workdir / "os.py").write_text(
        "import sys\n"
        "open('PLANTED-OS-EXECUTED', 'w').write('pwned')\n",
        encoding="utf-8")

    # The documented fallback is the system python3 3.9.x, whose ``os`` is NOT
    # frozen — i.e. the interpreter on which the vector is real.  On an
    # interpreter that freezes ``os`` the vector cannot be reproduced, so the
    # guard is honestly skipped rather than passing vacuously.
    system_py = "/usr/bin/python3"
    if not Path(system_py).exists():
        pytest.skip("no /usr/bin/python3 fallback interpreter")
    subprocess.run(
        [system_py, "-S", "-c", "import os"], cwd=workdir,
        capture_output=True)
    vulnerable = sentinel.exists()
    if vulnerable:
        sentinel.unlink()
    if not vulnerable:
        pytest.skip("fallback interpreter freezes os — vector not reproducible")

    # An interpreter that does NOT preload ``os`` (no site), so the planted
    # ./os.py is reachable exactly as it is for the shipped fallback.
    wrapper = tmp_path / "bin" / "pyS"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f"exec {shlex.quote(system_py)} -S \"$@\"\n",
        encoding="utf-8")
    wrapper.chmod(0o755)

    (home / ".tortoise").mkdir(parents=True, exist_ok=True)
    (home / ".tortoise" / "hook-src-dir").write_text(str(src) + "\n")

    legit = tmp_path / "legit.log"
    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = _run_claude_hook(
        hook, home, transcript=transcript,
        path=f"{tmp_path / 'bin'}:/usr/bin:/bin",
        extra_env={"PYTHON_BIN": str(wrapper), "LEGIT_LOG": str(legit)},
        cwd=workdir)

    assert proc.returncode == 0, proc.stderr
    assert _wait_for(lambda: legit.is_file()
                     and "capture" in legit.read_text(encoding="utf-8")), (
        "the resolved module never ran — the test proved nothing")
    assert not sentinel.exists(), (
        "a planted ./os.py in $PWD executed — the cwd is attacker-influenced")


VOLUNTEER_HOOK = (REPO_ROOT / "tortoise" / "claude-hooks"
                  / "volunteer-turn.sh")


def test_no_interpreter_breadcrumb_is_written_without_python3(tmp_path):
    """P2.1: the breadcrumb writer is PURE SHELL. The "resolved a module dir but
    found no interpreter" branch is reached BECAUSE python3 is missing, so a
    python3-written breadcrumb could never run there.

    Mutation: restore the ``python3 - <<'PY'`` heredoc in ``_record_breadcrumb``
    — with no python3 on PATH nothing is written and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hooks = home / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "volunteer-turn.sh"
    shutil.copy(VOLUNTEER_HOOK, hook)
    hook.chmod(0o755)

    # A module dir that resolves, with NO interpreter and NO tortoise binary.
    src = _module_dir(tmp_path / "srcmod")

    # A PATH with the shell plumbing the hook needs but NO python3.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("cat", "tr", "head", "mkdir", "date", "dirname"):
        real = shutil.which(tool)
        assert real, tool
        (bindir / tool).symlink_to(real)

    env = {"HOME": str(home), "PATH": str(bindir), "TMPDIR": str(tmp_path),
           "TORTOISE_SRC_DIR": str(src)}
    proc = subprocess.run(
        ["/bin/bash", str(hook), "codex"], input="a real prompt\n",
        text=True, capture_output=True, env=env, cwd=str(home), timeout=20)

    assert proc.returncode == 0, proc.stderr
    body = _breadcrumb(home, "codex")
    assert body["kind"] == "install-inert", body
    assert "no python3 interpreter" in body["detail"], body


@pytest.mark.parametrize("suffix", ["", "/", "///"])
def test_breadcrumb_dir_agrees_with_verify_for_a_trailing_slash(
        tmp_path, suffix):
    """The hook writes the install-inert breadcrumb with SHELL string surgery
    (``${receipt_dir%/*}``) while ``session verify`` reads it with pathlib's
    ``.parent``. Those two disagree on a TRAILING SLASH — ``Path('…/r/').parent``
    is ``…``, but ``${'…/r/'%/*}`` is ``…/r`` — so an inert install whose
    ``TORTOISE_IMPORT_RECEIPT_DIR`` ends in ``/`` writes a breadcrumb verify
    never looks at and reads **PROVEN**. That is the exact false-PROVEN this
    seam exists to remove (#4314), so the two derivations are pinned EQUAL here
    rather than merely both-existing.

    Mutation: drop the trailing-slash normalization from the hook's
    ``_record_breadcrumb`` — the shell writes ``…/receipts/capture-errors/``
    while ``_local_capture_error_file`` resolves ``…/capture-errors/``, and
    this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hooks = home / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "session-end.sh"
    shutil.copy(CLAUDE_HOOK, hook)
    hook.chmod(0o755)

    receipt_dir = str(tmp_path / "receipts") + suffix
    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = _run_claude_hook(hook, home, transcript=transcript,
                            path=f"{tmp_path / 'bin'}:/usr/bin:/bin",
                            extra_env={"TORTOISE_IMPORT_RECEIPT_DIR":
                                       receipt_dir})
    assert proc.returncode == 0, proc.stderr

    # What `session verify` will actually look for, under the hook's env.
    from tortoise import session_verify
    looked_for = session_verify._local_capture_error_file(
        "claude", {"HOME": str(home),
                   "TORTOISE_IMPORT_RECEIPT_DIR": receipt_dir})
    assert _wait_for(lambda: looked_for.is_file()), (
        f"the hook's breadcrumb is invisible to verify for "
        f"TORTOISE_IMPORT_RECEIPT_DIR={receipt_dir!r}: verify reads "
        f"{looked_for} but the hook wrote "
        f"{sorted(str(p) for p in tmp_path.rglob('*.json'))}")
    body = json.loads(looked_for.read_text(encoding="utf-8"))
    assert body["kind"] == "install-inert", body


_HOOK_SCRIPTS = sorted(
    list((REPO_ROOT / "tortoise" / "claude-hooks").glob("*.sh"))
    + list((REPO_ROOT / "tortoise" / "codex-hooks").glob("*.sh"))
    + list((REPO_ROOT / "tortoise" / "cursor-hooks").glob("*.sh")))


def _hook_id(p: Path) -> str:
    """Unique per file: three hooks are all named ``session-end.sh``."""
    return f"{p.parent.name}/{p.name}"


def _double_quoted_python_regions(text: str) -> list[str]:
    """The body of every ``-c "…"`` in ``text`` (a DOUBLE-quoted shell string,
    where the shell would substitute before Python ever sees it)."""
    return [m.group(1) for m in
            re.finditer(r'-c\s+"\n(.*?)\n"', text, re.S)]


@pytest.mark.parametrize("hook", _HOOK_SCRIPTS, ids=_hook_id)
def test_double_quoted_python_blocks_are_shell_safe(hook):
    """A ``-c "…"`` block embeds Python inside a DOUBLE-quoted shell string, so
    the shell processes it first. Two things then silently MANGE the Python,
    and neither is visible to ``bash -n``:

    * a ``"`` anywhere in the source **closes the shell string** — so
      ``p not in ("", ".")`` reaches the interpreter as ``p not in (, .)``, a
      ``SyntaxError``;
    * a backtick in a *comment* is **command-substituted**, so the shell runs
      that text and splices its output into the source.

    Both were introduced while fixing #4314 and both were silent. The first
    made ``SWEEP_CORPUS`` always empty, so a configured
    ``TORTOISE_SESSION_CORPUS`` was ignored and the sweep fell back to the
    default corpus — the exact divergence the block above it documents. The
    second ran three bogus commands on every prompt.

    Mutation: add a ``"`` or a backtick to any ``-c "`` block in these hooks
    and this REDs with the offending file."""
    text = hook.read_text(encoding="utf-8")
    for i, region in enumerate(_double_quoted_python_regions(text)):
        assert '"' not in region, (
            f"{hook.name}: a double quote in -c \" block #{i} closes the shell "
            f"string and mangles the Python — use a single-quoted block, or a "
            f"quote-free expression. Region:\n{region}")
        assert "`" not in region, (
            f"{hook.name}: a backtick in -c \" block #{i} is command-"
            f"substituted by the shell and its output spliced into the Python. "
            f"Region:\n{region}")
        # The region as it appears in the file is NOT what the interpreter
        # receives, so compile it the way Python will see it (the shell consumes
        # the delimiting quotes; ``-c`` strips nothing else once they are gone).
        compile(region, f"{hook.name}#{i}", "exec")


def test_the_double_quoted_block_extractor_is_not_vacuous():
    """The per-hook guard above asserts nothing if the extractor matches no
    block, so pin that it actually finds the hooks' embedded Python. A stale
    regex would silently retire the guard."""
    total = sum(len(_double_quoted_python_regions(
        h.read_text(encoding="utf-8"))) for h in _HOOK_SCRIPTS)
    assert total >= 2, (
        f"the -c \" extractor found only {total} block(s) across the shipped "
        f"hooks — it has gone stale and the shell-safety guard is vacuous. "
        f"(Only ``volunteer-turn.sh`` currently uses a double-quoted block; "
        f"the rest are single-quoted or heredocs, so this floor is small on "
        f"purpose and still catches a regex that stopped matching.)")
