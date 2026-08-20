#!/usr/bin/env python3
"""Tests for review.py and commitments.py."""
import json  # noqa: F401
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crm", "twenty"))
import review  # noqa: I001
import commitments


class TestReviewQueue(unittest.TestCase):
    """Test review queue operations."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)  # noqa: SIM115
        self.tmp.close()
        self.orig_path = review.REVIEW_QUEUE_PATH

    def tearDown(self):
        review.REVIEW_QUEUE_PATH = self.orig_path
        os.unlink(self.tmp.name)

    def test_empty_queue(self):
        review.REVIEW_QUEUE_PATH = self.tmp.name
        queue = review.load_queue()
        self.assertEqual(queue, [])

    def test_save_and_load(self):
        review.REVIEW_QUEUE_PATH = self.tmp.name
        review.save_queue([
            {"meeting_id": "m1", "speaker_name": "John", "segments": 5}
        ])
        queue = review.load_queue()
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["speaker_name"], "John")

    def test_resolved_speaker_has_resolved_at(self):
        """Resolved speakers should have resolved_at timestamp."""
        review.REVIEW_QUEUE_PATH = self.tmp.name
        import time
        item = {
            "meeting_id": "m1",
            "speaker_name": "John",
            "segments": 5,
            "email": "john@test.com",
            "resolved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        review.save_queue([item])
        queue = review.load_queue()
        self.assertIn("resolved_at", queue[0])
        self.assertIn("email", queue[0])


class TestCommitments(unittest.TestCase):
    """Test commitment listing and updates."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.orig_dir = commitments.MEETINGS_DIR

    def tearDown(self):
        commitments.MEETINGS_DIR = self.orig_dir
        import shutil
        shutil.rmtree(self.tmp_dir)

    def test_no_meetings(self):
        commitments.MEETINGS_DIR = self.tmp_dir
        commits = commitments.load_all_commitments()
        self.assertEqual(commits, [])

    def test_parse_commitments(self):
        commitments.MEETINGS_DIR = self.tmp_dir
        md = """---
id: "test-1"
date: "2026-07-31"
commitments:
  - text: "Send pricing"
    person: "Danny"
    deadline: "2026-08-03"
    status: open
  - text: "Review docs"
    person: "Alex"
    deadline: "2026-08-01"
    status: done
---
## Transcript
Test.
"""
        path = Path(self.tmp_dir) / "test.md"
        path.write_text(md)

        commits = commitments.load_all_commitments()
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[1]["status"], "done")
        self.assertEqual(commits[0]["person"], "Danny")
        # Verify IDs are unique
        self.assertNotEqual(commits[0]["id"], commits[1]["id"])


if __name__ == "__main__":
    unittest.main()
