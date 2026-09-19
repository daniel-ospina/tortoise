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
