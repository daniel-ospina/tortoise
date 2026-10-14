"""#2792 — configurable per-graph pipelines (CRM-style stage lifecycles).

Pins the lane's acceptance targets:

- **default → override → advance → read** — a pack declares default stages; a
  graph overrides them (rename / reorder / add / retire, no code change and no
  pack release); a stage change reported later updates the current stage and
  the prior value is retained as dated journal history.
- **unknown stage surfaced, not lost** — an unrecognised extracted stage is
  parked on the Object for review, never dropped and never auto-adopted.
- **terminal is sticky** — a passing later mention cannot resurrect a closed
  thing; only an explicit reopen moves it.
- **isolation + durability** — a graph's override does not leak into another
  graph, and both the pipeline configuration and the Object's stage survive a
  real ``rebuild_all`` wipe+replay (#2814's authoritative-config registry now
  enrols ``:Pipeline`` / ``:PipelineStage``).

Lane: **embedded** (``TORTOISE_TEST_CARVE_OUT=1``) — the state pins drive the
real ``EntityMutated`` journal + rebuild replay, which the server-side
bulk-wipe guard refuses on a non-test graph. Registered in
``config/ci-surfaces.yml`` (``slow_files`` + ``carve_out``), in
``tests/_embedded.py::TEST_NO_REDIRECT_STEMS``, and the literal in
``tests/test_markers.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import pipeline as P  # noqa: E402, RUF100
from tortoise.pack_registry import PackRegistry  # noqa: E402, RUF100
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100

KEY = "venture:deal"

DECL = {
    "defaultStage": "stg_contact",
    "stages": [
        {"id": "stg_contact", "label": "Contact", "order": 1, "slot": "entry"},
        {"id": "stg_discussion", "label": "Discussion", "order": 2, "slot": "active"},
        {"id": "stg_won", "label": "Won", "order": 3, "slot": "terminal",
         "outcome": "won"},
    ],
}


def _mk_sdk(tmp_path: Path) -> TortoiseSDK:
    events = tmp_path / "events"
    events.mkdir(parents=True)
    return TortoiseSDK(str(tmp_path / "t.db"),
                       event_log_path=str(events / "events.jsonl"))


def _journal_stages(events_dir: Path) -> list[str]:
    from tortoise.log import EventLog
    out = []
    for ev in EventLog(str(events_dir / "events.jsonl")).read_all():
        if ev.get("type") == "EntityMutated":
            sid = (ev.get("state") or {}).get("pipelineStageId")
            if sid:
                out.append(sid)
    return out


# ── Declaration validation (no DB) ────────────────────────────────────────


class TestDeclarationValidation:
    def test_valid_declaration(self):
        assert P.validate_pipeline_decl(DECL) == []

    def test_missing_stages(self):
        errs = P.validate_pipeline_decl({"defaultStage": "x"})
        assert any("non-empty list" in e for e in errs)

    def test_unknown_slot_rejected(self):
        bad = {"stages": [{"id": "a", "label": "A", "order": 1, "slot": "done"}]}
        errs = P.validate_pipeline_decl(bad)
        assert any("'slot' must be one of" in e for e in errs)

    def test_terminal_requires_outcome(self):
        bad = {"stages": [{"id": "a", "label": "A", "order": 1, "slot": "terminal"}]}
        errs = P.validate_pipeline_decl(bad)
        assert any("terminal stage must declare 'outcome'" in e for e in errs)

    def test_non_terminal_forbids_outcome(self):
        bad = {"stages": [{"id": "a", "label": "A", "order": 1, "slot": "active",
                           "outcome": "won"}]}
        errs = P.validate_pipeline_decl(bad)
        assert any("'outcome' is only valid on a terminal stage" in e for e in errs)

    def test_duplicate_ids_and_bad_default(self):
        bad = {
            "defaultStage": "nope",
            "stages": [
                {"id": "a", "label": "A", "order": 1, "slot": "entry"},
                {"id": "a", "label": "B", "order": 2, "slot": "active"},
            ],
        }
        errs = P.validate_pipeline_decl(bad)
        assert any("duplicate stage id" in e for e in errs)
        assert any("defaultStage" in e and "not one of" in e for e in errs)

    def test_unknown_pipeline_key_rejected(self):
        errs = P.validate_pipeline_decl({"stages": DECL["stages"], "extra": 1})
        assert any("unknown key" in e for e in errs)


class TestPackManifestIntegration:
    def test_pipeline_key_is_accepted_by_kinddef_validation(self):
        reg = PackRegistry("/tmp/nonexistent")
        errors = reg._validate({
            "namespace": "venture", "name": "Venture",
            "ontology": {
                "extends": "core",
                "objectKinds": ["deal"],
                "kindDefs": {"deal": {"pipeline": DECL}},
            },
        })
        assert not any("pipeline" in e for e in errors), errors

    def test_malformed_pipeline_is_refused_with_the_kind_named(self):
        reg = PackRegistry("/tmp/nonexistent")
        errors = reg._validate({
            "namespace": "venture", "name": "Venture",
            "ontology": {
                "extends": "core",
                "objectKinds": ["deal"],
                "kindDefs": {"deal": {"pipeline": {
                    "stages": [{"id": "a", "label": "A", "order": 1,
                                "slot": "nonsense"}]}}},
            },
        })
        assert any("kindDefs 'deal'" in e and "slot" in e for e in errors), errors

    def test_unknown_kinddef_key_still_refused(self):
        reg = PackRegistry("/tmp/nonexistent")
        errors = reg._validate({
            "namespace": "venture", "name": "Venture",
            "ontology": {"extends": "core", "objectKinds": ["deal"],
                         "kindDefs": {"deal": {"pipelines": []}}},
        })
        assert any("unknown key 'pipelines'" in e for e in errors), errors

    def test_declared_pipeline_for_resolves_namespaced_kind(self):
        pack = SimpleNamespace(namespace="venture",
                               kind_defs={"deal": {"pipeline": DECL}})
        reg = SimpleNamespace(packs={"venture": pack})
        assert P.declared_pipeline_for(reg, "venture:deal") == DECL
        assert P.declared_pipeline_for(reg, "venture:other") is None
        assert P.declared_pipeline_for(reg, "deal") is None

    def test_declared_pipelines_enumerates_the_universe(self):
        pack = SimpleNamespace(namespace="venture",
                               kind_defs={"deal": {"pipeline": DECL},
                                          "thing": {"description": "x"}})
        reg = SimpleNamespace(packs={"venture": pack})
        assert P.declared_pipelines(reg) == {"venture:deal": DECL}


# ── Default → override (configuration) ────────────────────────────────────


class TestEffectivePipeline:
    def test_pack_default_applies_to_a_fresh_graph(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            stages = P.list_stages(sdk._get_proj().g, KEY, declared=DECL)
            assert [s.stage_id for s in stages] == [
                "stg_contact", "stg_discussion", "stg_won"]
            assert P.effective_pipeline(
                sdk._get_proj().g, KEY, declared=DECL).source == "pack-default"
        finally:
            sdk.close()

    def test_override_survives_and_pack_default_no_longer_applies(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            P.seed_pipeline(g, KEY, DECL)
            P.rename_stage(g, KEY, "stg_discussion", "Diligence", declared=DECL)
            P.add_stage(g, KEY, stage_id="stg_ts", label="Term sheet",
                        slot="active", declared=DECL)
            P.reorder_stages(
                g, KEY, ["stg_contact", "stg_discussion", "stg_ts", "stg_won"],
                declared=DECL)
            stages = P.list_stages(g, KEY, declared=DECL)
            assert [s.stage_id for s in stages] == [
                "stg_contact", "stg_discussion", "stg_ts", "stg_won"]
            assert [s.order for s in stages] == [1, 2, 3, 4]
            assert stages[1].label == "Diligence"
            assert P.effective_pipeline(
                g, KEY, declared=DECL).source == "graph-override"
        finally:
            sdk.close()

    def test_rename_leaves_stage_id_so_objects_are_unaffected(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-r", objectKind=KEY)
            P.seed_pipeline(g, KEY, DECL)
            P.advance_stage(sdk, "deal-r", KEY, "stg_discussion", declared=DECL)
            P.rename_stage(g, KEY, "stg_discussion", "Assinado", declared=DECL)
            cur = P.current_stage(g, "deal-r")
            assert cur["stageId"] == "stg_discussion"
            assert cur["slot"] == "active"
        finally:
            sdk.close()

    def test_pipeline_edits_bump_an_audited_version_counter(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            P.seed_pipeline(g, KEY, DECL)
            v1 = g.query("MATCH (p:Pipeline {pipelineKey:$k}) RETURN p.version",
                         params={"k": KEY}).result_set[0][0]
            P.rename_stage(g, KEY, "stg_discussion", "Diligence", declared=DECL)
            P.add_stage(g, KEY, stage_id="stg_x", label="X", slot="active",
                        declared=DECL)
            v3 = g.query("MATCH (p:Pipeline {pipelineKey:$k}) RETURN p.version",
                         params={"k": KEY}).result_set[0][0]
            assert v1 == 1 and v3 == 3, (v1, v3)
        finally:
            sdk.close()

    def test_retire_a_stage_objects_sit_in_is_allowed_and_flagged(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-ret", objectKind=KEY)
            P.seed_pipeline(g, KEY, DECL)
            P.advance_stage(sdk, "deal-ret", KEY, "stg_discussion", declared=DECL)
            retired = P.retire_stage(g, KEY, "stg_discussion", declared=DECL)
            assert retired.status == P.STAGE_RETIRED
            # The Object keeps its stage — never silently moved.
            assert P.current_stage(g, "deal-ret")["stageId"] == "stg_discussion"
            active = [s.stage_id for s in P.list_stages(g, KEY, declared=DECL)]
            assert "stg_discussion" not in active
        finally:
            sdk.close()

    def test_default_stage_cannot_be_retired(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            P.seed_pipeline(g, KEY, DECL)
            with pytest.raises(ValueError, match="default stage"):
                P.retire_stage(g, KEY, "stg_contact", declared=DECL)
        finally:
            sdk.close()


# ── Movement: advance → current-stage read, history retained ──────────────


class TestStageAdvance:
    def test_default_to_override_to_advance_to_current_read(self, tmp_path):
        """The lane's headline target, end to end."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-2792", objectKind=KEY)
            # 1. pack default applies
            assert [s.stage_id for s in
                    P.list_stages(g, KEY, declared=DECL)] == [
                        "stg_contact", "stg_discussion", "stg_won"]
            # 2. graph override
            P.rename_stage(g, KEY, "stg_discussion", "Diligence", declared=DECL)
            # 3. stage advance (reported in a later meeting)
            move = P.advance_stage(sdk, "deal-2792", KEY, "Diligence",
                                   at="2026-09-02", declared=DECL)
            assert move.applied and move.stage.stage_id == "stg_discussion"
            # 4. the current stage is what a query returns — one answer
            cur = P.current_stage(g, "deal-2792")
            assert cur["stageId"] == "stg_discussion"
            assert cur["slot"] == "active"
            assert cur["since"] == "2026-09-02"
            assert cur["isClosed"] is False
        finally:
            sdk.close()

    def test_prior_value_is_retained_as_dated_history(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-hist", objectKind=KEY)
            P.advance_stage(sdk, "deal-hist", KEY, "stg_contact",
                            at="2026-09-01", declared=DECL)
            P.advance_stage(sdk, "deal-hist", KEY, "stg_discussion",
                            at="2026-09-02", declared=DECL)
            assert P.current_stage(g, "deal-hist")["stageId"] == "stg_discussion"
            # The journal keeps BOTH moves -> the prior value is history, and
            # rebuild replays it to the same current stage.
            assert _journal_stages(tmp_path / "events") == [
                "stg_contact", "stg_discussion"]
            sdk._get_proj().rebuild_all(str(tmp_path / "events"),
                                        confirm_destructive=True)
            assert P.current_stage(g, "deal-hist")["stageId"] == "stg_discussion"
        finally:
            sdk.close()

    def test_out_of_order_meeting_does_not_regress_the_current_stage(self, tmp_path):
        """A January meeting processed in March must not undo a February move."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-stale", objectKind=KEY)
            P.advance_stage(sdk, "deal-stale", KEY, "stg_discussion",
                            at="2026-09-10", declared=DECL)
            # An EARLIER-valid transition, processed later.
            move = P.advance_stage(sdk, "deal-stale", KEY, "stg_contact",
                                   at="2026-09-01", declared=DECL)
            assert not move.applied and move.reason == "stale-transition"
            assert P.current_stage(g, "deal-stale")["stageId"] == "stg_discussion"
            # An explicit correction still applies.
            fixed = P.advance_stage(sdk, "deal-stale", KEY, "stg_contact",
                                    at="2026-09-01", correction=True, declared=DECL)
            assert fixed.applied
        finally:
            sdk.close()

    def test_advancing_against_a_missing_pipeline_is_refused(self, tmp_path):
        """A MISSING pipeline is a config error, not an unknown stage — it
        must not silently park every value as a candidate."""
        sdk = _mk_sdk(tmp_path)
        try:
            sdk.create_object("deal-nopipe", objectKind=KEY)
            with pytest.raises(ValueError, match="no pipeline is configured"):
                P.advance_stage(sdk, "deal-nopipe", KEY, "anything")
        finally:
            sdk.close()

    def test_backward_move_is_recorded_but_not_applied_without_correction(
            self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-back", objectKind=KEY)
            P.advance_stage(sdk, "deal-back", KEY, "stg_discussion",
                            at="2026-09-02", declared=DECL)
            move = P.advance_stage(sdk, "deal-back", KEY, "stg_contact",
                                   at="2026-09-03", declared=DECL)
            assert not move.applied and move.reason == "regression-guard"
            assert P.current_stage(g, "deal-back")["stageId"] == "stg_discussion"
            # an explicit correction applies
            fixed = P.advance_stage(sdk, "deal-back", KEY, "stg_contact",
                                    at="2026-09-04", correction=True,
                                    reason="logged in the wrong stage",
                                    declared=DECL)
            assert fixed.applied
            assert P.current_stage(g, "deal-back")["stageId"] == "stg_contact"
        finally:
            sdk.close()


# ── Terminal stickiness + unknown-stage capture ───────────────────────────


class TestTerminalAndUnknown:
    def test_closed_thing_is_not_resurrected_by_a_later_mention(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-closed", objectKind=KEY)
            P.advance_stage(sdk, "deal-closed", KEY, "stg_won",
                            at="2026-09-04", declared=DECL)
            assert P.current_stage(g, "deal-closed")["isClosed"] is True
            move = P.advance_stage(sdk, "deal-closed", KEY, "stg_contact",
                                   at="2026-10-01", declared=DECL)
            assert not move.applied and move.reason == "terminal-guard"
            cur = P.current_stage(g, "deal-closed")
            assert cur["stageId"] == "stg_won" and cur["isClosed"] is True
        finally:
            sdk.close()

    def test_explicit_reopen_moves_a_terminal_object(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-reopen", objectKind=KEY)
            P.advance_stage(sdk, "deal-reopen", KEY, "stg_won",
                            at="2026-09-04", declared=DECL)
            move = P.advance_stage(sdk, "deal-reopen", KEY, "stg_discussion",
                                   at="2026-10-01", reopen=True,
                                   reason="deal revived", declared=DECL)
            assert move.applied
            cur = P.current_stage(g, "deal-reopen")
            assert cur["isClosed"] is False and cur["stageId"] == "stg_discussion"
        finally:
            sdk.close()

    def test_unknown_stage_is_captured_not_dropped_and_not_adopted(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-unknown", objectKind=KEY)
            move = P.advance_stage(sdk, "deal-unknown", KEY, "vendor diligence",
                                   at="2026-09-06", declared=DECL)
            assert not move.applied and move.reason == "unknown-stage"
            # Captured as a review candidate (raw label retained)...
            cands = P.unknown_stage_candidates(g, pipeline_key=KEY)
            assert [c["rawLabel"] for c in cands] == ["vendor diligence"]
            assert cands[0]["seenAt"] == "2026-09-06"
            # ...and NOT auto-adopted: it is not a stage of the pipeline.
            assert "vendor diligence" not in [
                s.label for s in P.list_stages(g, KEY, declared=DECL)]
            # The current state was not corrupted by the unrecognised label.
            assert P.current_stage(g, "deal-unknown")["stageId"] is None
        finally:
            sdk.close()

    def test_pending_candidate_is_cleared_by_a_later_valid_move(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-clear", objectKind=KEY)
            P.advance_stage(sdk, "deal-clear", KEY, "bogus", declared=DECL)
            assert P.unknown_stage_candidates(g, pipeline_key=KEY)
            P.advance_stage(sdk, "deal-clear", KEY, "stg_contact",
                            at="2026-09-07", declared=DECL)
            assert P.unknown_stage_candidates(g, pipeline_key=KEY) == []
            assert P.current_stage(g, "deal-clear")["pending"] is None
        finally:
            sdk.close()


# ── Isolation + durability ────────────────────────────────────────────────


class TestIsolationAndDurability:
    def test_pipeline_edits_in_one_graph_do_not_affect_another(self, tmp_path):
        a = _mk_sdk(tmp_path / "a")
        b = _mk_sdk(tmp_path / "b")
        try:
            P.rename_stage(a._get_proj().g, KEY, "stg_discussion", "Diligence",
                           declared=DECL)
            assert [s.label for s in
                    P.list_stages(a._get_proj().g, KEY, declared=DECL)] == [
                        "Contact", "Diligence", "Won"]
            assert [s.label for s in
                    P.list_stages(b._get_proj().g, KEY, declared=DECL)] == [
                        "Contact", "Discussion", "Won"]
        finally:
            a.close()
            b.close()

    def test_two_pipelines_sharing_stage_ids_do_not_collide(self, tmp_path):
        """A declared stage id is pack-local: two pipelines in one graph may
        both declare `stg_contact`, and each gets its own override."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            k1, k2 = "venture:deal", "venture:invest"
            P.rename_stage(g, k1, "stg_contact", "Contact (deal)", declared=DECL)
            P.rename_stage(g, k2, "stg_contact", "Contact (invest)", declared=DECL)
            assert P.list_stages(g, k1, declared=DECL)[0].label == "Contact (deal)"
            assert P.list_stages(g, k2, declared=DECL)[0].label == "Contact (invest)"
        finally:
            sdk.close()

    def test_pipeline_config_survives_a_full_rebuild(self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            P.seed_pipeline(g, KEY, DECL)
            P.rename_stage(g, KEY, "stg_discussion", "Diligence", declared=DECL)
            P.add_stage(g, KEY, stage_id="stg_ts", label="Term sheet",
                        slot="active", declared=DECL)
            sdk._get_proj().rebuild_all(str(tmp_path / "events"),
                                        confirm_destructive=True)
            stages = P.list_stages(g, KEY, declared=DECL)
            assert [s.stage_id for s in stages] == [
                "stg_contact", "stg_discussion", "stg_won", "stg_ts"]
            assert stages[1].label == "Diligence"
        finally:
            sdk.close()
