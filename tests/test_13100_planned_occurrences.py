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
* :class:`TestAMentionIsNotAnOccurrence` (#13128) — the ``aboutObject`` link is
  the occurrence ONLY for an Event of the occurrence kind: a note *about* a plan
  is counted (``mentions``) and never read as the plan happening, and
  ``record_occurrence`` refuses to mint the ambiguous shape.
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
from typing import ClassVar

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


def _pos(sdk, plan_id):
    """The WRITER's position answer for a stored plan (``current_stage`` keys
    are exactly ``plan_position``'s inputs — ``test_13100`` review verified the
    mapping, so the tests cannot read a different question than the gate)."""
    cur = _stage(sdk, plan_id) or {}
    return PO.plan_position(
        cur.get("stageId"), outcome=cur.get("outcome"),
        is_closed=bool(cur.get("isClosed")),
        pipeline_key=cur.get("pipelineKey"))


#: The ingest states an ``eventStatus`` write may carry (#13100 §8): ``pending``
#: when the record arrives, ``indexed`` when it reaches the index. A lifecycle
#: word here is the retired third case wearing the marker's name.
INGEST_EVENT_STATES = frozenset({"pending", "indexed"})

#: The ingest-state writers this pin SCANS, declared rather than assumed: the
#: test below separately PROVES that no other ``tortoise/`` module carries a
#: write-shaped ``eventStatus`` literal, so a new writer module reds this file
#: instead of slipping into the blind spot its first version had (review round
#: 2, P2: a fourth module's lifecycle write stayed GREEN because the tuple was
#: hardcoded and the ``>= 7`` floor counted only the modules already scanned).
EVENT_STATUS_WRITER_MODULES = ("tortoise.sdk", "tortoise.hosted_api",
                               "tortoise.session_indexer")

#: The Cypher half of the pin: ``e.eventStatus='pending'`` /
#: ``eventStatus = 'indexed'`` inside a query string. Read from STRING LITERALS
#: in the AST (never from the raw source), so a docstring or a comment that
#: merely spells the word is not indexed as a write.
_EVENT_STATUS_LITERAL = re.compile(
    r"eventStatus[\"']?\s*[:=]\s*[\"']([A-Za-z_][\w-]*)[\"']")


def _docstring_nodes(tree) -> set[int]:
    """The ``id()`` of every module/class/function docstring node in ``tree``.

    Prose is not a write: without this, a docstring that says *"never write
    ``eventStatus='completed'``"* would be enumerated as a writer and the pin
    would have to be taught about text that writes nothing.
    """
    import ast

    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            ids.add(id(body[0].value))
    return ids


def _write_line(lines: list[str], node, value: str) -> int:
    """The line the WRITE is on, read from the node's own RAW source span.

    ⛔ ``node.lineno`` is where the string OPENED, not where the write is. An
    implicitly concatenated query (the shape ``hosted_api.py`` uses) is folded by
    ``ast`` into ONE ``Constant`` carrying the first fragment's line and NO
    newlines in ``node.value``, so ``node.lineno + value[:i].count("\\n")``
    answers the opening line and silently drifts from the site it names (#13100
    review round 2, P2: measured ``(22505, 'pending')`` for a write on
    ``:22509``). The raw source is the only place that offset survives.
    """
    end = getattr(node, "end_lineno", None) or node.lineno
    for lineno in range(node.lineno, end + 1):
        text = lines[lineno - 1] if lineno <= len(lines) else ""
        for match in _EVENT_STATUS_LITERAL.finditer(text):
            if match.group(1) == value:
                return lineno
    return node.lineno


def _event_status_writes(source: str) -> list[tuple[int, str]]:
    """``(lineno, value)`` for every LITERAL ``eventStatus`` write in ``source``.

    Three spellings, because the tree uses all three: a **kwarg**
    (``eventStatus="indexed"``), a **dict literal**
    (``{"eventStatus": "indexed"}``) and a **Cypher string**
    (``e.eventStatus='pending'``). The first two are structural AST nodes, so a
    fourth Python spelling cannot defeat the pin; the third is text inside a
    query string, so the regex reads the AST's string CONSTANTS (docstrings
    excluded). Results are de-duplicated.
    """
    import ast

    tree = ast.parse(source)
    lines = source.splitlines()
    docstrings = _docstring_nodes(tree)
    found: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
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
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            for match in _EVENT_STATUS_LITERAL.finditer(node.value):
                found.add((_write_line(lines, node, match.group(1)),
                           match.group(1)))
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

    def test_a_cleared_key_beside_a_TERMINAL_stage_reads_ENDED(self, sdk):
        """`ended` follows the RESOLVED stage, never the key.

        A terminal stage beside a cleared key has ended, and EVERY reader must say
        so: Q5 must not answer "upcoming" for exactly the shape an operator has to
        see (an ended plan with no linked Event), and the writers refuse it as
        settled rather than as a repairable binding.
        """
        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "terminal then key cleared")
        sdk.update_knowledge(plan["id"], pipelineStageId="held")
        sdk.update_knowledge(plan["id"], pipelineStageKey=None)
        rows = {r["name"]: r for r in PO.planned_without_occurrence(g)}
        assert rows["terminal then key cleared"]["ended"] is True
        assert rows["terminal then key cleared"]["outcome"] == "completed"
        with pytest.raises(ValueError, match="already settled"):
            PO.settle_plan(sdk, plan["id"], PO.STAGE_MISSED)

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

    def test_create_plan_cannot_RETAG_another_kinds_record(self, sdk):
        """`create_entity("object", name)` is the documented re-mention (#452).

        Without a pre-flight, `create_plan` over an existing `core:goal` Object
        RETAGGED it into `core:plan` and drove its stage props — the kind clobber
        this module's own writers refuse, reached one door over.
        """
        g = sdk._get_proj().g
        goal = sdk.create_entity("object", "Retire the legacy queue",
                                 objectKind="core:goal",
                                 description="an operator's goal record")["node"]
        with pytest.raises(ValueError, match="would have RETAGGED"):
            PO.create_plan(sdk, "Retire the legacy queue")
        assert g.query(
            "MATCH (o:Object {id:$o}) RETURN o.objectKind, o.description, "
            "o.pipelineStageId, o.pipelineStageKey",
            params={"o": goal["id"]}).result_set == [
                ["core:goal", "an operator's goal record", None, None]], (
            "the existing record must be left untouched")

    def test_create_plan_refuses_a_LIVE_holder_that_has_ENDED(self, sdk):
        """A live holder whose stage is terminal cannot be re-created.

        (A CLOSED holder is not reachable here: the identity resolver resolves
        LIVE holders only, so a closed plan's name is free — same-name coexistence
        is legal, #3590 D2. This pins the shape the resolver DOES return.)
        """
        plan = PO.create_plan(sdk, "Ended but still live")
        sdk.update_knowledge(plan["id"], pipelineStageId="held")
        with pytest.raises(ValueError, match="already exists and has ENDED"):
            PO.create_plan(sdk, "Ended but still live")

    def test_create_plan_refuses_an_ENDED_holder_even_with_a_broken_key(
            self, sdk):
        """`ended` is checked FIRST — an ended plan with a cleared key is still
        ENDED, and the binding message would call it "still open"."""
        plan = PO.create_plan(sdk, "Ended and key-broken, still live")
        sdk.update_knowledge(plan["id"], pipelineStageId="held")
        sdk.update_knowledge(plan["id"], pipelineStageKey=None)
        with pytest.raises(ValueError) as exc:
            PO.create_plan(sdk, "Ended and key-broken, still live")
        message = str(exc.value)
        assert "already exists and has ENDED" in message, message
        assert "still open" not in message

    def test_a_CLOSED_plans_name_is_free_for_a_new_plan(self, sdk):
        """#3590 D2: same-name coexistence is legal, and a closed record is not a
        live holder — so the next plan of that name is a NEW record, not a retag."""
        first = PO.create_plan(sdk, "Quarterly review")
        PO.record_occurrence(sdk, first["id"], name="Q1 review held")
        second = PO.create_plan(sdk, "Quarterly review")
        assert second["id"] != first["id"]
        assert _stage(sdk, second["id"])["stageId"] == "scheduled"
        assert _stage(sdk, first["id"])["stageId"] == "held"

    def test_create_plan_over_a_LIVE_plan_is_the_documented_re_mention(self, sdk):
        plan = PO.create_plan(sdk, "Same plan, mentioned twice")
        again = PO.create_plan(sdk, "Same plan, mentioned twice")
        assert again["id"] == plan["id"]
        assert _stage(sdk, plan["id"])["stageId"] == "scheduled"

    def test_a_re_mention_returns_the_SAME_node_shape(self, sdk):
        """Both arms return `create_entity`'s node payload, so a caller reading
        `title` works on either — the narrower dict KeyError'd only on this arm."""
        created = PO.create_plan(sdk, "shape parity", title="A title")
        again = PO.create_plan(sdk, "shape parity")
        assert again["id"] == created["id"]
        assert again.get("title") == "A title"

    def test_create_plan_over_a_POSITIONLESS_plan_STAGES_it(self, sdk):
        """A plan with no stage is not a plan the caller was promised.

        ⛔ The re-mention arm returned the record unchanged and said "its
        position is already its own" — false for the extractor-minted
        ``core:plan`` Object, which carries NO position. `has_position` is
        exactly the input the promotion gate is BOUNDED on (a position-less plan
        suppresses nothing, deliberately), so the caller was told it had created
        the shape that keeps a draft operator down while the suppression was
        silently OFF (#13100 review round 3, F1(b)). The sibling test
        (`test_create_plan_STAGES_a_positionless_live_holder`) pins both
        spellings; THIS one pins the two consequences the fix is FOR — the
        suppression is actually in force afterwards, and the STORED prop moved.
        """
        point = sdk.create_point("statement", "a live point")["id"]
        bare = sdk.create_entity("object", "extractor plan",
                                 objectKind=PO.PLAN_PIPELINE_KEY)["node"]
        assert PO.plan_position(_stage(sdk, bare["id"])["stageId"])[
            "has_position"] is False
        staged = PO.create_plan(sdk, "extractor plan")
        assert staged["id"] == bare["id"], (
            "the create must re-mention the existing record, not mint a twin")
        assert _stage(sdk, bare["id"])["stageId"] == PO.STAGE_SCHEDULED
        # The consequence the reviewer named: the suppression the create's
        # contract implies must actually be in force afterwards.
        op = f"op-positionless-{bare['id']}"
        _operator_over(sdk, (point, bare["id"]), op)
        assert op not in _promote(sdk, point), (
            "the create returned a plan whose position it had just staged, but "
            "the operator over it was still promoted")
        # ⛔ NOT `planned_without_occurrence(...)[0]["stage"] == "scheduled"`:
        # that read is `pos["stageId"] or STAGE_SCHEDULED`, so a POSITION-LESS
        # record reports `scheduled` by default and the assertion held with the
        # fix reverted (#13100 review round 4, F3 — measured identical both
        # ways). The STORED prop is the discriminating input.
        assert sdk._get_proj().g.query(
            "MATCH (o:Object {id:$o}) RETURN o.pipelineStageId",
            params={"o": bare["id"]}).result_set[0][0] == PO.STAGE_SCHEDULED

    def test_a_POSITIONED_bare_spelling_holder_is_BOUND_by_the_create(self, sdk):
        """The create door must never hand back a plan no pipeline can bind.

        ``is_plan_kind`` accepts both spellings; every writer's ``_resolve_plan``
        requires the NAMESPACED one (a pipeline keys on it, #2767). A positioned
        bare-kind record short-circuited the re-mention arm and was returned as a
        SUCCESS, so the caller's very next write refused with "…which no pipeline
        can bind … create it with `create_plan`" — which returned this same
        record again: a refusal loop with no exit (#13100 review round 3,
        F1(a)). The sibling test above covers the POSITION-LESS bare spelling
        (staged on the way through); this one is the positioned one, where the
        create must still repair the binding rather than report success.
        """
        g = sdk._get_proj().g
        bare = sdk.create_entity("object", "bare but staged",
                                 objectKind="plan")["node"]
        # A BOUND but bare-kind plan: a declared stage id AND the pipeline key, so
        # the position resolves and the record is not refused as unresolvable
        # (that refusal is a different input and has its own arms above).
        sdk.update_knowledge(bare["id"], pipelineStageId=PO.STAGE_SCHEDULED,
                             pipelineStageKey=PO.PLAN_PIPELINE_KEY)
        created = PO.create_plan(sdk, "bare but staged")
        assert created["id"] == bare["id"]
        assert g.query(
            "MATCH (o:Object {id:$o}) RETURN o.objectKind",
            params={"o": bare["id"]}).result_set[0][0] == PO.PLAN_PIPELINE_KEY, (
            "the create returned a plan the writers cannot bind")
        # The contract that a refusal loop broke: the returned plan is settlable.
        assert PO.record_occurrence(sdk, bare["id"])["move"].applied

    def test_a_SETTLED_plan_is_addressable_BY_NAME(self, sdk):
        """The name coordinate must reach the settled plan the writers settle.

        ``resolve_entity_id``'s NAME arm applies the currency predicate by
        default, and since #13034 that predicate reads the position axis — so a
        plan this module had just settled (``record_occurrence`` / ``settle_plan``
        leave ``isClosed=True``) was unresolvable BY NAME. Every crafted
        "already settled" refusal was therefore unreachable on the coordinate the
        API advertises, and the one that fired instead advised ``create_plan``,
        which resolves LIVE holders only — so following it minted a SECOND plan
        under the same name (#13100 review round 3, F2). ``live_only=False`` is
        the same mode ``pipeline._resolve_object`` takes, for the same reason.
        """
        plan = PO.create_plan(sdk, "Weekly board review")
        PO.record_occurrence(sdk, plan["id"])
        assert _stage(sdk, plan["id"])["isClosed"] is True
        with pytest.raises(ValueError, match="already settled") as exc:
            PO.record_occurrence(sdk, "Weekly board review")
        message = str(exc.value)
        assert "no plan Object" not in message
        # ⛔ A count(:Object) assertion here would be VACUOUS — nothing in this
        # test mints, so it holds under every implementation (#13100 review round
        # 5, same class as R4 F3). The discriminating inputs are the words: the
        # refusal must name the settled stage, and must not prescribe the create
        # that minted a duplicate.
        assert PO.STAGE_HELD in message, message
        assert "create the plan first" not in message, (
            "the refusal prescribed the very create that mints a twin")
        with pytest.raises(ValueError, match="already settled"):
            PO.settle_plan(sdk, "Weekly board review", PO.STAGE_CANCELLED)

    def test_a_NON_STRING_status_is_foreign_not_a_crash(self, sdk):
        """The currency predicate cannot hash an arbitrary stored value.

        `update_knowledge(id, status=[…])` is ACCEPTED and stored verbatim, and
        `is_terminal_status` does membership, so the gate used to raise a bare
        `TypeError: unhashable type: 'list'` — an unnamed 500 with no remedy on a
        plan the rest of the system reads as LIVE (no list equals a status word
        in the Cypher arm). The shape is normalised to "no word", so the write
        proceeds exactly as it did before the status gate existed.
        """
        plan = PO.create_plan(sdk, "Listy")
        sdk._get_proj().g.query(
            "MATCH (o:Object {id:$o}) SET o.status = ['archived']",
            params={"o": plan["id"]})
        assert PO.record_occurrence(sdk, plan["id"])["move"].applied

    def test_a_NON_STRING_status_does_not_hide_the_OUTDATED_flag(self, sdk):
        """Normalising the word must not normalise the FLAG with it.

        The two are separate sources of the same answer; a gate that read
        `outdated` only on the string branch would let the normalisation silence
        a legacy ending beside a foreign status (#13100 review round 6).
        """
        plan = PO.create_plan(sdk, "Listy and outdated")
        sdk._get_proj().g.query(
            "MATCH (o:Object {id:$o}) SET o.status = ['archived'], "
            "o.outdated = true", params={"o": plan["id"]})
        with pytest.raises(ValueError, match="outdated marker set"):
            PO.record_occurrence(sdk, plan["id"])

    def test_an_AMBIGUOUS_plan_name_still_leaves_its_R8_record(self, sdk):
        """The speculative probe must not suppress the record it cannot retry.

        `record_misses=False` exists so a successful WIDENED resolve leaves no
        phantom `no-live-holder` entry (#12047 N6/N1) — but on an ambiguous name
        the live arm raises before the widened call runs, so suppressing there
        dropped the only evidence of the ambiguity (measured: 0 entries vs 1 for
        the same lookup at the default).
        """
        from tortoise.entity_identity import AmbiguousEntityName, non_folded_entries

        g = sdk._get_proj().g
        # Two HOLDERS, not two creates: `create_entity` re-mentions the single
        # live holder (#452), so a second create cannot mint a same-name twin.
        for oid in ("twin-plan-1", "twin-plan-2"):
            g.query(
                "CREATE (o:Object {id:$id, name:'Twin plan', objectKind:$k})",
                params={"id": oid, "k": PO.PLAN_PIPELINE_KEY})
        before = len(non_folded_entries())
        with pytest.raises(AmbiguousEntityName):
            PO.record_occurrence(sdk, "Twin plan")
        assert len(non_folded_entries()) == before + 1, (
            "the ambiguity must leave exactly ONE R8 record")

    def test_a_DUPLICATE_ID_plan_coordinate_is_recorded_ONCE(self, sdk):
        """The duplicate-id refusal records even under `record_misses=False`.

        `resolve_entity_id`'s duplicate-id arm calls `_refuse` unconditionally, so
        a fix that restored the missing evidence by RE-INVOKING the resolver
        filed the same entry twice (measured: 2 entries, both
        `shape='duplicate-id'`) — inflating the refused-event set the R9
        invariant asserts on. The gate now records from the caught exception and
        only when the probe left nothing behind (#13100 review round 6).
        """
        from tortoise.entity_identity import AmbiguousEntityName, non_folded_entries

        g = sdk._get_proj().g
        for _ in range(2):
            g.query(
                "CREATE (o:Object {id:'dup-plan', name:'Duplicate id plan', "
                "objectKind:$k})", params={"k": PO.PLAN_PIPELINE_KEY})
        before = len(non_folded_entries())
        with pytest.raises(AmbiguousEntityName):
            PO.record_occurrence(sdk, "dup-plan")
        assert len(non_folded_entries()) == before + 1, (
            "the duplicate-id refusal was recorded more than once")
        # ⛔ The COUNT alone would pass if the gate filed one entry of the WRONG
        # shape: the property the fix rests on is that the resolver's OWN
        # `duplicate-id` record survives, not that some record exists.
        entry = non_folded_entries()[-1]
        assert entry.shape == "duplicate-id", entry
        assert entry.name == "dup-plan", entry

    def test_the_R8_guard_ignores_ANOTHER_refusals_entry(self, sdk, monkeypatch):
        """The guard must identify its OWN entry, not merely notice that the
        process-global record grew.

        ⛔ The R8 record is shared by every thread (`_non_folded_lock` exists for
        that reason), so a COUNT-based guard skips this coordinate's record
        whenever another refusal lands in the window — losing exactly the
        evidence the arm exists to keep. This test makes that window
        deterministic: the resolver files an unrelated entry and then raises, so
        a count guard records NOTHING for our coordinate and a coordinate guard
        records it once (#13100 review round 8).
        """
        from tortoise import entity_identity
        from tortoise.entity_identity import (
            AmbiguousEntityName,
            non_folded_entries,
            record_non_folded,
        )

        real = entity_identity.resolve_entity_id_same_entity

        def noisy_resolver(graph, label, value, **kwargs):
            if value == "Concurrent plan":
                record_non_folded("Object", "someone-else", ["other-id"])
                raise AmbiguousEntityName("Object", value, ["c1", "c2"])
            return real(graph, label, value, **kwargs)

        monkeypatch.setattr(entity_identity, "resolve_entity_id_same_entity",
                            noisy_resolver)
        before = len(non_folded_entries())
        with pytest.raises(AmbiguousEntityName):
            PO.record_occurrence(sdk, "Concurrent plan")
        mine = [e for e in non_folded_entries()[before:]
                if e.name == "Concurrent plan"]
        assert len(mine) == 1, (
            "the guard treated another refusal's entry as its own and dropped "
            "this coordinate's record")

    def test_a_name_held_by_a_SETTLED_and_a_LIVE_plan_resolves_the_LIVE_one(
            self, sdk):
        """Widening the name arm must not over-block the shape this module mints.

        `create_plan`'s own pre-flight resolves LIVE holders only, so after a
        settling, `create_plan(<same name>)` mints a live successor and the name
        is held by one settled plan AND one open one (legal, #3590 D2).
        `resolve_entity_id`'s documented rule is "exactly one LIVE holder -> its
        id", so that coordinate must keep resolving the OPEN plan: an
        unconditional `live_only=False` saw two holders and refused as ambiguous,
        turning a by-name call that worked into a permanent refusal with only ids
        as an exit (#13100 review round 4, F2).
        """
        first = PO.create_plan(sdk, "Quarterly review")
        PO.settle_plan(sdk, first["id"], PO.STAGE_MISSED)
        second = PO.create_plan(sdk, "Quarterly review")
        assert second["id"] != first["id"]
        PO.record_occurrence(sdk, "Quarterly review")
        assert _stage(sdk, second["id"])["stageId"] == PO.STAGE_HELD
        assert _stage(sdk, first["id"])["stageId"] == PO.STAGE_MISSED, (
            "the settled predecessor must not be the record the write landed on")

    def test_two_SETTLED_holders_of_one_name_still_refuse(self, sdk):
        """With nothing live, ambiguity is still an answer — never a silent pick."""
        from tortoise.entity_identity import AmbiguousEntityName

        a = PO.create_plan(sdk, "Duplicated ending")
        PO.settle_plan(sdk, a["id"], PO.STAGE_MISSED)
        b = PO.create_plan(sdk, "Duplicated ending")
        PO.settle_plan(sdk, b["id"], PO.STAGE_CANCELLED)
        with pytest.raises(AmbiguousEntityName):
            PO.record_occurrence(sdk, "Duplicated ending")

    def test_a_STATUS_RETIRED_plan_is_refused_before_any_write(self, sdk):
        """The name arm drops the STATUS axis, so it is gated explicitly.

        `live_only=False` disables the whole currency predicate — the lifecycle
        word and the `outdated` flag as well as the position pair — while
        `plan_position` reads only the position axis. Without this gate a plan
        retired with a status word resolved, reported `ended: False`, and was
        WRITTEN to: a durable, journaled settlement of a record the currency rule
        calls ended (#13100 review round 4, F1 — measured).
        """
        plan = PO.create_plan(sdk, "Retired plan")
        sdk.update_knowledge(plan["id"], status="archived")
        events = ("MATCH (:Event)-[:aboutObject]->(:Object {id:$o}) "
                  "RETURN count(*)")
        with pytest.raises(ValueError, match="already settled") as exc:
            PO.record_occurrence(sdk, "Retired plan")
        # ⛔ PIN THE WORD THE ROW ACTUALLY HOLDS, not the word the caller passed.
        # `#11824`/`#13143` S0 put a synonym fold on the one lifecycle door
        # (`archived` -> `deprecated`: the store gets the CANONICAL word and the
        # caller gets no receipt), so after S0's merge the row holds `deprecated`
        # while `archived` is still an ENDED word to this PR's own gate. Reading the
        # value back keeps the assertion discriminating under BOTH models; deleting
        # it would leave the gate pinned only by `match="already settled"`, which
        # every status ending shares.
        assert repr(sdk._get_proj().g.query(
            "MATCH (o:Object {id:$o}) RETURN o.status",
            params={"o": plan["id"]}).result_set[0][0]) in str(exc.value)
        with pytest.raises(ValueError, match="already settled"):
            PO.settle_plan(sdk, "Retired plan", PO.STAGE_CANCELLED)
        assert sdk._get_proj().g.query(
            events, params={"o": plan["id"]}).result_set[0][0] == 0, (
            "the refused call left an :Event behind")
        assert _stage(sdk, plan["id"])["stageId"] == PO.STAGE_SCHEDULED, (
            "the refused call moved the retired plan's stage")

    def test_the_LEGACY_outdated_flag_is_the_same_gate(self, sdk):
        """The other half of the status axis — `outdated` is not a vocabulary.

        Written straight to the store: `outdated` is server-managed, no in-repo
        `update_knowledge` accepts it (measured — the door refuses it), which is
        exactly why the gate must read it as an INPUT rather than trust that the
        writers keep it in step.
        """
        plan = PO.create_plan(sdk, "Outdated plan")
        sdk._get_proj().g.query(
            "MATCH (o:Object {id:$o}) SET o.outdated = true",
            params={"o": plan["id"]})
        with pytest.raises(ValueError, match="outdated marker set"):
            PO.record_occurrence(sdk, plan["id"])

    def test_an_unresolvable_stage_never_reports_a_stale_outcome(self, sdk):
        """`outcome` is the STAGE's outcome; with no resolution it is None, never
        the stored `pipelineOutcome` prop (which would pair `ended: False` with
        `outcome: 'lost'`)."""
        pos = PO.plan_position("no-such-stage", outcome="lost",
                               pipeline_key=PO.PLAN_PIPELINE_KEY)
        assert pos["unresolvable"] is True
        assert pos["ended"] is False
        assert pos["outcome"] is None

    def test_a_POSITIONLESS_plan_never_reports_a_stale_outcome(self, sdk):
        """`outcome` is the STAGE's outcome — no-position is not an exception.

        `unresolvable` is gated on `bool(stage_id)`, so a plan with NO position is
        NOT unresolvable, and the earlier `else (None if unresolvable else
        outcome)` fallback reported the stored `pipelineOutcome` for that shape:
        `ended: False` beside `outcome: 'lost'`, the same mixed-cache pair the
        sibling test removes one input over (#13100 review round 13, P2). Pinned
        through BOTH doors — the pure helper and the Q5 read a caller consumes.
        """
        pos = PO.plan_position(None, outcome="lost")
        assert pos["has_position"] is False
        assert pos["unresolvable"] is False
        assert pos["ended"] is False
        assert pos["outcome"] is None, (
            "a position-less plan reported the STALE pipelineOutcome prop")
        # The durable `isClosed` marker answers `ended` on its own, so the stored
        # word is still not this field's answer.
        closed = PO.plan_position(None, outcome="lost", is_closed=True)
        assert closed["ended"] is True
        assert closed["outcome"] is None

        # The door a caller actually reads (Q5), not just the helper.
        bare = sdk.create_entity("object", "positionless outcome",
                                 objectKind=PO.PLAN_PIPELINE_KEY)["node"]
        sdk.update_knowledge(bare["id"], pipelineOutcome="lost")
        rows = {r["name"]: r for r in
                PO.planned_without_occurrence(sdk._get_proj().g)}
        row = rows["positionless outcome"]
        assert row["ended"] is False
        assert row["outcome"] is None, (
            f"the Q5 read reported the stale stored pipelineOutcome: {row!r}")

    def test_create_plan_STAGES_a_positionless_live_holder(self, sdk):
        """A live plan holder with NO position is not a successful re-mention.

        Short-circuiting on it told the caller a plan was on the books when
        nothing was staged: an operator over it was then PROMOTED (a positionless
        plan does not suppress, by design), and a bare `plan` spelling could never
        be bound at all — so the "create it with `create_plan`" remedy stayed
        refused after being issued. Both spellings are the SAME kind, so
        re-mentioning and staging it is not a kind clobber.
        """
        g = sdk._get_proj().g
        for label, kind in (("core", "core:plan"), ("bare", "plan")):
            holder = sdk.create_entity("object", f"positionless {label}",
                                       objectKind=kind)["node"]
            created = PO.create_plan(sdk, f"positionless {label}")
            assert created["id"] == holder["id"], label
            assert _stage(sdk, holder["id"])["stageId"] == "scheduled", label
            assert g.query("MATCH (o:Object {id:$o}) RETURN o.objectKind",
                           params={"o": holder["id"]}).result_set[0][0] == PO.PLAN_PIPELINE_KEY
            assert PO.record_occurrence(sdk, holder["id"])["move"].applied, label

    def test_the_non_plan_refusal_names_a_remedy_that_works(self, sdk):
        """`record_occurrence` settles a PLAN, so naming it for a non-plan record
        points the caller at a call that refuses in turn."""
        sdk.create_entity("object", "a goal record", objectKind="core:goal")
        with pytest.raises(ValueError) as exc:
            PO.create_plan(sdk, "a goal record")
        message = str(exc.value)
        assert "create_entity(type='event'" in message, message
        assert "or use `record_occurrence`" not in message

    def test_a_RETIRED_scheduled_stage_is_NOT_reported_as_settled(self, sdk):
        """A record with NO position is not a settled record.

        `advance_stage` resolves through the graph's `effective_pipeline` while
        `plan_position` (and so this read) resolves against the DECLARED one, so a
        graph override that RETIRES `scheduled` makes the move `unknown-stage` on
        a FRESH create. The defensive arm used to answer "already at None … a
        settled plan is a closed record, give it a distinct name" — false about
        the graph, and its advice would mint a second unstaged plan (#13100 review
        round 10).
        """
        import copy

        decl = copy.deepcopy(PO.PLAN_PIPELINE_DECL)
        decl["stages"].append({"id": "review", "label": "Review",
                               "order": 4, "slot": "active"})
        g = sdk._get_proj().g
        pipeline.set_default_stage(g, PO.PLAN_PIPELINE_KEY, "review",
                                   declared=decl)
        pipeline.retire_stage(g, PO.PLAN_PIPELINE_KEY, PO.STAGE_SCHEDULED,
                              declared=decl)
        with pytest.raises(ValueError) as exc:
            PO.create_plan(sdk, "Unstageable")
        message = str(exc.value)
        assert "could not be put on" in message, message
        assert "NOT settled" in message, message
        assert "already at" not in message, message
        assert "distinct name" not in message, message
        # ⛔ The SAME branch is reachable over a PRE-EXISTING position-less holder
        # (the fall-through re-mentions it), where nothing is created — so the
        # message must not claim a create (#13100 review round 11).
        sdk.create_entity("object", "extractor plan",
                          objectKind=PO.PLAN_PIPELINE_KEY)
        with pytest.raises(ValueError) as pre_existing:
            PO.create_plan(sdk, "extractor plan")
        pre_message = str(pre_existing.value)
        assert "resolves to" in pre_message, pre_message
        assert "was created" not in pre_message, pre_message

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

    def test_a_BROKEN_KEY_does_not_turn_the_suppression_off(self, sdk):
        """R2's first clause, and the reason the discriminant is the resolved SLOT.

        `unresolvable` was used as a fail-open blanket, so one supported write on
        the plan (`update_knowledge(id, pipelineStageKey=None)`) put a *mid-pipeline*
        plan's operator back to `live` — over an occurrence that demonstrably has
        not happened.
        """
        point = sdk.create_point("statement", "a live point")["id"]
        plan = PO.create_plan(sdk, "Scheduled, key cleared")
        op = f"op-brokenkey-{plan['id']}"
        _operator_over(sdk, (point, plan["id"]), op)
        sdk.update_knowledge(plan["id"], pipelineStageKey=None)
        assert op not in _promote(sdk, point), (
            "a broken stage key switched the suppression off for a mid-pipeline plan")

    def test_an_UNRESOLVABLE_STAGE_ID_does_not_turn_the_suppression_off(
            self, sdk):
        """R2 fails CLOSED on BOTH unresolvable axes, not only the key one.

        An unknown stage id is the SAME reachability as a cleared key: one
        supported, customer-callable write —
        `update_knowledge(id, pipelineStageId="Held")` — and the gate used to
        read the unresolvable slot as "no evidence the plan is mid-pipeline",
        promoting the operator over an occurrence that had not happened.

        ⛔ For THIS axis that was also PERMANENT: `record_occurrence`,
        `settle_plan` and `pipeline.advance_stage` refuse every move on such a
        plan, so it could never reach a terminal stage and the suppression could
        never be earned back. It is recoverable only through the stage-id repair
        (`_unresolvable_message`), which the next test drives.

        The Q5 read must answer the same row the same way (`ended: False`) —
        the two readers of one plan cannot disagree about whether it happened.
        """
        point = sdk.create_point("statement", "a live point")["id"]
        plan = PO.create_plan(sdk, "Unknown stage id")
        op = f"op-badstage-{plan['id']}"
        _operator_over(sdk, (point, plan["id"]), op)
        sdk.update_knowledge(plan["id"], pipelineStageId="Held")

        pos = _pos(sdk, plan["id"])
        assert pos["unresolvable"] is True and pos["ended"] is False, (
            "precondition: the stage id does not resolve and the plan has "
            "not ended")
        q5 = {row["id"]: row for row in
              PO.planned_without_occurrence(sdk._get_proj().g)}
        assert q5[plan["id"]]["ended"] is False, (
            "the Q5 read says this plan has not ended, so the gate cannot "
            "promote as if it had")

        assert op not in _promote(sdk, point), (
            "an unresolvable stage id switched the suppression off for an "
            "occurrence that can never be recorded as having happened")

    def test_the_STAGE_ID_repair_route_lifts_the_suppression(self, sdk):
        """Fail-closed here is not a dead end — the remedy the module's own
        refusal names (`update_knowledge(id, pipelineStageId=<declared id>)`)
        puts the plan back on a stage the writers can move, after which the
        ordinary ending lifts the suppression."""
        point = sdk.create_point("statement", "a live point")["id"]
        plan = PO.create_plan(sdk, "Repairable stage id")
        op = f"op-repaired-{plan['id']}"
        _operator_over(sdk, (point, plan["id"]), op)
        sdk.update_knowledge(plan["id"], pipelineStageId="Held")
        assert op not in _promote(sdk, point)

        sdk.update_knowledge(plan["id"], pipelineStageId=PO.STAGE_SCHEDULED)
        assert _pos(sdk, plan["id"])["unresolvable"] is False
        assert op not in _promote(sdk, point), (
            "repaired to scheduled — still not happened, so still suppressed")

        PO.record_occurrence(sdk, plan["id"], name="it happened")
        assert op in _promote(sdk, point), (
            "a repaired, then settled plan must not keep suppressing for ever")

    def test_a_NON_STRING_status_on_an_ENDPOINT_suppresses_and_never_crashes(
            self, sdk):
        """A stored ARRAY `status` is PERSISTABLE, so it can sit on an operator
        endpoint — and the gate must ANSWER it, never hash it.

        `is_terminal_status` is a `frozenset` membership test, so a list raises
        `TypeError: unhashable type` — and this gate runs inside
        `promote_point` with no try/except, so the escape is an unnamed 500 with
        no door name and no remedy (#10183). The pre-#13100 inline gate tested
        `(st or "live") == "live"` FIRST and short-circuited, so an array
        endpoint suppressed promotion silently; a predicate that runs the
        membership test first re-introduces the crash on the most-travelled
        path. Pinned at the PREDICATE and through the real door — the array
        value is one supported write (`update_knowledge(id, status=['archived'])`
        is accepted and stored verbatim on an `:Object`, an operator endpoint
        since #7853) and is made below the doors here, as the sibling
        `test_a_NON_STRING_status_is_foreign_not_a_crash` does.
        """
        from tortoise.live import endpoint_permits_operator_promotion

        # 1. The predicate itself: an array denies, and does not raise.
        assert endpoint_permits_operator_promotion(["archived"], False) is False

        # 2. Through the door, which is where a raise becomes a bare 500.
        point = sdk.create_point("statement", "a live point")["id"]
        endpoint = sdk.create_point("statement", "an array-status endpoint")["id"]
        op = f"op-array-{endpoint}"
        _operator_over(sdk, (point, endpoint), op)
        sdk._get_proj().g.query(
            "MATCH (s:Point {id:$s}) SET s.status = ['archived']",
            params={"s": endpoint})

        assert op not in _promote(sdk, point), (
            "an unhashable stored status must SUPPRESS promotion, not raise")
        assert sdk._get_proj().g.query(
            "MATCH (o:Point {id:$o}) RETURN o.status",
            params={"o": op}).result_set[0][0] == "draft"

        # 3. Control, so the suppression cannot pass vacuously: clearing the
        #    foreign value lets the SAME operator promote.
        sdk._get_proj().g.query(
            "MATCH (s:Point {id:$s}) SET s.status = null", params={"s": endpoint})
        assert op in _promote(sdk, point), (
            "the array must be the reason the operator stayed draft")

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

    def test_an_event_with_ONLY_an_eventId_still_counts_as_an_occurrence(self, sdk):
        """The link is counted as a NODE, not as an `id` property.

        The session/ingest writers mint `Event` nodes carrying `eventId` only, so
        counting `e.id` (a property `collect` DROPS when null) reported a plan with
        a real linked occurrence as "planned and never happened" — the exact
        inversion this read exists to prevent.

        ⛔ The fixture's kind is the OCCURRENCE kind (#13128): an Event of another
        kind is a MENTION of the plan, and since #13128 the read counts the two
        apart. This case is about the id key, so it must use the kind that IS the
        occurrence, or it would pin the old conflation.
        """
        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "Occurrence with an eventId-only Event")
        g.query("CREATE (e:Event {eventId:'sess-noid', eventKind:$k})",
                params={"k": PO.OCCURRENCE_EVENT_KIND})
        g.query("MATCH (e:Event {eventId:'sess-noid'}), (p:Object {id:$p}) "
                "CREATE (e)-[:aboutObject]->(p)", params={"p": plan["id"]})
        assert g.query(
            "MATCH (:Event)-[:aboutObject]->(:Object {id:$p}) RETURN count(*)",
            params={"p": plan["id"]}).result_set[0][0] == 1
        assert [r["name"] for r in PO.planned_without_occurrence(g)] == []

    def test_a_legacy_status_word_does_not_change_the_answer(self, sdk):
        """The read must not consult a mutable status — so a legacy word on the
        plan's node changes nothing."""
        g = sdk._get_proj().g
        held = PO.create_plan(sdk, "Held with a legacy word")
        PO.record_occurrence(sdk, held["id"], name="happened")
        g.query("MATCH (p:Object {id:$p}) SET p.status='draft'",
                params={"p": held["id"]})
        assert [r["name"] for r in PO.planned_without_occurrence(g)] == []


