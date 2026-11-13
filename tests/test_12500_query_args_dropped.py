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

    Fails on ``_is_refusal(result) is False``. The second assertion is the one
    that shows WHY that matters, and it has to be written against the shape this
    arm really returns: un-paginated the arm answers a bare ``list``, so a
    ``"results" not in result`` check would be inert (round-1 finding 4 made this
    real). With the guard removed the call returns 3 rows at confidence 0.01 —
    exactly the rows the floor of 0.9 excludes — so this reds on the rows, not
    just on the refusal.
    """
    from tortoise.mcp_server import tortoise_query

    value = _CONFIDENCE_FLOOR if argument == "min_confidence" else "confidence"
    result = tortoise_query(**base, **{argument: value})

    assert _is_refusal(result), (
        f"{argument}={value!r} on {sorted(base)} was accepted and DROPPED — the "
        f"accessor takes no such parameter, so the caller got rows they excluded: "
        f"{result if not isinstance(result, list) else len(result)}"
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

    Fails if the guard's condition is broader than the set of arguments the arm
    cannot apply (e.g. keying on ``order_by is not None`` alone would refuse the
    default everywhere).
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(**kwargs)

    assert not _is_refusal(result), f"refused a legitimate {sorted(kwargs)}: {result}"
    assert stub.calls, f"{sorted(kwargs)} never read anything"


def test_defaults_are_not_refusals(stub) -> None:
    """`min_confidence=None` / `order_by=None` are the DEFAULTS — never refused.

    Fails if the guard keys on the parameter's presence rather than on a value
    the caller actually set, which would refuse every structural call.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(kind="statement", min_confidence=None, order_by=None)

    assert not _is_refusal(result), f"refused the defaults: {result}"


@pytest.mark.parametrize("kwargs", [
    {"kind": "statement", "min_confidence": 0.0},
    {"kind": "statement", "offset": 0, "min_confidence": 0.0},
    {"kind": "statement", "order_by": ""},
    {"tag": "pricing", "kind": ""},
    {"tag": "pricing", "min_confidence": 0.0},
    {"tag": "pricing", "order_by": ""},
])
def test_falsy_values_are_no_ops_and_never_refused(stub, kwargs) -> None:
    """A client that serialises its own DEFAULTS must not be refused (#12500 r1).

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
