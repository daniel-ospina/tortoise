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

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "cmux_dispatch.py"
sys.path.insert(0, str(ROOT / "tools"))

import cmux_dispatch as cd  # noqa: E402

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


# --------------------------------------------------------------------------- #
# Hermetic dispatcher tests — the failure physics, end to end
# --------------------------------------------------------------------------- #


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
        #: must never be read as a queued success.
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
        self.read_calls = 0
        self.lag_remaining = 0
        self.visible: str | None = None

        self.pending = ""          # text in the composer, unsent
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
        # (simulated by the prompt appearing only after the gate's first look).
        fake = FakeCmux(boot_block=True, boot_polls=1)
        fake.prompt_eats_prefix = 0
        dispatcher = _dispatcher(fake)
        # Force the gate to return before the prompt is visible.
        original = dispatcher.screen

        def screen_after_first_call(ws, surface=None):
            dispatcher.screen = original  # only lie once
            return SCREEN_IDLE_READY

        dispatcher.screen = screen_after_first_call
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
        self.assertIn("grace window", result.detail)
        self.assertEqual(fake.submitted, [PROBE], "must not duplicate on lag")

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

    def test_busy_lane_pending_turn_is_a_queued_success(self):
        # THE #5979 DEFECT. `latest_submitted_message` only advances at a turn
        # boundary, so on a mid-turn lane the strict check can never be
        # satisfied: pi has ACCEPTED the message into its pending queue (the
        # `Steering:` display is up) and it becomes a turn when the current turn
        # ends. That is a DELIVERED message and it must exit 0 — the old verdict
        # spent the whole timeout and reported `sent-but-not-consumed`.
        fake = FakeCmux(queued_turn=True)
        result = self._send(fake, consume_timeout=0.0, retries=2)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, "queued")
        self.assertIn("pending queue", result.detail)
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

    def test_an_unreadable_pre_send_baseline_fails_closed(self):
        """Novelty cannot be established without a baseline, and a `queued` verdict
        without novelty is exactly the stale-line false success. The pre-send read
        failing (twice) must leave the pane's genuinely-queued message reported as
        `sent-but-not-consumed`, never as `queued`."""
        # read #1 is the readiness probe; reads #2 and #3 are the baseline + retry.
        fake = FakeCmux(queued_turn=True, fail_read_indices={2, 3})
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "sent-but-not-consumed")

    def test_a_transient_baseline_read_failure_still_confirms_the_queue(self):
        """The pre-send baseline is read twice: a single transient `read-screen`
        failure must not cost the `queued` verdict. Read #1 is the readiness probe,
        #2 the baseline, #3 the retry."""
        fake = FakeCmux(queued_turn=True, fail_read_indices={2})
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, "queued")

    def test_a_baseline_at_a_DIFFERENT_depth_than_the_confirmation_is_refused(self):
        """DEPTH-MIXING. The readiness probe reads a shallower window than the
        confirmation. If the baseline falls back to that shallower capture, a
        pending line outside it reads as NOVEL — a stale queue entry would confirm
        a send whose bytes never landed. With the deep baseline unreadable the
        verdict must fail closed, never borrow the shallow capture."""
        fake = FakeCmux(
            queued_turn=True,
            shallow_screen_without_queue=True,
            fail_read_indices={2, 3},
        )
        result = self._send(fake, consume_timeout=0.0, retries=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "sent-but-not-consumed")

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
        self.assertTrue(result.ok, result.detail)
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

    def test_exit_zero_when_the_message_is_queued_for_the_next_turn(self):
        # #5979: pi accepted the message into its pending queue. The CLI
        # contract is exit 0 — a queued message is delivered.
        fake = FakeCmux(queued_turn=True)
        rc = self._main(
            fake, "send", "--workspace", "workspace:99", "--text", PROBE,
            "--ready-timeout", "0", "--consume-timeout", "0",
        )
        self.assertEqual(rc, 0, "a queued message is a success, not a failure")

    def test_verify_is_SUBMITTED_only_and_diverges_from_send(self):
        # The documented contract: on a mid-turn lane a message `send` correctly
        # reports as `queued`/exit 0 still reads NOT-CONSUMED/exit 1 under
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
