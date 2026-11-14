"""#10371 — ``update_entity``'s Object and Subject arms had no lifecycle guard,
so a terminal row was resurrected by a prop edit.

Measured on ``main`` at ``6916a6cd89`` (2026-11-13), embedded engine, throwaway
nodes, deleted after:

    update_entity(<archived Object>,  status='live') -> ACCEPTED  RESURRECTED
    update_entity(<archived Subject>, status='live') -> ACCEPTED  RESURRECTED

That is the #9649 defect class on two more labels. #12325 closed
``update_entity``'s ``:Point`` arm, and ``_assert_lifecycle_guard`` is
``:Point``-hardcoded (``point_id``, ``MATCH (n:Point …)``), so the other two arms
never reached it.

⛔ WHAT THIS GUARD IS NOT: A VOCABULARY. A closed ``OBJECT_STATUS_VALUES`` was
drafted, measured, and DROPPED before it shipped (it is not in the tree) —
ONTOLOGY §4.3 (Object) says *"pack-declared
kinds validate against their own declared words"*, and `packs/venture/manifest.yaml`
declares ``active``, ``prospective``, ``invested``, ``written-off``,
``prioritized``, ``paused``, ``deprioritized``, ``prospect``, ``applied``,
``awarded``, ``closed`` for its Object kinds, while the connector vocabulary adds
``open``/``closed``/``active``/``pending``. A shared set refuses all of them, and
it also reddened ``tests/test_9206_apply_first_lane.py`` (which writes
``status='active'`` on an Object through this very door). So the guard asks the
TRANSITION question only — which is the question that stops a resurrection. What
it stops is narrower and more useful than "an undeclared word was written": any
word the family's read vocabulary does not treat as DEAD reads as CURRENT, so a
dead row departing for any of them is the harm. The guard therefore never
inspects the TARGET word — only the family's dead set.

What this suite pins:

* a terminal Object or Subject refuses a transition that makes it CURRENT;
* the dead set is the family's own recorded one
  (``commit_ops.OBJECT_TERMINAL_STATUSES`` / ``SUBJECT_TERMINAL_STATUSES``), not a
  literal. Pack/connector words (``active``, ``prospective``, ``invested``,
  ``prioritized``, ``paused``, ``open``, ``closed``, ``pending``) are not refused
  FOR BEING UNDECLARED — the anti-regression for the closed-set mistake. That
  acceptance is ROW-SCOPED: on a DEAD row those same words ARE refused, because
  they are not dead either, and leaving a dead state for a current-reading word
  is the resurrection this guard exists to stop;
* the #1423 SUBJECT VOCABULARY guard is a DIFFERENT question and survives
  unchanged: an undeclared word (``dead``, ``retracted``) is refused on a
  Subject, because that family's read vocabulary is a denylist of KNOWN terminal
  states and an unknown word reads as CURRENT. A first cut of this change
  REPLACED that block instead of extending it and silently reopened the hole; the
  pins below are the regression guard;
* DEAD -> DEAD stays legal, the per-family delta from Points (§5 declares every
  terminal Point with an empty outgoing set; an Object's dead words are
  bookkeeping, and ``tests/test_11826_object_supersession_instrument.py`` moves
  ``superseded -> archived`` and asserts what that does);
* an ENTAILED status beside a real edit is not a transition (the generic tenant
  read-modify-write), and content/metadata revisions on a dead row stay legal;
* the verdict is over EVERY row the write binds (#7822's fan-out rule, applied to
  the non-Point arms): a live sibling must not authorise a write that also lands
  on the terminal one;
* the refusal's repair is PERFORMABLE — it does not name ``supersede()``, which is
  ``:Point``-only and raises ``No point <id>`` for these labels (the AX-rule-2
  defect #10183 records; the missing SDK door is #12592);
* nothing leaks onto the Point arm, which owns its own vocabulary and table.

Runnable (docker lane):

    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_t10371' \\
        uv run pytest tests/test_10371_nonpoint_lifecycle_guard.py -q
"""
from __future__ import annotations

import pytest

from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES  # the Object dead set, driven below
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "t10371.db"))
    yield s
    s.close()


