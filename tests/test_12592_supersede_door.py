"""#12592 — the successor fact and the "is this current?" predicate.

ONTOLOGY §4.3 states the invariant for every entity that carries the word:

    ``superseded`` means replaced: it names the successor; a retirement with no
    successor is ``archived``; ``deprecated`` is an independent terminal state
    and needs no successor. A journal rebuild is unchanged and still restores a
    supersession recorded without a successor.

Measured on ``main`` at ``58f904ccf7`` (2026-11-15) on THIS checkout, embedded
engine, throwaway nodes — the defect, re-measured rather than inherited from the
filing lane:

    update_knowledge(<Point>,   status='superseded')  -> ACCEPTED  supersededBy=None
    update_knowledge(<Object>,  status='superseded')  -> ACCEPTED  supersededBy=None
    update_knowledge(<Subject>, status='superseded')  -> ACCEPTED  supersededBy=None
    update_knowledge(<Object>,  status='superseded', supersededBy='v2') -> ACCEPTED

and the door that reads as generic was Point-only:

    sdk.supersede_knowledge(<Point>, <new>)    -> OK (CORRECTS + outdated + status)
    sdk.supersede_knowledge(<Object>, <new>)   -> REFUSED  ValueError: No point '<id>'
    sdk.supersede_knowledge(<Subject>, <new>)  -> REFUSED  ValueError: No point '<id>'

So an Object/Subject could say "replaced" through one door and never name what
replaced it through any door.

What this suite pins (each row names the mutation that reds it):

* A BARE ``status='superseded'`` is REFUSED **at the update door**, on every
  label, writes nothing and journals nothing. (The CREATE door is a disclosed
  residual: ``create_object(name, kind, status='superseded')`` still mints the
  bare fact, because a record-sourced rebuild must be able to re-create a row a
  journal recorded as superseded — see the guard's SCOPE paragraph in
  ``tortoise/sdk.py``.) Mutation: delete the
  ``self._assert_supersession_names_a_successor(...)`` call in
  ``_update_entity`` -> the three ``test_a_bare...`` cases red.
* The pair IS admitted through the same door (the guard is not a blanket ban on
  the word). Mutation: make ``_successor_named_by`` return ``False``
  unconditionally -> ``test_the_coherent_pair_is_admitted_through_the_same_door``
  reds.
* The successor channel is the CAMEL property in BOTH renderings. Mutation:
  pass the full carrier set (``_LIFECYCLE_CARRIER_COLUMNS``-derived) instead of
  ``(_SUCCESSOR_PROP,)`` to ``_carrier_prop_named_by`` in
  ``_successor_named_by`` -> ``test_a_carrier_named_by_a_shadow_spelling_does
  _not_satisfy_the_guard`` reds (and ``superseded_by`` would then leave the row
  `superseded` with the pointer every reader resolves absent).
* A STAMP / an empty name is not a name. Mutation: drop the blank/NULL test in
  ``_successor_named_by`` -> the ``supersededAt``-only and ``''`` cases red.
* A POINT's successor may be NAMED BY THE EDGE: ``supersede_point`` writes
  `status` + `outdated` + `(new)-[:CORRECTS]->(old)` and NO ``supersededBy``, so
  judging a Point by the property alone would refuse the idempotent echo of a
  correctly-superseded claim (and reverse #3305's preserved capability).
  Mutation: drop the edge probe -> `test_a_point_superseded_the_documented_way_*
  reds (this over-block was MEASURED by ``test_a_rendered_ECHO_of_the_stored_*
  status_is_still_entailed`` in the 9649 suite, which is why both exist).
* The verdict is over EVERY bound row (#7822), PER NODE. Mutation: read one row
  (``rows[0]``) instead of the per-row loop -> the fan-out case reds; key the
  Point edge probe on ``n.id`` instead of ``ID(n)`` ->
  ``test_a_nameless_twin_is_not_authorised_by_its_siblings_corrects_edge`` reds.
* ``sdk.supersede_knowledge()`` dispatches on the OLD node's label: Point unchanged,
  Object/Subject write the coherent triple (status + successor NAME + instant)
  in ONE journaled write. Mutation: dispatch on nothing (today's Point-only
  chain) -> the two non-Point dispatch cases red with ``No point``; store the
  successor's ID instead of its NAME -> the name assertions red.
* The family door's guards: self, already-terminal old, terminal successor,
  nameless successor, cross-family successor, ``transfer_edges=False`` on a
  non-Point. Each is its own case; deleting a guard reds its case.
* THE ASYMMETRY: the write door refuses, and REPLAY still restores a
  successorless supersession (§4.3's last clause). Mutation: teach the fold the
  same refusal -> ``test_a_journalled_successorless_supersession_still_rebuilds``
  reds. Mutation: delete the door refusal -> the first block reds. The TWO
  halves cannot pass with either single change, which is the design.
* #12825's allowance is untouched: ``status='live'`` WITH both carriers cleared
  lifts a superseded row; a status-ONLY lift is still refused. Mutation: make
  the new guard fire on any status -> the un-supersede case reds.
* The #12907 IMPOSTOR class, at all FOUR sites this door reads a caller value
  through a ``str`` test: ``update_knowledge(status=<impostor>)`` still answers the
  CARRIER ``ValueError``; ``supersede_knowledge(<live>, <impostor>)`` still answers
  ``no :<label> <id>`` on Object/Subject and the Point doors' own ``No point
  <id>`` on ``Point`` (the family dispatch bounds the SUCCESSOR operand on both
  arms — the first cut bounded only the entity arm and the Point arm still
  raised ``TypeError``, #12914 review P2); ``supersede_knowledge(<impostor>, <id>)``
  answers a ``ValueError``
  rather than a ``TypeError``. Mutation: spell the base-content gate
  ``isinstance(x, str)`` instead of ``issubclass(type(x), str)`` -> the first two
  red with ``TypeError: descriptor '__str__' requires a 'str' object``; delete
  the door's impostor refusal (or ``_supersession_label``'s non-``str``
  short-circuit) -> the third reds with ``TypeError: argument of type 'Imp' is
  not iterable``; delete the Point arm's successor bound -> the ``Point`` case
  reds the same way. A GENUINE non-``str`` id (`123`, `None`, `[]`, `object()`)
  keeps the documented ``No point <id>`` fall-through, pinned in the same cases,
  so the bound cannot be widened into a blanket ``str``-only door. The refusal
  interpolates the refused operand, so BOTH caller reads are defensive: a
  raising ``__repr__`` (``_refusal_repr``) and a raising ``__name__`` on the
  class (``_refusal_type_name``) each still answer ``ValueError``.
  Mutation: spell either ``{old_id!r}`` / ``{type(old_id).__name__}`` -> the
  impostor refusal escapes as the caller's own ``RuntimeError``. The same holds
  where a metaclass ANSWERS with a non-``str`` name (``RuntimeError: fmt-boom``),
  where a ``__repr__`` ANSWERS with a ``str`` whose ``__format__`` raises
  (``_refusal_repr`` bounds the ANSWER, and so do the carrier message's
  ``status=`` and carrier-KEY operands), and where a non-``str`` CARRIER VALUE
  reached a ``.strip()`` through ``isinstance`` on the word-keeping write
  (``AttributeError`` for both successor spellings, #12914 review P2 cycle 5),
  and at the exotic-key refusal that
  groups class names
  (``{type(k).__name__ for k in _exotic_keys}`` -> ``RuntimeError: name-boom``).
* The SELF-SUCCESSOR GATE in ``supersede_point`` is a caller read too: it now
  compares BASE CONTENT when both operands are genuinely ``str``, because
  ``str.__eq__`` answers ``NotImplemented`` for a subclass overriding
  ``__eq__``. Mutation: restore a bare ``old_id == new_id`` ->
  ``test_the_POINT_arms_self_successor_gate_does_not_dispatch_to_the_caller``
  reds and a ``CORRECTS`` self-edge is written (measured:
  ``supersede_knowledge(<live Point>, SubLyingEq(<same id>))`` -> ACCEPTED, row
  ``['superseded', True, None]``). The ENTITY arm carries the same bound
  (cycle 7); a genuine successor still lands, pinned in the same case.

Runnable (docker lane):

    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_t12592' \\
        uv run pytest tests/test_12592_supersede_door.py -q
"""
from __future__ import annotations

import json

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "t12592.db"),
                    event_log_path=str(tmp_path / "events.jsonl"))
    yield s
    s.close()


def _make(s, label: str, name: str) -> str:
    """A throwaway node of ``label``, created through its own public door."""
    if label == "Point":
        return s.create_point("statement", f"{name} body", name=name)["id"]
    if label == "Object":
        return s.create_object(name, "core:strategy")["id"]
    if label == "Subject":
        return s.create_subject(name, "core:organization")["id"]
    raise AssertionError(label)


def _row(s, label: str, node_id: str):
    """``[status, supersededBy, supersededAt]`` for the row, or ``None``."""
    rows = s._get_proj().g.query(
        f"MATCH (n:{label} {{id:$id}}) "
        "RETURN n.status, n.supersededBy, n.supersededAt",
        params={"id": node_id},
    ).result_set
    return list(rows[0]) if rows else None


def _journal(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


LABELS = ["Point", "Object", "Subject"]


# ── 1. the write door: the WORD needs its partner ──────────────────────────

@pytest.mark.parametrize("label", LABELS)
def test_a_bare_superseded_status_is_refused_on_every_label(sdk, tmp_path, label):
    """The defect rows themselves, one case per label."""
    node = _make(sdk, label, f"bare-{label.lower()}")
    before_row = _row(sdk, label, node)
    before_records = len(_journal(tmp_path / "events.jsonl"))
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="superseded")
    msg = str(exc.value)
    assert "no successor" in msg, msg
    assert f":{label}" in msg, (
        f"the refusal must name the label it refused: {msg!r}")
    assert "supersede_knowledge(" in msg, (
        f"the repair must name the door that can be taken: {msg!r}")
    assert _row(sdk, label, node) == before_row, (
        "a REFUSED write moved the row: "
        f"{before_row} -> {_row(sdk, label, node)}")
    after = _journal(tmp_path / "events.jsonl")
    assert len(after) == before_records, (
        "a REFUSED write appended journal records "
        f"{[r.get('type') for r in after[before_records:]]}")


def test_the_refusal_names_this_labels_own_section_of_the_model(sdk):
    """§5 for a Point, §4.2 for a Subject, §4.3 for an Object — each its own.

    Mutation: hardcode one section -> two of the three assertions red.
    """
    expect = {"Point": "§5", "Object": "§4.3", "Subject": "§4.2"}
    for label, section in expect.items():
        node = _make(sdk, label, f"sect-{label.lower()}")
        with pytest.raises(ValueError) as exc:
            sdk.update_knowledge(node, status="superseded")
        assert f"ONTOLOGY {section}" in str(exc.value), (
            f"{label} refusal must cite ONTOLOGY {section}: {exc.value!r}")