# ── #13128: a MENTION is not an occurrence ────────────────────────────────


class TestAMentionIsNotAnOccurrence:
    """``aboutObject`` is BOTH the occurrence link and §3.2's ``schema:about``.

    So an Event can be *about* a plan without being its occurrence — a note, a
    reminder, an email summary — and before #13128 the read counted every linked
    Event, so a note made a plan read as "it happened". The distinguishing fact
    is the linked Event's OWN kind: :data:`PO.OCCURRENCE_EVENT_KIND`, whose only
    writer is :func:`record_occurrence` (which refuses any other kind).

    The two facts are then served SEPARATELY — ``mentions`` on the row — rather
    than one number being asked to mean both.
    """

    def test_a_mention_does_not_make_a_plan_read_as_happened(self, sdk):
        """The reported defect, end to end: the exact call from the issue."""
        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "Offsite at the beach club")
        sdk.create_entity("event", "email about the offsite",
                         eventKind="email", aboutObject=plan["id"])
        assert g.query(
            "MATCH (:Event)-[:aboutObject]->(:Object {id:$p}) RETURN count(*)",
            params={"p": plan["id"]}).result_set[0][0] == 1
        rows = {r["name"]: r for r in PO.planned_without_occurrence(g)}
        assert rows.get("Offsite at the beach club"), (
            "an Event merely ABOUT the plan was read as the plan happening")
        assert rows["Offsite at the beach club"]["ended"] is False

    def test_the_mention_count_is_served_beside_the_occurrence(self, sdk):
        """A caller can say "mentioned twice, never happened" — not "nothing"."""
        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "Quarterly partner call")
        for _ in range(2):
            sdk.create_entity("event", "a reminder about it",
                             eventKind="email", aboutObject=plan["id"])
        rows = {r["name"]: r for r in PO.planned_without_occurrence(g)}
        assert rows["Quarterly partner call"]["mentions"] == 2
        # …and the occurrence, when it lands, is what removes the row.
        PO.record_occurrence(sdk, plan["id"])
        assert [r["name"] for r in PO.planned_without_occurrence(g)] == []

    def test_a_mentions_count_is_ZERO_not_ONE_when_nothing_is_linked(self, sdk):
        """The OPTIONAL MATCH's null row must not read as a mention.

        `coalesce(e.eventKind,'') <> 'occurrence'` is TRUE for the planned null
        row, so an unguarded count reported every unlinked plan as mentioned.
        """
        g = sdk._get_proj().g
        PO.create_plan(sdk, "Nothing linked at all")
        rows = {r["name"]: r for r in PO.planned_without_occurrence(g)}
        assert rows["Nothing linked at all"]["mentions"] == 0

    def test_a_mention_does_not_change_an_ENDED_plan_s_ending(self, sdk):
        """Q5's capstone row: a cancelled plan with a note about it.

        The ending comes from the position axis and is served with its outcome;
        the note is neither the reason nor a hidden occurrence.
        """
        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "Thursday partner call")
        PO.settle_plan(sdk, plan["id"], PO.STAGE_CANCELLED, reason="called off")
        sdk.create_entity("event", "email about the partner call",
                         eventKind="email", aboutObject=plan["id"])
        rows = {r["name"]: r for r in PO.planned_without_occurrence(g)}
        row = rows["Thursday partner call"]
        assert row["ended"] is True
        assert row["outcome"] == "withdrawn"
        assert row["mentions"] == 1

    def test_the_KIND_is_the_proof_not_the_edge(self, sdk):
        """Only an Event OF THE OCCURRENCE KIND satisfies Q5.

        Every canonical kind links the same way; one of them is the occurrence.
        """
        g = sdk._get_proj().g
        for kind in ("email", "note", "meeting", "review", "decision"):
            plan = PO.create_plan(sdk, f"Mentioned by a {kind}")
            sdk.create_entity("event", f"a {kind}", eventKind=kind,
                             aboutObject=plan["id"])
            rows = {r["name"]: r for r in PO.planned_without_occurrence(g)}
            assert f"Mentioned by a {kind}" in rows, kind
            assert rows[f"Mentioned by a {kind}"]["mentions"] == 1, kind

    def test_record_occurrence_REFUSES_a_non_occurrence_kind(self, sdk):
        """The writer half: it may not mint a link its own reader must discount.

        A refused call leaves NOTHING behind — no Event, no link, no stage move.
        """
        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "Held as a meeting")
        with pytest.raises(ValueError) as exc:
            PO.record_occurrence(sdk, plan["id"], event_kind="meeting")
        message = str(exc.value)
        assert "MENTION" in message and "create_entity" in message, message
        assert g.query(
            "MATCH (:Event)-[:aboutObject]->(:Object {id:$p}) RETURN count(*)",
            params={"p": plan["id"]}).result_set[0][0] == 0
        assert _stage(sdk, plan["id"])["stageId"] == PO.STAGE_SCHEDULED
        assert plan["id"] in {r["id"] for r in PO.planned_without_occurrence(g)}

    def test_a_LYING_SUBCLASS_kind_cannot_slip_past_the_refusal(self, sdk):
        """The refusal asks the RENDERING, not `__eq__` (#13578 review).

        A `str` subclass whose `__ne__` returns False makes
        `event_kind != OCCURRENCE_EVENT_KIND` False, so a `__eq__`-trusting
        refusal does not fire — and `create_entity` files the kind under its
        name. The plan then advances to terminal `held` while the read
        honestly serves it as *planned and never happened*: the exact inversion
        this guard exists to make unrepresentable, with nothing raising. The
        assertions are the same three post-conditions the plain-`str` case
        uses, because a refusal that fires late leaves the same wreckage.
        """

        class _Kind(str):
            """Lies to equality in BOTH directions, as the file's `_KeyHash` does."""

            def __eq__(self, other):
                return False

            def __ne__(self, other):
                return False

            __hash__ = str.__hash__

        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "Held as a lying meeting")
        with pytest.raises(ValueError) as exc:
            PO.record_occurrence(sdk, plan["id"], event_kind=_Kind("meeting"))
        message = str(exc.value)
        assert "MENTION" in message and "create_entity" in message, message
        # …and the message names the kind that was REFUSED, not a `!r` the
        # subclass could have rewritten.
        assert "'meeting'" in message, message
        assert g.query(
            "MATCH (:Event)-[:aboutObject]->(:Object {id:$p}) RETURN count(*)",
            params={"p": plan["id"]}).result_set[0][0] == 0
        assert _stage(sdk, plan["id"])["stageId"] == PO.STAGE_SCHEDULED
        assert plan["id"] in {r["id"] for r in PO.planned_without_occurrence(g)}

    def test_a_LYING_SUBCLASS_of_the_occurrence_kind_is_written_as_its_RENDERING(
            self, sdk):
        """Normalising must cover the value WRITTEN, not only the one CHECKED.

        A subclass that IS the occurrence kind (but lies about equality) is
        accepted — and `create_entity` must receive the plain rendering, because
        a guard that compares one kind and writes another admits, one writer
        call later, the shape it just refused. The `eventKind` kwarg is captured
        at the door: comparing `==` alone cannot tell a subclass from its
        rendering (the store flattens it), so the assertion is on the TYPE. This
        is also the positive control — without it, a mutant that refuses EVERY
        kind reddens only negatives.
        """

        class _Kind(str):
            def __eq__(self, other):
                return False

            def __ne__(self, other):
                return False

            __hash__ = str.__hash__

        class _Door:
            """The sdk with `create_entity` observed, everything else delegated."""

            def __init__(self, inner):
                self._inner = inner
                self.kwargs = None

            def __getattr__(self, attr):
                return getattr(self._inner, attr)

            def create_entity(self, *args, **kwargs):
                self.kwargs = kwargs
                return self._inner.create_entity(*args, **kwargs)

        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "Held, lying kind")
        door = _Door(sdk)
        recorded = PO.record_occurrence(
            door, plan["id"], event_kind=_Kind(PO.OCCURRENCE_EVENT_KIND))
        assert recorded["move"].applied is True
        written = door.kwargs["eventKind"]
        assert type(written) is str, (
            f"the subclass {type(written).__name__} reached the write, not its "
            f"rendering — the guard checked one kind and the store keeps another")
        assert written == PO.OCCURRENCE_EVENT_KIND
        stored = g.query(
            "MATCH (e:Event)-[:aboutObject]->(:Object {id:$p}) "
            "RETURN e.eventKind",
            params={"p": plan["id"]}).result_set
        assert [row[0] for row in stored] == [PO.OCCURRENCE_EVENT_KIND]
        assert plan["id"] not in {r["id"]
                                 for r in PO.planned_without_occurrence(g)}

    def test_the_explicit_occurrence_kind_is_accepted(self, sdk):
        g = sdk._get_proj().g
        plan = PO.create_plan(sdk, "Held, kind stated")
        recorded = PO.record_occurrence(sdk, plan["id"],
                                        event_kind=PO.OCCURRENCE_EVENT_KIND)
        assert recorded["move"].applied is True
        assert plan["id"] not in {r["id"]
                                 for r in PO.planned_without_occurrence(g)}
        assert _stage(sdk, plan["id"])["stageId"] == PO.STAGE_HELD

    def test_the_occurrence_kind_is_a_core_event_kind(self):
        """The marker is not a coined word: it is ONTOLOGY §5's own kind."""
        from tortoise.pack_registry import CANONICAL_EVENT_KINDS

        assert PO.OCCURRENCE_EVENT_KIND == PO.DEFAULT_OCCURRENCE_KIND
        assert PO.OCCURRENCE_EVENT_KIND in CANONICAL_EVENT_KINDS

    def test_the_read_counts_the_linked_KIND_and_not_a_bare_edge(self):
        """The mutation that matters: a reader that counts every link is the bug.

        Hermetic: a stub graph captures the statement the read ACTUALLY issues, so
        this pins the Cypher (not a copy of it in prose) and cannot be satisfied by
        a reader that special-cases its fixtures. Both arms must be null-guarded —
        an unguarded third arm is how an unlinked plan reads as mentioned — and the
        kind must arrive as a PARAMETER, not be spelled at the call site.
        """

        class _Captured:
            result_set: ClassVar[list] = []

            def __init__(self) -> None:
                self.cypher = ""
                self.params: dict = {}

            def query(self, cypher, params=None):
                self.cypher, self.params = cypher, dict(params or {})
                return self

        graph = _Captured()
        assert PO.planned_without_occurrence(graph) == []
        assert "count(e) AS occurrences" not in graph.cypher, (
            "the read counts every linked Event again — that is #13128")
        assert graph.params["occurrence_kind"] == PO.OCCURRENCE_EVENT_KIND
        assert graph.cypher.count("$occurrence_kind") == 2, (
            "the two arms (occurrence / mention) must both read the kind")
        assert graph.cypher.count("e IS NOT NULL") == 2, (
            "each arm must guard the OPTIONAL MATCH's null row")
        assert graph.params["kinds"] == sorted(PO.PLAN_KINDS)


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
        """The ingest vocabulary is pinned at every writer in ``tortoise/``.

        ``eventStatus`` is ingest BOOKKEEPING (``pending`` -> ``indexed``); a
        lifecycle word in it is the #13100 defect wearing the marker's name —
        it would represent a not-yet-happened thing as an ``:Event``.

        ⛔ The pin reads the VALUE, not a spelling. Its first version matched a
        contiguous ``eventStatus=``/``eventStatus='`` and so could not see the
        dict-literal spelling ``"eventStatus": "indexed"`` — which is how five
        of the seven writers in the tree are written, so reverting one of them
        to ``completed`` left this pin GREEN (#13100 review F2).

        ⛔ And it reads every MODULE, not a hardcoded list. Its second version
        enumerated the three modules below and said "EVERY writer" — so a
        lifecycle write in a FOURTH ``tortoise/`` module stayed green (measured
        in review round 2: the same probe appended to ``record_writer.py``
        passed). The sweep at the end of this test is what makes the claim
        true: the tuple is a DECLARATION the sweep checks, and a new writer
        module reds here and is named.
        """
        import inspect
        import pathlib

        from tortoise import hosted_api, sdk, session_indexer

        scanned = {"tortoise.sdk": sdk, "tortoise.hosted_api": hosted_api,
                   "tortoise.session_indexer": session_indexer}
        assert tuple(scanned) == EVENT_STATUS_WRITER_MODULES

        writes = []
        for name, module in scanned.items():
            writes += [(name, line, value)
                       for line, value in _event_status_writes(
                           inspect.getsource(module))]

        bad = [(m, line, value) for m, line, value in writes
               if value not in INGEST_EVENT_STATES]
        assert not bad, (
            "a writer stamps a lifecycle-shaped ``eventStatus``: "
            f"{bad} — the ingest marker takes {sorted(INGEST_EVENT_STATES)} "
            "only; a not-yet-happened thing is a PLAN (#13100 §8)")

        # ⛔ EXHAUSTIVE BY CONSTRUCTION: the tuple above is a declaration, and
        # THIS is what checks it. Any ``tortoise/`` module carrying a
        # write-shaped literal must be one of the scanned three — otherwise the
        # value check above never sees its writes and the pin is green over a
        # writer it claims to cover. Files are read, not imported.
        package = pathlib.Path(sdk.__file__).resolve().parent
        unlisted = []
        for path in sorted(package.rglob("*.py")):
            name = "tortoise." + ".".join(
                path.relative_to(package).with_suffix("").parts)
            name = name.removesuffix(".__init__")
            if name in scanned:
                continue  # already read above; re-parsing it buys nothing
            source = path.read_text(encoding="utf-8", errors="replace")
            if "eventStatus" not in source:
                continue
            sites = _event_status_writes(source)
            if sites:
                unlisted.append((name, sites))
        assert not unlisted, (
            "a ``tortoise/`` module writes ``eventStatus`` and this pin does "
            f"not scan it: {unlisted} — add it to EVENT_STATUS_WRITER_MODULES "
            "(and to `scanned` above), or the value check never reads it")

        # ⛔ NON-VACUITY (agent-infra#3442): a pin that enumerates nothing is
        # green for ever. The tree has SEVEN literal sites at this head; if the
        # readers stop seeing them, a spelling changed and the pin must be
        # taught it — not silently pass.
        assert len(writes) >= 7, (
            "the pin enumerated only "
            f"{len(writes)} eventStatus writer(s) — fewer than the 7 in-tree "
            "literal sites, so a spelling escaped the readers; extend "
            "_event_status_writes")
