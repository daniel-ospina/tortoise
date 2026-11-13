"""#12372 — `tortoise_query(entity_type=...)` was ACCEPTED and DROPPED.

The tool's signature takes ``entity_type: str = "point"``, but only the ``text=``
arm forwards it (into ``sdk.tortoise_fts_query``, which honours it). The
paginated arm and the plain structural arm call
``sdk.paginated_query(kind, skip, limit, **_filters)`` — **no ``entity_type``**
— and ``paginated_query``/``query`` are Point readers by construction (no
``entity_type`` parameter at all). The tag arm ignores it too, by its own
docstring.

Measured 2026-11-13 against ``live-v4`` (read-only)::

    tortoise_query(entity_type="operator", offset=0, limit=5) -> total 15842
    tortoise_query(entity_type="point",    offset=0, limit=5) -> total 15842
    (the operator population is actually 3,727 rows — structure.operators)

So a caller asking for operators was served the Points population WITH a
``total`` and ``hasMore`` that read as authoritative. That is a false PASS on a
read instrument — the wrong-answer-is-indistinguishable-from-the-right-one class
(#6146).

The fix (option 2 in the issue, the smaller one that removes the fail-open
immediately): the arms that cannot serve a non-Point entity type REFUSE, naming
the surface that can. ``entity_type="point"`` — the default — stays served
everywhere, and the ``text=`` arm keeps forwarding it unchanged.

Test doctrine (Class B): every test states (1) the value whose presence makes it
fail and (2) that the fixture reaches that value.

Run with (stub-only, no database):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_12372_entity_type_dropped.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re

import pytest


class _StubSDK:
    """A Points-only graph whose accessors record what they were asked for.

    ``paginated_query``/``query`` behave like the SDK's own: they read the Point
    label and have NO ``entity_type`` parameter. The ``point_population`` value
    is the defect's signature — 15,842 is what ``live-v4`` answered to BOTH
    ``entity_type="operator"`` and ``entity_type="point"`` (measured 2026-11-13),
    so a stub of that size makes "the operator query was served the Points"
    visible as the returned ``total``.
    """

    def __init__(self, point_population=15_842, *, n_tag_points=5):
        self.calls = []
        self.points = [{"id": f"p{i}", "kind": "statement"}
                       for i in range(point_population)]
        self.n_tag_points = n_tag_points

    def query(self, kind=None, **kwargs):
        self.calls.append(("query", kind, kwargs))
        return self.points

    def paginated_query(self, kind=None, skip=0, limit=20, **kwargs):
        self.calls.append(("paginated_query", kind, skip, limit, kwargs))
        total = len(self.points)
        return {"results": self.points[skip:skip + limit], "total": total,
                "hasMore": skip + limit < total}

    def query_points_by_tag(self, tag, *, _record_reads=True):
        self.calls.append(("query_points_by_tag", tag, _record_reads))
        return [{"id": f"tag{i}"} for i in range(self.n_tag_points)]

    def tortoise_fts_query(self, query=None, kind=None, **kwargs):
        self.calls.append(("tortoise_fts_query", query, kind, kwargs))
        return [{"id": "op0", "is_operator": True}]


@pytest.fixture(autouse=True)
def _http_transport():
    """Drive the tool bodies directly (no HTTP middleware).

    `_safe` fail-closes when `_transport_mode` is None, and the stdio arm
    refuses when `TORTOISE_API_KEY` is present — the `http` arm is what this
    code runs under in production and it delegates auth to the transport, which
    is exactly what a tool-body test wants.
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
def stub_factory(monkeypatch):
    from tortoise import mcp_server

    def make(point_population=15_842, *, n_tag_points=5):
        stub = _StubSDK(point_population, n_tag_points=n_tag_points)
        monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: stub)
        return stub

    return make


# The documented vocabulary of the surface that CAN enumerate these
# (`tortoise_search`'s own docstring) minus `point`, which the structural arms
# legitimately serve.
_UNSERVED_TYPES = ("event", "subject", "document", "object", "operator", "source")


def _is_refusal(result) -> bool:
    from tortoise.mcp_server import _SafeError
    return isinstance(result, _SafeError) and "error" in result


@pytest.mark.parametrize("entity_type", _UNSERVED_TYPES)
def test_the_paginated_arm_never_serves_the_point_population(stub_factory,
                                                            entity_type) -> None:
    """RED before the fix: `total` was the Points' 15,842 for every one of these.

    Fails on the value `is_refusal(result) is False` / `total == 15842`; the
    fixture reaches it because the stub serves exactly that many Points and the
    arm used to return them verbatim.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory(point_population=15_842)

    result = tortoise_query(entity_type=entity_type, offset=0, limit=5)

    assert _is_refusal(result), (
        f"entity_type={entity_type!r} was ACCEPTED on the paginated arm — a "
        f"caller asking for {entity_type}s was silently served the Points "
        f"population ({result.get('total') if isinstance(result, dict) else 'rows'})"
    )
    assert isinstance(result, dict) and "results" not in result, (
        "a refusal must not also carry a page of the wrong population"
    )


@pytest.mark.parametrize("entity_type", _UNSERVED_TYPES)
def test_the_plain_structural_arm_never_serves_the_point_population(stub_factory,
                                                                   entity_type) -> None:
    """RED before the fix: the plain arm returned the Points as a bare list.

    Fails on the value `is_refusal(result)`; the fixture reaches it because the
    plain arm used to reach `paginated_query` and hand back its `results`.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory(point_population=50)

    result = tortoise_query(entity_type=entity_type, limit=2)

    assert _is_refusal(result), (
        f"entity_type={entity_type!r} was ACCEPTED on the plain structural arm: "
        f"got {type(result).__name__} instead of a refusal"
    )


