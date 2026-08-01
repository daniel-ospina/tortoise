#!/usr/bin/env python3
"""Tests for bridge.py — Meeting Intelligence Pipeline integration."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add bridge to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crm", "twenty"))
import bridge


class TestBridgeParsing(unittest.TestCase):
    """Test markdown parsing and validation."""

    def setUp(self):
        self.valid_md = """---
id: "2026-07-31-test-call"
date: "2026-07-31T14:30:22Z"
duration_sec: 1847
type: meeting
capture: full
title: "Test Call"
speakers:
  - name: "Danny"
    segments: 42
    email: "danny@eldato.com"
  - name: "Alex Chen"
    segments: 38
    email: "alex@company.com"
commitments:
  - text: "Send pricing by Friday"
    person: "Danny"
    deadline: "2026-08-03"
    status: open
decisions:
  - text: "Monthly billing"
    speaker: "Alex Chen"
topics:
  - pricing
content_hash: "abc123def456"
---
## Transcript
[00:00] Danny: Let's discuss pricing.
[00:45] Alex: Monthly works better.
"""

    def test_parse_valid_markdown(self):
        """Valid markdown should parse successfully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(self.valid_md)
            path = f.name

        try:
            with patch.object(bridge, "find_note_by_external_id", return_value=None):
                with patch.object(bridge, "find_person_by_email", return_value={"id": "person-1", "name": "Alex Chen"}):
                    with patch.object(bridge, "create_note", return_value={"id": "note-1"}):
                        with patch.object(bridge, "push_to_tortoise", return_value={"status": "skipped"}):
                            with patch.object(bridge, "get_calendar_attendees", return_value=[]):
                                result = bridge.process_meeting(path)
                                self.assertIn(result["status"], ["ok", "partial"])
        finally:
            os.unlink(path)

    def test_parse_invalid_yaml(self):
        """Invalid YAML should return error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\ninvalid: [unclosed\n---\n")
            path = f.name

        try:
            result = bridge.process_meeting(path)
            self.assertEqual(result["status"], "error")
            self.assertIn("invalid_yaml", result.get("reason", ""))
        finally:
            os.unlink(path)

    def test_missing_file(self):
        """Non-existent file should return error."""
        result = bridge.process_meeting("/tmp/nonexistent-file.md")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "file_not_found")


class TestBridgeIdempotency(unittest.TestCase):
    """Test idempotency via content_hash."""

    def test_skip_already_processed(self):
        """Already processed meeting should be skipped."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(self._make_md("hash-123"))
            path = f.name

        try:
            with patch.object(bridge, "find_note_by_external_id", return_value={"id": "note-1"}):
                result = bridge.process_meeting(path)
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["reason"], "already_processed")
        finally:
            os.unlink(path)

    def _make_md(self, content_hash):
        return f"""---
id: "test"
date: "2026-07-31"
title: "Test"
content_hash: "{content_hash}"
---
## Transcript
[00:00] Test.
"""


class TestBridgeContactMatching(unittest.TestCase):
    """Test contact matching from calendar attendees."""

    def test_auto_create_new_contact(self):
        """New calendar attendee should be auto-created."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(self._make_md_with_speaker("New Person", "new@test.com"))
            path = f.name

        try:
            with patch.object(bridge, "find_note_by_external_id", return_value=None):
                with patch.object(bridge, "find_person_by_email", return_value=None):
                    with patch.object(bridge, "create_person", return_value={"id": "new-1"}) as mock_create:
                        with patch.object(bridge, "create_opportunity", return_value={"id": "opp-1"}):
                            with patch.object(bridge, "create_note", return_value={"id": "note-1"}):
                                with patch.object(bridge, "push_to_tortoise", return_value={"status": "skipped"}):
                                    with patch.object(bridge, "get_calendar_attendees", return_value=[
                                        {"name": "New Person", "email": "new@test.com"}
                                    ]):
                                        bridge.process_meeting(path)
                                        mock_create.assert_called()
        finally:
            os.unlink(path)

    def test_no_contacts_returns_partial(self):
        """Meeting with no matched contacts should return partial."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(self._make_md_with_speaker("Unknown", ""))
            path = f.name

        try:
            with patch.object(bridge, "find_note_by_external_id", return_value=None):
                with patch.object(bridge, "find_person_by_email", return_value=None):
                    with patch.object(bridge, "get_calendar_attendees", return_value=[]):
                        result = bridge.process_meeting(path)
                        self.assertEqual(result["status"], "partial")
                        self.assertEqual(result["reason"], "no_contacts_matched")
        finally:
            os.unlink(path)

    def _make_md_with_speaker(self, name, email):
        email_line = f'\n    email: "{email}"' if email else ""
        return f"""---
id: "test"
date: "2026-07-31"
title: "Test"
speakers:
  - name: "{name}"{email_line}
    segments: 10
---
## Transcript
[00:00] Test.
"""


class TestCalendarAttendeeExtraction(unittest.TestCase):
    """Test calendar attendee extraction from state file."""

    def test_no_state_file(self):
        """No state file should return empty list."""
        with patch.object(bridge, "CAL_STATE_FILE", "/tmp/nonexistent-state.json"):
            result = bridge.get_calendar_attendees()
            self.assertEqual(result, [])

    def test_valid_state_file(self):
        """Valid state file should return attendees."""
        state = {
            "recording_pid": 12345,
            "event_title": "Test Call",
            "attendees": [
                {"name": "John", "email": "john@test.com"},
                {"name": "Jane", "email": "jane@test.com"},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(state, f)
            path = f.name

        try:
            with patch.object(bridge, "CAL_STATE_FILE", path):
                result = bridge.get_calendar_attendees()
                self.assertEqual(len(result), 2)
                self.assertEqual(result[0]["name"], "John")
                self.assertEqual(result[1]["email"], "jane@test.com")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
