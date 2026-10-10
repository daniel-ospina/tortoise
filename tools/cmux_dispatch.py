#!/usr/bin/env python3
"""cmux_dispatch.py — artifact-verified dispatch to a cmux pane (#4292).

WHY THIS EXISTS
---------------
`cmux send` reports success when *bytes were written to the terminal*, not when
the bytes became a pi conversation message. Two distinct failures sit downstream
of that syscall and are therefore invisible to any exit code:

  1. SEND-DURING-BOOT RACE. Bytes written before pi's TUI takes over stdin land
     in the terminal input buffer. The text can sit unsent in the composer
     forever, or be discarded. `cmux send` returns 0 for both calls.
  2. BOOT-BLOCK PROMPT. A freshly-booted pi can print

         Press any key to continue...

     and await `process.stdin.once("data")`
     (pi `dist/migrations.js::showDeprecationWarnings`, reached from
     `dist/main.js` when `appMode === "interactive"` and the deprecation
     warnings list is non-empty — e.g. a `~/.pi/agent/tools/` directory holding
     anything other than the auto-extracted fd/rg binaries). The prompt consumes
     whatever arrives *as its keypress*, so a pointer sent at that moment is
     EATEN — or, worse, its prefix is eaten and the remainder is submitted as a
     truncated turn. Boot never completes until some byte arrives.

The rule this tool enforces: **verify the ARTIFACT, not the send.** Confirmation
is a read of whether the message became a conversation message
(`cmux list-workspaces --json` -> `latest_submitted_message`) OR entered pi's
PENDING-TURN queue (the pane's `Steering:` / `Follow-up:` display), with the pane
screen as the discriminator between "sitting unsent in the composer" (release
with a bare Enter) and "never arrived" (re-send). A dispatch that cannot be
confirmed exits non-zero with `sent-but-not-consumed`; it reports SUCCESS (exit 0)
only when the message became the latest submitted message. A NEW pending-turn entry
carrying it is POSITIVE evidence of DELIVERY — exit 4, not 0 (see QUEUED IS NOT
SUCCESS). That evidence is pi's own render string, not
a secret, so a verdict means pi's display showed acceptance, not proof of it (see
RESIDUAL: a lane that deliberately prints pi's hint line can forge it).

QUEUED IS DELIVERED, NOT A LOST SEND (#5979)
--------------------------------------------
`latest_submitted_message` only advances at a TURN BOUNDARY. On a lane that is
mid-turn — i.e. every lane that is actually working — pi ACCEPTS a submission
into its queue and consumes it when the current turn ends, so the strict "is it
the latest submitted message?" test can never be satisfied inside any bounded
wait. The old verdict called that `sent-but-not-consumed` and the message was in
fact delivered; three callers (`ask-owner.py`, `turn-classify.py`, `turn-end.py`)
each discovered this independently and worked around it.

The verdict is therefore queue-aware, and it keys on POSITIVE evidence that pi
accepted THIS submission: pi's pending-turn display
(`dist/modes/interactive/interactive-mode.js::updatePendingMessagesDisplay`)
renders the queued text as `Steering: <text>` / `Follow-up: <text>`, followed by a
single `\u21b3 \u2026 to edit all queued messages` hint line, in `pendingMessagesContainer`
— a dock region below the transcript and above the editor. The container is
located STRUCTURALLY (the run of entries immediately above a hint line carrying
pi's OWN hint text), not by scanning for a `Steering:`-shaped line anywhere: a
lane's scrollback legitimately contains arbitrary such text. A pending entry that
is NEW (a higher COUNT of matching entries) relative to the pre-send screen and
carries THIS message — exactly, or as a truncated head whose raw text ends in the
renderer's `...` — is direct evidence the message entered pi's queue -> `queued`,
DELIVERED but not CONSUMED (exit 4 — see EXIT CODES; #7743). `Dispatcher.send_message` flattens newlines up front (as `_read_message`
does), because pi renders a submission as a single line and a first line is not
message-unique.

The match is on the whole message, not the 40-char fingerprint: a fingerprint is
not message-unique (the fleet's own nudges share a head, and a short dispatch's
fingerprint IS the whole dispatch), and accepting any pending line that merely
starts with it once confirmed a DROPPED `"continue"` against an unrelated queued
`"continue with the migration"`. Truncation must be EVIDENCED by the `...` the
renderer appends AND the head must be strictly shorter than the message — a
shorter line without an ellipsis is a different message, and a longer message cut
to our length is a different message too. Novelty is required for the same reason
`is_consumed` requires it: a line already on screen before the send is not
evidence about this send.

⚠️ RESIDUAL (known, not hidden): the hint text is pi's own public render string,
and the capture also carries arbitrary lane output, so a lane that DELIBERATELY
prints `Steering: <our message>` directly above `\u21b3 \u2026 to edit all queued
messages` can still forge the verdict. Ordinary output does not (the hint text and
that adjacency are what the bound requires), and a duplicate/stale frame is
rejected by novelty. Closing this fully needs a per-send capability token that only
a pi which RECEIVED the bytes could echo; that is a design change, not a matcher
fix, and is not attempted here.

TWO THINGS THIS DELIBERATELY DOES NOT DO:

  * The pane's COMPOSER is not evidence of a queue. On submit-while-streaming pi
    runs `editor.setText("")` BEFORE queueing, so a real queue leaves the
    composer EMPTY, and text still sitting in the composer is the UNSENT state
    (`SCREEN_COMPOSING_UNSENT`, "UNSENT, no turn"). Composer occupancy is an
    input to the RECOVERY decision only — it selects the non-duplicating
    `release-only` (bare Enter) recovery — and can never produce exit 0 on its
    own. The `queued` verdict does not read the composer at all.
  * TRANSCRIPT GROWTH is not evidence either, and is deliberately not wired in.
    The callers that trust it combine it with a SENTINEL CONTENT match, because
    on a mid-turn lane the transcript is growing for the lane's OWN turn —
    unattributable growth would fabricate success for a message that never
    landed. The attributable variant is unavailable at queue time: pi's
    `_queueSteer` pushes the text into an in-memory array and the session file is
    appended only on `message_end`, i.e. when the message is CONSUMED at the turn
    boundary — by which point `latest_submitted_message` also moves. So the
    pending-turn display is the queue signal.

The fail-closed direction is unchanged: a text that appears in NEITHER a
submitted message NOR a NEW pending line still fails `sent-but-not-consumed`, and
a pane whose `read-screen` failed (`screen` -> None) is never a queue. An
unreadable COMPOSER is not a fail-closed condition for the queue verdict (the
pending display is independent evidence); it only constrains the recovery.

QUEUED IS NOT SUCCESS (#7743)
-----------------------------
`queued` is DELIVERED, not CONSUMED: pi holds the text in its pending queue and
only turns it into a conversation message when the CURRENT turn ends. That is
correct on a healthy mid-turn lane and WRONG on a lane that cannot end its turn —
the #7743 wedge, where the in-flight `task`/`subagent` tool's child is gone and a
queued message can never be drained. There, exit 0 told the caller a wedged lane
had accepted a message it in fact can never read.

The two states are therefore DISTINGUISHED in the exit code, not collapsed:
`consumed` is exit 0, and `queued` is exit 4 — DELIVERED (the JSON `delivered`
field is true, so a caller can choose not to re-send and duplicate — this is
#5979's finding, preserved exactly) but NOT success (the agent has not acted on
it, so a caller that needs the turn to have STARTED must treat it as failure).
`queued` is still never re-sent, for the same no-duplicate reason as before.

PREVENTION, then DETECTION
--------------------------
The confirmation check is a BACKSTOP. The boot-block case has already run a
corrupted turn by the time anything could observe it, so the tool also gates the
send: it refuses to write into a pane that is sitting on the prompt, because
those bytes are what the prompt consumes. Readiness is an asymmetry — the prompt
marker AND the absence of pi's status bar (see `boot_blocked`) — never a
position-based guess.

NO FOOTER, NO SEND (#7158)
--------------------------
Readiness is a POSITIVE signal about the LIVE pane, not the absence of a known
failure: pi's footer (status bar) must be drawn and no shell prompt may appear
BELOW it before the tool writes. Two dead-pane shapes otherwise pass a "is a bar
present anywhere" test — one with no footer at all, and one whose previous pi
session left its footer in the scrollback above a freshly printed shell prompt —
and in both the bytes go to a bare login shell, which EXECUTES them. The earlier
revision treated the no-footer shape as a slow boot and sent anyway
("confirmation will decide"); but confirmation runs AFTER the bytes are written,
so it cannot un-execute a command. The gate now fails CLOSED — a footer drawn
with no shell prompt below it, on the initial attempt AND on the dismiss-and-
resend recovery. A genuinely slow boot is raised via `--ready-timeout`; a refusal
is recoverable, an executed brief is not.

UNREADABLE IS NOT A LANE STATE (#7913)
--------------------------------------
`cmux read-screen` can return NOTHING for a pane whose pi is running (measured:
22 of 24 live workspaces read empty in one minute; a `0`-line read from a pane
whose transcript was written 20 seconds earlier). Treating that as "the pane is
not ready" is a category error: a failed read is an assertion about the
INSTRUMENT, not about the lane, and it made the classifier flap lanes through
WIP/IDLE/STALL/WEDGED at the bidding of the read.

Two things change. First, the read is CLASSIFIED: `readiness_state` returns
`UNREADABLE` for a failed or empty capture, and only `READY`/`NOT_READY` are
assertions about the lane. `UNREADABLE` must never feed the beat's
dispatch advice (`wait-ready --json` exposes it as `state`).

Second, an `UNREADABLE` read is no longer the end of the decision: before
refusing, the tool consults a NON-PANE signal — the pi session transcript of the
PANE'S OWN session, resolved from the workspace's resume binding
(`cmux surface resume show`, the authoritative binding `fleet_state.py` also
uses). A transcript whose mtime advanced within `DEFAULT_SESSION_FRESH_S` is EVIDENCE that a live pi owns THIS pane (it is not
proof — a pi that exited seconds ago still carries a fresh mtime, and #7913
accepts that window deliberately), so the send proceeds and the log line names the evidence
used (`session-mtime` vs `screen`).

⚠ The transcript must be attributable to the pane, NEVER to its cwd. A
cwd-scoped glob is not evidence about a pane: measured 2026-10-10, 13 of 22 live
workspaces shared one `current_directory` and its session bucket held 245
transcripts, so a sibling lane kept a dead lane reading “live” — and an empty
read over a bare shell would have been executed. A workspace whose resume
binding does not resolve is UNKNOWN and the tool REFUSES, exactly as
`fleet_state.resolve_sessions` refuses an ambiguous candidate set rather than
guessing.

The fallback fires ONLY for `UNREADABLE`: a READABLE pane that lacks a footer is
still refused (#7158), because that is a real claim about the lane.

COMPOSER HYGIENE, AND THE MENU THAT EATS ENTER (#7913)
------------------------------------------------------
A second, independent failure produced the same `sent-but-not-consumed` verdict:
an OPEN COMPLETION MENU over the composer. While it is open, `Enter` accepts the
highlighted completion instead of submitting, so the dispatcher's chained Enter
is swallowed and the brief never becomes a turn — and the chosen recovery
(`release-only`, a bare Enter) is the exact keystroke the menu eats.

The pre-send sequence therefore clears the composer BEFORE typing: always
Ctrl-U (`deleteToLineStart`, which cannot interrupt), plus Escape when a menu is
OBSERVED. Escape is sent ONLY when the menu is observed because `Escape` with no
menu showing is `app.interrupt` and ABORTS the lane's live turn (verified in the
installed `CustomEditor.handleInput`); with a menu showing it is `tui.select.cancel`
and merely dismisses it. The menu is detected from the renderer's own selected-row
marker (`→ `) below the composer's bottom rule. If the brief's own text opens a
menu, the confirmation read sees it and the recovery becomes `dismiss-release`
(dismiss, then release) rather than a bare Enter or a duplicate re-send.

USAGE
-----
    uv run python tools/cmux_dispatch.py send --workspace workspace:12 \
        --label B4 --file /path/to/brief.txt
    uv run python tools/cmux_dispatch.py wait-ready --workspace workspace:12
    uv run python tools/cmux_dispatch.py verify --workspace workspace:12 --text "pointer"
    uv run python tools/cmux_dispatch.py state --workspace workspace:12

EXIT CODES
----------
    0  consumed — the message became a conversation message
    1  sent-but-not-consumed, or never-became-ready — NOT success
    2  usage error (missing/invalid input, unknown workspace)
    3  cmux transport error (binary missing, socket refused, non-zero rc)
    4  queued — pi accepted the message into its pending queue (the pane's
       `Steering:`/`Follow-up:` display carries this message) for the next turn,
       but it has NOT become a conversation message. DELIVERED but not consumed;
       a caller that needs the turn to have STARTED must treat this as NOT
       success (see QUEUED IS NOT SUCCESS)

A transport failure (exit 3) is NOT a consumption failure (exit 1), and the two
stay distinguishable. But a failure to REACH cmux — a non-zero rc on any
`list-workspaces` read or on the text `send`, a transport error raised at any
gate, or a spawn/exec `OSError` (converted at the source in `Cmux.run`, so a
non-executable `--cmux` fails like a missing binary or a timeout) — and an
`unknown-workspace` (exit 2), the DOCUMENTED post-cutover loss mode, no longer
DROPS the notice: the payload is appended to the orchestrator inbox and the
result/CLI reports which channel carried it (`channel: inbox`). If the inbox
fallback ALSO fails, the result carries both diagnostics (`channel: none`) and
says explicitly that the notice is not recorded anywhere (#4842).

The fallback fires ONLY where the notice would otherwise vanish — an unreachable
transport, or a workspace that is not listed. It does NOT fire on the ordinary
`sent-but-not-consumed` / refused-`send_enter` outcome, nor on the PRE-SEND
`never-became-ready` refusal (a blocked or unreadable-blind pane, where nothing
was ever written): those are negative-pinned, and in the ordinary consumption
failure the bytes did reach a live transport, so an entry there would be a
duplicate rather than a rescue. Those outcomes deliberately do NOT append.

That justification is NOT true of every exclusion, and the exception is named
here so it is not re-derived: the RECOVERY-branch `never-became-ready` (the
boot-block prompt ATE the first send and the re-send was refused rather than
written blind) loses the notice outright, and the recovery re-send does not
inspect its own rc either, so a refused retry also rides the no-append path.
That is a LOSS, is deliberate here, and is a scoped follow-up on the issue —
it is why the line above reads "do NOT append" rather than "cannot lose".

Honest limits, stated so they are not read as durability guarantees:

  * the append is a buffered `write`, NOT `fsync`'d — it returns before the bytes
    reach stable storage, so a machine crash inside that window can still lose
    it. (A send FAILURE is covered; a machine CRASH is not.)
  * a transport death DURING CONFIRMATION cannot be told apart from "the pane got
    it", so the inbox can carry a notice the pane also carries: a DUPLICATE
    rather than a loss — and that holds only while the append itself succeeds;
    if it fails too, `channel: none` records that nothing is recorded anywhere.
  * the RECOVERY-branch `never-became-ready` is a real LOSS, not a duplicate: the
    first send was eaten by the boot-block prompt, the re-send was refused, and
    its rc is not inspected, so nothing is recorded anywhere. Deliberate and
    scoped as a follow-up on the issue; it is excluded from the "never lost"
    claim above ON PURPOSE.

The `[YYYY-MM-DD HH:MM:SS TZ] [LABEL] <one line>` prefix is the convention
`notify-orchestrator.sh` emits on its FIRST line (`printf '[%s] [%s] %s\n'`,
2026-09-16), but that script does NOT flatten a multi-line message and other
writers use different shapes: measured 2026-10-08 against the live inbox, 6,963
of 7,441 lines do not match `^\\[ts\\] \\[label\\]`. This tool emits the flattened
one-line prefix anyway — line-orientation is what keeps a line readable by eye.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/cmux_dispatch.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/cmux_dispatch.py`"
    )

import argparse
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: The exact string pi prints before awaiting a keypress.
BOOT_BLOCK_MARKER = "Press any key to continue"

#: pi's footer markers. The canonical one is the context-budget token
#: (`N.N%/Nk (auto)`, or `?/Nk (auto)` when `getContextUsage()` has a null percent
#: after a compaction). Its presence is the cheapest reliable "the TUI owns stdin
#: now" signal: the footer is drawn only after the boot-block prompt has been
#: satisfied, and a *fresh idle* pane shows NO `↑`/`↓` counters — do not key
#: readiness off those.
#:
#: The token can also be MISSING on a live pane: a banner printed over the footer
#: tears the line and leaves only the model badge (verbatim capture, 2026-09-26:
#: `…jsonlepseek) deepseek-flash • high`). This fleet's own liveness checks accept
#: the badge for exactly that reason (`orchestrator-heartbeat.sh` `_pane_has_pi`,
#: `safe-send.sh`), so requiring the token alone refused a healthy lane (#7158
#: round 7).
#:
#: The badge is `${modelName} • ${thinkingLevel}` (`footer.js`), and the levels are
#: `off|minimal|low|medium|high|xhigh|max` (`thinking off` when off). The level SET
#: is finite and known, so it is ENUMERATED — a generic `• <word>` let ordinary
#: output (a markdown bullet) forge a footer block and move the prompt scan anchor
#: below a live shell prompt (#7158 round 9).
#:
#: `(auto)` is deliberately NOT a marker: it is only ever appended to the budget
#: token (so it adds no coverage), and as a lone token it is the easiest thing for
#: arbitrary output to hit.
READY_RE = re.compile(
    r"(?:\d+(?:\.\d+)?%|\?)/\d+(?:\.\d+)?[kKmM]\b"  # N.N%/Nk or ?/Nk
    r"|\u2022 (?:thinking off|off|minimal|low|medium|high|xhigh|max)\b"  # model badge
)

#: pi's footer prints the working directory on the line DIRECTLY ABOVE the stats
#: line — `FooterComponent.render` builds `[pwdLine, statsLine, ...statuses]` — so
#: a stats-shaped line with a pwd line above it is a genuine footer BLOCK, while
#: one without is output that merely LOOKS like a stats line. That distinction is
#: what keeps shell output which mimics the stats shape from hiding the prompt
#: ABOVE it (#7158 round 6). `formatCwdForFooter` renders the cwd as an absolute
#: path or a `~`-relative one and appends `(branch)` and ` • <sessionName>` — so
#: the pwd line can be BARE `~` (cwd == HOME), which a `\S` requirement rejected,
#: losing the anchor and refusing a healthy lane (#7158 round 9). Match the
#: PREFIX only; a missed real pwd line is the fail-open direction.
PWD_LINE_RE = re.compile(r"^\s*(?:~|/)")

#: A shell prompt SIGIL used ONLY to detect that a pane has returned to a shell
#: BELOW a stale pi frame — never to detect pi. A sigil counts when it is a
#: STANDALONE token (`%`, `$`, `#`, `>` at a whitespace/line boundary), or a
#: line-ending `%` not preceded by a digit, or a `$ # >` followed by whitespace or
#: end anywhere (which catches `bash-3.2$ `, `[root@host ~]# ls -la`). The digit
#: guard on line-ending `%` is what keeps a real percentage (`Uploading 50%`,
#: `Progress 99%`) from being read as a prompt; `#general thread` has no sigil
#: followed by whitespace, so pi's own status text is not flagged.
#: RESIDUAL (FAIL-OPEN, not fail-closed): a `%` prompt whose sigil abuts a digit
#: (`~/proj2%`) is indistinguishable from a percentage and is not flagged, as are
#: arrow prompts (`❯`, `➜`). Neither is emitted by this fleet's shells
#: (`/bin/zsh -ic 'exec pi'` → `%n@%m %1~ %# `, bash → `\h:\W \u\$ ` — the sigil
#: always follows a space). An extension status containing `[#$>]` followed by
#: whitespace (`Cost: $ 0.003`, `# general`) IS flagged — that direction is
#: fail-closed, and no status this fleet sets contains such a token.
SHELL_PROMPT_RE = re.compile(r"(?:(?:^|\s)[%$#>](?=\s|$)|(?<!\d)%\s*$|[#$>](?=\s|$))")

#: Fingerprint length. `latest_submitted_message` is truncated by cmux at 240
#: chars with a trailing `…`, so the fingerprint MUST come from the head of the
#: message. 40 is comfortably inside that budget while being long enough to be
#: message-specific.
FINGERPRINT_CHARS = 40

DEFAULT_READY_TIMEOUT = 180.0
DEFAULT_CONSUME_TIMEOUT = 45.0
DEFAULT_APPEAR_TIMEOUT = 30.0
DEFAULT_POLL = 2.0
DEFAULT_RETRIES = 2

#: Readiness wait used by the boot-block recovery path. Deliberately NOT the
#: caller's `--ready-timeout`: that flag answers "how long may I wait before the
#: FIRST send" (0 is a legitimate "I believe the pane is already up"). Once we
#: have dismissed a boot-block prompt we know the pane is mid-boot, so the
#: re-send must wait for the TUI regardless of what the caller asked for.
RECOVERY_READY_TIMEOUT = 180.0

#: Grace window granted to the artifact before we ever ADD bytes back to the
#: pane. `latest_submitted_*` is written at the turn boundary and can lag the
#: poll by a beat; re-sending a message that did land would DUPLICATE it. A
#: re-send is the one recovery whose failure mode is worse than not recovering,
#: so it is gated on a short re-read rather than taken on the first miss.
RECOVERY_GRACE_TIMEOUT = 6.0

#: Lines requested from `cmux read-screen`. The composer sits at the bottom, but a
#: long brief wraps across many lines and its HEAD — the only part the fingerprint
#: matches — would fall outside a shallow window, making a message that IS in the
#: composer look absent and inviting a duplicate re-send. Recovery therefore reads
#: much deeper than the readiness probe.
DEFAULT_SCREEN_LINES = 80
RECOVERY_SCREEN_LINES = 300

# Recovery actions
R_NONE = "none"
R_RELEASE = "release-only"
R_RESEND = "resend"
R_DISMISS_RESEND = "dismiss-and-resend"
#: A COMPLETION MENU swallowed the submit Enter (#7913, second root). Distinct
#: from `R_DISMISS_RESEND`: the brief is ALREADY in the composer (the menu is what
#: ate the Enter, not the transport), so re-sending would duplicate it. The
#: recovery is dismiss-the-menu THEN release the composer with a bare Enter.
R_DISMISS_RELEASE = "dismiss-release"

# --- non-pane liveness: the pi session file (#7913) ------------------------ #
#: pi writes each session's transcript to `~/.pi/agent/sessions/<mangled-cwd>/`
#: as `<ts>_<session-id>.jsonl`. A transcript whose mtime advanced recently can
#: only be advancing because a live pi owns the workspace — a liveness signal
#: INDEPENDENT of `cmux read-screen`, which is what this tool needs when the pane
#: read returns nothing for a pane that is in fact running (#7913).
#:
#: Overridable via the environment so every test is hermetic and never touches
#: the live store (the #4883 env-isolation class).
SESSIONS_ROOT_ENV = "CMUX_DISPATCH_SESSIONS_DIR"
DEFAULT_SESSIONS_ROOT = "~/.pi/agent/sessions"
#: How recently a session transcript must have advanced to count as a live pi.
#: The finding measured lanes alive with transcript writes seconds old; the
#: fleet's own beat used a 15-minute window. Five minutes sits inside both and is
#: short enough that a genuinely dead lane's frozen transcript is not mistaken for
#: a live one. Deliberately conservative: this signal only ever GATES a send that
#: the screen could not, so a too-strict window costs a re-dispatch.
DEFAULT_SESSION_FRESH_S = 300.0

# --- readiness classification (#7913) -------------------------------------- #
#: A FAILED READ is a statement about the INSTRUMENT, never about the lane.
#: `UNREADABLE` is therefore first-class and must never be fed to the beat's
#: WEDGED/STALL/IDLE dispatch advice: `STALL`/`WEDGED` are assertions about the
#: lane, and an intermittent read must not be able to move a lane between states
#: (that flapping is the audit trail of the instrument failing). Only `READY` and
#: `NOT_READY` are assertions about the lane; `BLOCKED` is pi's own boot prompt.
ST_READY = "READY"
ST_UNREADABLE = "UNREADABLE"
ST_BLOCKED = "BLOCKED"
ST_NOT_READY = "NOT_READY"

#: pi's autocomplete list (`SelectList.renderItem` in the installed pi-tui)
#: marks the SELECTED candidate with a `→ ` prefix and renders the whole list in
#: the editor's own output, just below the composer's bottom rule. Used for the
#: CONDITIONAL pre-send dismiss: Escape is routed to `tui.select.cancel` while
#: autocomplete is showing (SAFE), but to `app.interrupt` — which ABORTS the live
#: turn — when it is not (`CustomEditor.handleInput`). So this predicate must be
#: specific, and it is only ever used to ADD a safe keystroke.
SELECTED_COMPLETION_RE = re.compile("^\\s*\u2192\\s+\\S")

#: pi's input box is delimited by long horizontal rules; the composer is the
#: region between the LAST TWO of them. A mid-turn editor draws its TOP border with
#: the status label embedded (`── ⠼ Working ──…`), so a labelled border counts
#: too — otherwise the editor's two borders cannot be paired and `composer_region`
#: either returns None (`cannot tell`) or spans rules across the transcript; either
#: way an EMPTY composer is never positively identified.
RULE_RE = re.compile(r"^\s*(?:[\u2500-]{8,}|[\u2500-]{2,}\s+\S.*[\u2500-]{8,})\s*$")

#: pi's PENDING-TURN display, one line per queued submission (#5979).
#: `dist/modes/interactive/interactive-mode.js::updatePendingMessagesDisplay`:
#: `theme.fg("dim", `Steering: ${message}`)` for a submission queued while
#: streaming, `Follow-up: ${message}` for one queued during compaction. The
#: container is mounted in the input dock (`createChatViewport` order:
#: transcript, pendingMessages, status, editor, …), so the queued text is NOT in
#: the composer.
PENDING_TURN_RE = re.compile(r"^\s*(?:Steering|Follow-up):\s*(?P<text>.*)$")

#: pi's dequeue hint — the last line of the pending container, rendered by
#: `updatePendingMessagesDisplay` as
#: `theme.fg("dim", `\u21b3 ${key} to edit all queued messages`)`, where `${key}` is
#: the resolved key display (e.g. `Option+Up`). Requiring pi's own hint TEXT — not
#: just the `\u21b3` glyph — keeps ordinary scrollback (which can print `\u21b3`
#: freely) from being read as a container. It is NOT a secret and so is not proof
#: against a lane that deliberately renders pi's hint string; see the module
#: docstring's residual note. `.*` absorbs the keybinding-dependent key display.
#: A right-truncated hint (a very narrow pane) is not parsed as a container, so the
#: `queued` verdict fails closed there — and `pending_queue_unparsed` keeps the same
#: unobservable queue from authorizing a duplicate re-send in recovery.
PENDING_HINT_RE = re.compile(r"^\s*\u21b3.*to edit all queued messages\s*$")

#: A pi DEQUEUE HINT that the renderer CUT: it still starts with the `\u21b3` glyph
#: and ends in the truncation ellipsis, but no longer carries the full
#: `to edit all queued messages` text. A narrow pane truncates pi's own hint, so the
#: container cannot be located — the queue is unobservable, not absent.
TRUNCATED_HINT_RE = re.compile(r"^\s*\u21b3.*\.\.\.\s*$")

#: The ellipsis `truncateToWidth` appends to a cut line (its default).
TRUNCATION_ELLIPSIS = "..."

#: Minimum visible characters for a TRUNCATED pending line to count as ours. The
#: display is one line (`TruncatedText(text, 1, 0)`) cut by `truncateToWidth` to
#: roughly `pane_width - 2` (its horizontal padding) with a literal `...` appended.
#: Measured on this box the fleet's cmux panes are ~142-217 columns, so a real
#: truncated head is ~127-200 chars and this cap rarely binds: the identity floor
#: is `max(4, min(PENDING_MIN_CHARS, (len(message) + 1) // 2))` — half the message,
#: capped here and floored at 4 — so a cut SHORT message on a narrow pane still
#: matches, while a long message is identified by its first `PENDING_MIN_CHARS`.
PENDING_MIN_CHARS = 16


#: The durable fallback channel (#4842). When cmux cannot be reached — or the
#: workspace is not in the list (the documented post-cutover loss mode) — the
#: notice must not vanish: it is appended to the orchestrator inbox, the surface
#: the orchestrator already treats as intake truth.
#:
#: `notify-orchestrator.sh` writes the `[YYYY-MM-DD HH:MM:SS TZ] [LABEL] <msg>`
#: prefix on its FIRST line (`printf '[%s] [%s] %s\n'`, since 2026-09-16, "Durable
#: record first — never lose the signal to a failed send"), but it does NOT
#: flatten a multi-line message and other writers use other shapes: measured
#: 2026-10-08 against the live inbox, 6,963 of 7,441 lines do not match
#: `^\[ts\] \[label\]`. This tool emits the flattened `[ts] [label] <one line>`
#: form because a line read by eye must stay one line — it does NOT claim every
#: line in the file shares that shape.
#:
#: The append is a buffered `write`, not `fsync`'d: durable against a send
#: failure, not against a machine crash inside the write window.
INBOX_ENV = "CMUX_DISPATCH_INBOX"
DEFAULT_INBOX = "~/.pi/agent/state/orchestrator-inbox.log"
INBOX_FALLBACK_LABEL = "cmux-dispatch"


def orchestrator_inbox_path() -> Path:
    """The inbox the durable fallback appends to (env-overridable for tests)."""
    return Path(os.environ.get(INBOX_ENV) or DEFAULT_INBOX).expanduser()


def inbox_record(label: str, text: str) -> str:
    """One flattened `[ts] [label] <one line>` inbox record.

    BOTH `label` and `text` are flattened, because either can carry an embedded
    newline and the inbox is line-oriented: an unflattened `--label` such as
    `"B7]\\n[1999-01-01 00:00:00 XX] [INJECTED"` would otherwise forge a second,
    attacker-shaped record line. The same one-line invariant the watcher
    documents for `cmux send` (newlines arrive as Enters) applies here.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    lane = " ".join((label or "").split()) or INBOX_FALLBACK_LABEL
    return f"[{stamp}] [{lane}] {' '.join(text.split())}"