def test_a_refused_entity_type_never_reaches_a_point_reader(stub_factory) -> None:
    """The refusal must be FAIL-CLOSED: no read at all, not a read then discarded.

    Fails on the value `stub.calls == []`. A guard placed after the read would
    still pay it (and still be able to serve it on an error path).
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    tortoise_query(entity_type="operator", offset=0, limit=5)

    assert stub.calls == [], (
        f"the unsupported entity_type still reached a Point reader: {stub.calls}"
    )


def test_the_refusal_names_the_surface_that_can_serve_it(stub_factory) -> None:
    """The point of refusing rather than ignoring: the caller can ACT on it.

    Fails on the substring `tortoise_search` and on the offending value being
    named; a generic "invalid argument" would fail here.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory()

    result = tortoise_query(entity_type="operator", offset=0, limit=5)

    message = result["error"]
    assert "tortoise_search" in message, (
        f"the refusal must name the surface that CAN enumerate operators: {message!r}"
    )
    from tortoise.mcp_server import _ENTITY_TYPE_REMEDY
    assert _ENTITY_TYPE_REMEDY["operator"] in message, (
        "the refusal must carry the operator REMEDY, not merely the word: the "
        f"message prefix already spells entity_type='operator'. {message!r}"
    )


def test_a_value_outside_the_entity_type_vocabulary_is_refused_too(stub_factory) -> None:
    """`entity_type="banana"` was also silently answered with the Points.

    Fails on `is_refusal(result)`; the fixture reaches it because a nonsense
    value used to take the identical drop-the-argument path.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory()

    result = tortoise_query(entity_type="banana", offset=0, limit=5)

    assert _is_refusal(result), (
        "an entity_type that is not in VALID_ENTITY_TYPES must refuse, not "
        "return the Point population"
    )


def test_an_unknown_value_is_told_it_is_not_an_entity_type(stub_factory) -> None:
    """A TYPO and an UNSERVED type deserve different answers.

    Without the vocabulary arm a nonsense value falls into the same refusal and
    is told to "use tortoise_search(entity_type='banana')" — advice that names
    a value the surface does not have. Fails on `"Invalid entity_type" in
    message` and on `tortoise_search` being ABSENT from it (the generic arm's
    message does contain it), so deleting the vocabulary branch reds this.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory()

    result = tortoise_query(entity_type="banana", offset=0, limit=5)

    message = result["error"]
    assert "Invalid entity_type" in message, (
        f"a value outside the vocabulary is not an unsupported layer: {message!r}"
    )
    assert "operator" in message, (
        f"the message must list the real vocabulary: {message!r}"
    )
    assert "tortoise_search" not in message, (
        "a typo must not be told to enumerate a type that does not exist: "
        f"{message!r}"
    )


# ── no false blocks: the shapes that MUST keep working ──────────────────────


def test_point_stays_served_on_the_paginated_arm(stub_factory) -> None:
    """`entity_type="point"` is the default and is CORRECT here.

    Fails on `out.get("total") == 5`; a guard that refused every entity_type
    (including the one these arms actually serve) would red this.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory(point_population=5)

    out = tortoise_query(entity_type="point", offset=0, limit=2)

    assert isinstance(out, dict) and out.get("total") == 5, (
        f"entity_type='point' must stay served: {out!r}"
    )


def test_omitting_entity_type_is_unchanged(stub_factory) -> None:
    """The overwhelmingly common call passes NO entity_type — unchanged.

    Fails on `len(out) == 2`; the fixture holds 5 Points and a bound of 2.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory(point_population=5)

    out = tortoise_query(kind="statement", limit=2)

    assert isinstance(out, list) and len(out) == 2, f"plain call regressed: {out!r}"


