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
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.__main__ import _harness_mcp_config, _harness_stdio_config, _print_harness_instructions  # noqa: I001
from tortoise.auth import API_KEY_PREFIXES
from tortoise.oauth import ACCESS_TOKEN_PREFIX, REFRESH_TOKEN_PREFIX

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


class TestCommittedRepoMcpJson:
    """#3601 regression: the COMMITTED root `.mcp.json` must reach the hosted
    endpoint with an env-indirect key.

    The classes above pin the EMITTED onboarding configs (`_harness_mcp_config`
    / `init`); this pins the file the repo actually ships. It previously
    declared `http://localhost:8000/mcp` with `"headers": {}`, so every agent
    whose local daemon was not running got an opaque `fetch failed` -- and the
    entry could not authenticate even when the daemon WAS up under the
    documented `tortoise serve --http --auth tenant`.

    Covers all five fields of the entry: `url`, `type`, `headers`, the absence
    of a stdio `env`/`command`/`args`, and no literal token anywhere in the
    file. Reads the file on disk (the committed blob in any clean checkout /
    CI). A machine-local uncommitted edit to `.mcp.json` is deliberately NOT
    covered -- that is exactly what the entry's `_comment` tells a self-hoster
    to make.
    """

    COMMITTED = REPO_ROOT / ".mcp.json"

    def _tortoise(self) -> dict:
        assert self.COMMITTED.is_file(), f"committed {self.COMMITTED} is missing"
        cfg = json.loads(self.COMMITTED.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers")
        assert isinstance(servers, dict), (
            f"committed .mcp.json has no mcpServers object (got {type(servers).__name__})"
        )
        server = servers.get("tortoise")
        assert isinstance(server, dict), (
            f"committed .mcp.json has no 'tortoise' entry (have {sorted(servers)}); "
            f"the entry is how an agent reaches the graph at all (#3601)"
        )
        return server

    def test_tortoise_entry_targets_hosted_endpoint(self):
        url = self._tortoise().get("url")
        assert url == ENDPOINT, (
            f"committed .mcp.json must target the hosted endpoint {ENDPOINT}; "
            f"a local-daemon url breaks every agent without a running daemon "
            f"(#3601) -- got {url!r}"
        )

    def test_tortoise_entry_keeps_http_type(self):
        # This root file also serves Claude Code, which SKIPS a url entry with
        # no `type` (see module docstring); pi ignores `type` and picks the
        # transport from url-vs-command. Pinned because the sibling doc
        # docs/quickstart-cloud.md:47 names a DIFFERENT value (streamable-http)
        # for the same object -- drifting this field silently disables the
        # server for Claude Code with no other test failing.
        assert self._tortoise().get("type") == "http", (
            f"committed .mcp.json tortoise entry must keep type='http' -- got "
            f"{self._tortoise().get('type')!r}"
        )

    def test_tortoise_header_is_env_indirect(self):
        headers = self._tortoise().get("headers")
        # Diagnosable failure on every malformed shape, not just the empty one
        # (#3601 was `"headers": {}`): a bare AttributeError tells a maintainer
        # nothing about what drifted.
        assert headers is None or isinstance(headers, dict), (
            f"committed .mcp.json tortoise headers must be an object -- got "
            f"{type(headers).__name__}"
        )
        auth = (headers or {}).get("Authorization")
        assert auth, (
            f"committed .mcp.json must carry an Authorization header -- an "
            f"empty/absent headers block cannot authenticate even when the "
            f"daemon is up (#3601); got headers={headers!r}"
        )
        assert isinstance(auth, str), (
            f"committed .mcp.json Authorization must be a string -- got "
            f"{type(auth).__name__}"
        )
        # Env-indirect only: the wizard/CLI copy never writes a literal key.
        assert auth.startswith("Bearer ${") and auth.endswith("}"), (
            f"committed .mcp.json Authorization must be env-indirect, got {auth!r}"
        )
        assert "TORTOISE_API_KEY" in auth, auth

    def test_tortoise_entry_is_http_only(self):
        # The entry must stay an HTTP entry. `_comment` and
        # docs/infra-runbook.md section 4.5 both state that the committed entry
        # carries no `env` (the DB target is resolved server-side by the hosted
        # API), and the stdio command+args pattern was replaced in feat/338 --
        # so re-adding any of these drifts the docs silently.
        entry = self._tortoise()
        for key in ("env", "command", "args"):
            assert key not in entry, (
                f"committed .mcp.json tortoise entry must not define {key!r} "
                f"(it is an HTTP entry -- see its _comment); got {entry[key]!r}"
            )

    def test_no_literal_api_key_in_committed_config(self):
        # This file ships to users -- a literal token leaks a credential.
        text = self.COMMITTED.read_text(encoding="utf-8")
        # (a) Prefix scan over the whole file: a token pasted into a `_comment`
        # is the same leak. The prefixes are the MCP-bearer-accepted minting
        # sources (tortoise/mcp_auth.py accepts API_KEY_PREFIXES or `oat_`), so
        # a family minted there is covered without editing this test. Families
        # that are not bearer-reachable (the client id/secret `ct_`/`cs_` minted
        # inline in tortoise/oauth.py) are deliberately not listed.
        for marker in (*API_KEY_PREFIXES, ACCESS_TOKEN_PREFIX, REFRESH_TOKEN_PREFIX):
            assert marker not in text, (
                f"literal {marker!r} key material in committed .mcp.json -- "
                f"keys must stay env-indirect"
            )
        # (b) Structural backstop over the PARSED HEADER VALUES, not the raw
        # text: the Authorization header is where a credential would actually
        # be presented, and prose in a `_comment` that merely says "Bearer "
        # must not trip the guard. Catches any token family, including one this
        # test has never heard of.
        headers = self._tortoise().get("headers")
        if isinstance(headers, dict):
            for name, value in headers.items():
                if isinstance(value, str) and "Bearer " in value:
                    assert value.count("Bearer ") == value.count("Bearer ${"), (
                        f"committed .mcp.json header {name!r} carries a literal "
                        f"Bearer token -- every `Bearer ` in a user-shipped "
                        f"config must be followed by a ${{VAR}} expression"
                    )
