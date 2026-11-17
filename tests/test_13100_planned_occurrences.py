"""#13100 (S7) — a planned occurrence is a PLAN; an ``:Event`` is what happened.

The approved model (``docs/plans/2026-11-15-state-lifecycle-and-pipelines.md``
§8) says there is no third case: a thing has either happened — and is a dated
record you never edit — or it has not, and is a plan. Before this change the
graph had a third case, twice over: an ``:Event`` carried a writer-supplied
non-terminal ``status`` (``scheduled`` / ``draft`` / ``live``) as well as the
``eventStatus`` ingest marker, and the ``status`` half was load-bearing for
operator promotion.

What this file proves, one class per acceptance bullet:

* :class:`TestThePlanPipeline` — a plan advances through its stages and reaches
  a terminal one with an outcome.
* :class:`TestNoEventCarriesALifecycleWord` — no ``:Event`` carries a lifecycle
  word, and a legacy row can still be cleared.
* :class:`TestThePromotionSuppressionIsRehomed` — the operator-promotion
  suppression still holds: re-homed onto the plan's POSITION, and still reading a
  stored legacy word on the **Event path** (a test on that path, not just on
  draft Points).
* :class:`TestPlannedButNeverHappened` — *"what was planned and never
  happened?"* is answerable from position + link, with no mutable status read.
* :class:`TestTheIngestMarkerIsNotAnOccurrence` — an ingested ``eventStatus``
  transition cannot be mistaken for an occurrence.

Lane: **embedded** (``TORITOISE_TEST_CARVE_OUT=1``-style: no server), one
throwaway graph per case — the shape ``tests/test_pipeline_2792.py`` and
``tests/test_11825_event_terminal_status_refused.py`` use, because the stage
write goes through the journaled ``EntityMutated`` path.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise import pipeline
from tortoise import planned_occurrence as PO
from tortoise.live import status_as_reported
from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk():
    root = tempfile.mkdtemp(prefix="tortoise_13100_")
    client = TortoiseSDK(os.path.join(root, "t.db"),
                          event_log_path=os.path.join(root, "events.jsonl"))
    client._test_root = root
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


def _stage(sdk, plan_id):
    return pipeline.current_stage(sdk._get_proj().g, plan_id)


# ── acceptance 1: a plan advances through its stages, terminal + outcome ─────


class TestThePlanPipeline:
    def test_the_declaration_reuses_the_shipped_slots_and_outcomes(self):
        """No new vocabulary: the slots and outcomes are ``pipeline``'s own."""
        stages = {s["id"]: s for s in PO.PLAN_PIPELINE_DECL["stages"]}
        assert stages["scheduled"]["slot"] == "active"
        assert stages["scheduled"].get("outcome") is None
        for ending, outcome in (("held", "completed"),
                                ("missed", "lost"),
                                ("cancelled", "withdrawn")):
            assert stages[ending]["slot"] == "terminal"
            assert stages[ending]["outcome"] == outcome
            assert stages[ending]["outcome"] in pipeline.PIPELINE_OUTCOMES
        # The whole declaration validates against the shipped rules.
        assert pipeline.validate_pipeline_decl(PO.PLAN_PIPELINE_DECL) == []

    def test_the_plan_kind_shares_no_noun_with_the_event_kind(self):
        """The naming rule, pinned: an agent must not have to guess the word.

        A plan is an Object of the core ``plan`` kind (ONTOLOGY §5) — the bare
        and ``core:``-prefixed spellings are ONE kind, and neither is built out
        of the word "event".
        """
        assert PO.PLAN_KIND == "plan"
        assert PO.plan_kinds() == {"plan", "core:plan"}
        assert all("event" not in kind.lower() for kind in PO.plan_kinds())
        assert PO.is_plan_kind("plan") and PO.is_plan_kind("core:plan")
        assert not PO.is_plan_kind("core:goal") and not PO.is_plan_kind(None)

    def test_a_plan_is_born_scheduled(self, sdk):
        plan = PO.create_plan(sdk, "Board review Q3")
        cur = _stage(sdk, plan["id"])
        assert plan["objectKind"] == PO.PLAN_PIPELINE_KEY
        assert cur["stageId"] == "scheduled"
        assert cur["slot"] == "active"
        assert cur["isClosed"] is False

    def test_a_plan_reaches_a_terminal_stage_with_an_outcome(self, sdk):
        plan = PO.create_plan(sdk, "Board review Q3")
        recorded = PO.record_occurrence(sdk, plan["id"],
                                        name="Board review Q3 (held)")
        cur = _stage(sdk, plan["id"])
        assert recorded["move"].applied is True
        assert cur["stageId"] == "held"
        assert cur["slot"] == "terminal"
        assert cur["outcome"] == "completed"
        assert cur["isClosed"] is True

    @pytest.mark.parametrize("ending,outcome", [("missed", "lost"),
                                                ("cancelled", "withdrawn")])
    def test_a_plan_that_never_happened_ends_without_an_event(
            self, sdk, ending, outcome):
        plan = PO.create_plan(sdk, f"Never-{ending}")
        PO.settle_plan(sdk, plan["id"], ending)
        cur = _stage(sdk, plan["id"])
        assert cur["stageId"] == ending
        assert cur["outcome"] == outcome
        assert cur["isClosed"] is True
        # No occurrence Event exists — the ending is a position, not a fact.
        assert sdk._get_proj().g.query(
            "MATCH (:Event)-[:aboutObject]->(p:Object {id:$p}) RETURN count(*)",
            params={"p": plan["id"]}).result_set[0][0] == 0

    def test_a_settled_plan_refuses_a_second_ending(self, sdk):
        plan = PO.create_plan(sdk, "Settled once")
        PO.record_occurrence(sdk, plan["id"])
        with pytest.raises(ValueError, match="already settled"):
            PO.record_occurrence(sdk, plan["id"])
        with pytest.raises(ValueError, match="already settled"):
            PO.settle_plan(sdk, plan["id"], PO.STAGE_CANCELLED)

    def test_held_cannot_be_settled_without_an_occurrence(self, sdk):
        """``held`` asserts it happened — that is ``record_occurrence``'s job."""
        plan = PO.create_plan(sdk, "Held with no fact")
        with pytest.raises(ValueError, match="not a no-occurrence ending"):
            PO.settle_plan(sdk, plan["id"], PO.STAGE_HELD)

    def test_an_unknown_plan_reference_is_refused(self, sdk):
        with pytest.raises(ValueError, match="no plan Object"):
            PO.record_occurrence(sdk, "no-such-plan")


