"""#11239 — DM1's mention record reaches the claim Point, and SURVIVES a rebuild.

THE DEFECT this file guards. `#11021`'s mention channel shipped its shape and
not its home: a held phrase is recorded on the claim (`Point.mentions`) in the
payload and then the write boundary discarded it, so a graph read after a
capture held the claim and no mention on it. Persisting it is only half the
repair — a record that is written LIVE and dropped by every replay is the same
loss one step later, and it is invisible, because nothing reads the node.

So every row here is BEHAVIOURAL: each one reads the claim Point back off the
graph. Most write through a real door and then `rebuild_all` from a real JSONL
journal before comparing the node's bytes; the two FIRST-CREATION rows
(`..._the_first_creation_folds_the_writers_bytes...`,
`..._an_unwritable_record_on_a_first_creation...`) fold the journal entry
in-process instead, because what they pin is the pure fold's half of that arm.
A source-text assertion
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

WHY THE DOOR ROWS EXIST (review R1/R2 on the `eb7071ae` head). The rows above
drove `sdk.create_point` / `sdk.update_point` directly, so THREE doors the fix
is about — `hosted_api._execute_commit_writes`, the `_extract_session_v2`
capture seam, and `update_entity` — stayed guarded only by `inspect.getsource`
substrings in `test_referentiality_11021.py`, which a `if False:` or an
`or None` keeps green. Measured on that head: `if False and _mention_props:`
and `if False and _mention_records:` each neared the write completely while
every assertion passed. So each of those doors now has a NODE READ-BACK here
(or, for the HTTP door, in `tests/test_commit_endpoint.py`, which owns the
`TestClient` fixture):

  * the hosted commit door — both node read-back rows in
    `test_commit_endpoint.py` (`..._the_commit_payload...`), including the
    re-commit whose payload says nothing about mentions;
  * the capture seam — `..._the_capture_seam...` below;
  * `update_entity` — `..._update_entity_survives_a_rebuild` below, which is
    also review R1's fix: that door used to store the record LIVE with no
    journal record, so a rebuild dropped it and `check_consistency` redded a
    faithful graph with the one piece of advice that deletes it.

WHY THE RE-CREATE ROWS EXIST (review P1 on the `19575dd5922b` head). The wipe
that makes a re-created Point FRESH (`projection._rebuild_all_replay`'s pass-1a
`is_recreate` arm — the list that already clears `embedding_verbatim`,
`sourceVersionTransit`, `extractedFromAll` and `tags`) did not name `mentions`.
Pass 1a never folds the delete, so the re-create MERGEs onto the still-present
dead node and the open-set passthrough writes only the keys the payload states:
the DELETED incarnation's record survived on the new node, `check_consistency`
redded a faithful rebuild on `fields: ['mentions']`, and its advice ("replay the
journal") reproduced that graph (#5048/#5256). The mirror of DM1's "no held
item silently dropped" is that a dropped item must not silently RETURN. The
other half of the same arm is the omitted-key carry-forward in `_apply_one`'s
creation arm: a non-re-create second `PointAdded` that says nothing about
mentions leaves the stored value on the node, so the pure fold must keep it too
or the two replay engines disagree on the same journal.

TWO MORE ROWS, ADDED AFTER THE `e63a78de` REVIEW (findings 2 and 8):

  * `EventAPI.add_point` — the door that makes the PROJECTION's encode
    load-bearing rather than defensive. It journals `PointAdded(point=p)` with
    the payload's LIST spelling and calls no encoder of its own, so
    `_upsert_point_props`'s call is the only one on its path; neutering it was
    measured to store NOTHING, live and replay (#finding 2).
  * the ADDRESSED clear (`update_point(mentions=None)`) — three comments rest
    the create-door `None`-drop on this verb being the documented way to delete
    a record, and nothing pinned it (finding 8).
"""

from __future__ import annotations

import json

import pytest

# ⛔ NOT `pytest.importorskip("tortoise.sdk")` (review P3 on PR #11795): a
# module-level `importorskip` on an unimportable SDK reports ONE skipped module —
# measured, `1 skipped`, NOT one per row — so the whole file would leave the run
# as a single skip instead of a loud failure, and this file is the only
# behavioural pin of the SDK write doors and both replay folds (the hosted commit
# door's rows live in `tests/test_commit_endpoint.py`, which owns the TestClient).
# Its declared sibling
# (`test_3817_tags_rebuild_durability.py`) imports directly and fails loudly.
from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
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