def _object(s, status="live") -> str:
    return s.create_object(name=f"obj-{status}-{len(status)}", status=status)["id"]


def _subject(s, status="live") -> str:
    return s.create_subject(name=f"subj-{status}-{len(status)}", status=status)["id"]


def _stored(s, node_id) -> dict:
    """The row as a named mapping, or `{}` when it is gone.

    Keys: `label`, `status`, `supersededBy`, `supersededAt`. `.result_set` hands
    back positional LISTS (the driver does not project aliases into a mapping), so
    the four columns are labelled here rather than indexed by name — the carrier
    columns are what let the un-supersede pins assert a clear really CLEARED
    rather than merely being requested.
    """
    rows = s._get_proj().g.query(
        "MATCH (n {id:$id}) RETURN labels(n)[0], n.status, "
        "n.supersededBy, n.supersededAt",
        params={"id": node_id}).result_set
    if not rows:
        return {}
    return {"label": rows[0][0], "status": rows[0][1],
            "supersededBy": rows[0][2], "supersededAt": rows[0][3]}


# ── the dead set is the family's RECORDED one, not a literal in the guard ──

# ⛔ `sorted(...) or ["<none>"]` keeps the parametrization NON-EMPTY: an emptied
# dead set would otherwise generate ZERO cases and this test would silently skip,
# which is how a behavioural pin can pass by disappearing.
@pytest.mark.parametrize("dead", sorted(OBJECT_TERMINAL_STATUSES) or ["<none>"])
def test_every_word_the_family_calls_dead_is_treated_as_dead(sdk, dead):
    """Behavioural, not a re-read of the constant.

    ⛔ Asserting ``"archived" in OBJECT_TERMINAL_STATUSES`` cannot fail from any
    change to the GUARD — a matching literal in `sdk.py` would be behaviourally
    identical and both stay green. This drives the guard over EVERY word the
    family declares dead, so swapping the set for a literal that omits one reds
    here. (`retracted` is included: it is dead for these families and storable.)
    """
    assert OBJECT_TERMINAL_STATUSES, \
        "the Object dead set is empty — this pin would have been skipped, not passed"
    node = _object(sdk, "live")
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n.status=$st",
        params={"id": node, "st": dead})
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status="live")
    assert f"{dead!r} -> 'live'" in str(exc.value)


def test_completed_is_a_state_a_row_can_leave(sdk):
    """The complement of the set above, driven the same way.

    `completed` must NOT be dead: a finished work item can be reopened
    (`in_progress`). Pinned behaviourally — asserting
    ``"completed" not in OBJECT_TERMINAL_STATUSES`` would stay green against a
    literal set in the guard.
    """
    node = _object(sdk, "live")
    sdk.update_entity(node, status="completed")
    sdk.update_entity(node, status="in_progress")
    assert _stored(sdk, node)["status"] == "in_progress"


# ── the transition half: DEAD -> CURRENT is refused, on both families ──

@pytest.mark.parametrize("maker,label", [(_object, "Object"), (_subject, "Subject")])
@pytest.mark.parametrize("dead", ["archived", "superseded", "deprecated"])
def test_a_terminal_row_is_not_resurrected_by_a_prop_edit(sdk, maker, label, dead):
    node = maker(sdk, dead)
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status="live")
    message = str(exc.value)
    # AX rule 2 (#10183): that it refused, on what, and what to do instead.
    assert "update_entity refused the status transition" in message
    assert f"{dead!r} -> 'live'" in message
    assert f"on {label} {node!r}" in message
    assert _stored(sdk, node)["status"] == dead, "a refused write must store nothing"


def test_the_refusal_names_the_family_section_of_the_model(sdk):
    """§4.3 for an Object, §4.2 for a Subject — the model is what is cited.

    ⛔ Pinned because the first cut cited §4.1 for an Object, and §4.1 is POINT.
    A refusal that sends a reader to the Point table for an Object's dead words
    is the AX-rule-2 defect in its mildest form: the citation exists, it is just
    about something else. Measured: `docs/ONTOLOGY.md` §4.1 Point (:394),
    §4.2 Subject (:423), §4.3 Object (:441).
    """
    obj, subj = _object(sdk, "archived"), _subject(sdk, "archived")
    with pytest.raises(ValueError) as obj_exc:
        sdk.update_entity(obj, status="live")
    assert "ONTOLOGY §4.3" in str(obj_exc.value)
    assert "ONTOLOGY §4.1" not in str(obj_exc.value), \
        "§4.1 is the Point family — the Object refusal must not cite it"
    with pytest.raises(ValueError) as subj_exc:
        sdk.update_entity(subj, status="live")
    assert "ONTOLOGY §4.2" in str(subj_exc.value)