def append_to_inbox(label: str, text: str) -> Path:
    """Append the notice to the durable inbox and return the path written.

    Raises on failure — the CALLER must report both diagnostics rather than let
    the notice disappear (a swallowed exception here is exactly the silent-loss
    bug #4842 exists to close). The failure is not always an `OSError`:
    `Path.expanduser()` raises `RuntimeError` on an unexpandable home and
    `handle.write` raises `UnicodeEncodeError` for a lone surrogate, so callers
    catch broadly and treat the exception as a diagnostic.
    """
    path = orchestrator_inbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(inbox_record(label, text) + "\n")
    return path


# --------------------------------------------------------------------------- #
# Pure decision helpers (unit-tested without any cmux)
# --------------------------------------------------------------------------- #


def normalize(text: str | None) -> str:
    """Collapse all runs of whitespace to single spaces (cmux does the same)."""
    return " ".join((text or "").split())


def fingerprint(message: str, limit: int = FINGERPRINT_CHARS) -> str:
    """A short, head-anchored, whitespace-normalized identity for a message.

    Head-anchored because cmux truncates the stored message at 240 chars.
    """
    return normalize(message)[:limit]


def marker_present(screen: str | None) -> bool:
    """The boot-block prompt string appears anywhere in the capture."""
    return BOOT_BLOCK_MARKER in (screen or "")