#: DM1's declared record shape (`05-plan.md` §4). The encode writes
#: `surface`/`span_start`/`span_end` always and `kind_guess` only when the model
#: carries one, so the CLOSED set below is the narrowing the docstring depends on
#: (`entities.encode_json_record_props` says the docstring is the only guard).
_DM1_KEYS = {"surface", "span_start", "span_end", "kind_guess"}


def _assert_record_is_the_payload_surfaces(prop) -> None:
    """The stored scalar is DM1's declared row, parsed — not a blob."""
    assert isinstance(prop, str), (
        "the node must hold ONE compact JSON scalar: nothing in the write path "
        "STORES an array containing a map — the projection denies it "
        "(`_persist_extra_props`) and a raw `SET` is degraded to null by the "
        "driver before dispatch (#7369); neither is a refusal, which is why "
        "the value must be encoded before it is dispatched; "
        f"got {prop!r}")
    records = json.loads(prop)
    for rec in records:
        assert set(rec) <= _DM1_KEYS, (
            "the encoder wrote a field DM1 does NOT declare onto the node: "
            f"{sorted(set(rec) - _DM1_KEYS)} — promoting a payload-only field "
            "(claim_ref / quote / span_source / rule_id) is a DM1 reopen in "
            "DM1's own home, not an edit here")
        assert {"surface", "span_start", "span_end"} <= set(rec), (
            f"a stored record is missing a DM1 field: {sorted(rec)}")
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


def test_the_replay_door_normalises_a_non_canonical_record(env):
    """The REPLAY-door encode (`_upsert_point_props`) is pinned HERE, behaviourally.

    Review P2 on PR #11795 measured the placement question the honest way round:
    deleting the replay-door call changed no test, while deleting
    `create_point`'s changed the node. That is expected — a journal written by
    the live doors already carries the canonical STRING and the helper is a
    no-op for a `str`. So what is the replay call for? It is the only arm that
    can see a NON-canonical record: a hand-written journal, or an older
    emitter's snapshot, still in the PAYLOAD spelling (a list of dicts).
    `_persist_extra_props` DENIES an undeclared array of dicts — silently — so
    without the replay encode that record vanishes on replay while the live
    write had stored it: the same live/replay divergence, in the direction that
    loses memory.

    Rewriting the journal to the list spelling is the only way to exercise that
    arm through a real `rebuild_all` rather than through source text.
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft", mentions=MENTIONS)["id"]
    live = _node_prop(sdk, pid)
    assert live is not None, "the live create did not store the record"

    log = events / "events.jsonl"
    rows = [json.loads(line) for line in log.read_text().splitlines()
            if line.strip()]
    rewritten = 0
    for row in rows:
        point = row.get("point") or {}
        if point.get("id") == pid and isinstance(point.get("mentions"), str):
            point["mentions"] = json.loads(point["mentions"])
            rewritten += 1
    assert rewritten == 1, (
        "the journaled snapshot did not carry `mentions` as the canonical "
        "scalar — this row can only prove the normalisation arm if it starts "
        "from the encoded form")
    log.write_text("".join(json.dumps(row) + "\n" for row in rows))

    sdk._get_proj().rebuild_all(str(log.parent), confirm_destructive=True)
    rebuilt = _node_prop(sdk, pid)
    assert rebuilt is not None, (
        "the replay door DROPPED a non-canonical (list-shaped) record instead "
        "of normalising it: `_persist_extra_props` denies an undeclared array "
        "of dicts silently, so the record dies on replay while the live write "
        "stored it")
    assert rebuilt == live, (
        "the replay door normalised the record to DIFFERENT bytes than the "
        "live write stored for the same payload (#330)")


def test_a_mention_written_by_update_entity_survives_a_rebuild(env):
    """The GENERIC tenant surface — review R1 on the `eb7071ae` head.

    `_update_entity`'s `:Point` branch writes caller props with a live
    `SET n += $p`, and before this row it journalled only the annotator dims.
    #11239 is what made `mentions` persistable through that surface at all (a
    list of maps used to be a silent no-store — the driver degrades the value to
    `null` before dispatch, #7369's `tags` mechanism — never a refusal), so an
    unjournalled write here would be a NEW live-only door — and the worst kind:
    `check_consistency` reds a faithful graph with "reconcile by replaying the
    journal", and replaying it deletes the record. The fix journals the key on
    the same `PointRevised` record as the dims, which both replay folds already
    carry.

    Mutant it reds on: `journaled_updates` built WITHOUT the `mentions` key
    (i.e. reverting to `annotator_updates`) — the node keeps the record live
    and the rebuild loses it.
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft")["id"]
    assert _node_prop(sdk, pid) is None

    sdk.update_entity(pid, mentions=MENTIONS)
    live = _node_prop(sdk, pid)
    assert live is not None, (
        "the generic `update_entity` surface did not store the record live")
    _assert_record_is_the_payload_surfaces(live)

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _node_prop(sdk, pid)
    assert rebuilt is not None, (
        "`update_entity` stored the mention LIVE and journalled nothing for "
        "it, so the rebuild dropped it — the live-only third door review R1 "
        "filed (the #5048 class, in the direction that loses memory)")
    assert rebuilt == live, (
        "the rebuild re-derived different bytes for the same record (#330)")

    from tortoise.consistency import check_consistency

    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "a faithful graph must not be reported as diverged: the checker's "
        "advice (\"replay the journal\") is the repair that DELETES a "
        "graph-only record: " + str(report.get("divergent_points")))