def test_the_named_repair_is_performable(sdk):
    """⛔ The message must NOT offer `supersede()`.

    `TortoiseSDK.supersede` routes to `supersede_point`/`invalidate_point`, both
    `:Point`-only, and raises `No point <id>` for an Object. Naming it would be an
    unperformable prescription — the AX-rule-2 defect #10183 exists to stop. The
    real route today is the record path, and the missing SDK verb is #12592.
    """
    node = _object(sdk, "archived")
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status="live")
    message = str(exc.value)
    assert "supersede()" not in message, \
        "that door raises No point <id> for an Object — do not prescribe it"
    assert "apply_supersessions" in message
    # And the prescription that IS named really exists:
    from tortoise.commit_ops import apply_supersessions  # noqa: F401
    with pytest.raises(ValueError, match="No point"):
        sdk.supersede(node, "some-successor")   # Point-only, as the message says


# ── NOT a vocabulary: a pack/connector word is accepted ──

PACK_AND_CONNECTOR_WORDS = (
    # `packs/venture/manifest.yaml` Object-kind lifecycles
    "active", "prospective", "invested", "prioritized", "paused",
    "deprioritized", "prospect", "applied", "awarded", "closed",
    # `tortoise/status_folds.py`'s shared connector vocabulary
    "open", "pending",
)


@pytest.mark.parametrize("word", PACK_AND_CONNECTOR_WORDS)
def test_a_pack_or_connector_word_is_not_refused_on_an_object(sdk, word):
    """The anti-regression for the closed-set mistake, driven over EVERY word.

    A shared ``OBJECT_STATUS_VALUES`` refused all of these AND reddened
    ``tests/test_9206_apply_first_lane.py``, which writes ``active`` through this
    door. §4.3 (Object)'s rule is that a pack-declared kind validates against its
    OWN declared words. The guard therefore asks only the TRANSITION question, and
    on a live row that question has no complaint.

    ⛔ ROW-SCOPED, and the scope is the point: on a DEAD row these same words ARE
    refused, because leaving a dead state for a word the read surfaces call
    current is exactly the resurrection this guard stops. `active` is not a
    vocabulary exception; it is an undeclared-word exception, and only that.
    """
    node = _object(sdk, "live")
    sdk.update_entity(node, status=word)
    assert _stored(sdk, node)["status"] == word


@pytest.mark.parametrize("word", PACK_AND_CONNECTOR_WORDS)
def test_a_pack_word_on_a_DEAD_row_is_refused(sdk, word):
    """The other half of the row-scope claim, driven over the same words.

    The acceptance above is about a LIVE row. On a DEAD one these words are
    refused like any other non-dead target — precisely because they are not dead,
    and a dead row departing for a word the read surfaces call current is the
    resurrection this guard exists to stop. Pinned separately so the docstring's
    "ROW-SCOPED" sentence cannot claim coverage the suite does not carry.
    """
    node = _object(sdk, "archived")
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status=word)
    assert "update_entity refused the status transition" in str(exc.value)
    assert "'archived'" in str(exc.value)
    assert _stored(sdk, node)["status"] == "archived"


@pytest.mark.parametrize("word", ["retracted", "dead", "banana"])
def test_a_subject_still_refuses_an_undeclared_word(sdk, word):
    """#1423's guard, which this change must not and does not remove.

    ``retracted`` and ``dead`` are undeclared for a Subject (§4.2). They are not
    "dead" words this guard knows — an unknown word reads as CURRENT on the read
    sides, which is why the vocabulary check is the only place it can be caught.
    A first cut of #10371 dropped this block and these rows went green.
    """
    node = _subject(sdk, "live")
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status=word)
    assert f"Invalid status {word!r} for a :Subject" in str(exc.value)
    assert _stored(sdk, node)["status"] == "live"