def test_a_point_refusal_prescribes_retract_not_reserved_archived(sdk):
    """§5 declares `archived` RESERVED on Points, so naming it is unperformable."""
    node = _make(sdk, "Point", "reserved-archived")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="superseded")
    msg = str(exc.value)
    assert "retract_point" in msg, msg
    assert "RESERVED on Points" in msg, (
        f"the Point refusal must say `archived` is reserved: {msg!r}")


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_the_coherent_pair_is_admitted_through_the_same_door(sdk, label):
    """The refusal is about the MISSING partner, not about the word.

    Mutation: make `_successor_named_by` return False unconditionally -> red.
    """
    node = _make(sdk, label, f"pair-{label.lower()}")
    sdk.update_knowledge(node, status="superseded",
                      supersededBy=f"pair-{label.lower()}-v2")
    row = _row(sdk, label, node)
    assert row[0] == "superseded", row
    assert row[1] == f"pair-{label.lower()}-v2", (
        f"the named successor must land: {row}")


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_a_carried_row_may_restate_its_status(sdk, label):
    """A read-modify-write PUT that echoes the status must keep working.

    The pair is already coherent, so the word is not asserting anything new.
    Mutation: drop the "every bound row already names one" early return -> red.
    """
    node = _make(sdk, label, f"echo-{label.lower()}")
    sdk.update_knowledge(node, status="superseded",
                      supersededBy=f"echo-{label.lower()}-v2")
    before = _row(sdk, label, node)
    sdk.update_knowledge(node, status="superseded")
    assert _row(sdk, label, node) == before


def test_a_point_superseded_the_documented_way_may_restate_its_status(sdk):
    """§5 names a POINT's successor with the CORRECTS edge, not a property.

    ``supersede_point`` writes `status` + `outdated` + ``(new)-[:CORRECTS]->(old)``
    and NO ``supersededBy`` (measured on this head). So a Point superseded exactly
    as the model says to has a successor and an EMPTY property — and judging it by
    the property alone refuses the idempotent echo of a correctly-superseded
    claim, reversing the declared-preserved read-modify-write capability (#3305).

    ⛔ This over-block was real: it redded
    ``test_9649_update_entity_point_lifecycle.py::test_a_rendered_ECHO_of_the_*
    stored_status_is_still_entailed`` on the first cut of this change. Mutation:
    drop the CORRECTS probe from ``_assert_supersession_names_a_successor`` ->
    this reds.
    """
    old = _make(sdk, "Point", "edge-old")
    new = _make(sdk, "Point", "edge-new")
    sdk.supersede_point(old, new)
    before = _row(sdk, "Point", old)
    assert before[1] is None, (
        "fixture drifted: `supersede_point` is expected to name the successor "
        f"by the EDGE, not the property: {before}")
    sdk.update_knowledge(old, status="superseded")
    assert _row(sdk, "Point", old) == before, (
        "the echo of a correctly-superseded Point must be a no-op, not a refusal")


@pytest.mark.parametrize("carriers", [
    pytest.param({"superseded_by": "shadow-v2"}, id="snake-shadow"),
    pytest.param({"supersededAt": "2026-01-01T00:00:00Z"}, id="stamp-only"),
    pytest.param({"supersededBy": ""}, id="empty-name"),
    pytest.param({"supersededBy": "   "}, id="blank-name"),
    pytest.param({"supersededBy": None}, id="cleared"),
])
def test_a_carrier_that_names_nobody_does_not_satisfy_the_guard(sdk, carriers):
    """A STAMP, a CLEAR, a blank, or the SHADOW snake spelling names no successor.

    ⛔ The snake spelling is the load-bearing one (#12782): the live write applies
    ``SET n += $props`` literally, so ``superseded_by`` stores a shadow property
    that every camel-keyed reader of the successor ignores — admitting it would
    leave the record deriving 'superseded' while the pointer the model reads for
    "which row replaced this?" is written nowhere.

    Mutation: pass the full carrier-column set to `_carrier_prop_named_by` in
    `_successor_named_by` -> the snake case reds; drop the blank/NULL test ->
    the blank/empty/cleared cases red.
    """
    node = _make(sdk, "Object", "names-nobody")
    with pytest.raises(ValueError, match="no successor"):
        sdk.update_knowledge(node, status="superseded", **carriers)
    assert _row(sdk, "Object", node)[0] != "superseded", (
        "a refused write still left the row claiming superseded")


def test_the_verdict_is_over_every_bound_row(sdk):
    """#7822's fan-out rule on the successor question.

    Two Objects share an id; ONE carries a successor. The write binds both, so
    the row that names nothing is the reason to refuse. Mutation: consult a
    single row instead of every bound row -> this reds (the carried row would
    authorise the write).
    """
    g = sdk._get_proj().g
    shared = "fanout-12592"
    g.query("CREATE (:Object {id:$id, name:'fo-carried', status:'live', "
            "supersededBy:'fo-v2'})", params={"id": shared})
    g.query("CREATE (:Object {id:$id, name:'fo-bare', status:'live'})",
            params={"id": shared})
    with pytest.raises(ValueError, match="no successor"):
        sdk.update_knowledge(shared, status="superseded")
    statuses = sorted(
        r[0] for r in g.query(
            "MATCH (n:Object {id:$id}) RETURN n.status",
            params={"id": shared}).result_set)
    assert statuses == ["live", "live"], (
        f"the refused write terminalized a row: {statuses}")


# ── 2. the SDK door dispatches on the OLD node's label ─────────────────────

def test_supersede_on_a_point_is_unchanged(sdk):
    """Byte-compatible with today: CORRECTS + outdated + superseded status.

    Mutation: route Point through the entity path -> the CORRECTS/outdated
    assertions red.
    """
    old = _make(sdk, "Point", "pt-old")
    new = _make(sdk, "Point", "pt-new")
    result = sdk.supersede_knowledge(old, new)
    assert result["invalidated"] is True, result
    assert result["id"] == old and result["corrected_by"] == new, result
    assert "edges_transferred" in result, (
        "the Point door still reports its edge transfer")
    g = sdk._get_proj().g
    row = g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status, n.outdated",
        params={"id": old}).result_set[0]
    assert row[0] == "superseded" and row[1] is True, (
        f"the Point supersession must still write status+outdated: {row}")
    corrects = g.query(
        "MATCH (n:Point {id:$new})-[:CORRECTS]->(o:Point {id:$old}) "
        "RETURN count(o)", params={"old": old, "new": new}).result_set[0][0]
    assert corrects == 1, "the CORRECTS edge must still be minted for a Point"


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_supersede_writes_the_coherent_triple_for_a_non_point(sdk, tmp_path,
                                                              label):
    """The defect's second half: the pair WAS unwritable through any SDK door.

    Mutation: dispatch on nothing (the Point-only chain) -> `No point` raises;
    store the successor's ID -> the name assertions red; drop `supersededAt`
    from the write -> the instant assertions red.
    """
    old = _make(sdk, label, f"ct-old-{label.lower()}")
    new_name = f"ct-new-{label.lower()}"
    new = _make(sdk, label, new_name)
    result = sdk.supersede_knowledge(old, new)
    assert result["superseded"] is True and result["label"] == label, result
    assert result["superseded_by"] == new_name, (
        "the CARRIER is the successor's NAME (§4.2/§4.3: stored verbatim), "
        f"not its id: {result}")
    row = _row(sdk, label, old)
    assert row[0] == "superseded", row
    assert row[1] == new_name, (
        f"supersededBy must name the successor: {row}")
    assert row[2] == result["superseded_at"], (
        f"the instant the door reports must be the one it wrote: {row}")
    # …and it is JOURNALED, with the minted instant, so replay is byte-identical
    # (the #2164-P4 drift class the Object FOLD still exhibits on its live path).
    records = [r for r in _journal(tmp_path / "events.jsonl")
               if r.get("type") == "EntityMutated"
               and r.get("label") == label and r.get("op") == "supersede"]
    assert records, "the coherent triple journaled no supersede record"
    state = records[-1]["state"]
    assert state == {"status": "superseded", "supersededBy": new_name,
                     "supersededAt": result["superseded_at"]}, state
    # The successor itself is untouched.
    assert _row(sdk, label, new)[0] != "superseded", (
        "the successor must not be terminalized by naming it")


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_the_family_door_refuses_a_self_replacement(sdk, label):
    node = _make(sdk, label, f"self-{label.lower()}")
    with pytest.raises(ValueError, match="differ"):
        sdk.supersede_knowledge(node, node)


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_the_family_door_refuses_an_already_terminal_old(sdk, label):
    """Terminalization is one-way (#2498) — the same refusal the Point door makes."""
    old = _make(sdk, label, f"dead-old-{label.lower()}")
    sdk.update_knowledge(old, status="archived")
    with pytest.raises(ValueError, match="already terminal"):
        sdk.supersede_knowledge(old, _make(sdk, label, f"dead-old-new-{label.lower()}"))


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_the_family_door_refuses_a_terminal_successor(sdk, label):
    old = _make(sdk, label, f"tnew-old-{label.lower()}")
    new = _make(sdk, label, f"tnew-new-{label.lower()}")
    sdk.update_knowledge(new, status="archived")
    with pytest.raises(ValueError, match="itself terminal"):
        sdk.supersede_knowledge(old, new)
    assert _row(sdk, label, old)[0] != "superseded", (
        "a refused supersession terminalized the old row anyway")


def test_the_family_door_refuses_a_cross_family_successor(sdk):
    """`supersededBy` is resolved by a PER-LABEL successor probe.

    A Subject name stored on an Object would never resolve — the same defect as
    storing none, one step quieter.
    """
    old = _make(sdk, "Object", "xfamily-old")
    subj = _make(sdk, "Subject", "xfamily-subject")
    with pytest.raises(ValueError, match="same-family"):
        sdk.supersede_knowledge(old, subj)
    assert _row(sdk, "Object", old)[0] != "superseded"


def test_the_family_door_refuses_a_nameless_successor(sdk):
    """The carrier IS the name, so a nameless successor cannot be named."""
    g = sdk._get_proj().g
    g.query("CREATE (:Object {id:'nameless-12592', status:'live'})")
    old = _make(sdk, "Object", "nameless-old")
    with pytest.raises(ValueError, match="no name"):
        sdk.supersede_knowledge(old, "nameless-12592")


def test_transfer_edges_false_is_refused_on_a_non_point(sdk):
    """The invalidate half is `outdated` + CORRECTS — a Point's machinery (§4.7)."""
    old = _make(sdk, "Object", "xfer-old")
    new = _make(sdk, "Object", "xfer-new")
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(old, new, transfer_edges=False)
    msg = str(exc.value)
    assert "Point-only" in msg, msg
    assert "status='archived'" in msg, (
        f"the refusal must prescribe the retirement §4.3 gives it: {msg!r}")
    assert _row(sdk, "Object", old)[0] != "superseded"


def test_a_missing_old_node_still_answers_the_point_shaped_message(sdk):
    """Absent input is unchanged: the door falls through to the Point path."""
    with pytest.raises(ValueError, match="No point"):
        sdk.supersede_knowledge("no-such-node-12592",
                      _make(sdk, "Object", "absent-new"))


# ── 3. the asymmetry, pinned in BOTH directions ────────────────────────────

