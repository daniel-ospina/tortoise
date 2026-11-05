"""#10892 — a client hang-up must not build a Sentry event.

The 2026-11-04 outage: every aborted request (a health probe timing out, a closed
tab, a dropped fetch) reached ``_unhandled_exception_handler`` as an "unhandled"
exception, was logged at ERROR and captured to Sentry. Building that event reads
and decodes the source file of EVERY frame (``sentry_sdk``'s
``get_source_context``) in the logging thread — on the hosted API, the asyncio
event loop, GIL held. Under a probe storm the loop never returned to serving:
measured on ``tortoise-y4mjjq``, three ``py-spy`` dumps in a row parked the
MainThread in that stack, ``loop_age_ms`` reached 43 444, every request hung, and
the machine exited 1 and restarted.

Two independent guards are pinned here, because either one alone is a single
point of failure:

1. ``tortoise/sentry.py::init`` passes ``include_source_context=False`` — the
   per-event cost (sentry-python#8089) is removed for EVERY exception, not just
   the benign ones.
2. ``tortoise/sentry.py::capture_exception`` refuses a benign exception by name,
   and ``hosted_api._unhandled_exception_handler`` short-circuits it before the
   ERROR log — so the storm's fuel is gone even if a future handler forgets.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from types import SimpleNamespace

import pytest

import tortoise.sentry as tsentry


# A stand-in for starlette.requests.ClientDisconnect: the guard matches by NAME
# (tortoise/sentry.py deliberately does not import starlette), so the real class'
# identity is not what is under test — the name is.
class ClientDisconnect(Exception):
    pass


class _OtherError(Exception):
    pass


@pytest.fixture()
def enabled_sentry(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Inject a fake sentry_sdk and activate tortoise.sentry with a DSN."""
    calls: dict[str, list] = {"init": [], "exc": [], "msg": []}
    mod = SimpleNamespace(
        init=lambda **kw: calls["init"].append(kw),
        push_scope=lambda: __import__("contextlib").nullcontext(
            SimpleNamespace(set_tag=lambda *a, **k: None)
        ),
        capture_exception=lambda e: calls["exc"].append(e),
        capture_message=lambda m, level="info": calls["msg"].append((m, level)),
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", mod)
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.delenv("SENTRY_ENV", raising=False)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    importlib.reload(tsentry)
    assert tsentry.init() is True
    assert len(calls["init"]) == 1
    return calls


def test_init_turns_source_context_off(enabled_sentry: dict[str, list]) -> None:
    """The one option that removes the per-event source-file read (sentry-python#8089)."""
    kwargs = enabled_sentry["init"][0]
    assert kwargs.get("include_source_context") is False, (
        "sentry init must pass include_source_context=False — with the default "
        "(True) every captured event re-reads and tokenizes the source file of "
        "every stack frame on the calling thread, which on the hosted API is the "
        "asyncio event loop (#10892)"
    )
    # The #2863 credential fix must survive alongside it.
    assert kwargs.get("include_local_variables") is False


def test_capture_exception_refuses_a_client_disconnect(
    enabled_sentry: dict[str, list],
) -> None:
    tsentry.capture_exception(ClientDisconnect())
    assert enabled_sentry["exc"] == [], (
        "a client disconnect is a client-side event, not an application error — "
        "capturing it is what built an event per aborted request (#10892)"
    )


def test_capture_exception_still_reports_a_real_error(
    enabled_sentry: dict[str, list],
) -> None:
    """Non-vacuity: the refusal must be narrow, not a blanket mute."""
    error = _OtherError("boom")
    tsentry.capture_exception(error)
    assert enabled_sentry["exc"] == [error]


def test_benign_matching_survives_a_rewrapped_subclass() -> None:
    """uvicorn/starlette re-raise through wrappers; the name is what we match."""

    class _Rewrapped(ClientDisconnect):
        pass

    assert tsentry.is_benign_exception(_Rewrapped()) is True
    assert tsentry.is_benign_exception(_OtherError()) is False


def test_unhandled_handler_logs_a_disconnect_below_error_and_captures_nothing(
    enabled_sentry: dict[str, list], caplog: pytest.LogCaptureFixture
) -> None:
    """The end-to-end shape of the storm: handler in, no ERROR, no event out."""
    import tortoise.hosted_api as hapi

    captured: list = []
    monkey_import = hapi  # keep the reference readable
    monkey_import  # noqa: B018 - documentation of what is patched below
    import tortoise.sentry as sentry_mod

    real_capture = sentry_mod.capture_exception
    sentry_mod.capture_exception = lambda exc, tags=None: captured.append(exc)  # type: ignore[assignment]
    try:
        request = SimpleNamespace(
            url=SimpleNamespace(path="/health"),
            method="GET",
            headers={},
        )
        caplog.set_level(logging.DEBUG, logger="tortoise.api")
        response = asyncio.run(
            hapi._unhandled_exception_handler(request, ClientDisconnect())
        )
    finally:
        sentry_mod.capture_exception = real_capture  # type: ignore[assignment]

    assert captured == [], (
        "the handler must not route a client disconnect into Sentry — that path "
        "built a full event (with a per-frame source read) for every aborted "
        "request and starved the event loop (#11092)"
    )
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records == [], (
        "a client disconnect must not be logged at ERROR — the ERROR level is "
        "what feeds the Sentry logging integration"
    )
    # Review round 1 (P3): the response SHAPE is deliberately unchanged. A 204
    # for a request whose body never arrived would tell a slow-but-alive caller
    # "succeeded" for work that did not happen; the 500 tells it the truth, and
    # the status code was never part of the storm.
    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") is not None, (
        "#1591: the CORS headers must be on the response whatever the exception"
    )


