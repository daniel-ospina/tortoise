"""#8290 — the demo/sample ``:Session`` is excluded from every session read.

**The defect this pins.** ``tortoise/session_schema.py`` declared the demo
Session id as the "one source of truth", and #2360's OWNER-RECORDED exclusion
removed the demo POINTS from the count-of-record digest (``org.point_count``) —
but no session READ surface excluded the demo ``:Session`` container. A
demo-seeded org therefore showed a capture the user never made in "Captured
sessions", while the same sample's Points were excluded one screen away: two
surfaces disagreeing about whether the sample content was the user's own.

**What is asserted here (and what is not).** This module holds the FAST, DB-free
half: the prefix test's soundness — BOTH halves of it, the minted population AND
the door reservation (#8290 review: the mint half alone is a lemma about
``mint_session_id``, not about the capture doors, which accept a
CALLER-SUPPLIED id) — the ONE-predicate contract, and the source pins that keep
the three read surfaces bound to that predicate instead of re-spelling it. The
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

from tortoise import ids as ids_mod
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


# ── the RESERVED namespace: the door half of the soundness argument ──────


def test_the_demo_namespace_is_reserved_at_the_one_door_predicate():
    """#8290 review: the exclusion is a PREFIX test, so the prefix is RESERVED.

    The pre-fix soundness argument was about ``mint_session_id()`` only — and
    the capture doors are NOT mint-only: ``SessionRequest.session_id`` and the
    two SDK doors accept a CALLER-SUPPLIED id of any shape. A genuine capture
    filed as ``session_demo_…`` would then be written, paid for, and invisible
    on all three read surfaces. The namespace is now closed at the ONE predicate
    every door shares, so the read exclusion cannot swallow a real capture.
    """
    for reserved in (session_schema.DEMO_SESSION_ID_PREFIX,
                     session_schema.demo_session_id("abcdef123456"),
                     "session_demo_whatever-a-harness-sent"):
        reason = ids_mod.session_id_refusal(reserved)
        assert reason, f"{reserved!r} must be refused at the doors"
        assert "RESERVED demo/sample namespace" in reason, reason
        assert session_schema.DEMO_SESSION_ID_PREFIX in reason, reason

    # ...and the ordinary shapes the doors must keep admitting
    for admitted in ("session_0123456789ab", "sess-legacy", "idx:7",
                     "  s1  ", "a b"):
        assert ids_mod.session_id_refusal(admitted) is None, admitted


def test_the_exclusion_swallows_exactly_what_the_doors_refuse():
    """THE LEMMA: exclusion set == reserved set, so no capture can hide in it.

    A prefix exclusion is only sound if the ids it removes are ids NO capture
    door would admit. This asserts the agreement in BOTH directions over a
    corpus of every id class the tree produces (minted, harness-supplied,
    padded, legacy, blank, non-str), so widening either side alone reds here:
    a door that admits a demo-prefixed id would make a user's capture vanish,
    and an exclusion narrowed below the reserved set would leak the sample.
    """
    corpus: list[object] = [
        *[mint_session_id() for _ in range(200)],
        session_schema.demo_session_id("abcdef123456"),
        session_schema.DEMO_SESSION_ID_PREFIX,
        "session_demo_x",
        "sess-legacy", "idx:7", "  s1  ", "s1", "   ", "", None, 0, b"  ",
    ]
    for value in corpus:
        swallowed = session_schema.is_demo_session_id(value)
        refused = ids_mod.session_id_refusal(value)
        if swallowed:
            # what the read surfaces hide MUST be refused by every door
            assert refused, (
                f"{value!r} is hidden by the read exclusion but the capture "
                "doors admit it — a real capture filed under it would be "
                "written and then invisible on all three surfaces")
        else:
            # ...and the RESERVED class must never fire OUTSIDE the hidden set:
            # a refusal widened past the exclusion would 422 ids the read
            # surfaces still serve, so a caller could not re-capture under an id
            # it can read back. (Blank/non-str are refused for their OWN,
            # different classes — this asserts only the reserved one.)
            assert refused is None or "RESERVED demo/sample namespace" not in refused, (
                f"{value!r} is READABLE but the doors refuse it as a reserved "
                "demo id — a capture under it could never be re-filed")


def test_no_mintable_session_id_can_carry_the_demo_prefix():
    """NON-VACUITY for the prefix test (#8290 review class).

    The exclusion is a PREFIX test, so its soundness depends on no real capture
    id ever carrying ``session_demo_``. That is true by construction — every
    capture door mints ``mint_session_id()`` = ``session_<12 hex>`` and `demo_`
    is neither hex nor 12 characters — but "by construction" is exactly the
    claim a future id-minting change would break silently, and a prefix test
    that matched everything would hide every leak instead of one. A 1000-mint
    population is cheap and makes the claim falsifiable.

    The assertions are deliberately TWO: the ids must be UNIQUE (a constant
    mint would still carry the prefix question but would collapse the
    population's discriminating power), and no member may match. The
    population length itself is not asserted — it is built from a literal
    ``range(1000)`` two lines up, so a length test could never fail and would
    pin nothing (#8290 review P2).
    """
    population = [mint_session_id() for _ in range(1000)]
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
    # Cypher three-valued logic: `NULL STARTS WITH $p` is NULL and a WHERE drops
    # a NULL predicate, so the bare `NOT ...` form would ALSO remove a Session
    # that carries no id — a session this list served before #8290. The
    # exclusion must be exactly the demo set, so the NULL arm is explicit.
    assert "(s.id IS NULL OR NOT s.id STARTS WITH $demo_prefix)" in body, (
        "the null-id arm is missing: a NULL-id Session would now be silently "
        "dropped, which is not the demo set")
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
    """The guard is on ``get_session``'s ENTRY — it covers BOTH lanes.

    #8290 review: the first lane serves a raw ``AgentSession`` ``Event`` (by
    ``session_id``/``sessionId``) BEFORE the captured arm runs, so a guard
    inside ``_read_captured_session`` alone left the SDK answering for a
    demo-prefixed id that the hosted detail door 404s. The pin is therefore
    positional: the guard must appear before the Event query.
    """
    src = _src("tortoise/sdk.py")
    start = src.index("def get_session(")
    body = src[start:src.index("def _read_captured_session(", start)]
    assert "from tortoise.session_schema import is_demo_session_id" in body, (
        "the SDK read must import the ONE predicate")
    assert "if is_demo_session_id(session_id):" in body
    guard_at = body.index("if is_demo_session_id(session_id):")
    # anchor on the QUERY, not the word: the docstring above names the arm too
    event_at = body.index("MATCH (e:Event {eventKind: 'AgentSession'})")
    assert guard_at < event_at, (
        "the demo guard must precede the AgentSession Event query — otherwise "
        "the SDK serves the demo id through that arm while the hosted detail "
        "door 404s it (the surfaces-disagree defect one arm over)")
    assert "session_demo_" not in body, (
        "the SDK read must not re-spell the prefix")


def test_the_commit_door_refuses_a_reserved_id_too():
    """The FOURTH caller-id door (#8290 review P1).

    `POST /v1/sessions/commit`'s Layer-1 model judged `session_id` with its own
    ad-hoc blank test instead of the shared predicate, so a reserved-prefix id
    passed validation and was then written VERBATIM as a `:Session` (and an
    `AgentSession` Event) — durable and hidden from every read surface. The
    soundness claim in `session_schema.py` is about every door, so this pins
    the one that did not participate.

    REDS IF: the validator reverts to the blank-only test (a reserved id would
    then be returned unchanged instead of raising), or the validator stops
    being bound to the `session_id` field.
    """
    from tortoise.commit_schema import CommitPayload

    v = CommitPayload._non_blank_session_id
    reserved = session_schema.demo_session_id("abcdef123456")
    with pytest.raises(ValueError) as exc:
        v(reserved)
    assert "RESERVED demo/sample namespace" in str(exc.value), (
        "the commit door must name the reserved class, not a generic refusal")
    # ...and the classes it ALREADY owned are unchanged (non-vacuity: a
    # validator that raised on everything would pass the arm above)
    with pytest.raises(ValueError):
        v("   ")
    assert v("session_0123456789ab") == "session_0123456789ab"


def test_the_commit_door_rule_is_BOUND_to_the_session_id_field():
    """The committed model — not just the helper — refuses a reserved id.

    The arm above calls the validator directly, which cannot tell a rule that is
    DECORATED onto ``session_id`` from one that has been unbound (a renamed
    field, a dropped decorator). This drives the real model, so the refusal is
    observed at the door a caller actually reaches. The payload is deliberately
    PARTIAL: pydantic reports every field error at once, so the claim is that
    ``session_id`` carries the reserved refusal among them — not that the
    payload is otherwise valid.
    """
    from pydantic import ValidationError

    from tortoise.commit_schema import CommitPayload

    reserved = session_schema.demo_session_id("abcdef123456")
    with pytest.raises(ValidationError) as exc:
        CommitPayload.model_validate({"session_id": reserved})
    hits = [e for e in exc.value.errors()
            if e.get("loc") == ("session_id",)
            and "RESERVED demo/sample namespace" in e.get("msg", "")]
    assert hits, (
        "the reserved-id refusal is no longer BOUND to CommitPayload."
        f"session_id — errors seen: {exc.value.errors()[:3]!r}")


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


def test_the_demo_seeders_repair_record_no_longer_calls_the_session_readable():
    """The inverse drift, one file over — and the record a repair lane reads.

    ``_seed_demo_graph``'s §9.1 residual note is what a lane reads before the
    graph-side removal (#11325). It said the demo ``:Session`` "is still returned
    by the session reads" and that a node-only repair still *publishes* the
    ``declared=3, served=0`` split (a 503 on an armed deployment,
    ``{"turns": 3, "turn_points": []}`` on an unarmed one). #8290 closed all
    three read doors, so both statements are false at this head and a repair
    plan derived from them would target a read surface that no longer exists.

    Mutation: restore either sentence in ``tortoise/hosted_api.py`` — this REDs,
    and the assertions are text the PRE-fix file carries (measured: 1 occurrence
    each at ``f0667453``), so the pin is not vacuous.
    """
    src = _src("tortoise/hosted_api.py")
    assert "so on an armed, journaling deployment the" not in src, (
        "the seeder's note still publishes a read-door observation #8290 closed")
    assert "the demo `:Session` is still returned by" not in src, (
        "the seeder's blast radius still calls the demo :Session readable")
    # ...and the substance the note must still carry, so an empty section cannot
    # satisfy the two absences above.
    assert "The graph-side repair is BOTH halves in ONE step" in src
    assert "the demo id is absent from" in src
    assert "TortoiseSDK.get_session" in src


# ── the DOORS, not just the predicate (a predicate alone pins nothing) ────


def test_a_real_capture_cannot_be_filed_under_the_reserved_namespace(tmp_path):
    """NON-VACUITY for the whole exclusion, at the DOOR the user actually uses.

    Pinning ``session_id_refusal`` alone would leave this green if a door
    stopped calling it. This drives the REAL SDK doors (embedded lane, no
    FalkorDB) with a reserved id and asserts the refusal is audible AND that
    nothing was written — the capture that would otherwise have become
    invisible on every read surface.
    """
    from tortoise.sdk import TortoiseSDK

    reserved = session_schema.demo_session_id("abcdef123456")
    turns = [{"role": "user", "content": "reserved namespace probe"},
             {"role": "assistant", "content": "acknowledged"}]
    client = TortoiseSDK(str(tmp_path / "tortoise.db"),
                         event_log_path=str(tmp_path / "events" / "sdk.jsonl"))
    try:
        for door, res in (
            ("capture", client.capture_session(turns, session_id=reserved)),
            ("commit", client.commit_session(turns, session_id=reserved)),
        ):
            assert res["ok"] is False, (door, res)
            errors = [str(e) for e in (res.get("errors") or [])]
            assert any("RESERVED demo/sample namespace" in e for e in errors), (
                door, errors)
        # ⛔ LABEL SCAN, not an equality seek (#9599's lesson): what matters is
        # that NO :Session was written under the reserved id.
        written = client._get_proj().g.query(
            "MATCH (s:Session) RETURN s.id").result_set
        assert written == [], written
        # ...and the read mirror agrees the namespace is not readable either
        assert client.get_session(reserved) is None
    finally:
        client.close()
