"""Issue #13034 — the reader rule reads BOTH axes, and five consumers use it.

Design of record: ``docs/plans/2026-11-15-state-lifecycle-and-pipelines.md`` §6
— *"Is this still served as current?"* has TWO sources of meaning, and either
one alone is an ending:

* the LIFECYCLE WORD — ``status`` in the terminal vocabulary, or the legacy
  ``outdated`` flag;
* the POSITION AXIS — the durable ``isClosed`` marker, or a stage in the
  ``terminal`` pipeline slot (``tortoise/pipeline.py``).

At this issue's head ``live._terminal_excluded`` / ``is_terminal_status`` read
only the first (and only two of its three arms), and five consumers did not use
the predicate at all — so a closed object and a superseded claim were served as
current, and a superseded claim's stored prior stayed in EP's evidence set.

Measured before/after (private lane, isolated graph) — see the PR body for the
full table:

| behaviour | before | after |
|---|---|---|
| an Object ended ONLY by ``isClosed`` (status absent/live, flag unset) served by the Object FTS leg | served | not served |
| the same object resolved by the live name resolver | ``id`` | ``None`` |
| ``derived_invalidation`` retires its derived caches | ``None`` (no reason) | ``target-retired`` |
| a superseded claim in EP's hydrated evidence set | present, prior (8.0, 2.0) | absent |

Lane: needs a live FalkorDB (a private test lane: ``tools/test_lane.py run --
tests/test_13034_currency_rule_reads_position.py``); the predicate-matrix and
census classes are pure Python and need no database.
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
import sys
import uuid
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tests import _live_utils
from tortoise import derived_invalidation as di
from tortoise import live, pipeline
from tortoise.entity_identity import resolve_entity_id_same_entity
from tortoise.live import (
    TERMINAL_EXCLUDED_STATUSES,
    TERMINAL_STAGE_SLOT,
    _terminal_excluded,
    _terminal_expression,
    is_position_ended,
    is_terminal_status,
)
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import run_fts_query

#: A slot that LOOKS terminal and is not one. The slot is compared EXACTLY, on
#: every representation (#13450 review, P3): the Python mirror, the Cypher twin
#: (`coalesce(n.pipelineSlot, '') <> 'terminal'`) and `pipeline`'s own
#: `PipelineStage.is_terminal` all read `" terminal "` as CURRENT. It is
#: enumerated here because it used to sit OUTSIDE this file's truth table while
#: the Python arm stripped it and the Cypher arm did not — the one value the
#: two sides disagreed on, in the silent-drop direction.
PADDED_TERMINAL_SLOT = f" {TERMINAL_STAGE_SLOT} "

GRAPH = f"tortoise_test_13034_{uuid.uuid4().hex[:8]}"
DB_URI = _live_utils.docker_uri(GRAPH)
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
try:
    os.environ["TORTOISE_DB_URI"] = DB_URI
    _probe = TortoiseSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
    # ⛔ ⛔ A rename or typo in a seam this probe touches must NOT be read as
    #   "storage unavailable": that is exactly how #13899's retirement hid for a
    #   whole wave — the probe's own `except Exception` turned an AttributeError
    #   into a SKIP locally and a hard refusal in CI that names no retired door
    #   (#14114 class 2, #14125). `AttributeError`/`NameError`/`TypeError`/`ImportError` are
    #   PROGRAMMING errors and are re-raised; only genuine transport/driver
    #   failures fall through to the unavailable arm.
except (AttributeError, NameError, TypeError, ImportError):
    raise
except Exception:
    pass
finally:
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

_needs_db = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")


@pytest.fixture(autouse=True)
def _isolated_db_uri():
    _old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = DB_URI
    yield
    if _old is not None:
        os.environ["TORTOISE_DB_URI"] = _old
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


def _fresh_sdk() -> TortoiseSDK:
    sdk = TortoiseSDK(db_path=None, namespace=None)
    sdk._db_uri = DB_URI
    sdk._proj = None  # force re-init on first use
    return sdk


def _stored(g, label: str, node_id: str):
    rows = g.query(
        f"MATCH (n:{label} {{id:$id}}) RETURN n.status, "
        "coalesce(n.outdated, false), coalesce(n.isClosed, false), "
        "n.pipelineSlot",
        params={"id": node_id}).result_set
    return rows[0] if rows else None


# ══════════════════════════════════════════════════════════════════════════
# 1. The predicate reads both sources — the Python mirror
# ══════════════════════════════════════════════════════════════════════════


class TestThePythonMirrorReadsBothAxes:
    """No database: the mirror's own truth table, both axes, one function."""

    @pytest.mark.parametrize("status", [None, "live", "draft"])
    def test_a_live_word_with_no_position_is_current(self, status):
        assert is_terminal_status(status) is False

    @pytest.mark.parametrize("status", [None, "live", "draft"])
    def test_the_durable_marker_ends_it_whatever_the_word_says(self, status):
        # The measured defect: status still live, no `outdated` flag, and the
        # object is closed — every read surface served it as current.
        assert is_terminal_status(status, False, is_closed=True) is True

    @pytest.mark.parametrize("status", [None, "live", "draft"])
    def test_a_terminal_slot_ends_it_whatever_the_word_says(self, status):
        assert is_terminal_status(
            status, False, stage_slot=TERMINAL_STAGE_SLOT) is True

    @pytest.mark.parametrize("slot", ["entry", "active", "suspended", "", None,
                                      PADDED_TERMINAL_SLOT])
    def test_a_non_terminal_slot_is_not_an_ending(self, slot):
        # §4: those slots say how far along, never whether the commitment
        # still stands. A missing stage is not an ending either.
        # ⛔ AND NEITHER IS A PADDED ONE (#13450 review, P3): the slot is
        # compared EXACTLY, so `" terminal "` is not an ending here — which is
        # what the Cypher twin (`coalesce(n.pipelineSlot, '') <> 'terminal'`)
        # and `pipeline.PipelineStage.is_terminal` already answered.
        assert is_terminal_status("live", False, stage_slot=slot) is False

    def test_both_axes_are_read_not_one(self):
        # A word terminal on the lifecycle axis, and one on the position axis,
        # both answer True — the two are sources of ONE answer.
        assert is_terminal_status("superseded") is True
        assert is_terminal_status("live", is_closed=True) is True

    def test_the_marker_is_read_by_truthiness_like_advance_stage(self):
        # `pipeline.advance_stage` reads `bool(cur.get("isClosed"))`, and the
        # legacy `outdated` arm reads `bool()`. A truthy non-boolean legacy
        # value is an ending on all three reads rather than on two of them.
        assert is_position_ended(is_closed="yes") is True
        assert is_position_ended(is_closed=1) is True
        assert is_position_ended(is_closed=False) is False
        assert is_position_ended(is_closed=None) is False

    def test_the_predicate_does_not_read_goal_state(self):
        # §6: `goalState` is a legacy arm in `derived_invalidation`, never a
        # fifth input to the shared rule. Pinned because "align the two axes"
        # could be misread as "fold goalState into the predicate".
        assert "goalState" not in _terminal_excluded.__doc__
        assert "goalState" not in is_terminal_status.__doc__

    def test_the_family_seam_still_decides_the_vocabulary_only(self):
        # `excluded=` chooses WHICH WORDS end a thing; the position axis is a
        # different SOURCE and is read regardless (no opt-out to wire).
        clause = _terminal_excluded(
            "o.status", excluded=frozenset({"superseded"}),
            include_outdated_flag=False)
        assert "o.status <> 'superseded'" in clause
        assert "coalesce(o.isClosed, false) = false" in clause
        assert f"coalesce(o.pipelineSlot, '') <> '{TERMINAL_STAGE_SLOT}'" in clause
        # …and the flag conjunct still obeys its own switch.
        assert "o.outdated" not in clause

    def test_the_terminal_slot_literal_is_pipelines_terminal_role(self):
        # live.py is a LEAF (pipeline imports entity_identity imports live), so
        # the literal is declared here rather than imported. This pins it to
        # the pipeline module's own role for the slot, so a rename of the role
        # cannot leave the predicate reading a slot nobody uses.
        assert TERMINAL_STAGE_SLOT in pipeline.PIPELINE_SLOTS
        stage = pipeline.PipelineStage(
            stage_id="done", label="Done", order=3,
            slot=TERMINAL_STAGE_SLOT, outcome="completed")
        assert stage.is_terminal is True
        for other in ("entry", "active", "suspended"):
            assert pipeline.PipelineStage(
                stage_id=other, label=other, order=0,
                slot=other).is_terminal is False


