"""#12586 — the draft→live promote arm ignored the legacy ``outdated`` flag.

Measured on ``main`` at ``58f904ccf7`` (embedded engine), the shape the issue
reports and ``docs/ONTOLOGY-CHANGELOG.md`` v3.35 records as a code lag against
the model:

    # status=NULL, outdated=true
    update_point(id, status='live')   -> ACCEPTED, lands ['live', outdated=true]
    update_entity(id, status='live')  -> REFUSED  ("already terminal (outdated)")

§5's ``outdated`` row states the rule the promote arm was not reading —
*"Terminal on every read surface and for every lifecycle transition"* — and
``update_entity``'s guard already applied it, so the promote arm was the one
door that produced a claim reading **terminal to every reader** while carrying
``status='live'``: the two-facts-disagree state the guard exists to prevent.

What this suite pins:

* the promote is REFUSED on a flag-dead point, and the refusal carries AX rule
  2's three parts (#10183): that it refused, why, and a repair the caller can
  actually perform — the #432 remedy (``retract_point``/``supersede_point``)
  is NOT performable here, because both verbs run the same terminal guard;
* the two Point doors now agree on the same row — whatever ``update_entity``
  refuses, ``update_point`` refuses, from the same shared predicate;
* every other promote-arm behaviour is UNCHANGED: the CLI backfill's
  NULL-status-without-the-flag shape still promotes (ONTOLOGY §5, "migration-
  only, not a drift"), a ``draft`` still promotes, a terminal STATUS keeps its
  #432 message, an already-live status-only promote stays the #1088 no-op, the
  operator arm is untouched, and a flag-dead row keeps the #7819/#3305
  capability that a NON-status revision is still writable;
* the guard is in the STATEMENT, not only in the diagnostic: a point that goes
  flag-dead between the pre-write reads and the guarded ``SET`` takes the same
  refusal through ``_promote_witness`` instead of retrying an unmatchable write
  out to the 7-14 s window and surfacing a graph abort;
* what this change did NOT touch, stated so the next reader is not misled: the
  SAME root is live on ``promote_point`` — a ``draft + outdated=true`` row
  (invalidated before it was ever promoted) is promoted to
  ``['live', outdated=true]`` there, because that door's terminality test is the
  local ``status != 'draft'`` reading. Measured; recorded at the door and on
  #12586 as a separate change, because fixing it rewrites a second verb's return
  contract and ``test_6239_promote_after_supersede.py``'s raw-restatus fixture.
  The two pins at the bottom of this file only prove that door is UNCHANGED.

Runnable (the lane's own engine, no container needed):

    uv run python tools/test_lane.py run -- \
        tests/test_12586_update_point_promote_flag.py -q
"""
from __future__ import annotations

import json
import logging
import types
import uuid

import pytest
import redis.exceptions as redis_exc

from tortoise.sdk import TortoiseSDK, _raise_update_point_status_error

#: The plain-``:Point`` promote statement's distinctive tail. The
#: ``:Point:Object`` arm bumps ``version`` BETWEEN ``$now,`` and ``n +=``, so
#: this needle cannot match it (the split ``tests/test_7405_update_point_writes``
#: relies on).
_PROMOTE_NEEDLE = "n.status = 'live', n.updatedAt = $now, n += $props"


#: The MEASURED v6 literal (#7685): the store's one sentence for "a rebuild
#: happened" and "the graph was deleted", which is why the copy below also
#: answers the ``EXISTS`` probe (the retry predicate checks the graph KEY, not
#: this text).
_DELETED_OR_REPLACED = (
    "graph was deleted or replaced while the query was running, aborting")


