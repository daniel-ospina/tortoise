"""#10053 review, P1 — a DATED retraction must survive the durability gate.

THE DEFECT THIS PINS
--------------------
Phase 2.2 makes retraction a **temporal field** on the merged revise verb:
`update_knowledge(id, valid_until=…)` / `update_knowledge(id, invalid_at=…)` write the
caller's instant as the point's `validTo`. That value is carried by FIVE arms, and only
four of them were taught about it:

    live CAS                sdk.py            `retract_point` → `SET n.validTo = $valid_to`
    journal replay (pure)   projection/…      `_apply_one`'s PointRetracted arm
    journal replay (graph)  projection/…      `_retract` → `, n.validTo = $valid_to`
    record                  record_projection the record carries `valid_to`
    reference fold          consistency.py    <-- the fifth arm, which knew nothing

`check_consistency`'s reference fold is what `derived = replay(journal)` is measured
against, and `validTo` is in none of its silence maps (`_EXCLUSION_REASONS`,
`_ONE_SIDED_REASONS`, `_WRITER_DEFAULTS`, `_GRAPH_DEFAULTED_PROPS`). `_compare_views`
iterates the UNION of the journal-side and graph-side field sets, so a key present on the
graph only falls through every `continue` into `fields` → `mismatches` → `hash_ok = False`
→ `divergence = "content"`.

So before this file, every dated retraction left `tortoise check-consistency` — the CLI's
own durability verdict — reporting a content divergence on a graph that was byte-faithful,
and the advice it printed ("reconcile by replaying the journal") could never clear it,
because replay writes the key onto the GRAPH while the reference fold can never produce it.

WHY THE SIBLING SUITE MISSED IT
-------------------------------
`tests/test_5048_retract_instant_recorded.py` exercises `check_consistency` around a
retraction, but `updatedAt` is in `_NOT_COMPARED`, so its arms never reach `_compare_views`
— which is exactly why #5048 needed no reference-fold edit and this change does. And the
merge's own test file is deliberately graph-free (routing is what a merge changes), so no
test retracted with a window end and then asked the durability gate.

Run (embedded carve-out):
  TORTOISE_TEST_CARVE_OUT=1 python -m pytest \\
      tests/test_4282_dated_retraction_durability.py -q
"""
from __future__ import annotations

import json

import pytest

from tortoise.consistency import check_consistency
from tortoise.sdk import TortoiseSDK

#: The caller's window end. Deliberately NOT "now": a re-read clock and a carried instant
#: are indistinguishable if the test uses the instant the writer would mint anyway.
INSTANT = "2026-01-01T00:00:00Z"


def _node(sdk, pid: str) -> dict:
    """Raw read — a retracted Point is filtered out of every public surface."""
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN properties(n)", params={"i": pid}
    ).result_set
    assert rows, f"point {pid!r} missing from the graph"
    return rows[0][0]


def _stamp(sdk, pid: str, instant: str) -> None:
    """Overwrite the node's end so a replay that did NOTHING cannot satisfy the assertion."""
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) SET n.validTo = $t", params={"i": pid, "t": instant})