# ══════════════════════════════════════════════════════════════════════════
# 2. The Cypher twin agrees with the Python mirror, on every combination
# ══════════════════════════════════════════════════════════════════════════


@_needs_db
class TestTheCypherTwinReadsBothAxes:
    """The two surfaces are ONE rule: measured on a real node, not by reading."""

    @pytest.mark.parametrize("status", [None, "live", "superseded"])
    @pytest.mark.parametrize("outdated", [None, False, True])
    @pytest.mark.parametrize("is_closed", [None, False, True])
    @pytest.mark.parametrize("slot", [None, "active", TERMINAL_STAGE_SLOT,
                                      PADDED_TERMINAL_SLOT])
    def test_keep_filter_expression_and_mirror_agree(
            self, status, outdated, is_closed, slot):
        sdk = _fresh_sdk()
        try:
            g = sdk._get_proj().g
            oid = sdk._create_object(f"combo-{uuid.uuid4().hex[:8]}")["id"]
            g.query(
                "MATCH (n:Object {id:$id}) SET n.status = $st, "
                "n.outdated = $od, n.isClosed = $ic, n.pipelineSlot = $slot",
                params={"id": oid, "st": status, "od": outdated,
                        "ic": is_closed, "slot": slot})
            keep = bool(g.query(
                f"MATCH (n:Object {{id:$id}}) "
                f"RETURN CASE WHEN {_terminal_excluded('n.status')} "
                "THEN true ELSE false END",
                params={"id": oid}).result_set[0][0])
            ended = bool(g.query(
                f"MATCH (n:Object {{id:$id}}) "
                f"RETURN CASE WHEN {_terminal_expression('n.status')} "
                "THEN true ELSE false END",
                params={"id": oid}).result_set[0][0])
            mirror = is_terminal_status(
                status, bool(outdated), is_closed=is_closed, stage_slot=slot)
            assert keep is (not mirror), (
                f"keep-filter and mirror disagree on {status!r}/{outdated!r}/"
                f"{is_closed!r}/{slot!r}")
            # #7075's invariant, extended to the position axis: there is no
            # excluded middle where a node is "not terminal" to one surface and
            # "not live" to the other.
            assert ended is mirror, (
                f"the positive expression and the mirror disagree on {status!r}/"
                f"{outdated!r}/{is_closed!r}/{slot!r}")
        finally:
            sdk.close()


