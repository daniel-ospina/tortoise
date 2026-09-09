"""Session attribution helpers — machine_id and model derivation for CLI/MCP producers.

Mirrors the agent-infra capture-attribution.ts contract (PR #614, commit 173f504c):

- machine_id = sha256 hex of ``{hostname}\\0{username}`` — derive-only.
  Never-throw degradation: getpass.getuser() -> "unknown", socket.gethostname()
  -> "unknown-host". Memoized per process.
- model = runtime-only: the entry's initial model when available, else omitted.
- Sanitize: strip control chars, trim, cap machine_id <=256 / model <=128;
  empty -> None.
"""

from __future__ import annotations

import functools
import getpass
import hashlib
import re
import socket

__all__ = ["derive_machine_id", "sanitize_attribution_field"]


@functools.lru_cache(maxsize=1)
def derive_machine_id() -> str | None:
    """Derive the machine identifier: sha256 hex of ``{hostname}\\0{username}``.

    Never-throw: failures fall back to deterministic placeholders so that
    containers, CI, and sandboxed environments always produce a stable value.

    Returns a 64-char hex string, or ``None`` if both hostname and username
    fall back (defensive — the never-throw contract guarantees a value in
    practice).
    """
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "unknown-host"
    if not hostname or not isinstance(hostname, str):
        hostname = "unknown-host"

    try:
        username = getpass.getuser()
    except Exception:
        username = "unknown"
    if not username or not isinstance(username, str):
        username = "unknown"

    raw = f"{hostname}\0{username}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


# Control-character pattern for sanitisation (covers null byte through US).
_CTRL_CHAR_RE = re.compile(r"[\x00-\x1f]")


def sanitize_attribution_field(value: str | None, *, max_length: int = 256) -> str | None:
    """Sanitise a single attribution field per the capture-attribution contract.

    * Strip leading/trailing whitespace.
    * Remove control characters (``\\x00-\\x1f``).
    * Truncate to ``max_length``.
    * Return ``None`` if the result is empty.

    Mirrors ``sanitizeAttribution`` in capture-attribution.ts.
    """
    if value is None:
        return None
    s = value.strip()
    s = _CTRL_CHAR_RE.sub("", s)
    if not s:
        return None
    return s[:max_length]
