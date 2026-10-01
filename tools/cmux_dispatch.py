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
confirmed exits non-zero with `sent-but-not-consumed`; it reports success only on
POSITIVE artifact evidence — the message as the latest submitted message, or a
NEW pending-turn entry carrying it. That evidence is pi's own render string, not
a secret, so a verdict means pi's display showed acceptance, not proof of it (see
RESIDUAL: a lane that deliberately prints pi's hint line can forge it).

QUEUED IS NOT UNCONSUMED (#5979)
--------------------------------
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
exit 0. `Dispatcher.send_message` flattens newlines up front (as `_read_message`
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

PREVENTION, then DETECTION
--------------------------
The confirmation check is a BACKSTOP. The boot-block case has already run a
corrupted turn by the time anything could observe it, so the tool also gates the
send: it refuses to write into a pane that is sitting on the prompt, because
those bytes are what the prompt consumes. Readiness is an asymmetry — the prompt
marker AND the absence of pi's status bar (see `boot_blocked`) — never a
position-based guess.

USAGE
-----
    python3 tools/cmux_dispatch.py send --workspace workspace:12 \
        --label B4 --file /path/to/brief.txt
    python3 tools/cmux_dispatch.py wait-ready --workspace workspace:12
    python3 tools/cmux_dispatch.py verify --workspace workspace:12 --text "pointer"
    python3 tools/cmux_dispatch.py state --workspace workspace:12

EXIT CODES
----------
    0  consumed — the message became a conversation message, or
       queued — pi accepted it into its pending queue (the pane's
       `Steering:`/`Follow-up:` display carries this message) for the next turn
    1  sent-but-not-consumed, or never-became-ready — NOT success
    2  usage error (missing/invalid input, unknown workspace)
    3  cmux transport error (binary missing, socket refused, non-zero rc)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: The exact string pi prints before awaiting a keypress.
BOOT_BLOCK_MARKER = "Press any key to continue"

#: pi's status bar context-window indicator, e.g. `0.0%/700k (auto)` on a fresh
#: idle pane and `3.8%/700k (auto)` mid-turn. Its presence is the cheapest
#: reliable "the TUI owns stdin now" signal: the status bar is drawn only after
#: the boot-block prompt has been satisfied. Note that a *fresh idle* pane shows
#: NO `↑`/`↓` counters — do not key readiness off those.
READY_RE = re.compile(r"\d+(?:\.\d+)?%/\d+(?:\.\d+)?[kKmM]\b")

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


def screen_ready(screen: str | None) -> bool:
    """True when pi's TUI owns stdin: a status bar drawn after any prompt."""
    return status_bar_present(screen) and not boot_blocked(screen)


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

    def as_json(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
            "attempts": self.attempts,
            "recoveries": self.recoveries,
            "fingerprint": self.fingerprint,
            "reason": self.reason,
        }


class Dispatcher:
    def __init__(
        self,
        cmux: Cmux,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
        poll: float = DEFAULT_POLL,
    ) -> None:
        self.cmux = cmux
        self.sleep = sleep
        self.now = now
        self.poll = poll
        self._log = log or (lambda message: print(message, file=sys.stderr))

    def log(self, message: str) -> None:
        self._log(message)

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
        self, workspace: str, timeout: float, surface: str | None = None
    ) -> tuple[bool, bool, str]:
        """Poll until sending cannot be eaten by a boot-block prompt.

        Returns `(ready, blocked_now, last_screen)`.

        `blocked_now` matters: if the prompt is ON SCREEN at the deadline the pane
        is PROVABLY not accepting input, so the caller must refuse. If the prompt
        was seen earlier but is gone now, the caller may proceed best-effort —
        confirmation and recovery still gate success.
        """
        deadline = self.now() + timeout
        screen: str | None = ""
        announced = False
        dismissals = 0
        logged_failure = False
        while True:
            screen = self.screen(workspace, surface)
            if boot_blocked(screen):
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
            elif screen_ready(screen):
                return True, False, screen
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
                return False, boot_blocked(screen), screen
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
            return DispatchResult(
                False, "transport-error", f"{tag}cmux unreachable: {exc}", fingerprint=fp
            )
        if before is None:
            return DispatchResult(
                False,
                "unknown-workspace",
                f"{tag}{workspace} not found in `cmux list-workspaces --json` "
                f"after {appear_timeout:g}s",
                fingerprint=fp,
            )

        # --- gate: never send into a boot-blocked prompt -------------------- #
        self.log(f"{tag}waiting for {workspace} to be safe to send…")
        try:
            ready, blocked_now, gate_screen = self.wait_until_safe_to_send(
                workspace, ready_timeout, surface
            )
        except CmuxTransportError as exc:
            return DispatchResult(
                False, "transport-error", f"{tag}cmux unreachable: {exc}", fingerprint=fp
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
            )
        if not ready and gate_screen is None:
            # We could not read the pane AND never saw a ready signal. Sending
            # blind here is how a brief gets fed to a prompt we cannot see.
            return DispatchResult(
                False,
                "never-became-ready",
                f"{tag}{workspace} could not be read (`cmux read-screen` failed) "
                f"and no ready signal was seen in {ready_timeout:g}s — refusing to "
                f"send blind",
                fingerprint=fp,
            )
        if not ready:
            self.log(
                f"{tag}no ready signal after {ready_timeout:g}s — sending anyway "
                f"(confirmation will decide)"
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
            # A transient `read-screen` failure is recoverable — retry once before
            # giving up the `queued` verdict for this send.
            before_screen = self.screen(workspace, surface, lines=RECOVERY_SCREEN_LINES)
            if before_screen is None:
                self.log(
                    f"{tag}no readable pre-send baseline — the queued verdict "
                    f"will fail closed for this attempt"
                )

        # --- transmit ------------------------------------------------------- #
        self.log(f"{tag}sending {len(text.encode())} bytes to {workspace}…")
        text_result = self.cmux.send_text(workspace, text, surface)
        if text_result.rc != 0:
            return DispatchResult(
                False,
                "transport-error",
                f"{tag}cmux send (text) rc={text_result.rc}: "
                f"{text_result.err.strip() or 'no stderr'}",
                fingerprint=fp,
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
            )

        # --- confirm the ARTIFACT, then recover ----------------------------- #
        result = DispatchResult(False, "sent-but-not-consumed", "", fingerprint=fp)
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
                result.status = "transport-error"
                result.detail = f"{tag}cmux unreachable while confirming: {exc}"
                return result
            result.reason = reason
            if consumed:
                result.ok = True
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
            # `queued` is a DELIVERED message (exit 0), not a failure. The screen
            # read is reused by `recovery_action`, so the confirmation itself adds
            # no cmux call; novelty needs the one pre-send read taken above.
            screen = self.screen(workspace, surface, lines=RECOVERY_SCREEN_LINES)
            if pending_turn_matches(screen, text, before_screen):
                result.ok = True
                result.status = "queued"
                result.detail = (
                    f"{tag}{workspace} confirmed: pi accepted the message into its "
                    f"pending queue for the next turn (attempt {attempt}, {reason})"
                )
                self.log(
                    f"{tag}queued ({reason}) — the lane is mid-turn; the message is "
                    f"in pi's pending queue"
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
                late_consumed, late_reason, _ = self.wait_consumed(
                    workspace, before, fp, grace
                )
                if late_consumed:
                    result.ok = True
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
            elif action == R_DISMISS_RESEND:
                # The text was eaten by the boot-block prompt (possibly its
                # prefix, leaving a corrupted turn). Dismiss, wait for the TUI,
                # then send the full text again — but ONLY if the pane actually
                # became safe AND readable. Feeding the prompt a second time, or
                # writing into a pane whose state we cannot read, would recreate
                # the very corruption this tool prevents.
                dismissal = self.cmux.send_enter(workspace, surface)
                recovery_ready, recovery_blocked, recovery_screen = (
                    self.wait_until_safe_to_send(
                        workspace, RECOVERY_READY_TIMEOUT, surface
                    )
                )
                if recovery_blocked or (not recovery_ready and recovery_screen is None):
                    result.ok = False
                    result.status = "never-became-ready"
                    result.detail = (
                        f"{tag}{workspace} could not be recovered into a safe, "
                        f"READABLE state (blocked={recovery_blocked}, "
                        f"readable={recovery_screen is not None}, dismissal "
                        f"rc={dismissal.rc}) — the message was eaten and the "
                        f"re-send was REFUSED rather than written blind. "
                        f"Re-dispatch once the pane is idle."
                    )
                    return result
                if not recovery_ready:
                    self.log(
                        f"{tag}recovery: no ready signal — re-sending anyway "
                        f"(confirmation will decide)"
                    )
                self.cmux.send_text(workspace, text, surface)
                self.cmux.send_enter(workspace, surface)
            else:
                self.cmux.send_text(workspace, text, surface)
                self.cmux.send_enter(workspace, surface)

        result.ok = False
        result.status = "sent-but-not-consumed"
        result.detail = (
            f"{tag}{workspace} sent-but-not-consumed after {result.attempts} "
            f"attempt(s) — last reason: {result.reason or 'no-confirmation-poll'}. "
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
    else:
        print(("OK " if result.ok else "FAIL ") + result.detail)
    if result.ok:
        return 0
    return _EXIT_FOR_STATUS.get(result.status, 1)


def _cmd_wait_ready(args: argparse.Namespace) -> int:
    dispatcher = Dispatcher(Cmux(args.cmux))
    try:
        ready, blocked_now, screen = dispatcher.wait_until_safe_to_send(
            args.workspace, args.timeout, args.surface
        )
    except CmuxTransportError as exc:
        print(f"cmux unreachable: {exc}", file=sys.stderr)
        return 3
    payload = {
        "ready": ready,
        "boot_blocked_now": blocked_now,
        "screen_readable": screen is not None,
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
    reported as `queued`/exit 0 will print `NOT-CONSUMED` here and exit 1. This is
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