@pytest.fixture
def dated(tmp_path):
    """(events_dir, sdk, pid) with one point retracted THROUGH the merged verb."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "dated.db"),
                      event_log_path=str(events / "events.jsonl"))
    pid = sdk.create_point("statement", "a claim that stopped holding on a date")["id"]
    sdk.update_knowledge(pid, valid_until=INSTANT)
    yield events, sdk, pid
    sdk.close()


# ── 1. the value reaches the graph (the premise the gate then measures) ───────

def test_the_merged_verb_writes_the_callers_instant_to_the_node(dated):
    """Non-vacuity for everything below: the graph really does carry `validTo`.

    If the instant were still used as a BOOLEAN selector (the pre-#4282 shape) the node
    would have no `validTo` at all, and the reference fold's omission could not be
    observed — the gate below would pass for the wrong reason.
    """
    _events, sdk, pid = dated
    node = _node(sdk, pid)
    assert node["status"] == "retracted", node
    assert node["validTo"] == INSTANT, (
        "the caller's window end never reached the node — row 22 says retraction is a "
        f"temporal FIELD, not a verb; node={node!r}")


# ── 2. THE durability invariant: derived = replay(journal) ───────────────────

def test_the_record_really_carries_the_window_end(dated):
    """The JOURNAL, not just the change-feed payload.

    `_emit_event`'s positional `payload=` reaches the `:GraphEvent` store only — the JSONL
    record is assembled from `point`/`id`/`**extra`. So `retract_point` journalled a
    tombstone with NO `valid_to` while the live node held `validTo`, and both replay tests
    below failed with `KeyError: 'validTo'`. This asserts the datum at its source, so a
    future `payload=`-only shape is named here rather than surfacing as a puzzling rebuild
    divergence.
    """
    events, _sdk, _pid = dated
    records = [json.loads(line) for line in
               (events / "events.jsonl").read_text().splitlines() if line.strip()]
    retracted = [r for r in records if r.get("type") == "PointRetracted"]
    assert len(retracted) == 1, retracted
    assert retracted[0].get("valid_to") == INSTANT, (
        "the retraction record does not carry the caller's window end, so no replay can "
        f"restore it: record={retracted[0]!r}")


def test_a_dated_retraction_leaves_the_durability_gate_clean(dated):
    """`check_consistency` must not report a divergence on a faithful graph.

    FAILS BEFORE (the reason this file exists): the reference fold had no `validTo` for a
    retraction, `validTo` is silenced by none of the four maps, and `_compare_views`
    unions both field sets — so this asserted `divergence="content"` with `validTo` named
    in `divergent_points`, on a graph the four other arms had written correctly.
    """
    events, sdk, pid = dated
    proj = sdk._get_proj()
    log_path = str(events / "events.jsonl")

    first = check_consistency(log_path, proj)  # adopts the baseline on a first healthy run
    assert first["ok"], first

    result = check_consistency(log_path, proj)
    named = {d["id"]: d["fields"] for d in result["divergent_points"]}
    assert result["divergence"] is None, (
        "a faithful dated retraction was reported as a divergence — the reference fold is "
        f"not folding the window end. divergence={result['divergence']!r} named={named!r}")
    assert result["hash_match"] is True
    assert "validTo" not in named.get(pid, []), named


def test_a_rebuild_reproduces_the_recorded_window_end(dated):
    """The other half of the invariant: the replay engine must write the same end.

    Sentinel-stamped first, so a `rebuild_all` that did nothing cannot satisfy this.
    """
    events, sdk, pid = dated
    _stamp(sdk, pid, "REPLAY-DID-NOT-RUN")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    after = _node(sdk, pid)
    assert after["status"] == "retracted", after
    assert after["validTo"] == INSTANT, (
        f"the replay did not restore the recorded window end: rebuilt={after.get('validTo')!r}")


# ── 3. the legacy shape stays symmetric-absent ───────────────────────────────

def test_an_undated_retraction_is_still_clean_and_states_no_end(dated):
    """A retraction with no temporal field must not gain one — and must stay gate-clean.

    The reference fold writes `validTo` only "when the journal states it" (the same
    `_usable_instant` gate both replay arms use), so the undated path is symmetric-absent
    rather than a `None` on one side and a missing key on the other.
    """
    events, sdk, pid = dated
    bare = sdk.create_point("statement", "a claim withdrawn with no date")["id"]
    sdk.retract_point(bare)

    node = _node(sdk, bare)
    assert node["status"] == "retracted", node
    assert "validTo" not in node, (
        f"an undated retraction invented a window end: {node.get('validTo')!r}")

    proj = sdk._get_proj()
    log_path = str(events / "events.jsonl")
    check_consistency(log_path, proj)  # adopt
    result = check_consistency(log_path, proj)
    named = {d["id"]: d["fields"] for d in result["divergent_points"]}
    assert result["divergence"] is None, named
