"""#14407 — every abuse notification is ATTRIBUTABLE and its rate is BOUNDED.

The owner reported "Telegram alerts are out of control". The code-level cause is
that the human channel and the agent channel were two different decisions:

* the INCIDENT (GitHub issue) is deduped by the store's create-once R2 object per
  ``(kind, subject)`` — one issue for a lifecycle;
* the TELEGRAM PUSH was emitted per DETECTION by ``notify.notify_abuse``, gated
  only by the engine's in-process window claim — so one open incident pushed every
  window, and 5 of the 7 abuse kinds never filed anything at all (only the two
  incident kinds did), leaving a message no issue, runbook or search could attribute.

These tests pin the fixed contract:

1. a filing class pushes exactly ONCE per incident lifecycle, and the message
   names the issue (attribution);
2. a new EPISODE (the incident was resolved) pushes exactly once more — the bound
   is the episode, never a mute;
3. a measurement class (R3/R4/R8) files nothing BY DESIGN (#10156's ACTIVITY
   register) and delivers a bounded digest instead of one message per window;
4. when the incident channel cannot be built at all the human is STILL told — the
   message says why it is not attributable (never mute a security class);
5. the engine resolves its incidents when a rule's flag episode ends, so an
   incident cannot stay open (and its issue stale) for the rest of time.

They are written to FAIL on the pre-fix code: pre-fix, each ``notify_abuse`` call
sent one Telegram message and (for ``abuse_flag``) filed nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tortoise.abuse import AbuseEngine
from tortoise.alert_store import AlertStore
from tortoise.hosted_backup import MemoryStorage

# ── a REAL AlertStore over an in-memory object store ──────────────────────────
# The lifecycle under test is the store's own, so the double is the STORAGE and
# the EGRESS only — stub out ``open_incident_state`` and the dedup this issue is
# about would not be exercised at all.


class _RecordingStore:
    """A real ``AlertStore`` plus the egress it would perform, recorded."""

    def __init__(self, search_open=None) -> None:
        self.pushes: list[str] = []
        self.filed: list[tuple[str, str]] = []
        self.closed: list[int] = []
        self.comments: list[tuple[int, str]] = []
        self._closed: set[int] = set()
        self._next = 1
        self.store = AlertStore(
            MemoryStorage(),
            file_issue=self._file,
            close_issue=self._close,
            search_open=search_open or (lambda kind, org: []),
            push_telegram=self.pushes.append,
            issue_open=self._open,
            comment_issue=lambda n, body: self.comments.append((n, body)),
            repo="daniel-ospina/tortoise",
        )

    def _file(self, title: str, body: str) -> int:
        number = self._next
        self._next += 1
        self.filed.append((title, body))
        return number

    def _close(self, number: int, comment: str | None = None) -> None:
        self.closed.append(number)
        self._closed.add(int(number))

    def _open(self, number: int) -> bool:
        return int(number) not in self._closed

    # the seam ``notify`` consumes
    def open_incident_state(self, kind, org_id="", detail=None, writer=None):
        return self.store.open_incident_state(kind, org_id, detail, writer)

    def resolve_incident_state(self, kind, org_id="", **kw):
        return self.store.resolve_incident_state(kind, org_id, **kw)

    def open_incident(self, kind, org_id="", detail=None, writer=None):
        return self.store.open_incident(kind, org_id, detail, writer)

    def incident_record(self, kind, org_id=""):
        return self.store.incident_record(kind, org_id)

    def migrate_incident_subject(self, kind, new_subject, legacy_subject):
        return self.store.migrate_incident_subject(kind, new_subject, legacy_subject)


@pytest.fixture
def delivered(monkeypatch):
    """A recording incident store wired into ``notify``, plus the sent messages."""
    import tortoise.notify as notify

    calls = _RecordingStore()
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (calls, ""))
    # The activity (non-filing) classes deliver through ``telegram_send``; route
    # it into the same recording list so a test never touches the network.
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: calls.pushes.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()
    yield calls
    notify.reset_abuse_delivery_for_tests()


def _flag(notify, org="org-a", rule="point_create"):
    notify.notify_abuse(
        "abuse_flag", {"org_id": org},
        {"rule": rule, "count": 600, "threshold": 500, "window_s": 3600},
    )


# ── 1. one incident → one push, and the push is attributable ─────────────────

def test_one_incident_is_one_push_for_its_whole_lifecycle(delivered):
    """Pre-fix this sent 10 messages and filed 0 issues."""
    import tortoise.notify as notify

    for _ in range(10):
        _flag(notify)

    assert len(delivered.pushes) == 1, delivered.pushes
    assert len(delivered.filed) == 1, "the lifecycle must carry ONE issue"
    message = delivered.pushes[0]
    # The push names the INCIDENT kind (the canonical vocabulary), the subject's
    # rule, and the issue — the three facts a triager needs.
    assert "ABUSE_FLAG_RAISED" in message
    assert "issue #1" in message, f"not attributable: {message!r}"
    assert "rule=point_create" in message, "the triager's rule context was dropped"
    assert "count=600" in delivered.filed[0][1], "the count belongs on the issue"
    # ⛔ THE LABEL, pinned (review cycles 5–8). The episode's OPENING push is the
    # STORE's create-once template (`AlertStore._telegram_text`), because opening the
    # incident is the store's decision and every incident kind shares it. That makes
    # TWO ALERT labels, both deliberate: this opening message wears the DR vocabulary,
    # while the 24h REMINDER (the re-alarm arm of `notify._notify_abuse_incident`)
    # keeps the abuse-specific `🚨 Tortoise Security: <kind>` header — one of the THREE
    # renderers of `notify._abuse_message` (the other two are the unattributed delivery
    # and the activity digest), so the per-detection TEXT survives on those paths even
    # though the per-detection SENDER is gone. Each is attributable in its own words,
    # but they do not share one token — the two store templates spell the INCIDENT kind
    # (`ABUSE_FLAG_RAISED`) while the reminder spells the EVENT kind (`abuse_flag`) —
    # so a reader must not grep one label and conclude the other message does not
    # exist. A relabelling of a security alert into the DR vocabulary is fine ONLY
    # while it is stated and watched: the runbook's `ABUSE_FLAG_RAISED` row names both
    # spellings, and this assertion makes any future template change visible.
    assert message.startswith("🚨 DR alert: ABUSE_FLAG_RAISED"), (
        "the episode push no longer carries the store's create-once template — "
        f"if that is deliberate, update the runbook row and this pin: {message[:60]!r}")


def test_the_incident_subject_is_rule_scoped(delivered):
    import tortoise.notify as notify

    _flag(notify, rule="point_create")
    _flag(notify, rule="key_create")

    assert len(delivered.filed) == 2, delivered.filed
    assert "point_create" in delivered.filed[0][0]
    assert "key_create" in delivered.filed[1][0]


def test_an_open_episode_re_alarms_at_most_once_per_window(delivered, monkeypatch):
    """A persistent breach must not page every window, and must not go silent.

    Measured shape (#10576): ONE org stayed over its R1 threshold for 21 days
    behind ONE open issue, while the pre-fix code re-claimed the alert hourly —
    ~24 messages/day, none of them naming the issue that already tracked it.
    """
    import tortoise.notify as notify

    clock = [1_000.0]
    monkeypatch.setattr(notify, "_delivery_now", lambda: clock[0])

    _flag(notify)  # FILED: the store's create-once push opens the window
    assert len(delivered.pushes) == 1

    for _ in range(5):  # same window → folded, no message
        clock[0] += 60.0
        _flag(notify)
    assert len(delivered.pushes) == 1, delivered.pushes

    clock[0] += notify._INCIDENT_DELIVERY_WINDOW_S
    _flag(notify)
    assert len(delivered.pushes) == 2, delivered.pushes
    assert "Still open" in delivered.pushes[-1]
    assert "issue #1" in delivered.pushes[-1], "a reminder must name the incident"
    # The OTHER half of the arm-conditional pointer (round 7): the NUMBERED arm must
    # KEEP it, or a mutant deleting the parenthetical from all three arms stays green.
    assert "the issue comment trail is the recurrence record" in delivered.pushes[-1]
    # ⛔ THE SECOND ALERT LABEL, pinned (#14407 review cycles 6–8). One episode emits
    # THREE messages and each wears its own string: the OPENING push is the store's
    # create-once template (`🚨 DR alert: …`, shared with every incident kind), this
    # REMINDER renders the abuse-specific `notify._abuse_message` text, and the episode
    # END adds the store's `✅ DR resolved: …` close — which contains neither alert
    # label. They do not even share a KIND spelling: the two store templates spell the
    # INCIDENT kind (`ABUSE_FLAG_RAISED`) while this reminder spells the EVENT kind
    # (`abuse_flag`), so a triager who greps one label — or one spelling — concludes the
    # other message does not exist. (The reminder has THREE strings: `issue #N` when the
    # sentinel carries a number, `an open incident with no issue number on record` when
    # it carries none — a deferred-filing placeholder or a post-close tombstone — and
    # `could not be read just now` only when the read-back ITSELF raised. All three are
    # attributable, which is what `number_known` exists to keep apart.) Both ALERT
    # labels are read here.
    assert delivered.pushes[0].startswith("🚨 DR alert: ABUSE_FLAG_RAISED"), (
        delivered.pushes[0])
    assert delivered.pushes[-1].startswith("🚨 Tortoise Security:"), (
        "the reminder's abuse-specific header changed — if that is deliberate, the "
        f"runbook row names both labels and must change with it: {delivered.pushes[-1][:60]!r}")
    assert len(delivered.filed) == 1, "a re-alarm must never re-file"


def test_a_reminder_over_a_number_less_sentinel_says_which_state_it_is(monkeypatch):
    """The reminder's THREE strings, the two that are NOT `issue #N` pinned (#14407 review 12).

    `notify._notify_abuse_incident` selects its reference with
    `f"issue #{number}" if number else ..." if not number_known else ..."`, so the
    string for a sentinel that exists WITHOUT a number (a deferred-filing
    placeholder, or the tombstone a failed delete leaves after a close) is
    "an open incident with no issue number on record" — NOT the "could not be
    read just now" text, which belongs to the arm where the read-back itself RAISED.
    The distinction is what `number_known` exists for, and no test drove the
    number-less arm: swapping its string for the raise arm's left the whole file
    green (measured, 47 passed).
    """
    import tortoise.notify as notify
    from tortoise.alert_store import OpenOutcome

    class _NumberlessStore:
        """A live incident whose sentinel carries no `issue_number`."""

        def open_incident_state(self, kind, subject="", detail=None, writer=None):
            return OpenOutcome.DEDUP

        def incident_record(self, kind, subject=""):
            return {"issue_number": None, "detail": "deferred filing"}

    sent: list[str] = []
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_NumberlessStore(), ""))
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()

    # first detection spends the window on the store's create-once push (which the
    # store does, not this path), so drive the RE-ALARM by releasing the window.
    notify.notify_abuse("abuse_review_needed", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 900})
    notify.release_abuse_delivery("abuse_review_needed", "org-a:point_create")
    notify.notify_abuse("abuse_review_needed", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 900})

    reminder = sent[-1]
    assert "an open incident with no issue number on record" in reminder, reminder
    assert "could not be read" not in reminder, (
        "the number-less arm borrowed the RAISE arm's string — a reader is told the "
        f"read failed when it did not: {reminder!r}")
    # Round 5, P2: the sentence also said "the issue comment trail is the recurrence
    # record", naming a record that cannot exist in an arm whose own clause says
    # there is no issue number. The pointer is now arm-conditional and pinned here.
    assert "issue comment trail" not in reminder, (
        "the reminder points at a comment trail on an incident it just said has no "
        f"issue: {reminder!r}")
    notify.reset_abuse_delivery_for_tests()


# ── 2. the bound is the EPISODE, never a mute ────────────────────────────────
def test_a_pre_14407_rule_less_incident_is_adopted_not_duplicated(delivered):
    """Subjects became rule-scoped here, so an existing ``{org}`` incident must
    be ADOPTED — otherwise the first recurrence files a second issue for one
    condition (the #2844 alias class). Measured on record: #10576."""
    import tortoise.notify as notify

    # The pre-#14407 sentinel, exactly as the old code wrote it (subject = org).
    delivered.store.open_incident_state(
        "abuse_review_needed", "org-a", {"detail": "legacy rule-less incident"})
    assert len(delivered.filed) == 1
    notify.reset_abuse_delivery_for_tests()

    notify.notify_abuse("abuse_review_needed", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 1338})

    assert len(delivered.filed) == 1, "a live legacy incident must be adopted"
    assert len(delivered.pushes) == 2, delivered.pushes  # legacy alert + reminder
    assert "issue #1" in delivered.pushes[-1]


def test_an_adopted_legacy_incident_is_not_closed_by_the_other_rules_window(
        delivered):
    """Adoption must MOVE the sentinel, not collapse the subject.

    The pre-fix adoption returned the bare ``{org}`` subject whenever a
    pre-#14407 object existed, so every later filing/dedup for that kind/org used
    the SHARED spelling while the resolver released it for whichever rule ended:
    rule B going clean closed rule A's incident and pushed a false all-clear, then
    rule A's next detection re-filed — the #3029/#3031 flip, on the exact org
    (#10576) this migration exists for.
    """
    import tortoise.notify as notify

    # The pre-#14407 sentinel, exactly as the old code wrote it (subject = org),
    # for rule A.
    delivered.store.open_incident_state(
        "abuse_review_needed", "org-a", {"detail": "legacy rule-less incident"})
    assert len(delivered.filed) == 1
    notify.reset_abuse_delivery_for_tests()

    # rule A's detection adopts it (moves the sentinel onto A's scoped key).
    notify.notify_abuse("abuse_review_needed", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 1338})
    assert len(delivered.filed) == 1, "adoption must not file a second issue"
    assert delivered.closed == []

    # rule A's own episode end still resolves it (the recovery path works) —
    # after the recovery window: #3146 applies to the abuse kinds, so ONE clean
    # observation is observation 1 of DR_RECOVERY_STREAK and the close lands on
    # the second (the engine's pending-retry supplies it in production).
    #
    # ⛔ There is deliberately NO ``resolve_abuse_episode("org-a", "key_create")``
    # step here with an ``assert delivered.closed == []`` after it: it was DEAD
    # WEIGHT (agent-infra#3442's rule 3 — an assertion whose value cannot vary
    # under any mutation of this diff). Adoption has already MOVED the sentinel
    # onto A's scoped key, so a resolution aimed at the bare ``{org}`` spelling or
    # at another rule's key resolves nothing, and a mutant doing exactly that ran
    # GREEN. Cross-rule isolation is carried by the LIVE rows instead: the
    # ``filed == 1`` assertion above (adoption must not file a second issue) and
    # ``test_the_incident_subject_is_rule_scoped`` (a shared-subject mutant reds
    # it), plus the two clean-observation assertions below.
    notify.resolve_abuse_episode("org-a", "point_create")
    assert delivered.closed == [], (
        "one clean observation closed the incident — the #3146 recovery window "
        f"is not applied to an abuse kind ({delivered.closed})")
    notify.resolve_abuse_episode("org-a", "point_create")
    assert delivered.closed == [1], delivered.closed


