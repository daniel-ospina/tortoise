"""Integrity tests for harness onboarding variants (epic #529, issues #966/#967).

Guarantees the wrapper-not-fork contract: variant headers carry delivery
instructions only (never the question flow), and staged artifacts embed the
canonical AGENT_ONBOARDING.md body verbatim. Drift here means users get a
forked onboarding flow per harness — the exact failure epic #529 prevents.
"""
from __future__ import annotations

import os
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


# ── welcome.html ref tests (issue #967 — plan T3/T4/T7b) ──────────────────
# welcome.html is a static artifact: these tests pin the harness-optimal
# Block A shapes, the authenticated beacon, and the env-indirection contract
# for returning visits, straight from the shipped HTML (no fixtures — the
# page IS the surface users copy from).

WELCOME = REPO_ROOT / "website" / "welcome.html"


def _welcome() -> str:
    return WELCOME.read_text(encoding="utf-8")


def _extract_marker_json(html: str, harness: str) -> dict:
    """Pull the canonical JSON block between TORTOISE_CFG markers (T3/T10)."""
    pattern = (r"/\* TORTOISE_CFG_BEGIN:" + harness +
               r" \*/(.*?)/\* TORTOISE_CFG_END:" + harness + r" \*/")
    match = re.search(pattern, html, re.S)
    assert match, f"TORTOISE_CFG markers missing for {harness}"
    block = match.group(1).strip()
    block = re.sub(r"^const\s+\w+\s*=\s*", "", block)
    block = block.rstrip().rstrip(";")
    import json as _json
    return _json.loads(block)


def test_welcome_harness_configs_are_optimal():
    """T3: semantic fragments per harness (tolerates flag reorder/whitespace)."""
    html = _welcome()
    # Claude: CLI one-liner shape
    for frag in ("claude mcp add", "--transport http",
                 "https://api.premiselabs.co/mcp", "Authorization: Bearer"):
        assert frag in html, f"claude block missing fragment: {frag}"
    # .mcp.json alternative pins type:http (whitespace-tolerant)
    assert re.search(r'"type"\s*:\s*"http"', html), \
        "claude .mcp.json alternative must pin type:http"
    # Codex: CLI + env export (URL composed via the MCP_URL constant).
    # Trailing slash is load-bearing: POST /mcp 307-redirects with a
    # scheme-downgraded Location; some stacks convert the follow-up
    # http→https 301 POST→GET and miss the JSON-RPC endpoint (epic #529 E2E).
    assert 'const MCP_URL = "https://api.premiselabs.co/mcp/"' in html
    for frag in ("codex mcp add tortoise --url",
                 "--bearer-token-env-var TORTOISE_API_KEY",
                 "export TORTOISE_API_KEY="):
        assert frag in html, f"codex block missing fragment: {frag}"
    # File paths named
    for frag in (".cursor/mcp.json", ".cursor/rules/tortoise-onboarding.mdc",
                 ".mcp.json"):
        assert frag in html, f"missing file path: {frag}"
    # Variant URLs + enum→slug mapping (claude → claude-code is the only
    # non-identity mapping; owned by welcome.html per plan Substep 4)
    assert "/onboarding/\"" in html or "\"https://premiselabs.co/onboarding/\"" in html
    for slug in ("claude-code", "codex", "cursor", "pi"):
        assert f'"{slug}"' in html.replace("'", '"'), f"slug {slug} unmapped"
    assert "HARNESS_VARIANT_SLUG" in html
    # Cursor canonical JSON: shape + env expansion
    cursor_cfg = _extract_marker_json(html, "cursor")
    tortoise = cursor_cfg["mcpServers"]["tortoise"]
    assert tortoise["url"] == "https://api.premiselabs.co/mcp/"  # trailing slash load-bearing
    assert tortoise["headers"]["Authorization"] == "Bearer ${env:TORTOISE_API_KEY}"
    # Pi canonical JSON: shape + env expansion
    pi_cfg = _extract_marker_json(html, "pi")
    tortoise = pi_cfg["mcpServers"]["tortoise"]
    assert tortoise["url"] == "https://api.premiselabs.co/mcp/"  # trailing slash load-bearing
    assert tortoise["headers"]["Authorization"] == "Bearer ${TORTOISE_API_KEY}"
    # paste-session-only labeling on the literal-key alternatives
    assert html.count("paste-session only") >= 2, (
        "cursor + pi literal-key alternatives must be labeled paste-session only")


