"""#3311 — a status write on a node carrying the `:Point` label must be
validated and journaled through the generic entity surface.

A node can carry two canonical labels at once — the `:Point:Object` shape.
This surface had no Object status guard to escape: ``_update_entity`` applied
the caller's props as given, and the ``:Point`` arm matched first. What
distinguished the arms was JOURNALING, not validation — the ``:Point`` arm
wrote with a live ``SET n += $p`` and emitted no state record, while the
``:Object`` arm journaled a ``restatus`` against ITS label. Measured on
``origin/main`` via the documented tenant route ``update_entity(id, status=...)``:

    status='live'                 ACCEPTED  stored='live'
    status='draft'                ACCEPTED  stored='draft'
    status='not-a-status-word'    ACCEPTED  stored='not-a-status-word'
    status='deprecated'           ACCEPTED  stored='deprecated'
    status=''                     ACCEPTED  stored=''

Two defects:

  1. **No vocabulary validation.** Any text was written, including ``''`` and
     the legacy ``'deprecated'`` (deliberately outside ``POINT_STATUS_VALUES``
     — ``tortoise/live.py``'s ``LEGACY_NON_CURRENT_STATUS_VALUES``). The
     accepted vocabulary was never consulted for the labels the node carries.
  2. **No replayable journal line.** The ``:Point`` arm wrote with a live
     ``SET n += $p`` and emitted no state record; the ``:Object`` arm journaled
     a ``restatus`` against ITS label. No production path mints a
     ``:Point:Object``, so the node re-materializes as ``:Point`` and that
     record cannot fold — ``rebuild_all`` refused with ``state-op-miss`` and
     the status reverted to ``'draft'``.

The fix routes the write through the journaled lifecycle path — the ONE
``EntityMutated`` builder ``_journal_entity_mutation(..., "restatus")``, which
already handles the op — rather than adding a union vocabulary. A union
vocabulary alone would close defect 1 and leave defect 2: the requirement to
journal is what decides the mechanism.

Runnable (docker lane):
    PYTHONPATH=$PWD TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_t3311' \\
        .venv/bin/python -m pytest tests/test_3311_multilabel_status_vocab.py -q
"""
from __future__ import annotations

import pytest

from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK

_REC = "EntityMutated"


@pytest.fixture
def env(tmp_path):
    """``(sdk, events_dir)`` with the JSONL journal wired."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "ml3311.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _multilabel_point(sdk, *, status: str = "draft") -> str:
    """Mint a `:Point:Object` node.

    No production path creates one (the design plan says so and the repository
    confirms it: only hand-written test fixtures do), so the shape is built the
    only way it can be — create a Point, then add the sibling label.
    """
    pid = sdk.create_point("statement", "multi-label claim",
                           dedup=False, status=status)["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})
    return pid


def _labels(sdk, pid: str) -> set[str]:
    return set(sdk._get_proj().g.query(
        "MATCH (n {id:$id}) RETURN labels(n)", params={"id": pid}
    ).result_set[0][0])


def _status(sdk, pid: str):
    return sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status", params={"id": pid}
    ).result_set[0][0]


def _mutations(events, pid: str) -> list[dict]:
    return [e for e in EventLog(str(events / "events.jsonl")).read_all()
            if e.get("type") == _REC and e.get("id") == pid]


# ── Acceptance 1: an unrecognised status is refused, and writes nothing ───

@pytest.mark.parametrize("bad", ["not-a-status-word", ""])
def test_unrecognised_status_is_refused_and_writes_nothing(env, bad):
    """A word no door stores is refused BY THE VOCABULARY DOOR, and writes nothing.

    ⛔ #11824 S0 MOVED TWO VALUES OUT OF THIS PARAMETRIZATION, and the reason is
    the whole change: ``deprecated`` is now a DECLARED word (it was *"only legacy
    graphs carry it"* — the model that made an ending unsayable), and ``None``
    is refused on a Point by the SHAPE arm, not by the vocabulary. Both are
    pinned by their own tests below, so neither the acceptance nor the refusal
    is left unpinned.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)
    assert _labels(sdk, pid) == {"Point", "Object"}, \
        "the fixture must build the multi-label shape or this test is vacuous"
    before = _mutations(events, pid)

    # #11824 S0: the message is the ONE door's three-part refusal. It names the
    # word it refused and the vocabulary — the invariant here is unchanged.
    with pytest.raises(ValueError, match="was refused"):
        sdk.update_knowledge(pid, status=bad)

    assert _status(sdk, pid) == "draft", (
        "a refused status must not be written — validation must precede the "
        "write")
    assert _mutations(events, pid) == before, \
        "a refused status must not be journaled"