def test_a_rule_flapping_at_its_threshold_files_one_incident(delivered):
    """#3146 for the ABUSE plane: a boundary-hovering rule must not churn.

    R1 is a bounded sliding-window measurement, so an org whose hourly burst
    sits just above/below the threshold crosses it repeatedly. With
    ``resolve_streak=1`` (the pre-fix declaration) EVERY crossing was a full
    episode: the clean window closed the issue, released the flag claim and
    re-armed delivery, so the next burst re-flagged, found no LIVE issue and
    filed a NEW one — one issue open/close pair plus a 🚨/✅ pair per crossing,
    i.e. up to ~2× the pre-fix chat volume on the exact shape #14407 bounds.

    With the declared window, a single clean observation keeps the incident
    pending, and the re-flag in between is a ``DEDUP`` whose
    ``_note_still_unhealthy`` resets the count — so the flap collapses to ONE
    incident with no resolution announced.
    """

    class _FlappingStore:
        """R1's window hovering at the threshold: burst → ages out → burst."""

        def __init__(self) -> None:
            self.over = True
            self.flag = None
            self.flag_clears = 0

        def window_sum(self, org_id, rule, window_s, now):
            return 600 if self.over else 0

        def latest_flag_at(self, org_id, rule):
            return self.flag

        def flag_org(self, org_id, rule, details, now=None):
            self.flag = now

        def flag_clear(self, org_id, rule, now=None):
            self.flag = None
            self.flag_clears += 1

    engine_store = _FlappingStore()
    engine = AbuseEngine(engine_store)
    # Each step advances only a minute, so the flag stays INSIDE its staging
    # window and this test measures the episode bound, not the stage-2 escalation.
    clock = [datetime.now(timezone.utc)]  # noqa: UP017
    for _ in range(5):
        engine_store.over = True          # the burst: over threshold
        engine._evaluate("org-a", "point_create", 500, 3600, clock[0])
        clock[0] = clock[0] + timedelta(seconds=60)
        engine_store.over = False         # the burst ages out: window clean
        engine._evaluate("org-a", "point_create", 500, 3600, clock[0])
        clock[0] = clock[0] + timedelta(seconds=60)

    assert len(delivered.filed) == 1, (
        "a rule hovering at its threshold filed an issue per crossing: "
        f"{len(delivered.filed)} issues for ONE condition")
    assert delivered.closed == [], (
        f"a single clean observation resolved a flapping incident ({delivered.closed})")
    assert len(delivered.pushes) == 1, delivered.pushes


def test_a_new_episode_re_fires_exactly_once(delivered):
    """The store path's END-TO-END bound: alert, close, alert — 3, not 7.

    Pre-fix this walk sent 3 + 4 = 7 messages; a fix that MUTED would send 3 or
    fewer at the first assertion below, so the arm discriminates the mute
    direction on the store path.

    ⛔ IT DOES NOT DISCRIMINATE THE LEDGER RELEASE, and saying so here is the fix
    for an over-claiming docstring (round 2, P2 — the reviewer proved this arm
    stays GREEN with `release_abuse_delivery` replaced by a no-op): in the store
    path the second episode's page is the store's create-once FILED push, not the
    ledger, so the push COUNT cannot see a release that did not happen. The same
    masking is why the no-channel sibling below exists: there the ledger is the
    only delivery, and `assert len(sent) == 2` reds without the release. The
    property is carried by
    `test_episode_end_re_arms_abuse_flag_where_the_ledger_is_the_only_delivery`;
    this arm carries the end-to-end shape (one issue, one close, no duplicate
    filing) and is kept for that, not for the release.
    """
    import tortoise.notify as notify

    for _ in range(3):
        _flag(notify)
    assert len(delivered.pushes) == 1

    # The engine observed the rule's window go clean → episode over (see abuse.py).
    # The close needs the #3146 recovery window: DR_RECOVERY_STREAK consecutive
    # clean observations, so resolve twice (in production the second is the
    # engine's pending retry, ~1h later).
    notify.resolve_abuse_episode("org-a", "point_create")
    notify.resolve_abuse_episode("org-a", "point_create")
    assert delivered.closed == [1], "the episode-end close is the recovery path"

    for _ in range(4):
        _flag(notify)

    assert len(delivered.pushes) == 3, delivered.pushes  # alert, resolved, alert
    assert "issue #2" in delivered.pushes[-1]


