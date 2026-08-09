"""M2 tests — two-stage LLM extractor (offline, via MockModel) + adapter surface.

Runnable without pytest:  .venv/bin/python tests/test_m2.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI                       # noqa: E402
from tortoise.extractor import LLMExtractor, MockModel  # noqa: E402
from tortoise.idempotency import document_key           # noqa: E402
from tortoise.log import EventLog                        # noqa: E402
from tortoise.models import OllamaModel, OpenAICompatModel  # noqa: E402
from tortoise.projection import fold, split              # noqa: E402

SAMPLE = os.path.join(os.path.dirname(__file__), "sample_transcript.txt")


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def test_llm_extractor_end_to_end():
    text = open(SAMPLE, encoding="utf-8").read()
    log = EventLog(_tmp("events.jsonl"))
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    api = EventAPI(log, initiated_by="extractor", agent_id=ext.version)

    r = api.begin_ingest(SAMPLE, ext.version, document_key(text))
    assert not r.skip
    ext.run(text, "sample", api)

    points = fold(log.read_all())
    statements, operators = split(points)
    assert len(statements) >= 6 and len(operators) >= 3

    # provenance spans still slice back to the source quote (grounding invariant)
    for p in points.values():
        s, e = p["provenance"]["span"]
        assert text[s:e].strip() == p["provenance"]["quote"].strip()

    # operators are valid gates with resolved inputs
    ids = set(points)
    for op in operators:
        o = op["operator"]
        assert o["op_type"] in ("NAND", "IMPL")
        assert all(i in ids for i in o["inputs"])

    # version stamped for eval slicing / idempotency
    assert ext.version == "cheap/reason@v2"
    assert all(p["provenance"]["extracted_by"] == ext.version for p in points.values())
    print("PASS test_llm_extractor_end_to_end")


def test_reingest_same_version_skips():
    text = open(SAMPLE, encoding="utf-8").read()
    log = EventLog(_tmp("events.jsonl"))
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    api = EventAPI(log, initiated_by="extractor")
    assert not api.begin_ingest(SAMPLE, ext.version, document_key(text)).skip
    ext.run(text, "sample", api)
    n1 = len(fold(log.read_all()))
    # accidental re-run at same version → skip, graph unchanged
    assert api.begin_ingest(SAMPLE, ext.version, document_key(text)).skip
    assert len(fold(log.read_all())) == n1
    print("PASS test_reingest_same_version_skips")


def test_openai_compat_request_and_parse():
    m = OpenAICompatModel(id="deepseek-chat", base_url="https://api.example.com/v1")
    req = m.build_request("SYS", "USER")
    assert req["model"] == "deepseek-chat"
    assert req["response_format"] == {"type": "json_object"}
    assert req["messages"][0] == {"role": "system", "content": "SYS"}
    assert req["messages"][1] == {"role": "user", "content": "USER"}
    fake = {"choices": [{"message": {"content": '{"points":[]}'}}]}
    assert OpenAICompatModel.parse_response(fake) == '{"points":[]}'
    print("PASS test_openai_compat_request_and_parse")


def test_ollama_request_and_parse():
    m = OllamaModel(id="qwen3:4b")
    req = m.build_request("SYS", "USER")
    assert req["model"] == "qwen3:4b"
    assert req["think"] is False and req["format"] == "json" and req["stream"] is False
    fake = {"message": {"content": '{"points":{}}', "thinking": None}}
    assert OllamaModel.parse_response(fake) == '{"points":{}}'
    print("PASS test_ollama_request_and_parse")


class _MismapModel:
    """Point model that mis-maps: returns cleaned text for index 0 that is a
    cleaning of a DIFFERENT utterance. The overlap guard must reject it."""
    id = "mismap"

    def complete(self, *, system, user):
        import json as _j
        if "extract_points" in system:
            return _j.dumps({"points": {"0": "penguins waddle across antarctic ice"}})
        return _j.dumps({"relations": []})


def test_overlap_guard_rejects_mismap():
    from tortoise.extractor import LLMExtractor, MockModel, _overlap
    # a light cleaning (strip leading connective) keeps ~all source words;
    # an unrelated sentence does not
    assert _overlap("The schedule has to be unpredictable, not just slow",
                    "So the schedule has to be unpredictable, not just slow") >= 0.5
    assert _overlap("penguins waddle across ice",
                    "We should raise the burn rate slowly") < 0.5

    text = open(SAMPLE, encoding="utf-8").read()
    log = EventLog(_tmp("events.jsonl"))
    LLMExtractor(_MismapModel(), MockModel("r")).run(text, "s",
        EventAPI(log, initiated_by="extractor"))
    stmts, _ = split(fold(log.read_all()))
    first = min(stmts, key=lambda p: p["createdAt"])
    # mis-mapped content rejected → fell back to the verbatim utterance
    assert first["content"] == first["provenance"]["quote"]
    print("PASS test_overlap_guard_rejects_mismap")


def test_tolerant_json_parse():
    from tortoise.extractor import _json
    assert _json('{"points": []}') == {"points": []}
    assert _json('```json\n{"relations": [{"op_type":"IMPL"}]}\n```') == \
        {"relations": [{"op_type": "IMPL"}]}
    assert _json('Sure! Here is the JSON:\n{"points":[{"src":0}]}') == \
        {"points": [{"src": 0}]}
    print("PASS test_tolerant_json_parse")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall M2 tests passed")


if __name__ == "__main__":
    _run_all()
