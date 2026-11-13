"""#12455 — `tortoise_query(text=...)` ACCEPTED `filters=` and DROPPED it.

The tool's signature takes ``filters`` and the docstring documents it for the
structural path. The ``text=`` arm forwards only
``kind``/``entity_type``/``limit``/``min_confidence``/``order_by`` into
``sdk.tortoise_fts_query`` — **no filters** — and ``_filters`` is not even
computed until *after* that arm has returned. So a caller who passes
``text="x", filters={"domain": "y"}`` was answered with the UNFILTERED
population, shaped as if the filter had applied.

That is the #6146 class the sibling issue #12372 exists to remove for
``entity_type``: a filter that is accepted and ignored returns MORE rows than
the caller asked for, and "nothing matched my filter" is indistinguishable from
"my filter never ran".

Why REFUSE rather than honour (option 1 of the issue's two): the fts leg has no
property-filter parameter to honour it with. ``sdk.tortoise_fts_query`` takes
``exclude_status`` / ``claim_class`` / ``exclude_turn_echo_session`` /
``relationship_filter`` / ``structural_kind`` / ``traversal_path`` — named
knobs, not a property-filter dict (``tortoise/sdk.py:40637-40710``). Making it
apply arbitrary filters is an SDK **surface** change, which the repo's own rule
reserves to the owner ("MCP/SDK Surface Approval"), and it would have to land at
the pre-truncation seam or it would silently return a filtered-looking page
smaller than ``limit``. Refusing removes the fail-open now.

BOUNDARY, deliberate and stated here so it cannot drift silently:
``include_retracted`` is honoured on the structural, paginated and tag arms only.
A caller who puts it in ``filters`` still gets it promoted to the parameter (the
handler pops it before this guard), and the ``text=`` arm still does not apply
it. That is documented in the tool's docstring; it is NOT what this guard
covers, and the test at the bottom pins the boundary rather than hiding it.

Test doctrine (Class B): every test states (1) the value whose presence makes it
fail and (2) that the fixture reaches that value.

Run with (stub-only, no database):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_12455_text_filters_dropped.py -q -p no:cacheprovider
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

#: The unfiltered population the stub's fts leg answers with. Its size is the
#: defect's signature: a caller who filtered for one row and received this many
#: was served the whole population, and the page looked authoritative.
_UNFILTERED_POPULATION = 7


class _StubSDK:
    """Records what each accessor was asked for; applies NO filter.

    The fts leg mirrors the real signature's knobs (``exclude_status``,
    ``claim_class``, ``relationship_filter``, …) and, exactly like the real one,
    has no property-filter parameter — so a test cannot accidentally pass by
    inventing a filter path the product does not have.
    """

    def __init__(self, population=_UNFILTERED_POPULATION):
        self.calls = []
        self.rows = [{"id": f"p{i}", "kind": "statement"} for i in range(population)]

    def tortoise_fts_query(self, query=None, kind=None, *, entity_type="point",
                           limit=10, min_confidence=0.0, order_by="relevance",
                           **kwargs):
        self.calls.append(("tortoise_fts_query", query, kind,
                           {"entity_type": entity_type, "limit": limit,
                            "min_confidence": min_confidence,
                            "order_by": order_by, **kwargs}))
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
    """Drive the tool bodies directly (no HTTP middleware).

    `_safe` fail-closes when `_transport_mode` is None, and the stdio arm
    refuses when `TORTOISE_API_KEY` is present — the `http` arm is what this code
    runs under in production and it delegates auth to the transport, which is
    exactly what a tool-body test wants.
    """
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


# ── the defect ────────────────────────────────────────────────────────────────

def test_the_text_arm_refuses_a_filters_dict_it_cannot_apply(stub) -> None:
    """RED before the fix: the unfiltered population came back, in full.

    Fails on ``_is_refusal(result) is False`` — and, with the size assertion, on
    a page the caller never asked for. The fixture reaches it because the stub's
    fts leg answers with the whole population, which is what the real one does
    when no filter reaches it.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="anything", filters={"domain": "legal"})

    assert _is_refusal(result), (
        "text= + filters= was accepted and the filter was DROPPED — the caller "
        f"received {len(result) if isinstance(result, list) else 'a non-list'} "
        f"rows out of {_UNFILTERED_POPULATION} with no signal that "
        "filters={'domain': 'legal'} never ran"
    )
    assert "results" not in result, (
        "a refusal must not also carry the unfiltered page"
    )


