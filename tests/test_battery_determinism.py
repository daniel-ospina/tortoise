"""Task 8 tests — E2E-7.1 determinism: same seed → |Δ| ≤ 1e-6 across
metric_values, in SEPARATE subprocesses with pinned hash seed + isolated
embedded DB (TORTOISE_DB_URI="" + TORTOISE_DB_PATH=<tmpdir>)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "battery" / "config"
EPSILON = 1e-6


def _run_attempt(attempt_dir: Path) -> Path:
    """One CLI subprocess; returns the printed attempt dir."""
    env = {**os.environ,
           "PYTHONHASHSEED": "0",
           "TORTOISE_DB_URI": "",
           "TORTOISE_DB_PATH": str(attempt_dir / "db")}
    out = subprocess.run(
        [sys.executable, "-m", "battery", "run", "--mock", "--seed", "7",
         "--config", str(CONFIG), "--out", str(attempt_dir)],
        env=env, cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, f"battery run failed: {out.stderr}"
    printed = out.stdout.strip().splitlines()[-1]
    return Path(printed)


@pytest.mark.slow
class TestDeterminism:
    def test_two_runs_identical_metric_values(self, tmp_path):
        run1_dir = _run_attempt(tmp_path / "run1")
        run2_dir = _run_attempt(tmp_path / "run2")

        # The two attempt dirs MUST differ (sub-second attempt_ts) — identical
        # dirs would silently compare a dir to itself.
        assert run1_dir != run2_dir

        arts1 = sorted(p for p in run1_dir.glob("*.json") if p.name != "summary.json")
        arts2 = sorted(p for p in run2_dir.glob("*.json") if p.name != "summary.json")
        # Artifact count = scenario count (production corpus is 100+; the
        # count is derived from the corpus, not hardcoded).
        from battery.config.corpus import load_corpus
        expected = len(load_corpus(CONFIG / "corpus.yaml"))
        assert len(arts1) == len(arts2) == expected
        for p1, p2 in zip(arts1, arts2):
            a1 = json.loads(p1.read_text())
            a2 = json.loads(p2.read_text())
            assert a1["run_id"] == a2["run_id"]
            assert set(a1["metric_values"]) == set(a2["metric_values"])
            for mid in a1["metric_values"]:
                d = abs(a1["metric_values"][mid] - a2["metric_values"][mid])
                assert d <= EPSILON, f"{mid}: |Δ|={d} > {EPSILON}"

    def test_uri_neutralization(self, tmp_path):
        """With TORTOISE_DB_URI=\"\" the SDK honors TORTOISE_DB_PATH — the
        two runs share no graph state (distinct stores, no busy conflicts)."""
        db1 = tmp_path / "db1"
        db2 = tmp_path / "db2"
        env = {**os.environ, "TORTOISE_DB_URI": "", "TORTOISE_DB_PATH": str(db1)}
        out = subprocess.run(
            [sys.executable, "-c",
             "from tortoise.sdk import TortoiseSDK; "
             "import sys; s = TortoiseSDK(); "
             "print('uri', s._db_uri, 'path', s._db_path); s.close()"],
            env=env, cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        assert "uri None" in out.stdout
        assert str(db1) in out.stdout
        # second run uses its own path → no shared graph
        env2 = {**env, "TORTOISE_DB_PATH": str(db2)}
        out2 = subprocess.run(
            [sys.executable, "-c",
             "from tortoise.sdk import TortoiseSDK; "
             "s = TortoiseSDK(); print(s._db_path); s.close()"],
            env=env2, cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
        assert out2.returncode == 0
        assert str(db2) in out2.stdout
