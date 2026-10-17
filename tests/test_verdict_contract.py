"""#5049 — tests for the verdict contract (`tests/_verdict.py`).

These pin the contract's discriminating power:

* a deadline expiry is INCONCLUSIVE and *names the deadline* — the old
  fixed-budget shape (a bare `assert` on the same input) FAILs, which is the
  sabotage leg proving the conversion changes the verdict;
* a *recorded* margin is what `Deadline` judges — never a live measurement;
* a host-capability gap SKIPs;
* process globals are reset before every test (order-independence is the
  property, and its removal goes RED).
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import time
from pathlib import Path

import pytest

from tests import _verdict
from tests._fork_safety_verdict import (
    assert_fixed_race_verdict,
    healthy_result,
)
from tests._verdict import (
    AMBIENT_ENV_GLOBALS,
    PROCESS_GLOBALS,
    Deadline,
    HarnessDefect,
    ProcessGlobal,
    host_capability,
    inconclusive_on_timeout,
    is_transport_wait_bound_refusal,
    node_meets,
    require_answered,
    require_node_floor,
    require_within,
    reset_process_globals,
    transport_wait_bound_s,
    verdict_async_client,
    verdict_client,
    wait_for,
)


def _canned(*, status: int, text: str | None = None,
            json_body: dict | None = None):
    """A canned HTTP response; ``json_body`` and ``text`` are the body channels."""

    class _Response:
        def __init__(self) -> None:
            self.status_code = status
            if text is not None:
                self.text = text
            elif json_body is not None:
                self.text = json.dumps(json_body)
            else:
                self.text = ""

        def json(self):
            if json_body is not None:
                return json_body
            return json.loads(self.text)

    return _Response()


def _rest_refusal(*, status: int = 504):
    """The REST arm's refusal: ``{"detail": MESSAGE}`` (#8579 P2-1)."""
    from tortoise import mcp_auth

    return _canned(status=status, json_body={
        "detail": mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE})


def _jsonrpc_refusal(*, status: int = 504):
    """The ``/mcp`` JSON-RPC arm: ``error.code == ERR_TIMEOUT`` + the message."""
    from tortoise import mcp_auth

    return _canned(status=status, json_body={
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": mcp_auth.ERR_TIMEOUT,
                  "message": mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE,
                  "data": {"retry_after": 10}},
    })


def _sse_result(*, status: int = 200, home: str = "structuredContent"):
    """The MCP SSE arm: HTTP 200 whose ``data:`` payload carries the refusal.

    The ``data:`` payload is the JSON-RPC **response** the product actually
    emits — ``{"jsonrpc", "id", "result"}`` — so the ``CallToolResult`` is at
    ``result``. `test_transport_wait_bound.py` reads the real wire that way
    (``body = _parse_sse(r.text)`` then ``payload = body["result"]``); a bare
    ``CallToolResult`` as the payload is a shape the product never produces.

    The ``{code, message, data}`` mapping is at ``result.structuredContent.error``
    for a tool with no wrap envelope, or ``result._meta.tortoise_error`` for a
    wrap-envelope tool (#8482).
    """
    from tortoise import mcp_auth

    mapping = {"code": mcp_auth.ERR_TIMEOUT,
               "message": mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE,
               "data": {"retry_after": 10}}
    result: dict = {
        "content": [{"type": "text",
                     "text": mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE}],
        "isError": True,
    }
    if home == "structuredContent":
        result["structuredContent"] = {"error": mapping}
    else:
        result["_meta"] = {"tortoise_error": mapping}
    response = {"jsonrpc": "2.0", "id": 1, "result": result}
    return _canned(
        status=status,
        text=f"event: message\ndata: {json.dumps(response)}\n\n",
    )

# ── rule 1, product side: the client seam that covers every caller (#8582) ──
# `#8579` routed `require_answered` through the named seams it could reach. The
# residual is the callers behind no request helper, whose bare
# `assert r.status_code == 200` still reads the refusal as a FAIL. Those callers
# all build a client, so the client CONSTRUCTION is the seam that covers them
# (#8582). These tests pin the seam's discriminating power before any caller
# depends on it.


def _seam_app():
    """A tiny ASGI app carrying the three shapes the client seam distinguishes."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from tortoise import mcp_auth

    async def refused(_request):
        return JSONResponse(
            {"detail": mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE}, status_code=504)

    async def boom(_request):
        return JSONResponse({"detail": "the handler failed"}, status_code=500)

    async def ok(_request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[
        Route("/refused", refused), Route("/boom", boom), Route("/ok", ok)])


def test_the_verdict_client_seam_converts_the_refusal_and_names_the_request():
    with verdict_client(_seam_app()) as tc, pytest.raises(pytest.skip.Exception) as excinfo:
        tc.get("/refused")
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "GET /refused answered" in message, "the refused request must be named"


def test_the_verdict_client_seam_is_the_discriminator_not_the_app():
    """Non-vacuity: a PLAIN client reads the very same response as a 504."""
    from starlette.testclient import TestClient

    with TestClient(_seam_app()) as tc:
        assert tc.get("/refused").status_code == 504


def test_the_verdict_client_seam_leaves_an_answered_response_alone():
    with verdict_client(_seam_app()) as tc:
        assert tc.get("/ok").json() == {"ok": True}


def test_the_verdict_client_seam_leaves_an_answered_error_alone():
    """A genuine 5xx is not the refusal — it must still reach the caller."""
    with verdict_client(_seam_app()) as tc:
        assert tc.get("/boom").status_code == 500


def test_the_verdict_client_seam_does_not_consume_a_streamed_response():
    """A `stream=True` body is unread; the seam must not reach into it."""
    from tortoise import mcp_auth

    with verdict_client(_seam_app()) as tc, tc.stream("GET", "/refused") as r:
        assert r.status_code == 504
        assert mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE in r.read().decode()


def test_the_verdict_async_client_seam_converts_the_refusal():
    async def _run():
        async with verdict_async_client(
                _seam_app(), base_url="http://test") as ac:
            await ac.get("/refused")

    with pytest.raises(pytest.skip.Exception) as excinfo:
        asyncio.run(_run())
    assert "INCONCLUSIVE [#5049]" in str(excinfo.value)


_ATEXIT = "tortoise.embedded_lifecycle._atexit_deadline"


class _ClientTimeout(TimeoutError):
    """Stands in for redis's client socket-budget expiry (#4742)."""


