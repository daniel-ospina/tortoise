"""#12592 — the successor fact and the "is this current?" predicate.

ONTOLOGY §4.3 states the invariant for every entity that carries the word:

    ``superseded`` means replaced: it names the successor; a retirement with no
    successor is ``archived``; ``deprecated`` is an independent terminal state
    and needs no successor. A journal rebuild is unchanged and still restores a
    supersession recorded without a successor.

Measured on ``main`` at ``58f904ccf7`` (2026-11-15) on THIS checkout, embedded
engine, throwaway nodes — the defect, re-measured rather than inherited from the
filing lane:

    update_entity(<Point>,   status='superseded')  -> ACCEPTED  supersededBy=None
    update_entity(<Object>,  status='superseded')  -> ACCEPTED  supersededBy=None
    update_entity(<Subject>, status='superseded')  -> ACCEPTED  supersededBy=None
    update_entity(<Object>,  status='superseded', supersededBy='v2') -> ACCEPTED

and the door that reads as generic was Point-only:

    sdk.supersede(<Point>, <new>)    -> OK (CORRECTS + outdated + status)
    sdk.supersede(<Object>, <new>)   -> REFUSED  ValueError: No point '<id>'
    sdk.supersede(<Subject>, <new>)  -> REFUSED  ValueError: No point '<id>'

So an Object/Subject could say "replaced" through one door and never name what
replaced it through any door.

What this suite pins (each row names the mutation that reds it):

* A BARE ``status='superseded'`` is REFUSED on every label, writes nothing and
  journals nothing. Mutation: delete the
  ``self._assert_supersession_names_a_successor(...)`` call in
  ``_update_entity`` -> the three ``test_a_bare...`` cases red.
* The pair IS admitted through the same door (the guard is not a blanket ban on
  the word). Mutation: make ``_successor_named_by`` return ``False``
  unconditionally -> ``test_the_coherent_pair_is_admitted_through_the_same_door``
  reds.
* The successor channel is the CAMEL property in BOTH renderings. Mutation:
  pass the full carrier set (``_LIFECYCLE_CARRIER_COLUMNS``-derived) instead of
  ``(_SUCCESSOR_PROP,)`` to ``_carrier_prop_named_by`` in
  ``_successor_named_by`` -> ``test_a_shadow_spelling_does_not_name_a_successor``
  reds (and ``superseded_by`` would then leave the row `superseded` with the
  pointer every reader resolves absent).
* A STAMP / an empty name is not a name. Mutation: drop the blank/NULL test in
  ``_successor_named_by`` -> the ``supersededAt``-only and ``''`` cases red.
* The verdict is over EVERY bound row (#7822). Mutation: read one row
  (``rows[0]``) instead of the row-count-vs-named-count pair -> the fan-out case
  reds.
* ``sdk.supersede()`` dispatches on the OLD node's label: Point unchanged,
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
        sdk.update_entity(node, status="superseded")
    msg = str(exc.value)
    assert "no successor" in msg, msg
    assert f":{label}" in msg, (
        f"the refusal must name the label it refused: {msg!r}")
    assert "supersede(" in msg, (
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
            sdk.update_entity(node, status="superseded")
        assert f"ONTOLOGY {section}" in str(exc.value), (
            f"{label} refusal must cite ONTOLOGY {section}: {exc.value!r}")


def test_a_point_refusal_prescribes_retract_not_reserved_archived(sdk):
    """§5 declares `archived` RESERVED on Points, so naming it is unperformable."""
    node = _make(sdk, "Point", "reserved-archived")
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(node, status="superseded")
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
    sdk.update_entity(node, status="superseded",
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
    sdk.update_entity(node, status="superseded",
                      supersededBy=f"echo-{label.lower()}-v2")
    before = _row(sdk, label, node)
    sdk.update_entity(node, status="superseded")
    assert _row(sdk, label, node) == before


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
        sdk.update_entity(node, status="superseded", **carriers)
    assert _row(sdk, "Object", node)[0] != "superseded", (
        "a refused write still left the row claiming superseded")


def test_the_verdict_is_over_every_bound_row(sdk):
    """#7822's fan-out rule on the successor question.

    Two Objects share an id; ONE carries a successor. The write binds both, so
    the row that names nothing is the reason to refuse. Mutation: read a single
    row instead of the row/named PAIR -> this reds (the carried row would
    authorise the write).
    """
    g = sdk._get_proj().g
    shared = "fanout-12592"
    g.query("CREATE (:Object {id:$id, name:'fo-carried', status:'live', "
            "supersededBy:'fo-v2'})", params={"id": shared})
    g.query("CREATE (:Object {id:$id, name:'fo-bare', status:'live'})",
            params={"id": shared})
    with pytest.raises(ValueError, match="no successor"):
        sdk.update_entity(shared, status="superseded")
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
    result = sdk.supersede(old, new)
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
    result = sdk.supersede(old, new)
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
        sdk.supersede(node, node)


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_the_family_door_refuses_an_already_terminal_old(sdk, label):
    """Terminalization is one-way (#2498) — the same refusal the Point door makes."""
    old = _make(sdk, label, f"dead-old-{label.lower()}")
    sdk.update_entity(old, status="archived")
    with pytest.raises(ValueError, match="already terminal"):
        sdk.supersede(old, _make(sdk, label, f"dead-old-new-{label.lower()}"))


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_the_family_door_refuses_a_terminal_successor(sdk, label):
    old = _make(sdk, label, f"tnew-old-{label.lower()}")
    new = _make(sdk, label, f"tnew-new-{label.lower()}")
    sdk.update_entity(new, status="archived")
    with pytest.raises(ValueError, match="itself terminal"):
        sdk.supersede(old, new)
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
        sdk.supersede(old, subj)
    assert _row(sdk, "Object", old)[0] != "superseded"


def test_the_family_door_refuses_a_nameless_successor(sdk):
    """The carrier IS the name, so a nameless successor cannot be named."""
    g = sdk._get_proj().g
    g.query("CREATE (:Object {id:'nameless-12592', status:'live'})")
    old = _make(sdk, "Object", "nameless-old")
    with pytest.raises(ValueError, match="no name"):
        sdk.supersede(old, "nameless-12592")


def test_transfer_edges_false_is_refused_on_a_non_point(sdk):
    """The invalidate half is `outdated` + CORRECTS — a Point's machinery (§4.7)."""
    old = _make(sdk, "Object", "xfer-old")
    new = _make(sdk, "Object", "xfer-new")
    with pytest.raises(ValueError) as exc:
        sdk.supersede(old, new, transfer_edges=False)
    msg = str(exc.value)
    assert "Point-only" in msg, msg
    assert "status='archived'" in msg, (
        f"the refusal must prescribe the retirement §4.3 gives it: {msg!r}")
    assert _row(sdk, "Object", old)[0] != "superseded"


def test_a_missing_old_node_still_answers_the_point_shaped_message(sdk):
    """Absent input is unchanged: the door falls through to the Point path."""
    with pytest.raises(ValueError, match="No point"):
        sdk.supersede("no-such-node-12592",
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
    sdk.update_entity(node, status="superseded", supersededBy="unsup-v2",
                      supersededAt="2026-01-01T00:00:00Z")
    sdk.update_entity(node, status="live", supersededBy=None,
                      supersededAt=None)
    row = _row(sdk, "Object", node)
    assert row == ["live", None, None], (
        f"#12825's coherent un-supersede must keep working: {row}")


def test_a_status_only_lift_is_still_refused(sdk):
    """…and the incoherent half stays refused — the carriers would still say so."""
    node = _make(sdk, "Object", "unsup-only")
    sdk.update_entity(node, status="superseded", supersededBy="unsup-only-v2")
    with pytest.raises(ValueError, match="is TERMINAL for a :Object"):
        sdk.update_entity(node, status="live")
    assert _row(sdk, "Object", node)[0] == "superseded", (
        "a refused status-only lift still moved the row")
