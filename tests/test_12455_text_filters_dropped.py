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

``include_retracted`` is the one argument that is NOT a property filter: the
handler pops it out of ``filters`` into its own parameter *before* this guard, so
it must not be refused. It is honoured on every arm — on ``text=`` it is
forwarded as the fts leg's ``include_terminal`` (an existing SDK parameter), which
is what round 1 of review fixed: the arm used to accept it and never apply it.
The tests below pin both polarities, so neither the refusal nor the behaviour can
drift silently.

Test doctrine (Class B): every test states (1) the value whose presence makes it
fail and (2) that the fixture reaches that value.

Run with (stub-only, no database):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_12455_text_filters_dropped.py -q -p no:cacheprovider
"""
from __future__ import annotations

import inspect
import os
import re
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
    # MALFORMED JSON — the one payload that is still a `str` when the guard sees
    # it. `_parse` runs `json.loads` on any string, so a WELL-FORMED JSON object
    # arrives here as a dict and would pin nothing the first two cases do not
    # (#12455 round 1, finding 4). A truthiness guard still catches this shape;
    # an `isinstance(filters, dict)` guard would NOT, which is exactly why the
    # guard is written on truthiness.
    '{"domain": "legal"',
])
def test_every_shape_of_a_non_empty_filters_refuses(stub, filters) -> None:
    """RED before the fix for all three shapes.

    The third case is the only payload that reaches the guard as a ``str``: a
    well-formed JSON object is parsed into a dict by ``_parse`` first. It exists
    so the guard cannot be narrowed to ``isinstance(filters, dict)`` without a
    test going red.
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

def test_include_retracted_is_promoted_and_APPLIED_on_the_text_arm(stub) -> None:
    """The one exception, asserted rather than left implicit — and now HONOURED.

    ``include_retracted`` is popped out of ``filters`` into the parameter before
    this guard runs, so it is not a property filter and must not be refused.
    Round 1 of review found the other half: the ``text=`` arm then never applied
    it either, so a caller who asked for tombstoned rows got them excluded with
    no signal (the same accepted-and-dropped defect one level down). It is now
    forwarded as the fts leg's ``include_terminal`` — an EXISTING SDK parameter
    (sdk.py:40668), so no surface change — which is also what the tool's own
    docstring always promised ("surfaces tombstones on every path").

    Pins BOTH halves: a guard that starts refusing this shape over-blocks, and a
    text arm that stops forwarding it silently re-opens the drop.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(text="anything", filters={"include_retracted": True})

    assert not _is_refusal(result), (
        "include_retracted is promoted to the parameter, not a property filter — "
        "refusing it here is a false block"
    )
    fts = [c for c in stub.calls if c[0] == "tortoise_fts_query"]
    assert fts, f"the text arm did not read: {stub.calls}"
    assert fts[0][3].get("include_terminal") is True, (
        "the text arm did not forward include_retracted as the fts leg's "
        f"include_terminal — tombstoned rows are silently excluded: {fts[0][3]}"
    )


def test_include_retracted_stays_false_by_default(stub) -> None:
    """The other polarity: the default read must not start surfacing tombstones.

    Fails if the forwarding is written as a constant ``True`` (an easy slip that
    every other test here would miss, because they all pass the flag
    explicitly).
    """
    from tortoise.mcp_server import tortoise_query

    tortoise_query(text="anything")

    fts = [c for c in stub.calls if c[0] == "tortoise_fts_query"]
    assert fts, f"the text arm did not read: {stub.calls}"
    assert fts[0][3].get("include_terminal") is False, (
        f"a default text= read started surfacing terminal rows: {fts[0][3]}"
    )


# ── the tag arm cannot apply filters either (#12455 round 1, P1) ─────────────

@pytest.mark.parametrize("kwargs", [
    {"tag": "pricing", "filters": {"domain": "legal"}},
    {"tag": "pricing", "text": "q", "filters": {"domain": "legal"}},
    # The guard's `tag is not None` and the tag BRANCH's `tag is not None` are
    # the same spelling, and `""` is not `None` — pin it, because a branch moved
    # to `if tag:` would leave `tag="", filters=…` reaching the tag reader with
    # the dict dropped, silently (#12455 round-2 finding 7).
    {"tag": "", "filters": {"domain": "legal"}},
    # Same class for the text side: whitespace is TRUTHY, so it routes to the
    # fts leg — a guard that used `.strip()` would let the dict through.
    {"text": "  ", "filters": {"domain": "legal"}},
])
def test_the_tag_arm_refuses_a_filters_dict_it_cannot_apply(stub, kwargs) -> None:
    """RED before round-1's fix: the unfiltered TAG population came back.

    ``query_points_by_tag(tag)`` has no filter parameter, and the guard used to
    sit BELOW the tag branch, so `tag=…, filters=…` (and `tag=…, text=…,
    filters=…`, where tag takes precedence) answered every tagged row with no
    error — the same false PASS, one arm over. Fails if the guard stops covering
    the tag arm, or if it is moved back below it.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(**kwargs)

    assert _is_refusal(result), (
        f"{sorted(kwargs)} was accepted and the filter was DROPPED — the "
        f"unfiltered tag population was served: {len(result) if isinstance(result, list) else result}"
    )
    assert [c for c in stub.calls if c[0] == "query_points_by_tag"] == [], (
        f"the tag read happened before refusing: {stub.calls}"
    )