def test_a_journalled_successorless_supersession_still_rebuilds(tmp_path):
    """§4.3's last clause: *"A journal rebuild is unchanged and still restores a
    supersession recorded without a successor."*

    ⛔ This is the WHOLE design: the refusal is a WRITE-DOOR refusal. A
    legacy/pre-fix journal, or a raw producer, can carry the half-pair, and a
    fold that refused it would refuse to rebuild existing history. The record
    here is emitted through the ONE builder (``_journal_entity_mutation``), which
    is exactly what a pre-guard producer wrote.

    Mutation: teach the fold the same refusal -> the status assertion reds.
    Mutation: delete the door guard -> `test_a_bare_superseded_status_*` reds.
    Neither single change can leave both halves green.
    """
    events = tmp_path / "events"
    events.mkdir()
    journal = events / "events.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "t12592r.db"),
                      event_log_path=str(journal))
    try:
        oid = sdk.create_object("legacy-unsuccessored", "core:strategy")["id"]
        # The PRE-GUARD producer's shape: status only, no carrier.
        sdk._journal_entity_mutation("Object", oid, "supersede",
                                     state={"status": "superseded"})
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        row = sdk._get_proj().g.query(
            "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
            params={"n": "legacy-unsuccessored"}).result_set
        assert row, "the journaled registration must recreate the target"
        assert row[0][0] == "superseded", (
            "REPLAY must still restore a successorless supersession "
            f"(ONTOLOGY §4.3's last clause); the row came back {row[0]!r}")
        assert row[0][1] is None, (
            f"the successorless journal must replay successorless: {row[0]!r}")
    finally:
        sdk.close()


# ── 4. #12825's allowance is untouched ────────────────────────────────────

def test_the_record_carried_un_supersede_still_lifts_the_row(sdk):
    """`status='live'` WITH both carriers cleared is the recorded un-supersede.

    Mutation: make the new guard fire on any status-bearing write -> red.
    """
    node = _make(sdk, "Object", "unsup")
    sdk.update_knowledge(node, status="superseded", supersededBy="unsup-v2",
                      supersededAt="2026-01-01T00:00:00Z")
    sdk.update_knowledge(node, status="live", supersededBy=None,
                      supersededAt=None)
    row = _row(sdk, "Object", node)
    assert row == ["live", None, None], (
        f"#12825's coherent un-supersede must keep working: {row}")


def test_a_status_only_lift_is_still_refused(sdk):
    """…and the incoherent half stays refused — the carriers would still say so."""
    node = _make(sdk, "Object", "unsup-only")
    sdk.update_knowledge(node, status="superseded", supersededBy="unsup-only-v2")
    with pytest.raises(ValueError, match="is TERMINAL for a :Object"):
        sdk.update_knowledge(node, status="live")
    assert _row(sdk, "Object", node)[0] == "superseded", (
        "a refused status-only lift still moved the row")


# ── 5. The allowance is NARROW: the review's three bypasses ───────────────
# Each of these was measured on the first cut of this guard and ADMITTED.
# They are the difference between "supplies the missing half" and "mentions a
# property called supersededBy", which is the only thing §4.3 forbids.

def test_a_key_with_a_colliding_hash_is_refused_as_an_exotic_key(sdk):
    """A `str` subclass overriding `__hash__` is a SECOND dict entry, same content.

    ``{K('supersededBy'): 'v2', 'supersededBy': None}`` passes a per-KEY check
    while the live write applies both to ``supersededBy`` — a row could end
    ``superseded`` naming nobody with the guard reporting satisfied, and the fold's
    iteration order decides the winner. Since cycle 8 the guard is entered one
    step earlier and refuses the CLASS: a superseded write takes plain `str`
    keys, because a key's rendering can differ between the check and the write.
    The same-content conflict between PLAIN keys is pinned separately by
    ``test_a_snake_key_beside_the_camel_key_is_the_conflict``.

    Mutation: drop the ``type(k) is not str`` bound -> this reds (the call is
    accepted, and which of the two values lands is not knowable here). The
    CONFLICT refusal fires first on this shape, which is why the assertion below
    expects that message — both are refusals, and the conflict is the more
    specific of the two.
    """

    class _K(str):
        __hash__ = lambda self: 424242          # noqa: E731 — collision on purpose

    node = _make(sdk, "Object", "ambiguous")
    with pytest.raises(ValueError, match="CONFLICTING successor write"):
        sdk.update_knowledge(node, status="superseded",
                          **{_K("supersededBy"): "amb-v2",
                             "supersededBy": None})
    assert _row(sdk, "Object", node) == [None, None, None], (
        "a refused conflicting write still moved the row")


@pytest.mark.parametrize("carrier", [0, False, [], 123])
def test_a_non_string_carrier_names_nobody_and_is_refused(sdk, carrier):
    """A successor resolves through name readers — ``0``/``False``/``[]`` do not.

    Mutation: admit any non-``None`` value (the first cut's ``return True``) ->
    these four red, each storing a value no successor probe can resolve while the
    row claims the word. This is the docstring's own stated defect ("the
    successor a reader resolves is the empty string") in another spelling.
    """
    node = _make(sdk, "Object", f"nonstr-{type(carrier).__name__}-{carrier!r}")
    with pytest.raises(ValueError, match="no successor"):
        sdk.update_knowledge(node, status="superseded", supersededBy=carrier)
    assert _row(sdk, "Object", node)[0] is None


def test_a_hybrid_row_nameless_as_object_is_prescribed_the_point_remedy(sdk):
    """The repair must be performable on EVERY label it is spoken from.

    ``:Point:Object`` with the Point arm satisfied by an incoming CORRECTS edge
    and the Object arm nameless: the prescription must be the Point one
    (``retract_point``, or the edge ``supersede_knowledge()`` writes on that path).
    ``archived`` is RESERVED on Points (§5) and this door REFUSES it there, so
    the Object-first wording would prescribe an unperformable repair.

    Mutation: choose the wording from ``nameless[0]`` instead of from the BOUND
    labels -> the message prescribes ``status='archived'`` and offers
    ``supersede_knowledge()`` as the route, and this reds. Second mutation: drop
    ``mixed_clause`` -> the last assertion reds.
    """
    node = _make(sdk, "Point", "hybrid-12592")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Object",
        params={"id": node})
    successor = _make(sdk, "Point", "hybrid-12592-v2")
    sdk.supersede_knowledge(node, successor)          # Point path: CORRECTS edge, no prop
    sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) REMOVE o.supersededBy", params={"id": node})
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="superseded")
    message = str(exc.value)
    assert "retract_point" in message and "RESERVED" in message, (
        "a hybrid nameless as an Object was prescribed a Point-illegal "
        f"'archived': {message}")
    assert "status='archived'" not in message, message
    assert "binds :Object and :Point at once" in message, (
        f"a hybrid id was handed a route that cannot serve it: {message}")
    assert "is NOT the route here" in message, message


def test_an_id_bound_by_two_families_is_refused_not_fanned_out(sdk):
    """#12914 review P2: one resolved label must not be written to EVERY label.

    `_supersession_label` resolves ONE label, but the write goes through
    `update_knowledge`, whose non-Point arm applies ``SET n += $props`` to every
    label the id binds. Measured on the reviewed head: an id bound by
    :Object AND :Subject ended with BOTH rows carrying the Object's successor
    name, while naming that Subject successor directly is refused as
    cross-family — the same defect one step quieter. `supersededBy` is read by a
    per-label successor probe, so one stored name cannot be right for two families.

    Mutation: remove the sibling-label probe -> red (both rows take the Object's
    successor and the call returns success).
    """
    node = _make(sdk, "Object", "twofam-12592")
    sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) SET o:Subject, o.subjectKind='core:organization'",
        params={"id": node})
    successor = _make(sdk, "Object", "twofam-12592-v2")
    sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) SET o:Subject, o.subjectKind='core:organization'",
        params={"id": successor})
    with pytest.raises(ValueError, match="binds BOTH"):
        sdk.supersede_knowledge(node, successor)
    assert _row(sdk, "Object", node) == [None, None, None], (
        "a refused hybrid write moved the Object row")
    assert _row(sdk, "Subject", node) == [None, None, None], (
        "a refused hybrid write moved the Subject row")


def test_a_successor_id_that_names_two_rows_is_refused(sdk):
    """#12914 review P3: the SUCCESSOR side needs the source side's fan-out rule.

    The source is adjudicated per row; the successor was not — ``names[0]`` took
    whatever `collect(n.name)` returned first, so an id bound by two rows with
    different names stored an engine-order choice the caller never made
    (measured on the reviewed head: `Alpha` stored, no refusal).

    Mutation: restore ``successor_name = names[0]`` -> red (the call is accepted
    and stores one of the two names).
    """
    node = _make(sdk, "Object", "twoname-12592")
    successor = _make(sdk, "Object", "twoname-12592-v2")
    sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) CREATE (o2:Object {id:$id, "
        "name:'twoname-12592-alias', status:'live'})",
        params={"id": successor})
    rows = sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) RETURN count(o)",
        params={"id": successor}).result_set
    assert rows[0][0] == 2, "fixture drifted: the successor needs two rows"
    with pytest.raises(ValueError, match="MORE THAN ONE ROW"):
        sdk.supersede_knowledge(node, successor)
    assert _row(sdk, "Object", node) == [None, None, None], (
        "a refused ambiguous-successor write moved the source row")


# ── 6. The CLEAR and the per-node verdict (cycle-2 review P0 + P1) ────────

def test_a_point_named_by_the_property_loses_it_to_a_same_call_clear(sdk):
    """The Point arm must judge the POST-write name, like the property arm does.

    A Point can carry `supersededBy` as a plain property (this very door admits
    `status='superseded', supersededBy='v2'` on it — see the module table), so a
    same-call clear strips the only name it had and leaves no `CORRECTS` edge.
    Measured on the cycle-2 head: admitted, by reading the STORED value while the
    Object/Subject arm read the write's RESULT.

    Mutation: drop ``carrier_cleared or`` from the Point arm's unresolved test ->
    this reds, because the call is accepted.
    """
    node = _make(sdk, "Point", "pointprop")
    sdk.update_knowledge(node, status="superseded", supersededBy="pointprop-v2")
    with pytest.raises(ValueError, match="CLEARS `supersededBy`"):
        sdk.update_knowledge(node, status="superseded", supersededBy=None)
    assert _row(sdk, "Point", node)[1] == "pointprop-v2", (
        "a refused clear still moved the row")

def test_clearing_the_carrier_in_the_same_call_as_the_word_is_refused(sdk):
    """A `supersededBy=None` beside the word splits the pair by the WRITE itself.

    Measured on the cycle-1 head: a coherent Object superseded with 'acme-v2',
    then ``update_knowledge(o, status='superseded', supersededBy=None)`` was
    ACCEPTED and stored ``[['superseded', None]]`` — the exact state §4.3 says
    the model does not have. The stored-value allowance reads the PRE-write row,
    so it licensed a call that removed the very value it was reading.

    Mutation: drop ``carrier_cleared`` from the per-row ``names_after`` test
    (i.e. let the stored successor satisfy a call that clears it) -> red.
    """
    node = _make(sdk, "Object", "clearcall")
    sdk.update_knowledge(node, status="superseded", supersededBy="acme-v2")
    with pytest.raises(ValueError, match="CLEARS `supersededBy`"):
        sdk.update_knowledge(node, status="superseded", supersededBy=None)
    assert _row(sdk, "Object", node) == ["superseded", "acme-v2", None], (
        "a refused clear still moved the row")


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_a_bare_carrier_clear_stays_ACCEPTED_and_leaves_the_row_dead(sdk, label):
    """…but the CARRIER-ONLY clear is OUT of this guard's scope, by DECISION.

    ⛔ This is a pin of somebody else's ruling, not of a preference: a cycle-2
    review proposed refusing this shape too (it does leave the row saying
    'superseded' with nobody named), and that would have reversed **#12882 review
    F2**, which re-pins it — in that PR's
    `test_a_coherent_un_supersede_lifts_the_row_and_a_status_only_write_cannot`,
    which lands WITH #12882 and is therefore not on this branch — as *"the bare
    carrier CLEAR is still ACCEPTED on a superseded row and leaves the row
    TERMINAL: with no status named there is no transition to ask about"*.
    The residual is disclosed on #12592 rather than tightened here.

    Mutation: refuse a carrier-only clear (an `elif` arm on the successor key) ->
    red — and #12882's own test reds with it, which is the point of keeping the
    decision in one place.
    """
    node = _make(sdk, label, f"bareclear-{label.lower()}")
    sdk.update_knowledge(node, status="superseded",
                      supersededBy=f"bareclear-{label.lower()}-v2")
    sdk.update_knowledge(node, supersededBy=None)
    row = _row(sdk, label, node)
    assert row[0] == "superseded", (
        f"a bare carrier clear must leave the row dead (#12882 F2): {row}")
    assert row[1] is None, row


