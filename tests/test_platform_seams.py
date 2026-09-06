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

import json
import os
import subprocess
import sys
from pathlib import Path

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
