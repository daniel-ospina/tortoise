"""Hermetic tests for tools/cmux_dispatch.py (#4292).

No network, no cmux, no FalkorDB. The pane is modelled by `FakeCmux`, a state
machine that reproduces the REAL failure physics observed on 2026-09-20:

  * a `Press any key to continue...` boot-block prompt consumes whatever text
    arrives as its keypress — the message is EATEN (and, if only a prefix is
    consumed, the remainder is submitted as a truncated turn);
  * bytes sent before the TUI takes stdin can sit unsent in the composer;
  * `cmux send` returns rc=0 in every one of those cases.

Screen fixtures are verbatim captures from the reproduction run.

Run standalone:    python3 tests/test_cmux_dispatch.py
Run under pytest:  python3 -m pytest tests/test_cmux_dispatch.py -q
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "cmux_dispatch.py"
sys.path.insert(0, str(ROOT / "tools"))

import cmux_dispatch as cd  # noqa: E402

# #4842: the durable fallback appends to the ORCHESTRATOR inbox
# (~/.pi/agent/state/orchestrator-inbox.log). Tests must never write the live one,
# so every test in this module — including the pre-existing transport-error ones —
# points the tool at a throwaway file. The tool reads the override at CALL time.
#
# The throwaway is created lazily in `setUpModule`, NOT at import: an
# all-deselected run (`-k` matching nothing) never calls `setUpModule`, so it must
# leave no temp dir behind (the old import-time `mkdtemp` + `tearDownModule`-only
# teardown leaked exactly one). The env override is saved and restored, never
# leaked into later modules (the #4883 env-isolation class).
INBOX_PATH: Path | None = None
_INBOX_DIR: Path | None = None
_INBOX_ENV_SAVED: str | None = None
_MODULE_SESSIONS: dict[str, object] = {}


def setUpModule() -> None:
    global INBOX_PATH, _INBOX_DIR, _INBOX_ENV_SAVED
    _INBOX_ENV_SAVED = os.environ.get(cd.INBOX_ENV)
    _INBOX_DIR = Path(tempfile.mkdtemp(prefix="cmux-dispatch-inbox-"))
    INBOX_PATH = _INBOX_DIR / "orchestrator-inbox.log"
    os.environ[cd.INBOX_ENV] = str(INBOX_PATH)
    # #7913: the same hermeticity for the SESSION STORE. `session_file_for` globs
    # `~/.pi/agent/sessions` whenever the probe runs and no override is set, so
    # without this the refusal tests would read the REAL store (157 buckets,
    # measured) — a live dependency in a suite that claims to be hermetic, and a
    # way for the result to depend on which lanes happen to be running.
    _MODULE_SESSIONS["saved"] = os.environ.get(cd.SESSIONS_ROOT_ENV)
    _MODULE_SESSIONS["dir"] = tempfile.mkdtemp(prefix="cmux-module-sessions-")
    os.environ[cd.SESSIONS_ROOT_ENV] = str(_MODULE_SESSIONS["dir"])


def tearDownModule() -> None:
    global INBOX_PATH, _INBOX_DIR
    if _INBOX_ENV_SAVED is None:
        os.environ.pop(cd.INBOX_ENV, None)
    else:
        os.environ[cd.INBOX_ENV] = _INBOX_ENV_SAVED
    if _INBOX_DIR is not None:
        shutil.rmtree(_INBOX_DIR, ignore_errors=True)
    INBOX_PATH = None
    _INBOX_DIR = None
    sessions_saved = _MODULE_SESSIONS.get("saved")
    if sessions_saved is None:
        os.environ.pop(cd.SESSIONS_ROOT_ENV, None)
    else:
        os.environ[cd.SESSIONS_ROOT_ENV] = str(sessions_saved)
    shutil.rmtree(str(_MODULE_SESSIONS.get("dir") or ""), ignore_errors=True)
    _MODULE_SESSIONS.clear()
#: The dispatch probe used throughout. Defined before the fixtures because the
#: derived queued-turn fixture substitutes it.
PROBE = "DISPATCH-PROBE-BOOTBLOCK-4292 :: reply with the single word ACK4292"

# --------------------------------------------------------------------------- #
# Screen fixtures. Most are VERBATIM live captures (2026-09-20, pi 0.85.1);
# `SCREEN_QUEUED_MID_TURN` is a VERBATIM live capture of 2026-09-28 (a lane with
# a queued submission), and `SCREEN_QUEUED_MID_TURN_TRUNCATED` is DERIVED from the
# installed renderer (`updatePendingMessagesDisplay` + `TruncatedText`/
# `truncateToWidth`, verified by RUNNING the renderer, not by reading it). Each
# fixture's provenance is stated at its definition.
# --------------------------------------------------------------------------- #

#: A freshly-booted pi frozen on the deprecation prompt. Boot never completes.
SCREEN_BOOT_BLOCK = """\
[tortoise-capture] enabled — cloud capture ON
[verification-gate] \u2705 Loaded — blocking git operations until verification complete
[vision-interceptor] Loaded. Image paste interception active. read_image tool registered.
Warning: Global tools/ directory contains custom tools. Custom tools have been merged into extensions.

Move your extensions to the extensions/ directory.
Migration guide: https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/CHANGELOG.md#extensions-migration
Documentation: https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/extensions.md

Press any key to continue...
"""

#: Booted, idle, nothing ever sent. NOTE: a fresh idle pane shows NO up/down
#: token counters — readiness must not key off those.
SCREEN_IDLE_READY = """\
 design-reviewer: ELDATO_ROOT not set — tool registered as no-op

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
 Update Available
 New version 0.86.1 is available. Run pi update
 Changelog: https://pi.dev/changelog
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
/private/tmp
0.0%/700k (auto)                                                              (deepseek) deepseek-flash \u2022 high
"""

#: The issue's signature failure: message in the composer, UNSENT, no turn.
SCREEN_COMPOSING_UNSENT = """\
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
 DISPATCH-PROBE-BOOTBLOCK-4292 :: reply with the single word ACK4292

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
0.0%/700k (auto)                                                              (deepseek) deepseek-flash \u2022 high
"""

#: A mid-turn pane: the good case.
SCREEN_WORKING = """\
\u2500\u2500 \u280b Working \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
/private/tmp
\u219119k \u2193577 R25k CH91.1% $0.003 3.8%/700k (auto)                          (deepseek) deepseek-flash \u2022 high
"""

#: A MID-TURN pane whose submission pi ACCEPTED into its pending queue (#5979).
#: The shape is a VERBATIM live capture (`cmux read-screen`, 2026-09-28) of a lane
#: with a queued submission: the `Steering:` entry and pi's own
#: `↳ Option+Up to edit all queued messages` hint, then the editor whose TOP border
#: carries the status label (`── ⠼ Working ──`) and whose bottom border is bare,
#: with the cwd/status footer below. Only the queued TEXT is substituted (with
#: `PROBE`). Rule lines are shortened for readability; their width is not
#: load-bearing. A mid-turn editor draws its top border WITH the Working label
#: (`RULE_RE` matches a labelled border too), so the composer region between the
#: last two borders is EMPTY — pi runs `editor.setText("")` before queueing, and
#: the composer read reports UNSENT for a message pi in fact holds.
#: `SCREEN_COMPOSING_UNSENT` is the opposite state (text in the composer, no
#: pending display, no turn).
SCREEN_QUEUED_MID_TURN = """\
 Steering: DISPATCH-PROBE-BOOTBLOCK-4292 :: reply with the single word ACK4292
 \u21b3 Option+Up to edit all queued messages

\u2500\u2500 \u280c Working \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
/private/tmp
\u2191242 \u2193180 R32k CH99.3% $0.000 86.0%/300k (auto)   (deepseek) deepseek-flash \u2022 high
"""

#: A TRUNCATED pending line, in the EXACT shape the renderer emits. `TruncatedText`
#: renders through `truncateToWidth(text, width)`, whose default ellipsis is the
#: literal `...`, so a pending line wider than the pane is captured as `<head>...`.
#: Verified by running the INSTALLED renderer (not eyeballed):
#:   node -e "import('<pi-tui>/dist/utils.js').then(m=>console.log(JSON.stringify(
#:     m.truncateToWidth('Steering: ' + MSG, 40))))"
#:   -> "Steering: DISPATCH-PROBE-BOOTBLOCK-42\u001b[0m...\u001b[0m"  (cmux strips ANSI)
#: — a 27-char head at a synthetic 40-col truncation width, then `...`. Both this
#: and the base fixture are DERIVED, so their declared widths are illustrative and
#: do not agree with each other; what matters (and is verified) is the SHAPE the
#: renderer emits: a visible head followed by a literal `...`.
SCREEN_QUEUED_MID_TURN_TRUNCATED = SCREEN_QUEUED_MID_TURN.replace(
    PROBE, "DISPATCH-PROBE-BOOTBLOCK-42..."
)

#: A NARROW-pane mid-turn pane whose queue is present but UNPARSEABLE: pi's own
#: hint line is itself right-truncated, so it no longer carries the full
#: `to edit all queued messages` text `PENDING_HINT_RE` requires. The container is
#: therefore invisible to the matcher while the composer IS positively empty (pi
#: cleared it on submit) — the state in which recovery must not re-send. Shape
#: derived from the live capture by truncating the hint and the entry, verified by
#: running the matcher (not eyeballed).
SCREEN_QUEUED_MID_TURN_NARROW_HINT = """\
 Steering: DISPATCH-PROBE-BOOTBLOCK-42...
 \u21b3 Option+Up to edit all queued me...

\u2500\u2500 \u280c Working \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
/private/tmp
\u2191242 \u2193180 R32k CH99.3% $0.000 86.0%/300k (auto)   (deepseek) deepseek-flash \u2022 high
"""

#: The CORRUPTION case: the prompt ate the pointer's prefix and the remainder
#: was submitted as a real turn. Observed live: latest_submitted_message == "292".
SCREEN_TRUNCATED_TURN = """\
\u2500\u2500 \u280b Working \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
292
/private/tmp
\u21912.1k \u2193489 R25k CH91.1% $0.003 4.1%/700k (auto)                          (deepseek) deepseek-flash \u2022 high
"""

#: THE ARM-B REGRESSION (live, 2026-09-20). pi renders its TUI inline, so after
#: the boot-block prompt is satisfied the prompt line STAYS VISIBLE above the
#: input box — in the capture and in the viewport. A whole-capture search for
#: the marker latches `boot_blocked` to True forever and readiness can never be
#: reached (ARM-B burned its full 240s wait on a pane that was ready at ~110s).
SCREEN_READY_WITH_PROMPT_IN_SCROLLBACK = """\
Warning: Global tools/ directory contains custom tools. Custom tools have been merged into extensions.

Move your extensions to the extensions/ directory.
Migration guide: https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/CHANGELOG.md#extensions-migration
Documentation: https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/extensions.md

Press any key to continue...

292[auto-sync] 55 orphaned worktree/branch record(s) \u2014 inspect:
    GHOST RECORDS: branch=feat/1349-embedder-swap dispatch=d-1349 (never existed)
    Cleanup: bash scan-orphans.sh --apply
[reflect-hook] enabled \u2014 hosted tortoise capture
[review-enforcer] Loaded \u2014 binary review dispatch enforcement active
[sequence-enforcer] Loaded \u2014 mode: gate
[slack-bridge] Loaded
[tortoise-capture] enabled \u2014 cloud capture ON
[verification-gate] Loaded
[vision-interceptor] Loaded
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
/private/tmp/4292-verify
0.0%/700k (auto)                                                              (deepseek) deepseek-flash \u2022 high
"""



#: A BARE LOGIN SHELL pane. Readable, but it never draws pi's footer (status bar)
#: and carries no boot-block marker — the exact state a DEAD lane's pane is in, and
#: the state #7158 wrote a dispatch brief into (a shell EXECUTES the bytes).
SCREEN_BARE_SHELL = """\
Last login: Wed Oct  8 20:58:11 on ttys004
danielospina@Daniels-MacBook-Pro 7158-bare-shell % 
"""

#: A DEAD pane whose PREVIOUS pi session left its footer in the scrollback while
#: the shell printed its prompt BELOW it. There is no boot-block marker, so
#: `boot_blocked` is silent — a "is a bar present anywhere" readiness test declares
#: this pane ready and writes the brief into the shell (#7158, the shape the
#: no-footer fixture misses). Ordering is the discriminator: the footer must be
#: the LAST thing drawn. The pwd line is part of the footer block a real capture
#: always carries (`[pwdLine, statsLine, ...statuses]`) — its absence would make
#: the stats line look like mere output (the round-6 bypass).
SCREEN_STALE_FOOTER_ABOVE_SHELL_PROMPT = """\
[tortoise-capture] Captured session abc (2 turns)
~/Documents/GitHub/tortoise (main)
\u21b34.0k \u21b3151 R17k CH81.2% $0.001 3.0%/700k (auto)                    (deepseek) deepseek-flash \u2022 high
danielospina@Daniels-MacBook-Pro 7158-stale-footer % 
"""

#: A LIVE pi pane whose footer carries an EXTENSION-STATUS line below the stats
#: line. pi pushes that line whenever any extension calls `ctx.ui.setStatus`
#: (loop-enforcer sets `Loop: <slug> (cycle N)`; slack-bridge sets a channel
#: thread) — both are loaded in every fleet pane. The stats line is therefore NOT
#: the literal last line, and readiness must still say YES: this is pi's own
#: output, not a shell prompt. Shape taken from the installed renderer's
#: `FooterComponent.render` (lines = [pwdLine, statsLine, ...statuses]).
SCREEN_LIVE_WITH_EXTENSION_STATUS_FOOTER = SCREEN_IDLE_READY + (
    "Loop: 7158-heartbeat-dead-lane (cycle 2)\n"
)

#: A LIVE pi pane right after a compaction: `getContextUsage()` returns a null
#: percent, so pi renders `?/Nk (auto)` instead of `N.N%/Nk (auto)`. VERBATIM shape
#: from this box's session logs (`↑1.3M ↓643k R103M CH99.9% $0.887 ?/300k (auto)`
#: under `~/Documents/GitHub/tortoise (main)`). Readiness must say YES — a
#: digit-only indicator refused this healthy lane (#7158 round 6).
SCREEN_LIVE_WITH_UNKNOWN_CONTEXT_FOOTER = SCREEN_IDLE_READY.replace(
    "0.0%/700k", "?/700k"
)

#: A LIVE pi pane whose footer has been TORN by a banner printed over it: the
#: context-budget token is gone and only the model badge survives (banner VERBATIM
#: from this box's session logs, 2026-09-26; the pwd line above it survives, as a
#: real capture always has — `[pwdLine, statsLine, ...statuses]`). Readiness must
#: say YES — requiring the token refused a healthy lane (#7158 round 7).
SCREEN_LIVE_WITH_TORN_FOOTER = (
    "~/Documents/GitHub/tortoise (main)\n"
    "[tortoise-capture] Hosted capture FAILED (HTTP 402) \u2014 a manual-recovery "
    "JSONL record was kept at /Users/danielospina/.tortoise/session-events/"
    "2026-09-26.jsonlepseek) deepseek-flash \u2022 high\n"
)

#: A LIVE pane whose TRANSCRIPT carries lines that look like shell prompts
#: (`$ \u2026`, `# heading`) above a genuine footer block — routine in agent panes.
#: The block anchor must keep the prompt scan BELOW the footer; a whole-capture
#: scan would refuse a healthy lane (#7158 round 9).
SCREEN_LIVE_WITH_TRANSCRIPT = "$ uv run pytest tests/ -q\n# Findings\nall green\n" + SCREEN_IDLE_READY

#: A LIVE pane whose cwd IS `$HOME`, so `formatCwdForFooter` renders the pwd line as
#: bare `~` — which a `\S` requirement rejected, losing the block anchor and
#: refusing a healthy lane (#7158 round 9).
SCREEN_LIVE_WITH_HOME_PWD = (
    "$ uv run pytest -q\n# Findings\nall green\n~\n"
    "\u21911.3M \u2193643k R103M CH99.9% $0.887 3.0%/700k (auto)"
    "  (deepseek) deepseek-flash \u2022 high\n"
)

#: A DEAD pane whose stale footer carries a ` \u2022 <sessionName>` suffix on the pwd
#: line (the shape `pi --name` produces). The pwd anchor must still recognise the
#: footer block, or the stats-mimicking output below re-opens the round-6 bypass
#: (#7158 round 7).
SCREEN_STALE_FOOTER_WITH_SESSION_NAME = (
    "[tortoise-capture] Captured session abc (2 turns)\n"
    "~/Documents/GitHub/tortoise (main) \u2022 7158-lane\n"
    "\u21b34.0k \u21b3151 R17k CH81.2% $0.001 3.0%/700k (auto)"
    "                    (deepseek) deepseek-flash \u2022 high\n"
    "danielospina@Daniels-MacBook-Pro 7158-stale % \n"
    "42.0%/700k (auto)\ndone\n"
)

#: A DEAD pane whose stale footer is followed by a shell prompt WITH a typed
#: command — the line ends in text, not a sigil, so an end-anchored detector misses
#: it and the brief is EXECUTED (round-3 finding).
SCREEN_STALE_FOOTER_ABOVE_SHELL_COMMAND = (
    SCREEN_STALE_FOOTER_ABOVE_SHELL_PROMPT.rstrip("\n") + " ls -la\n"
)

#: A LIVE pi extension-status line carrying a percent. pi's own status text, not a
#: prompt: the digit guard must keep readiness TRUE.
SCREEN_LIVE_WITH_PERCENT_STATUS_FOOTER = SCREEN_IDLE_READY + "Uploading 50%\n"

#: A DEAD pane whose stale footer is followed by a bash/sh prompt whose sigil
#: ABUTS A DIGIT (`bash-3.2$ `) — the shape a digit-guarded line-end rule misses.
SCREEN_STALE_FOOTER_ABOVE_DIGIT_PROMPT = (
    "[tortoise-capture] Captured session abc (2 turns)\n"
    "\u21b34.0k \u21b3151 R17k CH81.2% $0.001 3.0%/700k (auto)"
    "                    (deepseek) deepseek-flash \u2022 high\n"
    "bash-3.2$ \n"
)

#: A DEAD pane whose stale footer is followed by a ROOT prompt with a command, so
#: the sigil abuts `]` and the line does not end in a sigil.
SCREEN_STALE_FOOTER_ABOVE_ROOT_PROMPT = (
    "[tortoise-capture] Captured session abc (2 turns)\n"
    "\u21b34.0k \u21b3151 R17k CH81.2% $0.001 3.0%/700k (auto)"
    "                    (deepseek) deepseek-flash \u2022 high\n"
    "[root@host ~]# ls -la\n"
)

#: A DEAD pane whose prompt is followed by OUTPUT that itself matches the stats
#: shape AND a further ordinary line. Anchoring on the last stats-shaped line alone
#: hides the prompt above it (round-5/6: a fixed-width tail window just moves the
#: problem down one line); the pwd-anchored footer block sees through it.
SCREEN_STALE_FOOTER_THEN_STATUS_LIKE_OUTPUT = (
    SCREEN_STALE_FOOTER_ABOVE_SHELL_PROMPT + "42.0%/700k (auto)\ndone\n"
)

#: A DEAD pane whose footer row is NOT newline-terminated and the shell prompt is
#: appended to it (a crash mid-line). Same-line prompt (#7158 round 5).
SCREEN_STALE_FOOTER_AND_PROMPT_SAME_LINE = (
    "[tortoise-capture] Captured session abc (2 turns)\n"
    "\u21b34.0k \u21b3151 R17k CH81.2% $0.001 3.0%/700k (auto)"
    "                    (deepseek) deepseek-flash \u2022 high"
    " danielospina@Daniels-MacBook-Pro 7158 % "
)

#: A DEAD pane that still parses as pi's composer (two rules) while showing a bare
#: shell prompt — the shape where recovery picks `R_RESEND` (the second write site).
SCREEN_DEAD_SHELL_WITH_COMPOSER = (
    "\u2500" * 36 + "\n" + "\u2500" * 36 + "\n"
    + "danielospina@Daniels-MacBook-Pro 7158-dead % \n"
)



# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


class TestScreenClassification(unittest.TestCase):
    def test_boot_block_detected_on_the_real_frozen_screen(self):
        self.assertTrue(cd.boot_blocked(SCREEN_BOOT_BLOCK))
        self.assertFalse(cd.screen_ready(SCREEN_BOOT_BLOCK))

    def test_idle_fresh_pane_is_ready_despite_having_no_token_counters(self):
        # Regression guard: a fresh idle pi has no `↑`/`↓`. Keying readiness off
        # those would mean "never ready" for exactly the pane we must dispatch to.
        self.assertNotIn("\u2191", SCREEN_IDLE_READY)
        self.assertTrue(cd.screen_ready(SCREEN_IDLE_READY))
        self.assertFalse(cd.boot_blocked(SCREEN_IDLE_READY))

    def test_working_pane_is_ready(self):
        self.assertTrue(cd.screen_ready(SCREEN_WORKING))

    def test_composing_pane_is_ready_but_screen_not_consumed(self):
        # Ready ≠ consumed: this is the state that used to be reported as success.
        self.assertTrue(cd.screen_ready(SCREEN_COMPOSING_UNSENT))

    def test_prompt_left_visible_above_a_live_tui_is_NOT_a_block(self):
        """ARM-B regression: readiness must not latch off scrollback."""
        self.assertIn(cd.BOOT_BLOCK_MARKER, SCREEN_READY_WITH_PROMPT_IN_SCROLLBACK)
        self.assertFalse(cd.boot_blocked(SCREEN_READY_WITH_PROMPT_IN_SCROLLBACK))
        self.assertTrue(cd.screen_ready(SCREEN_READY_WITH_PROMPT_IN_SCROLLBACK))

    def test_prompt_at_the_tail_IS_a_block(self):
        self.assertTrue(cd.boot_blocked(SCREEN_BOOT_BLOCK))
        # Same prompt, but as the last rendered thing after the TUI starts.
        self.assertTrue(cd.boot_blocked("tui row\n" + SCREEN_BOOT_BLOCK))

    def test_marker_followed_by_boot_output_is_STILL_a_live_block(self):
        """A position-based rule ("the marker is not last") reads a live block as
        safe as soon as ANY boot output follows the prompt, and the gate then
        feeds the brief to the prompt. Readiness is an asymmetry, not a position:
        marker present AND no status bar => blocked.
        """
        screen = (
            "Documentation: https://github.com/earendil-works/pi-mono/...\n"
            "\nPress any key to continue...\n\n"
            "[reflect-hook] enabled — hosted tortoise capture\n"
            "[review-enforcer] Loaded — binary review dispatch enforcement active\n"
            "[sequence-enforcer] Loaded — mode: gate\n"
        )
        self.assertTrue(cd.boot_blocked(screen))
        self.assertFalse(cd.screen_ready(screen))

    def test_status_bar_wins_over_a_lingering_marker(self):
        """The inverse error: a READY pane must never be refused just because the
        prompt line is still visible above the input box."""
        self.assertTrue(cd.marker_present(SCREEN_READY_WITH_PROMPT_IN_SCROLLBACK))
        self.assertTrue(cd.status_bar_present(SCREEN_READY_WITH_PROMPT_IN_SCROLLBACK))
        self.assertFalse(cd.boot_blocked(SCREEN_READY_WITH_PROMPT_IN_SCROLLBACK))
        self.assertTrue(cd.screen_ready(SCREEN_READY_WITH_PROMPT_IN_SCROLLBACK))

    def test_mid_boot_with_neither_marker_nor_status_bar_is_not_a_block(self):
        """Pins the `marker_present` half of the rule: a plain mid-boot screen is
        NOT ready and NOT blocked — it must simply keep waiting, never be
        mistaken for a live prompt (a false refusal)."""
        mid_boot = (
            "[audit-logger] \u2705 Loaded\n"
            "[builtin-tools] Registered: web_search, web_fetch, todo_write, task\n"
            "[loop-enforcer] \u2705 Loaded\n"
        )
        self.assertFalse(cd.marker_present(mid_boot))
        self.assertFalse(cd.status_bar_present(mid_boot))
        self.assertFalse(cd.boot_blocked(mid_boot))
        self.assertFalse(cd.screen_ready(mid_boot))

    def test_STALE_status_bar_above_a_live_prompt_is_STILL_a_block(self):
        """A pane restarted after a previous pi session still shows the PREVIOUS
        session's status bar in the capture, above the freshly printed prompt.
        Matching any status bar there declared the pane ready and fed the prompt
        the brief (the prompt then ate the prefix). Readiness must be ordered:
        the bar has to come AFTER the last prompt."""
        stale = (
            "[tortoise-capture] Captured session abc (2 turns)\n"
            "\u21914.0k \u2193151 R17k CH81.2% $0.001 3.0%/700k (auto)"
            "                    (deepseek) deepseek-flash \u2022 high\n"
            "[audit-logger] \u2705 Loaded\n"
            "Warning: Global tools/ directory contains custom tools.\n"
            "\nPress any key to continue...\n"
        )
        self.assertTrue(cd.status_bar_present(stale), "the stale bar IS present")
        self.assertTrue(cd.boot_blocked(stale), "but the prompt is LIVE")
        self.assertFalse(cd.screen_ready(stale))

    def test_truncated_turn_screen_does_not_contain_the_fingerprint(self):
        fp = cd.fingerprint(PROBE)
        self.assertFalse(cd.text_on_screen(SCREEN_TRUNCATED_TURN, fp))


class TestFingerprint(unittest.TestCase):
    def test_head_anchored_and_whitespace_normalized(self):
        self.assertEqual(cd.fingerprint(PROBE), PROBE[:40])
        self.assertEqual(cd.fingerprint("a\n\n b\tc"), "a b c")

    def test_within_cmux_truncation_budget(self):
        # cmux truncates latest_submitted_message at 240 chars with `…`; the
        # fingerprint must sit safely in the head.
        self.assertLessEqual(cd.FINGERPRINT_CHARS, 100)

    def test_empty_message_has_empty_fingerprint(self):
        self.assertEqual(cd.fingerprint("   \n "), "")


class TestTextOnScreen(unittest.TestCase):
    def test_finds_message_sitting_unsent_in_the_composer(self):
        fp = cd.fingerprint(PROBE)
        self.assertTrue(cd.text_on_screen(SCREEN_COMPOSING_UNSENT, fp))

    def test_finds_message_wrapped_mid_token(self):
        fp = cd.fingerprint(PROBE)
        wrapped = PROBE[:20] + "\n" + PROBE[20:]
        self.assertTrue(cd.text_on_screen(wrapped, fp))

    def test_absent_on_a_screen_that_never_received_it(self):
        self.assertFalse(cd.text_on_screen(SCREEN_IDLE_READY, cd.fingerprint(PROBE)))


class TestConsumption(unittest.TestCase):
    def _entry(self, message, at="2026-09-20T19:48:47.937Z"):
        return {"latest_submitted_message": message, "latest_submitted_at": at}

    def test_consumed_when_fingerprint_present_and_submission_is_new(self):
        fp = cd.fingerprint(PROBE)
        before = self._entry(None, None)
        after = self._entry(PROBE)
        self.assertEqual(cd.is_consumed(before, after, fp), (True, "submitted"))

    def test_not_consumed_when_message_still_sitting_unsent(self):
        fp = cd.fingerprint(PROBE)
        self.assertEqual(
            cd.is_consumed(self._entry(None, None), self._entry(None, None), fp)[0], False
        )

    def test_not_consumed_when_only_a_truncated_remnant_was_submitted(self):
        # The live corruption: the prompt ate the prefix, "292" was submitted.
        fp = cd.fingerprint(PROBE)
        consumed, reason = cd.is_consumed(self._entry(None, None), self._entry("292"), fp)
        self.assertFalse(consumed)
        self.assertEqual(reason, "not-at-the-head-of-latest-submitted-message")

    def test_re_sending_identical_text_is_not_falsely_reported_as_consumed(self):
        # Same text as the previous message + same timestamp => NOT a new turn.
        fp = cd.fingerprint(PROBE)
        entry = self._entry(PROBE)
        self.assertFalse(cd.is_consumed(dict(entry), dict(entry), fp)[0])

    def test_resubmission_of_identical_text_advances_the_timestamp(self):
        fp = cd.fingerprint(PROBE)
        before = self._entry(PROBE, "2026-09-20T19:48:47.937Z")
        after = self._entry(PROBE, "2026-09-20T19:50:00.000Z")
        self.assertTrue(cd.is_consumed(before, after, fp)[0])

    def test_short_message_is_not_confirmed_by_an_unrelated_turn(self):
        """The fingerprint is head-anchored and the match must be too: a dispatch
        into a SHARED lane can otherwise be confirmed by any other turn that
        merely contains those characters."""
        before = self._entry(None, None)
        after = self._entry("running the ongoing migration now")
        consumed, _ = cd.is_consumed(before, after, cd.fingerprint("go"))
        self.assertFalse(consumed, "a substring hit must not count as delivery")

    def test_a_superset_containing_but_not_starting_with_the_fp_is_not_consumed(self):
        """This is the test that actually pins HEAD-ANCHORING: with an unanchored
        `in` test, this screen reports consumed."""
        fp = cd.fingerprint(PROBE)
        before = self._entry(None, None)
        after = self._entry("context prefixed to " + PROBE)
        self.assertIn(fp, cd.submitted_message(after))
        self.assertFalse(cd.is_consumed(before, after, fp)[0])

    def test_presence_semantics_used_by_verify(self):
        """`verify` asks 'did this text land?', not 'is it newer than my
        baseline' — otherwise an already-submitted message reads NOT-CONSUMED."""
        entry = self._entry(PROBE)
        self.assertFalse(cd.is_consumed(entry, entry, cd.fingerprint(PROBE))[0])
        consumed, reason = cd.is_consumed(
            entry, entry, cd.fingerprint(PROBE), require_novelty=False
        )
        self.assertTrue(consumed, reason)

    def test_missing_workspace_is_a_failure_not_a_pass(self):
        self.assertFalse(cd.is_consumed(None, None, cd.fingerprint(PROBE))[0])