def test_the_text_arm_still_honours_entity_type(stub_factory) -> None:
    """`text=` forwards entity_type into the fts leg, which DOES honour it.

    Fails on `len(out) == 1`; a guard placed above the branches (instead of
    after the text arm) would refuse a combination that works, and the stub's
    fts leg answers one operator row to make the served population visible.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    out = tortoise_query(text="promotion", entity_type="operator", limit=1)

    assert isinstance(out, list) and out == [{"id": "op0", "is_operator": True}], (
        f"the text arm must keep forwarding entity_type: {out!r}"
    )
    assert stub.calls[-1][0] == "tortoise_fts_query"
    assert stub.calls[-1][3].get("entity_type") == "operator", (
        "entity_type must reach the fts leg unchanged"
    )


def test_the_tag_arm_refuses_a_non_point_entity_type(stub_factory) -> None:
    """The tag arm documents that it IGNORES entity_type — the same fail-open.

    Documented-and-ignored is still a wrong population served to a caller who
    asked for another one, so tag mode refuses it too. Fails on
    `is_refusal(result)`; the stub would otherwise answer 5 tagged Points.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    result = tortoise_query(tag="topic:x", entity_type="object", offset=0, limit=2)

    assert _is_refusal(result), (
        "tag mode ignored entity_type='object' and served pointed-tagged rows"
    )
    assert stub.calls == [], (
        f"the tag arm read the Points before refusing: {stub.calls}"
    )


def test_the_tag_arm_still_works_without_an_entity_type(stub_factory) -> None:
    """No false block on the tag arm's normal call. Fails on `len(out) == 2`."""
    from tortoise.mcp_server import tortoise_query
    stub_factory(n_tag_points=5)

    out = tortoise_query(tag="topic:x", offset=0, limit=2)

    assert isinstance(out, dict) and out.get("total") == 5, f"tag arm regressed: {out!r}"


# ── review round 1: the remedy must not be a second false PASS ──────────────


def test_every_refused_type_has_a_remedy(stub_factory) -> None:
    """Every refused type must carry its OWN remedy, not the generic fallback.

    The handler reads `_ENTITY_TYPE_REMEDY.get(...)` with a fallback, so a
    missing entry is a structured refusal, never a `KeyError` (pinned by
    `test_a_vocabulary_member_with_no_remedy_refuses_instead_of_raising`).
    What this test protects is SPECIFICITY: a fallback for every type would
    answer the call and tell the caller nothing. Fails on `missing == set()`.
    """
    from tortoise.mcp_server import _ENTITY_TYPE_REMEDY
    from tortoise.security import VALID_ENTITY_TYPES

    missing = set(VALID_ENTITY_TYPES) - {"point"} - set(_ENTITY_TYPE_REMEDY)
    assert missing == set(), f"refused types with no remedy: {sorted(missing)}"


def _message_for(entity_type: str) -> str:
    from tortoise.mcp_server import tortoise_query
    return tortoise_query(entity_type=entity_type, offset=0, limit=5)["error"]


def test_the_remedy_for_operator_names_the_search_with_its_limit() -> None:
    """`operator` is the one type a search REACHES directly — with an explicit limit.

    "Reaches", not "lists": the leg answers a status-filtered VIEW of operators
    stated `is_operator = true`, so the remedy must bound the call and disclose
    the view (the entry does; this test guards the bound half).

    `tortoise_search` defaults to `limit=10`, has no offset walk, and slices at
    `limit` (`sdk.py`: `result_ids = result_ids[:limit]`), returning a bare list
    with no total/hasMore — so a limit-less call answers a SILENTLY TRUNCATED
    layer, the same false-PASS class this guard removes. `tools/curate_graph.py`
    reads the 3,727-row layer as `tortoise_search(entity_type="operator",
    limit=OPERATOR_READ_CEILING)`, measured.
    Fails on the `, limit=` in the literal call string: a limit-less remedy
    (which would truncate at 10) reds here, and that is the point.
    """
    message = _message_for("operator")
    assert "tortoise_search(entity_type='operator', limit=" in message, (
        f"a limit-less search answers a truncated layer as if complete: {message!r}"
    )


@pytest.mark.parametrize("entity_type,expected", [
    ("event", "tortoise_get_entity(None, type='events'"),
    ("source", "tortoise_graph_overview(section='sources')"),
])
def test_the_remedy_names_a_call_that_answers_without_a_kind(entity_type,
                                                             expected) -> None:
    """`run_structural_query` returns `[]` when it has NO conditions.

    So `tortoise_search(entity_type="event")` with neither a query nor a kind is
    an `empty_results` RECORD, not an empty layer — naming it as the remedy
    would re-create the exact false PASS this guard removes, one level down.
    These two types have a real one-call enumerator; the message must name it.
    Fails on the literal substring; the fixture reaches it because the message
    is built from the mapping the caller is told to act on.
    """
    message = _message_for(entity_type)
    assert expected in message, (
        f"the remedy for {entity_type!r} must name a call that answers without "
        f"a caller-invented kind: {message!r}"
    )


@pytest.mark.parametrize("entity_type", ["event", "subject", "document",
                                         "object", "source"])
