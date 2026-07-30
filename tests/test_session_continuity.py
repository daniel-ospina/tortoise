"""Tests for session_continuity — auto-capture and auto-retrieve across sessions."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Check if FalkorDB is available for integration tests
try:
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK()
    sdk.status()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
class TestSessionContinuity:
    def test_start_creates_session(self):
        from tortoise.session_continuity import SessionContinuity
        sc = SessionContinuity()
        session_id = sc.start("Testing")
        assert session_id is not None
        assert session_id.startswith("session-")
        sc.sdk.close()

    def test_capture_creates_point(self):
        from tortoise.session_continuity import SessionContinuity
        sc = SessionContinuity()
        sc.start("Testing capture")
        point = sc.capture("Found a bug in auth module", kind="observation")
        assert point is not None
        assert len(sc.findings) == 1
        assert sc.findings[0]["content"] == "Found a bug in auth module"
        sc.end()
        sc.sdk.close()

    def test_capture_multiple_findings(self):
        from tortoise.session_continuity import SessionContinuity
        sc = SessionContinuity()
        sc.start("Multi-finding test")
        sc.capture("Finding 1", kind="observation")
        sc.capture("Decision: use Redis", kind="decision")
        sc.capture("Hypothesis: cache helps", kind="hypothesis")
        assert len(sc.findings) == 3
        sc.end()
        sc.sdk.close()

    def test_end_reports_findings(self):
        from tortoise.session_continuity import SessionContinuity
        sc = SessionContinuity()
        sc.start("End test")
        sc.capture("Test finding", kind="observation")
        # end() should not raise
        sc.end()
        assert len(sc.findings) == 1
        sc.sdk.close()

    def test_empty_session_end_does_not_crash(self):
        from tortoise.session_continuity import SessionContinuity
        sc = SessionContinuity()
        sc.start("Empty session")
        # No captures — end should not raise
        sc.end()
        sc.sdk.close()

    def test_capture_with_props(self):
        from tortoise.session_continuity import SessionContinuity
        sc = SessionContinuity()
        sc.start("Props test")
        point = sc.capture("Tagged finding", kind="observation", tags=["test", "integration"])
        assert point is not None
        sc.end()
        sc.sdk.close()
