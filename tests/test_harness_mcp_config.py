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

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.__main__ import (  # noqa: I001
    _cmd_install_hooks,
    _harness_mcp_config,
    _harness_stdio_config,
    _onboard_ready_lines,
    _print_harness_instructions,
)
from tortoise.capture_install import PI_EXTENSION_NAME
from tortoise.auth import API_KEY_PREFIXES
from tortoise.oauth import ACCESS_TOKEN_PREFIX, REFRESH_TOKEN_PREFIX

REPO_ROOT = Path(__file__).resolve().parent.parent
# #984 contract (merged to main): the hosted endpoint always carries the
# trailing slash — pinned here so a regression to the unsuffixed URL fails.
ENDPOINT = "https://api.premiselabs.co/mcp/"

#: #3502 — where an install surface names an extension: `<name> extension`
#: (prose) or `extensions/<name>` (an install-path claim). Kept to those two
#: shapes — rather than a bare `tortoise-…` scan — because the same surfaces
#: legitimately name `tortoise-config.json`, `tortoise-onboarding` (a skill),
#: `tortoise_create_point()` (a tool) and `tortoise-session-end.sh` (a shell
#: hook). A blanket scan false-positives on all four.
_EXTENSION_CLAIMS = (
    re.compile(r"(?P<name>tortoise-[a-z0-9-]+(?:\.[a-z0-9]+)?) extension\b"),
    re.compile(r"extensions/(?P<name>tortoise-[a-z0-9-]+(?:\.[a-z0-9]+)?)"),
)


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

    def test_pi_capture_note_does_not_call_the_second_producer_ungated(self):
        """#3512/#8900: the dashboard's Pi copy surface carried the pre-#803 claim.

        ``HARNESS_CAPTURE_INSTALL.pi`` is a source module users never read raw,
        but it is the copy surface this file guards, and its header comment still
        said the agent-infra ``reflect-hook`` is "the other ungated producer".
        agent-infra ``7e77470e4`` (2026-10-16, #803) closed that: both recorders
        now share ``extensions/shared/capture-gate.ts``, which uploads only on an
        explicit ``"cloud": true`` in the operator config *and* a key. A stale
        comment here is how the next lane re-derives a divergence that is closed
        at source — the same defect class #3512 exists to correct, on a surface
        the README guard cannot see (this file is selected by
        ``website/apps/dashboard/src/``, not by ``tortoise/pi-hooks/``).

        Mutation: restore "the other ungated producer" in ``harnesses.js`` — this
        REDs; drop the ``capture-gate.ts`` reference — this REDs.

        **Only the phrasing that actually lived in THIS file is asserted here.**
        The sibling stale claim — "credential presence alone" — never appeared in
        ``harnesses.js`` at all (measured: ``git show
        origin/main:website/apps/dashboard/src/harnesses.js | grep -c`` = 0).
        The copies this record retires are in ``tortoise/pi-hooks/README.md`` and
        the shipped artifact's header, and are pinned there by
        ``test_pi_capture_disposition.py::test_the_second_producers_gate_is_recorded_as_it_now_stands``;
        the copies it deliberately does **not** reach — ``.env.example``,
        ``docs/quickstart-cloud.md``, ``tortoise/onboarding/SKILL.md`` and its
        served mirror — are tracked by #8900.
        Carrying it in this file's stale-phrase list would be an assertion that
        can never RED on any version of this file — a false PASS, which is the
        one thing the sibling suite refuses to ship: ``tests/test_capture_install.py``
        states the discipline ("A test that cannot RED is a false PASS and does
        not belong here").
        """
        html = self.HARNESSES.read_text(encoding="utf-8")
        assert "the other ungated producer" not in html, (
            "the Pi copy surface still describes the pre-#803 gate: reflect-hook "
            "requires an explicit `cloud: true` since agent-infra 7e77470e4 "
            "(2026-10-16, #803)")
        assert "capture-gate.ts" in html, (
            "the Pi copy surface must name the shared egress gate both "
            "agent-infra recorders now use, not a bare 'ungated' claim")


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
    there is a PROJECT bound, not a platform rule, so this guard must not reject
    a legitimate rise toward the documented 600 s.

    #3803 corrected the parenthetical that used to sit on that sentence ("~6×
    headroom over the digest path"): no SessionStart measurement ever backed it.
    The ratio is 60/9.26 — the session-END measurement carried across events,
    the same borrowed-figure error #3793 corrected in the envelope, left
    standing in the prose — and a wall-clock claim nothing in the repo can
    reproduce is not evidence. #3793 also left SessionStart with a CEILING only, so a shipped
    `"timeout": 1` passed this guard and shipped the #3754 failure mode on the
    session-start side: `session-start.sh` is fail-open, the hook is cancelled
    before `tortoise context` can return, and the user gets a session with no
    memory digest and NO error. `MIN_TIMEOUT_S` below is that missing floor.

    The 9.26 s floor is a SessionEnd measurement, asserted only where that
    measurement exists.

    FOUR surfaces carry the same `settings.json` snippet, and a copy that loses
    its `timeout` re-opens the bug on that surface alone. #3802 corrected this
    docstring's reachability claim: the first is NOT shipped to any user today.
    `HARNESS_INSTALL` is read only by main.jsx's archived
    `{LEGACY_WIZARD_ARCHIVED && …}` render (the flag is a hard `false`;
    `website/apps/dashboard/src/wizardArchived.test.js` pins that census), so it
    is the A0 ROLLBACK copy. It is asserted here ANYWAY, deliberately:

      * the guard is cheap — it is a JSON parse of a literal already in the file;
      * a rollback re-enables that render, and it must not re-open #3754 then;
      * dropping the leg would leave the rollback path's `settings.json` entirely
        unguarded (the `session-end.sh`/`session-start.sh` legs cover only the
        in-repo install comments, not the dashboard copy).

    Do NOT delete surface 1 without also deciding who guards the rollback copy —
    and note that surface 2 is the LIVE carrier for the dashboard copy a user
    pastes (the Memory-sources row), so an edit meant to reach users belongs
    there (`harnesses.test.js` asserts that row separately, #3802):

    1. `harnesses.js` `HARNESS_INSTALL.claude` — the A0 ROLLBACK copy (reference-only today, #3802)
    2. `harnesses.js` `HARNESS_CAPTURE_INSTALL.claude` — the LIVE Memory-sources step
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
    # #3754: floors that ARE measurements, asserted only where the measurement
    # exists (a real hosted run with the seam-literal settings) — never carried
    # across events.
    MEASURED_S: ClassVar[dict[str, float]] = {"SessionEnd": 9.26}
    # #3803: floors that are PROJECT BOUNDS — policy, not measurements, and
    # labelled as such here AND in the failure message. `SessionStart` was the
    # gap #3793 left: a ceiling only, so `"timeout": 1` passed and re-opened
    # #3754 on the session-start side (fail-open hook cancelled before the
    # digest returns → no digest, no error).
    #
    # 20 s is DERIVED from the digest's own code-permitted worst case plus its
    # measured cold start, not picked for taste:
    #
    #   * `tortoise/__main__.py::_cmd_context` allows exactly ONE
    #     `urlopen(req, timeout=15)` on the hosted leg, so a single legitimate
    #     API wait can occupy 15 s.
    #   * the measured `tortoise context` → stdout run against a populated
    #     3.8 MB embedded store (the server starts on demand, as it does on a
    #     user's machine): cold 3.40 s on 3/3 runs, warm 2.07–2.15 s on 5/5,
    #     2026-10-11 on a dev box.
    #
    # 15 + 3.40 = 18.4 ⇒ 20 s: a shipped timeout below it cannot cover the
    # slowest run the digest's own code permits, so the hook can be cancelled
    # while the digest is still legitimately waiting.
    #
    # Deliberately NOT the 9.26 s SessionEnd figure: no SessionStart measurement
    # backs it, and asserting a session-END figure for SessionStart is the exact
    # error #3793 corrected. The local/embedded leg is still unbounded in code
    # (#3796 records a measured ~26.8 s slow-store query), so this floor bounds
    # the KNOWN path only — #3796, not this constant, owns that bound, and the
    # shipped 60 s sits above it.
    MIN_TIMEOUT_S: ClassVar[dict[str, float]] = {"SessionStart": 20}
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
        # #3803: a floor keyed on an event with no ceiling — or set at/above its
        # own ceiling — would make EVERY shipped value RED. Pin the invariant
        # here, where the failure names the constant, not as a bare red suite.
        for event, floor in self.MIN_TIMEOUT_S.items():
            assert event in self.MAX_TIMEOUT_S, (
                f"#3803: MIN_TIMEOUT_S carries a {event} floor and "
                f"MAX_TIMEOUT_S carries no {event} ceiling"
            )
            assert floor < self.MAX_TIMEOUT_S[event], (
                f"#3803: the {event} floor ({floor}s) is not below its ceiling "
                f"({self.MAX_TIMEOUT_S[event]}s) — no shipped timeout could pass"
            )
        for name, event, entry in self._walk():
            self._assert_entry_within_envelope(name, event, entry)

    def _assert_entry_within_envelope(self, name: str, event: str, entry: dict) -> None:
        """The per-entry guard — the #3754 envelope + the #3803 floor.

        Factored out so the shipped-snippet walk above AND the boundary test
        below exercise the SAME assertions: a boundary test that re-implemented
        them could stay green while the shipped-snippet path regressed.
        """
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
        floor = self.MIN_TIMEOUT_S.get(event)
        assert floor is None or timeout >= floor, (
            f"#3803: {name} {event} timeout={timeout}s is below this guard's "
            f"{event} floor ({floor}s — a PROJECT BOUND: the digest's own "
            f"code-permitted worst case is a 15s API wait plus a 3.4s measured "
            f"cold start), so the hook is cancelled before a slow-but-healthy "
            f"digest returns and, the hook being fail-open, the session gets no "
            f"memory digest and no error"
        )

    def test_session_start_floor_rejects_the_boundary_timeouts(self):
        """#3803: `SessionStart` 1 is RED; 60 (shipped) and 120 pass; 601 is RED.

        The floor IS the fix — before it, `SessionStart` had a ceiling only and
        a shipped `"timeout": 1` passed (the verifier's reproduction of P2
        #3803). Exercised on synthetic entries through the SAME per-entry guard
        the shipped-snippet walk uses, so the boundaries are pinned without
        hand-mutating a source file, while the walk above proves the shipped
        copies themselves.
        """
        entry = {"type": "command",
                 "command": ".claude/hooks/session-start.sh", "timeout": 0}
        for bad in (1, 5, 19):            # below the 20s floor → RED
            with pytest.raises(AssertionError):
                self._assert_entry_within_envelope(
                    "synthetic", "SessionStart", {**entry, "timeout": bad})
        for good in (20, 60, 120, 600):   # at/above the floor, within the ceiling
            self._assert_entry_within_envelope(
                "synthetic", "SessionStart", {**entry, "timeout": good})
        for high in (601, 3600):          # above the 600s ceiling → RED
            with pytest.raises(AssertionError):
                self._assert_entry_within_envelope(
                    "synthetic", "SessionStart", {**entry, "timeout": high})

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
        return self._capture_call(_print_harness_instructions, harness)

    @staticmethod
    def _capture_call(fn, *args) -> str:
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            fn(*args)
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

    # ── #3502 — the phantom-extension drift guard ──────────────────────
    #
    # These surfaces used to advertise a `tortoise-context` extension that has
    # never existed in this repo or in agent-infra, plus a per-turn context
    # injection Pi has no seam for (#2119 is the injection seam and is still
    # open). The name was hardcoded prose with nothing coupling it to the
    # shipped artifact, so nothing failed when the two drifted. The name is now
    # DERIVED from the install contract; these tests are the CI half of the
    # issue's fix — the drift fails the build instead of the user.

    def test_pi_block_names_the_installed_extension(self):
        """#3502: the Pi block must name the extension the installer writes.

        MUTATION: restore either the `tortoise-context` name or the
        "auto-injects context" promise → RED."""
        out = self._capture("pi")
        assert PI_EXTENSION_NAME in out, out
        assert "tortoise-context" not in out, (
            "`tortoise-context` has never existed — the Pi seam is the capture "
            "extension declared in hook_install.ARTIFACT_CONTRACTS")
        # The gap is Tortoise's wiring of Pi's injection seam, NOT a ceiling in
        # Pi: Pi's extension API exposes `before_agent_start`, which injects a
        # message (our own `skill-enforcer`/`loop-enforcer`/`cmux-session`
        # extensions use it). So do NOT assert "auto-inject" is absent in the
        # abstract — that would red the build for copy that is TRUE once #2119
        # lands. Pin the honest disclosure about the SHIPPED extension instead.
        assert "does not inject context per turn" in out, (
            "the Pi block must disclose that the SHIPPED extension is capture "
            "only (Tortoise's per-turn injection is #2119, still open)")
        assert "#2119" in out, (
            "the disclosure must point at the issue that wires the seam")
        assert (REPO_ROOT / "tortoise" / "pi-hooks" / PI_EXTENSION_NAME).is_file(), (
            "the advertised extension must be a real file the installer copies")

    def test_every_advertised_extension_resolves_to_a_real_artifact(self):
        """#3502: every extension an install surface names must resolve to a
        real extension artifact.

        Covers prose claims (`<name> extension`) and install-path claims
        (`extensions/<name>`). MUTATION: hardcode any other extension name
        into any of these surfaces → RED.
        """
        from types import SimpleNamespace

        from tortoise.__main__ import _install_read_hook_impl

        real = self._real_extension_names()
        surfaces = {
            f"_print_harness_instructions({harness!r})": self._capture(harness)
            for harness in ("pi", "multiple", "claude", "codex", "cursor")
        }
        surfaces["onboard completion banner"] = "\n".join(_onboard_ready_lines())
        surfaces["`tortoise install --list` catalogue"] = self._capture_call(
            _install_read_hook_impl, SimpleNamespace(list=True, harness=None))
        # #3502 P2/P3: the `--uninstall` message names the install path too —
        # it was the lone retyped literal in the class this guard closes.
        surfaces["`tortoise install pi --uninstall` message"] = self._capture_call(
            _cmd_install_hooks, SimpleNamespace(uninstall=True, harness="pi"))

        advertised: set[str] = set()
        for label, text in surfaces.items():
            for pattern in _EXTENSION_CLAIMS:
                for match in pattern.finditer(text):
                    name = match.group("name")
                    advertised.add(name)
                    assert name in real, (
                        f"{label} advertises the extension `{name}`, which is "
                        f"not a real extension artifact (real names: "
                        f"{sorted(real)}) — every Pi seam is declared once, "
                        "in hook_install.ARTIFACT_CONTRACTS")
        assert PI_EXTENSION_NAME in advertised, (
            "the Pi surfaces must name the extension the installer writes; a "
            "guard that also passes when the claim is deleted pins nothing")

    def test_pi_uninstall_message_derives_the_extension_name(self, monkeypatch):
        """#3502: `tortoise install pi --uninstall` must name the file the
        installer writes, DERIVED — never a retyped literal.

        The surface above can only prove the literal is *currently* correct
        (the literal and the contract agree today). Moving the contract is the
        drift this pins: with the name patched to something else, a derived
        message follows it and a hardcoded one does not.

        MUTATION: restore `tortoise-capture.ts` as a literal in
        `_cmd_install_hooks` → the mutated name never appears → RED.
        """
        from types import SimpleNamespace

        monkeypatch.setattr(
            "tortoise.capture_install.PI_EXTENSION_NAME", "tortoise-renamed.ts")
        out = self._capture_call(
            _cmd_install_hooks, SimpleNamespace(uninstall=True, harness="pi"))
        assert "tortoise-renamed.ts" in out, (
            "the --uninstall message retyped the extension name instead of "
            "deriving it from the install contract:\n" + out)
        assert "tortoise-capture.ts" not in out, out

    @staticmethod
    def _real_extension_names() -> set[str]:
        """Every seam name the installer may legitimately advertise, WITH and
        WITHOUT its file suffix — read off the install contract, never
        retyped (`tortoise-capture.ts` is the installed basename; the stem is
        the name a user reads in prose)."""
        from tortoise.hook_install import ARTIFACT_CONTRACTS

        names: set[str] = set()
        for contract in ARTIFACT_CONTRACTS.values():
            names.add(contract.install_name)
            names.add(Path(contract.install_name).stem)
        return names


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
    QUICKSTART = REPO_ROOT / "docs" / "quickstart-cloud.md"
    PACKS_JS = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "packs.js"
    MAIN_JSX = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"
    MCP_SERVER = REPO_ROOT / "tortoise" / "mcp_server.py"

    # Per-harness headings in the docs #mcp section. Rows are pinned by
    # heading (not a bare "Codex"/"Pi" substring) so deleting a row fails.
    ROW_HEADINGS = ("<h4>Claude Code</h4>", "<h4>Cursor", "<h4>Pi",
                    "<h4>Codex CLI</h4>", "<h4>Codex Desktop (no terminal)</h4>",
                    "<h4>Claude Desktop</h4>", "<h4>Claude Web</h4>",
                    '<h4 id="chatgpt">ChatGPT</h4>')

    def test_quickstart_cloud_teaches_the_http_shape_indirectly(self):
        """#4674: the cloud quickstart taught `streamable-http` plus a literal
        `Bearer tt_...` key -- the exact anti-pattern this module forbids -- and
        nothing pinned it, so it re-drifted (its own guard comment at the
        `.mcp.json` http-type test only *mentions* the divergence).

        The prose deliberately NAMES the legacy value, because a first-time
        reader needs to recognise it; so the assertions target the
        copy-pasteable SHAPE. The per-harness checks PARSE the blocks, so a bare
        string sitting in prose cannot satisfy them, and the Cursor block is
        pinned because a single block labelled for Cursor while carrying the
        Claude form is the same silent-drop defect one layer down.
        """
        text = self.QUICKSTART.read_text(encoding="utf-8")
        assert '"Bearer tt_' not in text, (
            "#4674: docs/quickstart-cloud.md shows a literal key on a commit "
            "surface; it must read ${TORTOISE_API_KEY}"
        )
        for cfg in self._quickstart_json_blocks():
            for entry in cfg.get("mcpServers", {}).values():
                assert entry.get("type") != "streamable-http", (
                    "#4674: docs/quickstart-cloud.md is teaching the banned "
                    "streamable-http entry again -- Cursor CLI drops it silently"
                )
        claude = self._quickstart_block("**Claude Code**")
        assert claude["type"] == "http", (
            "#4674: the quickstart's Claude Code block must use type=http"
        )
        assert claude["headers"]["Authorization"] == "Bearer ${TORTOISE_API_KEY}", (
            "#4674: the quickstart's Claude Code header must be env-indirect"
        )
        cursor = self._quickstart_block("**Cursor**")
        assert "type" not in cursor, (
            "#4674: the quickstart's Cursor block must omit `type` -- Cursor "
            "infers the transport from url, and a `type` there is the shape "
            "its CLI can silently drop"
        )
        assert cursor["headers"]["Authorization"] == "Bearer ${env:TORTOISE_API_KEY}", (
            "#4674: the quickstart's Cursor block must use the tested "
            "${env:...} expansion"
        )

    def _quickstart_json_blocks(self) -> list[dict]:
        """Every ```json config block in the quickstart, parsed."""
        text = self.QUICKSTART.read_text(encoding="utf-8")
        return [json.loads(b) for b in re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)]

    def _quickstart_block(self, label: str) -> dict:
        """The parsed `tortoise` entry of the JSON block following `label`.

        Parsed, not substring-matched, so the harness labels and the block
        contents are pinned together (a bare string in prose cannot pass).
        """
        text = self.QUICKSTART.read_text(encoding="utf-8")
        assert label in text, f"quickstart harness label missing: {label}"
        m = re.search(r"```json\s*(\{.*?\})\s*```", text.split(label, 1)[1], re.S)
        assert m, f"no JSON config block after {label!r} in quickstart"
        cfg = json.loads(m.group(1))["mcpServers"]["tortoise"]
        assert "tt_" not in json.dumps(cfg), f"literal key in config block: {cfg}"
        return cfg

    def _mcp_section(self) -> str:
        text = self.DOCS.read_text(encoding="utf-8")
        start = text.find('<h2 id="mcp">')
        assert start != -1, "docs.html lost its #mcp section anchor"
        end = text.find('<h2 id="api">', start)
        assert end != -1, "docs.html lost its #api section anchor"
        assert end > start, "docs.html #api now precedes #mcp — slice would be empty"
        return text[start:end]

    @staticmethod
    def _js_object_string(src: str, name: str, key: str) -> str:
        """The quoted value of `<name>.<key>` inside a JS object literal.

        A narrow, *fail-loud* reader, deliberately: it asserts both the
        constant and the key exist, so a rename fails here instead of silently
        degrading the owner cross-check below into a substring match against
        `""` (a vacuous pass is the failure mode this reader exists to
        remove).
        """
        start = src.find(f"export const {name} = Object.freeze({{")
        assert start != -1, (
            f"{name} is no longer an `Object.freeze({{...}})` literal — the copy "
            "the docs row quotes has lost its owner")
        body = src[start:]
        end = body.find("\n})")
        assert end != -1, f"{name}'s object literal is not terminated as expected"
        m = re.search(rf"\b{key}:\s*(['\"])(?P<val>.*?)\1", body[:end], re.S)
        assert m, (
            f"{name}.{key} is gone — the docs row cites it as the owner of its "
            "sentence, so the sentence can no longer be derived")
        return m.group("val")

    @staticmethod
    def _copy_norm(text: str) -> str:
        """Dash-folded, quote-folded, whitespace-collapsed, lower-cased copy.

        The docs row is PROSE: it may swap the module's `;` for a dash, add an
        `only`, or wrap a line. The claim is not byte-identity — it is that the
        row carries the same QUALIFIER the owning module states, so both sides
        are normalised and the module's fragments are required to survive.
        """
        folded = (text.replace("\u2014", "-").replace("\u2013", "-")
                      .replace("\u2019", "'").replace("\u201c", '"')
                      .replace("\u201d", '"'))
        return re.sub(r"\s+", " ", folded).strip().lower()

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

    # The dashboard's harness table: it pins the connector URL the ChatGPT row
    # must teach (#2864).
    HARNESSES = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "harnesses.js"

    @classmethod
    def _read_harnesses(cls) -> str:
        """One reader for `harnesses.js`, so the parses below cannot drift."""
        return cls.HARNESSES.read_text(encoding="utf-8")

    @classmethod
    def _dashboard_constant(cls, name: str) -> str:
        """The dashboard's exported string constant `name`.

        Exactly ONE match: a second definition would make the assertion below
        read whichever came first and silently drop the other from every guard
        that uses it.
        """
        found = re.findall(rf"^export const {name} =\s*\n?\s*'([^']+)'",
                           cls._read_harnesses(), re.M)
        assert len(found) == 1, (
            f"{name} must be defined exactly once in harnesses.js, found "
            f"{len(found)}: {found}")
        return found[0]

    def test_4836_docs_page_is_the_live_chatgpt_carrier(self):
        """#4836: the public docs page carries the ChatGPT journey end-to-end.

        The premise this file was written against is HISTORY now: ChatGPT used to
        have no dashboard CHOOSER/connect surface (#2912's 4-family chooser kept
        it out; #2698 deleted the flat tab first), so the docs page was the only
        carrier — and it CLAIMED a "the dashboard's ChatGPT tab" that never
        existed. That claim was false and must not come back.

        #9660 makes ChatGPT a live chooser leaf with its own connect card, so the
        docs row and the card are two renderings of ONE journey. This test keeps
        pinning the docs row as the journey a ChatGPT user performs, because it is
        the page the served onboarding document links to; a `/docs#chatgpt`
        visitor has to reach a verified connection, not just a URL. Dropping a
        step, or replacing one with a claim that is false for ChatGPT (a pasted
        key, an unnamed plan set), must fail here rather than ship.
        """
        canonical = self._dashboard_constant("CANONICAL_MCP_URL")
        instructions_url = self._dashboard_constant("ONBOARDING_INSTRUCTIONS_URL")
        slashed = self._dashboard_constant("MCP_URL")
        assert slashed == canonical + "/", "MCP_URL is the slashed keyed-row form"
        docs = self.DOCS.read_text(encoding="utf-8")
        assert "ChatGPT tab" not in docs, (
            "docs.html must not send a ChatGPT user to the retired dashboard tab")
        assert "it has no row below" not in docs, (
            "docs.html must not claim ChatGPT has no row below once the row lands")
        section = self._mcp_section()
        start = section.find('<h4 id="chatgpt">')
        assert start != -1, "docs #mcp must carry a ChatGPT carrier row (#4836)"
        # Bound the slice to the row's own end — the next heading OR the
        # hosted card's callout, which follows the row — so the literals below
        # cannot be satisfied elsewhere in the section (the id is un-anchored
        # because the docs indent their `<h4>` rows).
        row = section[start:]
        for delim in ('<p class="callout">', "<h3", "<h4"):
            cut = row.find(delim, 1)
            if cut != -1:
                row = row[:cut]
        # ...then to the ordered list INSIDE that row, so the step literals below
        # cannot be satisfied by the row's closing prose instead of a step.
        assert "<ol>" in row and "</ol>" in row, (
            "the ChatGPT row must carry the ordered procedure the user performs")
        steps = row.split("<ol>", 1)[1].split("</ol>", 1)[0]
        for step in (
            "chatgpt.com/plugins",       # 1 — the surface the connector is created on
            "Add custom MCP server",     # 1 — the real entry point (#9660)
            "Create as a plugin",        # 2 — the creation dialog's primary button
            "settings/plugins-settings",  # 3 — where the installed plugin + its Connect live
            "View details",              # 3 — the observed control on the plugin row
            "Connect",                   # 3 — the OAuth grant is a SEPARATE act
            "Connect Tortoise",          # 3 — the consent dialog between Connect and Authorize
            "Authorize",                 # 3 — consent (key-less connect completes here)
            "pick the Organization you are onboarding",  # 3 — the MULTI-org chooser (oauth.py)
                                       #     hides the named-org line and keeps
                                       #     Authorize disabled until it is picked
            "chatgpt.com/skills",        # 4 — the Skills surface the archives install on
            "Install the Tortoise skills",  # 4 — the hand-over is an INSTALL, not a paste
            "Upload from your computer", # 4 — ChatGPT's own Skills surface is the hand-off
            "Replace existing",          # 4 — the only update path a user-owned snapshot has
            "select all three files",   # 4 — one pick installs the set (verified: multi-select)
            "verifies the connection",  # 5 — the row names the verification hand-off
            "files your first memory",   # 5 — the journey's outcome
            "plan-dependent",            # 5 — which tools appear is plan-dependent
        ):
            assert step in steps, (
                f"the ChatGPT row must keep step {step!r} — a /docs#chatgpt "
                "visitor follows this list to a verified connection (#4836)")
        # #9660 owner ruling (copy): the row is a list of ACTIONS. The graph and
        # pack material is context — the user has nothing to do at connect time —
        # so it lives below the list, not as steps 5 and 6 of a procedure. Pin the
        # structure: exactly 5 top-level steps, and the field list nested inside
        # step 2 (the creation form is ONE modal, so it reads as one step).
        nested = re.search(r"<ul>.*?</ul>", steps, re.S)
        assert nested, (
            "step 2 must render the dialog as a FIELD LIST — it is a single modal "
            "with Name/Description/Connection/Server URL/Authentication/Icon")
        for field in ("<strong>Name</strong>", "<strong>Description</strong>",
                      "<strong>Connection</strong>", "<strong>Server URL</strong>",
                      "<strong>Authentication</strong>", "<strong>Icon</strong>"):
            assert field in nested.group(0), (
                f"the dialog field list must name {field} — these are the fields "
                "OpenAI's 'Add custom MCP server' modal actually shows (#9660)")
        top_level = re.sub(r"<ul>.*?</ul>", "", steps, flags=re.S)
        assert top_level.count("<li>") == 5, (
            "the ChatGPT procedure is 5 steps; if you deliberately changed the "
            "journey, update this count and the per-step literals together")
        # #9660 owner ruling (copy): the plan floor is a CLAIM about OpenAI's
        # product, so it is stated on BOTH surfaces — the docs row is the
        # reference copy the served document points at, and a Free-plan visitor
        # who reads only it must learn why the Add button is absent.
        assert "paid ChatGPT plan" in row and "Plus" in row, (
            "the ChatGPT row must warn that custom connectors need a paid "
            "ChatGPT plan (Plus or higher)")
        # #9660: the hand-over is a `.skill` ARCHIVE per skill, not a `SKILL.md`
        # download. The uploader enforces the filename — a file named
        # `tortoise-file-finding.md` is refused ("Choose a .zip, .skill, or
        # SKILL.md file") — so several `SKILL.md` downloads collide in the user's
        # Downloads folder and the renamed copies cannot be uploaded at all.
        # This pins card → artifact: every href must name an archive that exists,
        # and no `download="SKILL.md"` link may come back, because that shape
        # instructs the user to do something the uploader rejects.
        #
        # #4365: THREE archives, and `tortoise-onboarding` is not one of them. The
        # row briefly handed over a fourth, so a ChatGPT user uploaded a document
        # whose own first screen says it is never installed as a skill — and the
        # agent reading it was told both "never a skill" and "install this as a
        # skill". The served document is still linked from this row (it is read,
        # not installed), so the reachability claim is unaffected.
        hrefs = re.findall(r'href="(/skills/[^"]+\.skill)"', steps)
        assert len(hrefs) == 3, (
            f"step 4 must hand over one .skill archive per REUSABLE skill, and no "
            f"archive for the onboarding instructions (got {hrefs})")
        assert "/skills/tortoise-onboarding.skill" not in hrefs, (
            "the onboarding document must never ship as an installable archive "
            "(#4365: it is delivered as instructions)")
        for href in hrefs:
            on_disk = (REPO_ROOT / "website" / href.lstrip("/"))
            assert on_disk.is_file(), (
                f"the card links {href} but {on_disk.relative_to(REPO_ROOT)} does "
                "not exist — run uv run python tools/build_skill_archives.py")
        assert "download=\"SKILL.md\"" not in steps, (
            "a download=\"SKILL.md\" link collides with the other three skills "
            "and the renamed file is REJECTED by the uploader (#9660)")
        # #9660: the Developer-mode PREREQUISITE is gone from OpenAI's flow, and
        # the creation dialog has no "Scan Tools" button. Both were in this row
        # and both were unasserted-against (the literals below pinned the STALE
        # copy as required), so a user hunted a toggle that no longer exists and
        # looked for a button that was never there. Read off a signed-in Plus
        # account on 2026-10-23 — `/settings/connectors` redirects to
        # `/settings/plugins-settings`, `Security and login` carries MFA /
        # sessions / Lockdown mode / app-CSP and no Developer-mode switch, and the
        # plugin's Version field reads `dev mode` (the RESULT of the flow, never a
        # step). The owner then ruled the mention OUT of user copy entirely, so
        # the bare token is a failure here too. Screenshots are attached to the
        # PR; a re-add of any literal below means the card has gone back.
        for stale in (
            "Security and login",
            "Scan Tools",
            "Developer mode",
            "(Plus / Pro / Business / Enterprise / Education)",
            "the <strong>+</strong> button",
            "Developer-mode app",
        ):
            assert stale not in steps, (
                f"the ChatGPT row must not send the user to {stale!r} — it is not "
                "in OpenAI's current connector flow (#9660, observed live)")
        # The row must NOT name a RETIRED tool: `tortoise_health` is absent from
        # `tools/list` (#3883), so a tool-grant client like ChatGPT cannot call
        # it — naming it would claim a verification step ChatGPT cannot perform.
        # The advertised equivalent is an agent-side detail for the onboarding
        # document, not a step the human reads here.
        assert "tortoise_health" not in row, (
            "the ChatGPT row must not name the retired tortoise_health (#3883) — "
            "it is not in tools/list, so ChatGPT cannot call it")
        # The URL the row teaches must be the CONNECTOR form. harnesses.js pins
        # CANONICAL_MCP_URL for every connector surface (the two Claude leaves +
        # ChatGPT): it is the canonical endpoint, the value its PRM advertises and
        # the form the shipped connector copy uses (#2864/#2849). The server
        # TOLERATES the slash — oauth.py parse_resource rstrips it, and accepts
        # the bare origin too — so this is a canonicality guard, not a hard
        # failure the user would hit; the keyed rows keep the slashed MCP_URL.
        assert f"<code>{canonical}</code>" in steps, (
            f"the ChatGPT row must teach the canonical connector URL {canonical}")
        assert f"<code>{canonical}/</code>" not in steps, (
            "the ChatGPT row must not teach the slashed form (CANONICAL_MCP_URL)")
        # The VALUE above is the substance; the copy no longer explains the slash
        # (owner ruling, #9660 — the server rstrips it, so it is not a failure a
        # reader needs to avoid). No assertion on that wording: it is copy style,
        # and the owner reviews copy directly.
        # The carrier LINKS the SERVED instructions document (the reach claim is
        # that a user can open it), and it is the same constant the dashboard
        # serves — a second hard-coded copy on a static page is only safe while
        # a test ties it to the constant.
        assert f'href="{instructions_url}"' in steps, (
            "the ChatGPT row must LINK the onboarding instructions URL — it is "
            "the live carrier #4836 requires, not a bare mention")
        # Handing a cloud agent the whole document must not become a key leak:
        # the document's teach-human recipes carry `Bearer <TORTOISE_API_KEY>`,
        # so the row has to say the key-less path is the only one. The warning
        # names BOTH minted prefixes (`tk_` for scoped/graph-bound keys,
        # `tt_` for legacy shapes — hosted_api.py), so both are asserted here.
        assert "left the OAuth path" in row, (
            "the ChatGPT row must warn that no key/Authorization header belongs "
            "in ChatGPT — the OAuth path is the only one")
        assert "<code>tk_…</code>" in row, (
            "the warning must name the tk_ prefix too — a scoped key is tk_, and "
            "a warning that only says tt_ reads as not applying to its holder")
        assert "<code>tt_…</code>" in row, (
            "the warning must name the tt_ prefix too — dropping it would leave "
            "legacy-shaped keys looking acceptable to ChatGPT")

    def test_9660_chatgpt_row_names_the_graph_and_the_configured_packs(self):
        """#9660: the journey's two invisible surfaces.

        A ChatGPT user was told how to authorize and how to paste the
        instructions document, but nothing said **which graph** the connector
        writes to or **which packs** that graph recognises — so the two facts a
        first write depends on had to be discovered from odd extraction results.

        Both steps are PRODUCT facts, not UI labels, so they are assertable here
        without the live-browser walk #9660 requires for the Developer-Mode
        path: `docs.html#chatgpt` is the live carrier (#4836).

        Each claim the row makes is cross-checked against the artifact that OWNS
        it, so a reworded owner fails THIS test instead of leaving the row
        asserting a contract that no longer exists: `TOOL_REGISTRY` /
        `_OVERVIEW_SECTIONS` for the tools, `mcp_auth._get_org_sdk` (driven, both
        shapes) plus the `#236` HTTP override for the graph,
        `pack_state.DEFAULT_STARTER_PACKS` / its resolution arms, and
        `packs.js::PACK_CONSEQUENCE` with the Settings section id for the packs.
        Every owner is READ here (or called); none is re-typed.
        """
        section = self._mcp_section()
        start = section.find('<h4 id="chatgpt">')
        assert start != -1, "docs #mcp must carry a ChatGPT carrier row (#4836)"
        row = section[start:]
        for delim in ('<p class="callout">', "<h3", "<h4"):
            cut = row.find(delim, 1)
            if cut != -1:
                row = row[:cut]
        assert "<ol>" in row, (
            "the ChatGPT row must keep its ordered step list (#4836) — asserted "
            "here so a shape change names the reason instead of raising "
            "IndexError out of the slice below")
        steps = row.split("<ol>", 1)[1].split("</ol>", 1)[0]
        # #9660 owner ruling (copy): the graph and pack material is CONTEXT, not
        # a step — the user has nothing to do at connect time — so it sits below
        # the ordered list. These claims are therefore asserted against the whole
        # ROW; a card that dropped them entirely still fails here.
        row_norm = self._copy_norm(row)

        # ── which graph ────────────────────────────────────────────────────
        # The discovery tool named must be a REAL, ACTIVE, read-risk tool: a
        # connector holding a read-only grant is exactly the user this step
        # exists for (#9660's plan-dependent write-tool finding), so naming a
        # write/admin tool would prescribe a call that cannot be made.
        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY
        assert "tortoise_list_graphs" in row, (
            "the row must name the graph-discovery tool — a connector carries no "
            "graph of its own, so the user has to see what exists before writing")
        active = {t.name: t for t in TOOL_REGISTRY}
        retired = {t.name for t in RETIRED_TOOL_REGISTRY}
        assert "tortoise_list_graphs" in active, (
            "the row names tortoise_list_graphs, so it must stay in tools/list")
        assert "tortoise_list_graphs" not in retired, (
            "the row must not name a RETIRED tool (#3883 class) — a tool-grant "
            "client cannot call one")
        assert active["tortoise_list_graphs"].risk == "read", (
            "graph discovery must be a read-risk tool — write tools are the ones "
            "a plan can withhold")
        # The advertised (consolidated) form is named too, and the section it
        # teaches must be one the tool really accepts: a card that names an
        # `overview(section=…)` value the handler rejects sends the agent into a
        # validation error at the one moment it is trying to orient itself.
        assert 'section="graphs"' in row, (
            "the row must name the advertised overview(section=\"graphs\") form")
        assert "tortoise_overview" in active, (
            "the row names the advertised overview tool, so it must stay active")
        assert active["tortoise_overview"].risk == "read", (
            "overview must stay read-risk — it is the orientation call")
        # …and callable by a CONNECTOR at all: the row asks the user to have the
        # agent call these, so an http-exposure flip (the #3883 class this suite
        # already guards for other tools) must fail here rather than silently
        # hiding the tool from a tool-grant client.
        for name in ("tortoise_list_graphs", "tortoise_overview"):
            assert active[name].http_policy is True, (
                f"step 8 asks the agent to call {name}; it must stay "
                "http-exposed or a ChatGPT connector cannot make the call "
                "the row prescribes")
        from tortoise.mcp_server import _OVERVIEW_SECTIONS
        assert "graphs" in _OVERVIEW_SECTIONS, (
            "the row teaches overview(section='graphs'); the handler must accept it")
        # …and it must state the truth about per-call graph selection as the
        # surface now stands (#9340 landed it): a call that names no graph lands
        # on the DEFAULT graph, and a call can be pointed at another graph of the
        # same organization with the `graph` argument. A card that said the
        # control did not exist would send the user hunting for an absence — the
        # same defect as the stale Developer-Mode path this issue exists for.
        #
        # Both graph claims are owned by CODE, and both are exercised here:
        #   * the DESTINATION: with no graph scope on the request (the shape a
        #     session / legacy / OAuth connector credential has), `_get_org_sdk`
        #     binds the org's DEFAULT graph; only a credential that carries a
        #     graph scope is bound to another one. That is what makes "an
        #     unnamed call lands in the default graph" true of a connector
        #     rather than of callers in general — so both branches are driven,
        #     and the default branch is the one the row describes;
        #   * the ABSENCE of a per-tool SELECTOR parameter: no tool takes a
        #     graph-selecting parameter of its own (the three that do are
        #     read-only, or the admin recording flag — named explicitly below, so
        #     a new one fails). Per-call selection itself is a DISPATCH-level
        #     argument (`mcp_server._take_graph_argument` strips it,
        #     `mcp_server._resolve_graph_argument` resolves and scopes), which is
        #     why no handler signature carries one.
        from tortoise import mcp_auth
        # These ContextVars are process-wide on this thread and there is no
        # autouse fixture clearing them, so the reset is in a `finally`: a failing
        # assertion here must report ITSELF, not cascade into every later test
        # that expects the base/stdio SDK.
        try:
            mcp_auth._current_org_id.set("org_9660_probe")
            mcp_auth._current_graph_id.set(None)
            mcp_auth._current_graph_namespace.set(None)
            default_sdk = mcp_auth._get_org_sdk()
            assert default_sdk._graph_name is None, (
                "a credential with NO graph scope must open the org's default "
                f"graph (no explicit graph name), got {default_sdk._graph_name!r} "
                "— the row's 'every write lands in the default graph' (#9340)")
            assert default_sdk._namespace == "org_9660_probe", (
                "the default-graph SDK must be scoped to the organization the "
                f"credential names, got {default_sdk._namespace!r}")
            mcp_auth._current_graph_id.set("g-9660")
            mcp_auth._current_graph_namespace.set("org_9660_probe__other")
            assert mcp_auth._get_org_sdk()._graph_name == "org_9660_probe__other", (
                "a graph-BOUND credential must open ITS graph — and so must a "
                "graph the CALL selected (#9340), which pivots on exactly this "
                "ContextVar pair; a connector's own credential carries none")
        finally:
            for var in (mcp_auth._current_org_id, mcp_auth._current_graph_id,
                        mcp_auth._current_graph_namespace):
                var.set(None)
        # …and the per-tool SELECTOR is absent from the SURFACE. Enumerated from
        # the live registry through BOTH dispatch shapes (an `sdk_method`, or a
        # handler in mcp_server — the shape a new graph-aware tool is most likely
        # to use), so a tool that grows a graph parameter of its own fails here
        # the day it lands. Per-call selection is a DISPATCH-LEVEL argument
        # (#9340), not a handler parameter, which is what keeps this set exact.
        import inspect

        from tortoise import mcp_server as _mcp
        from tortoise.sdk import TortoiseSDK

        # A PREDICATE over the parameter name, not four literals: the change the
        # row forbids is a tool that can be pointed at a graph, and the next such
        # parameter may be spelled `target_graph` / `ns` / `tenant` rather than
        # `graph`. `graph_ranker` is the one benign name in the tree (a scoring
        # weight on two read tools), so it is excluded BY NAME, not by pattern.
        _BENIGN = {"graph_ranker"}

        def _is_graph_selector(name):
            return (name not in _BENIGN
                    and ("graph" in name.lower()
                         or name.lower() in {"ns", "namespace", "target", "tenant"}))

        def _graph_params(entry):
            found = set()
            for fn in (getattr(TortoiseSDK, entry.sdk_method, None)
                       if entry.sdk_method else None,
                       entry.handler_override,
                       getattr(_mcp, entry.name, None)):
                if fn is None:
                    continue
                try:
                    found |= {p for p in inspect.signature(fn).parameters
                              if _is_graph_selector(p)}
                except (TypeError, ValueError):
                    continue
            return found

        graph_aware = {name: _graph_params(t) for name, t in active.items()}
        graph_aware = {n: p for n, p in graph_aware.items() if p}
        # The exceptions are named, with their risk, so each one is a DECISION
        # rather than a hole: two READ tools render another graph's contents, and
        # one ADMIN tool toggles that graph's recording flag. None of them moves
        # where a connector's writes land. The set is exact — a fourth entry
        # fails, so a new selector is argued here rather than slipped in.
        assert {n: active[n].risk for n in graph_aware} == {
            "tortoise_entity_profile": "read",
            "tortoise_traverse": "read",
            "tortoise_graph_set_recording": "admin",
        }, (
            "exactly these three tools take a graph SELECTOR parameter and none "
            "is a write — a WRITE tool gaining one would move a connector's "
            "writes (#9340), so it must be argued here, not slipped in (the "
            "per-call `graph` argument is a dispatch seam and adds no "
            f"parameter): { {n: (active[n].risk, sorted(p)) for n, p in graph_aware.items()} }")
        mcp_src = self.MCP_SERVER.read_text(encoding="utf-8")
        # Matched as a PATTERN, not a byte string: a re-wrapped or re-quoted line
        # is a no-op, and a second guard that reds on a no-op teaches the next
        # lane to delete it (the #4365 shape-net finding this file already
        # records).
        #
        # #236 is still owned by CODE: on HTTP the caller-supplied `graph_name`
        # is overwritten with the SERVER's graph — the workspace default, or the
        # graph this call selected (#9340). The derivation is pinned too, because
        # a name read from the request would be the cross-tenant
        # `db.select_graph()` the guard exists to prevent.
        org_override = re.compile(
            r"graph_name\s*=\s*_dispatch_graph_name\(\)")
        assert len(org_override.findall(mcp_src)) >= 2, (
            "#236: HTTP mode must keep overwriting a caller-supplied graph_name "
            "with the server-resolved graph — that is what makes a selected "
            "graph safe to honour")
        assert "_mcp_auth._current_graph_namespace.get()" in mcp_src, (
            "the derived graph name must come from the SERVER's own resolution "
            "(#9340), never from the request")
        assert "default graph" in row_norm, (
            "the row must state that an UNNAMED call lands on the organization's "
            "DEFAULT graph — that is the connector's actual behaviour (#9340)")
        assert "graph</code> argument" in row, (
            "the row must state that a call CAN be pointed at another graph of "
            "the same organization, now that #9340 landed it")
        # …but not unconditionally on THIS client. The affordance above is "ask
        # the agent to pass `graph`", and on ChatGPT that promise held only for
        # reads: a write carrying `graph` came back refused by the client's own
        # safety check, before the request reached us, while the same write
        # without the argument succeeded (#10084, measured 2026-10-26). A row
        # that offers the argument and says nothing about the wall sends the
        # user into a refusal they cannot diagnose — and the reader of this card
        # has no way to tell it apart from our own validation error.
        #
        # The fragment is deliberately NOT "safety check": the deletions
        # paragraph in this same row already carries that phrase, so an
        # assertion on it would pass with this caveat deleted (vacuity).
        assert "naming a graph on a write does not work" in row_norm, (
            "the row must carry the #10084 caveat — on this client the `graph` "
            "argument is refused on a WRITE — otherwise the row promises an "
            "affordance the client blocks")
        # The caveat without the remedy sends a user to a wall with no way past
        # it, and "connect a keyed harness" alone is vacuous here (the row
        # already says it twice for deletions and for the read-only-grant case),
        # so the clause asserted is the one that names WHICH graph to write from.
        assert "to write to a graph other than the default" in row_norm, (
            "the #10084 caveat must name the keyed-harness fallback for writing "
            "to a non-default graph — a refusal with no route past it is a dead "
            "end, not a documented limit")

        # ── which packs ────────────────────────────────────────────────────
        # The hazard is INVISIBLE, not absent — but WHICH default applies depends
        # on the graph, and the row must not average the two. Measured on this
        # tree (embedded graph, `configured: false` throughout):
        #   * NO pack records at all (a graph created and never configured, an
        #     imported or self-hosted graph) → the catalog union, namespaces=None
        #     (#2714 indicator 3 — the owner's decided behaviour, restated in the
        #     /v1/graphs #9667 docstring);
        #   * the starter records every hosted organization is seeded with at
        #     provisioning (`ensure_tenant_packs` → DEFAULT_STARTER_PACKS) → the
        #     FIVE starters ∪ data, and the catalog's non-starter pack (`venture`)
        #     is excluded — while `configured` is STILL false.
        # So "never been configured, therefore the full catalog applies" is false
        # for the very graph the row's preceding step sends the user's writes to,
        # and it would have told that user their default graph recognises every
        # domain — the odd-extraction surprise #9660 exists to remove. Nor may the
        # row tell the user the set is unknowable in the chat: `tortoise_packs_list`
        # reads exactly it, on the HTTP surface. The row must therefore name the
        # starters as the default-graph set, name that read tool, scope the
        # catalog fallback to "no pack records" (including the read-path seeding
        # that narrows such a graph the first time anyone looks), and carry its
        # disable clause from `packs.js::PACK_CONSEQUENCE` (the module that owns
        # that sentence).
        packs_src = self.PACKS_JS.read_text(encoding="utf-8")
        disable = self._copy_norm(
            self._js_object_string(packs_src, "PACK_CONSEQUENCE", "disable"))
        # The Settings label is a real dashboard heading, not a name invented by
        # this test — and the row may not send the user to a section that moved.
        main_src = self.MAIN_JSX.read_text(encoding="utf-8")
        # The heading is bound to `PACK_SETTINGS_SECTION_ID` (packs.js exports it;
        # packs.test.js pins its value), so matching the CONSTANT keeps this guard
        # alive through a JSX prop reorder while still failing if the section is
        # renamed or removed.
        assert re.search(r"export const PACK_SETTINGS_SECTION_ID\s*=\s*['\"]",
                         packs_src), (
            "packs.js must export PACK_SETTINGS_SECTION_ID — the row's "
            "'Settings → Configured packs' destination is addressed by it")
        # ONE pattern that is both attribute-ORDER tolerant (a JSX prop reorder is
        # a no-op) and label-pinning: the row tells the user to look for
        # "Configured packs", so the visible heading text must survive too, not
        # just its id binding.
        assert re.search(
            r"<h3\b[^>]*\bid=\{PACK_SETTINGS_SECTION_ID\}[^>]*>Configured packs</h3>",
            main_src), (
            "the row sends the user to Settings → 'Configured packs'; that "
            "heading must stay bound to PACK_SETTINGS_SECTION_ID and keep its "
            "label in main.jsx")
        assert "configured packs" in row_norm, (
            "the row must name the Settings surface that configures packs")
        # The default graph's set is OWNED by `pack_state.DEFAULT_STARTER_PACKS`
        # plus the provisioning seed: read the constant (never re-type it), and
        # require each namespace in the row, so renaming or dropping a starter
        # fails here instead of leaving the card naming a pack that is not
        # installed.
        from tortoise.pack_state import DEFAULT_STARTER_PACKS
        assert len(DEFAULT_STARTER_PACKS) == 5, (
            "the row names the five starter packs; DEFAULT_STARTER_PACKS is the "
            f"owner of that set: {DEFAULT_STARTER_PACKS}")
        for ns in DEFAULT_STARTER_PACKS:
            assert f"<code>{ns}</code>" in row, (
                f"the row must name the starter pack {ns!r}: a new organization's "
                "default graph holds pack_state.DEFAULT_STARTER_PACKS (#318)")
        # …and the seeding is what keeps that true. Each provisioning path is
        # named, because the HOSTED deployment provisions through the Supabase
        # control plane: if `hosted_api` stops seeding, or the control-plane hook
        # in `supabase_control` stops seeding, the row's sentence is false for
        # every new organization — while a test that read only one file stays
        # green.
        for name, needle in (
            ("hosted_api.py", "ensure_tenant_packs(_make_sdk"),
            ("supabase_control.py", "ensure_tenant_packs(_ha._make_sdk"),
        ):
            src = (REPO_ROOT / "tortoise" / name).read_text(encoding="utf-8")
            assert src.count(needle) >= 1, (
                f"the row says a new organization's default graph HOLDS the "
                f"starter packs; {name} must keep seeding them (#318) or the "
                "sentence is false")
        assert (REPO_ROOT / "tortoise" / "hosted_api.py").read_text(
            encoding="utf-8").count("ensure_tenant_packs(_make_sdk") >= 2, (
            "both hosted_api provisioning paths (tenant_provision, register) "
            "seed the starter set (#318)")
        # The fallback is real — but only for a graph with NO pack records, and
        # reading the set on such a graph SEEDS the starters (the self-heal in
        # `get_tenant_packs`), so a row that said "no records, therefore the whole
        # catalog" without that caveat would describe a state its own instruction
        # ends. Both halves are read from `pack_state` so the row cannot outlive
        # them.
        pack_src = (REPO_ROOT / "tortoise" / "pack_state.py").read_text(encoding="utf-8")
        assert re.search(
            r"ensure_tenant_packs\(\s*sdk\b[^)]*graph_name\s*=\s*graph_name", pack_src), (
            "the row says reading the set seeds the five starters on a "
            "record-less graph; that is `get_tenant_packs`'s self-heal")
        assert re.search(r"_read_selection\(\s*g\s*\)\s+is\s+None", pack_src), (
            "the self-heal must stay suppressed for a DELIBERATELY configured "
            "graph (the `:PackSelection` marker)")
        assert "entire catalog" in row_norm, (
            "the row must keep the catalog-union fallback (#2714 indicator 3), "
            "scoped to its real predicate")
        # The predicate is BOTH halves: `POST /v1/graphs` with `packs: []` creates
        # a graph with no records AND a revision marker, which resolves to the
        # EMPTY set (#9667) — the opposite of the union. A row that said only "no
        # records" would describe that shipped state wrongly.
        assert "no pack records" in row_norm, (
            "the catalog fallback applies to a graph with NO :PackInstall "
            "records; the row must state that predicate rather than "
            "'unconfigured'")
        assert "no pack set of its own" in row_norm, (
            "the fallback's predicate is `not rows and selection is None` — a "
            "configured-EMPTY graph has no records and resolves to nothing "
            "(#9667), so the row may not attribute the union to 'no records' "
            "alone")
        union_arms = [ln for ln in pack_src.splitlines()
                      if ln.strip().startswith("if ")
                      and "not rows" in ln
                      and re.search(r"selection\s+is\s+None", ln)]
        assert union_arms, (
            "pack_state's union arm must keep requiring BOTH no records and no "
            "selection marker — that conjunction is the row's predicate")
        assert "seeds the five starters" in row_norm, (
            "the row points the user at Configured packs, and READING a "
            "record-less graph's set seeds the starters into it "
            "(pack_state.get_tenant_packs self-heal) — a silent narrowing the "
            "row must not leave out")
        assert "full pack catalog" not in row_norm, (
            "the row must not say an unconfigured graph uses the full pack "
            "catalog: a hosted default graph is seeded with the five starters, "
            "so `configured: false` and 'the whole catalog' do not co-occur "
            "there (measured: catalog − resolved = {'venture'})")
        # Nor may it call the set unknowable in the chat: the read tool exists,
        # on the HTTP surface, and the row names it — so it must stay active,
        # un-retired and http-exposed, or the card prescribes a call that cannot
        # be made (the #3883 class this suite already guards for other tools).
        assert "tortoise_packs_list" in steps, (
            "the row must name the pack read tool instead of telling the user "
            "the set is invisible from ChatGPT")
        assert "tortoise_packs_list" in active, (
            "the row names tortoise_packs_list, so it must stay in tools/list")
        assert "tortoise_packs_list" not in retired
        assert active["tortoise_packs_list"].http_policy is True, (
            "a connector reaches tools over HTTP; a packs read that is not "
            "http-exposed cannot answer the card's instruction")
        assert "does not choose packs" in row_norm, (
            "the row must state that creating a graph does not choose its packs "
            "— scoped to the dashboard form, which posts {org_id, name}; the "
            "API can set them at birth (#9667)")
        # …and the claim's OWNER is the create call, so read it: the dashboard's
        # POST /v1/graphs body is what makes "does not choose packs" true. A packs
        # picker added there would make the row false with the suite green, which
        # is the defect this very commit removes from the docstring one line up.
        create_body = re.search(
            r"\$\{API_BASE\}/v1/graphs`,\s*\{.*?body:\s*JSON\.stringify\((\{[^}]*\})\)",
            main_src, re.S)
        assert create_body, (
            "the dashboard's create-graph POST must stay findable — it is the "
            "owner of the row's 'the create form does not choose packs'")
        assert "packs" not in create_body.group(1), (
            "the dashboard create form now sends packs, so the row's "
            f"'does not choose packs' is false: {create_body.group(1).strip()}")
        for field in ("org_id", "name"):
            assert field in create_body.group(1), (
                f"the create body must still carry {field}: "
                f"{create_body.group(1).strip()}")
        # …and the disable consequence must keep the product's QUALIFIER. The
        # resolution contract re-admits a namespace WHOLE once the graph holds any
        # data in it (pack_state.resolved_pack_set = active records ∪ the graph's
        # namespaces; docs/EXPANSION_PACKS.md), so an unconditional "turning a pack
        # off stops new classifications" is false for exactly the packs a user is
        # most likely to have data in — the opposite of what it tells them.
        # `packs.js::PACK_CONSEQUENCE.disable` is the owner of that sentence and
        # states the namespace-granular qualifier; the row must not drop it.
        # The two fragments below are read OUT OF the module and then required in
        # the row — a reword in either place fails, which a plain substring check
        # over `docs.html` alone could not do.
        for fragment in ("packs this graph holds no memories from yet",
                         "kinds already here stay searchable and writable"):
            assert fragment in disable, (
                f"packs.js PACK_CONSEQUENCE.disable no longer carries {fragment!r} "
                "— the namespace-granular qualifier is the contract "
                "(pack_state.resolved_pack_set ∪ the graph's namespaces,"
                " docs/EXPANSION_PACKS.md), so the module and the row must move "
                "together")
            assert fragment in row_norm, (
                f"the pack-disable clause must keep the namespace qualifier "
                f"{fragment!r} — a pack the graph already holds data in stays "
                "classifiable (packs.js::PACK_CONSEQUENCE.disable)")
        # The unconditional form must be absent from BOTH sides: it is the shape
        # the resolution contract falsifies, so the module must not state it and
        # the page must not quote it.
        #   (FIXED in #9691: `packCountLine` and `PACK_CONSEQUENCE.unconfigured`
        #   now hang the catalog sentence on `packs.js::usesCatalogUnion` —
        #   `configured: false` AND zero records — so a starter-seeded default
        #   graph gets the "packs it already holds" line instead. The remaining
        #   residual is the record-count DERIVATION: it cannot see a tombstone
        #   the catalog arm offers as `available`. That shape is REACHABLE after
        #   a rebuild — `:PackSelection` is not a projection config class, so the
        #   tombstoned `:PackInstall` survives and the marker does not — and the
        #   durable fix is to enrol the marker, not this copy.)
        assert "only stops new classifications" not in disable, (
            "PACK_CONSEQUENCE.disable must not state the consequence "
            "unconditionally — the resolution contract re-admits a namespace "
            "that already has data")
        assert "only stops new classifications" not in row_norm, (
            "the row must not state the disable consequence unconditionally — "
            "the resolution contract re-admits a namespace that already has data")

        # ── the journey is ORDERED ─────────────────────────────────────────
        # The row is a procedure a visitor follows top to bottom (#9660), so the
        # one instruction that needs a live conversation — ask the agent to call
        # the graph-discovery tool — must come AFTER the chat is opened and the
        # workflows reached it (installed as a ChatGPT skill, or pasted into the
        # chat): before that there is no agent to ask.
        # The ordering is about the CONVERSATION, not about whether a graph
        # exists — `hosted_api.agent_signup` creates the `kind='default'` graph
        # at org creation, so a graph is listable from signup. What the user
        # cannot know before the write is WHICH graph the write landed in, and
        # reading the graph back is what answers it.
        handoff = "Install the Tortoise skills"
        assert handoff in steps, (
            "the row must keep the workflows hand-off step (#9660)")
        for call in ('section="graphs"', "tortoise_packs_list"):
            assert row.index(handoff) < row.index(call), (
                f"the {call} call must be asked of the agent AFTER the chat "
                "exists and the document is pasted — before that the instruction "
                "is not actionable in place, which defeats the point of an "
                "ordered journey")

    def test_4836_document_section2_names_the_live_chatgpt_carrier(self):
        """#4836 acceptance: `SKILL.md` §2's 7th-harness note must name the surface
        that actually carries the instructions instead of leaving that to a
        test-consumed constant.

        `tests/test_onboarding_variants.py::test_4365_served_document_sends_
        chatgpt_to_a_path_that_exists` owns the retired-tab pins for the whole
        served document; this test re-pins the same literals SCOPED TO §2 (a
        tighter surface — §2 could keep the retired tab while the rest of the
        document is clean) and owns the carrier name.

        The note↔chooser agreement is NOT re-checked here. The chooser half is
        derived and asserted against the real vocabulary by
        `website/apps/dashboard/src/onboardingEmptyStateKeyNote.test.js`
        (`assert.ok(!chooserLeafIds().includes('chatgpt'))`), and a text scan from
        Python cannot decide it without a shape heuristic (#4880).
        """
        skill = self.SKILL.read_text(encoding="utf-8")
        marker = "> **#1701 —"
        assert marker in skill, "SKILL.md lost the §2 7th-harness note"
        terminator = "\n\n## 3. Install + connect"
        assert terminator in skill, (
            "SKILL.md lost §2's terminator — the slice would run to EOF and the "
            "assertions below could be satisfied from any later section")
        note = skill.split(marker, 1)[1].split(terminator, 1)[0]
        carrier = "https://tortoise.premiselabs.co/docs#chatgpt"
        assert carrier in note, (
            "§2's note must name the live ChatGPT carrier (the public docs page)")
        # Reachability: the note SENDS a user to that URL, so pin that the path
        # still resolves to the docs page and that the fragment it names exists
        # there. Either half being renamed leaves the note pointing at nothing
        # (#4836) — this is the check the URL literal alone cannot make.
        docs = self.DOCS.read_text(encoding="utf-8")
        assert f'id="{carrier.rpartition("#")[2]}"' in docs, (
            "the note's carrier fragment must exist on the docs page")
        redirects = (REPO_ROOT / "website" / "_redirects").read_text(encoding="utf-8")
        assert re.search(r"^/docs\.html\s+/docs(\s+\d+)?\s*$", redirects, re.M), (
            "the carrier URL's path must keep resolving to docs.html")
        assert "chatgpt.com/plugins" in note, (
            "§2's note must keep naming the ChatGPT connector surface")
        assert "ChatGPT tab" not in note, (
            "§2's note must not name the retired dashboard ChatGPT tab")
        # #9660 OWNER RULING (2026-10-23): the chooser carries a ChatGPT surface
        # now, so §2 must say so instead of the (now false) exclusion.
        assert "no ChatGPT surface in the dashboard chooser" not in note, (
            "§2's note still claims ChatGPT is absent from the dashboard chooser — "
            "the #9660 owner ruling added it")
        assert "dashboard's connect step (the ChatGPT" in note, (
            "§2's note must name the dashboard ChatGPT surface a user now takes")
        # The document ITSELF is what a ChatGPT agent receives, so the key
        # prohibition has to travel with it — not only on the human-facing page
        # (docs.html), whose warning the pasted document never carries.
        assert "Never a request header" in note, (
            "§2's note must forbid a key/Authorization header — the agent that "
            "receives this document has to be told, not just the human")

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
        # The POSITIVE half, scoped to the skill (the docs' positive half is
        # `test_docs_hosted_json_blocks_use_env_indirection_and_canonical_type`,
        # which PARSES each block). Without this line nothing asserts that the
        # served document still teaches the canonical value at all.
        assert '"type": "http"' in skill, (
            "SKILL.md must state the canonical `\"type\": \"http\"` value — a "
            "Claude Code entry with no type is read as stdio and skipped")

    def test_skill_cursor_and_pi_rows_omit_type(self):
        skill = self.SKILL.read_text(encoding="utf-8")
        for start_h, end_h in (("### Cursor (self-install)", "### Codex CLI"),
                               ("### Pi (self-install)", "### Claude Desktop")):
            assert start_h in skill, f"skill row heading missing: {start_h}"
            assert end_h in skill, f"skill row heading missing: {end_h}"
            row = skill.split(start_h, 1)[1].split(end_h, 1)[0]
            assert '"type"' not in row, f"{start_h}: remote config must omit `type`"

    # `docs` + `skill` agreement on the canonical type is owned by
    # `test_docs_mcp_section_has_no_streamable_type` + the parsing check above
    # (docs) and `test_skill_does_not_teach_streamable_type` (skill, both
    # directions); a third copy that re-checked both surfaces against the same
    # string added no coverage (#9660 review: it is one failure counted twice).


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
        # transport from url-vs-command. Pinned because docs/quickstart-cloud.md
        # teaches the same object and could drift silently; since #4674 it shows
        # the same `http` value and is pinned by
        # `TestDocsPageAndSkillConfig::test_quickstart_cloud_teaches_the_http_shape_indirectly`.
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


# The ChatGPT card's SELECTOR: without it the connect step has no ChatGPT branch
# and a ChatGPT user gets no card at all, so it is pinned (the journey test below).
_CHATGPT_SELECTOR = "wizardConnectHarness === 'chatgpt' ? ("

# The card's END: the key-less Claude branch that terminates the same ternary chain.
_KEYLESS_BRANCH = "wizardKeyless ? ("

# The card's three block titles. Each appears EXACTLY ONCE in `main.jsx` and only
# in the ChatGPT arm, so they are what this test slices on: the boundaries are
# content the card itself renders, not punctuation in the source around it.
#
# This replaced two earlier guards that tried to find the arm's END by reading the
# surrounding JavaScript — first by the shape of the next ternary's condition, then
# by the `) :` token with comments blanked. Both were the same mistake: a regex is
# not a lexer, so each round found ordinary source it mis-read (a `) : \`x\` ?`, a
# `//` inside copy opening a phantom block comment that blanked thousands of real
# characters, a protocol-relative `href="//cdn"` hiding the terminator, a nested
# ternary inside the card). The lesson is in the review record; the idiom here is
# the cheap one: cut on strings the card renders.
_CHATGPT_BLOCKS = ("Add the Tortoise connector", "Install the Tortoise skills",
                   "Give it its first job")

# Any harness selector in the connect ternary. `_chatgpt_block` requires exactly ONE
# of these between the card's own selector and the end of each block: a unique title
# proves where a block STARTS, never that it belongs to this arm, so a sibling arm
# inserted between two titles would otherwise be swallowed by the slice — and its
# copy would satisfy the pins for a card that lost them (#9895 review). It is a
# SECOND net, overlapping `_ARM_BOUNDARY` but not identical to it: it sees a sibling
# arm written INLINE whose condition is a harness selector, and it sees one whose
# branch is parenthesised (`) : (`, no `?`).
_ARM_SELECTOR = "wizardConnectHarness ==="

# An arm-boundary CONTINUATION token, read BY POSITION and never by the shape of a
# condition. Two shapes count:
#   * a closing bracket that OPENS A LINE, followed by the `:` of a ternary chain
#     (`) : wizardKeyless ? (`, `) : (` for a parenthesised nested ternary);
#   * a MID-LINE one that continues a ternary on the same line (`>a</WizardBlock>
#     ) : wizardDesktop ? (` written on one line) — the `?` after the colon is what
#     distinguishes it from prose.
# What must NOT count is the raw two characters: they occur mid-sentence in rendered
# copy — "a paid plan (Plus or higher): ready" — where counting them reds a correct
# card with a "sibling arm" diagnosis, i.e. it teaches the next editor to delete the
# guard (reproduced in review, #9895).
# LIMIT, stated: a mid-line boundary whose branch is PARENTHESISED and carries no
# `?` (an inline `) : (` nested ternary) is not counted here — a mid-line `): (` is
# a real prose shape ("the plan floor (Plus or higher): (see pricing)"), and the
# card uses the inline `{cond ? a : b}` form nowhere. `_ARM_SELECTOR` catches that
# boundary whenever its condition names a harness; the LIMIT note in the shape test
# states the residual gap.
_ARM_BOUNDARY = re.compile(
    r"^[ \t]*[)}][ \t]*:"                  # a continuation token that opens a line
    r"|[)}][ \t]*:[^\n]*\?[ \t]*[(<]",     # a mid-line one continuing a ternary
    re.MULTILINE)


def _arm_boundaries(text: str, start: int, end: int) -> list[str]:
    """The continuation tokens inside `text[start:end]`, measured to end-of-line.

    The LAST block ends at the terminator's own `? (`, i.e. its line continues PAST
    `end`, so the window runs to the end of the line holding `end` and no further:
    the token that closes the slice is measured, and nothing after it is.
    """
    eol = text.find("\n", end)
    window = text[start:] if eol == -1 else text[start:eol]
    return _ARM_BOUNDARY.findall(window)


_ORG_CHOOSER = "or asks you to choose when your account can use more than one"


def _assert_org_pick_precedes_authorize(block: str) -> None:
    """The consent page disables Authorize until the Organization chooser has a value.

    `tortoise/oauth.py` hides the named-org line and leaves Authorize DISABLED on the
    `len(orgs) > 1` memberships branch (`authBtn().disabled = true`); the `#org-select`
    onchange is the only thing that re-arms it. So "click Authorize, then pick" names an
    inert control. Extracted from the journey test so the law itself can be proved on
    synthetic copy — an inline assertion is only ever exercised by the real file, which
    cannot show it would red.
    """
    # Flattened first: JSX and markdown wrap mid-sentence, so matching the raw
    # source would red a correct card whose sentence happens to break across a
    # line (#9895 review).
    flat = " ".join(block.split())
    at_org = flat.find(_ORG_CHOOSER)
    assert at_org != -1, (
        "the card no longer tells the reader to pick the Organization when the "
        "consent page offers a chooser — a multi-Organization account sees no "
        "named organization, and Authorize stays disabled")
    at_authorize = flat.find("Authorize</strong>")
    assert at_authorize != -1, "the card no longer names the Authorize button"
    assert at_org < at_authorize, (
        "the card picks the Organization AFTER Authorize — on a multi-Organization "
        "account that button is disabled until the chooser has a value")


def _assert_recipe_row_order(label: str, block: str) -> None:
    """One recipe block: names the chooser, names both acts, picks BEFORE Authorize.

    The chooser is a property of the PAGE (`tortoise/oauth.py` calls
    `authBtn().disabled = true` on the several-Organization branch), so every carrier that
    drives the consent page must state it, and must state the pick before the button
    it enables. Compared case-insensitively so the pin tracks the ORDER — the spelling
    is guarded separately by
    `test_9660_the_recipe_carriers_use_one_casing_for_the_tenant_noun`.
    """
    # Squash markdown/JSX wrapping so a phrase split across source lines is still the
    # phrase a reader sees (the served note wraps mid-sentence).
    flat = " ".join(block.replace("> ", " ").split())
    assert "asks you to choose" in flat, (
        f"the {label} no longer carries the chooser wording this pin looks for "
        "('asks you to choose'): the copy must tell a user that the page asks them "
        "to pick when their account can use more than one Organization "
        "(tortoise/oauth.py). If the sentence was reworded, update BOTH the copy and "
        "this pin — do not drop the fact.")
    org = flat.lower().find("organization")
    authorize = flat.lower().find("authorize")
    assert -1 not in (org, authorize), (
        f"the {label} no longer names both acts — Organization={org}, "
        f"Authorize={authorize}")
    assert org < authorize, (
        f"the {label} puts the Organization choice AFTER Authorize; on a "
        "multi-Organization account Authorize is disabled until a choice is "
        "made, so the copy names an inert control")


def _list_recipe_block(text: str, anchor: str) -> str:
    """The list ROW that carries `anchor`, through the end of its `<ol>`.

    ROW-bounded, on both edges, because a whole-list slice makes the order pins
    meaningless: measured from `<ol`, the block holds every earlier row too, so an
    unrelated `Organization` mention in the sign-in row satisfies "the pick comes
    first" while the row that matters is inverted (a false green), and an unrelated
    `Authorize` mention there reds correct copy (a false red). Both are ordinary copy
    edits and both were reproduced in review (#9895). The `<ol` and `</ol>` edges are
    still asserted — bounding from the anchor alone would let a row hoisted out of
    the list widen the block into the next list in the file.

    Every failure raises `AssertionError` naming what moved.
    """
    at = text.find(anchor)
    assert at != -1, (
        f"the Claude key-less recipe moved: {anchor!r} no longer appears, so "
        "the row it belongs to cannot be bounded")
    start = text.rfind("<ol", 0, at)
    assert start != -1, f"{anchor!r} is no longer inside an ordered list"
    row = text.rfind("<li", 0, at)
    assert row != -1 and start < row, (
        f"{anchor!r} is no longer inside a list item of the ordered list that holds "
        "it — the row that carries the pick cannot be bounded, so the order pins "
        "would be judged over the whole list")
    end = text.find("</ol>", start)
    assert end != -1, f"the ordered list holding {anchor!r} lost its closing tag"
    assert row < end, (
        f"the list item holding {anchor!r} closes after its own list — the markup "
        "moved out of the recipe")
    block = text[row:end]
    assert anchor in block, (
        f"{anchor!r} is no longer inside the nearest row of its ordered list — the "
        "copy moved, so this block would be some earlier recipe's")
    return block


def _chatgpt_block(main: str, index: int) -> str:
    """The text of the card's `index`-th block, sliced on the block titles.

    The boundaries are content the card itself renders — each title appears exactly
    once in `main.jsx` and only here — AND every slice is required to hold exactly
    one harness selector, the card's own. The second requirement is not implied by
    the first: a unique title says where a block STARTS, never which arm it belongs
    to, so a sibling arm inserted between two titles would be swallowed by the slice
    (and its copy would satisfy the pins for a card that lost them) — the false
    green both earlier versions of this guard kept reintroducing (#9895 review).

    Two further requirements bound a slice, because a unique title only says where a
    block STARTS: each slice must hold exactly one `_ARM_SELECTOR` (the card's own),
    and no `_ARM_BOUNDARY` continuation token (line-leading, or mid-line continuing a
    ternary) except the last block's own terminator. The token scan for block 1 starts
    at the card's own selector, so the plan note and caption — card content that
    belongs to no slice — are covered too. Both requirements are stated as assertions
    with their own message, so a boundary that moves is reported by name rather than
    silently widening a slice.

    The LAST block ends at the card's own terminator (`_KEYLESS_BRANCH`), which must
    start its own clause — a quoted copy of it, mid-line, would truncate the slice.

    Every failure raises `AssertionError` naming what moved, never a bare
    `ValueError` from `str.index`.
    """
    arm_at = main.find(_CHATGPT_SELECTOR)
    assert arm_at != -1, (
        f"the chatgpt card no longer opens with {_CHATGPT_SELECTOR!r} — the arm was "
        "renamed or deleted, so the slices below would be judging another arm's copy")
    positions = []
    for title in _CHATGPT_BLOCKS:
        seen = main.count(title)
        assert seen == 1, (
            f"the chatgpt card's block title {title!r} appears {seen} times in "
            "main.jsx; this test slices on it, so it must stay unique to that card")
        positions.append(main.index(title))
    assert positions == sorted(positions), (
        "the chatgpt card's blocks are out of order in main.jsx: the journey is "
        f"connect → install → first job, but the titles render as {positions}")

    start_pos = positions[index]
    if index + 1 < len(_CHATGPT_BLOCKS):
        end_pos = positions[index + 1]
    else:
        # The last block runs to the card's terminator.
        end_pos = main.find(_KEYLESS_BRANCH, start_pos)
        assert end_pos != -1, (
            f"the chatgpt card is no longer terminated by {_KEYLESS_BRANCH!r}: the "
            "card is the last arm in the connect step, or the branch was renamed. "
            "The last block cannot be bounded — update this guard with the shape "
            "that is there.")
        line_start = main.rfind("\n", start_pos, end_pos) + 1
        assert main[line_start:end_pos].strip() in (") :", "):"), (
            "the key-less anchor does not start its own clause, so it is probably "
            f"being QUOTED in the card's copy rather than ending the arm "
            f"({main[line_start:end_pos]!r}). Re-pin the bound: a truncated slice "
            "can pass the pins vacuously.")

    # A block is CARD COPY, so a line-leading continuation token inside it means an
    # arm boundary sits there — a sibling arm spelled some way `_ARM_SELECTOR` does
    # not count (`) : wizardDesktop ? (`, `) : wizardKeyless && extra ? (`), or a
    # nested ternary WITH A PARENTHESISED BRANCH (the inline `{cond ? a : b}` form
    # carries no `) :`, and is not detected — see the LIMIT note in the shape test).
    # Expected line-leading tokens in this slice: ZERO for a block ending at the next
    # title, and exactly ONE for the last block — its own terminator's clause opener,
    # which the adjacent-line check above pins to that terminator. Any extra one is an
    # arm boundary whose copy would be pinned as if it were the card's.
    expected = 0 if index + 1 < len(_CHATGPT_BLOCKS) else 1
    # Block 1 is scanned from the card's own selector, not from its title: the plan
    # note and caption between them belong to the card but to no block SLICE, so a
    # boundary wedged there would otherwise be inside no net at all (#9895 review).
    found = _arm_boundaries(main, arm_at if index == 0 else start_pos, end_pos)
    assert len(found) == expected, (
        f"an arm boundary sits inside block {index + 1} of the chatgpt card "
        f"({len(found)} continuation tokens, expected {expected}): a sibling arm or "
        "a parenthesised nested ternary is inside the slice, so the copy pinned "
        "there could be satisfied by the wrong card — or render conditionally")

    # Exactly one harness selector — the card's own — may precede the end of this
    # block. Two means a SIBLING arm was inserted between the card's start and the
    # block's end, and the slice spans it.
    assert main.count(_ARM_SELECTOR, arm_at, end_pos) == 1, (
        "another harness arm sits between the chatgpt card's start and the end of "
        f"block {index + 1} ({main.count(_ARM_SELECTOR, arm_at, end_pos)} harness "
        "selectors before that bound): this slice spans a sibling arm, so the copy "
        "pinned inside it could be satisfied by the wrong card")
    return main[start_pos:end_pos]


def test_9660_the_chatgpt_card_slices_red_on_a_moved_or_duplicated_title():
    """The slices red on every way the card's own boundaries can move."""
    good = (
        "{wizardConnectHarness === 'chatgpt' ? (\n"
        "  <WizardBlock title=\"Add the Tortoise connector\">a</WizardBlock>\n"
        "  <WizardBlock title=\"Install the Tortoise skills\">b</WizardBlock>\n"
        "  <WizardBlock title=\"Give it its first job\">c</WizardBlock>\n"
        ") : wizardKeyless ? (\n  <p>keyless</p>\n"
    )
    first = _chatgpt_block(good, 0)
    assert "a</WizardBlock>" in first and "b</WizardBlock>" not in first
    last = _chatgpt_block(good, 2)
    assert "c</WizardBlock>" in last and "keyless" not in last

    # A duplicated title: the slice would judge the wrong block.
    with pytest.raises(AssertionError, match="appears 2 times"):
        _chatgpt_block(good + good, 0)

    # EVERY other ordering of the three titles reds with a named error (and never
    # a bare ValueError from `str.index`).
    for order in ((1, 0, 2), (2, 1, 0), (0, 2, 1), (1, 2, 0), (2, 0, 1)):
        # built from the PERMUTED order directly: an earlier version marked the
        # titles and then restored them by source index, which cancelled out and
        # left the loop a no-op that tested nothing (found in review)
        shuffled = (
            "{wizardConnectHarness === 'chatgpt' ? (\n"
            + "".join(
                f'  <WizardBlock title="{_CHATGPT_BLOCKS[slot]}">{slot}</WizardBlock>\n'
                for slot in order)
            + ") : wizardKeyless ? (\n  <p>keyless</p>\n"
        )
        with pytest.raises(AssertionError, match="out of order"):
            _chatgpt_block(shuffled, 0)

    # A sibling arm wedged between the LAST title and the terminator is refused:
    # the slice would otherwise span it, and the copy pinned inside could be
    # satisfied by the wrong card.
    with pytest.raises(AssertionError, match="an arm boundary sits inside block 3"):
        _chatgpt_block(
            "{wizardConnectHarness === 'chatgpt' ? (\n"
            "  <WizardBlock title=\"Add the Tortoise connector\">a</WizardBlock>\n"
            "  <WizardBlock title=\"Install the Tortoise skills\">b</WizardBlock>\n"
            "  <WizardBlock title=\"Give it its first job\">c</WizardBlock>\n"
            ") : wizardConnectHarness === 'codex' ? (\n  <p>sibling</p>\n"
            ") : wizardKeyless ? (\n  <p>keyless</p>\n", 2)

    # …and the SAME is required of the earlier blocks, whose only bound is the next
    # title: a sibling arm inserted between two titles is inside the slice, so its
    # copy would satisfy the pins for a card that lost them (#9895 review). Both
    # gaps are proved, because each is a different boundary of the helper.
    for gap in (0, 1):
        titles = "".join(
            f'  <WizardBlock title="{title}">{slot}</WizardBlock>\n'
            for slot, title in enumerate(_CHATGPT_BLOCKS))
        split_at = titles.index(f'<WizardBlock title="{_CHATGPT_BLOCKS[gap + 1]}"')
        with pytest.raises(
                AssertionError,
                match="an arm boundary sits inside block"):
            _chatgpt_block(
                "{wizardConnectHarness === 'chatgpt' ? (\n"
                + titles[:split_at]
                + ") : wizardConnectHarness === 'codex' ? (\n  <p>sibling</p>\n"
                "  <p>sibling copy: wizardPromptText('chatgpt', 1)</p>\n"
                + titles[split_at:]
                + ") : wizardKeyless ? (\n  <p>keyless</p>\n", gap)

    # A reflowed sentence still satisfies the ordering law: JSX and markdown wrap
    # mid-sentence, so the anchor must be matched on flattened copy rather than on
    # the raw source (#9895 review).
    _assert_org_pick_precedes_authorize(
        "pick the organization\n   or asks you to choose when your account can use\n"
        "   more than one; then click <strong>Authorize</strong>")

    # The last block cannot run past the card: its terminator is pinned, and a
    # quoted copy of it is refused rather than truncating the slice.
    with pytest.raises(AssertionError, match="no longer terminated"):
        _chatgpt_block(
            "{wizardConnectHarness === 'chatgpt' ? (\n"
            "  <WizardBlock title=\"Add the Tortoise connector\">a</WizardBlock>\n"
            "  <WizardBlock title=\"Install the Tortoise skills\">b</WizardBlock>\n"
            "  <WizardBlock title=\"Give it its first job\">c</WizardBlock>\n", 2)
    with pytest.raises(AssertionError, match="does not start its own clause"):
        _chatgpt_block(good.replace(") : wizardKeyless ? (", "<code>) : wizardKeyless ? (</code>"), 2)

    # Source shapes that a REGEX mis-read are not this helper's business: a `//` in
    # copy, an unbalanced looking `/*`, a protocol-relative URL and a comment quoting
    # the branch name all bound correctly, because no comment is read. These are
    # exercised on a GAP bound (block 0 → next title). A comment quoting the
    # terminator is a different case and is REFUSED by the clause check below, not
    # bound — noted so the distinction is not read as an inconsistency.
    for inert in (
        'href="//cdn.example/x"',
        "<p>a /* b</p>",
        "// route (`/auth/*`).",
        "/* ends at wizardKeyless ? ( below */",
    ):
        src = good.replace("<WizardBlock title=\"Add the Tortoise connector\">",
                           f"<WizardBlock title=\"Add the Tortoise connector\">{inert}")
        block_text = _chatgpt_block(src, 0)
        assert "a</WizardBlock>" in block_text and "keyless" not in block_text, inert

    # A nested ternary whose branches are parenthesised (`? ( … ) : ( … )`) is
    # REFUSED rather than treated as inert: the copy pinned there would render
    # conditionally, so a user could get a card without the act the pin asserts.
    # LIMIT, stated: the INLINE form `{cond ? <p>x</p> : null}` does not carry the
    # `) :` token and is not detected — no block in the card uses one, and pinning
    # conditional copy remains this guard's blind spot rather than a false claim.
    with pytest.raises(AssertionError, match="an arm boundary sits inside block"):
        _chatgpt_block(
            "{wizardConnectHarness === 'chatgpt' ? (\n"
            "  <WizardBlock title=\"Add the Tortoise connector\">{a ? (\n"
            "    <p>x</p>\n  ) : (\n    <p>y</p>\n  )}</WizardBlock>\n"
            "  <WizardBlock title=\"Install the Tortoise skills\">b</WizardBlock>\n"
            "  <WizardBlock title=\"Give it its first job\">c</WizardBlock>\n"
            ") : wizardKeyless ? (\n", 0)

    # PROSE is not an arm boundary. A parenthesised clause followed by a colon is an
    # ordinary copy edit (`(Plus or higher):`, `(no key needed):`), and the raw
    # `) :`/`):` count read it as a ternary continuation — red on CORRECT copy, with a
    # message naming a sibling arm, which is how a future editor learns to delete
    # this guard. All four red pre-fix; the line-anchored count is green on each.
    for prose in (
        "Custom connectors need a paid plan (Plus or higher): ready",
        "No key is needed (once per account): the connector holds the grant",
        "wrap it up): done",
        "the chooser (when your account can use more than one) : pick one",
    ):
        src = good.replace(
            ">a</WizardBlock>", f">{prose}</WizardBlock>")
        assert prose in _chatgpt_block(src, 0), prose

    # …while a boundary that DOES open a line is still counted, including one whose
    # line carries no other reading of the token.
    with pytest.raises(AssertionError, match="an arm boundary sits inside block 1"):
        _chatgpt_block(
            good.replace(
                ">a</WizardBlock>",
                ">a</WizardBlock>\n  ) : wizardDesktop ? (\n  <p>sibling</p>\n"), 0)
    with pytest.raises(AssertionError, match="an arm boundary sits inside block 1"):
        _chatgpt_block(
            good.replace(">a</WizardBlock>", ">a</WizardBlock>\n} : (\n  <p>x</p>\n"), 0)

    # A boundary written INLINE — on a copy line, not opening one — is counted too
    # when it continues a ternary (`? (` after the colon). Neither net covered this
    # shape before review: the token count wanted a line start, and `_ARM_SELECTOR`
    # only sees a condition that names a harness, so a `) : wizardDesktop ? (` inside
    # a copy line swallowed a sibling arm silently (reproduced in review, #9895).
    with pytest.raises(AssertionError, match="an arm boundary sits inside block 1"):
        _chatgpt_block(
            good.replace(">a</WizardBlock>",
                         ">a</WizardBlock>) : wizardDesktop ? (\n  <p>sibling</p>\n"), 0)

    # …and so is one wedged between the card's SELECTOR and its first title: the plan
    # note and caption sit in no block SLICE, so the scan for block 1 starts at the
    # selector. Without that, this boundary was inside no net at all (#9895 review).
    with pytest.raises(AssertionError, match="an arm boundary sits inside block 1"):
        _chatgpt_block(
            good.replace(
                '? (\n  <WizardBlock title="Add',
                '? (\n  ) : wizardDesktop ? (\n  <WizardBlock title="Add'), 0)

    # The ordering law reds on inverted copy and passes on correct copy — proved on
    # synthetic blocks, because the real file can only ever show the passing case.
    _assert_org_pick_precedes_authorize(
        f"pick the organization {_ORG_CHOOSER}; then <strong>Authorize</strong>")
    with pytest.raises(AssertionError, match="picks the Organization AFTER Authorize"):
        _assert_org_pick_precedes_authorize(
            f"click <strong>Authorize</strong>; check {_ORG_CHOOSER}")
    with pytest.raises(AssertionError, match="no longer tells the reader to pick"):
        _assert_org_pick_precedes_authorize("click <strong>Authorize</strong>")


def test_9660_chatgpt_is_a_live_onboarding_journey_not_a_docs_dead_end():
    """#9660 OWNER RULING (2026-10-23): the dashboard's connect step must offer
    ChatGPT as a first-class journey.

    Before this, a signed-up ChatGPT user reached the harness chooser and found
    no ChatGPT option at all (#2698 deleted its tab; #2912's four-family chooser
    excluded it), so the flow dead-ended and the public docs page was their only
    carrier. The owner directed that the flow itself must carry the journey:
    pick ChatGPT → connect → install the skills → first job, with the smallest
    number of user acts.

    The assertions read the RENDERED branch in main.jsx (the surface a user
    actually gets) plus the two modules that feed it, and they pin exactly the
    facts verified in the real ChatGPT UI on 2026-10-23 — the field list, the
    primary button, the connection surface, and the plan floor. A reworded
    owner fails here instead of shipping a journey that no longer matches the
    product it describes.
    """
    root = Path(__file__).resolve().parent.parent
    main = (root / "website" / "apps" / "dashboard" / "src" / "main.jsx").read_text(
        encoding="utf-8")
    harnesses = (root / "website" / "apps" / "dashboard" / "src" / "harnesses.js"
                 ).read_text(encoding="utf-8")

    # 1. The chooser carries it (the exclusion this ruling reverses).
    assert "Object.freeze({ id: 'chatgpt', name: 'ChatGPT', surfaces: Object.freeze([]) })" in harnesses, (
        "the ChatGPT family must be in HARNESS_FAMILIES — without it the connect "
        "step has no ChatGPT option and the flow dead-ends in the docs page")
    assert "HARNESSES = ['pi', 'cursor', 'claude', 'codex', 'claude-desktop', 'claude-web', 'chatgpt']" in (
        root / "website" / "apps" / "dashboard" / "scripts" / "gen-wizard-prompts-snapshot.mjs"
    ).read_text(encoding="utf-8"), (
        "the snapshot generator must cover chatgpt, or its prompts can change "
        "with no pinned diff and no failing test (#4880)")

    # 2. The card still exists, and its blocks are bounded on content the card
    #    itself renders: the first two end at the NEXT block's title, the third at
    #    the card's own terminator. Those three titles appear exactly once in
    #    main.jsx and only on this card — and a unique title says only where a block
    #    STARTS, so THREE structural facts bound each slice, and each reds on its own
    #    name: the slice holds exactly one harness selector, no continuation token
    #    (`) :` / `) : (` opening a line, or continuing a ternary mid-line; a sibling
    #    arm carries one, and its condition need not be a harness selector), and for
    #    the last block the terminator starts its own clause (a quoted copy of it is
    #    refused rather than truncating the slice). Block 1's scan starts at the
    #    card's selector, so the plan note and caption — card content in no slice —
    #    are covered as well.
    #    No comment is stripped and no ternary's condition is matched — that is what
    #    two earlier designs got wrong; the token is read by POSITION, never by
    #    spelling, so ordinary copy such as "a paid plan (Plus or higher): ready" is
    #    not mistaken for a boundary.
    #    `test_9660_the_chatgpt_card_slices_red_on_a_moved_or_duplicated_title`
    #    proves a moved, reordered or duplicated boundary actually reds, and that
    #    prose does not.
    assert _CHATGPT_SELECTOR in main, (
        "the chatgpt connect arm is gone from main.jsx — without the branch a "
        "ChatGPT user gets no card in the connect step, and every pin below would "
        "be judging some other arm")
    connector, skills, first_job = (_chatgpt_block(main, i) for i in range(3))
    # The CARD slice: the plan note that frames the card, to the end of the last
    # block. Two uses — it bounds the plan-note pin, and the assertion below that
    # no other harness selector sits between the note and the first block. (The
    # re-typed-URL pin runs on the ARM, below.)
    #
    # Guarded by name, not by `str.index`: a reworded note must report which anchor
    # moved instead of dying as `ValueError: substring not found` naming nothing —
    # the diagnostic the rest of this guard preserves everywhere (#9895 review).
    plan_note = "paid ChatGPT plan"
    assert plan_note in main, (
        "the ChatGPT card no longer names its plan floor ('paid ChatGPT plan'): the "
        "note was reworded or removed, so the card slice this test bounds cannot be "
        "derived — re-pin the note, do not drop the fact that the card names a "
        "plan requirement")
    card_from = main.index(plan_note)
    first_at = main.index(_CHATGPT_BLOCKS[0])
    last_at = main.index(_CHATGPT_BLOCKS[2])
    last_end = last_at + len(first_job)
    card = main[card_from:last_end]
    assert "wizardConnectHarness ===" not in main[card_from:first_at], (
        "another harness selector sits between the plan note and the chatgpt card's "
        "blocks — the note has moved into a sibling arm")

    def in_block(block: str, marker: str) -> int:
        """Position of `marker` inside `block` — presence is asserted, not assumed.

        Matched on FLATTENED copy: JSX wraps mid-sentence, so a raw `find` reds a
        correct card whose sentence breaks across a line — the failure mode this
        file records as "teaches the next lane to delete the guard".
        """
        flat = " ".join(block.split())
        position = flat.find(marker)
        assert position != -1, (
            f"the chatgpt card no longer renders {marker!r} in the block this test "
            "expects it in — the copy moved or was reworded")
        return position

    # 3. The connector block carries the four acts the live UI requires, in the
    #    order a user performs them: View details → Connect → the consent dialog's
    #    **Connect Tortoise** → Authorize. Anchored to the MARKUP a user reads, not
    #    to bare words: `Connect` alone matches the identifier
    #    `wizardConnectHarness` on the branch line itself, so it would survive the
    #    deletion of the OAuth-grant step it protects (found in review, #9660).
    #    An earlier card folded all three into "Connect, sign in … Authorize" and
    #    left a user staring at a dialog no step had told them to expect.
    for fact in ("chatgpt.com/plugins", "Add custom MCP server",
                 "Create as a plugin</strong>", "settings/plugins-settings",
                 "View details</strong>", "<strong>Connect</strong>",
                 "Connect Tortoise</strong>", "Authorize</strong>"):
        in_block(connector, fact)
    grant = [in_block(connector, m) for m in (
        "View details</strong>", "<strong>Connect</strong>",
        "Connect Tortoise</strong>", "Authorize</strong>")]
    assert grant == sorted(grant), (
        "the OAuth grant reads View details → Connect → Connect Tortoise → "
        "Authorize; the card must render them in that order")
    # …and the Organization act survives on BOTH consent shapes. The page has two:
    # with ONE selectable org it NAMES the org (oauth.py `consent_preview`,
    # `len(orgs) == 1`), and with several it renders a CHOOSER, hides the named-org
    # line and keeps Authorize disabled until one is picked (the
    # `len(orgs) > 1` memberships path + the page script). Copy that says only
    # "check the name the consent page shows" points at text that is not on screen
    # for a multi-org account — the same class of defect as the unnamed consent
    # dialog — so the pick is pinned, not merely described.
    #
    # AND IT COMES BEFORE `Authorize`: on the chooser branch the button is disabled
    # until a value is picked (`authBtn().disabled = true`, then the select's onchange
    # re-arms it), so "click Authorize, then pick" names an inert control.
    for fact in ("the page names it", "or asks you to choose when your account can use "
                 "more than one", "the connector writes to"):
        in_block(connector, fact)
    _assert_org_pick_precedes_authorize(connector)
    # 4. The Server URL comes from the constant, and the card never re-types it:
    #    asserted POSITIVELY (the constant is rendered) plus the negative, so
    #    neither can pass vacuously on a slice that lost the connector block.
    assert "{CANONICAL_MCP_URL}" in connector, (
        "the card's Server URL must come from the constant, not a re-typed literal")
    # The ARM, not just `card`: prose above the plan note (the card's caption) is
    # exactly where a re-typed URL would land, and `card` starts below it.
    arm_from = main.index(_CHATGPT_SELECTOR)
    assert "https://api.premiselabs.co/mcp" not in main[arm_from:last_end], (
        "the ChatGPT card re-types the server URL — read it from CANONICAL_MCP_URL")
    # 5. The skill hand-off is an INSTALL of the real archives, in its own block.
    for fact in ("SKILL_ARCHIVE_NAMES.map", "skillArchiveUrl(name)",
                 "Upload from your computer", "Replace existing"):
        in_block(skills, fact)
    # 6. The first-job prompt is rendered as a COPYABLE card, from the prompt
    #    module (never re-typed inline), and the plan floor is named.
    in_block(first_job, "wizardPromptText('chatgpt', 1")
    # The plan floor sits in the card's frame, above the first block title, so it
    # is asserted as a UNIQUE line rather than inside a block slice.
    plan_lines = [line for line in card.splitlines() if "paid ChatGPT plan" in line]
    assert len(plan_lines) == 1, (
        "the ChatGPT card's plan note must appear exactly once in main.jsx")
    assert "Plus" in plan_lines[0], (
        "the plan warning must name the floor (Plus or higher), not just say the "
        "plan is paid")


def test_9660_the_recipe_carriers_use_one_casing_for_the_tenant_noun():
    """The tenant noun is capitalised on every recipe carrier — one casing, guarded.

    The ground is the ENTITY: `docs/ONTOLOGY.md` names the domain type
    `org:Organization`, so the tenant is written with a capital when the copy names
    it. (Not a citation to the consent page — `tortoise/oauth.py` writes "Org" and
    "Choose an org…" there, which is a different word for the same thing — and not a
    house style for all prose: `main.jsx` mixes both spellings, and unifying that is
    a separate change.)

    SCOPE, exactly as coded. All four carriers (`main.jsx`, `harnesses.js`,
    `docs.html`, `tortoise/onboarding/SKILL.md`) get the three phrase assertions and
    the capital-present assertion. `docs.html` ADDITIONALLY gets a window scan across
    its whole ChatGPT section — the `<h4 id="chatgpt">` heading to the next
    `<div class="card">`, both edges asserted — for the word " organization".
    None of them is scanned wholesale: each legitimately writes the lowercase noun
    elsewhere (docs.html says "An organization account gets an isolated graph"; the
    others use it in other phrasings), so a whole-file scan would red on correct copy.

    The ordered pins compare case-insensitively (the ORDER is what they are about),
    which is why the casing needs its own assertion.
    """
    root = Path(__file__).resolve().parent.parent
    carriers = (
        "website/apps/dashboard/src/main.jsx",
        "website/apps/dashboard/src/harnesses.js",
        "website/docs.html",
        "tortoise/onboarding/SKILL.md",
    )
    for rel in carriers:
        # Flatten for JSX line wrapping, unescape the list item's apostrophe, and
        # drop inline markup: the neighbouring controls are written
        # `<strong>Authorize</strong>`, so `<strong>Organization</strong>` must not
        # slip past on markup alone.
        raw = (root / rel).read_text(encoding="utf-8").replace("&apos;", "'")
        text = " ".join(re.sub(r"<[^>]+>", " ", raw).split())
        if rel.endswith("docs.html"):
            # Scanned OUTRIGHT, not by pattern: the three-phrase list below cannot
            # see a phrasing it does not name, and review found two such lines in
            # this very recipe. The window is the WHOLE ChatGPT section — its heading
            # and every step included — and BOTH edges are asserted, not reached by a
            # bare `index()`: the page has no `</section>` (its recipes live in
            # `<div class="card">` blocks), so an unasserted end silently ran the scan
            # to end-of-file and would accuse this recipe of any later lowercase
            # mention (#9895 review).
            raw = (root / rel).read_text(encoding="utf-8")
            anchor = '<h4 id="chatgpt">'
            assert anchor in raw, (
                "the docs page's ChatGPT section heading moved — this scan is scoped "
                "to it, so the pin cannot be applied blind")
            section = raw[raw.index(anchor):]
            close = section.find('<div class="card">')
            assert close != -1, (
                "the docs page's card structure changed — the end of the ChatGPT "
                "section cannot be bounded, so this scan could not be scoped to the "
                "recipe and is refused rather than widened to the whole page")
            recipe = " ".join(re.sub(r"<[^>]+>", " ", section[:close]).split())
            assert " organization" not in recipe, (
                "docs.html lowercases the tenant noun inside the ChatGPT recipe; the "
                "recipe copy capitalises it (`Organization`), so one card must not "
                "render it two ways")
        for lower in ("organization you are onboarding", "organization you're onboarding",
                      "organization is the one the connector writes to"):
            assert lower not in text, (
                f"{rel} lowercases the tenant noun in body prose ({lower!r}); every "
                "recipe carrier capitalises it (`docs/ONTOLOGY.md` names the type "
                "org:Organization).")
        assert any(capital in text for capital in (
            "Organization you are onboarding", "Organization you're onboarding")), (
            f"{rel} no longer carries the Organization act at all — the sentence this "
            "pin exists for was removed or reworded")


def test_9660_the_org_choice_precedes_authorize_on_every_recipe_carrier():
    """#9660: the connect recipes that drive the shared consent page, judged on ORDER
    across seven copy blocks in four files (dashboard `main.jsx` and
    `harnesses.js`, `website/docs.html`, `tortoise/onboarding/SKILL.md`).

    WHY ORDER, NOT PRESENCE. `tortoise/oauth.py` reveals the consent page's two
    shapes: with one selectable Organization it names it and Authorize is live;
    with several it hides that line, shows a "Choose an org…" chooser and leaves
    Authorize disabled, arming it only from the chooser's onchange.
    So "click Authorize, and check the Organization … if it offers a chooser, pick
    one" tells such a user to click a disabled control. A presence assertion cannot
    see that; two offsets can.

    The chooser is a property of the PAGE, not of ChatGPT (`docs/oauth-mcp.md`), so
    the Claude key-less carriers are judged here too — same three acts, same page,
    and they shipped the same inversion.

    Each block is bounded to the recipe it belongs to — one list row, one copy
    string or template, one intro/note paragraph — and not to the whole file. The
    bound is what makes the offsets mean anything: measured over a file or a whole
    list, an unrelated mention of either act satisfies the order while the step
    that matters is inverted (#9660 review).

    The ChatGPT card is NOT here — it is pinned by
    `test_9660_chatgpt_is_a_live_onboarding_journey_not_a_docs_dead_end`.
    """
    root = Path(__file__).resolve().parent.parent
    main = (root / "website/apps/dashboard/src/main.jsx").read_text(encoding="utf-8")
    harnesses = (root / "website/apps/dashboard/src/harnesses.js").read_text(
        encoding="utf-8")
    docs = (root / "website/docs.html").read_text(encoding="utf-8")
    skill = (root / "tortoise/onboarding/SKILL.md").read_text(encoding="utf-8")

    def step_line(text: str, needle: str) -> str:
        for line in text.splitlines():
            if needle in line:
                return line
        raise AssertionError(f"no wizard step contains {needle!r} — moved or reworded")

    def list_block(text: str, anchor: str) -> str:
        """The row that carries `anchor`, through the end of its `<ol>`.

        ROW-bounded, not list-bounded: a whole-list slice is satisfied by an
        earlier row's mention of either act, which inverts the verdict in both
        directions (`_list_recipe_block`, #9660 review).
        """
        return _list_recipe_block(text, anchor)

    def span(text: str, start_needle: str, end_needle: str) -> str:
        """`text` from its first `start_needle` to the next `end_needle`.

        ROW-level on purpose. A whole-list slice takes the block's FIRST
        `Organization`/`Authorize`, so an unrelated mention earlier in the same
        list satisfies the order while the step that matters is inverted — a
        false green demonstrated in review (#9660).
        """
        start = text.find(start_needle)
        assert start != -1, f"the copy block starting {start_needle!r} moved or was reworded"
        end = text.find(end_needle, start)
        assert end != -1, f"the copy block starting {start_needle!r} lost its end"
        return text[start:end]

    # The Claude key-less carriers describe the SAME consent page and the same
    # three acts: the chooser is a property of the page, not of ChatGPT
    # (docs/oauth-mcp.md). They shipped the same inversion, so they are judged
    # here too rather than left to a comment.
    blocks = [
        ("Claude key-less card list",
         list_block(main, "Pick the Organization you&apos;re onboarding")),
        ("claude-web step", step_line(harnesses, "Claude opens the authorization page")),
        ("Claude Desktop snippet",
         span(harnesses, "'claude-desktop': () =>", "'claude-web': () =>")),
        ("Claude Desktop intro",
         span(harnesses, "'claude-desktop': 'Open Claude Desktop", "'claude-web':")),
        ("wizard step", step_line(harnesses, "confirm Connect Tortoise in the dialog")),
        ("docs row", span(docs, "chatgpt.com/settings/plugins-settings", "</li>")),
        ("served document",
         span(skill, "#1701 — ChatGPT is a 7th harness",
              "installing the three reusable skills")),
    ]
    for label, block in blocks:
        _assert_recipe_row_order(label, block)


def test_9660_the_keyless_recipe_is_bounded_to_its_row_not_the_list():
    """#9660 review: the key-less Claude recipe is judged on its ROW, not its list.

    A whole-list slice takes the block's FIRST `Organization` and its FIRST
    `Authorize`, so an unrelated mention in an EARLIER row decides the verdict in
    both directions — a false green on inverted copy, and a false red on correct
    copy. Both are ordinary copy edits, so both are reproduced here on synthetic
    markup, and the whole-list offsets are computed alongside to show that the row
    bound is what makes the difference (on pre-fix code each case lands on the
    wrong verdict):
    """
    anchor = "Pick the Organization you&apos;re onboarding"
    chooser = "or asks you to choose when your account can use more than one"
    pick_row = f"{anchor} — the page names it, {chooser}."
    authorize_row = "Click <strong>Authorize</strong>."

    def page(rows: list[str]) -> str:
        return ('<ol className="wizard-fields">\n'
                + "".join(f"  <li>{row}</li>\n" for row in rows)
                + "</ol>\n"
                '<ol className="wizard-fields"><li>an unrelated recipe</li></ol>\n')

    def offsets(fragment: str) -> tuple[int, int]:
        flat = " ".join(fragment.replace("> ", " ").split()).lower()
        return flat.find("organization"), flat.find("authorize")

    # (1) CORRECT copy whose sign-in row happens to name Authorize. Over the whole
    #     list the first `Authorize` lands BEFORE the pick row, so the order reads
    #     inverted (a false red on a good card); over the row it does not.
    rows = ["Claude opens the sign-in page — sign in, then Authorize is live.",
            pick_row, authorize_row]
    markup = page(rows)
    whole = markup[markup.index("<ol"):markup.index("</ol>")]
    whole_org, whole_authorize = offsets(whole)
    assert whole_org > whole_authorize, (
        "the whole-list slice no longer inverts this case, so it is not the "
        "reproduction it claims to be")
    _assert_recipe_row_order(
        "Claude key-less card row", _list_recipe_block(markup, anchor))

    # (2) INVERTED copy: the Authorize row is hoisted ABOVE the pick row, and an
    #     earlier row names the Organization. Over the whole list that mention
    #     satisfies "the pick comes first" — a false green on inverted copy, the
    #     #9660 defect this pin exists for. Over the row, Authorize is simply not
    #     there, so the block reds instead of passing.
    rows = ["Claude opens the sign-in page — the Organization chooser is on it.",
            authorize_row, pick_row]
    markup = page(rows)
    whole = markup[markup.index("<ol"):markup.index("</ol>")]
    whole_org, whole_authorize = offsets(whole)
    assert whole_org < whole_authorize, (
        "the whole-list slice no longer reads GREEN on the inversion, so this case "
        "is not the reproduction it claims to be")
    with pytest.raises(AssertionError, match="no longer names both acts"):
        _assert_recipe_row_order(
            "Claude key-less card row", _list_recipe_block(markup, anchor))

    # EVERY edge is asserted and each is exercised here — a bound that is asserted
    # but never tested is a branch nobody would notice going dead (#9895 review).
    with pytest.raises(AssertionError, match="no longer inside an ordered list"):
        _list_recipe_block(f"<p>{anchor}</p>", anchor)
    with pytest.raises(AssertionError, match="no longer appears"):
        _list_recipe_block(page([pick_row]), "Pick the Organization you are onboarding")
    with pytest.raises(AssertionError, match="lost its closing tag"):
        _list_recipe_block(f'<ol className="wizard-fields">\n  <li>{anchor}</li>\n', anchor)
    # …a row that left the list, opening AFTER its own `</ol>`…
    with pytest.raises(AssertionError, match="closes after its own list"):
        _list_recipe_block(f'<ol className="wizard-fields"><li>a</li></ol>\n<li>{anchor}</li>', anchor)
    # …and an anchor whose nearest `<li` belongs to a row OUTSIDE the list that holds
    # the pick (an orphan row above it), so the two bounds disagree.
    with pytest.raises(AssertionError, match="no longer inside a list item"):
        _list_recipe_block(
            f'<li>an orphan row</li>\n<ol className="wizard-fields">\n  {anchor}\n</ol>',
            anchor)
