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
* the SAME root on the other promote door: ``promote_point`` promoted a
  ``draft + outdated=true`` row (invalidated before it was ever promoted) to
  ``['live', outdated=true]``, because its terminality test was the local
  ``status != 'draft'`` reading. It now consults the shared predicate.

Runnable (the lane's own engine, no container needed):

    uv run python tools/test_lane.py run -- \
        tests/test_12586_update_point_promote_flag.py -q
"""
from __future__ import annotations

import json
import types
import uuid

import pytest
import redis.exceptions as redis_exc

from tortoise.sdk import TortoiseSDK

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

    for door in ("update_point", "update_entity"):
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

    FALSIFIABLE: drop the ``_dead_status not in TERMINAL_EXCLUDED_STATUSES``
    guard from the flag-only arm and this fails on the flag message.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "a claim to kill", dedup=False)["id"]
    _force(sdk, pid, status="superseded", outdated=True)

    with pytest.raises(ValueError, match="Illegal status transition"):
        sdk.update_point(pid, status="live")
    assert _row(sdk, pid) == ("superseded", True)


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

    FALSIFIABLE: make the new dead-check run before the already-live arm and
    this raises instead of returning.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "already live", dedup=False)["id"]

    out = sdk.update_point(pid, status="live")

    assert out["status"] == "live"
    assert _row(sdk, pid) == ("live", False)


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
