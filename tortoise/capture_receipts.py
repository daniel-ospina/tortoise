"""Per-harness capture receipt / last-error STATE key names — ONE definition.

The dashboard's capture-status surface reads these server-written onboarding
state keys (never client state):

* ``session_capture_receipt_<harness>`` records a durable hosted 2xx capture
  under an authenticated agent credential, ATTRIBUTED to ``<harness>``. The
  attribution is the caller's declaration, not a server observation: on a
  fresh ``session_id`` it is ``body.harness`` (an agent self-report), and on a
  re-capture it is the Session's STORED harness when the Session has one (itself
  recorded from the declaration that first stamped it) or the current caller's
  ``body.harness`` when it does not (``stored or claimed``) — a caller
  declaration either way. No credential→harness binding exists (``tt_``/``tk_``
  keys carry no harness, `#3700`), so nothing here proves the server OBSERVED
  which harness captured. The bare ``session_capture_receipt`` is the
  harness-unproven key (legacy no-harness hooks, session-JWT captures).
* ``session_capture_last_error_<harness>`` carries the last non-2xx attempt's
  detail (the per-harness failure sub-line). Same conclusion, same resolution:
  every capture surface — the REST endpoint and the MCP
  ``tortoise_session_capture`` tool (``tortoise/mcp_server.py``) — resolves the
  harness with ``hosted_api._observed_capture_harness`` (stored or claimed)
  before writing the key (#3681 / #4898). That helper is the single resolver;
  its own docstring carries the credential and stored-harness rules.

The hosted API (``tortoise/hosted_api.py``) imports both keys from THIS module,
and the CLI's ``tortoise session verify`` (#3809) imports ``capture_receipt_key``
(it reads no last-error key). The hosted API still spells every per-harness key
name literally in its two onboarding default-state dicts
(``DEFAULT_ONBOARDING_STATE`` and ``_ONBOARDING_DEFAULT_STATE``) — that
enumeration is legitimate and must stay, because the dashboard needs a
REGISTERED row per harness even before any capture arrives and THIS module owns
only the key format, not the harness list. Those literals are pinned to these
formatters by ``tests/test_5051_capture_receipt_key_registry.py``, so changing a
key name here fails there instead of silently drifting the registry (an
unregistered key is dropped by the ``_update_onboarding_state`` allowlist
filter).

The dicts above are a legitimate TABLE (the dashboard needs a REGISTERED row
per harness). The ONE other literal spelling in the API is also a table, not a
derivation: ``_PATCH_FIELD_TO_STATE_KEY`` in ``tortoise/hosted_api.py``. It is
not a formatter call because it has to re-spell the harness itself — Pydantic
field names cannot carry hyphens, so the underscore JSON-field spelling maps to
the hyphenated STATE key, and the harness has to be transformed, not just
suffixed.

So the ``#5051`` rule is not "no literal key name anywhere in the API" — it is
"no literal key name at a site that CONSUMES the format". The two consumers
both DERIVE, and ``tests/test_5051_capture_receipt_key_registry.py`` pins every
one of the three literal tables to these formatters, so the safe direction —
change the format HERE and everything follows — holds rather than being merely
intended:

* the delete path's orphan sweep, ``_capture_receipt_buckets`` (derives — a
  stale spelling there makes ``state.get(key)`` always falsy, so an orphaned
  receipt is never cleared);
* the client-write-refusal set, ``_capture_server_owned_keys`` (derives — a
  stale spelling there leaves the live keys client-WRITABLE, re-opening the
  fabricated-receipt surface #3681 closed);
* the two onboarding default-state dicts and ``_PATCH_FIELD_TO_STATE_KEY``
  (literal tables, pinned to the formatters by the same test).

The one consumer that CANNOT import these formatters — the dashboard's JS
reader (``website/apps/dashboard/src/captureStatus.js``) — is pinned to their
prefix from the test instead.

```
#5051 — the SECOND contract this module owns: what a capture attempt MEANS
```

Keys are one half. The other half is the per-attempt OUTCOME, which used to be
re-derived at five seams from the wrong key and the wrong exit-code family
(``#5051``'s root). It is declared HERE, once, as a four-class vocabulary plus
the two predicates every consumer actually asks:

============  ==============================================================
``landed``    the commit is durable (2xx). Mint the receipt, retire the entry.
``retryable`` the MOMENT was wrong, not the bytes (no status, non-finite, 3xx,
              402, 408, 409, 425, 429, **every 5xx incl. 503/504**). Keep it.
``policy``    a REVERSIBLE authorization/entitlement state — 401, 403, 407.
              Resolved by changing credentials or org state, never by
              re-sending the same bytes. Keep it; NEVER terminalise it.
``permanent`` these bytes can never succeed (400, 404, 405, 406, 413, 415,
              422, any other 4xx). Terminalise — quarantine, never delete
              (``#7708``).
============  ==============================================================

The two predicates are RELATED but not derivable from each other, and both
facts are load-bearing:

* :func:`may_keep_entry` — the SPOOL's question, *"may this entry be retried
  again?"* — is ``capture_outcome(...) != "permanent"``. A ``policy`` refusal
  is kept: a suspended org is not a malformed payload (``#4895``), and on the
  ``sessions import`` path a 403 that is NOT kept is a capture that was never
  written locally at all.
* :func:`may_mint_local_receipt` — the RECEIPT gate's question, *"is this a
  refusal the user must be shown?"* — CONSUMES the class and adds ONE
  non-derivable set: it is true only for a ``retryable`` status that is NOT in
  :data:`REFUSAL_STATUSES`. That set is ``#4675``'s Task-15 acceptance line
  (*"a LOCAL receipt is written on a **2xx** (403/402/503 ⇒ exit 1, honest
  error, NO receipt) … that rule is correct and must not be weakened"*), and it
  is NOT a function of the class in either direction: ``402``/``503`` are
  ``retryable`` yet IN the set; ``429``/``500``/``502``/``504``/``None`` are
  ``retryable`` yet OUT of it (a ``504`` whose session landed must stay
  confirmable — ``#4675``'s own fix); ``403`` is ``policy`` and IN. Deriving the
  set from the class is the defect ``tests/test_session_confirm.py`` pins.

Because :func:`may_mint_local_receipt` reproduces ``status not in
{402,403,503} and classify_failure(status) == "retry"`` cell-for-cell, moving
the gates onto it is a RE-DECLARATION, not a behaviour change.
"""
from __future__ import annotations