def test_clearing_the_carrier_is_still_allowed_when_the_call_leaves_the_word(sdk):
    """The refusal is about the WORD, not about clears — #12825's lift must pass.

    Mutation: refuse any ``supersededBy=None`` regardless of the status in the
    call -> red, and #12825's coherent un-supersede stops working.
    """
    node = _make(sdk, "Object", "clearok")
    sdk.update_knowledge(node, status="superseded", supersededBy="clearok-v2")
    sdk.update_knowledge(node, status="live", supersededBy=None, supersededAt=None)
    assert _row(sdk, "Object", node) == ["live", None, None]


def test_a_nameless_twin_is_not_authorised_by_its_siblings_corrects_edge(sdk):
    """Ids are not unique, so the Point verdict must be PER NODE.

    Two ``:Point`` nodes sharing one id, an incoming CORRECTS edge into ONE of
    them: the id-keyed edge set said "this id is edged" and both rows were left
    naming nobody (measured on the cycle-1 head). §7822's fan-out rule is the
    same fact.

    Mutation: go back to a set of ids (``RETURN DISTINCT n.id``) instead of
    ``ID(n)`` -> this reds, because the call is ACCEPTED.
    """
    first = _make(sdk, "Point", "twin-12592")
    successor = _make(sdk, "Point", "twin-12592-v2")
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$old}), (b:Point {id:$new}) MERGE (b)-[:CORRECTS]->(a)",
        params={"old": first, "new": successor})
    # a second node under the SAME id, with no successor of its own
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) CREATE (m:Point) SET m = properties(n)",
        params={"id": first})
    with pytest.raises(ValueError, match="no successor"):
        sdk.update_knowledge(first, status="superseded")
    stored = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status AS st", params={"id": first}
    ).result_set
    assert [r[0] for r in stored] == ["live", "live"], (
        f"a nameless sibling rode its twin's edge: {stored}")


def test_the_SNAKE_spelling_of_a_same_call_clear_is_the_same_split(sdk):
    """`superseded_by` is the SAME record column, so it is the same split.

    `_split_properties` files `snake(key)` into the column, so `superseded_by` and
    `supersededBy` write ONE column while `keys_rendering_as` sees only the camel
    key: measured ``keys_rendering_as({'superseded_by': None}, {'supersededBy'})
    == []``. On the cycle-3 head that let
    ``update_knowledge(<superseded Object, supersededBy='acme-v2'>,
    status='superseded', superseded_by=None)`` through — the graph kept the name
    every reader resolves while the record's `superseded_by` went NULL, so the
    record derived the word from a column the caller had just cleared.

    Mutation: key `carrier_cleared` on `keys_rendering_as` (the camel spelling)
    instead of the record COLUMN -> this reds, because the call is accepted.

    The snake SET is the other half and gets its own wording: it is not a clear,
    it is a name in a channel no reader resolves (#12782), so the repair is the
    camel spelling rather than "name someone".
    """
    node = _make(sdk, "Object", "snakesplit")
    sdk.update_knowledge(node, status="superseded", supersededBy="snakesplit-v2")
    with pytest.raises(ValueError, match="CLEARS `supersededBy`"):
        sdk.update_knowledge(node, status="superseded", superseded_by=None)
    assert _row(sdk, "Object", node)[1] == "snakesplit-v2", (
        "a refused snake clear still moved the row")

    other = _make(sdk, "Object", "snakeset")
    with pytest.raises(ValueError, match="NO READER RESOLVES"):
        sdk.update_knowledge(other, status="superseded", superseded_by="snakeset-v2")
    assert _row(sdk, "Object", other) == [None, None, None], (
        "a refused shadow-spelled name still moved the row")


def test_a_snake_key_beside_the_camel_key_is_the_conflict(sdk):
    """Two spellings, one column: which value survives is not knowable here.

    Mutation: count only the camel keys for the conflict test -> red (the call is
    accepted and the fold's iteration order decides the column).
    """
    node = _make(sdk, "Object", "snakeconflict")
    with pytest.raises(ValueError, match="CONFLICTING successor write"):
        sdk.update_knowledge(node, status="superseded", supersededBy="snakeconflict-v2",
                          superseded_by=None)


def test_a_key_whose_RENDERING_hides_the_column_is_refused_as_exotic(sdk):
    """The RECORD files the JOURNAL rendering, so a key's two renderings can disagree.

    A `str` subclass whose CONTENT is `superseded_by` and whose `__str__` is
    something else is a carrier to `_split_properties` (`snake(prop)` on the
    journal prop) and invisible to a `str(k)`-only test. Measured on the cycle-4
    head: `update_knowledge(<superseded Object, supersededBy='acme-v2'>,
    status='superseded', **{K: None})` was ADMITTED — the graph kept the camel
    name while the record's column went NULL, so the record folded `live`.

    Mutation: test only `snake(str(k))` (the live rendering) -> the call is
    accepted, the graph keeps its camel name while the record's column goes NULL,
    and the two keys merge into one. Since cycle 8 the class is refused earlier
    (a superseded write takes plain `str` keys — the same bound as the value
    side), so both halves below assert THAT refusal; the rendering union stays in
    the code because the record, not this guard, is what files the column.
    """

    class _K(str):
        def __str__(self):
            return "zzz"          # the live name; the CONTENT is the column

    node = _make(sdk, "Object", "renderhide")
    sdk.update_knowledge(node, status="superseded", supersededBy="renderhide-v2")
    with pytest.raises(ValueError, match="not plain `str`"):
        sdk.update_knowledge(node, status="superseded", **{_K("superseded_by"): None})
    assert _row(sdk, "Object", node)[1] == "renderhide-v2", (
        "a refused write through the hidden rendering still moved the row")

    other = _make(sdk, "Object", "renderconflict")
    with pytest.raises(ValueError, match="CONFLICTING successor write"):
        sdk.update_knowledge(other, status="superseded", supersededBy="renderconflict-v2",
                          **{_K("superseded_by"): None})


def test_a_value_that_lies_about_being_blank_does_not_name_a_successor(sdk):
    """The ALLOWANCE reads the value's BASE content, never the caller's override.

    A `str` subclass can answer `strip()` while its content is empty; the write
    stores the base content, so the row would end `superseded` naming nobody and
    the record (reading `IS NOT NULL`) would agree with it. Measured on the
    cycle-5 head: admitted. `live.py` states the rule for the seam — "the VALUE
    keeps its base content (`str.__str__`)".

    Mutation: call the caller's `value.strip()` instead of
    `str.strip(str.__str__(value))` -> this reds, because the call is accepted.
    """

    class BlankLies(str):
        def strip(self, *args, **kwargs):
            return "v2"           # the lie; the CONTENT is ''

    node = _make(sdk, "Object", "blanklies")
    with pytest.raises(ValueError, match="no successor"):
        sdk.update_knowledge(node, status="superseded", supersededBy=BlankLies(""))
    assert _row(sdk, "Object", node) == [None, None, None], (
        "a refused lie still moved the row")


def test_a_status_word_that_lies_about_equality_is_still_the_word(sdk):
    """The comparison reads the word's BASE content, not the caller's `__eq__`.

    `lifecycle_props_by_rendering` hands back the CALLER'S object, so
    ``Liar('superseded') != 'superseded'`` — True, once `__ne__` is overridden —
    made the guard return as "this call moves the row OFF the word" while the wire
    rendered `"superseded"`: the write stored the word with no successor
    (measured on the cycle-6 head). The mirror direction was an over-block: the
    LEGITIMATE `status=Liar('superseded'), supersededBy='v2'` was refused.

    Mutation: compare `status_value` itself (or compare before normalising) ->
    both halves red.
    """

    class Liar(str):
        __eq__ = lambda s, other: False        # noqa: E731 — the lie IS the fixture
        __ne__ = lambda s, other: True         # noqa: E731

    node = _make(sdk, "Object", "eqliar")
    with pytest.raises(ValueError, match="no successor"):
        sdk.update_knowledge(node, status=Liar("superseded"))
    assert _row(sdk, "Object", node) == [None, None, None], (
        "a refused lying word still moved the row")

    # …and the coherent pair through the same object is still admitted.
    sdk.update_knowledge(node, status=Liar("superseded"), supersededBy="eqliar-v2")
    assert _row(sdk, "Object", node)[:2] == ["superseded", "eqliar-v2"]


def test_a_value_whose_rendering_blanks_the_wire_is_not_a_name(sdk):
    """An exact `str` is required: the LIVE wire is rendered by method lookups.

    `falkordb.helpers.quote_string` calls `len(v)` and `v.replace(...)` on the
    caller's object, so a subclass renders `""` for content `'v2'` while the
    journal keeps `'v2'`. Measured on the cycle-6 head: admitted, storing
    `supersededBy=''` in the graph with the record disagreeing.

    Mutation: accept any `isinstance(value, str)` -> both cases red (the call is
    accepted and the graph names nobody).
    """

    class Len0(str):
        __len__ = lambda s: 0                  # noqa: E731

    class Rep0(str):
        def replace(self, *args, **kwargs):
            return ""

    for name, value in (("len0", Len0("v2")), ("rep0", Rep0("v2"))):
        node = _make(sdk, "Object", f"wirelie-{name}")
        with pytest.raises(ValueError, match="no successor"):
            sdk.update_knowledge(node, status="superseded", supersededBy=value)
        assert _row(sdk, "Object", node) == [None, None, None], (
            f"a refused {name} value still moved the row")


def test_a_key_whose_str_CHANGES_between_reads_cannot_be_checked_at_all(sdk):
    """The TOCTOU case: a key that renders one way to the guard, another to the write.

    Measured on the cycle-7 head: a `str` subclass whose `__str__` answered
    `supersededBy` on the writer's later render and `harmless` on the guard's
    reads made `carrier_cleared` False and `_name_keys` empty, so the write landed
    `status='superseded'` having REMOVED the graph's `supersededBy` — the row
    §4.3 defines away — while every read the guard made looked innocent.

    Mutation: remove the `type(k) is not str` bound -> red (the call is accepted
    and the carrier is stripped).
    """
    calls = {"n": 0}

    class Flip(str):
        def __str__(self):
            calls["n"] += 1
            return "supersededBy" if calls["n"] >= 8 else "harmless"

    node = _make(sdk, "Object", "flip")
    sdk.update_knowledge(node, status="superseded", supersededBy="flip-v2")
    with pytest.raises(ValueError, match="not plain `str`"):
        sdk.update_knowledge(node, status="superseded", **{Flip("zzz"): None})
    assert _row(sdk, "Object", node)[1] == "flip-v2", (
        "a refused flip-key write still stripped the carrier")


