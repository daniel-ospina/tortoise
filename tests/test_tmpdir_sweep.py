"""#4069 — the age-gated temp-dir sweep (`tools/tmpdir_sweep.py`).

Pure unit tests over a sandbox root: no real `$TMPDIR` entry is ever a
candidate, so the suite cannot delete a developer's or another lane's
litter by running these.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.tmpdir_sweep import (
    _FORBIDDEN_ROOTS,
    DEFAULT_PREFIXES,
    _is_within,
    _live_pid_protects,
    _remove,
    main,
    resolve_root,
    sweep,
)

_OLD_H = 48.0   # comfortably past the 24h default
_YOUNG_H = 1.0


def _make(root: Path, name: str, *, age_hours: float, is_dir: bool = True,
          content: bytes | None = None) -> Path:
    path = root / name
    if is_dir:
        path.mkdir(parents=True)
        if content is not None:
            (path / "payload.bin").write_bytes(content)
    else:
        path.write_bytes(content or b"x")
    ts = time.time() - age_hours * 3600.0
    os.utime(path, (ts, ts))
    return path


# ── the allowlist the issue's evidence requires ───────────────────────────

def test_default_allowlist_covers_observed_creators():
    for prefix in ("ask_", "tortoise_", "tortoise-", "redislite_", "lme-",
                   "d3_session_", "reaper_probe_"):
        assert prefix in DEFAULT_PREFIXES, prefix


def test_agent_tooling_prefixes_are_not_in_the_default_allowlist():
    """Cross-repo owners (`pi-commit-msg-*`, `wf-lock-*`) are out of scope."""
    for prefix in ("pi-commit-msg-", "pi-pr-body-", "wf-lock-", "admin-"):
        assert prefix not in DEFAULT_PREFIXES, prefix


# ── root boundedness ──────────────────────────────────────────────────────

def test_resolve_root_refuses_unbounded_roots():
    for bad in ("/", os.path.expanduser("~")):
        with pytest.raises(ValueError):
            resolve_root(bad)


def test_forbidden_roots_include_the_realpath_spelling():
    # A guard matching only the raw `~` spelling fails open when $HOME is a
    # symlink; both spellings must be present.
    for raw in ("/", os.path.expanduser("~")):
        assert raw in _FORBIDDEN_ROOTS
        assert os.path.realpath(raw) in _FORBIDDEN_ROOTS


def test_resolve_root_refuses_dotdot():
    with pytest.raises(ValueError):
        resolve_root("/tmp/../etc")


def test_resolve_root_returns_realpath(tmp_path):
    assert resolve_root(str(tmp_path)) == os.path.realpath(str(tmp_path))


def test_is_within_is_boundary_exact(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    child = root / "a"
    child.mkdir()
    sibling = tmp_path / "root2"
    sibling.mkdir()
    assert _is_within(str(child), str(root))
    assert not _is_within(str(sibling), str(root))
    # `/tmp/ab` must never read as inside `/tmp/a`
    prefix_lookalike = tmp_path / "ab"
    prefix_lookalike.mkdir()
    a = tmp_path / "a"
    a.mkdir()
    assert not _is_within(str(prefix_lookalike), str(a))


def test_remove_refuses_a_path_outside_the_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = _make(tmp_path, "ask_outside", age_hours=_OLD_H)
    with pytest.raises(ValueError):
        _remove(str(outside), str(root))
    assert outside.exists()


# ── planning: matching + age gate ─────────────────────────────────────────

def test_dry_run_removes_nothing(tmp_path):
    stale = _make(tmp_path, "ask_old_aaaaaaaa", age_hours=_OLD_H)
    result = sweep(str(tmp_path), apply=False, older_than_hours=24.0)
    assert stale.exists()
    assert [d.name for d in result.removed] == ["ask_old_aaaaaaaa"]
    assert result.apply is False


def test_apply_removes_stale_prefixed_dir(tmp_path):
    stale = _make(tmp_path, "ask_old_aaaaaaaa", age_hours=_OLD_H)
    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert not stale.exists()
    assert [d.name for d in result.removed] == ["ask_old_aaaaaaaa"]


def test_young_entry_is_kept(tmp_path):
    young = _make(tmp_path, "ask_young_bbbbbbbb", age_hours=_YOUNG_H)
    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert young.exists()
    assert result.removed == []
    assert any("young" in d.reason for d in result.kept)


def test_unmatched_entry_is_untouched(tmp_path):
    other = _make(tmp_path, "someone_elses_scratch", age_hours=_OLD_H)
    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert other.exists()
    assert result.decisions == []  # never even considered


def test_exact_name_artifact_is_removed(tmp_path):
    artifact = _make(tmp_path, "a_ours.py", age_hours=_OLD_H, is_dir=False)
    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert not artifact.exists()
    assert [d.name for d in result.removed] == ["a_ours.py"]


def test_reclaimed_bytes_counts_nested_content(tmp_path):
    _make(tmp_path, "ask_big_cccccccc", age_hours=_OLD_H,
          content=b"z" * 4096)
    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert result.reclaimed_bytes >= 4096


def test_sweep_is_idempotent(tmp_path):
    _make(tmp_path, "ask_once_dddddddd", age_hours=_OLD_H)
    first = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    second = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert len(first.removed) == 1
    assert second.removed == []


# ── symlinks: never followed, never removed ───────────────────────────────

def test_symlink_is_never_followed_and_never_removed(tmp_path):
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep me")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "ask_link_eeeeeeee"
    os.symlink(str(outside), str(link))

    result = sweep(str(root), apply=True, older_than_hours=24.0)

    assert link.is_symlink() and link.exists()
    assert (outside / "keep.txt").read_text() == "keep me"
    assert result.removed == []
    assert any(d.reason == "symlink (never followed)" for d in result.kept)


def test_symlink_cannot_be_removed_even_directly(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "target"
    outside.mkdir()
    link = root / "ask_link_ffffffff"
    os.symlink(str(outside), str(link))
    with pytest.raises(ValueError):
        _remove(str(link), str(root))
    assert outside.exists()


# ── live-server guard ─────────────────────────────────────────────────────

def test_live_pid_protects_a_stale_dir(tmp_path):
    live = tmp_path / "ask_live_99999999"
    live.mkdir()
    (live / "redis.pid").write_text(str(os.getpid()))  # this test process
    ts = time.time() - _OLD_H * 3600.0
    os.utime(live, (ts, ts))

    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert live.exists()
    assert result.removed == []
    assert any("live redis pid" in d.reason for d in result.kept)


def test_dead_pid_does_not_protect_a_stale_dir(tmp_path):
    # A reaped child pid is guaranteed dead.
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead = tmp_path / "ask_dead_88888888"
    dead.mkdir()
    (dead / "redis.pid").write_text(str(proc.pid))
    ts = time.time() - _OLD_H * 3600.0
    os.utime(dead, (ts, ts))

    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert not dead.exists()
    assert [d.name for d in result.removed] == ["ask_dead_88888888"]


def test_unparseable_pid_fails_closed():
    # exercised through plan() below; direct helper check keeps the contract
    # explicit and independent of the sweep loop.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "redis.pid"), "w") as fh:
            fh.write("not-a-pid")
        assert _live_pid_protects(td) is not None


def test_unparseable_pid_protects_a_stale_dir(tmp_path):
    weird = tmp_path / "ask_weird_77777777"
    weird.mkdir()
    (weird / "redis.pid").write_text("not-a-pid")
    ts = time.time() - _OLD_H * 3600.0
    os.utime(weird, (ts, ts))
    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert weird.exists()
    assert result.removed == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses mode bits")
def test_unreadable_pid_protects_a_stale_dir(tmp_path):
    """The distinct `except OSError` branch of the pid guard (declared
    threat class 3: unreadable pid)."""
    weird = tmp_path / "ask_unreadable_66666666"
    weird.mkdir()
    pid_file = weird / "redis.pid"
    pid_file.write_text(str(os.getpid()))
    os.chmod(pid_file, 0o000)
    ts = time.time() - _OLD_H * 3600.0
    os.utime(weird, (ts, ts))
    try:
        result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
        assert weird.exists()
        assert result.removed == []
    finally:
        os.chmod(pid_file, 0o600)


def test_non_regular_pid_file_fails_closed(tmp_path):
    """A `redis.pid` that EXISTS but is not a regular file must protect the
    entry. `os.path.isfile` answers False for a FIFO — which read as "no pid
    file" and exposed the entry to removal (declared threat class 3:
    live-server clobber; found by review on this PR)."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("mkfifo is POSIX-only")
    weird = tmp_path / "ask_fifo_55555555"
    weird.mkdir()
    os.mkfifo(weird / "redis.pid")
    ts = time.time() - _OLD_H * 3600.0
    os.utime(weird, (ts, ts))

    assert _live_pid_protects(str(weird)) is not None
    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert weird.exists()
    assert result.removed == []
    assert any("non-regular" in d.reason for d in result.kept)


