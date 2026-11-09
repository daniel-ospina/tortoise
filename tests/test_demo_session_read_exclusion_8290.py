"""#8290 — the demo/sample ``:Session`` is excluded from every session read.

**The defect this pins.** ``tortoise/session_schema.py`` declared the demo
Session id as the "one source of truth", and #2360's OWNER-RECORDED exclusion
removed the demo POINTS from the count-of-record digest (``org.point_count``) —
but no session READ surface excluded the demo ``:Session`` container. A
demo-seeded org therefore showed a capture the user never made in "Captured
sessions", while the same sample's Points were excluded one screen away: two
surfaces disagreeing about whether the sample content was the user's own.

**What is asserted here (and what is not).** This module holds the FAST half:
the prefix test's soundness — BOTH halves of it, the minted population AND
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

⚠️ **NOT a fake, and not "no graph" either — read this before re-laning.**
Three tests here build a throwaway EMBEDDED engine and run real Cypher against
it: ``test_a_real_capture_cannot_be_filed_under_the_reserved_namespace`` (the
SDK doors), ``test_the_list_where_survives_a_legacy_non_string_session_id``
(the composed WHERE) and
``test_the_lifetime_baseline_excludes_the_sample_at_the_query``
(``LIFETIME_MEMORY_QUERY``). That engine is
``TortoiseSDK(<tmp_path>/tortoise.db)``: no docker, no server, no tenant graph
— which is why the file is registered on the FAST ``api``/``core``/``sdk``
surfaces and not the docker lane. ⛔ Do NOT resolve this by legislating a
meaning for "DB-free": ``config/ci-surfaces.yml`` uses the phrase BOTH ways —
its ``test_10047_record_every_lane.py`` note means *no graph* ("a fake control
plane and a recording fake SDK, no graph writes, no server"), while other
rows use it for a throwaway EMBEDDED engine — its
``test_10017_turn_carrier_replay.py`` note reads "DB-free and network-free — a
throwaway embedded ``TortoiseSDK`` per test". (Named rows, not line numbers: a
position cite here rots the moment anyone edits a line above it — one did, by
4, in this very branch.) Say what this file actually does instead; a header
claiming it touches no engine would invite exactly the re-laning that drops
the P1 regression pin (`STARTS WITH` on a non-string id).
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

    The conditions live in ``_session_list_conds`` (defined at the END of
    ``hosted_api.py`` so adding it shifts no line number cited elsewhere), so this
    pins BOTH halves: the endpoint CALLS that helper, and the helper's predicate
    is the derived, null-safe, string-coerced one.
    """
    src = _src("tortoise/hosted_api.py")
    start = src.index("async def list_sessions(")
    body = src[start:src.index("async def get_session_detail(", start)]
    assert "_session_conds, _query_params = _session_list_conds(actor_filter)" in body, (
        "list_sessions must compose its WHERE through the one helper — a local "
        "rebuild is the drift this pins against")
    helper = src[src.index("def _session_list_conds("):]
    assert "NOT toString(s.id) STARTS WITH $demo_prefix" in helper, (
        "the exclusion lost its DERIVED prefix binding, or the operand went "
        "back to the raw `s.id` — `STARTS WITH` raises a type error on a "
        "legacy non-string id and the handler then serves an EMPTY list for an "
        "org that has sessions (#8290 review, P1; measured)")
    assert '"demo_prefix": DEMO_SESSION_ID_PREFIX' in helper, (
        "the exclusion must be bound to the DERIVED prefix constant")
    # Cypher three-valued logic: `NULL STARTS WITH $p` is NULL and a WHERE drops
    # a NULL predicate, so the bare `NOT ...` form would ALSO remove a Session
    # that carries no id — a session this list served before #8290. The
    # exclusion must be exactly the demo set, so the NULL arm is explicit.
    assert "(s.id IS NULL OR NOT toString(s.id) STARTS WITH $demo_prefix)" in helper, (
        "the null-id arm is missing: a NULL-id Session would be silently "
        "dropped, which is not the demo set")
    # ⛔ THE PIN IS OVER THE COMPOSED WHERE, NEVER OVER THE FILE'S PROSE (review
    # of #8290, P2). The whole-file form of this assertion passed only because
    # the explaining comment wraps the bare form in BACKTICKS: an editor who
    # wrote it without them (a docstring, a bullet) got a failure claiming a
    # behavioural regression that did not exist — and the search could not see
    # the thing it cares about anyway, which is the condition list the query
    # actually receives.
    from tortoise.hosted_api import _session_list_conds
    composed = " AND ".join(_session_list_conds(None)[0])
    assert "toString(s.id) STARTS WITH $demo_prefix" in composed, composed
    assert "s.id STARTS WITH" not in composed.replace(
        "toString(s.id) STARTS WITH", ""), (
        "the raw-operand form is back in the COMPOSED where: it aborts the "
        "whole query on a non-string `:Session.id`, which the handler reports "
        f"as zero sessions; got {composed!r}")
    # the query builds a WHERE from the condition list; a `MATCH ... ` with no
    # WHERE would drop the exclusion on the actor_filter=None path
    assert '"MATCH (s:Session) WHERE "' in body, (
        "the MATCH-level WHERE must be unconditional — an actor-filter-"
        "conditional WHERE drops the demo exclusion for the common case")
    # #2600 rides the SAME condition list, so it cannot be appended on one branch
    assert 'conds.insert(0, "s.actor_user_id = $uid")' in helper, (
        "the actor filter must ride the same MATCH-level condition list")


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
    then be returned unchanged instead of raising). ⚠️ Whether the rule is still
    BOUND to the ``session_id`` field is NOT observable here — this arm calls the
    helper directly, so a dropped decorator or a renamed field leaves it green;
    that half is ``test_the_commit_door_rule_is_BOUND_to_the_session_id_field``
    below.
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


