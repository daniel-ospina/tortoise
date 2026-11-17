"""#10371 — ``update_entity``'s Object and Subject arms had no lifecycle guard,
so a terminal row was resurrected by a prop edit.

Measured on ``main`` at ``6916a6cd89`` (2026-11-13), embedded engine, throwaway
nodes, deleted after:

    update_knowledge(<archived Object>,  status='live') -> ACCEPTED  RESURRECTED
    update_knowledge(<archived Subject>, status='live') -> ACCEPTED  RESURRECTED

That is the #9649 defect class on two more labels. #12325 closed
``update_entity``'s ``:Point`` arm, and ``_assert_lifecycle_guard`` is
``:Point``-hardcoded (``point_id``, ``MATCH (n:Point …)``), so the other two arms
never reached it.

⛔ WHAT THIS GUARD IS NOT: A VOCABULARY — AND #11824 S0 IS WHERE THE VOCABULARY
WENT. A closed ``OBJECT_STATUS_VALUES`` was drafted, measured, and DROPPED before
it shipped (it is not in the tree): ONTOLOGY §4.3 (Object) said, as it then
stood, that *"pack-declared kinds validate against their own declared words"*,
and `packs/venture/manifest.yaml` declares ``active``, ``prospective``,
``invested``, ``written-off``, ``prioritized``, ``paused``, ``deprioritized``,
``prospect``, ``applied``, ``awarded``, ``closed`` for its Object kinds, while the
connector vocabulary adds ``open``/``closed``/``active``/``pending``. One shared
set refused all of them, and reddened
``tests/test_9206_apply_first_lane.py`` (which writes ``status='active'`` on an
Object through this very door). The objection was correct; the ANSWER was not to
widen ``status`` — and it was certainly not to leave the door unvalidated, which
is what happened instead, and is #10371's original complaint. #11824 S0 answered
it by moving a pack's own words onto the POSITION axis: they are now the kind's
declared PIPELINE STAGES (`packs/venture/manifest.yaml`'s ``pipeline:`` blocks;
design §3.2 and §4), so the LIFECYCLE axis closes at four words — ``draft``,
``live``, ``deprecated``, ``superseded`` — for EVERY knowledge kind, and ONE
closed set is now correct everywhere. ``tortoise/lifecycle_vocabulary.py`` is
that set's one home, and it is the write doors that consult it, not this guard.

So this guard asks the TRANSITION question only, and the division is deliberate:
"may this ROW leave a dead state?" is a different question from "is this WORD a
lifecycle word?", and only the first one stops a resurrection. What it stops is
narrower and more useful than "an undeclared word was written": any word the
family's read vocabulary does not treat as DEAD reads as CURRENT, so a dead row
departing for any of them is the harm. The guard therefore never inspects the
TARGET word's place in the vocabulary — only the family's dead set.

What this suite pins:

* a terminal Object or Subject refuses a transition that makes it CURRENT;
* the dead set is the family's own recorded one
  (``commit_ops.OBJECT_TERMINAL_STATUSES`` / ``SUBJECT_TERMINAL_STATUSES``), not a
  literal — #11824 S0 narrowed the WRITE door and left these READ sets alone
  (#2977: the net stays wider than the door, so a stored legacy word still reads
  as dead);
* a pack/connector word (``active``, ``prospective``, ``invested``,
  ``prioritized``, ``paused``, ``open``, ``closed``, ``pending``) is now REFUSED
  as a ``status`` — the hole this module's first version recorded as #10371's
  complaint is CLOSED — and the refusal is performable: a POSITION word names
  ``advance_stage``, anything else names the four lifecycle words. The
  constructive half is pinned beside it, because refusing a word is only safe if
  the pack still DECLARES it: every one of those words is a pipeline stage id of
  a loaded pack;
* the #1423 SUBJECT VOCABULARY guard survives as the SHARED door: an undeclared
  word (``dead``, ``banana``) is still refused on a Subject — and, since #11824
  S0, on an Object too — while ``retracted``, undeclared before that change, is
  now a DECLARED SYNONYM of ``deprecated`` and stores as that word. A first cut
  of #10371 REPLACED the #1423 block instead of extending it and silently
  reopened the hole; the pins below are the regression guard;
* DEAD -> DEAD stays legal, the per-family delta from Points (§5 declares every
  terminal Point with an empty outgoing set; an Object's dead words are
  bookkeeping, and ``tests/test_11826_object_supersession_instrument.py`` moves
  ``superseded -> archived`` and asserts what that does);
* an ENTAILED status beside a real edit is not a transition (the generic tenant
  read-modify-write), and content/metadata revisions on a dead row stay legal;
* the verdict is over EVERY row the write binds (#7822's fan-out rule, applied to
  the non-Point arms): a live sibling must not authorise a write that also lands
  on the terminal one;
* the refusal's repair is PERFORMABLE — it does not name ``supersede_knowledge()``, which is
  ``:Point``-only and raises ``No point <id>`` for these labels (the AX-rule-2
  defect #10183 records; the missing SDK door is #12592);
* nothing leaks onto the Point arm, which owns its own vocabulary and table.

Runnable (docker lane):

    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_t10371' \\
        uv run pytest tests/test_10371_nonpoint_lifecycle_guard.py -q
"""
from __future__ import annotations

import pytest

from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES, SUBJECT_TERMINAL_STATUSES
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
    """`{"label", "status"}` for the row, or `{}` when it is gone.

    `.result_set` hands back positional LISTS (the driver does not project aliases
    into a mapping), so the pair is labelled here rather than indexed by name.
    """
    rows = s._get_proj().g.query(
        "MATCH (n {id:$id}) RETURN labels(n)[0], n.status",
        params={"id": node_id}).result_set
    if not rows:
        return {}
    return {"label": rows[0][0], "status": rows[0][1]}


