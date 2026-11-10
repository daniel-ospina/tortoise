"""#11239 — DM1's mention record reaches the claim Point, and SURVIVES a rebuild.

THE DEFECT this file guards. `#11021`'s mention channel shipped its shape and
not its home: a held phrase is recorded on the claim (`Point.mentions`) in the
payload and then the write boundary discarded it, so a graph read after a
capture held the claim and no mention on it. Persisting it is only half the
repair — a record that is written LIVE and dropped by every replay is the same
loss one step later, and it is invisible, because nothing reads the node.

So every row here is BEHAVIOURAL: write through a real door, `rebuild_all` from
a real JSONL journal, then compare the node's bytes. A source-text assertion
cannot see a live-only write (an `inspect.getsource` check stays green under a
`if False:` neutering mutant), which is why the source pins in
`tests/test_referentiality_11021.py` are not enough on their own.

WHY THE REVISION ROWS EXIST (review P1 on PR #11795). The first cut added the
encode to three doors that run on the REVISION path and extended neither replay
fold — `projection._revise_point` (the graph fold) and `_apply_one`'s
`PointRevised` arm (the pure fold `check_consistency` reads). Two silent
directions followed:

  1. a mention arriving via `update_point` was live-only — a rebuild dropped it,
     and the checker then advised "replay the journal", which produces exactly
     the graph that is already there (the `#5048` unclearable-red class);
  2. a re-commit carrying NO mentions passed `mentions=None` into the dedup
     fold, and a `None` in a generic prop REMOVES the property — so the stored
     record was deleted on the ordinary re-commit path, in the direction that
     loses memory.

Both are pinned below, and the second is pinned at the SDK door that the hosted
commit path funnels through.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("tortoise.sdk")

from tortoise.sdk import TortoiseSDK

MENTIONS = [
    {"surface": "it", "span_start": 0, "span_end": 2, "kind_guess": None},
    {"surface": "the problem", "span_start": 12, "span_end": 23},
]
CONTENT = "the timeout command fails because it exceeds the limit"


@pytest.fixture()
def env(tmp_path):
    """``(sdk, events_dir)`` with the JSONL journal wired.

    Same shape as ``test_3817_tags_rebuild_durability.py``: the journal is a real
    file on disk so ``rebuild_all`` replays **the event log**, never an in-memory
    snapshot.
    """
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "mentions11239.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _node_prop(sdk, pid: str):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.mentions",
        params={"id": pid},
    ).result_set
    return rows[0][0] if rows else None


def _assert_record_is_the_payload_surfaces(prop) -> None:
    """The stored scalar is DM1's four-field row, parsed — not a blob."""
    assert isinstance(prop, str), (
        "the node must hold ONE compact JSON scalar: the engine refuses an "
        f"array containing a map (`_is_persistable_prop_value`); got {prop!r}")
    records = json.loads(prop)
    assert [r["surface"] for r in records] == ["it", "the problem"], records
    assert records[0]["span_start"] == 0 and records[0]["span_end"] == 2, records


def test_a_mention_written_by_create_reaches_the_node_and_survives_a_rebuild(env):
    """The CREATE path — the one `hosted_api._execute_commit_writes` rides."""
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft", mentions=MENTIONS)["id"]
    live = _node_prop(sdk, pid)
    # PRESENCE FIRST: an absent-on-both-sides equality is the silent-drop
    # signature this whole issue is about, so a row that only compared the two
    # sides would pass over a prop that never landed.
    assert live is not None, "the mention record never reached the node"
    _assert_record_is_the_payload_surfaces(live)

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _node_prop(sdk, pid)
    assert rebuilt is not None, (
        "the mention record was LIVE-ONLY: the rebuild dropped it, so the "
        "record a customer's graph holds depends on whether a replay ran")
    assert rebuilt == live, (
        "the rebuild re-derived DIFFERENT bytes for the same record — live and "
        "replay must be a pure function of the payload (#330)")


def test_a_mention_written_by_update_survives_a_rebuild(env):
    """The REVISION path (review P1 on PR #11795).

    `update_point(mentions=…)` encodes and writes the prop live and emits it on
    `PointRevised`; without the clause in `projection._revise_point` (and in the
    pure `PointRevised` fold `check_consistency` reads) the rebuild silently
    reverted the node to "no mentions".
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft")["id"]
    assert _node_prop(sdk, pid) is None

    sdk.update_point(pid, mentions=MENTIONS)
    live = _node_prop(sdk, pid)
    assert live is not None, "the update door did not store the record live"
    _assert_record_is_the_payload_surfaces(live)

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _node_prop(sdk, pid)
    assert rebuilt is not None, (
        "the revision path is LIVE-ONLY: `_revise_point` / the `PointRevised` "
        "fold ignored `mentions`, so a rebuild dropped a record the live write "
        "stored (the #5048 class)")
    assert rebuilt == live, (
        "the two replay engines disagree with the live value on the same "
        "journal record")

    # ⛔ THE PURE FOLD TOO. `check_consistency` builds its journal view from
    # `_apply_one`'s `PointRevised` arm, NOT from the graph — so a graph that
    # agrees with the node can still red if that arm ignores the key, with the
    # advice "replay the journal" that reproduces the same graph (the
    # unclearable-red class). Asserting the checker is CLEAN is what pins the
    # second replay engine.
    from tortoise.consistency import check_consistency

    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "the live write and both replay folds agree, so the checker must not "
        "red this graph: " + str(report.get("divergent_points")))


def test_a_recommit_with_no_mentions_does_not_clear_the_stored_record(env):
    """The opposite direction (review P1, direction 2 on PR #11795).

    A capture re-commits the same content with the cue OFF (the shipped default,
    plan R1) — the payload then carries no mentions. If that reaches the dedup
    fold as `mentions=None`, a `None` in a generic prop REMOVES the property
    (`SET n += $props`, measured), deleting a record nothing asked to delete. The
    hosted commit door therefore adds the key ONLY when the payload carries a
    record; this row pins the semantics at the SDK door it funnels through.
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft", mentions=MENTIONS)["id"]
    stored = _node_prop(sdk, pid)
    assert stored is not None

    # the re-commit: same content, no mentions spoken about
    again = sdk.create_point("statement", CONTENT, dedup=True, status="draft")
    assert again["id"] == pid, "the re-commit must dedup onto the same claim"
    after = _node_prop(sdk, pid)
    assert after == stored, (
        "a re-commit that says nothing about mentions CLEARED the stored "
        "record — the DM1 integrity constraint is 'no held item silently "
        "dropped'")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _node_prop(sdk, pid) == stored, (
        "live and replay disagree after a no-mention re-commit")