def test_episode_end_re_arms_abuse_flag_where_the_ledger_is_the_only_delivery(
        monkeypatch):
    """The ledger is armed by the notification EVENT kind and released by the
    INCIDENT kind, and for ``abuse_flag`` those are different strings
    (``abuse_flag`` vs ``ABUSE_FLAG_RAISED``), so episode end released nothing.

    In the store path that is masked (the store's create-once push carries the
    opening message); in the NO-CHANNEL shape the ledger is the only delivery
    there is, so a genuinely new episode folded into the previous episode's
    window — up to 24h of silence, the direction #4872/R2 forbids.
    """
    import tortoise.notify as notify

    sent: list[str] = []
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (
        None, "no alert channel configured (DR_ISSUES_PAT unset)"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()

    clock = [1_000.0]
    monkeypatch.setattr(notify, "_delivery_now", lambda: clock[0])

    _flag(notify)                              # episode 1 opens
    assert len(sent) == 1, sent

    clock[0] += 600                            # the rule's window goes clean
    notify.resolve_abuse_episode("org-a", "point_create")

    clock[0] += 600                            # a NEW episode, same day
    _flag(notify)
    assert len(sent) == 2, (
        "a new episode was folded into the previous episode's window — the "
        f"episode bound had become a mute ({sent})")

    notify.reset_abuse_delivery_for_tests()


def test_resolution_announces_before_it_deletes(delivered):
    import tortoise.notify as notify

    _flag(notify)
    # TWO clean observations: the #3146 recovery window is DR_RECOVERY_STREAK.
    notify.resolve_abuse_episode("org-a", "point_create")
    notify.resolve_abuse_episode("org-a", "point_create")
    assert any("resolved" in p for p in delivered.pushes), delivered.pushes


# ── 3. the measurement classes: declared non-filing, digest delivery ─────────

def test_a_failed_send_does_not_consume_the_digest_window(monkeypatch):
    """A Telegram blip must not mute the class for a whole window.

    `_claim_delivery` arms the window before the send, so a swallowed send
    failure used to cost that subject the full window (24h for an incident class
    in the no-channel shape this PR declares as prod's residual). A failed send
    releases the window instead.
    """
    import tortoise.notify as notify

    attempts: list[str] = []

    def _boom(bot, chat, text, **k):
        attempts.append(text)
        raise RuntimeError("telegram down")

    monkeypatch.setattr(notify, "telegram_send", _boom)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()
    for _ in range(2):
        notify.notify_abuse("abuse_new_ip", {"org_id": "org-a"}, {"country": "ES"})

    assert len(attempts) == 2, (
        "the failed send consumed the window — the second detection was "
        f"swallowed instead of retried ({len(attempts)} attempt(s))")
    notify.reset_abuse_delivery_for_tests()


def test_a_failed_unattributed_send_does_not_consume_the_window(monkeypatch):
    """The no-incident-channel path releases its window too — pin it.

    This is the shape this PR declares as prod's residual, and
    ``_deliver_unattributed``'s own docstring says one Telegram blip must not cost
    that subject the whole window. Removing ``release_abuse_delivery`` from it left
    every test green: a guard whose removal no test detects is not a guard.
    """
    import tortoise.notify as notify

    attempts: list[str] = []

    def _boom(bot, chat, text, **k):
        attempts.append(text)
        raise RuntimeError("telegram down")

    monkeypatch.setattr(notify, "_abuse_alert_store",
                        lambda: (None, "no alert channel configured"))
    monkeypatch.setattr(notify, "telegram_send", _boom)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()
    for _ in range(2):
        _flag(notify)

    assert len(attempts) == 2, (
        "the failed unattributed send consumed the window — the class stays mute "
        f"after the channel recovers ({len(attempts)} attempt(s))")
    notify.reset_abuse_delivery_for_tests()


def test_a_failed_re_alarm_does_not_consume_the_reminder_window(monkeypatch):
    """The DEDUP re-alarm releases on a failed send — pin it.

    A reminder whose send failed must not SPEND the 24h window: that turns a
    channel blip into silence for a persistent breach (#4872). Removing the
    release from the DEDUP arm left every test green.
    """
    import tortoise.notify as notify
    from tortoise.alert_store import OpenOutcome

    attempts: list[str] = []

    class _DedupStore:
        def open_incident_state(self, *a, **kw):
            return OpenOutcome.DEDUP

        def incident_record(self, *a, **kw):
            return {"issue_number": 5}

    def _boom(bot, chat, text, **k):
        attempts.append(text)
        raise RuntimeError("telegram down")

    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_DedupStore(), ""))
    monkeypatch.setattr(notify, "telegram_send", _boom)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()
    for _ in range(2):
        _flag(notify)

    assert len(attempts) == 2, (
        "the failed reminder consumed the 24h window — a channel blip became "
        f"silence for a persistent breach ({len(attempts)} attempt(s))")
    notify.reset_abuse_delivery_for_tests()


def test_an_unrecognised_outcome_still_reaches_the_human(monkeypatch):
    """A FOURTH ``OpenOutcome`` fact must not become a MUTE (#14407 review P2).

    The decision chain names two facts by identity (``is DEDUP`` / ``is FILED``),
    so a fact added to the enum later matches neither. Releasing the window alone
    does NOT close that hole: while the store keeps answering with the
    unclassified fact, every detection takes the same arm and releases again, so
    nothing is ever delivered — a total mute of a security class for as long as
    the fact lasts, which is the direction #4872 forbids. The fall-through must be
    the labelled delivery (the reviewer's prescription), bounded by the same
    window, and it must say the state is UNKNOWN rather than assert an absence.
    """
    import tortoise.notify as notify
    from tortoise.alert_store import OpenOutcome

    sent: list[str] = []

    class _FourthFactStore:
        fact: object = "a fourth fact"         # neither FILED, DEDUP nor SUPPRESSED

        def open_incident_state(self, *a, **kw):
            return self.fact

        def incident_record(self, *a, **kw):
            return {"issue_number": 5}

    store = _FourthFactStore()
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (store, ""))
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()
    for _ in range(5):
        _flag(notify)

    # ONE message for the window, not five: the bound still applies.
    assert len(sent) == 1, sent
    assert "not classify" in sent[0], sent[0]
    # UNKNOWN, never asserted as absent or as filed.
    assert "UNKNOWN" in sent[0], sent[0]
    assert "no incident was filed" not in sent[0], sent[0]

    # …and a recognised fact still delivers its own way afterwards.
    store.fact = OpenOutcome.DEDUP
    notify.reset_abuse_delivery_for_tests()
    _flag(notify)
    assert len(sent) == 2, sent
    assert "Still open" in sent[1], sent[1]
    notify.reset_abuse_delivery_for_tests()


def test_a_suppressed_outcome_stays_silent_and_releases_its_window(
        monkeypatch):
    """``SUPPRESSED`` is an OPERATOR decision, so it is the one silent fact.

    Pinned alongside the arm above because the fix there must not sweep this one
    up: ``ops/suppression.json`` pausing a kind is an explicit human choice, and
    delivering anyway would override it. The window is released rather than spent
    so that LIFTING the pause of a still-live condition pages at once.
    """
    import tortoise.notify as notify
    from tortoise.alert_store import OpenOutcome

    sent: list[str] = []

    class _SuppressingStore:
        def open_incident_state(self, *a, **kw):
            return OpenOutcome.SUPPRESSED

        def incident_record(self, *a, **kw):
            return {}

    monkeypatch.setattr(notify, "_abuse_alert_store",
                        lambda: (_SuppressingStore(), ""))
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()
    _flag(notify)

    assert sent == [], sent
    assert ("abuse_flag", "org-a:point_create") not in notify._delivery, (
        "a suppressed kind SPENT the window — lifting the pause would be silent "
        "for the rest of it (#4872)")
    notify.reset_abuse_delivery_for_tests()


def test_the_incident_subject_strips_the_rule_it_concatenates():
    """The emptiness check strips; the CONCATENATION must strip too (#14407 P3).

    ``"point_create "`` passes ``rule.strip()`` truthiness, so an unstripped
    concatenation spells ``{org}:point_create `` — a SECOND subject for one rule,
    i.e. two create-once points, one condition, and a resolve that strands the
    other (the #2844 alias class). ``_abuse_context`` already strips.
    """
    import tortoise.notify as notify

    assert (notify._abuse_subject("org-a", "point_create ")
            == notify._abuse_subject("org-a", "point_create")
            == "org-a:point_create")
    # Whitespace-only is still "no rule": the org alone (operator path).
    assert notify._abuse_subject("org-a", "   ") == "org-a"
    assert notify._abuse_subject("org-a", None) == "org-a"


def test_a_retry_never_resolves_on_an_unreadable_window(monkeypatch):
    """The retry finishes an episode KNOWN to have ended — it never decides one did.

    When `latest_flag_at`/`flag_clear` RAISES the episode state is UNKNOWN, and
    closing on that would push a `✅ DR resolved` for an org that may still be over
    threshold (#5143 / ADR-011's false all-clear). The pending retry must be gated
    on `ended` exactly like the confirmed episode-end arm.
    """
    import tortoise.notify as notify

    resolved: list[tuple[str, str]] = []

    class _Store:
        def resolve_incident_state(self, kind, subject="", **kw):
            resolved.append((kind, subject))
            raise RuntimeError("close failed (#5143)")   # first attempt fails

    class _FlakyStore:
        def __init__(self) -> None:
            self.calls = 0
            self.flagged = True

        def window_sum(self, org_id, rule, window_s, now):
            return 0

        def latest_flag_at(self, org_id, rule):
            self.calls += 1
            if self.calls == 1:
                return datetime.now(timezone.utc)   # noqa: UP017
            raise RuntimeError("supabase read blip")  # episode state UNKNOWN

        def flag_clear(self, org_id, rule, now=None):
            self.flagged = False

    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    engine = AbuseEngine(_FlakyStore())
    clock = [datetime.now(timezone.utc)]  # noqa: UP017
    monkeypatch.setattr("tortoise.abuse._utcnow", lambda: clock[0])

    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])  # raises → pending
    first = len(resolved)
    assert first == 2, resolved

    # Past the retry backoff, so the retry branch ACTUALLY RUNS — otherwise this
    # test is vacuous (the backoff alone would keep `resolved` unchanged).
    clock[0] = clock[0] + timedelta(seconds=3601)
    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])  # read RAISES
    assert len(resolved) == first, (
        "an UNREADABLE window closed the incident — a false all-clear "
        f"({resolved})")


