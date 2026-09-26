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
from typing import ClassVar

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


class TestClaudeHookTimeouts:
    """#3754: every shipped Claude Code capture snippet must pin a per-hook
    `timeout`.

    Claude Code cancels a SessionEnd hook at its **1.5 s default budget**; the
    budget only rises to the highest per-hook `timeout` in the settings files,
    **capped at 60 s**. `tortoise/claude-hooks/session-end.sh` measures 9.26 s
    end-to-end on a real hosted run (CLI cold start ~1.2 s + `POST /v1/sessions`
    ~4.4 s + the backgrounded index sweep), so the shipped seam — which
    specified no `timeout` — was cancelled on a normal session and filed
    nothing (debug log: `SessionEnd:other [...] cancelled`), silently, because
    the hook is fail-open (`2>/dev/null || exit 0`).

    That 60 s cap is the platform's only *documented* per-event ceiling, so the
    guard's envelope is PER EVENT and each figure is labelled for what it is: 60 s
    is SessionEnd's documented hard cap, while SessionStart is a command hook that
    defaults to 600 s with no documented ceiling above it — the shipped 60 s
    there is a PROJECT bound (~6× headroom over the digest path), not a
    platform rule, so this guard must not reject a legitimate rise toward the
    documented 600 s. The 9.26 s floor is a SessionEnd measurement, asserted
    only where that measurement exists.

    FOUR surfaces ship the same `settings.json` snippet, and a copy that loses
    its `timeout` re-opens the bug on that surface alone:

    1. `harnesses.js` `HARNESS_INSTALL.claude` — the full install copy
    2. `harnesses.js` `HARNESS_CAPTURE_INSTALL.claude` — the Memory-sources step
    3. `session-end.sh` header — the in-repo install comment
    4. `session-start.sh` header — the in-repo install comment

    Issue #3963 adds a FIFTH registration to the two `harnesses.js` copies: the
    `UserPromptSubmit` per-turn capture (`session-turn.sh`).  It is pinned by
    the same tables — a copy that loses its `timeout` re-opens #3754 on that
    surface alone, and the turn hook is the one whose cancellation loses a
    session outright (SessionEnd never fires on a kill).

    Each is JSON-PARSED back out of the file rather than substring-matched (a
    `"timeout": 60` sitting anywhere else in the file, or on the wrong event,
    must not pass), and the per-surface event counts are pinned — a new copy
    must be added here, not shipped unprotected.
    """

    HARNESSES = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "harnesses.js"
    HOOKS = REPO_ROOT / "tortoise" / "claude-hooks"
    # #3754: per-event envelope. SessionEnd 60 = documented hard cap (its hooks
    # share a 1.5 s budget raised only to the highest per-hook `timeout`, "up to
    # 60 seconds"). SessionStart 600 = the documented command-hook DEFAULT, and
    # the bound this guard holds SessionStart to — the platform states no
    # ceiling there, so claiming 60 for it was a false invariant.
    MAX_TIMEOUT_S: ClassVar[dict[str, int]] = {
        "SessionEnd": 60, "SessionStart": 600, "UserPromptSubmit": 60}
    # #3754: what each figure above IS, quoted into the failure message — a
    # documented DEFAULT must never be reported as a ceiling (the exact
    # overclaim this guard was corrected for).
    ENVELOPE_KIND: ClassVar[dict[str, str]] = {
        "SessionEnd": "the documented shared-budget cap",
        "SessionStart": "the documented command-hook default",
        # #3963: UserPromptSubmit carries no event-specific platform figure in
        # this repo, so its bound is the SAME per-hook raise cap #3754
        # established for the shared budget (the budget rises to the highest
        # per-hook `timeout`, "up to 60 seconds") rather than a second,
        # invented envelope.
        "UserPromptSubmit": "the #3754 documented per-hook budget cap",
    }
    # #3754: floors are MEASUREMENTS, and only SessionEnd has one (a real hosted
    # run with the seam-literal settings). SessionStart carries the guard's upper
    # bound only, rather than inheriting a session-END figure it never produced.
    MEASURED_S: ClassVar[dict[str, float]] = {"SessionEnd": 9.26}
    # surface → {event: number of snippets carrying that event}
    SURFACES: ClassVar[dict[str, dict[str, int]]] = {
        "harnesses.js": {"SessionStart": 2, "SessionEnd": 2,
                         "UserPromptSubmit": 2},
        "session-end.sh": {"SessionEnd": 1},
        "session-start.sh": {"SessionStart": 1},
    }

    @staticmethod
    def _snippets(text: str) -> list[dict]:
        """Every `{ "hooks": ... }` object literal in `text`, JSON-parsed.

        Brace-balanced so the multi-line literals in the shell-script headers
        parse too; the `#` comment markers carry no JSON meaning and are
        stripped from the span.
        """
        docs = []
        for m in re.finditer(r'\{\s*"hooks"\s*:', text):
            depth = 0
            for j in range(m.start(), len(text)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
            else:  # pragma: no cover - malformed source, not a test condition
                raise AssertionError('unbalanced { "hooks": ... } literal')
            docs.append(json.loads(text[m.start() : j + 1].replace("#", "")))
        return docs

    def _walk(self):
        """`(surface, event, inner-hook-entry)` for every shipped snippet."""
        texts = {"harnesses.js": self.HARNESSES.read_text(encoding="utf-8")}
        for script in ("session-end.sh", "session-start.sh"):
            texts[script] = (self.HOOKS / script).read_text(encoding="utf-8")
        for name, text in texts.items():
            for doc in self._snippets(text):
                for event, groups in doc["hooks"].items():
                    for group in groups:
                        for entry in group["hooks"]:
                            yield name, event, entry

    def test_every_shipped_snippet_pins_a_hook_timeout(self):
        assert set(self.MAX_TIMEOUT_S) == set(self.ENVELOPE_KIND), (
            "MAX_TIMEOUT_S and ENVELOPE_KIND must list the same events: "
            f"{sorted(self.MAX_TIMEOUT_S)} != {sorted(self.ENVELOPE_KIND)}"
        )
        for name, event, entry in self._walk():
            timeout = entry.get("timeout")
            assert isinstance(timeout, int) and not isinstance(timeout, bool), (
                f"#3754: {name} ships a {event} hook entry with no integer "
                f'"timeout" ({entry!r}) — Claude Code cancels SessionEnd at its '
                f"1.5s default, so the session is silently never filed"
            )
            bound = self.MAX_TIMEOUT_S.get(event)
            assert bound is not None, (
                f"#3754: {name} ships a {event} hook entry and no documented "
                f"timeout envelope is recorded for that event — add its "
                f"platform figure to MAX_TIMEOUT_S before shipping the snippet"
            )
            assert timeout <= bound, (
                f"#3754: {name} {event} timeout={timeout}s is above this "
                f"guard's {event} bound ({bound}s — {self.ENVELOPE_KIND[event]})"
            )
            measured = self.MEASURED_S.get(event)
            assert measured is None or measured < timeout, (
                f"#3754: {name} {event} timeout={timeout}s does not clear the "
                f"{measured}s measured {event} run — the hook would be "
                f"cancelled mid-flight"
            )

    def test_snippet_surfaces_are_fully_pinned(self):
        # An added or removed copy of the snippet must land here: an
        # unprotected surface is exactly how this bug shipped (#3754).
        counts: dict[str, dict[str, int]] = {}
        for name, event, _ in self._walk():
            counts.setdefault(name, {})
            counts[name][event] = counts[name].get(event, 0) + 1
        assert counts == self.SURFACES, (
            f"the shipped settings.json snippet surfaces changed: {counts} != "
            f"{self.SURFACES} — pin the new copy's hook timeout here"
        )


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

    def test_self_hosted_page_sends_the_onboarding_instructions(self):
        """#4365: this served page must hand the reader the onboarding INSTRUCTIONS
        (a document the agent reads), not a skill to install — and must not
        resurrect the retired Q&A "onboarding prompt" framing. Pinned because
        reverting the copy left the whole suite green."""
        html = (REPO_ROOT / "website" / "self-hosted.html").read_text()
        low = html.lower()
        assert "install the tortoise-onboarding skill" not in low, (
            "self-hosted.html must not tell the reader to install onboarding")
        assert ("https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md"
                in html), ("self-hosted.html must link the served instructions")
        assert "never an installed skill" in low, (
            "self-hosted.html must say onboarding is not an installed skill")
        # The retired framing must stay retired — the meta description and the
        # step-3 note both carried it, three lines from the note above, and
        # reverting them tripped no assertion at all (mutation-verified).
        assert "5-question" not in low, (
            "self-hosted.html still advertises the retired 5-question prompt")
        assert "canonical onboarding prompt" not in low, (
            "self-hosted.html still calls it the canonical onboarding prompt")


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


class TestDocsPageAndSkillConfig:
    """#3145: the public docs page (`#mcp` section) and the onboarding skill
    must agree with the tested harness shapes — no literal key, no
    `"type": "streamable-http"`, Pi + Codex present, and the per-harness
    `type` rule (claude = http; cursor/pi omit it).

    These two surfaces were previously unasserted, which is exactly how the
    docs shipped `streamable-http` + a literal key and the skill taught
    `"type": "http"` for Cursor/Pi against `_harness_mcp_config`.
    """

    DOCS = REPO_ROOT / "website" / "docs.html"
    SKILL = REPO_ROOT / "tortoise" / "onboarding" / "SKILL.md"

    # Per-harness headings in the docs #mcp section. Rows are pinned by
    # heading (not a bare "Codex"/"Pi" substring) so deleting a row fails.
    ROW_HEADINGS = ("<h4>Claude Code</h4>", "<h4>Cursor", "<h4>Pi",
                    "<h4>Codex CLI</h4>", "<h4>Codex Desktop (no terminal)</h4>",
                    "<h4>Claude Desktop</h4>", "<h4>Claude Web</h4>")

    def _mcp_section(self) -> str:
        text = self.DOCS.read_text(encoding="utf-8")
        start = text.find('<h2 id="mcp">')
        assert start != -1, "docs.html lost its #mcp section anchor"
        end = text.find('<h2 id="api">', start)
        assert end != -1, "docs.html lost its #api section anchor"
        assert end > start, "docs.html #api now precedes #mcp — slice would be empty"
        return text[start:end]

    def _row(self, section: str, start_heading: str, end_heading: str) -> str:
        assert start_heading in section, f"docs #mcp row missing: {start_heading}"
        assert end_heading in section, f"docs #mcp row missing: {end_heading}"
        return section.split(start_heading, 1)[1].split(end_heading, 1)[0]

    @staticmethod
    def _json_block(fragment: str) -> dict:
        m = re.search(r"<pre><code>(\{.*?\})</code></pre>", fragment, re.S)
        assert m, f"no JSON config block found in: {fragment[:80]!r}"
        cfg = json.loads(m.group(1))["mcpServers"]["tortoise"]
        assert "tt_" not in json.dumps(cfg), f"literal key in config block: {cfg}"
        return cfg

    # ── docs page (#mcp) ───────────────────────────────────────────────

    def test_docs_mcp_section_has_no_literal_key(self):
        section = self._mcp_section()
        assert "tt_YOUR_KEY" not in section
        # no config snippet ships a literal-looking tt_ key
        assert '"Authorization": "Bearer tt_' not in section

    def test_docs_mcp_section_has_no_streamable_type(self):
        # The prose may WARN about the alias; no config value may use it.
        assert '"type": "streamable-http"' not in self._mcp_section()

    def test_docs_mcp_section_lists_all_harnesses(self):
        section = self._mcp_section()
        for heading in self.ROW_HEADINGS:
            assert heading in section, f"docs #mcp must cover {heading} (#3145)"
        # pin the Codex command, not just the word "Codex"
        assert "codex mcp add tortoise --url" in section
        # Codex Desktop is a separate, shell-export-free path (#2756/#2832)
        desktop = self._row(section, "<h4>Codex Desktop (no terminal)</h4>",
                            "<h4>Claude Desktop</h4>")
        assert "~/.codex/config.toml" in desktop
        assert "bearer_token_env_var" in desktop
        assert "does <em>not</em> read shell exports" in desktop

    def test_docs_hosted_json_blocks_use_env_indirection_and_canonical_type(self):
        section = self._mcp_section()
        # Global invariant: EVERY hosted JSON block keeps the key in an env var
        # (a new harness row must not reintroduce a literal key).
        hosted = self._row(section, "<h3>Setup — hosted (no install)</h3>",
                           "<h3>Setup — self-hosted (stdio)</h3>")
        blocks = re.findall(r"<pre><code>(\{.*?\})</code></pre>", hosted, re.S)
        assert blocks, "no hosted MCP JSON blocks found in the docs #mcp section"
        for raw in blocks:
            server = json.loads(raw)["mcpServers"]["tortoise"]
            assert "tt_" not in json.dumps(server), f"literal key in hosted block: {server}"
            if "url" in server:
                assert "${" in server["headers"]["Authorization"], (
                    f"hosted block is not env-indirected: {server}")
        # Identify each block by heading slice — Claude Code and Pi share the
        # same plain ${VAR} header token, so a header search is ambiguous.
        claude = self._row(section, "<h4>Claude Code</h4>", "<h4>Cursor")
        cursor = self._row(section, "<h4>Cursor", "<h4>Pi")
        pi = self._row(section, "<h4>Pi", "<h4>Codex CLI</h4>")
        claude_cfg = self._json_block(claude)
        cursor_cfg = self._json_block(cursor)
        pi_cfg = self._json_block(pi)
        assert claude_cfg.get("type") == "http", f"Claude Code must carry type http: {claude_cfg}"
        assert claude_cfg["headers"]["Authorization"] == "Bearer ${TORTOISE_API_KEY}"
        assert "type" not in cursor_cfg, "Cursor remote config must omit `type`"
        assert cursor_cfg["headers"]["Authorization"] == "Bearer ${env:TORTOISE_API_KEY}"
        assert "type" not in pi_cfg, "Pi remote config must omit `type`"
        assert pi_cfg["headers"]["Authorization"] == "Bearer ${TORTOISE_API_KEY}"

    def test_docs_stdio_block_carries_no_api_key(self):
        # #702 class: TORTOISE_API_KEY disables the stdio transport, and the
        # server needs TORTOISE_DB_URI — the docs block must not set the key.
        section = self._mcp_section()
        stdio = self._row(
            section,
            "<h3>Setup — self-hosted (stdio)</h3>",
            "<h3>What your agent can do</h3>",
        )
        env = self._json_block(stdio)["env"]
        assert "TORTOISE_API_KEY" not in env, "stdio config must not set TORTOISE_API_KEY (#702)"
        # canonical compose sidecar URI (password + graph) — pinned so the
        # docs cannot silently drift back to the passwordless form.
        assert env.get("TORTOISE_DB_URI") == "docker://:falkordb@localhost:6379/tortoise", env

    # ── onboarding skill (canonical) ───────────────────────────────────

    def test_skill_does_not_teach_streamable_type(self):
        skill = self.SKILL.read_text(encoding="utf-8")
        assert '"type": "streamable-http"' not in skill
        assert 'use `"http"`' in skill, "skill must state the canonical type value"

    def test_skill_cursor_and_pi_rows_omit_type(self):
        skill = self.SKILL.read_text(encoding="utf-8")
        for start_h, end_h in (("### Cursor (self-install)", "### Codex CLI"),
                               ("### Pi (self-install)", "### Claude Desktop")):
            assert start_h in skill, f"skill row heading missing: {start_h}"
            assert end_h in skill, f"skill row heading missing: {end_h}"
            row = skill.split(start_h, 1)[1].split(end_h, 1)[0]
            assert '"type"' not in row, f"{start_h}: remote config must omit `type`"

    # ── cross-surface agreement (issue #3145 verification checklist) ───

    def test_docs_and_skill_agree_on_canonical_type(self):
        docs = self.DOCS.read_text(encoding="utf-8")
        skill = self.SKILL.read_text(encoding="utf-8")
        assert '"type": "http"' in docs
        for name, surface in (("docs.html", docs), ("SKILL.md", skill)):
            assert '"type": "streamable-http"' not in surface, f"{name} still teaches streamable-http"


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

    def _capture_install_body(self, harness: str) -> str:
        """The harness's entry in HARNESS_CAPTURE_INSTALL, with a shared
        constant (PI/CODEX/CURSOR_CAPTURE_INSTALL) expanded.  A harness whose
        MCP copy is a JSON file (Cursor) carries its capture step here rather
        than in HARNESS_INSTALL."""
        block = self._object(self._src(), "HARNESS_CAPTURE_INSTALL")
        m = re.search(
            rf"\n\s*'?{re.escape(harness)}'?:\s*([A-Za-z_][A-Za-z0-9_]*)", block)
        if not m:
            return ""
        name = m.group(1)
        return self._constant_text(self._src(), name) or name

    def test_every_seam_harness_is_committed_and_installed(self):
        """The seam map is the general contract the other harnesses can be
        checked against: declared ⟺ committed ⟺ installed.

        The install step may live in the MCP-setup copy (Pi/Codex embed it) or
        in the capture-install surface (Cursor's copy is a JSON file).  Either
        surface must name the declared artifact."""
        src = self._src()
        seam = self._seam()
        assert seam.get("pi") == "tortoise/pi-hooks/tortoise-capture.ts"
        assert seam.get("claude") == "tortoise/claude-hooks/session-end.sh"
        assert seam.get("cursor") == "tortoise/cursor-hooks/session-end.sh"
        install = self._object(src, "HARNESS_INSTALL")
        for harness, artifact in seam.items():
            assert (REPO_ROOT / artifact).is_file(), (
                f"HARNESS_CAPTURE_SEAM.{harness} names a missing artifact: {artifact}"
            )
            body = (self._expanded_install_body(install, harness)
                    + "\n" + self._capture_install_body(harness))
            assert artifact in body, (
                f"HARNESS_INSTALL/{harness} capture-install surface does not "
                f"install its declared seam {artifact}"
            )


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
    of a stdio `env`/`command`/`args`, and no literal credential in the file.
    "No literal credential" is scoped deliberately: every `env` and `headers`
    value of EVERY server must be structurally env-indirect and a non-empty
    string (those are the credential-carrying fields a client sends), every
    `env` value must additionally name ITS OWN section key verbatim
    (`{"VAR": "${VAR}"}`): the `env` key IS the variable a client exports, so
    a lookalike (`${VAR_TYPO}`) clears the shape check yet is unset in practice
    and expands to `""` -- the same silent-401 class the Authorization header
    guard pins. `headers` are exempt from the name-match because a header key
    (`Authorization`) is a header name, not a variable name. And every token
    family this repo mints today -- `tt_`/`tk_` (tortoise/auth.py),
    `oat_`/`ort_`/`ct_`/`cs_` (tortoise/oauth.py), `st_`
    (tortoise/hosted_api.py) -- is forbidden anywhere in the file, including
    prose and argv, since a key pasted there is the same leak. A THIRD-PARTY
    secret in PROSE or in `args`/`command` is out of scope: argv legitimately
    holds package names and flags, and telling a secret from ordinary text
    needs a heuristic that hyphenated keys defeat, so either check would be
    theatre.

    Reads the file on disk (the committed blob in any clean checkout /
    CI). This class pins what the repo SHIPS, so it is deliberately not
    override-aware: a self-hoster who follows the entry's `_comment` and points
    `url` at their own daemon WILL see `test_tortoise_entry_targets_hosted_endpoint`
    fail locally. That is expected -- the message describes the shipped default,
    not their tree -- and it is why this pins the shipped file rather than an
    effective or merged config.
    """

    COMMITTED = REPO_ROOT / ".mcp.json"
    # Scheme words that may precede a ${...} expression in a header value.
    GLUE = ("", "Bearer ", "Token ", "Basic ", "ApiKey ")
    # A `${VAR}` expression must name a bare variable: content inside the
    # braces -- a shell default (`${VAR:-literal}`) or a nested expression --
    # can smuggle a literal past any check that only looks for `${`.
    ENV_EXPR = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
    SPAN = re.compile(r"\$\{[^}]*\}")

    @staticmethod
    def _strip_env_spans(value: str) -> str:
        """`value` with every ${...} span removed -- what is left is literal."""
        return re.sub(r"\$\{[^}]*\}", "", value)

    def _parse_strict(self, text: str) -> dict:
        """Parse the way the CLIENT does.

        `JSON.parse` rejects the non-standard `NaN`/`Infinity` tokens that
        Python's `json` accepts, and the pi client treats a read failure as
        "skipping MCP" -- i.e. every server, `tortoise` included, silently
        stops working. Same failure class as #3601, so the guard has to see it.
        """

        def _reject(token: str):
            raise AssertionError(
                f"committed .mcp.json contains the non-JSON token {token!r} -- "
                f"JSON.parse rejects it, so the client skips the whole file"
            )

        return json.loads(text, parse_constant=_reject)

    def _servers(self) -> dict:
        assert self.COMMITTED.is_file(), f"committed {self.COMMITTED} is missing"
        cfg = self._parse_strict(self.COMMITTED.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers")
        assert isinstance(servers, dict), (
            f"committed .mcp.json has no mcpServers object (got {type(servers).__name__})"
        )
        return servers

    def _tortoise(self) -> dict:
        servers = self._servers()
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
        # This root file also serves Claude Code, whose schema treats `type` as
        # load-bearing for a url entry; pi ignores `type` and picks the
        # transport from url-vs-command. Pinned because the sibling doc
        # docs/quickstart-cloud.md:47 names a DIFFERENT value (streamable-http)
        # for the same object, and nothing else pins this field.
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
        # Exact value, not a substring: a lookalike env var
        # (`${TORTOISE_API_KEY_ALT}`) is unset in practice and expands to
        # `Bearer ` -- a silent 401 that a substring check would pass. Matches
        # what the emitted-config classes pin for the same header.
        assert auth == "Bearer ${TORTOISE_API_KEY}", (
            f"committed .mcp.json Authorization must be exactly "
            f"'Bearer ${{TORTOISE_API_KEY}}' (env-indirect, no literal key) -- "
            f"got {auth!r}"
        )

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
        # This file ships to users -- a literal credential leaks one. Resolve
        # the servers FIRST: an absent file must fail with this class's
        # authored `committed ... is missing` message, as the other four
        # tests do, not a bare FileNotFoundError from the raw read below. (An
        # UNPARSEABLE file fails with its own JSONDecodeError -- an accurate
        # message too, just not this class's authored one.)
        servers = self._servers()
        text = self.COMMITTED.read_text(encoding="utf-8")
        # (a) Every token family this repo MINTS, anywhere in the raw text: a
        # key pasted into a `_comment` or into `args` is the same leak. `tt_`/
        # `tk_` come from tortoise/auth.py, `oat_`/`ort_` and the client id/
        # secret `ct_`/`cs_` from tortoise/oauth.py, `st_` from
        # tortoise/hosted_api.py. A family minted by NEW code is not covered
        # here -- the values a client sends are, by (b).
        #
        # Anchored to a token START -- which is how every consumer checks these
        # prefixes (str.startswith, never a substring search) -- so ordinary
        # prose cannot red the guard: "support_ticket" and "float_value"
        # contain "ort_"/"oat_" mid-word and are not tokens.
        families = (
            *API_KEY_PREFIXES,
            ACCESS_TOKEN_PREFIX,
            REFRESH_TOKEN_PREFIX,
            "ct_",
            "cs_",
            "st_",
        )
        prefixes = "|".join(re.escape(p) for p in families)
        leak = re.search(rf"(?<![A-Za-z0-9_])(?:{prefixes})", text)
        assert leak is None, (
            f"literal key material in committed .mcp.json (found "
            f"{leak.group(0)!r}) -- keys must stay env-indirect"
        )
        # (b) Every `env` and `headers` VALUE of EVERY server must be
        # STRUCTURALLY indirect. Substring checks are not enough: a value like
        # `Bearer ${KEY}sk-live-...` contains `${` yet ships a literal, so the
        # test is what remains after every `${...}` span is removed. Reads
        # PARSED values, so a JSON-escaped literal the raw-text scan cannot see
        # is caught too, and any token family is caught.
        for server, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            for section in ("env", "headers"):
                values = entry.get(section)
                assert values is None or isinstance(values, dict), (
                    f"committed .mcp.json {server}.{section} must be an object "
                    f"-- got {type(values).__name__}"
                )
                for key, value in (values or {}).items():
                    assert isinstance(value, str) and value, (
                        f"committed .mcp.json {server}.{section}.{key} must be "
                        f"a non-empty string -- got {type(value).__name__}"
                    )
                    if section == "env":
                        direct = self.ENV_EXPR.fullmatch(value) is not None
                    else:
                        spans = self.SPAN.findall(value)
                        direct = (
                            bool(spans)
                            and all(self.ENV_EXPR.fullmatch(s) for s in spans)
                            and self._strip_env_spans(value) in self.GLUE
                        )
                    assert direct, (
                        f"committed .mcp.json {server}.{section}.{key} is not "
                        f"env-indirect ({value!r}) -- a user-shipped value must "
                        f"be a ${{VAR}} expression, with at most a scheme word "
                        f"around it"
                    )
                    if section == "env":
                        assert value == f"${{{key}}}", (
                            f"committed .mcp.json {server}.env.{key} must be "
                            f"exactly '${{{key}}}' -- the env key names the "
                            f"variable a client exports, so {value!r} is unset "
                            f"in practice and expands to an empty value"
                        )
