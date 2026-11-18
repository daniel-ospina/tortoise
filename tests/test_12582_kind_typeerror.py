"""#12582 — a non-str `kind` raised a bare `TypeError` out of the tool body.

`tortoise_query(kind=False)` / `(kind=0)` reached the empty-read tail with a
falsy, non-`None`, non-string value. That tail attached a "did you mean…"
suggestion via `compute_suggestion(kind)`, whose ``":" not in kind`` test raises
for any non-iterable, so the caller got an uncaught exception out of the tool
body instead of the served read the tool documents (a falsy `kind` is "the
spelling of absent", which that contract says "stays served" — the answer was
never meant to be a refusal). The crash was in-process only: over MCP the
published schema for `kind` is `anyOf[string, null]` (measured with
`mcp.list_tools()`), so fastmcp refuses `false`/`0` before the body runs. The
body's contract must not depend on the transport narrowing its types.

Its mirror image is the SECOND half of the fix, over a slightly wider
population — three different wrong answers for a `kind` the accessor can never
match, all measured on a live lane:

- a truthy non-ITERABLE non-str (`True`, `1`, `5`) raised at `":" not in kind`,
  and once the tail learned to tolerate a non-str the STRUCTURAL arm served it as
  `[]` — a true-looking "this kind has no rows". (Non-iterability, not non-str-ness,
  is what raised: a tuple `kind` never raised — a tuple read matches no row, so it
  was served `[]`, or a suggestion envelope when the tuple is a near-miss
  (measured with the guard removed: `("a",)` -> "kind '('a',)' not found. Did you
  mean: 'api', …"). The
  fts arm was never part of this: it answers through `tortoise_fts_query` and
  returns above the tail, so `text=..., kind=True` was `[]` both before and after
  the tail change — which is exactly why the refusal, not the tail, is what makes
  one message for all three arms.)
- a non-empty LIST (`["statement"]`) did not raise there; it raised EARLIER, in
  the kind-expansion map before any read (`_operator_scope_clauses` ->
  `_expand_kind` -> `_kind_expansions.get(kind, [kind])`), and surfaced as
  `{"error": "unhashable type: 'list'"}` — an internal detail, converted to a dict
  by `_safe`, i.e. neither a refusal nor a route;
- the tag arm refused all of them by the #12500 rule, so the same value answered
  two different ways depending on which arm a caller happened to use.

That last point is the defect #12372 removed for `entity_type`, so any truthy
non-str is now refused above every arm with one message, and only a FALSY one
keeps meaning "absent".

It matters beyond the crash: #12500 made a FALSY `min_confidence` / `order_by` /
`kind` the *documented spelling of absent*, precisely so a client that
serialises its own defaults is never refused for asking for nothing. So
`kind=False` meaning "no kind filter" is a shape the contract now invites, and it
was the one shape that raised.

The fix is the guard the SDK already applies: `if kind:` (`sdk.py`, before it
builds the kind clause) — i.e. a falsy non-str `kind` is ABSENT and the read is
served the whole population, exactly as `kind=""` always was. The suggestion
feature is unchanged for a non-empty string, the only input it can say anything
about.

Test doctrine (Class B): each test states (1) the value whose presence makes it
fail and (2) that the fixture reaches that value.

Run with (stub-only, no database):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_12582_kind_typeerror.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


class _StubSDK:
    """Records the structural read; returns an EMPTY page unless told otherwise.

    Empty by default on purpose: the TypeError needed `len(result) == 0` before
    the suggestion path ran, so a stub that answered with rows could not observe
    the defect at all.
    """

    def __init__(self):
        self.calls = []
        #: Empty by default and filled by the case that needs a row, so the
        #: suggestion path (which only runs on an empty page) is the default.
        self.rows = []
        #: Mirrors the real accessor's own test (`sdk.py`: `if kind:` before the
        #: kind clause is built), so a stub that merely records the argument
        #: could not tell "served as absent" from "filtered to nothing".
        self.kind_clause_applied = None

    def paginated_query(self, kind=None, skip=0, limit=20, **kwargs):
        self.calls.append(("_paginated_query", kind, skip, limit, kwargs))
        self.kind_clause_applied = bool(kind)
        return {"results": self.rows[skip:skip + limit], "total": len(self.rows),
                "hasMore": skip + limit < len(self.rows)}


@pytest.fixture(autouse=True)
def _http_transport():
    """Drive the tool body directly (no HTTP middleware)."""
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


@pytest.mark.parametrize("falsy_kind", [False, 0, 0.0])
def test_a_falsy_non_str_kind_is_served_instead_of_raising(stub, falsy_kind) -> None:
    """#12582's own reproduction, on the arm that raised.

    This is the OUTCOME the contract promises (a served read, never an exception),
    and it is deliberately not the test that pins WHICH half produces it: measured
    at this head, restoring the shipped tail (`kind is not None`) leaves THIS case
    green, because the defensive guard in `compute_suggestion` then absorbs the
    non-str and returns None. Each half prevents the crash on its own. The
    tail-level invariant — that a non-str never reaches a string-only API at all —
    is pinned by `test_a_non_str_kind_never_reaches_the_suggestion_builder`, and
    the served ROWS by `test_a_falsy_non_str_kind_serves_the_whole_population`.

    The fixture reaches the value: the stub answers with an EMPTY page, which is
    what makes the suggestion path run at all.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(kind=falsy_kind)

    assert isinstance(result, list), (
        f"kind={falsy_kind!r} must be served as a plain list (falsy = absent): "
        f"{result!r}"
    )
    assert result == [], f"the stub's empty page must come through: {result!r}"
    # `is False`, not `not`: the attribute starts as None, so the `not` form
    # passes VACUOUSLY when the accessor was never called at all (measured — with
    # a `if not kind: return []` short-circuit inserted ahead of the read, the
    # `not` assertion itself still passed; the `calls` check below is the one that
    # reds, and it is what makes this test able to see a read that never
    # happened).
    assert stub.kind_clause_applied is False, (
        "the read must REACH the accessor with a falsy `kind`, which then applies "
        f"no kind clause (the SDK's own `if kind:`): {stub.calls}"
    )
    # Only THAT the accessor was asked — deliberately not WHICH spelling of
    # "absent" it was handed. Issue #12582 names the alternative fix itself
    # ("normalise a non-str `kind` to `None` at the top of the body"); that fix
    # serves the identical read (measured on a live lane: the whole population),
    # and a `== [falsy_kind]` assertion here reds it for 3 params while the
    # contract is honoured — the same "pins an implementation detail" defect this
    # file's doctrine bans.
    assert stub.calls, f"the accessor was never asked: {stub.calls}"


