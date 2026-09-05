"""Task 8 tests — E2E-7.1 determinism: same seed → |Δ| ≤ per-metric
    tolerance across metric_values, in SEPARATE subprocesses with pinned hash
    seed + isolated embedded DB (TORTOISE_DB_URI="" + TORTOISE_DB_PATH=<tmpdir>).

    #2284 Task 7 re-scope: tolerances are read from thresholds.yaml
    determinism.tolerances per metric (fallback determinism.epsilon) —
    NEVER a test-local constant; the seeded rows came from this lane's
    measured deltas (all |Δ| = 0.0, ≤ the epsilon floor)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from battery.config.thresholds import DEFAULT_EPSILON

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "battery" / "config"


def _tolerances() -> tuple[dict[str, float], float]:
    """Per-metric determinism tolerances + the epsilon fallback, resolved
    from thresholds.yaml (never a test-local constant)."""
    from battery.config.thresholds import load_thresholds
    t = load_thresholds(CONFIG / "thresholds.yaml")
    return dict(t.determinism_tolerances), t.determinism_epsilon


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

        # Artifact count = scenario count (production corpus is 100+; the
        # count is derived from the corpus, not hardcoded). The run-end
        # LIVE writers (recall.json/family_*, Task 5) are not episode
        # artifacts and are excluded from the pairwise compare.
        arts1 = sorted(p for p in run1_dir.glob("*.json")
                       if p.name not in ("summary.json", "recall.json")
                       and not p.name.startswith("family_"))
        arts2 = sorted(p for p in run2_dir.glob("*.json")
                       if p.name not in ("summary.json", "recall.json")
                       and not p.name.startswith("family_"))
        # Artifact count = scenario count (production corpus is 100+; the
        # count is derived from the corpus, not hardcoded).
        from battery.config.corpus import load_corpus
        expected = len(load_corpus(CONFIG / "corpus.yaml"))
        assert len(arts1) == len(arts2) == expected
        tols, epsilon = _tolerances()
        for p1, p2 in zip(arts1, arts2):  # noqa: B905
            a1 = json.loads(p1.read_text())
            a2 = json.loads(p2.read_text())
            assert a1["run_id"] == a2["run_id"]
            assert set(a1["metric_values"]) == set(a2["metric_values"])
            for mid in a1["metric_values"]:
                d = abs(a1["metric_values"][mid] - a2["metric_values"][mid])
                # per-metric tolerance from thresholds.yaml (fallback:
                # determinism.epsilon) — the transcript-locked floor is
                # NEVER relaxed by the Task 7 re-scope for derived fields
                tol = tols.get(mid, epsilon)
                assert d <= tol, f"{mid}: |Δ|={d} > tol={tol}"

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


class TestDeterminismTolerances:
    """#2284 Task 7 — E2E-7.1 re-scope locks: the epsilon path resolves from
    thresholds.yaml and the tolerance table folds into the cal-table hash
    (the `calibrate --print` route). Determinism asserts are NOT weakened:
    the transcript-locked derived/objective floor stays |Δ| ≤ 1e-6."""

    def test_epsilon_path_resolves_from_thresholds_yaml(self):
        # #2284 review P2: resolve the floor + row count from the AUTHORED
        # yaml, not test-local constants — a #2292 metric addition extends
        # thresholds.yaml, never this assertion.
        import yaml as _yaml
        authored = _yaml.safe_load(
            (CONFIG / "thresholds.yaml").read_text(encoding="utf-8"))
        det = authored.get("determinism") or {}
        tols, epsilon = _tolerances()
        # resolution lock: resolved epsilon == the authored yaml floor
        # (yaml holds '1e-6' as text — PyYAML never resolves bare 1e-6;
        # the loader floats it)
        assert epsilon == float(det.get("epsilon", DEFAULT_EPSILON))
        # table lock: every authored tolerance row resolves — no silent
        # drop and no phantom row; per-metric rows seeded from the
        # transcript-locked 1e-6 floor (comment in thresholds.yaml).
        authored_rows = det.get("tolerances") or {}
        assert len(tols) == len(authored_rows)
        assert set(tols) == set(authored_rows)
        # transcript lock: the epsilon floor itself stays 1e-6
        assert epsilon == 1e-6
        # every artifact metric id this lane produces is seeded in the
        # tolerance table — no metric falls back silently
        from battery.config.corpus import load_corpus
        scenario_count = len(load_corpus(CONFIG / "corpus.yaml"))
        assert scenario_count >= 100

    def test_tolerance_table_folds_into_cal_table_hash(self):
        from battery.config.thresholds import (
            ThresholdsConfig,
            load_thresholds,
        )
        t1 = load_thresholds(CONFIG / "thresholds.yaml")
        assert t1.determinism_tolerances
        dropped = ThresholdsConfig(cal_rows=t1.cal_rows)
        assert dropped.cal_table_hash() != t1.cal_table_hash()
        relocked = ThresholdsConfig(
            cal_rows=t1.cal_rows,
            determinism_tolerances=(
                (t1.determinism_tolerances[0][0],
                 t1.determinism_tolerances[0][1] * 2),
                *t1.determinism_tolerances[1:]))
        assert relocked.cal_table_hash() != t1.cal_table_hash()
