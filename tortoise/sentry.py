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


def capture_exception(exc: BaseException, tags: dict[str, Any] | None = None) -> None:
    """Capture an exception with optional tags. No-op when disabled."""
    if not _enabled:
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