#: #11824 S0: ``archived`` and ``retracted`` are DECLARED SYNONYMS of
#: ``deprecated`` at the write door, so a row created with ``status='archived'``
#: STORES ``deprecated`` (silently — AX §1: a synonym supplies no meaning we did
#: not already have). Every "how dead is this row, and which word does it hold"
#: assertion below reads the STORED word, never the spelling the caller wrote.
DEAD_WORD = "deprecated"

#: The STORED word a caller's dead spelling lands as (``lifecycle_vocabulary``'s
#: synonym table). ``superseded`` is its own word: only the retirement synonyms
#: collapse onto ``deprecated``.
_DEAD_SPELLINGS = {
    "archived": DEAD_WORD,
    "retracted": DEAD_WORD,
    "deprecated": DEAD_WORD,
    "superseded": "superseded",
}


# ── the dead set is the family's RECORDED one, not a literal in the guard ──

def test_the_guard_uses_the_family_terminal_sets_that_already_exist():
    """A literal here would be a second declaration of "dead" for these labels.

    #11824 S0 narrowed the WRITE vocabulary to four words and left these READ
    sets alone: ``archived`` / ``retracted`` are still DEAD for a STORED row (a
    pre-fix writer, a raw Cypher producer, a JSONL replay of either), even though
    neither is a word the door will mint any more. The read set is the net, the
    write set is the door, and the net is wider on purpose (#2977).
    """
    assert "superseded" in OBJECT_TERMINAL_STATUSES
    assert "archived" in OBJECT_TERMINAL_STATUSES
    assert "deprecated" in OBJECT_TERMINAL_STATUSES
    assert "superseded" in SUBJECT_TERMINAL_STATUSES
    assert "archived" in SUBJECT_TERMINAL_STATUSES


def test_completed_and_in_progress_are_not_dead_for_an_object():
    """A connector work item moves between them; neither is a retirement.

    Still true after #11824 S0, and for a sharper reason: they are not lifecycle
    words at all any more — they are stage ids of
    ``tortoise.pipeline.CORE_WORK_ITEM_PIPELINE`` (the POSITION axis), and a
    position is never an ending. A DEAD set that contained one would retire a row
    that is merely in progress.
    """
    assert not ({"completed", "in_progress"} & OBJECT_TERMINAL_STATUSES)


# ── the transition half: DEAD -> CURRENT is refused, on both families ──

@pytest.mark.parametrize("maker,label", [(_object, "Object"), (_subject, "Subject")])
@pytest.mark.parametrize("dead", ["archived", "superseded", "deprecated"])
def test_a_terminal_row_is_not_resurrected_by_a_prop_edit(sdk, maker, label, dead):
    # #11824 S0: the message names the STORED word, which is not the caller's
    # spelling for a synonym — `archived` is held as `deprecated`.
    stored = _DEAD_SPELLINGS[dead]
    node = maker(sdk, dead)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="live")
    message = str(exc.value)
    # AX rule 2 (#10183): that it refused, on what, and what to do instead.
    assert "update_entity refused the status transition" in message
    assert f"{stored!r} -> 'live'" in message
    assert f"on {label} {node!r}" in message
    assert _stored(sdk, node)["status"] == stored, "a refused write must store nothing"


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
        sdk.update_knowledge(obj, status="live")
    assert "ONTOLOGY §4.3" in str(obj_exc.value)
    assert "ONTOLOGY §4.1" not in str(obj_exc.value), \
        "§4.1 is the Point family — the Object refusal must not cite it"
    with pytest.raises(ValueError) as subj_exc:
        sdk.update_knowledge(subj, status="live")
    assert "ONTOLOGY §4.2" in str(subj_exc.value)


def test_the_named_repair_is_performable(sdk):
    """⛔ The message must NOT offer `supersede_knowledge()` as the way back.

    `TortoiseSDK.supersede` now DISPATCHES for an Object/Subject (#12592) — the
    missing verb this test used to name — but a supersession is written from the
    LIVE side: that door runs the family lifecycle guard and refuses a TERMINAL
    source (#2498, terminalization is one-way). So it is still not the repair for
    a dead row, and naming it here would be an unperformable prescription — the
    AX-rule-2 defect #10183 exists to stop. The route that exists is minting the
    replacement, and that is what the message names.
    """
    node = _object(sdk, "archived")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="live")
    message = str(exc.value)
    assert "apply_supersessions" in message
    assert "supersede_knowledge()" not in message, \
        "that door refuses a terminal source — do not prescribe it"
    # And the prescription that IS named really exists:
    from tortoise.commit_ops import apply_supersessions  # noqa: F401
    # ⛔ #12592: the door EXISTS for this label now, and still refuses the dead
    # row — the refusal is the one-way rule, never `No point` (which is what this
    # assertion pinned before the family path existed).
    with pytest.raises(ValueError, match="already terminal"):
        sdk.supersede_knowledge(node, "some-successor")


# ── the vocabulary door (#11824 S0): these words are REFUSED as a `status` ──
#
# ⛔ INVERTED. This block used to pin the OPPOSITE — that a pack/connector word
# was ACCEPTED as an Object's `status`, as the anti-regression for the closed-set
# mistake. #11824 S0 closed that hole: the lifecycle axis is four words for every
# knowledge kind, and a pack's own words are its PIPELINE STAGES. The invariant
# that DOES survive — and is pinned by the constructive test at the end of this
# block — is that refusing them on the lifecycle axis cost no pack its
# vocabulary: each is still declared, as a stage id.

PACK_AND_CONNECTOR_WORDS = (
    # `packs/venture/manifest.yaml` Object-kind lifecycles
    "active", "prospective", "invested", "prioritized", "paused",
    "deprioritized", "prospect", "applied", "awarded", "closed",
    # `tortoise/status_folds.py`'s shared connector vocabulary
    "open", "pending",
)

#: The words in the list above that ``lifecycle_vocabulary`` refuses as a
#: **position** word — it carries a per-word repair naming ``advance_stage``,
#: because position lives on the stage axis (design §3.2/§4) and each word names a
#: different place in it.
POSITION_WORDS = ("active", "paused")