@pytest.fixture
def env(tmp_path):
    """``(sdk, events_dir)`` with the JSONL journal wired (the #3311 shape)."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t12586.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _force(sdk, pid: str, *, status, outdated: bool) -> None:
    """Persist a state ``create_point`` cannot mint (the legacy flag shape)."""
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status=$st, n.outdated=$od",
        params={"id": pid, "st": status, "od": outdated})


def _row(sdk, pid: str) -> tuple:
    r = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status, n.outdated",
        params={"id": pid}).result_set[0]
    return (r[0], bool(r[1]))


def _revisions(events) -> list[dict]:
    """Every journal record addressing a promote (``PointRevised`` /
    ``PointPromoted``) — a refused promote must add none."""
    path = events / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("type") in ("PointRevised", "PointPromoted"):
            out.append(rec)
    return out


def _draft(sdk, text="draft claim") -> str:
    return sdk.create_point("evidence", f"{text} {uuid.uuid4().hex}",
                            status="draft")["id"]


# ══════════════════════════════════════════════════════════════════════
# The defect — a flag-dead point is refused, with a performable repair
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("stored", [None, "draft"])
def test_a_flag_dead_point_is_refused_and_the_row_is_untouched(env, stored):
    """The reported probe (NULL status), and its ``draft`` sibling.

    Both are promotable VOCABULARY, so only the flag separates them from a
    healthy row — which is why both must be refused.

    FALSIFIABLE: drop the flag conjunct from ``_PROMOTABLE_CLAUSE`` and this
    test's ``pytest.raises`` fails with "DID NOT RAISE" — the promote lands
    ``['live', True]``, which is the state the issue measured.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "a claim to kill", dedup=False)["id"]
    _force(sdk, pid, status=stored, outdated=True)
    before = _row(sdk, pid)
    assert before == (stored, True), "the harness did not persist the shape"

    with pytest.raises(ValueError) as exc:
        sdk.update_point(pid, status="live")

    assert _row(sdk, pid) == before, (
        "a refused promote wrote to the graph")
    message = str(exc.value)
    # AX rule 2's three parts (#10183).
    assert "refused to promote" in message, "part 1: what was refused"
    assert "outdated=true" in message and "terminal" in message, (
        "part 2: why")
    assert "create_point" in message, "part 3: a performable repair"


def test_the_refused_promote_journals_nothing(env):
    """A refused promote must leave the durable record untouched (#7951 §17.1).

    FALSIFIABLE: hoist ``_record_promote()`` above the write and this goes red
    with a ``PointRevised`` a replay would apply — resurrecting the claim.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "a claim to kill", dedup=False)["id"]
    _force(sdk, pid, status=None, outdated=True)
    before = _revisions(events)

    with pytest.raises(ValueError):
        sdk.update_point(pid, status="live")

    assert _revisions(events) == before, (
        "the refused promote was journalled: a rebuild would apply it — "
        f"{_revisions(events)[len(before):]!r}")


def test_the_two_point_doors_agree_on_the_flag_dead_row(env):
    """``update_point`` refuses exactly what ``update_entity`` refuses.

    FALSIFIABLE: revert either door and this fails on that door only, which is
    the disagreement the issue exists to close.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "a claim to kill", dedup=False)["id"]
    _force(sdk, pid, status=None, outdated=True)

    # ⛔ `update_entity` LEFT the public surface with #12901 wave 2 (#4282): its body
    # survives as `_update_entity`, and the public name now raises the migration hint
    # instead of guarding a row. The door under test is the SAME DOOR — assert it on the
    # surviving body, because re-adding the public attribute would itself be a surface
    # change (and would make this row pass for the wrong reason).
    for door in ("update_point", "_update_entity"):
        with pytest.raises(ValueError) as exc:
            getattr(sdk, door)(pid, status="live")
        assert "terminal" in str(exc.value), door
    assert _row(sdk, pid) == (None, True)


def test_a_point_that_goes_flag_dead_in_the_write_window_is_refused(
        env, monkeypatch):
    """The guard is in the STATEMENT, not only in the pre-write reads.

    A competing writer sets ``outdated=true`` and aborts the first promote
    statement; the re-issue can then never match, so the witness must answer
    with the documented refusal rather than retry out the 7-14 s window and
    surface the graph abort.

    FALSIFIABLE: revert ``_promote_witness``'s ``outdated`` read and this
    fails with the re-raised graph abort instead of the flag refusal.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk, _ = env
    pid = _draft(sdk)
    proj = sdk._get_proj()
    proj.g = _AbortOnceThenFlagDead(proj.g)

    with pytest.raises(ValueError) as exc:
        sdk.update_point(pid, status="live")

    assert "outdated=true" in str(exc.value), (
        "the flag-dead row surfaced as something other than the refusal")
    assert _row(sdk, pid) == ("draft", True), (
        "the promote landed over a row that went dead mid-write")


class _AbortOnceThenFlagDead:
    """Abort the promote once AND set the legacy flag — a competing
    ``invalidate_point`` winning between the guard read and the write."""

    def __init__(self, real) -> None:
        self._real = real
        self.matching = 0

    def query(self, cypher, params=None, timeout=None):
        if _PROMOTE_NEEDLE in cypher:
            self.matching += 1
            if self.matching == 1:
                self._real.query("MATCH (n:Point) SET n.outdated = true")
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        # The retry predicate proves the graph KEY still exists; the node is
        # present here (it is the graph that aborted, not the node that went),
        # so the abort must be classified as retryable for the witness to be
        # consulted at all.
        if a and a[0] == "EXISTS":
            return 1
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


# ══════════════════════════════════════════════════════════════════════
# Everything else the promote arm does — unchanged
# ══════════════════════════════════════════════════════════════════════

def test_the_cli_backfill_shape_still_promotes(env):
    """A NULL-status legacy point WITHOUT the flag is not terminal.

    This is ONTOLOGY §5's own route — "CLI backfill promotes NULL-status legacy
    points to ``live`` (migration-only, not a drift)" — and the only condition
    this change adds is the flag conjunct, so it must survive.

    FALSIFIABLE: replace the flag conjunct with a blanket refusal of
    NULL-status points and this goes red, stranding every pre-#1088 node.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "a legacy claim", dedup=False)["id"]
    _force(sdk, pid, status=None, outdated=False)

    out = sdk.update_point(pid, status="live")

    assert out["status"] == "live"
    assert _row(sdk, pid) == ("live", False)