def test_the_refusal_names_the_argument_and_the_arm_that_applies_it(stub) -> None:
    """RED before the fix: there was no message at all.

    Fails unless the message names ``filters``, the ``text=`` arm it does not
    apply to, and the structural path that does — a generic "invalid argument"
    would leave the caller unable to act.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="anything", filters={"domain": "legal"})

    assert _is_refusal(result), "no refusal to read a message off"
    message = result["error"]
    assert "filters" in message, message
    assert "text=" in message, message
    assert "structural" in message, message


def test_the_refusal_happens_before_the_read(stub) -> None:
    """RED before the fix: the fts leg WAS called and its rows were served.

    Refusing after the read would still pay for it, and — the reason this is its
    own test — a guard placed on the result could still serve rows on an error
    path. Fails on any ``tortoise_fts_query`` entry in ``stub.calls``.
    """
    from tortoise.mcp_server import tortoise_query

    tortoise_query(text="anything", filters={"domain": "legal"})

    assert [c for c in stub.calls if c[0] == "tortoise_fts_query"] == [], (
        f"the fts leg was read before refusing: {stub.calls}"
    )


@pytest.mark.parametrize("filters", [
    {"domain": "legal"},                      # one property
    {"domain": "legal", "tenant": "acme"},    # two
    '{"domain": "legal"}',                    # a JSON string, as MCP delivers it
])
def test_every_shape_of_a_non_empty_filters_refuses(stub, filters) -> None:
    """RED before the fix for all three shapes.

    The JSON-string shape is the one that matters most: ``_parse`` hands it
    through as a string, so a guard written as ``isinstance(filters, dict)``
    would miss the exact form the MCP transport delivers.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="anything", filters=filters)

    assert _is_refusal(result), (
        f"filters={filters!r} was accepted on the text arm and dropped"
    )


# ── the guard must not over-block ─────────────────────────────────────────────

def test_text_without_filters_still_serves(stub) -> None:
    """A false block here would be worse than the bug: this is the hot path.

    Fails if the guard refuses or perturbs the served page — the fix must not
    touch a call that has no filters to drop.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="anything")

    assert not _is_refusal(result), f"refused a legitimate text= read: {result}"
    assert len(result) == _UNFILTERED_POPULATION, result


def test_an_empty_filters_dict_is_not_a_refusal(stub) -> None:
    """Nothing to drop is not a drop.

    Fails if the guard keys on ``filters is not None`` instead of on content —
    which would refuse the many clients that serialise an empty object.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="anything", filters={})

    assert not _is_refusal(result), f"refused an EMPTY filters dict: {result}"
    assert len(result) == _UNFILTERED_POPULATION, result


def test_the_structural_arm_still_applies_the_same_filters(stub) -> None:
    """The filter itself is legitimate — only the combination is refused.

    Fails if the guard is placed above the whole body (refusing the structural
    path too) or if the filters stop reaching the accessor.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(kind="statement", filters={"domain": "legal"})

    assert not _is_refusal(result), f"the structural arm was blocked: {result}"
    forwarded = [c for c in stub.calls if c[0] == "paginated_query"]
    assert forwarded, f"the structural arm never read: {stub.calls}"
    assert forwarded[0][4].get("domain") == "legal", (
        f"the filter did not reach the accessor: {forwarded[0][4]}"
    )


# ── the boundary this guard does NOT cover, pinned so it cannot drift ────────

def test_include_retracted_in_filters_is_promoted_and_the_text_arm_ignores_it(
    stub,
) -> None:
    """The one documented exception, asserted rather than left implicit.

    ``include_retracted`` is popped out of ``filters`` into the parameter before
    this guard runs, so it is not a property filter and must not be refused. The
    ``text=`` arm does not apply it (the fts leg excludes terminal statuses
    unless it is given ``include_terminal``), and the tool's docstring says so.
    This test pins BOTH halves: a future change that starts refusing this shape
    over-blocks, and one that silently starts applying it changes behaviour
    without the docstring moving.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="anything", filters={"include_retracted": True})

    assert not _is_refusal(result), (
        "include_retracted is promoted to the parameter, not a property filter — "
        "refusing it here is a false block"
    )
    fts = [c for c in stub.calls if c[0] == "tortoise_fts_query"]
    assert fts, f"the text arm did not read: {stub.calls}"
    assert "include_terminal" not in fts[0][3], (
        "the text arm started forwarding include_retracted as include_terminal — "
        "that is a behaviour change the docstring and the guard must move with"
    )


def test_the_docstring_does_not_advertise_filters_for_the_text_arm() -> None:
    """The issue's other acceptance half: the docs must stop implying it applies.

    Fails if the ``text=`` bullet does not name ``filters`` — the docstring is the
    only place a caller learns this before making the call. The bullet is read
    as the head line PLUS its continuation lines (up to the next ``- `` bullet),
    because that is how a wrapped docstring bullet is written; reading only the
    head line would make this test unfalsifiable by any wording change that
    wraps.
    """
    from tortoise.mcp_server import tortoise_query

    doc = inspect.getdoc(tortoise_query) or ""
    lines = doc.splitlines()
    heads = [i for i, line in enumerate(lines)
             if line.strip().startswith("- text=<query>")]
    assert heads, "the `text=<query>` docstring bullet is missing"
    start = heads[0]
    end = next(
        (j for j in range(start + 1, len(lines))
         if lines[j].strip().startswith("- ")),
        len(lines),
    )
    bullet = " ".join(lines[start:end])
    assert "filters" in bullet, (
        "the `text=` bullet does not mention `filters`, so a caller still reads "
        f"the structural documentation as applying here: {bullet!r}"
    )
    assert "refus" in bullet.lower(), (
        "the `text=` bullet mentions filters without saying they are REFUSED, "
        f"which still reads as `applies unless said otherwise`: {bullet!r}"
    )
