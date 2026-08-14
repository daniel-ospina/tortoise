"""Tests for the production two-process extractor (no LLM — deterministic)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.value_extractor import (  # noqa: E402
    compile_value_brief, validate_summary, check_guards, _mask_refs,
)
from tortoise.sdk import _summary_to_payload  # noqa: E402


class TestValueBrief:
    def test_compiles_kinds(self):
        brief = compile_value_brief()
        assert "product-strategy:product" in brief
        assert "core:concept" in brief
        assert len(brief) >= 20

    def test_kinds_have_semantics(self):
        brief = compile_value_brief()
        assert brief["product-strategy:useCase"]["nearMisses"]


class TestReferenceMask:
    def test_masks_refs(self):
        assert _mask_refs("see PR #999 and issue 1013") == "see [REF] and [REF]"

    def test_keeps_text(self):
        assert _mask_refs("the graph stores state") == "the graph stores state"


class TestValidateSummary:
    def test_missing_why(self):
        errs = validate_summary({"decisions": [{"content": "X"}]})
        assert any("why" in e for e in errs)

    def test_missing_sources(self):
        errs = validate_summary({"logic": [{"point": "P"}]})
        assert any("sources" in e for e in errs)

    def test_clean_passes(self):
        s = {"decisions": [{"content": "X", "why": "y"}],
             "state": [{"name": "A", "objectKind": "core:concept"}],
             "logic": [{"point": "P", "sources": [1]}]}
        assert validate_summary(s) == []


class TestGuards:
    def test_empty_summary_warns(self):
        assert check_guards({"state": [], "decisions": [], "logic": []})

    def test_decisions_without_logic(self):
        assert check_guards({"state": [], "decisions": [{"content": "X"}],
                             "logic": []})


class TestSummaryToPayload:
    def test_shape(self):
        s = {
            "session": {"summary": "S", "type": "design"},
            "state": [{"name": "ontology", "objectKind": "core:document",
                       "status": "changed"}],
            "decisions": [{"content": "remove observation", "options": ["obs"]}],
            "logic": [{"point": "observation is vague", "sources": [36]}],
            "issues": [{"id": "issue-1013", "status": "created"}],
        }
        p = _summary_to_payload(s, "s1")
        assert p["summary"] == "S"
        assert p["entities"][0]["name"] == "ontology"
        assert p["events"][0]["eventKind"] == "decision"
        assert p["events"][0]["about_entities"] == ["obs"]
        assert p["points"][0]["pointKind"] == "statement"
        assert any(e["eventKind"] == "occurrence" for e in p["events"])
        assert p["schema_version"] == "1"

    def test_replay_safe_id(self):
        from tortoise.sdk import _post_commit  # noqa: F401 (importable)
        from tortoise.commit_schema import compute_client_commit_id
        s = {"session": {"summary": "S"}, "state": [], "decisions": [],
             "logic": [], "issues": []}
        p = _summary_to_payload(s, "s1")
        cid = compute_client_commit_id(p["session_id"], p["points"],
                                       p["entities"], p["operators"],
                                       p["summary"], p["story_arc"],
                                       p.get("events", []))
        assert cid  # deterministic id computable (endpoint recomputes it)