def test_a_draft_point_still_promotes_with_the_rest_of_the_props(env):
    """The #2884 A2 shape: the promote and the caller's other props land.

    FALSIFIABLE: widen the new conjunct into "only a status write" and the
    ``custom_field`` assertion goes red.
    """
    sdk, _ = env
    pid = _draft(sdk)

    sdk.update_point(pid, status="live", custom_field="promoted")

    assert _row(sdk, pid) == ("live", False)
    assert sdk.get_point(pid)["custom_field"] == "promoted"


def test_a_terminal_status_with_the_legacy_flag_keeps_the_status_message(env):
    """A row dead by BOTH a status word and the flag keeps the #432 sentence.

    This is the shape ``supersede_point`` actually writes (``status='superseded'``
    plus ``outdated=true``), so it is the common case: the more specific
    flag-only refusal must not swallow it, because on this row the #432 remedy
    (mint a new Point, then the verb) IS performable.

    ⚠️ SINGLE-row shape. On a mixed fan-out the refusal's row is chosen by
    ``live.first_terminal_row``'s stable key (status text, then flag) — the same
    rule the rest of ``sdk.py`` uses, NOT a status-word precedence invented for
    this message — so a flag-dead ``draft`` beside a ``retracted`` sibling names
    the flag. That is pinned separately, as "refused", below.

    FALSIFIABLE: drop the ``_dead_status not in TERMINAL_EXCLUDED_STATUSES``
    guard from the flag-only arm and this fails on the flag message.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "a claim to kill", dedup=False)["id"]
    _force(sdk, pid, status="superseded", outdated=True)

    with pytest.raises(ValueError, match="Illegal status transition"):
        sdk.update_point(pid, status="live")
    assert _row(sdk, pid) == ("superseded", True)


def test_a_mixed_fan_out_is_refused_without_a_partial_promote(env):
    """Two nodes share the id; one is flag-dead, one is terminal by status.

    Neither is promotable, so the call must raise and NEITHER row may move — a
    per-row WHERE could otherwise promote the promotable-looking sibling and
    leave the pair disagreeing. The message is whichever row the shared picker
    selects (stable key, not position); this pin asserts the refusal and the
    untouched rows, not the sentence.

    FALSIFIABLE: drop the flag conjunct and the ``draft``+flag row promotes,
    leaving ``('live', True)`` on a row this test says must not move.
    """
    sdk, _ = env
    twin = "twin-" + uuid.uuid4().hex
    a = sdk.create_point("statement", "twin A", status="draft",
                         id=twin, dedup=False)["id"]
    b = sdk.create_point("statement", "twin B", status="draft",
                         id=twin, dedup=False)
    assert b["id"] == a, "the fixture failed to mint a duplicate id"
    # One row per statement, keyed by content: the fan-out is what makes the id
    # ambiguous, so each row must be addressed by something unambiguous.
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) WHERE n.content = 'twin A' "
        "SET n.outdated = true", params={"id": twin})
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) WHERE n.content = 'twin B' "
        "SET n.status = 'retracted'", params={"id": twin})

    with pytest.raises(ValueError):
        sdk.update_point(twin, status="live")

    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status, n.outdated",
        params={"id": twin}).result_set
    assert sorted((st, bool(od)) for st, od in rows) == [
        ("draft", True), ("retracted", False)], (
        f"a mixed fan-out was partially promoted: {rows!r}")


@pytest.mark.parametrize("status", ["retracted", "superseded", "outdated",
                                    "archived", "deprecated"])
def test_a_terminal_status_keeps_its_illegal_transition_message(env, status):
    """A dead STATUS word keeps the pre-existing #432 sentence.

    The flag-only shape gets its own refusal because the #432 remedy is not
    performable on it; that must not leak onto the shapes where the remedy IS
    performable. ``deprecated`` has no ``_ALLOWED_TRANSITIONS`` key and is
    terminal only in the shared vocabulary — another reason the predicate, not
    a local table, must answer.

    FALSIFIABLE: route every terminal row to the flag message and this fails.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "a claim to kill", dedup=False)["id"]
    _force(sdk, pid, status=status, outdated=False)

    with pytest.raises(ValueError, match="Illegal status transition"):
        sdk.update_point(pid, status="live")
    assert _row(sdk, pid) == (status, False)