#: Everything else gets the VOCABULARY refusal, whose third part names the four
#: words. Derived by exclusion from the one list, so the two parametrizations
#: PARTITION it: no word can be silently dropped from both, and no word is
#: asserted against a branch it does not take.
NON_LIFECYCLE_WORDS = tuple(
    w for w in PACK_AND_CONNECTOR_WORDS if w not in POSITION_WORDS)


@pytest.mark.parametrize("word", POSITION_WORDS)
def test_a_position_word_is_refused_as_a_status_with_a_stage_repair(sdk, word):
    """#11824 S0: a POSITION word is refused, and told where it belongs.

    ``active`` / ``paused`` name a place in the PIPELINE, not a lifecycle state,
    so the write is refused by the vocabulary door and the refusal is
    PERFORMABLE (AX rule 2, #10183): it names ``advance_stage``, the door that
    moves a row between stages, and says what the ``status`` field keeps. The
    row is untouched — a refused write stores nothing.
    """
    node = _object(sdk, "live")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status=word)
    message = str(exc.value)
    assert message.startswith(f"status {word!r} was refused")
    assert "advance_stage" in message, \
        "a position word must be told to advance a stage, not to pick another word"
    assert _stored(sdk, node)["status"] == "live"


def test_the_outdated_repair_names_BOTH_words_and_the_supersede_route(sdk):
    """`outdated` is refused because it names a CHOICE, and the choice is named.

    The generic refusal cannot stand in for this one: it already contains the
    word `outdated` (``_render`` interpolates the refused value) and already
    names `superseded` (``_ACCEPTED_FORMS`` lists the vocabulary), so a
    ``match="outdated"`` / ``match="deprecated"`` row stays GREEN if the
    dedicated arm is deleted — the repair silently degrades to "pass a lifecycle
    word", which picks neither meaning for the caller. The discriminating text
    is the ROUTE: ``supersededBy`` (an Object) or ``supersede_point`` (a Point).

    Both doors are pinned: the module question and the write the caller makes.
    """
    import re

    from tortoise.lifecycle_vocabulary import read_caller_status

    with pytest.raises(ValueError) as exc:
        read_caller_status("outdated")
    assert re.search(r"supersededBy|supersede_point", str(exc.value)), (
        "the refusal must name the supersede ROUTE, not only the two words — "
        f"otherwise it cannot be acted on: {exc.value}")

    node = _object(sdk, "live")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="outdated")
    message = str(exc.value)
    assert "deprecated" in message and "superseded" in message, (
        f"`outdated` must be answered with BOTH words: {message}")
    assert re.search(r"supersededBy|supersede_point", message), (
        f"the write refusal must name the supersede route: {message}")
    assert _stored(sdk, node)["status"] == "live", "a refused write stores nothing"


@pytest.mark.parametrize("word", NON_LIFECYCLE_WORDS)
def test_a_pack_or_connector_word_is_now_refused_as_a_status(sdk, word):
    """#11824 S0: the pack/connector word is REFUSED, with the vocabulary named.

    This is the inversion of ``test_a_pack_or_connector_word_is_not_refused_on_
    an_object``: the old pin asserted each of these STORED on a live Object, on
    the ground that a pack kind validates against its own declared words. That is
    still true — but a pack's declared words are its pipeline STAGES now, so
    ``status`` is not the field they belong in.

    ⚠️ Being a stage id is NOT what makes a word safe on this axis: ``open`` and
    ``pending`` are stage ids of ``venture:actionItem`` / ``venture:tranche`` and
    are ALSO connector vocabulary (``status_folds.SHARED_CONNECTOR_STATUS_WORDS``)
    — they are refused here because they are not lifecycle words, full stop.
    """
    node = _object(sdk, "live")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status=word)
    message = str(exc.value)
    assert message.startswith(f"status {word!r} was refused")
    assert "not a lifecycle word for :Object" in message
    assert "draft" in message and "superseded" in message, \
        "the third part must name the vocabulary the caller may use instead"
    assert _stored(sdk, node)["status"] == "live", "a refused write stores nothing"


@pytest.mark.parametrize("word", PACK_AND_CONNECTOR_WORDS)
def test_a_pack_word_on_a_DEAD_row_is_refused_by_the_vocabulary_door(sdk, word):
    """Refused either way — but by the VOCABULARY door since #11824 S0.

    The old pin asserted the TRANSITION refusal ("update_entity refused the
    status transition") on an ``'archived'`` row. The vocabulary check now runs
    FIRST, on any mounted row, so the caller is told the actionable thing —
    "this is not a lifecycle word" — rather than a transition verdict about a
    word that was never a candidate. Pinned so a later reordering cannot quietly
    send a caller down the transition message for a spelling problem.

    The row is asserted DEAD by MEMBERSHIP, not by the literal ``'archived'``:
    #11824 S0 stores that synonym as ``deprecated``.
    """
    node = _object(sdk, "archived")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status=word)
    message = str(exc.value)
    assert message.startswith(f"status {word!r} was refused"), \
        "the VOCABULARY door refuses first — not this module's transition guard"
    assert "update_entity refused the status transition" not in message
    assert _stored(sdk, node)["status"] in OBJECT_TERMINAL_STATUSES, \
        "a refused write must leave the row as dead as it found it"


#: Words that are DECLARED, but on the STAGE axis — the constructive half of the
#: inversion above. Measured: every word in ``PACK_AND_CONNECTOR_WORDS`` is a
#: ``pipeline.stages[].id`` of a kind in ``packs/`` (``open``/``pending`` via
#: ``venture:actionItem``/``venture:tranche``), so nothing had to be excluded —
#: and the assertion is written to REPORT anything that stops being declared
#: rather than to assume the set is empty.

def _declared_stage_ids() -> set:
    from pathlib import Path

    from tortoise.pack_registry import PackRegistry

    reg = PackRegistry(Path(__file__).resolve().parent.parent / "packs")
    reg.load_all()
    stage_ids: set = set()
    for pack in reg.packs.values():
        for kind_def in pack.kind_defs.values():
            pipeline = (kind_def or {}).get("pipeline") or {}
            for stage in pipeline.get("stages") or ():
                if stage.get("id"):
                    stage_ids.add(stage["id"])
    return stage_ids


