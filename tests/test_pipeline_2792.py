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
``config/ci-surfaces.yml`` (``core`` + ``slow_files`` + ``carve_out``), in
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

    def test_duplicate_labels_are_refused_in_a_declaration(self):
        bad = {"stages": [
            {"id": "a", "label": "Alpha", "order": 1, "slot": "entry"},
            {"id": "b", "label": "alpha", "order": 2, "slot": "active"},
        ]}
        errs = P.validate_pipeline_decl(bad)
        assert any("duplicate stage label" in e for e in errs), errs

    def test_a_stage_id_containing_the_key_separator_is_refused(self):
        """``stageKey`` is ``pipeline|stage``. An id carrying ``|`` made the
        composite ambiguous, so two distinct (pipeline, id) pairs collided on
        one node and the second write silently removed the first pipeline's
        stage. The declaration must refuse the separator."""
        bad = {"stages": [
            {"id": "a|b", "label": "Alpha", "order": 1, "slot": "entry"},
        ]}
        errs = P.validate_pipeline_decl(bad)
        assert any("must not contain" in e for e in errs), errs

    def test_tied_stage_orders_are_refused_in_a_declaration(self):
        """The forward/regression judgement is ``target.order <
        cur_stage.order``, so a move BETWEEN two tied stages is neither
        forward nor backward and was applied with no guard at all.
        ``reorder_stages`` refuses ties for exactly that reason; the
        pack-declaration path (the only path a graph that never edits its
        pipeline takes) must refuse them too."""
        tie = {"stages": [
            {"id": "a", "label": "A", "order": 1, "slot": "entry"},
            {"id": "b", "label": "B", "order": 1, "slot": "active"},
        ]}
        errs = P.validate_pipeline_decl(tie)
        assert any("duplicate stage order" in e for e in errs), errs
        # A strict order is still valid, and validation must not have been
        # made vacuous by the new check.
        assert P.validate_pipeline_decl(DECL) == []


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

    def test_a_graph_override_is_editable_without_the_pack_declaration(
            self, tmp_path):
        """An override the graph already owns is editable even when the pack
        cannot be resolved (unloaded/renamed) — only CREATING one needs the
        declaration. Pre-fix every mutator refused, misstating the reason."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            P.seed_pipeline(g, KEY, DECL)
            P.rename_stage(g, KEY, "stg_discussion", "Diligence", declared=None)
            assert [s.label for s in P.list_stages(g, KEY, declared=None)] == [
                "Contact", "Diligence", "Won"]
            P.add_stage(g, KEY, stage_id="stg_x", label="X", slot="active",
                        declared=None)
            assert [s.stage_id for s in P.list_stages(g, KEY, declared=None)] == [
                "stg_contact", "stg_discussion", "stg_won", "stg_x"]
        finally:
            sdk.close()

    def test_editing_a_pipeline_that_neither_declares_nor_overrides_is_refused(
            self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            with pytest.raises(ValueError, match="nothing to edit"):
                P.rename_stage(g, "venture:none", "stg_x", "Y", declared=None)
        finally:
            sdk.close()

    def test_rename_stage_returns_the_updated_label(self, tmp_path):
        """The return value is the POST-write state (as ``add_stage`` and
        ``retire_stage`` already were). Pre-fix it returned the pipeline read
        before the write, so the caller saw the OLD label while
        ``list_stages`` reported the new one."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            returned = P.rename_stage(g, KEY, "stg_discussion", "Diligence",
                                      declared=DECL)
            assert returned.label == "Diligence"
            assert [s.label for s in P.list_stages(g, KEY, declared=DECL)] == [
                "Contact", "Diligence", "Won"]
        finally:
            sdk.close()

    def test_add_stage_refuses_an_id_containing_the_key_separator(
            self, tmp_path):
        """A graph-created stage id carries the same constraint as a declared
        one: without it, stage ``a|b`` under ``venture:deal`` and stage ``b``
        under ``venture:deal|a`` resolved to one ``stageKey``, and the second
        write silently removed the first pipeline's stage."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            P.seed_pipeline(g, KEY, DECL)
            with pytest.raises(ValueError, match="must not contain"):
                P.add_stage(g, KEY, stage_id="a|b", label="A B",
                            slot="active", declared=DECL)
            assert [s.stage_id for s in P.list_stages(g, KEY, declared=DECL)] == [
                "stg_contact", "stg_discussion", "stg_won"]
        finally:
            sdk.close()

    def test_add_stage_refuses_a_tied_or_negative_explicit_order(self, tmp_path):
        """An explicit ``order`` carries the same constraint as a declared one.

        The forward/regression judgement is ``target.order < cur_stage.order``,
        so a tie makes a move between the two stages neither forward nor
        backward and it is applied with NO guard at all — the shape
        ``validate_pipeline_decl`` and ``reorder_stages`` both refuse. The
        tenant EDIT path's explicit ``order`` accepted it, re-creating the tie
        the declaration path had just been hardened against.
        """
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            P.seed_pipeline(g, KEY, DECL)  # orders 1, 2, 3
            with pytest.raises(ValueError, match="already used"):
                P.add_stage(g, KEY, stage_id="stg_x", label="Xs",
                            slot="active", order=2, declared=DECL)
            with pytest.raises(ValueError, match="int >= 0"):
                P.add_stage(g, KEY, stage_id="stg_neg", label="Neg",
                            slot="active", order=-5, declared=DECL)
            assert [s.stage_id for s in P.list_stages(g, KEY, declared=DECL)] == [
                "stg_contact", "stg_discussion", "stg_won"]
            # A free explicit order is still accepted, so the check did not
            # simply disable the parameter.
            added = P.add_stage(g, KEY, stage_id="stg_x", label="Xs",
                                slot="active", order=4, declared=DECL)
            assert added.order == 4
            assert [s.order for s in P.list_stages(g, KEY, declared=DECL)] == [
                1, 2, 3, 4]
        finally:
            sdk.close()

    def test_override_stages_are_read_in_declared_order_not_row_order(
            self, tmp_path):
        """``_read_override`` orders its rows, so the read is deterministic
        and a resolver cannot depend on the store's row yield order."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            P.seed_pipeline(g, KEY, DECL)
            P.reorder_stages(g, KEY,
                             ["stg_won", "stg_discussion", "stg_contact"],
                             declared=DECL)
            assert [s.stage_id for s in
                    P.effective_pipeline(g, KEY, declared=DECL).stages] == [
                        "stg_won", "stg_discussion", "stg_contact"]
        finally:
            sdk.close()

    def test_a_refused_edit_does_not_materialise_the_graph_override(
            self, tmp_path):
        """Copy-on-write seeds the override — a durable write (``:Pipeline`` +
        every ``:PipelineStage``, enrolled in #2814's config registry). The seed
        used to happen at the START of every mutator (inside
        ``_require_override``), before the edit was validated, so a call that
        RAISED still flipped the graph to ``source="graph-override"`` forever:
        ``effective_pipeline`` then short-circuited on it and a later pack
        release no longer reached the graph. Validate against a side-effect-free
        read; seed only a legal edit.
        """
        cases = {
            "rename_unknown_stage": lambda g: P.rename_stage(
                g, KEY, "stg_nope", "X", declared=DECL),
            "retire_the_default_stage": lambda g: P.retire_stage(
                g, KEY, "stg_contact", declared=DECL),
            "add_a_duplicate_label": lambda g: P.add_stage(
                g, KEY, stage_id="stg_x", label="Won", slot="active",
                declared=DECL),
            "add_a_tied_order": lambda g: P.add_stage(
                g, KEY, stage_id="stg_y", label="Y", slot="active", order=2,
                declared=DECL),
            "reorder_partially": lambda g: P.reorder_stages(
                g, KEY, ["stg_contact"], declared=DECL),
            "set_an_unknown_default": lambda g: P.set_default_stage(
                g, KEY, "stg_nope", declared=DECL),
        }
        for name, edit in cases.items():
            sdk = _mk_sdk(tmp_path / name)
            try:
                g = sdk._get_proj().g
                assert P.effective_pipeline(
                    g, KEY, declared=DECL).source == "pack-default"
                with pytest.raises(ValueError):
                    edit(g)
                assert P.effective_pipeline(
                    g, KEY, declared=DECL).source == "pack-default", name
                assert g.query(
                    "MATCH (p:Pipeline {pipelineKey:$k}) RETURN p.pipelineKey",
                    params={"k": KEY}).result_set == [], name
                assert g.query(
                    "MATCH (s:PipelineStage {pipelineKey:$k}) "
                    "RETURN s.stageKey",
                    params={"k": KEY}).result_set == [], name
            finally:
                sdk.close()

    def test_a_padded_label_is_stored_trimmed_and_resolvable(self, tmp_path):
        """A padded label passed every uniqueness check (compared stripped)
        but was stored verbatim, and ``resolve`` trims only the PROBE — so the
        stored label could not be resolved by its natural spelling.
        ``advance_stage`` then parked a stage that EXISTS as an "unknown stage"
        review candidate and left the Object's stage unchanged. Canonicalise on
        write so the resolver, the label owner and the validator agree."""
        padded = {
            "defaultStage": "stg_contact",
            "stages": [
                {"id": "stg_contact", "label": " Contact ", "order": 1,
                 "slot": "entry"},
                {"id": "stg_won", "label": "Won", "order": 2,
                 "slot": "terminal", "outcome": "won"},
            ],
        }
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            assert P.validate_pipeline_decl(padded) == []
            # The pack-default tier (no override yet) canonicalises too.
            assert [s.label for s in P.list_stages(g, KEY, declared=padded)] == [
                "Contact", "Won"]
            added = P.add_stage(g, KEY, stage_id="stg_x",
                                label="  Diligence  ", slot="active",
                                declared=padded)
            assert added.label == "Diligence"
            renamed = P.rename_stage(g, KEY, "stg_won", "  Closed  ",
                                     declared=padded)
            assert renamed.label == "Closed"
            pipe = P.effective_pipeline(g, KEY, declared=padded)
            assert pipe.resolve("Diligence").stage_id == "stg_x"
            assert pipe.resolve("Closed").stage_id == "stg_won"
            assert pipe.resolve("  Diligence  ").stage_id == "stg_x"
            # The consequence the padding used to cause is gone: a real stage is
            # applied, not parked as an unknown candidate.
            sdk.create_object("deal-pad", objectKind=KEY)
            move = P.advance_stage(sdk, "deal-pad", KEY, "Diligence",
                                   at="2026-09-02", declared=padded)
            assert move.applied and move.stage.stage_id == "stg_x", move
            assert P.unknown_stage_candidates(g, pipeline_key=KEY) == []
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

    def test_pipeline_key_must_match_the_objects_namespaced_kind(self, tmp_path):
        """A pipeline keys on the namespaced kind (#2767), so no Object can be
        advanced through a pipeline that is not its own."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("client-x", objectKind="core:client")
            with pytest.raises(ValueError, match="does not apply"):
                P.advance_stage(sdk, "client-x", KEY, "stg_won", declared=DECL)
            assert P.current_stage(g, "client-x")["stageId"] is None
        finally:
            sdk.close()

    def test_an_object_without_a_namespaced_kind_cannot_be_advanced(
            self, tmp_path):
        """The kind binding is not conditional. An Object carrying no
        ``objectKind`` (reachable via raw Cypher / a legacy import) cannot bind
        ANY pipeline; pre-fix the guard was ``if kind and kind != ...``, so
        the binding was skipped entirely and the Object was advanced and then
        permanently stamped with that pipeline's key."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            g.query("CREATE (o:Object {id:'nokind', name:'nokind', "
                    "status:'live'})")
            with pytest.raises(ValueError, match="no namespaced objectKind"):
                P.advance_stage(sdk, "nokind", KEY, "stg_won", declared=DECL)
            cur = P.current_stage(g, "nokind")
            assert cur["stageId"] is None and cur["pipelineKey"] is None
        finally:
            sdk.close()

    def test_a_null_stage_reference_is_a_no_op_not_a_candidate_named_none(
            self, tmp_path):
        """An absent stage reference is not an unrecognised LABEL: it must not
        park a review candidate literally named ``None``."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-null", objectKind=KEY)
            for absent in (None, "", "   ", 7):
                move = P.advance_stage(sdk, "deal-null", KEY, absent,
                                       declared=DECL)
                assert not move.applied and move.reason == "no-stage"
                assert move.pending is None
            assert P.unknown_stage_candidates(g, pipeline_key=KEY) == []
            assert P.current_stage(g, "deal-null")["stageId"] is None
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


# ── Guard integrity (the P1 resurrection vector) ──────────────────────────


class TestGuardIntegrity:
    def test_a_current_stage_missing_from_the_declaration_cannot_disarm_the_guards(
            self, tmp_path):
        """Declaration drift must fail closed, not skip the guards.

        An Object sits in a stage that resolves in the declaration it was
        advanced against. A pack release renames/drops that stage id, so the
        stored ``pipelineStageId`` no longer resolves in the effective
        pipeline. Pre-fix ``cur_stage`` was then ``None`` and the terminal,
        regression and stale-time guards were ALL skipped, so a passing later
        mention reopened a CLOSED Object (and rewrote ``isClosed=False``,
        journaled durably). The move must be refused with the state untouched.
        """
        renamed = {
            "defaultStage": "stg_contact",
            "stages": [
                {"id": "stg_contact", "label": "Contact", "order": 1,
                 "slot": "entry"},
                {"id": "stg_closed", "label": "Closed", "order": 3,
                 "slot": "terminal", "outcome": "won"},
            ],
        }
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-drift", objectKind=KEY)
            P.advance_stage(sdk, "deal-drift", KEY, "stg_won", at="2026-09-04",
                            declared=DECL)
            assert P.current_stage(g, "deal-drift")["isClosed"] is True
            # Control: the same call with the SAME declaration fires the guard.
            control = P.advance_stage(sdk, "deal-drift", KEY, "stg_contact",
                                      at="2026-10-01", declared=DECL)
            assert not control.applied
            assert control.reason == "terminal-guard"
            # Drifted declaration: refuse, never resurrect.
            move = P.advance_stage(sdk, "deal-drift", KEY, "stg_contact",
                                   at="2026-10-01", declared=renamed)
            assert not move.applied, move
            assert move.reason == "unresolvable-current-stage"
            cur = P.current_stage(g, "deal-drift")
            assert cur["stageId"] == "stg_won"
            assert cur["isClosed"] is True
        finally:
            sdk.close()

    def test_a_slot_reclassification_cannot_resurrect_a_closed_object(
            self, tmp_path):
        """Stickiness keys on the Object's durable ``isClosed`` marker, not on
        the effective declaration's live ``slot``.

        A graph that never overrides its pipeline reads the pack default on
        the fly, so a pack release that reclassifies the stage the Object sits
        in from ``terminal`` to an active slot takes effect immediately. Keying
        the terminal guard off the resolved slot alone then skipped it, and the
        next mention rewrote ``isClosed=True`` to ``False`` — durable,
        journaled, and exactly the resurrection the guard exists to stop.
        """
        reclassified = {
            "defaultStage": "stg_contact",
            "stages": [
                {"id": "stg_contact", "label": "Contact", "order": 1,
                 "slot": "entry"},
                {"id": "stg_won", "label": "Won", "order": 3,
                 "slot": "active"},
                {"id": "stg_post", "label": "Post", "order": 4,
                 "slot": "active"},
            ],
        }
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-slot-drift", objectKind=KEY)
            P.advance_stage(sdk, "deal-slot-drift", KEY, "stg_won",
                            at="2026-09-04", declared=DECL)
            assert P.current_stage(g, "deal-slot-drift")["isClosed"] is True
            move = P.advance_stage(sdk, "deal-slot-drift", KEY, "stg_post",
                                   at="2026-10-01", declared=reclassified)
            assert not move.applied and move.reason == "terminal-guard", move
            cur = P.current_stage(g, "deal-slot-drift")
            assert cur["stageId"] == "stg_won" and cur["isClosed"] is True
            # The explicit reopen still moves it: the guard is a gate, not a
            # dead end.
            revived = P.advance_stage(sdk, "deal-slot-drift", KEY, "stg_post",
                                      at="2026-11-01", reopen=True,
                                      reason="deal revived",
                                      declared=reclassified)
            assert revived.applied, revived
            assert P.current_stage(g, "deal-slot-drift")["isClosed"] is False
        finally:
            sdk.close()

    def test_reopen_does_not_bypass_the_regression_guard_on_a_live_object(
            self, tmp_path):
        """``reopen`` is the TERMINAL guard's escape hatch, not a general
        regression opt-out.

        Pre-fix the regression condition read ``not correction and not
        reopen``, so an explicit ``reopen=True`` silently disabled the
        backwards-move guard on an Object that was never closed, where
        "reopen" has no meaning at all.
        """
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-live-reopen", objectKind=KEY)
            P.advance_stage(sdk, "deal-live-reopen", KEY, "stg_discussion",
                            at="2026-09-02", declared=DECL)
            move = P.advance_stage(sdk, "deal-live-reopen", KEY, "stg_contact",
                                   at="2026-09-03", reopen=True, declared=DECL)
            assert not move.applied and move.reason == "regression-guard", move
            assert P.current_stage(g, "deal-live-reopen")["stageId"] == (
                "stg_discussion")
            # ``correction`` remains the documented backward-move opt-out.
            fixed = P.advance_stage(sdk, "deal-live-reopen", KEY, "stg_contact",
                                    at="2026-09-04", correction=True,
                                    declared=DECL)
            assert fixed.applied, fixed
        finally:
            sdk.close()

    def test_stray_unknown_for_another_pipeline_cannot_disarm_the_guards(
            self, tmp_path):
        """The park must not rewrite the Object's ``pipelineStageKey``.

        Pre-fix, one unknown-label observation carrying a different pipeline
        key overwrote the Object's real key, so every later call for the
        Object's real pipeline saw no current stage and skipped the terminal /
        regression / stale-time guards — a closed Object was resurrected.
        """
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-arm", objectKind=KEY)
            P.advance_stage(sdk, "deal-arm", KEY, "stg_won",
                            at="2026-09-04", declared=DECL)
            other_key = "venture:invest"
            # The stray observation is refused (kind binding) ...
            with pytest.raises(ValueError, match="does not apply"):
                P.advance_stage(sdk, "deal-arm", other_key, "some other stage",
                                at="2026-09-05", declared=DECL)
            # ... and the guards still fire for the Object's real pipeline.
            move = P.advance_stage(sdk, "deal-arm", KEY, "stg_contact",
                                   at="2026-10-01", declared=DECL)
            assert not move.applied and move.reason == "terminal-guard"
            cur = P.current_stage(g, "deal-arm")
            assert cur["stageId"] == "stg_won" and cur["isClosed"] is True
            assert cur["pipelineKey"] == KEY
        finally:
            sdk.close()

    def test_a_park_reports_the_label_without_touching_the_pipeline_key(
            self, tmp_path):
        """The park's write is the pending PAIR only — pinned directly, since
        with kind binding active the different-key call above is refused and
        this rewrite is the residual vector."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-park", objectKind=KEY)
            calls: list[dict] = []
            real = sdk.update_entity

            def _spy(oid, **kw):
                calls.append(kw)
                return real(oid, **kw)

            sdk.update_entity = _spy
            P.advance_stage(sdk, "deal-park", KEY, "vendor diligence",
                            at="2026-09-06", declared=DECL)
            assert calls, "the park must journal through the generic write"
            assert "pipelineStageKey" not in calls[0]
            assert calls[0]["pipelineStagePendingKey"] == KEY
            assert calls[0]["pipelineStagePending"] == "vendor diligence"
            assert P.unknown_stage_candidates(g, pipeline_key=KEY)[0][
                "rawLabel"] == "vendor diligence"
        finally:
            sdk.close()

    def test_duplicate_labels_are_refused_so_a_label_resolves_to_one_stage(
            self, tmp_path):
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            P.seed_pipeline(g, KEY, DECL)
            with pytest.raises(ValueError, match="already used"):
                P.rename_stage(g, KEY, "stg_won", "Discussion", declared=DECL)
            with pytest.raises(ValueError, match="already used"):
                P.add_stage(g, KEY, stage_id="stg_dup", label="discussion",
                            slot="active", declared=DECL)
            assert P.effective_pipeline(g, KEY, declared=DECL).resolve(
                "Discussion").stage_id == "stg_discussion"
        finally:
            sdk.close()

    def test_an_ambiguous_object_name_is_refused_not_silently_picked(
            self, tmp_path):
        """Two live Objects may share a name (#3590 D2). The hand-rolled
        ``name = $n OR id = $n ... LIMIT 1`` silently picked one carrier, so
        the read could answer from the wrong Object and a stage could be
        journaled onto it; both sites must route-then-refuse."""
        from tortoise.entity_identity import AmbiguousEntityName

        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            for oid in ("dup-a", "dup-b"):
                g.query(
                    "CREATE (o:Object {id:$id, name:'dup-deal', "
                    "objectKind:$k, status:'live'})",
                    params={"id": oid, "k": KEY})
            with pytest.raises(AmbiguousEntityName):
                P.current_stage(g, "dup-deal")
            with pytest.raises(AmbiguousEntityName):
                P.advance_stage(sdk, "dup-deal", KEY, "stg_discussion",
                                declared=DECL)
            # An exact id is unambiguous and still resolves.
            assert P.current_stage(g, "dup-a")["name"] == "dup-deal"
        finally:
            sdk.close()

    def test_current_stage_reads_every_prop_the_fold_maintains(self, tmp_path):
        """``STAGE_PROPS`` is load-bearing: the read covers the write."""
        assert set(P.STAGE_PROPS) == set(P._STAGE_READ_KEYS)
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-props", objectKind=KEY)
            written: dict = {}
            real = sdk.update_entity

            def _spy(oid, **kw):
                written.update(kw)
                return real(oid, **kw)

            sdk.update_entity = _spy
            P.advance_stage(sdk, "deal-props", KEY, "stg_discussion",
                            declared=DECL)
            cur = P.current_stage(g, "deal-props")
            for prop, key in P._STAGE_READ_KEYS.items():
                assert prop in written, f"the write omits {prop}"
                assert key in cur, f"the read omits {key}"
        finally:
            sdk.close()

    def test_a_closed_object_with_no_stage_is_not_resurrected(self, tmp_path):
        """``isClosed`` is the Object's durable closed marker, written through
        the generic entity surface and INDEPENDENT of the stage. The terminal
        guard used to live inside ``if cur_stage is not None``, so an Object the
        graph records as closed with NO current stage was reopened by an
        ordinary later mention (``isClosed=True`` -> ``False``, journaled,
        replayed by rebuild). Fail closed."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            node = sdk.create_object("legacy-closed", objectKind=KEY)
            sdk.update_entity(node["id"], isClosed=True)
            cur = P.current_stage(g, "legacy-closed")
            assert cur["isClosed"] is True and cur["stageId"] is None, cur
            move = P.advance_stage(sdk, "legacy-closed", KEY, "stg_discussion",
                                   at="2026-10-01", declared=DECL)
            assert not move.applied and move.reason == "terminal-guard", move
            after = P.current_stage(g, "legacy-closed")
            assert after["isClosed"] is True and after["stageId"] is None
            # The explicit reopen still moves it — a gate, not a dead end.
            revived = P.advance_stage(sdk, "legacy-closed", KEY,
                                      "stg_discussion", at="2026-11-01",
                                      reopen=True, reason="deal revived",
                                      declared=DECL)
            assert revived.applied, revived
            assert P.current_stage(g, "legacy-closed")["isClosed"] is False
        finally:
            sdk.close()

    def test_reorder_stages_cannot_leave_a_tie_with_a_retired_stage(
            self, tmp_path):
        """``retire_stage`` keeps the retired row (and the Objects in it), so
        renumbering only the ACTIVE block from 1 could collide with a retired
        stage's order — re-creating the tie ``validate_pipeline_decl`` and
        ``add_stage`` both refuse, and silently disabling the regression guard
        (``target.order < cur_stage.order``) for the tied pair. The renumber
        must produce a total order."""
        sdk = _mk_sdk(tmp_path)
        try:
            g = sdk._get_proj().g
            sdk.create_object("deal-tie", objectKind=KEY)
            P.seed_pipeline(g, KEY, DECL)
            P.advance_stage(sdk, "deal-tie", KEY, "stg_discussion",
                            at="2026-09-02", declared=DECL)
            # Retire the stage the Object sits in (allowed and flagged), then
            # reorder the survivors. The active renumber from 1 produced
            # stg_won=1 / stg_contact=2 — tying with the retired
            # stg_discussion=2.
            P.retire_stage(g, KEY, "stg_discussion", declared=DECL)
            P.reorder_stages(g, KEY, ["stg_won", "stg_contact"], declared=DECL)
            stages = P.list_stages(g, KEY, declared=DECL, include_retired=True)
            orders = [s.order for s in stages]
            assert len(orders) == len(set(orders)), orders
            by_id = {s.stage_id: s for s in stages}
            # The Object still sits in the retired stage, which keeps its own
            # position rather than being pushed behind the active block.
            assert by_id["stg_discussion"].status == P.STAGE_RETIRED
            cur_order = by_id["stg_discussion"].order
            earlier = [s for s in stages if s.status == P.STAGE_ACTIVE
                       and s.order < cur_order]
            assert earlier, "the fixture must exercise the guard"
            move = P.advance_stage(sdk, "deal-tie", KEY, earlier[0].stage_id,
                                   at="2026-09-03", declared=DECL)
            assert not move.applied and move.reason == "regression-guard", move
            # And the mutators agree: the state reorder produced is one
            # add_stage refuses to duplicate.
            with pytest.raises(ValueError, match="already used"):
                P.add_stage(g, KEY, stage_id="stg_z", label="Z", slot="active",
                            order=cur_order, declared=DECL)
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
