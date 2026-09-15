"""#2517 (C4): hermetic tests for the cohort builder.

``tools/longmem_eval/build_cohorts.py`` is the only guard between an
unverified corpus and the measurement denominator (the run side,
``dataset.load_dataset``, performs no digest check). Its selectors, its
refusals and its provenance sidecar are therefore pinned here — no graph,
no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.longmem_eval import build_cohorts as bc


def _instance(qid: str, qtype: str = "single-session-user") -> dict:
    return {"question_id": qid, "question_type": qtype, "question": qid}


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_selectors_are_pinned():
    assert bc.TAIL_SLICE == (150, 250)
    assert bc.HEAD_TYPE == "single-session-user"
    assert bc.HEAD_N == 50


def test_build_cohorts_slices_both_cohorts_in_source_order(tmp_path):
    instances = [_instance(f"q{i}", "multi-session") for i in range(260)]
    instances[255] = _instance("head-a", "single-session-user")
    instances[5] = _instance("head-b", "single-session-user")
    src = _write(tmp_path / "corpus.json", instances)
    cohorts = bc.build_cohorts(src)
    assert [q["question_id"] for q in cohorts["tail"]] == \
        [f"q{i}" for i in range(150, 250)]
    # head = the first HEAD_N single-session-user instances, source order
    assert [q["question_id"] for q in cohorts["head"]] == \
        ["head-b", "head-a"]
    assert "q150" in [q["question_id"] for q in cohorts["tail"]]


def test_build_cohorts_accepts_a_jsonl_source(tmp_path):
    """The run side (``--data``) accepts JSONL via ``dataset._read_instances``;
    the builder must read the same corpus forms instead of raising a bare
    ``json.JSONDecodeError`` on a JSONL source."""
    lines = [json.dumps(_instance(f"q{i}")) for i in range(160)]
    src = tmp_path / "corpus.jsonl"
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cohorts = bc.build_cohorts(src)
    assert len(cohorts["tail"]) == 10
    assert cohorts["tail"][0]["question_id"] == "q150"


def test_verify_source_pins_a_known_split_and_refuses_a_mismatch(
        tmp_path, monkeypatch):
    name = "longmemeval_s_cleaned.json"
    src = _write(tmp_path / name, [])
    monkeypatch.setattr(bc, "SPLIT_FILES", {"s": name})
    monkeypatch.setattr(bc, "SPLIT_DIGESTS", {"s": "0" * 64})
    with pytest.raises(SystemExit) as exc:
        bc._verify_source(src)
    assert "does not match the pinned digest" in str(exc.value)
    # the matching digest verifies
    monkeypatch.setattr(bc, "SPLIT_DIGESTS", {"s": bc._sha256(src)})
    digest, verified = bc._verify_source(src)
    assert verified is True and digest == bc._sha256(src)


def test_verify_source_refuses_an_unpinned_corpus_unless_overridden(
        tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "SPLIT_FILES", {})
    monkeypatch.setattr(bc, "SPLIT_DIGESTS", {})
    src = _write(tmp_path / "unknown.json", [])
    with pytest.raises(SystemExit) as exc:
        bc._verify_source(src)
    assert "not a pinned split corpus" in str(exc.value)
    digest, verified = bc._verify_source(src, allow_unpinned=True)
    assert verified is False and len(digest) == 64


def test_main_writes_the_provenance_sidecar(tmp_path, monkeypatch):
    """The override's provenance must be PERSISTED next to the cohorts — a
    cohort sliced from an unverified corpus must be distinguishable from a
    digest-verified one."""
    monkeypatch.setattr(bc, "SPLIT_FILES", {})
    monkeypatch.setattr(bc, "SPLIT_DIGESTS", {})
    src = _write(tmp_path / "unknown.json",
                 [_instance(f"q{i}") for i in range(260)])
    assert bc.main(["--source", str(src), "--out-dir", str(tmp_path),
                    "--cohort", "tail",
                    "--allow-unpinned-source"]) == 0
    assert (tmp_path / "longmemeval_2517_tail.json").exists()
    assert not (tmp_path / "longmemeval_2517_head.json").exists()
    written = json.loads(
        (tmp_path / "longmemeval_2517_tail.json").read_text())
    prov = json.loads(
        (tmp_path / "longmemeval_2517.provenance.json").read_text())
    assert prov["verified"] is False
    assert prov["source"] == str(src)
    assert prov["source_sha256"] == bc._sha256(src)
    assert prov["selectors"]["tail"] == list(bc.TAIL_SLICE)
    assert prov["questions"] == {"tail": len(written)}


def test_main_refuses_an_unpinned_corpus_without_the_override(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bc, "SPLIT_FILES", {})
    monkeypatch.setattr(bc, "SPLIT_DIGESTS", {})
    src = _write(tmp_path / "unknown.json", [_instance("q0")])
    with pytest.raises(SystemExit) as exc:
        bc.main(["--source", str(src), "--out-dir", str(tmp_path)])
    assert "unknown.json" in str(exc.value)
    assert not (tmp_path / "longmemeval_2517_tail.json").exists()
    assert not (tmp_path / "longmemeval_2517.provenance.json").exists()


def test_main_refuses_a_missing_source_without_slicing(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "SPLIT_FILES", {})
    monkeypatch.setattr(bc, "SPLIT_DIGESTS", {})
    with pytest.raises(SystemExit) as exc:
        bc.main(["--source", str(tmp_path / "nope.json"),
                 "--out-dir", str(tmp_path)])
    assert "not found" in str(exc.value)
