"""#10849 — a committed WRITE must not be reported as a FAILURE.

The measured defect: on 2026-11-04 a hosted ``tortoise_create_point`` answered
the wait-budget refusal (``-32009``) after 12.45 s and the Point **existed** on
the next read. The refusal was not a statement about the graph's state, and the
two natural readings of a refusal are both wrong for a write:

* read as "nothing happened" it makes a lane skip a write that LANDED (the
  fleet's ``GRAPH-WRITE-DEFERRED`` false negative);
* read as "failed, so retry" it duplicates every NON-idempotent write —
  ``tortoise_create_operator`` has no dedup, and a duplicated IMPL/NAND
  double-counts the claim's EP weight in ``ep.py``.

The mechanism is in the product, not in the network: ``mcp_server``'s seam
ABANDONS the dispatch on a breach (it must — cancelling it runs the hosted
handlers' SDK-closing ``finally`` under work still in flight, #2988 / #3718), so
the abandoned write keeps running and can commit after the caller is told the
request failed. Nothing about that is fixable by raising the bound, and the
bound is owner-pinned (D-12, #3834) — so the fix is the CONTRACT:

* a WRITE refusal says the outcome is ``"unknown"`` instead of implying failure;
* it carries the registry's own retry-safety verdict for that tool, so a caller
  never has to re-derive it from prose (and a new writer defaults to UNSAFE);
* it carries a retry RULE, with the registry's own idempotency verdict as the
  gate — never a read-back recipe, because no single recipe is sound across the
  write surface (see `test_a_breached_write_ships_a_retry_rule_not_a_fabricated_recipe`).

Every assertion here is about a caller being able to DECIDE. The envelope
(``code``, the ``tortoise_wait_bound`` meta marker, ``data.retry_after``) is
pinned unchanged alongside, because ``tests/_verdict.py`` classifies a
wait-bound refusal by that envelope and a restructured one would be read as a
genuine product failure on a busy host — the rule-1 violation #5049 exists to
remove.
"""

from __future__ import annotations

import asyncio

import pytest

from tortoise import mcp_auth as ma
from tortoise import mcp_server as ms
from tortoise.mcp_auth import ERR_TIMEOUT


def _error(result) -> dict:
    """The machine-readable half of a refusal, on whichever channel carries it.

    A tool with no wrap envelope keeps it at ``structuredContent.error``; a
    wrap-envelope tool has its payload re-homed under ``_meta.tortoise_error``
    by ``_schema_error_result`` (#8482). Reading both here is what lets the
    wrap-envelope case below be a real regression pin rather than a claim.
    """
    payload = getattr(result, "structured_content", None)
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return payload["error"]
    meta = getattr(result, "meta", None) or {}
    inner = meta.get("tortoise_error")
    assert isinstance(inner, dict), (
        "the refusal carries no error mapping on either channel — a caller has "
        f"nothing to branch on: structured_content={payload!r} meta={meta!r}")
    return inner


def _text(result) -> str:
    """The refusal's human-readable half, whichever shape the SDK gave it.

    ``ToolResult.content`` is a list of ``TextContent`` blocks on the wire, but
    a plain string is accepted on construction — normalise both here so an
    assertion about PROSE never depends on which one it got.
    """
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    return "\n".join(getattr(c, "text", "") for c in (content or []))


# ── the contract a caller of a breached WRITE must be able to read ─────────


def test_a_breached_write_never_reads_as_a_plain_failure():
    """``committed: "unknown"`` — the seam cannot know, and saying so is the fix.

    This is the assertion that fails on the pre-fix tree: the shipped refusal
    carried only ``retry_after``, so a caller had no field to read and the
    refusal *was* a failure claim by construction.
    """
    err = _error(ms._wait_bound_refusal_result("tortoise_create_point"))
    assert err["data"]["committed"] == "unknown", (
        "a breached write must not be presentable as a plain failure — the "
        "dispatch is ABANDONED, not cancelled, so it can still commit")


def test_a_breached_write_does_not_reuse_the_neutral_read_message():
    """Prose is not a decision procedure, but it must not assert a falsehood.

    The read message tells a caller to retry "only if repeating the operation
    is safe" — true, but it never says a WRITE may have landed, so the caller's
    default reading is 'it failed'. The write refusal says it explicitly, on the
    HUMAN channel (``content``).

    ⛔ ``error.message`` stays NEUTRAL on both arms. It is the refusal's
    IDENTITY, and ``tests/_verdict.py`` matches it by exact equality — a write
    prose there made the fleet's verdict contract miss every refused write, i.e.
    turn a busy-host refusal into a false red charged to the product.
    """
    write = ms._wait_bound_refusal_result("tortoise_create_point")
    read = ms._wait_bound_refusal_result("tortoise_search")
    assert _text(write) == ma._TRANSPORT_WAIT_BOUND_WRITE_MESSAGE
    assert _text(read) == ma._TRANSPORT_WAIT_BOUND_MESSAGE
    assert _text(write) != _text(read)
    assert "NOT a statement that nothing was written" in _text(write)
    # ...and the identity is shared, so every existing detector still fires.
    assert _error(write)["message"] == ma._TRANSPORT_WAIT_BOUND_MESSAGE
    assert _error(read)["message"] == ma._TRANSPORT_WAIT_BOUND_MESSAGE


