"""#12500 — `min_confidence` / `order_by` (and `kind` on the tag arm) were
accepted by `tortoise_query` and DROPPED on the arms that cannot apply them.

The tool's signature takes ``min_confidence`` and ``order_by``, and its docstring
presents them as general arguments — but only the ``text=`` arm consumes them
(forwarded into ``sdk.tortoise_fts_query``). Both structural arms call
``sdk.paginated_query(kind, skip, limit, include_retracted, **filters)``, whose
signature has **neither** parameter (``tortoise/sdk.py:26585-26588``), and the tag
arm calls ``query_points_by_tag(tag)``, which takes the tag and nothing else.

Measured: ``tortoise_query(kind="statement", min_confidence=0.9)`` reaches the
accessor as ``paginated_query(kind='statement', skip=0, limit=100,
include_retracted=False)`` — the floor is simply gone, and rows below 0.9 come
back. Same class as #12372 (``entity_type``) and #12455 (``filters=``): the
#6146 wrong-answer-indistinguishable-from-right failure.

It is NOT a fixable-by-forwarding gap: ``docs/plan/2026-08-03-hybrid-search-plan.md``
records the non-text path as **Phase 2** — "Full-scan mode already has EP
annotation; structural filtering by confidence is a separate feature from hybrid
search" — so the parameters were always text-path-only in the design, while the
served description did not say so. Refusing is therefore the honest fix, and it
is the same shape #12455 shipped for ``filters=``.

``kind`` on the tag arm is filed here too (per the #12500 comment thread): tag
mode reads every kind of tagged Point, so a caller who names a kind gets a wider
population than they asked for.

Test doctrine (Class B): every test states (1) the value whose presence makes it
fail and (2) that the fixture reaches that value.

Run with (stub-only, no database):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_12500_query_args_dropped.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

#: What a floor of 0.9 must NOT return if it is applied. The stub answers with
#: rows at 0.01 so a dropped floor is visible as a returned row, not as a count.
_CONFIDENCE_FLOOR = 0.9
_LOW_CONFIDENCE = 0.01


class _StubSDK:
    """Records what each accessor was asked for; applies no floor and no order.

    The two structural accessors mirror the REAL signatures — ``paginated_query``
    and ``query`` take ``(kind, …, **filters)`` and have no ``min_confidence`` or
    ``order_by`` — so a test cannot pass by inventing a knobs path the product
    does not have. ``query_points_by_tag`` takes the tag and nothing else.
    """

    def __init__(self, n=3):
        self.calls = []
        self.rows = [{"id": f"p{i}", "kind": "statement",
                      "confidence": _LOW_CONFIDENCE} for i in range(n)]

    def tortoise_fts_query(self, query=None, kind=None, *, entity_type="point",
                           min_confidence=0.0, order_by="relevance",
                           limit=10, include_terminal=False, **kwargs):
        self.calls.append(("tortoise_fts_query", query, kind, {
            "min_confidence": min_confidence, "order_by": order_by,
            "limit": limit, "include_terminal": include_terminal, **kwargs}))
        return self.rows

    def paginated_query(self, kind=None, skip=0, limit=20, **kwargs):
        self.calls.append(("paginated_query", kind, skip, limit, kwargs))
        total = len(self.rows)
        return {"results": self.rows[skip:skip + limit], "total": total,
                "hasMore": skip + limit < total}

    def query(self, kind=None, **kwargs):
        self.calls.append(("query", kind, kwargs))
        return self.rows

    def query_points_by_tag(self, tag, *, _record_reads=True):
        self.calls.append(("query_points_by_tag", tag, _record_reads))
        return self.rows


@pytest.fixture(autouse=True)
def _http_transport():
    """Drive the tool bodies directly (no HTTP middleware)."""
    from tortoise.mcp_auth import (  # noqa: I001
        _current_org_id, _current_org_limits, _transport_mode,
    )
    _transport_mode.set("http")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_org_id.set(None)
    _current_org_limits.set(None)


@pytest.fixture
def stub(monkeypatch):
    from tortoise import mcp_server

    sdk = _StubSDK()
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: sdk)
    return sdk


def _is_refusal(result) -> bool:
    from tortoise.mcp_server import _SafeError
    return isinstance(result, _SafeError) and "error" in result


#: The structural shapes that cannot apply each argument. ``offset=0`` is the
#: paginated arm; no offset is the plain arm — both call ``paginated_query``,
#: which has neither parameter.
_STRUCTURAL_SHAPES = [
    {"kind": "statement"},                      # plain structural arm
    {"kind": "statement", "offset": 0},         # paginated arm
]


@pytest.mark.parametrize("base", _STRUCTURAL_SHAPES)
@pytest.mark.parametrize("argument", ["min_confidence", "order_by"])
def test_the_structural_arms_refuse_a_floor_or_an_order(stub, base, argument) -> None:
    """RED before the fix: the accessor never saw the argument, rows came back.

    Two falsifiers, and BOTH are reachable (round-2 finding 1: the previous
    version claimed a rows assertion it did not have):

    * ``_is_refusal(result) is False`` — the shipped defect;
    * the rows report below, which runs only in the mutant case and names what
      the caller actually received — 3 rows at confidence 0.01, exactly what a
      floor of 0.9 excludes. It is written as an `if` rather than a second
      `assert` because an `assert` after the first one is unreachable: pytest
      stops at the first failure, so a "second assertion" there would be dead by
      construction — which is how the claim outlived the assertion it described.
    """
    from tortoise.mcp_server import tortoise_query

    value = _CONFIDENCE_FLOOR if argument == "min_confidence" else "confidence"
    result = tortoise_query(**base, **{argument: value})

    if not _is_refusal(result):
        rows = result if isinstance(result, list) else result.get("results", [])
        below = [r for r in rows
                 if isinstance(r, dict)
                 and float(r.get("confidence", 1.0)) < _CONFIDENCE_FLOOR]
        pytest.fail(
            f"{argument}={value!r} on {sorted(base)} was accepted and DROPPED — "
            f"the accessor takes no such parameter, so the caller received "
            f"{len(rows)} row(s), {len(below)} of them BELOW the requested "
            f"{argument}: {below[:3]}"
        )


@pytest.mark.parametrize("argument,value", [
    ("kind", "statement"),
    ("min_confidence", _CONFIDENCE_FLOOR),
    ("order_by", "confidence"),
])
def test_the_tag_arm_refuses_arguments_it_cannot_apply(stub, argument, value) -> None:
    """RED before the fix: `query_points_by_tag(tag)` was called regardless.

    Tag mode reads every kind of tagged Point and has no confidence floor and no
    ordering, so a caller who names one gets a result they did not ask for — a
    WIDER population for `kind`/`min_confidence`, a SEQUENCE the arm chose for
    `order_by`. (This docstring used to lump all three as "wider population",
    which is false of an ordering — an ordering re-decides the sequence, it
    cannot widen the population. The shipped message already gets this right per
    argument, and `test_the_message_states_the_harm_for_the_argument_it_names`
    forbids "rows they excluded" on the `order_by` message — round-20 P2 2.)
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(tag="pricing", **{argument: value})

    assert _is_refusal(result), (
        f"tag= with {argument}={value!r} was accepted and DROPPED"
    )
    assert [c for c in stub.calls if c[0] == "query_points_by_tag"] == [], (
        f"the tag read happened before refusing: {stub.calls}"
    )
    # The reason must be the TAG arm's, not the structural accessor's — a shared
    # one-size-fits-all explanation shipped "the structural arms read through
    # sdk.paginated_query" on a tag= refusal (rounds 4 and 5, both measured).
    assert "paginated_query" not in result["error"], result["error"]