def test_a_resolution_window_stays_pending_until_it_completes(monkeypatch):
    """`ALERT_RECOVERY_STREAK` applies to every kind; abuse must honour it.

    `resolve_incident_state` returns SKIPPED_WINDOW until N consecutive healthy
    observations are counted (#3146). The episode end fires ONCE, so treating that
    outcome as success meant a deployment raising the fleet-wide recovery window
    never resolved an abuse incident at all — defect 3 back through an env var.
    """
    import tortoise.notify as notify
    from tortoise.alert_store import ResolveOutcome

    calls: list[tuple[str, str]] = []
    # 2 resolve calls per evaluation (one per incident kind)
    outcomes = [ResolveOutcome.SKIPPED_WINDOW] * 4 + [ResolveOutcome.RESOLVED] * 8

    class _Store:
        def resolve_incident_state(self, kind, subject="", **kw):
            calls.append((kind, subject))
            return outcomes[min(len(calls) - 1, len(outcomes) - 1)]

    class _FlagOnceStore:
        def __init__(self) -> None:
            self.flagged = True

        def window_sum(self, org_id, rule, window_s, now):
            return 0

        def latest_flag_at(self, org_id, rule):
            return (datetime.now(timezone.utc)  # noqa: UP017
                    if self.flagged else None)

        def flag_clear(self, org_id, rule, now=None):
            self.flagged = False

    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    engine = AbuseEngine(_FlagOnceStore())
    clock = [datetime.now(timezone.utc)]  # noqa: UP017
    monkeypatch.setattr("tortoise.abuse._utcnow", lambda: clock[0])

    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])   # 2 SKIPPED_WINDOW
    after_first = len(calls)
    assert after_first == 2, calls

    clock[0] = clock[0] + timedelta(seconds=3601)
    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])   # 2 more SKIPPED
    assert len(calls) == 4, f"SKIPPED_WINDOW dropped the retry ({calls})"

    clock[0] = clock[0] + timedelta(seconds=3601)
    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])   # RESOLVED now
    assert len(calls) == 6, calls

    clock[0] = clock[0] + timedelta(seconds=3601)
    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])   # completed
    assert len(calls) == 6, f"a completed resolution kept retrying ({calls})"


def test_a_migration_that_cannot_release_the_legacy_object_is_rolled_back(
        monkeypatch):
    """One issue, ONE claimant — a partial migration must leave none behind.

    The legacy object is the adoption SOURCE, so a scoped key created next to it
    lets the NEXT rule migrate the same issue number again: two sentinels for one
    issue, and then one rule's episode end closes the issue the other rule is
    tracked by. The scoped key is therefore deleted when the legacy delete fails.
    """
    from tortoise.alert_store import AlertStore
    from tortoise.hosted_backup import MemoryStorage

    class _Stubborn(MemoryStorage):
        def delete(self, key):
            if key.endswith("/org-a.json"):
                raise RuntimeError("R2 refused the delete")
            return super().delete(key)

    storage = _Stubborn()
    store = AlertStore(
        storage,
        file_issue=lambda title, body: 7,
        close_issue=lambda number, comment=None: None,
        search_open=lambda kind, org: [],
        push_telegram=lambda text: None,
        issue_open=lambda number: True,
        comment_issue=lambda number, body: None,
        repo="daniel-ospina/tortoise",
    )
    store.open_incident_state("abuse_review_needed", "org-a", {"detail": "legacy"})

    moved = store.migrate_incident_subject(
        "abuse_review_needed", "org-a:point_create", "org-a")

    assert moved is False
    assert store.incident_record("abuse_review_needed", "org-a:point_create") == {}, \
        "the rolled-back migration left a second claimant for one issue"
    assert store.incident_record("abuse_review_needed", "org-a").get(
        "issue_number") == 7, "the legacy claimant must survive the rollback"


def test_a_cooldown_refusal_does_not_consume_the_retry_budget(monkeypatch):
    """The store refuses retries inside its own cooldown; that is not an attempt.

    `resolve_incident_state` raises `CloseCooldown` for `close_cooldown_min` after
    a failed close (#5143), while the retry branch fires on EVERY clean-window
    evaluation of the rule (every point create for an org under threshold). A
    call-count cap is therefore spent inside one cooldown, and the entry is
    dropped exactly when the cooldown expires and the retry could have worked.
    """
    import tortoise.notify as notify
    from tortoise.alert_store import CloseCooldown

    calls: list[tuple[str, str]] = []

    class _Store:
        def resolve_incident_state(self, kind, subject="", **kw):
            calls.append((kind, subject))
            if len(calls) == 1:
                raise RuntimeError("close failed (#5143)")
            raise CloseCooldown("backing off for 60 min")

    class _FlagOnceStore:
        def __init__(self) -> None:
            self.flagged = True

        def window_sum(self, org_id, rule, window_s, now):
            return 0

        def latest_flag_at(self, org_id, rule):
            return (datetime.now(timezone.utc)  # noqa: UP017
                    if self.flagged else None)

        def flag_clear(self, org_id, rule, now=None):
            self.flagged = False

    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    engine = AbuseEngine(_FlagOnceStore())
    clock = [datetime.now(timezone.utc)]  # noqa: UP017
    monkeypatch.setattr("tortoise.abuse._utcnow", lambda: clock[0])

    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])  # real failure
    # 24 hourly retries — every one REFUSED by the store's cooldown. A call-count
    # budget would be spent here (the store refuses for `close_cooldown_min`, but
    # the retry fires on every clean evaluation), dropping the entry just as the
    # cooldown expires.
    for _ in range(24):
        clock[0] = clock[0] + timedelta(seconds=3601)
        engine._evaluate("org-a", "point_create", 500, 3600, clock[0])

    assert ("org-a", "point_create") in engine._pending_episode_resolves, (
        "the retry entry was dropped during the store's own cooldown — the "
        "incident is stranded OPEN once the cooldown expires")
    assert len(calls) > 2, f"the retry never actually ran ({calls})"


def test_two_rules_cannot_both_claim_one_migrated_issue():
    """ONE issue, ONE scoped claimant — INTERLEAVED across two store instances.

    The racers are two REPLICAS (`AlertStore` objects sharing one object store), and
    the window that matters is where both are inside the migration at once: rule
    B's migration runs while rule A has created its scoped key but has NOT yet
    released the legacy object. `delete` is idempotent on both substrates, so a
    read-then-delete "claim check" would let both claim — one rule's episode end
    then closes the issue the other rule is tracked by (a false `✅`) and the other
    re-files (#3029/#3031).

    Sequential execution does NOT test this (the second migration would just find
    the legacy object gone), so A's release is held open by a storage double while
    B runs to completion.
    """
    import threading

    from tortoise.alert_store import AlertStore
    from tortoise.hosted_backup import MemoryStorage

    class _PausableStorage(MemoryStorage):
        """Blocks the FIRST legacy release so the second migration interleaves."""

        def __init__(self) -> None:
            super().__init__()
            self.block_release = True
            self.release_started = threading.Event()
            self.release_may_finish = threading.Event()

        def delete(self, key):
            if self.block_release and key.endswith("/org-a.json"):
                self.block_release = False
                self.release_started.set()
                assert self.release_may_finish.wait(timeout=10), "release never freed"
            return super().delete(key)

    storage = _PausableStorage()

    def _store():
        return AlertStore(
            storage,
            file_issue=lambda title, body: 7,
            close_issue=lambda number, comment=None: None,
            search_open=lambda kind, org: [],
            push_telegram=lambda text: None,
            issue_open=lambda number: True,
            comment_issue=lambda number, body: None,
            repo="daniel-ospina/tortoise",
        )

    first, second = _store(), _store()
    first.open_incident_state("abuse_review_needed", "org-a", {"detail": "legacy"})

    results: dict[str, bool] = {}

    def _migrate(store, subject, name):
        results[name] = store.migrate_incident_subject(
            "abuse_review_needed", subject, "org-a")

    winner = threading.Thread(
        target=_migrate, args=(first, "org-a:point_create", "A"))
    winner.start()
    assert storage.release_started.wait(timeout=10), \
        "the winning migrator never reached its legacy release"

    # ...and B migrates from the SAME legacy object while A holds it mid-flight.
    _migrate(second, "org-a:key_create", "B")

    storage.release_may_finish.set()
    winner.join(timeout=10)

    claims = [subj for subj in ("org-a:point_create", "org-a:key_create")
              if first.incident_record("abuse_review_needed", subj)]
    assert results.get("A") is True, results
    assert results.get("B") is False, f"both replicas claimed one issue: {results}"
    assert claims == ["org-a:point_create"], (
        f"two scoped sentinels claim one issue: {claims}")


