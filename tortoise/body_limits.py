"""Streaming request-body size caps — shared by the hosted API app and the
mounted ``/mcp`` sub-app (#2029/#2032/#2048).

The streaming core (``read_capped_body``) was #2029's ``_read_capped_body`` in
``tortoise/hosted_api.py``; #2032 swept the explicit ``request.json()`` /
``request.body()`` sites through it. #2048 closes the residual class:

* endpoints declaring ``body: XxxRequest`` / ``body: dict`` — FastAPI reads
  those in ``get_request_handler`` (``await request.body()``) BEFORE any
  dependency or handler body runs, so the whole wire body is buffered upstream
  of where a per-site capped read could sit (see ``CappedBodyMiddleware``);
* the ``/mcp`` sub-app — its middleware stack is separate from the parent
  app's, and its ``RequestBodySizeMiddleware`` checked ``content-length`` only,
  so a chunked ``Transfer-Encoding`` body bypassed it.

The core lives here rather than in ``hosted_api.py`` because
``tortoise/mcp_auth.py`` must import it: ``mcp_auth`` is imported BY
``mcp_server`` (which ``hosted_api`` imports), so importing ``hosted_api`` from
``mcp_auth`` would be a cycle. This module imports only ``starlette`` and
``tortoise.capture_spool`` (stdlib-only — no cycle).
"""
from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import get_route_path

from tortoise.capture_spool import SPOOL_MAX_ENTRY_BYTES

#: Default cap for the small JSON/form surfaces (#2032), now also applied by
#: ``CappedBodyMiddleware`` to the pydantic/``dict``-body endpoints that
#: previously buffered unbounded before validation (#2048).
BODY_MAX_BYTES = 256 * 1024
BODY_413_DETAIL = f"request body exceeds the size cap ({BODY_MAX_BYTES // 1024} KiB)"

#: Session-content capture (``POST /v1/sessions``) carries ``conversation`` —
#: a list whose per-turn content is schema-UNBOUNDED on the wire (the handler
#: truncates each turn to the 5000-char stored window), so the small-surface
#: 256 KiB default would false-413 a legal capture. The capture hook's own
#: spool ceiling is 16 MiB, sized on the SERVER's legal maximum (MAX_TURNS=500
#: turns x TURN_MAX_CHARS=5000 chars, up to 4 UTF-8 bytes/char ≈ 10 MB of
#: JSON) plus envelope room (tortoise/pi-hooks/tortoise-capture.ts,
#: ``SPOOL_MAX_ENTRY_BYTES``). The server accepts at least the client's ceiling
#: for the same class #2032 sized at 8 MiB for commit_session.
#:
#: It is an ALIAS, not a second literal: the TypeScript capture contract states
#: the client spool ceiling "must exceed the SERVER's own legal maximum", so a
#: retune of ``SPOOL_MAX_ENTRY_BYTES`` that did not move this constant would
#: make the server 413 a capture the client believes is legal. The alias makes
#: the two move together (``tortoise/capture_spool.py`` is stdlib-only, so the
#: import is cycle-free). The worst-case admission arithmetic is pinned in
#: ``tests/test_body_cap_sweep.py::TestCaptureSessionCapOverride``.
CAPTURE_SESSION_MAX_BYTES = SPOOL_MAX_ENTRY_BYTES
CAPTURE_SESSION_413_DETAIL = (
    f"session request body exceeds the size cap "
    f"({CAPTURE_SESSION_MAX_BYTES // (1024 * 1024)} MiB)"
)


class BodyTooLargeError(Exception):
    """A streamed body exceeded its cap.

    ``detail`` is the caller's surface-specific message: the HTTP 413
    ``detail`` for the FastAPI app, the JSON-RPC error message for the
    ``/mcp`` sub-app.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


async def read_capped_body(request: Request, max_bytes: int, detail: str) -> bytes:
    """Read the raw request body under a HARD streaming cap.

    Content-Length alone is spoofable (a client can claim a small length and
    stream unbounded bytes) — the cap is enforced while draining the stream, so
    an oversized body is rejected before the ENTIRE body is buffered or any
    parse work runs. This is the #2029/#2032 streaming core; it raises
    ``BodyTooLargeError`` carrying ``detail`` (``hosted_api._read_capped_body``
    converts that to ``HTTPException(413)``).
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise BodyTooLargeError(detail)
        chunks.append(chunk)
    return b"".join(chunks)