@pytest.mark.parametrize("truthy_kind", [True, 1, 5, 1.0, ["statement"]])
def test_a_truthy_non_str_kind_is_refused_not_served_as_an_empty_page(
        stub, truthy_kind) -> None:
    """A kind that is SET but not a string cannot match, so it is REFUSED.

    Measured at `2d19277237` (tail tolerant, refusal not yet added) on a live lane:
    `kind=True`/`1`/`5` returned `[]` while `kind=False` returned the whole
    population — the
    #6146 wrong-empty class, and the tag arm refused those same values (#12500),
    so one argument value answered two ways. The list param is here for its own
    measured pre-fix answer: `{"error": "unhashable type: 'list'"}` leaked out of
    the SDK's kind-expansion map, which was neither a refusal nor a route. Fails
    if the refusal is absent (the empty page, or the leaked error, comes back) or
    if it is widened to falsy values (the served-read tests red instead — five,
    measured). It cannot see WHERE the refusal sits relative to the arms: measured,
    moving it below both the `tag=` and `text=` arms leaves THIS test green — the
    placement claim belongs to
    `test_the_two_arms_agree_that_a_truthy_non_str_kind_is_refused`.

    Reaches the value: no read may happen at all, which is what the empty call
    log asserts — a refusal placed after the accessor would still return an error
    dict, so the log is the assertion that pins placement.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(kind=truthy_kind)

    assert isinstance(result, dict) and "error" in result, (
        f"kind={truthy_kind!r} must be refused, not served as {result!r}")
    assert "kind must be a string" in result["error"], result["error"]
    assert stub.calls == [], (
        f"the refusal must fire ABOVE the arm, before any read: {stub.calls}")


def test_the_two_arms_agree_that_a_truthy_non_str_kind_is_refused(stub) -> None:
    """The tag arm and the structural arm must give ONE answer for one value.

    Measured on a live lane at `2d19277237` (tail tolerant, refusal not yet added):
    `tortoise_query(kind=True)` returned `[]` (a wrong-empty) while
    `tortoise_query(tag="x", kind=True)` was refused by the #12500 tag block — two
    answers for the same argument value,
    the defect #12372 removed for `entity_type`. With the refusal above every arm
    both answer with the SAME message: the #12582 one. Measured at this head, a
    STRING kind still reaches the per-arm block on the tag arm (`tag="x",
    kind="statement"` → "kind is not applied on the tag= arm (#12500)"), which is
    why asserting the message — not just "an error" — is what pins the new
    refusal ABOVE the tag arm's precedence split: moved below it, the tag arm
    would answer `kind=True` with the #12500 sentence and this test would red.

    Reaches the value: neither call may reach the accessor.
    """
    from tortoise.mcp_server import tortoise_query

    structural = tortoise_query(kind=True)
    tagged = tortoise_query(tag="x", kind=True)

    for name, res in (("structural", structural), ("tag", tagged)):
        assert isinstance(res, dict) and "error" in res, (
            f"the {name} arm served {res!r} for kind=True")
        assert "kind must be a string" in res["error"], (
            f"the {name} arm answered with another rule's message: {res['error']}")
    assert stub.calls == [], (
        f"neither arm may reach the accessor with a non-str kind: {stub.calls}")


@pytest.mark.parametrize("not_a_kind", [False, 0, 0.0, "", None, True, 1])
def test_a_non_str_kind_never_reaches_the_suggestion_builder(
        stub, monkeypatch, not_a_kind) -> None:
    """The tail must not hand a non-str to an API whose every branch assumes one.

    Fails in BOTH of the ways the condition can be weakened, measured separately:

    - back to the shipped `kind is not None` -> the builder IS called for the
      falsy params here (`False`, `0`, `0.0`) plus `""`, and the truthy ones are
      already refused upstream — 4 reds, measured;
    - the truthy-non-str refusal removed -> `True`/`1` reach the builder here,
      all five refusal params are served an empty page BY THIS STUB (the real SDK
      answers the list with the leaked `unhashable type: 'list'` instead, which
      this fixture does not model — it never touches the expansion map), and the
      two-arm test sees one arm refuse while the other serves — 8 reds, measured;
    - a refusal for a FALSY kind -> the served-read cases red (5 in all), because
      they assert the read is SERVED, not refused.

    It is the tail-level half of #12582;
    `test_a_falsy_non_str_kind_is_served_instead_of_raising` covers the outcome.
    The truthy params pin the OTHER half: they are refused above the arm
    (`test_a_truthy_non_str_kind_is_refused_not_served_as_an_empty_page`), which
    is why a non-str never reaches the builder whatever the tail does: the
    refusal stopped `True`/`1` upstream, so the truthy params here are a belt on
    that half, not its pin. The tail's own `and kind` is pinned by the FALSY
    params — back to `kind is not None`, `False`/`0`/`0.0` reach the builder,
    4 reds, measured.
    """
    from tortoise import query_suggestions
    from tortoise.mcp_server import tortoise_query

    calls = []
    monkeypatch.setattr(query_suggestions, "compute_suggestion",
                        lambda k: calls.append(k) or None)

    tortoise_query(kind=not_a_kind)

    assert calls == [], (
        f"kind={not_a_kind!r} reached the suggestion builder, which is a "
        f"string-only API: {calls}"
    )


@pytest.mark.parametrize("falsy_kind", [False, 0])
def test_a_falsy_non_str_kind_serves_the_whole_population(stub, falsy_kind) -> None:
    """The served half must deliver the ROWS, not a fabricated empty page.

    The empty-page case above cannot tell "the accessor answered with its page"
    from "an empty list was manufactured for this input" — measured: inserting
    either `if not isinstance(kind, str): return []` or `if not kind: return []`
    between the read and the tail leaves every other case in this file green
    (26 passed), i.e. the very #6146 wrong-empty this diff removes for TRUTHY
    values could ship for falsy ones with no test red. Fails if such a
    short-circuit is introduced after the read: the rows must come back.

    Reaches the value: the stub is given two rows, so an empty answer is
    distinguishable from a served one.
    """
    from tortoise.mcp_server import tortoise_query

    stub.rows = [{"id": "p0", "kind": "observation"},
                 {"id": "p1", "kind": "statement"}]

    assert tortoise_query(kind=falsy_kind) == stub.rows, (
        f"kind={falsy_kind!r} must serve the accessor's page")
    assert stub.kind_clause_applied is False, stub.calls


def test_an_empty_string_kind_is_served_as_before(stub) -> None:
    """The `kind=""` shape that already worked must keep working.

    Fails if the arm starts REFUSING a falsy kind instead of serving it — the
    #12500 contract this one extends — or if the read stops reaching the accessor
    with a falsy `kind`. It does NOT pin the tail's `and kind` half, and cannot:
    under that mutant `""` reaches the builder, but `compute_suggestion("")`
    returns None (measured), so no suggestion is attached and all three
    assertions here still hold — the four reds the mutant produces are all params
    of the test above (`False`, `0`, `0.0` and its own `""` param), not this one.
    There is no `isinstance` conjunct in the tail either: the truthy-non-str
    refusal above the arms makes one unreachable, so a redundant guard here would
    be a branch no case could distinguish from its absence.
    """
    from tortoise.mcp_server import tortoise_query

    assert tortoise_query(kind="") == []
    assert stub.kind_clause_applied is False, (
        f"the read must reach the accessor and apply no kind clause: {stub.calls}"
    )
    assert stub.calls, f"the accessor was never asked: {stub.calls}"


def test_the_suggestion_is_attached_for_a_non_empty_str_with_no_rows(
        stub, monkeypatch) -> None:
    """The feature the tail exists for is UNCHANGED — this is not a deletion.

    Fails if the guard is written so broadly that no suggestion can ever be
    produced (`and False`, or a condition that also excludes a real string).
    Reaches the value: the stub returns NO rows for a non-empty kind (the only
    state in which the tail runs), and the kind vocabulary is stubbed to a FIXED
    set. The stub is for determinism, not for reachability: measured at this head
    `_collect_known_kinds()` returns 122 kinds from the PACK registry (no graph
    read), so the real vocabulary would also answer here — but then both
    assertions would depend on which packs the machine happens to load: if a pack
    ever registered `statementt`, `compute_suggestion` takes its VALID-kind branch
    and returns "kind 'statementt' is valid but has 0 points…", which carries
    neither "Did you mean" nor "'statement'" — the case would go RED for an
    environment reason that has nothing to do with this fix. The stub keeps the
    case about the did-you-mean path it is named for.
    """
    from tortoise import query_suggestions
    from tortoise.mcp_server import tortoise_query

    monkeypatch.setattr(query_suggestions, "_collect_known_kinds",
                        lambda: {"statement:assertion", "statement"})

    result = tortoise_query(kind="statementt")

    assert isinstance(result, dict), f"expected the suggestion envelope: {result!r}"
    assert result["results"] == []
    assert "Did you mean" in result["suggestion"], result["suggestion"]
    assert "'statement'" in result["suggestion"], result["suggestion"]


def test_no_suggestion_is_attached_when_rows_come_back(stub) -> None:
    """A non-empty read returns the plain list, with no suggestion key.

    Fails if the tail starts wrapping every read (the `len(result) == 0` half is
    dropped). Reaches the value: the stub is given one row.
    """
    from tortoise.mcp_server import tortoise_query

    stub.rows = [{"id": "p0", "kind": "statement"}]

    assert tortoise_query(kind="statement") == [{"id": "p0", "kind": "statement"}]
    assert stub.kind_clause_applied is True, stub.calls


@pytest.mark.parametrize("not_a_kind", [False, 0, 0.0, None, [], {}])
def test_compute_suggestion_returns_none_for_a_non_str(not_a_kind) -> None:
    """The library entry point must not raise for its own callers (#12582).

    Measured on the shipped function with the guard removed: `False`, `0`, `0.0`
    and `None` raise `TypeError: argument of type '<T>' is not iterable` at
    ``":" not in kind``, and those four are the params that red when the guard
    goes. `[]` and `{}` are SCOPE coverage, not discriminating power: measured,
    they already return `None` without the guard, so they are here to pin that
    the contract is "any non-str" rather than "any non-iterable".
    """
    from tortoise.query_suggestions import compute_suggestion

    assert compute_suggestion(not_a_kind) is None


def test_compute_suggestion_still_answers_for_a_string(monkeypatch) -> None:
    """…and the guard does not make the function useless for real input.

    Fails if `compute_suggestion` is made to return None unconditionally, which
    would satisfy the test above while deleting the feature (measured: inserting
    an early `return None` reds 2 cases). The vocabulary is stubbed for the
    determinism reason given there.
    """
    from tortoise import query_suggestions

    monkeypatch.setattr(query_suggestions, "_collect_known_kinds",
                        lambda: {"statement:assertion", "statement"})

    suggestion = query_suggestions.compute_suggestion("statementt")

    assert suggestion is not None
    assert "Did you mean" in suggestion and "'statement'" in suggestion, suggestion