def test_a_falsy_tag_is_still_the_tag_arm(stub) -> None:
    """`tag=""` is a SET tag, and the arm test must key on `is not None`.

    `tag` is a string, so keying the arm on its truthiness looks harmless — but
    then `tag=""` is refused with the STRUCTURAL reason ("sdk.paginated_query …")
    while `query_points_by_tag` is the arm that would have answered (round-5
    finding 5b: the mutant `if tag` kept the whole suite green). Fails if the arm
    is decided by truthiness instead of presence.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(tag="", min_confidence=_CONFIDENCE_FLOOR)

    assert _is_refusal(result), "no refusal to read a message off"
    assert "tag=" in result["error"], result["error"]
    assert "paginated_query" not in result["error"], result["error"]


def test_the_refusal_names_the_argument_it_dropped(stub) -> None:
    """A refusal that does not say WHICH argument is a dead end for the caller.

    Fails if the message is generic ("invalid argument") — the caller has several
    arguments on this tool and only some are refused per arm.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(kind="statement", min_confidence=_CONFIDENCE_FLOOR)

    assert _is_refusal(result), "no refusal to read a message off"
    message = result["error"]
    assert "min_confidence" in message, message
    assert "structural" in message, message
    assert "text=" in message, message


def test_the_refusal_happens_before_the_read(stub) -> None:
    """Refusing after the read would still pay for it.

    Fails on any accessor call recorded while the call ends in a refusal.
    """
    from tortoise.mcp_server import tortoise_query

    tortoise_query(kind="statement", order_by="confidence")

    assert stub.calls == [], f"the read happened before refusing: {stub.calls}"


# ── the guard must not over-block ────────────────────────────────────────────

@pytest.mark.parametrize("argument,value", [
    ("min_confidence", _CONFIDENCE_FLOOR),
    ("order_by", "confidence"),
])
def test_the_text_arm_still_APPLIES_them(stub, argument, value) -> None:
    """These arguments are legitimate on `text=` — the fts leg takes both.

    Fails if the guard is placed above the whole body (refusing the arm that CAN
    apply them) or if the value stops reaching the accessor.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="anything", **{argument: value})

    assert not _is_refusal(result), f"refused a legitimate text= read: {result}"
    fts = [c for c in stub.calls if c[0] == "tortoise_fts_query"]
    assert fts, f"the fts leg was never read: {stub.calls}"
    assert fts[0][3][argument] == value, (
        f"{argument} did not reach the fts leg: {fts[0][3]}"
    )


@pytest.mark.parametrize("kwargs", [
    {"kind": "statement"},                       # plain structural, no extra args
    {"kind": "statement", "offset": 0, "limit": 5},
    {"tag": "pricing"},
    {"tag": "pricing", "offset": 0, "limit": 5},
    {"text": "anything"},
])
def test_legitimate_calls_are_untouched(stub, kwargs) -> None:
    """The hot paths of all three arms must not be perturbed.

    Falsified by a guard whose condition reaches these calls at all — e.g. one
    that refuses every structural read. NOT by a `filters`-keyed condition: none
    of these cases passes `filters`, so that mutant cannot reach them (round-11
    P2 5 — the previous wording claimed a discriminating power this test does not
    have, the same defect round 2 removed elsewhere in this file). It does not
    falsify `is not None` either (these cases pass no such argument, so the value
    is `None`); that mutant is caught by
    ``test_falsy_values_are_no_ops_and_never_refused``, which is stated here
    rather than claimed away.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(**kwargs)

    assert not _is_refusal(result), f"refused a legitimate {sorted(kwargs)}: {result}"
    assert stub.calls, f"{sorted(kwargs)} never read anything"