# ══════════════════════════════════════════════════════════════════════════
# 3. An object ended ONLY by isClosed is not current — on every surface
# ══════════════════════════════════════════════════════════════════════════


@_needs_db
class TestAClosedObjectIsNeverCurrent:
    def test_search_drops_it_and_the_live_resolver_refuses_it(self):
        sdk = _fresh_sdk()
        try:
            g = sdk._get_proj().g
            name = f"closed-only-deal-{uuid.uuid4().hex[:8]}"
            oid = sdk._create_object(name)["id"]
            assert oid in [r[0] for r in run_fts_query(
                g, name, entity_type="object")]
            assert resolve_entity_id_same_entity(g, "Object", name) == oid

            sdk.update_knowledge(oid, isClosed=True)

            status, outdated, closed, slot = _stored(g, "Object", oid)
            assert (status, bool(outdated), bool(closed), slot) == (
                None, False, True, None), (
                "the fixture must end the object on the POSITION axis ONLY")
            assert oid not in [r[0] for r in run_fts_query(
                g, name, entity_type="object")], (
                "a closed object is still served by the search surface")
            assert resolve_entity_id_same_entity(g, "Object", name) is None, (
                "the live resolver still hands out a closed object's id")
        finally:
            sdk.close()

    def test_the_by_id_read_still_returns_it(self):
        """§6's by-name arm: a caller who asks for a record gets it back.

        The filter is wrong when it DISAGREES WITH THE STATE, not when it
        differs from another filter — so the closed object must be gone from
        every "what counts now" read and still reachable by id, where the
        caller can render *why* it ended.
        """
        sdk = _fresh_sdk()
        try:
            name = f"closed-only-byid-{uuid.uuid4().hex[:8]}"
            oid = sdk._create_object(name)["id"]
            sdk.update_knowledge(oid, isClosed=True)
            got = sdk.get_entity(oid)
            assert got and got["id"] == oid
            assert got.get("isClosed") is True
        finally:
            sdk.close()

    def test_its_derived_caches_are_retired(self):
        sdk = _fresh_sdk()
        try:
            name = f"closed-only-target-{uuid.uuid4().hex[:8]}"
            oid = sdk._create_object(name)["id"]
            assert di._retirement(sdk.get_entity(oid)) is None, (
                "a live target must not be retired before the fixture closes it")
            sdk.update_knowledge(oid, isClosed=True)
            reason = di._retirement(sdk.get_entity(oid))
            assert reason and "position axis" in reason, reason
            # …and through the public verdict path the sweep actually uses.
            verdict = di.invalidation_for(sdk, {
                "id": f"j-{uuid.uuid4().hex[:8]}", "goalId": None,
                "indicatorId": None, "targetId": oid,
                "judgementState": "met"})
            assert verdict.invalidation is not None, (
                "a judgement on a closed target was reported healthy")
            assert verdict.invalidation.reason == "target-retired"
        finally:
            sdk.close()

    def test_a_terminal_pipeline_slot_ends_it_with_no_marker(self):
        """A terminal slot still ends an object whose marker was never set.

        `advance_stage` writes `isClosed` beside the slot, so this shape is the
        one a seeded/legacy graph (or the generic entity surface) produces: the
        slot is terminal, the durable marker is absent.
        """
        sdk = _fresh_sdk()
        try:
            g = sdk._get_proj().g
            name = f"slot-terminal-deal-{uuid.uuid4().hex[:8]}"
            oid = sdk._create_object(name)["id"]
            g.query(
                "MATCH (n:Object {id:$id}) SET n.pipelineStageId = 'stg_done', "
                "n.pipelineSlot = $slot, n.pipelineStageKey = 'core:deal'",
                params={"id": oid, "slot": TERMINAL_STAGE_SLOT})
            _status, _outdated, closed, slot = _stored(g, "Object", oid)
            assert slot == TERMINAL_STAGE_SLOT and closed is False, (
                "the fixture must end the object by SLOT alone, with no "
                "durable marker, or the assertion below could pass on "
                "isClosed")
            assert oid not in [r[0] for r in run_fts_query(
                g, name, entity_type="object")]
            assert resolve_entity_id_same_entity(g, "Object", name) is None
            assert di._retirement(sdk.get_entity(oid)) is not None
        finally:
            sdk.close()

    def test_a_slot_reclassification_does_not_resurrect_it_for_READERS(self):
        """The reader half of `test_pipeline_2792`'s pinned shapes.

        `pipeline.advance_stage` keys stickiness on the durable `isClosed`
        marker, not on the live declaration's slot, so a pack release that
        reclassifies the stage an object sits in cannot reopen it. The READERS
        must agree with that guard: an object closed while sitting in an
        `active` slot stays ended however the declaration is later rewritten.
        """
        sdk = _fresh_sdk()
        try:
            g = sdk._get_proj().g
            name = f"closed-active-slot-{uuid.uuid4().hex[:8]}"
            oid = sdk._create_object(name)["id"]
            g.query(
                "MATCH (n:Object {id:$id}) SET n.pipelineStageId = 'stg_won', "
                "n.pipelineSlot = 'active', n.pipelineStageKey = 'core:deal', "
                "n.isClosed = true", params={"id": oid})
            # the pack release: the stage it sits in becomes non-terminal, and
            # a re-resolve rewrites the stored slot
            g.query(
                "MATCH (n:Object {id:$id}) SET n.pipelineSlot = 'active'",
                params={"id": oid})
            assert oid not in [r[0] for r in run_fts_query(
                g, name, entity_type="object")]
            assert resolve_entity_id_same_entity(g, "Object", name) is None
            assert di._retirement(sdk.get_entity(oid)) is not None
        finally:
            sdk.close()

    def test_the_hosted_list_route_drops_it(self):
        """The public list route asks the shared predicate (#432/#689 arm)."""
        from tortoise import hosted_api

        sdk = _fresh_sdk()
        try:
            g = sdk._get_proj().g
            sfx = uuid.uuid4().hex[:8]
            live_id = sdk._create_point(
                "statement", f"list-live-{sfx}", status="live")["id"]
            closed_id = sdk._create_point(
                "statement", f"list-closed-{sfx}", status="live")["id"]
            superseded_id = sdk._create_point(
                "statement", f"list-sup-{sfx}", status="live")["id"]
            succ = sdk._create_point("statement", f"list-succ-{sfx}",
                                    status="live")["id"]
            sdk._supersede_point(superseded_id, succ)
            # list_points filters `n:Point`, so close a POINT to exercise the
            # route's own clause with the position axis (a Point carries no
            # pipeline; the marker is the generic-surface write).
            g.query("MATCH (n:Point {id:$id}) SET n.isClosed = true",
                    params={"id": closed_id})

            async def _patched_sdk(_org, **_kw):
                return sdk

            async def _inline(fn, **_kw):
                return fn()

            old_sdk, old_read = hosted_api._data_sdk_offloaded, hosted_api._read_offload
            hosted_api._data_sdk_offloaded = _patched_sdk
            hosted_api._read_offload = _inline
            try:
                rows = asyncio.run(hosted_api.list_points(
                    kind=None, tag=None, limit=100,
                    org={"legacy_full_access": True}))
            finally:
                hosted_api._data_sdk_offloaded = old_sdk
                hosted_api._read_offload = old_read
            served = {r["id"] for r in rows["points"]}
            assert live_id in served
            assert closed_id not in served, (
                "the public list route served an isClosed Point")
            assert superseded_id not in served, (
                "the public list route served a superseded Point")
        finally:
            sdk.close()