def test_a_created_claim_is_released_when_the_scoped_key_already_exists():
    """A claim left behind permanently blocks migrating that issue ever again.

    The "another writer already owns the scoped key" branch used to return without
    deleting the claim, so a later attempt found its own stale claim and refused
    for ever — leaving the legacy sentinel (and its OPEN issue) stranded next to a
    duplicate filed for one condition: a permanent #2844 alias.
    """
    from tortoise.alert_store import AlertStore
    from tortoise.hosted_backup import MemoryStorage

    class _Racing(MemoryStorage):
        """Loses the scoped create to a real writer that got there first."""

        def __init__(self) -> None:
            super().__init__()
            self.lose_next_create = False

        def create_if_not_exists(self, key, payload):
            if self.lose_next_create and key.endswith("/org-a:point_create.json"):
                self.lose_next_create = False
                super().create_if_not_exists(key, b'{"kind": "other-writer"}')
                return False
            return super().create_if_not_exists(key, payload)

    storage = _Racing()
    store = AlertStore(
        storage,
        file_issue=lambda title, body: 7,
        close_issue=lambda number, comment=None: None,
        search_open=lambda kind, org: [],
        push_telegram=lambda text: None,
        issue_open=lambda number: True,
        comment_issue=lambda number, body: None,
        repo="daniel-ospina/tortoise",
    )
    store.open_incident_state("abuse_review_needed", "org-a", {"detail": "legacy"})

    storage.lose_next_create = True
    assert store.migrate_incident_subject(
        "abuse_review_needed", "org-a:point_create", "org-a") is False

    # the competing scoped key goes away (its own episode end closes it)...
    storage.delete("ops/alerts/abuse_review_needed/org-a:point_create.json")

    # ...and the migration must be able to RUN AGAIN, i.e. the claim is gone.
    assert store.migrate_incident_subject(
        "abuse_review_needed", "org-a:point_create", "org-a") is True, (
        "the claim object outlived its own migration — this issue can never be "
        "migrated again, so its legacy sentinel is stranded")


def test_a_migration_that_fails_mid_flight_leaves_one_claimant():
    """An exception between the create and the release must ROLL BACK.

    `_read_json`/`delete` can raise on a transport error; a bare `create` followed
    by an unguarded read leaves the scoped sentinel AND the legacy object behind —
    two claimants for one issue.
    """
    from tortoise.alert_store import AlertStore
    from tortoise.hosted_backup import MemoryStorage

    class _FlakyRead(MemoryStorage):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next_read = False

        def download(self, key):
            if self.fail_next_read and key.endswith("/org-a.json"):
                self.fail_next_read = False
                raise RuntimeError("R2 transport error")
            return super().download(key)

    storage = _FlakyRead()
    store = AlertStore(
        storage,
        file_issue=lambda title, body: 7,
        close_issue=lambda number, comment=None: None,
        search_open=lambda kind, org: [],
        push_telegram=lambda text: None,
        issue_open=lambda number: True,
        comment_issue=lambda number, body: None,
        repo="daniel-ospina/tortoise",
    )
    store.open_incident_state("abuse_review_needed", "org-a", {"detail": "legacy"})

    # the claim read succeeds, the release raises
    storage.fail_next_read = False
    orig_delete = storage.delete

    def _bad_delete(key):
        if key.endswith("/org-a.json"):
            raise RuntimeError("R2 refused the delete")
        return orig_delete(key)

    storage.delete = _bad_delete
    moved = store.migrate_incident_subject(
        "abuse_review_needed", "org-a:point_create", "org-a")

    assert moved is False
    assert store.incident_record("abuse_review_needed", "org-a:point_create") == {}, \
        "a failed migration left a second claimant behind"
    assert store.incident_record("abuse_review_needed", "org-a").get(
        "issue_number") == 7


def test_a_completed_migration_releases_its_claim_too():
    """A claim that outlives a SUCCESSFUL migration blocks that issue for ever.

    The claim object (`ops/alerts/.migrating/<kind>/<issue>.json`) has no expiry,
    so releasing it only on the two FAILURE branches leaked one object per
    migrated issue — and, worse, made that issue number unmigratable: a second
    attempt finds its own stale claim and refuses, which strands the legacy
    sentinel next to a duplicate filed for one condition (the permanent #2844
    alias the sibling branches were fixed to avoid). Reachable the moment the
    legacy object reappears, because an R2/DR restore brings back the
    pre-migration `{org}.json` beside the scoped key.
    """
    storage = MemoryStorage()
    store = AlertStore(
        storage,
        file_issue=lambda title, body: 7,
        close_issue=lambda number, comment=None: None,
        search_open=lambda kind, org: [],
        push_telegram=lambda text: None,
        issue_open=lambda number: True,
        comment_issue=lambda number, body: None,
        repo="daniel-ospina/tortoise",
    )
    store.open_incident_state("abuse_review_needed", "org-a", {"detail": "legacy"})

    assert store.migrate_incident_subject(
        "abuse_review_needed", "org-a:point_create", "org-a") is True
    assert storage.list("ops/alerts/.migrating/") == [], (
        "the claim object outlived its own COMPLETED migration — one leaked "
        f"object per migrated issue: {storage.list('ops/alerts/.migrating/')}")

    # A restore brings the legacy object back beside the scoped key; the scoped key
    # then goes away (its own episode end closes + deletes it, `resolve_incident`).
    # The issue must be migratable AGAIN — a leaked claim makes that impossible,
    # which is the "permanent #2844 alias" these branches exist to prevent. This is
    # the DISCRIMINATING assertion: on the pre-fix code the claim from the first
    # (successful) migration is still `create_if_not_exists`-owned, so this call
    # returns False and the legacy sentinel stays stranded.
    storage.upload("ops/alerts/abuse_review_needed/org-a.json", b'{"issue_number": 7}')
    storage.delete("ops/alerts/abuse_review_needed/org-a:point_create.json")
    assert store.migrate_incident_subject(
        "abuse_review_needed", "org-a:point_create", "org-a") is True, (
        "the issue could not be migrated a second time — its claim never left")
    assert storage.list("ops/alerts/.migrating/") == [], (
        "the second migration left a claim behind: "
        f"{storage.list('ops/alerts/.migrating/')}")
    assert store.incident_record("abuse_review_needed", "org-a:point_create").get(
        "issue_number") == 7, "the scoped sentinel must still be the live one"


def test_an_unreadable_issue_number_does_not_arm_a_silent_window(monkeypatch):
    """FILED + a failing read-back must DEFER, not spend the window in silence.

    If the store filed WITHOUT a number (deferred filing — the path this guard
    exists for) it pushes nothing, and suppressing the unattributed message while
    leaving the window armed would be silent for 24h: "silence is an outcome",
    which #4872 forbids.
    """
    import tortoise.notify as notify
    from tortoise.alert_store import OpenOutcome

    sent: list[str] = []

    class _Store:
        def open_incident_state(self, *a, **kw):
            return OpenOutcome.FILED

        def incident_record(self, *a, **kw):
            raise RuntimeError("r2 blip")

    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()

    notify.notify_abuse("abuse_flag", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 900})

    assert sent == [], "the unattributed text would assert an absence we cannot see"
    assert notify._delivery == {}, (
        "the window was armed with nothing sent — a silent 24h for the class")


def test_a_suppressed_outcome_does_not_spend_the_delivery_window(monkeypatch):
    """An operator suppression must not become a mute when it is lifted.

    The delivery window is armed BEFORE the outcome is classified, and
    `SUPPRESSED` sends nothing — so leaving the window armed means the first
    detection after the suppression is lifted is swallowed for the rest of the
    window (up to 24h): the "#4872: never a mute" direction.

    A future `OpenOutcome` member is a DIFFERENT case, asserted separately in part
    3: treating it like `SUPPRESSED` would be a total mute — while the store keeps
    answering with the unclassified fact, every detection takes the same arm and
    nothing is ever delivered, which is the outcome the review's fall-through rule
    exists to forbid. `SUPPRESSED` is silent because a HUMAN paused the kind; an
    unknown fact carries no such decision, so it pages (bounded, labelled UNCERTAIN).
    """
    import tortoise.notify as notify
    from tortoise.alert_store import OpenOutcome

    sent: list[str] = []

    class _Store:
        def __init__(self, outcome):
            self.outcome = outcome

        def open_incident_state(self, *a, **kw):
            return self.outcome

        def incident_record(self, *a, **kw):
            return {"issue_number": 11}

    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()

    # 1. an operator has paused the kind
    notify.reset_abuse_delivery_for_tests()
    monkeypatch.setattr(notify, "_abuse_alert_store",
                        lambda: (_Store(OpenOutcome.SUPPRESSED), ""))
    for _ in range(3):
        notify.notify_abuse("abuse_flag", {"org_id": "org-a"},
                            {"rule": "point_create", "count": 900})

    assert sent == [], "a suppressed kind must not be sent"
    assert notify._delivery == {}, (
        "the window was spent while suppressed — lifting the suppression would be "
        "silent for the rest of it")

    # 2. suppression lifted: the first detection must DELIVER immediately
    monkeypatch.setattr(notify, "_abuse_alert_store",
                        lambda: (_Store(OpenOutcome.DEDUP), ""))
    notify.notify_abuse("abuse_flag", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 900})
    assert len(sent) == 1, f"the first post-suppression detection was swallowed ({sent})"

    # 3. a future outcome member that is neither DEDUP nor FILED is NOT a pause:
    # it carries no human decision, so it must PAGE (bounded), labelled UNCERTAIN
    # — silence here is the mute #4872 forbids, and releasing the window does not
    # help because the next detection takes the same arm.
    notify.reset_abuse_delivery_for_tests()
    sent.clear()

    # an outcome value that is neither member (what a new `OpenOutcome` entry, or a
    # store double that returns something unexpected, looks like here)
    monkeypatch.setattr(notify, "_abuse_alert_store",
                        lambda: (_Store("deferred-not-yet-classified"), ""))
    for _ in range(3):
        notify.notify_abuse("abuse_flag", {"org_id": "org-a"},
                            {"rule": "point_create", "count": 900})
    # ONE message for the window — bounded exactly like every other incident
    # delivery — and it names the uncertainty instead of asserting an absence.
    assert len(sent) == 1, sent
    assert "not classify" in sent[0], sent[0]
    assert "UNKNOWN" in sent[0], sent[0]
    notify.reset_abuse_delivery_for_tests()