def test_a_recommit_passing_mentions_none_does_not_clear_the_record(env):
    """The measured hazard review R2 names, at the door it happens on.

    `create_point` takes `**props`, so an ABSENT key and an explicit
    `mentions=None` are different inputs — and only the second clears: a `None`
    in `SET n += $props` REMOVES the property, and on a dedup hit those props
    are folded onto the existing claim by `self.update_point(pid, **props)`.
    The previous row re-committed with the key ABSENT, so it could never red
    for this mutant.

    Mutant it reds on: `create_point` forwarding a `None` mention to the dedup
    fold (the shape the hosted door produced with
    `point_props["mentions"] = _mention_props or None`).
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft", mentions=MENTIONS)["id"]
    stored = _node_prop(sdk, pid)
    assert stored is not None

    again = sdk.create_point("statement", CONTENT, dedup=True, status="draft",
                             mentions=None)
    assert again["id"] == pid, "the re-commit must dedup onto the same claim"
    after = _node_prop(sdk, pid)
    assert after == stored, (
        "a CREATE that passed `mentions=None` DELETED the stored record: the "
        "dedup fold forwarded the None to `update_point`, where it removes the "
        "property. A create must never destroy a record it did not restate "
        "(first-writer; DM1's 'no held item silently dropped')")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _node_prop(sdk, pid) == stored, (
        "live and replay disagree after a `mentions=None` re-commit")


def test_a_recreate_without_mentions_does_not_inherit_the_deleted_record(env):
    """The RE-CREATE wipe (review P1 on the `19575dd5922b` head).

    Pass 1a never folds the delete, so a same-id re-creation MERGEs onto the
    still-present dead node and `_persist_extra_props` writes only the keys the
    payload states. The wipe list that makes a re-created Point FRESH already
    named `embedding_verbatim` (#5004 r4), `sourceVersionTransit` (#5256),
    `extractedFromAll` (#7704) and `tags` (#2897); it did not name `mentions`,
    which this change is what puts in that class. Both consequences were
    measured on the pre-fix tree: the rebuilt node held the DELETED
    incarnation's record, and `check_consistency` redded the FAITHFUL rebuild on
    `fields: ['mentions']` with the advice "replay the journal" — which
    reproduces exactly that graph.

    Mutant it reds on: dropping `n.mentions = NULL` from the pass-1a wipe.
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft", mentions=MENTIONS)["id"]
    assert _node_prop(sdk, pid) is not None, "the fixture never stored a record"
    # A JOURNALED hard delete (`EntityMutated op=delete`) is what makes the next
    # same-id creation a RE-creation to pass 1a; a raw DETACH DELETE journals
    # nothing, the wipe would never fire, and this row would pass for the wrong
    # reason (the #5256 row's warning).
    assert sdk.delete_point(pid) is True

    sdk.create_point("statement", "second life", id=pid)
    assert _node_prop(sdk, pid) is None, (
        "guard: the LIVE re-creation must carry no record — otherwise this row "
        "is comparing the wrong thing")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _node_prop(sdk, pid) is None, (
        "the DEAD incarnation's mention record survived the re-create wipe: "
        "pass 1a MERGEs onto the still-present node, so a re-creation that "
        "states no mentions inherits a record the journal never authored for "
        "that incarnation (live has none — the #330 parity break)")

    from tortoise.consistency import check_consistency

    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "a faithfully rebuilt graph must not be reported as diverged — the "
        "checker's own advice (replay the journal) reproduces this graph: "
        + str(report.get("divergent_points")))


def test_a_second_creation_saying_nothing_keeps_the_record_in_both_folds(env):
    """The pure fold's omitted-key carry-forward (same review P1, other half).

    The graph fold MERGEs and the passthrough writes only stated keys, so a
    second `PointAdded` for an id that already holds a record — with no
    `mentions` on it — leaves the node's value untouched. `_apply_one`'s
    creation arm REPLACES the folded entry, so without the carry-forward it
    drops the key from the journal view only, and `check_consistency` then
    reports a `mentions` divergence on a graph that is right.

    The second record is written straight into the journal because no in-tree
    producer emits a non-dedup second `PointAdded` for a claim id today (review
    noted the arm as reachable-but-unproven); a hand-written journal line is
    exactly the input a replay must survive.

    Mutant it reds on: the `_POINT_JSON_RECORD_PROPS` carry-forward in
    `_apply_one`'s creation arm.
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft", mentions=MENTIONS)["id"]
    stored = _node_prop(sdk, pid)
    assert stored is not None

    log = events / "events.jsonl"
    rows = [json.loads(line) for line in log.read_text().splitlines()
            if line.strip()]
    source = next(r for r in rows
                  if r.get("type") == "PointAdded"
                  and (r.get("point") or {}).get("id") == pid)
    dup = json.loads(json.dumps(source))
    dup.pop("seq", None)  # the replay's seq space is the enumerate index
    assert dup["point"].pop("mentions", None) is not None, (
        "this row can only prove the carry-forward if the second snapshot "
        "starts WITHOUT the key")
    rows.append(dup)
    log.write_text("".join(json.dumps(row) + "\n" for row in rows))

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _node_prop(sdk, pid) == stored, (
        "a second creation that says nothing about mentions DELETED the "
        "stored record on replay: the MERGE keeps it, so the fold must too")

    from tortoise.consistency import check_consistency

    report = check_consistency(str(log), sdk._get_proj())
    assert report["ok"] is True, (
        "the pure fold dropped a record the node holds, so the checker reds a "
        "faithful graph with advice that reproduces it: "
        + str(report.get("divergent_points")))


def test_an_unwritable_record_folds_as_absent_not_as_stored(env):
    """The store-exactness gate on the REVISION fold (same review P1 arm).

    `update_point(mentions="a\\x00b")` stores NO property live: the raw string
    passes the payload shape filter, reaches `SET n += $props`, and the #7369
    transport gate nulls it at dispatch — which REMOVES the property, the same
    clear `_revise_point` spells as `n.mentions = NULL`. A pure fold that kept
    the raw string instead redded a FAITHFUL graph on `fields: ['mentions']`
    (measured at the pre-fix head) with the advice "replay the journal" that
    reproduces that graph.

    Mutant it reds on: folding `ev["mentions"]` without the store-exactness
    gate.
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft", mentions=MENTIONS)["id"]
    assert _node_prop(sdk, pid) is not None

    sdk.update_point(pid, mentions="a\x00b")
    assert _node_prop(sdk, pid) is None, (
        "guard: the store must have REFUSED the unusable string — otherwise "
        "this row measures nothing")

    from tortoise.consistency import check_consistency

    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "the fold kept a value the store refused, so the checker reds a "
        "faithful graph: " + str(report.get("divergent_points")))

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _node_prop(sdk, pid) is None, (
        "the rebuild resurrected a record the live write could not store")