def test_defaults_are_not_refusals(stub) -> None:
    """`min_confidence=None` / `order_by=None` are the DEFAULTS — never refused.

    Fails if the guard refuses on the parameter being PRESENT at all (e.g. an
    `if "min_confidence" in kwargs` shape, or a guard placed where the default
    trips it). An `is not None` mutant is NOT caught here — `None is not None` is
    false, so it passes — and that limitation is recorded rather than papered
    over (round-2 finding 2).
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(kind="statement", min_confidence=None, order_by=None)

    assert not _is_refusal(result), f"refused the defaults: {result}"


@pytest.mark.parametrize("kwargs", [
    {"kind": "statement", "min_confidence": 0.0},
    {"kind": "statement", "offset": 0, "min_confidence": 0.0},
    {"kind": "statement", "min_confidence": 0},      # int zero, not float
    {"kind": "statement", "min_confidence": False},  # bool zero
    {"kind": "statement", "order_by": ""},
    {"kind": "statement", "order_by": False},
    {"tag": "pricing", "kind": ""},
    {"tag": "pricing", "min_confidence": 0.0},
    {"tag": "pricing", "min_confidence": 0},
    {"tag": "pricing", "order_by": ""},
    # The round-3 text=-arm clause is keyed on `min_confidence` truthiness too,
    # and nothing else pins it: `is not None` there refuses a floor of 0.0 on a
    # document read whose answer would have been CORRECT (round-4 finding 7, the
    # round-1 P1 reintroducible with the suite green).
    {"text": "x", "entity_type": "document", "min_confidence": 0.0},
    {"text": "x", "entity_type": "source", "min_confidence": 0},
])
def test_falsy_values_are_no_ops_and_never_refused(stub, kwargs) -> None:
    """A client that serialises its own DEFAULTS must not be refused (#12500 r1).

    This is the ONLY test that reds when the guard is narrowed to `is not None`
    (measured: reintroducing it leaves the five `legitimate_calls` cases and
    `test_defaults_are_not_refusals` green, because an OMITTED argument is
    `None` and `is not None` never refuses `None` — round-2 finding 2). So it
    carries the shapes the round-1 review named: float zero, INT zero, `False`,
    and the empty strings.

    The arm itself defines these values as absent — the `text=` call normalises
    with `min_confidence or 0.0` and `order_by or "relevance"`, and the sibling
    `filters=` guard keys on truthiness for the same reason. Keying this guard on
    `is not None` refused `min_confidence=0.0` / `order_by=""` / `kind=""` on
    every structural and tag read, where the answer had always been CORRECT (a
    floor of 0.0 excludes nothing; an empty order names none). Measured before
    the fix: every shape in this list that existed then (six) was REFUSED. The
    list has since grown to twelve — rounds 2–4 added the int-zero, `False` and
    `text=`+non-Point shapes — and re-measuring the `is not None` key on this head
    reds ten of them, the two `text=` shapes being the ones that red under that
    arm's own key instead (round-20 P2 4: the sentence still said "six").
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(**kwargs)

    assert not _is_refusal(result), (
        f"refused a call whose {sorted(kwargs)} are semantic NO-OPS on this arm"
    )
    assert stub.calls, f"{sorted(kwargs)} never read anything"


def test_the_tag_arm_message_names_the_precedence_that_blocks_pass_text(stub) -> None:
    """`tag=` wins over `text=`, so "pass text=" alone loops the caller (#12500 r1).

    Fails if the tag-arm message omits `tag=` — the caller drops nothing, re-reads
    the same sentence, and is back where they started. The `filters=` guard had
    exactly this defect in #12455 and was fixed for it.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(tag="pricing", min_confidence=_CONFIDENCE_FLOOR)

    assert _is_refusal(result), "no refusal to read a message off"
    assert "tag=" in result["error"], result["error"]
    assert "drop tag=" in result["error"], result["error"]
    # …and WHY keeping tag= is a dead end. Without this clause the route is
    # "drop tag= and pass text=" with no explanation, so a caller who keeps tag=
    # re-reads the same sentence with no hint why (round-10 P2 3).
    assert "takes precedence" in result["error"], result["error"]


#: Valid NON-Point types only. "session" is not in VALID_ENTITY_TYPES, so it
#: was refused by `validate_entity_type` ABOVE this guard and passed the
#: assertion only because that message lists `point` (round-4 finding 5).
#: ALL six non-Point types. Pinning three of them left the class open for
#: `source`/`subject`/`operator` under a mutant that refused only the three
#: (79 passed, 0 failed — round-6 P2 2). `operator` is included because the SDK
#: gate is the literal `entity_type == "point"`, not Point-ness, so an operator
#: read is refused too.
@pytest.mark.parametrize("entity_type", ["document", "event", "object", "source",
                                         "subject", "operator"])
def test_the_text_arm_REFUSES_a_floor_it_cannot_apply(stub, entity_type) -> None:
    """`text=` applies the floor to POINT rows only (#12500 round-3 P1).

    The fts leg compares `min_confidence` against EP belief under
    `entity_type == "point"` (`sdk.py:43288` — `ep` is `None` for every other
    type, and the branch is gated on it), so a non-Point read passed the floor
    straight through: measured at the tool boundary,
    `text="…", entity_type="document", min_confidence=0.9` returned the
    0.1-belief rows, and the same read at 0.0 returned the identical page —
    silently wrong, on the very arm the other refusals tell callers to use.

    Fails if `text and min_confidence and entity_type != "point"` is missing from
    the guard (`_is_refusal` false), or if it is widened to the Point arm
    (`test_the_text_arm_still_APPLIES_them` covers that side).
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="anything", entity_type=entity_type,
                            min_confidence=_CONFIDENCE_FLOOR)

    assert _is_refusal(result), (
        f"text= with entity_type={entity_type!r} accepted a floor that this arm "
        f"cannot apply to a single row, and served: {result!r}"
    )
    assert "point" in result["error"], result["error"]

    # …and the bool spelling of the SAME floor. `True` is 1.0: the SDK's range
    # check passes it (`0.0 <= True <= 1.0`) and the fts leg then skips the floor
    # on a non-Point read, so it came back with the 0.01 rows while `1` and `1.0`
    # were refused — one argument value answering two ways depending on its Python
    # spelling (round-21 P2 1, measured). `False` stays SERVED: it is falsy, which
    # the first conjunct already excludes.
    assert _is_refusal(tortoise_query(text="anything", entity_type=entity_type,
                                      min_confidence=True)), (
        f"text= with entity_type={entity_type!r} served min_confidence=True — a "
        f"floor of 1.0 this arm cannot apply — while refusing 1 and 1.0"
    )


def test_the_text_arm_floor_message_offers_a_route_that_terminates(stub) -> None:
    """The route must be followable FROM here (#12500 round-3, P2 class).

    "Pass text=" is a dead end on this branch — `text=` is already set, which is
    why the call was refused. The message has to name `entity_type='point'` (or
    say to drop the argument), and must NOT re-advise the arm the caller is
    already on. The tag arm had exactly this defect in round 1.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="anything", entity_type="document",
                            min_confidence=_CONFIDENCE_FLOOR)

    assert _is_refusal(result), "no refusal to read a message off"
    message = result["error"]
    assert "entity_type='point'" in message, message
    assert "pass text=" not in message, message


def test_an_unusable_floor_is_left_to_the_sdk_to_refuse(stub) -> None:
    """An out-of-range floor must NOT get the arm-scoping paragraph (round-20 P2 3).

    That paragraph's reason ("the fts leg applies its floor under a literal
    `entity_type == 'point'` gate"), its harm ("rows they excluded came back") and
    its route ("pass entity_type='point'") are all false of a floor the SDK
    rejects outright: `sdk.py:41133-41134` range-checks `min_confidence` BEFORE
    any retrieval, so `min_confidence=1.5` here used to RAISE — it never returned
    the rows the message apologised for — and the offered route lands on that same
    range error. Following a terminating route into a second error is the class
    this guard exists to remove.

    Fails if the text=-arm clause stops checking that the floor is usable: the
    value is then intercepted and answered with a `#12500` paragraph.
    """
    from tortoise.mcp_server import tortoise_query

    for bad in (1.5, -0.5):
        result = tortoise_query(text="x", entity_type="document", min_confidence=bad)

        # Served, not refused here — and the stub records the forward, which is
        # the seam the real SDK validates at (a stub cannot reproduce the range
        # check, so the assertion is on the forward).
        assert not _is_refusal(result), (
            f"min_confidence={bad} is not a usable floor, so the #12500 paragraph "
            f"is false about it (and its route dead-ends): {result}"
        )
        fts = [c for c in stub.calls if c[0] == "tortoise_fts_query"]
        assert fts and fts[-1][3]["min_confidence"] == bad, fts

    # …and a NON-NUMERIC floor, which is what the `isinstance` conjunct is for.
    # Unpinned, deleting that conjunct left 87/87 green while
    # `min_confidence="0.9"` raised TypeError INSIDE the handler body — out of the
    # structured-error path this file's contract is about (round-22 P2 3,
    # measured: deleting the conjunct and refusing a string both stayed green).
    text_floor = tortoise_query(text="x", entity_type="document",
                               min_confidence="0.9")

    assert not _is_refusal(text_floor), (
        f"a string floor is not a floor this arm can judge — it must reach the "
        f"SDK's own validation, not this paragraph: {text_floor}"
    )
    fts = [c for c in stub.calls if c[0] == "tortoise_fts_query"]
    assert fts and fts[-1][3]["min_confidence"] == "0.9", fts


def test_the_text_arm_still_applies_the_floor_to_POINTS(stub) -> None:
    """The control for the branch above: `entity_type="point"` is the default.

    Fails if the new condition is written without the `entity_type != "point"`
    clause (over-block: the arm that DOES apply the floor gets refused).

    NOT the truthiness key, though an earlier version of this docstring claimed
    it: this test's inputs are `min_confidence=0.9` with `entity_type` `point`/None,
    where the key is unobservable — a POINT read normalises with
    `min_confidence or 0.0`, so `0.0` forwards identically under either key
    (round-20 P2 1, measured: an `is not None` key here leaves this test green).
    The falsy axis is pinned by the `{text, document, 0.0}` / `{text, source, 0}`
    shapes in `test_falsy_values_are_no_ops_and_never_refused`.
    """
    from tortoise.mcp_server import tortoise_query

    for entity_type in ("point", None):
        kwargs = {} if entity_type is None else {"entity_type": entity_type}
        result = tortoise_query(text="anything", min_confidence=_CONFIDENCE_FLOOR,
                                **kwargs)

        assert not _is_refusal(result), (
            f"refused the arm that CAN apply the floor (entity_type={entity_type!r}):"
            f" {result}"
        )
        fts = [c for c in stub.calls if c[0] == "tortoise_fts_query"]
        assert fts and fts[-1][3]["min_confidence"] == _CONFIDENCE_FLOOR


def test_the_single_argument_ordering_reason_is_the_arms_own(stub) -> None:
    """Each arm's `order_by` reason has a SINGLE-argument variant, and it is the one served.

    Every assertion in `test_the_message_agrees_in_number_…` passes `min_confidence`
    too, so `… if min_confidence else …` always selected the OTHER branch and this
    string was never read by any test — both arms stayed green with a false
    replacement (round-19 P2 1, measured):

    * structural `order_by` alone → "the accessor DOES apply the ordering to
      paginated reads", which is false: `paginated_query` declares no ordering at
      all (sdk.py:26585-26588);
    * tag `order_by` alone → "the accessor applies the tag ordering, not this
      guard", which is false: `query_points_by_tag` orders `p.createdAt DESC` and
      never applies the caller's ordering (sdk.py:26920) — the exact sentence the
      number-agreement test's docstring says it pins, on the arm it does not reach.

    Fails if either single-argument reason is replaced, or if the two messages stop
    being about their own arm.
    """
    from tortoise.mcp_server import tortoise_query

    structural_only = tortoise_query(kind="statement", order_by="confidence")
    tag_only = tortoise_query(tag="pricing", order_by="confidence")

    assert _is_refusal(structural_only) and _is_refusal(tag_only)
    # One name, so the verb is singular — and the reason is the accessor's.
    assert "order_by is not applied" in structural_only["error"], structural_only["error"]
    assert "paginated_query" in structural_only["error"], structural_only["error"]
    assert "takes no ordering" in structural_only["error"], structural_only["error"]
    assert ("tag mode reads a tag and nothing else"
            in tag_only["error"]), tag_only["error"]
    assert "cannot apply an ordering" in tag_only["error"], tag_only["error"]


def test_the_message_agrees_in_number_with_the_arguments_it_names(stub) -> None:
    """Two named arguments must not ship "min_confidence and order_by IS" (r3 P2).

    Measured on a real call before the fix:
    `tortoise_query(tag="pricing", min_confidence=0.9, order_by="confidence")`
    answered "min_confidence and order_by is not applied …". The message IS this
    PR's deliverable and it is read on the error path.
    """
    from tortoise.mcp_server import tortoise_query

    two = tortoise_query(tag="pricing", min_confidence=_CONFIDENCE_FLOOR,
                         order_by="confidence")
    one = tortoise_query(tag="pricing", min_confidence=_CONFIDENCE_FLOOR)

    assert _is_refusal(two) and _is_refusal(one)
    # BOTH NAMES, not a bare " and " — the harm clause supplies one of those
    # ("rows they excluded and an order they did not ask for"), so a join that
    # kept only `_names[0]` shipped "min_confidence are not applied … or drop
    # min_confidence for this read" and stayed green (round-19 P2 2, measured:
    # 84/84 in file, 247/247 across the gate set), leaving `order_by` refused one
    # call later — the ping-pong class this guard exists to close.
    assert "min_confidence and order_by are not applied" in two["error"], two["error"]
    assert " are not applied" in two["error"], two["error"]
    assert " is not applied" in one["error"], one["error"]
    # BOTH reasons, not just both names — a join that keeps only the first
    # explains one argument while naming two (round-6 P2 4).
    assert "confidence floor" in two["error"], two["error"]
    assert "ordering" in two["error"], two["error"]
    # …and the REASONS, not just the words. Unpinned, "the tag ordering is applied
    # by the accessor, not here" shipped green — false: `query_points_by_tag`
    # orders `p.createdAt DESC` and never applies the caller's ordering
    # (sdk.py:26920), while the test's bare "ordering" substring still matched.
    assert "tag mode reads a tag and nothing else" in two["error"], two["error"]
    assert "cannot apply an ordering" in two["error"], two["error"]


def test_a_floor_and_a_property_filter_have_no_common_arm(stub) -> None:
    """Following the structural route with `filters=` set must not ping-pong r4 P1.

    The structural refusal says "pass text="; the `#12455` refusal on the text=
    arm says "drop text=". A caller holding `filters=` + `min_confidence` would
    oscillate forever between two messages that each undo the other, because no
    arm applies a property filter together with a floor or an ordering. The
    message has to say so where the caller can see it.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(kind="statement", filters={"a": 1},
                            min_confidence=_CONFIDENCE_FLOOR)

    assert _is_refusal(result), "no refusal to read a message off"
    assert "filters=" in result["error"], result["error"]
    assert "drop filters=" in result["error"], result["error"]


@pytest.mark.parametrize("kwargs", [
    {"tag": "pricing", "text": "x", "min_confidence": _CONFIDENCE_FLOOR},
    {"tag": "pricing", "text": "x", "order_by": "confidence"},
])
def test_the_tag_arm_still_refuses_when_text_is_also_passed(stub, kwargs) -> None:
    """`tag=` wins, and the guard's `tag is not None` disjunct must survive (r4 P2 6).

    Without that disjunct the condition is `not text`, so `tag="t", text="x"`
    skipped the guard entirely and the TAG arm answered (`query_points_by_tag`)
    with the floor dropped — the exact defect this PR closes, reopened by a
    one-word refactor, with the whole suite green before this test existed.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(**kwargs)

    assert _is_refusal(result), f"served {sorted(kwargs)} on the tag= arm"
    assert "tag=" in result["error"], result["error"]
    assert not [c for c in stub.calls if c[0] == "query_points_by_tag"], (
        "the tag arm READ before refusing — rows can be served on an error path"
    )


@pytest.mark.parametrize("order_by", ["confidence", "graph"])
@pytest.mark.parametrize("entity_type", ["document", "source"])
def test_a_ranking_ordering_is_refused_where_every_key_is_constant(
        stub, entity_type, order_by) -> None:
    """A ranking ordering whose every key is constant is not applied (r4 P2 2, r7).

    `confidence` sorts on an EP belief neither type has; `graph` adds an EP belief
    AND a recency term, and a document/source row carries neither — so BOTH
    return the relevance order, unchanged and unmarked.

    `GraphRanker._fetch_signals` reads EPISTEMIC_LABELS = ("Point", "Subject",
    "Object", "Event") and BOTH `document` and `source` map to the `Source`
    label, so the sweep answers `{}` and neither extra term varies: `confidence`
    collapses to ONE key per row (`(1, 0.0)`), while `graph` keeps distinct keys
    that differ by an identical offset (measured: `final_score` 0.65 / 0.3722 /
    0.15 against similarity 0.10 / 0.05 / 0.01) — monotone in the relevance
    score, so the ORDER is the one the caller already had. Unmarked either way,
    so the caller cannot tell the ordering was never applied. Fails if the branch is missing (`_is_refusal` false)
    or widened to the types that DO honour it (next test).
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="x", entity_type=entity_type, order_by=order_by)

    assert _is_refusal(result), (
        f"served order_by={order_by!r} for entity_type={entity_type!r}, where "
        f"every row's sort key is identical so the order comes back unchanged"
    )
    assert "order_by" in result["error"], result["error"]
    # The REASON, not just the argument name: unpinned, "`order_by='confidence'`
    # is not supported for an entity_type='document' read" shipped green — false,
    # the value IS accepted and merely cannot reorder (round-17 P2 1).
    if order_by == "confidence":
        assert "four epistemic labels" in result["error"], result["error"]
    else:
        assert "IDENTICAL on every row" in result["error"], result["error"]


