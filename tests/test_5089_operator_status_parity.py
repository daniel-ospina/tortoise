"""#5089 (epic: durability — `derived = replay(journal)`) — operator status parity.

**The invariant.** ONTOLOGY.md §5 declares `status` the draft/live claim lifecycle and
gives it a canonical write path; `consistency.py` states the corresponding *rule*:

    _WRITER_DEFAULTS = {"content": "", "status": "live"}   # consistency.py:284
    "When the journal omits one, the graph's value must EQUAL the default: a faithful
     replay produces exactly that, while any other value is the graph holding something
     the journal never authored — a divergence, not a default."

So when the journal carries no `status`, the graph MUST hold `'live'` — anything else is
a divergence by the repo's own consistency model.

**The defect.** `sdk.create_operator` sets the operator node's status ONLY on the
extraction posture (`promote_source=False` → `'draft'`). With the DEFAULT
(`promote_source=True`) it writes no status at all, so the live node is born
`status=None`. The replay writer's coalesce default is `'live'`, so a rebuild does NOT
reproduce the live graph — and the operator's PARTICIPATION changes: `compute_grounding`
selects strictly on `MATCH (n:Point {status:'live'})` (`projection/grounding.py`), which
the rebuilt operator satisfies and the live one did not.

**Scope of that claim, stated precisely (review P2).** The divergence is a GROUNDING
participation difference, not a whole-EP one: the EP factor-extraction read path treats a
NULL status as live (`live.py`'s `_live_only` spares `NULL`, and excludes `'draft'`
plus every terminal status),
so the pre-fix SDK operator was already an EP participant *there*. Only the strict
grounding selector excluded it. The fix closes that — it does not claim a wider EP effect
than it has.

**Why the door is wrong and not the replay.** The writer's own docstring says the event
path default IS the coalesce default — *"the event path default ('live',
projection/entities.py coalesce) is overridden by the explicit status"* — and the graph's
standing rule after any write is `assert status in ("draft", "live")` (TORTOISE-GRAPH-BRIEF,
the `status=None` leak class). `None` is an accidental omission, not a designed state.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile

from tortoise.sdk import TortoiseSDK


def _status_of(proj, pid):
    rows = proj.query(
        "MATCH (n:Point {id:$id}) RETURN n.status", id=pid
    ).result_set
    assert rows, f"point {pid} missing"
    return rows[0][0]


def test_create_operator_writes_a_status_the_replay_reproduces():
    """The default door and a replay must agree on the operator's status."""
    d = tempfile.mkdtemp(prefix="t5089_opstatus_")
    sdk = TortoiseSDK(db_path=os.path.join(d, "t.db"),
                      event_log_path=os.path.join(d, "events.jsonl"))
    try:
        p = sdk.create_point("statement", "a claim an operator attaches to")
        op = sdk.create_operator("IMPL", p["id"], [p["id"]])

        live = _status_of(sdk._get_proj(), op["id"])

        # The graph's standing rule after any write: a born point is draft or live.
        assert live in ("draft", "live"), (
            f"#5089: create_operator born status={live!r} — neither 'draft' nor "
            f"'live'. A status-less Point is excluded by the strict grounding "
            f"selector while a rebuilt one is 'live' (projection/entities.py "
            f"coalesce), so the rebuild would change its grounding participation."
        )
        # Pin the VALUE on the default posture, not merely its membership in the
        # allowed set: `in ("draft", "live")` above would pass if the door
        # regressed to birthing 'draft', which would break the parity this file
        # exists for. (Review P2: the membership check alone was too weak.)
        assert live == "live", (
            f"#5089: the promoting posture must write 'live' (got {live!r}); "
            f"'draft' is the extraction posture's value, and the #780 EP draft "
            f"filter would then exclude this operator from EP computation."
        )

        sdk._get_proj().rebuild_all(os.path.dirname(os.path.join(d, "e")),
                                    confirm_destructive=True)
        rebuilt = _status_of(sdk._get_proj(), op["id"])

        assert rebuilt == live, (
            f"#5089: replay changed the operator's status {live!r} -> {rebuilt!r}; "
            f"the journal must be the truth of the live write."
        )
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
        # The SDK stops the DB but does not remove the tree; without this each
        # run leaks a temp dir (review P2).
        shutil.rmtree(d, ignore_errors=True)


