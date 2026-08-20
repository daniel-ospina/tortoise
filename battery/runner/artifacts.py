"""Artifact writers — run_artifact.json v1.0 + summary.json v1.0 (S7).

Schemas pinned in the plan (Task 6 Schema note); every write is
schema-validated by the schema tests. Timestamps are recorded but never
influence metrics. run_id = seed+arm+scenario (random-free).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from battery.enums import ModelCallOutcome

SCHEMA_VERSION = "1.0"

_ARTIFACT_KEYS = (
    "schema_version", "run_id", "seed", "arm", "scenario_id", "tier", "model",
    "determinism", "episode_trace", "metric_values", "model_call_outcomes",
    "ep_outcome", "isolation_breach", "excluded", "setup", "timestamps",
    "provenance",
)
_SUMMARY_KEYS = ("schema_version", "arms", "run", "timestamps")


def run_id(seed: int, arm: str, scenario_id: str) -> str:
    """Random-free run identity (seed+arm+scenario)."""
    return f"{seed}-{arm}-{scenario_id}"


def build_run_artifact(
    *, seed: int, arm: str, scenario, episode, metric_values: dict[str, float],
    outcomes: dict[str, int], ep_outcome: str, excluded: dict[str, Any],
    setup_info: dict[str, Any], provenance: dict[str, Any],
    python_hash_seed: str, isolation_breach: bool = False,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a schema-v1.0 run artifact (all top-level keys present)."""
    now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id(seed, arm, scenario.id),
        "seed": seed,
        "arm": arm,
        "scenario_id": scenario.id,
        "tier": scenario.tier.value,
        "model": model or {"provider": "mock", "model_id": "mock-agent",
                           "temperature": 0.0},
        "determinism": {"seed": seed, "execution_order": "sequential",
                        "python_hash_seed": python_hash_seed},
        "episode_trace": episode.to_artifact_trace(),
        "metric_values": metric_values,
        "model_call_outcomes": outcomes,
        "ep_outcome": ep_outcome,
        "isolation_breach": isolation_breach,
        "excluded": excluded,
        "setup": setup_info,
        "timestamps": {"written_utc": now},
        "provenance": provenance,
    }


def write_run_artifact(attempt_dir: Path, artifact: dict[str, Any]) -> Path:
    attempt_dir.mkdir(parents=True, exist_ok=True)
    path = attempt_dir / f"{artifact['run_id']}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(artifact, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def build_summary(*, arms: list[dict[str, Any]], exit_code: int,
                  run_ids: list[str], artifacts: list[str], seed: int,
                  timestamps: dict[str, str]) -> dict[str, Any]:
    """Assemble a schema-v1.0 run summary (per-arm + run-level)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "arms": arms,
        "run": {"exit_code": exit_code, "run_ids": run_ids,
                "artifacts": artifacts, "seed": seed},
        "timestamps": timestamps,
    }


def write_summary(attempt_dir: Path, summary: dict[str, Any]) -> Path:
    attempt_dir.mkdir(parents=True, exist_ok=True)
    path = attempt_dir / "summary.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def outcome_counts_dict(outcomes: list[ModelCallOutcome]) -> dict[str, int]:
    return {o.value: outcomes.count(o) for o in ModelCallOutcome}


def validate_artifact_keys(artifact: dict[str, Any]) -> None:
    missing = [k for k in _ARTIFACT_KEYS if k not in artifact]
    if missing:
        raise ValueError(f"run_artifact missing keys: {missing}")


def validate_summary_keys(summary: dict[str, Any]) -> None:
    missing = [k for k in _SUMMARY_KEYS if k not in summary]
    if missing:
        raise ValueError(f"summary missing keys: {missing}")
