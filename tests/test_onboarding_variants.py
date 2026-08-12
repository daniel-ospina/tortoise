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