#: Any non-empty conversation — the extractor is replaced by a payload below.
_CAPTURE_CONV = [
    {"role": "user", "content": "the timeout command fails because it exceeds"},
    {"role": "assistant", "content": "ack"},
]


def test_the_capture_seam_carries_the_payload_mentions_to_the_node(env,
                                                                  monkeypatch):
    """The SDK capture seam's create branch — review R2(c) on PR #11795.

    `_extract_session_v2` reads the payload point's `mentions` beside its other
    passthrough reads and merges it into the CREATE props. Before this row the
    only guard on that block was three `inspect.getsource` substrings, which
    survive `if False and _mention_records:` — measured on the reviewed head:
    the write is neutered completely and every assertion stays green.

    Driven hermetically: the offline `TORTOISE_SESSION_LLM_MOCK=1` provider
    seam plus a payload-injecting fake extractor (the
    `tests/test_capture_validfrom_3945.py` pattern), so no network and no model
    call is spent.
    """
    import tortoise.extractor_v2 as ev2

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)

    pid = "pt_11239_capture_seam"
    payload = {
        "entities": [], "events": [], "operators": [],
        "points": [{
            "id": pid,
            "content": CONTENT,
            "pointKind": "statement",
            "reason": "NEW",
            "confidence": 0.5,
            "c_cal": 0.5,
            "about_entities": [],
            "source_ref": "session.md",
            "status": "draft",
            # the record rides the PAYLOAD point, exactly as `route_mentions`
            # leaves it — the seam is the only thing between here and the node
            "mentions": [{"surface": "it", "span_start": 0, "span_end": 2,
                          "kind_guess": "pronoun"}],
        }],
    }

    def _fake_extract(model, conversation, **kw):
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "chain_notes": [], "link_before_create": [],
                "warnings": [], "story_arc": "", "search": {},
                "stats": {}, "errors": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)

    sdk, events = env
    receipt = sdk._capture_session(_CAPTURE_CONV, session_id="11239-capture")
    assert receipt["ok"] is True, receipt
    assert receipt["errors"] == [], receipt

    live = _node_prop(sdk, pid)
    # PRESENCE FIRST — an absent-on-both-sides equality is the silent-drop
    # signature this whole issue is about.
    assert live is not None, (
        "the capture seam read the payload's mentions and never carried them "
        "to the create: the record a customer's capture held is not on the "
        "claim Point (#11239)")
    assert json.loads(live)[0]["surface"] == "it", live

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _node_prop(sdk, pid) == live, (
        "the capture seam's record did not survive the rebuild — live and "
        "replay must be a pure function of the payload (#330)")