def test_the_commit_door_never_advises_a_mint_it_cannot_do():
    """The 422 detail must not name a remedy this door rejects (#11576 review P2).

    All four caller-id doors share ``ids.session_id_refusal``, but only three of
    them MINT: ``CommitPayload.session_id`` is a required ``str``
    (``Field(min_length=1)``) written VERBATIM by the handler, and ``None`` — or an
    omission — is a 400 ``missing_required_fields``. The shared text used to end
    with "pass None to let Tortoise mint one" for every door, reached this one
    verbatim through ``shape_errors`` → the 422 ``detail``, and a caller who
    followed it got a 400 for its trouble.

    ⛔ The validator runs ``mode="after"``, so pydantic's ``str`` type gate answers
    the non-``str`` class BEFORE it does (see the model arm below). The ``0`` case
    in the loop is therefore reachable only by calling the validator directly; it
    is here to pin what the predicate composes, not to claim that text reaches a
    caller.

    REDS IF: the validator stops passing ``may_mint=False``, or the shared
    predicate starts appending the mint remedy regardless of the door.
    """
    from pydantic import ValidationError

    from tortoise.commit_schema import CommitPayload
    from tortoise.ids import SESSION_ID_MINT_REMEDY, SESSION_ID_NO_MINT_REMEDY

    reserved = session_schema.demo_session_id("abcdef123456")
    for value in ("   ", reserved, 0):
        with pytest.raises(ValueError) as exc:
            CommitPayload._non_blank_session_id(value)
        detail = str(exc.value)
        assert SESSION_ID_MINT_REMEDY not in detail, (
            f"{value!r}: this door cannot mint, so it must not advise None — "
            f"got {detail!r}")
        assert SESSION_ID_NO_MINT_REMEDY in detail, (value, detail)

    # ...and the advice reaches the caller through the REAL model, not only the
    # validator: this is the string the 422 detail carries.
    with pytest.raises(ValidationError) as exc:
        CommitPayload.model_validate({"session_id": reserved})
    msgs = [e.get("msg", "") for e in exc.value.errors()
            if e.get("loc") == ("session_id",)]
    assert msgs, exc.value.errors()[:3]
    assert SESSION_ID_MINT_REMEDY not in msgs[0], msgs[0]
    assert SESSION_ID_NO_MINT_REMEDY in msgs[0], msgs[0]

    # ⛔ And the non-``str`` class really is answered UPSTREAM of the predicate, so
    # the caveat in ``ids.SESSION_ID_NO_MINT_REMEDY`` is measured, not narrated: a
    # ``mode="after"`` validator never sees an ``int``. If someone flips the mode,
    # the door starts answering with the predicate's own text and this reds — which
    # is the signal that the comment above needs to change with it.
    with pytest.raises(ValidationError) as exc:
        CommitPayload.model_validate({"session_id": 0})
    kinds = {e.get("type") for e in exc.value.errors()
             if e.get("loc") == ("session_id",)}
    assert kinds == {"string_type"}, kinds


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
    stopped calling it. This drives the REAL SDK doors (embedded lane —
    FalkorDBLite, no docker) with a reserved id and asserts the refusal is
    audible AND that
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


# ── the WHERE clause, RUN (the P1 the source pins could not see) ──────────
#
# The source pins above say the predicate is bound; they cannot say what the
# engine DOES with it. #8290's review found the difference the hard way.