def _last_status_bar_end(screen: str | None) -> int:
    """Offset just past the LAST status bar in the capture, or -1 if none."""
    matches = list(READY_RE.finditer(screen or ""))
    return matches[-1].end() if matches else -1


def _is_pwd_line(line: str) -> bool:
    """True when a line looks like pi's footer pwd line (not a shell prompt)."""
    return bool(PWD_LINE_RE.match(line)) and not SHELL_PROMPT_RE.search(line)


def _footer_stats_end(screen: str | None) -> int:
    """Offset just past the LAST stats line that belongs to a pi footer BLOCK,
    i.e. is directly preceded (ignoring blank lines) by a pwd line, or -1.

    This is the anchor `shell_prompt_below_footer` scans from: a stats-shaped
    line WITHOUT a pwd line above it is not a footer — it is output. See
    `PWD_LINE_RE`.
    """
    text = screen or ""
    prev_nonempty: str | None = None
    pos = 0
    best = -1
    for line in text.split("\n"):
        match = READY_RE.search(line)
        if match and prev_nonempty is not None and _is_pwd_line(prev_nonempty):
            best = pos + match.end()
        if line.strip():
            prev_nonempty = line
        pos += len(line) + 1
    return best


def status_bar_present(screen: str | None) -> bool:
    """pi's TUI status bar appears somewhere in the capture."""
    return _last_status_bar_end(screen) >= 0


def boot_blocked(screen: str | None) -> bool:
    """True when pi is sitting at the `Press any key to continue...` prompt.

    ORDER, not mere presence. The pane is blocked iff the prompt marker exists
    with NO status bar rendered after it:

    * the marker ALONE is ambiguous — pi's `regular` TUI mode renders inline, so
      once the prompt has been satisfied the line stays visible above the input
      box for the life of the session (observed live: 62 lines from the end of an
      80-line capture on a healthy pane). Position-relative rules ("the marker is
      not last") misread a live block as safe as soon as ANY boot output follows
      the prompt, and then feed the brief to the prompt — the corruption this
      tool exists to prevent;
    * the status bar ALONE is equally ambiguous in the other direction — a pane
      that PREVIOUSLY ran pi still shows the old session's status bar in the
      capture, above a freshly printed prompt. Matching "any status bar" there
      also declared the pane ready and fed the prompt the brief (reproduced: the
      prompt ate the prefix).

    Requiring the bar to come AFTER the prompt settles both cases, because the
    bar is drawn only once the prompt has been satisfied — for the CURRENT
    process.
    """
    if not screen:
        return False
    marker_at = screen.rfind(BOOT_BLOCK_MARKER)
    if marker_at < 0:
        return False
    return _last_status_bar_end(screen) < marker_at


def shell_prompt_below_footer(screen: str | None) -> bool:
    """True when a shell prompt is drawn BELOW the last pi footer.

    A status bar ANYWHERE is not evidence that the CURRENT process owns stdin.
    pi renders inline, so a pane whose pi exited retains the dead session's
    footer in the scrollback while the shell prints its prompt BELOW it — the
    same ordering trap `boot_blocked` documents for the boot-block marker, in
    the case where no marker is present to catch it. That prompt is what makes
    the stale frame EXECUTABLE: bytes sent there run as commands (#7158).

    A prompt is detected by a shell sigil used as a prompt token — standalone
    (`host % ls -la`), line-ending (`user@host dir %`), or followed by
    whitespace/end anywhere (`bash-3.2$ `, `[root@host ~]# ls -la`). pi's own
    footer and its EXTENSION-STATUS lines carry no such sigil (`Loop: <slug>
    (cycle 2)`, `#general thread`), and a line-ending `%` preceded by a digit is
    excluded so `Uploading 50%` stays READY. pi pushes status lines BELOW its
    stats line whenever an extension calls `ctx.ui.setStatus` (verified in the
    installed renderer: `modes/interactive/components/footer.js`), so "the footer
    must be the literal last line" would refuse healthy lanes.

    The scan starts just past the last FOOTER BLOCK — the last stats line with a
    pwd line above it (`_footer_stats_end`) — and covers every line after it, plus
    the remainder of the anchor line itself (a crash mid-render leaves the prompt
    appended to the footer's own row). Anchoring on "the last stats-shaped line"
    alone is bypassable: shell OUTPUT below the prompt which mimics the stats shape
    (`host % ` then a line reading `42.0%/700k (auto)`) would place the prompt ABOVE
    the anchor and hide it. Requiring a real footer block (a pwd line above the
    stats) rejects that; when no block exists at all the anchor falls back to the
    last stats-shaped line, which is the conservative (more-scanning) choice.

    RESIDUALS — direction stated honestly:
    * FAIL-OPEN (INHERENT to judging liveness from screen content, not fixable by
      this heuristic): shell output that reproduces an ENTIRE pi footer block — a
      pwd-shaped line (`~`/`/` prefix) directly above a line carrying a genuine
      marker (a real budget token, or a real badge level such as `• high`) — moves
      the anchor down past the prompt. Enumerating the badge levels (round 9) makes
      an arbitrary bullet like `• item one` no longer a marker, but a shell can
      still print `• high`. The durable signal is process/session liveness, not
      screen content (#7159).
    * FAIL-OPEN (narrow): a `%` prompt whose sigil abuts a digit (`~/proj2%`) is
      indistinguishable from a percentage, and arrow prompts (`❯`, `➜`) are
      outside the class. Neither is emitted by this fleet's shells.
    * FAIL-CLOSED: an extension status containing `[#$>]` followed by whitespace
      (`Cost: $ 0.003`, `# general`) is refused; no status this fleet sets does,
      and when there is no footer block at all the whole capture is scanned (see
      below), which can only over-refuse.
    """
    text = screen or ""
    end = _footer_stats_end(text)
    if end < 0:
        # ⛔ NO TRUSTWORTHY FOOTER BLOCK. Anchor on the last marker alone and the
        # scan sits BELOW the marker, so a loose marker printed by the shell
        # (`host % echo '(auto)'` then `(auto)`) hides the prompt ABOVE it and
        # declares a bare shell READY (#7158 round 8). Without a block, scan the
        # WHOLE capture — the fail-closed direction. A live pane does not reach
        # this branch: pi always draws the pwd line above the stats line
        # (`footer.js` `[pwdLine, statsLine, ...statuses]`).
        return any(
            line.strip() and SHELL_PROMPT_RE.search(line)
            for line in text.splitlines()
        )
    # Scan the whole tail INCLUDING the remainder of the anchor line, so a prompt
    # appended to a non-newline-terminated footer row is still caught.
    return any(
        line.strip() and SHELL_PROMPT_RE.search(line)
        for line in text[end:].splitlines()
    )


def not_ready_reason(screen: str | None) -> str:
    """Why a pane is not ready, in operator terms.

    `shell_prompt_below_footer` falls back to scanning the WHOLE capture when no
    footer BLOCK exists, so a pane with NO footer at all (a bare login shell)
    also reports True. Branching on it alone would therefore tell the operator
    "a shell prompt is drawn BELOW pi's footer" for a pane where no footer was
    ever drawn — naming a footer that does not exist and sending the reader
    after the wrong failure. Check presence first, then position.
    """
    if boot_blocked(screen):
        return (
            "pi is sitting on its `Press any key to continue...` boot-block "
            "prompt, which eats what is typed at it"
        )
    if status_bar_present(screen) and shell_prompt_below_footer(screen):
        return (
            "a shell prompt is drawn BELOW pi's footer, so the pane has "
            "returned to a shell"
        )
    return "no pi footer (status bar) was drawn"


def screen_ready(screen: str | None) -> bool:
    """True when pi's LIVE TUI owns stdin.

    Three requirements: a footer is present, no boot-block marker follows it, and
    no shell prompt is drawn below it — a stale footer above a live shell prompt
    is an executable pane, not a ready one (#7158).
    """
    return (
        status_bar_present(screen)
        and not boot_blocked(screen)
        and not shell_prompt_below_footer(screen)
    )


def readiness_state(screen: str | None) -> str:
    """Classify a pane read into one of `READY` / `UNREADABLE` / `BLOCKED` / `NOT_READY`.

    The one rule that matters (#7913): a read that returned NOTHING — a failed
    `read-screen` (`None`) or an empty capture — is `UNREADABLE`. It is a fact
    about the instrument, not about the lane, and it must never be collapsed into
    `STALL`/`WEDGED`/`IDLE` advice. A READABLE pane that merely shows no live
    footer stays `NOT_READY` (the #7158 bare-shell shape), which is a real claim
    about the lane and MUST keep refusing a blind send.
    """
    if not (screen or "").strip():
        return ST_UNREADABLE
    if boot_blocked(screen):
        return ST_BLOCKED
    if screen_ready(screen):
        return ST_READY
    return ST_NOT_READY


def condition_for(screen: str | None) -> str:
    """The operator-facing `condition` for a refusal, from the READ's shape.

    Lives beside `readiness_state` so a refusal's `condition` cannot disagree with
    the classification it came from. Two recovery refusals once shipped
    `condition == ""`, which the field's own docstring reads as a transport
    failure — a consumer branching on it misclassified a refusal as a lost
    transport (#7913 review).
    """
    state = readiness_state(screen)
    if state == ST_UNREADABLE:
        return "unreadable-pane"
    if state == ST_BLOCKED:
        return "boot-blocked"
    return "not-ready"


def unreadable_reason(screen: str | None) -> str:
    """Operator-facing reason for an `UNREADABLE` verdict."""
    if screen is None:
        return "`cmux read-screen` failed (no capture at all)"
    return "`cmux read-screen` returned an EMPTY capture"


def completion_menu_open(screen: str | None) -> bool:
    """True when pi's autocomplete/completion menu is open on the pane.

    The list is rendered by the editor component just BELOW its bottom rule, and
    the selected candidate carries a `→ ` prefix (`SelectList.renderItem` in the
    installed pi-tui). Requiring an arrow-prefixed line AFTER the last horizontal
    rule bounds the match to that region: `→ ` in a lane's transcript or bash
    output is scrollback, above the rule, and does not count.

    Conservative BY DIRECTION. A false NEGATIVE costs one recovery round-trip
    (the `R_DISMISS_RELEASE` path is reached from the confirmation read instead);
    a false POSITIVE would send Escape at a pane with no menu, which pi routes to
    `app.interrupt` and ABORTS the lane's live turn. So the predicate demands the
    renderer's own selected-row marker, its position after the last rule, and its
    place BEFORE the footer stats line (the editor region ends at the footer), and
    it is bounded to the autocomplete maximum-plus-scroll-indicator.
    """
    if not screen:
        return False
    rows = screen.splitlines()
    rule_at = -1
    for index, row in enumerate(rows):
        if RULE_RE.match(row):
            rule_at = index
    if rule_at < 0:
        return False
    # Scan only the editor region below the last rule: the autocomplete list can
    # hold up to `autocompleteMaxVisible` (max 20) entries plus a scroll line, and
    # it ends where the footer stats line begins.
    for row in rows[rule_at + 1 : rule_at + 1 + 25]:
        if READY_RE.search(row):
            break
        if SELECTED_COMPLETION_RE.match(row):
            return True
    return False


def sessions_root() -> Path:
    """The pi session store root (env-overridable for hermetic tests)."""
    return Path(os.environ.get(SESSIONS_ROOT_ENV) or DEFAULT_SESSIONS_ROOT).expanduser()


def mangle_cwd(cwd: str) -> str:
    """cwd -> session directory name: `/a/b.c` -> `--a-b-c--` (see fleet_state)."""
    return "--" + re.sub(r"[/.]", "-", cwd.strip("/")) + "--"


def _newest(paths: Iterable[Path]) -> Path | None:
    """The most recently modified regular file among `paths`, or None."""
    newest: Path | None = None
    newest_mtime = -1.0
    for path in paths:
        try:
            if not path.is_file():
                continue
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = path, mtime
    return newest


#: A session id is a UUID (measured 2026-10-10: 21/21 live bindings). The shape is
#: checked BEFORE the id is interpolated into a glob, so a malformed binding can
#: never WIDEN the pattern: `sid="*"` would otherwise match the store's newest
#: transcript and re-attribute a sibling lane's liveness to this pane — the exact
#: #7913 defect this function exists to fix.
_SESSION_ID_RE = re.compile(r"[0-9A-Za-z_-]+")


