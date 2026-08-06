"""Tortoise configuration — canonical embedded DB path resolution.

Child 2 (issue #176): unify all embedded connection points on ONE canonical
path so redislite's native path-keyed reuse works (one redis-server per
machine instead of one per connection/path).

Env precedence (plan Task 6):
  1. TORTOISE_DB_URI with `docker://` prefix -> URI mode (resolve_db_path()
     is NEVER called — the caller handles docker separately)
  2. TORTOISE_DB_PATH env -> file path (canonical for embedded)
  3. TORTOISE_DB_URI without `docker://` -> treated as a file path
     (backward compat)
  4. default ~/.tortoise/tortoise.db

When both a non-docker URI and PATH are set, PATH wins with a logged warning.
Empty/whitespace TORTOISE_DB_PATH falls through to the default (never passes
"" to FalkorProjection).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(os.path.expanduser("~"), ".tortoise", "tortoise.db")

# Shared error message for relative-path rejection (plan Task 7). All call
# sites (FalkorProjection hard-reject, ingest.py pre-check, mcp_server error
# surface) reference this constant so they cannot drift.
RELATIVE_PATH_ERROR = (
    "Relative DB path {path!r} rejected. Use (1) the canonical path "
    "(no-arg FalkorProjection() or TORTOISE_DB_PATH), (2) an absolute path, "
    "or (3) allow_nonstandard_path=True (env TORTOISE_ALLOW_NONSTANDARD_PATH=1) "
    "for absolute non-canonical paths. Relative paths are never permitted."
)


def resolve_db_path(explicit: str | None = None) -> str:
    """Resolve the canonical embedded DB path with explicit precedence.

    Args:
        explicit: caller-provided path (e.g. from CLI --db). Wins if present.

    Returns an absolute path string. Raises nothing — falls through to the
    default on empty/whitespace env values.
    """
    if explicit:
        return _abs(explicit)

    # 2. TORTOISE_DB_PATH env -> file path
    env_path = os.environ.get("TORTOISE_DB_PATH")
    if env_path is not None and env_path.strip():
        return _abs(env_path.strip())
    if env_path is not None and not env_path.strip():
        logger.warning(
            "TORTOISE_DB_PATH is empty/whitespace — using default %s",
            DEFAULT_DB_PATH)

    # 3. TORTOISE_DB_URI without docker:// -> treated as file path (backward compat)
    uri = os.environ.get("TORTOISE_DB_URI")
    if uri and not uri.startswith("docker://"):
        logger.warning(
            "TORTOISE_DB_URI=%r is a file path (not docker://) — treating as "
            "embedded DB path (backward compat)", uri)
        return _abs(uri)

    # 4. default
    return _abs(DEFAULT_DB_PATH)


def is_docker_uri(uri: str | None) -> bool:
    """True if a TORTOISE_DB_URI value is a docker:// connection string."""
    return bool(uri and uri.startswith("docker://"))


def _abs(path: str) -> str:
    """Expand ~ and make absolute (never returns a relative path)."""
    expanded = os.path.expanduser(path)
    return os.path.abspath(expanded)


def canonical_path_exists() -> bool:
    """True if the canonical embedded DB file already exists."""
    return Path(resolve_db_path()).exists()
