"""Tests for events — EventCodec, register_event_type, event_types."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared_state.events import EventCodec, event_types, register_event_type


@pytest.fixture(autouse=True)
def _clear_registry():
    """Isolate tests from global state."""
    import shared_state.events as _ev
    _ev._event_types.clear()


class TestEventCodecEncode:
    def test_encode_basic(self):
        register_event_type("test_type")
        ev = EventCodec.encode("test_type", {"key": "value"}, event_id="ev-1")
        assert ev["type"] == "test_type"
        assert ev["version"] == 1
        assert ev["event_id"] == "ev-1"
        assert ev["key"] == "value"
        assert "timestamp" in ev

    def test_encode_custom_timestamp(self):
        register_event_type("test_type")
        ev = EventCodec.encode("test_type", {}, timestamp=42.0)
        assert ev["timestamp"] == 42.0

    def test_encode_with_extra_fields(self):
        register_event_type("test_type")
        ev = EventCodec.encode("test_type", {"a": 1}, extra="bonus")
        assert ev["extra"] == "bonus"
        assert ev["a"] == 1

    def test_encode_unregistered_type_raises(self):
        with pytest.raises(KeyError, match="Unknown event type"):
            EventCodec.encode("nonexistent", {})

    def test_encode_version_matches_upcaster_chain(self):
        register_event_type("v2_type", upcasters=[lambda e: e])
        ev = EventCodec.encode("v2_type", {})
        assert ev["version"] == 2


class TestEventCodecDecode:
    def test_decode_no_type_passthrough(self):
        assert EventCodec.decode({"x": 1}) == {"x": 1}

    def test_decode_unknown_type_passthrough(self):
        assert EventCodec.decode({"type": "unknown", "x": 1}) == {"type": "unknown", "x": 1}

    def test_decode_current_version_no_change(self):
        register_event_type("test_type")
        raw = {"type": "test_type", "version": 1, "data": "hi"}
        assert EventCodec.decode(raw) == raw

    def test_decode_upcaster_chain(self):
        register_event_type("v3_type", upcasters=[
            lambda e: {**e, "v2_added": True, "version": 2},
            lambda e: {**e, "v3_added": True, "version": 3},
        ])
        raw = {"type": "v3_type", "version": 1, "orig": "data"}
        result = EventCodec.decode(raw)
        assert result["version"] == 3
        assert result["v2_added"] is True
        assert result["v3_added"] is True
        assert result["orig"] == "data"

    def test_decode_already_current_skips_upcasters(self):
        register_event_type("v2_type", upcasters=[lambda e: {**e, "extra": True}])
        raw = {"type": "v2_type", "version": 2, "x": 1}
        result = EventCodec.decode(raw)
        assert result == raw


class TestReadAll:
    def test_read_all_from_jsonl(self):
        register_event_type("test_type")
        d = Path(tempfile.mkdtemp())
        log = d / "events.jsonl"
        log.write_text(
            json.dumps(EventCodec.encode("test_type", {"n": 1})) + "\n" +
            json.dumps(EventCodec.encode("test_type", {"n": 2})) + "\n"
        )
        events = EventCodec.read_all(str(log))
        assert len(events) == 2
        assert events[0]["n"] == 1
        assert events[1]["n"] == 2

    def test_read_all_empty_file(self):
        d = Path(tempfile.mkdtemp())
        log = d / "empty.jsonl"
        log.write_text("")
        assert EventCodec.read_all(str(log)) == []

    def test_read_all_skips_blank_lines(self):
        register_event_type("test_type")
        d = Path(tempfile.mkdtemp())
        log = d / "events.jsonl"
        log.write_text(
            "\n" +
            json.dumps(EventCodec.encode("test_type", {"n": 1})) + "\n\n"
        )
        events = EventCodec.read_all(str(log))
        assert len(events) == 1

    def test_read_all_upcasts_on_read(self):
        register_event_type("v2_type", upcasters=[lambda e: {**e, "upgraded": True}])
        d = Path(tempfile.mkdtemp())
        log = d / "events.jsonl"
        log.write_text(json.dumps({"type": "v2_type", "version": 1, "x": 1}) + "\n")
        events = EventCodec.read_all(str(log))
        assert events[0]["upgraded"] is True

    def test_read_all_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            EventCodec.read_all("/nonexistent/path.jsonl")


class TestEventTypes:
    def test_event_types_returns_version_map(self):
        register_event_type("a")
        register_event_type("b", upcasters=[lambda e: e, lambda e: e])
        types = event_types()
        assert types == {"a": 1, "b": 3}


class TestRegistration:
    def test_duplicate_registration_raises(self):
        register_event_type("dup")
        with pytest.raises(ValueError, match="already registered"):
            register_event_type("dup")

    def test_register_with_upcasters(self):
        register_event_type("with_upcasters", upcasters=[lambda e: e, lambda e: e])
        assert event_types()["with_upcasters"] == 3