# ══════════════════════════════════════════════════════════════════════════
# 4. The consumers that used to answer currency themselves
# ══════════════════════════════════════════════════════════════════════════


@_needs_db
class TestTheEpEvidenceSet:
    """`sdk._hydrate_evidence` — a superseded claim's prior must not be loaded."""

    def test_a_superseded_claim_is_not_ep_evidence(self):
        sdk = _fresh_sdk()
        try:
            sfx = uuid.uuid4().hex[:8]
            claim = sdk._create_point("statement", f"ev-{sfx}",
                                     status="live")["id"]
            sdk._set_point_baseline(claim, 8.0, 2.0)
            succ = sdk._create_point("statement", f"ev-succ-{sfx}",
                                    status="live")["id"]
            sdk._evidence = {}
            sdk._hydrate_evidence()
            assert claim in sdk._evidence, (
                "the fixture must be hydrated BEFORE the supersede, or the "
                "assertion below could pass for the wrong reason")
            sdk._supersede_point(claim, succ)
            sdk._evidence = {}
            sdk._hydrate_evidence()
            assert claim not in sdk._evidence, (
                "a superseded claim's stored prior is still loaded as EP "
                "evidence")
        finally:
            sdk.close()

    def test_a_superseded_claim_stops_contributing_to_ep(self):
        """The operator boundary, measured on a real small graph.

        With the source LIVE, the operator consumes its (8, 2) prior and the
        target's posterior is INFORMED (asymmetric α/β, confidence != 0.5).
        Once the source is superseded the same run leaves a symmetric posterior
        at the neutral mean — the retired claim's operator contributes nothing.

        ⚠️ WHAT THIS PINS, AND WHAT IT DOES NOT. EP's factor assembly already
        drops terminal claims through ``live._live_only``, so this boundary held
        at HEAD too — measured before this change: 1.63906/1.03267 ->
        1.26272/1.26272 across the supersede. The arm #13034 had to fix is the
        EVIDENCE set (``_hydrate_evidence``), asserted separately above: the
        ghost's prior is no longer loaded at all. This test is the acceptance's
        second bullet stated as a measurement, not a claim that the posterior
        moved because of this diff.
        """
        sdk = _fresh_sdk()
        try:
            g = sdk._get_proj().g
            sfx = uuid.uuid4().hex[:8]
            source = sdk._create_point("statement", f"ep-src-{sfx}",
                                      status="live")["id"]
            sdk._set_point_baseline(source, 8.0, 2.0)
            target = sdk._create_point("statement", f"ep-tgt-{sfx}",
                                      status="live")["id"]
            op_id = sdk._create_operator(
                "IMPL", source, [target], direction="unidirectional")["id"]

            def _belief(pid):
                row = g.query(
                    "MATCH (n:Point {id:$id}) RETURN "
                    "coalesce(n.posterior_alpha, 0.0), "
                    "coalesce(n.posterior_beta, 0.0), n.confidence",
                    params={"id": pid}).result_set[0]
                return [float(x) if x is not None else None for x in row]

            sdk._compute_confidence(factors=[op_id], require_calibration=False)
            contributed = _belief(target)
            assert contributed[0] != contributed[1], (
                "the fixture never reached a state where the live operator "
                f"contributed (posterior {contributed})")
            assert contributed[2] != 0.5

            succ = sdk._create_point("statement", f"ep-succ-{sfx}",
                                    status="live")["id"]
            sdk._supersede_point(source, succ)
            sdk._compute_confidence(factors=[op_id], require_calibration=False)
            retired = _belief(target)
            # EP converges to a symmetric posterior rather than landing on it
            # exactly, so the symmetry is judged at the tolerance the run's own
            # `tol` uses; `confidence` is the served mean and is exact to 1e-6.
            assert retired[0] == pytest.approx(retired[1], rel=1e-4), (
                "a superseded claim's operator still contributes to EP: "
                f"posterior {retired}")
            assert retired[2] == pytest.approx(0.5, abs=1e-6)
            assert retired[0] < contributed[0], (
                "the retired prior must stop pulling the target's posterior "
                f"up: with the live source {contributed}, retired {retired}")
        finally:
            sdk.close()


