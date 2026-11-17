"""#10893 — a breached wait budget must NAME the stage it died in.

The defect was never only the latency. A breached request came back with one
sentence — *"The server's wait budget for this request was exceeded before a
response was ready. The work may still complete on the server."* — and that
sentence cannot distinguish:

* this request's own read path being slow (the encode? the graph round trip?),
* this request's read path being FINE and the event loop blocked by some other
  request's work while this one waited for its first instruction,
* the answer being already assembled and merely late.

Every re-measurement recorded on the issue picked a different one of those from
the same text, from outside the process. ``tortoise/read_stages.py`` makes the
server say which one it is, on the refusal, on the breach telemetry and on the
log line.

What this file pins, in order of what would break silently:

1. the SHARED-MUTABLE-OBJECT property — the stages are marked on a read-lane
   worker reached through a context COPY, and the timeline the transport seam
   reads is the one the worker mutated. If the ContextVar ever carried a VALUE
   instead of a reference, every breach would report ``pre_handler`` and the
   instrument would be a confident lie;
2. the CLOSED VOCABULARY — an unknown stage name never publishes;
3. the ADDITIVE-ONLY wiring — a request with no timeline bound is
   byte-identical to the pre-#10893 refusal, and the refusal's IDENTITY
   (``code`` + the exact neutral ``message``) never moves on any arm, because
   ``tests/_verdict.py`` classifies a wait-bound refusal by exactly that pair;
4. the ALLOWLIST registration — an unregistered analytics prop is stripped in
   silence (#3821), which here would rebuild the blindness the change removes.
"""
from __future__ import annotations

