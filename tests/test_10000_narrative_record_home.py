"""§9.1's narrative must reach the RECORD, and must NAME the session it pairs with (#10000).

THE DEFECT THIS PINS. `#10389` removed the two raw-Cypher writes that used to hold
the S1 narrative on a node (`s.story_arc` / `e.story_arc`) — §9.1/D1 puts the text
in Postgres — and the `SessionNarrated` carrier covers the COMMIT path. The
tenant doors then had no record half at all: `create_event(story_arc=…)` was
accepted, the graph fold dropped the key (`projection.entities._EVENT_HANDLED`),
and nothing wrote §9.1's home. Measured on the sibling lane
(`#10399` comment `6545567190`) as "the prose is in NEITHER store for that door".

WHY A SESSION IS THE WHOLE FIX, and not a new writer. The record's narrative home
is filled by the shape-keyed projection
(`record_projection._narrative_statements`): any record that names its session
AND carries `story_arc` upserts `public.session_narrative`. So a narrative with a
session ALREADY lands, through the seam that exists — measured at #10389's head,
Arm B (`tortoise_record_app`), `create_event(..., story_arc=<prose>,
sessionId='nstory-door-session-1')` wrote a 91-char row into
`public.session_narrative` while the graph held zero copies of the prose. The
ONLY shape with nowhere to go is the UNPAIRED one, and that is what this guard
refuses — loudly, at the door, before any write.

⛔ The refusal is NOT in the fold. The fold that drops the key is also the REPLAY
fold (`rebuild_all`, `recover_from_log`, `backup.restore`), so a raise there would
redden every rebuild of a journal holding a legacy `story_arc` line; a legacy line
replays to that fold's named warning, which is the declared trade.

WHAT THIS FILE DOES NOT PROVE: that a live Postgres accepted the row (that is the
`TEST_RECORD_DB_URI` legs of `tests/test_9555_narrative_carrier.py` and
`tests/test_9078_record_written_by_production_path.py`). It asserts the record
STATEMENT the projection emits for a PAIRED payload, so it is DB-free and runs on
the PR path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tortoise import record_projection  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402

PROSE = ("NARRATIVE-TENANT-DOOR-PROSE-10000D the session began with a probe "
         "and ended with a decision")
MARKER = "NARRATIVE-TENANT-DOOR-PROSE-10000D"
SESSION = "test-10000-narrative-session"


@pytest.fixture()
def sdk(tmp_path, monkeypatch):
    """A real SDK with its own embedded graph and its own journal."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    return TortoiseSDK(str(tmp_path / "graph.db"),
                       event_log_path=str(tmp_path / "journal.jsonl"))


def _prose_on_nodes(sdk) -> list:
    return sdk._get_proj().g.query(
        "MATCH (n) WHERE (n.story_arc CONTAINS $a) OR (n.content CONTAINS $a) "
        "OR (n.summary CONTAINS $a) RETURN labels(n), n.id",
        params={"a": MARKER}).result_set


def test_an_unpaired_narrative_is_refused_rather_than_stored_nowhere(sdk):
    """The measured hole: accepted, dropped by the fold, in no store (#10399 residual)."""
    with pytest.raises(ValueError) as exc:
        sdk.create_event("probe-unpaired", "AgentSession", story_arc=PROSE)
    message = str(exc.value)
    assert "story_arc" in message
    assert "sessionId" in message, (
        "the refusal must name the fix — the session the narrative pairs with")
    assert "§9.1" in message, "the refusal must cite the rule it enforces"
    # Nothing was written: no node, no journal line. A refusal that half-wrote
    # would be the same silent-loss class in a different costume.
    assert _prose_on_nodes(sdk) == []


def test_the_update_door_refuses_it_too(sdk):
    """`update_entity` is the second write door onto the same key (measured at #10389).

    ⚠️ ASSERT THE MESSAGE, not just the exception, and target a label whose
    refusal does NOT already come from a server-managed set: an `:Event` update
    is refused by `_EVENT_SERVER_MANAGED_PROPS` before this guard runs (measured
    pre-fix), so `pytest.raises(ValueError)` on an `:Event` would pin nothing
    about this change. `:Object` is the label that used to ACCEPT the write.
    """
    obj = sdk.create_object("probe-update-door")
    object_id = obj.get("id") or obj.get("objectId")
    with pytest.raises(ValueError) as exc:
        sdk.update_entity(object_id, story_arc=PROSE, sessionId=SESSION)
    assert "cannot be written through update_entity" in str(exc.value), (
        "the UPDATE door's own refusal must fire — a paired narrative is still "
        "unfileable through an update, which writes caller props onto the node")
    assert _prose_on_nodes(sdk) == []


def test_a_label_whose_fold_does_not_refuse_the_key_is_refused(sdk):
    """`:Object`/`:Subject` carry `story_arc` through #228 — a paired write there
    duplicates the prose into the graph (measured at #10389's head)."""
    with pytest.raises(ValueError) as exc:
        sdk.create_object("probe-object", story_arc=PROSE, sessionId=SESSION)
    message = str(exc.value)
    assert "cannot be written on a Object" in message
    assert "Object fold does not refuse the key" in message
    assert _prose_on_nodes(sdk) == []


@pytest.mark.parametrize("bad", [{"a": 1}, ["x"], 42, True])
def test_a_non_string_narrative_is_refused(sdk, bad):
    """A non-string lands in NO store: the fold drops the key and the record
    projects only a non-empty `str` (measured, review cycle 2 P2)."""
    with pytest.raises(ValueError) as exc:
        sdk.create_event("probe-nonstring", "AgentSession",
                         story_arc=bad, sessionId=SESSION)
    assert "must be the narrative TEXT" in str(exc.value)
    assert _prose_on_nodes(sdk) == []