import math
from typing import Literal

__all__ = [
    "CAPTURE_OUTCOMES",
    "POLICY_STATUSES",
    "REFUSAL_STATUSES",
    "CaptureOutcome",
    "capture_last_error_key",
    "capture_outcome",
    "capture_receipt_key",
    "may_keep_entry",
    "may_mint_local_receipt",
]

#: The declared outcome vocabulary. A closed set on purpose: a fifth class
#: would have to say what consequence it owns that these four do not.
CaptureOutcome = Literal["landed", "retryable", "policy", "permanent"]
CAPTURE_OUTCOMES: tuple[CaptureOutcome, ...] = (
    "landed", "retryable", "policy", "permanent")

#: A REVERSIBLE authorization/entitlement refusal: the credential or the org
#: state is wrong, the bytes are fine. Resolved by fixing the caller, so the
#: entry is KEPT (never terminalised) — ``#4895``.
POLICY_STATUSES = frozenset({401, 403, 407})

#: ``#4675``'s recorded local-receipt refusal set. Independent of the class by
#: decision, not by accident — see the module docstring.
REFUSAL_STATUSES = frozenset({402, 403, 503})


def capture_outcome(
    status: int | float | None, detail: str = "",
) -> CaptureOutcome:
    """What a capture ATTEMPT means — the ONE status→class table (``#5051``).

    Every leg of the capture path (the Python spool, the Pi/TS hook, the cursor
    drain and the shell seams) answers *"what may happen to this entry?"* from
    here, or from a declaration this table is asserted equal to by
    ``tests/test_capture_spool.py``'s parity test. Re-spelling the rule at a
    seam is the defect ``#5051`` exists to remove.

    TOTAL over every value a real caller can produce — ``None``, a boolean, a
    non-finite float, an int too large to convert (``10**400``), a string:
    everything that is not a usable finite number is ``retryable``. ``bool`` is
    checked FIRST because in Python a bool IS an int (``math.isfinite(True)`` is
    True), and letting one fall through would classify a corrupt status as
    ``permanent`` — i.e. terminalise a live capture.

    ``detail`` is accepted for signature symmetry with the TS leg and is
    deliberately NOT interpreted: keying on server prose would couple the
    client to a wording it ships independently of (``#4614`` gave the refusal a
    machine-readable CATEGORY instead, and that category is for REPORTING —
    treating a ``quota_exceeded`` 402 as terminal would re-open ``#4714``'s data
    loss).
    """
    if status is None or isinstance(status, bool):
        return "retryable"
    try:
        if not math.isfinite(status):
            return "retryable"
    except (TypeError, ValueError, OverflowError):
        # An unparseable status is not a verdict about the bytes.
        return "retryable"
    if 200 <= status < 300:
        return "landed"
    if 300 <= status < 400:
        # A redirect on a stored api_url must never destroy the only copy.
        return "retryable"
    if status >= 500:
        # 503 and 504 are the SAME class: a post-commit-timeout shape can be
        # retried and confirmed, so the two must not split (``#4925``).
        return "retryable"
    if status in POLICY_STATUSES:
        return "policy"
    if status in (402, 408, 425, 429):
        return "retryable"
    if status == 409:
        # On this idempotent upsert a 409 is either an in-flight concurrency
        # condition or a policy state the user can reverse.
        return "retryable"
    return "permanent"


def may_keep_entry(status: int | float | None, detail: str = "") -> bool:
    """The SPOOL's question: may this entry be retried again?

    ``True`` for every class except ``permanent`` — so a ``policy`` refusal
    (a suspended org's 403) is KEPT and retried once the caller fixes the
    credential or the org state, instead of being stranded terminal.
    """
    return capture_outcome(status, detail) != "permanent"


def may_mint_local_receipt(status: int | float | None, detail: str = "") -> bool:
    """The RECEIPT gate's question: may a local 2xx receipt be minted?

    ``#4675``'s recorded 2xx-only rule: a local receipt claims the session is
    durable, so it may only be written for a status that is ``retryable`` AND
    outside :data:`REFUSAL_STATUSES` — the family whose session can be CONFIRMED
    even though the POST did not answer 2xx (the post-commit-timeout shape).
    """
    return (capture_outcome(status, detail) == "retryable"
            and status not in REFUSAL_STATUSES)


def capture_receipt_key(harness: str | None) -> str:
    """Receipt state key for a harness — per-harness when present, the bare
    legacy key for no-harness hooks (T1-P3 None-guard)."""
    return f"session_capture_receipt_{harness}" if harness else \
        "session_capture_receipt"


def capture_last_error_key(harness: str | None) -> str | None:
    """Per-harness last-error state key. No bare variant is registered — a
    legacy no-harness hook has no per-harness dashboard row to read it."""
    if not harness:
        return None
    return f"session_capture_last_error_{harness}"
