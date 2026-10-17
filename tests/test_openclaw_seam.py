"""#2120 — the OpenClaw two-way seam (`tortoise/openclaw-plugin/`).

The seam is a native OpenClaw PLUGIN, not a shell hook: OpenClaw's per-turn
surface is the in-process `before_prompt_build` hook (returns
``{prependContext}``) and its capture surface is the ``llm_output``
observation.  The `docs/research/2026-09-01-gbrain-learnings/platform-seams.md`
row that named ``~/.openclaw/*.claw`` + ``handle_AgentRequest`` described a
contract that does not exist anywhere in ``openclaw/openclaw``; these tests pin
the VERIFIED contract instead, and pin the correction itself so the fabricated
one cannot come back.

What is graded here:

* the shipped artifact — the manifest OpenClaw validates before loading code,
  the entry ``package.json#openclaw.extensions`` declares, and the version
  marker ``tortoise session verify`` / ``doctor`` read;
* the installer — into a temp HOME, dry-run first, idempotent, with the
  ownership / deny / malformed-config refusals;
* the capture half — the spool contract the plugin writes and
  ``parse_openclaw`` reads, pinned from BOTH sides (this file and the plugin's
  own ``node --test`` suite, which is executed when Node >= 22.18 is present);
* the packaging — every shipped plugin file is matched by a package-data
  pattern, so a wheel install ships a plugin OpenClaw can load.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tortoise import capture_install, hook_install
from tortoise.capture_install import install_capture, openclaw_home
from tortoise.session_import import PARSERS, parse_openclaw, parse_transcript

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "tortoise" / "openclaw-plugin"
ENTRY = PLUGIN_DIR / "index.mjs"
MANIFEST = PLUGIN_DIR / "openclaw.plugin.json"
PACKAGE = PLUGIN_DIR / "package.json"
NODE_TEST = PLUGIN_DIR / "index.test.mjs"

#: The exact in-repo artifact path the seam map declares (and the dashboard's
#: `HARNESS_CAPTURE_SEAM` mirrors — pinned by
#: ``tests/test_capture_install.py::test_capture_seam_matches_the_dashboard_seam_map_and_timeout``).
SEAM_ARTIFACT = "tortoise/openclaw-plugin/index.mjs"

#: The plugin's own test suite needs the .mjs loader, which is unconditional on
#: modern Node (>= 22.18 enables type stripping + module detection by default).
_NODE_MIN = (22, 18)


def _node_version() -> tuple[int, int] | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True,
                             timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


# ── the shipped artifact ────────────────────────────────────────────────


def _src() -> str:
    return ENTRY.read_text(encoding="utf-8")


def test_entry_carries_exactly_one_version_marker():
    """The version contract reads ``index.mjs`` — so it must declare ONE
    column-0 marker, and ``contract_version_for`` must read it back."""
    assert ENTRY.is_file(), f"seam artifact missing: {ENTRY}"
    assert hook_install.count_canonical_markers(ENTRY) == 1, (
        "index.mjs must declare exactly one `// tortoise-hook-version: N` line — "
        "the detector reads the FIRST match and a second marker is drift")
    shipped = hook_install.contract_version_for("openclaw")
    assert isinstance(shipped, int) and shipped >= 1, (
        f"the shipped OpenClaw seam declares no readable generation: {shipped}")
    assert hook_install.ARTIFACT_CONTRACTS["openclaw"].source == ENTRY


def test_manifest_is_a_valid_native_openclaw_manifest():
    """OpenClaw reads `openclaw.plugin.json` BEFORE loading plugin code and
    treats a missing/invalid manifest as a plugin error — so the id and the
    config schema are the install contract, not documentation."""
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert doc["id"] == capture_install.OPENCLAW_PLUGIN_DIRNAME, (
        "the manifest id and the install directory name are one identity in "
        "OpenClaw — a mismatch leaves `plugins.entries.<id>` pointing at a "
        "plugin that never loads")
    schema = doc["configSchema"]
    assert schema["type"] == "object"
    # `additionalProperties: false` is the file's own contract: a typo'd config
    # key is rejected at validate time instead of silently ignored at runtime.
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) >= {
        "apiUrl", "apiKey", "command", "timeoutMs", "capture"}


def test_package_json_declares_the_entry_the_installer_writes():
    """`openclaw.plugin.json` carries no runtime entrypoint — OpenClaw reads
    that from ``package.json#openclaw.extensions``, which must stay inside the
    plugin directory and name the file the version contract grades."""
    doc = json.loads(PACKAGE.read_text(encoding="utf-8"))
    entries = doc["openclaw"]["extensions"]
    assert entries == [f"./{ENTRY.name}"], (
        f"package.json#openclaw.extensions must declare the shipped entry "
        f"(got {entries!r}, expected ['./{ENTRY.name}'])")
    assert doc["type"] == "module", "the entry is ESM (`export default`)"


def _fabricated_mentions_are_corrections(text: str, doc: str) -> None:
    """A seam doc may NAME the fabricated contract only while correcting it.

    The correction has to be able to say what was wrong (that is the whole
    point of the record), so "the string is absent" is the wrong check — the
    check is that no line presents it as the contract.  A line mentioning the
    fabricated symbols must carry a correction marker in the same line.
    """
    markers = re.compile(
        r"\b(no|not|never|wrong|absent|fabricat|correct|does not exist)\b",
        re.IGNORECASE)
    # Paragraphs, not lines: prose wraps, and a wrapped sentence that ends
    # "…does not exist" on the next line is still a correction.
    for block in re.split(r"\n\s*\n", text):
        if "handle_AgentRequest" not in block and ".claw" not in block:
            continue
        assert markers.search(block), (
            "a seam doc names the fabricated OpenClaw contract without "
            "marking it as corrected — the next reader would implement it: "
            f"{block.strip()[:160]!r}")


def test_plugin_registers_the_verified_hooks_and_no_fabricated_contract():
    """The VERIFIED contract: `before_prompt_build` → `prependContext` for the
    per-turn injection, `llm_output` for capture. The fabricated
    `~/.openclaw/*.claw` / `handle_AgentRequest` pair must never be presented
    as the contract — it was a research-doc error, and its return would
    re-break the seam."""
    src = _src()
    assert 'api.on("before_prompt_build"' in src
    assert "prependContext" in src
    assert 'api.on("llm_output"' in src
    assert "assistantTexts" in src
    assert 'API.on("before_prompt_build"' not in src
    _fabricated_mentions_are_corrections(src, ENTRY.name)


def test_fail_open_contract_is_explicit():
    """Content fail-open is the seam's contract (#2120 targets: a failed reflex
    never breaks the turn). Every hook body is wrapped and returns nothing on
    failure; nothing in the entry throws into the host."""
    src = _src()
    inject = src.index('api.on("before_prompt_build"')
    capture = src.index('api.on("llm_output"')
    inject_body = src[inject:capture]
    capture_body = src[capture:]
    assert "try {" in inject_body and "catch" in inject_body, (
        "the inject body must be wrapped — an unwrapped raise escapes into "
        "the agent turn")
    assert "return undefined" in inject_body, (
        "the inject body must return nothing rather than rethrow")
    assert "try {" in capture_body and "catch" in capture_body, (
        "the capture body must be wrapped — a spool write failure must not "
        "break the turn")


# ── install layout ─────────────────────────────────────────────────────


def test_openclaw_home_is_the_plugin_directory_and_honours_state_dir(monkeypatch, tmp_path):
    home = tmp_path / "home"
    assert openclaw_home(home) == (home / ".openclaw" / "extensions"
                                  / capture_install.OPENCLAW_PLUGIN_DIRNAME)
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "state"))
    assert openclaw_home(home) == (tmp_path / "state" / "extensions"
                                  / capture_install.OPENCLAW_PLUGIN_DIRNAME), (
        "OPENCLAW_STATE_DIR relocates the whole state tree — an install that "
        "ignored it would write where OpenClaw never looks and report success")


def test_capture_seam_declares_the_shipped_entry():
    assert capture_install.CAPTURE_SEAM["openclaw"] == SEAM_ARTIFACT
    assert (REPO_ROOT / SEAM_ARTIFACT).is_file()
    assert "openclaw" in hook_install.ARTIFACT_CONTRACTS
    assert "openclaw" not in hook_install.HARNESS_LAYOUTS, (
        "the seam is a plugin, not a shell hook — claiming a layout would make "
        "every layout consumer act on paths that do not exist")


# ── the installer ──────────────────────────────────────────────────────


def _installed_files(home: Path) -> set[str]:
    root = openclaw_home(home)
    return {p.name for p in root.iterdir()} if root.is_dir() else set()


def test_dry_run_writes_nothing(tmp_path):
    home = tmp_path / "home"
    res = install_capture("openclaw", home=home, dry_run=True)
    assert res.ok, res.error
    assert res.changed
    assert not (home / ".openclaw").exists(), "a dry run wrote to disk"
    assert any("[dry-run]" in a for a in res.actions)


def test_install_writes_the_plugin_and_enables_it(tmp_path):
    home = tmp_path / "home"
    res = install_capture("openclaw", home=home)
    assert res.ok, res.error
    assert res.changed
    shipped = {p.name for p in PLUGIN_DIR.iterdir() if p.is_file()}
    assert _installed_files(home) == shipped - {NODE_TEST.name}, (
        "the installer must copy EVERY shipped plugin file (a forgotten file "
        "is a plugin OpenClaw cannot load) and must NOT ship the test suite")
    state = json.loads((home / ".openclaw" / "openclaw.json").read_text())
    assert state["plugins"]["entries"]["tortoise"]["enabled"] is True, (
        "discovered is not loaded — the install must enable the plugin")
    # the installed copy is what the version contract grades
    root = openclaw_home(home)
    findings = hook_install.detect_artifact_install(root, "openclaw")
    assert [f for f in findings if f.blocking] == [], findings


def test_install_is_idempotent_and_keeps_foreign_config(tmp_path):
    home = tmp_path / "home"
    settings = home / ".openclaw" / "openclaw.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "plugins": {"entries": {"other": {"enabled": True, "config": {"x": 1}}}},
        "unrelated": {"keep": "me"},
    }))
    first = install_capture("openclaw", home=home)
    assert first.ok, first.error
    second = install_capture("openclaw", home=home)
    assert second.ok, second.error
    assert second.changed is False, "a re-run rewrote the install"
    assert second.actions == ()
    state = json.loads(settings.read_text())
    assert state["unrelated"] == {"keep": "me"}
    assert state["plugins"]["entries"]["other"] == {
        "enabled": True, "config": {"x": 1}}, (
        "another plugin's entry was clobbered")


def test_upgrade_backs_up_a_differing_our_file(tmp_path):
    home = tmp_path / "home"
    assert install_capture("openclaw", home=home).ok
    entry = openclaw_home(home) / capture_install.OPENCLAW_ENTRY_NAME
    entry.write_text("// tortoise-hook-version: 0\n// stale\n")
    res = install_capture("openclaw", home=home)
    assert res.ok, res.error
    assert res.changed
    backups = list(entry.parent.glob(f"{entry.name}.bak*"))
    assert len(backups) == 1, res.actions
    assert "stale" in backups[0].read_text(), "the previous copy was not preserved"
    assert entry.read_text() == ENTRY.read_text()


def test_foreign_entry_is_refused(tmp_path):
    home = tmp_path / "home"
    entry = openclaw_home(home) / capture_install.OPENCLAW_ENTRY_NAME
    entry.parent.mkdir(parents=True)
    entry.write_text("export default function someoneElsesPlugin() {}\n")
    res = install_capture("openclaw", home=home)
    assert not res.ok
    assert "does not look like a Tortoise artifact" in res.error
    assert entry.read_text().startswith("export default function someoneElse")


def test_plugins_deny_is_authoritative_and_refused(tmp_path):
    """OpenClaw treats a `plugins.deny` entry as authoritative: it will not
    load the plugin however we write `enabled`. Writing it anyway would install
    an inert plugin and report success (#4314 class), so the installer refuses
    and names the fix."""
    home = tmp_path / "home"
    settings = home / ".openclaw" / "openclaw.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"plugins": {"deny": ["tortoise"]}}))
    res = install_capture("openclaw", home=home)
    assert not res.ok
    assert "plugins.deny" in res.error and "openclaw.json" in res.error
    assert json.loads(settings.read_text()) == {"plugins": {"deny": ["tortoise"]}}
    assert not openclaw_home(home).exists(), "the refusal still wrote the plugin"


def test_restrictive_allow_list_is_widened_and_reported(tmp_path):
    """A restrictive `plugins.allow` without `tortoise` is the other silent
    inert path: discovered, configured, never loaded."""
    home = tmp_path / "home"
    settings = home / ".openclaw" / "openclaw.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"plugins": {"allow": ["other"]}}))
    res = install_capture("openclaw", home=home)
    assert res.ok, res.error
    state = json.loads(settings.read_text())
    assert state["plugins"]["allow"] == ["other", "tortoise"]
    assert any("plugins.allow" in a for a in res.actions), (
        "widening an access list must be REPORTED, never silent")


def test_malformed_config_is_refused_not_clobbered(tmp_path):
    home = tmp_path / "home"
    settings = home / ".openclaw" / "openclaw.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json")
    res = install_capture("openclaw", home=home)
    assert not res.ok
    assert "not valid JSON" in res.error
    assert settings.read_text() == "{not json"


def test_symlinked_plugin_root_is_refused(tmp_path):
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (home / ".openclaw" / "extensions").mkdir(parents=True)
    (home / ".openclaw" / "extensions" / "tortoise").symlink_to(elsewhere)
    res = install_capture("openclaw", home=home)
    assert not res.ok, "an install through a symlinked root was accepted"
    assert "symlink" in res.error
    assert list(elsewhere.iterdir()) == [], "bytes landed outside the home"


def test_unknown_harness_still_refuses(tmp_path):
    res = install_capture("nope", home=tmp_path)
    assert not res.ok
    assert "no capture seam" in res.error


# ── the capture spool contract (both languages) ───────────────────────


def test_spool_round_trip_groups_sessions_and_orders_by_ts(tmp_path):
    """The plugin writes one file per session, but the spool is append-only and
    a merged/appended file must still degrade HONESTLY: the parser groups by
    session (first-seen order) and orders within each group by ``ts``."""
    spool = tmp_path / "turns.jsonl"
    spool.write_text("\n".join([
        '{"session_key":"b","role":"user","text":"b1","ts":"2026-10-11T10:00:02Z"}',
        '{"session_key":"a","role":"user","text":"a1","ts":"2026-10-11T10:00:01Z"}',
        '{"session_key":"b","role":"assistant","text":"b2","ts":"2026-10-11T10:00:03Z"}',
        '{"session_key":"a","role":"assistant","text":"a2","ts":"2026-10-11T10:00:04Z"}',
        "",  # blank line
        "not json at all",
        '{"role":"system","text":"dropped — not a kept role"}',
        '{"session_key":"a","role":"user","text":"   "}',
        '["not", "an", "object"]',
    ]) + "\n")
    assert parse_openclaw(spool) == [
        {"role": "user", "content": "b1"},
        {"role": "assistant", "content": "b2"},
        {"role": "user", "content": "a1"},
        {"role": "assistant", "content": "a2"},
    ]
    # registered, and reachable through the dispatcher `sessions import` uses
    assert PARSERS["openclaw"] is parse_openclaw
    assert parse_transcript(spool, "openclaw") == parse_openclaw(spool)


def test_spool_falls_back_to_session_id_and_tolerates_a_missing_file(tmp_path):
    spool = tmp_path / "turns.jsonl"
    spool.write_text('{"session_id":"s1","role":"user","text":"hi"}\n')
    assert parse_openclaw(spool) == [{"role": "user", "content": "hi"}]
    assert parse_openclaw(tmp_path / "absent.jsonl") == []


def test_empty_spool_parses_to_no_turns(tmp_path):
    spool = tmp_path / "turns.jsonl"
    spool.write_text("")
    assert parse_openclaw(spool) == []


# ── the plugin's own behavioural suite ─────────────────────────────────


@pytest.mark.skipif(
    _node_version() is None or _node_version() < _NODE_MIN,
    reason="node >= 22.18 required for the .mjs test runner (esm + node:test)")
def test_plugin_suite_passes():
    """Runs the REAL handlers the installed plugin registers (into a temp
    state dir): the reflex block becomes ``prependContext``, a broken reflex
    injects nothing, and the captured turn lands in the spool."""
    node = shutil.which("node")
    with tempfile.TemporaryDirectory() as tmp:
        # The plugin and the node suite both read …_SPOOL_DIR (a DIRECTORY). The
        # previous name read by nothing, so the comment's promise of a temp
        # state dir was delivered only by the node test's own override.
        env = {**os.environ, "TORTOISE_OPENCLAW_SPOOL_DIR": str(Path(tmp))}
        proc = subprocess.run(
            [node, "--test", str(NODE_TEST)],
            capture_output=True, text=True, timeout=300, env=env,
            cwd=str(PLUGIN_DIR))
    assert proc.returncode == 0, (
        f"the seam's own suite failed:\n{proc.stdout}\n{proc.stderr}")


# ── packaging ──────────────────────────────────────────────────────────


def test_package_data_ships_every_plugin_file():
    """A wheel install resolves the plugin from the PACKAGE directory — a file
    missing from package-data fails the install on the user's machine, not in
    CI."""
    import tomllib
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    patterns = data["tool"]["setuptools"]["package-data"]["tortoise"]
    import fnmatch
    for path in sorted(PLUGIN_DIR.iterdir()):
        if not path.is_file() or path.name == NODE_TEST.name:
            continue
        rel = f"openclaw-plugin/{path.name}"
        assert any(fnmatch.fnmatch(rel, p) for p in patterns), (
            f"{rel} is not matched by any package-data pattern {patterns} — a "
            "wheel install would ship an incomplete plugin")


def test_no_fabricated_contract_survives_in_the_seam_docs():
    """The re-measured contract lives in the shipped README and the research
    matrix; the fabricated one may only appear inside a correction, never as
    the contract — otherwise the next lane re-implements it."""
    for doc in (PLUGIN_DIR / "README.md",
                REPO_ROOT / "docs/research/2026-09-01-gbrain-learnings/platform-seams.md"):
        text = doc.read_text(encoding="utf-8")
        _fabricated_mentions_are_corrections(text, doc.name)
        assert "before_prompt_build" in text, (
            f"{doc.name} must state the VERIFIED per-turn hook")


def test_cli_install_openclaw_end_to_end(tmp_path):
    """`tortoise install openclaw` — the agent-first onboarding surface the
    issue mandates (`tortoise install openclaw`, not a docs page)."""
    env = {**os.environ, "HOME": str(tmp_path), "TORTOISE_CAPTURE": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise", "install", "openclaw"],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    root = openclaw_home(tmp_path)
    assert (root / capture_install.OPENCLAW_ENTRY_NAME).is_file()
    assert "Gateway startup" in proc.stdout, (
        "an install the gateway will not load until restart must say so, or a "
        "fail-open seam reads as inert")
    # ...and re-running reports the no-op instead of a fabricated install
    again = subprocess.run(
        [sys.executable, "-m", "tortoise", "install", "openclaw"],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(REPO_ROOT))
    assert again.returncode == 0
    assert "already installed — nothing to do" in again.stdout


def test_install_guidance_names_the_per_session_spool(tmp_path):
    """The install surface must name the actual spool AND the per-file import
    loop: a printed success over a spool nobody files is the #3808 R13 class,
    and `sessions import` takes ONE session per invocation."""
    env = {**os.environ, "HOME": str(tmp_path), "TORTOISE_CAPTURE": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise", "install", "openclaw"],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "tortoise/turns/" in proc.stdout
    assert "sessions import --harness openclaw" in proc.stdout

    # #2120 review P2: asserting the message MENTIONS the command is a test of
    # copy, not of capability — it stayed green while `sessions import
    # --harness openclaw` exited 2 ("invalid choice"), because `sessions
    # import` kept its own hand-maintained harness list. Execute the printed
    # command so a second such list cannot silently re-appear: the probe is
    # expected to fail later (no such transcript, no server) but never on
    # harness admission.
    probe = subprocess.run(
        [sys.executable, "-m", "tortoise", "sessions", "import",
         "--harness", "openclaw", "--file", str(tmp_path / "absent.jsonl")],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(REPO_ROOT))
    out = probe.stdout + probe.stderr
    assert "invalid choice" not in out, \
        f"sessions import refuses the harness the install guidance prints:\n{out}"
    # ...and assert the LEGITIMATE later failure too: a CLI that broke earlier
    # (an import error, a traceback) satisfies the check above on its own, which
    # is the same vacuous pass in a new place. The probe names an absent
    # transcript, so this is the only acceptable outcome.
    assert probe.returncode == 1 and "Session file not found" in out, \
        f"expected the absent-file failure, got rc={probe.returncode}:\n{out}"


def test_session_verify_reaches_openclaw_and_is_honestly_unverifiable():
    """`session verify --harness openclaw` must RESOLVE (the harness is in the
    derived registry) and must not claim to have fired an in-process plugin."""
    from tortoise import session_verify

    assert "openclaw" in session_verify.HARNESSES
    assert session_verify.HEADLESS_FIRABLE["openclaw"] is False
    reason = session_verify.UNVERIFIABLE_REASON["openclaw"]
    assert "not firable by this command" in reason
    assert "index.mjs" in reason
