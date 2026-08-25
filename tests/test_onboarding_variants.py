"""Integrity tests for harness onboarding variants (epic #529, issues #966/#967).

Guarantees the wrapper-not-fork contract: variant headers carry delivery
instructions only (never the question flow), and staged artifacts embed the
canonical AGENT_ONBOARDING.md body verbatim. Drift here means users get a
forked onboarding flow per harness — the exact failure epic #529 prevents.
"""
from __future__ import annotations

import os  # noqa: F401
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ONBOARDING_DIR = REPO_ROOT / "tortoise" / "onboarding"
CANONICAL = ONBOARDING_DIR / "AGENT_ONBOARDING.md"
VARIANTS_DIR = ONBOARDING_DIR / "variants"
STAGE_SCRIPT = ONBOARDING_DIR / "stage_variants.py"

HARNESSES = ("claude-code", "codex", "cursor", "pi")
# Exact contract string (04-plan Substep 6, item 4 — double quotes normative).
FALLBACK_LINE = 'If the agent doesn\'t start the flow, paste: "Start Tortoise onboarding"'


def _header(harness: str) -> str:
    return (VARIANTS_DIR / f"{harness}-header.md").read_text(encoding="utf-8")


def _how_to_use_steps(content: str) -> int:
    """Count numbered delivery steps inside the '## How to use' section."""
    match = re.search(r"^## How to use\s*$(.*?)(?=^## |\Z)", content, re.M | re.S)
    assert match, "'## How to use' section missing"
    return len(re.findall(r"^\d+\.", match.group(1), re.M))


def test_all_variant_headers_exist():
    for harness in HARNESSES:
        path = VARIANTS_DIR / f"{harness}-header.md"
        assert path.exists(), f"missing variant header: {path}"
        assert path.read_text(encoding="utf-8").strip(), f"empty header: {path}"


def test_headers_have_no_question_flow():
    """The question flow lives ONLY in the canonical body (no fork)."""
    for harness in HARNESSES:
        content = _header(harness)
        assert "## Questions" not in content, (
            f"{harness} header contains its own question flow — variants must "
            "wrap the canonical body, never fork it"
        )


def test_headers_have_title_and_exactly_two_delivery_steps():
    labels = {"claude-code": "Claude Code", "codex": "Codex",
              "cursor": "Cursor", "pi": "Pi"}
    for harness in HARNESSES:
        content = _header(harness)
        assert f"# Tortoise Onboarding — {labels[harness]} setup" in content, (
            f"{harness} header missing the standard title line")
        assert _how_to_use_steps(content) == 2, (
            f"{harness} header must have EXACTLY 2 numbered delivery steps")


def test_chat_paste_variants_carry_exact_fallback_line():
    """Claude Code + Codex are behavioral-trigger variants (align AL-7b/R9)."""
    for harness in ("claude-code", "codex"):
        assert FALLBACK_LINE in _header(harness), (
            f"{harness} header missing the exact fallback line")


def test_claude_code_header_documents_alternatives():
    content = _header("claude-code")
    assert ".mcp.json" in content, "claude header must document the .mcp.json file alternative"
    assert "${TORTOISE_API_KEY}" in content, "claude header must show ${VAR} env expansion"
    assert "CLAUDE.md" in content, "claude header must document the CLAUDE.md persistent alternative"
    assert "one-time approval" in content, "claude header must note project-scope one-time approval"
    assert '"type": "http"' in content, "claude .mcp.json alternative must pin type:http (url without type = server skipped)"


def test_codex_header_documents_alternatives():
    content = _header("codex")
    assert "config.toml" in content, "codex header must document the config.toml snippet alternative"
    assert "AGENTS.md" in content, "codex header must document the AGENTS.md persistent alternative"
    assert "export TORTOISE_API_KEY=" in content, "codex header must carry the skip-export fix"


def test_cursor_header_names_file_and_auto_start():
    content = _header("cursor")
    assert ".cursor/rules/tortoise-onboarding.mdc" in content
    assert "automatically" in content, "cursor header must state onboarding starts automatically"
    assert ".md" in content and "ignored" in content, "cursor header must warn that plain .md is ignored"
    assert "alwaysApply: true" in content, "cursor artifact must carry the alwaysApply frontmatter"


def test_pi_header_names_file_merge_and_bootstrap():
    content = _header("pi")
    assert "AGENTS.md" in content
    assert "automatically" in content, "pi header must state onboarding starts automatically"
    assert "MERGE" in content or "merge" in content, "pi header must say merge-not-append for .mcp.json"
    assert "agent-infra" in content, "pi header must link the mcp-client bootstrap for the extension-absent case"