# ── rule 1: a wall-clock expiry is INCONCLUSIVE, never FAIL ────────────────
def test_wait_for_returns_false_at_the_deadline():
    assert wait_for(lambda: False, timeout_s=0.02, interval_s=0.005) is False


def test_wait_for_returns_true_when_the_condition_becomes_true():
    calls = {"n": 0}

    def predicate() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    assert wait_for(predicate, timeout_s=1.0, interval_s=0.001) is True


def test_deadline_expiry_skips_and_names_the_deadline():
    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_within(lambda: False, timeout_s=0.05, what="the probe")
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "0.05" in message, "the deadline must be named"


def test_sabotage_the_old_fixed_budget_shape_fails_the_same_input():
    """The shape #5049 replaces: `assert <condition>` after a fixed budget.

    The same never-true predicate that now SKIPs used to FAIL. This is the
    pin's must-fail leg — it proves the conversion changes the verdict, not
    just the wording.
    """
    with pytest.raises(AssertionError):
        assert wait_for(lambda: False, timeout_s=0.02, interval_s=0.005), (
            "the property was not observed within 0.02s")


def test_narrow_timeout_becomes_inconclusive_and_names_the_budget():
    with pytest.raises(pytest.skip.Exception) as excinfo, inconclusive_on_timeout(
        what="the probe", deadline_s=4.0, exceptions=(_ClientTimeout,)
    ):
        raise _ClientTimeout("Timeout reading from socket")
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "4" in message


def test_a_non_timeout_error_is_not_swallowed():
    """A defect that merely runs inside the block must still FAIL."""
    with pytest.raises(ValueError), inconclusive_on_timeout(
        what="the probe", deadline_s=4.0, exceptions=(_ClientTimeout,)
    ):
        raise ValueError("a real product defect")


# ── rule 1, product side: the transport wait-bound refusal ─────────────────
def test_the_transport_bound_refusal_skips_and_names_the_product_bound():
    """A bound reached with no answer is INCONCLUSIVE, and names the budget."""
    response = _rest_refusal()
    assert is_transport_wait_bound_refusal(response) is True
    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_answered(response, what="POST /v1/sessions answered")
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    # The deadline NAMED is the product's own bound, not an invented one.
    assert f"{transport_wait_bound_s():g}" in message


def test_every_refusal_envelope_is_caught_on_its_own_surface():
    """#8579 P2-1: the signal is the per-surface ENVELOPE, and each is caught.

    REST: 504 + `{"detail": MESSAGE}`. `/mcp` JSON-RPC: `ERR_TIMEOUT` + the
    message on 504. MCP SSE: a 200 whose JSON-RPC `result` carries a
    `CallToolResult(isError=True)` homing the mapping. A status-gated detector
    would MISS the 200; a message-gated one catches all three *and* every
    innocent body that merely mentions the message — which is the bug this
    replaces.
    """
    for response in (_rest_refusal(), _jsonrpc_refusal(), _sse_result()):
        assert is_transport_wait_bound_refusal(response) is True, response.text
        with pytest.raises(pytest.skip.Exception):
            require_answered(response, what="a probe")


def test_the_wrap_envelope_refusal_is_caught_at_its_re_homed_meta():
    """#8482: a wrap-envelope tool's refusal is re-homed on `result._meta`."""
    response = _sse_result(home="_meta")
    assert is_transport_wait_bound_refusal(response) is True
    with pytest.raises(pytest.skip.Exception):
        require_answered(response, what="MCP wrap tool answered")


def test_the_sse_fixture_mirrors_the_real_jsonrpc_framing():
    """The SSE `data:` payload is a JSON-RPC RESPONSE — the mapping is at `result`.

    The pre-fix `_refusal_error_mapping` probed only the TOP level while the
    pre-fix fixture put a bare `CallToolResult` there, so the pair certified a
    shape the product never emits and the whole SSE arm silently regressed to
    the caller's assertion. The real wire frames it as a JSON-RPC response
    (`test_transport_wait_bound.py`: `_parse_sse(r.text)` then `body["result"]`),
    which is what this pins: the homes must be under `result`, absent at the top.
    """
    for home in ("structuredContent", "_meta"):
        text = _sse_result(home=home).text
        line = next(ln for ln in text.splitlines() if ln.startswith("data:"))
        payload = json.loads(line[len("data:"):].strip())
        assert payload["jsonrpc"] == "2.0" and "id" in payload, payload
        assert isinstance(payload["result"], dict), payload
        assert home in payload["result"], payload
        assert home not in payload, (
            "the refusal home must sit under `result` — a top-level home is the "
            "unemitted shape the pre-fix fixture drilled")
        # The pre-fix top-level-only probe read exactly these three keys, all of
        # which are absent on the real frame — hence the P1 regression.
        assert "error" not in payload, payload
        assert is_transport_wait_bound_refusal(_sse_result(home=home)) is True


