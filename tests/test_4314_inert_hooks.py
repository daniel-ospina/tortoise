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
import shutil
import subprocess
import time
from pathlib import Path

from tortoise.capture_install import install_capture

REPO_ROOT = Path(__file__).resolve().parent.parent
CODEX_HOOK = REPO_ROOT / "tortoise" / "codex-hooks" / "session-end.sh"


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
    breadcrumb = home / ".tortoise" / "capture-errors" / "codex.json"
    assert _wait_for(breadcrumb.is_file), (
        "an inert install left no breadcrumb — the failure is invisible")
    body = json.loads(breadcrumb.read_text(encoding="utf-8"))
    assert body["harness"] == "codex"
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
    # The breadcrumb the inert hook itself would have left.
    crumbs = tmp_path / "capture-errors"
    crumbs.mkdir()
    (crumbs / "claude.json").write_text(json.dumps({
        "harness": "claude", "detail": "could not resolve a tortoise module dir",
        "recorded_at": "2026-09-19T00:00:00Z"}), encoding="utf-8")

    monkeypatch.setattr(
        sv, "_fire",
        lambda *a, **k: sv.FireResult(
            sv.LaunchOutcome.EXITED, "executed '…' (rc=0)", returncode=0))
    monkeypatch.setattr(sv, "_read_receipt", lambda *a, **k: None)
    monkeypatch.setattr(sv, "_session_detail", lambda *a, **k: None)

    def _api(*_a, **_k):
        raise sv._ApiError("no api (test)", status=404)

    monkeypatch.setattr(sv, "_api", _api)

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
