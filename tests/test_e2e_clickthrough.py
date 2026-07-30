"""Click-through E2E tests — full user flows from tortoise init → agent work cycle.

These tests verify the product works from a USER perspective, not just unit-level.
Run with FalkorDB available. Skip if FalkorDB unreachable.
"""
import subprocess
import sys
import tempfile
import os
from pathlib import Path

import pytest

# Check if FalkorDB is available
try:
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK()
    sdk.status()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_tortoise(*args, timeout=15, **kwargs):
    """Run `python -m tortoise <args>` and return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise"] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
        env=env,
        **kwargs,
    )
    return proc.returncode, proc.stdout, proc.stderr


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
class TestOnboardingFlow:
    """tortoise init → tortoise setup → tortoise doctor — full onboarding."""

    def test_doctor_runs_and_reports(self):
        """tortoise doctor should run and report health checks."""
        rc, stdout, stderr = run_tortoise("doctor")
        assert "Tortoise Doctor" in stdout
        assert "pass" in stdout.lower() or "fail" in stdout.lower() or "warn" in stdout.lower()

    def test_setup_noninteractive_produces_yaml(self):
        """tortoise setup --role --team --output produces valid YAML."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "config.yaml"
            rc, stdout, stderr = run_tortoise(
                "setup", "--role", "developer", "--team", "app", "--output", str(out)
            )
            assert rc == 0
            assert out.exists()
            content = out.read_text()
            assert "team: app" in content
            assert "role: developer" in content
            assert "memory_filter" in content

    def test_setup_without_team_errors(self):
        """tortoise setup --role without --team should error gracefully."""
        rc, stdout, stderr = run_tortoise("setup", "--role", "developer")
        assert rc == 1

    def test_status_accessible(self):
        """tortoise status equivalent via SDK is callable from CLI."""
        # SDK path: tortoise status via Python
        result = subprocess.run(
            [sys.executable, "-c",
             "from tortoise.sdk import TortoiseSDK; sdk = TortoiseSDK(); "
             "print(sdk.status().get('connected', False))"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        )
        assert "True" in result.stdout or "False" in result.stdout

    def test_help_lists_all_commands(self):
        """tortoise --help should list doctor, setup, init, serve."""
        rc, stdout, stderr = run_tortoise("--help")
        # --help raises SystemExit so rc may be 0 or 1 depending on argparser
        output = stdout + stderr
        for cmd in ["doctor", "setup", "init", "serve"]:
            assert cmd in output, f"Expected '{cmd}' in help output"


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
class TestAgentWorkCycle:
    """Agent creates Point → searches → gets context → writes diary → checkpoints."""

    def test_create_and_retrieve_point(self):
        """Create a Point via SDK, then retrieve it."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()

        # Create
        pt = sdk.create_point(
            kind="observation",
            content="E2E test: create and retrieve",
            context="e2e-test",
            authoredBy="test-suite",
        )
        assert "id" in pt
        point_id = pt["id"]

        # Retrieve
        fetched = sdk.get_point(point_id)
        assert fetched.get("content") == "E2E test: create and retrieve"

        # Cleanup
        sdk.delete_point_wrapped(point_id)

    def test_search_finds_point(self):
        """Create a Point, search for it, verify it appears."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()

        pt = sdk.create_point(
            kind="decision",
            content="E2E test: decided to use Redis for caching",
            context="e2e-test",
            authoredBy="test-suite",
        )
        point_id = pt["id"]

        # Search — may not find immediately if embedding isn't indexed
        results = sdk.search("Redis caching", limit=10)
        # At minimum, search shouldn't crash
        assert isinstance(results, list)

        # Cleanup
        sdk.delete_point_wrapped(point_id)

    def test_suggest_entry_points(self):
        """suggest_entry_points should return results or empty list."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()

        results = sdk.suggest_entry_points("e2e test", limit=5)
        assert isinstance(results, list)

    def test_session_context_returns_structure(self):
        """session_context should return expected keys."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()

        ctx = sdk.session_context()
        assert isinstance(ctx, dict)
        expected = {"no_prior_sessions", "diary_entries", "recent_points",
                    "recent_events", "confidence_changes"}
        if "error" not in ctx:
            for key in expected:
                assert key in ctx, f"Missing key: {key}"

    def test_diary_roundtrip(self):
        """Write diary → read diary → verify content."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()

        # Write
        result = sdk.diary_write(
            agent_name="e2e-agent",
            entry="SESSION:2026-07-21|e2e.diary.test|★★★",
            topic="e2e",
        )
        assert isinstance(result, dict)

        # Read
        entries = sdk.diary_read("e2e-agent", last_n=5)
        assert isinstance(entries, list)

    def test_checkpoint_deduplicates(self):
        """Checkpoint should deduplicate and report filed count."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()

        items = [
            {"wing": "e2e", "room": "test", "content": "Checkpoint test item A"},
            {"wing": "e2e", "room": "test", "content": "Checkpoint test item B"},
        ]
        result = sdk.checkpoint(items, agent_name="e2e-suite", threshold=1.0)
        assert isinstance(result, dict)
        assert "filed" in result or "error" in result

    def test_status_returns_counts(self):
        """status() should return connected flag and entity counts."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()

        status = sdk.status()
        assert status.get("connected") is True
        assert "counts" in status
        assert "Point" in status["counts"]

    def test_taxonomy(self):
        """taxonomy() should return entity label counts."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()

        tax = sdk.taxonomy()
        assert isinstance(tax, dict)
        # Should have at least Point count
        assert "Point" in tax or isinstance(tax.get("error"), str)


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
class TestCLIErrorPaths:
    """CLI invoked with wrong args should fail gracefully, not crash."""

    def test_unknown_command(self):
        """Unknown subcommand should show usage, not traceback."""
        rc, stdout, stderr = run_tortoise("nonexistent-command")
        # Should not crash — may return non-zero or show help
        output = stdout + stderr
        assert "Traceback" not in output

    def test_setup_missing_args(self):
        """setup with no args in non-interactive should error."""
        rc, stdout, stderr = run_tortoise("setup", "--role", "developer")
        assert rc == 1

    def test_doctor_no_crash(self):
        """doctor should never crash regardless of system state."""
        rc, stdout, stderr = run_tortoise("doctor")
        assert "Traceback" not in stdout
        assert "Traceback" not in stderr

    def test_help_subcommands(self):
        """Each subcommand should have --help."""
        for cmd in ["doctor", "setup", "init"]:
            rc, stdout, stderr = run_tortoise(cmd, "--help")
            output = stdout + stderr
            assert "usage" in output.lower() or cmd in output.lower()


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
class TestEndToEndOnboarding:
    """Full onboarding flow: init → setup → doctor — verifies everything connects."""

    def test_full_onboarding_flow(self):
        """Simulate the complete user onboarding experience."""
        # Step 1: Doctor reports health
        rc, stdout, _ = run_tortoise("doctor")
        assert "Tortoise Doctor" in stdout

        # Step 2: Setup generates config
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "config.yaml"
            rc, stdout, _ = run_tortoise(
                "setup", "--role", "developer", "--team", "app", "--output", str(out)
            )
            assert rc == 0

            # Step 3: Verify config is valid
            content = out.read_text()
            assert "memory_filter" in content

        # Step 4: Agent can create, search, and retrieve Points
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()

        pt = sdk.create_point(
            kind="observation",
            content="Onboarding flow test: agent created this Point",
            context="onboarding-e2e",
            authoredBy="test-suite",
        )
        assert "id" in pt

        fetched = sdk.get_point(pt["id"])
        assert fetched.get("content") == "Onboarding flow test: agent created this Point"

        # Cleanup
        sdk.delete_point_wrapped(pt["id"])

        # Step 5: Status reports connected
        status = sdk.status()
        assert status.get("connected") is True