class CappedBodyMiddleware(BaseHTTPMiddleware):
    """ASGI middleware: enforce a streaming cap on request bodies, replaying
    under-cap bodies downstream.

    WHY A MIDDLEWARE FOR THIS CLASS (#2048). A FastAPI ``body: XxxRequest`` /
    ``body: dict`` parameter is parsed in ``get_request_handler``
    (``await request.body()``) BEFORE any dependency or handler body runs, so
    those endpoints buffer the entire wire body upstream of where #2032's
    per-site ``_read_capped_body`` call could sit. Converting the signatures
    would reorder validation and reshape the 422s (explicitly out of scope), so
    the cap is applied one layer out — where the body is still a stream — and
    the under-cap bytes are replayed to the router.

    Cap resolution, in order:

    1. ``path_caps`` — exact-path override (e.g. ``/v1/sessions`` at 16 MiB);
    2. ``exempt_prefixes`` / ``exempt_regexes`` — pass through UNCAPTURED
       because the route's own handler (or the mounted sub-app) applies its own
       larger ``read_capped_body`` cap, and reading here would either truncate
       a legal body or move an already-documented read ahead of its auth gate;
    3. ``default_max_bytes`` / ``default_detail``.

    REPLAY MECHANISM. This is a ``BaseHTTPMiddleware`` on purpose, and after a
    successful capped read it assigns ``request._body``: Starlette's
    ``_CachedRequest.wrapped_receive`` then hands the cached bytes to every
    downstream layer — the SAME path ``await request.body()`` uses. A raw-ASGI
    middleware that wrapped ``receive`` instead is brittle here: a downstream
    ``BaseHTTPMiddleware`` whose app has consumed the request calls ``receive``
    again on the response path expecting ``http.disconnect``, and a synthetic
    ``http.request`` there raises ``RuntimeError: Unexpected message received``
    (it broke the MCP sub-app's SSE response path). ``_body`` is the private
    field ``Request.body()`` itself populates; using it is the supported
    Starlette body-replay contract.
    """

    def __init__(
        self,
        app: Any,
        *,
        default_max_bytes: int,
        default_detail: str,
        path_caps: dict[str, tuple[int, str]] | None = None,
        exempt_prefixes: tuple[str, ...] = (),
        exempt_regexes: tuple[Any, ...] = (),
    ) -> None:
        super().__init__(app)
        self.default_max_bytes = default_max_bytes
        self.default_detail = default_detail
        # Normalize the KEYS at registration too, so a trailing-slash key can
        # never be registered in a form the lookup would miss.
        self.path_caps = {self._normalize_path(k): v
                          for k, v in (path_caps or {}).items()}
        self.exempt_prefixes = tuple(exempt_prefixes)
        self.exempt_regexes = tuple(exempt_regexes)

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Route path with any trailing slash removed (``"/"`` preserved).

        Every ``exempt_regexes`` entry tolerates an optional trailing slash
        (``/?$``), so the ``path_caps`` lookup must too: ``/v1/sessions/`` used
        to miss the ``/v1/sessions`` override and fall back to the 256 KiB
        default, false-413ing a legal ~10 MB capture POSTed with a trailing
        slash (Starlette's ``redirect_slashes`` would have 307'd it to the
        override path).
        """
        return path if path == "/" else path.rstrip("/")

    def resolve_cap(self, path: str) -> tuple[int, str] | None:
        """``(max_bytes, detail)`` for ``path``, or ``None`` to pass through.

        ``path`` must be the ROUTE path (root_path-stripped) — see
        ``dispatch``.
        """
        key = self._normalize_path(path)
        override = self.path_caps.get(key)
        if override is not None:
            return override
        for pattern in self.exempt_regexes:
            if pattern.match(path) or pattern.match(key):
                return None
        for prefix in self.exempt_prefixes:
            if key == prefix or key.startswith(prefix.rstrip("/") + "/"):
                return None
        return (self.default_max_bytes, self.default_detail)

    async def dispatch(self, request: Request, call_next):
        # Compare on the ROUTE path (Starlette strips root_path there), not the
        # raw path: under an ASGI mount prefix (`uvicorn --root-path /x`)
        # scope["path"] is "/x/v1/sessions" while the route path is
        # "/v1/sessions", so a raw-path lookup would miss the override AND
        # every exemption — reading `/v1/internal/**` bodies BEFORE the auth
        # gate (breaking #4939's "not one byte read before auth") and
        # false-413ing the larger legal bodies on the import / commit / stripe
        # routes. Mirrors McpPathCanonicalizerMiddleware in hosted_api.py.
        cap = self.resolve_cap(get_route_path(request.scope))
        if cap is not None:
            try:
                request._body = await read_capped_body(
                    request, cap[0], cap[1])
            except BodyTooLargeError as exc:
                return JSONResponse({"detail": exc.detail}, status_code=413)
        return await call_next(request)
