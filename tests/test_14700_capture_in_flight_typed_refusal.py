"""#14700 — the in-flight 409 NAMES ITSELF, and stays exempt from the carve-outs.

THE DEFECT THIS PINS
--------------------
`POST /v1/sessions` answers TWO different `409`s, and only one of them clears by
waiting:

* the in-flight duplicate (`hosted_api._reserve_capture_slot`, #3129) — the same
  session_id already being captured by a concurrent writer, which the
  `Retry-After: 30` answers; and
* "Session recording is disabled for this team/graph" — an org POLICY the user
  has to reverse, which no retry can clear.

Until #14700 only the first was typed and **neither** carried a `code`, so the
client could not tell them apart. Measured (`~/.tortoise/capture-retry.log`,
2026-11-25T21:29:44Z, session `0d568953-…`):

    [reflect-hook] capture retry STARTED (pid 39569, attempt 2/4, last: HTTP 409 retry_after=30s)

— no `code`, no message, so the refusal cannot be triaged from a log at all, while
its sibling capacity refusal logs `code=capture_capacity_saturated` and the policy
409 would have logged no `retry_after` at all. The reproduced response (real raise
path, real FastAPI serialization) was:

    409  Retry-After: 30
    {"detail":"a capture for this session_id is already in flight — retry shortly"}

WHAT IS PINNED (each a MUTATION TARGET)
---------------------------------------
1. the body is TYPED — `code` + `message` + `retry_after`, the same shape as the
   capacity 429 — while the `Retry-After` header and the pre-#14700 sentence are
   both KEPT (a header-honouring client, a code-branching client and a legacy
   prose-matching client all keep working);
2. the code is DISTINCT from the policy 409's absence of one, which is what makes
   the client's branch decidable instead of a guess;
3. the carve-out key survives the re-typing: `_is_capture_in_flight_refusal`
   recognises the new body AND the legacy bare string, and REJECTS the policy
   refusal — otherwise typing the body would have exempted a real org failure
   from the dashboard's last-error state;
4. there is ONE predicate, imported by `mcp_server` — not a second copy.
"""
from __future__ import annotations

import pytest

import tortoise.hosted_api as ha


def _raise_in_flight(key: str = "team:graph:session-14700"):
    """Hold `key` and return the refusal a second reservation raises."""
    slot = ha._reserve_capture_slot(key)
    try:
        with pytest.raises(ha.HTTPException) as excinfo:
            ha._reserve_capture_slot(key)
    finally:
        slot.release()
    return excinfo.value


def test_the_in_flight_refusal_names_itself_and_the_delay() -> None:
    """`detail` is a TYPED condition — a client can branch on `code`.

    MUTATION THAT REDS THIS: restore `detail=_CAPTURE_SESSION_IN_FLIGHT_DETAIL`
    at the raise site -> `detail` is a `str`, `detail["code"]` raises
    `TypeError`, and the refusal itself still asserts clean, so the red is the
    SHAPE — which is exactly the pre-fix state (#14700).
    """
    exc = _raise_in_flight()

    assert exc.status_code == 409
    # The header is KEPT: an HTTP client that honours `Retry-After` must not
    # lose the instruction to the typed body.
    assert exc.headers is not None
    assert exc.headers["Retry-After"] == "30"

    detail = exc.detail
    assert isinstance(detail, dict), (
        "the in-flight 409 is a bare string again — a client must prose-match it "
        "and cannot tell it from the recording-disabled 409 (#14700)")
    assert detail["code"] == "capture_session_in_flight"
    assert detail["retry_after"] == 30
    # The pre-#14700 sentence survives VERBATIM inside the body, so a legacy
    # reader that matches on prose keeps matching (`resp.text` still contains it)
    # AND the human reading a log can still see what happened.
    assert detail["message"] == ha._CAPTURE_SESSION_IN_FLIGHT_DETAIL
    assert "already in flight" in detail["message"]


def test_the_in_flight_code_is_not_the_policy_409() -> None:
    """The two 409s on this route are now DISTINGUISHABLE by `code`.

    The policy refusal ("Session recording is disabled for this team/graph") is
    raised by `_capture_session_impl` with a plain string detail and no
    `Retry-After`. The point of #14700 is that a client (or an operator reading a
    log) can tell the retryable one from the terminal one; if the in-flight code
    were ever shared with — or dropped in favour of — the policy shape, that
    branch collapses again.
    """
    detail = _raise_in_flight().detail
    assert isinstance(detail, dict)
    # The policy 409 answers a bare string and declares no delay: nothing about
    # it should ever satisfy the in-flight predicate.
    policy = "Session recording is disabled for this team. Enable it in the dashboard"
    assert not ha._is_capture_in_flight_refusal(policy)
    assert detail["code"] != "capture_recording_disabled"


def test_the_carve_out_recognises_the_typed_body_and_the_legacy_string() -> None:
    """The last-error exemption must survive the re-typing — BOTH shapes.

    MUTATION THAT REDS THIS: restore the equality compare
    (`e.detail != _CAPTURE_SESSION_IN_FLIGHT_DETAIL`) in `hosted_api.py` or
    `mcp_server.py` -> the typed body is no longer exempt, so a server
    concurrency condition is written to the org-visible last-error state and
    painted on the dashboard as a capture failure (#3129's carve-out).
    """
    body = _raise_in_flight().detail

    assert ha._is_capture_in_flight_refusal(body), (
        "the typed in-flight body is no longer exempt from the last-error carve-out")
    # A replay/older client can still hand the legacy bare string through.
    assert ha._is_capture_in_flight_refusal(ha._CAPTURE_SESSION_IN_FLIGHT_DETAIL)
    # …and it must not swallow anything else: the policy 409 is a REAL org
    # failure and has to keep reaching the dashboard.
    assert not ha._is_capture_in_flight_refusal(
        {"code": "capture_recording_disabled", "message": "disabled"})
    assert not ha._is_capture_in_flight_refusal("capture boom")
    assert not ha._is_capture_in_flight_refusal(None)


def test_both_surfaces_use_the_one_predicate() -> None:
    """`mcp_server` must IMPORT the predicate, never re-implement it.

    MUTATION THAT REDS THIS: copy the two branches into `mcp_server.py` (or
    restore its `detail != _CAPTURE_SESSION_IN_FLIGHT_DETAIL` compare) -> this
    row reds on the second assertion, and the REST and MCP surfaces then
    disagree about what the in-flight 409 is, which is the drift the shared
    constant existed to prevent in the first place.

    ⛔ `mcp_server`'s import is FUNCTION-LOCAL (the tool body imports from
    `hosted_api` on call, to keep the module's import cheap), so the predicate is
    not a module attribute and an identity assert cannot reach it. The call sites
    are read from the SOURCE of the two entry points instead — that is what the
    mutation changes.
    """
    import inspect

    import tortoise.mcp_server as ms

    rest_src = inspect.getsource(ha.capture_session)
    mcp_src = inspect.getsource(ms.tortoise_session_capture)
    for name, src in (("REST", rest_src), ("MCP", mcp_src)):
        assert "_is_capture_in_flight_refusal(" in src, (
            f"the {name} surface no longer consults the shared carve-out predicate")
        assert "!= _CAPTURE_SESSION_IN_FLIGHT_DETAIL" not in src, (
            f"the {name} surface went back to comparing the detail to the bare "
            f"message — the typed body would stop being exempt (#14700)")