def test_a_bare_sse_result_is_still_caught_when_it_is_the_payload():
    """Defence in depth: the pre-#8579 shape (bare result) is not a regression.

    The product frames the SSE payload as JSON-RPC, but a `structuredContent` /
    `_meta` mapping at the top level of a `data:` payload is still the refusal —
    the probe descends rather than demanding the wrapper.
    """
    from tortoise import mcp_auth

    mapping = {"code": mcp_auth.ERR_TIMEOUT,
               "message": mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE,
               "data": {"retry_after": 10}}
    for result in ({"isError": True, "structuredContent": {"error": mapping}},
                   {"isError": True, "_meta": {"tortoise_error": mapping}}):
        response = _canned(
            status=200, text=f"event: message\ndata: {json.dumps(result)}\n\n")
        assert is_transport_wait_bound_refusal(response) is True, result


def test_the_inconclusive_diagnosis_names_no_surface_status():
    """The refusal rides a 504 on REST and a 200 on the MCP arm.

    The diagnosis is the artifact a lane reads when a site skips, so it must
    not send that reader hunting for a REST-only status that never occurred on
    the MCP surface (the detector's own docstring forbids assuming one).
    """
    for response in (_rest_refusal(), _sse_result()):
        with pytest.raises(pytest.skip.Exception) as excinfo:
            require_answered(response, what="a probe")
        message = str(excinfo.value)
        assert "(HTTP 504)" not in message, message
        assert "#3834" in message, message


def test_an_unrelated_504_is_returned_to_the_caller():
    """Only the product's OWN refusal is converted — a bare 504 is not it."""
    response = _canned(status=504, json_body={"detail": "gateway timeout"})
    assert is_transport_wait_bound_refusal(response) is False
    assert require_answered(response, what="a probe") is response


def test_the_product_message_alone_is_not_a_refusal():
    """#8579 P2-1: the message TEXT is not the signal — the envelope is.

    The pre-fix detector was `MESSAGE in body_text`, status-blind, so a genuine
    500 (or a 200) whose body merely CONTAINED the message was reported as the
    refusal and a true red became INCONCLUSIVE. Each case below carries the
    message without the envelope and must NOT be converted.
    """
    from tortoise import mcp_auth

    message = mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE
    assert is_transport_wait_bound_refusal(
        _canned(status=504, text=message)) is False  # bare text, no envelope
    assert is_transport_wait_bound_refusal(
        _rest_refusal(status=200)) is False  # REST detail on the wrong status
    assert is_transport_wait_bound_refusal(
        _canned(status=504, json_body={"detail": f"proxy: {message}"})
    ) is False  # substring, not the detail
    assert is_transport_wait_bound_refusal(
        _canned(status=504, json_body={
            "error": {"code": -32001, "message": message}})
    ) is False  # right message, WRONG error code


def test_an_answered_response_is_returned_untouched():
    response = _canned(status=200, json_body={"ok": True})
    assert require_answered(response, what="a probe") is response


def test_a_non_504_error_still_reaches_the_callers_own_assertion():
    """#8579 P2-1 must-fail leg: a NON-504 error CARRYING the message reaches
    the caller.

    The status-blind message-substring detector made this case SKIP; the fix must
    let the caller's own assertion see it — `assert resp.status_code == 200` on a
    500 is then a real red, not a swallowed one.
    """
    for status in (402, 500, 409, 429):
        response = _rest_refusal(status=status)
        assert is_transport_wait_bound_refusal(response) is False, status
        assert require_answered(response, what="a probe") is response, status
        with pytest.raises(AssertionError):
            assert response.status_code == 200, response.text


# ── rule 2: sufficiency is a RECORDED property, never a live measurement ───
def test_deadline_sufficient_returns_itself():
    deadline = Deadline("probe", budget_s=100.0, recorded_s=17.0)
    assert deadline.assert_sufficient() is deadline


def test_deadline_insufficient_recorded_margin_is_a_harness_defect():
    # 20 s budget against a 17 s recorded endpoint is the #4736 counter-example.
    with pytest.raises(HarnessDefect):
        Deadline("probe", budget_s=20.0, recorded_s=17.0).assert_sufficient()


def test_deadline_never_measures_the_endpoint_live(monkeypatch):
    class _FakeTime:
        @staticmethod
        def monotonic() -> float:  # pragma: no cover - only fires on a live measurement
            raise AssertionError("assert_sufficient must not measure the endpoint live")

    # patch the module REFERENCE, not the stdlib module (which pytest itself uses)
    monkeypatch.setattr(_verdict, "time", _FakeTime)
    Deadline("probe", budget_s=100.0, recorded_s=17.0).assert_sufficient()


# ── rule 4: process globals reset per test ─────────────────────────────────
def test_process_globals_registry_is_pinned():
    """A shrinking registry must go RED — parametrizing over it would not."""
    assert {spec.dotted for spec in PROCESS_GLOBALS} == {_ATEXIT}