def test_extraction_posture_still_births_a_draft_operator():
    """The #780 posture is unchanged: an extraction operator stays EP-inert.

    The source is created ``status='draft'`` on purpose. With the #1088 default
    it would already be ``'live'``, and then ``assert ... == 'live'`` could not
    detect a broken no-promotion contract — it would hold however the source got
    there (review P2).
    """
    d = tempfile.mkdtemp(prefix="t5089_opposture_")
    sdk = TortoiseSDK(db_path=os.path.join(d, "t.db"),
                      event_log_path=os.path.join(d, "events.jsonl"))
    try:
        p = sdk.create_point("statement", "an extraction source",
                             status="draft")
        assert _status_of(sdk._get_proj(), p["id"]) == "draft"
        op = sdk.create_operator("IMPL", p["id"], [p["id"]], promote_source=False)
        assert _status_of(sdk._get_proj(), op["id"]) == "draft"
        # #1088/#780: the source is NOT auto-promoted on the extraction path.
        assert _status_of(sdk._get_proj(), p["id"]) == "draft", (
            "promote_source=False must not auto-promote the source"
        )
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
        shutil.rmtree(d, ignore_errors=True)


def test_partial_absorb_still_finds_a_live_partial_operator():
    """The P1 the door fix would otherwise have introduced (regression guard).

    `_find_operator`'s partial-absorb exists to recover a crash between the
    operator node's CREATE and its per-input edge loop: on an exact miss it looks
    for an operator whose written targets are a PROPER SUBSET of the requested
    set. Its status clause was `(o.status IS NULL OR o.status = 'draft')`, which
    held only while the default door left the operator status-less.

    With the door now writing 'live' (the fix in this file), that clause stopped
    matching the crash-partial: the retry would miss it and MINT A DUPLICATE
    operator, double-counting it in EP. This test drives the crash window and
    asserts the partial is still found.

    **Scope of the window (review P2-3).** The predicate requires
    `size(targets) > 0`, so what is recovered is a crash AFTER AT LEAST ONE edge
    is written (`n >= 1`). A crash immediately after CREATE, before any edge,
    is NOT absorbed by this arm and still mints a duplicate — pre-existing, and
    stated here rather than left implied by "a crash between CREATE and the edge
    loop". This test therefore drops ONE of TWO edges, not both.

    Mutation proof: restore the clause to
    `(o.status IS NULL OR o.status = 'draft')` and this test fails — the operator
    is 'live', so the lookup returns None instead of kind='partial'.
    """
    d = tempfile.mkdtemp(prefix="t5089_partial_")
    sdk = TortoiseSDK(db_path=os.path.join(d, "t.db"),
                      event_log_path=os.path.join(d, "events.jsonl"))
    try:
        proj = sdk._get_proj()
        a = sdk.create_point("statement", "src")["id"]
        b = sdk.create_point("statement", "dst one")["id"]
        c = sdk.create_point("statement", "dst two")["id"]
        op = sdk.create_operator("IMPL", a, [b, c])

        # The door writes the status this test is about.
        assert _status_of(proj, op["id"]) == "live"

        # Simulate the crash: drop ONE of the two destination edges (plus its
        # reverse INPUT mirror) so the written set is a proper subset.
        proj.g.query(
            "MATCH (o:Point {id:$op})-[r:IMPL]->(t:Point {id:$c}) DELETE r",
            params={"op": op["id"], "c": c})
        proj.g.query(
            "MATCH (t:Point {id:$c})-[r:INPUT]->(o:Point {id:$op}) DELETE r",
            params={"op": op["id"], "c": c})

        found = sdk._find_operator("IMPL", [a, b, c])
        assert found is not None, (
            "#5089 regression: the crash-partial is invisible to the absorb "
            "because the operator is now 'live' — a retry would mint a "
            "duplicate operator"
        )
        assert found.get("kind") == "partial", (
            f"expected the partial-absorb to claim the crash-partial, got {found}"
        )
        assert found["id"] == op["id"], "the absorb must reuse the same operator"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
        shutil.rmtree(d, ignore_errors=True)
