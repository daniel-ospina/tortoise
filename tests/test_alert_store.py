"""Tests for tortoise/alert_store.py — the per-incident dedup state machine."""

from __future__ import annotations

import json

import pytest  # noqa: F401

from tortoise.alert_store import AlertStore
from tortoise.hosted_backup import MemoryStorage


class _FakeChannels:
    def __init__(self):
        self.issues: dict[int, str] = {}      # number -> title
        self.closed: list[int] = []
        self.comments: list[tuple[int, str]] = []
        self.telegram: list[str] = []
        self.search_calls: list[str] = []
        self.fail_file = False
        self.fail_push = False
        self._next = 1

    def file_issue(self, title, body):
        if self.fail_file:
            raise RuntimeError("github down")
        n = self._next
        self._next += 1
        self.issues[n] = title
        return n

    def close_issue(self, number, comment=None):
        self.closed.append(number)
        if comment:
            self.comments.append((number, comment))

    def search_open(self, kind, team_id=""):
        self.search_calls.append((kind, team_id))
        return [
            n for n, t in self.issues.items()
            if f"[DR] {kind}" in t
            and (team_id == "" or t.endswith(f" — {team_id}"))
            and n not in self.closed
        ]

    def push_telegram(self, text):
        if self.fail_push:
            raise RuntimeError("telegram down")
        self.telegram.append(text)


def _store(channels, storage=None) -> AlertStore:
    return AlertStore(
        storage or MemoryStorage(),
        file_issue=channels.file_issue,
        close_issue=channels.close_issue,
        search_open=channels.search_open,
        push_telegram=channels.push_telegram,
        repo="daniel-ospina/tortoise",
        assignee="daniel-ospina",
    )


def test_open_once_files_issue_and_pushes():
    ch = _FakeChannels()
    store = _store(ch)
    assert store.open_incident("STALE", "team_x") is True
    assert len(ch.issues) == 1
    assert "[DR] STALE" in list(ch.issues.values())[0]  # noqa: RUF015
    assert len(ch.telegram) == 1
    assert "STALE" in ch.telegram[0]


def test_repeat_open_reuses_incident():
    """While the incident is open, repeats never file again (per-incident dedup)."""
    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE", "team_x")
    store.open_incident("STALE", "team_x")
    store.open_incident("STALE", "team_x")
    assert len(ch.issues) == 1
    assert len(ch.telegram) == 1


def test_resolve_closes_and_delete_to_resolve():
    """Recovery closes the issue + deletes the dedup object; a later recurrence
    is a NEW incident (new issue number)."""
    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE", "team_x")
    first = list(ch.issues)[0]  # noqa: RUF015
    assert store.resolve_incident("STALE", "team_x") is True
    assert ch.closed == [first]
    assert len(ch.telegram) == 2  # open + resolved
    assert "resolved" in ch.telegram[1].lower()
    # Recurrence → new incident, new issue.
    store.open_incident("STALE", "team_x")
    assert len(ch.issues) == 2
    assert store.resolve_incident("STALE", "team_x") is True


def test_adopter_does_not_double_file():
    """A 412 loser adopts the winner's issue_number and never files again."""
    ch = _FakeChannels()
    s1 = _store(ch)
    s2 = _store(ch)
    s1.open_incident("STALE")
    s2.open_incident("STALE")  # loser — must adopt, not file
    assert len(ch.issues) == 1


def test_create_then_die_window_is_not_silent():
    """Winner died between create and backfill: the adopter finds a placeholder,
    becomes the filer (via GH-search fallback), and files exactly once."""
    ch = _FakeChannels()
    storage = MemoryStorage()

    # First actor opens but its filing fails (GH down) — placeholder remains.
    ch.fail_file = True
    s1 = _store(ch, storage)
    s1.open_incident("STALE")
    assert ch.issues == {}
    ch.fail_file = False

    # Second actor adopts the placeholder; GH-search finds nothing; becomes filer.
    s2 = _store(ch, storage)
    s2.open_incident("STALE")
    assert len(ch.issues) == 1


def test_suppression_pauses_kind():
    ch = _FakeChannels()
    store = _store(ch)
    store._storage.upload(
        "ops/suppression.json",
        json.dumps({"STALE": {"until": "2099-01-01T00:00:00+00:00"}}).encode(),
    )
    assert store.open_incident("STALE") is False
    assert ch.issues == {}
    assert ch.telegram == []


