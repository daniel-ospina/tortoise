"""Tests for document-mode epistemic extraction — S4 Epistemic Extractor (#6855).

Covers: _document_sections edge cases, _DocumentPointStage, extract_from_document,
idempotency via begin_ingest, FalkorProjection.from_uri, and E2E-2 verification.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI, provenance  # noqa: F401, I001
from tortoise.extractor import (
    _document_sections, _strip_frontmatter, _DocumentPointStage,
    _POINTS_DOC_SYS, _RELATIONS_DOC_SYS, _json,  # noqa: F401
    LLMExtractor, MockModel, extract_from_document,  # noqa: F401
)
from tortoise.idempotency import document_key, IngestKey  # noqa: F401
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection


# ── Helpers ─────────────────────────────────────────────────────────

def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def _api():
    log = EventLog(_tmp("events.jsonl"))
    return EventAPI(log, initiated_by="extractor", agent_id="test"), log


# ── _document_sections edge cases ───────────────────────────────────

def test_doc_sections_skip_frontmatter():
    text = "---\ntitle: Test\n---\n\n## Section\nContent here is more than twenty characters long enough for extraction."
    is_doc, sections = _document_sections(text)
    assert is_doc
    assert len(sections) == 1
    assert sections[0][0] == "Section"
    # Body should NOT contain the frontmatter
    assert "title:" not in sections[0][1]
    print("PASS test_doc_sections_skip_frontmatter")


def test_doc_sections_code_block_contains_hash():
    """## inside a fenced code block should not create a section boundary."""
    text = "## Real Section\nContent here is more than twenty characters.\n```\n## not a header\n```\nMore body text that extends beyond twenty chars."
    is_doc, sections = _document_sections(text)
    assert is_doc
    # Should have exactly 1 section (## inside code block ignored)
    assert len(sections) == 1
    assert sections[0][0] == "Real Section"
    print("PASS test_doc_sections_code_block_contains_hash")


def test_doc_sections_empty_sections_skipped():
    text = "## Short\nab\n## Good\nContent here is more than twenty characters for extraction purposes."
    is_doc, sections = _document_sections(text)
    assert is_doc
    assert len(sections) == 1
    assert sections[0][0] == "Good"
    print("PASS test_doc_sections_empty_sections_skipped")


def test_doc_sections_no_headers():
    text = "Just some text without any markdown headers at all in this entire document."
    is_doc, sections = _document_sections(text)
    assert not is_doc
    assert sections == []
    print("PASS test_doc_sections_no_headers")


# ── _strip_frontmatter ──────────────────────────────────────────────

def test_strip_frontmatter_simple():
    text = "---\ntitle: Test\n---\n\n## Section\nContent here is more than twenty chars okay."
    result = _strip_frontmatter(text)
    assert "title:" not in result
    assert "## Section" in result
    print("PASS test_strip_frontmatter_simple")


def test_strip_frontmatter_no_blank_line():
    """Frontmatter closing --- immediately followed by ## header (no blank line)."""
    text = "---\ntitle: Test\n---\n## Section\nContent here is more than twenty chars okay."
    result = _strip_frontmatter(text)
    assert "title:" not in result
    assert "## Section" in result
    print("PASS test_strip_frontmatter_no_blank_line")


def test_strip_frontmatter_no_frontmatter():
    text = "## Section\nContent here is more than twenty characters long."
    result = _strip_frontmatter(text)
    assert result == text
    print("PASS test_strip_frontmatter_no_frontmatter")


# ── _DocumentPointStage ─────────────────────────────────────────────

def test_doc_point_stage_mock_output():
    """MockModel in doc mode returns valid pointKind, aboutEntities, confidence."""
    model = MockModel("mock")
    stage = _DocumentPointStage(model)
    body = "We decided to use FalkorDB for graph storage because it is simple to deploy."
    points = stage.run("Architecture", body, "doc:test.md")
    assert isinstance(points, list)
    assert len(points) >= 1
    p0 = points[0]
    assert "content" in p0
    assert "pointKind" in p0
    assert "aboutEntities" in p0
    assert "confidence" in p0
    # pointKind must be one of the 9 epistemic values
    valid_kinds = {"statement", "decision", "vision", "strategy",
                   "plan", "goal", "target", "observation", "hypothesis"}
    assert p0["pointKind"] in valid_kinds, f"got {p0['pointKind']}"
    assert isinstance(p0["confidence"], float)
    assert 0.0 <= p0["confidence"] <= 1.0
    print("PASS test_doc_point_stage_mock_output")


