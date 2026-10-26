"""Preview-then-confirm deletion (#9660, owner ruling 2026-10-25).

The owner approved a two-call deletion protocol: **the first call changes
nothing** and returns a receipt describing what would be destroyed plus a
short-lived token bound to that one target; **the second call carries the token
and performs the delete**. The reason is stated in the ruling — a bare
``confirm: true`` flag can be set by the assistant in the same breath as the
call, which puts nothing between a misread instruction and a destroyed claim.

Design constraints that shaped this module:

* **Stateless.** The token is a signed payload, so the hosted service holds no
  server-side session for a pending deletion (a fleet of workers would otherwise
  need shared state, and an idle pending deletion would need a reaper).
* **Bound to one target.** The payload names the id (and the node kind, so a
  receipt cannot be replayed against an id that later resolves to something
  else) and the token is verified against the id the caller is deleting NOW.
* **Short-lived and single-use by construction.** It expires (default ten
  minutes) and, because it names a target that no longer exists after a
  successful delete, a replay finds nothing to destroy.
* **No new secret to operate.** The signing key is derived from an existing
  secret when one is configured, and falls back to a per-process random key.
  The fallback is correct rather than a stopgap: a token only has to outlive one
  user's confirmation, and a process that restarts mid-confirmation should make
  the user confirm again (a silent cross-restart ability to delete is the thing
  we are trying to avoid, not a feature to preserve).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

#: How long a receipt stays valid. Ten minutes is long enough for a person to
#: read a summary and answer, and short enough that a token left in a
#: conversation is worthless by the time anyone scrolls back to it.
DEFAULT_TTL_SECONDS = 600

#: The exact warning a caller shows (and every MCP client displays). Deliberately
#: short: it is read at the moment of a destructive action, not browsed.
WARNING = (
    "Deletes permanently — cannot be undone. The first call previews what would "
    "be destroyed and returns a confirmation token; call again with it to delete. "
    "When the item is wrong rather than unwanted, supersede it or retract it instead."
)

#: Named capability for a future permission layer (#9660). Inert today: nothing
#: enforces it, and nothing should until permission management exists. It exists
#: so that "deletion requires an administrator" has a stable key to attach to,
#: instead of a future policy inferring intent from a tool's name.
CAPABILITY = "knowledge:delete"

_SECRET_ENV_VARS = ("TORTOISE_DELETE_CONFIRM_SECRET", "TORTOISE_SECRET_PEPPER")
_process_secret: bytes | None = None


class DeletionConfirmationError(ValueError):
    """Base for a refused confirmation. Every subclass is a REFUSAL, never a delete."""


class ConfirmationInvalid(DeletionConfirmationError):
    """The token is malformed, forged, or was issued for a different target."""


class ConfirmationExpired(DeletionConfirmationError):
    """The token is well-formed but past its expiry — ask for a fresh preview."""


def _secret() -> bytes:
    """The signing key: a configured secret if there is one, else per-process.

    The env fallback chain is deliberate — an operator who has already configured
    a service secret should not have to configure a second one for this.
    """
    for name in _SECRET_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return hashlib.sha256(b"delete-confirm\0" + value.encode("utf-8")).digest()
    global _process_secret
    if _process_secret is None:
        _process_secret = secrets.token_bytes(32)
    return _process_secret


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: bytes) -> str:
    return _b64(hmac.new(_secret(), payload, hashlib.sha256).digest())


def issue(
    target: str,
    *,
    kind: str = "",
    actor: str = "",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Issue a confirmation token for ONE target.

    ``actor`` binds the token to the caller when the surface knows one (the
    hosted API does). An empty actor means "any caller holding this token", which
    is what a single-user local SDK session implies.
    """
    if not target:
        raise ConfirmationInvalid("a deletion confirmation needs the id it is for")
    payload: dict[str, Any] = {
        "t": target,
        "k": kind,
        "a": actor,
        "e": int((now if now is not None else time.time()) + ttl_seconds),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{_b64(raw)}.{_sign(raw)}"


def verify(
    token: str,
    *,
    target: str,
    actor: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Verify a token against the target being deleted NOW.

    Raises a :class:`DeletionConfirmationError` subclass on any mismatch. Returns
    the decoded payload on success so a caller can report what was confirmed.
    """
    if not token or not isinstance(token, str) or token.count(".") != 1:
        raise ConfirmationInvalid("not a deletion confirmation token")
    body, signature = token.split(".", 1)
    try:
        raw = _unb64(body)
    except (binascii.Error, ValueError) as exc:
        raise ConfirmationInvalid("malformed deletion confirmation token") from exc
    # Constant-time compare, then decode: a tampered payload must never be
    # interpreted, however harmless the field looks.
    if not hmac.compare_digest(_sign(raw), signature):
        raise ConfirmationInvalid("deletion confirmation token does not verify")
    try:
        payload = json.loads(raw)
    except ValueError as exc:  # pragma: no cover - unreachable through _sign
        raise ConfirmationInvalid("malformed deletion confirmation token") from exc
    if payload.get("t") != target:
        raise ConfirmationInvalid(
            "this token confirms a different item — ask for a fresh preview of the one you mean to delete")
    if payload.get("a") != actor:
        raise ConfirmationInvalid("this token was issued to a different caller")
    expiry = payload.get("e")
    current = now if now is not None else time.time()
    if not isinstance(expiry, (int, float)) or current > expiry:
        raise ConfirmationExpired("this confirmation has expired — ask for a fresh preview")
    return payload


def receipt(
    *,
    target: str,
    kind: str,
    title: str = "",
    edges: int = 0,
    token: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """The preview a caller shows a person before anything is destroyed.

    One readable line plus the machine-readable fields. ``deleted`` is False by
    construction: a preview is not a delete, and a client that reads only that
    field must never mistake planning for destruction.
    """
    what = f"{kind} {target}" if kind else target
    if title:
        what = f"{kind + ' ' if kind else ''}{title!r} ({target})"
    return {
        "deleted": False,
        "preview": True,
        "confirmation": token,
        "expires_in": ttl_seconds,
        "id": target,
        "kind": kind,
        "title": title,
        "edges": edges,
        "warning": WARNING,
        "summary": (
            f"About to delete {what}. "
            + (f"{edges} connection{'s' if edges != 1 else ''} to other items would go with it. " if edges else "")
            + "Nothing has changed yet."
        ),
    }