def test_an_undeclared_word_is_still_the_other_issue(sdk):
    """⛔ Pinned so nobody reads this guard as closing the vocabulary hole.

    `banana` still stores on an Object; that is #10371's ORIGINAL complaint and it
    stays OPEN, because closing it needs per-kind resolution (a pack kind's own
    words, §4.3 Object), not a shared set. If this test ever fails, someone added
    a closed set and owes §4.3 an answer.

    The one real Object writer of a legacy word is
    ``tests/test_11926_derived_invalidation.py`` (``update_entity(<Object>,
    status='outdated')``, read back at its ``status`` assertion);
    ``tortoise/derived_invalidation.py`` only MENTIONS the word in a comment and
    never calls this door with it.
    """
    node = _object(sdk, "live")
    sdk.update_entity(node, status="banana")
    assert _stored(sdk, node)["status"] == "banana"


# ── SCOPE: an entailed status is not a transition; content stays revisable ──

@pytest.mark.parametrize("maker", [_object, _subject])
def test_an_entailed_status_beside_a_real_edit_is_accepted(sdk, maker):
    """The read-modify-write shape a generic tenant PUT actually sends."""
    node = maker(sdk, "archived")
    assert sdk.update_entity(node, status="archived", description="a note")
    assert _stored(sdk, node)["status"] == "archived"


@pytest.mark.parametrize("maker", [_object, _subject])
def test_a_content_revision_without_a_status_is_untouched(sdk, maker):
    """The block keys on a supplied `status` only — it must not gate content."""
    node = maker(sdk, "archived")
    assert sdk.update_entity(node, description="revision only")
    assert _stored(sdk, node)["status"] == "archived"


# ── DEAD -> DEAD is legal: the per-family delta from Points ──

def test_a_dead_row_may_be_re_filed_between_dead_words(sdk):
    """``archived -> deprecated`` STAYS LEGAL, and that is the delta from Points.

    ⛔ Pinned because it is the opposite of the Point rule and the difference is
    load-bearing. §5 declares every terminal POINT with an empty outgoing set
    (``_ALLOWED_TRANSITIONS``): a dead claim is answered only by minting a
    successor. An Object's dead words are bookkeeping, and `main` moves them:
    ``tests/test_11826_object_supersession_instrument.py`` takes a `superseded`
    Object to `archived` and then asserts that archiving SILENCES the
    ``superseded_object_active_edges`` finding while (per `audit.py`'s own
    docstring) NOT clearing the deficit — so nothing there offers archiving as a
    repair, and refusing the move would red that module.
    """
    node = _object(sdk, "archived")
    sdk.update_entity(node, status="deprecated")
    assert _stored(sdk, node)["status"] == "deprecated"
    with pytest.raises(ValueError):
        sdk.update_entity(node, status="live")   # ...and it is still dead


def test_a_live_row_is_retired_by_a_status_write(sdk):
    """CURRENT -> DEAD is a retirement, not a resurrection; it stays legal."""
    node = _object(sdk, "live")
    sdk.update_entity(node, status="archived")
    assert _stored(sdk, node)["status"] == "archived"


def test_an_object_moves_between_non_terminal_states(sdk):
    node = _object(sdk, "live")
    sdk.update_entity(node, status="completed")
    assert _stored(sdk, node)["status"] == "completed"
    sdk.update_entity(node, status="in_progress")
    assert _stored(sdk, node)["status"] == "in_progress"


# ── the record-carried un-supersede is the ONE legal DEAD -> CURRENT ──

def test_clearing_both_carriers_in_the_same_call_un_supersedes(sdk):
    """⛔ A RECORDED CAPABILITY, not a convenience — this red went unseen once.

    `cache_projection`'s "NOT MONOTONE — the carriers CAN be cleared" bullet:
    `update_entity(row, status='live', supersededBy=None, supersededAt=None)`
    journals the null carriers, so the record and a rebuild agree that nothing
    replaced the row. The first cut of this guard refused it and reddened
    `tests/test_status_projection.py::test_clearing_a_supersession_is_still_writable`,
    which was outside the batch being run.
    """
    node = _object(sdk, "live")
    sdk.update_entity(node, status="superseded", supersededBy="successor-v2",
                      supersededAt="2026-01-01T00:00:00Z")
    assert _stored(sdk, node)["status"] == "superseded"
    sdk.update_entity(node, status="live", supersededBy=None,
                      supersededAt=None)
    row = _stored(sdk, node)
    assert row["status"] == "live"
    # ⛔ BOTH carriers must be GONE from the row, not merely requested: a clear
    # that does not clear is exactly what the snake spelling does, and asserting
    # only the status is how that went unnoticed.
    assert row["supersededBy"] is None, row
    assert row["supersededAt"] is None, row


