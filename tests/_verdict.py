"""#5049 — the verdict contract: a test verdict is a function of the code under test.

The parent issue's refactor direction, encoded once so no site re-invents it:

1. **No fixed wall-clock budget may produce FAIL.** A wait becomes an event/condition
   wait (`wait_for`); where a deadline is still needed, its expiry is an explicit
   INCONCLUSIVE skip that *names the deadline* (`inconclusive`, `require_within`,
   `inconclusive_on_timeout`) — never a product-defect FAIL.
2. **Sufficiency of a deadline is a measured property, not a guess.** `Deadline`
   compares a budget against a *recorded* (checked-in) endpoint time and raises
   `HarnessDefect` when the margin is absent. It never measures the endpoint live:
   a live sample on a loaded host would itself manufacture the FAIL rule 1 forbids.
3. Exact equality on a derived quantity is a numerical-tolerance surface and is
   deliberately **not** expressed here (see the plan's Remaining members).
4. **Process globals reset per test** (`PROCESS_GLOBALS`, `reset_process_globals`,
   `AMBIENT_ENV_GLOBALS`) — an order-dependent outcome is a harness defect.
5. **A host capability gap is a SKIP, not a FAIL** (`host_capability`,
   `node_meets`, `require_node_floor`).

Design constraints:

* **Stdlib + pytest only** at module import. `tortoise.*` targets are resolved
  lazily inside `reset_process_globals`, so this module can be imported at
  `tests/conftest.py` module level without perturbing the #1686 test-mode import
  ordering.
* The INCONCLUSIVE skip reason is a **declared family** (`INCONCLUSIVE_PREFIX`)
  whose wording deliberately names NO availability class — so the skip guard's
  FalkorDB trip does not apply to it, and no per-flake allow-list entry is
  needed (#5049's owner frame: `tools/skip-guard.py` is unchanged).
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, NoReturn

import pytest

#: The declared reason family for "the property could not be observed in the
#: deadline". A deadline-class skip names NO availability class, so the skip
#: guard's FalkorDB/embedder trips cannot apply to it and `tools/skip-guard.py`
#: needs no exemption for it (pinned by
#: `tests/test_skip_guard.py::test_inconclusive_reason_needs_no_exemption`).
#: The wording is part of the contract, not a per-site string.
INCONCLUSIVE_PREFIX = "INCONCLUSIVE [#5049]"

#: A harness (test-substrate) defect — the harness is at fault, not the change.
#: This is deliberately a FAIL: rule 4 says an order-dependent / under-specified
#: outcome must fail the *harness*, and this is how it is attributed.
_HARNESS_PREFIX = "HARNESS DEFECT [#5049]"


class HarnessDefect(AssertionError):
    """The test substrate itself is defective (rename, under-specified budget)."""


def inconclusive(what: str, *, deadline_s: float, diagnosis: str | None = None) -> NoReturn:
    """Skip with the declared INCONCLUSIVE reason, naming the deadline.

    ``what`` names the property that was not observed; ``deadline_s`` is the
    budget that expired. This is the ONLY exit for a wall-clock expiry (rule 1).
    """
    detail = (
        f"{INCONCLUSIVE_PREFIX}: {what} — not observed within the "
        f"{deadline_s:g}s deadline."
    )
    if diagnosis:
        detail = f"{detail} {diagnosis}"
    detail = (
        f"{detail} A deadline expiry is evidence about machine load, not about "
        f"the property under test (#5049 rule 1)."
    )
    pytest.skip(detail)


def wait_for(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    interval_s: float = 0.02,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Event/condition wait: True as soon as ``predicate()`` holds, else False at the deadline.

    This is the rule-1 primitive. It never raises on timeout — the caller decides
    the verdict (normally `require_within`, which skips).
    """
    deadline = clock() + timeout_s
    while True:
        if predicate():
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleep(min(interval_s, remaining))