def test_every_pack_word_is_still_declared_as_a_pipeline_stage():
    """The constructive half: refusing a word must not DELETE the declaration.

    #11824 S0 did not retire a pack's vocabulary, it moved which AXIS it belongs
    to (design §3.2/§4). So the fix for a refused word is visible in the data,
    not just in a message: the word is still declared, as a stage id, by the kind
    that owns it. Asserted over the loaded packs rather than a literal list, so a
    pack that drops a stage (and leaves a caller with a word that is refused on
    BOTH axes) reds this test instead of silently becoming unwritable.
    """
    declared = _declared_stage_ids()
    missing = sorted(w for w in PACK_AND_CONNECTOR_WORDS if w not in declared)
    assert not missing, (
        f"{missing} is refused as a `status` AND is no longer a declared stage "
        f"id of any kind in packs/ — the caller has no writable home for it"
    )


#: ``retracted`` used to be one of the undeclared words pinned here. #11824 S0
#: makes it a DECLARED SYNONYM of ``deprecated``, so it splits out below: the
#: #1423 invariant (an undeclared word is refused on a Subject) is kept, and the
#: synonym is pinned as what it now is rather than dropped.


def test_a_subject_stores_a_declared_synonym_as_deprecated(sdk):
    """``retracted`` is no longer undeclared on a Subject — it is a synonym.

    #11824 S0's synonym table makes ``retracted`` mean *ended, nothing replaced
    it*, which is ``deprecated``, so the write succeeds and stores the canonical
    word — SILENTLY (AX §1: a synonym supplies no meaning we did not already
    have, so there is nothing to echo back).
    """
    node = _subject(sdk, "live")
    sdk.update_knowledge(node, status="retracted")
    assert _stored(sdk, node)["status"] == DEAD_WORD


@pytest.mark.parametrize("word", ["dead", "banana"])
def test_a_subject_still_refuses_an_undeclared_word(sdk, word):
    """#1423's guard, which this change must not and does not remove.

    ``dead`` and ``banana`` are not lifecycle words for ANY knowledge kind
    (#11824 S0). An unknown word is not "dead" either, and the Subject read
    vocabulary is a denylist of KNOWN terminal states, so an unknown word reads
    as CURRENT — which is why the vocabulary door is the only place it can be
    caught, and why it now catches it on an Object as well as a Subject.
    A first cut of #10371 dropped this block and these rows went green.
    """
    node = _subject(sdk, "live")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status=word)
    message = str(exc.value)
    assert message.startswith(f"status {word!r} was refused")
    assert "not a lifecycle word for :Subject" in message
    assert _stored(sdk, node)["status"] == "live"


def test_an_undeclared_word_is_now_refused_the_hole_is_closed(sdk):
    """⛔ INVERTED by #11824 S0: ``banana`` no longer stores on an Object.

    The invariant this test pinned is GONE, deliberately: it asserted that an
    undeclared word still STORED on an Object, because closing that needed
    per-kind resolution (a pack kind's own words, §4.3 Object) and a shared set
    would have refused real ones. That was #10371's ORIGINAL complaint, and it
    stayed open with this pin guarding the hole.

    The route that arrived is not a per-kind STATUS list — it is the STAGE axis,
    which is where a pack's own words live now, so the lifecycle axis closes at
    four words for every knowledge kind and the hole is closed. If this test ever
    fails, an undeclared word is being STORED again.

    What is still true, and is why the closure is safe: the READ sets keep the
    legacy words (``archived`` / ``retracted`` remain in
    ``OBJECT_TERMINAL_STATUSES``), so a row holding one from a pre-fix writer
    still reads as dead (#2977 — the net is wider than the door).

    ⚠️ A caller that wrote a legacy word through this door must move with it:
    ``tests/test_11926_derived_invalidation.py`` writes
    ``update_entity(<Object>, status='outdated')``, and ``outdated`` is now
    REFUSED because it names a CHOICE between ``deprecated`` and ``superseded``.
    """
    node = _object(sdk, "live")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="banana")
    assert "not a lifecycle word for :Object" in str(exc.value)
    assert _stored(sdk, node)["status"] == "live"


# ── SCOPE: an entailed status is not a transition; content stays revisable ──

@pytest.mark.parametrize("maker", [_object, _subject])
def test_an_entailed_status_beside_a_real_edit_is_accepted(sdk, maker):
    """The read-modify-write shape a generic tenant PUT actually sends.

    #11824 S0: the caller still writes ``archived``; the door normalises it to
    ``deprecated`` on the way in, so the echo is ENTAILED (stored == target) and
    the content edit is not read as a transition.
    """
    node = maker(sdk, "archived")
    assert sdk.update_knowledge(node, status="archived", description="a note")
    assert _stored(sdk, node)["status"] == DEAD_WORD


@pytest.mark.parametrize("maker", [_object, _subject])
def test_a_content_revision_without_a_status_is_untouched(sdk, maker):
    """The block keys on a supplied `status` only — it must not gate content."""
    node = maker(sdk, "archived")
    assert sdk.update_knowledge(node, description="revision only")
    assert _stored(sdk, node)["status"] == DEAD_WORD


# ── DEAD -> DEAD is legal: the per-family delta from Points ──