def test_the_snake_spelling_is_NOT_an_un_supersede_because_it_clears_nothing(sdk):
    """⛔ It writes a DIFFERENT property, so it is a resurrection with a stray edit.

    Measured: ``update_entity(row, superseded_by=None)`` writes
    ``n.superseded_by``, while the supersession instruments (`audit`,
    `commit_ops`, `assembly`) read ``n.supersededBy``. Accepting the snake shape
    as an un-supersede therefore stored ``status='live'`` beside an INTACT
    ``supersededBy`` — the incoherent pair this guard exists to stop. The
    exemption is camel-only because the camel spelling is the one that clears the
    columns those instruments read.

    ⛔ `derived_invalidation` is NOT in that list, deliberately: it FALLS BACK to
    the snake spelling, so the property is read — by exactly one reader, which is
    what makes the pair incoherent rather than merely stale.

    (That the two spellings write different properties is a PRE-EXISTING defect,
    recorded on #12745 rather than fixed here. This pin is what makes the
    exemption honest in the meantime — and it fails if the exemption is widened
    back to "either spelling".)
    """
    node = _object(sdk, "live")
    sdk.update_entity(node, status="superseded", supersededBy="successor-v2",
                      supersededAt="2026-01-01T00:00:00Z")
    with pytest.raises(ValueError):
        sdk.update_entity(node, status="live", superseded_by=None,
                          superseded_at=None)
    assert _stored(sdk, node)["status"] == "superseded"


@pytest.mark.parametrize("only", ["supersededBy", "supersededAt"])
def test_clearing_only_ONE_carrier_is_not_an_un_supersede(sdk, only):
    """The exemption is narrow: BOTH, or it is a resurrection with a stray edit.

    Driven on EACH half — pinning one and leaving the other unmeasured is how a
    requirement quietly becomes "one of the two".
    """
    node = _object(sdk, "live")
    sdk.update_entity(node, status="superseded", supersededBy="successor-v2",
                      supersededAt="2026-01-01T00:00:00Z")
    with pytest.raises(ValueError):
        sdk.update_entity(node, status="live", **{only: None})
    assert _stored(sdk, node)["status"] == "superseded"


@pytest.mark.parametrize("dead", ["archived", "deprecated", "retracted"])
def test_two_no_op_kwargs_do_not_unlock_a_row_that_was_never_superseded(sdk, dead):
    """⛔ The exemption is scoped PER ROW: the row being revived must hold a carrier.

    Unscoped, "both carriers cleared" let `archived -> active`, `deprecated ->
    live` and `retracted -> live` through — a bare resurrection wearing the
    un-supersede shape, on rows with no supersession to undo. And scoping it to
    the row SET was not enough either: see
    `test_a_siblings_carrier_does_not_unlock_a_carrierless_dead_row`, which is
    the same hole one node over.
    """
    node = _object(sdk, dead)
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status="active", supersededBy=None,
                          supersededAt=None)
    assert "update_entity refused the status transition" in str(exc.value)
    assert _stored(sdk, node)["status"] == dead