def test_deprecated_is_now_a_DECLARED_word_that_lands(env):
    """⛔ THE REVERSAL (#11824 S0): ``deprecated`` used to be refused here.

    This test's parametrization used to carry ``deprecated`` as an UNRECOGNISED
    value, on the reasoning that only legacy graphs hold it. S0 makes it the
    canonical *"ended, and nothing replaced it"* word — the verb table's
    retire/withdraw/retract/reject/abandon/drop all land on it — so a claim that
    ended with no successor must be able to SAY so. ``draft -> deprecated`` is a
    declared transition (§5), so the write lands.

    Kept as its own test rather than deleting the case: the reversal is a
    behaviour change to this issue's own surface and must not be silent.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)
    sdk.update_knowledge(pid, status="deprecated")
    assert _status(sdk, pid) == "deprecated"
    assert [r["op"] for r in _mutations(events, pid)] == ["restatus"], (
        "the newly-declared word must be journaled like any other")


def test_a_NONE_status_is_refused_on_a_POINT_though_it_DELETES_on_an_Object(env):
    """``None`` is a property-DELETE on an Object/Subject and a SHAPE refusal here.

    #13143 review finding 1 made ``status=None`` the documented delete on the
    non-Point doors (a legal write that had become a refusal). A Point's status
    is the lifecycle carrier the fold reads, so the Point arm keeps refusing the
    SHAPE — and that asymmetry is pinned on both sides, in one place, so neither
    half can be read as the rule for the other.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)
    obj = sdk.create_object("Status None Widget", kind="core:entity")["id"]
    before = _mutations(events, pid)

    with pytest.raises(ValueError, match="Invalid status"):
        sdk.update_knowledge(pid, status=None)
    assert _status(sdk, pid) == "draft", "the refused shape must write nothing"
    assert _mutations(events, pid) == before

    # …and the other half: on an Object the same argument DELETES the property.
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n.status='live'", params={"id": obj})
    # ⛔ THE PRE-STATE IS ASSERTED (#13143 review F3, P3). Without it the pin
    # would pass VACUOUSLY if that raw SET ever no-opped (wrong id property, an
    # engine that ignores the clause): the property would read `None` after the
    # delete because it was never written, not because the delete removed it.
    assert sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) RETURN n.status", params={"id": obj}
    ).result_set[0][0] == "live", "the precondition must hold or the delete is unproven"
    sdk.update_knowledge(obj, status=None)
    got = sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) RETURN n.status", params={"id": obj}
    ).result_set[0][0]
    assert got is None, (
        "`status=None` is the declared property DELETE on a non-Point door: "
        + repr(got))


class _RenderedStatusKey(str):
    """A key whose CONTENT is not ``status`` while its ``__str__`` is.

    This is the renderer asymmetry the vocabulary guards exist for: the journal
    keeps the CONTENT and the live transport applies ``str(key)``, so a
    literal-name test (``"status" in props``) cannot see a write that the node
    receives as ``status``. Pinned against BOTH ``:Point`` doors below.
    """

    def __str__(self) -> str:
        """Render as ``status`` while the dict key keeps its own content."""
        return "status"


