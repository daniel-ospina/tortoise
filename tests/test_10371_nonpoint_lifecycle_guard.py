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


# ── the dead set is the family's RECORDED one, not a literal in the guard ──

def test_the_guard_uses_the_family_terminal_sets_that_already_exist():
    """A literal here would be a second declaration of "dead" for these labels."""
    assert "superseded" in OBJECT_TERMINAL_STATUSES
    assert "archived" in OBJECT_TERMINAL_STATUSES
    assert "deprecated" in OBJECT_TERMINAL_STATUSES
    assert "superseded" in SUBJECT_TERMINAL_STATUSES
    assert "archived" in SUBJECT_TERMINAL_STATUSES


def test_completed_and_in_progress_are_not_dead_for_an_object():
    """A connector work item moves between them; neither is a retirement."""
    assert not ({"completed", "in_progress"} & OBJECT_TERMINAL_STATUSES)


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
    s.update_entity(node, supersededBy=successor,
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
    sdk.update_entity(node, status="live", supersededBy=None, supersededAt=None)
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
        sdk.update_entity(node, status="live", superseded_by=None,
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
        sdk.update_entity(node, status="live")
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
        sdk.update_entity(node, status="live", **{cleared: None})
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
        sdk.update_entity(node, status="live", supersededBy="",
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
    `update_entity(status='live', supersededBy=None, supersededAt=None)` is
    REFUSED on this row — there is nothing carried to clear — so offering it
    would be an unperformable prescription, the AX-rule-2 defect #10183.
    """
    node = _object(sdk, "superseded")
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status="live")
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
            sdk.update_entity(shared, status="live", supersededBy=None,
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
    sdk.update_entity(node, **{rendering: "2026-01-01T00:00:00Z"})
    held = _carriers(sdk, node)
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, **{"status": "live",
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
        sdk.update_entity(node, **props)
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
    is a real alternative to `'live'`. A :Subject's vocabulary is CLOSED
    (`commit_ops.SUBJECT_STATUS_VALUES` — the #1423 guard below refuses `active`
    outright), so offering it there prescribes a call that raises. The
    `status='live'` form is performable for BOTH families and is always offered.
    """
    node = _superseded_with_carriers(sdk, maker)
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status="live")
    message = str(exc.value)
    assert "supersededBy=None" in message, \
        "the coherent clear IS performable on both families — always name it"
    assert ("own current word" in message) is offers_pack_word, \
        f"the pack-word clause is unperformable on a {label}"
    if label == "Subject":
        # The reason it is unperformable, asserted rather than asserted-about.
        with pytest.raises(ValueError):
            sdk.update_entity(node, status="active", supersededBy=None,
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
        sdk.update_entity(node, status="live", supersededAt=None,
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
        sdk.update_entity(node, supersededBy=None, supersededAt=None,
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
        sdk.update_entity(node, **{_alias_key("superseded", "status"):
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
    """
    node = _superseded_with_carriers(sdk, _object)
    sdk.update_entity(node, status="archived")            # dead -> dead: legal
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status="live", supersededBy=None,
                          supersededAt=None)
    assert "'archived' -> 'live'" in str(exc.value)
    assert _stored(sdk, node)["status"] == "archived"


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
