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
CALLER-SUPPLIED id) — the ONE-DECLARATION contract, and the source pins that
keep the three read surfaces bound to that one declaration (the detail door and
the SDK mirror through the predicate, the list query through the
``$demo_prefix`` parameter) instead of re-spelling it. The
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


# ── the three surfaces are BOUND to the one DECLARATION (2 predicate, 1 param) ─


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
        # ⛔ ONE arm, not two: the SDK's `commit_session` door is DELETED by
        # #4282 (its `_commit_session_v1`/`_v2` bodies still exist but run the
        # extractor, so they cannot be driven as a refusal), and the surviving
        # commit boundary is `POST /v1/sessions/commit`'s Layer-1 model, pinned
        # by `test_the_commit_door_refuses_a_reserved_id_too` and
        # `test_the_commit_door_rule_is_BOUND_to_the_session_id_field` below.
        # The capture door survives privately, which is what is driven here.
        res = client._capture_session(turns, session_id=reserved)
        assert res["ok"] is False, res
        errors = [str(e) for e in (res.get("errors") or [])]
        assert any("RESERVED demo/sample namespace" in e for e in errors), errors
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


# ── the cite-count notes, MEASURED (a hand count has been wrong at this head) ─

#: The pivot both notes are written around: the ONE production execution site of
#: ``LIFETIME_MEMORY_QUERY``. Spelled here rather than imported — the NUMBER is
#: the claim's subject, and importing the API module for it would pull the whole
#: stack into a fast suite.
_PIVOT = 23128

#: The one pattern that defines a "cite target" lives in
#: ``tools/hosted_api_cite_census.py`` — ONE implementation, so the note's stated
#: remedy and this pin cannot measure two different populations. It requires
#: DIGITS (`[0-9]+`); the `*` form also matches a bare ``hosted_api.py:`` and
#: ``sort -u`` then counts that empty run as one more target, which is how the
#: count was briefly "re-measured" from 449 to 450.


def _stated(pattern: str, src: str) -> int:
    found = re.search(pattern, src)
    assert found, (
        "the note's sentence is gone or re-worded; re-point this pin at it: "
        f"{pattern!r}")
    return int(found.group(1))


def test_the_hosted_api_cite_notes_state_one_measurement_and_the_pivot():
    """The two cite-count notes must state ONE measurement, and it must be arithmetic.

    Why this exists (review of #11576, P2): the scorecard note's figures are a
    hand count, and a review round "re-measured" **449 / 434 / 14** into
    **450 / 435 / 14** — a false correction, marked as a re-measurement, that no
    test could see. The `*` form of the count regex admits a bare
    ``hosted_api.py:`` and ``sort -u`` counts that empty run as one more target;
    the tool requires digits.

    Why it does NOT assert the tree-wide figures HERE (review of #11576, P2, second
    round): that population belongs to EVERY tracked file, so a fast-suite test
    comparing it to prose would red unrelated PRs — measured ~1 commit in 30 adds
    a ``hosted_api.py:<N>`` citation, and the only remedy would be hand-editing
    prose in a module the failing commit never touched. The census has its own
    command, ``uv run python tools/hosted_api_cite_census.py``, which is what both
    notes now point at. This pin covers what THIS change owns.

    REDS IF: the two notes disagree about the number of distinct targets, the three
    scorecard figures stop being arithmetic (below + above + the pivot target =
    the total), or the pivot cite stops being the ``LIFETIME_MEMORY_QUERY`` site.
    """
    from tools.hosted_api_cite_census import census, pivot_is_the_lifetime_site

    scorecard = _src("tortoise/activation_scorecard.py")
    hosted = _src("tortoise/hosted_api.py")

    below = _stated(r"\*\*(\d+)\*\* have a number smaller", scorecard)
    above = _stated(r"\*\*(\d+)\*\* distinct cite", scorecard)
    distinct = _stated(r"\((\d+) distinct targets in all", scorecard)
    hosted_distinct = _stated(r"tracked files \((\d+) distinct", hosted)

    # ONE measurement, stated twice — two figures at two sites can drift apart
    assert hosted_distinct == distinct, (
        f"the two notes disagree about the target count: {distinct} vs "
        f"{hosted_distinct}")

    # ... and it is ARITHMETIC: every distinct target is below the pivot, above it,
    # or the pivot itself. A hand-edit of any one figure breaks this without any
    # tree-wide scan, so an unrelated PR cannot fail it.
    assert below + above + 1 == distinct, (
        f"the figures are not arithmetic: below={below} + above={above} + the "
        f"pivot target (1) != distinct={distinct} — one of the three was "
        f"hand-edited; run `uv run python tools/hosted_api_cite_census.py`")

    # The pivot cite is not a bare number: it is the query's ONE production
    # execution site. This is a real invariant an unrelated doc edit cannot break.
    assert pivot_is_the_lifetime_site(_PIVOT), (
        f"hosted_api.py:{_PIVOT} is no longer the LIFETIME_MEMORY_QUERY site — "
        "re-derive the pivot and both notes' figures with "
        "`uv run python tools/hosted_api_cite_census.py --pivot <line>`")

    # the census command is the notes' stated remedy, so it must work and must not
    # collapse to nothing (that, and only that, about the population is pinned here)
    data = census(_PIVOT)
    assert data["distinct_targets"] > 100 and data["above_pivot"] > 0, (
        f"the census collapsed: {data!r}")