def test_stage_variants_embeds_canonical_verbatim(tmp_path):
    """Deploy-time concat: staged == header + separator + canonical body."""
    result = subprocess.run(
        [sys.executable, str(STAGE_SCRIPT), "--out", str(tmp_path)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"staging failed: {result.stderr}"

    canonical_body = CANONICAL.read_text(encoding="utf-8").lstrip()
    staged_canonical = (tmp_path / "onboarding-prompt.md").read_text(encoding="utf-8")
    assert staged_canonical == CANONICAL.read_text(encoding="utf-8")

    separator = "\n\n---\n\n"
    for harness in HARNESSES:
        out = tmp_path / "onboarding" / f"{harness}.md"
        assert out.exists(), f"staged variant missing: {out}"
        content = out.read_text(encoding="utf-8")
        # Canonical body embedded verbatim as suffix (drift-proof).
        assert content.endswith(canonical_body), (
            f"{harness} staged artifact does not embed the canonical body verbatim")
        # Header prefix preserved.
        header = _header(harness).rstrip()
        assert content.startswith(header), f"{harness} staged artifact lost its header"
        assert separator in content, f"{harness} staged artifact missing the separator"


def test_staged_cursor_variant_is_valid_mdc(tmp_path):
    """T10: the staged Cursor artifact must be a valid .mdc — YAML
    frontmatter with alwaysApply: true (structural trigger)."""
    import subprocess as _sp
    result = _sp.run(
        [sys.executable, str(STAGE_SCRIPT), "--out", str(tmp_path)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    content = (tmp_path / "onboarding" / "cursor.md").read_text(encoding="utf-8")
    assert content.startswith("---\n"), "cursor variant must open with YAML frontmatter"
    end = content.index("\n---\n", 4)
    frontmatter = content[4:end]
    assert "alwaysApply: true" in frontmatter, "frontmatter must set alwaysApply: true"


def test_stage_variants_fails_on_missing_header(tmp_path, monkeypatch):
    """W1 failure mode: a missing/empty header must block staging (non-zero exit)."""
    missing = VARIANTS_DIR / "pi-header.md"
    saved = missing.read_text(encoding="utf-8")
    try:
        missing.unlink()
        result = subprocess.run(
            [sys.executable, str(STAGE_SCRIPT), "--out", str(tmp_path)],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode != 0
        assert "pi-header.md" in result.stderr
    finally:
        missing.write_text(saved, encoding="utf-8")


# ── wizard copy ref tests (issue #967 contract, updated #1730) ─────────────
# welcome.html is a pure session/recovery bridge since #1730 — the canonical
# harness-copy surface is the dashboard wizard (harnesses.js) + the CLI
# (`_harness_mcp_config`). These tests pin the same contracts (optimal shapes,
# env-indirection, no literal key) against the current copy surface.

WELCOME = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "harnesses.js"


def _welcome() -> str:
    return WELCOME.read_text(encoding="utf-8")


def _extract_js_block(html: str, const_name: str) -> str:
    """Brace-balanced JS object literal for a harnesses.js const (raw text)."""
    idx = html.index(f"{const_name} =")
    open_brace = html.index("{", idx)
    depth = 0
    for j in range(open_brace, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                break
    return html[open_brace : j + 1]


def test_wizard_harness_configs_are_optimal():
    """T3: semantic fragments per harness in the wizard copy (harnesses.js)."""
    html = _welcome()
    # Claude: CLI one-liner shape in the copy
    assert "claude mcp add" in html
    assert "--transport http" in html
    assert "https://api.premiselabs.co/mcp/" in html
    # Codex: CLI + env export
    for frag in ("codex mcp add tortoise --url",
                 "--bearer-token-env-var TORTOISE_API_KEY",
                 "export TORTOISE_API_KEY="):
        assert frag in html, f"codex copy missing fragment: {frag}"
    # File paths named in the copy/steps
    for frag in (".cursor/mcp.json", ".mcp.json"):
        assert frag in html, f"missing file path: {frag}"
    # Cursor canonical JSON: shape + env expansion (trailing slash load-bearing)
    cursor_cfg = _extract_js_block(html, "CURSOR_MCP_CONFIG_ENV")
    assert "MCP_URL" in cursor_cfg  # url references the shared const
    assert "${env:TORTOISE_API_KEY}" in cursor_cfg
    # Pi canonical JSON: env expansion too (aligned with the CLI, #1730)
    pi_cfg = _extract_js_block(html, "PI_MCP_CONFIG_ENV")
    assert "MCP_URL" in pi_cfg
    assert "${env:TORTOISE_API_KEY}" in pi_cfg
    # The shared const pins the trailing-slash endpoint (load-bearing, #529)
    assert 'const MCP_URL = \'https://api.premiselabs.co/mcp/\'' in html


def test_wizard_env_form_blocks_carry_no_literal_key():
    """T3/T10 negative: the canonical env blocks contain no tt_ key."""
    html = _welcome()
    for const in ("CURSOR_MCP_CONFIG_ENV", "PI_MCP_CONFIG_ENV"):
        block = _extract_js_block(html, const)
        assert "tt_" not in block, f"{const} canonical env block must not contain a literal key"


def test_wizard_copy_beacon_persists_harness_section():
    """T4: the wizard copy action PATCHes /v1/onboarding/state with
    {harness, section} (the copy beacon moved from welcome.html to the
    dashboard wizard, #1566/#1730)."""
    dashboard = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"
    src = dashboard.read_text(encoding="utf-8")
    assert "onboarding/state" in src
    assert "harness: wizardHarness" in src or "harness: wizardHarness" in src
    assert "section: 'config'" in src


def test_key_never_interpolated_into_env_blocks():
    """T7b/J5: the canonical env blocks use env expansion only — the API key
    never appears as a literal in the wizard copy (HARNESS_INSTALL receives
    the key as a function argument; the env configs never carry it)."""
    html = _welcome()
    assert "TORTOISE_API_KEY" in html
    for const in ("CURSOR_MCP_CONFIG_ENV", "PI_MCP_CONFIG_ENV"):
        block = _extract_js_block(html, const)
        assert "tt_" not in block, f"{const} must stay key-free"
        assert "${env:TORTOISE_API_KEY}" in block