def test_an_already_live_status_only_promote_is_still_the_noop(env):
    """#1088: already-live is an idempotent success, not a refusal.

    A REGRESSION pin on the pre-write probe (``update_point``'s
    ``if _cur_status[0][0] == 'live'`` early return), not a mutation target for
    the new refusal: the probe returns before any promote statement or
    diagnostic runs, so no reordering inside ``_raise_update_point_status_error``
    can reach this call. What it protects is the ORDER — a future edit that puts
    the new dead-check ahead of that probe would make an idempotent re-promote
    raise, and this goes red.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "already live", dedup=False)["id"]

    out = sdk.update_point(pid, status="live")

    assert out["status"] == "live"
    assert _row(sdk, pid) == ("live", False)


def test_an_invalidated_live_row_keeps_the_1088_idempotent_answer(env):
    """`live` + the flag is an ORDINARY row, and a status-only promote of it is
    the #1088 no-op — not a refusal.

    ``invalidate_point`` sets ``outdated=true`` and leaves the status alone, so
    ``status='live'`` beside the flag is what a live claim looks like after it is
    withdrawn. The pre-write probe answers the status-only call, but the
    DIAGNOSTIC is reached in the retry window (``_promote_witness``, on an
    aborted promote), and it must still answer ``True`` there — otherwise a
    caller is told their current-but-invalidated point is dead and told to mint
    a new one, for a call that writes nothing.

    FALSIFIABLE: drop the ``_dead_status != 'live'`` guard from the flag-only arm
    and this raises the flag refusal instead of returning ``True``.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "a claim to invalidate",
                           dedup=False)["id"]
    _force(sdk, pid, status="live", outdated=True)

    assert _raise_update_point_status_error(
        sdk._get_proj(), pid, status_only=True, present=True) is True

    # …and the door itself still answers the status-only call as the no-op.
    out = sdk.update_point(pid, status="live")
    assert out["status"] == "live"
    assert _row(sdk, pid) == ("live", True)


def test_an_operator_fan_out_beside_a_dead_row_is_not_a_false_success(env):
    """One id, two rows: an OPERATOR and a terminal statement point.

    ``classify_lifecycle_fanout`` ranks ``operator`` above ``terminal`` and
    reports the OPERATOR's status — which here is ``live`` — so a diagnostic that
    trusted that representative would take the #1088 no-op branch and report
    SUCCESS for a promote that matched nothing beside a dead sibling. The
    terminal row must win the answer.

    The operator row is minted by raw write: ``create_operator`` mints its own id,
    so a fan-out is the only way two such rows share one.

    ⚠️ The pin targets the DIAGNOSTIC, not the door, on purpose: ``update_point``
    answers a status-only call from its own single-row pre-write probe first, and
    on a two-row fan-out *which* row that probe sees is server order — so a
    door-level assertion here would be flaky rather than wrong. The diagnostic is
    reached whenever the promote statement matched nothing (and on every aborted
    promote through ``_promote_witness``), and there the terminal row must win.

    FALSIFIABLE: let the terminal block fall through to the classifier's
    representative status and this returns ``True`` instead of raising.
    """
    sdk, _ = env
    twin = "opfan-" + uuid.uuid4().hex
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:$id, status:'live', is_operator:true})",
        params={"id": twin})
    sdk._get_proj().g.query(
        "CREATE (m:Point {id:$id, status:'retracted'})", params={"id": twin})

    with pytest.raises(ValueError):
        _raise_update_point_status_error(
            sdk._get_proj(), twin, status_only=True, present=True)