def test_no_remedy_promises_a_bare_search_that_would_answer_empty(entity_type) -> None:
    """The generic advice `tortoise_search(entity_type=X)` is FALSE for 5 types.

    `operator` is the only type whose structural leg adds a condition of its own
    (`n.is_operator = true`); for the rest the leg has no conditions to fire and
    returns `[]` with `reason="empty_results"`. Fails on the bare-call substring
    being present; the fixture reaches it because the message embeds the remedy
    string verbatim.
    """
    message = _message_for(entity_type)
    bare = f"tortoise_search(entity_type={entity_type!r})"
    assert bare not in message, (
        f"the remedy for {entity_type!r} promises a call that answers [] with "
        f"no conditions: {message!r}"
    )


def test_none_means_point_on_the_structural_arm(stub_factory) -> None:
    """A payload with a missing `entity_type` key hands us None (#12372 review P2).

    Fails on `out.get("total") == 5`; before the normalisation the same value
    also reached the `text=` arm raw and the SDK refused it (`ValueError`), so
    one spelling meant two things depending on the arm.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory(point_population=5)

    out = tortoise_query(entity_type=None, offset=0, limit=2)

    assert isinstance(out, dict) and out.get("total") == 5, f"None is not point: {out!r}"


def test_none_means_point_on_the_text_arm_too(stub_factory) -> None:
    """The SAME resolved value must reach the fts leg, not the raw None.

    Fails on `entity_type == "point"` in the forwarded kwargs; before the
    normalisation the raw `None` was forwarded and the SDK raised
    (`entity_type must be 'point', 'event', …`, sdk.py:40829).
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    tortoise_query(text="promotion", entity_type=None, limit=1)

    forwarded = stub.calls[-1][3].get("entity_type")
    assert forwarded == "point", (
        f"the text arm was handed {forwarded!r} instead of the resolved value"
    )


def test_the_vocabulary_refusal_is_the_owners_message(stub_factory) -> None:
    """`security.validate_entity_type` is the single owner of this refusal.

    A second copy of the wording is what drifts; the handler must surface the
    owner's message VERBATIM. Fails on exact-string equality; the fixture
    reaches it because both strings are built from the same sorted vocabulary.
    """
    from tortoise.mcp_server import tortoise_query
    from tortoise.security import VALID_ENTITY_TYPES
    stub_factory()

    message = tortoise_query(entity_type="banana", offset=0, limit=5)["error"]
    expected = (f"Invalid entity_type: {'banana'!r}. Must be one of "
                f"{sorted(VALID_ENTITY_TYPES)}.")
    assert message == expected, f"the owner's message was re-typed: {message!r}"


# ── review round 2: one invalid value, one answer, on EVERY arm ─────────────


def test_an_invalid_value_gets_the_owners_message_on_the_text_arm_too(stub_factory) -> None:
    """The vocabulary check is above the arms, so the `text=` arm cannot answer
    with the SDK's own re-typed copy.

    Before the hoist, `text=...` + `entity_type="banana"` surfaced
    `entity_type must be 'point', 'event', …` from `sdk.py:40830` while the
    structural arms surfaced the owner's message — one invalid value, two
    answers. Fails on exact-string equality with `security.validate_entity_type`.
    """
    from tortoise.mcp_server import tortoise_query
    from tortoise.security import VALID_ENTITY_TYPES
    stub = stub_factory()

    result = tortoise_query(text="promotion", entity_type="banana", limit=1)

    expected = (f"Invalid entity_type: {'banana'!r}. Must be one of "
                f"{sorted(VALID_ENTITY_TYPES)}.")
    assert result["error"] == expected, f"the text arm answered differently: {result!r}"
    assert stub.calls == [], "an invalid entity_type must not reach the fts leg"


def test_a_vocabulary_member_with_no_remedy_refuses_instead_of_raising(
        stub_factory, monkeypatch) -> None:
    """`_ENTITY_TYPE_REMEDY` is read with a fallback, so a missing entry is a
    REFUSAL, not a `KeyError` escaping the tool body.

    The docstring promises a structured error dict for validation violations;
    only a test (the coverage test above) stood between a future vocabulary
    addition and an unhandled exception. Fails on `_is_refusal(result)`; the
    fixture reaches it by deleting the one entry the call needs.
    """
    from tortoise import mcp_server
    stub_factory()
    monkeypatch.delitem(mcp_server._ENTITY_TYPE_REMEDY, "operator")

    result = mcp_server.tortoise_query(entity_type="operator", offset=0, limit=5)

    assert _is_refusal(result), (
        f"a missing remedy must still be a refusal, not an exception: {result!r}"
    )
    assert "No bulk enumeration" in result["error"], (
        f"the fallback remedy was not used: {result['error']!r}"
    )


# ── review round 3: the two regressions that passed all 35 tests ─────────────