def test_the_list_where_survives_a_legacy_non_string_session_id(tmp_path):
    """A legacy non-``str`` ``:Session.id`` must not empty the whole list (P1).

    ``STARTS WITH`` is a STRING predicate. Against a non-NULL non-string ``id``
    FalkorDB does not answer ``false`` — it RAISES
    ``Type mismatch: expected String or Null but was Integer``, and
    ``list_sessions``' fail-soft handler turns that into ``rows = []``:
    ``200 {"sessions": []}`` for an org that HAS captures, the shape #3892
    forbids. Such a row is legacy-producible — before #9599 the capture door kept
    any TRUTHY caller id and wrote it verbatim as ``MERGE (s:Session {id:$sid})``
    — so the predicate has to tolerate it.

    This runs the REAL WHERE (the one `list_sessions` composes, read from its one
    home) against a REAL graph holding four rows: the demo session, a normal one,
    a legacy int id, and one with NO id at all. All three non-demo rows must come
    back; the raw-operand form is asserted to raise on the same data, so this pin
    cannot pass on a vacuous engine.
    """
    from tortoise.hosted_api import _session_list_conds
    from tortoise.sdk import TortoiseSDK

    client = TortoiseSDK(str(tmp_path / "tortoise.db"),
                         event_log_path=str(tmp_path / "events" / "sdk.jsonl"))
    try:
        g = client._get_proj().g
        for stmt in (
            "CREATE (s:Session {id:'session_demo_abcdef12', "
            "created_at:'2026-01-01T00:00:00Z'})",
            "CREATE (s:Session {id:'session_0123456789ab', "
            "created_at:'2026-01-02T00:00:00Z'})",
            "CREATE (s:Session {id:123, created_at:'2026-01-03T00:00:00Z'})",
            "CREATE (s:Session {created_at:'2026-01-04T00:00:00Z'})",
        ):
            g.query(stmt)
        conds, params = _session_list_conds(None)
        rows = g.query("MATCH (s:Session) WHERE " + " AND ".join(conds)
                       + " RETURN s.id", params=params).result_set
        seen = [r[0] for r in rows]
        assert "session_0123456789ab" in seen, (
            f"the real session went missing: {seen!r}")
        assert 123 in seen, (
            "the legacy non-string id must be SERVED, not fatal — `toString` "
            f"makes it match nothing; got {seen!r}")
        assert None in seen, (
            "a NULL-id Session must survive the exclusion — the demo set is "
            f"exactly the prefixed ids; got {seen!r}")
        assert not any(isinstance(i, str) and i.startswith(
            session_schema.DEMO_SESSION_ID_PREFIX) for i in seen), (
            f"the demo Session is still listed: {seen!r}")
        # NON-VACUITY: the raw-operand form really does abort on this data, so
        # the assertion above is a fix and not an artefact of the fixture.
        with pytest.raises(Exception) as exc:
            _ = g.query(
                "MATCH (s:Session) WHERE (s.id IS NULL OR NOT s.id STARTS "
                "WITH $demo_prefix) RETURN s.id",
                params={"demo_prefix": session_schema.DEMO_SESSION_ID_PREFIX},
            ).result_set
        assert "Type mismatch" in str(exc.value), exc.value
    finally:
        client.close()


def test_the_hosted_capture_door_refuses_a_reserved_id_too():
    """The THIRD caller-id door, BOUND to ``SessionRequest.session_id`` (#8290 P2).

    ``CommitPayload`` has a bound arm above; ``SessionRequest`` — the model the
    public ``POST /v1/sessions`` boundary validates — had none, so reverting its
    validator to the old ad-hoc blank test would have red nothing. The namespace
    is reserved only if EVERY id-accepting boundary refuses it.
    """
    from pydantic import ValidationError

    from tortoise.hosted_api import SessionRequest

    reserved = session_schema.demo_session_id("abcdef123456")
    with pytest.raises(ValidationError) as exc:
        SessionRequest.model_validate({
            "conversation": [{"role": "user", "content": "probe"}],
            "session_id": reserved,
        })
    hits = [e for e in exc.value.errors()
            if e.get("loc") == ("session_id",)
            and "RESERVED demo/sample namespace" in e.get("msg", "")]
    assert hits, (
        "the reserved-id refusal is no longer BOUND to "
        f"SessionRequest.session_id — errors: {exc.value.errors()[:3]!r}")
    # non-vacuity: a real id still validates, so the field is not simply refused
    ok = SessionRequest.model_validate({
        "conversation": [{"role": "user", "content": "probe"}],
        "session_id": "session_0123456789ab",
    })
    assert ok.session_id == "session_0123456789ab"


