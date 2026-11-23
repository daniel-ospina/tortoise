"""#14407 — every abuse notification is ATTRIBUTABLE and its rate is BOUNDED.

The owner reported "Telegram alerts are out of control". The code-level cause is
that the human channel and the agent channel were two different decisions:

* the INCIDENT (GitHub issue) is deduped by the store's create-once R2 object per
  ``(kind, subject)`` — one issue for a lifecycle;
* the TELEGRAM PUSH was emitted per DETECTION by ``notify.notify_abuse``, gated
  only by the engine's in-process window claim — so one open incident pushed every
  window, and 4 of the 6 abuse classes never filed anything at all, leaving a
  message no issue, runbook or search could attribute.

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
    assert len(delivered.filed) == 1, "a re-alarm must never re-file"


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

    # rule B has its own (absent) episode ending clean — it must touch nothing.
    notify.resolve_abuse_episode("org-a", "key_create")
    assert delivered.closed == [], (
        "another rule's clean window closed this incident (false all-clear): "
        f"{delivered.closed} — and the next detection would re-file it")

    # rule A's own episode end still resolves it (the recovery path works).
    notify.resolve_abuse_episode("org-a", "point_create")
    assert delivered.closed == [1], delivered.closed


def test_a_new_episode_re_fires_exactly_once(delivered):
    """Pre-fix: 3 + 4 = 7 messages. A fix that muted would send 3."""
    import tortoise.notify as notify

    for _ in range(3):
        _flag(notify)
    assert len(delivered.pushes) == 1

    # the engine observed the rule's window clean → episode over (see abuse.py)
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
    notify.resolve_abuse_episode("org-a", "point_create")
    assert any("resolved" in p for p in delivered.pushes), delivered.pushes


# ── 3. the measurement classes: declared non-filing, digest delivery ─────────

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

class _CleanWindowStore:
    """An abuse store whose window has just gone clean with a flag on record."""

    def __init__(self) -> None:
        self.cleared: list[tuple[str, str]] = []

    def window_sum(self, org_id, rule, window_s, now):
        return 0

    def latest_flag_at(self, org_id, rule):
        return datetime.now(timezone.utc) - timedelta(hours=2)  # noqa: UP017

    def flag_clear(self, org_id, rule, now=None):
        self.cleared.append((org_id, rule))


def test_episode_end_resolves_the_abuse_incidents(delivered, monkeypatch):
    """Pre-fix nothing ever resolved an abuse incident: the sentinel and its
    issue stayed open for the rest of time, which is why no incident search can
    attribute a month-old recurrence (#9944's shape)."""
    import tortoise.notify as notify

    engine = AbuseEngine(_CleanWindowStore())
    _flag(notify)
    assert delivered.filed, "the alert must have filed its incident first"

    engine._evaluate("org-a", "point_create", 500, 3600,
                     datetime.now(timezone.utc))  # noqa: UP017

    assert delivered.closed == [1], "the episode-end close is the recovery path"
    titles = [t for t, _ in delivered.filed]
    assert len(titles) == 1, "resolving must not re-file"


def test_a_clean_window_with_no_episode_does_no_store_work(monkeypatch):
    """A clean window on an org that was NEVER flagged is not an episode end.

    Pre-fix `ended` meant "the clean-window read did not raise", so every clean
    evaluation — the overwhelmingly common case, twice per point create — ran the
    episode-end resolution: 2 incident kinds × 2 subject spellings =
    up to 8 `resolve_incident_state` calls, each one an R2 `get_object` round
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
    assert "no issue number on record" in sent[0]
    notify.reset_abuse_delivery_for_tests()


# ── 6. the security instrument itself is untouched ───────────────────────────

def test_detection_thresholds_are_not_loosened():
    """⛔ #4872/#14407: the fix is the DELIVERY, never the detector."""
    engine = AbuseEngine(object())
    assert engine.point_threshold() == 500
    assert engine.point_window_s() == 3600
    assert engine.key_threshold() == 10
    assert engine.key_window_s() == 86400
