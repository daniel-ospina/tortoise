"""Epic #2080 end-state platform seams — wave-1 verification (W3-style).

Covers the SHIPPED seam surfaces (issues #2123 Codex / #2126 Claude-family /
#2124 Cline + the shared per-turn reflex surface for the rest of #2119-#2126):

1. ``tortoise volunteer`` — the per-turn reflex CLI (raw-prompt stdin, JSON
   window, ``--json`` contract, clean-silence fail-open on an empty graph).
2. ``tortoise install`` — agent-first harness registration (codex /
   claude / cline) incl. merge-idempotency and uninstall.
3. ``tortoise/claude-hooks/volunteer-turn.sh`` — the SHIPPED UserPromptSubmit
   hook script, executed for real, asserting the per-harness OUTPUT
   contracts (codex/claude ``hookSpecificOutput.additionalContext``, cline
   ``contextModification.context``) carry the reflex block; empty graph →
   empty stdout + exit 0 (content fail-open).

The reflex only surfaces EP-MEASURED points (neutral Beta(1,1) = 0.5 sits
below the 0.7 floor), so the seed wires evidence→claim IMPL + posterior
alphas directly — the documented seeding pattern shared with
test_selfhost_volunteer_context.py.

Self-contained embedded lane: clears TORTOISE_DB_URI and points
TORTOISE_DB_PATH at a per-test temp db (runs under any lane).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _REPO_ROOT / "tortoise" / "claude-hooks" / "volunteer-turn.sh"
_PY = sys.executable

_SEED = """
import os
from tortoise.sdk import TortoiseSDK
sdk = TortoiseSDK(db_path=os.environ["TORTOISE_DB_PATH"])
proj = sdk._get_proj()
ev = sdk.create_point(
    "evidence",
    "CI deploys on main merge via GitHub Actions [supporting record]")
claim = sdk.create_point(
    "statement",
    "The CI pipeline deploys via GitHub Actions on main merge.")
sdk.create_operator("IMPL", ev["id"], [claim["id"]])
for pid, a, b in ((claim["id"], 12.0, 1.0), (ev["id"], 12.0, 1.0)):
    m = round(a / (a + b), 4)
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence=$c, "
        "n.posterior_alpha=$a, n.posterior_beta=$b",
        params={"id": pid, "a": a, "b": b, "c": m})
sdk.close()
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Seed a measured embedded graph + hand back the child env dict.

    HOME is isolated to tmp_path so the file-config resolver
    (cwd/.tortoise, ~/.tortoise) can NEVER flip the child into hosted mode
    (a real hosted identity on the runner machine would make the
    local-graph tests vacuous/failing — see the wave-1 review P2).
    """
    db = tmp_path / "seams.db"
    base = {
        **os.environ,
        "TORTOISE_DB_PATH": str(db),
        "TORTOISE_DB_URI": "",
        "HOME": str(tmp_path / "home"),
    }
    subprocess.run(
        [_PY, "-c", _SEED], env=base, check=True,
        capture_output=True, timeout=120,
    )
    child_env = {
        **base, "TORTOISE_SECRET_PEPPER": "test-static-pepper",
        "_SEAM_CWD": str(tmp_path),
    }
    return child_env


def _isolated(tmp_path):
    """Env for empty/broken-db tests: isolated HOME, no db file."""
    return {
        **os.environ,
        "TORTOISE_DB_URI": "",
        "HOME": str(tmp_path / "home"),
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
        "_SEAM_CWD": str(tmp_path),
    }


def _run(argv, env, stdin: str | None = None, cwd: str | None = None):
    return subprocess.run(
        [_PY, "-m", "tortoise", *argv],
        input=stdin,
        env=env,
        cwd=cwd or env.get("_SEAM_CWD") or ".",
        capture_output=True,
        text=True,
        timeout=180,
    )


# ── 1. `tortoise volunteer` CLI ─────────────────────────────────────────


def test_volunteer_cli_raw_prompt_emits_block(env):
    """Hook ergonomics: a RAW prompt on stdin (no JSON envelope) surfaces a
    pointer block (the Claude/Codex UserPromptSubmit hook pipes raw text)."""
    r = _run(["volunteer"], env, stdin="How is CI deploy triggered?")
    assert r.returncode == 0, r.stderr
    assert "point/" in r.stdout  # a pointer id is emitted
    assert "CI pipeline deploys" in r.stdout


def test_volunteer_cli_json_window_and_contract(env):
    """JSON window input + --json returns the full contract shape."""
    window = json.dumps([{"role": "user", "content": "How is CI deploy triggered?"}])
    r = _run(["volunteer", "--json"], env, stdin=window)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["pointers"], "expected at least one pointer"
    assert data["block"]
    assert any(
        "CI pipeline" in ((p.get("synopsis") or "") + (p.get("label") or ""))
        for p in data["pointers"]
    )


def test_volunteer_cli_fail_open_on_empty_graph(tmp_path):
    """Empty graph → clean silence: empty stdout, exit 0 — never an error
    (a hook failure must not break the agent turn)."""
    env = _isolated(tmp_path)
    env["TORTOISE_DB_PATH"] = str(tmp_path / "empty.db")
    r = _run(["volunteer"], env, stdin="How is CI deploy triggered?")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_volunteer_cli_unavailable_db_is_hard_error(tmp_path):
    """A genuinely broken db is a hard (non-zero) failure on stderr — the
    hook appends || true; honest errors are never masked as clean silence."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    env = _isolated(tmp_path)
    env["TORTOISE_DB_PATH"] = str(blocker / "g.db")
    r = _run(["volunteer"], env, stdin="anything")
    assert r.returncode != 0
    assert r.stderr


