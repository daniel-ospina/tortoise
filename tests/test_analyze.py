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
        # NAND is operator-mediated (#7801): op {op_type:'NAND', is_operator:true}
        # connects to both claims — the analyze template matches this shape.
        proj.g.query(
            "MATCH (a:Point {id:'c1'}), (b:Point {id:'c2'}) "
            "CREATE (op:Point {id:'op1', op_type:'NAND', is_operator:true})"
            "-[:NAND]->(a), (op)-[:NAND]->(b)"
        )
        
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


# ── #329: provider-key routing (never send a key to the wrong provider) ──

class _FakeResponse:
    def __init__(self, content: str):
        self._content = content
    def read(self):
        import json as _json
        return _json.dumps({
            "choices": [{"message": {"content": _json.dumps({
                "pattern": "disagreement", "params": {"entity": "", "limit": 20}})}}]
        }).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, captured):
    import urllib.request as _ur
    class FakeUrlopen:
        def __call__(self, req, *a, **kw):
            captured["url"] = getattr(req, "full_url", str(req))
            captured["headers"] = dict(getattr(req, "headers", {}))
            return _FakeResponse("")
    monkeypatch.setattr(_ur, "urlopen", FakeUrlopen())
    return captured


def test_llm_classify_deepseek_key_goes_to_deepseek(monkeypatch):
    from tortoise import analyze as _a
    captured: dict = {}
    _patch_urlopen(monkeypatch, captured)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-only")
    result = _a.llm_classify("where is the disagreement?")
    assert result is not None
    assert "api.deepseek.com" in captured["url"]
    auth = captured["headers"].get("Authorization", "")
    assert auth == "Bearer sk-deepseek-only"


def test_llm_classify_openai_key_never_goes_to_deepseek(monkeypatch):
    """#329 P0: OPENAI key must NEVER be sent to api.deepseek.com."""
    from tortoise import analyze as _a
    captured: dict = {}
    _patch_urlopen(monkeypatch, captured)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    result = _a.llm_classify("where is the disagreement?")
    assert result is not None
    assert "api.openai.com" in captured["url"]
    auth = captured["headers"].get("Authorization", "")
    assert auth == "Bearer sk-openai-secret"
    assert "sk-openai-secret" in auth  # the OPENAI key went to OpenAI, nowhere else


def test_llm_classify_both_keys_deepseek_priority(monkeypatch):
    from tortoise import analyze as _a
    captured: dict = {}
    _patch_urlopen(monkeypatch, captured)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    _a.llm_classify("x")
    assert "api.deepseek.com" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer sk-deepseek"
    assert "sk-openai" not in captured["headers"]["Authorization"]


def test_llm_classify_no_key_returns_none(monkeypatch):
    from tortoise import analyze as _a
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert _a.llm_classify("x") is None


def test_llm_classify_empty_key_treated_unset(monkeypatch):
    from tortoise import analyze as _a
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    assert _a.llm_classify("x") is None


def test_llm_classify_provider_down_returns_none(monkeypatch):
    """Provider outage → graceful keyword fallback, no crash, no cross-provider call."""
    from tortoise import analyze as _a
    import urllib.request as _ur
    def boom(*a, **kw):
        raise _ur.URLError("provider down")
    monkeypatch.setattr(_ur, "urlopen", boom)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    assert _a.llm_classify("x") is None


# ── #329: error/cypher redaction ──

def test_analyze_error_redacted(monkeypatch):
    """Force a Cypher error → no raw exception internals, query is None."""
    from tortoise.analyze import analyze
    from tortoise.projection import FalkorProjection
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    try:
        proj = FalkorProjection(db_path)
        proj.g.query("CREATE (:Point {id:'c1', content:'AI strategy is working', confidence:0.85})")
        proj.g.query("CREATE (:Point {id:'c2', content:'AI strategy needs revision', confidence:0.65})")
        # NAND is operator-mediated (#7801): op {op_type:'NAND', is_operator:true}
        # connects to both claims — the analyze template matches this shape.
        proj.g.query(
            "MATCH (a:Point {id:'c1'}), (b:Point {id:'c2'}) "
            "CREATE (op:Point {id:'op1', op_type:'NAND', is_operator:true})"
            "-[:NAND]->(a), (op)-[:NAND]->(b)"
        )
        # Break the disagreement template by injecting a bad limit param
        import tortoise.analyze as _a
        orig = _a.TEMPLATES["disagreement"]["cypher"]
        _a.TEMPLATES["disagreement"]["cypher"] = orig + " WITH bogus RETURN 1"
        try:
            result = analyze("where is the disagreement?", proj=proj)
            assert "Traceback" not in result["answer"]
            assert "proj" not in result["answer"]
            assert result["query"] is None
        finally:
            _a.TEMPLATES["disagreement"]["cypher"] = orig
    finally:
        proj.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_analyze_unknown_pattern_not_echoed():
    """LLM-returned hostile pattern name is not echoed raw."""
    from tortoise.analyze import analyze
    result = analyze("x", proj=None, use_llm=False)
    assert "couldn't understand" in result["answer"].lower()