def test_a_dead_row_may_be_re_filed_between_dead_words(sdk):
    """``superseded -> deprecated`` STAYS LEGAL, the delta from Points.

    ⛔ Pinned because it is the opposite of the Point rule and the difference is
    load-bearing. §5 declares every terminal POINT with an empty outgoing set
    (``_ALLOWED_TRANSITIONS``): a dead claim is answered only by minting a
    successor. An Object's dead words are bookkeeping, and `main` moves them:
    ``tests/test_11826_object_supersession_instrument.py`` takes a `superseded`
    Object to `archived` and then asserts that archiving SILENCES the
    ``superseded_object_active_edges`` finding while (per `audit.py`'s own
    docstring) NOT clearing the deficit — so nothing there offers archiving as a
    repair, and refusing the move would red that module.

    ⚠️ The origin is ``superseded`` and not ``archived`` (#11824 S0): ``archived``
    is a SYNONYM stored as ``deprecated``, so an ``archived -> deprecated`` write
    is now an ENTAILED no-op and would pin nothing. `main`'s own move —
    ``superseded -> archived`` — is this transition, spelled through the synonym.
    """
    node = _object(sdk, "superseded")
    sdk.update_knowledge(node, status="deprecated")
    assert _stored(sdk, node)["status"] == DEAD_WORD
    sdk.update_knowledge(node, status="archived")   # the same move, as a synonym
    assert _stored(sdk, node)["status"] == DEAD_WORD
    with pytest.raises(ValueError):
        sdk.update_knowledge(node, status="live")   # ...and it is still dead


def test_a_live_row_is_retired_by_a_status_write(sdk):
    """CURRENT -> DEAD is a retirement, not a resurrection; it stays legal.

    #11824 S0: ``archived`` is a DECLARED SYNONYM, so the retirement is STORED as
    ``deprecated`` — the write is accepted, the spelling is not what lands.
    """
    node = _object(sdk, "live")
    sdk.update_knowledge(node, status="archived")
    assert _stored(sdk, node)["status"] == DEAD_WORD


def test_an_object_moves_between_the_two_non_terminal_lifecycle_words(sdk):
    """⛔ ``completed`` / ``in_progress`` are NOT status words since #11824 S0.

    They are the POSITION axis (``CORE_WORK_ITEM_PIPELINE``'s stage ids, design
    §3.2): moving a row between positions is ``advance_stage(...)``, never a
    ``status`` write, and this door refuses them with that repair named (see the
    ``POSITION_WORDS`` pins above). What a ``status`` write still moves a row
    between is the two NON-TERMINAL lifecycle words — a live row can be demoted
    to ``draft`` and promoted back, which is the read-modify-write capability a
    terminal-only check must not break.
    """
    node = _object(sdk, "live")
    sdk.update_knowledge(node, status="draft")
    assert _stored(sdk, node)["status"] == "draft"
    sdk.update_knowledge(node, status="live")
    assert _stored(sdk, node)["status"] == "live"


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
        sdk.update_knowledge(shared, status="live")
    assert "archived" in str(exc.value)
    statuses = g.query(
        "MATCH (n:Object {id:$id}) RETURN n.status ORDER BY n.status",
        params={"id": shared}).result_set
    assert [r[0] for r in statuses] == ["archived", "live"], \
        "the refusal must leave BOTH rows untouched"
    g.query("MATCH (n:Object {id:$id}) DELETE n", params={"id": shared})


# ── #12786: the ONE recoverable dead word, under the record's condition ──
#
# `superseded` is not a retirement: §4.3 (Object) says *"`superseded` means
# replaced: it names the successor; a retirement with no successor is
# `archived`; `deprecated` is an independent terminal state"*. It is therefore
# the one dead word whose CAUSE can be withdrawn — and `tortoise/cache_projection`
# .py's NOT MONOTONE bullet measures the withdrawal end-to-end (the carriers go
# NULL, and a rebuild then derives nothing). The door admits it under exactly
# that shape and no wider; the pins below carry BOTH halves, because a permission
# without its boundary is how a state machine becomes permissive.

def _superseded_with_carriers(s, maker, successor="v2") -> str:
    """A `superseded` row carrying the successor pair — what a real fold writes."""
    node = maker(s, "superseded")
    s.update_knowledge(node, supersededBy=successor,
                    supersededAt="2026-01-01T00:00:00Z")
    return node


def _carriers(s, node) -> tuple:
    rows = s._get_proj().g.query(
        "MATCH (n {id:$id}) RETURN n.supersededBy, n.supersededAt",
        params={"id": node}).result_set
    return tuple(rows[0]) if rows else ()


@pytest.mark.parametrize("maker,label", [(_object, "Object"), (_subject, "Subject")])
def test_the_record_carried_un_supersede_is_admitted(sdk, maker, label):
    """Clearing a supersession IS writable — that is what the record can express.

    `tests/test_status_projection.py::TestLifecycleCarrierCoherence::`
    `test_a_coherent_un_supersede_lifts_the_row_and_a_status_only_write_cannot`
    (cited as `test_clearing_a_supersession_is_still_writable` before #12790/#12882
    renamed it; the CLASS is part of the node path or pytest collects nothing)
    caught this guard refusing the shape `cache_projection`'s NOT MONOTONE bullet
    documents and measures. A state machine that cannot un-supersede has a
    user-visible shape: a fact superseded BY MISTAKE cannot be restored at all —
    clearing the carriers alone leaves `status='superseded'`, so every read
    surface still drops the row.
    """
    node = _superseded_with_carriers(sdk, maker)
    assert _carriers(sdk, node) == ("v2", "2026-01-01T00:00:00Z")
    sdk.update_knowledge(node, status="live", supersededBy=None, supersededAt=None)
    assert _stored(sdk, node)["status"] == "live"
    assert _carriers(sdk, node) == (None, None), \
        "the clear must reach the properties the cache derives status from"


@pytest.mark.parametrize("maker", [_object, _subject])
def test_the_snake_spelling_cannot_un_supersede_yet(sdk, maker):
    """⛔ The SNAKE clear does not reach the node property — #12782, still open.

    This door writes `SET n += $props` literally, so `superseded_by=None` clears a
    SHADOW property the fold never reads and leaves `supersededBy` intact
    (measured). Admitting that spelling would resurrect the row while the cache
    still derived `'superseded'` — the #9974/#10192 divergence class rather than a
    recovery — so it stays refused until #12782 makes the snake clear reach the
    node property. The refusal is the conservative direction: nothing is written.

    ⛔ Do not "fix" this pin by widening the allowance to `snake` spellings. The
    asymmetry is deliberate — a REFUSAL must cover every channel that can set the
    column (#10192's review P1), an ALLOWANCE must be true of the write that
    actually happens.
    """
    node = _superseded_with_carriers(sdk, maker)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="live", superseded_by=None,
                          superseded_at=None)
    assert "'superseded' -> 'live'" in str(exc.value)
    assert _stored(sdk, node)["status"] == "superseded"
    assert _carriers(sdk, node) == ("v2", "2026-01-01T00:00:00Z")