@pytest.mark.parametrize("entity_type", ["point", "event", "subject", "object",
                                         "operator"])
def test_the_confidence_ordering_still_works_for_the_epistemic_labels(
        stub, entity_type) -> None:
    """The control: these types DO carry a belief, so refusing them is a false block.

    `operator` is the interesting one — operator rows are POINTS
    (`security.py:166-174`), so a blanket "non-Point" refusal would break a
    working call. Fails if the condition is written as `entity_type != "point"`
    or against the whole non-Point set instead of the two Source-backed types.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="x", entity_type=entity_type,
                            order_by="confidence")

    assert not _is_refusal(result), (
        f"refused order_by='confidence' for entity_type={entity_type!r}, whose "
        f"ranker read DOES return beliefs: {result}"
    )


def test_the_kind_only_refusal_names_the_wider_population(stub) -> None:
    """A `kind`-only tag refusal must not explain a floor the caller never passed.

    Shipped before round 4: "kind is not applied on the tag= arm: tag mode reads a
    tag and nothing else, so it cannot apply a confidence floor or an ordering.
    … rows they excluded, or an order they did not ask for." — every clause names
    an argument that was not in the call. `kind`'s harm is a WIDER population.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(tag="pricing", kind="statement")

    assert _is_refusal(result), "no refusal to read a message off"
    message = result["error"]
    assert "wider population" in message, message
    assert "floor" not in message and "ordering" not in message, message
    # …and the REASON. Unpinned, this shipped: "a named `kind` excludes tagged
    # Points, so rows you asked for came back missing" — false (tag mode serves a
    # SUPERSET) and self-contradictory with the harm clause in the same sentence
    # (round-17 P2 1, measured green).
    assert "reads every kind of tagged Point" in message, message
    assert "narrows nothing" in message, message
    # …and the route must be the KIND one. Deleting that branch pushed a caller
    # who wanted a narrower population into a text search, with the suite green
    # (round-6 P2 3).
    assert "pass kind= to the structural arm" in message, message