class TestTheMcpTagArm:
    """`tortoise_query(tag=…)` used to carry its own `status != 'retracted'`."""

    _ROWS: ClassVar[list[dict]] = [
        {"id": "live", "status": "live"},
        {"id": "no-status", "status": None},
        {"id": "draft", "status": "draft"},
        {"id": "superseded", "status": "superseded"},
        {"id": "retracted", "status": "retracted"},
        {"id": "deprecated", "status": "deprecated"},
        {"id": "archived", "status": "archived"},
        {"id": "outdated-flag", "status": "live", "outdated": True},
        {"id": "closed", "status": "live", "isClosed": True},
        {"id": "terminal-stage", "status": "live",
         "pipelineSlot": TERMINAL_STAGE_SLOT},
        {"id": "active-stage", "status": "live", "pipelineSlot": "active"},
    ]

    @pytest.fixture()
    def tool(self, monkeypatch):
        from tortoise import mcp_server
        from tortoise.mcp_auth import (
            _current_org_id,
            _current_org_limits,
            _transport_mode,
        )

        class _Stub:
            # The door calls the PRIVATE seam (`mcp_server.py` →
            # `_safe(_get_org_sdk()._query_points_by_tag, …)`), so the double must
            # answer the spelling the class actually holds — a public `def` here
            # left the assertions running over an AttributeError envelope.
            def _query_points_by_tag(self, tag, *, _record_reads=True):
                return [dict(r) for r in TestTheMcpTagArm._ROWS]

        monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _Stub())
        # Drive the tool body directly, without the HTTP middleware — the same
        # shape `tests/test_12500_query_args_dropped.py` uses.
        _transport_mode.set("http")
        _current_org_id.set(None)
        _current_org_limits.set(None)
        try:
            yield mcp_server
        finally:
            _transport_mode.set(None)
            _current_org_id.set(None)
            _current_org_limits.set(None)

    def test_the_tag_arm_excludes_every_ending(self, tool):
        served = {r["id"] for r in tool.tortoise_query(tag="t")}
        assert served == {"live", "no-status", "draft", "active-stage"}, (
            f"the tag arm served ended rows as current: {sorted(served)}")

    def test_include_retracted_still_surfaces_everything(self, tool):
        served = {r["id"] for r in tool.tortoise_query(
            tag="t", include_retracted=True)}
        assert served == {r["id"] for r in self._ROWS}