def test_the_activation_scorecard_does_not_count_the_demo_session():
    """The aggregate surface agrees with the list (#8290 review, P2).

    ``GET /v1/activation/scorecard`` counts ``:Session`` rows in its window, so a
    demo-seeded org published ``captured_sessions: 1`` beside
    ``GET /v1/sessions`` → ``{"sessions": []}`` — the same two-surfaces
    disagreement one surface over. ``stage_counts`` reads its rows through the ONE
    predicate, so the sample is not a capture.
    """
    from tortoise.activation_scorecard import stage_counts

    rows = [
        ("session_demo_abcdef12", "2026-01-01T00:00:00Z", 3, 3, 2),
        ("session_0123456789ab", "2026-01-02T00:00:00Z", 1, 1, 4),
    ]
    stages, detail = stage_counts(rows, "2026-01-01T00:00:00Z",
                                  "2026-01-03T00:00:00Z")
    assert detail["captured_sessions"] == 1, (
        f"only the real capture counts: {detail!r}")
    assert stages["captured"]["value"] == 1, stages["captured"]
    # non-vacuity: the same fixture WITHOUT the demo row is unchanged, so the
    # assertion above is not passing because the loop counts nothing.
    _stages2, detail2 = stage_counts(rows[1:], "2026-01-01T00:00:00Z",
                                     "2026-01-03T00:00:00Z")
    assert detail2["captured_sessions"] == 1, detail2


def test_the_lifetime_baseline_excludes_the_sample_at_the_query(tmp_path):
    """Stage 4's baseline is a SIBLING surface, and it is fixed at the query (P2).

    ``LIFETIME_MEMORY_QUERY`` returns ``min(s.created_at)`` over sessions that
    carry a non-``event`` point, and that value is load-bearing: a non-NULL
    ``first_memory_at`` is what moves stage 4 off ``no_memory_produced_in_lifetime``
    onto a measured recall count. ``stage_counts`` filtering the funnel's rows
    does not reach it, so a demo Session that carried such a point — through a
    journal replay, or a future seeder — would move the baseline BACK to the
    sample's creation date and stage 4 with it (review of #8290, P2).

    Runs the real constant against a real graph holding both sessions, and
    re-runs the pre-exclusion form on the same data so the pin cannot pass on a
    fixture the exclusion never reached.
    """
    from tortoise.activation_scorecard import LIFETIME_MEMORY_QUERY
    from tortoise.sdk import TortoiseSDK

    client = TortoiseSDK(str(tmp_path / "tortoise.db"),
                         event_log_path=str(tmp_path / "events" / "sdk.jsonl"))
    try:
        g = client._get_proj().g
        for stmt in (
            "CREATE (s:Session {id:'session_demo_abcdef12', "
            "created_at:'2026-01-01T00:00:00Z'})",
            "CREATE (p:Point {id:'p_demo_memory', pointKind:'claim', "
            "created_at:'2026-01-01T00:00:00Z'})",
            "MATCH (s:Session {id:'session_demo_abcdef12'}), "
            "(p:Point {id:'p_demo_memory'}) CREATE (s)-[:CONTAINS]->(p)",
            "CREATE (s:Session {id:'session_0123456789ab', "
            "created_at:'2026-01-05T00:00:00Z'})",
            "CREATE (p:Point {id:'p_real_memory', pointKind:'claim', "
            "created_at:'2026-01-05T00:00:00Z'})",
            "MATCH (s:Session {id:'session_0123456789ab'}), "
            "(p:Point {id:'p_real_memory'}) CREATE (s)-[:CONTAINS]->(p)",
        ):
            g.query(stmt)
        rows = g.query(LIFETIME_MEMORY_QUERY).result_set
        assert rows and rows[0][1] == 1, (
            f"the baseline counted the sample session: {rows!r}")
        assert rows[0][0] == "2026-01-05T00:00:00Z", (
            "the baseline's first_memory_at moved back to the sample's "
            f"creation date: {rows!r}")
        # NON-VACUITY: the same graph WITHOUT the exclusion counts both, so the
        # assertions above are the exclusion working, not an empty fixture.
        pre = g.query(
            "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
            "WHERE (p.pointKind IS NULL OR p.pointKind <> 'event') "
            "RETURN min(s.created_at), count(DISTINCT s)").result_set
        assert pre == [["2026-01-01T00:00:00Z", 2]], pre
    finally:
        client.close()