@pytest.mark.parametrize("arm", ["structural", "text"])
def test_empty_string_means_point_too(stub_factory, arm) -> None:
    """`entity_type=""` is the same caller intent as `None` — on BOTH arms.

    Mutating `(None, "")` back to `is None` passed all 35 earlier tests: only
    `None` was covered, so `""` could silently revert to a vocabulary refusal
    while the docstring promises one meaning for a missing key. Fails on the
    served rows; the fixture holds 5 Points.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory(point_population=5, n_tag_points=5)

    if arm == "structural":
        out = tortoise_query(entity_type="", offset=0, limit=2)
        assert isinstance(out, dict) and out.get("total") == 5, (
            f"entity_type='' must be served as point: {out!r}"
        )
    else:
        out = tortoise_query(text="promotion", entity_type="", limit=1)
        assert isinstance(out, list) and out == [{"id": "op0", "is_operator": True}], (
            f"entity_type='' must be served as point on the text arm: {out!r}"
        )


def test_tag_takes_precedence_over_text_so_it_must_still_refuse(stub_factory) -> None:
    """`tag=` + `text=` + a non-point type served the TAGGED POINTS.

    Mutating the guard's `tag is not None or` clause away passed all 35 earlier
    tests: no test combined tag, text and a non-point type, and tag mode takes
    precedence over text — so that combination reached `query_points_by_tag` and
    answered Points. Fails on `stub.calls == []` and on the refusal.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    result = tortoise_query(tag="topic:x", text="anything", entity_type="object",
                            offset=0, limit=2)

    assert _is_refusal(result), (
        f"tag+text+entity_type='object' served the tagged Points: {result!r}"
    )
    assert stub.calls == [], f"the tag arm read before refusing: {stub.calls}"


def test_a_whitespace_only_text_refuses_where_the_legs_would_answer_empty(
        stub_factory) -> None:
    """A blank `text` takes the fts arm, whose legs test `query.strip()`.

    With no `kind` the structural leg then has NO conditions and answers `[]`
    (`reason="empty_results"`) — an empty result indistinguishable from a
    complete one, on the arm the guard otherwise leaves open. Fails on
    `is_refusal(result)`; the fixture reaches it because `"  "` is truthy, so a
    guard testing only `not text` lets it through.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    result = tortoise_query(text="  ", entity_type="event", limit=5)

    assert _is_refusal(result), (
        f"a blank text answered [] for a non-point type: {result!r}"
    )
    assert stub.calls == [], "the blank-text arm read before refusing"


def test_a_blank_text_with_a_kind_still_serves(stub_factory) -> None:
    """The false block to avoid: `text=" "` + `kind=` + `entity_type=` DOES work.

    The fts arm honours `entity_type` and the kind clause gives the structural
    leg its condition, so refusing this would be over-blocking a working call.
    Fails on the forwarded kwargs reaching the fts leg.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    out = tortoise_query(text="  ", kind="AgentSession", entity_type="event", limit=1)

    assert isinstance(out, list), f"a working combination was refused: {out!r}"
    assert stub.calls[-1][0] == "tortoise_fts_query"
    assert stub.calls[-1][3].get("entity_type") == "event"


@pytest.mark.parametrize("entity_type", ["document", "object", "subject"])
def test_a_non_enumerable_remedy_states_its_bound_and_the_real_size_source(
        entity_type) -> None:
    """These three have no bulk listing, so the remedy must say so USEFULLY.

    An answer that slices at `limit` (default 10) with no total is the same
    false PASS as a limit-less `operator` search, and `kind=...` alone is the
    caller inventing the filter that makes the leg fire at all. So each entry
    must name the bound it hits (`limit=`) and where the layer's SIZE is read
    (`graph_overview(section='status')`), not merely "no bulk enumeration".
    Fails on both substrings; the fixture reaches them because the message
    embeds the remedy verbatim.
    """
    message = _message_for(entity_type)

    assert "limit=10000" in message, (
        f"the {entity_type!r} remedy has no bound: {message!r}"
    )
    assert "graph_overview(section='status')" in message, (
        f"the {entity_type!r} remedy names no source for the layer's size: {message!r}"
    )


# ── review round 4: the blank-text clause, its bounds, and the count key ────


def test_an_empty_string_kind_does_not_widen_the_blank_text_hole(stub_factory) -> None:
    """The legs test `if kind:` (truthiness), so `kind=""` adds no condition.

    Round 3's clause keyed on `kind is None`, so `text="  ", kind="",
    entity_type="event"` slipped through and answered `[]` — an empty result
    indistinguishable from a complete one. Fails on `is_refusal(result)`; the
    fixture reaches it because `""` is falsy but not `None`.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    result = tortoise_query(text="  ", kind="", entity_type="event", limit=5)

    assert _is_refusal(result), f"kind='' left the leg conditionless: {result!r}"
    assert stub.calls == [], f"the leg was read before refusing: {stub.calls}"


def test_a_blank_text_still_serves_operator_because_its_leg_has_a_condition(
        stub_factory) -> None:
    """The false block to avoid: `operator` answers a blank text on its own clause.

    `run_structural_query` appends `n.is_operator = true` for this type, so the
    leg fires with no kind and no query and really does read the layer — the
    same read the remedy names. Fails on the fts leg being reached; a blanket
    blank-text refusal would red here.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    out = tortoise_query(text="  ", entity_type="operator", limit=3)

    assert isinstance(out, list), f"a working combination was refused: {out!r}"
    assert stub.calls[-1][0] == "tortoise_fts_query"
    assert stub.calls[-1][3].get("entity_type") == "operator"