def test_the_DEDICATED_point_doors_grade_a_renderer_named_status_key(env):
    """⛔ #13143 review **F1** (P1): the two Point doors read the LITERAL name.

    ``create_point`` tested ``"status" in props`` / popped ``"status"`` and
    ``update_point`` tested ``'status' in props``, while the transport applies
    ``str(key)``. Measured on this branch BEFORE the fix, per engine:

        update_point({<content 'zzz', __str__ 'status'>: 'garbage'})
          -> ACCEPTED, stored 'garbage'                 (BOTH engines)
        create_point(the same shape)
          -> embedded engine: the caller's key won last-wins, stored the
             ungraded word (measured: returned `status: in_progress`)
          -> graph lane: `ResponseError: Duplicate property key 'status' in
             inline property map` — the caller's key arrives beside the CREATE
             map's own `status: $st` field, so the driver refused the statement

    ⛔ THE ENGINE SPLIT IS PART OF THE CLAIM (#13143 review round 2, P3): the
    create half's failure MODE is engine-dependent, so this docstring must not
    say "stored" for it as if it were universal — a measurement that does not
    reproduce in the lane this test actually runs in is not evidence. What is
    universal, and what the fix closes, is that **neither door GRADED the word**:
    an ungraded word no read leg excludes reads as CURRENT, which is the
    fail-open #10371 / #11824 exists to close — and the identical bags were
    already refused by ``create_object`` (the two-key case) and
    ``update_knowledge`` (the single-key case). This test pins the family
    DISAGREEING before the fix and agreeing after it, on both doors and on the
    two-key bag; §1's discriminator is therefore the GRADED refusal
    (``ValueError … was refused``), which the pre-fix graph lane does not raise
    either — it raises the driver's duplicate-key error instead.

    It also pins the DELIVERY: a rendered key is re-filed under the literal
    spelling, so a bag that IS accepted lands exactly one ``status`` column —
    never the graded word plus a raw renderer key.
    """
    sdk, events = env
    key = _RenderedStatusKey("zzz")

    # 1. a single renderer-named key carrying a position/junk word: REFUSED.
    for word in ("in_progress", "garbage"):
        with pytest.raises(ValueError, match="was refused"):
            sdk.create_point("statement", f"rendered key {word}", dedup=False,
                             **{key: word})
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.status IN ['in_progress','garbage'] "
        "RETURN count(n)").result_set[0][0]
    assert rows == 0, (
        "neither refused word may be stored anywhere — a refusal that writes is "
        f"the defect itself; found {rows} row(s)")

    # 2. the same shape through the dedicated UPDATE door: REFUSED, untouched.
    pid = _multilabel_point(sdk, status="draft")
    before = _mutations(events, pid)
    with pytest.raises(ValueError, match="was refused"):
        sdk.update_point(pid, **{key: "garbage"})
    assert _status(sdk, pid) == "draft", "a refused update must write nothing"
    assert _mutations(events, pid) == before

    # 3. the TWO-key bag: both Point doors refuse it as CONFLICTING, exactly as
    #    the entity door does — the landing word of two status keys cannot be
    #    known, and guessing is a bypass (#12786).
    with pytest.raises(ValueError, match="CONFLICTING status write"):
        sdk.create_point("statement", "rendered ambiguity", dedup=False,
                         **{key: "garbage", "status": "live"})
    with pytest.raises(ValueError, match="CONFLICTING status write"):
        sdk.update_point(pid, **{key: "garbage", "status": "live"})
    assert _status(sdk, pid) == "draft"

    # 4. CONTROL — the fix must not over-reject: a rendered CONSISTENT word is
    #    accepted and delivered as exactly one `status` column.
    ok = sdk.create_point("statement", "rendered consistent", dedup=False,
                          **{key: "live"})["id"]
    assert _status(sdk, ok) == "live", "a legal rendered status must still land"


# ── Acceptance 2: the write is journaled and survives a rebuild ───────────

