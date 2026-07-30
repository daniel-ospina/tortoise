"""Tests for tortoise_analyze."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tortoise.analyze import classify, analyze, TEMPLATES


def test_classify_disagreement():
    result = classify("where is the disagreement?")
    assert result is not None
    assert result[0] == "disagreement"


def test_classify_strongest():
    result = classify("what is the strongest argument for AI strategy?")
    assert result is not None
    assert result[0] == "strongest_for"
    assert "ai strategy" in result[1]["entity"].lower()


def test_classify_counter():
    result = classify("what are the counter arguments to climate change?")
    assert result is not None
    assert result[0] == "counter_arguments"


def test_classify_consensus():
    result = classify("what do we have consensus on?")
    assert result is not None
    assert result[0] == "consensus"


def test_classify_uncertain():
    result = classify("what are we most uncertain about?")
    assert result is not None
    assert result[0] == "most_uncertain"


def test_classify_evidence():
    result = classify("show me the evidence chain for the AI strategy")
    assert result is not None
    assert result[0] == "evidence_chain"


def test_classify_trends():
    result = classify("how has the strategy changed over time?")
    assert result is not None
    assert result[0] == "trends"


def test_classify_grounding():
    result = classify("what are the most central claims?")
    assert result is not None
    assert result[0] == "grounding"


def test_classify_unknown():
    result = classify("what is the meaning of life?")
    assert result is None  # No keyword match


def test_all_templates_exist():
    assert len(TEMPLATES) == 8
    for name, tmpl in TEMPLATES.items():
        assert "cypher" in tmpl
        assert "triggers" in tmpl
        assert "format" in tmpl


def test_no_llm_no_proj():
    """Without a projection and without LLM, returns helpful error."""
    result = analyze("what is the meaning of life?", proj=None, use_llm=False)
    assert "couldn't understand" in result["answer"].lower()
    assert result["raw"] == []


def test_with_projection():
    """With a real FalkorProjection, executes queries."""
    from tortoise.projection import FalkorProjection
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    try:
        proj = FalkorProjection(db_path)
        # Add some test data
        proj.g.query("CREATE (:Point {id:'c1', content:'AI strategy is working', confidence:0.85})")
        proj.g.query("CREATE (:Point {id:'c2', content:'AI strategy needs revision', confidence:0.65})")
        proj.g.query("MATCH (a:Point {id:'c1'}), (b:Point {id:'c2'}) CREATE (a)-[:NAND]->(b)")
        
        result = analyze("where is the disagreement?", proj=proj)
        assert result["pattern"] == "disagreement"
        assert len(result["raw"]) > 0
        assert result["query"] is not None
    finally:
        proj.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