@_needs_db
class TestTheTopicNeighborhood:
    """`topic_summarization` seeded a live neighborhood from dead claims."""

    def test_a_superseded_claim_never_seeds_or_expands_a_neighborhood(self):
        from tortoise.topic_summarization import _retrieve_topic_neighborhood

        sdk = _fresh_sdk()
        try:
            g = sdk._get_proj().g
            sfx = uuid.uuid4().hex[:8]
            topic = f"topicneighborhood{sfx}"
            dead = sdk._create_point("statement", f"{topic} dead seed",
                                    status="live")["id"]
            succ = sdk._create_point("statement", f"{topic} successor",
                                    status="live")["id"]
            sdk._supersede_point(dead, succ)
            live_pt = sdk._create_point("statement", f"{topic} live seed",
                                       status="live")["id"]

            seeded, _meta, _seed_count = _retrieve_topic_neighborhood(g, topic)
            assert live_pt in seeded, (
                "the fixture must keep a live claim reachable, or the "
                "assertion below could pass because NOTHING was seeded")
            assert dead not in seeded, (
                "a superseded claim's content seeded a topic neighborhood")
        finally:
            sdk.close()


# ══════════════════════════════════════════════════════════════════════════
# 5. The census — no module outside live.py re-spells a terminal currency test
# ══════════════════════════════════════════════════════════════════════════


