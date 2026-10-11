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
reproduce the live graph — and the operator silently changes EP posture: a `None` status
is not `'live'`, so the live operator is exempt from the grounding selector
(`MATCH (n:Point {status:'live'})`) that the rebuilt one satisfies.

**Why the door is wrong and not the replay.** The writer's own docstring says the event
path default IS the coalesce default — *"the event path default ('live',
projection/entities.py coalesce) is overridden by the explicit status"* — and the graph's
standing rule after any write is `assert status in ("draft", "live")` (TORTOISE-GRAPH-BRIEF,
the `status=None` leak class). `None` is an accidental omission, not a designed state.
"""
from __future__ import annotations

import contextlib
import os
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
            f"'live'. A status-less Point is EP-inert while a rebuilt one is "
            f"'live' (projection/entities.py coalesce), so live != replay."
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


def test_extraction_posture_still_births_a_draft_operator():
    """The #780 posture is unchanged: an extraction operator stays EP-inert."""
    d = tempfile.mkdtemp(prefix="t5089_opposture_")
    sdk = TortoiseSDK(db_path=os.path.join(d, "t.db"),
                      event_log_path=os.path.join(d, "events.jsonl"))
    try:
        p = sdk.create_point("statement", "an extraction source")
        op = sdk.create_operator("IMPL", p["id"], [p["id"]], promote_source=False)
        assert _status_of(sdk._get_proj(), op["id"]) == "draft"
        # #1088/#780: the source is NOT auto-promoted on the extraction path.
        assert _status_of(sdk._get_proj(), p["id"]) == "live"  # created live
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