def require_within(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    what: str,
    diagnosis: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """`wait_for`, but a deadline expiry is INCONCLUSIVE (rule 1)."""
    if not wait_for(predicate, timeout_s=timeout_s, clock=clock):
        inconclusive(what, deadline_s=timeout_s, diagnosis=diagnosis)


@contextmanager
def inconclusive_on_timeout(
    *,
    what: str,
    deadline_s: float,
    exceptions: Sequence[type[BaseException]] = (TimeoutError,),
    diagnosis: str | None = None,
) -> Iterator[None]:
    """Convert a *deadline-bearing* timeout into INCONCLUSIVE, never FAIL.

    ``exceptions`` must name the *narrow* class that the callee's own deadline
    raises (e.g. ``redis.exceptions.TimeoutError``). Do **not** catch a broad
    programming error class here — a defect that merely happens to surface as a
    timeout is a product verdict, not a machine one, and wrapping it would turn a
    regression into a skip. Prefer `require_within` where a condition is
    observable; use this only for an API whose own timeout IS the deadline.
    """
    try:
        yield
    except tuple(exceptions) as exc:  # type: ignore[misc]
        inconclusive(
            what,
            deadline_s=deadline_s,
            diagnosis=diagnosis or f"({type(exc).__name__}: {exc})",
        )


# ── rule 1, product side: the transport wait-bound refusal ────────────────
# The hosted API wraps every non-exempt route in `WaitBoundMiddleware` and
# answers a request that outlives `mcp_auth._TRANSPORT_WAIT_BOUND_S` with a
# refusal (#3834, owner ruling 2026-09-20). The refusal's shape differs by
# surface, and the product emits exactly two:
#
#   * REST — status 504, body `{"detail": mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE}`
#     (`hosted_api._send_wait_bound_refusal`);
#   * JSON-RPC / MCP — a `{code, message, data}` error mapping carrying
#     `mcp_auth.ERR_TIMEOUT` + the same message, on status 504 for the `/mcp`
#     route and inside a `CallToolResult(isError=True)` on the SSE arm (HTTP
#     200), the SDK having collapsed a raised `McpError` into that shape — see
#     `mcp_server._await_under_mcp_wait_bound`. The SSE `data:` payload is a
#     JSON-RPC RESPONSE, so the `CallToolResult` sits one level down at
#     `result`, and the mapping is homed at `result.structuredContent.error`
#     (no wrap envelope) or `result._meta.tortoise_error` (a wrap-envelope tool
#     — #8482). (`test_transport_wait_bound.py` reads the real wire the same
#     way: `_parse_sse(r.text)` then `body["result"]`.)
#
# That refusal is the LEGIBLE REFUSAL the ruling requires — it is the product
# saying "no answer yet", not an answer. A test that reads it as a FAIL is
# therefore reading the host's load, which is exactly what rule 1 forbids; the
# property under test was never observed.
#
# ⛔ The detection is the refusal ENVELOPE, never the message text alone. A bare
# substring test classified ANY response whose body merely CONTAINED the
# message — a genuine 4xx/5xx error included — as a non-answer, so a true red
# was reported INCONCLUSIVE and silently not reported (#8579 review: a 500 and a
# 200 whose bodies carried the message both returned `True`). Each arm is gated
# on the surface's own shape above. Everything that is not this refusal —
# including every other error status, and any body that merely mentions the
# message — is returned untouched, so a genuine product failure still FAILs the
# caller.


def _body_text(response: Any) -> str:
    """The response body as text, whatever the client exposes it on."""
    body = getattr(response, "text", None)
    if isinstance(body, str):
        return body
    content = getattr(response, "content", b"")
    if isinstance(content, bytes):
        return content.decode("utf-8", "replace")
    return str(content)


def transport_wait_bound_s() -> float:
    """The product's transport wait bound, resolved lazily (rule 2: name the budget).

    Imported inside the call so this module stays stdlib+pytest at import time
    (it is imported at `tests/conftest.py` module level).
    """
    from tortoise import mcp_auth

    return float(mcp_auth._TRANSPORT_WAIT_BOUND_S)


def _json_objects(response: Any) -> list[dict]:
    """Every JSON object the response body carries.

    A REST/JSON response carries one; the MCP arm's SSE stream carries its
    ``data:`` payloads. Parsing the STRUCTURE — not searching the text — is what
    lets the refusal be identified by its envelope rather than by a substring.
    """
    as_json = getattr(response, "json", None)
    if callable(as_json):
        try:
            parsed = as_json()
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return [parsed]
    text = _body_text(response).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return [parsed]
    objects: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except Exception:
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
    return objects


def _refusal_error_mapping(body: dict) -> dict | None:
    """The ``{code, message}`` mapping a JSON-RPC/MCP refusal carries.

    JSON-RPC homes it at ``error``. The MCP SSE arm's ``data:`` payload is a
    JSON-RPC *response*, so the ``CallToolResult`` is one level down at
    ``result`` and the same mapping lives at ``result.structuredContent.error``
    (no wrap envelope) or ``result._meta.tortoise_error`` (the wrap-envelope
    repair, #8482). Both levels are probed: the `/mcp` pre-SSE 504 carries the
    mapping at the TOP level, the SSE frame carries it under ``result``.
    """
    layers: list[dict] = [body]
    result = body.get("result")
    if isinstance(result, dict):
        layers.append(result)
    for layer in layers:
        homes: list[Any] = [layer.get("error")]
        structured = layer.get("structuredContent")
        if isinstance(structured, dict):
            homes.append(structured.get("error"))
        meta = layer.get("_meta")
        if isinstance(meta, dict):
            homes.append(meta.get("tortoise_error"))
        for home in homes:
            if isinstance(home, dict):
                return home
    return None


def is_transport_wait_bound_refusal(response: Any) -> bool:
    """True when ``response`` is the product's transport wait-bound refusal.

    Gated on the refusal ENVELOPE per surface — never on the message alone, which
    classified a genuine error that merely mentioned the message as a non-answer
    and reported a true red as INCONCLUSIVE (#8579 review). The accepted shapes
    are the two the product actually emits (see the block comment above), so a
    conforming caller catches the refusal on every surface while every other
    response reaches its own assertion.
    """
    from tortoise import mcp_auth

    status = getattr(response, "status_code", None)
    message = mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE
    for body in _json_objects(response):
        # REST arm: 504 whose body is exactly the refusal.
        if status == 504 and body == {"detail": message}:
            return True
        # JSON-RPC / MCP arm: the product's error code AND message, on either
        # the `/mcp` route's 504 or the SSE arm's 200.
        error = _refusal_error_mapping(body)
        if (
            status in (200, 504)
            and error is not None
            and error.get("code") == mcp_auth.ERR_TIMEOUT
            and error.get("message") == message
        ):
            return True
    return False


def require_answered(response: Any, *, what: str) -> Any:
    """Return an ANSWERED response; a transport wait-bound refusal is INCONCLUSIVE.

    This is the product-side exit for rule 1: the request was refused by a
    wall-clock bound with no answer, so the property was never observed. The
    verdict is an INCONCLUSIVE skip that names the bound — never a bare FAIL
    (which reads as a product defect) and never a silent pass.

    Every other response — including a non-504 error, and any response that
    merely mentions the refusal message without its envelope — is returned
    unchanged.
    """
    if is_transport_wait_bound_refusal(response):
        inconclusive(
            what,
            deadline_s=transport_wait_bound_s(),
            diagnosis=(
                "The product's transport wait bound refused the request instead "
                "of answering it (#3834); the refusal's status differs by "
                "surface (REST 504, MCP 200 with an isError result), so it is "
                "named by neither — the property under test was never observed."
            ),
        )
    return response


# ── rule 1, product side: the client seam that covers every caller ────────
# #8579 routed `require_answered` through the *named* seams it could reach (a
# `_commit`, a `_poll_until`, a `_post_signed`). The residual is the callers
# that sit behind no shared request helper, where a bare
# `assert r.status_code == 200` reads the refusal as a product FAIL (#8582).
#
# Every one of those callers still builds a client, and every response it reads
# comes back through that client — so the client construction is the seam that
# covers them all, and one conversion per construction replaces a per-read
# sweep (the plan doc's "convert the seam, not the leaf",
# `docs/plans/2026-09-26-5049-verdicts-from-conditions.md`). The check sits at
# the request boundary — after the response is fetched, before it is returned —
# which is httpx's own documented home for a shared per-response decision
# (https://www.python-httpx.org/advanced/event-hooks/).
#
# ⛔ The seam converts only the REFUSAL ENVELOPE (`require_answered`'s rule), so
# a genuine answered 4xx/5xx still reaches the caller's own assertion, and a
# test that deliberately pins the refusal must build a plain client (a converted
# seam SKIPs before its assertion is reached).


def _answered(request: Any, response: Any) -> Any:
    """Route one client response through `require_answered`, naming the request.

    ``what`` names the method and route that was refused, so the INCONCLUSIVE
    reason points at the request rather than at the seam that observed it.
    """
    return require_answered(
        response, what=f"{request.method} {request.url.path} answered")


def _already_read(response: Any) -> bool:
    """True when the body is available without consuming the caller's stream.

    httpx leaves ``is_stream_consumed`` False until ``read()``/``aread()`` is
    called, so a response returned with ``stream=True`` is still unread when it
    reaches the seam; reaching for ``.text`` there would raise
    `httpx.ResponseNotRead` and break the caller. Such a response is left
    untouched — the seam cannot inspect a body it must not consume.

    ⛔ This is a known limit, not coverage: a streamed refusal is not converted.
    No converted seam streams (there is no ``.stream(`` call site in any of
    them), and `test_the_verdict_client_seam_does_not_consume_a_streamed_response`
    pins the pass-through so the limit stays visible rather than silently
    widening.
    """
    return getattr(response, "is_stream_consumed", True) is not False


def verdict_client(app: Any, **kwargs: Any) -> Any:
    """A `TestClient` whose every response passes the #5049 contract (#8582).

    The seam, not the leaf: a route behind `WaitBoundMiddleware` that refuses a
    too-long request is a NON-ANSWER, so it becomes the contract's INCONCLUSIVE
    skip instead of a load-dependent `assert 504 == 200` FAIL.

    Imported inside the call so this module stays stdlib+pytest at import time
    (it is imported at `tests/conftest.py` module level).
    """
    from starlette.testclient import TestClient

    class _VerdictTestClient(TestClient):
        def send(self, request: Any, **send_kwargs: Any) -> Any:
            response = super().send(request, **send_kwargs)
            if not _already_read(response):
                return response
            return _answered(request, response)

    return _VerdictTestClient(app, **kwargs)


def verdict_async_client(app: Any, **kwargs: Any) -> Any:
    """The async twin of `verdict_client` — an `httpx.AsyncClient` ASGI seam.

    For the call sites that build their own client around
    `httpx.ASGITransport(app=...)` rather than going through `TestClient`.
    """
    import httpx

    class _VerdictAsyncClient(httpx.AsyncClient):
        async def send(self, request: Any, **send_kwargs: Any) -> Any:
            response = await super().send(request, **send_kwargs)
            if not _already_read(response):
                return response
            return _answered(request, response)

    kwargs.setdefault("transport", httpx.ASGITransport(app=app))
    return _VerdictAsyncClient(**kwargs)


@dataclasses.dataclass(frozen=True)
class Deadline:
    """A named budget with its *recorded* endpoint time (rule 2).

    ``recorded_s`` is a static, checked-in measurement of the endpoint/service
    the site observes — never a live sample. ``assert_sufficient`` raises
    `HarnessDefect` when the budget lacks the required margin over it, so an
    under-specified deadline fails the harness at the point it is declared
    rather than flaking under load.
    """

    name: str
    budget_s: float
    recorded_s: float
    margin_factor: float = 2.0

    def assert_sufficient(self) -> Deadline:
        floor = self.recorded_s * self.margin_factor
        if self.budget_s < floor:
            raise HarnessDefect(
                f"{_HARNESS_PREFIX}: deadline {self.name!r} budget "
                f"{self.budget_s:g}s has no measured margin over the recorded "
                f"endpoint {self.recorded_s:g}s (needs >= {floor:g}s, "
                f"margin x{self.margin_factor:g}). A budget chosen without a "
                f"recorded margin is what #5049 rule 2 rejects."
            )
        return self


# ── host capability (rule 5) ──────────────────────────────────────────────
def host_capability(probe: Callable[[], bool], *, what: str, remedy: str) -> None:
    """Skip when a host capability is absent — never FAIL."""
    if not probe():
        pytest.skip(f"HOST CAPABILITY [#5049]: {what} unavailable — {remedy}")


# The ONLY floor this increment declares. Every converted site invokes node with
# `--experimental-strip-types`, which needs 22.7 — the floor
# `tests/test_capture_spool.py::_node_that_can_strip_ts` already records. The
# bare `node --test <file>.ts` floor (type stripping must be default-on) is a
# DIFFERENT, higher band and is deliberately a REMAINING member of #5049, not a
# constant declared here.
NODE_FLOOR_STRIP_TYPES: tuple[int, int] = (22, 7)


def node_version(node: str = "node") -> tuple[int, int] | None:
    """``(major, minor)`` for the node on PATH, or None when absent/unprobeable.

    None covers absent, a non-zero `--version` exit, empty/garbage output — all
    host-capability gaps, never product verdicts.
    """
    try:
        proc = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    m = re.match(r"v(\d+)\.(\d+)(?:\.|$)", proc.stdout.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def node_meets(floor: tuple[int, int], node: str = "node") -> bool:
    """True when the node on PATH is at or above ``floor``.

    ``node_version`` reports ``(major, minor)`` only, so a prerelease is compared
    AT its numeric floor: ``v22.7.0-rc.1`` meets ``(22, 7)`` and RUNS. That is
    deliberate — the floor is a numeric capability band, and a candidate build of
    it carries the same flag. Pinned by
    ``tests/test_verdict_contract.py::test_node_version_parses_the_runtime``.
    """
    version = node_version(node)
    return version is not None and version >= floor


def require_node_floor(
    floor: tuple[int, int],
    *,
    what: str,
    node: str = "node",
    absent: str = "skip",
) -> None:
    """Skip when node is absent or older than ``floor``.

    ``absent="fail"`` preserves a site's recorded decision that a *missing* node
    must fail loudly (a skipped guard can look like a passing one); even then a
    present-but-too-old node SKIPs, because it is a host-capability gap (rule 5).
    A present-but-UNPROBEABLE node (garbage/non-zero ``--version``) is a broken
    host, not a capability gap, so it fails loud for ``absent="fail"`` sites too.
    """
    if shutil.which(node) is None:
        if absent == "fail":
            pytest.fail(
                f"no node runtime for {what} — need node >= "
                f"{floor[0]}.{floor[1]}; refusing to skip, because a skipped "
                f"guard looks like a passing one"
            )
        host_capability(
            lambda: False,
            what=f"a Node runtime for {what}",
            remedy=f"install Node >= {floor[0]}.{floor[1]}",
        )
        return
    version = node_version(node)
    if version is None:
        # Present but unprobeable. A host that cannot say which runtime it has is
        # broken, not capability-poor: `absent="fail"` sites must fail loud here
        # exactly as they do when node is absent.
        if absent == "fail":
            pytest.fail(
                f"the node runtime for {what} did not report a parseable "
                f"`--version`; refusing to skip, because a skipped guard looks "
                f"like a passing one — need node >= {floor[0]}.{floor[1]}"
            )
        host_capability(
            lambda: False,
            what=f"a probeable Node runtime for {what}",
            remedy=f"ensure `node --version` reports >= {floor[0]}.{floor[1]}",
        )
        return
    if version < floor:
        pytest.skip(
            f"HOST CAPABILITY [#5049]: node v{version[0]}.{version[1]} is below "
            f"the {floor[0]}.{floor[1]} floor required by {what} — a host "
            f"capability gap is a SKIP, not a FAIL (rule 5)"
        )


# ── process globals (rule 4) ──────────────────────────────────────────────
@dataclasses.dataclass(frozen=True)
class ProcessGlobal:
    """A process-global attribute and the value it must be reset to per test."""

    dotted: str
    clean: Any
    why: str


#: Module attributes reset before AND after every test. Add here — not as a
#: second per-module fixture — so one declaration owns the reset.
PROCESS_GLOBALS: tuple[ProcessGlobal, ...] = (
    ProcessGlobal(
        "tortoise.embedded_lifecycle._atexit_deadline",
        None,
        "#4913/#4879: the exit-seam budget is once-armed and never re-armed, so "
        "a mid-run seam call would otherwise arm a 30 s budget for every later test",
    ),
)

#: Ambient env vars deleted per test (via monkeypatch, which restores them).
#: `TORTOISE_API_URL` makes the suite non-hermetic on a fleet shell: the ask lane
#: fails loud on it (`ask_lane.py:438,848`) and the commit client routes to it
#: (`sdk.py` `_post_commit`). Clearing it makes a local run match CI.
AMBIENT_ENV_GLOBALS: tuple[str, ...] = ("TORTOISE_API_URL",)


def reset_process_globals(
    globals_: Sequence[ProcessGlobal] | None = None,
) -> list[str]:
    """Reset every declared process global; return the names reset.

    ``globals_`` defaults to `PROCESS_GLOBALS`; passing an explicit sequence
    keeps the rename-must-fail test from having to mutate module state.

    A declared attribute that does not exist raises `HarnessDefect` — a rename
    must fail loud, never no-op into a silently-created attribute.
    """
    reset: list[str] = []
    for spec in (PROCESS_GLOBALS if globals_ is None else globals_):
        module_path, _, attr = spec.dotted.rpartition(".")
        module = importlib.import_module(module_path)
        if not hasattr(module, attr):
            raise HarnessDefect(
                f"{_HARNESS_PREFIX}: process-global {spec.dotted!r} does not "
                f"exist — the declared name was renamed or removed, so the per-test "
                f"reset would silently no-op ({spec.why})."
            )
        setattr(module, attr, spec.clean)
        reset.append(spec.dotted)
    return reset