def test_a_key_that_names_the_successor_to_the_guard_and_NOT_to_the_write_is_refused(sdk):
    """P1, the shape the cycle-8 suite case could NOT discriminate (#12914).

    The sibling case above passes a `None` VALUE, so `_successor_named_by` refuses
    it on the value test whatever the KEY renders — the bound was therefore never
    the thing under test. THIS case supplies a non-empty plain `str` value, so the
    ALLOWANCE is what is being decided: a stateful `str` subclass whose CONTENT is
    `supersededBy` (so `str.__str__` names the carrier) and whose `__str__` answers
    the camel name for this guard's reads and a harmless name on the WRITER's later
    call names the successor to the guard while filing a DIFFERENT property on the
    wire — measured on the reviewed head (`f59a6451`) as `K>=7` admitted: graph
    `['superseded', None, 'v2']` (the word, nobody named, plus an orphan `zzz`).

    So the allowance must sit BELOW the key bound: "can any read here be trusted"
    is prior to "does this call name a successor".

    Mutation: put `if _successor_named_by(props): return` back ABOVE the
    `type(k) is not str` bound -> red (the call is accepted, `supersededBy` goes
    NULL and `zzz` is written).
    """
    calls = {"n": 0}

    class Flip(str):
        def __str__(self):
            calls["n"] += 1
            return "supersededBy" if calls["n"] <= 8 else "zzz"

    node = _make(sdk, "Object", "flipkey")
    sdk.update_knowledge(node, status="superseded", supersededBy="flipkey-v2")
    before = _row(sdk, "Object", node)
    with pytest.raises(ValueError, match="not plain `str`"):
        sdk.update_knowledge(node, status="superseded",
                             **{Flip("supersededBy"): "v2"})
    assert _row(sdk, "Object", node) == before, (
        "a refused flip-key write moved the row")
    assert _row(sdk, "Object", node)[1] == "flipkey-v2", (
        "the carrier was replaced by the key's wire spelling")
    # ⛔ THE ORPHAN IS THE DAMAGE, and it is measured, not asserted: the write
    # renders the key AGAIN, later, and files it under `zzz`. A guard that only
    # checks the row's `supersededBy` cannot see it.
    orphans = sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) RETURN n.zzz", params={"id": node}).result_set
    assert [list(r) for r in orphans] == [[None]], (
        f"the flip key parked the successor's name on a property no reader "
        f"resolves: {[list(r) for r in orphans]}")


def test_a_successor_with_a_lying_eq_is_still_the_same_row(sdk):
    """`old_id == new_id` is a method lookup, so it is read at base content.

    A `str` subclass with `__eq__ -> False` slipped the self-successor gate and
    the row was recorded as its OWN successor (measured on the cycle-7 head).

    Mutation: compare the caller's objects -> red.
    """

    class NeverEqual(str):
        __eq__ = lambda s, other: False        # noqa: E731
        __ne__ = lambda s, other: True         # noqa: E731
        __hash__ = str.__hash__

    node = _make(sdk, "Object", "selfeq")
    with pytest.raises(ValueError, match="cannot be its own successor"):
        sdk.supersede_knowledge(NeverEqual(node), NeverEqual(node))
    assert _row(sdk, "Object", node)[0] is None, (
        "a refused self-supersede still moved the row")


# ── #12914 review: the IMPOSTOR class, at every site this door reads ──────

class _Impostor:
    """Declares ``__class__ = str`` WITHOUT being one (#12907's class).

    ``isinstance(x, str)`` consults ``__class__`` and answers True; ``str.__str__``
    and the driver's ``str`` branch both refuse it. Nothing here overrides
    ``__eq__`` — the crash is the forged ``__class__`` alone, so a fix that only
    hardened ``__eq__`` would not close this.
    """

    __class__ = str

    def __repr__(self) -> str:                 # a stable label in a message
        return "<impostor>"


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_an_impostor_status_still_answers_the_carrier_refusal(sdk, label):
    """the write door's carrier guard reads the status at BASE CONTENT — safely.

    Measured on the #12914-reviewed head ``6823ed2b84``: ``update_knowledge(<live
    Object>, status=<impostor>, supersededBy='y')`` escaped as ``TypeError:
    descriptor '__str__' requires a 'str' object`` (sdk.py:5806) where the same
    call with ``status='live'`` answers the documented carrier ``ValueError``.
    Fail-closed either way — the row does not move — but the door's contract is
    a ``ValueError``, and a ``TypeError`` out of a guard is not a refusal a
    caller can act on.

    Mutation: spell the base-content gate ``isinstance(status, str)`` ->
    TypeError instead of ValueError.
    """
    node = _make(sdk, label, f"impostor-status-{label.lower()}")
    before = _row(sdk, label, node)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status=_Impostor(), supersededBy="v2")
    msg = str(exc.value)
    assert "lifecycle CARRIER" in msg, msg
    assert f":{label}" in msg, f"the refusal must name the label: {msg!r}"
    assert _row(sdk, label, node) == before, (
        "a refused impostor write moved the row: "
        f"{before} -> {_row(sdk, label, node)}")


@pytest.mark.parametrize("label", ["Point", "Object", "Subject"])
def test_an_impostor_successor_answers_the_same_missing_node_refusal(sdk, label):
    """``supersede_knowledge(<live>, <impostor>)`` answers ``no <label> <id>``, not a TypeError.

    Measured at ``6823ed2b84``: ``TypeError: descriptor '__str__' requires a
    'str' object`` at the self-successor gate (sdk.py:22819), where
    ``supersede_knowledge(<live>, 'no-such-node')`` answers ``supersede: no :Object …``.
    A value the graph cannot encode names no node, so it must reach that same
    refusal.

    ⛔ THE POINT ARM IS ITS OWN CASE (#12914 review P2, cycle 5): the first cut of
    this fix only bounded the Object/Subject dispatch, so
    ``supersede_knowledge(<live Point>, <impostor>)`` still escaped as ``TypeError:
    argument of type '…' is not iterable`` (measured) out of
    ``_assert_lifecycle_guard`` — the default and most-used family, untested
    because the parametrization stopped at the two entity labels. Its documented
    answer is the Point doors' own ``No point <id>``, which every GENUINE
    non-``str`` successor (``123``, ``None``) already gets; the ``No point``
    cases below keep that fall-through from being narrowed.

    Mutation: spell either base-content gate ``isinstance(x, str)`` -> TypeError;
    drop the non-``str`` short-circuit in ``_rows`` -> TypeError inside the
    query; delete the Point arm's successor bound -> ``TypeError`` on the Point
    case alone.
    """
    node = _make(sdk, label, f"impostor-new-{label.lower()}")
    before = _row(sdk, label, node)
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(node, _Impostor())
    msg = str(exc.value)
    expected = "No point" if label == "Point" else f"no :{label}"
    assert expected in msg, f"the missing-successor refusal must land: {msg!r}"
    assert _row(sdk, label, node) == before, (
        "a refused impostor successor moved the row: "
        f"{before} -> {_row(sdk, label, node)}")
    # The bound is the FORGED-CLASS shape, not a blanket `str`-only door.
    for genuine in (123, None):
        with pytest.raises(ValueError) as exc:
            sdk.supersede_knowledge(node, genuine)
        assert expected in str(exc.value), exc.value
        assert _row(sdk, label, node) == before, (
            f"a refused {genuine!r} successor moved the row: "
            f"{before} -> {_row(sdk, label, node)}")


def test_an_impostor_old_id_is_refused_at_this_door(sdk):
    """``supersede_knowledge(<impostor>, <id>)`` is a ``ValueError``, never a ``TypeError``.

    Measured at ``6823ed2b84``: ``TypeError: argument of type 'Imp' is not
    iterable`` out of the driver's parameter check, reached from
    ``_supersession_label``'s query (sdk.py:22756) — where every GENUINE
    unusable id answers ``No point <id>``. The impostor cannot take that
    fall-through (`supersede_point` queries it too, and the driver's
    ``isinstance``-shaped check refuses it there), so the door refuses it here,
    where the documented contract is a ``ValueError``.

    Mutation: delete the impostor refusal (or the non-``str`` short-circuit in
    ``_supersession_label``) -> TypeError.
    """
    node = _make(sdk, "Object", "impostor-old")
    before = _row(sdk, "Object", node)
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(_Impostor(), node)
    msg = str(exc.value)
    assert "is not a node id" in msg, msg
    assert "cannot encode it" in msg, (
        f"the refusal must say the graph cannot carry the value: {msg!r}")
    assert _row(sdk, "Object", node) == before, (
        "a refused impostor old_id moved the row: "
        f"{before} -> {_row(sdk, 'Object', node)}")


@pytest.mark.parametrize("value", [123, None, [], (1, 2), object()])
def test_every_GENUINE_non_str_id_keeps_the_documented_no_point_answer(sdk,
                                                                     value):
    """The impostor bound must not become a blanket ``str``-only door.

    Every one of these answers ``No point <id>`` today (measured on the head
    this suite runs at), and the #12914 fix must leave that untouched: it bounds
    the FORGED-``__class__`` class only, because those values ARE encodable by
    the driver (it degrades them to null, so nothing matches) and the impostor is
    not.

    Mutation: widen the door's impostor refusal to ``not issubclass(type(x),
    str)`` -> all five red with the impostor's message instead of ``No point``.
    """
    node = _make(sdk, "Object", f"genuine-non-str-{type(value).__name__}")
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(value, node)
    msg = str(exc.value)
    assert msg.startswith("No point "), (
        f"a genuine non-`str` id must keep the Point doors' documented answer: "
        f"{msg!r}")
    assert _row(sdk, "Object", node)[0] is None, (
        "a refused non-`str` old_id moved the row")


class _ImpostorBoomRepr(_Impostor):
    """An impostor that also raises in ``__repr__`` (#12907's second lesson).

    A refusal interpolates the refused operand, and for a caller object that is
    the CALLER'S ``__repr__`` — so a refusal that spells ``!r`` escapes as the
    caller's exception class instead of the promised ``ValueError``. #12907 fixed
    this for ``update_entity`` with ``_refusal_repr``; the arms this review round
    added must use it too.
    """

    def __repr__(self) -> str:
        raise RuntimeError("repr-boom")


