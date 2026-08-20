"""S7 Semantic Extractor tests — domain_loader registries, Subject/Object API,
LLMExtractor.extract_entities, and --domain flag wiring.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI  # noqa: E402, I001, RUF100
from tortoise.domain_loader import (  # noqa: E402, RUF100
    known_kinds, register_kind, kind_is_known, load_manifest, known_kinds,  # noqa: F401, F811
)
from tortoise.extractor import (  # noqa: E402, RUF100
    LLMExtractor, MockModel, _SemanticStage, _document_sections,  # noqa: F401
)
from tortoise.log import EventLog  # noqa: E402, RUF100


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def _api():
    log = EventLog(_tmp("events.jsonl"))
    return EventAPI(log, initiated_by="extractor", agent_id="test"), log


# ── Domain loader: per-kind registries ────────────────────────────────

def test_known_kinds_per_type():
    """known_kinds() returns all registered kinds including base point kinds."""
    kinds = known_kinds()
    assert isinstance(kinds, frozenset)
    assert "statement" in kinds
    assert "decision" in kinds
    assert "goal" in kinds
    assert "observation" in kinds
    print("PASS test_known_kinds_per_type")


def test_known_kinds_object_type():
    """known_kinds() returns all registered kinds including base event kinds."""
    kinds = known_kinds()
    assert isinstance(kinds, frozenset)
    assert "session" in kinds
    assert "meeting" in kinds
    assert "milestone" in kinds
    assert "incident" in kinds
    print("PASS test_known_kinds_object_type")


def test_register_kind_per_type():
    """register_kind adds to the global kind registry."""
    register_kind("testOrg")
    assert kind_is_known("testOrg")
    print("PASS test_register_kind_per_type")


def test_known_kinds():
    """known_kinds returns frozenset of all registered kinds."""
    kinds = known_kinds()
    assert isinstance(kinds, frozenset)
    assert "goal" in kinds
    print("PASS test_known_kinds")


# ── API: add_subject / add_object ────────────────────────────────────

def test_api_add_subject():
    """add_subject emits a SubjectAdded event."""
    api, log = _api()
    sid = api.add_subject("Test Org", "organization")
    events = log.read_all()
    subjects = [e for e in events if e["type"] == "SubjectAdded"]
    assert len(subjects) == 1
    assert subjects[0]["name"] == "Test Org"
    assert subjects[0]["subject_kind"] == "organization"
    assert subjects[0]["id"] == sid
    print("PASS test_api_add_subject")


def test_api_add_object():
    """add_object emits an ObjectRegistered event."""
    api, log = _api()
    oid = api.add_object("Tortoise DB", "database")
    events = log.read_all()
    objects = [e for e in events if e["type"] == "ObjectRegistered"]
    assert len(objects) == 1
    assert objects[0]["name"] == "Tortoise DB"
    assert objects[0]["object_kind"] == "database"
    assert objects[0]["id"] == oid
    print("PASS test_api_add_object")


# ── SemanticExtractor with MockModel ──────────────────────────────────

def test_semantic_extract_entities():
    """extract_entities extracts subjects/objects from a doc via MockModel."""
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    api, log = _api()

    text = (
        "## Product Strategy\n"
        "The Organisation Design Team decided to adopt FalkorDB as the memory backbone "
        "for the Tortoise project. This replaces MemPalace and Graphiti.\n\n"
        "## Architecture\n"
        "FalkorDB runs in Docker and uses the Redis protocol. The Tortoise API "
        "writes events to a JSONL log."
    )

    result = ext.extract_entities(text, "strategy.md", api)
    events = log.read_all()
    subjects = [e for e in events if e["type"] == "SubjectAdded"]  # noqa: F841
    objects = [e for e in events if e["type"] == "ObjectRegistered"]  # noqa: F841

    # MockModel now produces subjects from "X Team" patterns and objects from other caps
    assert result["subjects"] >= 1, f"expected >=1 subjects, got {result['subjects']}"
    assert result["objects"] >= 1, f"expected >=1 objects, got {result['objects']}"
    assert isinstance(result["entities"], list)
    print(f"PASS test_semantic_extract_entities "
          f"({result['subjects']} subjects, {result['objects']} objects, "
          f"{len(result['entities'])} entities)")


def test_semantic_extract_no_doc():
    """extract_entities on non-document returns empty."""
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    api, log = _api()  # noqa: RUF059
    text = "Alice: Hello world, this is just a conversation with enough text content."
    result = ext.extract_entities(text, "chat.txt", api)
    assert result["subjects"] == 0
    assert result["objects"] == 0
    assert result["entities"] == []
    print("PASS test_semantic_extract_no_doc")


def test_semantic_extract_dedup():
    """extract_entities deduplicates entities by name within a run."""
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    api, log = _api()
    text = (
        "## Team\n"
        "The Organisation Design Team owns the Tortoise project.\n\n"
        "## Project\n"
        "The Organisation Design Team decided to use FalkorDB for Tortoise."
    )
    result = ext.extract_entities(text, "doc.md", api)  # noqa: F841
    events = log.read_all()
    subjects = [e for e in events if e["type"] == "SubjectAdded"]
    # Same entity name across sections should dedup
    names = [s["name"] for s in subjects]
    assert len(names) == len(set(names)), f"duplicate names found: {names}"
    print("PASS test_semantic_extract_dedup")


# ── _SemanticStage unit test ─────────────────────────────────────────

def test_semantic_stage_mock():
    """_SemanticStage with MockModel returns entity data."""
    stage = _SemanticStage(MockModel("mock"))
    result = stage.run(
        "Architecture",
        "The DevOps Team deploys FalkorDB in Docker containers. "
        "Tortoise writes to JSONL logs and projects to the graph.",
        "document:test.md",
    )
    assert "subjects" in result
    assert "objects" in result
    assert "aboutEntities" in result
    # MockModel extracts capitalized words
    assert len(result["aboutEntities"]) >= 1
    print("PASS test_semantic_stage_mock")


# ── Projection integration: Subject/Object nodes in FalkorDB ──────────

def test_projection_subject_object():
    """add_subject/add_object emit events to the log.

    Projection's apply() only handles PointAdded/OperatorAdded/etc —
    SubjectAdded/ObjectRegistered events are logged but not materialized
    as graph nodes. This test verifies the log events exist.
    """
    import tempfile, os  # noqa: E401, I001
    from tortoise.projection import FalkorProjection
    from tortoise.log import EventLog

    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_"), "test.db")
    log_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_"), "events.jsonl")

    log = EventLog(log_path)
    proj = FalkorProjection(db_path)
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=proj)

    try:
        sid = api.add_subject("Test Team", "team")
        oid = api.add_object("Test Product", "product")

        events = log.read_all()
        subjects = [e for e in events if e["type"] == "SubjectAdded"]
        objects = [e for e in events if e["type"] == "ObjectRegistered"]

        assert len(subjects) == 1
        assert subjects[0]["name"] == "Test Team"
        assert subjects[0]["subject_kind"] == "team"
        assert subjects[0]["id"] == sid

        assert len(objects) == 1
        assert objects[0]["name"] == "Test Product"
        assert objects[0]["object_kind"] == "product"
        assert objects[0]["id"] == oid
    finally:
        proj.close()

    print("PASS test_projection_subject_object")


# ── skip_on_failure coverage ──────────────────────────────────────────

def test_semantic_extract_skip_on_failure():
    """extract_entities with skip_on_failure=True survives a failing model."""

    class FailingModel:
        id = "fail"
        def complete(self, *, system, user):
            raise RuntimeError("LLM unavailable")

    ext = LLMExtractor(FailingModel(), MockModel("reason"))
    api, log = _api()  # noqa: RUF059
    text = (
        "## Section One\n"
        "This has more than twenty characters of content here yes it does.\n\n"
        "## Section Two\n"
        "Another section with enough content to pass the twenty character minimum test."
    )

    result = ext.extract_entities(text, "doc.md", api, skip_on_failure=True)
    # Should return zeros — all sections failed extraction
    assert result["subjects"] == 0
    assert result["objects"] == 0
    print("PASS test_semantic_extract_skip_on_failure")


# ── YAML manifest loading coverage ───────────────────────────────────

def test_load_manifest_yaml():
    """load_manifest loads a YAML file and registers kind values."""
    import tempfile, os, yaml  # noqa: E401, I001

    manifest = {
        "version": 1,
        "domains": {
            "test-domain": {
                "name": "Test Domain",
                "active": True,
                "version": "1.0",
                "kind_values": {
                    "subjectKind": ["testRole", "testPerson"],
                    "objectKind": ["testArtifact"],
                },
            },
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(manifest, f)
        tmp_path = f.name

    try:
        from tortoise.domain_loader import load_manifest, known_kinds  # noqa: F401, I001
        result = load_manifest(tmp_path)
        assert "test-domain" in result
        config = result["test-domain"]
        assert config.key == "test-domain"
        assert config.name == "Test Domain"
        # Verify kinds were registered (kind_values are consumed at load time,
        # not stored on DomainRoutingConfig)
        assert kind_is_known("testRole")
        assert kind_is_known("testArtifact")
    finally:
        os.unlink(tmp_path)

    print("PASS test_load_manifest_yaml")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
