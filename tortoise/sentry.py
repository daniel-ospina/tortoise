"""Optional Sentry integration for the hosted API (#2444).

Sentry is a HARD dependency (declared in pyproject) but is only ACTIVATED
when the SENTRY_DSN environment variable is set — local SDK use, self-hosted
deploys, and tests never initialize it (zero behavioral change without a DSN).

All capture helpers no-op when Sentry is disabled, so call sites can be added
without ceremony. Hosted-only concern: nothing in the local SDK path should
ever import this module.
"""

from __future__ import annotations

import logging
import os
from typing import Any

_log = logging.getLogger("tortoise.sentry")

_enabled = False
_service = "tortoise-hosted-api"

# Exceptions that are NOT application errors and must never become Sentry events.
# #11092: a peer hanging up is routine (a health probe timing out, a browser tab
# closed, a dropped fetch). Routing it through the unhandled-exception handler
# built a full event — including the per-frame source read that
# `include_source_context=False` now removes — for EVERY disconnect, and under a
# probe storm that starved the event loop into a customer-visible outage.
#
# ⛔ THIS IS THE ONLY COPY OF THE SET. `init()` derives its `ignore_errors` from
# it (review round 3, P2): the two used to be written out separately, which is
# one silent drift away from re-opening the storm — shrink this and the SDK
# delivers an event the helper already called benign; grow only the SDK list and
# the helper captures one the SDK drops. `test_ignore_errors_agrees_with_the_
# benign_names` pins them equal.
BENIGN_EXCEPTION_NAMES = frozenset(
    {"ClientDisconnect", "BrokenResourceError", "BrokenPipeError", "ConnectionResetError"}
)


def init() -> bool:
    """Initialize Sentry iff SENTRY_DSN is set. Returns whether enabled.

    Idempotent: a second call while already enabled returns True without
    re-initializing the SDK.
    """
    global _enabled
    if _enabled:
        return True
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        _enabled = False
        return False
    try:
        import sentry_sdk  # noqa: PLC0415, RUF100

        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("SENTRY_ENV", "production"),
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            send_default_pii=False,
            # #2863 security review: sentry-sdk defaults to attaching every frame's
            # LOCAL VARIABLES to the event. On the /oauth/token path those frames
            # hold plaintext `refresh_token` / `code_verifier` / `client_secret` and
            # the freshly minted `oat_`/`ort_` pair, so the default ships live
            # credentials to Sentry. The traceback itself is still sent.
            include_local_variables=False,
            # #10892: source context is ON by default, and building an event then
            # READS AND DECODES THE SOURCE FILE OF EVERY FRAME
            # (`sentry_sdk.utils.get_source_context` -> `SourceLoader.get_source`
            # -> `tokenize.detect_encoding`/`find_cookie`) in the thread that logged
            # the error — on the hosted API that is the asyncio event loop, holding
            # the GIL. Measured on tortoise-y4mjjq 2026-11-04: three `py-spy dump`s
            # in a row parked the MainThread in exactly that stack while every
            # request hung, `loop_age_ms` reached 43 444, and the machine exited 1
            # and restarted. The storm's fuel is a client hanging up (see
            # `is_benign_exception`), but source context is what makes each event
            # expensive; the vendor tracks the defect as sentry-python#8089 ("~2-3.7
            # ms per event … asyncio applications can experience event-loop blocking
            # during bursts of handled exceptions"). The traceback itself is still
            # sent.
            include_source_context=False,
            # #11092 review round 1 (P1): `ServerErrorMiddleware` ALWAYS re-raises
            # after our handler returns, and the Starlette integration (installed
            # by this same `init`) then captures that re-raised exception itself —
            # through `sentry_sdk.capture_event`, NOT through
            # `tortoise.sentry.capture_exception`, so the refusal there is not on
            # that path. Measured with a real client and an aborted POST: 2 events
            # per abort before, 1 after the handler change. `ignore_errors` is
            # evaluated in `Client._should_capture`, i.e. BEFORE
            # `_prepare_event`/`serialize`, which is where the remaining cost was
            # (measured `_capture_exception`: 3-7 ms -> 0.15-0.18 ms). The
            # integration builds the event before `capture_event`, so this saves
            # the tail and not the build — which is why
            # `include_source_context=False` above is still required.
            # Broken pipe / reset / anyio's broken-resource all mean the same
            # thing at this layer (the peer is gone) and none is an app fault.
            #
            # ⚠️ The SDK matches these by EXACT type name (`Client._is_ignored_error`),
            # not by MRO, so a SUBCLASS of ClientDisconnect is benign here and not
            # there — measured (review round 2, P3): a subclass delivered 1 event
            # while `is_benign_exception` said benign. Starlette raises the base
            # class itself, so nothing in this tree is in that gap; the names are
            # exact on purpose. Round 3 (P3) made the HELPER exact by name too: its
            # MRO walk was the BROADER half, and broader here means silencing real
            # faults — `http.client.RemoteDisconnected` ("the upstream we called
            # reset the connection") SUBCLASSES `ConnectionResetError`, so it was
            # classified benign and never captured. The `isinstance` arm in
            # `is_benign_exception` still covers genuine subclasses of starlette's
            # `ClientDisconnect`, which is the re-wrap case the MRO walk was for.
            #
            # ⛔ DERIVED, never re-typed — one source of truth (round 3, P2).
            ignore_errors=sorted(BENIGN_EXCEPTION_NAMES),
        )
        _enabled = True
        _log.info("sentry enabled for %s", _service)
        return True
    except Exception as e:  # noqa: BLE001, RUF100 — a Sentry init failure must never break the API
        _log.warning("sentry init failed — continuing without: %s", e)
        _enabled = False
        return False


