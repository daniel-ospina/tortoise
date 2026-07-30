"""Connector loader — reads config/connector_manifest.yaml, instantiates connectors."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any


def load_connectors(
    manifest_path: str | Path | None = None,
    api=None,
) -> dict[str, Any]:
    """Load and instantiate active connectors from manifest.

    Returns {connector_key: connector_instance}.
    """
    if manifest_path is None:
        manifest_path = Path(__file__).parent.parent / "config" / "connector_manifest.yaml"
    else:
        manifest_path = Path(manifest_path)

    if not manifest_path.exists():
        return {}

    import yaml
    with open(manifest_path) as f:
        data = yaml.safe_load(f) or {}

    connectors: dict[str, Any] = {}
    for key, cfg in (data.get("connectors") or {}).items():
        if not cfg.get("active", True):
            continue

        mod = importlib.import_module(cfg["module"])
        cls = getattr(mod, cfg["class"])
        instance = cls(config=cfg.get("config", {}), api=api)

        # Check for env-var config overrides
        repo_env = os.environ.get("GITHUB_REPO")
        if repo_env and hasattr(instance, "repo"):
            instance.repo = repo_env

        connectors[key] = instance

    return connectors