# ── acceptance 2: no :Event carries a lifecycle word ────────────────────────


class TestNoEventCarriesALifecycleWord:
    @pytest.mark.parametrize("word", ["draft", "live", "scheduled",
                                      "completed", "superseded", "in_progress"])
    def test_the_create_door_refuses_every_word(self, sdk, word):
        with pytest.raises(ValueError, match=":Event"):
            sdk.create_entity("event", f"E-{word}", eventKind="deployment",
                              status=word)
        assert not sdk._get_proj().g.query(
            "MATCH (e:Event {name:$n}) RETURN count(e)",
            params={"n": f"E-{word}"}).result_set[0][0]

    def test_the_update_door_refuses_and_clear_still_works(self, sdk):
        eid = sdk.create_entity("event", "Legacy", eventKind="deployment")[
            "node"]["id"]
        with pytest.raises(ValueError, match=":Event"):
            sdk.update_knowledge(eid, status="live")
        # A legacy row (raw/out-of-tree writer) can still be CLEARED — the
        # reader net stays wider than the write door (#2977).
        sdk._get_proj().g.query(
            "MATCH (e:Event {id:$e}) SET e.status='draft'", params={"e": eid})
        sdk.update_knowledge(eid, status=None)
        assert sdk._get_proj().g.query(
            "MATCH (e:Event {id:$e}) RETURN e.status",
            params={"e": eid}).result_set[0][0] is None

    def test_the_read_surface_still_reports_no_status_family(self):
        assert status_as_reported(None, label="Event") == ""
        # …and a stored legacy word is echoed, not invented.
        assert status_as_reported("draft", label="Event") == "draft"

    def test_the_refusal_teaches_the_plan_route(self, sdk):
        """AX (#10183): a refusal names the remedy, and the remedy is the plan."""
        with pytest.raises(ValueError) as exc:
            sdk.create_entity("event", "Future thing", eventKind="meeting",
                              status="scheduled")
        message = str(exc.value)
        assert "plan" in message and "eventStatus" in message


