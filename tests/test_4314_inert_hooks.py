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
import shlex
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
    """Wait for a COMPLETE install-inert breadcrumb and return its parsed body.

    #5838: ``install-inert`` has its OWN slot (``<harness>-install.json``),
    separate from the ``capture-failure`` slot — this helper is used only for
    the inert writers, so it reads the install slot.
    """
    path = (home / ".tortoise" / "capture-errors"
            / f"{harness}-install.json")
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
            # #5838: route by kind to the slot the shipped writer uses.
            suffix = "-install" if kind == "install-inert" else ""
            path = (home / ".tortoise" / "capture-errors"
                    / f"claude{suffix}.json")
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
    """The install leg must key on the install-inert marker.  #5838 moved the
    LIVE install-inert record to its OWN slot, so a ``sessions import`` capture
    failure (an API outage) is never even on the install leg's path — and the
    ``kind`` gate still refuses a capture-failure-SHAPED record dropped in the
    install slot (a legacy single-slot install, or a foreign file).

    Mutation: drop the ``kind == KIND_INSTALL_INERT`` test in ``_install_link``
    — the capture-failure-shaped record is accepted as install-inert evidence
    and the PROVEN assertion REDs (the exact defect P1-A)."""
    from tortoise import session_verify as sv

    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "proj"
    root.mkdir()
    assert install_capture("claude", root=root, home=home).ok

    crumbs = home / ".tortoise" / "capture-errors"
    crumbs.mkdir(parents=True)
    # A capture-failure-shaped record at the INSTALL slot is foreign evidence
    # (the writer puts that kind in the capture slot); the kind gate must
    # refuse it.  It also survives the before-fire clear, which unlinks only an
    # install-inert record.
    (crumbs / "claude-install.json").write_text(json.dumps({
        "harness": "claude", "detail": "stub capture-failure",
        "kind": "capture-failure"}), encoding="utf-8")

    _stub_verify_fire(sv, monkeypatch, home=home)
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
    (crumbs / "claude-install.json").write_text(json.dumps({
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
    assert not (crumbs / "claude-install.json").exists(), (
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
    assert path == (fire_home / ".tortoise" / "capture-errors"
                    / "claude-install.json"), path
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


# ── #4848: existence is not usability ────────────────────────────────────
#
# A capture seam accepted "a directory that holds `tortoise/`" as proof the
# tree was RUNNABLE, then ran it under an interpreter it never version-checked.
# On a stock macOS host `python3` is 3.9 while the package needs 3.12+, so a
# checkout with no `.venv` degraded to `ImportError: cannot import name 'UTC'
# from 'datetime'` at rc 0 — silent, and misattributed. These guards drive the
# REAL shipped seams and the REAL guard block; each names its mutation.

_GUARD_BEGIN = "# BEGIN tortoise-interp-guard (#4848)"
_GUARD_END = "# END tortoise-interp-guard (#4848)"


def _guard_block(path: Path) -> str:
    """The sentinel-delimited guard block of a shipped seam, verbatim."""
    text = path.read_text(encoding="utf-8")
    start = text.index(_GUARD_BEGIN)
    end = text.index(_GUARD_END) + len(_GUARD_END)
    return text[start:end]


def _old_python_shim(bindir: Path, *, version: str = "3.9.6") -> Path:
    """A ``python3`` that is TOO OLD for the package.

    Faithful where it matters and transparent everywhere else: it answers the
    guard's version probe with "no", reports ``version`` when asked what it is,
    and delegates every other call to this test process's interpreter (a real
    3.9 cannot parse what a 3.12 needs, and this shim only has to be
    *unusable*, not a python of its own).
    """
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "python3"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do\n'
        "  case \"$a\" in\n"
        "    *version_info*) exit 1 ;;\n"
        f"    *platform.python_version*) printf '%s\\n' '{version}'; exit 0 ;;\n"
        "  esac\n"
        "done\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _logging_capture_bin(venv_bin: Path, log: Path) -> Path:
    """A checkout's own ``tortoise`` console script: logs its path + argv.

    A ``#!/usr/bin/env bash`` shebang is deliberately NOT python — the guard
    judges what it can see, and an unknown interpreter is never refused.
    """
    venv_bin.mkdir(parents=True, exist_ok=True)
    script = venv_bin / "tortoise"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "SELF=%s\\n" "$0" >> {log}\n'
        f'printf "%s\\n" "$@" >> {log}\n'
        f'echo DONE >> {log}\n',
        encoding="utf-8")
    script.chmod(0o755)
    return script


def _coreutils(bindir: Path) -> None:
    """The shell plumbing a seam needs, plus nothing that could pass for an
    interpreter: ``python3`` is planted by the caller."""
    bindir.mkdir(parents=True, exist_ok=True)
    for tool in ("cat", "tr", "head", "mkdir", "date", "dirname", "sed",
                 "basename", "rm", "env", "mktemp", "sleep", "awk", "grep",
                 "cut", "nohup", "sort", "uniq", "wc", "ls", "pwd", "chmod",
                 "cp", "mv", "bash"):
        real = shutil.which(tool)
        if real:
            (bindir / tool).symlink_to(real)


def test_the_interp_guard_block_is_byte_identical_across_the_seams():
    """#4848 shipped ONE guard block, copied into every seam that resolves an
    interpreter, because the plan's rejected alternative was a sourced shared
    helper (an install-surface change: nothing in these hooks sources anything,
    and each is installed as an independent artifact with its own
    ``tortoise-hook-version`` marker).

    A copy that drifts is the defect. `test_cursor_capture_hook.py`'s warning —
    "a one-sided fix is the same class of defect as this one" — is why this pin
    exists: it fails the moment one seam is fixed and another is not.

    Mutation: edit one copy (a comment, a probe, the floor) — this REDs with
    the two files that disagree."""
    blocks = {_hook_id(p): _guard_block(p) for p in _HOOK_SCRIPTS}
    assert len(blocks) == len(_HOOK_SCRIPTS), blocks.keys()
    distinct = set(blocks.values())
    assert len(distinct) == 1, (
        "the #4848 interpreter guard has DRIFTED between seams — the copies "
        "must be byte-identical:\n"
        + "\n".join(f"  {name}: {len(b)} bytes"
                    for name, b in blocks.items()))
    block = distinct.pop()
    # The floor must travel via ONE variable, so the probe and every refusal
    # text move together (a hard-coded floor in a seam is what re-stales).
    assert block.count('_TORTOISE_MIN_PY="') == 1, block
    for fn in ("_tortoise_py_qualifies", "_tortoise_script_interp",
               "_tortoise_bin_qualifies", "_tortoise_dir_python",
               "_tortoise_dir_runnable", "_tortoise_json_escape",
               "_tortoise_unusable_reason"):
        assert f"{fn}() {{" in block, fn


@pytest.mark.parametrize("hook", _HOOK_SCRIPTS, ids=_hook_id)
def test_every_seam_declares_the_guard_exactly_once(hook):
    """Exactly one block per seam, at column 0 and OUTSIDE the worker branch:
    the synchronous half of a detached seam fires far more often than the
    worker, and a block defined only inside ``if --worker`` would be undefined
    on the branch that refuses."""

    text = hook.read_text(encoding="utf-8")
    assert text.count(_GUARD_BEGIN) == 1, hook
    assert text.count(_GUARD_END) == 1, hook
    for line in ("_tortoise_py_qualifies() {", "_tortoise_dir_runnable() {",
                 "_tortoise_dir_python() {", "_tortoise_json_escape() {",
                 "_tortoise_unusable_reason() {"):
        starts = [ln for ln in text.splitlines() if ln.startswith(line)]
        assert len(starts) == 1, (hook.name, line)


def test_the_guard_refuses_an_interpreter_that_is_too_old(tmp_path):
    """The predicate itself, driven as the seams drive it: a 3.9 answers the
    probe with "no", a 3.12 with "yes", an absent interpreter is a REFUSAL (not
    a pass), and a console script whose `#!` names a missing interpreter is
    refused while a non-python shebang is left alone.

    Mutation: make the probe ``[ -x "$1" ]`` (existence, not version) — this is
    exactly the shipped defect, and every assertion below REDs."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    old = _old_python_shim(bindir)
    block = _guard_block(CODEX_HOOK)
    script = tmp_path / "guard.sh"
    script.write_text(block + "\n" + f'''
_tortoise_py_qualifies "{old}" && echo "old:qualifies" || echo "old:refused"
_tortoise_py_qualifies "{sys.executable}" \\
  && echo "new:qualifies" || echo "new:refused"
_tortoise_py_qualifies "" && echo "empty:qualifies" || echo "empty:refused"
_tortoise_py_qualifies "/nonexistent/python3" \\
  && echo "absent:qualifies" || echo "absent:refused"
dead="$(mktemp -d)/tortoise"
printf '#!/nonexistent/python3\\n' > "$dead"
chmod +x "$dead"
_tortoise_bin_qualifies "$dead" && echo "deadshebang:qualifies" \\
  || echo "deadshebang:refused"
sh="$(mktemp -d)/tortoise"
printf '#!/bin/sh\\nexit 0\\n' > "$sh"
chmod +x "$sh"
_tortoise_bin_qualifies "$sh" && echo "shshebang:qualifies" \\
  || echo "shshebang:refused"
reason="$(_tortoise_unusable_reason "")"
case "$reason" in
  *"3.9.6"*"tortoise needs Python"*) echo "reason:names-version" ;;
  *) echo "reason:$reason" ;;
esac
''', encoding="utf-8")
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path),
           "TMPDIR": str(tmp_path)}
    proc = subprocess.run(["/bin/bash", str(script)], text=True,
                          capture_output=True, env=env, timeout=60)
    out = proc.stdout
    assert "old:refused" in out, out
    assert "new:qualifies" in out, out
    assert "empty:refused" in out, out
    assert "absent:refused" in out, out
    assert "deadshebang:refused" in out, out
    assert "shshebang:qualifies" in out, out
    assert "reason:names-version" in out, (
        f"the refusal must name the interpreter's VERSION and the floor: {out}")


def test_a_checkout_that_cannot_be_run_is_skipped_for_a_later_one(tmp_path):
    """THE root cause (#4848): the resolution loop accepted a candidate on
    "it holds `tortoise/`" and never tried the next one, so a stale tree with
    no interpreter *won* over a perfectly good checkout.

    Mutation: restore `[ -d "$CANDIDATE/tortoise" ]` as the whole acceptance
    test — the stale candidate wins, the good checkout's console script never
    runs, and this REDs."""
    home = tmp_path / "home"
    (home / ".codex" / "hooks").mkdir(parents=True)
    hook = home / ".codex" / "hooks" / "session-end.sh"
    shutil.copy(CODEX_HOOK, hook)
    hook.chmod(0o755)

    # Candidate 1 ($TORTOISE_SRC_DIR): the package, and NOTHING to run it with.
    stale = _module_dir(tmp_path / "stale")
    # Candidate 2 (the installer's record): a checkout with its own venv.
    good = _module_dir(tmp_path / "good")
    log = tmp_path / "argv.log"
    _logging_capture_bin(good / ".venv" / "bin", log)

    (home / ".tortoise").mkdir(parents=True, exist_ok=True)
    (home / ".tortoise" / "hook-src-dir").write_text(str(good) + "\n")

    bindir = tmp_path / "bin"
    _coreutils(bindir)
    _old_python_shim(bindir)  # the ambient interpreter cannot run the package

    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    proc = subprocess.run(
        ["/bin/bash", str(hook)], input=json.dumps({
            "session_id": "sid-4848", "transcript_path": str(transcript),
            "cwd": str(home)}), text=True, capture_output=True,
        env={"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin",
             "TMPDIR": str(tmp_path), "TORTOISE_SRC_DIR": str(stale)},
        timeout=30)

    assert proc.returncode == 0, proc.stderr
    assert _wait_for(lambda: log.is_file()
                     and "DONE" in log.read_text(encoding="utf-8")), (
        "the stale candidate was accepted and the runnable one never tried — "
        "the loop still stops at the first directory that holds `tortoise/`")
    body = log.read_text(encoding="utf-8")
    assert "SELF=" in body and str(good / ".venv" / "bin" / "tortoise") in body, body
    assert "sessions" in body, body


def test_a_stale_checkout_refuses_with_a_wrong_interpreter_reason(tmp_path):
    """The refusal must say *wrong interpreter*, not leave the CLI to say
    `datetime has no UTC`. The message names the version and the path, and it
    is NOT the "could not resolve a module dir" text — that would send the user
    to `hooks upgrade` for a problem an upgrade cannot fix.

    Mutation: drop the `_tortoise_unusable_reason` splice — the detail falls
    back to "could not resolve", which is false here, and this REDs."""
    home = tmp_path / "home"
    (home / ".codex" / "hooks").mkdir(parents=True)
    hook = home / ".codex" / "hooks" / "session-end.sh"
    shutil.copy(CODEX_HOOK, hook)
    hook.chmod(0o755)
    stale = _module_dir(tmp_path / "stale")

    bindir = tmp_path / "bin"
    _coreutils(bindir)
    _old_python_shim(bindir, version="3.9.6")

    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    proc = subprocess.run(
        ["/bin/bash", str(hook)], input=json.dumps({
            "session_id": "sid-4848", "transcript_path": str(transcript),
            "cwd": str(home)}), text=True, capture_output=True,
        env={"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin",
             "TMPDIR": str(tmp_path), "TORTOISE_SRC_DIR": str(stale)},
        timeout=30)

    assert proc.returncode == 0, proc.stderr
    body = _breadcrumb(home, "codex")
    assert body["kind"] == "install-inert", body
    detail = body["detail"]
    assert str(stale) in detail, detail
    assert "3.9.6" in detail, detail
    assert "tortoise needs Python" in detail, detail
    assert "could not resolve a tortoise module dir" not in detail, (
        f"a dir WAS resolved; the refusal must not send the user to "
        f"`hooks upgrade`: {detail}")


def test_the_claude_seam_refuses_a_stale_checkout_the_same_way(tmp_path):
    """The Claude seam carries the same block and must produce the same shape,
    because a one-sided fix is the same class of defect as the one being fixed.
    This seam resolves no console script, so the PROBE is what refuses it: with
    no qualifying interpreter the capture is not even attempted.

    Mutation: make ``_tortoise_py_qualifies`` an existence test — the seam runs
    the capture under 3.9, the ImportError is swallowed by its own ``exit 0``,
    no breadcrumb is written at all, and this REDs."""
    home = tmp_path / "home"
    (home / ".claude" / "hooks").mkdir(parents=True)
    hook = home / ".claude" / "hooks" / "session-end.sh"
    shutil.copy(CLAUDE_HOOK, hook)
    hook.chmod(0o755)
    stale = _module_dir(tmp_path / "stale")

    bindir = tmp_path / "bin"
    _coreutils(bindir)
    _old_python_shim(bindir, version="3.9.6")

    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = _run_claude_hook(
        hook, home, transcript=transcript,
        path=f"{bindir}:/usr/bin:/bin",
        extra_env={"TORTOISE_SRC_DIR": str(stale)})

    assert proc.returncode == 0, proc.stderr
    body = _breadcrumb(home, "claude")
    assert body["kind"] == "install-inert", body
    detail = body["detail"]
    assert str(stale) in detail, detail
    assert "3.9.6" in detail, detail
    assert "tortoise needs Python" in detail, detail


# ── #4848 review: acceptance and execution must be ONE predicate ──────────
#
# The guard's acceptance predicated on interpreter legs (`pyvenv.cfg` owning
# venv, active `$VIRTUAL_ENV`) that three seams' resolution ladders did not
# know. A candidate the guard had just called RUNNABLE was then refused as
# un-runnable — or, where `PYTHON_BIN` was left unset, the seam died at
# `unbound variable` BEFORE it could record the refusal, which is silent
# no-capture: the exact class #4848 exists to remove.

_WORKER_SEAMS = [
    ("codex-hooks/session-end.sh", "TORTOISE_CODEX_PAYLOAD"),
    ("cursor-hooks/session-end.sh", "TORTOISE_CURSOR_PAYLOAD"),
]


def _logging_python(venv: Path, log: Path, *, version: str = "3.12.0") -> Path:
    """A QUALIFYING interpreter that records every invocation.

    It qualifies on the guard's own probe and reports ``version``; everything
    else is a no-op exit 0, so a test can tell "the capture leg was REACHED
    under this interpreter" without needing a runnable ``tortoise`` package.
    """
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    py = venv / "bin" / "python"
    py.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "INVOKED %s\\n" "$*" >> {log}\n'
        'for a in "$@"; do\n'
        '  case "$a" in\n'
        '    *version_info*) exit 0 ;;\n'
        f"    *platform.python_version*) printf '%s\\n' '{version}'; exit 0 ;;\n"
        "  esac\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8")
    py.chmod(0o755)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    return py


def _wheel_install(tmp_path: Path, log: Path) -> tuple[Path, Path]:
    """A WHEEL layout: the module dir lives INSIDE a venv's ``site-packages``,
    so the venv that OWNS it is found only by the bounded ``pyvenv.cfg`` walk-up
    (PEP 405) — there is no ``.venv`` beneath the module dir."""
    venv = tmp_path / "owning-venv"
    _logging_python(venv, log)
    module = venv / "lib" / "python3.12" / "site-packages"
    (module / "tortoise").mkdir(parents=True)
    (module / "tortoise" / "__init__.py").write_text("", encoding="utf-8")
    return venv, module


@pytest.mark.parametrize("seam,payload_env", _WORKER_SEAMS,
                         ids=[s for s, _ in _WORKER_SEAMS])
def test_a_wheel_install_runs_under_the_venv_that_owns_it(
        tmp_path, seam, payload_env):
    """A wheel install's module dir is accepted through the ``pyvenv.cfg``
    walk-up — so the seam must RUN it with that venv's interpreter. It did not:
    none of the resolution ladders knew the owning venv, `PYTHON_BIN` was left
    UNSET, and under ``set -u`` the worker aborted at ``PYTHON_BIN: unbound
    variable`` with rc=1 and NO breadcrumb. ``main`` at least recorded a
    ``capture-failure`` there, so this was a regression against the issue's own
    "evidence, not silence" contract.

    Mutation: resolve the interpreter any way other than
    ``_tortoise_dir_python`` (the pre-review ladders) — the owning-venv path has
    no interpreter, the run dies before the capture, and this REDs on the missing
    ``sessions`` invocation."""
    hook = REPO_ROOT / "tortoise" / seam
    home = tmp_path / "home"
    homedir = home / (".codex" if seam.startswith("codex") else ".cursor") / "hooks"
    homedir.mkdir(parents=True)
    installed = homedir / "session-end.sh"
    shutil.copy(hook, installed)
    installed.chmod(0o755)

    log = tmp_path / "capture.log"
    _venv, module = _wheel_install(tmp_path, log)
    (home / ".tortoise").mkdir(parents=True)
    (home / ".tortoise" / "hook-src-dir").write_text(str(module) + "\n",
                                                    encoding="utf-8")

    bindir = tmp_path / "bin"
    _coreutils(bindir)
    _old_python_shim(bindir, version="3.9.6")  # stock-macOS ambient python3

    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({
        "session_id": "sid-4848-wheel", "transcript_path": str(transcript),
        "cwd": str(home)}), encoding="utf-8")

    proc = subprocess.run(
        ["/bin/bash", str(installed), "--worker"], text=True,
        capture_output=True, timeout=30,
        env={"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin",
             "TMPDIR": str(tmp_path), payload_env: str(payload)})

    assert proc.returncode == 0, proc.stderr
    assert "unbound variable" not in proc.stderr, proc.stderr
    body = _wait_for(lambda: log.is_file()
                     and "tortoise.__main__" in log.read_text(encoding="utf-8"))
    assert body, (
        "the owning venv was accepted but never used to run the capture — the "
        f"seam did not reach `sessions import`: {log.read_text(encoding='utf-8') if log.is_file() else '(no invocation at all)'}")
    crumbs = home / ".tortoise" / "capture-errors"
    assert not crumbs.exists(), (
        f"a refusal was recorded although a qualifying interpreter was in hand: "
        f"{sorted(p.name for p in crumbs.iterdir())}")


def test_the_claude_seam_runs_under_a_virtualenv_it_accepted(tmp_path):
    """``_tortoise_dir_runnable`` accepts a checkout on an ACTIVE
    ``$VIRTUAL_ENV`` whose interpreter qualifies. Three claude seams then
    resolved ``PYTHON_BIN`` without that leg and refused — naming the very
    interpreter they had just accepted as the one that *cannot run it*:

        "... the interpreter available is 3.12.0 at <venv>/bin/python and cannot
         run it (tortoise needs Python 3.12+), and captured nothing."

    A 3.12 named as unusable against a 3.12 floor, a remedy ("create that
    checkout's venv") that is wrong for the cause, and a capture dropped that the
    guard had already blessed — while codex/cursor on the same host captured.

    Mutation: drop the ``$VIRTUAL_ENV`` leg from ``_tortoise_dir_python`` — the
    refusal comes back and this REDs."""
    home = tmp_path / "home"
    (home / ".claude" / "hooks").mkdir(parents=True)
    hook = home / ".claude" / "hooks" / "session-end.sh"
    shutil.copy(CLAUDE_HOOK, hook)
    hook.chmod(0o755)

    # A source checkout that holds the package but has NO `.venv` of its own.
    src = _module_dir(tmp_path / "src")
    log = tmp_path / "capture.log"
    venv = tmp_path / "active-venv"
    _logging_python(venv, log)

    bindir = tmp_path / "bin"
    _coreutils(bindir)
    _old_python_shim(bindir, version="3.9.6")

    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = _run_claude_hook(
        hook, home, transcript=transcript,
        path=f"{bindir}:/usr/bin:/bin",
        extra_env={"VIRTUAL_ENV": str(venv), "TORTOISE_SRC_DIR": str(src)})

    assert proc.returncode == 0, proc.stderr
    assert _wait_for(lambda: log.is_file()
                     and "tortoise.__main__" in log.read_text(encoding="utf-8")), (
        "the checkout was accepted on the ACTIVE venv and then refused: "
        f"{log.read_text(encoding='utf-8') if log.is_file() else '(no invocation)'}")
    crumbs = home / ".tortoise" / "capture-errors"
    assert not crumbs.exists(), (
        "a false refusal was recorded for a candidate the guard accepted: "
        f"{[p.read_text(encoding='utf-8') for p in crumbs.iterdir()]}")


@pytest.mark.parametrize("seam,payload_env", _WORKER_SEAMS,
                         ids=[s for s, _ in _WORKER_SEAMS])
def test_a_refusal_naming_a_hostile_path_is_still_valid_json(
        tmp_path, seam, payload_env):
    """#4848 splices a FILESYSTEM PATH into the refusal's ``detail``. A backslash
    is legal in a macOS/Linux path and a ``"`` closes the JSON string early, so a
    writer that interpolates ``detail`` raw emits INVALID JSON. That is not
    cosmetic: ``session_verify._local_capture_error`` returns ``None`` on
    ``ValueError``, i.e. an unparseable breadcrumb is indistinguishable from NO
    breadcrumb, so an inert install reports PROVEN — the #4314 inversion this
    whole file exists to prevent. Five of the seven seams were raw while codex's
    and cursor's ``session-end`` escaped, which is the same "one-sided fix" class.

    Mutation: interpolate ``detail`` raw — ``json.loads`` REDs with
    ``Invalid \\escape``."""
    hook = REPO_ROOT / "tortoise" / seam
    home = tmp_path / "home"
    homedir = home / (".codex" if seam.startswith("codex") else ".cursor") / "hooks"
    homedir.mkdir(parents=True)
    installed = homedir / "session-end.sh"
    shutil.copy(hook, installed)
    installed.chmod(0o755)

    # A dir that HOLDS the package but offers nothing to run it with, at a path
    # that needs escaping in both directions.
    stale = tmp_path / 'weird\\dir "quoted"' / "stale"
    _module_dir(stale)

    bindir = tmp_path / "bin"
    _coreutils(bindir)
    _old_python_shim(bindir, version="3.9.6")

    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({
        "session_id": "sid-4848-esc", "transcript_path": str(transcript),
        "cwd": str(home)}), encoding="utf-8")

    proc = subprocess.run(
        ["/bin/bash", str(installed), "--worker"], text=True,
        capture_output=True, timeout=30,
        env={"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin",
             "TMPDIR": str(tmp_path), payload_env: str(payload),
             "TORTOISE_SRC_DIR": str(stale)})
    assert proc.returncode == 0, proc.stderr

    harness = "codex" if seam.startswith("codex") else "cursor"
    crumb = home / ".tortoise" / "capture-errors" / f"{harness}-install.json"
    assert _wait_for(lambda: crumb.is_file()), (
        "the refusal was not recorded at all — no evidence, no diagnosis")
    raw = crumb.read_text(encoding="utf-8")
    body = json.loads(raw)  # the assertion: unescaped writers raise here
    assert str(stale) in body["detail"], body
    assert body["kind"] == "install-inert", body


@pytest.mark.parametrize("hook", _HOOK_SCRIPTS, ids=_hook_id)
def test_every_breadcrumb_writer_escapes_through_the_shared_helper(hook):
    """ONE escaper, in the byte-identical guard block, used by EVERY writer.

    The defect the behaviour tests above pin was seven writers and two escaping
    behaviours, so a seam could be JSON-safe while its sibling was raw. The two
    behaviour tests can only drive the seams whose stdin contract this suite
    already knows; this pins the property for all seven, and it fails the moment
    one seam keeps a private escape chain (or none)."""
    text = hook.read_text(encoding="utf-8")
    assert "_tortoise_json_escape() {" in text, hook
    assert '_tortoise_json_escape "$detail"' in text, (
        f"{hook.name} interpolates `detail` without the shared escaper")
    if '"harness": "%s"' in text:
        assert '_tortoise_json_escape "$harness"' in text, (
            f"{hook.name} interpolates `harness` without the shared escaper")
    assert text.index('_tortoise_json_escape "$detail"') < text.rindex(
        '"detail": "%s"'), f"{hook.name}: the escaper must run BEFORE the writer"


# ── The second review round ───────────────────────────────────────────────
#
# Each test below pins one seam-level hole the first fix left open. All four were
# reproduced against the shipped artifacts before the fix.

def _guard_functions(tmp_path: Path, script: str) -> Path:
    """The guard block extracted to a sourceable file, plus ``script`` appended.

    The block is pure function definitions (plus the floor literal), so sourcing
    it exercises the REAL shipped bytes without running a seam — which is how a
    guard-block helper (the escaper, the interpreter precedence) gets a direct
    unit test rather than only an end-to-end one."""
    block = _guard_block(REPO_ROOT / "tortoise" / "claude-hooks" / "session-end.sh")
    f = tmp_path / "guard.sh"
    f.write_text(block + "\n" + script, encoding="utf-8")
    return f


def test_the_escaper_round_trips_json_escapes_and_names_its_c0_policy(tmp_path):
    """`_tortoise_json_escape` is the ONE writer-side guarantee that a breadcrumb
    stays parseable, so it is pinned directly:

    * quote, backslash, LF, CR, TAB, BS and FF must survive a JSON round-trip
      BYTE-FOR-BYTE (the field is a filesystem path in #4848's refusals — a
      mangled path sends the user to a directory that does not exist);
    * the rest of the C0 class has no JSON escape and IS discarded — the
      function's own comment says so, and this pins it so the "lossless" reading
      cannot come back (#4848 review: the comment claimed losslessness while the
      sweep deleted those bytes)."""
    script = r'''
case "$1" in
  roundtrip) _tortoise_json_escape "$INPUT" ;;
  c0) _tortoise_json_escape "$(printf 'A\001B\013C\016D')" ;;
esac
'''
    guard = _guard_functions(tmp_path, script)

    # A path that needs every escape JSON has: backslash, quote, LF, TAB, BS, FF.
    hostile = 'a\\b"c\nd\te' + "\b" + "\f"
    got = subprocess.run(["/bin/bash", str(guard), "roundtrip"], text=True,
                         capture_output=True, timeout=20,
                         env={**os.environ, "INPUT": hostile})
    assert got.returncode == 0, got.stderr
    assert json.loads(f'{{"detail": "{got.stdout}"}}')["detail"] == hostile, (
        "the escaper is not round-trip safe — a path with a quote, a backslash "
        f"or a control escape comes back mangled: {got.stdout!r}")

    dropped = subprocess.run(["/bin/bash", str(guard), "c0"], text=True,
                             capture_output=True, timeout=20)
    assert dropped.returncode == 0, dropped.stderr
    body = json.loads(f'{{"detail": "{dropped.stdout}"}}')
    assert body["detail"] == "ABCD", (
        "the C0 sweep changed: either those bytes are now ESCAPED (then the "
        f"comment is wrong again) or something else moved: {body['detail']!r}")


def test_the_python_floor_is_tied_to_pyproject_requires_python():
    """The floor is a literal in the byte-identical block, and the ONLY thing
    that keeps it true is a test: #4848's own research comment warned that a
    hard-coded floor "re-stales the next time the floor moves — the same failure
    mode this issue is about". When ``requires-python`` moves, every seam would
    keep accepting the old floor and the ImportError comes back silently.

    Mutation: bump `requires-python` in pyproject.toml and not the block (or the
    reverse) — this REDs."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^requires-python\s*=\s*">=\s*(\d+)\.(\d+)', pyproject, re.M)
    assert m, "pyproject.toml no longer declares requires-python >= X.Y"
    expected = f"{m.group(1)}.{m.group(2)}"
    block = _guard_block(REPO_ROOT / "tortoise" / "claude-hooks" / "session-end.sh")
    floor = re.findall(r'^_TORTOISE_MIN_PY="([^"]+)"$', block, re.M)
    assert floor == [expected], (
        f"the guard's floor {floor} has drifted from pyproject's "
        f"requires-python {expected}")


def test_a_record_with_a_non_utf8_byte_still_reads_as_evidence(tmp_path):
    """#4848 splices filesystem PATHS into ``detail``, and a directory name may
    carry a byte that is not valid UTF-8 (legal on Linux). The pure-shell writer
    cannot transcode it, so the record's bytes are not decodable as UTF-8 — and
    ``_local_capture_error`` used a STRICT decode, whose ``UnicodeDecodeError`` is
    a ``ValueError``, so it returned ``None``: an unreadable record read exactly
    like NO record and the inert install reported PROVEN.

    Mutation: read the record with a strict ``encoding="utf-8"`` — this REDs."""
    from tortoise import session_verify

    home = tmp_path / "home"
    crumb_dir = home / ".tortoise" / "capture-errors"
    crumb_dir.mkdir(parents=True)
    # The bytes are written DIRECTLY: `json.dumps` would escape the offending
    # byte as `\ufffd`/`\udcff` (ensure_ascii) and the test would then prove
    # nothing about a real undecodable record. 0xff is not valid UTF-8 anywhere.
    (crumb_dir / "claude-install.json").write_bytes(
        b'{"harness": "claude", "detail": "resolved a tortoise source dir at '
        b'/tmp/we' + bytes([0xFF]) + b'ird but could not run it",'
        b' "recorded_at": "2026-10-28T00:00:00Z",'
        b' "kind": "install-inert"}')

    got = session_verify._local_capture_error("claude", {"HOME": str(home)})
    assert got is not None, (
        "a record whose bytes are not valid UTF-8 was read as NO record — the "
        "inert install reports PROVEN and the evidence is invisible")
    assert got["kind"] == "install-inert", got


def test_the_per_turn_seam_refuses_a_binary_it_cannot_run(tmp_path):
    """``session-turn.sh`` fires on EVERY prompt, and it runs the `tortoise` it
    finds on PATH. That leg was the one the first round left unprobed: a stale
    console script whose `#!` interpreter is gone made ``session spool`` fail and
    the seam's `|| exit 0` swallowed it — nothing captured, NO record — which
    `session verify` reads as PROVEN. The other six seams probe this leg.

    Mutation: drop the ``_tortoise_bin_qualifies`` probe (and its refusal) from
    ``session-turn.sh`` — no breadcrumb is written and this REDs."""
    home = tmp_path / "home"
    (home / ".claude" / "hooks").mkdir(parents=True)
    hook = home / ".claude" / "hooks" / "session-turn.sh"
    shutil.copy(REPO_ROOT / "tortoise" / "claude-hooks" / "session-turn.sh", hook)
    hook.chmod(0o755)

    bindir = tmp_path / "bin"
    _coreutils(bindir)
    _old_python_shim(bindir, version="3.9.6")
    # A console script whose interpreter does not exist: `bad interpreter`.
    stale = bindir / "tortoise"
    stale.write_text("#!/nonexistent/python3\n", encoding="utf-8")
    stale.chmod(0o755)

    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = subprocess.run(
        ["/bin/bash", str(hook)], input=json.dumps({
            "session_id": "sid-4848-turn", "transcript_path": str(transcript),
            "cwd": str(home)}), text=True, capture_output=True, timeout=30,
        env={"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin",
             "TMPDIR": str(tmp_path)})

    assert proc.returncode == 0, proc.stderr
    body = _breadcrumb(home, "claude")
    assert body["kind"] == "install-inert", body
    assert str(stale) in body["detail"], body
    assert "cannot run it" in body["detail"], body
    assert "could not resolve" not in body["detail"], (
        f"the binary WAS found; the refusal must say why it was not run: {body}")


def test_the_per_turn_seam_skips_a_binary_it_cannot_run_for_a_checkout(tmp_path):
    """#4848's acceptance decision — *"the loop still preferring an unusable
    candidate over a usable later one"* (the scoping comment on #4848) — applies
    to the PATH binary TOO, and this seam was the only one that did not apply
    it: it ``exit 0``-ed at a stale `tortoise` on PATH *before* the module loop,
    so a host with a leftover `uv tool install` and a perfectly runnable
    checkout in `TORTOISE_SRC_DIR`/`hook-src-dir` kept the stale install and
    captured nothing. The refusal's own remedy was unactionable for the same
    reason: it offers "set TORTOISE_SRC_DIR to a checkout with a working
    .venv", and that variable was read only AFTER the exit, so a user who
    followed it saw no change.

    Mutation: put the ``if [ -n "$TORTOISE_UNUSABLE_BIN" ]`` refusal back above
    the module loop — the seam refuses, the checkout's console script never
    runs, and this REDs."""
    home = tmp_path / "home"
    (home / ".claude" / "hooks").mkdir(parents=True)
    hook = home / ".claude" / "hooks" / "session-turn.sh"
    shutil.copy(REPO_ROOT / "tortoise" / "claude-hooks" / "session-turn.sh", hook)
    hook.chmod(0o755)

    # The later, USABLE candidate: a checkout whose console script the guard
    # accepts (a non-python `#!` is unknown, and unknown is never refused) and
    # which records that it was the thing this seam actually RAN.
    good = _module_dir(tmp_path / "good")
    log = tmp_path / "argv.log"
    _logging_capture_bin(good / ".venv" / "bin", log)

    bindir = tmp_path / "bin"
    _coreutils(bindir)
    _old_python_shim(bindir, version="3.9.6")
    # ...and the earlier, UNUSABLE one: a stale console script on PATH.
    stale = bindir / "tortoise"
    stale.write_text("#!/nonexistent/python3\n", encoding="utf-8")
    stale.chmod(0o755)

    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = subprocess.run(
        ["/bin/bash", str(hook)], input=json.dumps({
            "session_id": "sid-4848-turn-skip",
            "transcript_path": str(transcript), "cwd": str(home)}),
        text=True, capture_output=True, timeout=30,
        env={"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin",
             "TMPDIR": str(tmp_path), "TORTOISE_SRC_DIR": str(good)})

    assert proc.returncode == 0, proc.stderr
    assert _wait_for(lambda: log.is_file()
                     and "DONE" in log.read_text(encoding="utf-8")), (
        "the seam stopped at the stale binary on PATH instead of running the "
        "runnable checkout it was handed — the skip rule #4848 installs was "
        "not applied to the PATH leg")
    body = log.read_text(encoding="utf-8")
    assert f"SELF={good / '.venv' / 'bin' / 'tortoise'}" in body, body
    assert "session" in body and "spool" in body, body
    crumbs = home / ".tortoise" / "capture-errors"
    assert not crumbs.exists(), (
        "a refusal was recorded on a host that HAD a runnable checkout: "
        f"{[p.read_text(encoding='utf-8') for p in crumbs.iterdir()]}")


def test_a_checkout_accepted_on_its_console_script_is_the_one_that_runs(tmp_path):
    """``_tortoise_dir_runnable`` accepts a candidate whose ``.venv/bin/tortoise``
    has a NON-python `#!` (an unknown interpreter is never refused). The three
    Claude seams then resolved `TORTOISE_BIN` only from `command -v tortoise` and
    otherwise ran an interpreter — so the leg the loop had just ACCEPTED was
    never executed, the loop did not skip to the later runnable candidate, and
    the seam refused a candidate it had blessed. ``test_4041``'s own fixture
    builds exactly this shape (`#!/bin/sh` console script), so it is supported.

    Mutation: drop the `TORTOISE_BIN` resolution from ``$TORTOISE_MODULE/.venv``
    in the Claude seam — the wrapper never runs, the refusal comes back, RED."""
    home = tmp_path / "home"
    (home / ".claude" / "hooks").mkdir(parents=True)
    hook = home / ".claude" / "hooks" / "session-end.sh"
    shutil.copy(CLAUDE_HOOK, hook)
    hook.chmod(0o755)

    # Candidate 1: the package plus a NON-python console script — accepted, and
    # the only thing this seam could run.
    first = _module_dir(tmp_path / "first")
    log = tmp_path / "capture.log"
    wrapper = first / ".venv" / "bin" / "tortoise"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        "#!/bin/sh\n"
        f'printf "WRAPPER %s\\n" "$*" >> {log}\n'
        "exit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)

    bindir = tmp_path / "bin"
    _coreutils(bindir)
    _old_python_shim(bindir, version="3.9.6")

    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = _run_claude_hook(
        hook, home, transcript=transcript,
        path=f"{bindir}:/usr/bin:/bin",
        extra_env={"TORTOISE_SRC_DIR": str(first)})

    assert proc.returncode == 0, proc.stderr
    # The wrapper is invoked for the reindex sweep AND for the capture; either
    # proves the accepted leg is the executed one, so wait for the capture.
    assert _wait_for(lambda: log.is_file()), (
        "the console script the guard ACCEPTED was never run — the seam refused "
        "a candidate it had just blessed instead of running it")
    assert _wait_for(lambda: "capture" in log.read_text(encoding="utf-8")), (
        f"the wrapper ran but never for the capture leg: {log.read_text(encoding='utf-8')}")
    crumbs = home / ".tortoise" / "capture-errors"
    assert not crumbs.exists(), (
        f"a refusal was recorded for a candidate the guard accepted: "
        f"{[p.read_text(encoding='utf-8') for p in crumbs.iterdir()]}")


def test_the_claude_refusal_naming_a_hostile_path_is_valid_json(tmp_path):
    """The escaping behaviour test covered the two WORKER seams; this covers a
    Claude seam — the family that was raw before the first fix (F1 of the first
    review round). Same property, different stdin contract, so the structural pin
    is not the only coverage for these five."""
    home = tmp_path / "home"
    (home / ".claude" / "hooks").mkdir(parents=True)
    hook = home / ".claude" / "hooks" / "session-end.sh"
    shutil.copy(CLAUDE_HOOK, hook)
    hook.chmod(0o755)

    stale = tmp_path / 'weird\\dir "quoted"' / "stale"
    _module_dir(stale)

    bindir = tmp_path / "bin"
    _coreutils(bindir)
    _old_python_shim(bindir, version="3.9.6")

    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl")
    proc = _run_claude_hook(
        hook, home, transcript=transcript,
        path=f"{bindir}:/usr/bin:/bin",
        extra_env={"TORTOISE_SRC_DIR": str(stale)})

    assert proc.returncode == 0, proc.stderr
    crumb = home / ".tortoise" / "capture-errors" / "claude-install.json"
    assert _wait_for(lambda: crumb.is_file()), "the refusal was not recorded"
    body = json.loads(crumb.read_text(encoding="utf-8"))  # RED if unescaped
    assert str(stale) in body["detail"], body
