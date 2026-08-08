"""Tests for tortoise/alert_store.py — the per-incident dedup state machine."""

from __future__ import annotations

import json

import pytest

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

    def search_open(self, kind):
        self.search_calls.append(kind)
        return [
            n for n, t in self.issues.items()
            if f"[DR] {kind}" in t and n not in self.closed
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
    assert "[DR] STALE" in list(ch.issues.values())[0]
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
    first = list(ch.issues)[0]
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