class _ProbeUnavailable:
    """Sentinel: the binding probe could not be ASKED — distinct from "no binding".

    `None` is cmux's answer that the pane has no binding; this is the absence of
    an answer. Only the first is safe to memoize for a dispatch (#7913 review).
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "PROBE_UNAVAILABLE"


PROBE_UNAVAILABLE = _ProbeUnavailable()


def session_file_for(session_id: str | None, entry: dict | None) -> Path | None:
    """The transcript belonging to `session_id`, or None — NEVER a guess.

    A pane's own transcript is named `<timestamp>_<session-id>.jsonl`, so the
    session id — not the cwd — is what attributes a file to a pane (#7913: a
    cwd-scoped glob let a sibling lane's fresh transcript stand in for a dead
    lane's, measured 13-of-22 workspaces sharing one cwd and 245 transcripts in
    its bucket).

    The mangled-`current_directory` bucket is the fast path; the whole store is
    the fallback, for a pane whose cwd changed after its session started. The
    session id is unique, so the fallback cannot pick up a sibling's file.

    A missing `session_id` is UNKNOWN, never “no evidence against”: the caller
    must refuse.
    """
    sid = (session_id or "").strip()
    if not sid or not _SESSION_ID_RE.fullmatch(sid):
        return None
    cwd = str((entry or {}).get("current_directory") or "").strip()
    lookups: list[tuple[Path, str]] = []
    if cwd:
        lookups.append((sessions_root() / mangle_cwd(cwd), f"*_{sid}.jsonl"))
    lookups.append((sessions_root(), f"**/*_{sid}.jsonl"))
    for root, pattern in lookups:
        try:
            best = _newest(root.glob(pattern))
        except OSError:
            continue
        if best is not None:
            return best
    return None


def text_on_screen(screen: str | None, fp: str) -> bool:
    """True when the message fingerprint is visible on the pane.

    The composer wraps long lines, so an exact substring match is not enough:
    the fallback comparison strips ALL whitespace, which survives a wrap at a
    space AND a wrap mid-token.

    A false positive here costs a re-send attempt, not correctness: the recovery
    still has to confirm the artifact before reporting success, so a message that
    was never delivered ends at `sent-but-not-consumed` (fail closed) rather than
    a false success. That fail-closed direction is why the search covers the
    whole capture rather than only the composer region: mistaking transcript text
    for composer text costs an extra `release-only` attempt, while the opposite
    mistake duplicates a message.
    """
    if not fp:
        return False
    if fp in normalize(screen):
        return True
    return re.sub(r"\s+", "", fp) in re.sub(r"\s+", "", screen or "")


def parse_workspaces(json_text: str) -> dict[str, dict]:
    """Index `cmux list-workspaces --json` output by BOTH `ref` and `id`."""
    try:
        payload = json.loads(json_text or "{}")
    except json.JSONDecodeError:
        return {}
    index: dict[str, dict] = {}
    for entry in payload.get("workspaces") or []:
        if not isinstance(entry, dict):
            continue
        for key in ("ref", "id"):
            value = entry.get(key)
            if value:
                index[str(value)] = entry
    return index


def workspace_entry(json_text: str, workspace: str) -> dict | None:
    """Resolve a workspace by ref, id, or (as a last resort) unique ref suffix."""
    index = parse_workspaces(json_text)
    if workspace in index:
        return index[workspace]
    # `workspace:12` vs `12` and vice versa.
    digits = re.sub(r"^\D+", "", workspace)
    if digits:
        for key, entry in index.items():
            if key.endswith((":" + digits, "-" + digits)) or key == digits:
                return entry
    return None


def submitted_message(entry: dict | None) -> str:
    """Normalized `latest_submitted_message` of a workspace entry."""
    if not entry:
        return ""
    return normalize(entry.get("latest_submitted_message"))


def is_consumed(
    before: dict | None,
    after: dict | None,
    fp: str,
    require_novelty: bool = True,
) -> tuple[bool, str]:
    """Did the message become a conversation message?

    With `require_novelty` (the `send` path) the submission must also be NEW
    relative to `before`, so a pointer whose text equals the previous message
    cannot be reported as success forever. Without it (the `verify` path) a
    present, head-anchored fingerprint is the whole question.

    The match is ANCHORED at the head: the fingerprint is the message's head, and
    cmux stores the submitted message from its head. An unanchored substring test
    would report a short dispatch (`"go"`) as delivered by any unrelated turn
    that happened to contain it — the fleet dispatches into shared lanes, so that
    coincidence is reachable in practice.
    """
    if not after:
        return False, "workspace-not-found"
    current = submitted_message(after)
    if not fp:
        return False, "empty-fingerprint"
    if not current.startswith(fp):
        return False, "not-at-the-head-of-latest-submitted-message"
    if not require_novelty:
        return True, "present"
    if (
        submitted_message(before) == current
        and (before or {}).get("latest_submitted_at")
        == after.get("latest_submitted_at")
    ):
        return False, "unchanged-from-previous-submission"
    return True, "submitted"


def composer_region(screen: str | None) -> str | None:
    """The text pi's input box currently holds, or None if not identifiable.

    The composer is delimited by the last two horizontal rules on screen. None
    means "cannot tell" — which must NOT be read as "empty".
    """
    rows = (screen or "").splitlines()
    rules = [i for i, row in enumerate(rows) if RULE_RE.match(row)]
    if len(rules) < 2:
        return None
    return "\n".join(rows[rules[-2] + 1 : rules[-1]])


def composer_empty(screen: str | None) -> bool:
    """True only when the composer is POSITIVELY shown to be empty."""
    region = composer_region(screen)
    return region is not None and not region.strip()


def _pending_entries(
    screen: str | None,
) -> list[tuple[str, str, bool]]:
    """The pending-container entries as `(raw, visible, truncated)` triples.

    The container is bounded STRUCTURALLY from pi's own render. Each queue change
    runs `updatePendingMessagesDisplay`, which clears the container and re-adds:
    a spacer, one `Steering: <text>` / `Follow-up: <text>` line per queued
    submission, then a single `\u21b3 <key> to edit all queued messages` hint — all
    under the same `steering.length || followUp.length` guard. So the entries are
    exactly the contiguous `Steering:`/`Follow-up:` lines directly ABOVE the last
    hint line that has them; a `Steering:`-shaped line anywhere else (a lane's
    transcript, model output, bash output) is scrollback, not evidence about a
    dispatch.

    There is deliberately NO distance-from-the-bottom bound. pi appends its
    `bashComponent` to this same container while a lane streams, so live output can
    sit BELOW the hint and push it arbitrarily far from the end of the capture; a
    numeric window would then hide the live container, turning a genuinely queued
    message into a failure (and, with an empty composer, into a duplicate re-send).
    Staleness is handled by NOVELTY instead, which is the right instrument.

    `truncated` records whether the RAW text carried the renderer's `...`, and
    `raw` is kept so a message that itself ends in `...` can still be matched
    exactly. The flag matters the other way too: the ellipsis is the only thing
    that distinguishes a real truncation of OUR message from a DIFFERENT, shorter
    message that merely shares a prefix.
    """
    rows = (screen or "").splitlines()
    # Scan hints newest-first: the container's own hint is the LAST hint that has
    # entries directly above it. A stray line carrying pi's hint text BELOW the
    # container (a bash output block appended while the lane streams) must not hide
    # the container's real hint.
    for hint_at in reversed(
        [index for index, row in enumerate(rows) if PENDING_HINT_RE.match(row)]
    ):
        entries: list[tuple[str, str, bool]] = []
        for row in reversed(rows[:hint_at]):
            match = PENDING_TURN_RE.match(row)
            if not match:
                break
            raw = normalize(match.group("text"))
            truncated = raw.endswith(TRUNCATION_ELLIPSIS)
            visible = raw[: -len(TRUNCATION_ELLIPSIS)] if truncated else raw
            entries.append((raw, visible, truncated))
        if entries:
            entries.reverse()
            return entries
    return []


def _entry_matches_message(raw: str, visible: str, truncated: bool, target: str) -> bool:
    """Is one pending-container entry the WHOLE message `target` (or its cut head)?

    `raw` is the entry's rendered text; `visible` is `raw` with a trailing
    renderer ellipsis removed. Two shapes are accepted:

      * `raw == target` — the rendered line IS the message (the comparison is on
        the RAW text, so a message that legitimately ends in `...` matches
        itself), or
      * a TRUNCATED head: the raw text carries the renderer's `...`, the visible
        head is a prefix of the message, and it is STRICTLY SHORTER than the
        message. That last inequality is what rejects a DIFFERENT, LONGER message
        whose truncated head happens to equal ours (`Q = target + " tail"` cut to
        `target...`), and the ellipsis is what rejects a different SHORTER message
        that merely shares a prefix.
    """
    if raw == target:
        return True
    if not truncated or not visible:
        return False
    # A real cut head is STRICTLY shorter than the message (`len(visible) <
    # len(target)`) and long enough to identify: half the message, CAPPED at
    # PENDING_MIN_CHARS and floored at 4, so a genuinely cut SHORT message (a very
    # narrow pane) is still matchable while a 1-char remnant is not and a long
    # message is identified by its first PENDING_MIN_CHARS.
    floor = max(4, min(PENDING_MIN_CHARS, (len(target) + 1) // 2))
    return len(visible) >= floor and len(visible) < len(target) and target.startswith(visible)


def pending_turn_identity(screen: str | None, message: str) -> str | None:
    """A pending-container entry carrying THIS message, or None (#5979 helper).

    Identity is the WHOLE message, not the 40-char fingerprint. A fingerprint is
    not message-unique — the fleet's own nudges share a head, and a short
    dispatch's fingerprint IS the whole dispatch — so accepting any pending line
    that merely STARTS WITH the fingerprint confirmed a dropped `"continue"`
    against an unrelated queued `"continue with the migration"`. See
    `_entry_matches_message` for the accepted shapes; a pending line LONGER than
    the message is a DIFFERENT message and never matches.

    Returns the entry's visible text (a display value for diagnostics), and None
    when no entry is ours. Multi-line messages cannot be confirmed: pi renders only
    the first line, which is not message-unique. `Dispatcher.send_message` flattens
    a multi-line body up front (as `_read_message` does) so a caller never reaches
    this state.
    """
    if not screen:
        return None
    target = normalize(message)
    if not target:
        return None
    for raw, visible, truncated in _pending_entries(screen):
        if _entry_matches_message(raw, visible, truncated, target):
            return visible
    return None


def _entry_count(screen: str | None, message: str) -> int:
    """How many pending-container entries are ours (same predicate as identity)."""
    if not screen:
        return 0
    target = normalize(message)
    if not target:
        return 0
    return sum(
        1
        for raw, visible, truncated in _pending_entries(screen)
        if _entry_matches_message(raw, visible, truncated, target)
    )


def pending_turn_matches(
    screen: str | None,
    message: str,
    before_screen: str | None,
) -> bool:
    """True when a NEW pending turn carries THIS message (#5979).

    A submission that never became the latest submitted message but IS shown in
    pi's pending queue has been ACCEPTED: pi's `latest_submitted_*` advances only
    at a turn boundary, so a pane that is mid-turn takes the submission into its
    queue without surfacing it as a turn until the current turn ends. That is a
    DELIVERED message, not a failed one, and reporting `sent-but-not-consumed`
    for it is what silently dropped every nudge to a busy lane.

    The evidence is the `Steering: <text>` / `Follow-up: <text>` line pi renders
    from `pendingMessagesContainer` — a dock region the composer read cannot see
    (pi clears the editor before queueing).

    NOVELTY, like `is_consumed`'s. A pending entry that was ALREADY on the pane
    before the send is not evidence that THIS send was accepted: a repeat dispatch
    of the same brief, or a stale entry in scrollback, would otherwise confirm a
    send whose bytes never landed. Novelty is a COUNT of our entries, not a
    string comparison: two identical queued copies of the same brief are a change
    (`after > before`) even though their identity text is the same, and a
    width-change that turns an exact entry into a truncated one does not read as
    novel. `before_screen` is the pre-send capture; when it is None the novelty
    cannot be established and the verdict stays fail-closed.

    FAIL CLOSED in every other direction too: the composer is NOT consulted here
    (a real queue has an EMPTY composer, and text in the composer is the UNSENT
    state), and a message nowhere on screen is not a queue.
    """
    if before_screen is None:
        return False
    return _entry_count(screen, message) > _entry_count(before_screen, message)


def pending_turn_ambiguous(screen: str | None, message: str) -> bool:
    """A pending line that COULD be a too-short truncation of ours.

    A recovery HINT, never a success verdict. Only this narrow case suppresses the
    `resend` recovery: the pending text carries the renderer's `...`, is a head of
    our message, and is shorter than `min(len(message), PENDING_MIN_CHARS)`, the
    threshold this hint exists for. (The identity floor is at or below this one for
    messages of at least 4 chars. For a 1-char message the ambiguity branch cannot
    fire at all (its threshold is 1 and the guard needs `0 < len(visible) < 1`); for
    a 2-3-char message the hard floor of 4 puts the identity floor above, so an
    entry there is not an identity. Harmless either way — both branches yield
    `release`.)

    The `...` requirement is load-bearing in the other direction too: a plain
    short line that merely shares a head with our message (`Steering: continue`
    while we send `continue with the migration`) is a DIFFERENT queued message,
    and suppressing the resend for it would make a lost send undeliverable.
    """
    if not screen:
        return False
    target = normalize(message)
    if not target:
        return False
    floor = min(len(target), PENDING_MIN_CHARS)
    for _raw, visible, truncated in _pending_entries(screen):
        if truncated and 0 < len(visible) < floor and target.startswith(visible):
            return True
    return False


def pending_queue_unparsed(screen: str | None) -> bool:
    """pi has a queue the matcher CANNOT parse, so our message may be in it.

    On a narrow pane pi right-truncates its own hint line, so `PENDING_HINT_RE`
    cannot locate the container and `_pending_entries` returns nothing — the queue
    is UNOBSERVABLE, not absent. The evidence required is a CUT hint line
    (`\u21b3 … ...`) with a `Steering:`/`Follow-up:`-shaped line DIRECTLY above it,
    mirroring the container's own contiguity: a bare `Steering:`-shaped line in a
    lane's transcript or bash output is scrollback, not a queue, and an unrelated
    `\u21b3 … ...` line elsewhere must not disable recovery for a genuinely lost
    send.

    A parsed container is never reported here, and a full hint line with no
    entries above it is not either (there is no queue to hold our message). When
    this IS true the queued entry cannot be compared with `message` at all, so the
    verdict is the module's fail-closed default even if the unparsed entry belongs
    to another message: a re-dispatch is cheaper than a duplicated turn.
    """
    if not screen:
        return False
    if _pending_entries(screen):
        return False
    rows = screen.splitlines()
    for index, row in enumerate(rows):
        if index and TRUNCATED_HINT_RE.match(row) and PENDING_TURN_RE.match(rows[index - 1]):
            return True
    return False


def recovery_action(screen: str | None, fp: str, message: str = "") -> str:
    """Choose the cheapest safe recovery for an unconsumed send.

    Re-sending is only SAFE when the composer is positively shown to be empty AND
    no queue is present that could hold our message. Anything less — an unreadable
    pane, an unidentifiable composer, an unparseable pending container, a screen
    that does not show the text — falls back to `release-only`, because a bare
    Enter can never duplicate while a blind re-send can: the composer would hold
    hold the message twice and the next Enter would submit it doubled, which
    `is_consumed` would then report as success (the head is unchanged). The
    fail-closed direction costs a re-dispatch; the other corrupts the lane.

    ⚠️ `...` is NECESSARY BUT NOT SUFFICIENT truncation evidence: the renderer
    appends it, but a queued message may legitimately end in `...` too. A queued
    DIFFERENT message whose visible head (the text before its trailing `...`) is a
    prefix of ours and at least the floor long is therefore accepted as a truncated
    head of ours. That residual needs a coincidental prefix plus a literal
    ellipsis, and no alternative is observable from a capture (the pane width is
    not in the text).

    `message` (the full text; `fp` when omitted) narrows the duplicate risk that
    reaches the re-send branch: a pane whose pending container already carries a
    head of OUR message. `recovery_action` is reached whenever
    `pending_turn_matches` did NOT accept the queue — the baseline was unreadable
    or the count did not grow — so this is the case where our text is on the pane
    but could not be ATTRIBUTED to this send. Re-sending there queues a second
    copy, so both branches must suppress it. A pending entry for an UNRELATED
    message shares no head with ours, and a truncated head that is not ours is not
    a prefix of ours, so resend stays safe and is still chosen rather than leaving
    a lost send undeliverable.
    """
    if screen is None:
        return R_RELEASE
    if boot_blocked(screen):
        return R_DISMISS_RESEND
    if completion_menu_open(screen) and text_on_screen(screen, fp):
        # A completion menu is open ABOVE a composer that already holds our brief:
        # a bare Enter is consumed by the menu (it accepts the highlighted
        # completion) and never submits — which is exactly why `release-only`
        # cannot recover this condition, and why the recovery must depend on the
        # observed condition rather than on the umbrella `sent-but-not-consumed`.
        # Dismiss the menu, THEN release; never re-send (the brief is present).
        return R_DISMISS_RELEASE
    if text_on_screen(screen, fp):
        return R_RELEASE
    if pending_turn_identity(screen, message or fp) is not None or (
        pending_turn_ambiguous(screen, message or fp)
    ):
        # The pane already carries a head of OUR message, so a bare Enter cannot
        # duplicate while a re-send would queue a SECOND copy. With a readable
        # baseline a NOVEL entry with an IDENTITY would have been accepted by
        # `pending_turn_matches` before recovery was reached, leaving only a
        # pre-existing copy; an entry that is novel but too short to identify has
        # no identity (`pending_turn_matches` returns False), which is exactly why
        # the ambiguity disjunct is here. With an unreadable baseline novelty
        # cannot be established at all, and failing closed here is what keeps the
        # stale-line false positive out.
        return R_RELEASE
    if pending_queue_unparsed(screen):
        # pi's hint line is CUT and a pending-shaped line exists: the queue is
        # unobservable, not absent, and the composer reads EMPTY because pi cleared
        # it — so `composer_empty` would authorize a resend that queues a SECOND
        # copy. Release instead. (A bare `Steering:`-shaped scrollback line without
        # a cut hint does NOT reach here; see `pending_queue_unparsed`.)
        return R_RELEASE
    if composer_empty(screen):
        return R_RESEND
    return R_RELEASE


# --------------------------------------------------------------------------- #
# cmux transport
# --------------------------------------------------------------------------- #


class CmuxTransportError(RuntimeError):
    """cmux itself could not be reached or refused the call.

    Distinct from "the workspace is not in the list": a broken transport is an
    OPERATIONAL failure the caller must not confuse with a missing workspace.
    """


@dataclass
class CmuxResult:
    rc: int
    out: str = ""
    err: str = ""
    argv: list[str] = field(default_factory=list)


class Cmux:
    """Thin, injectable wrapper over the cmux CLI."""

    def __init__(self, binary: str = "cmux", timeout: float = 30.0) -> None:
        self.binary = binary
        self.timeout = timeout

    def run(self, argv: list[str], timeout: float | None = None) -> CmuxResult:
        env = dict(os.environ)
        env.setdefault("CMUX_QUIET", "1")  # silence the legacy-alias notice on stderr
        try:
            proc = subprocess.run(
                [self.binary, *argv],
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                env=env,
            )
        except FileNotFoundError:
            return CmuxResult(127, "", f"cmux binary not found: {self.binary}", argv)
        except subprocess.TimeoutExpired:
            # NEVER echo the message operand: `send` puts the brief after `--`,
            # so a raw argv dump would write the whole payload (which may carry
            # credentials or confidential content) into the caller's log. The
            # tool otherwise logs only byte counts and a 40-char fingerprint.
            return CmuxResult(124, "", self._describe(argv), argv)
        except OSError as exc:
            # A SPAWN/EXEC failure — `PermissionError` for a non-executable
            # `--cmux`, or any other `OSError` from starting the process. It is a
            # TRANSPORT failure exactly like the two above: left to propagate it
            # sails past every `except CmuxTransportError` and every durable
            # fallback, and the notice is recorded NOWHERE (the silent loss #4842
            # exists to close). Returned as a non-zero rc (POSIX 126, "found but
            # not executable") so it rides the SAME conversion every other read
            # and transport fault uses. `FileNotFoundError` is caught above — it
            # is an `OSError` subclass, so this handler must stay after it.
            # Like the timeout branch, the error text names only the binary (the
            # exec failure is on the executable, never the `--` payload).
            return CmuxResult(126, "", f"cmux could not be spawned: {exc}", argv)
        return CmuxResult(proc.returncode, proc.stdout or "", proc.stderr or "", argv)

    @staticmethod
    def _describe(argv: list[str]) -> str:
        """`cmux <subcommand> ...` with any message operand redacted."""
        safe = list(argv)
        if "--" in safe:
            cut = safe.index("--")
            payload = safe[cut + 1 :]
            size = sum(len(part.encode()) for part in payload)
            safe = [*safe[: cut + 1], f"<{size} bytes redacted>"]
        return f"cmux timed out: {' '.join(safe)}"

    # -- concrete operations ------------------------------------------------- #

    def list_workspaces_json(self) -> CmuxResult:
        # `--id-format uuids` OMITS `ref` entirely (verified against cmux: the
        # entry carries only id + index), which silently breaks every
        # ref-addressed dispatch (`--workspace workspace:12` -> not found).
        # `both` emits ref AND id so either form resolves.
        return self.run(["list-workspaces", "--json", "--id-format", "both"])

    def pane_session_id(
        self, workspace: str, surface: str | None = None
    ) -> str | object | None:
        """The PANE's resume-bound session id, `None`, or `PROBE_UNAVAILABLE`.

        ⚠ SURFACE-SCOPED, and that is load-bearing. `cmux surface resume show`
        with no `--surface` answers for the workspace's SELECTED surface, but a
        workspace can hold several surfaces with DIFFERENT sessions (measured
        2026-10-10: `workspace:14` bound `surface:15` and `surface:16` to two
        distinct checkpoint ids). Probing without the target surface would let a
        sibling surface's live pi certify a blind write into the target one —
        #7158's harm one granularity narrower than the cwd scoping already fixed
        (#7913 review, P1).

        `resume_binding` is the AUTHORITATIVE field — NOT
        `restore_record.checkpoint_id`. Measured 2026-10-08: a workspace printed
        “No resume binding” from the text form while `restore_record` still
        carried a STALE id, so reading the stale field is exactly the class of
        defect this tool exists to remove (see `fleet_state.pane_bindings`).

        The two negative outcomes are DELIBERATELY distinct, because they mean
        opposite things downstream (#7913 review):

        - `None` — cmux ANSWERED, and this pane has no binding. A definitive
          fact, safe to memoize for the dispatch, and the caller refuses.
        - `PROBE_UNAVAILABLE` — the probe could not be ASKED (non-zero exit, a
          timeout, a malformed reply). TRANSIENT: under fleet load a cmux call
          does time out, and caching this would let one flaky poll disable the
          #7913 fallback for the whole dispatch while reporting the wrong cause.
        """
        result = self.run(
            [
                "surface",
                "resume",
                "show",
                "--workspace",
                workspace,
                *(["--surface", surface] if surface else []),
                "--json",
            ],
            timeout=min(self.timeout, 15.0),
        )
        if result.rc != 0:
            return PROBE_UNAVAILABLE
        try:
            data = json.loads(result.out)
        except Exception:  # a malformed reply is not an answer about the pane
            return PROBE_UNAVAILABLE
        if not isinstance(data, dict):
            return PROBE_UNAVAILABLE
        rb = data.get("resume_binding")
        # ONLY the dict form's `checkpoint_id`: a bare string is not a shape cmux
        # emits, and accepting extra shapes here would let this tool and
        # `fleet_state.pane_bindings` — which reads `checkpoint_id` only —
        # disagree about whether a pane is bound at all (#7913 review).
        sid = rb.get("checkpoint_id") if isinstance(rb, dict) else None
        sid = str(sid).strip() if sid else ""
        return sid or None

    def read_screen(
        self, workspace: str, lines: int = 80, surface: str | None = None
    ) -> CmuxResult:
        argv = ["read-screen", "--workspace", workspace, "--lines", str(lines)]
        if surface:
            argv += ["--surface", surface]
        return self.run(argv)

    def send_text(self, workspace: str, text: str, surface: str | None = None) -> CmuxResult:
        argv = ["send", "--workspace", workspace]
        if surface:
            argv += ["--surface", surface]
        # `--` so a message beginning with `-` is never parsed as a flag.
        argv += ["--", text]
        return self.run(argv)

    def send_enter(self, workspace: str, surface: str | None = None) -> CmuxResult:
        # A BARE ENTER IN THE ESCAPE FORM. cmux turns the two-character sequence
        # `\n` into Enter; a literal newline byte arrives as text and does not
        # submit (reproduced by the Decision relay 2026-09-17).
        return self.send_text(workspace, "\\n", surface)

    def send_escape(self, workspace: str, surface: str | None = None) -> CmuxResult:
        """Send a lone ESC key (0x1b).

        ONLY safe while pi's autocomplete menu is open: `CustomEditor.handleInput`
        then routes Escape to `tui.select.cancel` (dismiss the menu), whereas with
        no menu showing it routes to `app.interrupt`, which ABORTS the live turn.
        Callers MUST gate this on `completion_menu_open(screen)`.
        """
        return self.send_text(workspace, "\x1b", surface)

    def send_ctrl_u(self, workspace: str, surface: str | None = None) -> CmuxResult:
        """Send Ctrl-U (`tui.editor.deleteToLineStart`) to clear the composer line.

        This is the UNSAFE-free half of composer hygiene: it cannot interrupt a
        turn, and it is what stops two briefs concatenating into one turn.
        """
        return self.send_text(workspace, "\x15", surface)


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #


@dataclass
class DispatchResult:
    ok: bool
    status: str
    detail: str
    attempts: int = 0
    recoveries: list[str] = field(default_factory=list)
    fingerprint: str = ""
    reason: str = ""
    #: The OBSERVED delivery condition behind a failure (#7913). The full value
    #: set is `unreadable-pane` (the instrument failed), `composer-not-submitted`
    #: (our text is visibly still in the composer), `unparsed-queue` (a queue
    #: marker we could not parse), `not-observed` (we could not confirm the
    #: turn), `boot-blocked` (the pane sat on a boot-block prompt past the
    #: budget) and `not-ready` (the pane never reached READY).
    #:
    #: `condition` and `status` are INDEPENDENT, but the mapping is NOT
    #: symmetric: only `unreadable-pane` rides BOTH `never-became-ready` (the
    #: pre-send and recovery re-asserts refuse a read that went blind) and
    #: `sent-but-not-consumed` (the bytes went and the pane went unreadable
    #: after). `boot-blocked` and `not-ready` ride `never-became-ready` only;
    #: `composer-not-submitted`/`unparsed-queue`/`not-observed` ride
    #: `sent-but-not-consumed` only. So a consumer must read `condition` as
    #: "why", never as "when". `""` means NO delivery condition was observed: a
    #: transport failure, an unknown workspace, a refusal raised before any read
    #: happened, or the submit-Enter leg failing after a text send that was
    #: confirmed in no other way. Reported so a log names WHICH failure was seen
    #: rather than only the umbrella status — the dispatcher's recovery is chosen
    #: from this condition.
    condition: str = ""
    #: Which channel carried the bytes — the question a transport failure makes
    #: ambiguous (#4842). `transport` = the text send reached cmux, `inbox` = the
    #: durable orchestrator-inbox fallback, `none` = NEITHER (the notice is not
    #: recorded anywhere), `""` = nothing was transmitted (usage/readiness refusal).
    #: Orthogonal to `status`, which carries whether the notification CONSUMED.
    channel: str = ""
    #: The bytes were CONFIRMED in pi's hands — consumed OR queued. True means
    #: never re-send (a re-send would DUPLICATE): that is #5979's finding. False is
    #: NOT a licence to re-send either — a transport failure can leave the bytes in
    #: pi's composer, so no non-success outcome is blindly retryable; read `status`.
    #: `ok` is the STRONGER claim — `consumed` only: a caller that must know the
    #: turn STARTED keys on `ok`/exit 0 (#7743). Invariant: `ok` ⇒ `delivered`.
    delivered: bool = False

    def as_json(self) -> dict:
        return {
            "ok": self.ok,
            "delivered": self.delivered,
            "status": self.status,
            "detail": self.detail,
            "attempts": self.attempts,
            "recoveries": self.recoveries,
            "fingerprint": self.fingerprint,
            "reason": self.reason,
            "condition": self.condition,
            "channel": self.channel,
        }


class Dispatcher:
    def __init__(
        self,
        cmux: Cmux,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
        poll: float = DEFAULT_POLL,
        session_probe: Callable[[str | None, dict | None], Path | None] | None = None,
        session_fresh_s: float = DEFAULT_SESSION_FRESH_S,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.cmux = cmux
        self.sleep = sleep
        self.now = now
        self.poll = poll
        #: `(session_id, entry) -> Path | None`. The PROBE is pane-attributable:
        #: it receives the pane's resume-bound session id, never a bare cwd
        #: (#7913). An unresolvable id reaches it as None and it must return
        #: None — the caller then refuses.
        self._session_probe = session_probe or session_file_for
        #: workspace -> resume-bound session id, `None` meaning "resolved absent".
        #: A pane's binding cannot change mid-dispatch, and the gate re-probes on
        #: EVERY poll: without this, an unreadable pane cost one
        #: `cmux surface resume show` subprocess per poll — measured 91 spawns for
        #: a single dispatch at the 180s/2s defaults (#7913 review, load).
        self._session_ids: dict[tuple[str, str | None], str | None] = {}
        self.session_fresh_s = session_fresh_s
        #: Wall clock (NOT the monotonic deadline clock): freshness is a property
        #: of a file's mtime, which is wall-clock. Injected so tests are
        #: deterministic instead of racing `time.time()`.
        self.wall_clock = wall_clock
        self._log = log or (lambda message: print(message, file=sys.stderr))

    def log(self, message: str) -> None:
        self._log(message)

    # -- non-pane liveness (#7913) ------------------------------------------- #

    def pane_binding(
        self, workspace: str, surface: str | None = None
    ) -> tuple[bool, str | None]:
        """`(probe_answered, session_id)` for a pane — see `self._session_ids`.

        Keyed by `(workspace, surface)`: the two together name a PANE. Keying on
        the workspace alone would answer for whichever surface cmux has selected,
        certifying a send into a different one (#7913 review, P1).

        `probe_answered=False` means the binding could not be determined (cmux
        unreachable, timed out, no such method, or an unparseable reply); it is
        NOT memoized, so the next poll re-asks. `None` with
        `probe_answered=True` is a definitive "this pane has no binding" and IS
        memoized — one dispatch must not spawn a `cmux surface resume show` per
        poll (measured 91 at the 180 s/2 s defaults).
        """
        key = (workspace, surface)
        if key in self._session_ids:
            return True, self._session_ids[key]
        getter = getattr(self.cmux, "pane_session_id", None)
        # A cmux with no such method has NOT answered, and must not be memoized as
        # a definitive absence — that would let a duck-typed cmux poison the whole
        # dispatch with the wrong cause.
        raw: object = PROBE_UNAVAILABLE
        if getter is not None:
            try:
                raw = getter(workspace, surface)
            except TypeError:
                # A seam predating surface-scoping: ask it the old way rather than
                # silently degrading to "no binding".
                try:
                    raw = getter(workspace)
                except Exception:  # broad by design: a probe is best-effort
                    raw = PROBE_UNAVAILABLE
            except Exception:  # broad by design: a probe is best-effort
                raw = PROBE_UNAVAILABLE
        if raw is PROBE_UNAVAILABLE:
            return False, None
        session_id = raw if isinstance(raw, str) and raw else None
        self._session_ids[key] = session_id
        return True, session_id

    def session_liveness(
        self, workspace: str, entry: dict | None, surface: str | None = None
    ) -> tuple[bool, str]:
        """Is a live pi advancing THIS pane's session transcript?

        Returns `(fresh, detail)`; `detail` is always a human sentence suitable
        for the evidence log line, whether or not the signal fired. NEVER raises:
        a probe failure is a diagnostic and degrades to `(False, ...)` so the
        caller can refuse (fail-closed) rather than crash.

        The signal is PANE-attributable by construction: the transcript is
        selected by the pane's resume-bound session id, so a sibling lane
        sharing the cwd cannot stand in for this one (#7913). Three states are
        refusal, not evidence: no binding, no matching transcript, and an age
        that cannot be measured (an empty file, or a mtime ahead of the clock) —
        an unmeasurable age fails CLOSED.

        The signal is EVIDENCE of a live pi, not PROOF: a pi that wrote 200s ago
        and then exited still carries a mtime inside the window, so it can
        certify a pane that has since become a bare shell. That window is
        deliberate — #7913's own acceptance criterion — and it is why the window
        must stay short and the pane attribution must stay strict. The
        over-refusal direction is the safe one; do not widen this to close it.
        """
        session_id: str | None = None
        try:
            answered, session_id = self.pane_binding(workspace, surface)
            if not answered:
                return False, (
                    "the binding probe could not be asked (cmux unreachable, timed "
                    "out, or an unparseable reply) — it is re-probed on the next "
                    "poll, and this refusal is a refusal to GUESS"
                )
            resolved = self._session_probe(session_id, entry)
        except Exception as exc:  # broad by design: a probe is best-effort
            return False, f"session probe failed: {exc}"
        if resolved is None:
            if not session_id:
                return False, (
                    "no resume binding for this pane — its transcript cannot be "
                    "attributed, so it is UNKNOWN and the send is refused"
                )
            if not _SESSION_ID_RE.fullmatch(session_id):
                return False, (
                    f"this pane's binding is not a session id ({session_id[:24]!r}) "
                    "— refusing rather than widening the search to its cwd"
                )
            return False, (
                f"no transcript for this pane's session {session_id[:8]} — "
                "refusing rather than guessing from the cwd"
            )
        path = Path(resolved)
        try:
            st = path.stat()
        except OSError as exc:
            return False, f"session file unreadable: {exc}"
        if st.st_size <= 0:
            # A freshly-created, still-empty transcript is a pi that has written
            # no turn: it is not evidence that a live pi owns the lane.
            return False, f"session {path.name} is empty — not a liveness signal"
        age = self.wall_clock() - st.st_mtime
        if age < 0:
            # A mtime AHEAD of the clock cannot be measured (clock step, a
            # restored/copied file). Clamping it to 0 would make the lane look
            # maximally fresh forever, so an unmeasurable age refuses.
            return False, (
                f"session {path.name} has a mtime {-age:.0f}s ahead of the clock "
                "— age unmeasurable, refusing"
            )
        fresh = age <= self.session_fresh_s
        return fresh, f"session {path.name} advanced {age:.0f}s ago"

    def clear_composer(
        self, workspace: str, screen: str | None, surface: str | None = None
    ) -> list[str]:
        """Pre-send composer hygiene; returns the keystrokes actually sent.

        ALWAYS Ctrl-U (`deleteToLineStart`, cannot interrupt) so a half-typed line
        or a previous brief cannot concatenate onto ours. ADDITIONALLY Escape when
        a completion menu is OBSERVED — the one context where Escape dismisses
        rather than interrupts (`completion_menu_open`). Clearing happens BEFORE
        the brief is typed, not after a failure, so a menu can never eat the
        submit Enter in the first place.
        """
        sent: list[str] = []
        if completion_menu_open(screen):
            self.cmux.send_escape(workspace, surface)
            sent.append("escape")
        self.cmux.send_ctrl_u(workspace, surface)
        sent.append("ctrl-u")
        return sent

    def _send_allowed(
        self,
        workspace: str,
        entry: dict | None,
        screen: str | None,
        gate_evidence: str,
        surface: str | None = None,
    ) -> tuple[bool, str, str]:
        """May the bytes be written, given the pre-send read? (#7913)

        Returns `(allowed, evidence_note, refusal_reason)`. A live footer passes on
        `screen`. The ONE non-ready case that passes is UNREADABLE (a failed or
        empty read) *with* a fresh transcript OF THIS PANE'S OWN SESSION — and only
        when the gate also accepted the pane on that same signal, so a transiently
        blind read cannot smuggle a send past a gate that itself refused. A
        READABLE pane with no footer is still refused: session-mtime is weaker
        evidence than a screen that explicitly shows no pi, and this pane's own
        transcript advancing does not say the pane is at a pi prompt (#7158).
        """
        if screen_ready(screen):
            return True, "screen", ""
        if not (screen or "").strip():
            fresh, detail = self.session_liveness(workspace, entry, surface)
            if gate_evidence == "session-mtime" and fresh:
                return True, f"session-mtime ({detail})", ""
            return False, "", f"{unreadable_reason(screen)}, and {detail}"
        return False, "", not_ready_reason(screen)

    def failure_condition(self, screen: str | None, fp: str, message: str) -> str:
        """Name the OBSERVED failure condition from the last confirmation read."""
        if not (screen or "").strip():
            return "unreadable-pane"
        if pending_queue_unparsed(screen):
            return "unparsed-queue"
        if text_on_screen(screen, fp):
            return "composer-not-submitted"
        return "not-observed"

    def _deliver_via_inbox(self, tag: str, text: str, label: str) -> tuple[str, str]:
        """Append the notice to the durable inbox; return `(channel, note)`.

        NEVER raises. When the fallback itself fails, that failure is a
        DIAGNOSTIC the caller must carry, not an exception: a crash here would
        lose the notice a second time and hand the caller an exit 1 that no
        transport failure ever produces. The catch is deliberately broad —
        `Path.expanduser()` raises `RuntimeError` on an unexpandable home and a
        lone surrogate in the payload raises `UnicodeEncodeError`, NEITHER an
        `OSError` — so both diagnostics always reach the result.
        """
        try:
            path = append_to_inbox(label, text)
        except Exception as exc:  # broad by design: report, never crash
            self.log(f"{tag}durable inbox fallback failed: {exc}")
            return (
                "none",
                f" — durable inbox fallback FAILED ({exc}): the notice is not "
                f"recorded anywhere",
            )
        self.log(f"{tag}notice appended to the durable inbox: {path}")
        return "inbox", f" — delivered via the durable inbox (channel: inbox): {path}"

    def _fallback_failure(
        self,
        tag: str,
        status: str,
        fp: str,
        detail: str,
        text: str,
        label: str,
        attempts: int = 0,
        recoveries: list[str] | None = None,
        reason: str = "",
    ) -> DispatchResult:
        """A failure result that still DELIVERS the notice via the inbox (#4842).

        Before this, a bail returned its exit code and DROPPED the payload: the
        caller learned the dispatch failed and the orchestrator never learned a
        lane had finished — the exact failure the notifier exists to eliminate,
        and one that fires under the congestion that makes "which lane finished"
        matter most (measured 2026-09-23: `cmux unreachable: cmux timed out` at
        load 95-113), and again after the 2026-09-24 workspace cutover, when
        every lane's notice aimed at the retired id landed as `unknown-workspace`
        (`notify-orchestrator.sh` records it: live delivery was failing for EVERY
        lane). The fallback appends the notice to the orchestrator inbox.

        The STATUS/exit is preserved, not collapsed to success: `transport-error`
        stays exit 3 and `unknown-workspace` stays exit 2, so the failure stays
        distinguishable from `sent-but-not-consumed` (a CONSUMPTION failure,
        exit 1) — the whole reason this tool exists. If BOTH channels fail, the
        detail carries both diagnostics, `channel` is `none`, and the detail says
        the notice is not recorded anywhere.

        ⚠️ TRADE-OFF, deliberate: a transport failure DURING CONFIRMATION means the
        bytes may already have landed, so the inbox can carry a notice the pane
        also carries — a DUPLICATE. That direction is chosen on purpose: a
        duplicate notice is recoverable and obvious, a LOST one is neither, and
        the transport cannot tell us which happened.
        """
        channel, note = self._deliver_via_inbox(tag, text, label)
        return DispatchResult(
            False,
            status,
            detail + note,
            attempts=attempts,
            recoveries=recoveries or [],
            fingerprint=fp,
            reason=reason,
            channel=channel,
        )

    def _transport_failure(
        self,
        tag: str,
        fp: str,
        detail: str,
        text: str,
        label: str,
        attempts: int = 0,
        recoveries: list[str] | None = None,
        reason: str = "",
    ) -> DispatchResult:
        """A transport failure (exit 3) routed through the durable inbox fallback."""
        return self._fallback_failure(
            tag,
            "transport-error",
            fp,
            detail,
            text,
            label,
            attempts=attempts,
            recoveries=recoveries,
            reason=reason,
        )

    # -- reads --------------------------------------------------------------- #

    def workspace_state(self, workspace: str) -> dict | None:
        """The workspace's entry, or None when it is not listed.

        Raises `CmuxTransportError` when cmux itself failed — a broken transport
        must never be reported as "unknown workspace".
        """
        result = self.cmux.list_workspaces_json()
        if result.rc != 0:
            raise CmuxTransportError(
                result.err.strip()
                or f"cmux list-workspaces --json rc={result.rc}"
            )
        return workspace_entry(result.out, workspace)

    def screen(
        self,
        workspace: str,
        surface: str | None = None,
        lines: int = DEFAULT_SCREEN_LINES,
    ) -> str | None:
        """The pane text, or None when read-screen FAILED.

        None is a distinct state from "": an unreadable pane must not be treated
        as one that is simply not ready yet, or the gate would send blind and the
        recovery could pick `resend` from no information at all.
        """
        result = self.cmux.read_screen(workspace, lines=lines, surface=surface)
        return result.out if result.rc == 0 else None

    def wait_for_workspace(self, workspace: str, timeout: float) -> dict | None:
        """Wait for a freshly-created workspace to appear in `list-workspaces`.

        `cmux new-workspace` returns before the workspace is enumerable, so an
        immediate lookup can miss it for several seconds. Reported separately
        from `sent-but-not-consumed` because the diagnosis is different.
        """
        deadline = self.now() + timeout
        while True:
            entry = self.workspace_state(workspace)
            if entry is not None:
                return entry
            if self.now() >= deadline:
                return None
            self.sleep(self.poll)

    # -- readiness (Defect 2 / send-during-boot race) ------------------------ #

    def wait_until_safe_to_send(
        self,
        workspace: str,
        timeout: float,
        surface: str | None = None,
        entry: dict | None = None,
    ) -> tuple[bool, bool, str | None, str, str]:
        """Poll until sending cannot be eaten by a boot-block prompt.

        Returns `(ready, blocked_now, last_screen, evidence, refusal)`. `evidence`
        is `"screen"` when a LIVE pi footer was read, `"session-mtime"` when the
        pane read returned nothing but a fresh transcript of the PANE'S OWN
        session is evidence of a live pi (#7913), and `""` on refusal. `refusal`
        is the PROBE'S OWN diagnosis on a refusal and `""` otherwise — six
        distinct causes (no binding, no transcript, stale, empty, future mtime,
        probe error) otherwise render as one indistinguishable message, and each
        has a different remedy (#7913 review).

        `blocked_now` matters: if the prompt is ON SCREEN at the deadline the pane
        is PROVABLY not accepting input, so the caller must refuse. `ready=False`
        means no pi footer was drawn by the deadline AND no non-pane liveness
        signal was available; the caller must ALSO refuse — there is no
        "best-effort proceed" (removed in #7158): a readable pane with no live pi
        is a bare shell, and bytes written there are executed.

        The non-pane fallback fires ONLY for `UNREADABLE` (a failed or empty
        read). A READABLE pane that lacks a footer stays a refusal: session-mtime
        is weaker evidence about the LANE than a readable screen that explicitly
        shows no pi, and a live transcript says the lane's pi is working — not
        that this pane is at a prompt that can accept input.
        """
        deadline = self.now() + timeout
        screen: str | None = ""
        announced = False
        dismissals = 0
        logged_failure = False
        while True:
            refusal = ""
            screen = self.screen(workspace, surface)
            state = readiness_state(screen)
            if state == ST_BLOCKED:
                if not announced:
                    self.log(
                        "  boot-block prompt detected — dismissing with a bare Enter "
                        "(pi dist/migrations.js::showDeprecationWarnings)"
                    )
                    announced = True
                dismissal = self.cmux.send_enter(workspace, surface)
                dismissals += 1
                if dismissal.rc != 0 and not logged_failure:
                    # A dismissal that does not land leaves the pane wedged for
                    # the whole timeout with no explanation. Never swallow this.
                    logged_failure = True
                    self.log(
                        f"  WARN: boot-block dismissal REJECTED rc={dismissal.rc}: "
                        f"{dismissal.err.strip() or 'no stderr'}"
                    )
            elif state == ST_READY:
                return True, False, screen, "screen", ""
            elif state == ST_UNREADABLE:
                # The instrument failed. Before refusing (or waiting out the
                # timeout on a deterministic empty read), consult the non-pane
                # signal: a transcript advancing within the freshness window is
                # EVIDENCE of a live pi (#7913) — evidence, not proof, since a pi
                # that exited seconds ago leaves a fresh mtime behind.
                fresh, detail = self.session_liveness(workspace, entry, surface)
                refusal = detail
                if fresh:
                    self.log(
                        f"  read-screen returned nothing ({unreadable_reason(screen)}) "
                        f"but {detail} — accepting the pane as LIVE (evidence: "
                        f"session-mtime)"
                    )
                    return True, False, screen, "session-mtime", ""
            # Deadline is checked AFTER the dismissal attempt and BEFORE the
            # sleep, so a zero timeout still gets one probe + one dismissal.
            if self.now() >= deadline:
                if screen is None:
                    self.log(
                        f"  read-screen FAILED on all {dismissals + 1} probe(s) — "
                        f"pane state is unknown"
                    )
                elif boot_blocked(screen):
                    self.log(
                        f"  still boot-blocked after {dismissals} dismissal attempt(s); "
                        f"last rc={dismissal.rc if dismissals else 'n/a'}"
                    )
                return False, boot_blocked(screen), screen, "", refusal
            self.sleep(self.poll)

    # -- confirmation (Defect 1) --------------------------------------------- #

    def wait_consumed(
        self,
        workspace: str,
        before: dict | None,
        fp: str,
        timeout: float,
        require_novelty: bool = True,
    ) -> tuple[bool, str, dict | None]:
        deadline = self.now() + timeout
        after: dict | None = None
        reason = "no-poll"
        while True:
            after = self.workspace_state(workspace)
            consumed, reason = is_consumed(before, after, fp, require_novelty)
            if consumed:
                return True, reason, after
            if self.now() >= deadline:
                return False, reason, after
            self.sleep(self.poll)

    # -- the whole operation ------------------------------------------------- #

    def send_message(
        self,
        workspace: str,
        text: str,
        surface: str | None = None,
        label: str = "",
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
        consume_timeout: float = DEFAULT_CONSUME_TIMEOUT,
        appear_timeout: float = DEFAULT_APPEAR_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> DispatchResult:
        # Flatten newlines HERE, at the single boundary, exactly as `_read_message`
        # does for the CLI. A newline would submit early in pi's one-line composer,
        # and pi renders a queued submission as only its FIRST line — which is not
        # message-unique, so a multi-line body could be neither confirmed nor
        # protected from a duplicate re-send. Flattening makes the sent text, the
        # fingerprint and the confirmation identity the same string.
        text = " ".join(text.split())
        fp = fingerprint(text)
        if not fp:
            return DispatchResult(False, "empty-message", "nothing to send")
        # A negative --retries would make the confirmation loop body never run
        # while the text has ALREADY been transmitted, producing a false
        # "sent-but-not-consumed" for a message that may well have landed.
        retries = max(0, retries)

        tag = f"[{label}] " if label else ""
        try:
            before = self.wait_for_workspace(workspace, appear_timeout)
        except CmuxTransportError as exc:
            return self._transport_failure(
                tag, fp, f"{tag}cmux unreachable: {exc}", text, label
            )
        if before is None:
            # The 2026-09-24 cutover's documented loss mode: every lane-completion
            # notice aimed at the retired workspace id landed here and was
            # recorded NOWHERE (notify-orchestrator.sh: "live delivery was failing
            # for every lane"). Same durable fallback, but the status/exit stay
            # `unknown-workspace`/2 so the diagnosis is not erased.
            return self._fallback_failure(
                tag,
                "unknown-workspace",
                fp,
                f"{tag}{workspace} not found in `cmux list-workspaces --json` "
                f"after {appear_timeout:g}s",
                text,
                label,
            )

        # --- gate: never send into a boot-blocked prompt -------------------- #
        self.log(f"{tag}waiting for {workspace} to be safe to send…")
        try:
            ready, blocked_now, gate_screen, gate_evidence, gate_refusal = (
                self.wait_until_safe_to_send(workspace, ready_timeout, surface, entry=before)
            )
        except CmuxTransportError as exc:
            return self._transport_failure(
                tag, fp, f"{tag}cmux unreachable: {exc}", text, label
            )
        if blocked_now:
            return DispatchResult(
                False,
                "never-became-ready",
                f"{tag}{workspace} is sitting on the `Press any key to continue...` "
                f"boot-block prompt after {ready_timeout:g}s — refusing to send "
                f"(bytes sent at that prompt are eaten, and a partial eat submits "
                f"a truncated turn)",
                fingerprint=fp,
                condition="boot-blocked",
            )
        if not ready:
            # ⛔ NO FOOTER, NO SEND (#7158) / NO NON-PANE LIVENESS (#7913). The
            # gate refuses unless it read a live pi footer OR a fresh session
            # transcript proved a live pi. A READABLE pane with no footer may be a
            # bare login shell (a dead lane), which EXECUTES the bytes as a
            # command; an UNREADABLE pane with no session signal is invisible, so
            # writing would be blind. Both are refusals: a refusal is recoverable,
            # an executed brief is not.
            state = readiness_state(gate_screen)
            condition = condition_for(gate_screen)
            if state == ST_UNREADABLE:
                reason = (
                    f"{unreadable_reason(gate_screen)}, and no fresh session "
                    f"transcript proves a live pi"
                )
            else:
                reason = not_ready_reason(gate_screen)
            if gate_refusal:
                # The probe's OWN diagnosis, not a generic paraphrase: "no resume
                # binding" (re-bind), "no transcript for this pane" (wrong store),
                # "stale" (restart the lane), "empty"/"future mtime" (store
                # anomaly) and "probe failed" (cmux broken) each have a different
                # remedy (#7913 review).
                reason = f"{reason} ({gate_refusal})"
            return DispatchResult(
                False,
                "never-became-ready",
                f"{tag}{workspace}: {reason} within {ready_timeout:g}s — "
                f"refusing to send (condition: {condition}): the bytes would be "
                f"typed blind or into a bare shell and EXECUTED. Confirm the lane "
                f"has a live pi (or raise --ready-timeout for a slow boot), then "
                f"re-dispatch.",
                fingerprint=fp,
                condition=condition,
            )

        # --- pre-send baseline (novelty for the pending-turn check) --------- #
        # Captured BEFORE the bytes are written: a pending line already on the pane
        # is not evidence that THIS send was accepted (#5979 novelty, mirroring
        # `is_consumed`'s). It is read at the SAME depth as the confirmation read
        # so the two scopes cannot differ; if it cannot be read, the baseline stays
        # None and `pending_turn_matches` fails closed. `gate_screen` is NOT reused
        # as a baseline: the readiness probe reads a shallower window, and mixing
        # depths is exactly how a line outside the shallow window would read as
        # novel.
        before_screen = self.screen(workspace, surface, lines=RECOVERY_SCREEN_LINES)
        if before_screen is None:
            # A transient `read-screen` failure is recoverable — retry once.
            before_screen = self.screen(workspace, surface, lines=RECOVERY_SCREEN_LINES)

        # ⛔ RE-ASSERT READINESS ON THE FRESH READ (#7158, TOCTOU), now with the
        # non-pane fallback (#7913). `gate_window` is deliberately the SAME window
        # depth the gate used: `before_screen` is read deeper, and
        # `shell_prompt_below_footer`'s no-footer-block fallback scans the WHOLE
        # capture, so an unsliced deep read can carry an older shell prompt line the
        # gate never saw and refuse a pane the gate just approved. The fallback is
        # allowed ONLY on an UNREADABLE read (see `_send_allowed`).
        gate_window = "\n".join(
            (before_screen or "").splitlines()[-DEFAULT_SCREEN_LINES:]
        )
        allowed, evidence_note, refusal = self._send_allowed(
            workspace, before, gate_window, gate_evidence, surface
        )
        if not allowed:
            # `condition_for`, not a hand-rolled if/else: a window that is READABLE
            # but sitting on the boot-block prompt (`BLOCKED`) is not `not-ready`,
            # and the two have different remedies (#7913 review).
            condition = condition_for(gate_window)
            return DispatchResult(
                False,
                "never-became-ready",
                f"{tag}{workspace}: {refusal} on the pre-send read — refusing to "
                f"write blind (condition: {condition}): had the pane died after the "
                f"gate passed, the bytes would be typed into a bare shell and "
                f"EXECUTED. Re-dispatch once the pane is readable and idle.",
                fingerprint=fp,
                condition=condition,
            )
        if evidence_note != "screen":
            self.log(f"{tag}pre-send read not ready — accepted on {evidence_note}")

        # --- composer hygiene, BEFORE the brief is typed (#7913, 2nd root) --- #
        # Escape only when a completion menu is OBSERVED (safe there), always
        # Ctrl-U. This is what stops a menu eating the submit Enter and stops two
        # briefs concatenating into one turn.
        hygiene = self.clear_composer(workspace, before_screen, surface)
        self.log(f"{tag}pre-send composer hygiene: {'+'.join(hygiene)}")

        # --- transmit ------------------------------------------------------- #
        self.log(
            f"{tag}sending {len(text.encode())} bytes to {workspace}… "
            f"(readiness evidence: {gate_evidence or 'screen'})"
        )
        text_result = self.cmux.send_text(workspace, text, surface)
        if text_result.rc != 0:
            return self._transport_failure(
                tag,
                fp,
                f"{tag}cmux send (text) rc={text_result.rc}: "
                f"{text_result.err.strip() or 'no stderr'}",
                text,
                label,
            )
        enter_result = self.cmux.send_enter(workspace, surface)
        if enter_result.rc != 0:
            return DispatchResult(
                False,
                "sent-but-not-consumed",
                f"{tag}text accepted but the submit Enter failed "
                f"(rc={enter_result.rc}) — the message may sit unsent",
                attempts=1,
                fingerprint=fp,
                channel="transport",
            )

        # --- confirm the ARTIFACT, then recover ----------------------------- #
        result = DispatchResult(
            False, "sent-but-not-consumed", "", fingerprint=fp, channel="transport"
        )
        # The grace window is at least a full consume budget: a submission that is
        # real but slow to appear (cmux writes it at the turn boundary, and under
        # load reads time out) must not be mistaken for a lost one, because
        # re-sending it would duplicate the message in the lane.
        grace = max(RECOVERY_GRACE_TIMEOUT, consume_timeout)
        for attempt in range(1, retries + 2):
            result.attempts = attempt
            try:
                consumed, reason, _ = self.wait_consumed(
                    workspace, before, fp, consume_timeout
                )
            except CmuxTransportError as exc:
                return self._transport_failure(
                    tag,
                    fp,
                    f"{tag}cmux unreachable while confirming: {exc}",
                    text,
                    label,
                    # Carry forward what THIS send already learned — the transport
                    # died mid-confirmation, so the attempt count and the
                    # recoveries tried are evidence, not noise.
                    attempts=result.attempts,
                    recoveries=result.recoveries,
                    reason=result.reason,
                )
            result.reason = reason
            if consumed:
                result.ok = True
                result.delivered = True
                result.status = "consumed"
                result.detail = (
                    f"{tag}{workspace} confirmed: message became a conversation "
                    f"message (attempt {attempt}, {reason})"
                )
                return result

            # A message that never became the latest submission but IS carried by
            # pi's pending-turn display has been QUEUED for the next turn
            # (#5979): a mid-turn pane accepts the submission and only surfaces it
            # as a turn when the current turn ends — later than any bounded wait.
            # ⛔ DELIVERED, not CONSUMED (#7743): pi holds the text and drains it
            # at the turn boundary — so it becomes real only if the turn CAN end.
            # On a lane wedged on an orphaned in-flight `task`/`subagent` tool it
            # never does, and which case we are in cannot be known here, so
            # `queued` must NOT report the dispatch as complete. `delivered`
            # carries #5979's finding (do not re-send — that would duplicate); `ok`
            # stays False so the exit code is 4, never 0. The screen read is reused
            # by `recovery_action`, so the confirmation itself adds no cmux call;
            # novelty needs the one pre-send read taken above.
            screen = self.screen(workspace, surface, lines=RECOVERY_SCREEN_LINES)
            if pending_turn_matches(screen, text, before_screen):
                result.ok = False
                result.delivered = True
                result.status = "queued"
                result.detail = (
                    f"{tag}{workspace} DELIVERED but NOT consumed: pi accepted the "
                    f"message into its pending queue for the next turn (attempt "
                    f"{attempt}, {reason}) — it is NOT yet a conversation message, so "
                    f"the lane has not acted on it. Not re-sent (a re-send would "
                    f"duplicate)."
                )
                self.log(
                    f"{tag}queued ({reason}) — delivered to pi's pending queue; NOT "
                    f"yet a conversation message"
                )
                return result

            if attempt > retries:
                break

            action = recovery_action(screen, fp, text)
            result.recoveries.append(action)
            self.log(f"{tag}not consumed ({reason}) — recovery: {action}")

            if action in (R_RESEND, R_DISMISS_RESEND):
                # Do not ADD bytes until the artifact has had a grace window to
                # catch up — re-sending a message that did in fact land would
                # DUPLICATE it in the lane.
                try:
                    late_consumed, late_reason, _ = self.wait_consumed(
                        workspace, before, fp, grace
                    )
                except CmuxTransportError as exc:
                    return self._transport_failure(
                        tag,
                        fp,
                        f"{tag}cmux unreachable in the pre-recovery grace window: "
                        f"{exc}",
                        text,
                        label,
                        attempts=result.attempts,
                        recoveries=result.recoveries,
                        reason=result.reason,
                    )
                if late_consumed:
                    result.ok = True
                    result.delivered = True
                    result.status = "consumed"
                    result.detail = (
                        f"{tag}{workspace} confirmed: message became a conversation "
                        f"message (attempt {attempt}, {late_reason}, confirmed in the "
                        f"pre-recovery grace window — no duplicate sent)"
                    )
                    return result

            if action == R_RELEASE:
                # The message is visibly sitting in the composer unsent: a bare
                # Enter releases it. Re-sending the text here would DUPLICATE it.
                self.cmux.send_enter(workspace, surface)
            elif action == R_DISMISS_RELEASE:
                # A COMPLETION MENU is open over a composer that already holds our
                # brief; a bare Enter would be eaten by the menu (it accepts the
                # highlighted completion). Dismiss the menu, THEN release. No
                # re-send — the brief is present.
                self.cmux.send_escape(workspace, surface)
                self.cmux.send_enter(workspace, surface)
            elif action == R_DISMISS_RESEND:
                # The text was eaten by the boot-block prompt (possibly its
                # prefix, leaving a corrupted turn). Dismiss, wait for the TUI,
                # then send the full text again — but ONLY if the pane actually
                # became safe AND readable. Feeding the prompt a second time, or
                # writing into a pane whose state we cannot read, would recreate
                # the very corruption this tool prevents.
                dismissal = self.cmux.send_enter(workspace, surface)
                try:
                    (
                        recovery_ready,
                        recovery_blocked,
                        recovery_screen,
                        _recovery_evidence,
                        recovery_refusal,
                    ) = self.wait_until_safe_to_send(
                        workspace, RECOVERY_READY_TIMEOUT, surface, entry=before
                    )
                except CmuxTransportError as exc:
                    # The RECOVERY-site gate, wrapped exactly like its sibling at
                    # the first gate. The text was eaten, so an un-wrapped raise
                    # here is the notice recorded NOWHERE — the loss this whole
                    # fallback exists to prevent. Carry the evidence this send
                    # already gathered (attempt/recoveries/reason).
                    return self._transport_failure(
                        tag,
                        fp,
                        f"{tag}cmux unreachable in the boot-block recovery gate: "
                        f"{exc}",
                        text,
                        label,
                        attempts=result.attempts,
                        recoveries=result.recoveries,
                        reason=result.reason,
                    )
                if recovery_blocked or (not recovery_ready and recovery_screen is None):
                    result.ok = False
                    result.status = "never-became-ready"
                    # NOT a transport failure: the read said something and we
                    # refused on it, so `condition` must say WHICH refusal — an
                    # empty `condition` reads as a lost transport (#7913 review).
                    result.condition = condition_for(recovery_screen)
                    result.detail = (
                        f"{tag}{workspace} could not be recovered into a safe, "
                        f"READABLE state (blocked={recovery_blocked}, "
                        f"readable={recovery_screen is not None}, dismissal "
                        f"rc={dismissal.rc}) — the message was eaten and the "
                        f"re-send was REFUSED rather than written blind. "
                        f"Re-dispatch once the pane is idle."
                        + (f" ({recovery_refusal})" if recovery_refusal else "")
                    )
                    return result
                if not recovery_ready:
                    # ⛔ SAME FAIL-CLOSED RULE AS THE INITIAL GATE (#7158): the
                    # pane became readable after the dismissal but never drew
                    # pi's footer, so there is still no evidence a pi owns
                    # stdin. Re-sending would write the brief into whatever is
                    # there. Refuse rather than fall back to the old
                    # "re-sending anyway (confirmation will decide)".
                    result.ok = False
                    result.status = "never-became-ready"
                    result.condition = condition_for(recovery_screen)
                    result.detail = (
                        f"{tag}{workspace} was dismissed but never presented "
                        f"pi's footer within {RECOVERY_READY_TIMEOUT:g}s — the "
                        f"re-send was REFUSED rather than written into a pane "
                        f"with no live pi. Re-dispatch once the pane is idle."
                        + (f" ({recovery_refusal})" if recovery_refusal else "")
                    )
                    return result
                self.cmux.send_text(workspace, text, surface)
                self.cmux.send_enter(workspace, surface)
            else:
                # ⛔ R_RESEND writes the brief a SECOND time, so it must face the
                # same liveness gate as the first send (#7158). The `screen` used
                # by `recovery_action` was read BEFORE the duplicate-guard grace
                # window (`grace`, up to a full consume budget), so it can be
                # stale by the time we write; re-read immediately before the
                # write and re-assert readiness rather than trusting the older
                # frame.
                fresh_screen = self.screen(
                    workspace, surface, lines=RECOVERY_SCREEN_LINES
                )
                # Judged on the SAME window as the gate, for the SAME reason the
                # pre-send re-assert is sliced (see `gate_window` above): this
                # read is `RECOVERY_SCREEN_LINES` deep, and
                # `shell_prompt_below_footer`'s no-footer-block fallback scans the
                # WHOLE capture, so an unsliced window can carry an older shell
                # prompt line the gate's 80-line window never saw. Without this
                # slice the gate approves the pane and this re-assert refuses it,
                # reporting "a shell prompt is drawn BELOW pi's footer" when no
                # footer BLOCK was found at all — a lost delivery, in the one path
                # that exists to RESCUE a delivery.
                fresh_window = "\n".join(
                    (fresh_screen or "").splitlines()[-DEFAULT_SCREEN_LINES:]
                )
                if not screen_ready(fresh_window):
                    # Same non-pane fallback as the first send (#7913): an
                    # UNREADABLE re-read with a fresh transcript is accepted only
                    # when the gate itself admitted the pane on that signal.
                    allowed, _note, refusal = self._send_allowed(
                        workspace, before, fresh_window, gate_evidence, surface
                    )
                    if not allowed:
                        result.ok = False
                        result.status = "never-became-ready"
                        result.condition = condition_for(fresh_window)
                        result.detail = (
                            f"{tag}{workspace} was not ready immediately before the "
                            f"recovery re-send ({refusal}) — the re-send was REFUSED "
                            f"rather than written into a pane with no live pi "
                            f"(condition: {result.condition}). Re-dispatch once the "
                            f"pane is idle."
                        )
                        return result
                self.cmux.send_text(workspace, text, surface)
                self.cmux.send_enter(workspace, surface)

        result.ok = False
        result.status = "sent-but-not-consumed"
        # Name the CONDITION observed, not just the umbrella status (#7913): an
        # `unreadable-pane` failure is an instrument fault and must never be fed to
        # the beat's WEDGED/STALL/IDLE advice, while `composer-not-submitted` is a
        # delivery fault with a different recovery. `screen` is the last
        # confirmation read taken inside the loop.
        result.condition = self.failure_condition(screen, fp, text)
        result.detail = (
            f"{tag}{workspace} sent-but-not-consumed after {result.attempts} "
            f"attempt(s) — last reason: {result.reason or 'no-confirmation-poll'} "
            f"(condition: {result.condition}). "
            f"The bytes were transmitted but never became a conversation message. "
            f"Recoveries tried: {', '.join(result.recoveries) or 'none'}. "
            f"Inspect: cmux read-screen --workspace {workspace} --lines 40"
        )
        return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _read_message(args: argparse.Namespace) -> str | None:
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            print(f"MISSING {args.file}: {exc}", file=sys.stderr)
            return None
    else:
        text = args.text or ""
    return " ".join(text.split())


_EXIT_FOR_STATUS = {
    "transport-error": 3,
    "unknown-workspace": 2,
    # DELIVERED but NOT consumed (#7743): distinct from 0 (consumed) and 1 (not
    # consumed). Exit 4 so a shell caller never reads a queued-only send as a
    # completed dispatch.
    "queued": 4,
}


def _cmd_send(args: argparse.Namespace) -> int:
    text = _read_message(args)
    if text is None:
        return 2
    if not text:
        print("refusing to send an empty message", file=sys.stderr)
        return 2
    if re.search(r"\\[nr]", text):
        print(
            "WARN: message contains a literal backslash-n/backslash-r sequence — "
            "cmux converts it to Enter, which would split the message across turns.",
            file=sys.stderr,
        )
    dispatcher = Dispatcher(Cmux(args.cmux))
    result = dispatcher.send_message(
        args.workspace,
        text,
        surface=args.surface,
        label=args.label,
        ready_timeout=args.ready_timeout,
        consume_timeout=args.consume_timeout,
        appear_timeout=args.appear_timeout,
        retries=args.retries,
    )
    if args.json:
        print(json.dumps(result.as_json(), indent=2))
    elif result.status == "queued":
        # DELIVERED but NOT consumed: neither "OK" nor "FAIL".
        print("QUEUED " + result.detail)
    else:
        print(("OK " if result.ok else "FAIL ") + result.detail)
    if result.ok:
        return 0
    return _EXIT_FOR_STATUS.get(result.status, 1)


def _cmd_wait_ready(args: argparse.Namespace) -> int:
    dispatcher = Dispatcher(Cmux(args.cmux))
    try:
        entry = dispatcher.workspace_state(args.workspace)
        ready, blocked_now, screen, evidence, refusal = dispatcher.wait_until_safe_to_send(
            args.workspace, args.timeout, args.surface, entry=entry
        )
    except CmuxTransportError as exc:
        print(f"cmux unreachable: {exc}", file=sys.stderr)
        return 3
    payload = {
        "ready": ready,
        # #7913: the CLASSIFICATION, first-class. `UNREADABLE` is a fact about the
        # instrument (the read returned nothing), NOT about the lane — a beat that
        # consumes this must never INTERPRET it as a lane state (WEDGED/STALL/
        # IDLE/WIP). `ready` may still be true on `UNREADABLE` via the pane's
        # session-mtime evidence; the two answer different questions ("may I
        # dispatch?" vs "what did this read show?").
        #
        # `screen_readable` is the READ's success — `screen is not None` before
        # #7913; it is now "the capture carried text", so a whitespace-only
        # rc==0 read is `false`, like a failed one. `state` is the classification
        # to branch on; `screen_readable` is retained for read diagnostics only.
        "state": readiness_state(screen),
        "readiness_evidence": evidence,
        # The PROBE'S OWN diagnosis on a refusal, so a beat can tell "no resume
        # binding" from "stale transcript" from "the probe could not be asked"
        # without re-deriving it (#7913 review). Empty when
        # `ready` is true, or when the refusal had no probe to diagnose.
        "readiness_refusal": refusal,
        "boot_blocked_now": blocked_now,
        "screen_readable": bool((screen or "").strip()),
        "workspace": args.workspace,
        "screen_tail": "\n".join((screen or "").splitlines()[-6:]),
    }
    print(json.dumps(payload, indent=2) if args.json else payload)
    return 0 if ready else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    """Is this text present as a SUBMITTED conversation message in the pane?

    Presence, not novelty: `verify` answers "did this text land?", so unlike the
    `send` path it must not demand that the submission be newer than a baseline —
    it takes the baseline itself, and a delta test would report an
    already-submitted message as NOT-CONSUMED (exit 1) for ever.

    ⛔ SUBMITTED-ONLY — do NOT use `verify` to audit a `send` verdict. `send` also
    accepts a message pi queued for the next turn (the pending-turn display, which
    needs a pre-send baseline), so on a mid-turn lane a message `send` correctly
    reports as `queued`/exit 4 will print `NOT-CONSUMED` here and exit 1. This is
    a deliberate contract difference, not a bug: `verify` is a "is it a turn yet?"
    probe and has no dispatch to be novel against.
    """
    text = _read_message(args)
    if text is None:
        return 2
    fp = fingerprint(text)
    dispatcher = Dispatcher(Cmux(args.cmux))
    try:
        before = dispatcher.workspace_state(args.workspace)
        if before is None:
            print(f"unknown workspace: {args.workspace}", file=sys.stderr)
            return 2
        consumed, reason, _after = dispatcher.wait_consumed(
            args.workspace, before, fp, args.timeout, require_novelty=False
        )
    except CmuxTransportError as exc:
        print(f"cmux unreachable: {exc}", file=sys.stderr)
        return 3
    print(f"{'CONSUMED' if consumed else 'NOT-CONSUMED'} ({reason})")
    return 0 if consumed else 1


def _cmd_state(args: argparse.Namespace) -> int:
    dispatcher = Dispatcher(Cmux(args.cmux))
    try:
        entry = dispatcher.workspace_state(args.workspace)
    except CmuxTransportError as exc:
        print(f"cmux unreachable: {exc}", file=sys.stderr)
        return 3
    if entry is None:
        print(f"unknown workspace: {args.workspace}", file=sys.stderr)
        return 2
    keys = (
        "ref",
        "id",
        "custom_title",
        "current_directory",
        "latest_submitted_message",
        "latest_submitted_at",
        "latest_conversation_message",
    )
    print(json.dumps({key: entry.get(key) for key in keys}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cmux_dispatch.py",
        description="Artifact-verified cmux dispatch (#4292).",
    )
    parser.add_argument("--cmux", default="cmux", help="cmux binary (default: cmux)")
    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser("send", help="send a message and confirm it became a turn")
    send.add_argument("--workspace", required=True)
    send.add_argument("--surface")
    send.add_argument("--label", default="")
    send.add_argument("--file")
    send.add_argument("--text")
    send.add_argument("--ready-timeout", type=float, default=DEFAULT_READY_TIMEOUT)
    send.add_argument("--consume-timeout", type=float, default=DEFAULT_CONSUME_TIMEOUT)
    send.add_argument("--appear-timeout", type=float, default=DEFAULT_APPEAR_TIMEOUT)
    send.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    send.add_argument("--json", action="store_true")
    send.set_defaults(func=_cmd_send)

    ready = sub.add_parser("wait-ready", help="poll until the pane can accept input")
    ready.add_argument("--workspace", required=True)
    ready.add_argument("--surface")
    ready.add_argument("--timeout", type=float, default=DEFAULT_READY_TIMEOUT)
    ready.add_argument("--json", action="store_true")
    ready.set_defaults(func=_cmd_wait_ready)

    verify = sub.add_parser(
        "verify",
        help="confirm a message became a turn (SUBMITTED-only: a message `send` "
        "queued for the next turn reads NOT-CONSUMED here — see _cmd_verify)",
    )
    verify.add_argument("--workspace", required=True)
    verify.add_argument("--file")
    verify.add_argument("--text")
    verify.add_argument("--timeout", type=float, default=DEFAULT_CONSUME_TIMEOUT)
    verify.set_defaults(func=_cmd_verify)

    state = sub.add_parser("state", help="dump a workspace's conversation state")
    state.add_argument("--workspace", required=True)
    state.set_defaults(func=_cmd_state)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