def test_the_temporal_probe_names_the_row_the_shared_key_picks(env, caplog):
    """`promote_point`'s temporal probe picks with the ONE shared picker.

    The same `min`-by-stable-key scan existed a THIRD time, hand-inlined at the
    temporal-target probe (#12586 review cycle 3, P2) — so `first_terminal_row`
    was documented as "the ONE place a caller asks which row made this terminal"
    while this site did the same pick inline. The swap is behaviour-identical for
    the WIRE (a dead target skips either way); what it changes is WHICH row the
    warning names, and that is the property worth pinning: the pick is the shared
    stable key (status word, then the flag), NOT server row order.

    FALSIFIABLE: replace the picker with a first-match scan
    (`next((… ) for r in trows if is_terminal_status(...))`) and the warning
    names `superseded` (created first) instead of `retracted` (stable-key first).
    """
    prior_live = "tgtdead-" + uuid.uuid4().hex
    # ⛔ ORDER IS THE MUTATION SENSOR: two TERMINAL rows in one fan-out, created
    # in the order server iteration returns them. A first-match scan names
    # `superseded` (created first); the shared stable key names `retracted`.
    sdk, _ = env
    sdk._get_proj().g.query(
        "CREATE (:Point {id:$id, status:'superseded', outdated:false})",
        params={"id": prior_live})
    sdk._get_proj().g.query(
        "CREATE (:Point {id:$id, status:'retracted', outdated:false})",
        params={"id": prior_live})

    cid = sdk.create_point("decision", "promote over a split prior",
                           status="draft")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "SET n.temporal_candidate = true, n.temporal_reviewed = 'merge', "
        "n.temporal_target_ids = [$tgt]",
        params={"id": cid, "tgt": prior_live})

    caplog.set_level(logging.WARNING, logger="tortoise.sdk")
    res = sdk.promote_point(cid)

    assert res["promoted"] is True, res
    # A dead sibling means the prior is NOT live: no wire, no supersession.
    assert not res.get("temporal_wired"), res
    assert res.get("superseded") is not True, res
    text = "\n".join(r.getMessage() for r in caplog.records
                      if r.name == "tortoise.sdk")
    assert prior_live in text, text
    assert "is retracted — skipping the wire" in text, text
    assert "is superseded" not in text, text


def test_a_flag_dead_temporal_target_is_named_outdated_not_live(env, caplog):
    """A NULL-status target dead by the FLAG is named by the flag.

    The picker reads the flag; a caller that then renders ``hit[0] or "live"``
    throws the flag away — so the exact #12586 shape was logged as
    ``is live — skipping the wire`` while being skipped *because it is terminal*
    (#12586 review cycle 4, P2). ``live.terminal_row_marker`` is the one
    renderer, shared with ``_lifecycle_terminal_marker``.

    FALSIFIABLE: render ``hit[0] or "live"`` at the temporal probe and the
    message says `is live`; remove the flag from the renderer and it says `live`
    for this row too.
    """
    sdk, _ = env
    prior = "tgtflag-" + uuid.uuid4().hex
    sdk._get_proj().g.query(
        "CREATE (:Point {id:$id, outdated:true})", params={"id": prior})
    cid = sdk.create_point("decision", "promote over a flag-dead prior",
                           status="draft")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "SET n.temporal_candidate = true, n.temporal_reviewed = 'merge', "
        "n.temporal_target_ids = [$tgt]",
        params={"id": cid, "tgt": prior})

    caplog.set_level(logging.WARNING, logger="tortoise.sdk")
    res = sdk.promote_point(cid)

    assert res["promoted"] is True, res
    assert not res.get("temporal_wired"), res
    text = "\n".join(r.getMessage() for r in caplog.records
                      if r.name == "tortoise.sdk")
    assert prior in text, text
    assert "is outdated — skipping the wire" in text, text
    assert "is live" not in text, text


