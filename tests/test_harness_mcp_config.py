"""Per-harness MCP config shapes (#529/#981).

Verifies each harness's emitted MCP config matches the canonical copy
surface — the dashboard wizard harnesses.js (hosted) and self-hosted.html
(stdio); welcome.html is a pure session/recovery bridge since #1730.

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
import re
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
        # pi's mcp-client expands plain ${VAR} only — no env: prefix (verified
        # against extensions/mcp-client expandExpr; pi-header.md documents this).
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


class TestWizardCopyParity:
    """#981-contract follow-up: the CLI hosted config must match the dashboard
    wizard's copy surface (harnesses.js — the page users actually copy from
    since #1566 moved onboarding in-app and welcome.html became a pure
    session/recovery bridge, #1730).

    welcome.html no longer hosts harness config blocks (stripped in #1730); the
    canonical hosted copy surface is website/apps/dashboard/src/harnesses.js.
    """

    HARNESSES = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "harnesses.js"
    PAGE_URL = "https://api.premiselabs.co"

    @classmethod
    def _extract_block(cls, const_name: str) -> str:
        """Return the raw JS object literal for a harnesses.js const (brace-
        balanced) — substring assertions avoid fragile JS→JSON parsing."""
        html = cls.HARNESSES.read_text(encoding="utf-8")
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

    def test_cursor_and_pi_match_harnesses_env_blocks(self):
        html = self.HARNESSES.read_text(encoding="utf-8")
        # cursor supports env: expansion (Cursor docs); pi's mcp-client expands
        # plain ${VAR} only — the exact pi token is pinned in #1729 (harness
        # copy); this PR scopes pi to the env-indirection contract.
        for harness, const, token in (("cursor", "CURSOR_MCP_CONFIG_ENV", "${env:TORTOISE_API_KEY}"),
                                      ("pi", "PI_MCP_CONFIG_ENV", None)):
            cli = _harness_mcp_config(harness, "tt_any", self.PAGE_URL)
            tortoise = cli["mcpServers"]["tortoise"]
            block = self._extract_block(const)
            # The wizard copy must use env-indirection (never a literal key)
            # and reference the same MCP_URL the CLI uses.
            assert "TORTOISE_API_KEY" in block, f"env token missing for {harness}"
            assert "tt_" not in block, f"literal key in {const}"
            assert "MCP_URL" in block, f"MCP_URL reference missing for {harness}"
            assert f"const MCP_URL = '{self.PAGE_URL}/mcp/'" in html, "MCP_URL const drift"
            assert tortoise["url"] == f"{self.PAGE_URL}/mcp/", tortoise["url"]
            if token:  # cursor: exact expansion pinned
                assert token in block, f"{token} missing for {harness}"
                assert token in tortoise["headers"]["Authorization"], (
                    f"CLI {harness} header drifted from wizard copy")

    def test_claude_http_config_has_env_expansion(self):
        cli = _harness_mcp_config("claude", "tt_any", self.PAGE_URL)
        headers = cli["mcpServers"]["tortoise"]["headers"]
        # CLI claude uses plain ${TORTOISE_API_KEY}; cursor/pi use env: expansion
        assert list(headers.values()) == ["Bearer ${TORTOISE_API_KEY}"], headers

    def test_codex_command_present_in_harness_copy(self):
        html = self.HARNESSES.read_text(encoding="utf-8")
        assert "codex mcp add" in html
        assert "TORTOISE_API_KEY" in html


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


class TestCaptureInstallSeam:
    """#3575: the capture-INSTALL seam.

    ``HARNESS_CAPTURE_SUPPORT[h] === true`` is a capability claim, and it is
    only honest when the product actually INSTALLS a capture step. This pins
    the three legs the claim requires: (a) HARNESS_CAPTURE_SEAM names an
    in-repo artifact, (b) that artifact is committed, and (c)
    HARNESS_INSTALL[h] installs it. The #3575 defect was `pi: true` with no
    (a)/(b)/(c) — a false PASS the user could not falsify.
    """

    HARNESSES = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "harnesses.js"

    @classmethod
    def _src(cls) -> str:
        return cls.HARNESSES.read_text(encoding="utf-8")

    @staticmethod
    def _object(src: str, name: str) -> str:
        """Brace-balanced JS object literal for a harnesses.js const."""
        idx = src.index(f"export const {name} =")
        open_brace = src.index("{", idx)
        depth = 0
        for j in range(open_brace, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    return src[open_brace : j + 1]
        raise AssertionError(f"unbalanced object literal for {name}")

    def _seam(self) -> dict[str, str]:
        block = self._object(self._src(), "HARNESS_CAPTURE_SEAM")
        return dict(re.findall(r"(\w+):\s*'([^']+)'", block))

    @staticmethod
    def _install_body(install: str, harness: str) -> str:
        """Slice one harness's arrow-function body out of HARNESS_INSTALL."""
        m = re.search(rf"\n\s+'?{re.escape(harness)}'?:\s*\((?:key)?\)", install)
        assert m, f"HARNESS_INSTALL.{harness} not found"
        tail = install[m.end() :]
        nxt = re.search(r"\n\s+'?[a-zA-Z][\w-]*'?:\s*\((?:key)?\)", tail)
        return tail[: nxt.start()] if nxt else tail

    @staticmethod
    def _constant_text(src: str, name: str) -> str:
        """Body of an `export const NAME = ...` JS template literal ('' if the
        constant is not a template literal — e.g. a plain string URL)."""
        marker = f"export const {name} = `"
        if marker not in src:
            return ""
        start = src.index(marker) + len(marker)
        return src[start : src.index("`", start)]

    def _expanded_install_body(self, install: str, harness: str) -> str:
        """The harness's install body with any `${CONST}` it interpolates
        expanded to that constant's text — so the artifact can live in a
        shared constant (PI_CAPTURE_INSTALL) and still be pinned here."""
        body = self._install_body(install, harness)
        for const in re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", body):
            body += "\n" + self._constant_text(self._src(), const)
        return body

    def test_pi_install_step_delivers_the_in_repo_capture_extension(self):
        """#3575 bite: remove the capture step from HARNESS_INSTALL.pi (or the
        artifact it copies) and this test fails."""
        install = self._object(self._src(), "HARNESS_INSTALL")
        pi_install = self._expanded_install_body(install, "pi")
        assert "tortoise/pi-hooks/tortoise-capture.ts" in pi_install, (
            "HARNESS_INSTALL.pi no longer installs the in-repo Pi capture extension"
        )
        assert ".pi/agent/extensions" in pi_install, (
            "HARNESS_INSTALL.pi no longer installs the extension into Pi's discovery dir"
        )
        artifact = REPO_ROOT / "tortoise" / "pi-hooks" / "tortoise-capture.ts"
        assert artifact.is_file(), f"seam artifact missing: {artifact}"

    def test_capture_support_is_derived_from_the_seam_not_asserted(self):
        """The #3575 defect was a hand-written `pi: true`. The supported
        harnesses must be DERIVED from HARNESS_CAPTURE_SEAM."""
        block = self._object(self._src(), "HARNESS_CAPTURE_SUPPORT")
        for harness in ("claude", "pi"):
            assert re.search(
                rf"{harness}:\s*CAPTURE_SEAM_HARNESSES\.has\('{harness}'\)", block
            ), f"HARNESS_CAPTURE_SUPPORT.{harness} must be derived, not asserted"
        # any literal `true` must also be a declared seam
        literal_true = set(re.findall(r"(\w[\w-]*):\s*true\b", block))
        assert literal_true <= set(self._seam())

    def test_every_seam_harness_is_committed_and_installed(self):
        """The seam map is the general contract the other harnesses can be
        checked against: declared ⟺ committed ⟺ installed."""
        src = self._src()
        seam = self._seam()
        assert seam.get("pi") == "tortoise/pi-hooks/tortoise-capture.ts"
        assert seam.get("claude") == "tortoise/claude-hooks/session-end.sh"
        install = self._object(src, "HARNESS_INSTALL")
        for harness, artifact in seam.items():
            assert (REPO_ROOT / artifact).is_file(), (
                f"HARNESS_CAPTURE_SEAM.{harness} names a missing artifact: {artifact}"
            )
            body = self._expanded_install_body(install, harness)
            assert artifact in body, (
                f"HARNESS_INSTALL.{harness} does not install its declared seam {artifact}"
            )