def test_an_anonymous_digest_does_not_claim_a_dashboard_row(
        delivered, monkeypatch):
    """The dashboard row is written only when the detection carried an org.

    An anonymous subject is mapped to ``"_"``, and its folded detections are
    recorded NOWHERE — so the digest must not assert that they are. Asserting an
    attribution that does not exist is the defect class this change removes.
    """
    import tortoise.notify as notify

    for _ in range(3):
        notify.notify_abuse("abuse_new_ip", {}, {"country": "ES"})

    assert len(delivered.pushes) == 1
    message = delivered.pushes[0]
    assert "dashboard" in message
    assert "NO dashboard row was written" in message, message
    assert "the rows are on the dashboard" not in message, message


def test_a_scoped_detection_digest_still_names_the_dashboard_row(delivered):
    """The POSITIVE arm: a detection WITH an org does write a dashboard row.

    Without this, a mutant that always emitted the anonymous sentence would stay
    green — the conditional would be pinned on one side only.

    ⛔ It asserts that a row is CLAIMED, and that the claim is caveated as
    best-effort — NOT which register the row lands in (round 3, P2). Naming the
    activity register here was a FALSE statement for this very class:
    `abuse.ALERT_REGISTER_OF[EVENT_AUTH_IP]` is `alert` ("a credential event:
    new-country auth"), so the digest sent a human to the wrong list — and every
    `record_*` write is debug-swallowed, so an unqualified promise can also be
    false on a control-plane outage.
    """
    import tortoise.notify as notify

    notify.notify_abuse("abuse_new_ip", {"org_id": "org-a"}, {"country": "ES"})

    assert len(delivered.pushes) == 1
    assert "the rows are on the dashboard" in delivered.pushes[0], \
        delivered.pushes[0]
    assert "best-effort" in delivered.pushes[0], delivered.pushes[0]
    assert "activity register" not in delivered.pushes[0], (
        "the digest named a register: for an R4 new-country auth event the row is "
        "on the ALERT register, so naming the activity one points a human at the "
        "wrong list")


def test_a_folded_read_velocity_digest_names_the_breaching_key(delivered,
                                                              monkeypatch):
    """R3 carries `id` (the key), NOT `ip` — and its folded sources must be named.

    Round 3, P2: `identity = details["ip"]` left the HIGHEST-volume class (R3
    re-arms every 5 min, 288 detections/day) folding with no source line at all,
    while the module docstring promises the folded sources are named. The sibling
    above only drove `abuse_signup_velocity`, which does carry an IP, so the
    contract was pinned on the one class that happens to satisfy it.
    """
    import tortoise.notify as notify

    now = [1_000_000.0]
    monkeypatch.setattr(notify, "_delivery_now", lambda: now[0])
    for key in ("k1", "k2", "k3"):
        notify.notify_abuse(
            "abuse_read_velocity", {"org_id": "org-a"},
            {"scope": "key", "id": key, "threshold": 100, "window_s": 300},
        )
    assert len(delivered.pushes) == 1, delivered.pushes

    now[0] += notify._ACTIVITY_DELIVERY_WINDOW_S + 1
    notify.notify_abuse(
        "abuse_read_velocity", {"org_id": "org-a"},
        {"scope": "key", "id": "k4", "threshold": 100, "window_s": 300},
    )

    assert len(delivered.pushes) == 2, delivered.pushes
    digest = delivered.pushes[-1]
    assert "Folded sources:" in digest, digest
    for key in ("k2", "k3"):
        assert key in digest, (key, digest)


def test_activity_detections_collapse_into_one_message(delivered):
    """Pre-fix this sent 100 messages — the R3 rule is 1 per 5-min window."""
    import tortoise.notify as notify

    for _ in range(100):
        notify.notify_abuse(
            "abuse_read_velocity", {"org_id": "org-a"},
            {"scope": "key", "id": "k1", "threshold": 100, "window_s": 300},
        )

    assert len(delivered.pushes) == 1, delivered.pushes
    assert delivered.filed == [], "a rate measurement must never file an incident"
    message = delivered.pushes[0]
    assert "activity" in message.lower()
    assert "#10156" in message, "the non-filing decision must be stated, not implied"
    assert "nothing was disabled" in message.lower()


def test_the_digest_reports_how_many_it_folded(delivered, monkeypatch):
    import tortoise.notify as notify

    clock = [1_000.0]
    monkeypatch.setattr(notify, "_delivery_now", lambda: clock[0])
    for _ in range(3):
        notify.notify_abuse("abuse_new_ip", {"org_id": "org-a"}, {"country": "ES"})
    assert len(delivered.pushes) == 1

    clock[0] += notify._ACTIVITY_DELIVERY_WINDOW_S + 1.0
    notify.notify_abuse("abuse_new_ip", {"org_id": "org-a"}, {"country": "ES"})

    assert len(delivered.pushes) == 2
    assert "2 further" in delivered.pushes[-1], delivered.pushes[-1]


def test_a_folded_digest_with_an_org_says_its_detections_are_recorded(
        delivered, monkeypatch):
    """The FOLDED sentence's positive arm: a scoped detection DOES write a row.

    ``_abuse_message`` states, for the folded detections, whether they are on the
    dashboard alert list — and the caller only writes a row when the detection
    carried an org. Without this arm a mutant that always emitted the anonymous
    wording would stay green (the conditional would be pinned on one side only,
    which is exactly how the sibling activity sentence was left half-covered).
    """
    import tortoise.notify as notify

    clock = [1_000.0]
    monkeypatch.setattr(notify, "_delivery_now", lambda: clock[0])
    for _ in range(3):
        notify.notify_abuse("abuse_new_ip", {"org_id": "org-a"}, {"country": "ES"})
    assert len(delivered.pushes) == 1

    clock[0] += notify._ACTIVITY_DELIVERY_WINDOW_S + 1.0
    notify.notify_abuse("abuse_new_ip", {"org_id": "org-a"}, {"country": "ES"})

    assert len(delivered.pushes) == 2, delivered.pushes
    folded_line = delivered.pushes[-1]
    assert "2 further" in folded_line, folded_line
    assert "all of them are recorded on the dashboard alert list" in folded_line, \
        folded_line
    # Round 2, P2: the sentence may not state the dashboard row as a guarantee —
    # every `record_*` caller is best-effort (`abuse.py` logs the failure and
    # continues), so on a control-plane outage the claim would be false exactly
    # when it matters. The caveat is pinned, not just present in the prose.
    assert "best-effort" in folded_line, folded_line
    assert "they are NOT recorded on the dashboard" not in folded_line, folded_line


def test_a_folded_anonymous_digest_says_its_detections_are_not_recorded(
        delivered, monkeypatch):
    """The FOLDED sentence's negative arm: no org ⇒ no row, and it must say so.

    The dashboard write is guarded by ``if org_id``, and ``notify_abuse`` maps a
    missing one to ``"_"``. Telling an anonymous subject that its folded
    detections "are recorded on the dashboard alert list" would assert an
    attribution that does not exist — the defect class #14407 removes.
    """
    import tortoise.notify as notify

    clock = [1_000.0]
    monkeypatch.setattr(notify, "_delivery_now", lambda: clock[0])
    for _ in range(3):
        notify.notify_abuse("abuse_new_ip", {}, {"country": "ES"})
    assert len(delivered.pushes) == 1

    clock[0] += notify._ACTIVITY_DELIVERY_WINDOW_S + 1.0
    notify.notify_abuse("abuse_new_ip", {}, {"country": "ES"})

    assert len(delivered.pushes) == 2, delivered.pushes
    folded_line = delivered.pushes[-1]
    assert "2 further" in folded_line, folded_line
    assert "they are NOT recorded on the dashboard" in folded_line, folded_line
    assert "all of them are recorded on the dashboard alert list" not in folded_line, \
        folded_line


# ── 4. no incident channel ⇒ still deliver, and SAY why ─────────────────────

def test_a_missing_incident_channel_delivers_anyway_and_names_the_reason(
        monkeypatch, caplog):
    """Prod's alert env carries no ``DR_ISSUES_PAT``, so the store cannot be
    built. The pre-fix code sent 20 unattributable messages; a fix that muted
    would send 0 — the honest answer is ONE, labelled."""
    import tortoise.notify as notify

    sent: list[str] = []
    monkeypatch.setattr(notify, "telegram_send", lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (
        None, "no alert channel configured (DR_ISSUES_PAT unset)"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()

    with caplog.at_level("WARNING"):
        for _ in range(20):
            notify.notify_abuse("abuse_review_needed", {"org_id": "org-a"},
                                {"rule": "point_create", "count": 900})

    assert len(sent) == 1, sent
    assert "DR_ISSUES_PAT" in sent[0], f"unattributable without a reason: {sent[0]!r}"
    assert "no incident" in sent[0].lower()
    assert any("DR_ISSUES_PAT" in r.message or "incident" in r.message.lower()
               for r in caplog.records), "an unattributable alert must be visible in logs"
    notify.reset_abuse_delivery_for_tests()


# ── 5. the engine owns the episode, so it must end it ───────────────────────

def test_episode_end_resolves_the_abuse_incidents(delivered, monkeypatch):
    """Pre-fix nothing ever resolved an abuse incident: the sentinel and its
    issue stayed open for the rest of time, which is why no incident search can
    attribute a month-old recurrence (#9944's shape).

    #3146: the episode end is the drain of the condition, not the whole recovery
    window — ONE clean observation must not close an incident for a rule that
    hovers at its threshold, so the close lands on observation
    ``DR_RECOVERY_STREAK`` (the engine's pending retry supplies the second, ~1h
    later).
    """
    import tortoise.notify as notify
    from tortoise.abuse import _EPISODE_RESOLVE_RETRY_BACKOFF_S

    class _CleanWindowStore:
        """A clean window with a flag on record, and a DURABLE clear (like R2)."""

        def __init__(self) -> None:
            self.cleared: list[tuple[str, str]] = []
            self.flagged = True

        def window_sum(self, org_id, rule, window_s, now):
            return 0

        def latest_flag_at(self, org_id, rule):
            return (datetime.now(timezone.utc)  # noqa: UP017
                    if self.flagged else None)

        def flag_clear(self, org_id, rule, now=None):
            self.cleared.append((org_id, rule))
            self.flagged = False

    clock = [datetime.now(timezone.utc)]  # noqa: UP017
    monkeypatch.setattr("tortoise.abuse._utcnow", lambda: clock[0])
    engine = AbuseEngine(_CleanWindowStore())
    _flag(notify)
    assert delivered.filed, "the alert must have filed its incident first"

    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])
    assert delivered.closed == [], (
        "one clean window closed the incident — the #3146 recovery window is "
        f"not applied to the abuse kinds ({delivered.closed})")

    # Observation 2, past the retry backoff so the engine's pending resolve runs.
    clock[0] = clock[0] + timedelta(seconds=_EPISODE_RESOLVE_RETRY_BACKOFF_S + 1)
    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])

    assert delivered.closed == [1], "the episode-end close is the recovery path"
    titles = [t for t, _ in delivered.filed]
    assert len(titles) == 1, "resolving must not re-file"
    # ⛔ THE THIRD STRING, pinned (#14407 review cycle 7). The episode's lifecycle emits
    # THREE messages, each with its own header: the store's `🚨 DR alert:` open, this
    # reminder's `🚨 Tortoise Security:`, and the store's `✅ DR resolved:` close below.
    # The close contains NEITHER alert label, so a triager who grepped both of those
    # would conclude the incident never resolved; the runbook row names all three and
    # says to grep the KIND. Pinned so a template change on this arm is visible.
    resolved_pushes = [p for p in delivered.pushes if "DR resolved" in p]
    assert resolved_pushes, (
        "the episode-end close pushed no message — the resolution is then invisible "
        f"in the chat: {delivered.pushes}")
    assert resolved_pushes[0].startswith("✅ DR resolved: ABUSE_FLAG_RAISED"), (
        f"the close's header changed: {resolved_pushes[0][:70]!r}")