def test_reset_process_globals_clears_an_armed_budget():
    from tortoise import embedded_lifecycle

    embedded_lifecycle._atexit_deadline = time.monotonic() + 30
    reset = reset_process_globals()
    assert embedded_lifecycle._atexit_deadline is None
    assert _ATEXIT in reset


def test_reset_process_globals_fails_loud_on_a_renamed_global():
    """A rename must fail the harness, not silently no-op."""
    bogus = (ProcessGlobal("tortoise.embedded_lifecycle._no_such_deadline", None, "test"),)
    with pytest.raises(HarnessDefect):
        reset_process_globals(bogus)


# Order-independence pin: this test runs after the one above in definition
# order (the suite has no pytest-randomly). The autouse
# `_process_global_isolation` reset must have cleared what was armed — an
# ordered outcome here is a HARNESS defect (#5049 rule 4), not the change.
def test_zz_armed_global_does_not_leak_into_the_next_test_part_1():
    from tortoise import embedded_lifecycle

    embedded_lifecycle._atexit_deadline = time.monotonic() + 30


def test_zz_armed_global_does_not_leak_into_the_next_test_part_2():
    from tortoise import embedded_lifecycle

    assert embedded_lifecycle._atexit_deadline is None, (
        "the suite-wide per-test process-global reset did not run — an armed "
        "exit-seam budget leaked into a later test (#4913). This is a HARNESS "
        "defect, not a product one.")


@pytest.fixture(scope="session", autouse=True)
def _seed_ambient_api_url():
    """Seed the ambient delegation var so its per-test deletion is non-vacuous.

    The fleet shell exports `TORTOISE_API_URL`; CI does not. Seeding it here
    means the assertion below is meaningful on a clean host too, and the
    conftest reset must remove it before every test body.
    """
    previous = os.environ.get("TORTOISE_API_URL")
    os.environ["TORTOISE_API_URL"] = "https://prod.invalid"
    yield
    if previous is None:
        os.environ.pop("TORTOISE_API_URL", None)
    else:
        os.environ["TORTOISE_API_URL"] = previous


def test_ambient_delegation_var_is_cleared_per_test():
    assert "TORTOISE_API_URL" in AMBIENT_ENV_GLOBALS
    assert os.environ.get("TORTOISE_API_URL") is None, (
        "the ambient TORTOISE_API_URL reached a test body — the suite is not "
        "hermetic (#4017); a seeded value must be deleted before every test")


# ── rule 5: a host-capability gap is a SKIP ────────────────────────────────
def test_host_capability_skips_when_absent():
    with pytest.raises(pytest.skip.Exception):
        host_capability(lambda: False, what="Node", remedy="install Node")


def test_host_capability_is_a_noop_when_present():
    host_capability(lambda: True, what="Node", remedy="install Node")


@pytest.mark.parametrize(
    "version,ok",
    [((22, 6), False), ((22, 7), True), ((23, 6), True), ((24, 1), True), (None, False)],
)
def test_node_meets_compares_the_major_minor_floor(monkeypatch, version, ok):
    monkeypatch.setattr(_verdict, "node_version", lambda node="node": version)
    assert node_meets((22, 7)) is ok


def test_node_version_parses_the_runtime(monkeypatch):
    """The `node_version` regex — the one probe the node tests above stub out.

    A prerelease is compared AT its numeric floor (``v22.7.0-rc.1`` -> (22, 7)),
    which is what makes `node_meets` a capability-band test rather than a
    build-identity test; garbage, a non-zero exit, and an unspawnable binary are
    all host-capability gaps (None), never a product verdict.
    """

    class _Completed:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    seen = {"stdout": "v22.7.0\n", "rc": 0}
    monkeypatch.setattr(
        _verdict.subprocess,
        "run",
        lambda *a, **k: _Completed(seen["stdout"], seen["rc"]),
    )
    for stdout, expected in [
        ("v22.7.0\n", (22, 7)),
        ("v22.7\n", (22, 7)),
        ("v22.7.0-rc.1\n", (22, 7)),
        ("v24.1.0\n", (24, 1)),
        ("garbage\n", None),
        ("\n", None),
    ]:
        seen["stdout"], seen["rc"] = stdout, 0
        assert _verdict.node_version() == expected, stdout
    seen["stdout"], seen["rc"] = "v22.7.0\n", 1
    assert _verdict.node_version() is None, "a non-zero --version is unprobeable"

    def _unspawnable(*a, **k):
        raise OSError("no node")

    monkeypatch.setattr(_verdict.subprocess, "run", _unspawnable)
    assert _verdict.node_version() is None, "an unspawnable node is unprobeable"


def test_require_node_floor_skips_below_the_floor(monkeypatch):
    monkeypatch.setattr(_verdict.shutil, "which", lambda _n: "/usr/bin/node")
    monkeypatch.setattr(_verdict, "node_version", lambda node="node": (22, 6))
    with pytest.raises(pytest.skip.Exception):
        require_node_floor((22, 7), what="a driver")


def test_require_node_floor_skips_an_unprobeable_node(monkeypatch):
    monkeypatch.setattr(_verdict.shutil, "which", lambda _n: "/usr/bin/node")
    monkeypatch.setattr(_verdict, "node_version", lambda node="node": None)
    with pytest.raises(pytest.skip.Exception):
        require_node_floor((22, 7), what="a driver")