#: The scorecard note spells the pivot THREE times. Each spelling is a figure the
#: census must be able to COMPARE and REWRITE — an uncaptured one is invisible to
#: both verbs (see ``test_the_census_refresh_restates_every_pivot_spelling``).
_PIVOT_SPELLINGS = (
    ("the ``hosted_api.py:<pivot>`` cite",
     r"PRODUCTION execution is ``hosted_api\.py:(\d+)``"),
    ("the ``LARGER than <pivot>`` bound of the ABOVE range",
     r"LARGER than (\d+)"),
    ("the ``the remainder is <pivot> itself`` tail",
     r"the remainder is (\d+) itself"),
)


def test_every_pivot_spelling_in_the_note_is_the_pivot_the_test_derives():
    """All three prose spellings of the pivot must equal ``_PIVOT``.

    Why this exists (review of #11576, P2): the scorecard note states the pivot at
    three sites, and before this pin the fast suite re-derived ``_PIVOT`` from
    ``hosted_api.py`` (``pivot_is_the_lifetime_site``) without ever comparing it to
    the PROSE. The third spelling — the ``LARGER than <pivot>`` bound — was not
    even captured by the census tool's own pattern table, so a hand-edit to that
    number (or a pivot move that ``--refresh`` failed to restate) left every
    assertion here green.

    REDS IF: any of the three spellings disagrees with ``_PIVOT``.
    """
    scorecard = _src("tortoise/activation_scorecard.py")
    for description, pattern in _PIVOT_SPELLINGS:
        assert _stated(pattern, scorecard) == _PIVOT, (
            f"{description} in tortoise/activation_scorecard.py does not state "
            f"the pivot the test derives ({_PIVOT}) — the note says otherwise; "
            f"re-state it with `uv run python tools/hosted_api_cite_census.py "
            f"--refresh`")