def test_a_non_string_text_does_not_raise_out_of_the_tool_body(stub_factory) -> None:
    """`.strip()` on a non-str `text` raised `AttributeError` out of the handler.

    The body contract is a structured error for a bad argument shape, not an
    exception; refusing a non-str is the SDK's job. Fails on the raise (an
    `AttributeError` escapes the call); the fixture reaches it because `5` is
    truthy, so a guard calling `text.strip()` unconditionally hits it.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory()

    out = tortoise_query(text=5, entity_type="event", limit=1)  # type: ignore[arg-type]

    assert out == [{"id": "op0", "is_operator": True}], (
        f"a non-str text must reach the leg unchanged and answer, not raise: {out!r}"
    )


@pytest.mark.parametrize("entity_type", ["document", "object", "subject"])
def test_a_remedys_named_count_is_a_key_the_status_section_serves(
        entity_type) -> None:
    """A remedy naming `counts.<X>` must name a key `taxonomy()` actually returns.

    Round 3 wrote `counts.Document`; D10 retires `:Document` (a document IS a
    `:Source`), so `taxonomy()` reports only the labels it owns — a caller
    following the remedy would find no such key. The key set is taken from
    `taxonomy()`'s own ANSWER (run against a fake projection), never re-typed
    here, so a future label change moves both together. Fails on `key in
    served`; a remedy naming any invented key reds.
    """
    import re

    from tortoise.taxonomy import taxonomy

    class _FakeProj:
        class g:  # mirrors the SDK's `.g.query(...)` surface
            @staticmethod
            def query(_cypher):
                return type("_R", (), {"result_set": [[0]]})()

    served = set(taxonomy(_FakeProj()).keys())
    message = _message_for(entity_type)
    named = re.findall(r"counts\.(\w+)", message)
    assert named, f"the {entity_type!r} remedy names no count key: {message!r}"
    for key in named:
        assert key in served, (
            f"the {entity_type!r} remedy names counts.{key}, which "
            f"tortoise_graph_overview(section='status') does not serve "
            f"({sorted(served)})"
        )


# ── review round 5: the two emptiness BOUNDARIES, one test each ─────────────


def test_an_empty_string_tag_is_still_tag_mode_and_must_refuse(stub_factory) -> None:
    """The body enters tag mode on `if tag is not None`, so `tag=""` IS a tag read.

    The guard's `tag is not None` is therefore load-bearing: rewritten as `tag`
    (truthiness) it passes every other test and `tag="", text="q",
    entity_type="object"` reaches `query_points_by_tag` and answers the tagged
    Points to a caller who asked for objects. Fails on `stub.calls == []` AND on
    the refusal — the first alone would let a read-then-refuse slip by.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    result = tortoise_query(tag="", text="q", entity_type="object", offset=0, limit=2)

    assert _is_refusal(result), (
        f"tag='' is tag mode and served the tagged Points: {result!r}"
    )
    assert stub.calls == [], f"the tag arm read before refusing: {stub.calls}"


