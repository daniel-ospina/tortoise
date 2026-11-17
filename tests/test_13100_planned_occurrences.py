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
import re
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


#: The ingest states an ``eventStatus`` write may carry (#13100 §8): ``pending``
#: when the record arrives, ``indexed`` when it reaches the index. A lifecycle
#: word here is the retired third case wearing the marker's name.
INGEST_EVENT_STATES = frozenset({"pending", "indexed"})

#: The Cypher half of the pin above ``_event_status_writes``:
#: ``e.eventStatus='pending'`` / ``eventStatus = 'indexed'`` inside a query
#: string. The AST readers are the structural half (kwargs and dict literals).
_EVENT_STATUS_LITERAL = re.compile(
    r"eventStatus[\"']?\s*[:=]\s*[\"']([A-Za-z_][\w-]*)[\"']")


def _event_status_writes(source: str) -> list[tuple[int, str]]:
    """``(lineno, value)`` for every LITERAL ``eventStatus`` write in ``source``.

    Three spellings, because the tree uses all three: a **kwarg**
    (``eventStatus="indexed"``), a **dict literal**
    (``{"eventStatus": "indexed"}``) and a **Cypher string**
    (``e.eventStatus='pending'``). The first two are read from the AST, so a
    fourth Python spelling cannot defeat the pin; the third is text inside a
    query, so the regex reads it. Results are de-duplicated, since a kwarg is
    matched by both readers.
    """
    import ast

    found: set[tuple[int, str]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (kw.arg == "eventStatus"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)):
                    found.add((kw.value.lineno, kw.value.value))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (isinstance(key, ast.Constant) and key.value == "eventStatus"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)):
                    found.add((value.lineno, value.value))
    for match in _EVENT_STATUS_LITERAL.finditer(source):
        found.add((source.count("\n", 0, match.start()) + 1, match.group(1)))
    return sorted(found)


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

    def test_a_create_after_a_settling_MINTS_a_new_plan_not_the_settled_one(
            self, sdk):
        """⛔ The reachable FAIL-OPEN of ``create_plan`` (#13100 review F1).

        A create NAME is the idempotent natural key (#452): ``create_entity``
        re-mentions the single LIVE holder. Before #3590's R5 rule reached the
        plan axis, a settled plan still counted as live, so
        ``settle_plan(..., "missed")`` followed by ``create_plan(<same name>)``
        returned the SETTLED plan's id while ``advance_stage`` answered
        ``applied=False`` (terminal guard) — a normal return, silently discarded:
        a caller told it had a scheduled upcoming plan had none, and the next
        ``record_occurrence`` refused it as "already settled".

        Pinned in the direction the model wants (§8): a new incarnation is a NEW
        record — a distinct id, born ``scheduled`` — and the settled plan keeps
        its ending (a later mention does not rewrite an earlier one).

        ⚠️ On the MERGED base (origin/main's #3590 R5 live-holder rule) this is a
        REGRESSION pin — it passes without the fix in ``create_plan``, because the
        identity layer no longer treats a settled plan as a live holder. It is
        kept because it fails on the base this review was written against
        (``c4b87a7f12``: same id, stage ``missed`` — measured), so the fail-open
        cannot come back unnoticed if that rule is ever narrowed again.
        """
        plan = PO.create_plan(sdk, "Weekly board review")
        PO.settle_plan(sdk, plan["id"], PO.STAGE_MISSED)

        again = PO.create_plan(sdk, "Weekly board review")
        assert again["id"] != plan["id"], (
            "the create re-mentioned the SETTLED plan; its returned id cannot be "
            "a staged, not-yet-happened plan")
        assert _stage(sdk, again["id"])["stageId"] == "scheduled"
        assert _stage(sdk, again["id"])["isClosed"] is False
        cur = _stage(sdk, plan["id"])
        assert cur["stageId"] == "missed"
        assert cur["isClosed"] is True

    def test_create_plan_re_mentioning_an_UPCOMING_plan_is_not_a_refusal(
            self, sdk):
        """The other half: the guard must not become a false block.

        When the name resolves to a plan that is ALREADY ``scheduled`` and open,
        the create's contract ("a scheduled, not-yet-happened plan exists under
        this name") is satisfied — the stale-``at`` re-mention moves nothing
        (``advance_stage`` answers ``stale-transition``; measured) and must not
        raise the "could not be put on 'scheduled'" refusal.
        """
        plan = PO.create_plan(sdk, "Standing sync")
        again = PO.create_plan(sdk, "Standing sync", at="2020-01-01T00:00:00Z")
        assert again["id"] == plan["id"]
        assert _stage(sdk, plan["id"])["stageId"] == "scheduled"

    def test_a_DRIFTED_plan_re_mentioned_by_create_names_the_real_cause(
            self, sdk):
        """An unapplied move must name the input that actually failed.

        The first version said "…a fresh plan carries none", which is false here:
        the plan carries a ``pipelineStageId`` that no longer resolves. The repair
        is the binding/stage id (the same one ``record_occurrence`` prescribes),
        never "a distinct name" — that would be a no-op.
        """
        plan = PO.create_plan(sdk, "Drifted plan")
        sdk.update_knowledge(plan["id"], pipelineStageId="no-such-stage")
        with pytest.raises(ValueError, match="not a stage of the") as exc:
            PO.create_plan(sdk, "Drifted plan")
        message = str(exc.value)
        assert "fresh plan carries none" not in message
        assert "distinct name" not in message

    def test_an_unknown_plan_reference_is_refused(self, sdk):
        with pytest.raises(ValueError, match="no plan Object"):
            PO.record_occurrence(sdk, "no-such-plan")

    def test_a_non_plan_reference_is_refused_BEFORE_any_write(self, sdk):
        """The refusal lands before the Event, so nothing is left behind.

        Without the kind check the ``:Event`` is written and JOURNALED (with its
        durable ``aboutObject`` edge) and the caller then gets a pipeline-binding
        error — an orphan occurrence behind a refusal that named the wrong
        problem.
        """
        goal = sdk.create_entity("object", "a goal", objectKind="core:goal")[
            "node"]
        with pytest.raises(ValueError, match="is not a plan"):
            PO.record_occurrence(sdk, goal["id"])
        assert sdk._get_proj().g.query(
            "MATCH (:Event)-[:aboutObject]->(:Object {id:$o}) RETURN count(*)",
            params={"o": goal["id"]}).result_set[0][0] == 0

    def test_the_BARE_plan_spelling_is_refused_before_any_write(self, sdk):
        """A bare `plan` kind is a plan to a READ and unbindable to a PIPELINE.

        Pipelines key on the namespaced kind (#2767), so accepting the bare form
        let the Event be written and journaled before the binding refusal — the
        refused-call-that-left-a-write defect, one spelling over.
        """
        bare = sdk.create_entity("object", "bare plan",
                                 objectKind="plan")["node"]
        with pytest.raises(ValueError, match="no pipeline can bind"):
            PO.record_occurrence(sdk, bare["id"])
        assert sdk._get_proj().g.query(
            "MATCH (:Event)-[:aboutObject]->(:Object {id:$o}) RETURN count(*)",
            params={"o": bare["id"]}).result_set[0][0] == 0

    def test_a_MARKER_CLOSED_plan_is_refused_before_any_write(self, sdk):
        """§6's two inputs again — and this one cannot be retried away.

        A marker-closed plan whose stage still reads `scheduled` used to reach
        the write, journal the Event, and then hit the pipeline's terminal guard:
        every retry added another Event, and the record answered NEITHER "it
        happened" nor "planned but never happened".
        """
        plan = PO.create_plan(sdk, "Closed by marker, scheduled stage")
        sdk.update_knowledge(plan["id"], isClosed=True)
        g = sdk._get_proj().g
        for _ in range(2):  # a retry must not mint a second Event
            with pytest.raises(ValueError, match="closed marker set"):
                PO.record_occurrence(sdk, plan["id"])
        assert g.query(
            "MATCH (:Event)-[:aboutObject]->(:Object {id:$p}) RETURN count(*)",
            params={"p": plan["id"]}).result_set[0][0] == 0
        with pytest.raises(ValueError, match="closed marker set"):
            PO.settle_plan(sdk, plan["id"], PO.STAGE_CANCELLED)

    def test_a_plan_ENDED_THROUGH_THE_GENERIC_DOOR_is_refused_before_any_write(
            self, sdk):
        """The writer resolves terminality from `pipelineStageId`, not the slot
        PROP — so the pre-check must ask the writer's question.

        Ending a plan with `update_knowledge(id, pipelineStageId='held')` leaves
        `pipelineSlot` stale/absent. A pre-check that read the prop thought the
        plan was still open, wrote and journaled the Event, and only then hit the
        writer's terminal guard — and every retry compounded another occurrence
        behind a refusal.
        """
        plan = PO.create_plan(sdk, "Ended via the generic door")
        g = sdk._get_proj().g
        g.query("MATCH (p:Object {id:$p}) SET p.pipelineStageId='held'",
                params={"p": plan["id"]})
        before = PO.planned_without_occurrence(g)
        assert [r["ended"] for r in before] == [True], before
        for _ in range(2):  # a retry must not mint a second Event
            with pytest.raises(ValueError, match="already settled"):
                PO.record_occurrence(sdk, plan["id"])
        with pytest.raises(ValueError, match="already settled"):
            PO.settle_plan(sdk, plan["id"], PO.STAGE_MISSED)
        assert g.query(
            "MATCH (:Event)-[:aboutObject]->(:Object {id:$p}) RETURN count(*)",
            params={"p": plan["id"]}).result_set[0][0] == 0

    def test_a_stage_BESIDE_A_CLEARED_OR_FOREIGN_KEY_is_unadvanceable(self, sdk):
        """The stored `pipelineStageKey` is part of the resolution.

        `pipeline.advance_stage` resolves the current stage only when
        `cur_key == pipeline_key`, so a stage id beside a cleared or foreign key is
        exactly as unadvanceable as an unknown id — and a pre-check that read the
        id alone wrote and journaled the Event first, with every retry compounding
        it.
        """
        g = sdk._get_proj().g
        for label, mutate in (
                ("cleared", "SET p.pipelineStageKey=null"),
                ("foreign", "SET p.pipelineStageKey='core:goal'"),
                # A NON-STRING key is foreign too — and must not raise: this
                # resolver runs on the promotion path after its CAS, and on the Q5
                # read where one bad row would fail every plan.
                ("non-string", "SET p.pipelineStageKey=5")):
            plan = PO.create_plan(sdk, f"key {label}")
            g.query(f"MATCH (p:Object {{id:$p}}) {mutate}",
                    params={"p": plan["id"]})
            for _ in range(2):
                with pytest.raises(ValueError, match="no stage move can be validated"):
                    PO.record_occurrence(sdk, plan["id"])
            assert g.query(
                "MATCH (:Event)-[:aboutObject]->(:Object {id:$p}) RETURN count(*)",
                params={"p": plan["id"]}).result_set[0][0] == 0, label

    def test_the_broken_binding_names_its_own_repair_and_is_performable(self, sdk):
        """NOT "already settled": the plan is open and one call repairs it.

        Folding the two preconditions into one sentence told an agent the plan had
        ended and sent it off to mint a duplicate; the advertised repair must be a
        call that works on this row.
        """
        plan = PO.create_plan(sdk, "Drifted key")
        g = sdk._get_proj().g
        g.query("MATCH (p:Object {id:$p}) SET p.pipelineStageKey=null",
                params={"p": plan["id"]})
        with pytest.raises(ValueError) as exc:
            PO.settle_plan(sdk, plan["id"], PO.STAGE_MISSED)
        message = str(exc.value)
        assert "already settled" not in message, message
        assert "update_knowledge" in message and "pipelineStageKey" in message
        # the named repair is performable, and the same call then succeeds
        sdk.update_knowledge(plan["id"], pipelineStageKey=PO.PLAN_PIPELINE_KEY)
        PO.settle_plan(sdk, plan["id"], PO.STAGE_MISSED)
        assert _stage(sdk, plan["id"])["stageId"] == "missed"

    def test_a_lying_SUBCLASS_key_cannot_slip_past_the_creation_guard(self, sdk):
        """The guard asks the RENDERER UNION, not a literal-name membership test.

        A `str` subclass key that lies about `__hash__`/`__eq__` is invisible to
        `k in merged` while the writer still files it under the name — measured
        at this guard: the position parked, the advance failed, and the refused
        create left the Object behind.
        """

        class _KeyHash(str):
            def __hash__(self):
                return 0

            def __eq__(self, other):
                return False

            __ne__ = __eq__

        class _KeyStr(str):
            def __str__(self):
                return "pipelineStageId"

        g = sdk._get_proj().g
        for label, key in (("hash", _KeyHash("pipelineStageId")),
                           ("renders", _KeyStr("harmless"))):
            with pytest.raises(ValueError, match="cannot be set on a plan at CREATION"):
                PO.create_plan(sdk, f"liar {label}", **{key: "held"})
            assert g.query("MATCH (o:Object {name:$n}) RETURN count(o)",
                           params={"n": f"liar {label}"}
                           ).result_set[0][0] == 0, label

    def test_an_UNKNOWN_STAGE_ID_names_the_stage_repair_not_the_key(self, sdk):
        """`unresolvable` has two causes and they need different repairs.

        Telling an agent to repair the KEY when the STAGE ID is the unknown one
        prescribes a no-op — and promising "still open" for a cleared key beside a
        terminal stage id is simply false.
        """
        g = sdk._get_proj().g
        typo = PO.create_plan(sdk, "stage typo")
        g.query("MATCH (p:Object {id:$p}) SET p.pipelineStageId='Held'",
                params={"p": typo["id"]})
        with pytest.raises(ValueError) as exc:
            PO.settle_plan(sdk, typo["id"], PO.STAGE_MISSED)
        message = str(exc.value)
        assert "pipelineStageId" in message and "declared stage id" in message
        assert "already settled" not in message
        # the NAMED repair must be performable, and must be the right one
        sdk.update_knowledge(typo["id"], pipelineStageId="scheduled")
        PO.settle_plan(sdk, typo["id"], PO.STAGE_MISSED)
        assert _stage(sdk, typo["id"])["stageId"] == "missed"

    def test_a_cleared_key_beside_a_TERMINAL_stage_says_ENDED(self, sdk):
        """The key arm must not report the stale `pipelineSlot` prop as "the stage
        itself", nor call an ended plan "still open".

        `pipelineStageId='held'` resolves TERMINAL; the slot PROP may still say
        `active`. Reporting the prop told the caller the plan was open, and the
        next call refused it as settled.
        """
        plan = PO.create_plan(sdk, "terminal then key cleared")
        sdk.update_knowledge(plan["id"], pipelineStageId="held")
        sdk.update_knowledge(plan["id"], pipelineStageKey=None)
        with pytest.raises(ValueError) as exc:
            PO.settle_plan(sdk, plan["id"], PO.STAGE_MISSED)
        message = str(exc.value)
        assert "resolves TERMINAL" in message, message
        assert "still open" not in message and "already settled" not in message
        assert "pipelineStageKey" in message

    def test_an_open_plan_with_a_cleared_key_says_open_and_the_repair_works(
            self, sdk):
        plan = PO.create_plan(sdk, "open then key cleared")
        sdk.update_knowledge(plan["id"], pipelineStageKey=None)
        with pytest.raises(ValueError) as exc:
            PO.record_occurrence(sdk, plan["id"])
        assert "still open" in str(exc.value)
        sdk.update_knowledge(plan["id"], pipelineStageKey=PO.PLAN_PIPELINE_KEY)
        assert PO.record_occurrence(sdk, plan["id"])["move"].applied is True

    def test_a_colliding_caller_prop_is_a_named_refusal(self, sdk):
        """`record_occurrence(aboutObject=…)` must not crash with a TypeError —
        and a key that only RENDERS as one of its kwargs must be refused too."""
        class _KeyHash(str):
            def __hash__(self):
                return 0

            def __eq__(self, other):
                return False

            __ne__ = __eq__

        plan = PO.create_plan(sdk, "collide")
        with pytest.raises(ValueError, match="set by `record_occurrence` itself"):
            PO.record_occurrence(sdk, plan["id"],
                                 **{_KeyHash("aboutObject"): "elsewhere"})
        with pytest.raises(ValueError, match="set by `record_occurrence` itself"):
            PO.record_occurrence(sdk, plan["id"], aboutObject="elsewhere")
        with pytest.raises(ValueError, match="set by `record_occurrence` itself"):
            PO.record_occurrence(sdk, plan["id"], eventKind="meeting")
        assert sdk._get_proj().g.query(
            "MATCH (:Event)-[:aboutObject]->(:Object {id:$p}) RETURN count(*)",
            params={"p": plan["id"]}).result_set[0][0] == 0

    def test_a_nested_props_map_cannot_park_a_position_at_creation(self, sdk):
        """`create_entity` flattens `props={...}`, so the guard must read the map
        the DOOR writes — not the kwargs shape."""
        g = sdk._get_proj().g
        for label, nested in (("stage", {"pipelineStageId": "held"}),
                              ("closed", {"isClosed": True})):
            with pytest.raises(ValueError, match="cannot be set on a plan at CREATION"):
                PO.create_plan(sdk, f"phantom {label}", props=nested)
            assert g.query("MATCH (o:Object {name:$n}) RETURN count(o)",
                           params={"n": f"phantom {label}"}
                           ).result_set[0][0] == 0, label

    def test_the_gate_reads_the_RESOLVED_end_of_a_plan(self, sdk):
        """R2's other direction: a plan that ENDED must not suppress for ever."""
        point = sdk.create_point("statement", "a live point")["id"]
        plan = PO.create_plan(sdk, "Ended via the generic door")
        op = f"op-ended-{plan['id']}"
        _operator_over(sdk, (point, plan["id"]), op)
        sdk._get_proj().g.query(
            "MATCH (p:Object {id:$p}) SET p.pipelineStageId='missed'",
            params={"p": plan["id"]})
        assert op in _promote(sdk, point), (
            "a plan that resolved to a terminal stage still suppressed promotion")

    def test_create_plan_refuses_a_position_in_its_caller_props(self, sdk):
        """A create cannot carry its own stage — and must not return a plan
        with NO position either, which is what parking one used to do."""
        with pytest.raises(ValueError, match="cannot be set on a plan at CREATION"):
            PO.create_plan(sdk, "positioned at birth", isClosed=True)
        with pytest.raises(ValueError, match="cannot be set on a plan at CREATION"):
            PO.create_plan(sdk, "positioned at birth", pipelineStageId="held")
        assert sdk._get_proj().g.query(
            "MATCH (o:Object) WHERE o.name = 'positioned at birth' "
            "RETURN count(o)").result_set[0][0] == 0

    def test_create_plan_cannot_be_mis_kinded_by_a_caller_prop(self, sdk):
        """The kind is the module's decision, not a `**props` override."""
        node = PO.create_plan(sdk, "sneaky", objectKind="core:goal")
        assert sdk._get_proj().g.query(
            "MATCH (o:Object {id:$o}) RETURN o.objectKind",
            params={"o": node["id"]}).result_set[0][0] == PO.PLAN_PIPELINE_KEY

    def test_a_BACKFILLED_occurrence_is_not_a_stale_transition(self, sdk):
        """The date an occurrence actually happened is a correction, not a stale
        observation — a plan is recorded now and backfilled for last March."""
        plan = PO.create_plan(sdk, "Backfilled")
        PO.record_occurrence(sdk, plan["id"], name="it happened last March",
                             at="2020-01-01T00:00:00+00:00")
        cur = _stage(sdk, plan["id"])
        assert cur["stageId"] == "held"
        assert cur["outcome"] == "completed"

    def test_a_BACKFILLED_missed_ending_applies_and_is_never_silent(self, sdk):
        """``settle_plan`` must not return a success-shaped UNapplied move.

        A no-op settlement leaves the plan reading as still upcoming to every
        reader — the answer this slice exists to get right.
        """
        plan = PO.create_plan(sdk, "Missed last March")
        PO.settle_plan(sdk, plan["id"], PO.STAGE_MISSED,
                       at="2020-01-01T00:00:00+00:00")
        cur = _stage(sdk, plan["id"])
        assert cur["stageId"] == "missed"
        assert cur["outcome"] == "lost"


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

    def test_the_clear_route_works_and_the_refusal_names_it(self, sdk):
        """R4: the advertised repair must be PERFORMABLE at the door it names.

        Only the UPDATE door removes a property; the record doors drop a null on
        the way to the fold, so a clear sent there writes nothing. The refusal
        text says which is which, and this pin holds both halves.
        """
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        eid = sdk.create_entity("event", "Legacy clear",
                                eventKind="deployment")["node"]["id"]
        g = sdk._get_proj().g
        g.query("MATCH (e:Event {id:$e}) SET e.status='superseded'",
                params={"e": eid})

        with pytest.raises(ValueError) as exc:
            sdk.update_knowledge(eid, status="live")
        assert "update_knowledge" in str(exc.value), (
            "the refusal must name the door that can actually clear the word")

        api = EventAPI(EventLog(os.path.join(sdk._test_root, "clear.jsonl")),
                       initiated_by="user", projection=sdk._get_proj())
        api.add_event(eid, "deployment", status=None)
        assert g.query("MATCH (e:Event {id:$e}) RETURN e.status",
                       params={"e": eid}).result_set[0][0] == "superseded", (
            "documented inertness: the record door cannot REMOVE a property")

        sdk.update_knowledge(eid, status=None)
        assert g.query("MATCH (e:Event {id:$e}) RETURN e.status",
                       params={"e": eid}).result_set[0][0] is None

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

    def test_a_plan_with_NO_position_does_not_suppress(self, sdk):
        """R2's other bound: an extractor-minted ``core:plan`` Object has no
        pipeline position and was promoted over before this change — inventing a
        "not yet" for it would block that operator for ever."""
        point = sdk.create_point("statement", "a live point")["id"]
        bare = sdk.create_entity("object", "extracted plan",
                                 objectKind="core:plan")["node"]
        op = f"op-bare-{bare['id']}"
        _operator_over(sdk, (point, bare["id"]), op)
        assert op in _promote(sdk, point)

    def test_a_marker_closed_plan_does_not_suppress(self, sdk):
        """An ending is durable through ``isClosed`` (§6), so a closed plan is a
        settled record even while its stage still reads non-terminal."""
        point = sdk.create_point("statement", "a live point")["id"]
        plan = PO.create_plan(sdk, "Closed but scheduled stage")
        op = f"op-closed-{plan['id']}"
        _operator_over(sdk, (point, plan["id"]), op)
        sdk.update_knowledge(plan["id"], isClosed=True)
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

    def test_the_durable_marker_ends_a_plan_too(self, sdk):
        """§6's two inputs: a CLOSED record is ended even with no stage.

        Reading the slot alone answered a marker-closed plan as "still upcoming"
        — the opposite of the claim this read makes.
        """
        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "Closed by marker")
        g.query("MATCH (p:Object {id:$p}) SET p.pipelineStageId=null, "
                "p.pipelineSlot=null, p.isClosed=true",
                params={"p": plan["id"]})
        rows = {r["name"]: r for r in PO.planned_without_occurrence(g)}
        assert rows["Closed by marker"]["ended"] is True

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
        """The ingest vocabulary is pinned at EVERY writer, in every spelling.

        ``eventStatus`` is ingest BOOKKEEPING (``pending`` -> ``indexed``); a
        lifecycle word in it is the #13100 defect wearing the marker's name —
        it would represent a not-yet-happened thing as an ``:Event``.

        ⛔ The pin reads the VALUE, not a spelling. Its first version matched a
        contiguous ``eventStatus=``/``eventStatus='`` and so could not see the
        dict-literal spelling ``"eventStatus": "indexed"`` — which is how five
        of the seven writers in the tree are written, so reverting one of them
        to ``completed`` left this pin GREEN (#13100 review F2).
        """
        import inspect

        from tortoise import hosted_api, sdk, session_indexer

        writes = []
        for module in (sdk, hosted_api, session_indexer):
            writes += [(module.__name__, line, value)
                       for line, value in _event_status_writes(
                           inspect.getsource(module))]

        bad = [(m, line, value) for m, line, value in writes
               if value not in INGEST_EVENT_STATES]
        assert not bad, (
            "a writer stamps a lifecycle-shaped ``eventStatus``: "
            f"{bad} — the ingest marker takes {sorted(INGEST_EVENT_STATES)} "
            "only; a not-yet-happened thing is a PLAN (#13100 §8)")

        # ⛔ NON-VACUITY (agent-infra#3442): a pin that enumerates nothing is
        # green for ever. The tree has SEVEN literal sites at this head; if the
        # readers stop seeing them, a spelling changed and the pin must be
        # taught it — not silently pass.
        assert len(writes) >= 7, (
            "the pin enumerated only "
            f"{len(writes)} eventStatus writer(s) — fewer than the 7 in-tree "
            "literal sites, so a spelling escaped the readers; extend "
            "_event_status_writes")