def test_require_node_floor_skips_an_absent_node_by_default(monkeypatch):
    monkeypatch.setattr(_verdict.shutil, "which", lambda _n: None)
    with pytest.raises(pytest.skip.Exception):
        require_node_floor((22, 7), what="a driver")


def test_require_node_floor_fails_an_absent_node_when_the_site_says_so(monkeypatch):
    """A security guard's recorded `absent=fail` policy is preserved."""
    monkeypatch.setattr(_verdict.shutil, "which", lambda _n: None)
    with pytest.raises(pytest.fail.Exception):
        require_node_floor((22, 7), what="the blog guard", absent="fail")


def test_node_driver_site_gates_on_the_floor(monkeypatch):
    """Wiring pin: the converted admin-origin site SKIPs on a too-old Node."""
    monkeypatch.setattr(_verdict.shutil, "which", lambda _n: "/usr/bin/node")
    monkeypatch.setattr(_verdict, "node_version", lambda node="node": (22, 6))
    from tests import test_admin_origin_redirect as admin

    with pytest.raises(pytest.skip.Exception):
        admin._run([])


# ── the #3845 fork-safety classifier (#4742/#5049), extracted for #5049 ─────
# It lives in `tests/_fork_safety_verdict.py` rather than in the platform-gated
# evidence file because that file's def count is pinned in
# `config/ci-expected-nodeids/platform-gated.txt`. These pin its POLARITY, which
# is the whole point of the extraction: structural evidence beats a clock, and
# an unobservable property is INCONCLUSIVE — never a pass.
def _must_fail(result: dict, *, match: str) -> None:
    """Assert the classifier FAILs — never PASSes, and never SKIPs.

    The INCONCLUSIVE exit is a `pytest.skip.Exception`, which is NOT an
    `AssertionError`: under the regressed branch ordering the classifier SKIPs,
    so a bare `pytest.raises(AssertionError)` would let that escape as a *green*
    skip — the very regression these pins exist to catch would read as "not
    applicable", and no skip budget covers this file. A skip here is a failure.
    """
    try:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    except pytest.skip.Exception as exc:
        pytest.fail(
            "the classifier reported INCONCLUSIVE where a FAIL is required — "
            f"the regression this pins: {exc}"
        )
    except AssertionError as exc:
        assert re.search(match, str(exc)), str(exc)
        return
    pytest.fail("the classifier PASSED where a FAIL is required")


def test_fixed_race_healthy_result_is_the_pass_shape():
    assert_fixed_race_verdict(
        healthy_result(), race_deadline_s=15.0, socket_budget_s=4.0
    )


def test_fixed_race_parked_child_fails_even_alongside_a_timeout():
    """A parked child is a FAIL, and a client timeout in the SAME run must not
    downgrade it to INCONCLUSIVE (the wedge is structural, not a clock)."""
    result = healthy_result()
    result["hung"] = [(4242, 6.0, 0.0)]
    result["errors"] = 3
    result["timeout_errors"] = 3
    result["last_err"] = "Timeout reading from socket"
    _must_fail(result, match=r"parked.*4242")


def test_fixed_race_non_timeout_refusal_fails_even_alongside_a_timeout():
    """A refusal (the wedge's own signature) is a product FAIL; a later
    timeout in the same run must not mask it as a load-class skip."""
    result = healthy_result()
    result["errors"] = 2
    result["non_timeout_errors"] = 1
    result["last_non_timeout_err"] = "ConnectionError: the fork slot is wedged"
    result["timeout_errors"] = 1
    result["last_err"] = "Timeout reading from socket"
    _must_fail(result, match="non-timeout")


@pytest.mark.parametrize(
    "unobservable", [{"sampling_unavailable": True}, {"forks": None}]
)
def test_fixed_race_refusal_fails_despite_an_unreadable_metric(unobservable):
    """A refusal is read from the CLIENT, so an unusable harness metric (`ps`
    census or `INFO` counter) must not downgrade it to INCONCLUSIVE — the same
    masking the timeout branch is forbidden from doing."""
    result = healthy_result()
    result.update(unobservable)
    result["errors"] = 1
    result["non_timeout_errors"] = 1
    result["last_non_timeout_err"] = "ConnectionError: the fork slot is wedged"
    _must_fail(result, match="non-timeout")


def test_fixed_race_unreadable_census_is_inconclusive_never_a_pass():
    result = healthy_result()
    result["sampling_unavailable"] = True
    with pytest.raises(pytest.skip.Exception) as excinfo:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "census" in message


def test_fixed_race_unreadable_fork_counter_is_inconclusive_never_a_pass():
    result = healthy_result()
    result["forks"] = None
    with pytest.raises(pytest.skip.Exception) as excinfo:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    assert "fork counter" in str(excinfo.value)


def test_fixed_race_setup_budget_expiry_names_the_setup_not_the_counter():
    """A setup-budget expiry leaves ``forks`` at its initial None. If the
    counter branch were read first the cause would be misreported as an
    unreadable counter with ``last error None``; the setup budget must be named.
    """
    result = healthy_result()
    result["forks"] = None
    result["setup_timeout_err"] = "TimeoutError: Timeout reading from socket"
    with pytest.raises(pytest.skip.Exception) as excinfo:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    message = str(excinfo.value)
    assert "setup query" in message
    assert "fork counter" not in message