# ── acceptance 3: the promotion suppression is re-homed, not dropped ────────


def _operator_over(sdk, endpoints, op_id):
    """A draft operator with IMPL edges to every endpoint (the #7853 shape)."""
    g = sdk._get_proj().g
    g.query(
        "CREATE (o:Point {id:$opid, is_operator:true, op_type:'IMPL', "
        "direction:'bidirectional', status:'draft'})", params={"opid": op_id})
    for idx, sid in enumerate(endpoints):
        g.query(
            "MATCH (o:Point {id:$opid}), (s) WHERE s.id = $sid "
            "CREATE (o)-[:IMPL {idx:$idx}]->(s) "
            "CREATE (s)-[:INPUT {idx:$idx}]->(o)",
            params={"opid": op_id, "sid": sid, "idx": idx})


def _promote(sdk, point_id):
    return sdk._promote_incident_operators(
        sdk._get_proj(), point_id, "2026-01-01T00:00:00+00:00")


class TestThePromotionSuppressionIsRehomed:
    def test_a_scheduled_plan_endpoint_blocks_promotion(self, sdk):
        """THE RE-HOME: a plan that has not reached a terminal stage has not
        happened, so it suppresses exactly as the retired ``status='scheduled'``
        on an Event did."""
        point = sdk.create_point("statement", "a live point")["id"]
        plan = PO.create_plan(sdk, "Not yet")
        op = f"op-plan-{plan['id']}"
        _operator_over(sdk, (point, plan["id"]), op)

        assert op not in _promote(sdk, point), (
            "a draft operator over a still-scheduled plan was promoted")
        assert sdk._get_proj().g.query(
            "MATCH (o:Point {id:$o}) RETURN o.status",
            params={"o": op}).result_set[0][0] == "draft"

    def test_the_suppression_lifts_when_the_plan_is_settled(self, sdk):
        """Control, so the negative above cannot pass vacuously: the same
        operator promotes once the plan reaches a terminal stage."""
        point = sdk.create_point("statement", "a live point")["id"]
        plan = PO.create_plan(sdk, "Happened")
        op = f"op-settled-{plan['id']}"
        _operator_over(sdk, (point, plan["id"]), op)
        PO.record_occurrence(sdk, plan["id"], name="it happened")

        assert op in _promote(sdk, point), (
            "a settled plan must not suppress promotion for ever")
        assert sdk._get_proj().g.query(
            "MATCH (o:Point {id:$o}) RETURN o.status",
            params={"o": op}).result_set[0][0] == "live"

    def test_a_missed_plan_does_not_suppress(self, sdk):
        """``missed`` is terminal: it ended, so the record is settled."""
        point = sdk.create_point("statement", "a live point")["id"]
        plan = PO.create_plan(sdk, "Missed it")
        op = f"op-missed-{plan['id']}"
        _operator_over(sdk, (point, plan["id"]), op)
        PO.settle_plan(sdk, plan["id"], PO.STAGE_MISSED)
        assert op in _promote(sdk, point)

    def test_the_LEGACY_event_path_still_suppresses(self, sdk):
        """The acceptance bullet says "on the Event path", not "on draft Points".

        A pre-retirement Event row can still carry ``draft`` (a legacy journal, a
        raw producer). No write door accepts it any more, so the row is made
        below the doors — and the gate must still hold promotion, because
        refusing new writes does not correct a stored fact (#2977).
        """
        point = sdk.create_point("statement", "a live point")["id"]
        ev = sdk.create_entity("event", "Legacy draft", eventKind="deployment")[
            "node"]
        eid = ev.get("eventId") or ev.get("id")
        sdk._get_proj().g.query(
            "MATCH (e:Event {id:$e}) SET e.status='draft'", params={"e": eid})
        op = f"op-legacy-{eid}"
        _operator_over(sdk, (point, eid), op)

        assert op not in _promote(sdk, point), (
            "a stored legacy draft on an :Event endpoint no longer suppresses")


# ── acceptance 4: planned-but-never-happened, with no status read ───────────