@pytest.mark.parametrize("dead_first", [True, False])
def test_a_live_row_beside_a_dead_sibling_is_refused_in_either_order(
        env, dead_first):
    """``{live + flag, retracted}`` on one id — refused, in BOTH row orders.

    The `live` carve-out that keeps the #1088 answer for a SINGLE invalidated
    live row must be per-ROW: carved out per FAN-OUT it let this shape fall
    through to the ``live`` branch and report an idempotent success for a
    promote that matched nothing beside a ``retracted`` sibling — because
    ``live`` sorts first, in every row order.

    Row order is forced by INSERTION order here, and asserted, so a green run
    cannot come from the server happening to be kind.

    FALSIFIABLE: scope the carve-out to the selected row
    (``dead[0] != 'live'``) and the diagnostic returns ``True`` instead of
    raising on this shape.
    """
    sdk, _ = env
    twin = "livedead-" + uuid.uuid4().hex
    first, second = ((("live", True), ("retracted", False)) if dead_first
                     else (("retracted", False), ("live", True)))
    for st, od in (first, second):
        sdk._get_proj().g.query(
            "CREATE (n:Point {id:$id, status:$st, outdated:$od})",
            params={"id": twin, "st": st, "od": od})

    # The pin targets the DIAGNOSTIC: `update_point`'s pre-write #1088 probe is
    # single-row (a pre-existing shape this change does not touch), so on this
    # fan-out a door-level assertion would depend on which row that probe sees.
    with pytest.raises(ValueError):
        _raise_update_point_status_error(
            sdk._get_proj(), twin, status_only=True, present=True)


def test_a_flag_dead_row_keeps_its_non_status_revision_capability(env):
    """#7819's scope: a NON-temporal, non-status revision of a dead claim stays
    writable — the fix must not become a blanket refusal of every write to a
    flag-dead point (that would delete the recorded #3305 capability: an
    ADDRESSED revision later than the terminalization is the newer same-id
    writer).

    The TEMPORAL half is #7819's own scoped refusal and stays refused — pinned
    in the same test so the two are read together and neither is mistaken for
    the other.

    FALSIFIABLE: refuse on terminality regardless of what ``props`` carries and
    the first half goes red; drop the flag from the terminal predicate the gate
    reads and the second half does.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "a dead claim", dedup=False)["id"]
    _force(sdk, pid, status=None, outdated=True)

    sdk.update_point(pid, confidence=0.42)

    assert _row(sdk, pid) == (None, True), "the flag was cleared by a revision"
    assert sdk.get_point(pid)["confidence"] == pytest.approx(0.42)

    with pytest.raises(ValueError, match="already terminal"):
        sdk.update_point(pid, validTo="2026-06-10")
    assert _row(sdk, pid) == (None, True)


def test_the_operator_arm_is_untouched(env):
    """``annotate_operator`` re-enters ``update_point``; a healthy operator
    still takes its annotation, so the new conjunct stayed out of that path.

    FALSIFIABLE: apply the promote clause to the non-status write path and this
    fails on the annotation never landing.
    """
    sdk, _ = env
    claim = sdk.create_point("statement", "operator endpoint", dedup=False)["id"]
    claim2 = sdk.create_point("statement", "operator endpoint 2",
                              dedup=False)["id"]
    op = sdk.create_operator("IMPL", claim, [claim2])["id"]

    sdk.update_point(op, annotator_bias=0.25)

    assert sdk.get_point(op)["annotator_bias"] == 0.25


# ══════════════════════════════════════════════════════════════════════
# The door this change did NOT touch — `promote_point` — stays as it was
#
# Same root, MEASURED on this tree, deliberately left to its own change: a
# draft point invalidated before it was ever promoted (`status='draft'` plus
# the legacy `outdated=true` flag) is promoted to `['live', outdated=true]`
# by `promote_point`, because its terminality test is the local
# `status != 'draft'` reading. Fixing it here would change a second verb's
# return contract and force a rewrite of
# `test_6239_promote_after_supersede.py`'s raw-restatus parity fixture, which
# writes exactly this row as its live-reachable ordering. #12586's thread
# carries the measurement; these two pins only prove the untouched door still
# behaves as its own docstring says.
# ══════════════════════════════════════════════════════════════════════

def test_promote_point_still_promotes_a_plain_draft(env):
    """The reviewer-gated door's normal route is unchanged.

    FALSIFIABLE: make the new entry check fire on a healthy draft and this
    fails on ``blocked``.
    """
    sdk, _ = env
    pid = _draft(sdk)

    out = sdk.promote_point(pid)

    assert out["promoted"] is True and out["status"] == "live"
    assert _row(sdk, pid) == ("live", False)


def test_promote_point_still_reports_a_live_node_as_already_live(env):
    """DE2E-N9: the already-live answer is not a refusal.

    FALSIFIABLE: move the dead-check above the already-live arm and this
    returns ``blocked`` for a healthy live point.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "already live", dedup=False)["id"]

    out = sdk.promote_point(pid)

    assert out == {"id": pid, "status": "live", "promoted": False,
                   "blocked": False, "reason": "already_live"}