def test_an_empty_journal_path_is_not_an_armed_journal(tmp_path, monkeypatch):
    """`event_log_path=""` is falsy for `_get_event_log`, so it must be falsy for
    the guard too — measured: the empty string defeated the no-journal refusal
    and the prose was stored nowhere with a 2xx (review cycle 2 P2)."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    empty = TortoiseSDK(str(tmp_path / "graph-emptyjournal.db"), event_log_path="")
    with pytest.raises(ValueError) as exc:
        empty.create_event("probe-empty-journal", "AgentSession",
                           story_arc=PROSE, sessionId=SESSION)
    assert "no record to be filed in" in str(exc.value)
    assert _prose_on_nodes(empty) == []


@pytest.mark.parametrize("door", ["create_point", "update_point"])
def test_the_point_doors_refuse_the_narrative(sdk, door):
    """§9.1 says "not a `Point`", and neither Point door consults a deny-set —
    `update_point` writes raw `SET n += $props` and journals `PointRevised`
    (measured, review cycle 3), so a fold-only deny would make live ≠ replay."""
    if door == "create_point":
        with pytest.raises(ValueError) as exc:
            sdk.create_point("statement", "a claim", story_arc=PROSE)
        assert "cannot be written on a Point" in str(exc.value)
    else:
        point = sdk.create_point("statement", "a claim for the update door")
        pid = point.get("id") or point.get("pointId")
        with pytest.raises(ValueError) as exc:
            sdk.update_point(pid, story_arc=PROSE, sessionId=SESSION)
        assert "cannot be written on a Point" in str(exc.value)
    assert _prose_on_nodes(sdk) == []


def test_a_paired_narrative_with_no_journal_is_refused(tmp_path, monkeypatch):
    """No journal ⇒ `_emit_event` returns before the record leg, so the prose is
    in no store and the caller gets a 2xx (measured: no journal file created)."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    journal_less = TortoiseSDK(str(tmp_path / "graph-nojournal.db"))
    with pytest.raises(ValueError) as exc:
        journal_less.create_event("probe-no-journal", "AgentSession",
                                  story_arc=PROSE, sessionId=SESSION)
    assert "no record to be filed in" in str(exc.value)
    assert _prose_on_nodes(journal_less) == []


def test_a_paired_narrative_is_journalled_and_files_its_record_row(sdk, tmp_path):
    """§9.1 end to end, DB-free: prose OFF the graph, ON a record the home can key."""
    sdk.create_event("probe-paired", "AgentSession",
                     story_arc=PROSE, sessionId=SESSION)
    # (b) nothing duplicated back into the graph
    assert _prose_on_nodes(sdk) == []
    # the node still exists as an identity carrier
    assert sdk._proj.g.query(
        "MATCH (e:Event {eventKind: 'AgentSession'}) RETURN count(e)"
    ).result_set[0][0] >= 1
    # (a) the record carries the prose WITH its session coordinate — the pair
    lines = [json.loads(line) for line in
             (tmp_path / "journal.jsonl").read_text().splitlines() if line.strip()]
    carriers = [l for l in lines if l.get("story_arc")]
    assert carriers, "the narrative must reach the journal, or it is in no store"
    assert carriers[-1]["sessionId"] == SESSION
    # …and the record's OWN projection turns that line into §9.1's row
    statements = record_projection.replay_plan(
        carriers, org_id="org-test", graph_id="g-test")
    inserts = [s for s in statements
               if "INSERT INTO public.session_narrative" in s.sql]
    assert inserts, "the shape-keyed home must claim a paired narrative"
    assert any(PROSE in [p for p in s.params if isinstance(p, str)]
               for s in inserts), (
        "the row's narrative column must carry the prose itself, not a count")
    assert any(SESSION in s.params for s in inserts), (
        "the row must be keyed by the session the narrative pairs with")


@pytest.mark.parametrize("door,label", [
    ("source", "Source"),
    ("document", "Document"),
])
def test_the_other_two_refusing_labels_accept_a_paired_narrative(sdk, door, label):
    """The ACCEPT side of `_NARRATIVE_LABELS` (#10389 review cycle 2 P3).

    Only `Event` was pinned; a regression that re-admitted `story_arc` on the
    `:Source`/`:Document` fold would ship green while the guard kept accepting
    the write — prose in the graph AND in the record, the duplicate home
    §9.1(b)/R7 exists to remove.
    """
    if door == "source":
        created = sdk.create_source(f"https://example.test/{SESSION}",
                                    "webpage", story_arc=PROSE, sessionId=SESSION)
    else:
        created = sdk.create_document(f"probe-{SESSION}", documentKind="transcript",
                                      story_arc=PROSE, sessionId=SESSION)
    assert created, f"the {label} door refused a paired narrative"
    assert _prose_on_nodes(sdk) == [], (
        f"the prose reached a {label} node through the create door")
    # ⚠️ A document IS a `:Source` (D10/§9.2), so BOTH doors are read on that
    # label — asking for `:Document` would find nothing and "prove" a refusal
    # that never happened.
    assert sdk._proj.g.query(
        "MATCH (n:Source) RETURN count(n)").result_set[0][0] >= 1, (
        f"a {label} create proved nothing — no :Source anchor exists")