def test_pending_push_retried_by_daemon():
    ch = _FakeChannels()
    ch.fail_push = True
    store = _store(ch)
    store.open_incident("STALE")  # issue filed, push parked
    assert ch.telegram == []
    pending = store._storage.list("ops/pending-push/")
    assert len(pending) == 1
    ch.fail_push = False
    assert store.retry_pending() == 1
    assert len(ch.telegram) == 1
    assert store._storage.list("ops/pending-push/") == []


# ── Channel independence (#673) ─────────────────────────────────────────────

def test_telegram_failure_does_not_block_github_issue():
    """When Telegram is down, the GitHub issue MUST still be filed.
    The incident lifecycle treats Telegram as best-effort — a failed push
    parks a pending-push blob and the issue filing proceeds normally."""
    ch = _FakeChannels()
    ch.fail_push = True  # Telegram down
    store = _store(ch)
    assert store.open_incident("STALE", "team_x") is True
    # Issue was filed.
    assert len(ch.issues) == 1
    assert "[DR] STALE" in list(ch.issues.values())[0]  # noqa: RUF015
    # Telegram was NOT pushed (parked instead).
    assert ch.telegram == []
    assert len(store._storage.list("ops/pending-push/")) == 1


def test_github_failure_does_not_block_telegram_push():
    """When GitHub is down, Telegram MUST still push.
    The issue filing fails but Telegram push still fires (best-effort)
    on the next polling cycle when the adopter picks up the placeholder."""
    ch = _FakeChannels()
    ch.fail_file = True  # GitHub down
    store = _store(ch)
    # open_incident tries to file → fails → still returns True (became filer
    # but couldn't file or push yet — placeholder state).
    store.open_incident("STALE")
    # Neither channel fired yet (both blocked by GH-down in this cycle).
    assert ch.issues == {}
    assert ch.telegram == []
    # On the next poll, a second call (adopter) picks up the placeholder,
    # GH-search finds nothing, files the issue, THEN pushes Telegram.
    ch.fail_file = False
    store.open_incident("STALE")
    assert len(ch.issues) == 1
    assert len(ch.telegram) == 1


def test_all_alert_kinds_push_telegram_on_open():
    """Every alert kind in the taxonomy sends a Telegram message on open."""
    # Alert kinds from the DR runbook taxonomy (docs/ops/registry-backup-dr.md).
    kinds = [
        "STALE",
        "NEVER_BACKED_UP",
        "METADATA_LOST",
        "BACKUP_SET_MISSING",
        "DRIVER_DOWN",
        "R2_DOWN",
        "ALERTER_DOWN",
        "APP_DOWN",
        "WATCHER_DOWN",
        "LIVENESS_NO_WORK",
        "SIZE_GUARD_ABORT",
        "DATA_LOSS_CANDIDATE",
    ]
    for kind in kinds:
        ch = _FakeChannels()
        store = _store(ch)
        assert store.open_incident(kind) is True
        assert len(ch.telegram) == 1, f"{kind} did not push Telegram"
        assert kind in ch.telegram[0], f"{kind} name missing from Telegram text"
        assert len(ch.issues) == 1, f"{kind} did not file GitHub issue"


def test_resolved_telegram_carries_issue_number():
    """The resolved Telegram message references the GitHub issue number."""
    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE", "team_x")
    issue_num = list(ch.issues)[0]  # noqa: RUF015
    ch.telegram.clear()  # clear the open message
    store.resolve_incident("STALE", "team_x")
    assert len(ch.telegram) == 1
    resolved_text = ch.telegram[0]
    assert "resolved" in resolved_text.lower()
    assert str(issue_num) in resolved_text


def test_dedup_no_repeat_telegram_same_incident():
    """While an incident is open, repeated open_incident calls do NOT
    send duplicate Telegram messages."""
    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE", "team_x")
    assert len(ch.telegram) == 1
    # Repeated calls — no new messages.
    for _ in range(5):
        store.open_incident("STALE", "team_x")
    assert len(ch.telegram) == 1
    # Resolve and reopen → new incident, new message.
    store.resolve_incident("STALE", "team_x")
    assert len(ch.telegram) == 2  # open + resolved
    store.open_incident("STALE", "team_x")
    assert len(ch.telegram) == 3  # new incident = new open message