def test_valid_status_is_journaled_against_point_and_survives_rebuild(env):
    """The record must name the label the node REPLAYS as.

    A ``restatus`` against the sibling ``:Object`` label cannot fold: the node
    is re-materialized as ``:Point`` (no production path mints the second
    label), so ``rebuild_all`` refuses with ``state-op-miss`` and the status
    reverts. Journaling against ``:Point`` is what makes the change durable.

    ⚠️ #9649: the fixture moved from ``archived`` to ``live`` because
    ``archived`` is not a DECLARED transition from ``draft``
    (``_ALLOWED_TRANSITIONS['draft']`` is ``{live, retracted, superseded}``;
    ONTOLOGY §5 records ``archived`` as *"reserved — no v1 write path"*), so the
    generic door now refuses it. The property this test owns is the LABEL the
    record names, not the value.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)

    sdk.update_knowledge(pid, status="live")
    assert _status(sdk, pid) == "live"

    recs = _mutations(events, pid)
    assert [r["op"] for r in recs] == ["restatus"], \
        f"expected exactly one restatus record, got {recs!r}"
    assert recs[0]["label"] == "Point", (
        "the record must name the label the node replays as; a record against "
        "the sibling :Object label cannot fold and fails the rebuild")
    assert recs[0]["state"] == {"status": "live"}

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _status(sdk, pid) == "live", (
        "the status change did not survive rebuild_all (defect 2)")


def test_reference_fold_agrees_with_the_journaled_point_status(env):
    """The pure/in-memory reference fold must fold the new record too.

    ``check_consistency`` compares the graph against a replay through
    ``_apply_one``/``fold``. A record the graph fold applies but the reference
    fold ignores makes it report a content divergence on a faithful journal
    (#330 parity; #5048's rule that every graph-folded write also rides
    ``_apply_one``) — so this pins the reference arm, not just ``rebuild_all``.
    """
    from tortoise.consistency import check_consistency

    sdk, events = env
    pid = _multilabel_point(sdk)
    sdk.update_knowledge(pid, status="live")   # #9649: a DECLARED transition

    result = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert result["ok"], (
        "the reference fold disagrees with the graph on a faithful journal: "
        f"{result.get('divergent_points')}")


# ── #3311 review P1: the journal's LAST status writer must survive replay ─

def test_status_write_after_supersede_wins_on_rebuild(env):
    """A status write the journal places AFTER a supersede must survive.

    ``_fold_point_superseded`` writes ``status='superseded'`` UNCONDITIONALLY
    from the deferred pass-1b sweep, which ran after the inline ``EntityMutated``
    ``restatus`` fold — so a later status write was reverted on ``rebuild_all``
    while live and the chronological ``apply()``/``check_consistency`` arm kept
    it. The point-side twin of the #4743 Object re-fold: the re-fold must run
    AFTER the point sweep and be gated on the supersede's journal seq.

    ⚠️ **#9649: this shape is no longer MINTABLE through a live door.**
    ``update_entity`` refuses a lifecycle write on a terminal Point, because a
    ``superseded`` claim has no outgoing transition (``_ALLOWED_TRANSITIONS`` /
    ONTOLOGY §5) and re-statusing it resurrected a dead claim beside its closed
    window. The journal shape still EXISTS — every graph written before #9649
    holds it — and the fold-ordering property below is unchanged, so the
    fixture is built the way a pre-#9649 writer left it: the graph value AND the
    ``restatus`` record. This remains a REPLAY pin; the door's refusal is pinned
    in ``tests/test_9649_update_entity_point_lifecycle.py``.
    """
    from tortoise.consistency import check_consistency

    sdk, events = env
    old = sdk.create_point("statement", "old claim", dedup=False,
                           status="live")["id"]
    new = sdk.create_point("statement", "new claim", dedup=False,
                           status="live")["id"]
    sdk.supersede_point(old, new)
    # The pre-#9649 live write, reproduced: graph first, record second (this
    # door's own order), with the refusal replaced by the write it used to do.
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status=$st",
        params={"id": old, "st": "archived"})
    sdk._journal_entity_mutation("Point", old, "restatus",
                                 state={"status": "archived"})
    assert _status(sdk, old) == "archived"

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _status(sdk, old) == "archived", (
        "the deferred PointSuperseded sweep clobbered a LATER journaled status "
        "write — live/replay divergence")

    result = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert result["ok"], result.get("divergent_points")


def test_supersede_after_status_write_still_ends_superseded(env):
    """The reverse order must NOT be inverted by the parity fix.

    A status write journalled BEFORE the supersede is the OLDER writer, so the
    deferred supersede fold legitimately wins. A fix that re-folded every state
    op (or that always re-applied the inline value) would flip this to the
    stale status — this is the guard that the ordering fix is a comparison and
    not an inversion.
    """
    sdk, events = env
    old = sdk.create_point("statement", "old claim", dedup=False,
                           status="draft")["id"]
    new = sdk.create_point("statement", "new claim", dedup=False,
                           status="live")["id"]
    # #9649: a DECLARED transition (draft → live). The pre-#9649 fixture used
    # live → draft, which ONTOLOGY §5 does not declare and the door now refuses.
    sdk.update_knowledge(old, status="live")
    sdk.supersede_point(old, new)
    assert _status(sdk, old) == "superseded"

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _status(sdk, old) == "superseded", (
        "a supersede journalled AFTER the status write must still terminalize "
        "the point on replay")


def test_supersede_status_then_delete_rebuild_does_not_miss(env):
    """The re-fold must not manufacture a fold-miss for a deleted Point.

    ``create -> supersede -> restatus -> delete``: the delete folds
    inline, so by the time the point re-fold runs the node is gone. Re-folding
    the status op then can only emit a spurious ``state-op-miss`` and fail the
    run (``NonFoldedEventsError``) for a journal every engine replays
    correctly — the same round-6 trap the Object re-fold's existence guard
    exists for. Pins that guard.

    ⚠️ #9649: the middle write is journalled directly, as a pre-#9649 writer
    left it — the live door now refuses a lifecycle write on a terminal Point.
    The node is deleted before the rebuild, so no graph value needs reproducing.
    """
    sdk, events = env
    old = sdk.create_point("statement", "old claim", dedup=False,
                           status="live")["id"]
    new = sdk.create_point("statement", "new claim", dedup=False,
                           status="live")["id"]
    sdk.supersede_point(old, new)
    sdk._journal_entity_mutation("Point", old, "restatus",
                                 state={"status": "archived"})
    sdk.delete(old)

    # Must not raise: the deleted Point's later status op is legitimately
    # unfolded (there is no node left), not a reconciliation failure.
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)",
        params={"id": old}).result_set[0][0] == 0, \
        "a hard-deleted point must not reappear on replay"


# ── The vocabulary values stay writable (owner ruling) ───────────────────

@pytest.mark.parametrize("good", ["draft", "live"])
def test_vocabulary_values_are_accepted_and_journaled(env, good):
    """The fix must refuse only NON-vocabulary values.

    The owner's state-model ruling on #3311 (comment 6091597170) requires
    ``draft`` to remain an explicit, honoured choice. A tightening to
    ``update_point``'s draft→live rule would be a behaviour change, not this fix.

    ⚠️ #9649: the parametrization narrowed to the transitions ONTOLOGY §5
    DECLARES from ``draft`` (``live``). ``archived`` and ``outdated`` are still
    vocabulary values — the status door must accept the WORD and refuse only
    the TRANSITION — so the test below pins that separation instead of losing it.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)
    sdk.update_knowledge(pid, status=good)
    assert _status(sdk, pid) == good
    recs = _mutations(events, pid)
    assert [r["op"] for r in recs] == ["restatus"]
    assert recs[0]["label"] == "Point", (
        "a sibling-label record cannot replay; this assertion makes the test "
        "discriminate the fix from the old :Object record")
    assert recs[0]["state"] == {"status": good}