def test_fixed_race_client_timeout_is_inconclusive():
    result = healthy_result()
    result["errors"] = 1
    result["timeout_errors"] = 1
    result["last_err"] = "Timeout reading from socket"
    with pytest.raises(pytest.skip.Exception) as excinfo:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    assert "INCONCLUSIVE [#5049]" in str(excinfo.value)


@pytest.mark.parametrize(
    "load_event",
    [
        {
            "errors": 1,
            "timeout_errors": 1,
            "last_err": "Timeout reading from socket",
        },
        {"sampling_unavailable": True},
        {"forks": None},
    ],
)
def test_fixed_race_verify_refusal_fails_despite_a_load_event(load_event):
    """The DR-restore leg carries the wedge's own signature too, so a
    non-timeout refusal there FAILs — a race timeout or an unreadable harness
    metric must not mask it."""
    result = healthy_result()
    result.update(load_event)
    result["verify_err"] = "ResponseError: could not fork"
    _must_fail(result, match="verify leg refused")


def test_fixed_race_verify_timeout_is_inconclusive_never_a_fail():
    result = healthy_result()
    result["verify_err"] = "TimeoutError: Timeout reading from socket"
    result["verify_timeout"] = True
    result["clone_nodes"] = None
    with pytest.raises(pytest.skip.Exception) as excinfo:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    assert "INCONCLUSIVE [#5049]" in str(excinfo.value)


def test_fixed_race_wrong_clone_count_fails_despite_a_race_timeout():
    """The clone counts are observed whenever the verify leg completed, so a
    wrong count is a data-integrity FAIL even alongside a load event."""
    result = healthy_result()
    result["errors"] = 1
    result["timeout_errors"] = 1
    result["last_err"] = "Timeout reading from socket"
    result["clone_nodes"] = 0
    _must_fail(result, match="clone_nodes")


# ── #8579 P3-3: the conversion is WIRED, not merely available ──────────────
# The contract pins above prove `require_answered` BEHAVES. None of them can see
# a converted SITE that silently drops the call: that site reverts to reading a
# wall-clock refusal as a product FAIL, and no amount of contract testing
# detects it. This registry pins the wiring itself — each converted seam/test
# must contain a direct `require_answered` call in its own body. Deleting the
# call goes RED here; `test_the_wiring_checker_discriminates` proves the checker
# is not vacuously true.
_REPO_ROOT = Path(__file__).resolve().parent.parent

_CONVERTED_SITES: dict[str, frozenset[str]] = {
    "tests/test_activation_scorecard.py": frozenset(
        {"test_default_window_is_24h"}),
    "tests/test_capture_cost_measurement.py": frozenset(
        {"test_hosted_capture_emits_that_session_s_measured_cost_row"}),
    "tests/test_capture_loop_responsiveness.py": frozenset(
        {"_measure_capture_graph_work"}),
    # `_commit` is the seam, but eight bodies post directly and so bypass it —
    # each is a converted site in its own right (#8579 review P3).
    "tests/test_commit_endpoint.py": frozenset({
        "_commit",
        "test_failed_write_retry_applies_the_counters_once",
        "test_missing_required_field_400",
        "test_missing_client_commit_id_400",
        "test_commit_id_mismatch_422",
        "test_401_unauthenticated",
        "test_commit_oversized_chunked_413",
        "test_commit_malformed_400_preserved",
        "test_commit_empty_400_preserved",
    }),
    "tests/test_github_index_lifecycle.py": frozenset(
        {"_poll_until", "_post_index", "_pump_until_walk_entered",
         "_pump_until_midwalk"}),
    "tests/test_hosted_api.py": frozenset({"test_dream_incremental"}),
    "tests/test_index_docs_api.py": frozenset(
        {"_poll_until", "_pump_until_midwalk",
         "test_cross_team_job_poll_404"}),
    "tests/test_oauth_mcp.py": frozenset(
        {"test_oauth_sessionless_create_point_actor_stamped"}),
    "tests/test_onboarding_endpoints.py": frozenset(
        {"test_off_switch_patch_stops_capture_409"}),
    "tests/test_one_free_org_entitlement.py": frozenset({"_post_signed"}),
    "tests/test_pack_state.py": frozenset(
        {"test_returns_tenant_packs_with_auth",
         "test_empty_masking_when_nothing_to_see"}),
    "tests/test_signup_token_revoke.py": frozenset(
        {"test_registry_revoke_pbkdf2_only_node"}),
    "tests/test_writer_inventory.py": frozenset(
        {"test_signup_provisions_identity_path",
         "test_create_graph_writes_row_supabase_mode",
         # #8000: the second nodeid the #8416 `test (b)` red recorded — the
         # SAME route class as the graph-write site above, left unconverted by
         # #8579 because the issue-measured inventory named only two sites.
         "test_list_my_teams_uses_derived_graphs"}),
}