def test_a_hostile_repr_cannot_replace_THIS_doors_new_refusals(sdk):
    """Both arms added for the impostor class answer ``ValueError``, not ``repr``.

    Measured on this fix's first cut: ``supersede_knowledge(<impostor with a raising
    ``__repr__``>, <id>)`` -> ``RuntimeError: repr-boom`` from the new door
    refusal, and ``supersede_knowledge(<live Object>, <same>)`` -> ``RuntimeError`` from
    the missing-successor message, i.e. the arms escaped as the caller's own
    exception class. Not fail-open (no write, row unchanged) but the contract is
    the exception CLASS.

    Mutation: spell either arm ``{old_id!r}`` / ``{new_id!r}`` instead of
    ``_refusal_repr(...)`` -> ``RuntimeError`` instead of ``ValueError``.
    """
    node = _make(sdk, "Object", "hostile-repr")
    before = _row(sdk, "Object", node)

    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(_ImpostorBoomRepr(), node)
    assert "is not a node id" in str(exc.value), exc.value

    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(node, _ImpostorBoomRepr())
    assert "no :Object" in str(exc.value), (
        f"the missing-successor refusal must land: {exc.value!r}")

    assert _row(sdk, "Object", node) == before, (
        "a refused hostile-repr call moved the row: "
        f"{before} -> {_row(sdk, 'Object', node)}")


def _raise_name_boom():
    raise RuntimeError("name-boom")


class _BoomNameMeta(type):
    """A metaclass whose ``__name__`` raises (#12914 review P3, measured).

    The door's impostor bound ADMITS this class (``isinstance(x, str)`` is True
    through the forged ``__class__``; ``issubclass(type(x), str)`` is False), so
    the refusal reaches the one caller operand ``_refusal_repr`` cannot cover:
    the CLASS NAME the message interpolates.
    """

    __name__ = property(lambda self: _raise_name_boom())


class _ImpostorBoomName(_Impostor, metaclass=_BoomNameMeta):
    """An impostor whose class name cannot be read."""


class _ImpostorBoomBoth(_ImpostorBoomRepr, metaclass=_BoomNameMeta):
    """Raises in BOTH caller reads: ``__repr__`` and the class name."""


def test_an_impostor_whose_class_name_raises_still_answers_this_door(sdk):
    """The refusal interpolates the class NAME — so that read must be safe too.

    Measured on this fix's second cut: ``supersede_knowledge(<impostor whose metaclass
    raises in ``__name__``>, <id>)`` escaped as ``RuntimeError: name-boom`` from
    the new door refusal, while the sibling arm
    ``supersede_knowledge(<live Object>, <same>)`` answered its ``no :Object`` ValueError.
    Not fail-open (no write, row unchanged), but the door's contract is the
    exception CLASS, and the name is a caller operand just as the ``repr`` is.

    Mutation: spell the refusal ``{type(old_id).__name__}`` instead of
    ``{_refusal_type_name(old_id)}`` -> ``RuntimeError: name-boom``.
    """
    node = _make(sdk, "Object", "impostor-boom-name")
    before = _row(sdk, "Object", node)
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(_ImpostorBoomName(), node)
    msg = str(exc.value)
    assert "is not a node id" in msg, msg
    assert "metaclass raises" in msg, (
        f"the refusal must name the unreadable class instead of crashing: {msg!r}")
    # Both reads hostile at once: the ``repr`` fallback calls the name helper,
    # so the second guard must hold inside the first one's fallback too.
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(_ImpostorBoomBoth(), node)
    assert "is not a node id" in str(exc.value), exc.value

    assert _row(sdk, "Object", node) == before, (
        "a refused boom-name impostor moved the row: "
        f"{before} -> {_row(sdk, 'Object', node)}")


class _BoomFmt:
    """A value whose EVERY rendering raises — a hostile ``__name__``'s answer."""

    def __format__(self, spec):
        raise RuntimeError("fmt-boom")

    def __repr__(self) -> str:
        raise RuntimeError("fmt-boom")

    __str__ = __repr__


class _FmtBoomMeta(type):
    """A metaclass whose ``__name__`` ANSWERS, but not with a ``str``.

    Guarding the attribute READ is not enough (measured: ``RuntimeError:
    fmt-boom`` from the f-string), so the helper must also refuse the ANSWER.
    """

    __name__ = property(lambda self: _BoomFmt())


class _ImpostorFmtBoom(_Impostor, metaclass=_FmtBoomMeta):
    """An impostor whose class name is unrenderable rather than raising."""


class _ImpostorBoomReprFmt(_ImpostorBoomRepr, metaclass=_FmtBoomMeta):
    """Both reads hostile: ``__repr__`` raises AND the class name is unrenderable."""


def test_a_class_name_that_is_not_a_str_cannot_escape_the_refusal(sdk):
    """A ``__name__`` that ANSWERS with a non-``str`` must not reach the f-string.

    Measured on this fix's third cut: a metaclass returning an object whose
    ``__format__``/``__str__``/``__repr__`` raise made
    ``supersede_knowledge(<impostor>, <id>)`` escape as ``RuntimeError: fmt-boom`` at BOTH
    call sites of ``_refusal_type_name`` (the door's message and
    ``_refusal_repr``'s fallback), where the contract is ``ValueError``.
    Fail-closed — the row does not move — but the contract is the exception CLASS.

    Mutation: drop ``return name if type(name) is str else …`` and return the
    bare ``name`` -> ``RuntimeError: fmt-boom`` on both arms.
    """
    node = _make(sdk, "Object", "impostor-fmt-boom")
    before = _row(sdk, "Object", node)

    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(_ImpostorFmtBoom(), node)
    msg = str(exc.value)
    assert "is not a node id" in msg, msg
    assert "its name is not a str" in msg, (
        f"the refusal must say the name is unrenderable: {msg!r}")

    # The `_refusal_repr` fallback path: both reads hostile at once.
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(_ImpostorBoomReprFmt(), node)
    assert "is not a node id" in str(exc.value), exc.value

    assert _row(sdk, "Object", node) == before, (
        "a refused fmt-boom impostor moved the row: "
        f"{before} -> {_row(sdk, 'Object', node)}")


class _FmtBoomKey(str):
    """A GENUINE ``str`` subclass whose ``__repr__`` ANSWERS with a bad subclass."""

    def __repr__(self):
        return _FmtBoomAnswer("'supersededBy'")


class _FmtBoomAnswer(str):
    """A ``str`` subclass whose ``__format__`` raises.

    CPython ACCEPTS a ``__repr__`` that answers with a ``str`` subclass, so
    guarding the ``repr`` CALL is not enough: the f-string then renders the
    ANSWER through that subclass's own ``__format__``.
    """

    def __format__(self, spec):
        raise RuntimeError("fmt-boom")


class _ImpostorFmtAnswer(_Impostor):
    """An impostor whose ``__repr__`` answers with an unrenderable subclass."""

    def __repr__(self):
        return _FmtBoomAnswer("<imp-fmt>")


def test_a_repr_that_ANSWERS_with_a_bad_subclass_cannot_escape_the_refusal(sdk):
    """`_refusal_repr` must bound the ANSWER, not just guard the call.

    Measured on this fix's fourth cut (#12914 review P3): a caller ``__repr__``
    returning a ``str`` subclass whose ``__format__`` raises made
    ``supersede_knowledge(<impostor>, <id>)`` -> ``RuntimeError: fmt-boom``, and the same
    value reached the PRE-EXISTING ``{status!r}`` carrier message this PR's F1
    fix sits beside. Fail-closed (no write) but the contract is the exception
    CLASS, and it is the same rule ``_refusal_type_name`` already states for the
    class NAME.

    Mutation: ``return repr(value)`` unchecked -> ``RuntimeError: fmt-boom`` on
    every arm; spell the carrier message ``status={status!r}`` /
    ``{key!r}`` -> ``RuntimeError`` on arms 3 and 4.
    """
    point = _make(sdk, "Point", "fmt-answer-point")
    obj = _make(sdk, "Object", "fmt-answer-object")
    before_p, before_o = _row(sdk, "Point", point), _row(sdk, "Object", obj)

    # 1. the Point successor arm (the value this cycle added a bound for).
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(point, _ImpostorFmtAnswer())
    assert "No point <imp-fmt>" in str(exc.value), exc.value

    # 2. the old-id arm (this PR's door refusal).
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(_ImpostorFmtAnswer(), obj)
    assert "is not a node id" in str(exc.value), exc.value
    assert "but its class is" in str(exc.value), (
        f"the class name is the fallback's second read: {str(exc.value)!r}")

    # 3. the carrier message's `status=` operand.
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(obj, status=_ImpostorFmtAnswer(), supersededBy="v2")
    assert "lifecycle CARRIER" in str(exc.value), exc.value

    # 4. the carrier message's KEY operand — a genuine `str` subclass is admitted
    #    as a keyword key by CPython's own check.
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(obj, status="live", **{_FmtBoomKey("supersededBy"): "v2"})
    assert "lifecycle CARRIER" in str(exc.value), exc.value

    assert _row(sdk, "Point", point) == before_p, (
        f"a refused fmt-answer successor moved the row: {before_p} -> "
        f"{_row(sdk, 'Point', point)}")
    assert _row(sdk, "Object", obj) == before_o, (
        f"a refused fmt-answer write moved the row: {before_o} -> "
        f"{_row(sdk, 'Object', obj)}")


class _BoomNameKey(str, metaclass=_BoomNameMeta):
    """A GENUINE ``str`` subclass whose class name raises.

    ``**props`` admits it (CPython's keyword check is a ``str`` instance check,
    which a subclass satisfies) and the exotic-key guard's own filter keeps it
    (``type(k) is not str``), so its class name reaches that refusal's message.
    """


def test_an_exotic_key_whose_class_name_raises_is_still_a_refusal(sdk):
    """The exotic-key refusal groups class NAMES — that read must be safe too.

    Measured on this fix's third cut: ``update_knowledge(<live Object>,
    status='superseded', **{<str subclass with a raising ``__name__``>: 1})``
    escaped as ``RuntimeError: name-boom`` from the ``type(k).__name__`` set
    comprehension, where the benign control (a plain ``str`` subclass key)
    answers the documented "not plain `str`" ValueError — the same class this
    whole review round is about, in a refusal this PR adds.

    Mutation: spell the message ``{type(k).__name__ for k in _exotic_keys}`` ->
    ``RuntimeError: name-boom`` instead of ``ValueError``.
    """
    node = _make(sdk, "Object", "exotic-key-boom-name")
    before = _row(sdk, "Object", node)

    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="superseded", **{_BoomNameKey("harmless"): 1})
    msg = str(exc.value)
    assert "are not plain `str`" in msg, msg
    assert "metaclass raises" in msg, (
        f"the refusal must name the unreadable class instead of crashing: {msg!r}")
    assert _row(sdk, "Object", node) == before, (
        "a refused exotic-key write moved the row: "
        f"{before} -> {_row(sdk, 'Object', node)}")