def test_the_tag_arm_names_itself_in_the_refusal(stub) -> None:
    """A message that says `text=` to a caller who passed `tag=` is a dead end.

    Fails if the arm name is hard-coded rather than derived — the caller cannot
    tell which argument to drop.
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(tag="pricing", text="q", filters={"domain": "legal"})

    assert _is_refusal(result), "no refusal to read a message off"
    assert "on the tag= arm" in result["error"], result["error"]
    # …and the remedy must name BOTH arguments this caller set: dropping only
    # `tag=` leaves `text=` truthy and the guard fires again with the structural
    # path still unreachable (#12455 round-2 finding 4).
    assert "Drop tag= and text=" in result["error"], result["error"]


@pytest.mark.parametrize("filters", [
    {},                               # clients that serialise an empty object
    {"include_retracted": True},      # popped into the parameter, not a filter
])
def test_a_tag_read_with_a_non_filter_still_serves(stub, filters) -> None:
    """The tag arm's over-block edges: an EMPTY dict, and the promoted flag.

    Fails if the guard is written ``if tag is not None and filters is not None``
    — which refuses the many clients that serialise ``{}`` — or if the
    `include_retracted` pop is ever moved below the guard, which would refuse a
    name that is not a property filter. Neither shape was pinned before
    (#12455 round-2 finding 7).
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(tag="pricing", filters=filters)

    assert not _is_refusal(result), f"refused a legitimate tag read: {result}"
    assert [c[0] for c in stub.calls] == ["query_points_by_tag"], stub.calls


def test_a_tag_read_without_filters_still_serves(stub) -> None:
    """The tag arm's hot path must be untouched by the widened guard.

    Fails if the guard keys on ``tag is not None`` too eagerly (e.g. treating an
    empty filters dict as a request).
    """
    from tortoise.mcp_server import tortoise_query

    result = tortoise_query(tag="pricing")

    assert not _is_refusal(result), f"refused a legitimate tag read: {result}"
    assert [c[0] for c in stub.calls] == ["query_points_by_tag"], stub.calls


#: A refusal ATTACHED TO `filters=` — not merely the word "refus" somewhere in
#: the bullet. The tag bullet legitimately contains "`entity_type` is REFUSED
#: there", so a bare substring test passes on the stale wording this test exists
#: to catch (#12455 round-3 finding 1, measured: restoring `origin/main`'s
#: "ignores kind/filters/…" left the suite green under the bare test).
_FILTERS_REFUSAL = re.compile(
    r"refus\w*\s+`?filters=?`?|`?filters=?`?\s+is\s+refused", re.IGNORECASE)


def _docstring_bullets(doc: str, head_prefix: str) -> list[str]:
    """Each bullet that starts with ``head_prefix``, joined with its
    continuation lines up to the next ``- `` bullet.

    Joining matters: the served docstring wraps, and the stale claim sat on a
    CONTINUATION line while a head-line-only read sees neither "filters" nor
    "REFUS" — which is how a vacuous test looked discriminating.
    """
    lines = doc.splitlines()
    heads = [i for i, line in enumerate(lines) if line.strip().startswith(head_prefix)]
    out = []
    for start in heads:
        end = next((j for j in range(start + 1, len(lines))
                    if lines[j].strip().startswith("- ")), len(lines))
        out.append(" ".join(lines[start:end]))
    return out


def test_no_docstring_bullet_still_says_the_tag_arm_IGNORES_filters() -> None:
    """The tag twin of the test below (#12455 round-2 finding 1).

    The old tag bullet said tag mode "ignores kind/filters/min_confidence/
    order_by" while a NEW bullet 17 lines later said `filters=` is REFUSED there:
    two mutually exclusive statements about one argument in the same served
    docstring, and the stale one is exactly the belief the guard exists to
    correct.

    Round 3 measured this test passing against that stale wording put back
    verbatim, for two independent reasons — it read only head lines (the claim
    was on a continuation line), and a bare ``"REFUS" in bullet`` was satisfied
    by the bullet's own "`entity_type` is REFUSED there". So it now joins
    continuations AND requires the refusal to be attached to `filters=`.
    """
    from tortoise.mcp_server import tortoise_query

    bullets = _docstring_bullets(inspect.getdoc(tortoise_query) or "", "- tag=<name>")
    assert len(bullets) >= 2, (
        "expected the tag= bullet AND the tag-mode bullet; found "
        f"{len(bullets)}: {bullets}"
    )
    stale = [b for b in bullets
             if "filters" in b and not _FILTERS_REFUSAL.search(b)]
    assert stale == [], (
        "a `tag=` bullet mentions `filters` without refusing it, contradicting "
        f"the guard: {stale}"
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