class TestRecoveryDecision(unittest.TestCase):
    def test_boot_block_wins_over_everything(self):
        fp = cd.fingerprint(PROBE)
        both = SCREEN_BOOT_BLOCK + "\n" + PROBE
        self.assertEqual(cd.recovery_action(both, fp), cd.R_DISMISS_RESEND)

    def test_text_visible_means_release_only_never_re_send(self):
        # Re-sending here would duplicate the message in the lane's editor.
        fp = cd.fingerprint(PROBE)
        self.assertEqual(
            cd.recovery_action(SCREEN_COMPOSING_UNSENT, fp), cd.R_RELEASE
        )

    def test_text_invisible_with_a_provably_empty_composer_means_re_send(self):
        fp = cd.fingerprint(PROBE)
        self.assertTrue(cd.composer_empty(SCREEN_IDLE_READY))
        self.assertEqual(cd.recovery_action(SCREEN_IDLE_READY, fp), cd.R_RESEND)

    def test_unidentifiable_composer_never_re_sends(self):
        """Re-sending requires POSITIVE evidence the composer is empty. A screen
        that merely fails to contain the text (a stale/partial frame, or a long
        brief whose head is outside the read window) must degrade to release-only
        — a blind re-send would leave the message in the composer TWICE and the
        next Enter would submit it doubled, which `is_consumed` then reports as
        success because the head is unchanged."""
        fp = cd.fingerprint(PROBE)
        partial = "\u2500" * 40 + "\n" + "... tail of a long brief ...\n"   # one rule only
        self.assertIsNone(cd.composer_region(partial))
        self.assertFalse(cd.composer_empty(partial))
        self.assertEqual(cd.recovery_action(partial, fp), cd.R_RELEASE)

    def test_non_empty_composer_without_a_visible_fingerprint_never_re_sends(self):
        """THE T4 PROPERTY, independent of read depth: the text may be in the
        composer but outside the capture (a long brief, a partial frame). The
        composer is then non-blank, so re-sending is refused regardless of whether
        the fingerprint was found."""
        fp = cd.fingerprint(PROBE)
        rule = "\u2500" * 40
        screen = (
            "some transcript text\n"
            + rule + "\n"
            + "a long brief whose HEAD is outside the captured window\n"
            + "\u2026\n"
            + rule + "\n"
            + "/private/tmp\n"
            + "0.0%/700k (auto)  (deepseek) deepseek-flash \u2022 high\n"
        )
        self.assertFalse(cd.text_on_screen(screen, fp), "fingerprint not visible")
        self.assertFalse(cd.composer_empty(screen), "but the composer is NOT blank")
        self.assertEqual(cd.recovery_action(screen, fp), cd.R_RELEASE)

    def test_composer_holding_the_text_is_not_empty(self):
        self.assertFalse(cd.composer_empty(SCREEN_COMPOSING_UNSENT))


class TestPendingTurnDisplay(unittest.TestCase):
    """`pending_turn_identity`/`pending_turn_matches` — the queue signal (#5979).

    `BEFORE` is a pre-send pane with no pending turn, so novelty holds in every
    test that is not about novelty itself.
    """

    BEFORE = SCREEN_IDLE_READY

    def test_steering_line_carrying_the_whole_message_is_a_pending_turn(self):
        self.assertIn("Steering: ", SCREEN_QUEUED_MID_TURN)
        # The composer is EMPTY: pi cleared it before queueing. That is exactly
        # why the composer read cannot be the queue signal.
        self.assertEqual(cd.composer_region(SCREEN_QUEUED_MID_TURN).strip(), "")
        self.assertEqual(cd.pending_turn_identity(SCREEN_QUEUED_MID_TURN, PROBE), PROBE)
        self.assertTrue(cd.pending_turn_matches(SCREEN_QUEUED_MID_TURN, PROBE, self.BEFORE))

    def test_follow_up_line_carrying_the_whole_message_is_a_pending_turn(self):
        screen = SCREEN_QUEUED_MID_TURN.replace("Steering:", "Follow-up:")
        self.assertTrue(cd.pending_turn_matches(screen, PROBE, self.BEFORE))

    def test_TEXT_IN_THE_COMPOSER_IS_NOT_A_PENDING_TURN(self):
        """THE REFUTATION, pinned. On submit-while-streaming pi clears the editor
        BEFORE queueing, so text still in the composer is the UNSENT state
        (`SCREEN_COMPOSING_UNSENT`, "UNSENT, no turn"). The refuted design read
        that as `queued` — a false success that also skipped the release
        recovery which would have submitted it."""
        self.assertEqual(cd.composer_region(SCREEN_COMPOSING_UNSENT).strip(), PROBE)
        self.assertFalse(
            cd.pending_turn_matches(SCREEN_COMPOSING_UNSENT, PROBE, self.BEFORE)
        )

    def test_a_different_pending_message_is_not_our_pending_turn(self):
        other = SCREEN_QUEUED_MID_TURN.replace(PROBE, "an unrelated brief")
        self.assertFalse(cd.pending_turn_matches(other, PROBE, self.BEFORE))

    def test_a_pending_line_LONGER_than_the_message_is_a_DIFFERENT_message(self):
        """THE #5983 ADVERSARIAL FINDING. Matching on the 40-char FINGERPRINT
        accepted a dropped short dispatch whenever an unrelated longer queued
        message shared its head — `visible.startswith(fp)` with `fp == "continue"`
        confirmed a lost `"continue"` against a queued `"continue with the
        migration"`. Identity is the WHOLE message, so a longer pending line never
        matches (and the fleet's own nudges, which share a head, stop colliding)."""
        screen = SCREEN_QUEUED_MID_TURN.replace(PROBE, "continue with the migration")
        self.assertIsNone(cd.pending_turn_identity(screen, "continue"))
        self.assertFalse(cd.pending_turn_matches(screen, "continue", self.BEFORE))

    def test_a_truncated_pending_line_with_the_renderers_ellipsis_matches(self):
        """`truncateToWidth` appends a literal `...`, so the truncated head on
        screen ends in `...`. The comparison strips it; comparing the raw text (as
        the first revision did) can NEVER match a truncated line."""
        self.assertIn("BOOTBLOCK-42...", SCREEN_QUEUED_MID_TURN_TRUNCATED)
        self.assertEqual(
            cd.pending_turn_identity(SCREEN_QUEUED_MID_TURN_TRUNCATED, PROBE),
            "DISPATCH-PROBE-BOOTBLOCK-42",
        )
        self.assertTrue(
            cd.pending_turn_matches(SCREEN_QUEUED_MID_TURN_TRUNCATED, PROBE, self.BEFORE)
        )

    def test_an_unreadably_short_pending_remnant_is_not_a_match(self):
        # Fail closed: a remnant below the identity floor is not enough.
        screen = SCREEN_QUEUED_MID_TURN.replace(PROBE, PROBE[:12] + "...")
        self.assertIsNone(cd.pending_turn_identity(screen, PROBE))
        self.assertFalse(cd.pending_turn_matches(screen, PROBE, self.BEFORE))

    def test_no_pending_display_is_never_a_pending_turn(self):
        for screen in (SCREEN_IDLE_READY, SCREEN_WORKING, None):
            self.assertFalse(cd.pending_turn_matches(screen, PROBE, self.BEFORE))
        self.assertFalse(cd.pending_turn_matches(SCREEN_QUEUED_MID_TURN, "", self.BEFORE))

    def test_a_PRE_EXISTING_identical_pending_line_is_not_novel(self):
        """A pending line already on the pane BEFORE the send is not evidence
        about THIS send: a repeat dispatch of the same brief (or a stale line left
        in scrollback) would otherwise confirm a send whose bytes never landed.
        Same novelty discipline as `is_consumed`'s."""
        self.assertEqual(cd.pending_turn_identity(SCREEN_QUEUED_MID_TURN, PROBE), PROBE)
        self.assertFalse(
            cd.pending_turn_matches(
                SCREEN_QUEUED_MID_TURN, PROBE, SCREEN_QUEUED_MID_TURN
            )
        )

    def test_without_a_pre_send_baseline_the_verdict_fails_closed(self):
        self.assertFalse(cd.pending_turn_matches(SCREEN_QUEUED_MID_TURN, PROBE, None))

    def test_an_ambiguous_short_remnant_OF_OURS_suppresses_the_resend(self):
        """A pending head of OUR message below the identity floor may be our own
        truncated queue entry, so a resend could duplicate it. The composer is
        empty, so without this hint `composer_empty` would pick `resend`."""
        fp = cd.fingerprint(PROBE)
        screen = SCREEN_QUEUED_MID_TURN.replace(PROBE, PROBE[:12] + "...")
        self.assertTrue(cd.composer_empty(screen))
        self.assertTrue(cd.pending_turn_ambiguous(screen, PROBE))
        self.assertEqual(cd.recovery_action(screen, fp, PROBE), cd.R_RELEASE)

    def test_a_short_pending_line_WITHOUT_an_ellipsis_is_a_DIFFERENT_message(self):
        """A shorter line is ours only when the renderer actually cut it (the raw
        text ends in `...`). Otherwise it is a DIFFERENT message sharing our head,
        and accepting it confirmed a dropped send against an unrelated queued one."""
        for queued in (
            "0123456789abcdef",
            "please run the full battery",
        ):
            message = queued + " and report results"
            screen = SCREEN_QUEUED_MID_TURN.replace(PROBE, queued)
            self.assertIsNone(cd.pending_turn_identity(screen, message), queued)
            self.assertFalse(cd.pending_turn_matches(screen, message, self.BEFORE), queued)

    def test_a_multi_line_message_does_NOT_match_on_its_first_line(self):
        """pi renders only the first line of a multi-line submission, but a first
        line is not message-unique — two briefs can share one — so accepting it
        would confirm a DIFFERENT queued message (and could suppress the resend
        that delivers a genuinely lost one). Fail closed instead: callers pass
        flattened text, as `_read_message` does."""
        message = "Fix the bug\n\nMore detail follows in this brief."
        screen = SCREEN_QUEUED_MID_TURN.replace(PROBE, "Fix the bug")
        self.assertIsNone(cd.pending_turn_identity(screen, message))
        self.assertFalse(cd.pending_turn_matches(screen, message, self.BEFORE))

    def test_a_message_that_ITSELF_ends_in_an_ellipsis_matches_exactly(self):
        """The renderer's `...` is stripped as truncation evidence, so a message
        that legitimately ENDS in `...` must still match its own rendered line —
        comparing only the stripped text made it a permanent false negative."""
        message = "check the build..."
        screen = SCREEN_QUEUED_MID_TURN.replace(PROBE, message)
        self.assertIsNotNone(cd.pending_turn_identity(screen, message))
        self.assertTrue(cd.pending_turn_matches(screen, message, self.BEFORE))

    def test_a_LONGER_message_cut_to_our_length_is_a_DIFFERENT_message(self):
        """A real cut head is STRICTLY shorter than the message. Without that, a
        different, LONGER queued message (`target + " tail"`) truncated to exactly
        our text was accepted as ours — a false `queued` for an unqueued send."""
        target = "continue with the migration and then run the full test suite"
        longer = target + " before reporting back to the lane owner"
        screen = SCREEN_QUEUED_MID_TURN.replace(PROBE, longer[: len(target)] + "...")
        self.assertIsNone(cd.pending_turn_identity(screen, target))
        self.assertFalse(cd.pending_turn_matches(screen, target, self.BEFORE))

    def test_a_SHORT_message_cut_on_a_narrow_pane_still_matches(self):
        """The floor must not make a genuinely cut SHORT message unmatchable: it
        drops below the target length for targets shorter than `PENDING_MIN_CHARS`."""
        target = "please run it"
        screen = SCREEN_QUEUED_MID_TURN.replace(PROBE, target[:9] + "...")
        self.assertIsNotNone(cd.pending_turn_identity(screen, target))
        self.assertTrue(cd.pending_turn_matches(screen, target, self.BEFORE))

    def test_two_identical_queued_copies_count_as_NEW(self):
        """Novelty is a COUNT, not a string comparison: a repeat dispatch whose
        copy is queued a second time leaves the identity text unchanged, so a
        string test reported the accepted submission as a failure."""
        before = SCREEN_QUEUED_MID_TURN
        after = SCREEN_QUEUED_MID_TURN.replace(
            " \u21b3 Option+Up to edit all queued messages",
            " Steering: DISPATCH-PROBE-BOOTBLOCK-4292 :: reply with the single word ACK4292\n"
            " \u21b3 Option+Up to edit all queued messages",
        )
        self.assertEqual(cd.pending_turn_identity(before, PROBE), cd.pending_turn_identity(after, PROBE))
        self.assertTrue(cd.pending_turn_matches(after, PROBE, before))
        # ...and the reverse (a copy REMOVED) is not a new submission
        self.assertFalse(cd.pending_turn_matches(before, PROBE, after))

    def test_a_live_container_with_bash_output_BELOW_it_is_still_found(self):
        """pi appends its `bashComponent` to the same container while a lane
        streams, so live output can sit BELOW the hint and push it far from the
        end of the capture. A distance-from-the-bottom bound would hide the LIVE
        container and turn a queued message into a failure (and a duplicate)."""
        filler = [f"bash output {i}" for i in range(150)]
        screen = "\n".join([*SCREEN_QUEUED_MID_TURN.splitlines(), *filler])
        self.assertEqual(cd.pending_turn_identity(screen, PROBE), PROBE)
        self.assertTrue(cd.pending_turn_matches(screen, PROBE, self.BEFORE))

    def test_a_QUEUE_WHOSE_HINT_IS_TRUNCATED_is_unparsed_fail_closed(self):
        """On a narrow pane pi's OWN hint line is right-truncated, so the container
        cannot be located: the queue is unobservable, not absent. The composer reads
        empty, so recovery must NOT re-send (that would queue a second copy)."""
        narrow = SCREEN_QUEUED_MID_TURN_NARROW_HINT
        self.assertEqual(cd._pending_entries(narrow), [])
        self.assertIsNone(cd.pending_turn_identity(narrow, PROBE))
        self.assertTrue(cd.pending_queue_unparsed(narrow))
        self.assertTrue(cd.composer_empty(narrow))
        self.assertEqual(cd.recovery_action(narrow, cd.fingerprint(PROBE), PROBE), cd.R_RELEASE)

    def test_a_parsed_queue_is_not_reported_as_unparsed(self):
        """The fail-closed guard must not fire when the container DID parse: that
        would strand a genuinely lost send whenever another lane's work was queued."""
        self.assertFalse(cd.pending_queue_unparsed(SCREEN_QUEUED_MID_TURN))
        self.assertFalse(cd.pending_queue_unparsed(SCREEN_IDLE_READY))
        self.assertFalse(cd.pending_queue_unparsed(None))

    def test_a_stray_pending_SHAPED_line_is_NOT_a_queue(self):
        """A `Steering:`-shaped line in the lane's own transcript or bash output is
        scrollback, not a queue. If a bare such line counted, a genuinely lost send
        could never be re-sent on a pane that merely printed one."""
        stray = SCREEN_IDLE_READY.replace(
            "/private/tmp",
            " Steering: continue with the migration\n/private/tmp",
        )
        self.assertFalse(cd.pending_queue_unparsed(stray))
        self.assertTrue(cd.composer_empty(stray))
        self.assertEqual(cd.recovery_action(stray, cd.fingerprint(PROBE), PROBE), cd.R_RESEND)

    def test_NON_ADJACENT_stray_shapes_are_not_a_queue(self):
        """Both pieces of the unparsed-queue evidence are required ADJACENT, as in
        the real container: two unrelated scrollback lines (a `Steering:` line here,
        an `↳ … ...` line elsewhere) must not strand a genuinely lost send."""
        filler = "\n".join(f"line {i}" for i in range(20))
        screen = SCREEN_IDLE_READY.replace(
            "/private/tmp",
            " Steering: continue with the migration\n" + filler + "\n ↳ see the docs...\n/private/tmp",
        )
        self.assertFalse(cd.pending_queue_unparsed(screen))
        self.assertEqual(
            cd.recovery_action(screen, cd.fingerprint(PROBE), PROBE), cd.R_RESEND
        )

    def test_a_FULL_hint_with_no_entries_is_NOT_a_queue(self):
        """A hint line whose container is EMPTY means pi holds no queued message,
        so suppressing the resend there would strand a lost send."""
        no_entries = "\n".join(
            row for row in SCREEN_QUEUED_MID_TURN.splitlines() if "Steering:" not in row
        )
        self.assertFalse(cd.pending_queue_unparsed(no_entries))
        self.assertEqual(
            cd.recovery_action(no_entries, cd.fingerprint(PROBE), PROBE), cd.R_RESEND
        )

    def test_an_ARBITRARY_arrow_line_does_not_forge_a_container(self):
        """SECURITY. The bound requires pi's OWN hint text, not just the `↳` glyph:
        a lane can print `↳` freely, but a `Steering: <our message>` line above an
        arbitrary arrow line must never be read as a queued submission."""
        forged = SCREEN_QUEUED_MID_TURN.replace(
            "↳ Option+Up to edit all queued messages", "↳ see docs/notes.md"
        )
        self.assertIsNone(cd.pending_turn_identity(forged, PROBE))
        self.assertFalse(cd.pending_turn_matches(forged, PROBE, self.BEFORE))

    def test_a_container_that_has_SCROLLED_UP_is_STILL_the_message(self):
        """A stale frame is rejected by NOVELTY, not by a distance bound: the same
        container already present in the pre-send capture is not a new submission,
        even though it is on screen."""
        stale = SCREEN_QUEUED_MID_TURN
        self.assertFalse(cd.pending_turn_matches(stale, PROBE, stale))

    def test_a_Steering_shaped_line_in_SCROLLBACK_is_not_a_queue(self):
        """SECURITY. A capture contains the whole lane: transcript, model output,
        bash output (which pi even renders into the same container). Only the
        pending CONTAINER — the contiguous entries directly above the
        `\u21b3 \u2026 to edit all queued messages` hint pi renders under the same
        guard — is evidence. A `Steering:` line anywhere else must never confirm a
        dispatch."""
        filler = [f"line {i}" for i in range(60)]
        # (a) the line in an ordinary transcript, with no hint anywhere
        no_hint = "\n".join([*filler[:30], f"Steering: {PROBE}", *filler[:30]])
        self.assertIsNone(cd.pending_turn_identity(no_hint, PROBE))
        self.assertFalse(cd.pending_turn_matches(no_hint, PROBE, self.BEFORE))
        # (b) a hint IS present, but the matching line is separated from it by other
        #     output, so it is not part of the container run
        separated = "\n".join(
            [f"Steering: {PROBE}", *filler, "\u21b3 ctrl+e to edit all queued messages"]
        )
        self.assertIsNone(cd.pending_turn_identity(separated, PROBE))
        self.assertFalse(cd.pending_turn_matches(separated, PROBE, self.BEFORE))
        # (c) a stray `\u21b3` line BELOW the real container (bash output is appended
        #     to the same container while the lane streams) must not hide the
        #     container's real hint — the search scans hints newest-first
        below = SCREEN_QUEUED_MID_TURN.replace(
            "3.8%/700k (auto)",
            "\u21b3 stray tool-output arrow\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n3.8%/700k (auto)",
        )
        self.assertEqual(cd.pending_turn_identity(below, PROBE), PROBE)
        self.assertTrue(cd.pending_turn_matches(below, PROBE, self.BEFORE))

    def test_a_SHORT_pending_line_WITHOUT_an_ellipsis_does_not_block_the_resend(self):
        """`Steering: continue` while we send `continue with the migration` is a
        DIFFERENT queued message, not a truncation of ours, so it must not suppress
        the resend — otherwise a lost send is undeliverable whenever the lane holds
        any head-sharing nudge."""
        fp = cd.fingerprint(PROBE)
        message = PROBE + " and then some more"
        screen = SCREEN_QUEUED_MID_TURN.replace(PROBE, PROBE[:12])
        self.assertTrue(cd.composer_empty(screen))
        self.assertFalse(cd.pending_turn_ambiguous(screen, message))
        self.assertEqual(cd.recovery_action(screen, fp, message), cd.R_RESEND)

    def test_an_UNRELATED_pending_line_does_not_block_the_resend(self):
        """THE #5983 UNDER-DELIVERY FINDING. A pending line for a DIFFERENT
        message shares no head with ours, so our message is NOT queued and a
        resend is the recovery that delivers it. The first revision suppressed
        the resend for ANY pending line, leaving a send lost during another
        lane's queued work permanently undeliverable."""
        fp = cd.fingerprint(PROBE)
        screen = SCREEN_QUEUED_MID_TURN.replace(PROBE, "an unrelated brief")
        self.assertTrue(cd.composer_empty(screen))
        self.assertFalse(cd.pending_turn_ambiguous(screen, PROBE))
        self.assertEqual(cd.recovery_action(screen, fp, PROBE), cd.R_RESEND)