def test_the_refusal_never_repeats_its_route_or_says_a_entity_type(stub) -> None:
    """Two shipped prose defects from round 4, pinned by their exact shapes.

    * the text= arm emitted the drop-clause twice ("… otherwise drop
      min_confidence for this read, or drop min_confidence for this read");
    * the reason read "so a entity_type='document' read" — ungrammatical, and the
      shipped message IS this PR's deliverable.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(text="x", entity_type="document",
                             min_confidence=_CONFIDENCE_FLOOR)["error"]

    assert message.count("drop min_confidence for this read") == 1, message
    assert "a entity_type" not in message, message
    assert "an entity_type" in message or "entity_type='document'" in message, message


@pytest.mark.parametrize("entity_type", ["document", "source"])
def test_the_relevance_ordering_is_served_for_every_type(
        stub, entity_type) -> None:
    """The refusal is keyed on `order_by == "confidence"`, not on the TYPE (#12500 r5).

    Mutating the condition to `if order_by and entity_type in (…, "source")` left
    the whole suite green while false-blocking `entity_type="document",
    order_by="relevance"` — the very fallback the refusal message recommends for
    a read that works. Fails if the value axis drops out of the condition.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="x", entity_type=entity_type, order_by="relevance")

    assert not _is_refusal(result), (
        f"refused order_by='relevance' for entity_type={entity_type!r}: that IS "
        f"the order those rows already have, so it is always applied"
    )
    fts = [c for c in stub.calls if c[0] == "tortoise_fts_query"]
    assert fts and fts[-1][3]["order_by"] == "relevance"


def test_a_refused_floor_and_ordering_do_not_share_one_route(stub) -> None:
    """`or order_by='relevance'` cannot resolve a refused FLOOR (#12500 r5).

    With both refused on the text= arm the message offered the two alternatives
    as if either would do, but relevance fixes only the ordering — following it
    landed on "min_confidence is not applied …" one round trip later. Fails if
    the both-arguments branch goes back to offering it as an equal alternative.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(text="x", entity_type="document",
                             min_confidence=_CONFIDENCE_FLOOR,
                             order_by="confidence")["error"]

    assert "fixes both" in message, message
    assert "only the ordering" in message, message


def test_a_kind_only_refusal_carries_no_filters_advice(stub) -> None:
    """The `filters=` note must not contradict the route in the same message (r5).

    `tag="t", kind="statement", filters={…}` used to read "No arm applies a
    property `filters=` together with a confidence floor or an ordering, so drop
    filters=" and then "pass kind= to the structural arm" — which DOES apply
    `filters=` with `kind=`. The note is gated on a floor/ordering being refused.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(tag="pricing", kind="statement",
                             filters={"a": 1})["error"]

    assert "drop filters=" not in message, message
    assert "kind" in message, message