def test_a_status_only_live_on_a_carried_supersession_is_still_refused(sdk):
    """The OTHER half: `live` alone leaves the carriers set, so it is not a clear.

    The record derives `'superseded'` from `superseded_by`/`superseded_at`
    (`public.object_current_status`), so a status-only write would leave the
    record reporting a terminal row against a LIVE graph — the #9974/#10192
    divergence class, not a recovery. Its refusal also names the performable
    repair (AX rule 2, #10183), which is the shape asserted above.
    """
    node = _superseded_with_carriers(sdk, _object)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="live")
    message = str(exc.value)
    assert "'superseded' -> 'live'" in message
    assert "supersededBy=None" in message, \
        "the refusal must name the coherent un-supersede, not just refuse"
    assert _stored(sdk, node)["status"] == "superseded"
    assert _carriers(sdk, node) == ("v2", "2026-01-01T00:00:00Z"), \
        "a refused write must store nothing"


@pytest.mark.parametrize("cleared", ["supersededBy", "supersededAt"])
def test_a_PARTIAL_carrier_clear_is_still_refused(sdk, cleared):
    """One carrier left set means the record can still read `'superseded'`.

    The record's fold is `superseded_by IS NOT NULL OR superseded_at IS NOT NULL`,
    so clearing HALF of the pair is not an un-supersede — it is a graph the
    record contradicts. Admitting it would be the permissive reading of this
    permission.
    """
    node = _superseded_with_carriers(sdk, _object)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="live", **{cleared: None})
    assert "'superseded' -> 'live'" in str(exc.value)
    assert _stored(sdk, node)["status"] == "superseded"


def test_an_empty_string_carrier_is_not_a_clear(sdk):
    """`''` is NOT NULL to the record's view, so it keeps the row terminal.

    ⛔ AND THE PIN MUST NAME WHICH DOOR REFUSED (review cycle 2, P2). Measured:
    this write is refused by the CARRIER door, before the transition guard ever
    runs — so asserting only `pytest.raises(ValueError)` cannot see a widening of
    the transition allowance's own falsy rule (`value is None`, not truthiness).
    The allowance's branch is defence in depth rather than the live refusal here
    (a non-`None` carrier with `status='live'` never reaches it — the carrier door
    refuses first), so the assertion pins the door that DOES decide it.
    """
    node = _superseded_with_carriers(sdk, _object)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="live", supersededBy="",
                          supersededAt=None)
    assert "lifecycle CARRIER" in str(exc.value), \
        "`''` is a carrier, not a clear — the CARRIER door is what refuses it"
    assert _stored(sdk, node)["status"] == "superseded"
    assert _carriers(sdk, node) == ("v2", "2026-01-01T00:00:00Z"), \
        "a refused write must store nothing"


def test_a_carrier_less_supersession_is_not_un_superseded(sdk):
    """Nothing to withdraw — the mirror shape #10192 left OUT of scope.

    Pinned so the permission above cannot be read as "`superseded` is simply not
    terminal": a row that never carried a successor has no record-expressible
    supersession to clear, and admitting it here would silently widen the
    transition table by one more shape.

    ⛔ And the refusal must NOT prescribe the clear here (review cycle 1, P1):
    `update_knowledge(status='live', supersededBy=None, supersededAt=None)` is
    REFUSED on this row — there is nothing carried to clear — so offering it
    would be an unperformable prescription, the AX-rule-2 defect #10183.
    """
    node = _object(sdk, "superseded")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="live")
    message = str(exc.value)
    assert "'superseded' -> 'live'" in message
    assert "supersededBy=None" not in message, \
        "that call is refused on a carrier-less row — do not prescribe it"
    assert _stored(sdk, node)["status"] == "superseded"


def test_a_mixed_dead_set_gets_no_un_supersede_hint(sdk):
    """Two dead words bound at once: the prescribed clear is refused there too."""
    shared = "fanout-12786-mixed"
    g = sdk._get_proj().g
    g.query("CREATE (:Object {id:$id, name:'mx-sup', status:'superseded', "
            "supersededBy:'v2'})", params={"id": shared})
    g.query("CREATE (:Object {id:$id, name:'mx-arch', status:'archived'})",
            params={"id": shared})
    try:
        with pytest.raises(ValueError) as exc:
            sdk.update_knowledge(shared, status="live", supersededBy=None,
                              supersededAt=None)
        assert "supersededBy=None" not in str(exc.value), \
            "the clear is refused on a mixed dead set — do not prescribe it"
    finally:
        g.query("MATCH (n:Object {id:$id}) DELETE n", params={"id": shared})


def _alias_key(content: str, rendering: str):
    """A `str` subclass whose CONTENT and `__str__` name different properties.

    `tortoise/live.py`'s two-renderer contract: the live write files by
    `str(key)`, the journal keeps the content. Both round-2 P1s live in that gap.
    """

    class _Alias(str):
        __hash__ = str.__hash__

        def __new__(cls, value):
            obj = str.__new__(cls, value)
            obj._rendering = rendering
            return obj

        def __str__(self) -> str:
            return self._rendering

    return _Alias(content)


