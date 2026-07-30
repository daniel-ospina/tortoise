"""E2E test for tortoise onboard — cohesive onboarding flow."""
from __future__ import annotations

import os
import sys
import tempfile
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_onboard_help():
    """onboard subcommand is registered and has help."""
    result = subprocess.run(
        [sys.executable, "-m", "tortoise", "onboard", "--help"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "onboard" in result.stdout.lower()
    print("PASS test_onboard_help")


def test_onboard_flow():
    """Full onboard flow completes without errors (non-git dir, skips index)."""
    d = tempfile.mkdtemp(prefix="tortoise_onboard_")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "tortoise", "onboard", "--path",
             os.path.join(d, "test.db")],
            capture_output=True, text=True, timeout=120,
            cwd=d,  # non-git dir — index step should skip gracefully
        )
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode

        print(f"--- stdout ---\n{stdout}")
        print(f"--- stderr ---\n{stderr}")
        print(f"exit_code={exit_code}")

        # Should complete (exit 0 even if FalkorDB unavailable — init has Lite fallback)
        assert exit_code == 0, f"Expected exit 0, got {exit_code}\nstderr: {stderr}"

        # Step numbering present
        assert "Step 1/5" in stdout, "Missing step 1"
        assert "Step 2/5" in stdout, "Missing step 2"
        assert "Step 3/5" in stdout, "Missing step 3"
        assert "Step 4/5" in stdout, "Missing step 4"
        assert "Step 5/5" in stdout, "Missing step 5"
        assert "Onboarding complete" in stdout, "Missing completion message"

        # Step 3 (index) should skip since temp dir isn't a git repo
        assert ("skipping index" in stdout.lower()
                or "not a git repo" in stdout.lower()
                or "no markdown" in stdout.lower()), \
            "Index step should gracefully skip in non-git dir"

        print("PASS test_onboard_flow")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_init_yes_flag():
    """init --yes skips input prompt."""
    d = tempfile.mkdtemp(prefix="tortoise_inity_")
    try:
        # Run with --yes in non-git dir — should complete without stdin
        result = subprocess.run(
            [sys.executable, "-m", "tortoise", "init", "--yes", "--path",
             os.path.join(d, "test.db")],
            capture_output=True, text=True, timeout=60,
            cwd=d,
        )
        stdout = result.stdout
        stderr = result.stderr

        print(f"--- stdout ---\n{stdout}")
        print(f"--- stderr ---\n{stderr}")
        print(f"exit_code={result.returncode}")

        # Should not hang on input() — should complete
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\n{stderr}"
        assert "Graph ready" in stdout or "Tortoise init" in stdout

        print("PASS test_init_yes_flag")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
