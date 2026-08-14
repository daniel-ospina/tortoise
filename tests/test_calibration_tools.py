"""Model-free tests for the calibration system (epic #909).

Every test here is DETERMINISTIC — no LLM calls. Covers: the S0 reference
mask, the distribution guards, stream schema validation, the metrics, the
source-event connector, the vocabulary discipline, and the criteria↔ontology
alignment.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS.parent))

import calibration_harness as ch  # noqa: E402
import probe_extractor as pe  # noqa: E402


# ── S0 reference mask ───────────────────────────────────────────────────────

class TestReferenceMask:
    def test_masks_pr_refs(self):
        assert pe._mask_references("PR #999 merged and PR 1016 pushed") == \
            "[REF] merged and [REF] pushed"

    def test_masks_issue_and_epic_refs(self):
        r = pe._mask_references("issue 953 and epic 909 and #1017")
        assert "[REF]" in r and r.count("[REF]") == 3

    def test_masks_versions(self):
        assert "v3.7" not in pe._mask_references("ontology v3.7")
        assert "[REF]" == pe._mask_references("v3.7")

    def test_keeps_normal_text(self):
        t = "the state-centric model stores state, points and events"
        assert pe._mask_references(t) == t

    def test_transcript_mask_integration(self):
        tr = "0: user: see PR #999 and issue 1013\n1: assistant: ok\n"
        edus = pe.parse_transcript(tr)
        assert "[REF]" in edus[0]["text"]  # user EDU masked
        assert "ok" == edus[1]["text"]     # assistant EDU untouched


# ── Distribution guards ─────────────────────────────────────────────────────

class TestGuards:
    def test_eventkind_collapse_detected(self):
        stream = {"events": [{"eventKind": "occurrence"} for _ in range(10)],
                  "claims": []}
        assert any("collapse" in w for w in ch.check_distribution_guards(stream))

    def test_missing_relations_detected(self):
        stream = {"events": [], "claims": [{"id": f"c{i}"} for i in range(6)],
                  "relations": []}
        assert any("relations" in w for w in ch.check_distribution_guards(stream))

    def test_healthy_stream_no_warnings(self):
        stream = {"events": [{"eventKind": "occurrence"},
                             {"eventKind": "decision"},
                             {"eventKind": "review"}],
                  "claims": [{"id": "c1"}, {"id": "c2"}],
                  "relations": [{"src": "c1", "dst": "c2", "op_type": "IMPL"}]}
        assert ch.check_distribution_guards(stream) == []


# ── Stream schema validation ────────────────────────────────────────────────

class TestValidateStream:
    def test_ok_stream_passes(self):
        stream = {
            "events": [{"id": "e1", "eventKind": "occurrence"}],
            "claims": [{"id": "c1", "kind": "statement", "quote": "q",
                        "source_ref": "s1"}],
            "entities": [{"name": "X", "kind": "core:concept"}],
            "relations": [{"src": "c1", "target_edge": {"src": "c1", "dst": "c2",
                          "op_type": "IMPL"}, "op_type": "MITIGATES",
                          "strength": 0.3}],
        }
        assert ch.validate_stream(stream) == []

    def test_minted_kind_flagged(self):
        stream = {"events": [], "claims": [],
                  "entities": [{"name": "X", "kind": "madeup:kind"}]}
        assert any("minted" in e for e in ch.validate_stream(stream))

    def test_bad_eventkind_flagged(self):
        stream = {"events": [{"id": "e1", "eventKind": "banana"}], "claims": []}
        assert any("eventKind" in e for e in ch.validate_stream(stream))

    def test_claim_kind_must_be_statement(self):
        stream = {"events": [], "claims": [{"id": "c1", "kind": "decision"}],
                  "entities": []}
        assert any("statement" in e for e in ch.validate_stream(stream))

    def test_mitigates_must_target_impl_edge(self):
        stream = {"events": [], "claims": [],
                  "relations": [{"src": "c1", "target_edge": {"src": "c1",
                                "dst": "c2", "op_type": "NAND"},
                                "op_type": "MITIGATES", "strength": 0.3}]}
        assert any("MITIGATES" in e for e in ch.validate_stream(stream))

    def test_mitigates_strength_band(self):
        stream = {"events": [], "claims": [],
                  "relations": [{"src": "c1", "target_edge": {"src": "c1",
                                "dst": "c2", "op_type": "IMPL"},
                                "op_type": "MITIGATES", "strength": 0.99}]}
        assert any("strength" in e for e in ch.validate_stream(stream))

    def test_missing_source_ref_flagged(self):
        stream = {"events": [], "claims": [{"id": "c1", "kind": "statement"}],
                  "entities": []}
        assert any("source_ref" in e for e in ch.validate_stream(stream))


# ── Metrics ─────────────────────────────────────────────────────────────────

class TestMetrics:
    def test_counts(self):
        d = {"events": [{"eventKind": "decision"}, {"eventKind": "occurrence"}],
             "claims": [{"id": "c1"}], "process": [{"id": "p1"}],
             "entities": [{"name": "X", "kind": "core:concept"},
                          {"name": "Y", "kind": "madeup:k"}],
             "relations": [{"src": "c1", "target_edge": {"src": "c1",
                           "dst": "c2", "op_type": "IMPL"},
                           "op_type": "MITIGATES", "strength": 0.3}]}
        m = ch.metrics(d)
        assert m["events"] == 2 and m["claims"] == 1 and m["process"] == 1
        assert m["decEv"] == 1 and m["minted"] == 1 and m["mitigates"] == 1
        assert m["badEvk"] == 0


# ── Source-event connector ──────────────────────────────────────────────────

class TestSourceEvents:
    def test_merged_pr_is_deployment_event(self):
        pr = {"number": 1016, "title": "docs: readme", "state": "closed",
              "merged_at": "2026-08-12T23:10:29Z", "closed_at": "2026-08-12T23:11:00Z",
              "html_url": "https://github.com/x/y/pull/1016"}
        events = ch.source_events_from_pr(pr)
        assert len(events) == 1
        assert events[0]["eventKind"] == "deployment"
        assert events[0]["capturedAt"] == "2026-08-12T23:10:29Z"
        assert events[0]["confidence"] == 1.0

    def test_open_pr_is_review_event(self):
        pr = {"number": 1, "title": "feat: x", "state": "open",
              "html_url": "u"}
        events = ch.source_events_from_pr(pr)
        assert events[0]["eventKind"] == "review"

    def test_closed_unmerged_is_occurrence(self):
        pr = {"number": 2, "title": "x", "state": "closed", "merged_at": None,
              "closed_at": "2026-08-01T00:00:00Z", "html_url": "u"}
        events = ch.source_events_from_pr(pr)
        assert events[0]["eventKind"] == "occurrence"

    def test_connection_name_resolution(self):
        events = [{"id": "e1", "content": "PR #1016 merged: state-centric memory"}]
        items = {"entities": [
            {"name": "state-centric memory", "kind": "core:concept"},
            {"name": "pricing", "kind": "core:concept"}]}
        conns = ch.connect_events_to_items(events, items)
        assert conns[0]["connects_to"] == ["state-centric memory"]


# ── Criteria ↔ ontology alignment (model-free, deterministic) ───────────────

class TestOntologyAlignment:
    def test_probe_vocab_is_subset_of_packs_and_core(self):
        """Every kind the probe emits must exist in the ontology/packs."""
        from tortoise.pack_registry import PackRegistry
        reg = PackRegistry(Path(__file__).resolve().parent.parent / "packs")
        reg.load_all()
        pack_kinds = set()
        import yaml as _y
        ns_files = {}
        for mf in (Path(__file__).resolve().parent.parent / "packs").glob("*/manifest.yaml"):
            d = _y.safe_load(mf.read_text())
            if d.get("namespace"):
                ns_files[d["namespace"]] = mf
        for ns, pack in reg.packs.items():
            raw = _y.safe_load(ns_files[ns].read_text())
            ont = raw.get("ontology", {})
            for k in ont.get("objectKinds", []) + ont.get("pointKinds", []):
                pack_kinds.add(f"{ns}:{k}")
        core = {"core:concept", "core:standard", "core:other", "core:WorkItem",
                "core:document", "core:tool", "core:workflow", "core:Project",
                "core:tag", "core:user", "core:skill", "core:agent",
                "core:agreement", "core:strategy", "core:plan", "core:goal",
                "core:target"}
        for kind in ch.VOCAB:
            assert kind in core or kind in pack_kinds, f"{kind} not in ontology/packs"

    def test_eventkinds_match_ontology(self):
        """The probe's eventKind set matches ONTOLOGY §5."""
        ont = (Path(__file__).resolve().parent.parent / "docs" / "ONTOLOGY.md")
        text = ont.read_text()
        for k in ch.EVK:
            assert k in text, f"eventKind {k} not in ONTOLOGY.md"

    def test_no_decision_points_in_schema(self):
        """The state-centric stream has NO decisions[] array (no decision
        Points — decisions are events)."""
        assert "decisions" not in (pe.EXTRACTION_SYSTEM.split("Output schema")[1]
                                   if "Output schema" in pe.EXTRACTION_SYSTEM else "")

    def test_observation_not_a_claim_kind(self):
        assert "observation" not in (pe.EXTRACTION_SYSTEM.split("Output schema")[1]
                                     if "Output schema" in pe.EXTRACTION_SYSTEM else "")

    def test_staged_pass1_carries_vocab_and_no_relations(self):
        p1 = ch._staged_pass1(pe.EXTRACTION_SYSTEM)
        assert "product-strategy" in p1          # vocab carried
        assert "DO NOT extract relations" in p1  # relations excluded