@pytest.mark.parametrize("content,rendering", [
    ("supersededBy", "supersededAt"),
    ("supersededAt", "supersededBy"),
])
def test_a_key_whose_renderings_name_DIFFERENT_carriers_is_refused(
        sdk, content, rendering):
    """⛔ Same-carrier, not merely "both are carrier NAMES" (round 2, P1).

    Membership of the declared-name SET was satisfied by a key whose content names
    one carrier and whose `__str__` names the other — so the live write cleared
    one property and the journal cleared the other. With the row holding only the
    journal-side carrier, that write was ADMITTED (measured: `superseded` ->
    `live`, both carriers NULL in the graph) while the record kept its own carrier
    set and still derived `'superseded'`: a PARTIAL clear in record terms, which
    is the shape `test_a_PARTIAL_carrier_clear_is_still_refused` claims to block.
    Both directions are pinned because the ordering decides which property each
    channel touches.
    """
    node = _object(sdk, "superseded")
    # The carrier the LIVE write names is the one whose clear would be
    # recorded under the OTHER property — that is the admitted shape.
    sdk.update_knowledge(node, **{rendering: "2026-01-01T00:00:00Z"})
    held = _carriers(sdk, node)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, **{"status": "live",
                                   _alias_key(content, rendering): None})
    assert "'superseded' -> 'live'" in str(exc.value)
    assert _stored(sdk, node)["status"] == "superseded"
    assert _carriers(sdk, node) == held, \
        "a refused write must clear nothing"


@pytest.mark.parametrize("order", ["shadow-first", "plain-first"])
def test_two_keys_rendering_as_status_with_different_words_is_refused(sdk, order):
    """⛔ No knowable landing word ⇒ refuse (round 2, P1).

    `lifecycle_props_by_rendering` reads ONE value (first-wins over the union)
    while the live map write applies the LAST. With a key whose content is a dead
    word and whose `__str__` is `status`, `{shadow: 'superseded', 'status':
    'live'}` was read as dead->dead (legal, guard `continue`d) and LANDED `live`
    on a `superseded` row — a resurrection past #12745's pin, with the carriers
    left intact (measured, and reproduced on `origin/main`). Both orderings are
    pinned because the accepted one was order-dependent.
    """
    node = _superseded_with_carriers(sdk, _object)
    shadow = _alias_key("superseded", "status")
    props = ({shadow: "superseded", "status": "live"}
             if order == "shadow-first"
             else {"status": "live", shadow: "superseded"})
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, **props)
    assert "CONFLICTING status write" in str(exc.value), \
        "the refusal must name the ambiguity, not report a transition verdict"
    assert _stored(sdk, node)["status"] == "superseded"
    assert _carriers(sdk, node) == ("v2", "2026-01-01T00:00:00Z"), \
        "a refused write must store nothing"


@pytest.mark.parametrize("maker,label,offers_pack_word", [
    (_object, "Object", True),
    (_subject, "Subject", False),
])
def test_only_the_object_hint_offers_a_pack_word(sdk, maker, label,
                                                offers_pack_word):
    """AX rule 2: a prescription must be PERFORMABLE on this family (cycle 2, P2).

    Pack kinds declare Object words (`packs/venture/manifest.yaml`: `active`,
    `prospective`, `invested`, …), so on an Object "this kind's own current word"
    is a real alternative to `'live'` — as a PIPELINE STAGE, since #11824 S0
    moved a pack's words onto the position axis. A :Subject has no such option:
    its vocabulary is the SAME closed four words as every other kind
    (`commit_ops.SUBJECT_STATUS_VALUES`; the vocabulary door refuses `active` as
    a position word, asserted below), so offering it there prescribes a call that
    raises. The `status='live'` form is performable for BOTH families and is
    always offered.
    """
    node = _superseded_with_carriers(sdk, maker)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="live")
    message = str(exc.value)
    assert "supersededBy=None" in message, \
        "the coherent clear IS performable on both families — always name it"
    assert ("own current word" in message) is offers_pack_word, \
        f"the pack-word clause is unperformable on a {label}"
    if label == "Subject":
        # The reason it is unperformable, asserted rather than asserted-about.
        with pytest.raises(ValueError):
            sdk.update_knowledge(node, status="active", supersededBy=None,
                              supersededAt=None)
        assert _stored(sdk, node)["status"] == "superseded"


@pytest.mark.parametrize("content,rendering", [
    ("supersededBy", "junk"),   # CONTENT names the carrier, the LIVE write does not
    ("zzz", "supersededBy"),    # LIVE write names the carrier, the JOURNAL does not
])
def test_a_key_that_renders_differently_does_not_clear_a_carrier(
        sdk, content, rendering):
    """⛔ BOTH renderers must name the carrier (review cycle 1, P1; 2, P2).

    The live writer files a property by `str(key)`, the journal keeps its CONTENT
    (`tortoise/live.py`'s two-renderer contract — the asymmetry #9649 review 13
    closed on `status`). A `str` subclass whose two renderings disagree is
    therefore written to a DIFFERENT property than the journal records —
    measured. Testing one rendering alone admits that divergence from one side or
    the other, so BOTH directions are pinned:

    * content `supersededBy`, `__str__` `junk` — the carrier SURVIVES in the graph
      while the journal carries the stale value into the record;
    * content `zzz`, `__str__` `supersededBy` — the carrier is cleared in the
      graph while the journal writes a key that is no column, so the record keeps
      `superseded_by` and rebuilds the row TERMINAL against a live graph.

    The first direction alone is refused even by a live-only rule, so it cannot
    discriminate on its own; the second is the direction that resurrects the
    record side, and it is refused only because the rule requires both.
    """
    class _Shadow(str):
        __hash__ = str.__hash__

        def __str__(self) -> str:
            return rendering

    node = _superseded_with_carriers(sdk, _object)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="live", supersededAt=None,
                          **{_Shadow(content): None})
    assert "'superseded' -> 'live'" in str(exc.value)
    assert _stored(sdk, node)["status"] == "superseded"
    assert _carriers(sdk, node) == ("v2", "2026-01-01T00:00:00Z"), \
        "a refused write must clear nothing"