@pytest.mark.parametrize("spelling", ["supersededBy", "superseded_by"])
def test_a_non_str_successor_on_a_word_keeping_write_is_the_clears_refusal(sdk, spelling):
    """A carrier value that is not genuinely ``str`` is not a name — and not a crash.

    Measured on this fix's fifth cut (#12914 review P2, cycle 5): the
    word-keeping refusal decided "is a value supplied?" with
    ``isinstance(v, str) and v.strip()`` — ``isinstance`` consults ``__class__``,
    so an impostor passed it and ``.strip()`` raised
    ``AttributeError: '…' object has no attribute 'strip'`` for BOTH spellings,
    where ``None``, ``''`` and ``123`` answer the documented
    "⛔ This call CLEARS ``supersededBy``" ValueError. Same root as the three
    sites this PR already reads at base content (`:5808`, `:22906`, `:48611`):
    the graph cannot encode the value, so it names nobody.

    The snake spelling keeps its own branch for a GENUINE ``str`` value (the
    shadow-column refusal, pinned by its own case); a non-``str`` there answers
    the same ValueError from the clears branch, because it is not a name at all.

    Mutation: ``isinstance(v, str) and v.strip()`` -> ``AttributeError`` (both rows).
    """
    node = _make(sdk, "Object", f"nonstr-successor-{spelling.lower()}")
    before = _row(sdk, "Object", node)

    for value in (_Impostor(), _ImpostorBoomRepr()):
        with pytest.raises(ValueError) as exc:
            sdk.update_knowledge(node, status="superseded", **{spelling: value})
        msg = str(exc.value)
        assert "CLEARS" in msg, (
            f"a non-str successor must reach the clears refusal, not a crash: {msg!r}")
        assert _row(sdk, "Object", node) == before, (
            f"a refused {type(value).__name__} successor moved the row: "
            f"{before} -> {_row(sdk, 'Object', node)}")

    # The benign control: a GENUINE str under the shadow spelling keeps the
    # shadow-column branch, so the bound did not swallow it.
    if spelling == "superseded_by":
        with pytest.raises(ValueError) as exc:
            sdk.update_knowledge(node, status="superseded", superseded_by="v2")
        assert "NO READER RESOLVES" in str(exc.value), str(exc.value)
        assert _row(sdk, "Object", node) == before, (
            f"a refused shadow-spelled write moved the row: "
            f"{before} -> {_row(sdk, 'Object', node)}")


class _ImpostorEqBoom(_Impostor):
    """An impostor whose ``__eq__`` raises — ``==`` is a caller read too."""

    __hash__ = None

    def __eq__(self, other):
        raise RuntimeError("eq-boom")


def test_the_self_successor_gate_does_not_dispatch_to_the_caller(sdk):
    """``_old_word == _new_word`` must not run the caller's ``__eq__``.

    Measured on this fix's sixth cut (#12914 review P2, cycle 7):
    ``supersede_knowledge(<live Object>, <impostor defining a raising ``__eq__``>)`` raised
    ``RuntimeError: eq-boom`` from the self-successor comparison, where the same
    call with a plain impostor answers ``supersede: no :Object <id>``. The gate
    now runs only when BOTH operands are genuinely ``str`` — the only case in
    which two "words" exist — so the value falls through to `_rows`' documented
    "names no node" answer instead of executing caller code.

    Mutation: drop the ``issubclass(type(...), str)`` pair from the gate
    condition -> ``RuntimeError: eq-boom``.
    """
    node = _make(sdk, "Object", "eq-boom-successor")
    before = _row(sdk, "Object", node)

    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(node, _ImpostorEqBoom())
    msg = str(exc.value)
    assert "no :Object" in msg, (
        f"the missing-successor refusal must land, not a caller exception: {msg!r}")
    assert _row(sdk, "Object", node) == before, (
        f"a refused eq-boom successor moved the row: {before} -> "
        f"{_row(sdk, 'Object', node)}")

    # The genuine self-successor refusal is unchanged.
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(node, node)
    assert "must differ" in str(exc.value), str(exc.value)


class _StrSubLyingEq(str):
    """A GENUINE ``str`` subclass that answers ``__eq__`` with a LIE.

    ``str.__eq__`` returns ``NotImplemented`` for a subclass that overrides
    ``__eq__``, so the subclass's answer is the one a bare ``==`` lands on.
    """

    def __eq__(self, other):
        return False

    def __ne__(self, other):
        return True

    __hash__ = str.__hash__


class _StrSubRaisingEq(str):
    """A GENUINE ``str`` subclass whose ``__eq__`` raises."""

    def __eq__(self, other):
        raise RuntimeError("eq-boom")

    __hash__ = str.__hash__


def _corrects_self(s, node_id: str) -> list:
    """The ``CORRECTS`` edges INTO ``node_id``, as ``[from_id]`` rows."""
    return [list(r) for r in s._get_proj().g.query(
        "MATCH (a:Point)-[:CORRECTS]->(b:Point {id:$id}) RETURN a.id",
        params={"id": node_id}).result_set]


def test_the_POINT_arms_self_successor_gate_does_not_dispatch_to_the_caller(sdk):
    """``supersede_point``'s ``old_id == new_id`` must compare BASE CONTENT.

    Measured on this fix's eighth cut (#12914 review P2, cycle 9): a GENUINE
    ``str`` subclass whose ``__eq__`` answers False walked
    ``supersede_knowledge(<live Point>, SubLyingEq(<the same id>))`` past the self-successor
    gate — ``str.__eq__`` answers ``NotImplemented`` for a subclass that
    overrides ``__eq__``, so the CALLER's answer is the one that lands — and the
    write stored the row as its OWN successor (row
    ``['superseded', True, None]`` with a ``CORRECTS`` edge ``<id> -> <id>``),
    the exact state the gate exists to refuse. This is the POINT arm of the root
    the ENTITY arm was bounded for in cycle 7; both arms now compare base content
    and run the gate only when BOTH operands are genuinely ``str``.

    Mutation: replace the ``issubclass(type(...), str)`` pair with a bare
    ``old_id == new_id`` -> the lying-``__eq__`` case is ACCEPTED and the
    ``CORRECTS`` self-edge is written.
    """
    for successor in (_StrSubLyingEq, _StrSubRaisingEq):
        node = _make(sdk, "Point", f"point-arm-{successor.__name__.lower()}")
        before = _row(sdk, "Point", node)

        with pytest.raises(ValueError) as exc:
            sdk.supersede_knowledge(node, successor(node))
        assert "must differ" in str(exc.value), (
            f"{successor.__name__}: the self-successor refusal must land, not "
            f"{str(exc.value)!r}")
        assert _row(sdk, "Point", node) == before, (
            f"{successor.__name__}: a refused self-supersession moved the row: "
            f"{before} -> {_row(sdk, 'Point', node)}")
        assert not _corrects_self(sdk, node), (
            f"{successor.__name__}: a refused self-supersession wrote a "
            f"CORRECTS self-edge: {_corrects_self(sdk, node)}")

        # The gate lives in ``supersede_point``, so the DIRECT door is pinned too.
        with pytest.raises(ValueError) as exc:
            sdk.supersede_point(node, successor(node))
        assert "must differ" in str(exc.value), str(exc.value)

    # The genuine self-successor refusal is unchanged, and a DIFFERENT successor
    # still lands (the bound did not become a blanket refusal).
    node = _make(sdk, "Point", "point-arm-plain")
    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(node, node)
    assert "must differ" in str(exc.value), str(exc.value)

    other = _make(sdk, "Point", "point-arm-successor")
    sdk.supersede_knowledge(node, other)
    assert [other] in _corrects_self(sdk, node), (
        f"a genuine successor must still be recorded: {_corrects_self(sdk, node)}")


class _PlainStrSubclassName(str):
    """A GENUINE ``str`` subclass — a usable value everywhere except here."""


def test_a_camel_str_subclass_successor_is_not_diagnosed_as_a_shadow_spelling(sdk):
    """The repair must name the ACTUAL cause: the value's class, not the spelling.

    Measured on this fix's sixth cut (#12914 review P3, cycle 7):
    ``update_knowledge(<live Object>, status='superseded', supersededBy=Name('v2'))``
    was refused with "supplies the successor under a spelling NO READER
    RESOLVES (`supersededBy`)" — false: `supersededBy` IS the resolving spelling
    and `supersededBy='v2'` is admitted. The shadow-column diagnosis is now
    reserved for a value supplied under the SNAKE spelling.

    Mutation: drop ``and not _camel_supplied`` -> the false diagnosis returns.
    """
    node = _make(sdk, "Object", "camel-str-subclass")
    before = _row(sdk, "Object", node)

    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="superseded",
                          supersededBy=_PlainStrSubclassName("v2"))
    msg = str(exc.value)
    assert "NO READER RESOLVES" not in msg, (
        f"the camel spelling DOES resolve — this diagnosis is false: {msg!r}")
    assert "CLEARS" in msg, msg

    # The snake spelling keeps its own (correct) diagnosis.
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(node, status="superseded", **{"superseded_by": "v2"})
    assert "NO READER RESOLVES" in str(exc.value), str(exc.value)

    assert _row(sdk, "Object", node) == before, (
        f"a refused str-subclass successor moved the row: {before} -> "
        f"{_row(sdk, 'Object', node)}")


class _StrSubLyingRender(str):
    """A GENUINE ``str`` subclass whose WIRE RENDERING is not its base content.

    ``falkordb.helpers.quote_string`` renders a parameter through
    ``v.replace(...)`` and the caller's ``__format__``, so overriding
    ``replace`` changes the bytes the engine receives while ``str.__str__``
    still answers the base content the gate compares. That gap is the
    guard/wire split #12914 review P1 measured.
    """

    render_as = ""

    def replace(self, sub, rep, *args):  # noqa: D401 - the driver's own call
        return self.render_as

    __hash__ = str.__hash__


def test_a_SUCCESSOR_that_only_RENDERS_as_the_old_id_is_not_a_self_supersession(sdk):
    """The guard must not compare a value the write does not send (#12914 P1).

    Measured on the reviewed head: an id whose BASE CONTENT is ``"fff"`` but
    which renders as the live row's id walked ``supersede_knowledge(<Object>,
    <that id>)`` past the self-successor gate — the gate compared base content,
    the probe and the write sent the RENDERED id — and the row was stored as
    ``['MinOld', 'superseded', 'MinOld']``: the row naming ITSELF as its
    successor, the exact state ``old_id != new_id`` exists to refuse. On the
    Point arm the same call wrote ``['superseded', True, None]`` plus a self
    ``CORRECTS`` edge.

    Mutation: probe and write the CALLER'S OBJECT while the gate compares base
    content (i.e. drop the `old_id = _old_word` / `new_id = _new_word` rebind in
    ``_supersede_entity``, or the ``str.__str__`` rebind in
    ``supersede_point``) -> both arms accept and store the self-successor.
    """
    for label in ("Point", "Object"):
        node = _make(sdk, label, f"render-first-{label.lower()}")
        before = _row(sdk, label, node)

        successor = _StrSubLyingRender("fff")
        successor.render_as = node

        with pytest.raises(ValueError):
            sdk.supersede_knowledge(node, successor)

        assert _row(sdk, label, node) == before, (
            f"{label}: a refused self-supersession moved the row: "
            f"{before} -> {_row(sdk, label, node)}")
        row = _row(sdk, label, node)
        assert row is None or row[1] != node, (
            f"{label}: the row names itself as its own successor: {row}")
        if label == "Point":
            assert not _corrects_self(sdk, node), (
                f"Point: a self CORRECTS edge was written: {_corrects_self(sdk, node)}")

        # The DIRECT door is pinned too — it carries its own copy of the gate.
        with pytest.raises(ValueError):
            sdk.supersede_point(node, successor) if label == "Point" \
                else sdk.supersede_knowledge(node, successor)