def test_a_siblings_carrier_does_not_unlock_a_carrierless_dead_row(sdk):
    """⛔ Per-ROW, not per-row-set: the write binds both rows, so the exemption must too.

    Measured on the set-scoped cut: an `archived` row holding NO carrier, sharing
    an id with a `superseded` sibling that DID hold one, was resurrected by the
    sibling's carrier — the un-supersede exemption travelled from the row that had
    something to clear to the row that had nothing. The write binds every node
    with the id, so this is the #7822 fan-out rule applied to the exemption, not
    only to the status verdict.
    """
    shared = "fanout-un-supersede"
    g = sdk._get_proj().g
    g.query("CREATE (:Object {id:$id, name:'fan-archived', status:'archived'})",
            params={"id": shared})
    g.query("CREATE (:Object {id:$id, name:'fan-super', status:'superseded', "
            "supersededBy:'v2', supersededAt:'2026-01-01T00:00:00Z'})",
            params={"id": shared})
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(shared, status="live", supersededBy=None,
                          supersededAt=None)
    assert "update_entity refused the status transition" in str(exc.value)
    after = g.query(
        "MATCH (n:Object {id:$id}) RETURN n.name, n.status ORDER BY n.name",
        params={"id": shared}).result_set
    assert [r[1] for r in after] == ["archived", "superseded"], \
        "the refusal must leave BOTH rows untouched: " + repr(after)
    g.query("MATCH (n:Object {id:$id}) DELETE n", params={"id": shared})


@pytest.mark.parametrize("target", [["live"], {"a": 1}])
def test_a_non_scalar_status_is_refused_with_the_guards_own_message(sdk, target):
    """⛔ A list/dict status must reach the REFUSAL, not raise `TypeError`.

    Measured: `_np_status in _np_terminal` is a membership test on a frozenset, so
    an unhashable target blew up with `TypeError: unhashable type` and the caller
    never saw the actionable message (the §4.3 citation + the performable repair)
    that every other refused target gets. The direction was never wrong — base
    let both shapes through on a dead row — but a traceback is not a refusal.
    """
    node = _object(sdk, "archived")
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status=target)
    assert "update_entity refused the status transition" in str(exc.value)
    assert _stored(sdk, node)["status"] == "archived"


# ── an ABSENT or non-string target on a dead row is still a resurrection ──

@pytest.mark.parametrize("target", [None, 123, True])
def test_removing_or_nonsense_status_on_a_dead_row_is_refused(sdk, target):
    """⛔ The first cut skipped non-`str` targets and this was the hole.

    `status=None` REMOVES the property, and §4.3's "an Object created without a
    status stores none and reads as `live`" then takes over: every read surface
    serves the row as current while `supersededBy` still names a successor
    (measured). `123` / `True` store as-is and read current the same way. So the
    question is "is the target in the DEAD set", never "is it a string".
    """
    node = _object(sdk, "superseded")
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status=target)
    assert "update_entity refused the status transition" in str(exc.value)
    assert _stored(sdk, node)["status"] == "superseded"


# ── the fan-out rule (#7822) applies to the non-Point arms too ──

def test_a_live_sibling_does_not_authorise_a_terminal_one(sdk):
    """The write binds EVERY node with the id, so the verdict is over all of them."""
    shared = "fanout-10371"
    g = sdk._get_proj().g
    g.query("CREATE (:Object {id:$id, name:'fan-live', status:'live'})",
            params={"id": shared})
    g.query("CREATE (:Object {id:$id, name:'fan-dead', status:'archived'})",
            params={"id": shared})
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(shared, status="live")
    assert "archived" in str(exc.value)
    statuses = g.query(
        "MATCH (n:Object {id:$id}) RETURN n.status ORDER BY n.status",
        params={"id": shared}).result_set
    assert [r[0] for r in statuses] == ["archived", "live"], \
        "the refusal must leave BOTH rows untouched"
    g.query("MATCH (n:Object {id:$id}) DELETE n", params={"id": shared})


# ── no leak onto the Point arm, which owns its own vocabulary and table ──

def test_a_point_with_an_object_word_is_refused_by_the_point_vocabulary(sdk):
    """`completed` is a legal Object word and NOT a Point word — each family
    validates against its own set, and the Point arm runs first."""
    pid = sdk.create_point("statement", "pt", dedup=False)["id"]
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(pid, status="completed")
    assert "Invalid status 'completed'" in str(exc.value)


def test_a_point_keeps_its_declared_transitions(sdk):
    """The sibling arms' guard must not tighten the Point arm it sits beside."""
    pid = sdk.create_point("statement", "pt-live", dedup=False)["id"]
    sdk.update_entity(pid, status="retracted")
    assert _stored(sdk, pid)["status"] == "retracted"
