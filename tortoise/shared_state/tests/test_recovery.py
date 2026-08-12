"""Tests for recovery — scan_incomplete_cards, dedup_events, find_last_checkpoint.

Covers: incomplete card detection, event ID deduplication, checkpoint recovery.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

# #331: parents[2] = repo root tortoise/ dir -- parents[1] is
# tortoise/shared_state, where `shared_state` is not importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared_state.recovery import (
    dedup_events,
    find_last_checkpoint,
    scan_incomplete_cards,
)


# ── scan_incomplete_cards ───────────────────────────────────

class TestScanIncomplete:
    def test_empty_dir(self):
        d = Path(tempfile.mkdtemp())
        assert scan_incomplete_cards(d) == []

    def test_nonexistent_dir(self):
        assert scan_incomplete_cards(Path("/nonexistent/xyzzy")) == []

    def test_all_complete(self):
        d = Path(tempfile.mkdtemp())
        (d / "c1.card").write_text(json.dumps({"card_id": "c1", "completed_at": 123}))
        assert scan_incomplete_cards(d) == []

    def test_some_incomplete(self):
        d = Path(tempfile.mkdtemp())
        (d / "c1.card").write_text(json.dumps({"card_id": "c1", "completed_at": 123}))
        (d / "c2.card").write_text(json.dumps({"card_id": "c2"}))
        incomplete = scan_incomplete_cards(d)
        assert len(incomplete) == 1
        assert incomplete[0]["card_id"] == "c2"

    def test_all_incomplete(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.card").write_text(json.dumps({"card_id": "a"}))
        (d / "b.card").write_text(json.dumps({"card_id": "b"}))
        incomplete = scan_incomplete_cards(d)
        assert len(incomplete) == 2

    def test_skips_dotfiles(self):
        d = Path(tempfile.mkdtemp())
        (d / ".tmp.card").write_text(json.dumps({"card_id": "tmp"}))
        (d / "real.card").write_text(json.dumps({"card_id": "real"}))
        incomplete = scan_incomplete_cards(d)
        assert len(incomplete) == 1
        assert incomplete[0]["card_id"] == "real"

    def test_skips_invalid_json(self):
        d = Path(tempfile.mkdtemp())
        (d / "bad.card").write_text("not json")
        (d / "good.card").write_text(json.dumps({"card_id": "good"}))
        incomplete = scan_incomplete_cards(d)
        assert len(incomplete) == 1
        assert incomplete[0]["card_id"] == "good"

    def test_custom_suffix(self):
        d = Path(tempfile.mkdtemp())
        (d / "t1.task").write_text(json.dumps({"task_id": "t1"}))
        (d / "t2.task").write_text(json.dumps({"task_id": "t2", "completed_at": 1}))
        incomplete = scan_incomplete_cards(d, suffix=".task")
        assert len(incomplete) == 1
        assert incomplete[0]["task_id"] == "t1"

    def test_sorted_output(self):
        d = Path(tempfile.mkdtemp())
        (d / "z.card").write_text(json.dumps({"card_id": "z"}))
        (d / "a.card").write_text(json.dumps({"card_id": "a"}))
        incomplete = scan_incomplete_cards(d)
        assert [c["card_id"] for c in incomplete] == ["a", "z"]


# ── dedup_events ────────────────────────────────────────────

class TestDedupEvents:
    def test_empty(self):
        assert dedup_events([]) == []

    def test_no_duplicates(self):
        events = [
            {"event_id": "ev-1", "x": 1},
            {"event_id": "ev-2", "x": 2},
        ]
        assert dedup_events(events) == events

    def test_dedup_keeps_first(self):
        events = [
            {"event_id": "ev-1", "x": 1},
            {"event_id": "ev-1", "x": 2},
            {"event_id": "ev-2", "x": 3},
        ]
        result = dedup_events(events)
        assert len(result) == 2
        assert result[0]["x"] == 1

    def test_no_event_id_always_included(self):
        events = [
            {"x": 1},
            {"x": 2},
        ]
        assert dedup_events(events) == events

    def test_mixed_ids_and_no_ids(self):
        events = [
            {"event_id": "ev-1", "x": 1},
            {"x": 2},
            {"event_id": "ev-1", "x": 3},  # duplicate event_id
        ]
        result = dedup_events(events)
        assert len(result) == 2  # ev-1 (x=1) + no-id (x=2)


# ── find_last_checkpoint ────────────────────────────────────

class TestFindLastCheckpoint:
    def test_empty_log(self):
        d = Path(tempfile.mkdtemp())
        log = d / "empty.jsonl"
        log.write_text("")
        assert find_last_checkpoint(log) is None

    def test_nonexistent_log(self):
        assert find_last_checkpoint(Path("/nonexistent/log.jsonl")) is None

    def test_no_gate_passed(self):
        d = Path(tempfile.mkdtemp())
        log = d / "log.jsonl"
        log.write_text(json.dumps({"type": "card_started"}))
        assert find_last_checkpoint(log) is None

    def test_single_gate_passed(self):
        d = Path(tempfile.mkdtemp())
        log = d / "log.jsonl"
        log.write_text(json.dumps({"type": "gatePassed", "gate": "review"}))
        cp = find_last_checkpoint(log)
        assert cp is not None
        assert cp["gate"] == "review"

    def test_returns_last_gate_passed(self):
        d = Path(tempfile.mkdtemp())
        log = d / "log.jsonl"
        log.write_text('\n'.join([
            json.dumps({"type": "gatePassed", "gate": "first"}),
            json.dumps({"type": "card_started"}),
            json.dumps({"type": "gatePassed", "gate": "last"}),
        ]))
        cp = find_last_checkpoint(log)
        assert cp["gate"] == "last"

    def test_skips_invalid_json(self):
        d = Path(tempfile.mkdtemp())
        log = d / "log.jsonl"
        log.write_text('\n'.join([
            "not json",
            json.dumps({"type": "gatePassed", "gate": "valid"}),
        ]))
        cp = find_last_checkpoint(log)
        assert cp["gate"] == "valid"

    def test_skips_blank_lines(self):
        d = Path(tempfile.mkdtemp())
        log = d / "log.jsonl"
        log.write_text('\n'.join([
            "",
            json.dumps({"type": "gatePassed", "gate": "valid"}),
            "",
        ]))
        cp = find_last_checkpoint(log)
        assert cp["gate"] == "valid"


# ── #331: docstring-vs-behavior — scan_incomplete_cards is FIELD-based ──

class TestScanIncompleteEventLogNotConsulted:
    def test_completion_event_in_log_does_not_affect_scan(self):
        """A card with a cardCompleted event in the log but no completed_at
        field is STILL incomplete — recovery is field-based (the old
        docstring claimed an event-log lookup that does not exist)."""
        d = Path(tempfile.mkdtemp())
        (d / "c1.card").write_text(json.dumps({"card_id": "c1"}))
        (d / "events.jsonl").write_text(
            json.dumps({"type": "cardCompleted", "card_id": "c1"}) + "\n"
        )
        incomplete = scan_incomplete_cards(d)
        assert [c["card_id"] for c in incomplete] == ["c1"]