#: A REAL, unedited `cmux list-workspaces --json --id-format both` capture
#: (2026-09-20), trimmed to the fields the resolver reads. Embedding the real
#: shape is the point: `--id-format uuids` OMITS `ref` entirely, so a fixture
#: that hand-writes `ref` hides the fact that ref-addressed dispatch is broken.
REAL_PAYLOAD = json.dumps(
    {
        "window_ref": "window:1",
        "workspaces": [
            {
                "ref": "workspace:45",
                "id": "60359FBC-A318-44B4-B83A-2AF3D8C8888E",
                "index": 0,
                "latest_submitted_message": "[B4] Unit VOID by evidence, not blocked. #4013+#3834",
                "latest_submitted_at": "2026-09-20T20:17:52.583Z",
                "latest_conversation_message": "[B4] Unit VOID by evidence, not blocked. #4013+#3834",
            },
            {
                "ref": "workspace:79",
                "id": "60B48156-B1C1-443F-8343-80E4EC20A489",
                "index": 1,
                "latest_submitted_message": "WRAPPER-POSITIVE-4292 :: reply with the single word WRAPOK",
                "latest_submitted_at": "2026-09-20T20:09:39.089Z",
                "latest_conversation_message": "WRAPPER-POSITIVE-4292 :: reply with the single word WRAPOK",
            },
        ],
    }
)


class RecordingCmux(cd.Cmux):
    """The REAL `Cmux` transport with its subprocess replaced by a recorder."""

    def __init__(self, payload: str = REAL_PAYLOAD, rc: int = 0) -> None:
        super().__init__("cmux")
        self.payload = payload
        self.rc = rc
        self.argv: list[list[str]] = []

    def run(self, argv: list[str], timeout: float | None = None) -> cd.CmuxResult:
        self.argv.append(list(argv))
        return cd.CmuxResult(self.rc, self.payload, "" if self.rc == 0 else "boom")


class TestCmuxTransport(unittest.TestCase):
    def test_list_workspaces_asks_for_a_format_that_carries_ref(self):
        """`--id-format uuids` omits `ref` (verified against cmux), which made
        every ref-addressed dispatch fail closed as unknown-workspace."""
        cmux = RecordingCmux()
        cmux.list_workspaces_json()
        argv = cmux.argv[-1]
        self.assertIn("list-workspaces", argv)
        self.assertIn("both", argv)
        self.assertNotIn("uuids", argv)

    def test_real_capture_resolves_by_ref_and_by_uuid(self):
        cmux = RecordingCmux()
        payload = cmux.list_workspaces_json().out
        self.assertEqual(
            cd.workspace_entry(payload, "workspace:79")["id"],
            "60B48156-B1C1-443F-8343-80E4EC20A489",
        )
        self.assertEqual(
            cd.workspace_entry(payload, "60B48156-B1C1-443F-8343-80E4EC20A489")["ref"],
            "workspace:79",
        )

    def test_transport_failure_raises_instead_of_reporting_unknown_workspace(self):
        dispatcher = cd.Dispatcher(RecordingCmux(rc=1))
        with self.assertRaises(cd.CmuxTransportError):
            dispatcher.workspace_state("workspace:79")

    def test_send_reports_a_transport_error_when_cmux_is_unreachable(self):
        result = cd.Dispatcher(RecordingCmux(rc=1)).send_message(
            "workspace:79", PROBE, appear_timeout=0.0
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "transport-error")

    def test_read_screen_threads_the_surface_through(self):
        cmux = RecordingCmux()
        cmux.read_screen("workspace:79", surface="surface:5")
        argv = cmux.argv[-1]
        self.assertIn("--surface", argv)
        self.assertIn("surface:5", argv)

    def test_malformed_json_is_not_an_error_but_yields_no_entry(self):
        self.assertEqual(cd.parse_workspaces("not json"), {})
        self.assertIsNone(cd.workspace_entry("not json", "workspace:70"))


class TestPaneSessionIdTransport(unittest.TestCase):
    """The authoritative-field rule, pinned at the transport.

    `Cmux.pane_session_id` is the only code that reads cmux's binding and every
    dispatcher test overrides it — so without these the rule that `resume_binding`
    is authoritative and `restore_record` is NOT had no test at all: replacing the
    whole method with `return None` left all 188 tests green (#7913 review).
    """

    @staticmethod
    def _stub(payload: str, rc: int = 0) -> cd.Cmux:
        return RecordingCmux(payload=payload, rc=rc)

    def test_reads_the_resume_binding_checkpoint_id(self):
        cmux = self._stub('{"resume_binding": {"checkpoint_id": "aaaa-bbbb"}}')
        self.assertEqual(cmux.pane_session_id("workspace:79"), "aaaa-bbbb")
        argv = cmux.argv[-1]
        self.assertEqual(argv[:3], ["surface", "resume", "show"])
        self.assertIn("--workspace", argv)

    def test_the_probe_names_the_TARGET_surface(self):
        # A workspace can hold several surfaces bound to DIFFERENT sessions, and
        # `surface resume show` with no `--surface` answers for the SELECTED one —
        # so omitting the flag lets a sibling surface's live pi certify a blind
        # write into the target pane (#7913 review, P1). This is the only place
        # the argv is observable: the dispatcher-level fake receives the surface
        # as an argument whether or not the real implementation forwards it.
        cmux = self._stub('{"resume_binding": {"checkpoint_id": "aaaa-bbbb"}}')
        self.assertEqual(cmux.pane_session_id("workspace:79", "surface:16"), "aaaa-bbbb")
        argv = cmux.argv[-1]
        self.assertIn("--surface", argv)
        self.assertEqual(argv[argv.index("--surface") + 1], "surface:16")

    def test_the_probe_omits_the_flag_when_no_surface_is_named(self):
        cmux = self._stub('{"resume_binding": {"checkpoint_id": "aaaa-bbbb"}}')
        cmux.pane_session_id("workspace:79")
        self.assertNotIn("--surface", cmux.argv[-1])

    def test_a_stale_restore_record_is_never_used(self):
        # Measured 2026-10-08: a workspace printed "No resume binding" from the
        # text form while `restore_record` still carried an old id. Reading that
        # field attributes ANOTHER session's transcript to this pane.
        cmux = self._stub('{"restore_record": {"checkpoint_id": "stale-1111"}}')
        self.assertIsNone(cmux.pane_session_id("workspace:79"))

    def test_a_plain_string_binding_is_not_an_answer(self):
        # Strict on purpose: cmux does not emit a bare string, and accepting extra
        # shapes here would let this tool and `fleet_state.pane_bindings` — which
        # reads `checkpoint_id` only — disagree about whether a pane is bound
        # (#7913 review). An unrecognised shape is not an answer.
        cmux = self._stub('{"resume_binding": "cccc-dddd"}')
        self.assertIsNone(cmux.pane_session_id("workspace:79"))

    def test_a_reply_that_is_an_answer_but_carries_no_id_is_None(self):
        # cmux ANSWERED and the pane has no binding — a definitive fact, and the
        # only negative outcome that is safe to memoize for the dispatch.
        for payload in ('{"resume_binding": null}', '{"resume_binding": {}}', "{}"):
            with self.subTest(payload=payload):
                self.assertIsNone(self._stub(payload).pane_session_id("workspace:79"))

    def test_an_unaskable_probe_is_NOT_the_same_as_no_binding(self):
        # Distinct because they mean opposite things downstream: one is a fact,
        # the other is a transient failure that must be re-asked (#7913 review).
        for payload in ("not json", "[]"):
            with self.subTest(payload=payload):
                self.assertIs(
                    self._stub(payload).pane_session_id("workspace:79"),
                    cd.PROBE_UNAVAILABLE,
                )

    def test_a_nonzero_exit_is_unaskable_not_a_guess(self):
        # The payload is deliberately PARSEABLE: with an empty one the JSON guard
        # would return the same value, and the rc check would have no guard at all
        # (#7913 review — this test used to pass with the rc check deleted).
        cmux = self._stub('{"resume_binding": {"checkpoint_id": "aaaa-bbbb"}}', rc=1)
        self.assertIs(cmux.pane_session_id("workspace:79"), cd.PROBE_UNAVAILABLE)


# --------------------------------------------------------------------------- #
# Hermetic dispatcher tests — the failure physics, end to end
# --------------------------------------------------------------------------- #


#: The session id the #7913 fixture files are named after, and the id the fake
#: panes are resume-bound to. A transcript is evidence only for the pane whose OWN
#: session id matches (`tools/cmux_dispatch.py::session_file_for`).
SESSION_7913 = "00000000-0000-4000-8000-000000000000"


_MODULE_SESSIONS: dict[str, object] = {}