#: Sentences that were TRUE-LOOKING and FALSE, each measured and each shipped at
#: some point in this PR. This docstring is the tool's DEVELOPER-facing contract
#: — NOT the description a model receives over MCP, which is
#: `ToolDefinition.description` and does not mention these arguments at all
#: (round-9 finding 4, measured with `mcp.list_tools()`). It still steers every
#: developer reading the source; "the docstring says nothing wrong" is not
#: decidable, but "these exact claims are gone" is.
_DOCSTRING_FALSE = (
    # round-5: the falsity is a CLAUSE, not a bare phrase. The bare phrase reds a
    # sentence that REJECTS the claim ("it is NOT the case that only a Point
    # carries one — event/subject/object do too"), so each entry is the shipped
    # clause verbatim, wrap-insensitively matched. `event`/`subject`/`object`/
    # `operator` DO carry a belief (measured live); only `document`/`source` do
    # not.
    "compares the floor against EP belief, and only a Point carries one",
    "(the fts leg compares the floor against EP belief, and only a Point "
    "carries one",
    # RETIRED (round 14): the exclusive form "`order_by` is applied on the text=
    # arm for `entity_type="point"` only" is false, but a sentence REJECTING it
    # contains it verbatim, so the ban reded a correct edit — the same residual
    # recorded above. `min_confidence` and `order_by` really are both applied on
    # the text= arm for `entity_type="point"`, and the positive assertions in
    # this module state that, which is what a decidable test can do.
    # round-6/7/13: BOTH ranking orderings (`confidence` and `graph`) are refused
    # on document/source, and each leaves the order unchanged for its OWN reason:
    # `confidence` because every key collapses to `(1, 0.0)`, `graph` because its
    # two extra terms are identical on every row, so the key is monotone in the
    # relevance score. (It is NOT "the same key for every row" — measured: graph
    # keys 0.65 / 0.3722 / 0.15, three distinct values.) `order_by="relevance"` IS
    # applied there. So the UNQUALIFIED form is false
    # for a reason the qualified one is not: it drops the value and implies no
    # ordering works on those two types, which sends a caller away from a read
    # that works. Banned in that unqualified form only; the sentence that ships
    # names the values, and `order_by="relevance"` served for `document`/`source`
    # is pinned by `test_the_relevance_ordering_is_served_for_every_type`
    # (round 10: the previous comment still argued the refuted round-6 premise —
    # "only a CONFIDENCE ordering is refused there" — while the ban itself stands
    # on the measurement above).
    # Round-13: the previous entry ("…EXCEPT `document`/`source` (both map to the")
    # was both too broad and too narrow — it reded the TRUE value-qualified form
    # and let "applied to every type including those two" through. Pinned is the
    # FALSE half itself, which is exactly what the claim got wrong.
    #
    # Value-AGNOSTIC: "`order_by` is applied to every type including those two"
    # is the false claim. Naming the argument means a TRUE sentence that names a
    # VALUE — `order_by="relevance"` IS applied to every type — no longer matches
    # (round-14 P2 1: the round-13 entry dropped the argument name and reded it).
    "`order_by` is applied to every type including those two",
    #
    # RECORDED RESIDUAL, not papered over: substring bans catch a RESTATEMENT of
    # a claim, not a sentence REJECTING it ("it is not the case that …"), and they
    # cannot separate a bare lossy summary ("`order_by` for every type EXCEPT
    # `document`/`source`") from the true value-qualified form. Four rounds of
    # widening these entries produced more over-block than protection, so the
    # residual is stated instead: the POSITIVE assertions in this module hold the
    # truth in the document, and catching a lossy summary is a review question.
    # round-8: the harm clause was lumped, so it claimed an ORDERING let rows
    # through — an ordering excludes nothing; it only re-decides their sequence.
    "an accepted value was discarded and rows the caller excluded came back",
    # round-4 banned "selects non-Point rows" here, but that sentence is TRUE of
    # `document`/`source` (they map to the `Source` label) and was never a
    # docstring claim at all — it was the MESSAGE's reason for `operator`, which
    # IS a Point. Pinned as a message assertion instead, scoped to `operator`
    # (see `test_an_operator_floor_refusal_names_the_gate_not_a_label`).
)


def test_the_tool_docstring_never_restates_a_falsified_claim() -> None:
    """The false claims this PR shipped, pinned so they cannot come back.

    Each is wrap-insensitive (whitespace-collapsed), because a re-wrap must not
    silently disarm the pin — the `#12455` lesson. Fails if any reappears in
    `tortoise_query.__doc__`.
    """
    from tortoise.mcp_server import tortoise_query

    # CASEFOLDED on both sides (round-13 P2 3): the bans were case-sensitive, so
    # the round-8 lumped-harm claim came back green with a capital A, and the
    # round-5 sentence came back green with a lowercase "except" — a casing change
    # is not a fix to a false claim.
    flat = " ".join((tortoise_query.__doc__ or "").split()).casefold()

    for claim in _DOCSTRING_FALSE:
        assert " ".join(claim.split()).casefold() not in flat, (
            f"the docstring restates a falsified claim: {claim!r}"
        )
    # …and the TRUE statement is present, so the pin is not satisfied by silence.
    assert "except `document` and `source`" in flat, flat[:600]


def test_each_refused_argument_gets_its_own_reason(stub) -> None:
    """Two refused arguments need TWO reasons, on every arm (#12500 round 6).

    Fails if a per-argument reason is replaced by its sibling — e.g. the
    structural `order_by` reason answering "takes no confidence floor", which
    leaves the caller told about a floor they may not have passed and never told
    why the ordering went missing. Measured: that swap alone kept the whole suite
    green before this test, because the agreement test above uses the tag arm.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(kind="s", min_confidence=_CONFIDENCE_FLOOR,
                             order_by="confidence")["error"]

    assert "takes no confidence floor" in message, message
    assert "takes no ordering" in message, message


@pytest.mark.parametrize("entity_type", ["point", "event", "subject", "object",
                                         "operator"])
def test_the_graph_ordering_is_served_where_a_signal_exists(
        stub, entity_type) -> None:
    """The round-7 refusal must stay narrow: these rows DO carry graph signals.

    Fails if `graph` is refused on the whole non-Point set — `event`/`subject`/
    `object`/`operator` all have a belief and/or a timestamp, so their graph
    ordering is real (measured live: each reordered a contradictory-belief
    corpus). Only `document`/`source` reduce to the relevance order.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="x", entity_type=entity_type,
                            order_by="graph")

    assert not _is_refusal(result), (
        f"refused order_by='graph' for entity_type={entity_type!r}, whose rows "
        f"carry the signals the ordering reads: {result}"
    )


