"""Per-harness MCP config shapes (#529/#981).

Verifies each harness's emitted MCP config matches the welcome page Block A
contracts (tests/test_onboarding_variants.py T3) — the paste-path surface for
hosted (HTTP) and self-hosted (stdio) onboarding:

- claude:  .mcp.json, `type: "http"` (hosted) / stdio command (self-hosted)
- codex:   `codex mcp add ...` command — Codex manages its own config
- cursor:  .mcp.json — url+headers WITHOUT `type` (hosted, per Cursor docs for
           remote servers) but WITH `type: "stdio"` (self-hosted, docs require it)
- pi:      .mcp.json — url+headers without `type` (hosted) / stdio command (self-hosted)

Hosted headers use env expansion (${TORTOISE_API_KEY} / ${env:TORTOISE_API_KEY})
— no literal key on disk, matching the page's canonical blocks.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.__main__ import _harness_mcp_config, _harness_stdio_config, _print_harness_instructions  # noqa: I001

REPO_ROOT = Path(__file__).resolve().parent.parent
# #984 contract (merged to main): the hosted endpoint always carries the
# trailing slash — pinned here so a regression to the unsuffixed URL fails.
ENDPOINT = "https://api.premiselabs.co/mcp/"


class TestHostedHttpShapes:
    """`_harness_mcp_config` — hosted onboarding (HTTP, page #529 shapes)."""

    def test_claude_http_with_type(self):
        cfg = _harness_mcp_config("claude", "tt_testkey", "https://api.premiselabs.co")
        tortoise = cfg["mcpServers"]["tortoise"]
        # Page pins type:http — a url entry WITHOUT type is skipped by Claude Code.
        assert tortoise["type"] == "http"
        assert tortoise["url"] == ENDPOINT
        assert tortoise["headers"]["Authorization"] == "Bearer ${TORTOISE_API_KEY}"

    def test_pi_env_form_no_type(self):
        # Page's pi canonical block carries url+headers, no `type`.
        cfg = _harness_mcp_config("pi", "tt_testkey", "https://api.premiselabs.co")
        tortoise = cfg["mcpServers"]["tortoise"]
        assert "type" not in tortoise
        assert tortoise["url"] == ENDPOINT
        assert tortoise["headers"]["Authorization"] == "Bearer ${TORTOISE_API_KEY}"

    def test_cursor_env_form_no_type(self):
        # Cursor docs: remote url-based servers take url+headers, no `type`.
        cfg = _harness_mcp_config("cursor", "tt_testkey", "https://api.premiselabs.co")
        tortoise = cfg["mcpServers"]["tortoise"]
        assert "type" not in tortoise
        assert tortoise["url"] == ENDPOINT
        assert tortoise["headers"]["Authorization"] == "Bearer ${env:TORTOISE_API_KEY}"

    def test_codex_remote_command(self):
        cfg = _harness_mcp_config("codex", "tt_testkey", "https://api.premiselabs.co")
        cmd = cfg["command"]
        assert cmd.startswith("codex mcp add tortoise")
        assert f"--url {ENDPOINT}" in cmd
        assert "--bearer-token-env-var TORTOISE_API_KEY" in cmd

    def test_endpoint_trailing_slash_normalized(self):
        # #984: regardless of whether the input api_url has a trailing
        # slash, the emitted endpoint keeps exactly one.
        for api_url in ("https://api.premiselabs.co", "https://api.premiselabs.co/"):
            cfg = _harness_mcp_config("claude", "tt_testkey", api_url)
            assert cfg["mcpServers"]["tortoise"]["url"] == ENDPOINT

    def test_all_harness_keys_emitted(self):
        for harness in ("claude", "codex", "cursor", "pi"):
            cfg = _harness_mcp_config(harness, "tt_testkey", "https://api.premiselabs.co")
            assert cfg, f"empty config for {harness}"


class TestWelcomePageParity:
    """#981: CLI hosted config must match welcome.html Block A (T3).

    The page is the surface users copy from — the CLI and the page must not
    be able to drift again (this is the #529-regression that #981 fixes).
    """

    PAGE = REPO_ROOT / "website" / "welcome.html"
    PAGE_URL = "https://api.premiselabs.co"

    @staticmethod
    def _marker_json(html: str, harness: str) -> dict:
        import re
        pattern = (r"/\* TORTOISE_CFG_BEGIN:" + harness +
                   r" \*/(.*?)/\* TORTOISE_CFG_END:" + harness + r" \*/")
        match = re.search(pattern, html, re.S)
        assert match, f"TORTOISE_CFG markers missing for {harness}"
        block = match.group(1).strip()
        block = re.sub(r"^const\s+\w+\s*=\s*", "", block)
        return json.loads(block.rstrip().rstrip(";"))

    def test_cursor_and_pi_match_page_env_blocks_exactly(self):
        html = self.PAGE.read_text(encoding="utf-8")
        for harness in ("cursor", "pi"):
            cli = _harness_mcp_config(harness, "tt_any", self.PAGE_URL)
            page = self._marker_json(html, harness)
            assert cli == page, (
                f"CLI {harness} config drifted from welcome.html canonical block:\n"
                f"  CLI:  {json.dumps(cli)}\n  PAGE: {json.dumps(page)}"
            )

    def test_claude_matches_page_mcpjson_alternative(self):
        html = self.PAGE.read_text(encoding="utf-8")
        i = html.find("claude mcp add")
        assert i > 0, "claude mcp add one-liner missing from welcome.html"
        start = html.find("# {", i)
        assert start > 0, "claude .mcp.json alternative missing from welcome.html"
        end = html.find("\n", start)
        raw = html[start:end].lstrip("# ").replace("\\${", "${")
        raw = raw.replace("${MCP_URL}", f"{self.PAGE_URL}/mcp/")
        page_cfg = json.loads(raw)
        cli = _harness_mcp_config("claude", "tt_any", self.PAGE_URL)
        assert cli == page_cfg, (
            f"CLI claude config drifted from welcome.html .mcp.json alternative:\n"
            f"  CLI:  {json.dumps(cli)}\n  PAGE: {json.dumps(page_cfg)}"
        )

    def test_codex_command_matches_page(self):
        html = self.PAGE.read_text(encoding="utf-8")
        i = html.find("codex mcp add tortoise")
        assert i > 0, "codex mcp add line missing from welcome.html"
        end = html.find("\n", i)
        page_cmd = html[i:end].rstrip("`,").strip().replace(
            "${MCP_URL}", f"{self.PAGE_URL}/mcp/"
        )
        cli = _harness_mcp_config("codex", "tt_any", self.PAGE_URL)["command"]
        assert cli == page_cmd, (
            f"CLI codex command drifted from welcome.html:\n"
            f"  CLI:  {cli}\n  PAGE: {page_cmd}"
        )

    def test_claude_one_liner_printed_by_cli(self):
        # The page's PRIMARY claude shape is the CLI one-liner — the CLI's
        # --harness claude output must show it (with the user's literal key).
        from tortoise.__main__ import _print_mcp_configs
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            _print_mcp_configs("tt_key", self.PAGE_URL, "claude")
        finally:
            sys.stdout = old
        out = buf.getvalue()
        assert "claude mcp add --transport http tortoise https://api.premiselabs.co/mcp/" in out
        assert '"type": "http"' in out


class TestSelfHostedStdioShapes:
    """`_harness_stdio_config` — self-hosted onboarding (stdio)."""

    def test_claude_stdio(self):
        cfg = _harness_stdio_config("claude")
        tortoise = cfg["mcpServers"]["tortoise"]
        assert tortoise["command"] == "python3"
        assert tortoise["args"] == ["-m", "tortoise.mcp_server"]
        assert tortoise["env"]["TORTOISE_DB_URI"] == "docker://localhost:6379"
        assert "type" not in tortoise  # Claude Code infers stdio from command

    def test_pi_stdio(self):
        # pi's mcp-client extension keys off `command` for stdio servers.
        cfg = _harness_stdio_config("pi")
        tortoise = cfg["mcpServers"]["tortoise"]
        assert tortoise["command"] == "python3"
        assert "type" not in tortoise

    def test_cursor_stdio_requires_type_field(self):
        # Cursor docs mark `type` REQUIRED for stdio servers — the #529 gap.
        cfg = _harness_stdio_config("cursor")
        tortoise = cfg["mcpServers"]["tortoise"]
        assert tortoise["type"] == "stdio"
        assert tortoise["command"] == "python3"
        assert tortoise["args"] == ["-m", "tortoise.mcp_server"]

    def test_codex_stdio_command(self):
        cfg = _harness_stdio_config("codex")
        assert cfg["command"] == "codex mcp add tortoise -- python3 -m tortoise.mcp_server"

    def test_self_hosted_page_cursor_has_type_stdio(self):
        # The paste-path surface users actually copy from — self-hosted.html.
        html = (REPO_ROOT / "website" / "self-hosted.html").read_text()
        # cursor stdio block carries type: "stdio" (and claude/pi blocks don't)
        cursor_block = html.split('cursor: () => JSON.stringify({', 1)[1].split('}, null, 2)', 1)[0]
        assert 'type: "stdio"' in cursor_block
        claude_block = html.split('claude: () => JSON.stringify({', 1)[1].split('}, null, 2)', 1)[0]
        assert 'type:' not in claude_block


class TestPrintHarnessInstructions:
    """`_print_harness_instructions` — CLI self-hosted guidance output."""

    def _capture(self, harness: str) -> str:
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            _print_harness_instructions(harness)
        finally:
            sys.stdout = old
        return buf.getvalue()

    def test_cursor_block_prints_stdio_type(self):
        out = self._capture("cursor")
        assert '"type": "stdio"' in out
        assert 'python3' in out

    def test_codex_block_uses_codex_mcp_add(self):
        out = self._capture("codex")
        assert "codex mcp add tortoise -- python3 -m tortoise.mcp_server" in out
        assert "config.toml" not in out  # stale edit-the-toml flow removed

    def test_all_blocks_valid_json(self):
        for harness in ("claude", "codex", "cursor", "pi"):
            out = self._capture(harness)
            # The json.dumps'd .mcp.json block parses (multi-line pretty print).
            lines = out.splitlines()
            start = next((i for i, l in enumerate(lines) if l.strip().startswith("{")), None)  # noqa: E741
            if start is None:
                continue  # codex prints a shell command, not JSON
            block = "\n".join(lines[start:])  # noqa: F841
            # Cut at the first line that closes the top-level object.
            depth, end = 0, None
            for i, l in enumerate(lines[start:], start):  # noqa: E741
                depth += l.count("{") - l.count("}")
                if depth == 0:
                    end = i
                    break
            json.loads("\n".join(lines[start : end + 1]))