def test_unhandled_handler_still_reports_a_real_error_at_error_level(
    enabled_sentry: dict[str, list], caplog: pytest.LogCaptureFixture
) -> None:
    """Non-vacuity: the benign short-circuit must not swallow genuine failures."""
    import tortoise.hosted_api as hapi
    import tortoise.sentry as sentry_mod

    captured: list = []
    real_capture = sentry_mod.capture_exception
    sentry_mod.capture_exception = lambda exc, tags=None: captured.append(exc)  # type: ignore[assignment]
    try:
        request = SimpleNamespace(
            url=SimpleNamespace(path="/v1/state"),
            method="GET",
            headers={},
        )
        caplog.set_level(logging.DEBUG, logger="tortoise.api")
        response = asyncio.run(
            hapi._unhandled_exception_handler(request, _OtherError("boom"))
        )
    finally:
        sentry_mod.capture_exception = real_capture  # type: ignore[assignment]

    assert [type(e) for e in captured] == [_OtherError]
    assert any(r.levelno >= logging.ERROR for r in caplog.records), (
        "a real unhandled exception must still log at ERROR"
    )
    assert response.status_code == 500


# ── The assertion that catches the P1 review round 1 found ──────────────────
#
# `ServerErrorMiddleware` ALWAYS re-raises after the app's exception handler
# returns, and Sentry's Starlette integration captures that re-raised exception
# itself through `capture_event` — NOT through `tortoise.sentry.capture_exception`.
# A test that only patches our helper therefore stays green while a real event is
# built per aborted request (measured before the fix: 2 events per abort; after
# the handler change alone: 1). This test drives a REAL client with a capturing
# transport through a REAL `ServerErrorMiddleware` and asserts on what actually
# reaches Sentry.

sentry_sdk = pytest.importorskip("sentry_sdk")


class _CaptureTransport(sentry_sdk.transport.Transport):
    def __init__(self, options=None) -> None:
        super().__init__(options)
        self.events: list = []

    def capture_envelope(self, envelope) -> None:
        # Only EVENT items are errors. A flush also carries a `sessions`
        # envelope in every scenario — including the benign controls — so
        # counting every item would put a background-flush false red inside a
        # window that has no instance today (review round 2, P3).
        for item in envelope.items:
            if item.headers.get("type") == "event":
                self.events.append(item)


@pytest.fixture()
def live_sentry_client(monkeypatch: pytest.MonkeyPatch):
    """A REAL sentry_sdk client wired through OUR init, capturing to memory."""
    transport = _CaptureTransport()
    real_init = sentry_sdk.init  # captured BEFORE the patch or the wrapper recurses

    def _init_with_transport(**kwargs):
        kwargs["transport"] = transport
        return real_init(**kwargs)

    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.delenv("SENTRY_ENV", raising=False)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    monkeypatch.setattr(sentry_sdk, "init", _init_with_transport)
    importlib.reload(tsentry)
    try:
        assert tsentry.init() is True
        yield transport
    finally:
        sentry_sdk.Hub.current.bind_client(None)
        importlib.reload(tsentry)


def _disconnect_app(routes):
    """A Starlette app whose ServerErrorMiddleware uses the real handler."""
    from starlette.applications import Starlette
    from starlette.middleware.errors import ServerErrorMiddleware

    app = Starlette(routes=routes)
    app.add_middleware(ServerErrorMiddleware, handler=_real_handler())
    return app


def _real_handler():
    import tortoise.hosted_api as hapi

    return hapi._unhandled_exception_handler


def test_a_disconnect_delivers_no_event_to_a_real_sentry_client(live_sentry_client) -> None:
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def boom(request):
        raise ClientDisconnect()

    app = _disconnect_app([Route("/boom", boom)])
    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500, "the 500 shape is deliberately unchanged"
    assert live_sentry_client.events == [], (
        "NOTHING may reach Sentry for a client disconnect. If this fails, the "
        "re-raise path is back: ServerErrorMiddleware re-raises and the Starlette "
        "integration captures it via capture_event, so `ignore_errors` in "
        "tortoise/sentry.py::init (and not the helper in this module) is what "
        "guards it (#11092, review round 1 P1)"
    )


def test_a_real_error_does_reach_a_real_sentry_client(live_sentry_client) -> None:
    """Non-vacuity for the test above: the transport is wired and it does fire."""
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def boom(request):
        raise _OtherError("boom")

    app = _disconnect_app([Route("/boom", boom)])
    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert live_sentry_client.events, (
        "a genuine unhandled exception must still be reported — a filter that "
        "swallows everything would pass the test above vacuously"
    )