def test_dangling_symlink_pid_file_fails_closed(tmp_path):
    weird = tmp_path / "ask_dangling_44444444"
    weird.mkdir()
    os.symlink(str(weird / "does-not-exist"), weird / "redis.pid")
    ts = time.time() - _OLD_H * 3600.0
    os.utime(weird, (ts, ts))

    assert _live_pid_protects(str(weird)) is not None
    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert weird.exists()
    assert result.removed == []


def test_a_dead_pid_does_not_short_circuit_a_live_second_pid(
        tmp_path, monkeypatch):
    """The dead-pid branch must `continue` to the REMAINING pid files rather
    than `return None`: with a second entry in `_PID_FILENAMES`, a dead
    `redis.pid` would otherwise silently disable a live server's protection.

    `_PID_FILENAMES` is a one-tuple today, so this is the latent fail-open the
    loop shape exists to prevent — monkeypatched to two entries to make it
    reachable."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    monkeypatch.setattr(
        "tools.tmpdir_sweep._PID_FILENAMES", ("redis.pid", "falkor.pid"))
    live = tmp_path / "ask_two_pids_22222222"
    live.mkdir()
    (live / "redis.pid").write_text(str(proc.pid))      # provably dead
    (live / "falkor.pid").write_text(str(os.getpid()))  # this process: live
    ts = time.time() - _OLD_H * 3600.0
    os.utime(live, (ts, ts))

    assert _live_pid_protects(str(live)) is not None
    result = sweep(str(tmp_path), apply=True, older_than_hours=24.0)
    assert live.exists()
    assert result.removed == []


def test_the_two_guards_agree_on_every_shape(tmp_path, monkeypatch):
    """`tools/tmpdir_sweep.py::_live_pid_protects` and
    `tests/_tmpdir_hygiene.py::_protected_reason` are deliberate MIRRORS of one
    safety property: only a PROVABLY dead pid permits removal.

    They are two copies because the sweep must stay runnable as a bare script —
    `python3 tools/tmpdir_sweep.py` puts `tools/`, not the repo root, on
    `sys.path`, so the sweep cannot import a shared `tools.*` helper. Two copies
    drift: both used `os.path.isfile`, which answers False for an EXISTING
    non-regular pid file, so the same guarded entry read as unguarded in BOTH.

    This test pins the equivalence across EVERY decision branch of both guards:
    the four filesystem shapes, the four branches that need an injected I/O
    failure (unreadable, unstattable, `os.kill` EACCES, `os.kill` generic
    OSError), and the `_PID_FILENAMES` probe loop plus the tuple itself. A
    one-sided change to either copy fails here.
    """
    import builtins
    import tempfile

    import tests._tmpdir_hygiene as hygiene_mod
    import tools.tmpdir_sweep as sweep_mod

    tracker = hygiene_mod._protected_reason

    # Channel 1 — the probe LIST. Two mirrors that probe different filenames
    # diverge with no shape below changing at all, so pin the tuple first.
    assert sweep_mod._PID_FILENAMES == hygiene_mod._PID_FILENAMES, (
        "the two mirrored guards probe different pid-file names")

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    shapes: list[tuple[str, str]] = []

    def _shape(label: str, setup) -> str:
        path = tempfile.mkdtemp(prefix="ask_parity_", dir=str(tmp_path))
        setup(path)
        shapes.append((label, path))
        return path

    def _pid(text: str):
        def _write(path: str) -> None:
            with open(os.path.join(path, "redis.pid"), "w") as fh:
                fh.write(text)
        return _write

    _shape("absent", lambda path: None)
    _shape("live", _pid(str(os.getpid())))
    _shape("dead", _pid(str(proc.pid)))
    _shape("unparseable", _pid("not-a-pid"))
    _shape("empty", _pid(""))
    _shape("zero", _pid("0"))
    _shape("negative", _pid("-3"))
    _shape("out-of-range", _pid("1" + "0" * 30))
    _shape("directory", lambda path: os.mkdir(
        os.path.join(path, "redis.pid")))
    _shape("dangling-symlink", lambda path: os.symlink(
        os.path.join(path, "gone"), os.path.join(path, "redis.pid")))
    if hasattr(os, "mkfifo"):
        _shape("fifo", lambda path: os.mkfifo(
            os.path.join(path, "redis.pid")))

    # Channel 2 — every filesystem shape, agreed and judged.
    assert len(shapes) >= 10
    for label, path in shapes:
        sweep_removable = sweep_mod._live_pid_protects(path) is None
        tracker_removable = tracker(path) is None
        assert sweep_removable == tracker_removable, (
            f"the two guards disagree on the {label!r} shape: sweep "
            f"removable={sweep_removable}, tracker removable={tracker_removable}")
    # And the property itself, not only the agreement: ONLY the two
    # no-live-pid shapes may be removable.
    removable = {label for label, path in shapes
                 if sweep_mod._live_pid_protects(path) is None}
    assert removable == {"absent", "dead"}, removable

    # Channel 3 — the branches that cannot be reached from filesystem state
    # alone. Each injects one I/O failure for the pid file only and requires
    # BOTH guards to agree AND to treat the entry as unprovable (protected).
    probe = _shape("injected", _pid(str(os.getpid())))
    pid_file = os.path.join(probe, "redis.pid")
    real_open, real_lstat, real_kill = builtins.open, os.lstat, os.kill

    def _guarded(target_path: str) -> None:
        sweep_reason = sweep_mod._live_pid_protects(probe)
        tracker_reason = tracker(probe)
        assert (sweep_reason is None) == (tracker_reason is None), (
            f"guards disagree on injected {target_path}: "
            f"sweep={sweep_reason!r}, tracker={tracker_reason!r}")
        assert sweep_reason is not None, (
            f"injected {target_path} left the entry REMOVABLE (fail-open)")

    def _open_raises(file, *args, **kwargs):
        if os.path.basename(str(file)) == "redis.pid":
            raise PermissionError("injected: unreadable pid file")
        return real_open(file, *args, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(builtins, "open", _open_raises)
        _guarded("open() OSError -> unreadable")

    def _lstat_raises(path, *args, **kwargs):
        if os.path.basename(str(path)) == "redis.pid":
            raise PermissionError("injected: unstattable pid file")
        return real_lstat(path, *args, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(os, "lstat", _lstat_raises)
        _guarded("os.lstat OSError -> unstattable")

    def _kill_raises(exc):
        def _patched(pid, sig):
            if pid == os.getpid():
                raise exc
            return real_kill(pid, sig)
        return _patched

    with monkeypatch.context() as mp:
        mp.setattr(os, "kill", _kill_raises(PermissionError("injected")))
        _guarded("os.kill PermissionError -> alive, no permission")
    with monkeypatch.context() as mp:
        mp.setattr(os, "kill", _kill_raises(OSError("injected")))
        _guarded("os.kill OSError -> probe failed")

    # Channel 4 — the probe LOOP: a provably dead FIRST pid must not
    # short-circuit a live SECOND one, in either mirror.
    pair = _shape("pair", lambda path: None)
    with open(os.path.join(pair, "redis.pid"), "w") as fh:
        fh.write(str(proc.pid))                      # provably dead
    with open(os.path.join(pair, "second.pid"), "w") as fh:
        fh.write(str(os.getpid()))                   # this process: live
    with monkeypatch.context() as mp:
        mp.setattr(sweep_mod, "_PID_FILENAMES", ("redis.pid", "second.pid"))
        mp.setattr(hygiene_mod, "_PID_FILENAMES", ("redis.pid", "second.pid"))
        assert sweep_mod._live_pid_protects(pair) is not None
        assert tracker(pair) is not None
    assert os.path.exists(pid_file)


def test_sweep_refuses_a_non_finite_age_gate(tmp_path):
    # `age_h < nan` is always False -> every entry becomes a candidate.
    _make(tmp_path, "ask_nan_33333333", age_hours=0.0)
    with pytest.raises(ValueError):
        sweep(str(tmp_path), apply=True, older_than_hours=float("nan"))
    with pytest.raises(ValueError):
        sweep(str(tmp_path), apply=True, older_than_hours=-1.0)
    assert (tmp_path / "ask_nan_33333333").exists()


def test_sweep_refuses_an_empty_prefix_at_the_library_boundary(tmp_path):
    # `startswith("")` matches every name: the guard must hold for `sweep()`
    # callers, not only for the CLI (declared threat class 5).
    alien = _make(tmp_path, "someone_elses_scratch", age_hours=_OLD_H)
    for bad in ([""], ["  "]):
        with pytest.raises(ValueError):
            sweep(str(tmp_path), prefixes=bad, apply=True,
                  older_than_hours=24.0)
    with pytest.raises(ValueError):
        sweep(str(tmp_path), exact_names=[""], apply=True,
              older_than_hours=24.0)
    assert alien.exists()


# ── CLI surface ───────────────────────────────────────────────────────────

def test_main_dry_run_exit_0_and_json(tmp_path, capsys):
    _make(tmp_path, "ask_cli_00000000", age_hours=_OLD_H)
    rc = main(["--root", str(tmp_path), "--json",
               "--older-than-hours", "24"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["apply"] is False
    assert payload["candidates"] == 1
    assert (tmp_path / "ask_cli_00000000").exists()


def test_main_apply_json_then_idempotent(tmp_path, capsys):
    _make(tmp_path, "ask_cli_11111111", age_hours=_OLD_H)
    rc = main(["--root", str(tmp_path), "--apply", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"] == 1
    rc = main(["--root", str(tmp_path), "--apply", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["candidates"] == 0


def test_main_refuses_an_empty_prefix(tmp_path, capsys):
    # `str.startswith("")` matches everything — an unset "$OWNER_" must not
    # silently widen the sweep to every entry.
    _make(tmp_path, "ask_anything_44444444", age_hours=_OLD_H)
    rc = main(["--root", str(tmp_path), "--apply", "--prefix", ""])
    assert rc == 2
    assert "empty/whitespace" in capsys.readouterr().err
    assert (tmp_path / "ask_anything_44444444").exists()


def test_main_refuses_a_missing_root(tmp_path, capsys):
    rc = main(["--root", str(tmp_path / "does-not-exist")])
    assert rc == 2
    assert "refusing" in capsys.readouterr().err


def test_main_refuses_a_non_finite_age_gate(tmp_path, capsys):
    young = _make(tmp_path, "ask_fresh_99999999", age_hours=0.0)
    rc = main(["--root", str(tmp_path), "--apply",
               "--older-than-hours", "nan"])
    assert rc == 2
    assert "non-finite" in capsys.readouterr().err
    assert young.exists()  # a nan gate must never widen the sweep


def test_main_exits_2_when_a_removal_fails(tmp_path, capsys, monkeypatch):
    import tools.tmpdir_sweep as sweep_mod

    _make(tmp_path, "ask_locked_55555555", age_hours=_OLD_H)

    def _boom(path, root):
        raise PermissionError("simulated rmtree failure")

    monkeypatch.setattr(sweep_mod, "_remove", _boom)
    rc = main(["--root", str(tmp_path), "--apply", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 2, payload
    assert payload["remove_failures"] >= 1


def test_main_refuses_an_unbounded_root(capsys):
    rc = main(["--root", "/", "--apply"])
    assert rc == 2
    assert "refusing" in capsys.readouterr().err


def test_main_custom_prefix_overrides_default_allowlist(tmp_path, capsys):
    _make(tmp_path, "ask_ignored_22222222", age_hours=_OLD_H)
    custom = _make(tmp_path, "myapp_scratch_33333333", age_hours=_OLD_H)
    rc = main(["--root", str(tmp_path), "--apply", "--json",
               "--prefix", "myapp_"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["removed"] == [custom.name]
    assert (tmp_path / "ask_ignored_22222222").exists()