def test_the_event_api_door_carries_a_mention_to_the_point(env):
    """`EventAPI.add_point` — the door that makes the PROJECTION encode load-bearing.

    Review finding 2 on the `e63a78de` head. `add_point` updates the point dict
    and journals `PointAdded(point=p)` with the payload's LIST spelling, and it
    calls no encoder of its own — so `_upsert_point_props`'s call is the ONLY
    one on its path, live as well as replayed. The docstring that called that
    call "DEFENCE-IN-DEPTH, not the load-bearing one" was therefore false, and
    would have invited a deletion that silently stops this door storing the
    record at all.

    Mutant this reds on: neutering `entities.encode_json_record_props` — the
    live write then stores NOTHING, so `live` is None here.
    """
    sdk, events = env
    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=sdk._get_proj())
    prov = provenance("session.md", [0, 23], "quote", extracted_by="t@0")
    pid = api.add_point(CONTENT, prov, mentions=[dict(m) for m in MENTIONS])

    live = _node_prop(sdk, pid)
    assert live is not None, (
        "`EventAPI.add_point` was handed a mention record and the claim Point "
        "holds none: this door journals the payload's LIST spelling and relies "
        "on the projection's encode, which is the only encoder on its path")
    _assert_record_is_the_payload_surfaces(live)

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _node_prop(sdk, pid)
    assert rebuilt is not None, (
        "the EventAPI door's record is LIVE-ONLY: the replay dropped it")
    assert rebuilt == live, (
        "the rebuild re-derived DIFFERENT bytes for the same record (#330)")


