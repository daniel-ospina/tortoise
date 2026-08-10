"""Tortoise configuration — canonical embedded DB path resolution.

Child 2 (issue #176): unify all embedded connection points on ONE canonical
path so redislite's native path-keyed reuse works (one redis-server per
machine instead of one per connection/path).

Env precedence (plan Task 6):
  1. TORTOISE_DB_URI with a supported scheme (docker://, redis://, rediss://)
     -> URI mode (resolve_db_path() is NEVER called — the caller handles
     the URI separately; see SUPPORTED_URI_SCHEMES)
  2. TORTOISE_DB_PATH env -> file path (canonical for embedded)
  3. TORTOISE_DB_URI without a supported scheme -> treated as a file path
     (backward compat)
  4. default ~/.tortoise/tortoise.db

When both a non-URI TORTOISE_DB_URI value and PATH are set, PATH wins with
an explicit warning.
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


# Canonical set of supported TORTOISE_DB_URI schemes — the single source of
# truth for URI-vs-path routing. docker:// (local instance), redis:// /
# rediss:// (FalkorDB Cloud / managed instances). Keep in sync with
# projection._validate_uri_scheme, which REUSES this tuple so the routing
# checks and the connection layer cannot drift (#715: rediss:// was
# documented-supported but _resolve_db_target only recognized docker://).
SUPPORTED_URI_SCHEMES = ("docker", "redis", "rediss")


def is_db_uri(uri: str | None) -> bool:
    """True if a value is a supported connection URI (docker://, redis://,
    rediss://) rather than a file path."""
    if not uri:
        return False
    scheme = uri.split("://", 1)[0]
    return scheme in SUPPORTED_URI_SCHEMES


def resolve_db_path(explicit: str | None = None) -> str:
    """Resolve the canonical embedded DB path with explicit precedence.

    Args:
        explicit: caller-provided path (e.g. from CLI --db). Wins if present.

    Returns an absolute path string. Raises nothing — falls through to the
    default on empty/whitespace env values.
    """
    if explicit:
        # #715 P2 conf 75: a supported URI is NOT a path — resolving it as
        # one mangles the string into a garbage "path" and the caller
        # silently misses the real target. Route URIs through
        # FalkorProjection.from_uri() instead (this pre-check keeps the
        # failure loud, never silent).
        if is_db_uri(explicit):
            raise ValueError(
                f"{explicit.split('://', 1)[0]}:// DB URI passed to "
                f"resolve_db_path — route it through "
                f"FalkorProjection.from_uri() instead")
        return _abs(explicit)

    # 2. TORTOISE_DB_PATH env -> file path
    env_path = os.environ.get("TORTOISE_DB_PATH")
    if env_path is not None and env_path.strip():
        return _abs(env_path.strip())
    if env_path is not None and not env_path.strip():
        logger.warning(
            "TORTOISE_DB_PATH is empty/whitespace — using default %s",
            DEFAULT_DB_PATH)

    # 3. TORTOISE_DB_URI without a supported URI scheme -> treated as file
    # path (backward compat). Supported schemes (docker://, redis://,
    # rediss://) are handled by the caller via is_db_uri / from_uri — they
    # fall through to the default here, mirroring the docker:// behavior.
    uri = os.environ.get("TORTOISE_DB_URI")
    if uri and not is_db_uri(uri):
        # Reject relative paths BEFORE _abs() normalizes them — otherwise
        # FalkorProjection's hard-reject is defeated (plan Task 7).
        expanded = os.path.expanduser(uri)
        if not os.path.isabs(expanded):
            raise ValueError(RELATIVE_PATH_ERROR.format(path=uri))
        logger.warning(
            "TORTOISE_DB_URI=%r is a file path (no supported scheme: %s) — "
            "treating as embedded DB path (backward compat)",
            uri, ", ".join(f"{s}://" for s in SUPPORTED_URI_SCHEMES))
        return _abs(uri)

    # 4. default
    return _abs(DEFAULT_DB_PATH)


def is_docker_uri(uri: str | None) -> bool:
    """True if a TORTOISE_DB_URI value is a docker:// connection string.

    Legacy docker-only check — kept for callers with docker-specific
    semantics (migrate_kinds, ingest pre-checks). New URI-routing code
    should use is_db_uri() so redis:// and rediss:// are recognized too.
    """
    return bool(uri and uri.startswith("docker://"))


def _abs(path: str) -> str:
    """Expand ~ and make absolute.

    REJECTS relative paths (plan Task 7): a relative path like 'tortoise.db'
    resolves per-CWD and silently creates a per-directory redislite server
    (Category-3 leak). This guard in the single choke-point covers ALL
    branches (explicit arg, TORTOISE_DB_PATH, TORTOISE_DB_URI) uniformly.

    Exceptions: ``:memory:`` (redislite in-memory server, not a file path)
    passes through untouched.
    """
    if path == ":memory:":
        return path
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        raise ValueError(RELATIVE_PATH_ERROR.format(path=path))
    return os.path.abspath(expanded)


def canonical_path_exists() -> bool:
    """True if the canonical embedded DB file already exists."""
    return Path(resolve_db_path()).exists()
