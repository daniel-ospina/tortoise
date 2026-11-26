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

Before #14700 **neither** `409` was typed (the capacity `429` was) and neither carried
a `code`, so the client could not tell them apart. Measured (`~/.tortoise/capture-retry.log`,
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
   from the dashboard's last-error state, AND inverting the predicate would have
   blacked out every genuine failure. The predicate's ARMS are pinned by a unit
   row; the REST carve-out's POLARITY is pinned BEHAVIOURALLY (the write must be
   skipped for this refusal and made for a real one), which no source-substring
   assertion can discriminate (`hosted_api.py`'s `capture_session` is driven
   through the app);
4. there is ONE predicate, imported by `mcp_server` — not a second copy;
5. the WIRE body is `{"detail": {...}}` — the nested envelope FastAPI produces —
   so a client reading `resp.json()["detail"]["code"]` gets the code, and
   `retry_after` agrees with the `Retry-After` header on the same response.
"""
from __future__ import annotations

import asyncio

import pytest

import tortoise.hosted_api as ha

#: The sentence a LEGACY prose-matcher keys on, as a literal — the compat promise
#: is that text a consumer already matches KEEPS matching, so the assertion has to
#: be against bytes fixed here, not against `hosted_api`'s own constant (#14700
#: review, P3): a constant-to-constant compare moves both sides at once, so
#: rewording the constant would keep that row green while breaking every matcher.
_PRE_14700_IN_FLIGHT_SENTENCE = (
    "a capture for this session_id is already in flight — retry shortly")

#: The REAL policy `409` sentence (`hosted_api._capture_session_impl`, the
#: team branch) — no `code`, no `retry_after`, cleared by a human, not by
#: waiting. Copied verbatim so the mirror row below drives the shape the route
#: can actually emit, rather than a string no raise site produces.
#:
#: ⛔ THIS IS A COPY, SO ITS `isinstance(...)` GRADES NOTHING (#14700 review, P3):
#: it is decidable when written and stays green under every edit to the
#: production refusal. The row's claim is carried by the two
#: `_is_capture_in_flight_refusal(...)` calls below it, which DO vary.
_POLICY_409_DETAIL = (
    "Session recording is disabled for this team. Enable it "
    "in the dashboard (Memory sources > Agent sessions) or "
    "via tortoise_manage_sources(action=\"enable\", "
    "source=\"agent_sessions\") to capture sessions.")


def _capture_org() -> dict:
    """An org the handler admits without a credential or graph lookup.

    `legacy_full_access` is `_require_scope`'s documented short-circuit (the
    same override `test_12992`'s wire row uses), so the handler reaches the
    admission gate with no DB and no network — which is what lets the carve-out
    rows below drive the REAL route instead of a stub.
    """
    return {"org_id": "org-14700-carve-out", "key_id": None,
            "legacy_full_access": True, "tier": "free",
            "max_points": 100000, "max_sessions": None}


def _capture_body(session_id: str = "s-14700-carve-out"):
    from tortoise.hosted_api import SessionRequest

    return SessionRequest(
        conversation=[{"role": "user", "content": "hi"}],
        session_id=session_id)


def _drive_capture_session(org: dict, body):
    """Call the REAL route handler in-process (no TestClient, no lifespan).

    `request` is only forwarded to `_capture_session_impl`, which both callers
    here replace or never reach, so `None` is honest rather than a stub.
    """
    return asyncio.run(ha.capture_session(body, None, org))


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
    assert detail["message"] == _PRE_14700_IN_FLIGHT_SENTENCE
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
    # The policy 409 answers a bare STRING and declares no delay: nothing about
    # it should ever satisfy the in-flight predicate, and — the real asymmetry,
    # read off the shape rather than off an invented literal — it carries no
    # `code` at all, so a client cannot branch on one. ⛔ (No PRODUCTION raise
    # site emits `capture_recording_disabled` — the only occurrence in the tree
    # is the rejection INPUT asserted BELOW — so asserting it as though
    # the route could emit it proved nothing about the production refusal,
    # #14700 review.)
    policy = _POLICY_409_DETAIL
    assert not ha._is_capture_in_flight_refusal(policy)
    assert not ha._is_capture_in_flight_refusal({"message": policy})


def test_the_carve_out_recognises_the_typed_body_and_the_legacy_string() -> None:
    """The last-error exemption must survive the re-typing — BOTH shapes.

    ⛔ THIS ROW PINS THE PREDICATE'S ARMS ONLY. It does NOT catch a carve-out
    CALL SITE reverted to the old equality compare: reverting `hosted_api.py`'s
    or `mcp_server.py`'s call site leaves `_is_capture_in_flight_refusal`
    untouched, so every assertion here stays green. That revert is caught by
    `test_both_surfaces_use_the_one_predicate`; the REST arm's POLARITY (a call
    kept but inverted) is caught by the two behavioural rows below. Stating a
    mutant that does not red the row is how a later reader concludes a guard is
    covered when it is covered elsewhere — or not at all (agent-infra#3442,
    rule 2; #14700 review finding 4).

    MUTATION THAT REDS THIS (each on the predicate body):
    * `return False` for a dict            -> the typed body stops being exempt;
    * dropping the non-dict arm            -> the legacy bare string stops being exempt;
    * `return True` for any dict           -> the policy refusal is swallowed.
    """
    body = _raise_in_flight().detail

    assert ha._is_capture_in_flight_refusal(body), (
        "the typed in-flight body is no longer exempt from the last-error carve-out")
    # The legacy arm guards a REVERT of the raise site (not a client or a
    # replay — no value but a server-raised detail reaches the predicate).
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


# ── The REST carve-out's POLARITY, driven through the route ───────────────

def test_the_in_flight_409_is_NOT_written_to_the_org_last_error(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The REST carve-out SKIPS the write for the in-flight refusal.

    ⛔ WHY A BEHAVIOURAL ROW AND NOT A SOURCE GREP (this is the gap the review
    measured): `test_both_surfaces_use_the_one_predicate` asserts the call site
    MENTIONS `_is_capture_in_flight_refusal(` and does not carry the old equality
    compare. Both survive the mutant that keeps the call and deletes its `not`
    — the substring is still there, the forbidden compare is still absent — and
    under that mutant the in-flight 409 is written to the org-visible last-error
    state. Nothing in the tree varied with that polarity on the REST arm.

    MUTATION THAT REDS THIS: delete the `not` at `hosted_api.py`'s REST carve-out
    (`and _is_capture_in_flight_refusal(e.detail)`). The in-flight body then
    satisfies the guard, `_record_capture_last_error` is called, and the
    assertion below fails — the substring rows stay GREEN, which is why they
    cannot carry this claim.
    """
    org = _capture_org()
    body = _capture_body()
    written: list[tuple] = []
    monkeypatch.setattr(ha, "_CAPTURE_SESSIONS", {})
    monkeypatch.setattr(ha, "_CAPTURE_IN_FLIGHT", 0)
    monkeypatch.setattr(ha, "_record_capture_last_error",
                        lambda *a, **k: written.append(a))

    # The REAL raise site: a second reservation for a key a live capture holds.
    key = ha._capture_session_key(org, body.session_id)
    assert key is not None, "the session key must resolve for this org/session"
    holder = ha._reserve_capture_slot(key)
    try:
        with pytest.raises(ha.HTTPException) as excinfo:
            _drive_capture_session(org, body)
    finally:
        holder.release()

    assert excinfo.value.status_code == 409
    # Precondition: this IS the in-flight refusal, not some other 409 — a row
    # that skipped the write for the wrong exception would prove nothing.
    assert ha._is_capture_in_flight_refusal(excinfo.value.detail), (
        f"the refusal raised here is not the in-flight one: {excinfo.value.detail!r}")
    assert written == [], (
        "the in-flight 409 was written to the org-visible last-error state — a "
        "server concurrency condition is being painted on the dashboard as a "
        f"capture failure (#3129's carve-out, #14700): {written}")


def test_a_genuine_capture_failure_IS_written_to_the_org_last_error(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """…and the SAME guard still writes a REAL failure. The other polarity.

    Without this row the carve-out could be satisfied by skipping every write
    (`if False`), which is the dashboard blackout in the opposite direction.

    MUTATION THAT REDS THIS: `return True` in `_is_capture_in_flight_refusal` for
    any dict, or widening the predicate to swallow the policy sentence — the
    genuine org failure stops reaching the dashboard, and the `written` assertion
    fails. A predicate that matches nothing (`return False`) is honoured here and
    caught by the row above.
    """
    org = _capture_org()
    body = _capture_body("s-14700-genuine")
    written: list[tuple] = []
    monkeypatch.setattr(ha, "_CAPTURE_SESSIONS", {})
    monkeypatch.setattr(ha, "_CAPTURE_IN_FLIGHT", 0)
    monkeypatch.setattr(ha, "_record_capture_last_error",
                        lambda *a, **k: written.append(a))
    # The harness resolution reads the graph; stub the two seams so this row is
    # about the carve-out, not about a store.
    monkeypatch.setattr(ha, "_observed_capture_harness",
                        lambda *_a, **_k: "claude-code")
    async def _no_stored_harness(*_a, **_k):
        return None
    monkeypatch.setattr(ha, "_stored_session_harness", _no_stored_harness)

    # The policy 409 the route really raises: a bare string, no `code`.
    async def _raise_policy(*_a, **_k):
        raise ha.HTTPException(status_code=409, detail=_POLICY_409_DETAIL)
    monkeypatch.setattr(ha, "_capture_session_impl", _raise_policy)

    with pytest.raises(ha.HTTPException) as excinfo:
        _drive_capture_session(org, body)

    assert excinfo.value.detail == _POLICY_409_DETAIL
    assert not ha._is_capture_in_flight_refusal(_POLICY_409_DETAIL)
    assert len(written) == 1, (
        "a genuine org capture failure was NOT written to the last-error state — "
        f"the dashboard would show nothing (#3129, #14700): {written}")
    org_id, harness, detail = written[0]
    assert org_id == org["org_id"]
    assert harness == "claude-code"
    assert detail == _POLICY_409_DETAIL


# ── The WIRE shape, asserted through the app (not the exception object) ───

def test_the_wire_body_is_the_nested_detail_envelope_with_the_header_agreeing(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """What a CLIENT parses: `{"detail": {"code", "message", "retry_after"}}`.

    Every row above reads the `HTTPException`. That is one layer short of the
    response: the body is produced by FastAPI's `http_exception_handler`, so "the
    exception carries a dict" and "the wire answers the nested envelope" are two
    claims. The PR body got this wrong once (it showed the FLAT object, so a
    client following it would read `body["code"]` and get nothing), and the
    repo's own client parses the nested form
    (`capture_spool._parsed_refusal_code`), so the wire shape is pinned here.

    MUTATION THAT REDS THIS: drop `headers={"Retry-After": ...}` at the raise
    site (the header assertion), or re-type `detail` as the pre-#14700 string
    (`.json()["detail"]["code"]` raises), or publish a `retry_after` that
    disagrees with the header — the body/header agreement is asserted, not
    implied.

    No DB is touched: admission is the FIRST thing the handler does after the
    scope check, so the refusal is answered before any state is written (the
    ordering `_reserve_capture_slot`'s docstring is about), and
    `legacy_full_access` satisfies `_require_scope` with no credential lookup.
    """
    from fastapi.testclient import TestClient

    from tortoise.hosted_api import app, get_current_org_gated

    monkeypatch.setattr(ha, "_CAPTURE_SESSIONS", {})
    monkeypatch.setattr(ha, "_CAPTURE_IN_FLIGHT", 0)
    monkeypatch.setattr(ha, "_record_capture_last_error",
                        lambda *a, **k: pytest.fail(
                            "the in-flight 409 is a server condition — it must "
                            f"not write the org last-error state: {a!r}"))

    org = _capture_org()
    app.dependency_overrides[get_current_org_gated] = lambda: org
    session_id = "s-14700-wire"
    key = ha._capture_session_key(org, session_id)
    assert key is not None
    holder = ha._reserve_capture_slot(key)
    try:
        with TestClient(app) as client:
            response = client.post("/v1/sessions", json={
                "conversation": [{"role": "user", "content": "hi"}],
                "session_id": session_id})
    finally:
        holder.release()
        app.dependency_overrides.pop(get_current_org_gated, None)

    assert response.status_code == 409, response.text
    # The header a plain HTTP client honours, on the RESPONSE.
    assert response.headers.get("retry-after") == "30", response.headers
    body = response.json()
    assert isinstance(body, dict) and "detail" in body, (
        f"the wire body is not the envelope FastAPI produces: {body!r}")
    detail = body["detail"]
    assert isinstance(detail, dict), (
        "the wire body's `detail` is not a JSON object — a client cannot read "
        f"`detail.code` (got {type(detail).__name__}: {detail!r})")
    assert detail["code"] == "capture_session_in_flight"
    # Body and header must not disagree: one contract, two carriers.
    assert detail["retry_after"] == int(response.headers["retry-after"])
    assert detail["message"] == _PRE_14700_IN_FLIGHT_SENTENCE
    assert "already in flight" in detail["message"]