def test_a_write_refusal_is_still_recognised_by_the_fleet_verdict_contract():
    """The pin for the trap above, against the contract itself.

    ``tests/_verdict.py`` is what stops a wait-bound refusal on a loaded host
    from being reported as a product failure. It is a REAL consumer, outside
    this module, and it matches ``error.message`` by exact equality — so this
    cross-module assertion is the only thing that keeps the write arm from
    silently falling out of it (#5049 / #8579).
    """
    import json

    from tests._verdict import is_transport_wait_bound_refusal

    class _Resp:
        status_code = 200

        def __init__(self, text):
            self.text = text

    for name in ("tortoise_create_point", "tortoise_search"):
        result = ms._wait_bound_refusal_result(name)
        frame = {"jsonrpc": "2.0", "id": 1, "result": {
            "isError": True,
            "structuredContent": result.structured_content,
            "_meta": result.meta,
        }}
        resp = _Resp("data: " + json.dumps(frame))
        assert is_transport_wait_bound_refusal(resp) is True, (
            f"the fleet verdict contract no longer recognises a {name} refusal "
            "— a busy host would surface it as a product failure")


def test_a_breached_write_ships_a_retry_rule_not_a_fabricated_recipe():
    """The machine-actionable half is the RULE, and there is no generic recipe.

    A single read-back recipe cannot be sound across the write surface, and both
    failure modes are measured:

    * `tortoise_search` indexes a Point's `content` + `search_keys`, while
      `create_operator` writes a `:Point` with NO `content` — so the recipe
      returns nothing for every created operator, "nothing" reads as "it did not
      commit", and the retry duplicates the edge. That is the exact tool #10849
      measured.
    * and the meaning is not even uniform across the destructive tier: for a
      hard delete / terminal-marking write a HIT means it did NOT commit, while
      for `update_point` presence carries no information at all — the node
      exists before the write, so a HIT is what a not-committed update produces
      as much as a committed one (`hit => committed` is unsound), and a generic
      revision of an already-terminal point commits while staying outside the
      default result set.

    So the refusal ships no `verify` recipe and never instructs a retry on the
    strength of a read-back. Shipping one would have been worse than shipping
    nothing: it would instruct the duplicate this change exists to prevent.
    """
    err = _error(ms._wait_bound_refusal_result("tortoise_create_operator"))
    assert "verify" not in err["data"], (
        "a generic read-back recipe is unsound across the write surface — see "
        "the two measured failure modes in this test's docstring")
    assert err["data"]["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_UNSAFE
    assert "do NOT repeat it" in err["data"]["retry_rule"]
    # ...and the unsafe rule must not send the caller to the recipe that is
    # unsound for this very tool, nor claim what a repeat would DO.
    assert "search" not in err["data"]["retry_rule"]
    assert "duplicate" not in err["data"]["retry_rule"]

    safe = _error(ms._wait_bound_refusal_result("tortoise_create_point"))["data"]
    assert safe["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_SAFE
    assert "converges" in safe["retry_rule"]


def test_retry_safe_is_derived_from_the_registry_and_fails_closed():
    """The retry-safety verdict is the registry's, not a second hand-kept list.

    #10849's measured asymmetry, now machine-readable at the call site:
    ``create_point`` dedups on ``(pointKind, content_hash)`` and ``ingest`` on a
    content-derived ``batch_id``, so a repeat CONVERGES; ``create_operator`` has
    no dedup at all, so a repeat DUPLICATES the edge.

    An unresolvable name must never be advertised as safe: this flag is what a
    caller acts on to skip the manual reconciliation, so a wrong ``true`` is a duplicate.
    """
    def safe(name):
        return _error(ms._wait_bound_refusal_result(name))["data"]["retry_safe"]

    assert safe("tortoise_create_point") is True, (
        "create_point is idempotent (dedup=True, content-hash identity) — "
        "refusing to retry it costs a caller a write that would have converged")
    assert safe("tortoise_ingest") is True, (
        "ingest is exactly-once via a content-derived batch_id")
    assert safe("tortoise_create_operator") is False, (
        "create_operator has NO dedup — a retry writes a second IMPL/NAND and "
        "double-counts the claim's EP weight (#10849)")


def test_an_unresolvable_write_name_still_fails_closed():
    """The derivation's defensive default, pinned at the function.

    ``WRITE_TOOL_NAMES`` is itself derived from the registry, so a listed name
    that the registry cannot resolve is not reachable today — which is exactly
    why the default needs its own assertion. This flag is what a caller acts on
    to skip the manual reconciliation, so the day the two sets diverge the answer must be
    "not safe", never a duplicate-producing ``True``.
    """
    assert ms._write_retry_safety("tortoise_not_a_real_tool") is False


# ── the envelope must not move: existing readers classify by it ────────────


def test_the_refusal_envelope_is_unchanged_so_the_verdict_contract_still_reads_it():
    """The write contract is ADDITIVE.

    ``tests/_verdict.py`` recognises a wait-bound refusal by this envelope on
    the real SSE wire. A change that renames the code, drops the meta marker,
    or moves ``retry_after`` turns a busy-host refusal into a genuine product
    failure in every caller's eyes — including the fleet's own lanes.
    """
    for name in ("tortoise_create_point", "tortoise_search"):
        result = ms._wait_bound_refusal_result(name)
        assert result.is_error is True
        assert result.meta == {ms._WAIT_BOUND_META_KEY: True}
        err = _error(result)
        assert err["code"] == ERR_TIMEOUT
        assert err["data"]["retry_after"] == ma._TRANSPORT_WAIT_RETRY_AFTER_S


def test_a_read_refusal_is_byte_identical_to_the_shipped_refusal():
    """No widening for reads.

    A read mutated nothing, so a read refusal may present as a failure and must
    stay exactly as shipped — the retry protocol of every existing reader is
    written against it.
    """
    err = _error(ms._wait_bound_refusal_result("tortoise_search"))
    assert err["data"] == {"retry_after": ma._TRANSPORT_WAIT_RETRY_AFTER_S}
    assert set(err) == {"code", "message", "data"}


def test_an_unattributed_refusal_keeps_the_neutral_shape():
    """The seam can still be entered without a name (defensive default).

    An unnamed refusal must not guess: guessing WRITE would put a mutation
    warning on every ordinary refusal, guessing READ would hide one. It keeps
    the neutral shipped shape.
    """
    err = _error(ms._wait_bound_refusal_result())
    assert err["data"] == {"retry_after": ma._TRANSPORT_WAIT_RETRY_AFTER_S}
    assert _text(ms._wait_bound_refusal_result()) == ma._TRANSPORT_WAIT_BOUND_MESSAGE


# ── the path that could silently drop the new fields (wrap envelope, #8482) ─


def test_a_wrap_envelope_tool_keeps_the_write_contract_when_the_payload_is_rehomed():
    """``_schema_error_result`` moves the error mapping to ``_meta.tortoise_error``.

    That repair exists because a wrap-envelope tool's payload can violate its
    declared output schema, and it DROPS ``structuredContent`` when it does. It
    re-homes the mapping wholesale — so the write contract survives — but a
    future repair that rebuilt the mapping field-by-field would drop
    ``committed``/``retry_safe``/``retry_rule`` on exactly the tools most likely to
    be written to, and every other test in this file would still pass.
    """
    result = ms._wait_bound_refusal_result("tortoise_create_point")
    repaired = ms._schema_error_result(result, "tortoise_create_point",
                                       {"properties": {"result": {"type": "object"}}})
    err = _error(repaired)
    assert err["data"]["committed"] == "unknown"
    assert err["data"]["retry_safe"] is True
    assert err["data"]["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_SAFE


# ── end to end through the real seam, with a real write tool name ──────────


@pytest.fixture
def slow_dispatch(monkeypatch):
    """A dispatch that OUTLIVES the bound, under a registered WRITE tool name.

    The seam is driven directly with ``tortoise_create_point`` rather than a
    registered probe tool: the defect is a property of the write SURFACE (which
    names are in ``WRITE_TOOL_NAMES``), so a synthetic name would test the
    branch without testing the classification.
    """
    finished: list = []

    async def _slow(name, arguments, *, version=None, run_middleware=True,
                    task_meta=None):
        await asyncio.sleep(0.3)
        finished.append(name)
        return ms.ToolResult(content="{}", structured_content={"ok": True})

    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    monkeypatch.setattr(ms, "_original_call_tool", _slow)
    # Telemetry is not the unit under test, and its writer imports the whole
    # hosted FastAPI app — the exact cost #3834 G1 moved off the loop.
    monkeypatch.setattr(ms, "_emit_mcp_wait_bound_breach_off_loop",
                        lambda *a, **k: None)
    try:
        yield finished
    finally:
        ms._pending_mcp_wait_bound.clear()


@pytest.mark.asyncio
async def test_the_seam_returns_the_write_contract_and_still_abandons(slow_dispatch):
    """The whole point, at the seam: the caller learns it is ambiguous — and the
    write is NOT cancelled.

    Both halves matter. If the dispatch were cancelled the ambiguity would
    disappear (nothing could commit) but so would every slow write; the product
    chose ABANDON deliberately (#2988/#3718), so the contract has to carry the
    consequence, which is what this change does.
    """
    result = await ms._await_under_mcp_wait_bound(
        "tortoise_create_point", {}, version=None, task_meta=None)

    err = _error(result)
    assert err["code"] == ERR_TIMEOUT
    assert err["data"]["committed"] == "unknown"
    assert err["data"]["retry_safe"] is True
    assert err["data"]["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_SAFE

    for _ in range(150):
        if slow_dispatch:
            break
        await asyncio.sleep(0.02)
    assert slow_dispatch == ["tortoise_create_point"], (
        "the breached dispatch was cancelled, not abandoned — an abandoned "
        "write is exactly why the refusal cannot claim failure")