@pytest.mark.parametrize("word", ["outdated", "archived"])
def test_a_WORD_problem_is_refused_as_a_WORD_while_a_declared_synonym_lands(env, word):
    """⛔ #11824 S0 RESHAPED THIS ARM: neither word is "a legal word with an
    undeclared transition" any more, so the two halves are pinned by what each
    now IS rather than by one shared message.

    * ``outdated`` is REFUSED AS A WORD — it names an ending without saying
      whether anything replaced it, so the vocabulary door answers first and
      names the successor route. Refusing it as arbitrary text would still be
      this issue's defect 1; refusing it as AMBIGUOUS is the S0 rule.
    * ``archived`` is a DECLARED SYNONYM of ``deprecated``, and
      ``draft -> deprecated`` IS declared, so it LANDS (stored canonically,
      silently). It is no longer *"reserved — no v1 write path"*.

    ⛔ WHAT THIS TEST DOES **NOT** PIN, and the name now says so (#13143 review
    **F2**): the TRANSITION half. ``test_an_undeclared_transition_is_refused_as_
    a_TRANSITION`` claimed it in its name while both of its arms are WORD-level
    outcomes, so a reader scanning names was told a transition was pinned here
    when nothing in the body exercised one. The transition refusal has its own
    home — ``tests/test_9649_update_entity_point_lifecycle.py::
    test_an_undeclared_transition_from_live_is_refused`` — and the name is part
    of the claim, the same standard this branch applied to the cache pin.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)
    before = _mutations(events, pid)

    if word == "outdated":
        with pytest.raises(ValueError) as exc:
            sdk.update_knowledge(pid, status=word)
        text = str(exc.value)
        assert "was refused" in text, (
            f"`outdated` must be refused as a WORD by the vocabulary door: {text!r}")
        # The DISCRIMINATING text: the generic "not a lifecycle word" refusal
        # does not name the successor route, and `_render` quotes the value into
        # every refusal, so matching on the word alone would stay green if this
        # arm were deleted (#13143 review finding 3's lesson).
        assert "supersededBy" in text and "supersede_point" in text, (
            f"the `outdated` refusal must name the successor route: {text!r}")
        assert _status(sdk, pid) == "draft"
        assert _mutations(events, pid) == before, (
            "a refused word must not be journaled")
        return

    sdk.update_knowledge(pid, status=word)
    assert _status(sdk, pid) == "deprecated", (
        f"{word!r} is a DECLARED SYNONYM of `deprecated` since #11824 S0, and "
        f"`draft -> deprecated` is a declared transition: {_status(sdk, pid)!r}")
    assert [r["op"] for r in _mutations(events, pid)] == ["restatus"], (
        "the synonym's landing must be journaled against the same label")


# ── Acceptance 4: the dedicated point guard is untouched ─────────────────

def test_update_point_still_only_promotes_draft_to_live(env):
    """The generic-surface fix must not move ``update_point``'s own guard."""
    sdk, _ = env
    pid = _multilabel_point(sdk, status="draft")

    sdk.update_point(pid, status="live")
    assert _status(sdk, pid) == "live"

    # ⛔ #11824 S0 split this loop, because the two halves are refused by two
    # DIFFERENT instruments and a single loose regex would pin neither:
    #   * a DECLARED word this door does not promote → its own promote guard
    #     (`archived`/`retracted` normalise to `deprecated` first, then `!= 'live'`);
    #   * a word no door stores → the SHARED vocabulary door, whose message is
    #     the three-part refusal.
    # The claim is unchanged: `update_point` still only promotes draft→live.
    for word in ("archived", "retracted", "deprecated"):
        with pytest.raises(ValueError, match="only promotes"):
            sdk.update_point(pid, status=word)
    for bad in ("not-a-status-word", ""):
        with pytest.raises(ValueError, match="was refused"):
            sdk.update_point(pid, status=bad)