def test_a_graph_ordering_with_no_signal_is_refused_not_silently_reordered(
        stub) -> None:
    """`graph` on document/source reordered NOTHING, and said nothing (round 7).

    Measured live on a `:Source` corpus: `order_by="graph"` returned exactly the
    relevance order for every input, with `graph_ranking={graph_boost: 0.0,
    recency_boost: 1.0}` on every row — both extra terms identical, so the key is
    monotone in the relevance score and reorders nothing. The class this guard
    exists for, on the sentence that recommends an alternative. Fails if only `confidence` is
    refused there.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(text="x", entity_type="document",
                             order_by="graph")["error"]

    assert "graph" in message, message
    assert "relevance" in message, message
    # …and the MECHANISM, which round 13 corrected: the keys are DISTINCT and
    # merely monotone in the relevance score, so the refuted wording can no
    # longer return silently (round-14 P2 3: it survived every assertion here).
    assert "every row's key is the same" not in message, message
    assert "IDENTICAL on every row" in message, message
    assert "monotone in" in message, message


#: The REFUSAL half of the contract, verbatim — one entry per SHIPPED sentence.
#: A bullet carries more than one `REFUS…` clause, so a presence check on the
#: word alone stayed green while one of them was flipped to "SERVED"
#: (round-15, measured at four sites). Three of these sit inside the spans the
#: falsy check splits on — claims 1 and 2 in the structural bullet, claim 3 in the
#: FIRST `tag=` bullet — and one, claim 4, is in the SECOND `tag=` bullet, outside
#: both spans. That one is why they are matched against the whole docstring rather
#: than per bullet. (The counts in this paragraph were wrong twice and are now
#: measured on the flattened docstring: round 18 said "two of these live in the
#: SECOND bullet" — one does; round 19 caught the follow-up sentence still saying
#: "two of the claims" sit inside the spans — three do.)
_REFUSAL_CLAIMS = (
    "`min_confidence` and `order_by` are REFUSED on this arm rather than dropped",
    "A floor on any other `entity_type`, and a confidence or graph ordering on "
    "those two, are REFUSED rather than dropped",
    "REFUSES kind/min_confidence/order_by and `filters=`",
    "`kind`, `min_confidence` and `order_by` are REFUSED on this arm as well",
)


def test_the_tool_docstring_states_the_falsy_exception() -> None:
    """The contract must not read as an unconditional refusal (#12500 round 8).

    The guard keys on truthiness, so `min_confidence=0.0` / `order_by=""` /
    `kind=""` are SERVED — pinned as required behaviour by
    `test_falsy_values_are_no_ops_and_never_refused`, and justified in the guard
    comment precisely because a client that serialises its own defaults must not
    be refused. The docstring said "are REFUSED" with no exception, so a
    contract reader could not tell which behaviour applies to `min_confidence=0.0`.
    Fails if the exception is dropped from either bullet.
    """
    from tortoise.mcp_server import tortoise_query

    flat = " ".join((tortoise_query.__doc__ or "").split())

    # Split on the bullet headers and check the MEANING in each. A count of the
    # token "FALSY" was satisfied by any two occurrences — including a bullet
    # stating the OPPOSITE of shipped behaviour, which flipped the contract to
    # false with the suite green (round-11 P2 4, measured).
    _struct = flat[flat.find("Structural path"):flat.find("- tag=<name>")]
    _tag = flat[flat.find("- tag=<name>"):flat.find("- Pagination")]
    assert _struct and _tag, flat[:400]
    for _label, _bullet in (("structural", _struct), ("tag", _tag)):
        # BOTH halves of the carve-out: the REFUSAL and the falsy exception. The
        # refusal half was unpinned, so flipping "are REFUSED on this arm" to
        # "are SERVED" — the opposite of shipped behaviour, in the primary
        # contract statement — left the whole gate green (round-15 P2, measured
        # at all four sites). Each SENTENCE is pinned verbatim, wrap-insensitively:
        # a bare `"REFUS" in bullet` check is what a first attempt used, and it
        # stayed green because each bullet carries more than one `REFUS…` clause,
        # so flipping one left the other. These are presence checks — they cannot
        # re-red a true sentence, unlike the word bans retired above.

        assert "FALSY" in _bullet, f"the {_label} bullet omits the falsy case: {_bullet}"
        assert "stays served" in _bullet, (
            f"the {_label} bullet must say the falsy value is SERVED, not refused: "
            f"{_bullet}"
        )
    # No file-wide ban on "refused": `sdk.py:41138` DOES refuse an unknown
    # `order_by` outright, so banning those words reded a true sentence elsewhere
    # in the docstring (round-12 P2 1c). The PER-BULLET "stays served" assertion
    # above is what catches an inverted falsy clause — measured: replacing the
    # tag bullet's clause with "is REFUSED OUTRIGHT here" reds it, because the
    # served claim is then simply absent.
    assert "serialises its own defaults" in flat, flat[:600]

    for _claim in _REFUSAL_CLAIMS:
        assert _claim in flat, (
            f"the docstring must state that the argument is REFUSED where it "
            f"cannot be applied; missing {_claim!r}"
        )


def test_the_tool_docstring_states_the_harm_per_argument() -> None:
    """A floor excludes rows; an ordering does not (#12500 round 8).

    The bullet used to say a dropped value meant "rows the caller excluded came
    back" — true of `min_confidence`, false of `order_by`, whose row SET is
    identical and only the sequence differs. The refusal message gets this right
    per argument; the served contract must too. Fails if the split is dropped
    (the lumped sentence is pinned ABSENT above) or the ordering half is lost.
    """
    from tortoise.mcp_server import tortoise_query

    flat = " ".join((tortoise_query.__doc__ or "").split())

    assert "an ordering they asked for was replaced" in flat, flat[:800]


@pytest.mark.parametrize("kwargs,expected,forbidden", [
    ({"kind": "statement", "order_by": "confidence"},
     "an order they did not ask for", "rows they excluded"),
    ({"kind": "statement", "min_confidence": _CONFIDENCE_FLOOR},
     "rows they excluded", "an order they did not ask for"),
    ({"tag": "pricing", "order_by": "confidence"},
     "an order they did not ask for", "rows they excluded"),
])
def test_the_message_states_the_harm_for_the_argument_it_names(
        stub, kwargs, expected, forbidden) -> None:
    """The harm clause must match the argument (#12500 round 9).

    A dropped ORDERING re-decides the sequence; it cannot admit a row the caller
    excluded. Swapping the two harm halves left the whole suite green while the
    served message told an `order_by` caller that "rows they excluded" came back
    — the lumped-harm falsehood round 8 removed from the docstring, still live in
    the artifact every refused caller receives.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(**kwargs)["error"]

    assert expected in message, message
    assert forbidden not in message, message


def test_the_filters_note_states_that_no_arm_applies_it_with_a_floor(stub) -> None:
    """The note's CLAIM, not just its advice, must be pinned (#12500 round 9).

    `test_a_floor_and_a_property_filter_have_no_common_arm` asserts only
    "drop filters=", which an inverted note ("Every arm applies …") still
    contains. Fails if the premise is negated or the pairing claim is dropped.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(kind="statement", filters={"a": 1},
                             min_confidence=_CONFIDENCE_FLOOR)["error"]

    assert "No arm applies a property `filters=`" in message, message
    assert "together with a confidence floor or an ordering" in message, message


@pytest.mark.parametrize("kwargs", [
    # structural + paged: the route sends the caller to text=, which refuses
    # offset/page — a second refusal one call later (round-9 finding 3).
    {"kind": "statement", "offset": 0, "min_confidence": _CONFIDENCE_FLOOR},
    {"kind": "statement", "page": 1, "order_by": "confidence"},
    {"tag": "pricing", "offset": 0, "min_confidence": _CONFIDENCE_FLOOR},
    {"tag": "pricing", "page": 1, "order_by": "confidence"},
    # text= arm + paged: the route changes entity_type, and the read is still paged
    {"text": "x", "entity_type": "document", "min_confidence": _CONFIDENCE_FLOOR,
     "offset": 0},
    {"text": "x", "entity_type": "document", "order_by": "graph", "page": 1},
])
def test_a_paged_caller_is_told_to_drop_offset_in_the_route(stub, kwargs) -> None:
    """Following a route to `text=` must not land on a second refusal (#12500 r9).

    Measured: `text=` with `offset=` answers "offset/page not supported with
    text", so a refusal whose route is "pass text=" left a paged caller one call
    from another error. The route now names the paging drop too. Fails if the
    `paginated` suffix is removed.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(**kwargs)["error"]

    assert "drop offset=/page=" in message, message
    assert "text= takes no offset" in message, message
    # …and it must be a whole SENTENCE. Appended to the route with its own full
    # stop it collided with the template's ", or drop …" and every paged refusal
    # shipped "… takes no offset., or drop min_confidence for this read"
    # (round-22 P2 2, measured on all six paged shapes). Pinning the two fragments
    # never caught it, so the junction is pinned here.
    assert "text= takes no offset.," not in message, message
    assert "text= takes no offset. " in message, message

    # …and it must qualify EVERY alternative, not read as trailing advice on the
    # last one. Appended as a final parenthetical it did exactly that, and
    # `text=`+`offset=` is refused whatever `entity_type` says, so following the
    # FIRST alternative ("pass entity_type='point'") landed on "offset/page not
    # supported with text" (round-21 P2 2, measured on both two-alternative
    # routes). The decidable form: execute the alternative, dropping only what the
    # message says to drop, and require a served read.
    if " or " in message:
        assert ". Also drop offset=/page=" in message, (
            f"a multi-alternative paged route qualifies the drop only at the end, "
            f"so it reads as applying to the last alternative: {message}"
        )


def test_every_alternative_of_a_paged_text_route_terminates(stub) -> None:
    """Each alternative the paged `text=` routes offer must be followable (#12500 r21).

    The paging parenthetical appended at the END of a route read as qualifying only
    the last alternative, and `text=`+`offset=` is refused whatever `entity_type`
    says (`if paginated:` → "offset/page not supported with text",
    mcp_server.py:4444-4446) — so following the FIRST alternative, "pass
    entity_type='point'", landed on "offset/page not supported with text"
    (round-21 P2 2, measured verbatim on both two-alternative routes). The routes
    now state the drop as its own sentence, for the whole route, and this test
    executes each alternative as written.

    Fails if the paging sentence stops qualifying every alternative (first
    assertion), or if an alternative it names still refuses (the executions).
    """
    from tortoise.mcp_server import tortoise_query

    # order_by only, paged: the route offers entity_type='point' OR relevance.
    order_only = tortoise_query(text="x", entity_type="document",
                                order_by="confidence", offset=0)["error"]
    assert ". Also drop offset=/page=" in order_only, order_only

    # both refused, paged: "Passing entity_type='point' fixes both".
    both = tortoise_query(text="x", entity_type="document",
                          min_confidence=_CONFIDENCE_FLOOR,
                          order_by="confidence", offset=0)["error"]
    assert ". Also drop offset=/page=" in both, both

    # Follow each FIRST alternative, dropping only what the message names. The
    # order-only route's first alternative is "pass entity_type='point'", which
    # FIXES THE ORDERING — so `order_by` stays. Dropping it as well (the round-21
    # version) executed the LAST alternative and left the claim "each alternative
    # as written" half-true (round-22 P2 4, measured).
    first = tortoise_query(text="x", entity_type="point", order_by="confidence")
    assert not _is_refusal(first), first
    fixes_both = tortoise_query(text="x", entity_type="point",
                                min_confidence=_CONFIDENCE_FLOOR,
                                order_by="confidence")
    assert not _is_refusal(fixes_both), fixes_both
    # …and the second alternative: relevance on a document read.
    relevance = tortoise_query(text="x", entity_type="document",
                               order_by="relevance")
    assert not _is_refusal(relevance), relevance


def test_an_operator_floor_refusal_names_the_gate_not_a_label(stub) -> None:
    """Operators ARE Points, so the false reason must not come back (#12500 r4/r10).

    The refuted sentence was `entity_type='operator' selects non-Point rows — so
    no row could ever be excluded`: `operator` maps to the `Point` label
    (`security.py:166-174`), and the floor is skipped because the SDK's gate is
    the literal `entity_type == "point"`, not because the rows are non-Point.
    Fails if the reason reverts to a label claim.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(text="x", entity_type="operator",
                             min_confidence=_CONFIDENCE_FLOOR)["error"]

    assert "literal" in message and "point" in message, message
    assert "selects non-Point rows" not in message, message


@pytest.mark.parametrize("kwargs", [
    {"tag": "pricing", "kind": "statement", "offset": 0},
    {"tag": "pricing", "kind": "statement", "page": 2},
])
def test_a_paged_kind_only_refusal_does_not_advise_dropping_paging(
        stub, kwargs) -> None:
    """The kind route lands on the STRUCTURAL arm, which pages (#12500 r10 P2 1).

    Appending the paging suffix to every route told a paged caller to drop
    `offset=` while sending them to an arm that supports it — and named `text=`,
    an arm the route never mentions, while silently downgrading the response from
    `{results,total,hasMore}` to a plain list. Fails if the suffix is
    unconditional.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(**kwargs)["error"]

    assert "pass kind= to the structural arm" in message, message
    assert "drop offset=/page=" not in message, message


@pytest.mark.parametrize("kwargs,expects", [
    # no text=: dropping tag= reaches the structural arm in one call
    ({"tag": "t", "kind": "statement"}, "drop tag= and pass kind="),
    # text= ALSO set: "drop tag=" alone would land on the text= arm, not the
    # structural arm the route promises, so the route must name text= too
    ({"tag": "t", "text": "x", "kind": "statement"},
     "drop tag= and text=, then pass kind="),
])
def test_the_kind_route_names_every_argument_that_must_be_dropped(
        stub, kwargs, expects) -> None:
    """`tag=` wins over `text=`, so the route must account for both (#12500 r11).

    Measured: with `text=` also set, "drop tag= and pass kind= to the structural
    arm" landed on the FTS leg (which is what answers once `tag=` goes) — an arm
    the sentence does not name — and a paged caller then hit "offset/page not
    supported with text" one call later. Fails if the `text=` clause is dropped
    from the route.
    """
    from tortoise.mcp_server import tortoise_query

    message = tortoise_query(**kwargs)["error"]

    assert expects in message, message
    assert "pass kind= to the structural arm" in message, message


def test_following_the_kind_route_from_tag_and_text_terminates(stub) -> None:
    """The route's own instruction, executed, must be served (#12500 r11 P2 1).

    Fails if the route says only "drop tag=": the retry keeps `text=`, the fts
    leg answers, and with `offset=` set it refuses instead — a dead end the round
    10 exemption assumed impossible on this route.
    """
    from tortoise.mcp_server import tortoise_query

    first = tortoise_query(tag="t", text="x", kind="statement", offset=0)

    # The refusal must EXIST and must carry the instruction this test follows.
    # Discarding `first` (as the first version did) made the whole test inert: it
    # passed with the entire guard disabled, and it passed on the round-10 route
    # whose instruction dead-ends (round-16 P2, both measured).
    assert _is_refusal(first), first
    assert "drop tag= and text=, then pass kind=" in first["error"], first["error"]

    # …and the destination the route names, reached as instructed:
    retry = tortoise_query(kind="statement", offset=0)

    assert not _is_refusal(retry), retry
