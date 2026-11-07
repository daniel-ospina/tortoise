"""#10849 — a committed WRITE must not be reported as a FAILURE.

The measured defect: on 2026-11-04 a hosted ``tortoise_create_point`` answered
the wait-budget refusal (``-32009``) after 12.45 s and the Point **existed** on
the next read. The refusal was not a statement about the graph's state, and the
two natural readings of a refusal are both wrong for a write:

* read as "nothing happened" it makes a lane skip a write that LANDED (the
  false negative #10849 reports the fleet's lanes recording on this signal);
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
        "the measured failure modes in this test's docstring")
    assert err["data"]["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_UNSAFE
    assert "do NOT re-send it" in err["data"]["retry_rule"]
    assert "operation" not in err["data"]["retry_rule"], (
        "the rule is about the CALL. `tortoise_create_operator` declares NOTHING "
        "(idempotent=None -> idempotentHint False), so a claim about its operation "
        "would be a claim the registry never made; on the create_point(dedup=False) "
        "arm the same words would contradict a registry that DOES declare it.")
    # ...and the unsafe rule must not send the caller to the recipe that is
    # unsound for this very tool, nor claim what a repeat would DO.
    assert "search" not in err["data"]["retry_rule"]
    assert "duplicate" not in err["data"]["retry_rule"]

    safe = _error(ms._wait_bound_refusal_result("tortoise_create_point"))["data"]
    assert safe["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_SAFE
    assert "converges" in safe["retry_rule"]


def test_retry_safe_is_derived_from_the_registry_not_a_second_list():
    """The retry-safety verdict is the registry's, not a second hand-kept list.

    #10849's measured asymmetry, now machine-readable at the call site:
    ``create_point`` dedups on ``(pointKind, content_hash)`` and ``ingest``
    reaches that same fold for its points, so a props-free repeat CONVERGES;
    ``create_operator`` has no dedup at all, so a repeat DUPLICATES the edge.
    (NOT because of ``batch_id`` — that is a STAMP written after the fact
    (``_stamp_batch_id``), never a dedup key, which is exactly why ingest's
    ``entities`` leg had to be handled separately.)

    (The FAIL-CLOSED half — an unresolvable name is never advertised as safe —
    is pinned separately in ``test_an_unresolvable_write_name_still_fails_closed``;
    this test covers the three resolvable declarations.)
    """
    def safe(name):
        return _error(ms._wait_bound_refusal_result(name))["data"]["retry_safe"]

    assert safe("tortoise_create_point") is True, (
        "create_point is idempotent (dedup=True, content-hash identity), so a "
        "SEQUENTIAL repeat converges — refusing to retry it costs a caller a write "
        "that would have converged")
    assert safe("tortoise_ingest") is False, (
        "ingest is NOT advertised safe: its bundle is open-ended (append-only "
        "entities, a JSON-string spelling, and flat `props` beside `kind`), so "
        "no finite predicate proves it — see the ingest test below")
    assert safe("tortoise_create_operator") is False, (
        "create_operator has NO dedup — a retry writes a second IMPL/NAND and "
        "double-counts the claim's EP weight (#10849)")


def test_a_PROPS_PAYLOAD_defeats_the_dedup_fold_so_it_is_never_called_safe():
    """`props` is an OPEN caller channel onto the node, and it can defeat dedup.

    Measured through the real MCP door, `create_point(content=X,
    props={"is_operator": True})` twice mints TWO nodes with DIFFERENT ids (and
    the nested `props={"props": {...}}` spelling does the same), while the
    props-free control converges on one. The reason is the fold's own predicate:
    it EXCLUDES operator nodes, and `is_operator` is neither server-managed nor
    stripped, so it lands on the node through the plain props passthrough.
    (`op_type` alone does NOT: the fold's second conjunct is `n.is_operator =
    false`, which passes whatever `op_type` holds — measured, SAME id. It is
    still over-blocked here because convergence is not PROVABLE, not because this
    particular key diverges.)

    So a finite allow-list of "safe" prop keys would be re-derived every time a
    prop is added — the hand-maintained parallel list this whole design avoids.
    The sound rule is the cheap one: convergence is PROVEN only for a props-free
    call, and everything else gets UNSAFE. Over-blocking costs a legitimate
    retry; under-blocking duplicates a write, which is the entire defect.
    """
    def safe(tool, args):
        return _error(ms._wait_bound_refusal_result(tool, args))["data"]["retry_safe"]

    assert safe("tortoise_create_point", {"content": "X"}) is True, (
        "the props-free control really does converge")
    # `op_type` and `confidence` do NOT diverge on their own — they are here to
    # pin the rule "any props payload ⇒ not provable", which is the sound one.
    for props in ({"is_operator": True}, {"op_type": "NAND"},
                  {"props": {"is_operator": True}},
                  {"confidence": 0.9}):
        assert safe("tortoise_create_point",
                    {"content": "X", "props": props}) is False, (
            f"props={props} makes convergence unprovable")
    # The nested spelling is the same channel one fold deeper — the outer dict is
    # non-empty, so it is a payload either way. (Measured under `dedup=True`, this
    # spelling also returns two DIFFERENT ids on calls 1 and 2; the verdict below
    # is false because convergence is not PROVABLE, which is the claim, and it
    # does not rest on that measurement.)
    assert safe("tortoise_create_point",
                {"content": "X", "props": {"props": {"is_operator": True}}}) is False
    # ⛔ And `props` is not the only argument the handler folds into the props
    # dict: `authoredBy` / `credibility` are merged into it before the write, so a
    # call carrying neither `props` NOR a folded sibling is the only props-free
    # shape. Measured:
    # `create_point(content=x, authoredBy="me", dedup=True)` twice returns the
    # same id but the second lands on the dedup-hit UPDATE leg, emits
    # `PointRevised` and bumps `updatedAt` — so the repeat is visible to a reader
    # even though it converged, and "repeating it converges" is the wrong promise.
    for sibling in ({"authoredBy": "me"}, {"credibility": 0.8},
                    {"credibility": 0}, {"credibility": 0.0}):
        assert safe("tortoise_create_point", {"content": "X", **sibling}) is False, (
            f"{sibling} is folded into props by the handler — note `credibility=0`"
            " is a real tier (T0), and `0 == False` in Python, so a truthiness test"
            " reads it as absent while the handler folds it (`0 is not None`)")


def test_the_sibling_fold_probe_answers_a_real_bool_not_the_raw_value():
    """The `-> bool` return of `_call_folds_siblings_into_props` is load-bearing.

    Ruff's SIM103 asked for this two-predicate body to be inlined, and its own
    "Fix safety" note is why the `bool(...)` wrap has to survive that rewrite:
    `arguments.get("authoredBy")` yields the RAW value (`"me"`), so the unwrapped
    form leaks a string out of a `-> bool` function. Downstream that stays
    invisible until it is not: the value feeds `_write_retry_safety`, whose
    `retry_safe` cell callers and this suite compare with `is True` / `is False`.

    So this pins the two things the simplification must not lose: the return is a
    real `bool` (the unwrapped form returns `"me"`), and the two predicates stay
    DIFFERENT — `authoredBy` on truthiness (`if authoredBy:`), `credibility` on
    presence (`if credibility is not None:`), because `credibility=0` is the real
    T0 tier and `0 == False` in Python. Collapsing either one is how this rule
    failed OPEN before.
    """
    folds = ms._call_folds_siblings_into_props

    assert folds({"authoredBy": "me"}) is True
    assert isinstance(folds({"authoredBy": "me"}), bool), (
        "the raw `authoredBy` value must not escape — SIM103's fix-safety note is "
        "exactly this hazard, and the `bool(...)` wrap is what prevents it")
    assert folds({"credibility": 0}) is True, "`credibility=0` is T0, not absent"
    assert folds({"credibility": 0.0}) is True, "same value, float spelling"
    assert folds({"authoredBy": ""}) is False, (
        "the handler folds `authoredBy` on TRUTHINESS, so an empty string is not "
        "folded — presence is the OTHER predicate's rule")
    assert folds({"credibility": None}) is False, "an explicit None is not folded"
    assert folds({}) is False
    assert folds({"content": "X"}) is False, (
        "the props-free shape is the only one convergence is provable for")
    assert folds(None) is False, "a non-dict bundle carries no named argument"


def test_a_STATE_conditional_writer_is_never_called_safe():
    """`tortoise_mitigate_operator` converges only for a CURRENT mitigation.

    Its own registry description says so: "Idempotent for a CURRENT mitigation …
    once it has been withdrawn (retracted/superseded/outdated/archived) a
    re-assertion mints a NEW mitigation Point and returns its id." So convergence
    depends on the mitigation's stored STATE, which no argument predicate can
    see — the same ruling this PR already applied to `tortoise_onboarding_seed`.
    Advertising it repeatable told a caller "repeating it converges" about a
    retry that mints a second mitigation (and applies it to the operator's edge
    strength twice).
    """
    data = _error(ms._wait_bound_refusal_result(
        "tortoise_mitigate_operator", {"target": "x", "reason": "r", "strength": 0.8}))["data"]
    assert data["committed"] == "unknown"
    assert data["retry_safe"] is False
    assert data["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_UNSAFE


def test_the_unsafe_rule_speaks_about_the_CALL_never_the_operation():
    """The arm's population is MIXED, so the rule cannot speak about the operation.

    Most of the arm is write-set tools that declare no idempotency at all
    (`create_operator`, `ingest`, …), where "this operation is NOT idempotent"
    would be TRUE. But the arm also takes declared-idempotent tools whose
    arguments defeated convergence — `create_point(dedup=False)`, and
    `create_point` carrying `props`/`authoredBy`/`credibility` — where the
    registry DOES say the operation converges and the tool is served with
    `idempotentHint: true`. One wording has to be true of both, so it says what
    holds on every arm (this call is not safe to repeat) and makes no claim about
    the operation — matching this file's own vocabulary: the declaration says the
    OPERATION converges, the ARGUMENTS decide whether this CALL does.
    """
    assert ms._WAIT_BOUND_RETRY_RULE_UNSAFE.startswith("this call is NOT safe to repeat"), (
        "the UNSAFE rule must be about this call, not about the operation")
    assert "operation" not in ms._WAIT_BOUND_RETRY_RULE_UNSAFE, (
        "a claim about the OPERATION is false on the create_point(dedup=False) arm,"
        " where the registry declares the operation idempotent")
    # the SAFE rule MAY name the operation: it is reachable only when the
    # declaration AND the arguments both hold. It must also name the CONDITION its
    # promise rests on — the registry's flag is a SEQUENTIAL claim, and the dedup
    # is a check-then-write with no uniqueness guard, so a retry that overlaps the
    # abandoned write can mint a second row (measured: 14 of 20 rounds on the
    # embedded lane, identical content_hash; the absent fourth precondition of
    # #5064 = #9879).
    safe_rule = ms._WAIT_BOUND_RETRY_RULE_SAFE
    assert "converges when repeated" in safe_rule
    assert "may still be in flight" in safe_rule, (
        "the promise must be bounded by the condition it holds under")
    # ...and it must NOT name a mechanism only SOME members have: the rule ships
    # for three tools now (`create_point`, `checkpoint`, `create_source`), and
    # `create_source` converges in a single conditional MERGE rather than a
    # pre-read. The check-then-write census belongs in the rationale, not in a
    # shipped promise.
    assert "check-then-write" not in safe_rule
    assert "uniqueness guard" not in safe_rule


def test_the_verdict_is_per_CALL_not_per_name():
    """`create_point(dedup=False)` must NOT ship "repeating it converges".

    `tortoise_create_point` is idempotent BECAUSE of its `dedup` argument, which
    the caller controls: `dedup=False` mints a second Point with identical
    content instead of folding onto the existing one (its own docs say so, and
    `sdk.py` gates the whole dedup branch on `if dedup:`). A name-only verdict
    would therefore hand `retry_safe: true` to the exact duplicate this contract
    exists to prevent — on the exact tool #10849 measured.
    """
    def data(**args):
        return _error(ms._wait_bound_refusal_result("tortoise_create_point",
                                                    args))["data"]

    assert data()["retry_safe"] is True, "an absent dedup keeps the tool default"
    assert data(dedup=True)["retry_safe"] is True
    assert data(dedup=False)["retry_safe"] is False, (
        "dedup=False diverges — a retry writes a second Point")
    assert data(dedup=False)["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_UNSAFE
    # ⛔ The STRING spellings reach the handler as `False`: `dedup` is declared
    # `{"type": "boolean"}` in the served schema and FastMCP coerces before the
    # handler runs — `"false"`/`"False"`/`"0"`/`"no"` all arrive as `False`
    # (measured; the same coercion `tests/test_10084_graph_disclosure.py` pins
    # for `dry_run`). The refusal reads the RAW pre-validation dict, so it must
    # fail closed on a non-bool rather than trust Python truthiness — a
    # truthiness test would call `"false"` converging and license a duplicate.
    for spelling in ("false", "False", "0", "no"):
        assert data(dedup=spelling)["retry_safe"] is False, (
            f"dedup={spelling!r} reaches the handler as False and mints a "
            f"second Point — it must not be advertised as safe to retry")


def test_a_preview_writes_nothing_and_says_so():
    """`dry_run=True` on a destructive writer: committed "none", and re-sendable.

    A preview mutates nothing by construction (#10084's recorded contract), so
    "the write may have committed" is false for it — and "do NOT repeat it"
    forbids the one safe action, which is the action that matters most for a
    preview of a destructive operation.
    """
    data = _error(ms._wait_bound_refusal_result(
        "tortoise_delete_point", {"dry_run": True}))["data"]
    assert data["committed"] == "none"
    assert data["retry_safe"] is True
    assert data["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_PREVIEW
    # ...and the same tool WITHOUT dry_run is a real mutation.
    real = _error(ms._wait_bound_refusal_result(
        "tortoise_delete_point", {}))["data"]
    assert real["committed"] == "unknown"
    assert real["retry_safe"] is False


def test_a_forged_dry_run_prop_cannot_claim_a_preview():
    """The preview arm is gated on the tool's DECLARED `dry_run` parameter.

    A tool that does not declare `dry_run` must never be reported as a preview,
    however the caller spells the request — otherwise a forged "nothing was
    written" sits over an executed write (the #3926 / #8135 class).

    ⛔ The discriminating case is the SECOND assertion, not the first. A tool
    outside the write set carries no `committed` key at all (its refusal is
    `{"retry_after": 10}` plus the neutral message), so the `props` case alone
    could pass under a gate-deleted implementation — `props` is simply not read
    either way. `tortoise_create_point` DOES declare nothing for `dry_run` and IS
    in the write set, so a real top-level `dry_run=True` there is the case that
    separates "gated on the declared parameter" from "reads the raw dict".
    """
    forged = _error(ms._wait_bound_refusal_result(
        "tortoise_create_point", {"props": {"dry_run": True}}))["data"]
    assert forged["committed"] == "unknown"
    # ...and the reader does not see `props` at all, so the payload-echo vector
    # belongs to the payload reader, not here. Pinned by asserting the gate's
    # actual rule: a non-dry_run tool stays write-ambiguous even when the spelling
    # is a real boolean at the TOP level (which no pydantic coercion can rescue).
    top_level = _error(ms._wait_bound_refusal_result(
        "tortoise_create_point", {"dry_run": True}))["data"]
    assert top_level["committed"] == "unknown", (
        "`tortoise_create_point` does not declare `dry_run`, so a top-level "
        "`dry_run=True` must not be read as a preview")


def test_a_COERCED_true_is_a_preview_and_a_coerced_false_is_not():
    """The refusal reads the RAW dict; the handler runs AFTER pydantic coerces it.

    `{"dry_run": "true"}` is a spelling an LLM client emits naturally and the
    served schema accepts; FastMCP coerces it to `True`, so the handler takes its
    preview branch and every write sits behind `if not dry_run` — nothing is
    written. Reading only `value is True` reported `committed: "unknown"` +
    "this call is NOT safe to repeat" for it, i.e. told a caller an unexecuted
    preview may have committed and forbade re-sending it.

    ⛔ The asymmetry is the point: only the TRUE direction is admitted. Reading
    `"false"`/`"0"` as `False` would let a caller's SPELLING move the verdict
    toward "nothing was written"; leaving them unread can only fall through to
    the conservative write arm.
    """
    def committed(v):
        return _error(ms._wait_bound_refusal_result(
            "tortoise_backfill_v25", {"dry_run": v}))["data"]["committed"]

    for truthy in (True, "true", "TRUE", "1", 1, 1.0, "yes", "on"):
        assert committed(truthy) == "none", f"{truthy!r} coerces to True -> preview"
    # ⛔ and the FALSE half, plus everything the coercion REJECTS, stays
    # conservative. `" true "` is here on purpose: the coercion does not STRIP, so
    # pydantic refuses that spelling outright — the call never reaches the handler,
    # so nothing is written either way and the conservative answer costs nothing.
    for not_true in ("false", "0", False, 0, 0.0, None, "", "nonsense", " true "):
        assert committed(not_true) == "unknown", (
            f"{not_true!r} must NOT be read as a preview — the fail-closed direction")


def test_a_no_dispatch_arm_claims_nothing_was_sent():
    """When no dispatch was ever started, "the write may have committed" is FALSE.

    Both arms that reach the refusal without scheduling a dispatch (the
    ``transport_refused`` short-circuit and the argument-check fall-through) are
    knowable at the call site. Reporting them as ``"unknown"`` + "do NOT repeat
    it" told a caller to reconcile a write it never sent and to withhold the safe
    re-send — i.e. it silently dropped the write.
    """
    result = ms._wait_bound_refusal_result(
        "tortoise_create_operator", {}, dispatched=False)
    data = _error(result)["data"]
    # ...and the SERVED PROSE has to match the payload on this arm too: the
    # dispatched-write message says the write "may still complete", which is
    # false when no dispatch exists.
    assert _text(result) == ma._TRANSPORT_WAIT_BOUND_NOT_SENT_MESSAGE
    assert "nothing was written" in _text(result)
    assert data["committed"] == "none"
    assert data["retry_safe"] is True
    assert data["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_NOT_SENT
    # the same tool, actually dispatched, is a real mutation
    real = _error(ms._wait_bound_refusal_result(
        "tortoise_create_operator", {}))["data"]
    assert real["committed"] == "unknown" and real["retry_safe"] is False


def test_the_two_call_delete_preview_counts_as_a_preview():
    """`tortoise_delete`'s FIRST call writes nothing, and it is the most
    destructive tool on the surface.

    Its own contract: with no `confirmation` it "CHANGES NOTHING: it reports …
    and returns a short-lived confirmation token bound to this id"
    (`_preview_delete`; the token is signed and persists nothing). Reading only
    `dry_run` reported that call as a possible mutation and told the caller not
    to re-send — which is the one call the delete cannot proceed without.
    """
    first = _error(ms._wait_bound_refusal_result("tortoise_delete", {"id": "x"}))["data"]
    assert first["committed"] == "none"
    assert first["retry_safe"] is True
    assert first["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_PREVIEW
    # ...a CONFIRMED call is a real deletion.
    done = _error(ms._wait_bound_refusal_result(
        "tortoise_delete", {"id": "x", "confirmation": "tok"}))["data"]
    assert done["committed"] == "unknown"
    assert done["retry_safe"] is False


def test_create_document_is_not_advertised_as_repeatable():
    """The door mints a fresh ulid per call; `_upsert_document` MERGEs on it.

    So a retry writes a SECOND Document Source and a second `DocumentCreated`
    (#2296). The registry declared `idempotent=True` on the strength of the
    description ("keyed by url") rather than the code — the same false
    retry-safety claim this branch corrected for `create_operator`.
    """
    data = _error(ms._wait_bound_refusal_result("tortoise_create_document"))["data"]
    assert data["retry_safe"] is False
    assert data["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_UNSAFE


def test_the_write_message_names_both_homes_of_the_payload():
    """A wrap-envelope write tool re-homes the payload to `_meta.tortoise_error`.

    `_schema_error_result` DROPS `structuredContent` when the refusal violates a
    declared output schema — and `tortoise_delete_entity` is exactly that tool. A
    message that pointed only at `error.data` sent its caller to a field that is
    not there, on a destructive tool, where the committed/retry_rule distinction
    is the whole value of the change.
    """
    msg = ma._TRANSPORT_WAIT_BOUND_WRITE_MESSAGE
    assert "error.data.committed" in msg
    assert "_meta.tortoise_error.data.committed" in msg


def test_an_ingest_bundle_with_append_only_entities_is_not_repeatable():
    """POINTS converge in `ingest`; `entities` of type document/event do NOT.

    The SDK shape-validates the entity leg but DEDUPS nothing on it: "Document
    records are append-only — never deduped", same for `event`
    (`docs/INGEST_CONTRACT.md` row 1 `append_only_items`). So a retry mints a
    NEW `:Source` for a document — and a new `:Event` occurrence for an event,
    which can also gain a NEW operator where the bundle wires one to it (a
    document cannot: a `:Source` is refused as a connection endpoint — the ERRORS
    table's row 6, `Endpoint typing`).
    `batch_id` is a STAMP, not a dedup key, so it does not save it.

    The verdict that follows from those facts is NOT "read the bundle and decide"
    — that was the first attempt, and it lost three rounds running, each time to a
    spelling it did not enumerate. The bundle's key space is open-ended, so the
    seam reports "not provable" (UNSAFE) for every ingest call. The cases below
    are kept as the record of WHY: each is a concrete input that a bundle-reading
    predicate had to grow a branch for.
    """
    def data(bundle):
        return _error(ms._wait_bound_refusal_result(
            "tortoise_ingest", {"bundle": bundle}))["data"]

    # ⛔ Even the innocent-looking points-only bundle reports UNSAFE, and that is
    # the RESOLUTION rather than an oversight: three review rounds each closed ONE
    # spelling of this hole (the append-only entities leg, the JSON-string bundle,
    # and the FLAT `props` splat), which is the signal that the enumeration is the
    # defect. `sdk.py:26647` documents the points form as `{kind, content, ref?,
    # status?, **props}` — so `is_operator` sits BESIDE `kind`, not in `props`, and
    # no key allow-list sees it. The bundle's key space is open-ended, so the seam
    # reports what it knows ("not provable") instead of a claim it cannot ground.
    for bundle in (
        {"points": [{"ref": "p1", "kind": "statement", "content": "x"}]},
        {"points": [{"kind": "statement", "content": "y", "is_operator": True}]},
        {"entities": [{"type": "subject", "name": "s"}]},
    ):
        assert data(bundle)["retry_safe"] is False, (
            f"ingest must never claim convergence for an open-ended bundle: {bundle}")
    # ⛔ The bundle's SPELLING is caller-controlled and parsed AFTER the seam:
    # `_parse(bundle)` in the handler accepts a JSON string, and the served schema
    # puts no `type` on `bundle`. Reading only the dict spelling was measured to
    # return retry_safe=true for a JSON-string bundle carrying a Document — the
    # exact duplicate this contract exists to prevent, forged from one argument.
    import json as _json
    for bad in ({"type": "document", "name": "d"}, {"type": "event", "name": "e"}):
        assert data(_json.dumps({"entities": [bad]}))["retry_safe"] is False, (
            f"a JSON-string bundle is parsed by the handler, so {bad['type']} "
            "still reaches the append-only writer")
    # an unreadable bundle is not evidence that it writes nothing.
    assert data("{not json")["retry_safe"] is False
    assert data('["a list"]')["retry_safe"] is False

    # ⛔ The SDK strips and lowercases the type on both legs, so a case-variant
    # reaches the append-only writer — a case-sensitive gate here fails OPEN.
    for bad in ({"type": "document", "name": "d"}, {"type": "event", "name": "e"},
                {"type": "Document", "name": "d"}, {"type": " DOCUMENT ", "name": "d"},
                {"type": "EVENT", "name": "e"}):
        assert data({"entities": [bad]})["retry_safe"] is False, (
            f"an append-only {bad['type']} entity mints a new node on retry")
    # (a subject/object entity resolves-or-mints on its name and would converge on
    # its own — but `ingest` ships a BUNDLE, so the open-ended sibling items above
    # are what decides the verdict. There is no per-item safe arm.)


def test_an_argument_that_LOOKS_read_only_is_not_taken_at_face_value():
    """There is no "this call only reads" arm, and here is why.

    `tortoise_manage_sources(action="list")` looks like a pure read, and an
    earlier revision of this fix had an arm that said so — `committed: "none"`,
    `retry_safe: true`, "This call only reads — it writes nothing, so no mutation
    is in flight". A reviewer
    falsified it: `_sources_view()` → `_onboarding_state()` → on a hosted org
    `_get_onboarding_state`, whose registry leg runs
    `_cas_write_onboarding_state(org_id, state, 0)` — a real
    `SET t.onboarding_state = $state, t.state_version = $expected + 1` that
    MATERIALISES the state when it is missing ("Auto-initializes to defaults if
    missing"). So the arm could have committed a write while telling the caller
    nothing could have been.

    `tortoise_onboarding_seed`'s no-name call was ruled out PRE-EMPTIVELY for the
    same reason (a COMPACT org has no person gap, so the same no-name call falls
    through to `seed_onboarding_anchors`) — that exclusion is the only thing ever
    written about it: no arm for it was added and then removed.

    ⛔ `tortoise_onboarding_seed` has NO ARM HERE ANY MORE, and its absence is
    deliberate rather than an omission: #4282 Phase 3.2 REMOVED the tool from
    the registry, so it is in neither `TOOL_REGISTRY` nor
    `RETIRED_TOOL_REGISTRY` — and `_wait_bound_refusal_result` emits
    `committed`/`retry_safe`/`retry_rule` only for a name in the SERVED write
    set, so an arm naming it would assert a payload the seam no longer builds
    for it (measured: `KeyError: 'committed'`). The property it existed for is
    still carried, by the `tortoise_manage_sources` arm above — the surviving
    ``looks-like-a-read`` case, and the one whose state-conditional write the
    reviewer actually falsified. It stays UNSAFE, so the rule count stays four.

    The rule itself is unchanged: both exclusions are STATE-conditional, which
    no argument predicate can see.
    """
    listing = _error(ms._wait_bound_refusal_result(
        "tortoise_manage_sources", {"action": "list"}))["data"]
    assert listing["committed"] == "unknown"
    assert listing["retry_safe"] is False
    assert listing["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_UNSAFE


def test_a_tool_whose_DECLARED_default_is_the_preview_is_recognised():
    """`tortoise_backfill_v25(dry_run: bool = True)` — the no-arg call is a preview.

    Its most common invocation is a read-only schema inspection, and it writes
    nothing: every write in the SDK door sits behind `if not dry_run`. A check
    for an EXPLICIT `dry_run=True` misses it entirely and reports "a schema
    migration may be half-applied; do not re-run".
    """
    no_args = ms._wait_bound_refusal_result("tortoise_backfill_v25", {})
    assert _error(no_args)["data"]["committed"] == "none"
    assert _error(no_args)["data"]["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_PREVIEW
    # an explicit opt-in to the real run is a real write.
    real = _error(ms._wait_bound_refusal_result(
        "tortoise_backfill_v25", {"dry_run": False}))["data"]
    assert real["committed"] == "unknown"


def test_approve_merge_is_not_advertised_as_repeatable():
    """Its dedup branch appends an event with no already-reviewed guard.

    The TEMPORAL branch is guarded ("re-approving with the SAME action is a
    no-op — no duplicate event"); the DEDUP branch falls through to
    `_emit_event("DedupeRecorded"|"DedupeRejected", …)`, which writes a journal
    line AND a graph event record — so a retry is visible twice to `poll_events`.
    """
    data = _error(ms._wait_bound_refusal_result("tortoise_approve_merge"))["data"]
    assert data["retry_safe"] is False
    assert data["retry_rule"] == ms._WAIT_BOUND_RETRY_RULE_UNSAFE


def test_a_getter_that_RE_STORES_a_clock_dependent_value_is_not_repeatable():
    """`tortoise_get_source_reliability` looks like a getter and is not one.

    It refreshes a cache on the Source node, and the value it refreshes is
    CLOCK-dependent — `mean = alpha / (alpha + beta)` over a recency-decayed
    prior whose `now` defaults to the wall clock — so a repeat past the cache
    interval re-derives and RE-STORES a different float (measured: 0.65087406 →
    0.65087345 at +1 h → 0.65043060 at +30 d). An earlier revision of this PR
    declared it repeatable on "a repeat converges on the same value", which that
    measurement falsifies.

    It is the same property this repo already uses to keep the `maintenance`
    tier non-idempotent — `tortoise_compute_confidence`'s own note is "call 2
    starts from the value call 1 stored (0.693315 -> 0.693320), so repeated calls
    DO have an additional effect". So the `write` tier default stands: a repeat is
    a real write, and the refusal says so.
    """
    assert _error(ms._wait_bound_refusal_result(
        "tortoise_get_source_reliability"))["data"]["retry_safe"] is False


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
