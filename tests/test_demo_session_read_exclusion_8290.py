"""#8290 — the demo/sample ``:Session`` is excluded from every session read.

**The defect this pins.** ``tortoise/session_schema.py`` declared the demo
Session id as the "one source of truth", and #2360's OWNER-RECORDED exclusion
removed the demo POINTS from the count-of-record digest (``org.point_count``) —
but no session READ surface excluded the demo ``:Session`` container. A
demo-seeded org therefore showed a capture the user never made in "Captured
sessions", while the same sample's Points were excluded one screen away: two
surfaces disagreeing about whether the sample content was the user's own.

**What is asserted here (and what is not).** This module holds the FAST, DB-free
half: the prefix test's soundness (a vacuous prefix test would hide *every*
leak, not one), the ONE-predicate contract, and the source pins that keep the
three read surfaces bound to that predicate instead of re-spelling it. The
BEHAVIOURAL half — demo absent, a real session still served, on the list /
detail / SDK doors — lives in
``tests/test_onboarding_seed_endpoint.py::TestDigestExcludesDemo`` because it
needs a real tenant graph (docker lane). Both halves are deliberate: a
source-pin alone cannot see a route that quietly stops calling the predicate,
and a behavioural test alone cannot see WHY the exclusion is safe.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tortoise import session_schema
from tortoise.ids import mint_session_id

_REPO = Path(__file__).resolve().parent.parent


# ── the predicate ─────────────────────────────────────────────────────────


def test_the_prefix_is_declared_once_and_is_the_demo_derivation():
    """One derivation: ``demo_session_id`` composes the declared prefix."""
    assert session_schema.DEMO_SESSION_ID_PREFIX == "session_demo_"
    assert session_schema.demo_session_id("abcdef123456") == "session_demo_abcdef12"
    assert session_schema.demo_session_id("abcdef123456").startswith(
        session_schema.DEMO_SESSION_ID_PREFIX)


@pytest.mark.parametrize("sid,expected", [
    ("session_demo_abcdef12", True),
    ("session_demo_", True),
    # a REAL captured id — the shape every capture door mints
    ("session_0123456789ab", False),
    # an AgentSession Event lane id (`get_session`'s other arm)
    ("sess-legacy", False),
    ("", False),
    (None, False),
])
def test_is_demo_session_id_truth_table(sid, expected):
    assert session_schema.is_demo_session_id(sid) is expected


def test_no_mintable_session_id_can_carry_the_demo_prefix():
    """NON-VACUITY for the prefix test (#8290 review class).

    The exclusion is a PREFIX test, so its soundness depends on no real capture
    id ever carrying ``session_demo_``. That is true by construction — every
    capture door mints ``mint_session_id()`` = ``session_<12 hex>`` and `demo_`
    is neither hex nor 12 characters — but "by construction" is exactly the
    claim a future id-minting change would break silently, and a prefix test
    that matched everything would hide every leak instead of one. A 1000-mint
    population is cheap and makes the claim falsifiable.

    The assertions are deliberately TWO: the population must be large enough for
    the test to mean anything (a generator that yielded nothing would pass the
    loop vacuously), and no member may match.
    """
    population = [mint_session_id() for _ in range(1000)]
    assert len(population) == 1000, "the mint population collapsed — vacuous run"
    assert len(set(population)) == 1000, "mint_session_id is no longer unique"
    offenders = [s for s in population
                 if session_schema.is_demo_session_id(s)]
    assert not offenders, (
        "a real minted session id matched the demo prefix — the exclusion is "
        f"now excluding real captures: {offenders[:3]!r}")
    # ...and the length/shape claim those ids rely on is pinned too
    assert all(re.fullmatch(r"session_[0-9a-f]{12}", s) for s in population), (
        "mint_session_id's shape changed; re-derive the prefix-soundness "
        "argument in session_schema.py before trusting the exclusion")


# ── the three surfaces are BOUND to the one predicate ─────────────────────


def _src(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


def test_hosted_list_excludes_by_the_derived_prefix():
    """``list_sessions`` filters on the DERIVED prefix, never a literal id.

    A literal ``'session_demo_'`` in the query would be a second spelling of the
    declaration — the drift class this module's header exists to prevent — and a
    literal ORG-BOUND id would exclude only the orgs whose id happened to match.
    """
    src = _src("tortoise/hosted_api.py")
    start = src.index("async def list_sessions(")
    body = src[start:src.index("async def get_session_detail(", start)]
    assert "NOT s.id STARTS WITH $demo_prefix" in body, (
        "list_sessions lost its demo-Session exclusion")
    assert '"demo_prefix": DEMO_SESSION_ID_PREFIX' in body, (
        "list_sessions must bind the exclusion to the DERIVED prefix constant")
    # the query builds a WHERE from the condition list; a `MATCH ... ` with no
    # WHERE would drop the exclusion on the actor_filter=None path
    assert '"MATCH (s:Session) WHERE "' in body, (
        "the MATCH-level WHERE must be unconditional — an actor-filter-"
        "conditional WHERE drops the demo exclusion for the common case")


def test_detail_door_uses_the_shared_predicate():
    src = _src("tortoise/hosted_api.py")
    start = src.index("async def get_session_detail(")
    body = src[start:start + 4000]
    assert "if is_demo_session_id(session_id):" in body, (
        "get_session_detail must refuse a demo Session id")
    assert 'detail="Session not found"' in body
    assert "session_demo_" not in body, (
        "the detail door must not re-spell the prefix")


def test_sdk_read_uses_the_shared_predicate():
    src = _src("tortoise/sdk.py")
    start = src.index("def _read_captured_session(")
    body = src[start:start + 5000]
    assert "from tortoise.session_schema import is_demo_session_id" in body, (
        "the SDK mirror must import the ONE predicate")
    assert "if is_demo_session_id(session_id):" in body
    assert "session_demo_" not in body, (
        "the SDK mirror must not re-spell the prefix")


def test_the_session_schema_no_longer_records_the_gap_as_open():
    """The module's own prose is the record the next lane reads.

    It used to say the read-side exclusion was NOT implemented and that #8290
    tracked the decision. Leaving that sentence in place after implementing the
    exclusion is the inverse defect — a reader would re-derive a closed gap.
    """
    src = _src("tortoise/session_schema.py")
    assert "The read-side exclusion is NOT implemented" not in src
    assert "is not excluded from any session read surface" not in src.lower()
    assert "is_demo_session_id" in src
