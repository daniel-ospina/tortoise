"""M5 reader pinning tests — model + prompt constants for the run (#1525).

The V2 runs were confounded by reader-model drift between runs
(deepseek-chat → deepseek-v4-flash); M5 makes the code default equal the
run constant (READER_MODEL), records the reader's resolved identity +
the verbatim prompt constants in the report methodology, and fixes the
endpoint-truth gap (named provider becomes authoritative for routing).
Soft pin only — record + warn; hard enforcement is M7's checkpoint
fingerprint.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval.reader import (
    READER_MODEL,
    LLMReader,
    build_reader,
)


def test_reader_model_is_pinned_constant():
    assert READER_MODEL == "openrouter:deepseek/deepseek-v4-flash"


def test_default_build_resolves_to_pin(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    r = build_reader()
    assert isinstance(r, LLMReader)
    assert r.model_spec == READER_MODEL
    assert r.model_id == "deepseek/deepseek-v4-flash"
    assert r.provider == "openrouter"
    assert r.pinned is True


def test_override_is_not_pinned_and_warns(monkeypatch, capsys):
    """The v2 confound made loud: a non-pinned reader warns + records false."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    r = build_reader("openrouter:deepseek/deepseek-chat")
    assert r.pinned is False
    assert "NOT pinned" in capsys.readouterr().err


def test_report_methodology_records_prompt_constants(tmp_path):
    import json

    from tools.longmem_eval.judge import MockJudge
    from tools.longmem_eval.reader import _SYSTEM_PROMPT, _TYPE_FRAGMENTS, MockReader
    from tools.longmem_eval.run import run_evaluation
    MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"
    instances = json.loads(MINI.read_text(encoding="utf-8"))
    outcomes, report = run_evaluation(  # noqa: RUF059
        instances, reader=MockReader(), judge=MockJudge(),
        ks=(5, 10, 20), top_k=20, split="s", work_dir=str(tmp_path))
    m = report["methodology"]
    assert m["reader_model"] == "mock-reader"
    assert m["reader_model_spec"] == "mock-reader"
    assert m["reader_provider"] == "mock"
    assert m["reader_pinned"] is None          # mock: pin N/A
    assert m["reader_system_prompt"] == _SYSTEM_PROMPT
    assert m["reader_type_fragments"] == _TYPE_FRAGMENTS
    # parity unchanged-check input must not move (no-hash-infra boundary)
    assert m["reader_prompt_hash"]  # still present


def test_named_provider_routes_endpoint(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    r = build_reader("deepseek:deepseek/deepseek-v4-flash")
    assert r.provider == "deepseek"          # spec is authoritative
    r2 = build_reader("deepseek/deepseek-v4-flash")  # bare spec
    assert r2.provider == "openrouter"       # first configured in priority
    assert r2.pinned is False                # bare spec != pinned spec


def test_named_provider_missing_key_still_raises(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    # deterministic regardless of the ambient env (this machine has multiple
    # keys set) — only openrouter is present, deepseek must not resolve
    for k in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError):
        build_reader("deepseek:deepseek/deepseek-v4-flash")


def test_no_keys_still_fail_closed(monkeypatch):
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
              "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError):
        build_reader()


def test_cross_cell_reader_pin_is_stable(tmp_path):
    """Two runs with the same reader produce identical reader methodology;
    a divergent reader is recorded with its real spec — drift visible, never
    silent (the run protocol's cross-cell comparability contract)."""
    import json

    from tools.longmem_eval.dataset_audit import audit_dataset
    from tools.longmem_eval.judge import MockJudge
    from tools.longmem_eval.reader import MockReader
    from tools.longmem_eval.run import outcomes_to_report, run_evaluation
    MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"
    instances = json.loads(MINI.read_text(encoding="utf-8"))

    def _run():
        outcomes, report = run_evaluation(  # noqa: RUF059
            instances, reader=MockReader(), judge=MockJudge(),
            ks=(5,), top_k=20, split="s", work_dir=str(tmp_path))
        m = report["methodology"]
        return (m["reader_model_spec"], m["reader_provider"],
                m["reader_pinned"], m["reader_system_prompt"])

    cell_a, cell_b = _run(), _run()
    assert cell_a == cell_b                       # pinned across cells

    drift = outcomes_to_report(
        [], reader_model="deepseek/deepseek-chat",
        reader_model_spec="openrouter:deepseek/deepseek-chat",
        reader_provider="openrouter", reader_pinned=False,
        judge_model="mock-judge", ks=(5,), top_k=20, split="s",
        # M7 publication gate (E2E-3 Precondition 2).
        dataset_semantics_audit=audit_dataset(instances))
    m = drift["methodology"]
    assert m["reader_model_spec"] == "openrouter:deepseek/deepseek-chat"
    assert m["reader_pinned"] is False            # drift recorded, not hidden