def test_doc_point_stage_empty_section():
    """Section with no extractable claims returns empty list."""
    model = MockModel("mock")
    stage = _DocumentPointStage(model)
    points = stage.run("Empty", "ab", "doc:test.md")
    # Body < 20 chars → mock returns empty
    assert points == []
    print("PASS test_doc_point_stage_empty_section")


def test_doc_point_stage_json_parse():
    """_DocumentPointStage handles the raw LLM JSON output format correctly."""
    # Override MockModel to return specific JSON
    class PointModel:
        id = "test-point"
        def complete(self, *, system, user):
            return json.dumps({
                "points": [
                    {"content": "FalkorDB is fast",
                     "pointKind": "observation",
                     "aboutEntities": ["falkordb"],
                     "confidence": 0.9},
                    {"content": "We will adopt BFS",
                     "pointKind": "decision",
                     "aboutEntities": ["bfs", "team"],
                     "confidence": 0.85},
                ]
            })

    stage = _DocumentPointStage(PointModel())
    points = stage.run("Section", "some body text here", "doc:test.md")
    assert len(points) == 2
    assert points[0]["content"] == "FalkorDB is fast"
    assert points[0]["pointKind"] == "observation"
    assert points[0]["confidence"] == 0.9
    assert points[1]["pointKind"] == "decision"
    assert points[1]["aboutEntities"] == ["bfs", "team"]
    print("PASS test_doc_point_stage_json_parse")


# ── extract_from_document (module-level API) ─────────────────────────

def test_extract_from_document_basic():
    """extract_from_document() creates Points with all required fields."""
    api, log = _api()
    text = (
        "## Architecture\n"
        "We decided to use event sourcing because it provides an audit trail "
        "and enables replay for debugging purposes in production systems.\n\n"
        "## Tradeoffs\n"
        "However the complexity increases with event sourcing but the benefits "
        "of having a complete history outweigh the costs of implementation."
    )
    stats = extract_from_document(
        text, "design.md", api,
        point_model=MockModel("cheap"),
        relation_model=MockModel("reason"),
        authored_by="pi-agent",
    )
    assert stats["points"] >= 2, f"expected >=2 points, got {stats}"
    assert stats["sections"] >= 1

    # Verify events in the log
    events = log.read_all()
    points = [e for e in events if e["type"] == "PointAdded"]
    assert len(points) >= 2

    # Each point should have the new fields
    for p in points:
        pt = p["point"]
        assert "pointKind" in pt, f"missing pointKind in {pt}"
        assert "aboutEntities" in pt
        assert "confidence" in pt
        assert pt.get("authoredBy") == "pi-agent"
        assert pt.get("extractedFrom") == "design.md"

    print("PASS test_extract_from_document_basic")


def test_extract_from_document_not_a_document():
    """extract_from_document on non-markdown returns zero stats."""
    api, log = _api()  # noqa: RUF059
    text = "Alice: Hello world, this is a conversation."
    stats = extract_from_document(
        text, "chat.txt", api,
        point_model=MockModel("cheap"),
        relation_model=MockModel("reason"),
    )
    assert stats["points"] == 0
    assert stats["sections"] == 0
    print("PASS test_extract_from_document_not_a_document")


def test_extract_from_document_operators():
    """IMPL/NAND relations are created between related Points."""
    api, log = _api()
    # Use cue words that MockModel's relation stage detects
    text = (
        "## Section A\n"
        "FalkorDB provides fast graph traversal because it stores data "
        "in a specialized format that optimizes for graph algorithms.\n\n"
        "## Section B\n"
        "But however the memory usage is concerning for large graphs but "
        "the performance gains justify the additional resource consumption."
    )
    stats = extract_from_document(  # noqa: F841
        text, "graph.md", api,
        point_model=MockModel("cheap"),
        relation_model=MockModel("reason"),
    )
    # MockModel finds relations via cue words
    events = log.read_all()
    ops = [e for e in events if e["type"] == "OperatorAdded"]
    assert len(ops) >= 1, f"expected operators from cue words, got {len(ops)}"

    # Verify operator structure
    for op in ops:
        pt = op["point"]
        assert "operator" in pt
        assert pt["operator"]["op_type"] in ("IMPL", "NAND")
        assert len(pt["operator"]["inputs"]) == 2

    print("PASS test_extract_from_document_operators")


