"""DE2E-10 — graph-scale diagnostics gate (issue #1239, epic 903-C1).

Runs ``graph-scripts/graph-diagnostics.py`` against the F5 representative synthetic
fixture and asserts the MEASURABLE invariants only (the stale-first-vs-full
decision itself is a HUMAN gate recorded in
``docs/epics/2026-08-13-903-dreaming-ep/06-diagnostics.md`` — a "decision
recorded" assertion cannot fail meaningfully, so it is NOT asserted here).

What is asserted:
- script exits 0 (all invariants PASS) on the F5 fixture;
- node/edge counts > 0; fan-out sums to the edge count;
- neighborhood (region) sizes emitted with a non-empty sample;
- connected-component stats emitted and consistent (sizes sum to all points);
- the decision record file exists and contains an explicit recorded decision.

The script is executed as a SUBPROCESS (``sys.executable``) so the test
exercises the real CLI entry point end-to-end — including the script's own
F5 fixture construction (fresh tempfile per process → no embedded-store
overlap with the pytest process).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Repo convention (#129): repo-local graph scripts live in ``graph-scripts/``;
# ``scripts/`` is a symlink to the shared agent-infra tooling repo (the
# issue's nominal ``scripts/`` path is stale).
_SCRIPT = _REPO_ROOT / "graph-scripts" / "graph-diagnostics.py"
_DECISION_RECORD = (
    _REPO_ROOT
    / "docs" / "epics" / "2026-08-13-903-dreaming-ep" / "06-diagnostics.md"
)


def _run_diagnostics(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """Run the diagnostics script in a fresh subprocess (hermetic: the script
    builds its own F5 fixture on its own tempfile path)."""
    env = dict(os.environ)
    env.pop("TORTOISE_DB_URI", None)  # --fixture ignores it anyway; be explicit
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--fixture", "--json", *args],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestGraphDiagnosticsScript:
    def test_f5_run_passes_invariants_and_emits_metrics(self):
        """DE2E-10 measurable invariants on the F5 fixture: counts > 0,
        fan-out sums to the edge count, component + neighborhood stats
        emitted."""
        proc = _run_diagnostics()
        assert proc.returncode == 0, (
            f"script failed (exit {proc.returncode})\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        report = json.loads(proc.stdout)
        stats = report["stats"]

        # fan-out histogram (JSON keys are strings — coerce back to int)
        fan_out = {int(a): c for a, c in stats["fan_out"].items()}

        # counts > 0
        assert stats["n_claims"] > 0, stats
        assert stats["n_operators"] > 0, stats
        assert stats["n_edges"] > 0, stats

        # pinned F5 values (cross-check the script's fixture against the
        # shared F5 builder constants — catches drift on either side)
        from tests.epic903_fixtures import (  # noqa: PLC0415
            F5_FAN_OUT,
            F5_N_CLAIMS,
            F5_N_EDGES,
            F5_N_OPERATORS,
        )
        assert stats["n_claims"] == F5_N_CLAIMS, stats
        assert stats["n_operators"] == F5_N_OPERATORS, stats
        assert stats["n_edges"] == F5_N_EDGES, stats
        assert fan_out == dict(sorted(F5_FAN_OUT.items())), stats

        # fan-out sums to the edge count (per-op arity × count)
        fan_sum = sum(arity * count
                      for arity, count in fan_out.items())
        assert fan_sum == stats["n_edges"], (
            f"fan-out {stats['fan_out']} sums to {fan_sum}, "
            f"expected {stats['n_edges']}"
        )
        # IMPL + NAND == total edges
        assert stats["n_edges_impl"] + stats["n_edges_nand"] == stats["n_edges"]

        # neighborhood (region) sizes emitted with a non-empty sample
        nbr = stats["neighborhoods"]
        assert nbr["sample_size"] > 0
        assert len(nbr["sizes"]) == nbr["sample_size"]
        assert nbr["min_operators"] >= 0
        assert nbr["max_operators"] >= nbr["min_operators"]

        # connected-component stats emitted and consistent
        assert stats["n_components"] >= 1
        assert sum(stats["component_sizes"]) == (
            stats["n_claims"] + stats["n_operators"]
        ), "component sizes must cover every Point exactly once"
        assert len(stats["component_sizes"]) == stats["n_components"]

        # script-side invariant checks all PASS (matches exit 0)
        assert report["invariants"]["all_pass"] is True
        assert len(report["invariants"]["checks"]) >= 8

    def test_f5_run_is_deterministic(self):
        """Two runs over identical F5 fixtures produce identical metrics."""
        a = json.loads(_run_diagnostics().stdout)
        b = json.loads(_run_diagnostics().stdout)
        assert a["stats"] == b["stats"]

    def test_decision_record_exists_and_contains_decision(self):
        """The human decision gate is recorded in epic docs (DE2E-10): the
        record file must exist and carry an explicit recorded decision."""
        assert _DECISION_RECORD.is_file(), (
            f"decision record missing: {_DECISION_RECORD}"
        )
        text = _DECISION_RECORD.read_text(encoding="utf-8")
        assert "## Decision Rule" in text, "decision rule section missing"
        assert "**Decision:" in text  # human gate — assert the marker exists, never the value (DE2E-10), (
            "an explicit recorded decision (FULL) is missing from the record"
        )
        # the production-snapshot confirmation is marked as an open TO-DO
        assert "TO-DO" in text