def test_a_clean_window_with_no_episode_does_no_store_work(monkeypatch):
    """A clean window on an org that was NEVER flagged is not an episode end.

    Pre-fix `ended` meant "the clean-window read did not raise", so every clean
    evaluation — the overwhelmingly common case, twice per point create — ran the
    episode-end resolution: 2 rules × 2 incident kinds × ONE rule-scoped subject
    (the bare-spelling arm is gone since `439db940711c`) = up to 4
    `resolve_incident_state` calls, each one an R2 `get_object` round
    trip through `_read_json`, on the 4-worker abuse pool. The pre-#14407 path
    spent zero network calls here.
    """
    import tortoise.notify as notify

    resolved: list[tuple[str, str]] = []

    class _Store:
        def resolve_incident_state(self, kind, subject="", **kw):
            resolved.append((kind, subject))

    class _NoEpisodeStore:
        def __init__(self) -> None:
            self.cleared: list[tuple[str, str]] = []

        def window_sum(self, org_id, rule, window_s, now):
            return 0

        def latest_flag_at(self, org_id, rule):
            return None                     # no flag ⇒ no episode to end

        def flag_clear(self, org_id, rule, now=None):
            self.cleared.append((org_id, rule))

    store = _NoEpisodeStore()
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    engine = AbuseEngine(store)

    engine._evaluate("org-a", "point_create", 500, 3600,
                     datetime.now(timezone.utc))  # noqa: UP017

    assert store.cleared == [], "there is no flag to clear"
    assert resolved == [], (
        "a clean window with no episode must not touch the incident store — "
        f"{len(resolved)} resolve call(s) on the request path ({resolved})")


def test_an_out_of_band_clear_releases_the_alert_claim():
    """An episode cleared OUT OF BAND must not mute a re-breach inside its window.

    `abuse_unsuspend` (and a manual `flag_clear`) writes the clear with no
    in-process caller, so the NEXT clean window reads `flagged=False`. Gating the
    in-process claim release on `flagged` therefore kept the claim for the rest of
    the original window — 86400 s for R2 — and a new detection inside it was
    dropped at `_claim_notify`, never reaching `notify_abuse` at all: not a folded
    digest, not even the one reminder the delivery ledger promises at the window's
    end. `_release_notify` is a dict `pop` under a lock with no I/O, so the
    R2-cost motive for the `flagged` gate never applied to it, and
    `_release_notify`'s own docstring states the behaviour this restores.
    """
    from tortoise.abuse import EVENT_FLAG, EVENT_REVIEW

    class _ClearedOutOfBand:
        """Under threshold, and the flag is already gone (cleared elsewhere)."""

        def window_sum(self, org_id, rule, window_s, now):
            return 0

        def latest_flag_at(self, org_id, rule):
            return None

        def flag_clear(self, org_id, rule, now=None):
            raise AssertionError(
                "no flag to clear — a clean window on a cleared rule must not "
                "write one")

    now = datetime.now(timezone.utc)  # noqa: UP017
    engine = AbuseEngine(_ClearedOutOfBand())
    # the earlier episode's claims are still armed (a 24 h R2 window)
    engine._last_notified[("org-a", "point_create", EVENT_FLAG)] = now + timedelta(hours=24)
    engine._last_notified[("org-a", "point_create", EVENT_REVIEW)] = now + timedelta(hours=24)

    engine._evaluate("org-a", "point_create", 500, 3600, now)

    assert ("org-a", "point_create", EVENT_FLAG) not in engine._last_notified, (
        "the flag claim survived a clean window, so a re-breach inside the "
        "original 24 h window is dropped at `_claim_notify` and never reaches "
        "`notify_abuse` — a muted security class (#4872)")
    assert ("org-a", "point_create", EVENT_REVIEW) not in engine._last_notified


def test_an_incident_that_cannot_name_an_issue_still_reaches_the_human(
        monkeypatch):
    """An incident OBJECT that carries no issue number is not a delivered alert.

    When the open-issue SEARCH raises, ``_become_filer`` DEFERS: the state is
    written, ``issue_number`` is left unset, and the store's own push is guarded
    on holding a number — so nothing reaches the human. Pre-fix the Telegram leg
    fired independently, so silence here is a REGRESSION, and it lands exactly
    when filing fails hardest (a search rate limit exhausted by the alert burst
    this change bounds). Unattributable is allowed; silent is not (#4872).
    """
    import tortoise.notify as notify

    def _search_raises(kind, org):
        raise RuntimeError("GitHub search quota exhausted")

    store = _RecordingStore(search_open=_search_raises)
    sent: list[str] = []
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (store, ""))
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()

    for _ in range(20):
        _flag(notify)

    assert store.pushes == [], "the store cannot push without an issue number"
    assert len(sent) == 1, (
        f"the class was MUTED while filing was deferred ({len(sent)} message(s))")
    assert "Not attributable" in sent[0], sent[0]
    assert "issue number" in sent[0], sent[0]

    notify.reset_abuse_delivery_for_tests()


def test_a_failed_episode_end_close_is_retried_on_the_next_clean_window(
        monkeypatch):
    """The episode end fires ONCE, so a close that fails must not be lost.

    The `flagged` gate means no later clean window for that org ever sees
    `flagged=True` again — the flag is gone — so a single transient close failure
    (a failed `_close` re-raises by design, #5143; an R2 transport error
    propagates) would strand the incident OPEN forever. The engine keeps a
    (org, rule) retry hint: a set lookup on the clean path, never a store read.
    """
    import tortoise.notify as notify

    calls: list[tuple[str, str]] = []
    fail_first = {"n": 2}          # the first episode-end resolve fails entirely

    class _Store:
        def resolve_incident_state(self, kind, subject="", **kw):
            calls.append((kind, subject))
            if fail_first["n"] > 0:
                fail_first["n"] -= 1
                raise RuntimeError("close failed (#5143)")

    class _FlagOnceStore:
        def __init__(self) -> None:
            self.flagged = True
            self.cleared: list[tuple[str, str]] = []

        def window_sum(self, org_id, rule, window_s, now):
            return 0

        def latest_flag_at(self, org_id, rule):
            return (datetime.now(timezone.utc)  # noqa: UP017
                    if self.flagged else None)

        def flag_clear(self, org_id, rule, now=None):
            self.cleared.append((org_id, rule))
            self.flagged = False

    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    engine = AbuseEngine(_FlagOnceStore())
    clock = [datetime.now(timezone.utc)]  # noqa: UP017
    monkeypatch.setattr("tortoise.abuse._utcnow", lambda: clock[0])

    def _advance(seconds):
        clock[0] = clock[0] + timedelta(seconds=seconds)

    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])
    first = len(calls)
    assert first == 2, f"one resolve per incident kind, got {calls}"   # both RAISE

    # The retry is spaced by the store's own close-cooldown (#14407 round 5), so a
    # clean window inside that backoff costs no store read at all...
    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])
    assert len(calls) == first, (
        f"the retry ran inside its backoff, paying a store read ({calls})")

    # ...and once the backoff has elapsed it retries.
    _advance(3601)
    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])
    assert len(calls) == first * 2, (
        "the failed episode-end close was never retried — the incident stays "
        f"OPEN forever ({calls})")

    # ...and once it succeeds the retry is dropped: no further store work.
    _advance(3601)
    engine._evaluate("org-a", "point_create", 500, 3600, clock[0])
    assert len(calls) == first * 2, f"a resolved episode kept retrying ({calls})"


def test_an_unresolvable_close_never_breaks_the_request_path(monkeypatch):
    """Best-effort on the request path: a close failure is a log line, not a 500."""
    import tortoise.notify as notify

    class _Store:
        def resolve_incident_state(self, *a, **kw):
            raise RuntimeError("github down")

    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    notify.resolve_abuse_episode("org-a", "point_create")  # must not raise