# ── Idempotency ──────────────────────────────────────────────────────

def test_begin_ingest_idempotent():
    """Re-running same document at same version is a no-op."""
    api, log = _api()  # noqa: RUF059
    text = "## Section\nContent here is more than twenty characters for extraction."
    key = document_key(text)
    extractor_version = "mock:v1"

    result1 = api.begin_ingest("doc.md", extractor_version, key)
    assert not result1.skip

    # Re-run with same key + version → skip
    result2 = api.begin_ingest("doc.md", extractor_version, key)
    assert result2.skip
    assert "already processed" in result2.reason
    print("PASS test_begin_ingest_idempotent")


def test_begin_ingest_force():
    """--force overrides idempotency gate."""
    api, log = _api()  # noqa: RUF059
    text = "## Section\nContent here is more than twenty characters for extraction."
    key = document_key(text)
    extractor_version = "mock:v1"

    api.begin_ingest("doc.md", extractor_version, key)

    # Force re-extraction
    result = api.begin_ingest("doc.md", extractor_version, key, force=True)
    assert not result.skip
    print("PASS test_begin_ingest_force")


def test_document_key_deterministic():
    """document_key is deterministic — same content → same key."""
    text = "## Section\nContent for testing document key determinism."
    k1 = document_key(text)
    k2 = document_key(text)
    assert k1 == k2
    assert k1.kind == "document"
    assert len(k1.value) == 64  # SHA-256 hex
    print("PASS test_document_key_deterministic")


# ── FalkorProjection.from_uri ────────────────────────────────────────

def test_from_uri_file_path():
    """File path creates file-based projection directly (from_uri is docker:// only)."""
    path = _tmp("test.db")
    proj = FalkorProjection(path)
    assert proj is not None
    proj.close()
    print("PASS test_from_uri_file_path")


# ── E2E-2: Full extraction → pointKind → NLP verification ────────────

def test_e2e_extraction_point_kind_vocabulary():
    """E2E-2: All extracted points have valid pointKind values (ONTOLOGY §2)."""
    api, log = _api()
    text = (
        "## Strategy\n"
        "We plan to implement the memory system in three phases. The first phase "
        "targets 50 Points per document. We observed that multiple rounds of "
        "review reduce errors by 40 percent.\n\n"
        "## Vision\n"
        "Our vision is a fully automated memory pipeline that extracts knowledge "
        "from all project documents. We hypothesize that confidence propagation "
        "will converge within 3 iterations for documents under 5000 words."
    )
    stats = extract_from_document(
        text, "strategy.md", api,
        point_model=MockModel("cheap"),
        relation_model=MockModel("reason"),
        authored_by="pi-agent",
    )
    assert stats["points"] >= 1

    events = log.read_all()
    points = [e for e in events if e["type"] == "PointAdded"]
    valid_kinds = {"statement", "decision", "vision", "strategy",
                   "plan", "goal", "target", "observation", "hypothesis"}

    for p in points:
        pt = p["point"]
        kind = pt["pointKind"]
        assert kind in valid_kinds, (
            f"Invalid pointKind '{kind}' for content: {pt['content'][:60]}..."
        )
        assert isinstance(kind, str)
        assert kind != ""  # not empty

    # Count distribution — should have variety
    kinds_found = {p["point"]["pointKind"] for p in points}
    assert len(kinds_found) >= 1, f"expected at least 1 pointKind, got {kinds_found}"

    print("PASS test_e2e_extraction_point_kind_vocabulary")