def _functions_calling(source: str, name: str) -> set[str]:
    """Names of the functions whose body contains a call to ``name``."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            callee = sub.func
            callee_name = (
                callee.id if isinstance(callee, ast.Name)
                else callee.attr if isinstance(callee, ast.Attribute)
                else None
            )
            if callee_name == name:
                found.add(node.name)
                break
    return found


#: The class NAMES a converted file must not construct directly. Matching on the
#: final name — bare or attribute-qualified — is deliberate: an aliased or
#: module-qualified construction (`from httpx import AsyncClient; AsyncClient(...)`
#: or `client_mod.TestClient(...)`) is the SAME un-converted site, and matching
#: only the two spellings this repo happens to use today is how the reversion
#: this registry exists to catch would slip through.
_PLAIN_CLIENT_NAMES = frozenset({"TestClient", "AsyncClient"})


def _plain_client_constructions(source: str) -> int:
    """Count direct `TestClient(...)` / `AsyncClient(...)` CALLS.

    AST, not substring: prose that merely names the class (a docstring, a
    comment such as test_capture_loop_responsiveness.py's note about the shape
    of the `client` fixture) is not a client construction and must not be
    counted as one. The seam's own factories are `verdict_client` /
    `verdict_async_client`, so they never match.
    """
    count = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else None
        )
        if name in _PLAIN_CLIENT_NAMES:
            count += 1
    return count


def test_the_plain_client_counter_ignores_prose_and_sees_aliases():
    """Non-vacuity control: a comment is not a construction, an alias is one."""
    assert _plain_client_constructions("x = TestClient(app)\n") == 1
    assert _plain_client_constructions(
        "x = httpx.AsyncClient(transport=t)\n") == 1
    assert _plain_client_constructions(
        "x = AsyncClient(transport=t)\n") == 1
    assert _plain_client_constructions("x = client_mod.TestClient(app)\n") == 1
    assert _plain_client_constructions(
        "# wraps the app in `TestClient(app)`\n") == 0
    assert _plain_client_constructions("x = _verdict_client(app)\n") == 0
    assert _plain_client_constructions("x = verdict_async_client(app)\n") == 0


def test_the_wiring_checker_discriminates():
    """Non-vacuity control: the checker sees the call, and its absence."""
    with_call = "def a():\n    return require_answered(x, what='w')\n"
    without_call = "def a():\n    return x\n"
    assert _functions_calling(with_call, "require_answered") == {"a"}
    assert _functions_calling(without_call, "require_answered") == set()
    with_seam = "def a():\n    return verdict_client(app)\n"
    assert _functions_calling(with_seam, "verdict_client") == {"a"}
    assert _functions_calling(without_call, "verdict_client") == set()
    with_async_seam = "def a():\n    return verdict_async_client(app)\n"
    assert _functions_calling(with_async_seam, "verdict_async_client") == {"a"}
    assert _functions_calling(without_call, "verdict_async_client") == set()


#: The #8582 CLIENT seam. `_CONVERTED_SITES` cannot be the home for it: that
#: registry identifies a converted site by a `require_answered(` call IN the
#: file, and a client-seam conversion need not leave one (and where a file also
#: carries #8579-era call sites, one may remain and is then covered by the seam
#: as well). Each entry is ``(plain constructions allowed, seam functions that
#: must call a verdict-client factory)``. The allowed count is a DECLARED
#: exception, never slack: any other plain construction in the file has
#: re-opened the class for that file, because the reads it binds go back to
#: reading a refusal as a FAIL. This is the checkable restatement of #8582's
#: Indicator (1), which a plain `grep` cannot discharge for a seam conversion.
_CONVERTED_CLIENT_SEAMS: dict[str, tuple[int, frozenset[str]]] = {
    # ── #8802: the remainder of the #8582 sweep ────────────────────────────
    # Same seam, same declaration: every client these files build routes its
    # responses through `require_answered`, so a transport wait-bound refusal is
    # the contract's INCONCLUSIVE skip rather than an `assert 504 == 200` FAIL.
    # +1 each: the refusal pin's own control client, which must stay PLAIN to
    # observe the 504 it proves is real.
    "tests/test_agent_signup_idempotency.py": (1, frozenset({"client"})),
    # +2: the mount-only MCP parent (`_mounted_test_client` — a local
    # `Mount("/mcp", ...)` Starlette carrying NO `WaitBoundMiddleware`) and the
    # pin's control. That parent cannot see the REST 504 arm, so it is out of
    # THIS sweep's class; the MCP seam's own refusal is a **200** with an error
    # envelope (`mcp_server._await_under_mcp_wait_bound`), which a bare
    # `assert status_code == 200` PASSES on — the false-PASS arm, #8763's
    # subject, not this one. This file makes no `tools/call` request at all.
    "tests/test_auth_flip.py": (2, frozenset({"rest_client"})),
    "tests/test_billing.py": (1, frozenset({"billing_client", "env", "sb"})),
    "tests/test_claim_endpoints.py": (1, frozenset({"client"})),
    # +2: the R-13 rate-limit test's `TestClient(mini)` (a `mini` Starlette with
    # only `RateLimitMiddleware` — no bound at either seam, so none of its reads
    # can be a refusal) and the pin's control.
    "tests/test_commit_endpoint.py": (2, frozenset({"client", "client_no_auth"})),
    "tests/test_dashboard_login.py": (1, frozenset({"client"})),
    "tests/test_data_source_capture.py": (1, frozenset({"client"})),
    "tests/test_delivery_tenancy.py": (1, frozenset({"_sb_env", "_spine_env"})),
    "tests/test_invite_fusion_http.py": (1, frozenset({"client"})),
    # +2: `_mounted_test_client`'s mount-only parent (as in test_auth_flip —
    # no REST 504 arm reaches it; the file's single `tools/call` read carries a
    # `require_answered` call in its own body) and the pin's control.
    "tests/test_oauth_mcp.py": (2, frozenset({"api_client"})),
    "tests/test_onboarding_endpoints.py": (1, frozenset({"client",
                                                         "unauth_client"})),
    # +1: the pin's own control client, which must stay PLAIN to observe the 504.
    "tests/test_capture_loop_responsiveness.py": (1, frozenset({
        "_verdict_async_client"})),
    "tests/test_dr_endpoints.py": (1, frozenset({"client"})),
    "tests/test_invites_http.py": (1, frozenset({"client",
                                                  "_verdict_async_client"})),
    # (the async seam calls `verdict_async_client`, hence the union below)
    # +1: the pin's control client (above).
    "tests/test_writer_inventory.py": (1, frozenset({"client"})),
    # +1: the #3246 capture-retry helper's client, deliberately left plain — it
    # must OBSERVE the 504 to re-issue the request. Built by
    # `TestSessionActorStamp2600._setup` and fed to
    # `_post_capture_allowing_transport_retry`; named by symbol, not by line, in
    # a 12k-line file.
    # +1: this file's own pin control, which must stay plain to observe the 504.
    "tests/test_hosted_api.py": (2, frozenset({"_verdict_client"})),

    # ── #8818: the #8802 remainder, tranche 1 (>= 17 in-class reads) ────────
    # Same seam, same declaration. Each file's IN-CLASS reads bind from a
    # client over the wait-bound hosted app on a route outside the exempt set
    # (`{("POST", "/v1/context")}` + the `/v1/internal/` prefix,
    # `hosted_api._TRANSPORT_WAIT_BOUND_EXEMPT`), so one conversion per client
    # construction turns a transport wait-bound refusal into the contract's
    # INCONCLUSIVE skip instead of an `assert 504 == 200` product FAIL. Not
    # every plain construction a converted file keeps is in class — see this
    # block's `test_github_index_lifecycle.py` entry, whose eleven mock-transport
    # clients are outside it.
    # +1 in every entry below: the refusal pin's own control client, which must
    # stay PLAIN to observe the 504 it proves is real.
    "tests/test_action_endpoints_dual_auth.py": (1, frozenset({"env"})),
    "tests/test_agent_signup.py": (1, frozenset({"client"})),
    "tests/test_capture_session.py": (1, frozenset({
        "consent_client", "test_phase_e_rest_mcp_same_flag_drift_proof"})),
    "tests/test_export_delete.py": (1, frozenset({"reg_client", "sb_client"})),
    # +12: eleven `httpx.AsyncClient(transport=<MockGitHubTransport>)` clients
    # (`_github_mock` — the real-GitHub API stub this file drives) plus the pin
    # control. Those eleven are not ASGI clients over `app`, so no
    # `WaitBoundMiddleware` sits on their path and no read bound to them can be
    # a refusal; their `status_code` reads are stub expectations. The file's
    # only in-class construction is the `client` fixture's, now converted.
    "tests/test_github_index_lifecycle.py": (12, frozenset({"client"})),
    "tests/test_github_connect.py": (1, frozenset({"client", "unauth_client"})),
    "tests/test_import_endpoint.py": (1, frozenset({"sb_client"})),
    "tests/test_onboarding_state_split_checkpoint.py": (1, frozenset({
        "_registered_client"})),
    "tests/test_rotate_key.py": (1, frozenset({"client"})),
    "tests/test_session_extraction_modes.py": (1, frozenset({"client"})),
    # the async half is the file's `_run` (an `ASGITransport(app=app)` burst),
    # converted to `verdict_async_client`; the pin's control is the +1.
    "tests/test_session_key_http.py": (1, frozenset({
        "client", "_run", "test_concurrent_recovery_mints_stay_at_cap"})),
}


@pytest.mark.parametrize("path,expected", sorted(_CONVERTED_CLIENT_SEAMS.items()))
def test_every_converted_client_seam_builds_a_verdict_client(path, expected):
    """#8582: a dropped client-seam conversion goes RED here."""
    plain_allowed, seams = expected
    source = (_REPO_ROOT / path).read_text()
    converters = ({"verdict_client", "verdict_async_client"})
    builds = set().union(
        *(_functions_calling(source, name) for name in converters))
    missing = seams - builds
    assert not missing, (
        f"{path}: {sorted(missing)} no longer build a verdict client — the "
        "#8582 seam was dropped, so a wall-clock refusal at that seam is read "
        "as a product FAIL again"
    )
    plain = _plain_client_constructions(source)
    assert plain == plain_allowed, (
        f"{path}: {plain} plain client construction(s), {plain_allowed} "
        "declared. A converted file's reads are covered by its seam, so an "
        "undeclared plain construction re-opens #8582 for this file — convert "
        "it, or declare the exception here with its reason."
    )


@pytest.mark.parametrize("path,expected", sorted(_CONVERTED_SITES.items()))
def test_every_converted_site_wires_the_verdict_contract(path, expected):
    """#8579 P3-3: a dropped conversion at a converted site goes RED."""
    source = (_REPO_ROOT / path).read_text()
    missing = expected - _functions_calling(source, "require_answered")
    assert not missing, (
        f"{path}: {sorted(missing)} no longer call `require_answered` — the "
        "#5049 conversion was dropped, so a wall-clock refusal at that site is "
        "read as a product FAIL again"
    )