class FakeCmux:
    """A cmux whose `send` ALWAYS returns 0, exactly like the real one.

    Models the pane's actual state machine so the recovery logic can be tested
    against the real failure modes.
    """

    def __init__(
        self,
        boot_block: bool = False,
        boot_polls: int = 1,
        prompt_eats_prefix: int = 0,
        enter_is_noop: int = 0,
        never_consumes: bool = False,
        submit_lag_polls: int = 0,
        screen_unreadable: bool = False,
        drops_message: bool = False,
        no_rules: bool = False,
        queued_turn: bool = False,
        pre_queued: str = "",
        drop_first_send: bool = False,
        fail_read_indices: frozenset[int] | set[int] = frozenset(),
        shallow_screen_without_queue: bool = False,
        narrow_unparsed_queue: bool = False,
        menu_open: bool = False,
        pre_typed: str = "",
        cwd: str = "",
        session_id: str = SESSION_7913,
        bindings_by_surface: dict | None = None,
        selected_surface: str = "surface:15",
    ) -> None:
        self.state = "boot_block" if boot_block else "ready"
        self.boot_polls = boot_polls
        self.prompt_eats_prefix = prompt_eats_prefix
        self.enter_is_noop = enter_is_noop
        self.never_consumes = never_consumes
        #: How many `list-workspaces` reads it takes for a real submission to
        #: become visible. cmux writes `latest_submitted_*` at the turn boundary,
        #: so a reader can observe a submission as absent for a beat.
        self.submit_lag_polls = submit_lag_polls
        self.screen_unreadable = screen_unreadable
        #: cmux accepts the bytes but they surface NOWHERE — not in the composer,
        #: not as a submitted turn. Models a send lost by the boot-block prompt
        #: (or any silent drop). The fail-closed guard: this must never confirm.
        self.drops_message = drops_message
        #: Render the composer WITHOUT its enclosing horizontal rules, so
        #: `composer_region` returns None ("cannot tell"). A pane in that state
        #: must never be read as a CONFIRMED DELIVERY (a false `queued` here would
        #: set `delivered=True` and suppress the re-send of a lost message).
        self.no_rules = no_rules
        #: A MID-TURN pane that ACCEPTED the message into pi's pending queue: the
        #: `Steering: <text>` display is up, the composer is EMPTY, and the turn
        #: boundary has not arrived — so `latest_submitted_*` never moves and the
        #: composer holds nothing.
        self.queued_turn = queued_turn
        #: A pending line ALREADY on the pane before the send (a repeat dispatch of
        #: the same brief, or a stale scrollback line). Must NOT confirm THIS send.
        self.queued = pre_queued
        #: Drop only the FIRST message send (the resend then lands), so the
        #: resend-as-recovery path can be exercised end to end.
        self.drop_first_send = drop_first_send
        #: 1-based `read_screen` call indices to fail, so a specific read can be
        #: made unreadable without blinding the pane for the whole dispatch.
        self.fail_read_indices = set(fail_read_indices)
        #: A SHALLOW read (the readiness probe's window) that omits the pending
        #: container while the DEEP read carries it. Models a stale queue entry
        #: that only the deep window can see — the depth-mixing trap.
        self.shallow_screen_without_queue = shallow_screen_without_queue
        #: A live queue that the matcher CANNOT observe: pi's hint line is itself
        #: right-truncated on a narrow pane, so no container parses. Recovery must
        #: not treat the empty composer as licence to re-send.
        self.narrow_unparsed_queue = narrow_unparsed_queue
        #: AN OPEN COMPLETION MENU (#7913, second root). While it is open the menu
        #: renders a `→ ` selected row below the composer's bottom rule and a bare
        #: Enter is consumed by the menu (accepts the highlighted completion) — it
        #: NEVER submits. Escape dismisses it; Ctrl-U clears the line but does NOT
        #: close the menu (verified against the installed pi-tui editor).
        self.menu_open = menu_open
        #: The workspace cwd `cmux list-workspaces --json` reports. It is the FAST
        #: path the non-pane session-mtime probe (#7913) searches, but it is never a
        #: substitute for the pane's own session id: a transcript is evidence only
        #: when its filename carries THIS pane's binding.
        self.cwd = cwd
        #: The id `cmux surface resume show` reports as the pane's
        #: `resume_binding.checkpoint_id`; `""` models an unbindable pane.
        self.session_id = session_id
        #: Optional `{surface: session_id}` — models a workspace whose surfaces
        #: are bound to DIFFERENT sessions, which is how a workspace-scoped probe
        #: certified a send into the wrong pane (#7913 review, P1).
        self.bindings_by_surface = bindings_by_surface or {}
        #: The surface cmux reports for this workspace when the caller names none.
        self.selected_surface = selected_surface
        self.read_calls = 0
        self.lag_remaining = 0
        self.visible: str | None = None

        self.pending = pre_typed    # text in the composer, unsent
        self.submitted: list[str] = []
        self.sends: list[str] = repr
        self.sent_log: list[str] = []
        self.list_calls = 0
        self.eaten: list[str] = []

    # -- state -------------------------------------------------------------- #

    def _tick(self) -> None:
        if self.state == "booting":
            self.boot_polls -= 1
            if self.boot_polls <= 0:
                self.state = "ready"

    # -- cmux surface ------------------------------------------------------- #

    def pane_session_id(self, workspace: str, surface: str | None = None):
        # SURFACE-SCOPED, like the real cmux: with no `--surface` cmux answers for
        # the workspace's SELECTED surface, and a workspace can hold several
        # surfaces bound to DIFFERENT sessions (#7913 review, P1).
        if self.bindings_by_surface:
            return self.bindings_by_surface.get(surface or self.selected_surface)
        return self.session_id or None

    def list_workspaces_json(self) -> cd.CmuxResult:
        self.list_calls += 1
        self._tick()
        if self.lag_remaining > 0:
            self.lag_remaining -= 1          # the artifact has not caught up yet
        else:
            self.visible = self.submitted[-1] if self.submitted else None
        entry = {
            "ref": "workspace:99",
            "id": "FAKE-0000",
            "current_directory": self.cwd,
            "latest_submitted_message": self.visible,
            "latest_submitted_at": (
                f"2026-09-20T19:48:{len(self.submitted):02d}.000Z"
                if self.visible
                else None
            ),
            "latest_conversation_message": self.visible,
        }
        return cd.CmuxResult(
            0, json.dumps({"window_ref": "window:1", "workspaces": [entry]})
        )

    def read_screen(
        self, workspace: str, lines: int = 80, surface: str | None = None
    ) -> cd.CmuxResult:
        if self.screen_unreadable:
            return cd.CmuxResult(1, "", "cmux read-screen: command timed out")
        self.read_calls += 1
        if self.read_calls in self.fail_read_indices:
            return cd.CmuxResult(1, "", "cmux read-screen: command timed out")
        if self.shallow_screen_without_queue and lines <= cd.DEFAULT_SCREEN_LINES:
            return cd.CmuxResult(0, SCREEN_IDLE_READY)
        self._tick()
        if self.state == "boot_block":
            return cd.CmuxResult(0, SCREEN_BOOT_BLOCK)
        if self.state == "booting":
            return cd.CmuxResult(0, "[loop-enforcer] loaded\n[verification-gate] loaded\n")
        screen = SCREEN_IDLE_READY
        if self.narrow_unparsed_queue:
            return cd.CmuxResult(0, SCREEN_QUEUED_MID_TURN_NARROW_HINT)
        if self.queued:
            return cd.CmuxResult(
                0,
                SCREEN_QUEUED_MID_TURN.replace(
                    "DISPATCH-PROBE-BOOTBLOCK-4292 :: reply with the single word ACK4292",
                    self.queued,
                ),
            )
        if self.pending:
            if self.no_rules:
                screen = (
                    self.pending
                    + "\n/private/tmp\n0.0%/700k (auto)"
                    "  (deepseek) deepseek-flash \u2022 high\n"
                )
            else:
                screen = SCREEN_COMPOSING_UNSENT.replace(
                    "DISPATCH-PROBE-BOOTBLOCK-4292 :: reply with the single word ACK4292",
                    self.pending,
                )
        if self.menu_open:
            # The autocomplete list renders below the composer's bottom rule, with
            # the selected candidate marked `→ ` (`SelectList.renderItem`).
            screen = screen.replace(
                "\n0.0%/700k", "\n \u2192 ci-checks/\n0.0%/700k", 1
            )
        return cd.CmuxResult(0, screen)

    def send_text(self, workspace: str, text: str, surface: str | None = None) -> cd.CmuxResult:
        self.sent_log.append(text)
        self._tick()
        if text == "\\n":
            return self.send_enter(workspace, surface)
        if self.state == "boot_block":
            # THE BOOT-BLOCK EAT. The prompt's `stdin.once("data")` consumes the
            # bytes as its keypress. A prefix may be eaten, the rest survives
            # into the composer as a corrupted turn.
            eaten, remainder = text[: self.prompt_eats_prefix] or text, ""
            if self.prompt_eats_prefix:
                eaten = text[: self.prompt_eats_prefix]
                remainder = text[self.prompt_eats_prefix :]
            self.eaten.append(eaten)
            self.state = "booting"
            self.pending += remainder
            return cd.CmuxResult(0, "OK")
        if self.drop_first_send:
            self.drop_first_send = False
            return cd.CmuxResult(0, "OK")
        if self.drops_message:
            return cd.CmuxResult(0, "OK")
        if self.queued_turn or self.narrow_unparsed_queue:
            # pi ACCEPTS the submission into its pending queue. The editor is
            # cleared, so the text is NOT in the composer — it is in the pending
            # display, and it becomes a turn only when the current turn ends.
            self.queued = text
            return cd.CmuxResult(0, "OK")
        self.pending += text
        return cd.CmuxResult(0, "OK")

    def send_enter(self, workspace: str, surface: str | None = None) -> cd.CmuxResult:
        self.sent_log.append("\\n")
        self._tick()
        if self.menu_open:
            # Enter ACCEPTS the highlighted completion — it never submits. This is
            # the exact keystroke the menu eats (#7913).
            return cd.CmuxResult(0, "OK")
        if self.enter_is_noop > 0:
            self.enter_is_noop -= 1
            return cd.CmuxResult(0, "OK")  # rc=0, but nothing is submitted
        if self.state == "boot_block":
            self.state = "booting"
            return cd.CmuxResult(0, "OK")
        if self.queued_turn or self.narrow_unparsed_queue:
            # The turn boundary has not arrived: pi holds the submission in its
            # queue and a bare Enter cannot release it.
            return cd.CmuxResult(0, "OK")
        if self.pending and not self.never_consumes:
            self.submitted.append(self.pending)
            self.pending = ""
            self.lag_remaining = self.submit_lag_polls
        return cd.CmuxResult(0, "OK")


    def send_ctrl_u(self, workspace: str, surface: str | None = None) -> cd.CmuxResult:
        self.sent_log.append("\x15")
        self.pending = ""   # deleteToLineStart — does NOT close the menu
        return cd.CmuxResult(0, "OK")

    def send_escape(self, workspace: str, surface: str | None = None) -> cd.CmuxResult:
        self.sent_log.append("\x1b")
        self.menu_open = False   # tui.select.cancel — dismiss the menu
        return cd.CmuxResult(0, "OK")


#: A LIVE pane read at the RECOVERY depth (`RECOVERY_SCREEN_LINES`), whose
#: transcript carries a shell-prompt-shaped line EXACTLY ONE LINE OUTSIDE the
#: gate's window (`DEFAULT_SCREEN_LINES`) and ends in a stats line with NO pwd line
#: above it. Because no footer BLOCK exists (`_footer_stats_end` == -1),
#: `shell_prompt_below_footer` falls back to scanning the WHOLE capture and sees
#: that old prompt — so the SAME pane is not ready at 300 lines and ready at 80.
#:
#: The one-line-outside placement is load-bearing. With the prompt deeper in the
#: capture (say 100 lines from the end) the test passes for ANY slice that drops
#: it, including a wrong 100-line window; here only a slice of `DEFAULT_SCREEN_LINES`
#: or less drops the prompt, so the test pins the window it claims to pin.
DEEP_READ_WITH_A_TORN_FOOTER = (
    "$ uv run pytest tests/ -q\n"
    + ("filler line\n" * 79)
    + "[tortoise-capture] Hosted capture FAILED (HTTP 402) \u2014 kept a JSONL "
    "record deepseek-flash \u2022 high\n"
)


class DeepReadTornFooterCmux(FakeCmux):
    """A pane whose RECOVERY-depth read carries an old shell prompt above the
    gate's window, with no footer block (see `DEEP_READ_WITH_A_TORN_FOOTER`).
    Shallower reads behave normally, so the gate and the pre-send re-assert both
    pass normally and only the recovery re-send sees the wide capture."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deep_reads = 0

    def read_screen(self, workspace, lines=80, surface=None):
        # Delegate FIRST: the base fake advances its state machine, its read
        # counter and its failure injection on every read, and the recovery
        # decision depends on that state. Only the CONTENT of the re-read taken
        # immediately before the re-send is substituted (deep read #3: the first
        # two are the recovery decision's own probes, which must see the pane as
        # it really is or the decision picks `release` instead of `resend`). That
        # ordering is the point of the test, not an accident of it: the dispatcher
        # re-reads precisely because the pane can CHANGE during the grace window,
        # and the change modelled here is the footer being torn mid-redraw.
        result = super().read_screen(workspace, lines=lines, surface=surface)
        if lines >= cd.RECOVERY_SCREEN_LINES:
            self.deep_reads += 1
            if self.deep_reads >= 3 and result.rc == 0:
                return cd.CmuxResult(0, DEEP_READ_WITH_A_TORN_FOOTER)
        return result


class BareShellCmux(FakeCmux):
    """A pane that is a BARE LOGIN SHELL: readable, but it never draws pi's footer
    and carries no boot-block marker. The #7158 target."""

    def read_screen(self, workspace, lines=80, surface=None):
        return cd.CmuxResult(0, SCREEN_BARE_SHELL)


class BareShellAtTheGateThenReadyCmux(FakeCmux):
    """A pane that is a BARE LOGIN SHELL on the GATE's probe and a live pi after.

    This exists to pin the GATE, which `BareShellCmux` cannot do. `BareShellCmux`
    is bare on EVERY read, so the pre-send re-assert refuses it too and the
    refusal is reported identically whichever check made it — measured by
    mutation: with the gate's fail-closed branch disabled (`if not ready:` never
    refusing), 133 of 133 tests still passed, because the pre-send re-assert
    produced the same status and the same zero writes. Here the pane is bare ONLY
    for the gate's probe, so if the gate stops refusing, the send proceeds and the
    bytes reach a bare shell — which is the failure #7158 exists to prevent.
    """

    def read_screen(self, workspace, lines=80, surface=None):
        self.read_calls += 1
        if self.read_calls <= 1:
            return cd.CmuxResult(0, SCREEN_BARE_SHELL)
        return cd.CmuxResult(0, SCREEN_IDLE_READY)


class MenuOpensOnOurTextCmux(FakeCmux):
    """A pane where typing OUR brief OPENS a completion menu (#7913, 2nd root).

    Models the real trigger: the brief carries a completion trigger character (or
    a Tab-like completion), so the autocomplete list appears AFTER the text is
    typed and the subsequent Enter is consumed by the menu. The pre-send hygiene
    cannot see this menu (it did not exist at read time); the RECOVERY must.
    """

    def send_text(self, workspace, text, surface=None):
        result = super().send_text(workspace, text, surface)
        if text == PROBE:
            self.menu_open = True
        return result