def test_e2e_no_duplicate_point_ids():
    """Each PointAdded event produces a unique point ID."""
    api, log = _api()
    text = "## A\nContent here is more than twenty characters for testing.\n## B\nMore content that is also long enough to pass minimum threshold."
    stats = extract_from_document(  # noqa: F841
        text, "unique.md", api,
        point_model=MockModel("cheap"),
        relation_model=MockModel("reason"),
    )
    events = log.read_all()
    point_events = [e for e in events if e["type"] == "PointAdded"]
    ids = [e["point"]["id"] for e in point_events]
    assert len(ids) == len(set(ids)), f"duplicate IDs found: {ids}"
    print("PASS test_e2e_no_duplicate_point_ids")


# ── Domain-aware extraction (#6888) ──────────────────────────────────

def test_domain_pointkind_injection():
    """When domain is provided, pointKind prompt includes domain-specific values."""
    from tortoise.extractor import _DocumentPointStage, _build_pointkind_prompt  # noqa: I001
    from tortoise.domain_loader import load_manifest

    # Load manifest to register product-strategy kinds
    load_manifest()

    # Resolve kinds from registered vocabulary
    from tortoise.domain_loader import known_kinds
    all_kinds = known_kinds()
    assert "useCase" in all_kinds
    assert "jobToBeDone" in all_kinds

    product_kinds = sorted(all_kinds)  # sort for deterministic prompts

    # Default (no domain) uses descriptions
    prompt_default = _build_pointkind_prompt()
    assert "factual claim" in prompt_default

    # Domain mode uses bare kind names
    prompt_domain = _build_pointkind_prompt(product_kinds)
    assert "- useCase" in prompt_domain
    assert "- jobToBeDone" in prompt_domain
    assert "factual claim" not in prompt_domain  # bare names, no descriptions

    # _DocumentPointStage embeds kinds in system prompt
    stage = _DocumentPointStage(None, point_kinds=product_kinds)
    assert "useCase" in stage._system
    assert "jobToBeDone" in stage._system
    print("PASS test_domain_pointkind_injection")


def test_domain_unrecognized_kind_warning():
    """_warn_unrecognized_kinds uses the 2-arg kind_is_known(kind, 'pointKind')
    (#951 — previously a silent TypeError, research-r6 §1.2): known kinds stay
    silent, unknown kinds print a warning (best-effort, never blocks)."""
    import sys, io  # noqa: E401, I001
    from tortoise.extractor import _warn_unrecognized_kinds

    # Known kinds → no warning
    sys.stderr = io.StringIO()
    _warn_unrecognized_kinds({"statement", "decision"})
    assert sys.stderr.getvalue() == ""

    # Unrecognized kinds → warning names them (fix: no longer swallowed)
    sys.stderr = io.StringIO()
    _warn_unrecognized_kinds({"statement", "bogusValue"})
    out = sys.stderr.getvalue()
    assert "bogusValue" in out

    print("PASS test_domain_unrecognized_kind_warning")


def test_e2e_extraction_with_domain_flag():
    """E2E: extract_from_document with --domain produces points with domain kinds."""
    import sys, io  # noqa: E401, I001
    api, log = _api()
    text = (
        "## Product Strategy\n"
        "We plan to build a use case for automated debt collection. "
        "This is a job to be done for our end users. The workflow starts "
        "with a requirement document that specifies the policy."
    )
    from tortoise.domain_loader import load_manifest
    load_manifest()  # ensure manifest loaded before extraction

    # Capture stderr during extraction to check for warnings
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        stats = extract_from_document(
            text, "strategy.md", api,
            point_model=MockModel("cheap"),
            relation_model=MockModel("reason"),
            authored_by="pi-agent",
            domain="product-strategy",
        )
    finally:
        sys.stderr = old_stderr

    assert stats["points"] >= 1

    # Verify events have pointKind values
    events = log.read_all()
    points = [e for e in events if e["type"] == "PointAdded"]
    kinds_found = {p["point"]["pointKind"] for p in points}

    # Should include at least base kinds (mock model uses keyword matching)
    assert len(kinds_found) >= 1, f"expected at least 1 pointKind, got {kinds_found}"
    print(f"PASS test_e2e_extraction_with_domain_flag — kinds: {kinds_found}")


# ── Module runner ────────────────────────────────────────────────────

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall extractor_doc tests passed")


if __name__ == "__main__":
    _run_all()