def test_telegram_and_github_independent_resolve():
    """On resolve, both channels fire independently — a Telegram push failure
    does not prevent the GitHub issue close, and vice versa."""
    # Case A: Telegram fails on resolve — issue still closes.
    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE")
    ch.telegram.clear()
    ch.fail_push = True
    assert store.resolve_incident("STALE") is True
    assert len(ch.closed) == 1  # Issue closed.
    assert ch.telegram == []     # Telegram parked.
    assert len(store._storage.list("ops/pending-push/")) == 1

    # Case B: GitHub fails on resolve — Telegram still sends.
    ch2 = _FakeChannels()
    store2 = _store(ch2)
    store2.open_incident("STALE")
    ch2.telegram.clear()
    # Simulate GitHub close failure by monkeypatching the close callable.
    original_close = ch2.close_issue  # noqa: F841
    def failing_close(number, comment=None):
        raise RuntimeError("github api down")
    ch2.close_issue = failing_close
    assert store2.resolve_incident("STALE") is True  # still resolves
    assert len(ch2.telegram) == 1
    assert "resolved" in ch2.telegram[0].lower()
    # Dedup object is deleted even if issue close failed (delete-to-resolve
    # runs regardless).
    assert store2._storage.list("ops/alerts/STALE/") == []


# ── #673 review P2: pending-push TTL + incident check ─────────────────────

def test_stale_pending_push_discarded_on_ttl():
    """A pending push older than 24h is discarded, not retried (#673 P2)."""
    from datetime import datetime, timedelta, timezone

    ch = _FakeChannels()
    storage = MemoryStorage()
    # park a push at 25h ago
    old_ts = datetime.now(timezone.utc) - timedelta(hours=25)  # noqa: UP017
    import hashlib
    digest = hashlib.sha256(b"ops/alerts/STALE/_.json").hexdigest()[:16]
    storage.upload(
        f"ops/pending-push/{digest}.json",
        json.dumps({
            "key": "ops/alerts/STALE/_.json",
            "text": "stale alert",
            "created_at": old_ts.isoformat(),
        }).encode(),
    )
    store = _store(ch, storage)
    # retry_pending must discard it (TTL expired), not push.
    assert store.retry_pending() == 0
    assert ch.telegram == []
    assert storage.list("ops/pending-push/") == []


def test_resolved_incident_pending_push_not_fired():
    """A pending push whose incident dedup key is gone (resolved) is
    discarded, not fired (#673 P2)."""
    from datetime import datetime, timezone

    ch = _FakeChannels()
    storage = MemoryStorage()
    # park a fresh push referencing an incident key that does NOT exist
    import hashlib
    digest = hashlib.sha256(b"ops/alerts/STALE/team_x.json").hexdigest()[:16]
    storage.upload(
        f"ops/pending-push/{digest}.json",
        json.dumps({
            "key": "ops/alerts/STALE/team_x.json",
            "text": "orphan push",
            "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        }).encode(),
    )
    store = _store(ch, storage)
    # retry_pending must discard it (incident resolved), not push.
    assert store.retry_pending() == 0
    assert ch.telegram == []
    assert storage.list("ops/pending-push/") == []


def test_search_fallback_is_subject_scoped():
    """#2313 Task 4: create-then-die adoption via GH-search must match the
    incident's OWN subject. A kind-only search would let team_b's adopter bind
    team_a's pre-existing open issue number — and resolving team_b would then
    close team_a's issue (silent-loss cross-talk)."""
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    # Pre-existing open STALE for team_a (issue #1 on the board).
    assert store.open_incident("STALE", "team_a") is True
    assert len(ch.issues) == 1
    # team_b's winner died between create and backfill: its dedup key exists
    # as an issue_number-less placeholder (the exact create-then-die window).
    storage.upload(
        "ops/alerts/STALE/team_b.json",
        json.dumps({"kind": "STALE", "team_id": "team_b", "detail": {},
                    "filed_at": "2026-08-08T00:00:00+00:00",
                    "issue_number": None, "telegram_pushed": False}).encode(),
    )
    # Adopter for team_b: the search must be scoped to team_b (no match — the
    # only open issue belongs to team_a), so team_b files its OWN issue.
    store.open_incident("STALE", "team_b")
    assert ("STALE", "team_b") in ch.search_calls
    titles = list(ch.issues.values())
    assert len(titles) == 2
    assert any("team_a" in t for t in titles)
    assert any("team_b" in t for t in titles)
    # Resolving team_b closes ONLY team_b's issue (#2); team_a's (#1) stays
    # open (the channels fake marks closed rather than popping issues).
    store.resolve_incident("STALE", "team_b")
    assert ch.closed == [2]
    store.resolve_incident("STALE", "team_a")
    assert ch.closed == [2, 1]
