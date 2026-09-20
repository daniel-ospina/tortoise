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

# --------------------------------------------------------------------------- #
# Verbatim screen fixtures (captured 2026-09-20, pi 0.85.1)
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

#: The CORRUPTION case: the prompt ate the pointer's prefix and the remainder
#: was submitted as a real turn. Observed live: latest_submitted_message == "292".
SCREEN_TRUNCATED_TURN = """\
\u2500\u2500 \u280b Working \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
292
/private/tmp
\u21912.1k \u2193489 R25k CH91.1% $0.003 4.1%/700k (auto)                          (deepseek) deepseek-flash \u2022 high
"""

PROBE = "DISPATCH-PROBE-BOOTBLOCK-4292 :: reply with the single word ACK4292"

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
        self._tick()
        if self.state == "boot_block":
            return cd.CmuxResult(0, SCREEN_BOOT_BLOCK)
        if self.state == "booting":
            return cd.CmuxResult(0, "[loop-enforcer] loaded\n[verification-gate] loaded\n")
        screen = SCREEN_IDLE_READY
        if self.pending:
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
        # the composer. Correct recovery is a bare Enter, NOT a re-send.
        fake = FakeCmux(enter_is_noop=1)
        result = self._send(fake, consume_timeout=0.0)
        self.assertTrue(result.ok, result.detail)
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
        fake = FakeCmux(never_consumes=True)
        result = self._send(fake, consume_timeout=0.0, retries=2)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "sent-but-not-consumed")
        self.assertIn("sent-but-not-consumed", result.detail)
        self.assertEqual(result.attempts, 3)  # initial + 2 retries

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