#: The consumers the issue names, plus the two modules whose hand-written
#: filters fed the same question (`sdk`'s recall pool, `consistency`'s carrier
#: gate). Scanned by AST, so COMMENTS and DOCSTRINGS cannot satisfy or defeat
#: this test — only real code and real query strings can.
_CENSUS_MODULES = (
    "tortoise/mcp_server.py",
    "tortoise/hosted_api.py",
    "tortoise/topic_summarization.py",
    "tortoise/sdk.py",
    "tortoise/derived_invalidation.py",
    "tortoise/consistency.py",
    # #13450: EP's operator-input participation test was the consumer that read
    # only two of the four conditions (`status in TERMINAL_EXCLUDED_STATUSES or
    # bool(outdated)`), so a position-axis-ended input still voted.
    # ⛔ What this row DOES and DOES NOT guard, stated because the first cut of
    # this comment claimed the row meant "the same re-spelling cannot come back"
    # and that was FALSE for the exact defect it was written for (PR #14600's
    # review, P2). `_currency_comparisons` inspects `ast.Eq`/`ast.NotEq` against a
    # terminal WORD LITERAL and `status <> '<word>'` Cypher literals; the live
    # spelling was an `ast.In` MEMBERSHIP against an IDENTIFIER
    # (`TERMINAL_EXCLUDED_STATUSES`), which the detector never looks at — MEASURED
    # over a mutant restoring that arm: zero hits, i.e. this row is detector-wise
    # a no-op for this defect. What it DOES catch is a future hand-written
    # `status == "retracted"`-shaped test in `ep.py` (verified by AST: the real
    # module has zero hits today).
    # ⛔ The membership arm is deliberately NOT added here. MEASURED at this head:
    # teaching the detector `ast.In` produces ~10 pre-existing hits across
    # `sdk.py` and `derived_invalidation.py`, and several are currency answers that
    # read the word axis ALONE (`derived_invalidation.py:952`/`:999`,
    # `sdk.py:27773`, `sdk.py:37107`, `sdk.py:37109`) — the SAME class #13450 fixed,
    # whose widening is the ingest/direct-edge decision this PR states as a
    # residual rather than a tidy-up. Landing the detector without that decision
    # would either red four modules this PR does not own or exempt them by
    # `(module, word)`, which is fail-open for any NEW bad membership there. The
    # population is measured and filed instead; the ep.py reversion is caught
    # STRUCTURALLY by `tests/test_13450_...::test_ep_imports_the_shared_answer...`
    # (a real `ast.Call` to `ending_axes`, no `Name`/`Attribute` of the vocabulary),
    # which is comment-immune — that is the arm the reversion trips.
    "tortoise/ep.py",
)