def _census_tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway copy of the three note files, with the census pointed at it.

    The census verbs WRITE, so nothing here may touch the real checkout: ``_sweep``
    resolves every path through the module-global ``REPO``, which is redirected.
    """
    from tools import hosted_api_cite_census as census_mod

    for rel in (census_mod._SCORECARD, census_mod._HOSTED, census_mod._TEST):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_src(rel), encoding="utf-8")
    monkeypatch.setattr(census_mod, "REPO", tmp_path)
    return tmp_path


def test_the_census_check_verb_reds_on_a_stale_figure(tmp_path, monkeypatch):
    """``--check`` must exit 1 when a stated figure is not the measured one.

    Why this exists (review of #11576, P3): ``--check`` is the ONLY detector of a
    stale note — the nightly job runs it and nothing else does — and it had no test
    at all. Mutating ``if stated == want:`` to ``if True:`` left ``--check``
    reporting "every stated figure is the measured one" with RC 0 and the whole
    fast suite green. The two arms below are the discriminating pair.
    """
    from tools import hosted_api_cite_census as census_mod

    repo = _census_tmp_repo(tmp_path, monkeypatch)
    measured = census_mod.census(_PIVOT)

    # arm 1: the notes as written agree with the tree — green.
    assert census_mod._sweep(measured, refresh=False) == 0, (
        "the shipped notes must be in agreement with the measured census")

    # arm 2: degrade ONE figure by hand — the verb must now report stale (1), not 0.
    scorecard = repo / census_mod._SCORECARD
    text = scorecard.read_text(encoding="utf-8")
    # ⛔ DERIVE the figure to degrade; never spell it. A hard-coded literal here
    # reds the moment `--refresh` moves the census — which is exactly what happened
    # to the former `**436**`: an honest restatement left this arm pointing at a
    # number the note no longer states, and the arm failed on the ARM rather than on
    # the verb it exists to convict. A literal that must be re-pointed by hand is
    # the bump-fragility the branch already removed from the Pi-seam row (#11662).
    import re as _re

    figures = _re.findall(r"\*\*(\d+)\*\*", text)
    assert figures, (
        "the scorecard note states no bold figure to degrade — the census restates "
        "these with `uv run python tools/hosted_api_cite_census.py --refresh`; this "
        "arm needs one of them")
    stated = figures[0]
    scorecard.write_text(text.replace(f"**{stated}**", "**999**", 1),
                         encoding="utf-8")
    assert census_mod._sweep(measured, refresh=False) == 1, (
        "`--check` green-lit a note whose stated figure is not the measured one — "
        "that is the false green the nightly census-drift job exists to prevent")


def test_the_census_refresh_restates_every_pivot_spelling(tmp_path, monkeypatch):
    """A pivot MOVE must be restated at all three prose sites, not two.

    Why this exists (review of #11576, P2): ``--refresh`` is documented as
    re-stating "every figure", and the ``LARGER than <pivot>`` bound was matched by
    an UNCAPTURED digit run, so a pivot move left the note saying 24500 and 23128
    at once while ``--check`` reported it clean. A pivot move is not exotic: the
    pivot IS the ``LIFETIME_MEMORY_QUERY`` execution site, so any commit that adds
    a line above it moves the pivot — the exact event both notes say `--refresh`
    exists for.

    REDS IF: any pivot spelling survives the refresh with its old value.
    """
    from tools import hosted_api_cite_census as census_mod

    repo = _census_tmp_repo(tmp_path, monkeypatch)
    moved = dict(census_mod.census(_PIVOT), pivot=_PIVOT + 1372, at_pivot=0)

    assert census_mod._sweep(moved, refresh=True) == 1, (
        "a pivot move is stale work: the refresh pass must report it")

    text = (repo / census_mod._SCORECARD).read_text(encoding="utf-8")
    for description, pattern in _PIVOT_SPELLINGS:
        assert _stated(pattern, text) == _PIVOT + 1372, (
            f"`--refresh` left {description} stating the OLD pivot — this figure "
            f"is outside the census's figure set, so `--check` would then call the "
            f"self-contradictory note clean")

    # and the next pass converges (a rewrite that cannot be re-matched is the
    # corruption Finding 2 of the same review names).
    assert census_mod._sweep(moved, refresh=True) == 0


def test_the_census_refuses_an_unmeasurable_figure_instead_of_writing_None(
        tmp_path, monkeypatch):
    """An EMPTY side of the pivot is a REFUSAL, never a write of ``None``.

    Why this exists (review of #11576, P3): ``census()`` returns `None` for the
    min/max of an empty side, and ``_restate`` stringified it into the prose as the
    word ``None`` — destroying the very sentence the next pass must match to
    repair it, so the tool then printed "nothing was written" over a file it had
    already modified (both documented contracts false on that path).

    REDS IF: the note is modified, ``(None … None)`` reaches the prose, or the
    exit code is not the documented refusal (2).
    """
    from tools import hosted_api_cite_census as census_mod

    repo = _census_tmp_repo(tmp_path, monkeypatch)
    scorecard = repo / census_mod._SCORECARD
    before = scorecard.read_text(encoding="utf-8")

    empty_below = dict(census_mod.census(_PIVOT), below_pivot=0,
                       min_target=None, max_below=None)
    assert census_mod._sweep(empty_below, refresh=True) == 2, (
        "an unmeasurable figure must be a REFUSAL (2), not a rewrite")

    after = scorecard.read_text(encoding="utf-8")
    assert after == before, "`nothing was written` must be true: the file changed"
    assert "(None … None)" not in after, "`None` reached the note's prose"


def test_the_nightly_census_drift_job_still_runs_the_check_verb():
    """The nightly ``census-drift`` job is the ONLY thing that runs ``--check``.

    Why this exists (review of #11576, P3): the previous round's fix was "nothing
    runs the command", and that fix is itself unpinned — deleting the whole job
    left the suite green and ``grep -rn census-drift tests/`` empty, so the same
    defect could return silently.

    The ``timeout-minutes`` arm pins the floor ``docs/ops/ci-wall-clock.md`` states
    (15 m, not 5 m): that doc measured a ~9 m runner-side stall on a one-minute
    job, and a cap under the observed stall false-reds the check — here it would
    erase the only channel that reports the drift. (A timed-out job is reported
    `cancelled`, which is non-red, so the signal would vanish silently.)
    """
    import yaml

    workflow = yaml.safe_load(
        _src(".github/workflows/main-health-nightly.yml"))
    jobs = workflow["jobs"]
    assert "census-drift" in jobs, (
        "the nightly census-drift job is gone — `--check` then runs NOWHERE and "
        "the two notes can drift with every signal green")

    job = jobs["census-drift"]
    commands = [step.get("run", "") for step in job.get("steps", [])]
    assert any("tools/hosted_api_cite_census.py --check" in c for c in commands), (
        f"the census-drift job no longer runs the `--check` verb: {commands!r}")
    assert job.get("timeout-minutes", 0) >= 15, (
        f"timeout-minutes: {job.get('timeout-minutes')!r} is under the floor "
        f"`docs/ops/ci-wall-clock.md` states (15 m, from a measured ~9 m"
        f" runner-side stall) — a stall would false-red the only drift signal")


def test_the_predicate_docstring_names_every_production_caller():
    """``is_demo_session_id`` is shared by THREE production callers, not two.

    Review of #11576 (P2): the docstring was narrowed to "the DETAIL DOOR and
    the SDK MIRROR", which is true of the two GUARD sites but omits the third
    production caller — ``activation_scorecard.stage_counts``, which imports and
    calls the predicate to filter the scorecard's rows (that file says so in its
    own note: it uses the shared predicate rather than a second Cypher spelling).
    A docstring that under-counts its callers is the same drift class it exists
    to prevent, one surface over.
    """
    doc = session_schema.is_demo_session_id.__doc__ or ""
    assert "activation_scorecard.stage_counts" in doc, (
        "the predicate's docstring must name the scorecard caller — it is the "
        "third production call site, and omitting it is how the claim stayed "
        "false in a commit message")
    assert "THREE production callers" in doc or "three production callers" in doc, doc

    # non-vacuity: each named caller really does call it at this head
    assert "if is_demo_session_id(session_id):" in _src("tortoise/hosted_api.py")
    assert "if is_demo_session_id(session_id):" in _src("tortoise/sdk.py")
    scorecard = _src("tortoise/activation_scorecard.py")
    assert "from tortoise.session_schema import is_demo_session_id" in scorecard
    assert re.search(r"if is_demo_session_id\(", scorecard), (
        "the scorecard caller the docstring now names must still be a CALL")
    # and the FOURTH `mint_session_id()` site is recorded where the mint's own
    # docstring counts its create sites — not left to a commit message, which is
    # where the review found the false version of this claim (P3, #11576)
    assert "extract_session_v2" in _src("tortoise/ids.py"), (
        "ids.py must still name ``extractor_v2.extract_session_v2`` among the "
        "four create sites the mint replaced")