def test_a_falsy_non_none_text_must_still_refuse(stub_factory) -> None:
    """The guard's `not text` covers a FALSY text (`0`, `[]`, `False`), not just None.

    Rewritten as `text is None` it passes every other test, and `text=0` with a
    non-point type falls through to the plain structural arm and answers the
    Points population with a `total`. Fails on `stub.calls == []` and on the
    refusal; the fixture reaches it because `0` is falsy but not `None`.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory()

    result = tortoise_query(text=0, entity_type="event", limit=2)  # type: ignore[arg-type]

    assert _is_refusal(result), f"a falsy text served the Points: {result!r}"
    assert stub.calls == [], f"the structural arm read before refusing: {stub.calls}"


# ── review round 6: two remedies still implied completeness ─────────────────


def test_the_operator_remedy_discloses_the_view_it_bounds() -> None:
    """The operator remedy names a CURRENT-VIEW read while implying the layer.

    `tortoise_search`'s structural leg for `operator` sets
    `label_str = "Point"` (`tortoise/search_engine.py:4261` region) and `Point`
    is in `_STATUS_FILTERED_LABELS` (`:148`), so the terminal-status clause is
    appended to the WHERE — a terminally-stated operator is NOT returned. And
    the reconciliation target counts a WIDER predicate than the leg reads:
    `summarize_structure` uses `is_operator = true OR op_type IS NOT NULL`
    (`tortoise/sdk.py:26563`) while the leg appends only `n.is_operator = true`
    (`search_engine.py:4261`). So "one call at the SDK's maximum … no
    single-call read above 10,000" hands the caller a short answer shaped like
    the layer, with none of its causes named — the same false PASS this guard
    removes, one level down (round 2 fixed the `limit` half of it). The NAMED
    causes are pinned here; the SILENT ones (a timed-out or breaker-open leg)
    are pinned by `test_the_operator_remedy_names_the_silent_cause_too`. Fails
    on the `CURRENT VIEW` clause and on either named cause.
    """
    message = _message_for("operator")

    assert "CURRENT VIEW" in message, (
        f"the remedy implies the operator LAYER while naming a view: {message!r}"
    )
    assert "op_type" in message and "terminally-stated" in message, (
        "a reconciled gap's NAMED causes (terminal status, and operators "
        f"carrying only `op_type`) are missing: {message!r}"
    )


def test_the_event_remedys_alternative_branch_is_bounded_where_it_truncates() -> None:
    """`tortoise_search` slices at `limit` (default 10) with a bare list.

    `tortoise/sdk.py` validates `limit: int = 10` and `1-10000`, and the tool
    returns a plain list — no `total`, no `hasMore`. So the event remedy's
    alternative branch answers TEN rows of a multi-thousand-row layer shaped
    exactly like the window, which is the shape round 2 removed for `operator`.
    The `get_entity` branch has the same trap via its own default. Fails on the
    `limit=10000` in the alternative branch and on `DEFAULTS TO 20`; a
    limit-less alternative reds it.
    """
    message = _message_for("event")

    assert "DEFAULTS TO 20" in message, (
        f"the get_entity branch's truncating default must be named: {message!r}"
    )
    assert "limit=10000" in message, (
        f"the tortoise_search branch truncates at 10 without a limit: {message!r}"
    )

# ── review round 7: the last three remedies that still overpromised ──────────


@pytest.mark.parametrize("entity_type,count_key", [
    ("object", "counts.Object"),
    ("subject", "counts.Subject"),
])
def test_the_object_and_subject_remedies_disclose_the_status_filter(
        entity_type, count_key) -> None:
    """`Point`, `Object` and `Subject` are ALL status-filtered, not just `Point`.

    Verified in `tortoise/search_engine.py`: `_STATUS_FILTERED_LABELS =
    ("Point", "Object", "Subject")` (`:148`) and the exclusion clause is
    appended for each of those labels (`:2967`), while the count these remedies
    tell the caller to reconcile against — `taxonomy()` served as
    `status().counts` — is `MATCH (n:<Label>) RETURN count(n)` over EVERY
    status. So a reconciled gap for `object`/`subject` has two causes (the
    status filter and the un-named kinds) and the remedies named one of them
    only. Round 5 fixed exactly this for `operator`. Fails on `CURRENT VIEW`
    and on the terminal-status clause; replacing either entry with an
    unconditional "answers the COMPLETE layer" reds it.
    """
    message = _message_for(entity_type)

    assert "CURRENT VIEW" in message, (
        f"the {entity_type!r} remedy implies the layer while the read is "
        f"status-filtered: {message!r}"
    )
    assert "terminal-status" in message, (
        f"the {entity_type!r} remedy must name the exclusion: {message!r}"
    )
    assert count_key in message, (
        f"the {entity_type!r} remedy must keep its size source: {message!r}"
    )
    assert "500 ms" in message and "breaker" in message, (
        "this leg can also answer [] on its own budget/breaker, and no field "
        f"reports it — the remedy must name that too: {message!r}"
    )


def test_no_remedy_names_a_tool_the_registry_has_retired() -> None:
    """A refusal may not send the caller to a name that is off `tools/list`.

    `tortoise/tool_registry.py` builds `TOOL_REGISTRY` as every entry whose name
    is NOT in `RETIRED_USE_INSTEAD` (`:2187`), and the retired ones answer only
    through a warning shim that redirects to the advertised path. The `source`
    remedy used to say `tortoise_list_sources()` — which is in that map
    (`:2109`, pointer `tortoise_graph_overview(section="sources")`) — so the one
    place a caller is told where to go was telling them to a deprecated door.
    Fails on every retired name; the vocabulary is READ from the registry's own
    map, never re-typed here, so a future retirement reds this too.
    """
    from tortoise.mcp_server import _ENTITY_TYPE_REMEDY, _ENTITY_TYPE_REMEDY_FALLBACK
    from tortoise.tool_registry import RETIRED_USE_INSTEAD

    messages = {k: v for k, v in _ENTITY_TYPE_REMEDY.items()}
    messages["<fallback>"] = _ENTITY_TYPE_REMEDY_FALLBACK
    # A retired name is only an offence where it is named AS A CALL — a bare
    # substring test would red a live name that merely CONTAINS a retired one
    # (`tortoise_get_entity(` contains `tortoise_get`).
    call = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in RETIRED_USE_INSTEAD) + r")\s*\("
    )
    offenders = {
        f"{entity_type}: {m.group(1)}"
        for entity_type, message in messages.items()
        if (m := call.search(message))
    }
    assert offenders == set(), (
        f"remedies naming retired tools (they answer only via the shim): "
        f"{sorted(offenders)}"
    )


def test_the_operator_remedy_names_the_silent_cause_too() -> None:
    """A short answer has causes that are NOT population differences.

    The structural leg runs under a 500 ms driver budget and a circuit breaker
    (`tortoise/search_engine.py`: `runner_timeout = int(elevated_timeout_ms or
    500)`, `except` -> `reason="query_failed"`, `_breaker_allow` ->
    `reason="breaker_open"`), and BOTH answer `[]` with no field reporting it.
    A message that enumerates only the two population causes tells a reconciler
    to attribute the whole gap to them, which is the same false PASS this guard
    removes, one level down. Fails on `500 ms` and `breaker`; a closed
    enumeration of two causes reds it.
    """
    message = _message_for("operator")

    assert "500 ms" in message, (
        f"the remedy omits the leg's silent timeout: {message!r}"
    )
    assert "breaker" in message, (
        f"the remedy omits the leg's silent breaker-open answer: {message!r}"
    )


def test_the_document_remedy_states_the_partition_not_the_union() -> None:
    """`counts.Source` is documents PLUS provenance Sources, not the layer's size.

    Verified: `taxonomy()` counts `MATCH (n:Source) RETURN count(n)` over every
    Source (`tortoise/taxonomy.py:17-21`), while the remedy names a read scoped
    to `documentKind` (`run_structural_query`'s document arm — `label_str =
    "Source"`, `kind_field = "documentKind"`), i.e. the `documentKind NOT NULL`
    partition. `sdk.list_sources()` is the complement (`WHERE s.documentKind IS
    NULL`) and says so in its own docstring. So naming `counts.Source` as "its
    size" hands the caller a number shaped like a DIFFERENT population — the
    defect this PR removes from rows, one level down in prose. Fails on the
    partition statement: dropping back to a bare "Its size is counts.Source"
    reds it.
    """
    message = _message_for("document")

    assert "counts.Source" in message, (
        f"the document remedy must keep its count key: {message!r}"
    )
    assert "MINUS" in message and "provenance" in message, (
        f"the union must be stated as a partition, not as the layer: {message!r}"
    )
    assert "documentKind IS NULL" in message, (
        f"the complementary partition must be named: {message!r}"
    )


def test_every_remedy_that_names_a_search_names_the_silent_cause() -> None:
    """The silent-cause disclosure is a property of the READ, not of one entry.

    Round 9 fixed the `document` partition but left that entry — and `event`'s
    alternative branch — without the clause `operator`, `object` and `subject`
    carry, although both name the same read:
    `tortoise_search(entity_type=…, kind=…)`, i.e. `run_structural_query`. That
    leg answers `[]` with no field reporting it from two causes that are not
    population differences at all: `_breaker_allow("structural")` returning
    False (`tortoise/search_engine.py:4252`), and `graph.query(...,
    timeout=timeout_ms)` raising into the `except Exception` arm (`:4308`,
    500 ms default from the signature at `:4168`). A per-entry assertion cannot
    stop the NEXT entry from repeating the omission — this PR has already
    re-derived the same class three times — so this sweeps the remedy table
    itself. `source` is exempt BY CONSTRUCTION: it names
    `tortoise_graph_overview(section='sources')`, a direct Cypher read with no
    breaker (`sdk.py:26630-26640`), so it names no search at all.
    """
    from tortoise.mcp_server import _ENTITY_TYPE_REMEDY

    searching = {
        entity_type: message
        for entity_type, message in _ENTITY_TYPE_REMEDY.items()
        if "tortoise_search(" in message
    }
    assert searching, (
        "no remedy names a tortoise_search read — this sweep would be vacuous"
    )
    missing = {
        entity_type
        for entity_type, message in searching.items()
        if "500 ms" not in message or "breaker" not in message
    }
    assert missing == set(), (
        "these remedies name a structural-leg search but not the causes that "
        "make it answer [] with nothing reporting it: "
        f"{sorted(missing)}"
    )


def test_the_event_remedy_names_the_population_to_reconcile_against() -> None:
    """`event` is the one entry whose size source is a RECONCILIATION, not a count key.

    The table's own rule is that every entry says where the layer's SIZE comes
    from. `document`/`object`/`subject` name `graph_overview(section='status')`
    and are pinned by `test_a_non_enumerable_remedy_states_its_bound_and_the_real_size_source`;
    `event` names no count key at all — it names a read that can be reconciled
    against the graph's own Event count, which is a different and equally
    load-bearing claim (`tortoise_get_entity(None, type='events')` →
    `sdk.get_events` → `MATCH (e:Event) LIMIT $lim`, default 20, a bare list,
    no status filter, so `counts.Event` is the matching population). Round 10
    measured the gap: deleting that clause left the suite fully GREEN, i.e. the
    one entry whose size source is prose could lose it silently — the same
    "bound pinned, reconcilability not" defect this PR removes. Fails on the
    reconciliation clause being absent.
    """
    message = _message_for("event")

    assert "reconcilable against the graph's own Event count" in message, (
        "the event remedy sells a truncated window with nothing to reconcile "
        f"it against: {message!r}"
    )
