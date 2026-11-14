"""#12582 — a non-str `kind` raised a bare `TypeError` out of the tool body.

`tortoise_query(kind=False)` / `(kind=0)` reached the empty-read tail with a
falsy, non-`None`, non-string value. That tail attached a "did you mean…"
suggestion via `compute_suggestion(kind)`, whose ``":" not in kind`` test raises
for any non-iterable, so the caller got a transport-level failure instead of the
structured error dict the tool documents.

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
        self.calls.append(("paginated_query", kind, skip, limit, kwargs))
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
    at this head, restoring the shipped tail (`kind is not None`) leaves this test
    green, because the defensive guard in `compute_suggestion` then absorbs the
    non-str and returns None. Each half prevents the crash on its own. The
    tail-level invariant — that a non-str never reaches a string-only API at all —
    is pinned by `test_a_non_str_kind_never_reaches_the_suggestion_builder`.

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
    assert [c[1] for c in stub.calls] == [falsy_kind], (
        f"the accessor was not asked for kind={falsy_kind!r}: {stub.calls}"
    )


@pytest.mark.parametrize("not_a_kind", [False, 0, 0.0, "", None, True, 1])
def test_a_non_str_kind_never_reaches_the_suggestion_builder(
        stub, monkeypatch, not_a_kind) -> None:
    """The tail must not hand a non-str to an API whose every branch assumes one.

    Fails in BOTH of the ways the condition can be weakened, measured separately:

    - back to the shipped `kind is not None` -> the builder IS called for every
      param except `None` (SIX of the seven here), so this test reds;
    - reduced to `... and kind` without the `isinstance(kind, str)` half -> a
      TRUTHY non-str (`True`, `1`) is passed through and reaches the builder, so
      this test reds for those two alone. That half is why the truthy params are
      here: with falsy values only, `and kind` and `and isinstance(kind, str) and
      kind` are indistinguishable, and the `isinstance` half would be pinned by
      nothing.

    It is the tail-level half of #12582;
    `test_a_falsy_non_str_kind_is_served_instead_of_raising` covers the outcome,
    which either half can produce.
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


def test_an_empty_string_kind_is_served_as_before(stub) -> None:
    """The `kind=""` shape that already worked must keep working.

    Fails if the arm starts REFUSING a falsy kind instead of serving it — the
    #12500 contract this one extends — or if the read stops reaching the accessor
    with a falsy `kind`. It does NOT pin the `isinstance` conjunct: `""` is falsy
    and a string, so `and kind` alone behaves identically FOR THIS CASE — measured,
    dropping the `isinstance` half leaves this test green (it reds two params of
    `test_a_non_str_kind_never_reaches_the_suggestion_builder` instead, `True` and
    `1`, which are the only cases that pin that half).
    """
    from tortoise.mcp_server import tortoise_query

    assert tortoise_query(kind="") == []
    assert stub.kind_clause_applied is False, (
        f"the read must reach the accessor and apply no kind clause: {stub.calls}"
    )
    assert [c[1] for c in stub.calls] == [""], (
        f"the accessor was not asked for kind='': {stub.calls}"
    )


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