def test_volunteer_cli_json_error_contract(tmp_path):
    """--json failures emit {status: error, ...} on STDOUT (machine
    contract) instead of an empty stream."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    env = _isolated(tmp_path)
    env["TORTOISE_DB_PATH"] = str(blocker / "g.db")
    r = _run(["volunteer", "--json"], env, stdin="anything")
    assert r.returncode != 0
    data = json.loads(r.stdout)
    assert data["status"] == "error"


def test_volunteer_cli_raw_prompt_that_parses_as_json(tmp_path):
    """A raw prompt that happens to parse as JSON (scalar / foreign dict) is
    treated as a prompt, never a hard error (clean silence on an empty
    graph, exit 0)."""
    env = _isolated(tmp_path)
    env["TORTOISE_DB_PATH"] = str(tmp_path / "empty.db")
    for raw in ("123", "true", '{"prompt": "hello"}'):
        r = _run(["volunteer"], env, stdin=raw)
        assert r.returncode == 0, (raw, r.stderr)
        assert r.stdout.strip() == "", raw


# ── 2. `tortoise install` — agent-first harness registration ────────────


def test_install_codex_writes_registration(tmp_path):
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    r = _run(["install", "codex", "--dir", str(tmp_path)], env)
    assert r.returncode == 0, r.stderr
    cfg = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    # Codex events are Vec<MatcherGroup> — each event entry wraps handlers in
    # a "hooks" array (flat {type, command} is silently ignored by codex).
    entry = cfg["hooks"]["UserPromptSubmit"][0]
    assert "hooks" in entry and "command" not in entry
    cmd = entry["hooks"][0]["command"]
    assert cmd.endswith("volunteer-turn.sh codex")
    assert Path(cmd.split()[0]).exists()  # the shipped hook is executable


def test_install_codex_idempotent_and_uninstall(tmp_path):
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    _run(["install", "codex", "--dir", str(tmp_path)], env)
    _run(["install", "codex", "--dir", str(tmp_path)], env)
    cfg = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    assert len(cfg["hooks"]["UserPromptSubmit"]) == 1  # no duplicate
    r = _run(["install", "codex", "--dir", str(tmp_path), "--uninstall"], env)
    assert r.returncode == 0
    cfg = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    assert "UserPromptSubmit" not in (cfg.get("hooks") or {})


def test_install_claude_merges_preserving_existing_hooks(tmp_path):
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "lint.sh"}]}
            ]
        },
        "enableAllProjectHooks": True,
    }
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(existing))
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    r = _run(["install", "claude", "--dir", str(tmp_path)], env)
    assert r.returncode == 0, r.stderr
    cfg = json.loads(target.read_text())
    assert len(cfg["hooks"]["PreToolUse"]) == 1  # untouched
    ups = cfg["hooks"]["UserPromptSubmit"]
    assert len(ups) == 1
    inner = ups[0]["hooks"][0]
    assert inner["command"].endswith("volunteer-turn.sh claude")


def test_install_claude_idempotent_reinstall(tmp_path):
    """Re-installing claude must NOT duplicate the UserPromptSubmit entry
    (regression: the merge dedup used to miss wrapper-shaped entries)."""
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    _run(["install", "claude", "--dir", str(tmp_path)], env)
    _run(["install", "claude", "--dir", str(tmp_path)], env)
    _run(["install", "claude", "--dir", str(tmp_path)], env)
    target = tmp_path / ".claude" / "settings.json"
    cfg = json.loads(target.read_text())
    assert len(cfg["hooks"]["UserPromptSubmit"]) == 1


def test_install_codex_refuses_non_object_config(tmp_path):
    """A config file whose top level is not a JSON object is refused with a
    clean error, never a traceback."""
    target = tmp_path / ".codex" / "hooks.json"
    target.parent.mkdir(parents=True)
    target.write_text("[1, 2, 3]")
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    r = _run(["install", "codex", "--dir", str(tmp_path)], env)
    assert r.returncode == 1
    assert "not a JSON object" in r.stderr
    assert "Traceback" not in r.stderr


def test_install_cline_writes_hook_file(tmp_path):
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    r = _run(["install", "cline", "--dir", str(tmp_path)], env)
    assert r.returncode == 0, r.stderr
    hook = tmp_path / ".cline" / "hooks" / "UserPromptSubmit"
    assert hook.exists()
    assert os.access(hook, os.X_OK)  # Cline hooks are executed files
    assert "volunteer-turn.sh cline" in hook.read_text()


def test_install_cline_refuses_overwrite_of_foreign_hook(tmp_path):
    """A pre-existing cline UserPromptSubmit hook that is NOT ours is
    refused (never overwritten)."""
    hook = tmp_path / ".cline" / "hooks" / "UserPromptSubmit"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/usr/bin/env bash\necho user-authored\n")
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    r = _run(["install", "cline", "--dir", str(tmp_path)], env)
    assert r.returncode == 1
    assert "refusing" in r.stderr.lower()
    assert "user-authored" in hook.read_text()  # untouched
    # --uninstall must refuse too (ownership check).
    r = _run(["install", "cline", "--dir", str(tmp_path), "--uninstall"], env)
    assert r.returncode == 1
    assert hook.exists()


def test_install_cline_uninstall_removes_own_hook(tmp_path):
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    _run(["install", "cline", "--dir", str(tmp_path)], env)
    hook = tmp_path / ".cline" / "hooks" / "UserPromptSubmit"
    assert hook.exists()
    r = _run(["install", "cline", "--dir", str(tmp_path), "--uninstall"], env)
    assert r.returncode == 0, r.stderr
    assert not hook.exists()


def test_install_refuses_symlink_escape(tmp_path):
    """A .claude/settings.json symlinked OUTSIDE the install dir is refused
    — a repo symlink must not write through to a real user file."""
    outside = tmp_path / "outside" / "settings.json"
    outside.parent.mkdir(parents=True)
    outside.write_text('{"hooks": {}}')
    target_dir = tmp_path / "proj" / ".claude"
    target_dir.mkdir(parents=True)
    (target_dir / "settings.json").symlink_to(outside)
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    r = _run(["install", "claude", "--dir", str(target_dir.parent)], env)
    assert r.returncode == 1
    assert "Refusing" in r.stderr
    assert "volunteer-turn.sh" not in outside.read_text()  # untouched


def test_install_refuses_symlinked_intermediate_dir(tmp_path):
    """A symlinked INTERMEDIATE dir (.cline/ → outside) must refuse even when
    the leaf file does not exist yet — no write-through outside the root."""
    outside = tmp_path / "outside-hooks"
    outside.mkdir()
    proj = tmp_path / "proj"
    (proj / ".cline").parent.mkdir(parents=True, exist_ok=True)
    (proj / ".cline").symlink_to(outside)
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    r = _run(["install", "cline", "--dir", str(proj)], env)
    assert r.returncode == 1
    assert "Refusing" in r.stderr
    assert not (outside / "hooks" / "UserPromptSubmit").exists()  # untouched


def test_install_cline_refuses_directory_target(tmp_path):
    """A directory sitting at the cline hook path is a clean refusal, not a
    traceback."""
    hook = tmp_path / ".cline" / "hooks" / "UserPromptSubmit"
    hook.mkdir(parents=True)
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    r = _run(["install", "cline", "--dir", str(tmp_path)], env)
    assert r.returncode == 1
    assert "is a directory" in r.stderr
    assert "Traceback" not in r.stderr


def test_volunteer_turn_hook_no_interpreter_fails_open(tmp_path):
    """No tortoise + no python3 resolvable → the hook exits 0 with empty
    output (fail-open preserved even when the reflex cannot run at all).
    PATH is kept to coreutils only (python3 lives in /usr/bin on macOS and
    cannot be split out of PATH); TORTOISE_SRC_DIR is redirected to an empty
    dir so the repo .venv fallbacks are never found."""
    coreutils = tmp_path / "coreutils"
    coreutils.mkdir()
    for tool, path in (("cat", "/bin/cat"), ("bash", "/bin/bash"),
                       ("tr", "/usr/bin/tr"), ("head", "/usr/bin/head"),
                       ("dirname", "/usr/bin/dirname")):
        (coreutils / tool).symlink_to(path)
    empty_src = tmp_path / "empty-src"
    empty_src.mkdir()
    env = {
        **os.environ,
        "PATH": str(coreutils),
        "HOME": str(tmp_path / "home"),
        "TORTOISE_SRC_DIR": str(empty_src),
        "VIRTUAL_ENV": "",
        "TORTOISE_DB_URI": "",
        "TORTOISE_DB_PATH": str(tmp_path / "nope.db"),
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }
    r = _hook_run(env, "codex", json.dumps({"prompt": "hi"}), tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


# ── 3. The SHIPPED volunteer-turn.sh per-harness output contracts ───────


def _hook_run(env, harness: str, stdin: str, tmp: Path):
    return subprocess.run(
        [_HOOK, harness],
        input=stdin,
        env=env,
        cwd=str(tmp),
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_volunteer_turn_hook_codex_contract(env, tmp_path):
    """Codex/Claude UserPromptSubmit stdin JSON → hookSpecificOutput with
    additionalContext carrying the reflex block."""
    stdin = json.dumps(
        {"prompt": "How is CI deploy triggered?", "hook_event_name": "UserPromptSubmit"}
    )
    r = _hook_run(env, "codex", stdin, tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "point/" in ctx and "CI pipeline deploys" in ctx


def test_volunteer_turn_hook_cline_contract(env, tmp_path):
    """Cline UserPromptSubmit → contextModification.context."""
    stdin = json.dumps({"prompt": "How is CI deploy triggered?"})
    r = _hook_run(env, "cline", stdin, tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "point/" in out["contextModification"]["context"]


def test_volunteer_turn_hook_fail_open_empty_graph(tmp_path):
    """Empty graph → the hook emits NOTHING and exits 0 (content fail-open:
    the turn proceeds untouched when Tortoise has nothing to say)."""
    env = _isolated(tmp_path)
    env["TORTOISE_DB_PATH"] = str(tmp_path / "empty.db")
    r = _hook_run(env, "codex", json.dumps({"prompt": "hi there"}), tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


# ── #2383 post-merge bug-hunt regressions ─────────────────────────────────
# (found by fresh-context reviewers on the merged wave-1 code)

def test_install_uninstall_when_absent_is_clean_noop(tmp_path):
    """--uninstall with NO registration must be a clean no-op — it must NOT
    invert into a fresh install (the pre-#2383 bug registered a per-turn
    hook on double-uninstall)."""
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    for harness, rel in (("codex", ".codex/hooks.json"),
                         ("claude", ".claude/settings.json"),
                         ("cline", ".cline/hooks/UserPromptSubmit")):
        target = tmp_path / rel
        r = _run(["install", harness, "--dir", str(tmp_path), "--uninstall"],
                 env)
        assert r.returncode == 0, (harness, r.stderr)
        assert "nothing to remove" in r.stdout, (harness, r.stdout)
        assert "Installed" not in r.stdout, harness  # the inversion bug
        assert not target.exists(), (harness, "no file may be created")
        # Double-uninstall after a real install also stays a clean no-op.
        _run(["install", harness, "--dir", str(tmp_path)], env)
        assert target.exists(), harness
        _run(["install", harness, "--dir", str(tmp_path), "--uninstall"], env)
        if harness == "cline":
            assert not target.exists(), harness  # cline unlinks its file
        else:
            cfg = json.loads(target.read_text())
            assert "UserPromptSubmit" not in (cfg.get("hooks") or {}), harness
        r = _run(["install", harness, "--dir", str(tmp_path), "--uninstall"],
                 env)
        assert r.returncode == 0 and "nothing to remove" in r.stdout, harness
        if harness == "cline":
            assert not target.exists(), harness


def test_install_cline_uninstall_absent_no_traceback(tmp_path):
    """cline --uninstall with no hook file: clean message, no FileNotFound
    traceback (the pre-#2383 raw traceback path)."""
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    r = _run(["install", "cline", "--dir", str(tmp_path), "--uninstall"], env)
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr
    assert "nothing to remove" in r.stdout


def test_install_codex_repairs_stale_script_path(tmp_path):
    """A registration pointing at a STALE (moved/dead) volunteer-turn.sh
    path is repaired in place on reinstall — not left as a silent dead hook
    while install reports success."""
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    stale = "/old/vanished/path/volunteer-turn.sh codex"
    cfg = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
                                                      "command": stale}]}]}}
    target = tmp_path / ".codex" / "hooks.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(cfg))
    r = _run(["install", "codex", "--dir", str(tmp_path)], env)
    assert r.returncode == 0, r.stderr
    assert "Repaired" in r.stdout
    cfg = json.loads(target.read_text())
    cmd = cfg["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert cmd != stale
    assert cmd.endswith("volunteer-turn.sh codex")
    assert Path(cmd.split()[0]).exists()  # points at the live shipped hook


def test_install_claude_repairs_stale_script_path(tmp_path):
    """Same stale-path repair for the claude wrapper shape."""
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    stale = "/gone/volunteer-turn.sh claude"
    cfg = {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": stale}]}]}}
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(cfg))
    r = _run(["install", "claude", "--dir", str(tmp_path)], env)
    assert r.returncode == 0, r.stderr
    cfg = json.loads(target.read_text())
    cmd = cfg["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert cmd != stale and cmd.endswith("volunteer-turn.sh claude")


def test_install_uninstall_dangling_symlink_no_inversion(tmp_path):
    """R2 P1: --uninstall against a DANGLING in-root symlink target must be
    a clean no-op — the R1 guard's is_symlink() carve-out let it fall
    through to the fresh-install write (re-inverting the #2383 bug)."""
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    for harness, rel in (("codex", ".codex/hooks.json"),
                         ("claude", ".claude/settings.json")):
        target = tmp_path / rel
        target.parent.mkdir(parents=True)
        target.symlink_to(target.parent / "real-config" / "hooks.json")
        assert target.is_symlink() and not target.exists()  # dangling
        r = _run(["install", harness, "--dir", str(tmp_path), "--uninstall"],
                 env)
        assert r.returncode == 0, (harness, r.stderr)
        assert "nothing to remove" in r.stdout, (harness, r.stdout)
        assert "Installed" not in r.stdout, (harness, "inversion bug")
        dest = target.parent / "real-config" / "hooks.json"
        assert not dest.exists(), (harness, "no registration may be created")


def test_install_codex_leaves_user_wrapper_untouched(tmp_path):
    """R2 (security): an entry that WRAPS our script (firejail/venv-pinned)
    — merely CONTAINING the substring — must NEVER be rewritten on
    reinstall; the pre-R2 repair replaced the whole command and silently
    stripped the security wrapper."""
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    wrapped = ("'/usr/local/bin/firejail' "
               "'/opt/tortoise/volunteer-turn.sh' codex")
    cfg = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
                                                      "command": wrapped}]}]}}
    target = tmp_path / ".codex" / "hooks.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(cfg))
    r = _run(["install", "codex", "--dir", str(tmp_path)], env)
    assert r.returncode == 0, r.stderr
    cfg = json.loads(target.read_text())
    cmd = cfg["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert cmd == wrapped  # untouched — wrapper preserved
    assert "Repaired" not in r.stdout
    assert len(cfg["hooks"]["UserPromptSubmit"]) == 1  # no duplicate


def test_install_codex_leaves_foreign_volunteer_hook_untouched(tmp_path):
    """R2 (security): another product's OWN live volunteer-turn.sh hook at a
    different path must not be hijacked by the repair."""
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    # A LIVE foreign volunteer engine hook at another path (still running) —
    # structurally identical to ours (2 tokens, harness word) but serving a
    # different engine: must not be hijacked by the repair.
    foreign_dir = tmp_path / "gbrain"
    foreign_dir.mkdir()
    (foreign_dir / "volunteer-turn.sh").write_text("#!/usr/bin/env bash\n")
    foreign = f"'{foreign_dir / 'volunteer-turn.sh'}' codex"
    cfg = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
                                                      "command": foreign}]}]}}
    target = tmp_path / ".codex" / "hooks.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(cfg))
    r = _run(["install", "codex", "--dir", str(tmp_path)], env)
    assert r.returncode == 0, r.stderr
    cfg = json.loads(target.read_text())
    cmd = cfg["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert cmd == foreign  # live foreign hook untouched
    assert len(cfg["hooks"]["UserPromptSubmit"]) == 1


# ── #2369 per-turn reflex trust boundary (co-sourced identity) ──────────
# (trust-posture hardening: file-sourced keys never take their destination
# from env; a repo .tortoise can never supply a transmitting identity; a
# hosted-against-file run prints a stderr endpoint-mode note.)


class _RecordingStub(BaseHTTPRequestHandler):
    """Localhost HTTP stub that records (path, Authorization) per hit and
    answers JSON. Subclass per test to set `body` + a fresh `hits` list.

    Hosted reflex runs POST /v1/context and print the returned "block";
    hosted team commands GET /v1/team. The child subprocess connects to
    the loopback port the test serves on — hermetic, no real network.
    """
    body: ClassVar[dict] = {"block": "stub"}
    hits: ClassVar[list] = []

    def _answer(self):
        self.hits.append({
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body_len": int(self.headers.get("Content-Length") or 0),
        })
        payload = json.dumps(self.body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        self._answer()

    def do_GET(self):
        self._answer()

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def _stub_server(handler):
    """Serve `handler` on an ephemeral loopback port until the context ends."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _write_global_credentials(home: Path, api_key: str, api_url: str) -> Path:
    """Write the user-global ~/.tortoise/credentials.json (signup shape)."""
    d = home / ".tortoise"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "credentials.json"
    f.write_text(json.dumps({"api_key": api_key, "api_url": api_url}))
    f.chmod(0o600)
    return f


def _trust_env(tmp_path: Path, home: Path | None = None) -> dict:
    """Isolated-HOME child env for trust tests: no real identity leaks in,
    no docker lane, empty api-key vars for the test to poison deliberately."""
    env = {
        **os.environ,
        "TORTOISE_DB_URI": "",
        "HOME": str(home or (tmp_path / "home")),
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
        "_SEAM_CWD": str(tmp_path),
    }
    env.pop("TORTOISE_API_KEY", None)
    env.pop("TORTOISE_API_URL", None)
    return env


class TestVolunteerTrustBoundary:
    """#2369 D1: the per-turn reflex identity is ONE source chain."""

    def test_poisoned_env_url_cannot_redirect_file_sourced_key(self, tmp_path):
        """(a) A poisoned TORTOISE_API_URL with a FILE-sourced key must NOT
        redirect: the reflex POSTs to the file's own api_url (a live stub),
        the live attacker recorder sees zero requests, and stdout carries
        the legit stub's block — not the attacker's."""
        legit = type("Legit", (_RecordingStub,), {
            "body": {"block": "legit-stub-block"}, "hits": []})
        attacker = type("Attacker", (_RecordingStub,), {
            "body": {"block": "attacker-block"}, "hits": []})
        with _stub_server(legit) as legit_url, _stub_server(attacker) as atk_url:
            home = tmp_path / "home"
            _write_global_credentials(home, "tt_file_key", legit_url)
            env = _trust_env(tmp_path, home)
            env["TORTOISE_API_URL"] = atk_url  # the poisoned override
            r = _run(["volunteer"], env, stdin="How is CI deploy triggered?")
        assert r.returncode == 0, r.stderr
        assert "legit-stub-block" in r.stdout  # answered by the FILE's URL
        assert "attacker-block" not in r.stdout  # never redirected
        assert attacker.hits == [], "poisoned env URL must never be POSTed"
        assert len(legit.hits) == 1
        assert legit.hits[0]["auth"] == "Bearer tt_file_key"
        assert legit.hits[0]["body_len"] > 0  # the prompt window went to the file URL

    def test_env_key_env_url_pair_still_honored(self, tmp_path):
        """(b) The allowed case: when the KEY also came from env, the env
        TORTOISE_API_URL override is honored — an env identity is one chain.
        A stored file identity (different host) must NOT win over the env
        pair for env-identity surfaces (team info reads the shared resolver)."""
        env_stub = type("EnvStub", (_RecordingStub,), {
            "body": {"team_id": "team-e", "tier": "free", "point_count": 0},
            "hits": []})
        file_stub = type("FileStub", (_RecordingStub,), {
            "body": {"team_id": "team-f", "tier": "free", "point_count": 0},
            "hits": []})
        with _stub_server(env_stub) as env_url, _stub_server(file_stub) as file_url:
            home = tmp_path / "home"
            _write_global_credentials(home, "tt_file_key", file_url)
            env = _trust_env(tmp_path, home)
            env["TORTOISE_API_KEY"] = "tt_env_key"
            env["TORTOISE_API_URL"] = env_url
            r = _run(["team", "info"], env)
        assert r.returncode == 0, r.stderr
        assert "Team:       team-e" in r.stdout  # answered by the ENV URL
        assert len(env_stub.hits) == 1
        assert env_stub.hits[0]["auth"] == "Bearer tt_env_key"
        assert file_stub.hits == [], "file identity must not shadow the env pair"

    def test_repo_tortoise_cannot_flip_reflex_hosted_without_global(self, tmp_path):
        """(c) A repo-shipped .tortoise (attacker key+URL) with NO user-global
        config must NOT flip the reflex to hosted: it degrades to local
        (no-send, fail-open — clean silence, exit 0), even with a poisoned
        env URL pointing at the attacker."""
        attacker = type("Attacker", (_RecordingStub,), {
            "body": {"block": "attacker-block"}, "hits": []})
        repo = tmp_path / "repo"
        repo.mkdir()
        with _stub_server(attacker) as atk_url:
            (repo / ".tortoise").write_text(json.dumps({
                "api_key": "tt_repo_key", "api_url": atk_url}))
            env = _trust_env(tmp_path, tmp_path / "home-empty")
            env["TORTOISE_API_URL"] = atk_url  # poison, must be irrelevant
            env["TORTOISE_DB_PATH"] = str(tmp_path / "empty.db")
            r = _run(["volunteer"], env, stdin="hello", cwd=str(repo))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == ""  # local clean silence — nothing injected
        assert attacker.hits == [], "repo .tortoise must never authorize a POST"
        assert "tortoise: hosted-mode note:" not in r.stderr

    def test_repo_tortoise_cannot_override_user_global_identity(self, tmp_path):
        """(c) Precedence: repo .tortoise (attacker) + user-global config
        (legit) → the reflex transmits with the USER-GLOBAL identity only —
        the legit stub is hit with the global key, the attacker sees zero."""
        legit = type("Legit", (_RecordingStub,), {
            "body": {"block": "legit-stub-block"}, "hits": []})
        attacker = type("Attacker", (_RecordingStub,), {
            "body": {"block": "attacker-block"}, "hits": []})
        repo = tmp_path / "repo"
        repo.mkdir()
        with _stub_server(legit) as legit_url, _stub_server(attacker) as atk_url:
            (repo / ".tortoise").write_text(json.dumps({
                "api_key": "tt_repo_key", "api_url": atk_url}))
            home = tmp_path / "home"
            _write_global_credentials(home, "tt_global_key", legit_url)
            env = _trust_env(tmp_path, home)
            r = _run(["volunteer"], env, stdin="hello", cwd=str(repo))
        assert r.returncode == 0, r.stderr
        assert "legit-stub-block" in r.stdout
        assert attacker.hits == [], "repo .tortoise identity must never be used"
        assert len(legit.hits) == 1
        assert legit.hits[0]["auth"] == "Bearer tt_global_key"

    def test_hosted_against_file_emits_stderr_endpoint_note(self, tmp_path):
        """(d) A hosted-against-file run prints the run-time endpoint-mode
        note on stderr (naming the endpoint + identity file), so a future
        redirect is visible at run time. stdout stays the clean injection
        channel — the note never pollutes the block."""
        legit = type("Legit", (_RecordingStub,), {
            "body": {"block": "legit-stub-block"}, "hits": []})
        with _stub_server(legit) as legit_url:
            home = tmp_path / "home"
            cfg = _write_global_credentials(home, "tt_file_key", legit_url)
            env = _trust_env(tmp_path, home)
            r = _run(["volunteer"], env, stdin="hello")
        assert r.returncode == 0, r.stderr
        assert "legit-stub-block" in r.stdout
        # The note names the endpoint mode (host label) and the identity file.
        assert "tortoise: hosted-mode note: volunteer:" in r.stderr
        assert "hosted (127.0.0.1:" in r.stderr
        assert str(cfg) in r.stderr  # identity file named
        assert "attacker" not in r.stderr
