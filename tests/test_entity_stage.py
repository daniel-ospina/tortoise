"""Phase-2 entity stage unit tests (issue #782 / epic #264 plan §5-§7).

Covers the extractor.py surface: EntityStage (extends _SemanticStage with span
+ canonical_candidates), EntityStageMock deterministic fixture, rule/keyword
fallback (DE2E-N2), unknown objectKind → 'other' (DE2E-N7), and the renamed
extract_conversation_entities API coexisting with S7 extract_entities
(DE2E-N12). No LLM, no network — all deterministic fixtures.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI          # noqa: E402
from tortoise.extractor import (           # noqa: E402
    LLMExtractor,
    MockModel,
    EntityStage,
    EntityStageMock,
    entity_stage_fixture,
    extract_conversation_entities,
    _rule_fallback_entities,
)
from tortoise.log import EventLog          # noqa: E402


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_estage_"), name)


def _api():
    log = EventLog(_tmp("events.jsonl"))
    return EventAPI(log, initiated_by="extractor", agent_id="test"), log


TRANSCRIPT = (
    "Alice: We decided to move the FalkorDB default port to 16379.\n"
    "Bob: I disagree because changing port 16379 breaks the redis config.\n"
    "Alice: But tortoise#123 tracks the migration work.\n"
)


# ── EntityStageMock determinism ────────────────────────────────────

def test_entity_stage_mock_deterministic_fixture():
    """EntityStageMock returns the fixed DE2E-1 set with deterministic spans."""
    stage = entity_stage_fixture()
    ents = stage.run(TRANSCRIPT, "s1")
    by_name = {e["name"]: e for e in ents}

    assert set(by_name) == {"port 16379", "FalkorDB", "tortoise#123"}
    assert by_name["port 16379"]["objectKind"] == "other"
    assert by_name["FalkorDB"]["objectKind"] == "tool"
    assert by_name["tortoise#123"]["objectKind"] == "workitem"
    # spans are actual char offsets into the transcript
    for e in ents:
        assert e["span"] is not None
        assert TRANSCRIPT[e["span"][0]:e["span"][1]] == e["name"]
    # deterministic: same input → same output
    assert stage.run(TRANSCRIPT, "s1") == ents
    print("PASS test_entity_stage_mock_deterministic_fixture")


def test_entity_stage_mock_empty_transcript():
    """No seed fragments → empty entity list, no crash (DE2E-N5 style)."""
    stage = entity_stage_fixture()
    assert stage.run("Alice: nothing here.\n", "s2") == []
    print("PASS test_entity_stage_mock_empty_transcript")


# ── Rule/keyword fallback (DE2E-N2) ────────────────────────────────

def test_rule_fallback_entities():
    """repo#NNN refs → workitem entities with spans (session_indexer.py:204 regex)."""
    ents = _rule_fallback_entities(
        "Alice: we should fix tortoise#123 and eldato#45 today.\n"
    )
    by_name = {e["name"]: e for e in ents}
    assert set(by_name) == {"tortoise#123", "eldato#45"}
    assert by_name["tortoise#123"]["objectKind"] == "workitem"
    assert by_name["tortoise#123"]["canonical_candidates"] == ["tortoise#123"]
    assert by_name["tortoise#123"]["span"] == [21, 33]
    # no refs → empty, never raises
    assert _rule_fallback_entities("Alice: nothing here.\n") == []
    print("PASS test_rule_fallback_entities")


# ── extract_conversation_entities API (DE2E-N12: no S7 clash) ─────

def test_extract_conversation_entities_with_injected_mock():
    """Injected EntityStageMock drives the API — no LLM involvement."""
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    api, _ = _api()
    ents = ext.extract_conversation_entities(
        TRANSCRIPT, "s1", api, entity_stage=entity_stage_fixture(),
    )
    assert {e["name"] for e in ents} == {"port 16379", "FalkorDB", "tortoise#123"}
    print("PASS test_extract_conversation_entities_with_injected_mock")


def test_extract_conversation_entities_module_level_no_model():
    """model=None + no stage → rule/keyword fallback (deterministic, no LLM)."""
    api, _ = _api()
    ents = extract_conversation_entities(TRANSCRIPT, "s1", api)
    assert {e["name"] for e in ents} == {"tortoise#123"}
    assert ents[0]["objectKind"] == "workitem"
    print("PASS test_extract_conversation_entities_module_level_no_model")


def test_de2e_n2_llm_failure_falls_back_to_rules():
    """Mock that raises on first call → rule fallback extracts known refs."""
    api, _ = _api()
    stage = EntityStageMock(
        {"FalkorDB": [{"name": "FalkorDB", "objectKind": "tool"}]},
        fail_first_call=True,
    )
    ents = extract_conversation_entities(TRANSCRIPT, "s1", api, entity_stage=stage)
    # fallback correctness: the known ref is extracted, no crash
    assert {e["name"] for e in ents} == {"tortoise#123"}
    assert ents[0]["objectKind"] == "workitem"
    print("PASS test_de2e_n2_llm_failure_falls_back_to_rules")


def test_de2e_n7_unknown_object_kind_normalized_to_other():
    """Mock returning 'wibble' → objectKind 'other' (no error)."""
    api, _ = _api()
    stage = EntityStageMock(
        {"FalkorDB": [{"name": "FalkorDB", "objectKind": "wibble"}]},
    )
    ents = extract_conversation_entities(
        "Alice: FalkorDB is the store.\n", "s1", api, entity_stage=stage,
    )
    assert len(ents) == 1
    assert ents[0]["name"] == "FalkorDB"
    assert ents[0]["objectKind"] == "other"
    print("PASS test_de2e_n7_unknown_object_kind_normalized_to_other")


def test_de2e_n12_conversation_and_s7_extract_entities_coexist():
    """Both APIs exist on LLMExtractor with distinct contracts (no clash)."""
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    assert callable(getattr(ext, "extract_entities"))
    assert callable(getattr(ext, "extract_conversation_entities"))
    # S7 returns {subjects, objects, entities}; Phase-2 returns a list of dicts
    api, _ = _api()
    s7 = ext.extract_entities(
        "## Section\nFalkorDB is the memory backend for the Tortoise project.",
        "doc.md", api,
    )
    assert isinstance(s7, dict) and "subjects" in s7 and "objects" in s7
    conv = ext.extract_conversation_entities(
        "Alice: FalkorDB is the memory backend.\n", "s1", api,
        entity_stage=entity_stage_fixture(),
    )
    assert isinstance(conv, list)
    print("PASS test_de2e_n12_conversation_and_s7_extract_entities_coexist")


def test_entity_stage_llm_path_with_mock_model_no_crash():
    """EntityStage over MockModel (unknown TASK → '{}') → empty list, no crash."""
    stage = EntityStage(MockModel("cheap"))
    ents = stage.run("Alice: FalkorDB is the store.\n", "s1")
    assert ents == []
    print("PASS test_entity_stage_llm_path_with_mock_model_no_crash")


if __name__ == "__main__":
    test_entity_stage_mock_deterministic_fixture()
    test_entity_stage_mock_empty_transcript()
    test_rule_fallback_entities()
    test_extract_conversation_entities_with_injected_mock()
    test_extract_conversation_entities_module_level_no_model()
    test_de2e_n2_llm_failure_falls_back_to_rules()
    test_de2e_n7_unknown_object_kind_normalized_to_other()
    test_de2e_n12_conversation_and_s7_extract_entities_coexist()
    test_entity_stage_llm_path_with_mock_model_no_crash()