class TestPlannedButNeverHappened:
    def test_the_read_separates_happened_from_not(self, sdk):
        g = sdk._get_proj().g
        held = PO.create_plan(sdk, "Held thing")
        PO.record_occurrence(sdk, held["id"], name="the held occurrence")
        PO.create_plan(sdk, "Upcoming thing")
        missed = PO.create_plan(sdk, "Missed thing")
        PO.settle_plan(sdk, missed["id"], PO.STAGE_MISSED)
        cancelled = PO.create_plan(sdk, "Cancelled thing")
        PO.settle_plan(sdk, cancelled["id"], PO.STAGE_CANCELLED)

        rows = {r["name"]: r for r in PO.planned_without_occurrence(g)}
        assert set(rows) == {"Upcoming thing", "Missed thing",
                             "Cancelled thing"}
        assert rows["Upcoming thing"]["stage"] == "scheduled"
        assert rows["Upcoming thing"]["ended"] is False
        assert rows["Missed thing"]["ended"] is True
        assert rows["Missed thing"]["outcome"] == "lost"
        assert rows["Cancelled thing"]["outcome"] == "withdrawn"
        # The answer is position + link only — no lifecycle word is returned.
        assert all("status" not in r for r in rows.values())

    def test_the_link_is_the_proof_not_the_plan_s_words(self, sdk):
        """A plan whose words say "held" but with no linked Event has no
        occurrence in the graph, and the read does not take its word for it."""
        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "Claims to have happened")
        g.query(
            "MATCH (p:Object {id:$p}) SET p.pipelineStageId='held', "
            "p.pipelineSlot='terminal', p.pipelineOutcome='completed', "
            "p.isClosed=true", params={"p": plan["id"]})
        names = [r["name"] for r in PO.planned_without_occurrence(g)]
        assert "Claims to have happened" in names

    def test_a_legacy_status_word_does_not_change_the_answer(self, sdk):
        """The read must not consult a mutable status — so a legacy word on the
        plan's node changes nothing."""
        g = sdk._get_proj().g
        held = PO.create_plan(sdk, "Held with a legacy word")
        PO.record_occurrence(sdk, held["id"], name="happened")
        g.query("MATCH (p:Object {id:$p}) SET p.status='draft'",
                params={"p": held["id"]})
        assert [r["name"] for r in PO.planned_without_occurrence(g)] == []


# ── acceptance 5: the ingest marker is not an occurrence ────────────────────


class TestTheIngestMarkerIsNotAnOccurrence:
    def test_creation_stamps_an_ingest_word_not_a_lifecycle_word(self, sdk):
        ev = sdk.create_entity("event", "Ingested thing",
                               eventKind="deployment")["node"]
        eid = ev.get("eventId") or ev.get("id")
        props = sdk._get_proj().g.query(
            "MATCH (e:Event {id:$e}) RETURN properties(e)",
            params={"e": eid}).result_set[0][0]
        assert props.get("eventStatus") == "pending"
        assert props.get("eventStatus") not in {"scheduled", "completed",
                                                "draft", "live"}
        assert "status" not in props

    def test_the_marker_transition_is_bookkeeping(self, sdk):
        """``pending`` -> ``indexed`` is an ordinary prop move: it creates no
        plan, no stage, and no occurrence."""
        g = sdk._get_proj().g
        ev = sdk.create_entity("event", "Indexed later",
                               eventKind="deployment")["node"]
        eid = ev.get("eventId") or ev.get("id")
        sdk.update_knowledge(eid, eventStatus="indexed")
        assert g.query("MATCH (e:Event {id:$e}) RETURN e.eventStatus",
                       params={"e": eid}).result_set[0][0] == "indexed"
        assert g.query("MATCH (p:Object) WHERE p.objectKind IN $k RETURN "
                       "count(p)", params={"k": sorted(PO.PLAN_KINDS)}
                       ).result_set[0][0] == 0
        assert PO.planned_without_occurrence(g) == []

    def test_no_in_tree_writer_emits_a_lifecycle_shaped_ingest_word(self):
        """The ingest vocabulary is pinned where the writers are declared."""
        import inspect

        from tortoise import hosted_api, sdk, session_indexer

        for module in (sdk, hosted_api, session_indexer):
            source = inspect.getsource(module)
            for word in ("eventStatus='scheduled'", 'eventStatus="scheduled"',
                         "eventStatus='completed'", 'eventStatus="completed"'):
                assert word not in source, (module.__name__, word)
