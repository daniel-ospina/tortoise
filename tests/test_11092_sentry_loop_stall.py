"""#11092 — a client hang-up must not build a Sentry event.

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


# A stand-in for starlette.requests.ClientDisconnect: this module exercises the
# NAME arm (tortoise/sentry.py resolves starlette's class lazily), so the real
# class' identity is not what is under test here — the exact name is. A genuine
# starlette subclass is covered by the isinstance arm, pinned separately.
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
        "asyncio event loop (#11092)"
    )
    # The #2863 credential fix must survive alongside it.
    assert kwargs.get("include_local_variables") is False
    # REVIEW ROUND 3 (P2): the SDK-side list must be DERIVED from the helper's
    # constant, never re-typed. Two hand-written copies are one silent drift from
    # re-opening the storm — the handler would call a class benign while the SDK
    # still delivered an event for it (or the reverse), and every other test here
    # would stay green.
    #
    # ⛔ What this assertion holds is DERIVATION, and only derivation (review round
    # 5, P3). Editing `BENIGN_EXCEPTION_NAMES` alone does NOT redden it — `init()`
    # derives `ignore_errors` from that same constant, so both sides move together.
    # That is the property we want (one source of truth), not a hole in the pin.
    # The mutation that reddens it is re-typing a literal into `init()`.
    assert set(kwargs.get("ignore_errors") or ()) == set(tsentry.BENIGN_EXCEPTION_NAMES), (
        "init()'s ignore_errors and BENIGN_EXCEPTION_NAMES must be the SAME set — "
        "init derives it, so a difference means one of them was re-typed by hand"
    )
    # REVIEW ROUND 4 (P2): it must be the DIRECTION-UNAMBIGUOUS set. A global
    # `ignore_errors` entry is applied to every event from every path, so adding
    # `ConnectionResetError` here would silence a real upstream reset process-wide.
    assert "ConnectionResetError" not in (kwargs.get("ignore_errors") or ()), (
        "ignore_errors is CLIENT-GLOBAL — a direction-ambiguous name here blinds "
        "the process to upstream resets; scope it to the request layer instead"
    )
    assert "BrokenPipeError" not in (kwargs.get("ignore_errors") or ())


def test_capture_exception_refuses_a_client_disconnect(
    enabled_sentry: dict[str, list],
) -> None:
    tsentry.capture_exception(ClientDisconnect())
    assert enabled_sentry["exc"] == [], (
        "a client disconnect is a client-side event, not an application error — "
        "capturing it is what built an event per aborted request (#11092)"
    )


def test_capture_exception_still_reports_a_real_error(
    enabled_sentry: dict[str, list],
) -> None:
    """Non-vacuity: the refusal must be narrow, not a blanket mute."""
    error = _OtherError("boom")
    tsentry.capture_exception(error)
    assert enabled_sentry["exc"] == [error]


def test_capture_exception_uses_the_NARROW_set_not_the_request_layer_one(
    enabled_sentry: dict[str, list],
) -> None:
    """Round 5 (P3): the belt's direction scope was unpinned.

    `capture_exception` is the GLOBAL belt — it is what `oauth._log_and_capture`'s
    "single capture" for the token boundary goes through. If it were changed to
    pass `inbound_request=True`, an OUTBOUND reset would be swallowed there and
    the oauth boundary would log at WARNING and capture nothing. Pinned at the
    composite, not at the predicate.
    """
    error = ConnectionResetError("reset by peer (upstream)")
    tsentry.capture_exception(error)
    assert enabled_sentry["exc"] == [error], (
        "capture_exception must NOT treat a direction-ambiguous class as benign — "
        "this call is the belt behind every capture, including the oauth boundary"
    )


def test_benign_matching_survives_a_rewrapped_subclass() -> None:
    """A re-wrapped SUBclass of starlette's own ClientDisconnect must stay benign.

    ⚠️ This must use the REAL ``starlette.requests.ClientDisconnect``, not the
    module-level stand-in above: the stand-in exists to exercise the NAME arm,
    and a subclass of it matches nothing (its own name is not in the set, and
    dropping the MRO walk removed the walk that used to reach the stand-in's
    name). Subclass coverage is the ``isinstance`` arm's job, so the test has to
    hand it the class that arm compares against.
    """
    from starlette.requests import ClientDisconnect as RealClientDisconnect

    class _Rewrapped(RealClientDisconnect):
        pass

    assert tsentry.is_benign_exception(_Rewrapped()) is True
    assert tsentry.is_benign_exception(_OtherError()) is False


def test_benign_matching_is_exact_by_name_not_by_mro() -> None:
    """Review round 3 (P3): the name arm must NOT walk the MRO.

    A bare MRO walk makes this helper BROADER than the SDK's `ignore_errors`,
    and broadening here silences real faults. ``http.client.RemoteDisconnected``
    is the measured instance: it subclasses ``ConnectionResetError`` but means
    "the UPSTREAM we called reset the connection", so an MRO walk classified a
    dependency outage as a client hang-up — logged at DEBUG, captured nowhere.

    Subclass coverage is ``isinstance(exc, starlette.requests.ClientDisconnect)``'s
    job (see the test above); this one pins the fallback to exact names.
    """

    class _UpstreamHungUp(ConnectionResetError):
        pass

    assert tsentry.is_benign_exception(_UpstreamHungUp()) is False, (
        "a SUBCLASS of a benign name is not itself benign — that is how an "
        "upstream reset gets silenced as if the client had hung up"
    )
    # An EXACT name in the global set still matches, with no library import — the
    # point of the name arm. (BrokenPipeError/ConnectionResetError live in the
    # REQUEST-LAYER set only, pinned by the direction test below.)
    BrokenResourceError = type("BrokenResourceError", (Exception,), {})
    assert tsentry.is_benign_exception(BrokenResourceError()) is True

    # ⛔ ROUND 5 (P3): the fixture above is HARMLESS under an MRO walk, because
    # `ConnectionResetError` is no longer in the GLOBAL set. These two are the
    # ones that actually discriminate, and both were False-but-should-be-True
    # under a walk: `RemoteDisconnected`'s MRO contains `ConnectionResetError` and
    # a subclass of a globally-named class inherits that name in its MRO.
    import http.client

    assert tsentry.is_benign_exception(
        http.client.RemoteDisconnected(), inbound_request=True
    ) is False, (
        "the UPSTREAM-reset class must not be benign even at the request layer — "
        "an MRO walk would classify it benign and silence a dependency outage"
    )
    _SubOfGlobalName = type("_SubOfGlobalName", (BrokenResourceError,), {})
    assert tsentry.is_benign_exception(_SubOfGlobalName()) is False, (
        "a SUBCLASS of a globally-named class is not itself benign — the name arm "
        "is exact, and only the isinstance arm covers subclasses"
    )


def test_direction_ambiguous_names_are_benign_only_on_the_inbound_layer() -> None:
    """Review round 4 (P2): `ConnectionResetError` means two different things.

    Inbound (the handler): the CALLER reset on us → benign.
    Anywhere else (oauth token exchange, a connector): the UPSTREAM we called
    reset us → a real dependency outage that must be captured.

    So the name is benign ONLY with `inbound_request=True`, and it is NOT in the
    client-global set — `ignore_errors` is applied by `Client._should_capture` to
    every event from every path, so a global entry would blind the whole process
    to upstream resets (including `oauth._log_and_capture`'s "single capture").
    """
    for name, cls in (("ConnectionResetError", ConnectionResetError),
                      ("BrokenPipeError", BrokenPipeError)):
        assert name not in tsentry.BENIGN_EXCEPTION_NAMES, (
            f"{name} is direction-ambiguous and must not be a GLOBAL benign name"
        )
        assert tsentry.is_benign_exception(cls()) is False, (
            f"{name} on an OUTBOUND path means the upstream hung up — capturing "
            "it is the whole point"
        )
        assert tsentry.is_benign_exception(cls(), inbound_request=True) is True, (
            f"{name} while SERVING an inbound request means the caller hung up"
        )
    # The global set is a strict subset, so the request-layer set can never fall
    # behind it (a name added globally but not inbound would be a silent gap).
    assert tsentry.BENIGN_EXCEPTION_NAMES <= tsentry.REQUEST_LAYER_DISCONNECT_NAMES


def test_unhandled_handler_logs_a_disconnect_below_error_and_captures_nothing(
    enabled_sentry: dict[str, list], caplog: pytest.LogCaptureFixture
) -> None:
    """The end-to-end shape of the storm: handler in, no ERROR, no event out."""
    import tortoise.hosted_api as hapi

    captured: list = []
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
    # Review round 6 (P3): the DEBUG line is asserted POSITIVELY, not only as an
    # absence of ERROR. Absence-of-ERROR is equally true when nothing is logged
    # at all, so deleting the `.debug("client disconnected: …")` call in
    # `_unhandled_exception_handler` left all 15 tests green. This pin makes that
    # deletion red, so the line cannot be dropped silently.
    #
    # ⛔ WHAT THIS PIN DOES **NOT** CLAIM (review round 7, P2 — measured): that
    # this line is the production per-abort signal. It is not. The hosted runtime
    # applies uvicorn's `LOGGING_CONFIG`, which configures ONLY the `uvicorn*`
    # loggers and leaves root at WARNING (`Dockerfile.hosted:153` passes no
    # `--log-level`; nothing in `tortoise/` or `entrypoint.sh` sets a level for
    # `tortoise.*`), so `tortoise.api` is effectively WARNING in production and a
    # `.debug()` record is dropped there. The production per-abort signal is
    # uvicorn's own `Exception in ASGI application` ERROR record with traceback
    # (see the ⛔ block in `_unhandled_exception_handler`), which is not ours to
    # silence. So `caplog` observes this record only because the test RAISES the
    # level; the assertion pins the call, not a production emission.
    #
    # The level is asserted as `< ERROR` rather than `== DEBUG` because that is
    # the property this repo DEPENDS on: Sentry's logging integration captures at
    # `event_level=ERROR` (`LoggingIntegration` default, pinned SDK 2.68.1), so any
    # level below ERROR is guaranteed to build no event. A move to INFO keeps that
    # guarantee and must not red a test that never promised DEBUG specifically.
    assert any(
        r.levelno < logging.ERROR and "client disconnected" in r.getMessage()
        for r in caplog.records
    ), (
        "a benign disconnect must still emit one sub-ERROR record wherever the "
        "level is enabled — below the level Sentry turns into an event"
    )
    # Review round 1 (P3): the response SHAPE is deliberately unchanged. A 204
    # for a request whose body never arrived would tell a slow-but-alive caller
    # "succeeded" for work that did not happen; the 500 tells it the truth, and
    # the status code was never part of the storm.
    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") is not None, (
        "#1591: the CORS headers must be on the response whatever the exception"
    )


def test_unhandled_handler_treats_an_inbound_reset_as_a_disconnect(
    enabled_sentry: dict[str, list], caplog: pytest.LogCaptureFixture
) -> None:
    """Review round 4 (P2): the handler is the ONE place the layer is known.

    `ConnectionResetError` is direction-ambiguous, so it is benign only when the
    peer is the CALLER. This drives the handler with it and asserts the inbound
    wiring (`inbound_request=True`) is actually passed — without it, a reset
    storm on the send path would log at ERROR and capture per abort again.
    """
    import tortoise.hosted_api as hapi
    import tortoise.sentry as sentry_mod

    captured: list = []
    real_capture = sentry_mod.capture_exception
    sentry_mod.capture_exception = lambda exc, tags=None: captured.append(exc)  # type: ignore[assignment]
    try:
        request = SimpleNamespace(
            url=SimpleNamespace(path="/health"), method="GET", headers={}
        )
        caplog.set_level(logging.DEBUG, logger="tortoise.api")
        response = asyncio.run(
            hapi._unhandled_exception_handler(request, ConnectionResetError("reset"))
        )
    finally:
        sentry_mod.capture_exception = real_capture  # type: ignore[assignment]

    assert captured == [], (
        "an inbound ConnectionResetError is the caller hanging up — the handler "
        "passes inbound_request=True, so it must not be captured"
    )
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == [], (
        "and it must not be logged at ERROR — that is what feeds the Sentry "
        "logging integration"
    )
    # Positive half of the same pin (review round 6, P3): the inbound-reset
    # branch keeps the same single sub-ERROR signal, so its removal must red HERE
    # too — the two benign branches are reached by different exceptions. Same
    # `levelno < ERROR` rationale as the block above (review round 7, P2): the
    # property is "no event", not the literal token DEBUG.
    assert any(
        r.levelno < logging.ERROR and "client disconnected" in r.getMessage()
        for r in caplog.records
    ), (
        "an inbound reset is still a disconnect — it must log below ERROR, "
        "which is what keeps it out of Sentry's event stream"
    )
    assert response.status_code == 500


def test_a_broken_classifier_cannot_suppress_the_500(
    enabled_sentry: dict[str, list],
) -> None:
    """Round 5 (P3): the round-3 (d) 500-guarantee was unpinned.

    The classifier import/call sits inside a `try` that fails closed to
    `_benign = False`. Deleting that guard (making the call unguarded in an
    exception HANDLER) would produce NO response at all instead of the promised
    500 — and the suite stayed green without this test.
    """
    import tortoise.hosted_api as hapi
    import tortoise.sentry as sentry_mod

    real = sentry_mod.is_benign_exception
    sentry_mod.is_benign_exception = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("classifier exploded")
    )
    try:
        request = SimpleNamespace(
            url=SimpleNamespace(path="/v1/state"), method="GET", headers={}
        )
        response = asyncio.run(
            hapi._unhandled_exception_handler(request, _OtherError("boom"))
        )
    finally:
        sentry_mod.is_benign_exception = real

    assert response.status_code == 500, (
        "the handler MUST still answer 500 when classification fails — an "
        "exception escaping a handler produces no response at all"
    )
    assert response.headers.get("access-control-allow-origin") is not None, (
        "#1591 CORS headers must survive a classification failure too"
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


@pytest.fixture()
def real_sentry(monkeypatch: pytest.MonkeyPatch):
    """Init the REAL ``tortoise.sentry`` against the REAL pinned SDK, then restore.

    ``enabled_sentry`` above fakes ``sentry_sdk`` so this module's CALL SURFACE can
    be observed with nothing installed; this fixture instead lets it build a
    genuine client, which is the only way to ask the pinned SDK's own event
    builder what the configured value actually does.
    """
    previous_client = sentry_sdk.get_client()
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.delenv("SENTRY_ENV", raising=False)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    importlib.reload(tsentry)
    try:
        assert tsentry.init() is True
        yield sentry_sdk
    finally:
        sentry_sdk.get_global_scope().set_client(previous_client)
        # ``_enabled`` is module state, which monkeypatch does not unwind; reload it
        # back to OFF so no later test inherits an enabled module with a dead client.
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        importlib.reload(tsentry)


def _recorded_event(sdk, client) -> dict:
    """Capture one exception through ``client`` and return the event it built.

    The recording TRANSPORT is the observation point that matters: it sits after
    event construction and serialization, so it sees exactly what the product
    would send — ``before_send`` on the production client is not configurable, and
    ``client.options[...]`` is only an echo of the kwargs.
    """

    class _Recorder(sdk.transport.Transport):
        def __init__(self) -> None:
            super().__init__()
            self.events: list[dict] = []

        def capture_envelope(self, envelope) -> None:
            # Event items only — a flush also carries a `sessions` envelope.
            for item in envelope.items:
                if item.type == "event":
                    self.events.append(item.payload.json)

    recorder = _Recorder()
    previous_transport = client.transport
    client.transport = recorder
    try:
        try:
            raise _OtherError("boom")
        except _OtherError as exc:
            sdk.capture_exception(exc)
    finally:
        client.transport = previous_transport
    assert recorder.events, "the recording transport saw no event"
    return recorder.events[-1]


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


def test_an_upstream_connection_reset_still_reaches_a_real_sentry_client(
    live_sentry_client,
) -> None:
    """Review round 4 (P2) — the GLOBAL scope, driven through a REAL client.

    `ignore_errors` is applied by `Client._should_capture` to every event from
    every path, so it must not carry a direction-ambiguous name. Measured both
    ways here: a bare `ConnectionResetError` captured straight through the SDK —
    the shape an outbound call produces when the UPSTREAM resets us — must
    deliver an event, while `ClientDisconnect` (unambiguous: only a caller can
    produce it) must not. Putting `ConnectionResetError` back into
    `BENIGN_EXCEPTION_NAMES` reddens this test and proves the regression it
    guards: the oauth token boundary's "single capture" would go silent.
    """
    before = len(live_sentry_client.events)
    sentry_sdk.capture_exception(ConnectionResetError("reset by peer (upstream)"))
    sentry_sdk.get_client().flush(timeout=2.0)
    assert len(live_sentry_client.events) > before, (
        "an UPSTREAM ConnectionResetError must reach Sentry — a client-global "
        "ignore_errors entry for it blinds the process to dependency outages"
    )

    after_upstream = len(live_sentry_client.events)
    sentry_sdk.capture_exception(ClientDisconnect())
    sentry_sdk.get_client().flush(timeout=2.0)
    assert len(live_sentry_client.events) == after_upstream, (
        "a ClientDisconnect is unambiguously the CALLER leaving — it must still "
        "be filtered globally, otherwise the storm's fuel is back"
    )


def test_the_real_sdk_builds_an_event_with_no_source_context(real_sentry) -> None:
    """Guard 1 must be honoured by the INSTALLED SDK, not merely passed to it.

    ``test_init_turns_source_context_off`` pins a kwarg captured by the FAKE SDK,
    so a ``sentry-sdk`` bump that keeps the key accepted while no longer reading
    it would leave that pin green while the per-frame source read — the outage —
    came back. ``with_locals`` → ``include_local_variables`` is the precedent this
    very module already relies on for #2863, and it is the SAME major that removes
    both options (`sentry-sdk` 3.x drops ``include_source_context`` and
    ``include_local_variables`` for ``data_collection=``), while
    ``pyproject.toml`` declares ``sentry-sdk>=2.0`` with no upper bound. So assert
    on the BUILT event: the frames a real exception produces must carry no
    ``context_line``/``pre_context``.

    The control below is the falsifier: the same observation point on a client
    built with source context ON must SEE those keys, so a zero is a fact about
    the option and not about the recorder.
    """
    event = _recorded_event(real_sentry, real_sentry.get_client())
    frames = event["exception"]["values"][-1]["stacktrace"]["frames"]
    assert frames, "the event carries no frames — the pin below would be vacuous"
    assert not any("context_line" in f or "pre_context" in f for f in frames), (
        "the installed sentry-sdk still attached source context to an event frame: "
        "building that event re-reads and tokenizes the source file of every frame "
        "on the calling thread, which is the 2026-11-04 outage (#11092, "
        "sentry-python#8089)"
    )

    # Control: same recorder, same exception, source context switched BACK ON.
    previous_client = real_sentry.get_global_scope().get_client()
    real_sentry.init(
        dsn="https://public@example.invalid/1",
        include_source_context=True,
        default_integrations=False,
        integrations=[],
    )
    try:
        control = _recorded_event(real_sentry, real_sentry.get_client())
    finally:
        real_sentry.get_global_scope().set_client(previous_client)
    control_frames = control["exception"]["values"][-1]["stacktrace"]["frames"]
    assert any("context_line" in f for f in control_frames), (
        "falsifier: with include_source_context=True the recorder must SEE "
        "context_line, otherwise the assertion above measures the recorder rather "
        "than the option"
    )
