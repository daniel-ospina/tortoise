"""#12500 — `min_confidence` / `order_by` (and `kind` on the tag arm) were
accepted by `tortoise_query` and DROPPED on the arms that cannot apply them.

The tool's signature takes ``min_confidence`` and ``order_by``, and its docstring
presents them as general arguments — but only the ``text=`` arm consumes them
(forwarded into ``sdk.tortoise_fts_query``). Both structural arms call
``sdk.paginated_query(kind, skip, limit, include_retracted, **filters)``, whose
signature has **neither** parameter (``tortoise/sdk.py:26582-26585``), and the tag
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
    ordering, so a caller who names one gets a wider population than asked for.
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
    keyed on the presence of ``filters``, or one that refuses every structural
    read. It does NOT falsify `is not None` (these cases pass no such argument,
    so the value is `None`); that mutant is caught by
    ``test_falsy_values_are_no_ops_and_never_refused``, which is stated here
    rather than claimed away (round-2 finding 2).
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
    the fix: all six shapes REFUSED.
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
    `entity_type == "point"` (`sdk.py:43206` — `ep` is `None` for every other
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


def test_the_text_arm_still_applies_the_floor_to_POINTS(stub) -> None:
    """The control for the branch above: `entity_type="point"` is the default.

    Fails if the new condition is written without the `entity_type != "point"`
    clause (over-block: the arm that DOES apply the floor gets refused) or
    without the truthiness key (`min_confidence=0.0` is the shipped "filter off"
    default and must stay served).
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
    assert " and " in two["error"], two["error"]
    assert " are not applied" in two["error"], two["error"]
    assert " is not applied" in one["error"], one["error"]
    # BOTH reasons, not just both names — a join that keeps only the first
    # explains one argument while naming two (round-6 P2 4).
    assert "confidence floor" in two["error"], two["error"]
    assert "ordering" in two["error"], two["error"]


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


@pytest.mark.parametrize("entity_type", ["document", "source"])
def test_the_confidence_ordering_is_refused_where_no_row_carries_a_belief(
        stub, entity_type) -> None:
    """`order_by="confidence"` sorts on an EP belief neither type has (r4 P2 2).

    `GraphRanker._fetch_signals` reads EPISTEMIC_LABELS = ("Point", "Subject",
    "Object", "Event") and BOTH `document` and `source` map to the `Source`
    label, so the sweep answers `{}`, every key falls to `(1, 0.0)`, and the
    stable sort leaves the RRF order — unmarked, so the caller cannot tell the
    order was never applied. Fails if the branch is missing (`_is_refusal` false)
    or widened to the types that DO honour it (next test).
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="x", entity_type=entity_type,
                            order_by="confidence")

    assert _is_refusal(result), (
        f"served order_by='confidence' for entity_type={entity_type!r}, which has "
        f"no belief to sort on"
    )
    assert "order_by" in result["error"], result["error"]


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
@pytest.mark.parametrize("order_by", ["relevance", "graph"])
def test_a_non_confidence_ordering_is_served_for_every_type(
        stub, entity_type, order_by) -> None:
    """The refusal is keyed on `order_by == "confidence"`, not on the TYPE (#12500 r5).

    Mutating the condition to `if order_by and entity_type in (…, "source")` left
    the whole suite green while false-blocking `entity_type="document",
    order_by="relevance"` — the very fallback the refusal message recommends for
    a read that works. Fails if the value axis drops out of the condition.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="x", entity_type=entity_type, order_by=order_by)

    assert not _is_refusal(result), (
        f"refused order_by={order_by!r} for entity_type={entity_type!r}: the arm "
        f"applies any value other than 'confidence'"
    )
    fts = [c for c in stub.calls if c[0] == "tortoise_fts_query"]
    assert fts and fts[-1][3]["order_by"] == order_by


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
#: some point in this PR. The docstring is served product text, so a false claim
#: in it steers every reader of the tool contract; "the docstring says nothing
#: wrong" is not decidable, but "these exact claims are gone" is.
_DOCSTRING_FALSE = (
    # round-5: order_by is applied for every type but document/source, and
    # `event`/`subject`/`object`/`operator` DO carry a belief (measured live).
    "only a Point carries one",
    'applied on the text= arm for `entity_type="point"`',
    # round-6: only a CONFIDENCE ordering is refused there — `graph` and
    # `relevance` really do reorder document/source reads. This sentence was
    # mandated as TRUE by this very test for one round, which is how a pin turns
    # into a false-PASS generator.
    "for every type EXCEPT `document`/`source`",
    # round-4: operators are Points, so this reason was self-contradictory.
    "selects non-Point rows",
)


def test_the_served_docstring_never_restates_a_falsified_claim() -> None:
    """The false claims this PR shipped, pinned so they cannot come back.

    Both are wrap-insensitive (whitespace-collapsed), because a re-wrap must not
    silently disarm the pin — the `#12455` lesson. Fails if any reappears in
    `tortoise_query.__doc__`.
    """
    from tortoise.mcp_server import tortoise_query

    flat = " ".join((tortoise_query.__doc__ or "").split())

    for claim in _DOCSTRING_FALSE:
        assert " ".join(claim.split()) not in flat, (
            f"the docstring restates a falsified claim: {claim!r}"
        )
    # …and the TRUE statement is present, so the pin is not satisfied by silence.
    assert "except a `confidence` ordering on `document`/`source`" in flat, flat[:400]


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