def test_a_failing_incident_read_never_raises_from_the_alert_path(monkeypatch):
    """``notify_abuse`` documents NEVER raising, and the re-alarm reads the
    incident — that read must be guarded too, or a DEDUP outcome on an R2 blip
    would propagate into the request path."""
    import tortoise.notify as notify
    from tortoise.alert_store import OpenOutcome

    sent: list[str] = []
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))

    class _Store:
        def open_incident_state(self, *a, **kw):
            return OpenOutcome.DEDUP

        def incident_record(self, *a, **kw):
            raise RuntimeError("r2 blip")

    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()

    notify.notify_abuse("abuse_review_needed", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 900})

    assert len(sent) == 1
    # The message must state the UNCERTAINTY, not an absence: a read that failed
    # cannot establish that the incident has no issue number (#14407 round 4) —
    # asserting one would be the same unattributable-attribution defect this change
    # removes, on the arm where the read is the thing that broke.
    assert "could not be read" in sent[0], sent[0]
    assert "no issue number on record" not in sent[0], sent[0]
    notify.reset_abuse_delivery_for_tests()


# ── 5b. round-5 review: the seams the fixes themselves created ───────────────

def test_a_blocked_signup_burst_names_the_clients_it_refused(delivered, monkeypatch):
    """A digest that folds a distributed burst must not also discard WHO.

    The 429 path calls `record_signup_block(ip)` with no org, so `notify_abuse`
    maps the subject to "_" and every refused client in a window shares ONE
    digest key. Pre-fix each block sent its own message carrying its IP; a bound
    that collapses them into a single anonymous message would delete the security
    record rather than bound it (#14407 round 5).
    """
    import tortoise.notify as notify

    now = [1_000_000.0]
    monkeypatch.setattr(notify, "_delivery_now", lambda: now[0])
    ips = ["203.0.113.7", "203.0.113.8", "203.0.113.9"]
    for ip in ips:  # one window: the first is delivered, the other two fold
        notify.notify_abuse("abuse_signup_velocity", {"org_id": None},
                            {"ip": ip, "count": 0})
    assert len(delivered.pushes) == 1, delivered.pushes

    # The NEXT window's delivery is where the folded window is accounted for.
    now[0] += notify._ACTIVITY_DELIVERY_WINDOW_S + 1
    notify.notify_abuse("abuse_signup_velocity", {"org_id": None},
                        {"ip": "203.0.113.10", "count": 0})

    assert len(delivered.pushes) == 2, delivered.pushes
    digest = delivered.pushes[-1]
    for ip in ips[1:]:
        assert ip in digest, (ip, digest)
    assert "Folded sources:" in digest, digest


def test_an_incident_that_raised_after_filing_is_not_called_absent(
        delivered, monkeypatch):
    """A raise after the issue exists must not produce 'no incident was filed'.

    `open_incident_state` can raise AFTER it created + announced the issue (a
    trailing write, the pending-push bookkeeping, the DEDUP health-streak write
    that deliberately propagates). Telling the human "no incident was filed"
    while issue #N is open would be a false statement and a second page about the
    same condition (#14407 round 5).
    """
    import tortoise.notify as notify

    class _Store:
        def open_incident_state(self, kind, subject="", detail=None, writer=None):
            raise RuntimeError("trailing write failed after filing")

        def incident_record(self, kind, subject=""):
            return {"issue_number": 4242}

    sent: list[str] = []
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()

    notify.notify_abuse("abuse_review_needed", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 900})

    assert len(sent) == 1, sent
    assert "#4242" in sent[0], sent[0]
    assert "no incident was filed" not in sent[0], sent[0]
    notify.reset_abuse_delivery_for_tests()


def test_a_read_back_that_also_fails_is_not_called_absent(monkeypatch):
    """A failed WRITE and a failed READ must not add up to "no incident was filed".

    `open_incident_state` can raise AFTER it filed and pushed, and the two failures
    are CORRELATED — an R2 outage fails the trailing write first, then the read-back
    that would have found the issue. The pre-fix fall-through rendered
    "no incident was filed" in exactly that state, which is a false statement to a
    human about an open incident: the same class this change removes, one level
    deeper than `number_known` covers. Whether an incident is on record is UNKNOWN,
    and the message must say so.
    """
    import tortoise.notify as notify

    class _Store:
        def open_incident_state(self, kind, subject="", detail=None, writer=None):
            raise RuntimeError("trailing write failed after filing")

        def incident_record(self, kind, subject=""):
            raise RuntimeError("R2 read failed")

    sent: list[str] = []
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()

    notify.notify_abuse("abuse_review_needed", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 900})

    assert len(sent) == 1, sent
    assert "no incident was filed" not in sent[0], sent[0]
    assert "UNKNOWN" in sent[0], sent[0]
    notify.reset_abuse_delivery_for_tests()


def test_an_established_empty_record_still_says_nothing_was_filed(monkeypatch):
    """The boundary the UNKNOWN framing must not swallow (#14407 review cycle 3).

    "cannot establish" and "nothing was filed" are two different facts, and the
    whole point of the `read_failed`/`record` split is to keep them apart. A
    SUCCESSFUL read that returns an empty record IS evidence of absence, and the
    message must still say `no incident was filed` — otherwise a future widening of
    the uncertain arm turns every unclassifiable failure into permanent
    "UNKNOWN" framing and the reassuring case is lost its voice.
    """
    import tortoise.notify as notify

    class _Store:
        def open_incident_state(self, kind, subject="", detail=None, writer=None):
            raise RuntimeError("filing raised before anything was written")

        def incident_record(self, kind, subject=""):
            return {}                       # a SUCCESSFUL read of an empty record

    sent: list[str] = []
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()

    notify.notify_abuse("abuse_review_needed", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 900})

    assert len(sent) == 1, sent
    assert "no incident was filed" in sent[0], sent[0]
    assert "UNKNOWN" not in sent[0], sent[0]
    notify.reset_abuse_delivery_for_tests()


def test_a_numberless_record_is_named_as_existing_not_unknown(monkeypatch):
    """The fourth shape: a record EXISTS with no `issue_number` (review cycle 4).

    `AlertStore` writes a number-less sentinel for three different reasons — the
    open-issue search raised (filing deferred), `_file` raised (filing failed), and a
    successful CLOSE whose object delete failed (a tombstone). All three are ON
    record, so the message must say the record exists and why it cannot be named;
    it must not borrow either neighbouring sentence — "UNKNOWN" (which belongs to a
    read that raised) or "no incident was filed" (which belongs to an EMPTY read).
    Without this pin the arm can be collapsed back into the self-refuting UNKNOWN
    wording with every other test still green.
    """
    import tortoise.notify as notify

    class _Store:
        def open_incident_state(self, kind, subject="", detail=None, writer=None):
            raise RuntimeError("trailing write failed after filing")

        def incident_record(self, kind, subject=""):
            return {"issue_number": None, "resolve_failed_at": "2026-11-23T00:00:00Z"}

    sent: list[str] = []
    monkeypatch.setattr(notify, "_abuse_alert_store", lambda: (_Store(), ""))
    monkeypatch.setattr(notify, "telegram_send",
                        lambda bot, chat, text, **k: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    notify.reset_abuse_delivery_for_tests()

    notify.notify_abuse("abuse_review_needed", {"org_id": "org-a"},
                        {"rule": "point_create", "count": 900})

    assert len(sent) == 1, sent
    assert "an incident record EXISTS" in sent[0], sent[0]
    assert "UNKNOWN" not in sent[0], sent[0]
    assert "no incident was filed" not in sent[0], sent[0]
    assert "leftover sentinel" in sent[0], sent[0]
    notify.reset_abuse_delivery_for_tests()


def test_the_absent_channel_reason_names_the_leg_that_is_missing(monkeypatch):
    """`DR_ISSUES_PAT` unset and an unusable `R2_*` set are different fixes.

    The builder returns None for both (`hosted_api`'s D6 residual), and the
    reason is rendered into the message a human acts on — reporting every
    absence as "DR_ISSUES_PAT unset" sends them to the wrong credential.
    Names only, never values (#4010).
    """
    import tortoise.notify as notify

    monkeypatch.delenv("DR_ISSUES_PAT", raising=False)
    assert "DR_ISSUES_PAT" in notify._channel_absent_reason()

    monkeypatch.setenv("DR_ISSUES_PAT", "placeholder-value-never-read")
    for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                 "R2_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    reason = notify._channel_absent_reason()
    assert "R2_" in reason and "DR_ISSUES_PAT" not in reason, reason
    assert "placeholder-value-never-read" not in reason, reason

    monkeypatch.setenv("R2_BUCKET", "b")
    monkeypatch.setenv("R2_ACCOUNT_ID", "a")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "s")
    assert "unavailable" in notify._channel_absent_reason()


def test_the_delivery_ledger_cap_bounds_LIVE_entries(monkeypatch):
    """The documented cap must bound live keys, not only expired ones.

    The previous filter removed only entries whose deadline had already passed,
    so with 24h windows a live population above the cap was never reduced and
    the constant bounded nothing (measured in review: 4596 live subjects, 4596
    entries). Evicting the soonest-to-expire is the honest bound.
    """
    import tortoise.notify as notify

    notify.reset_abuse_delivery_for_tests()
    now = 5_000_000.0
    monkeypatch.setattr(notify, "_delivery_now", lambda: now)
    over = notify._MAX_DELIVERY_KEYS + 50
    for i in range(over):
        notify._claim_delivery("abuse_flag", f"org-{i}:point_create",
                               notify._INCIDENT_DELIVERY_WINDOW_S)

    assert len(notify._delivery) <= notify._MAX_DELIVERY_KEYS, len(notify._delivery)
    notify.reset_abuse_delivery_for_tests()


# ── 6. the security instrument itself is untouched ───────────────────────────

def test_detection_thresholds_are_not_loosened():
    """⛔ #4872/#14407: the fix is the DELIVERY, never the detector."""
    engine = AbuseEngine(object())
    assert engine.point_threshold() == 500
    assert engine.point_window_s() == 3600
    assert engine.key_threshold() == 10
    assert engine.key_window_s() == 86400