def test_an_addressed_clear_removes_the_stored_record_and_survives_a_rebuild(env):
    """`update_point(mentions=None)` — the ADDRESSED clear (review finding 8).

    Three comments rest the create door's `None`-drop on this verb being *the*
    documented way to delete a record (`sdk.py`'s `create_point` note,
    `projection.__init__`'s revision fold note, `_update_entity`'s journal
    note), and nothing pinned it — so if the clear ever regressed, the
    justification for dropping `None` at CREATE would be void and every row
    would stay green.

    This also keeps the two directions honest side by side: an addressed edit
    clears, an unaddressed re-commit (`test_a_recommit_with_no_mentions_...`,
    and the hosted-door row) does not.
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft", mentions=MENTIONS)["id"]
    stored = _node_prop(sdk, pid)
    assert stored is not None, "the create did not store the record"

    sdk.update_point(pid, mentions=None)
    cleared = _node_prop(sdk, pid)
    assert cleared is None, (
        "`update_point(mentions=None)` did not clear the stored record: this is "
        "the ADDRESSED clear, the one verb allowed to delete a mention — "
        f"without it the create door's None-drop has no sanctioned counterpart "
        f"(node still holds {cleared!r})")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _node_prop(sdk, pid) is None, (
        "the clear is LIVE-ONLY: the rebuild restored the deleted record, so "
        "live and replay disagree on a journal that says `mentions: None`")

    from tortoise.consistency import check_consistency

    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "live and replay agree, so the checker must not red this graph: "
        + str(report.get("divergent_points")))


RESTATED = [{"surface": "the problem", "span_start": 9, "span_end": 20,
             "kind_guess": "pronoun"}]


def test_a_restated_mention_replaces_the_record_in_both_folds(env):
    """A SECOND statement of the key must SET, not carry the old value forward.

    Review P1 on PR #11795, measured. `stated_json_record_prop` consulted
    `_is_persistable_prop_value` BEFORE the encoder, and that filter refuses the
    payload's OWN spelling (any array containing a dict) — so the payload's list
    of mention dicts was classified `"skip"`. The creation fold then carried the
    PREVIOUS incarnation's record forward while the live write and `rebuild_all`
    stored the NEW one, and `check_consistency` redded a FAITHFUL graph with the
    advice "replay the journal", which reproduces exactly that graph (the
    #5048/#5256 unclearable-red class). Reachable through a door this PR
    declares carried: `EventAPI.add_point` journals the list spelling.

    Mutant this reds on: the `_is_persistable_prop_value` pre-check ahead of the
    encoder (the pre-fix `stated_json_record_prop`) — the checker assertion
    fails on `fields: ['mentions']` while the node itself already agrees.
    """
    sdk, events = env
    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=sdk._get_proj())
    prov = provenance("session.md", [0, 23], "quote", extracted_by="t@0")
    pid = api.add_point(CONTENT, prov, mentions=[dict(m) for m in MENTIONS])
    first = _node_prop(sdk, pid)
    assert first is not None

    # The SAME point, restating the key with a DIFFERENT record.
    api.add_point(CONTENT, prov, id=pid, mentions=[dict(m) for m in RESTATED])
    live = _node_prop(sdk, pid)
    assert live is not None and live != first, (
        "the restated record did not reach the node")
    assert json.loads(live)[0]["surface"] == "the problem", live

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _node_prop(sdk, pid) == live, (
        "the creation fold carried the FIRST record forward instead of folding "
        "the restated one: live and replay disagree on the same journal")

    from tortoise.consistency import check_consistency

    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "live and replay agree, so the checker must not red this graph: "
        + str(report.get("divergent_points")))


def test_a_revision_stating_the_payload_spelling_folds_to_the_writers_bytes(env):
    """The REVISION arm, with the journal in the payload's list spelling.

    The sibling half of the same P1: the revision arm treated `"skip"` as the
    absence the graph fold produces (`p.pop`), so a journal whose `PointRevised`
    carries the PAYLOAD spelling CLEARED the node on one engine while
    `_revise_point` SET the encoded value on the other — two engines, opposite
    outcomes, one record. `stated_json_record_prop` now classifies that spelling
    as `"set"` for both arms.

    Rewriting the journaled `PointRevised` is the only way to reach it through a
    real `rebuild_all`.
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft")["id"]
    sdk.update_point(pid, mentions=MENTIONS)
    live = _node_prop(sdk, pid)
    assert live is not None

    log = events / "events.jsonl"
    rows = [json.loads(line) for line in log.read_text().splitlines()
            if line.strip()]
    rewritten = 0
    for row in rows:
        if row.get("type") == "PointRevised" and row.get("id") == pid \
                and isinstance(row.get("mentions"), str):
            row["mentions"] = json.loads(row["mentions"])
            rewritten += 1
    assert rewritten >= 1, (
        "no journaled `PointRevised` carried `mentions` as the canonical "
        "scalar — this row can only pin the revision arm if it starts there")
    log.write_text("".join(json.dumps(row) + "\n" for row in rows))

    sdk._get_proj().rebuild_all(str(log.parent), confirm_destructive=True)
    rebuilt = _node_prop(sdk, pid)
    assert rebuilt == live, (
        "the revision fold disagreed with `_revise_point` on the same "
        f"list-shaped record: live {live!r} vs rebuilt {rebuilt!r}")

    from tortoise.consistency import check_consistency

    report = check_consistency(str(log), sdk._get_proj())
    assert report["ok"] is True, (
        "the two replay engines now agree, so the checker must not red: "
        + str(report.get("divergent_points")))


LATEST = [{"surface": "the limit", "span_start": 41, "span_end": 50}]


def test_a_creation_after_a_revision_wins_the_mention_record_on_replay(env):
    """The pass-1b boundary's PER-KEY anchor (review residual on PR #11795).

    `_rebuild_all_replay` folds in two passes: 1a applies every creation, then
    1b applies every revision. A `PointRevised` therefore reaches the node
    AFTER creations that were journalled later than it, and its
    presence-conditional `mentions` clause would overwrite the newer creation's
    record out of order — so pass 1b gates that clause on
    `last_mentions_write_by_source[key] > seq`: the boundary is the creation
    that actually WROTE the record, read from the writer's reported key set.

    That term had NO discriminating row (every branch row stayed green with it
    deleted), which is the state the review flagged as a residual: a boundary
    nothing varies is a boundary nobody maintains. This row is the input it
    exists for — a revision sandwiched between two creations, all three stating
    the key.

    Measured: the LAST statement in the journal wins (`LATEST`), on both
    engines — deleting the `last_mentions_write_by_source` term from
    `skip_mentions` makes the rebuild restore the SECOND statement (`RESTATED`)
    and `check_consistency` reds the resulting graph on `fields: ['mentions']`.
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft", mentions=MENTIONS)["id"]
    assert _node_prop(sdk, pid) is not None, "the fixture never stored a record"

    # A revision that STATES the key, journalled BETWEEN the two creations.
    sdk.update_point(pid, mentions=RESTATED)
    live = _node_prop(sdk, pid)
    assert live is not None
    assert [r["surface"] for r in json.loads(live)] == ["the problem"]

    # A SECOND creation, journalled after the revision, stating a third record.
    # Hand-written for the same reason the sibling carry-forward row is: no
    # in-tree producer emits a second `PointAdded` for a claim id.
    log = events / "events.jsonl"
    rows = [json.loads(line) for line in log.read_text().splitlines()
            if line.strip()]
    assert any(r.get("type") == "PointRevised" and r.get("id") == pid
               and r.get("mentions") for r in rows), (
        "this row can only pin the boundary if the journal really holds a "
        "mention-carrying revision")
    source = next(r for r in rows
                  if r.get("type") == "PointAdded"
                  and (r.get("point") or {}).get("id") == pid)
    later = json.loads(json.dumps(source))
    later.pop("seq", None)  # the replay's seq space is the enumerate index
    later["point"]["mentions"] = [dict(m) for m in LATEST]
    rows.append(later)
    log.write_text("".join(json.dumps(row) + "\n" for row in rows))

    sdk._get_proj().rebuild_all(str(log.parent), confirm_destructive=True)
    rebuilt = _node_prop(sdk, pid)
    assert rebuilt is not None, (
        "the rebuild dropped a record the journal states three times")
    assert [r["surface"] for r in json.loads(rebuilt)] == ["the limit"], (
        "the replay folded the OLDER revision OVER the later creation: the "
        "creation that wrote the record last must win on every engine, or the "
        f"rebuilt node holds a superseded record ({rebuilt!r})")

    from tortoise.consistency import check_consistency

    report = check_consistency(str(log), sdk._get_proj())
    assert report["ok"] is True, (
        "both replay engines fold the same journal to the same record, so the "
        "checker must not red: " + str(report.get("divergent_points")))


def test_the_first_creation_folds_the_writers_bytes_not_the_payload_spelling(env):
    """The creation fold's disposition runs on the FIRST creation too (review P2).

    The `set`/`skip`/`null` disposition used to sit inside the carry-forward
    block, which only runs when the id ALREADY has a folded entry — so a fresh
    claim's payload reached `points[p["id"]] = p` verbatim and the journal view
    held the raw LIST while the node held the compact SCALAR. One payload, two
    fold values, which is the "one payload, one value" property this prop
    exists for (`_upsert_point_props` / `stated_json_record_prop` share it).

    `EventAPI.add_point` is the door that journals the list spelling
    (`api.py` passes its record to `_emit` positionally, rendered as
    `point`, with no store-value filter), so it is also the door that reaches
    this arm on a FIRST creation.

    Mutant it reds on: nesting the loop back inside `if isinstance(_prev, dict)`
    (the pre-fix shape) — the fold then yields the payload list, not the scalar.
    """
    from tortoise.consistency import _fold_journal

    sdk, events = env
    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=sdk._get_proj())
    prov = provenance("session.md", [0, 23], "quote", extracted_by="t@0")
    pid = api.add_point(CONTENT, prov, mentions=[dict(m) for m in MENTIONS])
    live = _node_prop(sdk, pid)
    assert live is not None, "the add_point door did not store the record"

    rows = [json.loads(line)
            for line in (events / "events.jsonl").read_text().splitlines()
            if line.strip()]
    assert any(isinstance((r.get("point") or {}).get("mentions"), list)
               for r in rows if r.get("type") == "PointAdded"), (
        "this row can only pin the fold's dispose if the journal really carries "
        "the PAYLOAD spelling")
    assert len([r for r in rows
                if r.get("type") == "PointAdded"
                and (r.get("point") or {}).get("id") == pid]) == 1, (
        "the row must reach the FIRST-creation arm — a second PointAdded takes "
        "the carry-forward branch instead")

    folded = _fold_journal(rows)[pid]
    assert isinstance(folded.get("mentions"), str), (
        "the FIRST creation folded the payload's raw list: the writer stored "
        f"the encoded scalar, the fold kept {folded.get('mentions')!r}")
    assert folded["mentions"] == live, (
        "the fold's view and the node disagree on the same journal record: "
        f"folded {folded['mentions']!r} vs node {live!r}")


def test_an_unwritable_record_on_a_first_creation_folds_as_absent(env):
    """The other half of the same arm — the #7369 null (review P2).

    `add_point(mentions="a\\x00b")` journals that raw string; the transport gate
    nulls it at dispatch, so the node holds NO property at all. A fold that
    keeps the raw value makes `check_consistency` compare it against an absent
    property on a FAITHFUL graph — and `consistency._uncarried` classes a `str`
    as canonically carried, so nothing suppresses the comparison: the #5048
    red whose own advice ("replay the journal") reproduces it.

    Mutant it reds on: nesting the loop back inside `if isinstance(_prev, dict)`
    (no `_prev` on a first creation, so the null disposition never runs).
    """
    from tortoise.consistency import _fold_journal, check_consistency

    sdk, events = env
    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=sdk._get_proj())
    prov = provenance("session.md", [0, 23], "quote", extracted_by="t@0")
    pid = api.add_point(CONTENT, prov, mentions="a\x00b")
    assert _node_prop(sdk, pid) is None, (
        "guard: the store must have refused the unusable string — otherwise "
        "this row measures nothing")

    rows = [json.loads(line)
            for line in (events / "events.jsonl").read_text().splitlines()
            if line.strip()]
    folded = _fold_journal(rows)[pid]
    assert "mentions" not in folded, (
        "the fold kept a record the node cannot hold (#7369 nulls it before "
        f"dispatch): {folded.get('mentions')!r}")

    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "the graph is faithful, so the checker must not red it: "
        + str(report.get("divergent_points")))


def test_the_uncarried_branch_names_what_the_encoder_pops(env):
    """The `_uncarried` mentions branch's DECISIONS, pinned (review P3, PR #11795).

    On the ordinary `PointAdded`/`PointRevised` path the fold disposes the value
    first, so this branch sees the fold's view; what it still decides there is
    (i) an EMPTY list must not be reported as an `_UNCARRIED_LIST` loss and (ii) a
    non-str/non-list shape the fold's `skip` arm leaves in the view must be NAMED
    with the encoder's reason (`encode_json_record_props` POPS it — measured,
    `_is_persistable_prop_value(5)` is True, so the map/bytes/set text would
    misdirect the diagnosis).

    Mutant this reds on: restoring the `_is_persistable_prop_value` pre-check
    (the number stops being named) or routing the empty list to `_UNCARRIED_LIST`.
    """
    from tortoise.consistency import _UNCARRIED_JSON_RECORD_POPPED, _uncarried

    assert _uncarried({"p1": {"mentions": []}}) == {}, (
        "an empty list is POPPED by the writer, not a lost list — reporting it "
        "would be an `_UNCARRIED_LIST` false positive")

    named = _uncarried({"p1": {"mentions": 5}})
    assert named == {"p1": {"mentions": _UNCARRIED_JSON_RECORD_POPPED}}, (
        "a non-str/non-list `mentions` must be named with the ENCODER's reason, "
        f"not `_is_persistable_prop_value`'s: {named}")


def test_a_promotion_carrying_a_store_refusing_scalar_is_named_uncarried(env):
    """The REACHABLE hand-built path: a `PointPromoted` snapshot.

    `_fold_journal` merges a promotion's snapshot VERBATIM (`merged.update(p)`),
    with no `stated_json_record_prop` pass — so a store-refusing scalar arrives at
    `_uncarried` through `check_consistency` itself, not only through a
    hand-called helper. This is the row that makes the branch's store-exactness
    test reachable rather than defensive-only (review P3 on PR #11795).

    Mutant this reds on: the shape-only `isinstance(v, str)` test (the value is
    then reported as carried and the promotion's divergence is never named).
    """
    sdk, events = env
    pid = sdk.create_point("statement", CONTENT, dedup=True,
                           status="draft")["id"]

    log = events / "events.jsonl"
    rows = [json.loads(line) for line in log.read_text().splitlines()
            if line.strip()]
    rows.append({"type": "PointPromoted",
                 "point": {"id": pid, "mentions": "a\x00b"}})
    log.write_text("".join(json.dumps(row) + "\n" for row in rows))

    from tortoise.consistency import (
        _UNCARRIED_JSON_RECORD_UNWRITABLE,
        _fold_journal,
    )

    folded = _fold_journal(rows)[pid]
    assert folded.get("mentions") == "a\x00b", (
        "the promotion arm no longer merges the snapshot verbatim — this row "
        f"can only prove the branch is reachable if it does: {folded!r}")

    from tortoise.consistency import _uncarried

    named = _uncarried({pid: folded})
    assert named.get(pid, {}).get("mentions") == (
        _UNCARRIED_JSON_RECORD_UNWRITABLE), (
        "a promoted snapshot carrying a store-refusing scalar was reported as "
        f"carried: {named}")