import ast
import asyncio
import contextvars
import json
import os
import threading
from pathlib import Path

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from tortoise import hosted_api as ha
from tortoise.mcp_auth import _TRANSPORT_WAIT_BOUND_MESSAGE
from tortoise.read_stages import (
    STAGE_EMBED_ENCODE,
    STAGE_EMBED_GET,
    STAGE_PRE_HANDLER,
    STAGE_RESPONSE,
    STAGE_RETRIEVAL,
    STAGE_SET,
    STAGES,
    ReadStages,
    begin,
    mark,
    request_stages,
    stage_view,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SDK = _REPO_ROOT / "tortoise" / "sdk.py"


# ── helpers ─────────────────────────────────────────────────────────────────

def _collect_send(messages: list):
    async def send(message):
        messages.append(message)
    return send


def _decode(messages: list) -> dict:
    return json.loads(messages[-1]["body"].decode())


# ── 1. the shared-mutable-object property (the load-bearing one) ────────────

def test_a_mark_inside_a_copied_context_reaches_the_transport_seam():
    """The read path runs on a worker through a context COPY.

    ``hosted_api._read_offload`` submits ``tortoise_fts_query`` to the read lane
    under ``monitoring.confine_context``, i.e. a FRESH ``Context``. A stage
    written into the ContextVar from that copy would never be visible to the
    seam that reads it at the deadline. It is visible because the ContextVar
    carries a REFERENCE to one ``ReadStages`` and the worker mutates the object.

    This is the test that would red if someone "simplified" the module to store
    the current stage in the ContextVar itself.
    """
    timeline = begin()

    # Bound on the "request" thread; the worker runs a COPY of that context —
    # exactly what ``_read_offload``'s ``monitoring.confine_context`` does. ⛔ The
    # copy is taken INSIDE the binding and on the REQUEST thread, for two
    # separate reasons: taken outside it, the copy carries no timeline; taken
    # inside a ``threading.Thread``, it would copy the thread's OWN empty
    # context (a new thread does not inherit its parent's), and the assertion
    # below would fail for a reason that has nothing to do with the property
    # under test.
    def _worker():
        ctx.run(mark, STAGE_EMBED_GET)
        ctx.run(mark, STAGE_EMBED_ENCODE)

    with request_stages(timeline):
        ctx = contextvars.copy_context()
        t = threading.Thread(target=_worker)
        t.start()
        t.join()

    # Read from the ORIGINAL context, exactly as the middleware does.
    assert timeline.current() == STAGE_EMBED_ENCODE
    assert STAGE_EMBED_GET in timeline.spent_ms()


def test_the_open_stage_is_distinct_from_the_spent_stages():
    """``spent_ms`` reports FINISHED stages; ``current`` reports the incident."""
    timeline = begin()
    with request_stages(timeline):
        mark(STAGE_EMBED_GET)
        mark(STAGE_RETRIEVAL)
        snapshot = stage_view()

    assert snapshot["stage"] == STAGE_RETRIEVAL
    # The open stage's elapsed time is NOT an observation — it is the incident.
    assert STAGE_RETRIEVAL not in snapshot["stages_ms"]
    assert STAGE_EMBED_GET in snapshot["stages_ms"]



def test_remecking_the_same_stage_folds_rather_than_duplicating():
    """A dense EXPANSION pass re-enters retrieval; the total must not double-count."""
    timeline = begin()
    with request_stages(timeline):
        mark(STAGE_EMBED_GET)
        mark(STAGE_RETRIEVAL)
        mark(STAGE_RETRIEVAL)  # the #2518 expansion pass — a no-op re-mark
        mark(STAGE_RESPONSE)

    spent = timeline.spent_ms()
    # `pre_handler` legitimately appears: a few µs elapse between `begin()` and
    # the first mark, and on a real request that interval is the auth/middleware
    # cost — the very thing "the bound expired before any stage ran" reports.
    assert set(spent) == {STAGE_PRE_HANDLER, STAGE_EMBED_GET, STAGE_RETRIEVAL}
    assert STAGE_RESPONSE not in spent


# ── 2. the closed vocabulary ────────────────────────────────────────────────

def test_an_unknown_stage_is_refused_rather_than_published():
    """The vocabulary is CLOSED: a dashboard groups by stage, so a typo must not
    become a new one. ``mark`` is on the read path — it must refuse SILENTLY,
    never raise into a request."""
    timeline = begin()
    with request_stages(timeline):
        mark("embed_encode_typo")
        mark(STAGE_EMBED_ENCODE)
        snapshot = stage_view()

    assert snapshot["stage"] == STAGE_EMBED_ENCODE
    assert "embed_encode_typo" not in json.dumps(snapshot)


def test_the_vocabulary_is_a_closed_set_of_named_stages():
    assert STAGES == tuple(dict.fromkeys(STAGES)), "duplicate stage name"
    assert STAGE_SET == frozenset(STAGES)
    # Every stage the read path marks is a member — a `mark` call naming a
    # non-member is a silent no-op, which is exactly how an instrument dies.
    assert {STAGE_PRE_HANDLER, STAGE_EMBED_GET, STAGE_EMBED_ENCODE,
            STAGE_RETRIEVAL, STAGE_RESPONSE} <= STAGE_SET


# ── 3. the boundary: nothing bound ⇒ nothing changes ───────────────────────

def test_unbound_marks_are_a_noop_and_publish_nothing():
    assert stage_view() is None
    assert mark(STAGE_RETRIEVAL) is None          # does not raise
    assert stage_view() is None


def test_the_wire_view_withholds_an_attribution_for_a_read_that_never_started():
    """``pre_handler`` + no accrued time is the operator's answer, not the
    caller's: no stage was slow, so there is no lever for the caller to pull,
    and publishing it would put noise on a wire contract three shipped tests
    pin to exactly ``{"detail": …}``."""
    timeline = begin()
    with request_stages(timeline):
        # Nothing marked — the bound expired before the read path started.
        assert timeline.wire_snapshot() is None
        # …while telemetry still reports the (informative) stage.
        assert stage_view() == {"stage": STAGE_PRE_HANDLER, "stages_ms": {}}


def test_the_wire_view_publishes_once_a_stage_has_been_marked():
    timeline = begin()
    with request_stages(timeline):
        mark(STAGE_EMBED_ENCODE)
        wire = timeline.wire_snapshot()
    assert wire is not None
    assert wire["stage"] == STAGE_EMBED_ENCODE


def test_the_binding_does_not_leak_out_of_its_block():
    timeline = begin()
    with request_stages(timeline):
        assert stage_view() is not None
    assert stage_view() is None


# ── 4. the refusal is ADDITIVE on both surfaces ────────────────────────────

def test_the_rest_refusal_is_byte_identical_when_no_timeline_is_bound():
    """The unbound shape must not move at all: callers that know nothing about
    stages are the overwhelming majority and #3834's contract is theirs."""
    messages: list = []
    asyncio.run(ha._send_wait_bound_refusal(
        _collect_send(messages), {"type": "http", "headers": [], "method": "GET"},
        "/v1/search"))
    body = _decode(messages)
    assert set(body) == {"detail"}
    assert body["detail"] == _TRANSPORT_WAIT_BOUND_MESSAGE


def test_the_rest_refusal_names_the_stage_when_one_is_bound():
    messages: list = []
    asyncio.run(ha._send_wait_bound_refusal(
        _collect_send(messages), {"type": "http", "headers": [], "method": "GET"},
        "/v1/search",
        stages={"stage": STAGE_EMBED_ENCODE,
                "stages_ms": {STAGE_EMBED_GET: 12.0}}))
    body = _decode(messages)
    # The shipped `detail` is unchanged — the stage rides ADDITIVELY beside it.
    assert body["detail"] == _TRANSPORT_WAIT_BOUND_MESSAGE
    assert body["stage"] == STAGE_EMBED_ENCODE
    assert body["stages_ms"] == {STAGE_EMBED_GET: 12.0}


def test_the_rest_body_is_untouched_for_a_timeline_that_never_started():
    """The gate the shipped pinned-shape tests rely on: a timeline whose read
    never marked a stage must NOT reach the body."""
    timeline = begin()
    with request_stages(timeline):
        view = timeline.wire_snapshot()
    messages: list = []
    asyncio.run(ha._send_wait_bound_refusal(
        _collect_send(messages), {"type": "http", "headers": [], "method": "GET"},
        "/v1/search", stages=view))
    assert set(_decode(messages)) == {"detail"}


def test_the_jsonrpc_refusal_keeps_its_envelope_and_gains_the_stage():
    """``code`` + the exact neutral ``message`` is the refusal's IDENTITY
    (``tests/_verdict.py::is_transport_wait_bound_refusal``). The stage goes in
    ``data``, beside ``retry_after``, and must not touch either half."""
    messages: list = []
    asyncio.run(ha._send_wait_bound_refusal(
        _collect_send(messages), {"type": "http", "headers": [], "method": "POST"},
        "/mcp", stages={"stage": STAGE_RETRIEVAL, "stages_ms": {}}))
    error = _decode(messages)["error"]
    assert error["code"] == -32009
    assert error["message"] == _TRANSPORT_WAIT_BOUND_MESSAGE
    assert error["data"]["stage"] == STAGE_RETRIEVAL
    assert "retry_after" in error["data"]


def test_the_mcp_tool_refusal_carries_the_stage_without_touching_its_envelope():
    from tortoise.mcp_server import _wait_bound_refusal_result

    plain = _wait_bound_refusal_result("tortoise_search")
    staged = _wait_bound_refusal_result(
        "tortoise_search",
        stages={"stage": STAGE_EMBED_ENCODE, "stages_ms": {STAGE_EMBED_GET: 4.0}})

    plain_err = plain.structured_content["error"]
    staged_err = staged.structured_content["error"]
    assert staged_err["code"] == plain_err["code"]
    assert staged_err["message"] == plain_err["message"] == _TRANSPORT_WAIT_BOUND_MESSAGE
    assert "stage" not in plain_err["data"]      # additive only
    assert staged_err["data"]["stage"] == STAGE_EMBED_ENCODE
    assert staged_err["data"]["stages_ms"] == {STAGE_EMBED_GET: 4.0}
    assert staged_err["data"]["retry_after"] == plain_err["data"]["retry_after"]


def test_a_write_refusal_carries_the_stage_beside_its_committed_verdict():
    """``stage`` is a fact about the WAIT; ``committed`` is a fact about the
    WRITE. They cannot contradict, so both ride — the write arm included."""
    from tortoise.mcp_server import _wait_bound_refusal_result

    staged = _wait_bound_refusal_result(
        "tortoise_create_point", dispatched=False,
        stages={"stage": STAGE_PRE_HANDLER, "stages_ms": {}})
    data = staged.structured_content["error"]["data"]
    assert data["stage"] == STAGE_PRE_HANDLER
    assert data["committed"] == "none"


# ── 5. the analytics row ────────────────────────────────────────────────────

def test_the_breach_telemetry_carries_the_stage_attribution(monkeypatch):
    """The row that exists to explain ``latency_ms`` must also explain WHAT the
    latency was. Captured at the one emit site's own writer."""
    captured: dict = {}

    real = ha._track_analytics_event

    def _fake(org_id, event, props):
        captured["event"] = event
        captured["props"] = dict(props)
        return real(org_id, event, props)

    # The emit dispatches to the telemetry pool; drive it synchronously instead
    # of racing a daemon thread.
    monkeypatch.setattr(ha, "_track_analytics_event", _fake)
    monkeypatch.setattr(
        "tortoise.monitoring.control_plane_worker",
        lambda _name: type("W", (), {"submit": staticmethod(
            lambda fn: (fn(), _Done())[1])})())

    ha._emit_wait_bound_breach(
        "org", "/v1/search", "GET", 24000,
        stages={"stage": STAGE_EMBED_ENCODE,
                "stages_ms": {STAGE_EMBED_GET: 8123.0}})

    assert captured["props"]["latency_ms"] == 24000
    assert captured["props"]["read_stage"] == STAGE_EMBED_ENCODE
    assert captured["props"]["read_stages_ms"] == {STAGE_EMBED_GET: 8123.0}


def test_the_breach_telemetry_emits_no_stage_keys_when_none_is_bound(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda org, ev, props: captured.update(props=dict(props)))
    monkeypatch.setattr(
        "tortoise.monitoring.control_plane_worker",
        lambda _name: type("W", (), {"submit": staticmethod(
            lambda fn: (fn(), _Done())[1])})())

    ha._emit_wait_bound_breach("org", "/v1/search", "GET", 24000)

    assert "read_stage" not in captured["props"]
    assert "read_stages_ms" not in captured["props"]


def test_the_stage_props_are_allowlisted():
    """An unregistered prop is stripped in silence (#3821) — the #3359 loss
    mode. These two carry the whole point of the change."""
    assert "read_stage" in ha._ALLOWED_ANALYTICS_PROPS
    assert "read_stages_ms" in ha._ALLOWED_ANALYTICS_PROPS
    assert STAGES, "the vocabulary must be non-empty for the claim above"


class _Done:
    """Stand-in for the future ``_emit_wait_bound_breach`` tracks."""

    def add_done_callback(self, _cb):
        return None

    def cancelled(self):
        return False

    def exception(self):
        return None


# ── 6. the read path actually marks ────────────────────────────────────────

def _marks_inside_fts_query() -> set[str]:
    """AST pass: the ``_mark_read_stage(...)`` call sites in ``tortoise_fts_query``.

    Structural on purpose. The alternative is to drive a real hybrid read, which
    needs a live graph AND the embeddings extra — and would then pass on a tree
    where the encode mark had been dropped from the one path that matters, if the
    test's own fixture happened to take a different branch. What this pins is the
    shape a refactor silently loses.
    """
    tree = ast.parse(_SDK.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "tortoise_fts_query":
            marks: set[str] = set()
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "_mark_read_stage"
                        and inner.args
                        and isinstance(inner.args[0], ast.Name)):
                    marks.add(inner.args[0].id)
            return marks
    raise AssertionError("tortoise_fts_query not found in tortoise/sdk.py")


def test_the_search_path_marks_every_stage_the_issue_asked_about():
    """The issue's own question — "which stage exceeds the budget: FTS, the
    embedding call, the vector query, or the RRF merge" — is answerable only if
    each of those is marked. A missing mark is a stage the breach can never name."""
    marked = _marks_inside_fts_query()
    for constant in ("STAGE_EMBED_GET", "STAGE_EMBED_ENCODE", "STAGE_RETRIEVAL",
                     "STAGE_FUSION", "STAGE_EP_ANNOTATION", "STAGE_RESPONSE"):
        assert constant in marked, f"{constant} is never marked in tortoise_fts_query"


def test_the_middleware_binds_the_timeline_around_the_handler_task():
    """The binding must straddle ``ensure_future`` — a timeline bound AFTER the
    task is created is not in the task's context, and every breach would then
    publish ``pre_handler`` for a read that ran perfectly."""
    source = (_REPO_ROOT / "tortoise" / "hosted_api.py").read_text(encoding="utf-8")
    bind = source.index("_bind_read_stages(request_stages)")
    spawn = source.index(
        "task = asyncio.ensure_future(self.app(scope, receive, _guarded_send))")
    assert bind < spawn, "the read-stage binding must precede the task creation"