@pytest.mark.parametrize("content,rendering", [
    ("status", "junk"),   # journal names it, the LIVE write does not
    ("junk", "status"),   # LIVE write names it, the journal does not
])
@pytest.mark.parametrize("maker,label", [(_object, "Object"),
                                         (_subject, "Subject")])
def test_a_status_key_whose_live_rendering_differs_is_not_an_un_supersede(
        sdk, maker, label, content, rendering):
    """⛔ THE ADMISSION'S PREMISE MUST BE THE WRITE THAT HAPPENS (cycle 2, P1).

    `lifecycle_props_by_rendering` answers the PRESENCE question over the renderer
    UNION, while the live transport applies `SET n += $props` by `str(key)` and
    the journal keeps the CONTENT. So a `str` subclass whose content is `status`
    and whose `__str__` differs is SEEN as a status write and APPLIED as a
    property with another name: the row never leaves `superseded`, yet the
    carriers are cleared. Measured before the fix on an Object — status stayed
    `superseded`, the carriers went NULL, `junk` was set — i.e. a success report
    on a carrier wipe, which is the #10192 graph/record disagreement this guard
    exists to refuse. Pinned on BOTH families because the allowance is shared.

    ⛔ BOTH DIRECTIONS ARE PINNED (review round 3, P2): the first is refused by a
    content-only rule as well, so on its own it cannot see the LIVE-only drift —
    the second is the direction where a live-only rule admits the write (status
    lands `live`, the journal files `junk`) and the record keeps deriving
    `'superseded'` for a live row.
    """
    class _Shadow(str):
        __hash__ = str.__hash__

        def __str__(self) -> str:
            return rendering

    node = _superseded_with_carriers(sdk, maker)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, supersededBy=None, supersededAt=None,
                          **{_Shadow(content): "live"})
    assert "'superseded' -> 'live'" in str(exc.value)
    assert _stored(sdk, node)["status"] == "superseded", \
        f"the write never applies `status` on a {label} — it must not be admitted"
    assert _carriers(sdk, node) == ("v2", "2026-01-01T00:00:00Z"), \
        "a refused write must clear nothing"


def test_a_value_whose_repr_lies_cannot_disguise_a_conflicting_status_write(sdk):
    """⛔ OWNERSHIP IS DECIDED BY THE KEY COUNT, NOT BY COMPARING VALUES (r3, P1).

    The first cut compared the two status words by `repr()` — a rendering NEITHER
    channel uses — so a value whose `__repr__` echoed the dead word passed the
    comparison while the write landed the other word. Measured before the fix:
    the row went `superseded` -> `live` with both carriers still set, i.e. a
    resurrection past #12745's pin with a record that still derives
    `'superseded'`. `==`/`__hash__` are the same trap from the other side, which
    is why the check counts keys instead of comparing anything about the values.
    """
    class _LiarRepr(str):
        __hash__ = str.__hash__

        def __repr__(self) -> str:
            return repr("superseded")

    node = _superseded_with_carriers(sdk, _object)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, **{_alias_key("superseded", "status"):
                                   "superseded",
                                   "status": _LiarRepr("live")})
    assert "CONFLICTING status write" in str(exc.value)
    assert _stored(sdk, node)["status"] == "superseded"
    assert _carriers(sdk, node) == ("v2", "2026-01-01T00:00:00Z")


def test_an_archived_row_is_not_resurrected_by_clearing_a_legacy_carrier(sdk):
    """`archived` is a RETIREMENT — a carrier clear does not undo it.

    A row can reach `archived` from `superseded` (dead -> dead stays legal), so
    the carriers may still be set on it. That does not make it recoverable: §4.3
    separates "replaced" from "retired", and only the former has a withdrawal.

    #11824 S0: `archived` is a DECLARED SYNONYM stored as `deprecated`, so the
    stored word AND the refusal's origin are both `'deprecated'` here.
    """
    node = _superseded_with_carriers(sdk, _object)
    sdk.update_knowledge(node, status="archived")            # dead -> dead: legal
    assert _stored(sdk, node)["status"] == DEAD_WORD
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="live", supersededBy=None,
                          supersededAt=None)
    assert f"{DEAD_WORD!r} -> 'live'" in str(exc.value)
    assert _stored(sdk, node)["status"] == DEAD_WORD


# ── no leak onto the Point arm, which owns its own vocabulary and table ──

def test_a_point_with_an_object_word_is_refused_by_the_point_vocabulary(sdk):
    """A POSITION word reaches the Point arm and is refused by the shared door.

    This used to pin a per-family difference — `completed` was a legal Object
    word and not a Point word. #11824 S0 removed the difference: one four-word
    set for every knowledge kind, and `completed` is not even an Object word any
    more (it is a stage id). The invariant worth keeping is that the Point arm
    still validates FIRST and still refuses, so nothing leaks onto it.
    """
    pid = sdk.create_point("statement", "pt", dedup=False)["id"]
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(pid, status="completed")
    message = str(exc.value)
    assert "status 'completed' was refused" in message
    assert "advance_stage" in message, \
        "a position word must be told to advance a stage"


def test_a_point_keeps_its_declared_transitions(sdk):
    """The sibling arms' guard must not tighten the Point arm it sits beside.

    ⛔ #11824 S0 makes the WRITE `retracted` LAND as `deprecated`: it is a
    DECLARED SYNONYM (the same ending under another spelling), so the door
    normalises it and `_ALLOWED_TRANSITIONS`' declared `live -> deprecated` is
    the transition taken. The table itself is unchanged — `draft -> {live,
    deprecated, superseded}`, `live -> {deprecated, superseded}`, and every
    legacy terminal row (`retracted`, `outdated`, `archived`) is `frozenset()` —
    so nothing leaves a dead Point, which the second half pins.
    """
    pid = sdk.create_point("statement", "pt-live", dedup=False)["id"]
    sdk.update_knowledge(pid, status="retracted")
    assert _stored(sdk, pid)["status"] == DEAD_WORD
    with pytest.raises(ValueError):
        sdk.update_knowledge(pid, status="live")   # terminal: no outgoing edge