def enabled() -> bool:
    return _enabled


def is_benign_exception(exc: BaseException) -> bool:
    """True for exception types that are client-side events, not server faults.

    Two checks, and the identity one is the load-bearing half:

    * `isinstance` against starlette's own `ClientDisconnect`, imported lazily so
      this module stays importable without starlette. This is the half that
      covers SUBCLASSES (a server that re-wraps or re-exports the class);
    * an EXACT type-name match against `BENIGN_EXCEPTION_NAMES`, which is the
      fallback for a class that cannot be `isinstance`d here because its library
      is absent, and which deliberately does NOT walk the MRO.

    ⛔ The name match is EXACT on purpose (review round 3, P3). Walking the MRO
    makes this helper broader than the SDK's own `ignore_errors` — and broader is
    the harmful direction: it silences real faults. `http.client.
    RemoteDisconnected` is a `ConnectionResetError` subclass that means "the
    UPSTREAM reset the connection", so an MRO walk classified a dependency
    outage as a client hang-up and the handler logged it at DEBUG and captured
    nothing. Subclass coverage is the `isinstance` arm's job, not this one's.
    """
    try:
        from starlette.requests import ClientDisconnect as _ClientDisconnect

        if isinstance(exc, _ClientDisconnect):
            return True
    except Exception:  # noqa: BLE001, RUF100 — starlette absent is not a failure here
        pass
    return type(exc).__name__ in BENIGN_EXCEPTION_NAMES


def capture_exception(exc: BaseException, tags: dict[str, Any] | None = None) -> None:
    """Capture an exception with optional tags. No-op when disabled."""
    if not _enabled:
        return
    if is_benign_exception(exc):
        # Belt to the handler's braces (#11092): even if a caller forgets to
        # check, a peer that hung up never costs an event.
        return
    try:
        import sentry_sdk  # noqa: PLC0415, RUF100

        with sentry_sdk.push_scope() as scope:
            if tags:
                for k, v in tags.items():
                    scope.set_tag(str(k), str(v))
            sentry_sdk.capture_exception(exc)
    except Exception as e:  # noqa: BLE001, RUF100 — a capture failure must never reach the API
        _log.debug("sentry capture_exception failed: %s", e)


def capture_message(msg: str, level: str = "warning", tags: dict[str, Any] | None = None) -> None:
    """Capture a message at a level. No-op when disabled."""
    if not _enabled:
        return
    try:
        import sentry_sdk  # noqa: PLC0415, RUF100

        with sentry_sdk.push_scope() as scope:
            if tags:
                for k, v in tags.items():
                    scope.set_tag(str(k), str(v))
            sentry_sdk.capture_message(msg, level=level)
    except Exception as e:  # noqa: BLE001, RUF100 — a capture failure must never reach the API
        _log.debug("sentry capture_message failed: %s", e)