def test_a_no_status_update_on_a_LEGACY_successorless_row_is_not_refused(sdk):
    """The guard adjudicates what the CALL writes, not the row's stored word.

    Measured on the reviewed head (#12914 review P2): with the stored status used
    as a fallback, a call that named NO status was still adjudicated against it,
    so on a legacy successorless-superseded ``:Object``/``:Subject`` the
    legitimate temporal fields were refused — ``update_knowledge(<legacy
    superseded Object>, expiredAt=…)`` and the same with ``validTo=…`` raised
    "refused status='superseded' … with no successor", naming a field the caller
    never sent. That contradicts this PR's own statement that a graph already
    holding the shape "is untouched", and it hits exactly the population
    ``tests/test_11826``'s audit exists to repair.

    Mutation: restore ``effective = word_after or stored_status`` -> both calls
    below raise.
    """
    for label in ("Object", "Subject"):
        node = _make(sdk, label, f"legacy-window-{label.lower()}")
        # The legacy shape: the word with no carrier, written under the guard.
        sdk._get_proj().g.query(
            f"MATCH (n:{label} {{id:$id}}) SET n.status='superseded', "
            "n.supersededBy=NULL",
            params={"id": node},
        )
        assert _row(sdk, label, node)[0] == "superseded"

        # A window-only revision names no lifecycle word: it must land.
        sdk.update_knowledge(node, expiredAt="2026-01-01T00:00:00Z")
        sdk.update_knowledge(node, validTo="2026-01-02T00:00:00Z")
        # ...and the legacy row is still the shape it was (nothing re-minted).
        assert _row(sdk, label, node)[1] is None


def test_transfer_edges_is_refused_on_the_family_arm_in_BOTH_directions(sdk):
    """An explicit ``transfer_edges`` the arm cannot honour is REFUSED, not dropped.

    Measured on the reviewed head (#12914 review P3): the parameter defaulted to
    ``True``, so on an ``:Object``/``:Subject`` an EXPLICIT ``True`` was silently
    dropped — no edges transferred, no ``edges_transferred`` in the return —
    while ``False`` on the same labels was refused. That is the one-arm-per-call
    rule this file states for the Point arm (#10053) applied unevenly.

    Mutation: restore the ``transfer_edges: bool = True`` default (an explicit
    ``True`` then takes the arm silently) -> the ``True`` case below reds.
    """
    node = _make(sdk, "Object", "family-transfer-explicit-true")
    successor = _make(sdk, "Object", "family-transfer-explicit-true-v2")

    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(node, successor, True)
    assert "transfer_edges=True" in str(exc.value), str(exc.value)
    assert _row(sdk, "Object", node)[0] != "superseded", (
        "a refused keyword must cost no write")

    with pytest.raises(ValueError) as exc:
        sdk.supersede_knowledge(node, successor, False)
    assert "transfer_edges=False" in str(exc.value), str(exc.value)

    # The omitted keyword still takes the family arm.
    out = sdk.supersede_knowledge(node, successor)
    assert out["superseded"] is True, out
    assert _row(sdk, "Object", node)[0] == "superseded"


def test_the_ENTRY_ARMS_resolve_every_id_on_the_bytes_the_write_sends(sdk):
    """The rebind must sit ABOVE the label read and the lifecycle guards.

    Measured at the parent commit (#12914 re-review finding 3), where the rebind
    lived inside ``_supersede_entity`` — below ``_supersession_label`` and below
    ``supersede_point``'s source guard:

    * TERMINAL-GUARD BYPASS: an ``old_id`` whose BASE CONTENT is an
      already-``superseded`` Point but whose ``replace()`` renders a LIVE Point
      read as live, so ``supersede_point`` ACCEPTED and appended a SECOND
      ``PointSuperseded`` for a dead claim — the #2498 "re-terminalize a dead
      claim" harm ``_assert_lifecycle_guard`` exists to prevent.
    * PHANTOM SUCCESS: an ``old_id`` whose base content is an ``:Object`` and
      that renders a ``:Point`` resolved AS A POINT, returned
      ``{'invalidated': True}`` and wrote NOTHING — a record/graph divergence
      reported as success.

    Mutation: move either rebind below the label read / the source guard -> both
    cases below stop refusing (and the second returns success having written
    nothing).
    """
    # ── case A: terminal BASE, live RENDER, source operand ────────────────────
    dead = _make(sdk, "Point", "render-src-dead")
    sdk.supersede_knowledge(dead, _make(sdk, "Point", "render-src-dead-v2"))
    assert _row(sdk, "Point", dead)[0] == "superseded"

    live = _make(sdk, "Point", "render-src-live")
    live_before = _row(sdk, "Point", live)

    render_live = _StrSubLyingRender(dead)
    render_live.render_as = live

    with pytest.raises(ValueError) as exc:
        sdk.supersede_point(render_live, _make(sdk, "Point", "render-src-succ"))
    assert "already terminal" in str(exc.value), (
        f"the source guard read the RENDERED id instead of the base content: "
        f"{exc.value!r}")
    assert _row(sdk, "Point", live) == live_before, (
        f"a refused call moved the render target: {_row(sdk, 'Point', live)}")

    # ── case B: an :Object that RENDERS as a :Point ───────────────────────────
    obj = _make(sdk, "Object", "render-cross")
    point = _make(sdk, "Point", "render-cross-point")
    point_before = _row(sdk, "Point", point)

    render_point = _StrSubLyingRender(obj)
    render_point.render_as = point

    out = sdk.supersede_knowledge(render_point, _make(sdk, "Object", "render-cross-v2"))
    assert _row(sdk, "Point", point) == point_before, (
        f"the call resolved the RENDERED :Point and touched it: "
        f"{_row(sdk, 'Point', point)}")
    assert not out.get("invalidated"), (
        f"the call took the Point INVALIDATE arm for an :Object id it rendered "
        f"as a Point, having written nothing: {out}")
    assert _row(sdk, "Object", obj)[0] == "superseded", (
        f"a success that did not land on the row it names: {_row(sdk, 'Object', obj)}")


def test_transfer_edges_None_is_the_ONLY_default_and_other_falsy_values_keep_their_arm(sdk):
    """Pin the ARM SELECTION for every supplied value, not just the two singletons.

    #12914 re-review finding 4, measured at the parent commit: pinning
    ``is True`` / ``is False`` left the original defect reachable for every other
    value — on the family arm ``1``, ``0``, ``'yes'`` and ``1.0`` were still
    silently dropped — and keying the Point arm on ``is False`` re-pointed the
    falsy non-bools: ``transfer_edges=0`` / ``''`` took the SUPERSEED arm
    (``edges_transferred`` counted real edges) where ``not transfer_edges`` had
    always taken INVALIDATE, i.e. the *more* destructive arm.

    Mutations, each reddening one block below:
      * family arm keyed on ``is True`` -> the ``1`` / ``'yes'`` / ``1.0`` cases stop refusing;
      * Point arm keyed on ``is False``   -> the ``0`` / ``''`` cases supersede instead of invalidating.
    """
    node = _make(sdk, "Object", "arm-values")
    successor = _make(sdk, "Object", "arm-values-v2")
    for value in (True, False, 1, 0, "yes", 1.0):
        with pytest.raises(ValueError) as exc:
            sdk.supersede_knowledge(node, successor, value)
        assert "transfer_edges=" in str(exc.value), (value, str(exc.value))
    assert _row(sdk, "Object", node)[0] != "superseded", "a refused keyword wrote"

    # The omitted keyword is the ONLY value that takes the family arm.
    assert sdk.supersede_knowledge(node, successor)["superseded"] is True

    # A Point: a falsy non-bool keeps INVALIDATE (the arm it always took); the
    # omitted keyword keeps FULL SUPERSEED.
    for value in (0, "", []):
        p = _make(sdk, "Point", f"arm-point-falsy-{type(value).__name__}")
        out = sdk.supersede_knowledge(p, _make(sdk, "Point", f"arm-point-v2-{id(value)}"),
                                      value)
        assert out.get("invalidated") is True, (value, out)
        assert "edges_transferred" not in out, (value, out)

    p = _make(sdk, "Point", "arm-point-default")
    out = sdk.supersede_knowledge(p, _make(sdk, "Point", "arm-point-default-v2"))
    # ⛔ `invalidated` CANNOT TELL THE ARMS APART (#12914 round-3 finding 5).
    # `supersede_point` has ONE exit that returns `invalidated: True` for BOTH
    # arms, so the old assertion here passed whether the omitted keyword took
    # FULL SUPERSEED or INVALIDATE — the block's claim rested on the next line
    # alone. Measured on the reviewed head: the full arm answers
    # `{'invalidated': True, ..., 'edges_transferred': 0}` and the invalidate arm
    # answers `{'invalidated': True, ...}` with the key absent. Mutation: make the
    # `None` default fall to the invalidate arm -> the key-presence line reds.
    assert "edges_transferred" in out, out
    assert isinstance(out.get("edges_transferred"), int), out


def test_the_dry_run_preview_takes_the_same_arm_the_write_takes(sdk):
    """The preview and the write choose the SAME arm (#12914 round-3 finding 2).

    Measured on the reviewed head: the family write answered
    ``{'superseded': True, 'label': 'Object', 'superseded_by': 'fam-b'}`` while the
    IDENTICAL call with ``dry_run=True`` answered ``{'error': "No point '<id>'"}``
    — ``_preview_supersede`` was Point-only, so the one public route to a family
    supersede could not be previewed at all, and the ``dry_run`` contract ("it
    runs the same guards, so a rejected input is rejected in the preview too")
    was false in the other direction.

    Mutation: restore a Point-only ``_preview_supersede`` (drop the label
    dispatch) -> the family preview asserts red; restate the family keyword
    refusal in the preview instead of calling the write's helper -> the
    preview-vs-write equality reds on the first wording drift.
    """
    from tortoise import mcp_server as mcp
    from tortoise.mcp_auth import _current_org_id, _current_org_limits, _transport_mode

    _transport_mode.set("stdio")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    original = mcp._get_org_sdk
    mcp._get_org_sdk = lambda: sdk
    try:
        node = _make(sdk, "Object", "preview-arm")
        successor = _make(sdk, "Object", "preview-arm-v2")

        preview = mcp.tortoise_supersede(node, successor, dry_run=True)
        assert preview.get("dry_run") is True, preview
        assert preview.get("superseded") is True, preview
        assert preview["target"]["superseded_by"] == "preview-arm-v2", preview
        assert _row(sdk, "Object", node)[0] != "superseded", "the preview wrote"

        # A keyword the family arm cannot honour is refused by the PREVIEW with
        # the WRITE's own words — one helper, compared value-for-value.
        refused_preview = mcp.tortoise_supersede(node, successor, True, dry_run=True)
        refused_write = mcp.tortoise_supersede(node, successor, True, dry_run=False)
        assert "transfer_edges=True" in refused_preview.get("error", ""), refused_preview
        assert refused_preview == refused_write, (refused_preview, refused_write)
        assert _row(sdk, "Object", node)[0] != "superseded", "a refusal wrote"

        # The Point arm keeps its edge walk (the family branch must not swallow it).
        point = _make(sdk, "Point", "preview-point")
        point_v2 = _make(sdk, "Point", "preview-point-v2")
        point_preview = mcp.tortoise_supersede(point, point_v2, dry_run=True)
        assert point_preview.get("dry_run") is True, point_preview
        assert point_preview.get("corrects_edge"), point_preview
        assert _row(sdk, "Point", point)[0] != "superseded", "the preview wrote"
    finally:
        mcp._get_org_sdk = original
        _transport_mode.set(None)
        _current_org_id.set(None)
        _current_org_limits.set(None)
