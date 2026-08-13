"""Per-harness MCP config shapes (#529).

Verifies each harness's emitted MCP config matches its client's expected
format — the paste-path surface for hosted (Streamable HTTP) and self-hosted
(stdio) onboarding:

- claude:  .mcp.json, streamable-http with `type` field (hosted) / stdio command (self-hosted)
- codex:   `codex mcp add ...` command — Codex manages its own config
- cursor:  .mcp.json — url+headers WITHOUT `type` (hosted, per Cursor docs for
           remote servers) but WITH `type: "stdio"` (self-hosted, docs require it)
- pi:      .mcp.json — streamable-http (hosted) / stdio command (self-hosted);
           pi's mcp-client extension reads project .mcp.json and keys off url/command
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.__main__ import _harness_mcp_config, _harness_stdio_config, _print_harness_instructions

REPO_ROOT = Path(__file__).resolve().parent.parent
# #984 contract (merged to main): the hosted endpoint always carries the
# trailing slash — pinned here so a regression to the unsuffixed URL fails.
ENDPOINT = "https://api.premiselabs.co/mcp/"


class TestHostedStreamableHttpShapes:
    """`_harness_mcp_config` — hosted onboarding (Streamable HTTP)."""

    def test_claude_streamable_http_with_type(self):
        cfg = _harness_mcp_config("claude", "tt_testkey", "https://api.premiselabs.co")
        tortoise = cfg["mcpServers"]["tortoise"]
        assert tortoise["type"] == "streamable-http"
        assert tortoise["url"] == ENDPOINT
        assert tortoise["headers"]["Authorization"] == "Bearer tt_testkey"

    def test_pi_streamable_http_with_type(self):
        # pi's mcp-client extension reads project .mcp.json and connects via
        # the `url` field (StreamableHTTPClientTransport) — type is accepted.
        cfg = _harness_mcp_config("pi", "tt_testkey", "https://api.premiselabs.co")
        tortoise = cfg["mcpServers"]["tortoise"]
        assert tortoise["type"] == "streamable-http"
        assert tortoise["url"] == ENDPOINT
        assert tortoise["headers"]["Authorization"] == "Bearer tt_testkey"

    def test_cursor_remote_has_no_type(self):
        # Cursor docs: remote url-based servers take url+headers, no `type`.
        cfg = _harness_mcp_config("cursor", "tt_testkey", "https://api.premiselabs.co")
        tortoise = cfg["mcpServers"]["tortoise"]
        assert "type" not in tortoise
        assert tortoise["url"] == ENDPOINT
        assert tortoise["headers"]["Authorization"] == "Bearer tt_testkey"

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
            start = next((i for i, l in enumerate(lines) if l.strip().startswith("{")), None)
            if start is None:
                continue  # codex prints a shell command, not JSON
            block = "\n".join(lines[start:])
            # Cut at the first line that closes the top-level object.
            depth, end = 0, None
            for i, l in enumerate(lines[start:], start):
                depth += l.count("{") - l.count("}")
                if depth == 0:
                    end = i
                    break
            json.loads("\n".join(lines[start : end + 1]))