#: Every remaining hand-written comparison, with the reason it is NOT a
#: currency filter. Each entry is `(module, the literal it compares against)`.
#: ⛔ Adding a row is a decision: a currency filter belongs in `live.py`.
_DECLARED_EXCEPTIONS = {
    # The tombstone 404 arm: it asks "is this record a TOMBSTONE this route
    # refuses to name", not "is it current". Design §6's by-name row says a
    # caller who asks for a record gets it, so widening this to every terminal
    # word would change a documented REST contract (#432/#689) — a different
    # decision from #13034's.
    ("tortoise/hosted_api.py", "retracted"),
    # #1391/#689 recall leak guard: an OPT-IN path (`include_superseded`)
    # that already surfaces terminal rows on purpose re-filters the tombstone
    # out of its own pool. It is not serving a dead row as current — the caller
    # asked for the superseded ones.
    ("tortoise/sdk.py", "retracted"),
    # #5285 cycle-3: an AMBIGUITY gate on carrier counts (exactly one
    # still-live carrier is the only one a fold could claim), never a
    # target-existence or currency answer.
    ("tortoise/consistency.py", "superseded"),
}

_TERMINAL_WORD = "|".join(sorted(TERMINAL_EXCLUDED_STATUSES))
_CYPHER_FILTER = re.compile(
    rf"status\s*(?:<>|!=)\s*['\"](?:{_TERMINAL_WORD})['\"]", re.I)
_PY_COMPARISON = re.compile(rf"^(?:{_TERMINAL_WORD})$", re.I)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def _currency_comparisons(path: str) -> list[str]:
    """Every hand-written terminal-status comparison that is real CODE."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    docs = _docstring_nodes(tree)
    found: list[str] = []

    def _name(node) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)):
            return f".get({node.args[0].value!r})"
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docs:
            for m in _CYPHER_FILTER.finditer(node.value):
                found.append(f"{path}:{node.lineno}: {m.group(0)}")
        elif isinstance(node, ast.Compare):
            left = _name(node.left)
            for op, comp in zip(node.ops, node.comparators,
                                 strict=True):
                right = _name(comp)
                if not isinstance(op, (ast.Eq, ast.NotEq)):
                    continue
                if "status" not in (left + right):
                    continue
                word = right if _PY_COMPARISON.match(right or "") else (
                    left if _PY_COMPARISON.match(left or "") else "")
                if word:
                    found.append(f"{path}:{node.lineno}: "
                                 f"{left} vs {word!r}")
    return found


class TestNoReaderRespellsThePredicate:
    def test_no_module_outside_live_spells_a_terminal_currency_filter(self):
        offenders = []
        for module in _CENSUS_MODULES:
            for hit in _currency_comparisons(module):
                word = hit.rsplit("'", 2)[-2] if "'" in hit else hit
                if not any(module == m and word == w
                           for m, w in _DECLARED_EXCEPTIONS):
                    offenders.append(hit)
        assert not offenders, (
            "these hand-written terminal-status comparisons answer the "
            "currency question themselves instead of importing "
            "`live.is_terminal_status` / `live._terminal_excluded`:\n"
            + "\n".join(offenders))

    def test_the_predicate_is_what_the_converted_consumers_import(self):
        # The census above can only prove an ABSENCE; this proves the presence
        # of the shared answer at each converted site, so deleting a filter
        # outright (rather than converting it) reds here.
        expected = {
            "tortoise/mcp_server.py": "is_terminal_status",
            "tortoise/hosted_api.py": "_terminal_excluded",
            "tortoise/topic_summarization.py": "_terminal_excluded",
            "tortoise/sdk.py": "_terminal_excluded",
            "tortoise/derived_invalidation.py": "is_position_ended",
            # ⛔ `tortoise/ep.py` is deliberately NOT an entry here. The assertion below is a
            # plain substring test, and `ep.py` names `ending_axes` in its IMPORT (:32), in
            # `_input_status_label`'s DOCSTRING (:231/:235/:238) and in a COMMENT (:2295), so a
            # row here stays green over a mutant that deletes the CALL and restores the
            # two-column test — the #14600 P2 vacuity class, measured. Its real pin is the
            # comment-immune AST arm `test_13450_ep_position_axis_participation.py::
            # test_ep_imports_the_shared_answer_and_spells_no_filter`, which asserts an
            # `ast.Call` to `ending_axes` and no terminal-word vocabulary reference at all; a
            # substring row beside it would advertise coverage this test cannot give.
        }
        for module, symbol in expected.items():
            src = Path(module).read_text(encoding="utf-8")
            assert symbol in src, f"{module} no longer uses {symbol}"
            assert live.__name__ and symbol in dir(live)
