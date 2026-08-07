"""Connector loader — reads config/connector_manifest.yaml, instantiates connectors."""
from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Fields that should only be set via environment variables, never in YAML (#324)
_SECRET_FIELDS = {
    "token", "signing_secret", "api_key", "webhook_secret",
    "supabase_key", "supabase_service_key", "service_role_key",
}


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

        config = cfg.get("config", {})

        # Strip plaintext secrets from YAML config, warn if any found (#324)
        plaintext_found = []
        for field in _SECRET_FIELDS:
            if config.get(field) and config[field] != "":
                plaintext_found.append(field)
        if plaintext_found:
            logger.warning(
                "Connector '%s' has plaintext secrets in YAML config: %s. "
                "These will be ignored — use environment variables instead. "
                "See issue #324 for migration details.",
                key,
                ", ".join(plaintext_found),
            )
            # Strip the plaintext values so constructors must use env vars
            for field in plaintext_found:
                config[field] = ""

        mod = importlib.import_module(cfg["module"])
        cls = getattr(mod, cfg["class"])
        instance = cls(config=config, api=api)

        # Check for env-var config overrides
        repo_env = os.environ.get("GITHUB_REPO")
        if repo_env and hasattr(instance, "repo"):
            instance.repo = repo_env

        connectors[key] = instance

    return connectors