class FakeClock:
    """A clock whose `sleep` advances it, so bounded-wait loops terminate in
    `timeout / poll` iterations instead of spinning forever."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(seconds, 0.001)


def _dispatcher(fake: FakeCmux) -> cd.Dispatcher:
    clock = FakeClock()
    return cd.Dispatcher(
        fake, sleep=clock.sleep, now=clock.now, log=lambda _m: None
    )


class TestDispatcherRecovery(unittest.TestCase):
    def _send(self, fake, **kwargs):
        return _dispatcher(fake).send_message(
            "workspace:99", PROBE, label="B4", **kwargs
        )

    def test_happy_path_no_recovery(self):
        fake = FakeCmux()
        result = self._send(fake)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, "consumed")
        self.assertEqual(result.recoveries, [])
        self.assertEqual(fake.submitted, [PROBE])

    def test_boot_block_is_dismissed_before_sending_so_nothing_is_eaten(self):
        fake = FakeCmux(boot_block=True, boot_polls=1)
        result = self._send(fake)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(fake.eaten, [], "text must never be fed to the prompt")
        self.assertEqual(fake.submitted, [PROBE])

    def test_race_the_composer_holds_the_text_and_release_recovers_it(self):
        # The 2026-09-17 incident: the Enter does not submit; the text sits in
        # the composer. Correct recovery is a bare Enter, NOT a re-send. An
        # UNSENT composer is NEVER a success — pi clears the editor before it
        # queues anything, so text still here has not been accepted (#5979).
        fake = FakeCmux(enter_is_noop=1)
        result = self._send(fake, consume_timeout=0.0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, "consumed")
        self.assertEqual(result.recoveries, [cd.R_RELEASE])
        self.assertEqual(fake.submitted, [PROBE], "must not duplicate the message")

    def test_release_recovers_when_the_composer_is_unidentifiable(self):
        # Pins the RELEASE OUTCOME for a no-rules pane (the 2026-09-17 race): the
        # composer region cannot be read, so no resend is safe, and the bare Enter
        # is what submits the message. NOTE: the R_RELEASE here comes from the
        # `text_on_screen` branch — the unreadable-composer property itself is
        # pinned separately by `test_unidentifiable_composer_never_re_sends`.
        fake = FakeCmux(enter_is_noop=1, no_rules=True)
        result = self._send(fake, consume_timeout=0.0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, "consumed")
        self.assertEqual(result.recoveries, [cd.R_RELEASE])
        self.assertEqual(fake.submitted, [PROBE], "must not duplicate the message")

    def test_prompt_eats_text_then_full_message_is_re_sent(self):
        # Send lands while the prompt is up AND the pre-send gate was skipped
        # (simulated by the prompt appearing only after the gate AND the pre-send
        # baseline read — the pre-send read now re-asserts readiness, #7158).
        fake = FakeCmux(boot_block=True, boot_polls=1)
        fake.prompt_eats_prefix = 0
        dispatcher = _dispatcher(fake)
        # Force the readiness gate and the baseline read to see a ready pane.
        original = dispatcher.screen
        lies = [2]

        def screen_before_prompt(ws, surface=None, lines=None):
            if lies[0] > 0:
                lies[0] -= 1
                return SCREEN_IDLE_READY
            dispatcher.screen = original
            if lines is None:
                return original(ws, surface)
            return original(ws, surface, lines=lines)

        dispatcher.screen = screen_before_prompt
        result = dispatcher.send_message("workspace:99", PROBE, consume_timeout=0.0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(fake.submitted[-1], PROBE)

    def test_truncated_turn_is_detected_and_the_full_brief_is_delivered(self):
        # THE CORRUPTION CASE: the prompt ate the prefix; "292" was submitted as
        # a real turn. Verification must NOT accept it, and recovery must
        # deliver the whole brief.
        fake = FakeCmux()
        fake.submitted.append("292")  # the corrupted remnant
        result = self._send(fake, consume_timeout=0.0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(
            fake.submitted, ["292", PROBE], "the full brief must be delivered"
        )

    def test_lagging_artifact_does_not_cause_a_duplicate_re_send(self):
        """cmux writes `latest_submitted_*` at the turn boundary, so the reader
        can see a landed message as absent for a beat. Re-sending on that first
        miss would duplicate the message — the one recovery whose failure mode is
        worse than not recovering. The pre-recovery grace window must absorb it.
        """
        fake = FakeCmux(submit_lag_polls=2)
        result = self._send(fake, consume_timeout=0.0)
        self.assertTrue(result.ok, result.detail)
        self.assertTrue(result.delivered, result.detail)
        self.assertIn("grace window", result.detail)
        self.assertEqual(fake.submitted, [PROBE], "must not duplicate on lag")

    def test_grace_window_covers_a_full_consume_budget(self):
        """A submission that is real but slow to appear must never be mistaken for
        a lost one. With `consume_timeout > RECOVERY_GRACE_TIMEOUT`, the grace must
        scale, or a lag longer than the fixed 6s window produces a duplicate."""
        # 15 polls: longer than the FIRST 20s confirmation window (~10 polls at
        # poll=2s), so only a grace that scales with the consume budget reaches it.
        # A fixed 6s grace (3 polls) would fall short and duplicate the message.
        fake = FakeCmux(submit_lag_polls=15)
        result = self._send(fake, consume_timeout=20.0)
        self.assertTrue(result.ok, result.detail)
        self.assertTrue(result.delivered, result.detail)
        self.assertIn("grace window", result.detail)
        self.assertEqual(fake.submitted, [PROBE], "must not duplicate on lag")

    def test_ok_always_implies_delivered_on_every_success_path(self):
        """#7885's field contract: `delivered` = the bytes reached pi's hands, so
        `ok ⇒ delivered` must hold on EVERY success path. The pre-recovery grace
        window once set `ok`/`status="consumed"` without `delivered`, so a consumed
        send carried `delivered=False` and invited the duplicate re-send #5979
        forbids. This walks all three outcomes."""
        # the grace-window CONSUMED path (the one that was wrong)
        lag = self._send(FakeCmux(submit_lag_polls=2), consume_timeout=0.0)
        self.assertTrue(lag.ok, lag.detail)
        self.assertTrue(lag.delivered, lag.detail)
        # the QUEUED path: delivered WITHOUT ok
        queued = self._send(FakeCmux(queued_turn=True), consume_timeout=0.0)
        self.assertFalse(queued.ok, queued.detail)
        self.assertTrue(queued.delivered, queued.detail)
        # a failure is NEITHER
        failed = self._send(FakeCmux(never_consumes=True), consume_timeout=0.0)
        self.assertFalse(failed.ok, failed.detail)
        self.assertFalse(failed.delivered, failed.detail)

    def test_never_consumed_fails_closed(self):
        # The message never becomes a turn AND never enters pi's pending queue:
        # it sits in the composer, unsent. The strict verdict must hold — the
        # refuted design reported this very screen as success, turning the
        # module's own UNSENT failure signature into a false positive.
        fake = FakeCmux(never_consumes=True)
        result = self._send(fake, consume_timeout=0.0, retries=2)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "sent-but-not-consumed")
        self.assertIn("sent-but-not-consumed", result.detail)
        self.assertEqual(result.attempts, 3)  # initial + 2 retries

    def test_busy_lane_pending_turn_is_delivered_but_not_consumed(self):
        # THE #5979 DEFECT, then the #7743 correction. `latest_submitted_message`
        # only advances at a turn boundary, so on a mid-turn lane the strict check
        # can never be satisfied: pi has ACCEPTED the message into its pending
        # queue (the `Steering:` display is up) and it becomes a turn when the
        # current turn ends. That is a DELIVERED message and the old verdict spent
        # the whole timeout reporting `sent-but-not-consumed` (#5979).
        #
        # #7743: it is NOT CONSUMED, and on a lane wedged inside its turn the queue
        # is drained NEVER. So delivery is carried (`delivered`) but success is not
        # (`ok` stays False, exit 4) — reporting this as a completed dispatch is
        # exactly the false positive #7743 names.
        fake = FakeCmux(queued_turn=True)
        result = self._send(fake, consume_timeout=0.0, retries=2)
        self.assertFalse(result.ok, "a queued-only send is NOT a completed dispatch")
        self.assertTrue(result.delivered, "it IS in pi's hands — do not re-send")
        self.assertEqual(result.status, "queued")
        self.assertIn("pending queue", result.detail)
        self.assertIn("NOT consumed", result.detail)
        self.assertEqual(result.attempts, 1, "no need to wait out the retries")
        self.assertEqual(fake.submitted, [], "queued is not yet consumed")
        self.assertEqual(fake.sent_log.count(PROBE), 1, "must never be re-sent")
        self.assertEqual(result.recoveries, [], "a queue is not a recovery")

    def test_eaten_message_is_not_queued_and_still_fails(self):
        # The boot-block prompt ate the pointer and nothing retained the text:
        # absent from BOTH the submitted message and the pending display, and
        # absent from the composer. Fail closed.
        fake = FakeCmux(drops_message=True)
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "sent-but-not-consumed")
        self.assertNotIn("queued", result.status)

    def test_a_PRE_EXISTING_identical_pending_line_does_not_confirm_a_lost_send(self):
        # The same brief was dispatched before and is STILL queued; THIS send's
        # bytes are lost. The pending line predates the send, so it is not
        # evidence about this send — fail closed, never a false success.
        fake = FakeCmux(drops_message=True, pre_queued=PROBE)
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "sent-but-not-consumed")

    def test_a_TRUNCATED_pre_existing_pending_line_does_not_trigger_a_duplicate_resend(self):
        """A mid-turn pane already holds OUR message as a TRUNCATED queue entry: the
        head is below the 40-char fingerprint (`text_on_screen` misses it) and
        at/above `PENDING_MIN_CHARS` (the ambiguity branch misses it). Novelty
        correctly refuses to CONFIRM it, and the recovery must not contradict that
        by re-sending — each retry would queue another copy. Exactly one
        `send_text` may happen."""
        fake = FakeCmux(
            drops_message=True, pre_queued="DISPATCH-PROBE-BOOTBLOCK-42..."
        )
        result = self._send(fake, consume_timeout=0.0, retries=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "sent-but-not-consumed")
        self.assertEqual(result.recoveries, [cd.R_RELEASE])
        self.assertEqual(fake.sent_log.count(PROBE), 1)

    def test_a_lost_send_is_resent_when_an_UNRELATED_message_is_queued(self):
        """The pane is mid-turn with another message queued and OUR first send is
        lost. `Steering: <other>` shares no head with our message, so our message
        is not queued and the resend is the recovery that delivers it. Suppressing
        the resend for ANY pending line left the dispatch undeliverable."""
        fake = FakeCmux(drop_first_send=True, pre_queued="an unrelated brief")
        result = self._send(fake, consume_timeout=0.0, retries=1)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, "consumed")
        self.assertEqual(result.recoveries, [cd.R_RESEND])
        self.assertEqual(fake.submitted, [PROBE])
        self.assertEqual(fake.sent_log.count(PROBE), 2)

    def test_the_recovery_re_send_judges_the_SAME_window_as_the_gate(self):
        """The recovery re-send reads `RECOVERY_SCREEN_LINES` (300) deep, and
        `shell_prompt_below_footer`'s no-footer-block fallback scans the WHOLE
        capture — so an unsliced re-assert can refuse a pane the gate just
        approved, reporting a shell prompt below a footer that was never drawn.
        That is a LOST delivery, in the one path that exists to RESCUE a delivery.
        The pre-send re-assert already slices to the gate's window for exactly this
        reason; this pins the recovery path to the same window."""
        fake = DeepReadTornFooterCmux(
            drop_first_send=True, pre_queued="an unrelated brief"
        )
        result = self._send(fake, consume_timeout=0.0, retries=1)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, "consumed")
        self.assertEqual(result.recoveries, [cd.R_RESEND])
        self.assertEqual(
            fake.sent_log.count(PROBE),
            2,
            "the re-send must happen: the old prompt is above the gate's window",
        )

    def test_an_unreadable_pre_send_baseline_is_refused(self):
        """Round-6 finding (TOCTOU): the pre-send read is the readiness re-check. If
        it fails twice the pane state is unknown, and writing blind into a pane that
        died after the gate passed would EXECUTE the brief. A refusal is
        recoverable; an executed brief is not. Mirrors the resend path, which
        already refuses on an unreadable fresh read."""
        # read #1 is the readiness probe; reads #2 and #3 are the baseline + retry.
        fake = FakeCmux(queued_turn=True, fail_read_indices={2, 3})
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(fake.sent_log, [], "no bytes may be written blind")

    def test_a_transient_baseline_read_failure_still_confirms_the_queue(self):
        """The pre-send baseline is read twice: a single transient `read-screen`
        failure must not cost the `queued` verdict. Read #1 is the readiness probe,
        #2 the baseline, #3 the retry."""
        fake = FakeCmux(queued_turn=True, fail_read_indices={2})
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertTrue(result.delivered, result.detail)
        self.assertFalse(result.ok, result.detail)
        self.assertEqual(result.status, "queued")

    def test_a_baseline_at_a_DIFFERENT_depth_than_the_confirmation_is_refused(self):
        """DEPTH-MIXING. The readiness probe reads a shallower window than the
        confirmation. The baseline is NEVER borrowed from that shallower capture (a
        pending line outside it would read as NOVEL, confirming a send whose bytes
        never landed), and with the deep baseline unreadable the send is refused
        outright — never written blind."""
        fake = FakeCmux(
            queued_turn=True,
            shallow_screen_without_queue=True,
            fail_read_indices={2, 3},
        )
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(fake.sent_log, [], "no bytes may be written blind")

    def test_a_MULTI_LINE_body_is_flattened_so_it_can_be_confirmed(self):
        """A newline would submit early in pi's one-line composer, and pi renders a
        queued submission as only its first line (not message-unique). `send_message`
        therefore flattens at the boundary, exactly as `_read_message` does, so the
        sent text, the fingerprint and the confirmation identity are one string."""
        fake = FakeCmux(queued_turn=True)
        result = _dispatcher(fake).send_message(
            "workspace:99", "line one\n\nline   two", label="B4",
            consume_timeout=0.0, retries=0,
        )
        flat = "line one line two"
        self.assertTrue(result.delivered, result.detail)
        self.assertEqual(result.status, "queued")
        self.assertEqual(fake.queued, flat)

    def test_an_UNPARSEABLE_queue_is_not_resent(self):
        """End to end: pi holds the submission but pi's hint line is truncated, so
        no container parses and the composer reads empty. The dispatcher must report
        failure (exit 1) WITHOUT writing a second copy into the lane — the fail-open
        duplicate this guard exists to prevent."""
        fake = FakeCmux(narrow_unparsed_queue=True)
        result = _dispatcher(fake).send_message(
            "workspace:99", PROBE, label="B4", consume_timeout=0.0, retries=1,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "sent-but-not-consumed")
        # Exactly ONE copy of the text reaches the lane; the recovery is a bare
        # Enter against the pane, not a re-send.
        self.assertEqual(fake.sent_log.count(PROBE), 1)
        self.assertEqual(fake.sent_log.count("\\n"), 2)

    def test_a_text_in_the_composer_is_not_queued(self):
        # `composer_region` -> None means "cannot tell", and an unsent composer
        # means "not accepted". Neither may confirm a delivery.
        fake = FakeCmux(never_consumes=True, no_rules=True)
        result = self._send(fake, consume_timeout=0.0, retries=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "sent-but-not-consumed")

    def test_refuses_to_send_into_a_prompt_that_never_yields(self):
        fake = FakeCmux(boot_block=True)
        fake.state = "boot_block"
        # Make dismissal ineffective so the pane stays blocked forever.
        fake.send_enter = lambda *a, **k: cd.CmuxResult(0, "OK")
        result = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(fake.submitted, [])
        self.assertNotIn(PROBE, fake.sent_log)

    def test_recovery_refuses_to_re_send_into_a_still_blocked_pane(self):
        """The boot-block recovery must not feed a second copy of the brief to the
        prompt it just failed to dismiss — that recreates the corruption."""

        class PromptForever(FakeCmux):
            """Ready until the first send, then the live prompt, for ever."""

            def __init__(self):
                super().__init__()
                self.stuck = False

            def read_screen(self, workspace, lines=80, surface=None):
                if self.stuck:
                    return cd.CmuxResult(0, SCREEN_BOOT_BLOCK)
                return super().read_screen(workspace, lines, surface)

            def send_text(self, workspace, text, surface=None):
                if text != "\\n":
                    self.stuck = True
                return super().send_text(workspace, text, surface)

            def send_enter(self, workspace, surface=None):
                self.sent_log.append("\\n")   # dismissal never takes effect
                return cd.CmuxResult(0, "OK")

        fake = PromptForever()
        result = self._send(fake, consume_timeout=0.0, retries=2)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(
            fake.sent_log.count(PROBE), 1, "the brief must never be re-sent"
        )

    def test_recovery_refuses_when_the_pane_goes_unreadable(self):
        """T1 on the RECOVERY path: the unreadable-pane refusal must apply to the
        dismiss-and-resend recovery too, not only to the first send. Otherwise an
        unreadable pane gets the brief written into it blind — which can be a live
        prompt, or a composer that already holds the message."""

        class GoesUnreadableAfterSend(FakeCmux):
            def __init__(self):
                super().__init__()
                self.phase = 0
                self.enters = 0

            def read_screen(self, workspace, lines=80, surface=None):
                if self.phase == 0:
                    return cd.CmuxResult(0, SCREEN_IDLE_READY)   # gate: ready
                if self.phase == 1:
                    self.phase = 2
                    return cd.CmuxResult(0, SCREEN_BOOT_BLOCK)   # recovery: prompt
                return cd.CmuxResult(1, "", "cmux read-screen: command timed out")

            def send_text(self, workspace, text, surface=None):
                if text != "\\n" and self.phase == 0:
                    self.phase = 1
                return super().send_text(workspace, text, surface)

            def send_enter(self, workspace, surface=None):
                # The submit never takes, so the confirmation has to fail.
                self.sent_log.append("\\n")
                self.enters += 1
                return cd.CmuxResult(0, "OK")

        fake = GoesUnreadableAfterSend()
        result = self._send(fake, consume_timeout=0.0, retries=2)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        # A recovery refusal is a REFUSAL, not a lost transport: an empty
        # `condition` would read as a transport failure to any consumer of the
        # field (#7913 review).
        self.assertEqual(result.condition, "unreadable-pane")
        self.assertIn("session", result.detail, "the refusal must carry the diagnosis")
        self.assertEqual(
            fake.sent_log.count(PROBE), 1, "the brief must never be written blind"
        )

    def test_unreadable_screen_degrades_recovery_to_release_only(self):
        """An unreadable pane must never produce a blind re-send: a bare Enter
        cannot duplicate, a blind re-send into a composer already holding the
        message submits it twice."""
        self.assertEqual(cd.recovery_action(None, cd.fingerprint(PROBE)), cd.R_RELEASE)

    def test_unreadable_pane_is_refused_rather_than_sent_blind(self):
        """An unreadable pane + no ready signal must REFUSE. Treating a failed
        read as an empty screen falls through to "sending anyway" — i.e. a brief
        written into a prompt the tool cannot see."""
        fake = FakeCmux(screen_unreadable=True)
        result = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(fake.submitted, [])
        self.assertNotIn(PROBE, fake.sent_log)

    def test_negative_retries_are_clamped_and_still_confirm(self):
        """`range(1, retries+2)` with a negative value would skip the loop body
        entirely AFTER the text was already transmitted, reporting a false
        sent-but-not-consumed for a message that did land."""
        fake = FakeCmux()
        result = self._send(fake, retries=-5)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.attempts, 1)

    def test_failure_detail_carries_the_reason_not_a_placeholder(self):
        fake = FakeCmux(never_consumes=True)
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertFalse(result.ok)
        self.assertNotIn("no confirmation)", result.detail)
        self.assertIn("not-at-the-head-of-latest-submitted-message", result.detail)

    def test_timeout_in_run_redacts_the_message_operand(self):
        """Drive the real `Cmux.run` timeout branch — not just the helper."""
        with mock.patch.object(
            subprocess, "run", side_effect=subprocess.TimeoutExpired("cmux", 1)
        ):
            result = cd.Cmux().run(["send", "--workspace", "w", "--", "SECRET-BRIEF"])
        self.assertEqual(result.rc, 124)
        self.assertNotIn("SECRET-BRIEF", result.err)
        self.assertIn("redacted", result.err)

    def test_timeout_error_redacts_the_message_operand(self):
        """The timeout path must not dump the brief (which may carry secrets)
        into the caller's log."""
        described = cd.Cmux._describe(
            ["send", "--workspace", "w", "--", "SECRET-BRIEF-CONTENT"]
        )
        self.assertNotIn("SECRET-BRIEF-CONTENT", described)
        self.assertIn("redacted", described)

    def test_unknown_workspace_fails_closed_after_a_bounded_appearance_wait(self):
        # `cmux new-workspace` returns before the workspace is enumerable, so a
        # missing workspace must be WAITED for, then reported clearly.
        result = _dispatcher(FakeCmux()).send_message(
            "workspace:nope", PROBE, appear_timeout=4.0
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unknown-workspace")

    def test_empty_message_is_a_usage_error(self):
        result = _dispatcher(FakeCmux()).send_message("workspace:99", "   ")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "empty-message")

    def test_a_readable_pane_with_no_pi_footer_is_REFUSED_not_sent_into(self):
        """#7158: a readable pane with NO pi footer and no boot marker is a bare
        login shell (a dead lane), not a slow boot. The old fail-open logged
        "sending anyway (confirmation will decide)" and wrote the brief into the
        shell, which EXECUTES it as a command; confirmation runs after the bytes
        and cannot undo that."""
        fake = BareShellCmux()
        result = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(fake.submitted, [])
        self.assertEqual(fake.sent_log, [], "no bytes may reach a bare shell")

    def test_the_GATE_itself_refuses_a_bare_shell_before_any_write(self):
        """The GATE's own pin, which the test above does not provide.

        Both refusals produce the same status and the same zero writes, so a pane
        that is bare for every read cannot tell them apart — and with the gate's
        fail-closed branch deleted the suite stayed green (mutation-proven). This
        pane is a bare shell for the gate's single probe and a live pi afterwards,
        so the gate is the ONLY thing between the brief and a shell. If the gate
        stops refusing, the pre-send read sees a ready pane, the bytes are written,
        and `sent_log` is no longer empty.
        """
        fake = BareShellAtTheGateThenReadyCmux()
        result = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(fake.sent_log, [], "the gate must refuse before any write")
        self.assertNotIn(
            "on the pre-send read",
            result.detail,
            "the refusal must come from the GATE, not from the pre-send re-assert",
        )
        self.assertEqual(fake.read_calls, 1, "the gate refuses on its first probe")

    def test_STALE_footer_above_a_LIVE_SHELL_PROMPT_is_not_ready(self):
        """#7158, the shape a "bar present anywhere" test misses: a DEAD pane keeps
        the previous pi session's footer in the scrollback while the shell prompt
        prints BELOW it. No boot marker, so `boot_blocked` is silent; readiness
        must require the footer to be the LAST thing drawn."""
        stale = SCREEN_STALE_FOOTER_ABOVE_SHELL_PROMPT
        self.assertTrue(cd.status_bar_present(stale), "the stale bar IS present")
        self.assertFalse(cd.boot_blocked(stale), "no boot marker catches it")
        self.assertTrue(
            cd.shell_prompt_below_footer(stale),
            "the shell prompt BELOW the stale footer is what makes it executable",
        )
        self.assertFalse(cd.screen_ready(stale))

    def test_a_LIVE_pi_with_an_EXTENSION_STATUS_below_the_footer_is_READY(self):
        """pi renders an extension-status line BELOW its stats line whenever an
        extension calls `ctx.ui.setStatus` (loop-enforcer, slack-bridge — loaded
        in every fleet pane). That is pi's own output, not a shell prompt, so
        readiness must NOT require the stats line to be the literal last line."""
        screen = SCREEN_LIVE_WITH_EXTENSION_STATUS_FOOTER
        self.assertTrue(cd.status_bar_present(screen))
        self.assertFalse(cd.boot_blocked(screen))
        self.assertFalse(cd.shell_prompt_below_footer(screen))
        self.assertTrue(cd.screen_ready(screen), "a live pi must stay dispatchable")

    def test_trailing_blank_lines_after_the_footer_are_READY(self):
        """`cmux read-screen` pads the capture; blank lines are not a prompt."""
        self.assertTrue(cd.screen_ready(SCREEN_IDLE_READY + "\n\n   \n"))

    def test_dispatch_into_a_LIVE_pi_with_an_extension_status_is_NOT_refused(self):
        """The end-to-end guard for the shape above: a healthy lane mid-loop must
        still receive its brief."""

        class ExtensionStatusCmux(FakeCmux):
            def read_screen(self, workspace, lines=80, surface=None):
                return cd.CmuxResult(0, SCREEN_LIVE_WITH_EXTENSION_STATUS_FOOTER)

        fake = ExtensionStatusCmux()
        result = self._send(fake, ready_timeout=0.0, consume_timeout=0.0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(fake.submitted, [PROBE])

    def test_a_stale_footer_above_a_PROMPT_WITH_A_COMMAND_is_not_ready(self):
        """Round-3 finding: an end-anchored prompt detector misses a prompt line
        that carries a typed command (`host % ls -la`) — the sigil is mid-line, so
        readiness said YES and the brief was executed. The STANDALONE-sigil rule
        catches it while leaving pi's status text alone."""
        stale = SCREEN_STALE_FOOTER_ABOVE_SHELL_COMMAND
        self.assertTrue(cd.status_bar_present(stale))
        self.assertFalse(cd.boot_blocked(stale))
        self.assertTrue(cd.shell_prompt_below_footer(stale))
        self.assertFalse(cd.screen_ready(stale))

    def test_dispatch_into_a_STALE_footer_above_a_shell_COMMAND_is_REFUSED(self):
        class StaleCommandCmux(FakeCmux):
            def read_screen(self, workspace, lines=80, surface=None):
                return cd.CmuxResult(0, SCREEN_STALE_FOOTER_ABOVE_SHELL_COMMAND)

        fake = StaleCommandCmux()
        result = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertIn("shell prompt", result.detail)
        self.assertEqual(fake.sent_log, [], "no bytes may reach the shell prompt")

    def test_a_LIVE_pi_with_a_PERCENT_in_its_extension_status_is_READY(self):
        """The digit guard: `Uploading 50%` is pi's own status text, not a prompt,
        and must not be refused (the round-2 over-refusal class)."""
        screen = SCREEN_LIVE_WITH_PERCENT_STATUS_FOOTER
        self.assertFalse(cd.shell_prompt_below_footer(screen))
        self.assertTrue(cd.screen_ready(screen))

    def test_a_stale_footer_above_a_DIGIT_PROMPT_is_not_ready(self):
        """Round-4 finding: `bash-3.2$ ` / `sh-3.2$ ` abut a digit, so a
        digit-guarded line-end rule missed them and the brief was executed."""
        for screen in (
            SCREEN_STALE_FOOTER_ABOVE_DIGIT_PROMPT,
            SCREEN_STALE_FOOTER_ABOVE_ROOT_PROMPT,
        ):
            with self.subTest(screen=screen.splitlines()[-1]):
                self.assertTrue(cd.status_bar_present(screen))
                self.assertFalse(cd.boot_blocked(screen))
                self.assertTrue(cd.shell_prompt_below_footer(screen))
                self.assertFalse(cd.screen_ready(screen))

    def test_dispatch_into_a_STALE_footer_above_a_DIGIT_PROMPT_is_REFUSED(self):
        class DigitPromptCmux(FakeCmux):
            def read_screen(self, workspace, lines=80, surface=None):
                return cd.CmuxResult(0, SCREEN_STALE_FOOTER_ABOVE_DIGIT_PROMPT)

        fake = DigitPromptCmux()
        result = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(fake.sent_log, [], "no bytes may reach the shell prompt")

    def test_a_status_shaped_line_below_the_prompt_does_not_hide_it(self):
        """Round-6 finding: anchoring the scan on the last stats-shaped line lets
        shell output that mimics the stats line hide the prompt ABOVE it (and a
        fixed-width tail window only moves the problem one line down)."""
        screen = SCREEN_STALE_FOOTER_THEN_STATUS_LIKE_OUTPUT
        self.assertTrue(cd.status_bar_present(screen))
        self.assertTrue(cd.shell_prompt_below_footer(screen))
        self.assertFalse(cd.screen_ready(screen))

    def test_a_torn_footer_with_only_the_model_badge_is_still_LIVE(self):
        """Round-7 finding: a banner printed over the footer removes the
        context-budget token but leaves the model badge. This fleet's own liveness
        checks accept the badge; requiring the token refused a healthy lane."""
        screen = SCREEN_LIVE_WITH_TORN_FOOTER
        self.assertTrue(cd.status_bar_present(screen))
        self.assertFalse(cd.shell_prompt_below_footer(screen))
        self.assertTrue(cd.screen_ready(screen))

    def test_every_thinking_level_badge_is_LIVE(self):
        """Round-8 finding: the badge is `${model} • ${level}` and the levels are
        off|minimal|low|medium|high|xhigh|max — enumerating three of them refused a
        torn footer at `• max`/`• xhigh`, shapes this box's models.json produces."""
        for level in ("max", "xhigh", "minimal", "low", "medium", "high"):
            screen = (
                "~/Documents/GitHub/tortoise (main)\n"
                "↑1.3M ↓643k R103M CH99.9% $0.887 "
                f"(deepseek) deepseek-flash \u2022 {level}\n"
            )
            with self.subTest(level=level):
                self.assertTrue(cd.status_bar_present(screen))
                self.assertTrue(cd.screen_ready(screen), level)
        off = (
            "~/Documents/GitHub/tortoise (main)\n"
            "↑1.3M ↓643k (deepseek) deepseek-flash \u2022 thinking off\n"
        )
        self.assertTrue(cd.status_bar_present(off))
        self.assertTrue(cd.screen_ready(off))

    def test_a_bare_shell_printing_a_LOOSE_marker_is_still_not_ready(self):
        """Round-8/9 finding: without a pwd+stats footer BLOCK the anchor must not
        be the last marker line — the shell's own output (`(auto)`, a markdown
        bullet) would sit below the prompt and hide it, declaring a bare shell
        READY. With no block the WHOLE capture is scanned, and the badge level set
        is enumerated so an arbitrary bullet is not a marker at all."""
        for screen in (
            "Last login: Wed Oct  8 20:58:11 on ttys004\nhost % echo '(auto)'\n(auto)\n",
            "host % cat priorities.md\n\u2022 high \u2014 fix send boundary\n",
            "host % cat notes.md\n\u2022 item one\n",
        ):
            with self.subTest(screen=screen):
                self.assertFalse(cd.screen_ready(screen))

    def test_an_arbitrary_bullet_is_not_a_footer_marker(self):
        """Round-9 finding: a generic `• <word>` badge let ordinary output forge a
        footer block. The level set is finite, so it is enumerated."""
        self.assertFalse(cd.status_bar_present("host % cat notes.md\n\u2022 item one\n"))
        self.assertFalse(cd.status_bar_present("host % ls\n\u2022 item\n"))

    def test_a_LIVE_pane_with_prompt_like_transcript_lines_is_READY(self):
        """Round-9 finding: the block anchor is what keeps the prompt scan below
        the footer. Without it (whole-capture fallback) a live pane whose
        TRANSCRIPT contains `$ \u2026` / `# \u2026` lines is refused. This pins the
        anchor: mutating `_footer_stats_end` to -1 turns this READY into a refusal."""
        screen = SCREEN_LIVE_WITH_TRANSCRIPT
        self.assertGreaterEqual(cd._footer_stats_end(screen), 0)
        self.assertFalse(cd.shell_prompt_below_footer(screen))
        self.assertTrue(cd.screen_ready(screen))

    def test_a_bare_HOME_pwd_line_still_anchors_the_footer_block(self):
        r"""Round-9 finding: `formatCwdForFooter(HOME)` renders the pwd line as a
        bare `~`; a `\S` requirement rejected it, disabling the anchor and
        refusing a healthy lane at $HOME."""
        screen = SCREEN_LIVE_WITH_HOME_PWD
        self.assertGreaterEqual(cd._footer_stats_end(screen), 0)
        self.assertFalse(cd.shell_prompt_below_footer(screen))
        self.assertTrue(cd.screen_ready(screen))

    def test_a_pwd_line_with_a_session_name_still_anchors_the_footer_block(self):
        """Round-7 finding: a real pwd line can carry ` \u2022 <sessionName>`. If the
        pwd anchor rejects it the block anchor silently disappears and the
        stats-mimicking output below re-opens the round-6 bypass."""
        screen = SCREEN_STALE_FOOTER_WITH_SESSION_NAME
        self.assertTrue(cd.status_bar_present(screen))
        self.assertTrue(cd.shell_prompt_below_footer(screen))
        self.assertFalse(cd.screen_ready(screen))

    def test_an_unknown_context_indicator_is_a_LIVE_footer(self):
        """Round-6 finding: pi renders `?/Nk (auto)` after a compaction, so an
        indicator that requires digits refused a healthy lane (#7158)."""
        screen = SCREEN_LIVE_WITH_UNKNOWN_CONTEXT_FOOTER
        self.assertTrue(cd.status_bar_present(screen))
        self.assertFalse(cd.shell_prompt_below_footer(screen))
        self.assertTrue(cd.screen_ready(screen))

    def test_dispatch_succeeds_on_an_unknown_context_footer(self):
        class UnknownContextCmux(FakeCmux):
            def read_screen(self, workspace, lines=80, surface=None):
                result = super().read_screen(workspace, lines=lines, surface=surface)
                return cd.CmuxResult(
                    result.rc, result.out.replace("0.0%/700k", "?/700k"), result.err
                )

        fake = UnknownContextCmux()
        result = self._send(fake, ready_timeout=0.0, consume_timeout=0.0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(fake.submitted, [PROBE])

    def test_a_same_line_footer_and_shell_prompt_is_not_ready(self):
        """A footer row that is not newline-terminated (crash mid-line) with the
        prompt appended must still be refused."""
        screen = SCREEN_STALE_FOOTER_AND_PROMPT_SAME_LINE
        self.assertTrue(cd.status_bar_present(screen))
        self.assertFalse(cd.boot_blocked(screen))
        self.assertTrue(cd.shell_prompt_below_footer(screen))
        self.assertFalse(cd.screen_ready(screen))

    def test_initial_send_reasserts_readiness_on_the_pre_send_read(self):
        """Round-5 finding (TOCTOU): the gate can be ready and the pane can die
        before the baseline read, one read-screen later. The brief must not be
        written on the stale `ready`."""

        class DiesAfterGate(FakeCmux):
            def __init__(self):
                super().__init__()
                self.reads = 0

            def read_screen(self, workspace, lines=80, surface=None):
                self.reads += 1
                if self.reads <= 1:      # the readiness gate's read
                    return cd.CmuxResult(0, SCREEN_IDLE_READY)
                return cd.CmuxResult(0, SCREEN_STALE_FOOTER_ABOVE_SHELL_PROMPT)

        fake = DiesAfterGate()
        result = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(
            fake.sent_log, [], "the pre-send read must gate the first write"
        )

    def test_recovery_RESEND_refuses_when_the_pane_died_BEFORE_the_write(self):
        """The R_RESEND gate must judge a screen read IMMEDIATELY before the
        second write, not the recovery read taken before the duplicate-guard
        grace window (up to a full consume budget): a pane that dies in that
        window would otherwise be written into."""

        class DiesBeforeResend(FakeCmux):
            def __init__(self):
                super().__init__(never_consumes=True)
                self.reads = 0

            def read_screen(self, workspace, lines=80, surface=None):
                self.reads += 1
                if self.reads <= 3:      # gate, baseline, recovery read
                    return cd.CmuxResult(0, SCREEN_IDLE_READY)
                # A footer IS present, so the refusal must come from the shell
                # prompt BELOW it — not from a missing footer (round-7 test gap).
                return cd.CmuxResult(0, SCREEN_STALE_FOOTER_ABOVE_SHELL_PROMPT)

        fake = DiesBeforeResend()
        result = self._send(fake, consume_timeout=0.0, retries=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(
            fake.sent_log.count(PROBE),
            1,
            "the second write must be gated on a FRESH readiness read",
        )

    def test_recovery_RESEND_refuses_when_the_pane_died_after_the_gate(self):
        """Round-3 finding: the `R_RESEND` recovery writes the brief a SECOND time,
        so a pane that dies between the readiness gate and the recovery must be
        refused there too — otherwise the bytes land in the shell."""

        class DiesAfterSend(FakeCmux):
            def __init__(self):
                super().__init__(never_consumes=True)
                self.dead = False

            def read_screen(self, workspace, lines=80, surface=None):
                if self.dead:
                    return cd.CmuxResult(0, SCREEN_DEAD_SHELL_WITH_COMPOSER)
                return cd.CmuxResult(0, SCREEN_IDLE_READY)

            def send_text(self, workspace, text, surface=None):
                if text != "\\n":
                    self.dead = True
                return super().send_text(workspace, text, surface)

        fake = DiesAfterSend()
        result = self._send(fake, consume_timeout=0.0, retries=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(
            fake.sent_log.count(PROBE),
            1,
            "the brief must not be re-sent into a pane that became a shell",
        )

    def test_a_pane_with_a_STALE_footer_above_a_shell_prompt_is_REFUSED(self):
        """The no-footer refusal alone does not cover the stale-footer shape: the
        pane LOOKS ready (a footer is on screen) while a shell prompt below it
        would execute the bytes. The dispatch must be refused with zero writes."""

        class StaleFooterCmux(FakeCmux):
            def read_screen(self, workspace, lines=80, surface=None):
                return cd.CmuxResult(0, SCREEN_STALE_FOOTER_ABOVE_SHELL_PROMPT)

        fake = StaleFooterCmux()
        result = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(fake.sent_log, [], "no bytes may reach the shell prompt")

    def test_recovery_refuses_to_re_send_into_a_pane_that_never_drew_the_footer(self):
        """#7158 on the RECOVERY path: after a boot-block dismissal the pane
        became readable but never drew pi's footer (the pi died), so the
        dismiss-and-resend recovery must REFUSE rather than write the brief a
        second time into whatever is there."""

        class DiesAfterDismiss(FakeCmux):
            def __init__(self):
                super().__init__(never_consumes=True)
                self.phase = "ready"

            def read_screen(self, workspace, lines=80, surface=None):
                if self.phase == "ready":
                    return cd.CmuxResult(0, SCREEN_IDLE_READY)
                if self.phase == "blocked":
                    self.phase = "shell"
                    return cd.CmuxResult(0, SCREEN_BOOT_BLOCK)
                # A stale footer is still on screen; only the prompt below it
                # makes the pane unsafe (round-7 test gap).
                return cd.CmuxResult(0, SCREEN_STALE_FOOTER_ABOVE_SHELL_PROMPT)

            def send_text(self, workspace, text, surface=None):
                if text != "\\n":
                    self.phase = "blocked"
                return super().send_text(workspace, text, surface)

        fake = DiesAfterDismiss()
        result = self._send(fake, consume_timeout=0.0, retries=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(
            fake.sent_log.count(PROBE),
            1,
            "the brief must not be re-sent into a pane with no live pi",
        )


# --------------------------------------------------------------------------- #
# CLI contract — exit codes, through the real entry point with a faked transport
# --------------------------------------------------------------------------- #


class TestCliExitCodes(unittest.TestCase):
    """The exit-code contract the fleet depends on.

    Driven through `cd.main()` (argparse -> _cmd_send -> exit code) with the
    transport faked, so the contract is asserted without paying for a subprocess
    per `cmux` call. The real end-to-end path against live cmux is exercised
    separately in the issue's verification transcript.
    """

    def _main(self, fake, *args: str) -> int:
        with mock.patch.object(cd, "Cmux", lambda *a, **k: fake):
            return cd.main(list(args))

    def test_exit_zero_only_when_the_artifact_confirms(self):
        fake = FakeCmux()
        rc = self._main(
            fake, "send", "--workspace", "workspace:99", "--text", PROBE,
            "--ready-timeout", "0", "--consume-timeout", "0",
        )
        self.assertEqual(rc, 0)
        self.assertEqual(fake.submitted, [PROBE])

    def test_exit_one_when_sent_but_never_consumed(self):
        fake = FakeCmux(never_consumes=True)
        rc = self._main(
            fake, "send", "--workspace", "workspace:99", "--text", PROBE,
            "--ready-timeout", "0", "--consume-timeout", "0", "--retries", "0",
        )
        self.assertEqual(rc, 1, "an unconsumed send must NEVER exit 0")

    def test_exit_four_when_the_message_is_queued_but_not_consumed(self):
        # #7743: pi accepted the message into its pending queue, so it is
        # DELIVERED — but it is NOT CONSUMED, and the lane has not acted on it. A
        # queued-only send must NOT read as a completed dispatch (exit 0): the
        # lane may be wedged inside its turn, in which case the queue is never
        # drained. Exit 4 is the distinct DELIVERED-but-not-consumed outcome.
        fake = FakeCmux(queued_turn=True)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = self._main(
                fake, "send", "--workspace", "workspace:99", "--text", PROBE,
                "--ready-timeout", "0", "--consume-timeout", "0",
            )
        self.assertEqual(rc, 4, "a queued-only send is delivered but NOT success")
        self.assertTrue(out.getvalue().startswith("QUEUED "), out.getvalue())
        self.assertNotIn("OK ", out.getvalue())

    def test_exit_four_is_distinct_from_both_success_and_unconsumed(self):
        # The three-way contract the fleet keys on: 0 consumed, 4 queued (in
        # pi's hands), 1 not consumed. A change that collapses any pair breaks an
        # orchestration caller silently.
        self.assertEqual(cd._EXIT_FOR_STATUS["queued"], 4)
        self.assertNotEqual(cd._EXIT_FOR_STATUS["queued"], 1)
        self.assertNotEqual(cd._EXIT_FOR_STATUS["queued"], 3)
        # An UNMAPPED status (sent-but-not-consumed / never-became-ready) must fall
        # to the default 1 — the DEFAULT is the contract, not a table entry, so
        # assert the absence rather than `.get(..., 1) == 1` (which is a tautology
        # that passes for any unknown key).
        self.assertNotIn("sent-but-not-consumed", cd._EXIT_FOR_STATUS)

    def test_json_carries_delivered_and_not_ok_for_a_queued_send(self):
        # `--json` is the machine-readable contract: a queued send is
        # `delivered: true` (so a caller knows a re-send would duplicate) AND
        # `ok: false` (so a caller knows the turn has not started).
        fake = FakeCmux(queued_turn=True)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = self._main(
                fake, "send", "--workspace", "workspace:99", "--text", PROBE,
                "--ready-timeout", "0", "--consume-timeout", "0", "--json",
            )
        self.assertEqual(rc, 4)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["delivered"])
        self.assertEqual(payload["status"], "queued")

    def test_consumed_json_is_ok_and_delivered(self):
        fake = FakeCmux()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = self._main(
                fake, "send", "--workspace", "workspace:99", "--text", PROBE,
                "--ready-timeout", "0", "--consume-timeout", "0", "--json",
            )
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["delivered"])
        self.assertEqual(payload["status"], "consumed")

    def test_verify_is_SUBMITTED_only_and_diverges_from_send(self):
        # The documented contract: on a mid-turn lane a message `send` reports as
        # `queued`/exit 4 still reads NOT-CONSUMED/exit 1 under
        # `verify`, because `verify` has no dispatch to be novel against. Pinned so
        # a future "consistency" change to `verify` cannot land silently.
        fake = FakeCmux(queued_turn=True)
        self.assertEqual(
            self._main(
                fake, "verify", "--workspace", "workspace:99", "--text", PROBE,
                "--timeout", "0",
            ),
            1,
        )

    def test_exit_one_when_a_stale_pending_line_does_not_confirm(self):
        # #5979: the pending line predates the send (a repeat dispatch of the same
        # brief, or a stale scrollback line), so it is not evidence about THIS
        # send. The CLI must NOT report success — the fail-closed direction.
        fake = FakeCmux(drops_message=True, pre_queued=PROBE)
        rc = self._main(
            fake, "send", "--workspace", "workspace:99", "--text", PROBE,
            "--ready-timeout", "0", "--consume-timeout", "0", "--retries", "0",
        )
        self.assertEqual(rc, 1, "a stale pending line must never confirm a lost send")

    def test_idle_lane_confirmation_is_unchanged(self):
        # The idle path keeps today's exact success semantics: the message
        # becomes the latest submitted message and exits 0 with `consumed`.
        fake = FakeCmux()
        rc = self._main(
            fake, "send", "--workspace", "workspace:99", "--text", PROBE,
            "--ready-timeout", "0", "--consume-timeout", "0",
        )
        self.assertEqual(rc, 0)
        self.assertEqual(fake.submitted, [PROBE])

    def test_exit_two_on_empty_message(self):
        self.assertEqual(
            self._main(FakeCmux(), "send", "--workspace", "workspace:99", "--text", "   "),
            2,
        )

    def test_exit_two_on_missing_message_file(self):
        self.assertEqual(
            self._main(
                FakeCmux(), "send", "--workspace", "workspace:99",
                "--file", "/nonexistent/brief.txt",
            ),
            2,
        )

    def test_exit_two_on_unknown_workspace(self):
        rc = self._main(
            FakeCmux(), "send", "--workspace", "workspace:nope", "--text", PROBE,
            "--ready-timeout", "0", "--appear-timeout", "0",
        )
        self.assertEqual(rc, 2, "unknown workspace is a usage error, documented as 2")

    def test_verify_reports_consumed_for_a_present_message(self):
        fake = FakeCmux()
        fake.submitted.append(PROBE)
        fake.visible = PROBE
        rc = self._main(
            fake, "verify", "--workspace", "workspace:99", "--text", PROBE,
            "--timeout", "0",
        )
        self.assertEqual(rc, 0)


# --------------------------------------------------------------------------- #
# Durable fallback — a transport failure must never silently drop the notice
# --------------------------------------------------------------------------- #


class _TransportDiesAfterFirstRead(cd.Cmux):
    """`list-workspaces` succeeds once, then the transport dies.

    Models the issue's own evidence: the appearance/readiness reads land, the
    bytes go out, and cmux goes unreachable mid-confirmation (load 95-113) — the
    point at which the old code returned exit 3 and dropped the notice.
    """

    def __init__(self) -> None:
        super().__init__("cmux")
        self.reads = 0

    def run(self, argv: list[str], timeout: float | None = None) -> cd.CmuxResult:
        if argv and argv[0] == "list-workspaces":
            self.reads += 1
            if self.reads > 1:
                return cd.CmuxResult(124, "", "cmux timed out: list-workspaces")
            return cd.CmuxResult(0, REAL_PAYLOAD, "")
        if argv and argv[0] == "read-screen":
            return cd.CmuxResult(0, SCREEN_IDLE_READY, "")
        return cd.CmuxResult(0, "OK", "")


class _TransportDiesAtReadinessGate(cd.Dispatcher):
    """The readiness gate itself raises a transport error (site 2).

    `wait_until_safe_to_send` swallows `read-screen` rc failures into `None`
    rather than raising, so the except that guards it is only reachable from a
    transport that raises. This pins that guard: a real `CmuxTransportError`
    there must take the durable fallback, never the old drop-the-notice return.
    """

    def wait_until_safe_to_send(self, *args, **kwargs):
        raise cd.CmuxTransportError("cmux timed out: read-screen")


class _SendTextFails(cd.Cmux):
    """Reads succeed; the text `send` is refused (site 3)."""

    def __init__(self, workspace: str = "cmux") -> None:
        super().__init__(workspace)

    def run(self, argv: list[str], timeout: float | None = None) -> cd.CmuxResult:
        if argv and argv[0] == "send" and "--" in argv and argv[-1] != "\\n":
            return cd.CmuxResult(1, "", "cmux send: connection refused")
        if argv and argv[0] == "list-workspaces":
            return cd.CmuxResult(0, REAL_PAYLOAD, "")
        if argv and argv[0] == "read-screen":
            return cd.CmuxResult(0, SCREEN_IDLE_READY, "")
        return cd.CmuxResult(0, "OK", "")


class _TransportDiesOnNthListRead(FakeCmux):
    """`list-workspaces` succeeds until its Nth call, then the transport dies.

    Used to reach the PRE-RECOVERY GRACE `wait_consumed` specifically: reads #1
    (appearance) and #2 (confirmation) must land, and read #3 (the grace window)
    is the one that raises.
    """

    def __init__(self, *, fail_on: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fail_on = fail_on
        self.state_reads = 0

    def list_workspaces_json(self) -> cd.CmuxResult:
        self.state_reads += 1
        if self.state_reads == self.fail_on:
            return cd.CmuxResult(124, "", "cmux timed out: list-workspaces")
        return super().list_workspaces_json()


class _PromptForever(FakeCmux):
    """Ready until the first send, then the live boot-block prompt for ever.

    Reaches the RECOVERY `never-became-ready` branch, where the written text was
    eaten and the re-send refused — the branch deliberately NOT wired to the
    durable fallback in this change (tracked as a scoped follow-up).
    """

    def __init__(self) -> None:
        super().__init__()
        self.stuck = False

    def read_screen(self, workspace, lines=80, surface=None):
        if self.stuck:
            return cd.CmuxResult(0, SCREEN_BOOT_BLOCK)
        return super().read_screen(workspace, lines, surface)

    def send_text(self, workspace, text, surface=None):
        if text != "\\n":
            self.stuck = True
        return super().send_text(workspace, text, surface)

    def send_enter(self, workspace, surface=None):
        self.sent_log.append("\\n")
        return cd.CmuxResult(0, "OK")


class _TransportDiesAtTheRecoveryGate(cd.Dispatcher):
    """The RECOVERY-site readiness gate raises a transport error.

    The FIRST `wait_until_safe_to_send` (the pre-send gate) succeeds; the SECOND
    is reached only after the boot-block prompt ate the text and recovery chose
    `dismiss-and-resend`. Un-wrapped, that raise propagates out of `send_message`
    and the notice is recorded nowhere — this pins the wrap at that second site.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.gate_calls = 0

    def wait_until_safe_to_send(self, *args, **kwargs):
        self.gate_calls += 1
        if self.gate_calls >= 2:
            raise cd.CmuxTransportError("cmux timed out: read-screen")
        return super().wait_until_safe_to_send(*args, **kwargs)


class TestDurableInboxFallback(unittest.TestCase):
    """#4842: when cmux is unreachable the notice lands in the orchestrator inbox.

    Measured 2026-09-23: `cmux unreachable: cmux timed out` at load 95-113, and
    the lane-completion notice was lost — exactly under the congestion when
    "which lane finished" matters most. The inbox
    (`~/.pi/agent/state/orchestrator-inbox.log`) is the surface the orchestrator
    already treats as intake truth (`notify-orchestrator.sh` writes it FIRST,
    before it ever touches cmux); this class pins that it is now a real fallback
    of the dispatch path and not an agent's manual improvisation.
    """

    def setUp(self) -> None:
        INBOX_PATH.unlink(missing_ok=True)

    def _timeout_send(self, text: str = PROBE, label: str = "B7") -> cd.DispatchResult:
        """Drive the REAL transport into its timeout branch (`subprocess.run`)."""
        with mock.patch.object(
            subprocess, "run", side_effect=subprocess.TimeoutExpired("cmux", 30)
        ):
            # A no-op log, exactly as the module's other helper does: the real
            # logger spams stderr with every poll.
            return cd.Dispatcher(cd.Cmux("cmux"), log=lambda _m: None).send_message(
                "workspace:79", text, label=label, appear_timeout=0.0
            )

    # -- the wired sites: each is a separate place the notice could be dropped -- #

    def test_a_spawn_oserror_is_a_transport_failure_not_an_escape(self):
        # #4842 re-review: `Cmux.run` converted only FileNotFoundError and
        # TimeoutExpired. A PermissionError (a non-executable `--cmux`) escaped
        # PAST every `except CmuxTransportError` and every fallback, so the notice
        # was recorded nowhere. It must be a transport failure like the rest.
        with mock.patch.object(
            subprocess,
            "run",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            result = cd.Dispatcher(cd.Cmux("cmux"), log=lambda _m: None).send_message(
                "workspace:79", PROBE, label="B7", appear_timeout=0.0
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "transport-error")
        self.assertEqual(result.channel, "inbox")
        self.assertTrue(INBOX_PATH.exists(), "a spawn OSError dropped the notice")
        self.assertIn("could not be spawned", result.detail)
        self.assertIn("[B7]", INBOX_PATH.read_text(encoding="utf-8"))

    def test_a_timed_out_cmux_still_reaches_the_inbox_and_names_the_channel(self):
        result = self._timeout_send()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "transport-error")
        self.assertTrue(
            INBOX_PATH.exists(),
            "the notice was DROPPED: a transport failure recorded nothing in the inbox",
        )
        line = INBOX_PATH.read_text(encoding="utf-8").strip()
        self.assertIn("[B7]", line)
        self.assertTrue(line.endswith(PROBE), line)
        self.assertEqual(result.channel, "inbox")
        self.assertIn("channel: inbox", result.detail)
        self.assertIn("timed out", result.detail)

    def test_a_transport_death_at_the_readiness_gate_still_reaches_the_inbox(self):
        clock = FakeClock()
        dispatcher = _TransportDiesAtReadinessGate(
            FakeCmux(), sleep=clock.sleep, now=clock.now, log=lambda _m: None
        )
        result = dispatcher.send_message(
            "workspace:99", PROBE, label="B7", appear_timeout=0.0
        )
        self.assertEqual(result.status, "transport-error")
        self.assertEqual(result.channel, "inbox")
        self.assertTrue(INBOX_PATH.exists(), "the notice was dropped")
        self.assertIn("channel: inbox", result.detail)

    def test_a_transport_death_at_the_recovery_gate_still_reaches_the_inbox(self):
        # #4842 re-review: the RECOVERY-site `wait_until_safe_to_send` was
        # un-wrapped, so a raise there escaped with no inbox record. Drive the
        # real recovery branch (boot-block prompt eats the text, recovery chooses
        # dismiss-and-resend) and make only the SECOND gate raise.
        clock = FakeClock()
        dispatcher = _TransportDiesAtTheRecoveryGate(
            _PromptForever(), sleep=clock.sleep, now=clock.now, log=lambda _m: None
        )
        result = dispatcher.send_message(
            "workspace:99", PROBE, label="B7", consume_timeout=0.0, retries=2
        )
        self.assertEqual(result.status, "transport-error")
        self.assertEqual(result.channel, "inbox")
        self.assertTrue(INBOX_PATH.exists(), "the notice was dropped")
        self.assertIn("recovery gate", result.detail)
        self.assertEqual(result.attempts, 1, "the attempt count is evidence")
        self.assertEqual(result.recoveries, [cd.R_DISMISS_RESEND])

    def test_a_refused_text_send_still_reaches_the_inbox(self):
        result = cd.Dispatcher(_SendTextFails(), log=lambda _m: None).send_message(
            "workspace:79", PROBE, label="B7", appear_timeout=0.0, ready_timeout=0.0
        )
        self.assertEqual(result.status, "transport-error")
        self.assertEqual(result.channel, "inbox")
        self.assertTrue(INBOX_PATH.exists(), "the notice was dropped")
        self.assertIn("rc=1", result.detail)

    def test_a_transport_death_mid_confirmation_keeps_the_attempt_evidence(self):
        result = cd.Dispatcher(
            _TransportDiesAfterFirstRead(), log=lambda _m: None
        ).send_message(
            "workspace:79", PROBE, label="B7", appear_timeout=0.0, consume_timeout=0.0
        )
        self.assertEqual(result.status, "transport-error")
        self.assertEqual(result.channel, "inbox")
        self.assertTrue(INBOX_PATH.exists(), "the notice was dropped")
        self.assertEqual(result.attempts, 1, "the send's attempt count is evidence")

    def test_a_transport_death_in_the_pre_recovery_grace_window_reaches_the_inbox(self):
        fake = _TransportDiesOnNthListRead(fail_on=3, drops_message=True)
        result = _dispatcher(fake).send_message(
            "workspace:99", PROBE, label="B7",
            appear_timeout=0.0, ready_timeout=0.0, consume_timeout=0.0, retries=1,
        )
        self.assertEqual(result.status, "transport-error")
        self.assertEqual(result.channel, "inbox")
        self.assertTrue(INBOX_PATH.exists(), "the notice was dropped")
        self.assertIn("grace window", result.detail)
        self.assertEqual(result.attempts, 1, "the attempt count is evidence")
        self.assertEqual(result.recoveries, [cd.R_RESEND])

    def test_an_unknown_workspace_still_reaches_the_inbox_and_keeps_exit_two(self):
        result = _dispatcher(FakeCmux()).send_message(
            "workspace:nope", PROBE, label="B7", appear_timeout=0.0
        )
        self.assertEqual(result.status, "unknown-workspace")
        self.assertEqual(result.channel, "inbox")
        self.assertTrue(INBOX_PATH.exists(), "the notice was dropped")
        self.assertIn("not found", result.detail)
        self.assertIn("channel: inbox", result.detail)
        self.assertEqual(cd._EXIT_FOR_STATUS[result.status], 2)

    def test_cli_unknown_workspace_exits_two_and_still_records_the_notice(self):
        fake = FakeCmux()
        out = io.StringIO()
        with mock.patch.object(cd, "Cmux", lambda *a, **k: fake), redirect_stdout(out):
            rc = cd.main(
                [
                    "send", "--workspace", "workspace:nope", "--text", PROBE,
                    "--label", "B7", "--appear-timeout", "0",
                ]
            )
        self.assertEqual(rc, 2)
        self.assertTrue(INBOX_PATH.exists(), "the notice was dropped")
        self.assertIn("channel: inbox", out.getvalue())

    # -- the channel is a real, pinned field (not the inbox PATH, not a rename) -- #

    def test_the_json_surface_carries_the_channel(self):
        result = self._timeout_send()
        self.assertEqual(result.as_json()["channel"], "inbox")

    def test_the_channel_is_named_consistently_in_detail_and_cli(self):
        result = self._timeout_send()
        self.assertIn("channel: inbox", result.detail)
        out = io.StringIO()
        with (
            mock.patch.object(
                subprocess, "run", side_effect=subprocess.TimeoutExpired("cmux", 30)
            ),
            redirect_stdout(out),
        ):
            cd.main(
                [
                    "send", "--workspace", "workspace:79", "--text", PROBE,
                    "--label", "B7", "--appear-timeout", "0", "--ready-timeout", "0",
                ]
            )
        self.assertIn("channel: inbox", out.getvalue())

    # -- negative: the fallback fires ONLY where the notice would vanish --------- #

    def test_sent_but_not_consumed_never_touches_the_inbox(self):
        result = _dispatcher(FakeCmux(never_consumes=True)).send_message(
            "workspace:99", PROBE, label="B7",
            ready_timeout=0.0, consume_timeout=0.0, retries=0,
        )
        self.assertEqual(result.status, "sent-but-not-consumed")
        self.assertEqual(result.channel, "transport")
        self.assertFalse(
            INBOX_PATH.exists(),
            "the bytes reached the transport; an inbox entry would be a duplicate",
        )

    def test_a_refused_submit_enter_is_not_routed_to_the_inbox(self):
        # #4842 re-review: the `send_enter rc != 0` branch is deliberately NOT
        # wired to the durable fallback (the text reached cmux; a recovery entry
        # would be a duplicate). `FakeCmux.send_enter` always returns 0, so the
        # branch was never exercised and could silently be re-routed.
        fake = FakeCmux()
        fake.send_enter = lambda *a, **k: cd.CmuxResult(
            1, "", "cmux send: connection refused"
        )
        result = _dispatcher(fake).send_message(
            "workspace:99", PROBE, label="B7", ready_timeout=0.0
        )
        self.assertEqual(result.status, "sent-but-not-consumed")
        self.assertEqual(result.channel, "transport")
        self.assertIn("rc=1", result.detail)
        self.assertFalse(
            INBOX_PATH.exists(),
            "the bytes reached the transport; an inbox entry would be a duplicate",
        )

    def test_a_refusal_to_send_never_touches_the_inbox(self):
        fake = FakeCmux(boot_block=True)
        fake.send_enter = lambda *a, **k: cd.CmuxResult(0, "OK")
        result = _dispatcher(fake).send_message(
            "workspace:99", PROBE, label="B7", ready_timeout=0.0
        )
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(result.channel, "")
        self.assertFalse(INBOX_PATH.exists())

    def test_a_refused_recovery_never_touches_the_inbox(self):
        result = _dispatcher(_PromptForever()).send_message(
            "workspace:99", PROBE, label="B7", consume_timeout=0.0, retries=2
        )
        self.assertEqual(result.status, "never-became-ready")
        self.assertFalse(INBOX_PATH.exists())

    # -- the shape: append-only, one line, flattened, honest about failure -------- #

    def test_the_record_matches_the_established_inbox_shape(self):
        # `[YYYY-MM-DD HH:MM:SS TZ] [LABEL] <one line>` — what
        # notify-orchestrator.sh writes on its FIRST line since 2026-09-16.
        self._timeout_send()
        line = INBOX_PATH.read_text(encoding="utf-8").rstrip("\n")
        self.assertRegex(
            line,
            r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+\] \[B7\] "
            + re.escape(PROBE)
            + r"$",
        )

    def test_a_multi_line_notice_stays_one_line_in_the_inbox(self):
        # The watcher's header documents the one-line invariant: embedded newlines
        # become Enters. The fallback payload must flatten them too.
        result = self._timeout_send(text="first line\n\nsecond   line\nthird")
        self.assertTrue(INBOX_PATH.exists(), "the notice was dropped")
        lines = INBOX_PATH.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("first line second line third", lines[0])
        self.assertEqual(result.channel, "inbox")

    def test_the_fallback_appends_and_never_truncates_the_inbox(self):
        INBOX_PATH.write_text(
            "[2026-01-01 00:00:00 UTC] [PRIOR] keep me\n", encoding="utf-8"
        )
        self._timeout_send()
        data = INBOX_PATH.read_text(encoding="utf-8")
        self.assertIn("keep me", data, "open('w') would have truncated the inbox")
        self.assertEqual(len(data.splitlines()), 2, data)

    def test_inbox_record_uses_the_fallback_label_when_the_label_is_empty(self):
        # #4842 re-review: the previous form compared the record against the
        # constant itself, so renaming `INBOX_FALLBACK_LABEL` stayed green. Pin
        # the LITERAL value in the produced record, not the symbol.
        self.assertEqual(cd.INBOX_FALLBACK_LABEL, "cmux-dispatch")
        line = cd.inbox_record("", PROBE)
        self.assertIn("[cmux-dispatch]", line)
        self.assertTrue(line.endswith(PROBE))

    def test_inbox_record_flattens_label_and_text_into_one_line(self):
        line = cd.inbox_record("B7]\n[1999-01-01 00:00:00 XX] [INJECTED", "a\nb   c")
        self.assertNotIn("\n", line)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn("[B7] [1999-01-01 00:00:00 XX] [INJECTED]", line)
        self.assertTrue(line.endswith("a b c"), line)

    def test_a_newline_in_the_label_cannot_forge_a_second_record(self):
        cd.append_to_inbox("B7]\n[1999-01-01 00:00:00 XX] [INJECTED", PROBE)
        lines = INBOX_PATH.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"^\[\d{4}-\d{2}-\d{2} .+\] \[B7\] ")

    def test_default_inbox_is_the_orchestrator_inbox(self):
        self.assertEqual(cd.DEFAULT_INBOX, "~/.pi/agent/state/orchestrator-inbox.log")
        with mock.patch.dict(os.environ, {cd.INBOX_ENV: ""}):
            self.assertEqual(
                cd.orchestrator_inbox_path(),
                Path("~/.pi/agent/state/orchestrator-inbox.log").expanduser(),
            )

    # -- fallback failure is a diagnostic, never a crash ------------------------- #

    def test_both_channels_failing_names_no_channel_and_carries_both_diagnostics(self):
        with mock.patch.object(
            cd, "append_to_inbox", side_effect=OSError("permission denied")
        ):
            result = self._timeout_send()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "transport-error")
        self.assertEqual(result.channel, "none")
        self.assertIn("timed out", result.detail)
        self.assertIn("permission denied", result.detail)
        self.assertIn("not recorded anywhere", result.detail)
        self.assertFalse(INBOX_PATH.exists())
        self.assertEqual(cd._EXIT_FOR_STATUS[result.status], 3)

    def test_a_real_write_failure_is_reported_not_raised(self):
        # Hermetic, mock-free: point the inbox UNDER A FILE. `mkdir` there raises
        # an OSError, so the REAL `append_to_inbox` failure path is exercised.
        blocker = _INBOX_DIR / "afile"
        blocker.write_text("not a directory", encoding="utf-8")
        with mock.patch.dict(os.environ, {cd.INBOX_ENV: str(blocker / "x.log")}):
            with self.assertRaises(OSError):
                cd.append_to_inbox("B7", PROBE)
            result = self._timeout_send()
        self.assertEqual(result.status, "transport-error")
        self.assertEqual(result.channel, "none")
        self.assertIn("not recorded anywhere", result.detail)

    def test_a_broad_non_oserror_failure_is_reported_not_raised(self):
        # `Path.expanduser()` raises RuntimeError (an unexpandable home), which a
        # narrow `except OSError` would let escape as a traceback with no result.
        with mock.patch.object(
            cd, "orchestrator_inbox_path", side_effect=RuntimeError("no home")
        ):
            result = self._timeout_send()
        self.assertEqual(result.channel, "none")
        self.assertIn("no home", result.detail)
        self.assertIn("not recorded anywhere", result.detail)

    def test_a_lone_surrogate_is_reported_not_raised(self):
        # `handle.write` raises UnicodeEncodeError for a lone surrogate (an
        # `OSError` it is NOT). The result must still report both diagnostics.
        result = self._timeout_send(label="B7\ud800")
        self.assertEqual(result.channel, "none")
        self.assertIn("not recorded anywhere", result.detail)

    def test_transport_and_consumption_failures_are_distinct_outcomes(self):
        transport = self._timeout_send()
        unconsumed = _dispatcher(FakeCmux(never_consumes=True)).send_message(
            "workspace:99", PROBE, ready_timeout=0.0, consume_timeout=0.0, retries=0
        )
        self.assertEqual(transport.status, "transport-error")
        self.assertEqual(unconsumed.status, "sent-but-not-consumed")
        self.assertNotEqual(transport.status, unconsumed.status)
        self.assertEqual(cd._EXIT_FOR_STATUS["transport-error"], 3)
        self.assertEqual(cd._EXIT_FOR_STATUS.get("sent-but-not-consumed", 1), 1)
        self.assertNotEqual(cd._EXIT_FOR_STATUS.get(unconsumed.status, 1), 3)

    def test_cli_reports_the_inbox_channel_and_still_exits_three(self):
        out = io.StringIO()
        with (
            mock.patch.object(
                subprocess, "run", side_effect=subprocess.TimeoutExpired("cmux", 30)
            ),
            redirect_stdout(out),
        ):
            rc = cd.main(
                [
                    "send", "--workspace", "workspace:79", "--text", PROBE,
                    "--label", "B7", "--appear-timeout", "0", "--ready-timeout", "0",
                ]
            )
        self.assertEqual(rc, 3)
        self.assertTrue(INBOX_PATH.exists(), "the notice was dropped")
        self.assertIn("FAIL", out.getvalue())
        self.assertIn("channel: inbox", out.getvalue())


class TestBracketedProgramOutputIsNotAFooterRow(unittest.TestCase):
    """#7918 — the fail-open #7875 shipped to `main` must not come back.

    To survive a clobbered pwd row (#7863), #7875 widened the footer anchor to
    accept ANY bracketed line as a "footer row". But a bracketed line is
    PROGRAM OUTPUT, so the anchor then trusts whatever the process printed: a
    bare shell that writes a bracketed line above a stats-shaped line moves the
    anchor PAST the prompt, `shell_prompt_below_footer` scans only below itself,
    and `screen_ready` returns True — the brief is then typed into a live shell
    and EXECUTED. That is the #7158 direction this tool exists to prevent.

    Measured on `main@8dc62a407` while #7875 was on it (`_footer_stats_end` = 47,
    `shell_prompt_below_footer` = False, `screen_ready` = True); this class
    reddens if the anchor is ever widened again.
    """

    _BARE_SHELL = ("host % cat notes.txt\n"
                   "[INFO] starting\n"
                   "42.0%/700k (auto)\n")

    def test_a_bracketed_output_line_cannot_move_the_anchor_past_a_prompt(self):
        self.assertTrue(cd.status_bar_present(self._BARE_SHELL),
                        "precondition: this fixture must have a status bar")
        self.assertTrue(
            cd.shell_prompt_below_footer(self._BARE_SHELL),
            "the live prompt above the bracketed line was not seen")
        self.assertFalse(
            cd.screen_ready(self._BARE_SHELL),
            "FAIL-OPEN: a bracketed program-output line moved the anchor past "
            "a live prompt — this types the brief into a bare shell")

    def test_a_bracketed_line_is_not_accepted_as_a_footer_row(self):
        # The anchor may trust the pwd line and NOTHING else.
        self.assertGreater(
            cd._footer_stats_end("~/proj\n42.0%/700k (auto)\n"), 0,
            "a pwd line above the stats line must still anchor the scan")
        self.assertEqual(
            cd._footer_stats_end("[INFO] starting\n42.0%/700k (auto)\n"), -1,
            "a bracketed program-output line was accepted as a footer row — "
            "this is the #7918 fail-open")
        self.assertFalse(cd._is_pwd_line("[INFO] starting"))

    def test_this_fleets_own_status_tags_cannot_anchor_either(self):
        # The widening was motivated by THIS fleet's status tags, so they are
        # the adversarial case: these bytes appear on real panes, so a shell can
        # print them. None may anchor the scan.
        for tag in ("[loop-enforcer] agent_end FIRED",
                    "[tortoise-capture] appended 12 messages",
                    "[repo-freshness] auto-pull 1 behind origin/main",
                    "[session-checks] hub-state-check: skipped"):
            with self.subTest(tag=tag):
                self.assertEqual(
                    cd._footer_stats_end(f"{tag}\n42.0%/700k (auto)\n"), -1,
                    f"{tag!r} anchored the scan — a shell can print this")
                self.assertFalse(cd._is_pwd_line(tag))


class TestIssue7913UnreadablePaneFallback(unittest.TestCase):
    """#7913: an EMPTY read must not be a statement about the lane.

    A lane whose session transcript advanced recently has a live pi whatever the
    pane read returned, so it must stay DISPATCHABLE; a lane with no fresh
    transcript must still be REFUSED (the #7158 fail-closed direction).
    """

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="cmux-7913-sessions-"))
        self._saved = os.environ.get(cd.SESSIONS_ROOT_ENV)
        os.environ[cd.SESSIONS_ROOT_ENV] = str(self._dir)
        self._clock = FakeClock()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(cd.SESSIONS_ROOT_ENV, None)
        else:
            os.environ[cd.SESSIONS_ROOT_ENV] = self._saved
        shutil.rmtree(self._dir, ignore_errors=True)

    def _session(self, cwd: str, age_s: float, sid: str = SESSION_7913) -> Path:
        session_dir = self._dir / cd.mangle_cwd(cwd)
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / f"2026-10-10T00-00-00-000Z_{sid}.jsonl"
        path.write_text('{"type":"user","message":"x"}\n')
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
        return path

    def _send(self, fake, **kwargs):
        logs: list[str] = []
        dispatcher = cd.Dispatcher(
            fake, sleep=self._clock.sleep, now=self._clock.now, log=logs.append
        )
        result = dispatcher.send_message("workspace:99", PROBE, label="B4", **kwargs)
        return result, logs

    def test_empty_read_with_a_fresh_session_file_is_NOT_refused(self):
        cwd = "/private/tmp"
        self._session(cwd, age_s=5.0)
        fake = FakeCmux(screen_unreadable=True, cwd=cwd)
        result, logs = self._send(fake, ready_timeout=0.0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, "consumed")
        self.assertIn(PROBE, fake.sent_log, "the brief must be sent")
        self.assertIn(
            "session-mtime", "\n".join(logs), "the evidence used must be logged"
        )

    def test_an_EMPTY_capture_is_treated_like_a_failed_read(self):
        # rc==0 with zero lines is the same instrument failure as rc!=0.
        cwd = "/private/tmp"
        self._session(cwd, age_s=5.0)

        class EmptyCaptureCmux(FakeCmux):
            def read_screen(self, workspace, lines=80, surface=None):
                return cd.CmuxResult(0, "")

        fake = EmptyCaptureCmux(cwd=cwd)
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(cd.readiness_state(""), cd.ST_UNREADABLE)

    def test_a_fresh_transcript_for_ANOTHER_pane_is_not_evidence_for_this_one(self):
        # #7913 review P1: the transcript must be attributable to the PANE. Lanes
        # sharing one cwd is the measured norm (13 of 22 workspaces, 245
        # transcripts in that bucket), so a SIBLING lane's fresh transcript must
        # not green-light a write into a dead lane's bare shell — that is exactly
        # how #7158's harm returns through a new door.
        cwd = "/private/tmp"
        self._session(cwd, age_s=5.0, sid="11111111-1111-4111-8111-111111111111")
        fake = FakeCmux(screen_unreadable=True, cwd=cwd)
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok, "a sibling lane's transcript is not evidence")
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(result.condition, "unreadable-pane")
        self.assertEqual(fake.sent_log, [], "no bytes may be written blind")

    def test_an_unbindable_pane_is_refused_even_with_a_fresh_transcript(self):
        # No resume binding -> the transcript cannot be attributed -> UNKNOWN.
        # fleet_state.refresh refuses an ambiguous candidate set for the same
        # reason: a guess here writes into whatever pane happens to share the cwd.
        cwd = "/private/tmp"
        self._session(cwd, age_s=5.0)
        fake = FakeCmux(screen_unreadable=True, cwd=cwd, session_id="")
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(fake.sent_log, [], "no bytes may be written blind")
        self.assertEqual(result.condition, "unreadable-pane")

    def test_a_zero_byte_session_file_is_not_a_liveness_signal(self):
        # A transcript created but never written is a pi with no turn, and an
        # EMPTY file cannot have “advanced”: it must not certify liveness.
        cwd = "/private/tmp"
        path = self._session(cwd, age_s=5.0)
        path.write_text("")
        fake = FakeCmux(screen_unreadable=True, cwd=cwd)
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(fake.sent_log, [])
        self.assertEqual(result.condition, "unreadable-pane")

    def test_a_future_mtime_is_unmeasurable_and_fails_closed(self):
        # Clamping a future mtime to age 0 made a lane look maximally fresh
        # forever. An age that cannot be measured must refuse.
        cwd = "/private/tmp"
        self._session(cwd, age_s=-3600.0)  # an hour AHEAD of the clock
        fake = FakeCmux(screen_unreadable=True, cwd=cwd)
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(fake.sent_log, [])
        self.assertEqual(result.condition, "unreadable-pane")

    def test_a_binding_that_is_not_an_id_shape_is_refused(self):
        # `session_id` is interpolated into a glob. A `*` binding would widen it
        # to the store's NEWEST transcript and re-attribute a sibling lane's
        # liveness to this dead pane — the exact #7913 defect (#7913 review).
        cwd = "/private/tmp"
        self._session(cwd, age_s=5.0)
        entry = {"current_directory": cwd}
        for bad in ("*", "?", "[a-z]", "a*b", "../x", "a b", ""):
            with self.subTest(sid=bad):
                self.assertIsNone(cd.session_file_for(bad, entry))
        self.assertIsNotNone(cd.session_file_for(SESSION_7913, entry))

    def test_a_blind_RE_READ_is_not_rescued_by_the_gate_it_follows(self):
        # The gate accepted on a READABLE footer; the PRE-SEND read then came
        # back empty. A fresh transcript must not smuggle the send past a gate
        # that could actually see the pane — the two reads disagree, and on a
        # fail-closed path disagreement means REFUSE (#7913 review).
        cwd = "/private/tmp"
        self._session(cwd, age_s=5.0)

        class ReadyThenBlindCmux(FakeCmux):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.blind_after = 1

            def read_screen(self, workspace, lines=80, surface=None):
                self.read_calls += 1
                if self.read_calls <= self.blind_after:
                    return cd.CmuxResult(0, SCREEN_IDLE_READY)
                return cd.CmuxResult(0, "")

        fake = ReadyThenBlindCmux(cwd=cwd)
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok, "a blind re-read must not be rescued")
        self.assertEqual(result.condition, "unreadable-pane")
        self.assertEqual(fake.sent_log, [], "no bytes may be written blind")

    def test_the_refusal_names_WHY_not_just_unreadable(self):
        # Six causes, six remediations, one indistinguishable message before the
        # #7913 review: the probe's own diagnosis must reach the operator.
        fake = FakeCmux(screen_unreadable=True, session_id="")
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.condition, "unreadable-pane")
        self.assertIn("no resume binding for this pane", result.detail)

    def test_the_pane_binding_is_resolved_once_per_dispatch_not_once_per_poll(self):
        # The gate re-probes on EVERY poll, and each resolution is a `cmux surface
        # resume show` subprocess — measured 91 spawns for one dispatch at the
        # 180s/2s defaults (#7913 review, load).
        cwd = "/private/tmp"
        self._session(cwd, age_s=cd.DEFAULT_SESSION_FRESH_S + 60)

        class CountingCmux(FakeCmux):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.binding_calls = 0

            def pane_session_id(self, workspace, surface=None):
                self.binding_calls += 1
                return super().pane_session_id(workspace, surface)

        fake = CountingCmux(screen_unreadable=True, cwd=cwd)
        dispatcher = cd.Dispatcher(
            fake, sleep=self._clock.sleep, now=self._clock.now
        )
        result = dispatcher.send_message(
            "workspace:99", PROBE, label="B4", ready_timeout=10.0
        )
        self.assertFalse(result.ok)
        self.assertGreater(
            self._clock.now(), 0.0, "the fake clock must have polled at all"
        )
        self.assertEqual(
            fake.binding_calls,
            1,
            "the binding is a per-dispatch fact, not a per-poll subprocess",
        )

    def test_a_transient_probe_failure_does_not_disable_the_fallback(self):
        # A cmux call DOES time out under load (#4842). Memoizing "probe failed"
        # as "no binding" let one flaky poll refuse the lane for the whole
        # dispatch AND report the wrong cause (#7913 review).
        cwd = "/private/tmp"
        self._session(cwd, age_s=5.0)

        class FlakyProbeCmux(FakeCmux):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.probe_calls = 0

            def pane_session_id(self, workspace, surface=None):
                self.probe_calls += 1
                if self.probe_calls == 1:
                    return cd.PROBE_UNAVAILABLE
                return super().pane_session_id(workspace, surface)

        fake = FlakyProbeCmux(screen_unreadable=True, cwd=cwd)
        result, logs = self._send(fake, ready_timeout=10.0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, "consumed")
        self.assertGreaterEqual(fake.probe_calls, 2, "a failed probe must be re-asked")
        self.assertIn("session-mtime", "\n".join(logs))

    def test_the_memo_is_keyed_per_SURFACE_not_per_workspace(self):
        # Keyed on the workspace alone, the FIRST surface's answer would be served
        # for the SECOND — the #7913 P1 in cache form. (A fresh Dispatcher per
        # operation hides this today, which is exactly why it needs a test.)
        a = "33333333-3333-4333-8333-333333333333"
        b = "44444444-4444-4444-8444-444444444444"
        fake = FakeCmux(
            bindings_by_surface={"surface:1": a, "surface:2": b},
        )
        dispatcher = cd.Dispatcher(fake, sleep=self._clock.sleep, now=self._clock.now)
        self.assertEqual(
            dispatcher.pane_binding("workspace:14", "surface:1"), (True, a)
        )
        self.assertEqual(
            dispatcher.pane_binding("workspace:14", "surface:2"), (True, b)
        )

    def test_a_definitive_absence_is_resolved_once_per_dispatch(self):
        # `None` IS an answer, so it is memoized: resolution stays at one cmux
        # call even though the gate polls (the load the memo exists for).
        cwd = "/private/tmp"
        self._session(cwd, age_s=5.0)

        class CountingCmux(FakeCmux):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.binding_calls = 0

            def pane_session_id(self, workspace, surface=None):
                self.binding_calls += 1
                return super().pane_session_id(workspace, surface)

        fake = CountingCmux(screen_unreadable=True, cwd=cwd, session_id="")
        dispatcher = cd.Dispatcher(
            fake, sleep=self._clock.sleep, now=self._clock.now
        )
        result = dispatcher.send_message(
            "workspace:99", PROBE, label="B4", ready_timeout=10.0
        )
        self.assertFalse(result.ok)
        self.assertIn("no resume binding for this pane", result.detail)
        self.assertEqual(fake.binding_calls, 1)

    def test_a_pane_stuck_on_the_boot_block_prompt_reports_boot_blocked(self):
        # The `boot-blocked` value had NO test at all: mutating it to `not-ready`
        # (or `""`) left the whole file green, so the diagnosis could regress
        # silently (#7913 review).
        fake = FakeCmux(boot_block=True)
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(result.condition, "boot-blocked")
        self.assertNotIn(
            PROBE, fake.sent_log, "the brief must never be typed at that prompt"
        )

    def test_a_boot_blocked_RE_READ_is_reported_as_boot_blocked(self):
        # The two re-assert sites used to hand-roll this mapping, so a window that
        # was READABLE but sitting on the boot-block prompt was labelled
        # `not-ready` — a different remedy, and the exact disagreement
        # `condition_for` exists to prevent (#7913 review).
        cwd = "/private/tmp"
        self._session(cwd, age_s=5.0)

        class EmptyGateThenBlockedCmux(FakeCmux):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.reads = 0

            def read_screen(self, workspace, lines=80, surface=None):
                self.reads += 1
                if self.reads == 1:
                    # The gate: UNREADABLE, rescued by this pane's transcript.
                    return cd.CmuxResult(0, "")
                # The pre-send re-assert: READABLE, but blocked.
                return cd.CmuxResult(0, SCREEN_BOOT_BLOCK)

        fake = EmptyGateThenBlockedCmux(cwd=cwd)
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok, "a blocked re-read must not be rescued")
        self.assertEqual(
            result.condition,
            cd.condition_for(SCREEN_BOOT_BLOCK),
            "the condition must agree with the classification it came from",
        )
        self.assertEqual(result.condition, "boot-blocked")

    def test_a_sibling_SURFACE_cannot_certify_this_pane(self):
        # #7913 review P1: `cmux surface resume show` with no `--surface` answers
        # for the workspace's SELECTED surface, but one workspace can hold several
        # surfaces bound to DIFFERENT sessions (measured 2026-10-10: `workspace:14`
        # bound `surface:15` and `surface:16` to two distinct checkpoint ids).
        # Probing the workspace would let surface:15's live pi certify a blind
        # write into surface:16 — #7158's harm one granularity narrower than the
        # cwd scoping already fixed.
        cwd = "/private/tmp"
        live = "22222222-2222-4222-8222-222222222222"
        self._session(cwd, age_s=5.0, sid=live)
        fake = FakeCmux(
            screen_unreadable=True,
            cwd=cwd,
            bindings_by_surface={"surface:15": live, "surface:16": None},
        )
        dispatcher = cd.Dispatcher(fake, sleep=self._clock.sleep, now=self._clock.now)
        result = dispatcher.send_message(
            "workspace:14", PROBE, surface="surface:16", label="B4", ready_timeout=0.0
        )
        self.assertFalse(
            result.ok, "a sibling surface's live pi is not evidence about this pane"
        )
        self.assertEqual(fake.sent_log, [], "no bytes may be written blind")

    def test_a_legacy_seam_cannot_answer_for_a_NAMED_surface(self):
        # A `pane_session_id(workspace)` seam predating surface-scoping answers for
        # the workspace's SELECTED surface. Accepting that answer for a NAMED
        # target surface is the P1 in cache form (#7913 review).
        class LegacyCmux(FakeCmux):
            def pane_session_id(self, workspace):  # no surface parameter
                return "99999999-9999-4999-8999-999999999999"

        dispatcher = cd.Dispatcher(LegacyCmux(), sleep=self._clock.sleep)
        self.assertEqual(
            dispatcher.pane_binding("workspace:14", "surface:16"),
            (False, None),
            "a one-arg seam must not answer for a named surface",
        )
        self.assertEqual(
            dispatcher.pane_binding("workspace:14", None)[1],
            "99999999-9999-4999-8999-999999999999",
            "with no surface named, the one-arg answer names the same pane",
        )

    def test_the_refusal_names_a_MALFORMED_binding(self):
        # A binding that is not an id shape is refused AND said to be malformed:
        # reporting it as "no transcript for this pane" sends the operator after
        # the session store instead of after the binding (#7913 review).
        fake = FakeCmux(screen_unreadable=True, session_id="not an id")
        dispatcher = cd.Dispatcher(fake, sleep=self._clock.sleep, now=self._clock.now)
        fresh, detail = dispatcher.session_liveness(
            "workspace:99", {"current_directory": "/private/tmp"}
        )
        self.assertFalse(fresh)
        self.assertIn("not a session id", detail)

    def test_the_refusal_distinguishes_an_UNASKABLE_probe(self):
        # "The probe could not be asked" must not read as "this pane has no
        # binding": the remedies are opposite (fix cmux vs re-bind the lane).
        class UnaskableCmux(FakeCmux):
            def pane_session_id(self, workspace, surface=None):
                return cd.PROBE_UNAVAILABLE

        dispatcher = cd.Dispatcher(
            UnaskableCmux(screen_unreadable=True), sleep=self._clock.sleep
        )
        fresh, detail = dispatcher.session_liveness("workspace:99", None)
        self.assertFalse(fresh)
        self.assertIn("could not be asked", detail)

    def test_empty_read_with_a_STALE_session_is_still_refused(self):
        cwd = "/private/tmp"
        self._session(cwd, age_s=cd.DEFAULT_SESSION_FRESH_S + 60)
        fake = FakeCmux(screen_unreadable=True, cwd=cwd)
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(result.condition, "unreadable-pane")
        self.assertEqual(fake.sent_log, [], "no bytes may be written blind")

    def test_empty_read_with_NO_resolvable_session_is_refused(self):
        fake = FakeCmux(screen_unreadable=True)   # no cwd -> no session
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(fake.sent_log, [])

    def test_a_READABLE_pane_with_no_footer_is_never_rescued_by_session_evidence(self):
        # #7158 must stay fail-closed: a bare shell is a real claim about the lane,
        # so a fresh sibling transcript must NOT green-light a write.
        cwd = "/private/tmp"
        self._session(cwd, age_s=5.0)
        fake = BareShellCmux(cwd=cwd)
        result, _ = self._send(fake, ready_timeout=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "never-became-ready")
        self.assertEqual(result.condition, "not-ready")
        self.assertEqual(
            fake.sent_log, [],
            "session-mtime must not override a readable bare shell",
        )

    def test_stale_session_mtime_is_reported_not_asserted(self):
        cwd = "/private/tmp"
        self._session(cwd, age_s=cd.DEFAULT_SESSION_FRESH_S + 1)
        fake = FakeCmux(screen_unreadable=True, cwd=cwd)
        dispatcher = cd.Dispatcher(
            fake, sleep=self._clock.sleep, now=self._clock.now
        )
        fresh, detail = dispatcher.session_liveness(
            "workspace:99", {"current_directory": cwd}
        )
        self.assertFalse(fresh)
        self.assertIn("advanced", detail)


class TestIssue7913ReadinessClassification(unittest.TestCase):
    def test_readiness_state_classifies_the_instrument_not_the_lane(self):
        self.assertEqual(cd.readiness_state(None), cd.ST_UNREADABLE)
        self.assertEqual(cd.readiness_state(""), cd.ST_UNREADABLE)
        self.assertEqual(cd.readiness_state("   \n"), cd.ST_UNREADABLE)
        self.assertEqual(cd.readiness_state(SCREEN_BOOT_BLOCK), cd.ST_BLOCKED)
        self.assertEqual(cd.readiness_state(SCREEN_IDLE_READY), cd.ST_READY)
        self.assertEqual(cd.readiness_state(SCREEN_BARE_SHELL), cd.ST_NOT_READY)

    def test_unreadable_cannot_be_read_as_a_lane_state(self):
        # The vocabulary is load-bearing: it is what the beat branches on. Drive
        # the CLASSIFIER on the real renders rather than asserting a constant
        # against a literal, so a collision introduced in `readiness_state` is
        # caught here (#7913 review).
        self.assertEqual(cd.readiness_state(""), cd.ST_UNREADABLE)
        self.assertEqual(cd.readiness_state(None), cd.ST_UNREADABLE)
        self.assertEqual(cd.readiness_state(SCREEN_BARE_SHELL), cd.ST_NOT_READY)
        self.assertEqual(cd.readiness_state(SCREEN_IDLE_READY), cd.ST_READY)
        for lane_state in ("WEDGED", "STALL", "IDLE", "WIP"):
            self.assertNotIn(
                lane_state,
                {
                    cd.readiness_state(""),
                    cd.readiness_state(None),
                    cd.readiness_state(SCREEN_BOOT_BLOCK),
                    cd.readiness_state(SCREEN_IDLE_READY),
                    cd.readiness_state(SCREEN_BARE_SHELL),
                },
                "an instrument state must never collide with a lane state",
            )

    def test_completion_menu_is_detected_from_the_renderer_shape(self):
        screen = SCREEN_IDLE_READY.replace(
            "\n0.0%/700k", "\n \u2192 ci-checks/\n0.0%/700k", 1
        )
        self.assertTrue(cd.completion_menu_open(screen))

    def test_an_arrow_ABOVE_the_rule_is_not_a_menu(self):
        screen = "\u2192 not a menu\n" + SCREEN_IDLE_READY
        self.assertFalse(cd.completion_menu_open(screen))

    def test_a_plain_ready_screen_has_no_menu(self):
        self.assertFalse(cd.completion_menu_open(SCREEN_IDLE_READY))


class TestIssue7913ComposerHygiene(unittest.TestCase):
    def _send(self, fake, **kwargs):
        clock = FakeClock()
        dispatcher = cd.Dispatcher(
            fake, sleep=clock.sleep, now=clock.now, log=lambda _m: None
        )
        return dispatcher.send_message("workspace:99", PROBE, label="B4", **kwargs)

    def test_dismiss_keystroke_precedes_the_text_when_a_menu_is_open(self):
        fake = FakeCmux(menu_open=True)
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertTrue(result.ok, result.detail)
        self.assertIn("\x1b", fake.sent_log, "a dismiss keystroke must be sent")
        self.assertLess(
            fake.sent_log.index("\x1b"),
            fake.sent_log.index(PROBE),
            "Escape must be sent BEFORE the brief is typed",
        )

    def test_leftover_composer_text_is_cleared_before_the_brief(self):
        fake = FakeCmux(pre_typed="leftover text from a previous dispatch ")
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(
            fake.submitted, [PROBE],
            "the brief must not concatenate onto leftover text",
        )
        self.assertIn("\x15", fake.sent_log, "Ctrl-U must clear the line")

    def test_clear_composer_dismisses_a_menu_that_is_the_last_line(self):
        # Literal #7913 acceptance shape: a screen whose LAST LINE is a completion
        # menu. The pre-send sequence must include the dismiss keystroke BEFORE
        # anything else so the menu cannot eat the submit Enter.
        fake = FakeCmux()
        sent: list[str] = []
        fake.send_escape = lambda ws, surface=None: (
            sent.append("escape") or cd.CmuxResult(0, "OK")
        )
        fake.send_ctrl_u = lambda ws, surface=None: (
            sent.append("ctrl-u") or cd.CmuxResult(0, "OK")
        )
        clock = FakeClock()
        dispatcher = cd.Dispatcher(
            fake, sleep=clock.sleep, now=clock.now, log=lambda _m: None
        )
        screen = (
            "\u2500" * 36 + "\n" + PROBE + "\n\n" + "\u2500" * 36
            + "\n \u2192 ci-checks/\n"
        )
        self.assertTrue(cd.completion_menu_open(screen))
        returned = dispatcher.clear_composer("workspace:99", screen)
        self.assertEqual(returned, ["escape", "ctrl-u"])
        self.assertEqual(sent, ["escape", "ctrl-u"])

    def test_no_escape_when_no_menu_is_open(self):
        fake = FakeCmux()
        result = self._send(fake)
        self.assertTrue(result.ok)
        self.assertNotIn(
            "\x1b", fake.sent_log,
            "Escape with no menu open would ABORT the live turn",
        )

    def test_menu_that_our_own_text_opens_is_dismissed_then_released(self):
        fake = MenuOpensOnOurTextCmux()
        result = self._send(fake, consume_timeout=0.0, retries=1)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, "consumed")
        self.assertIn(cd.R_DISMISS_RELEASE, result.recoveries)
        self.assertEqual(
            fake.sent_log.count(PROBE), 1, "the brief must not be re-sent"
        )
        self.assertEqual(fake.submitted, [PROBE])


class TestIssue7913FailureCondition(unittest.TestCase):
    def test_recovery_action_dismisses_a_menu_instead_of_a_bare_enter(self):
        screen = (
            "\u2500" * 36 + "\n" + PROBE + "\n\n" + "\u2500" * 36
            + "\n \u2192 ci-checks/\n"
        )
        self.assertEqual(
            cd.recovery_action(screen, cd.fingerprint(PROBE), PROBE),
            cd.R_DISMISS_RELEASE,
        )

    def test_unreadable_confirmation_reports_unreadable_pane(self):
        class DiesAtConfirmation(FakeCmux):
            """Ready through gate + pre-send; every read after that fails."""

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.my_reads = 0

            def read_screen(self, workspace, lines=80, surface=None):
                self.my_reads += 1
                if self.my_reads <= 2:
                    return super().read_screen(workspace, lines, surface)
                return cd.CmuxResult(1, "", "cmux read-screen: timed out")

        fake = DiesAtConfirmation(never_consumes=True)
        clock = FakeClock()
        dispatcher = cd.Dispatcher(
            fake, sleep=clock.sleep, now=clock.now, log=lambda _m: None
        )
        result = dispatcher.send_message(
            "workspace:99", PROBE, label="B4", consume_timeout=0.0, retries=0
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.condition, "unreadable-pane")
        self.assertIn("condition: unreadable-pane", result.detail)

    def test_unsent_composer_reports_composer_not_submitted(self):
        fake = FakeCmux(never_consumes=True, no_rules=True)
        clock = FakeClock()
        dispatcher = cd.Dispatcher(
            fake, sleep=clock.sleep, now=clock.now, log=lambda _m: None
        )
        result = dispatcher.send_message(
            "workspace:99", PROBE, label="B4", consume_timeout=0.0, retries=0
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.condition, "composer-not-submitted")
        self.assertIn("condition: composer-not-submitted", result.detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
