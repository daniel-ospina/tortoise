"""Extractor tests — _utterances, _document_sections, _is_claim, MockExtractor,
MockModel, _json, _overlap, _has_cue, LLMExtractor.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI, provenance          # noqa: E402
from tortoise.extractor import (                        # noqa: E402
    _document_sections, _has_cue, _is_claim, _json, _overlap,
    _PUNC, _REFUTE_PHRASES, _REFUTE_SINGLE_RE,
    _SUPPORT_PHRASES, _SUPPORT_SINGLE_RE,
    _utterances, LLMExtractor, MockExtractor, MockModel,
)
from tortoise.log import EventLog                       # noqa: E402


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def _api():
    log = EventLog(_tmp("events.jsonl"))
    return EventAPI(log, initiated_by="extractor", agent_id="test"), log


# ── _has_cue ─────────────────────────────────────────────────────────

def test_has_cue_support_words():
    assert _has_cue("because it works", _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES)
    assert _has_cue("Therefore, yes", _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES)
    assert _has_cue("and so they left", _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES)
    assert not _has_cue("soda is great", _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES)
    print("PASS test_has_cue_support_words")


def test_has_cue_refute_words():
    assert _has_cue("but it failed", _REFUTE_SINGLE_RE, _REFUTE_PHRASES)
    assert _has_cue("However, no", _REFUTE_SINGLE_RE, _REFUTE_PHRASES)
    assert not _has_cue("butter is tasty", _REFUTE_SINGLE_RE, _REFUTE_PHRASES)
    print("PASS test_has_cue_refute_words")


def test_has_cue_phrases():
    text = _PUNC.sub('', " not relevant. ")
    assert _has_cue(text, _REFUTE_SINGLE_RE, _REFUTE_PHRASES)
    text2 = _PUNC.sub('', " given that it exists ")
    assert _has_cue(text2, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES)
    print("PASS test_has_cue_phrases")


def test_has_cue_punctuation():
    assert _has_cue("But,", _REFUTE_SINGLE_RE, _REFUTE_PHRASES)
    assert _has_cue("but!", _REFUTE_SINGLE_RE, _REFUTE_PHRASES)
    assert _has_cue("Because.", _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES)
    print("PASS test_has_cue_punctuation")


# ── _utterances ───────────────────────────────────────────────────────

def test_utterances_simple():
    text = "Alice: hello world, this is a test.\nBob: I disagree with that point."
    utts = list(_utterances(text))
    assert len(utts) >= 1
    assert utts[0][0] == "Alice"
    assert "hello" in utts[0][1]
    print("PASS test_utterances_simple")


def test_utterances_multiple_sentences():
    text = "Alice: First sentence. Second sentence here. Third one too."
    utts = list(_utterances(text))
    assert len(utts) >= 2
    assert all(u[0] == "Alice" for u in utts)
    print("PASS test_utterances_multiple_sentences")


def test_utterances_skips_preamble():
    text = "Some preamble text without speaker\nAlice: Hello world."
    utts = list(_utterances(text))
    assert len(utts) == 1
    assert utts[0][0] == "Alice"
    print("PASS test_utterances_skips_preamble")


def test_utterances_short_text_filtered():
    text = "Alice: Hi"
    utts = list(_utterances(text))
    # "Hi" is < 3 chars, filtered out
    assert len(utts) == 0
    print("PASS test_utterances_short_text_filtered")


def test_utterances_span():
    text = "Alice: Hello world there."
    utts = list(_utterances(text))
    assert len(utts) == 1
    span = utts[0][2]
    assert span[0] >= 0 and span[1] > span[0]
    print("PASS test_utterances_span")


# ── _document_sections ───────────────────────────────────────────────

def test_document_sections_basic():
    text = "## First Section\nContent here is more than twenty characters.\n## Second\nMore content that is also quite long enough to pass."
    is_doc, sections = _document_sections(text)
    assert is_doc
    assert len(sections) == 2
    assert sections[0][0] == "First Section"
    assert sections[1][0] == "Second"
    print("PASS test_document_sections_basic")


def test_document_sections_not_document():
    text = "Just a regular conversation\nAlice: Hello world, testing here with enough chars."
    is_doc, sections = _document_sections(text)
    assert not is_doc
    assert sections == []
    print("PASS test_document_sections_not_document")


def test_document_sections_preamble():
    text = "This is preamble text that is more than twenty characters long.\n## Section One\nContent for section one that is long enough to qualify as valid content here."
    is_doc, sections = _document_sections(text)
    assert is_doc
    assert len(sections) >= 1
    assert sections[0][0] == "preamble"
    print("PASS test_document_sections_preamble")


def test_document_sections_short_body_filtered():
    text = "## Section\nShort"
    is_doc, sections = _document_sections(text)
    assert is_doc
    # "Short" is < 20 chars, so section is filtered out
    assert len(sections) == 0
    print("PASS test_document_sections_short_body_filtered")


# ── _is_claim ────────────────────────────────────────────────────────

def test_is_claim_stance_words():
    assert _is_claim("This is a claim that should be recognized as important")
    assert _is_claim("We should definitely consider this approach carefully")
    assert _is_claim("The evidence suggests a different conclusion altogether")
    print("PASS test_is_claim_stance_words")


def test_is_claim_short_text():
    """Text under 40 chars is not a claim."""
    assert not _is_claim("Short text")
    assert not _is_claim("This is too brief.")
    print("PASS test_is_claim_short_text")


def test_is_claim_markdown_filtered():
    assert not _is_claim("| Table | Row |")
    assert not _is_claim("```code block```")
    assert not _is_claim("# Header")
    assert not _is_claim("> Blockquote text that is longer than forty characters")
    print("PASS test_is_claim_markdown_filtered")


def test_is_claim_bold_short():
    assert not _is_claim("**Bold short** text")
    print("PASS test_is_claim_bold_short")


def test_is_claim_arrow_notation():
    assert _is_claim("A → B represents a causal chain that is longer than eighty characters total right here")
    assert _is_claim("X -- Y mapping is a structural relationship that exceeds the eighty character minimum threshold now")
    print("PASS test_is_claim_arrow_notation")


def test_is_claim_comparative():
    assert _is_claim("This approach is better than the alternative method of doing things")
    assert _is_claim("The primary concern is that we need more evidence here")
    print("PASS test_is_claim_comparative")


def test_is_claim_percentage_with_stance():
    text = "The rate is 45% increase"  # < 60 chars, no stance
    assert not _is_claim(text)
    text2 = "The failure rate is 45% which represents a significant increase over the past few years of careful study"
    assert _is_claim(text2)  # > 60 chars with stance word
    print("PASS test_is_claim_percentage_with_stance")


# ── MockExtractor sequential mode ────────────────────────────────────

CONVO = """Alice: I think we should use BFS because it is simple.
Bob: But however the memory usage is concerning.
Charlie: Therefore we need a hybrid approach that balances both concerns."""


def test_mock_extractor_sequential():
    api, log = _api()
    MockExtractor().run(CONVO, "test.txt", api)
    events = log.read_all()
    types = [e["type"] for e in events]
    assert "PointAdded" in types
    # At least one IMPL from "therefore" and one NAND from "but"
    assert "OperatorAdded" in types
    ops = [e for e in events if e["type"] == "OperatorAdded"]
    assert len(ops) >= 1
    print("PASS test_mock_extractor_sequential")


def test_mock_extractor_no_cue_words():
    text = "Alice: The sky is blue today.\nBob: I agree with that observation."
    api, log = _api()
    MockExtractor().run(text, "test.txt", api)
    events = log.read_all()
    # Points added but no operators (no cue words)
    ops = [e for e in events if e["type"] == "OperatorAdded"]
    assert len(ops) == 0
    assert len([e for e in events if e["type"] == "PointAdded"]) >= 2
    print("PASS test_mock_extractor_no_cue_words")


def test_mock_extractor_single_utterance():
    text = "Alice: This is a test because we need to verify."
    api, log = _api()
    MockExtractor().run(text, "test.txt", api)
    events = log.read_all()
    # One point, no operator (need prev_pid)
    ops = [e for e in events if e["type"] == "OperatorAdded"]
    assert len(ops) == 0
    print("PASS test_mock_extractor_single_utterance")


def test_mock_extractor_multi_source_fallback():
    """multi_source=True without embeddings triggers all-pairs fallback."""
    api, log = _api()
    text = ("Alice: We should invest in better infrastructure because growth depends on it.\n"
            "Bob: The cost analysis shows however that returns are diminishing.")
    # Patch to force ImportError for embeddings
    import builtins
    _orig_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if "embeddings" in name:
            raise ImportError("fake missing")
        return _orig_import(name, *args, **kwargs)

    builtins.__import__ = _fake_import
    try:
        MockExtractor().run(text, "test.txt", api, multi_source=True)
    finally:
        builtins.__import__ = _orig_import

    events = log.read_all()
    ops = [e for e in events if e["type"] == "OperatorAdded"]
    # Should find at least one operator from cue words
    assert len(ops) >= 1
    print("PASS test_mock_extractor_multi_source_fallback")


# ── MockModel ────────────────────────────────────────────────────────

def test_mock_model_points():
    m = MockModel()
    utts = {"0": "hello world", "1": "goodbye world"}
    user = json.dumps({"context": "test", "utterances": utts})
    out = m.complete(system="TASK: extract_points", user=user)
    data = json.loads(out)
    assert "points" in data
    assert data["points"]["0"] == "hello world"
    print("PASS test_mock_model_points")


def test_mock_model_relations():
    m = MockModel()
    pts = [{"content": "This is important because it works"},
           {"content": "But however there are issues"}]
    user = json.dumps({"context": "test", "points": pts})
    out = m.complete(system="TASK: extract_relations", user=user)
    data = json.loads(out)
    assert "relations" in data
    # Should find IMPL from "because" and NAND from "but"
    types = [r["op_type"] for r in data["relations"]]
    assert "IMPL" in types or "NAND" in types
    print("PASS test_mock_model_relations")


def test_mock_model_unknown():
    m = MockModel()
    out = m.complete(system="TASK: unknown_task", user="{}")
    assert out == "{}"
    print("PASS test_mock_model_unknown")


# ── _json ────────────────────────────────────────────────────────────

def test_json_plain():
    assert _json('{"a": 1}') == {"a": 1}
    print("PASS test_json_plain")


def test_json_fenced():
    assert _json('```json\n{"b": 2}\n```') == {"b": 2}
    print("PASS test_json_fenced")


def test_json_think_tags():
    text = "<think>reasoning here</think>\n{\"c\": 3}"
    assert _json(text) == {"c": 3}
    print("PASS test_json_think_tags")


def test_json_preamble():
    text = "Sure, here is the output:\n{\"d\": 4}"
    assert _json(text) == {"d": 4}
    print("PASS test_json_preamble")


def test_json_invalid():
    try:
        _json("no json here at all")
        assert False, "should raise"
    except ValueError:
        pass
    print("PASS test_json_invalid")


# ── _overlap ─────────────────────────────────────────────────────────

def test_overlap_full():
    assert _overlap("hello world", "hello world") == 1.0
    assert _overlap("hello world test", "hello world xyz") == 2 / 3  # 2 of 3 source words match
    print("PASS test_overlap_full")


def test_overlap_different():
    assert _overlap("abc def", "xyz uvw") == 0.0
    print("PASS test_overlap_different")


def test_overlap_empty_source():
    assert _overlap("anything", "") == 1.0  # empty source → 1.0
    print("PASS test_overlap_empty_source")


# ── LLMExtractor with MockModel ──────────────────────────────────────

def test_llm_extractor_end_to_end_mock():
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    api, log = _api()
    text = ("Alice: I think we should use BFS because it is simple.\n"
            "Bob: But however the memory usage is concerning.")
    ext.run(text, "sample.txt", api)
    events = log.read_all()
    types = [e["type"] for e in events]
    assert "PointAdded" in types
    assert "OperatorAdded" in types
    print("PASS test_llm_extractor_end_to_end_mock")


def test_llm_extractor_document_mode():
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    api, log = _api()
    text = ("## Architecture\n"
            "The architecture should use event sourcing because it provides "
            "an audit trail and enables replay for debugging purposes.\n\n"
            "## Tradeoffs\n"
            "However the complexity increases with event sourcing but the "
            "benefits of having a complete history outweigh the costs.")
    ext.run(text, "design.md", api)
    events = log.read_all()
    # Should create points for each section
    points = [e for e in events if e["type"] == "PointAdded"]
    assert len(points) >= 2
    print("PASS test_llm_extractor_document_mode")


def test_llm_extractor_max_utterances():
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    api, log = _api()
    text = "\n".join(f"Speaker{i}: This is utterance number {i} with enough text." for i in range(10))
    ext.run(text, "many.txt", api, max_utterances=3)
    events = log.read_all()
    points = [e for e in events if e["type"] == "PointAdded"]
    assert len(points) == 3
    print("PASS test_llm_extractor_max_utterances")


# ── Multi-source with embeddings (success path) ──────────────────────

def test_mock_extractor_multi_source_embedding():
    """Multi-source mode cue-gate still creates operators for near-identical claims.

    #399 note: within a single transcript every point shares one source_id, so
    lens_key="source" yields NO cross-lens candidates → this exercises the
    all-pairs cue-gate fallback. Real cross-lens candidates require multi-source
    aggregation (#6306). The mocked tests above pin the candidate path.
    """
    api, log = _api()
    # Near-identical claims from different speakers so TF-IDF cosine > 0.40
    # Must be >40 chars and contain stance word for _is_claim
    text = (
        "Alice: The falkordb database provides excellent traversal performance for "
        "graph queries and supports complex algorithms because it scales deployment production system.\n"
        "Bob: The falkordb database provides excellent traversal performance for "
        "graph queries and supports complex algorithms but the cost deployment production environment is a concern."
    )
    MockExtractor().run(text, "test.txt", api, multi_source=True)
    events = log.read_all()
    ops = [e for e in events if e["type"] == "OperatorAdded"]
    assert len(ops) >= 1, f"expected operators from embedding path"
    print("PASS test_mock_extractor_multi_source_embedding")


def _mock_cross_lens(points, *, threshold=0.40, lens_key=None, encode=None):
    """Deterministic stand-in: pair the first two points with similarity 0.45."""
    import tortoise.cross_lens as cl  # noqa: F401 — ensure module importable
    ids = list(points)
    if len(ids) >= 2:
        return [{"src": ids[0], "dst": ids[1], "similarity": 0.45,
                 "lenses": [str(points[ids[0]].get("source", "s")),
                            str(points[ids[1]].get("source", "s"))],
                 "speakers": [points[ids[0]].get("speaker", "unknown"),
                              points[ids[1]].get("speaker", "unknown")],
                 "degraded": False}]
    return []


def test_mock_extractor_multi_source_semantic_agreement():
    """#399: candidate pair + SUPPORT cue → IMPL; recorded.

    The old ≥3-shared-content-words semantic-agreement gate is removed — a
    similarity-matched pair (even with few shared words) with a support cue
    creates an IMPL. (True zero-overlap pairs are covered by the real-embedder
    tests in test_cross_lens.py.)
    """
    import tortoise.cross_lens as cl
    api, log = _api()
    text = (
        "Alice: Winning requires strong go to market and channel partners because "
        "growth depends on distribution.\n"
        "Bob: Growth depends on distribution channels and partnerships is core."
    )
    _orig = cl.find_cross_lens_matches
    cl.find_cross_lens_matches = _mock_cross_lens
    try:
        ex = MockExtractor()
        ex.run(text, "test.txt", api, multi_source=True)
    finally:
        cl.find_cross_lens_matches = _orig
    events = log.read_all()
    ops = [e for e in events if e["type"] == "OperatorAdded"]
    assert len(ops) == 1 and ops[0]["point"]["operator"]["op_type"] == "IMPL", \
        f"expected exactly 1 IMPL (cue direction, zero shared words), got {len(ops)}"
    assert ex._last_candidates and ex._last_candidates[0]["similarity"] == 0.45
    print("PASS test_mock_extractor_multisource_semantic_agreement")


def test_mock_extractor_multi_source_refute_cue_nand():
    """#399: cross-vocabulary pair + REFUTE cue → NAND (no support cue present)."""
    import tortoise.cross_lens as cl
    api, log = _api()
    text = ("Alice: Growth depends on distribution channels and partnerships is core.\n"
            "Bob: Winning requires strong go to market and channel partners but "
            "the strategy is unproven.")
    _orig = cl.find_cross_lens_matches
    cl.find_cross_lens_matches = _mock_cross_lens
    try:
        ex = MockExtractor()
        ex.run(text, "test.txt", api, multi_source=True)
    finally:
        cl.find_cross_lens_matches = _orig
    ops = [e for e in log.read_all() if e["type"] == "OperatorAdded"]
    assert len(ops) == 1 and ops[0]["point"]["operator"]["op_type"] == "NAND", \
        f"expected exactly 1 NAND, got {len(ops)}"
    # Pin the candidate path (not the all-pairs fallback): _last_candidates is
    # only populated by the cross-lens branch.
    assert len(ex._last_candidates) == 1 and ex._last_candidates[0]["similarity"] == 0.45
    print("PASS test_mock_extractor_multi_source_refute_cue_nand")


def test_mock_extractor_multi_source_degraded_candidates_fallback():
    """#399: candidates marked degraded (TF-IDF) → extractor discards them and
    runs the pre-#399 all-pairs cue-gate (documented degraded semantics)."""
    import tortoise.cross_lens as cl
    api, log = _api()
    text = ("Alice: Growth depends on distribution channels and partnerships but "
            "returns are diminishing.\n"
            "Bob: Growth depends on distribution channels and partnerships is core.")
    _orig = cl.find_cross_lens_matches

    def _degraded(points, *, threshold=0.40, lens_key=None, encode=None):
        ids = list(points)
        if len(ids) >= 2:
            return [{"src": ids[0], "dst": ids[1], "similarity": 0.45,
                     "lenses": ["s", "s"], "speakers": ["a", "b"], "degraded": True}]
        return []

    cl.find_cross_lens_matches = _degraded
    try:
        ex = MockExtractor()
        ex.run(text, "test.txt", api, multi_source=True)
    finally:
        cl.find_cross_lens_matches = _orig
    ops = [e for e in log.read_all() if e["type"] == "OperatorAdded"]
    # All-pairs fallback: Alice's "but" cue → NAND against Bob even though the
    # pair is not a cross-lens candidate (degraded path discards candidates).
    assert len(ops) == 1 and ops[0]["point"]["operator"]["op_type"] == "NAND", \
        f"degraded path must run all-pairs cue-gate, got {len(ops)} ops"
    assert ex._last_candidates and ex._last_candidates[0]["degraded"] is True
    print("PASS test_mock_extractor_multi_source_degraded_candidates_fallback")


def test_mock_extractor_multi_source_same_source_no_candidates():
    """Real module, single source: lens_key="source" → all points same lens →
    no candidates, _last_candidates empty, operators only from cue-gate fallback."""
    api, log = _api()
    text = ("Alice: Growth depends on distribution channels and partnerships is core.\n"
            "Bob: Growth depends on distribution channels and partnerships is also "
            "essential for scale.")
    ex = MockExtractor()
    ex.run(text, "test.txt", api, multi_source=True)
    # Same source → same lens → no cross-lens candidates (documented #6306 gap).
    assert ex._last_candidates == []
    ops = [e for e in log.read_all() if e["type"] == "OperatorAdded"]
    assert len(ops) >= 0  # operators (if any) come from the cue-gate fallback only
    print("PASS test_mock_extractor_multi_source_same_source_no_candidates")


def test_mock_extractor_multi_source_no_cue_no_operator_but_recorded():
    """#399: matched cross-vocab pair WITHOUT cue words → no operator, recorded.

    Candidates never become operators from similarity alone (EP-safety boundary).
    """
    import tortoise.cross_lens as cl
    api, log = _api()
    text = ("Alice: Growth depends on distribution channels and partnerships is core.\n"
            "Bob: Winning requires strong go to market and channel partners is "
            "the winning approach.")
    _orig = cl.find_cross_lens_matches
    cl.find_cross_lens_matches = _mock_cross_lens
    try:
        ex = MockExtractor()
        ex.run(text, "test.txt", api, multi_source=True)
    finally:
        cl.find_cross_lens_matches = _orig
    ops = [e for e in log.read_all() if e["type"] == "OperatorAdded"]
    assert ops == [], f"similarity alone must not create operators, got {len(ops)}"
    assert len(ex._last_candidates) == 1, "candidate must be recorded for #6306"
    print("PASS test_mock_extractor_multi_source_no_cue_no_operator_but_recorded")


# ── _PointStage.run with list output ────────────────────────────────

def test_point_stage_list_output():
    """_PointStage.run handles model returning a list instead of dict."""
    from tortoise.extractor import _PointStage

    class ListModel:
        id = "list-model"
        def complete(self, *, system, user):
            # Return points as a list of dicts with 'content' key
            return ('{"points": [{"content": "cleaned text one", "src": 0}, '
                    '{"content": "cleaned text two", "i": 1}]}')

    stage = _PointStage(ListModel())
    result = stage.run(["raw text one", "raw text two"], "ctx")
    assert result[0] == "cleaned text one"
    assert result[1] == "cleaned text two"
    print("PASS test_point_stage_list_output")


def test_point_stage_list_strings():
    """_PointStage.run handles model returning a list of plain strings."""
    from tortoise.extractor import _PointStage

    class StringListModel:
        id = "str-model"
        def complete(self, *, system, user):
            return '{"points": ["first point", "second point"]}'

    stage = _PointStage(StringListModel())
    result = stage.run(["raw1", "raw2"], "ctx")
    assert result[0] == "first point"
    assert result[1] == "second point"
    print("PASS test_point_stage_list_strings")


# ── LLMExtractor: out-of-bounds relation index (line 409) ──────────

def test_llm_extractor_bad_relation_index():
    """LLMExtractor skips relations with out-of-bounds src/dst indices."""
    class BadRelationModel:
        id = "bad-rel"
        def complete(self, *, system, user):
            return '{"relations": ['\
                   '{"op_type": "IMPL", "src": 0, "dst": 1},'\
                   '{"op_type": "NAND", "src": 99, "dst": 100},'\
                   '{"op_type": "IMPL", "src": null, "dst": 0}]}'

    ext = LLMExtractor(MockModel("cheap"), BadRelationModel())
    api, log = _api()
    text = ("Alice: This is a test because we need coverage here.\n"
            "Bob: But however the results need more verification.")
    ext.run(text, "sample.txt", api)
    events = log.read_all()
    ops = [e for e in events if e["type"] == "OperatorAdded"]
    # Only the first valid relation (0→1) should be added; bad indices skipped
    assert len(ops) == 1, f"expected 1 valid operator, got {len(ops)}"
    print("PASS test_llm_extractor_bad_relation_index")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall extractor tests passed")


if __name__ == "__main__":
    _run_all()
