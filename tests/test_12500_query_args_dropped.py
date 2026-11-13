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


@pytest.mark.parametrize("entity_type", ["document", "event", "session"])
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