def test_welcome_env_form_blocks_carry_no_literal_key():
    """T3/T10 negative: the canonical (env) JSON blocks contain no tt_ key."""
    html = _welcome()
    for harness in ("cursor", "pi"):
        match = re.search(
            r"/\* TORTOISE_CFG_BEGIN:" + harness + r" \*/(.*?)/\* TORTOISE_CFG_END:" + harness + r" \*/",
            html, re.S)
        assert match
        assert "tt_" not in match.group(1), (
            f"{harness} canonical env block must not contain a literal key")


def test_welcome_beacon_authenticated_with_body():
    """T4: the copy beacon carries Bearer auth + {harness, section} body."""
    html = _welcome()
    assert "fireCopyBeacon" in html
    # The beacon function: auth header + JSON body with both fields.
    beacon = re.search(r"function fireCopyBeacon[\s\S]*?\n    \}", html)
    assert beacon, "fireCopyBeacon function missing"
    body = beacon.group(0)
    assert '"Authorization": "Bearer " + key' in body or "'Authorization'" in body
    assert "Authorization" in body and "Bearer" in body
    assert "harness" in body and "section" in body
    assert "JSON.stringify" in body
    # Both copy actions fire it with distinct sections.
    assert 'fireCopyBeacon("config")' in html
    assert 'fireCopyBeacon("prompt")' in html


def test_key_already_shown_env_indirection():
    """T7b/J5: returning visits keep the MCP card with env-indirection forms.

    The masked placeholder must never be interpolated into Block A: all
    fallback branches use env expansion + the tt_YOUR_KEY placeholder.
    """
    html = _welcome()
    # Returning-visit handler keeps mcp-card visible (no classList.add("hidden") on it).
    returning = re.search(r"function showAlreadyProvisioned[\s\S]*?\n    \}", html)
    assert returning
    body = returning.group(0)
    mcp_hide = re.search(r'mcpCard\)\s*mcpCard\.classList\.add\("hidden"\)', body)
    assert not mcp_hide, "mcp-card must stay visible for returning visitors (#529)"
    assert "renderMcpConfig()" in body, "returning visit must re-render env forms"
    # The masked placeholder never feeds a config: HARNESS_CONFIGS guard on tt_ prefix.
    assert "_usableKey" in html
    configs = re.search(r"const HARNESS_CONFIGS = \{[\s\S]*?\n    \};", html)
    assert configs
    cfg_block = configs.group(0)
    assert "_usableKey(key)" in cfg_block
    assert "•" not in cfg_block, "masked placeholder must never reach Block A"
    # Fallback branches offer env indirection + the placeholder, never a secret.
    assert "tt_YOUR_KEY" in cfg_block
    assert "${env:TORTOISE_API_KEY}" in cfg_block or "CURSOR_MCP_CONFIG_ENV" in cfg_block
    # Manual-mode ENV_CONFIGS (#1189): the masked-key branch must substitute
    # the tt_YOUR_KEY placeholder for the raw bullets before interpolation.
    assert "const effectiveKey = _usableKey(key) ? key : \"tt_YOUR_KEY\";" in html, (
        "renderMcpConfig must substitute tt_YOUR_KEY for masked keys")
    assert "ENV_CONFIGS[currentHarness](effectiveKey)" in html, (
        "manual-mode ENV_CONFIGS must interpolate the effective key")
    env_block = re.search(r"const ENV_CONFIGS = \{[\s\S]*?\n    \};", html)
    assert env_block, "ENV_CONFIGS const missing"
    assert "•" not in env_block.group(0), \
        "ENV_CONFIGS blocks must not contain the masked bullet literal"
