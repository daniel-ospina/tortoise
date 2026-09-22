"""#1785 Task 2 — resume-scan parity + checkpoint schema (runner side).

The #1764 resume-quality gate and the new integrity gate must not disagree
on resume: a gated outcome (shape-OK, would otherwise load byte-identical
and re-enter aggregates) is refused by the runner's OWN load path
(``resume_gate_reject_reason`` — the shared single source of truth) with
the UNION of the phase-keyed reason lists; ``gate_reasons`` never joins
REQUIRED_OUTCOME_KEYS (pre-change checkpoints resume identically); the
gate-config knobs are fingerprint-excluded (a knob change must not alter
resume-eligibility); run-level abort markers (``degraded_aborted`` /
``checkpoint_abort``) refuse the runner's own resume path.
"""
from __future__ import annotations

import json

import pytest

from tests.longmem_eval.test_vector_arm import _minimal_outcome
from tools.longmem_eval import run as runner
from tools.longmem_eval.retrieve import (
    GATE_REASON_CENSUS_ERROR,
    GATE_REASON_GRAPH_TRUNCATED,
)
from tools.longmem_eval.run import (
    REQUIRED_OUTCOME_KEYS,
    _build_fingerprint,
    _save_checkpoint,
    resume_gate_reject_reason,
)


def _outcome(qid: str = "mini_ie_user_001", **over) -> dict:
    out = _minimal_outcome(qid)
    out["legs"] = [{"leg": "fts", "reason": "ok", "count": 25},
                   {"leg": "vector", "reason": "ok", "count": 120},
                   {"leg": "structural", "reason": "ok", "count": 120}]
    out["session_recall@k"] = {"5": 1.0, "20": 1.0}
    out["turn_recall@k"] = {"5": 1.0, "20": 1.0}
    out.update(over)
    return out


# ── gate-reasons refusal (shared predicate) ─────────────────────────────────

def test_resume_refuses_graph_truncated_outcome():
    out = _outcome(gate_reasons=[GATE_REASON_GRAPH_TRUNCATED])
    reason = resume_gate_reject_reason(out)
    assert reason is not None
    assert "gate-red" in reason


def test_resume_refuses_pre_green_post_red_union():
    # a pre-green/post-red outcome (gate_reasons == [] with non-empty
    # post_retrieval_reasons — H6 loss between gate and retrieval) is
    # refused via the UNION of the phase-keyed lists (plan P1-3).
    out = _outcome(gate_reasons=[], post_retrieval_reasons=[GATE_REASON_CENSUS_ERROR])
    reason = resume_gate_reject_reason(out)
    assert reason is not None
    assert "census_error" in reason


def test_resume_accepts_gate_green_outcome():
    out = _outcome(gate_reasons=[], post_retrieval_reasons=[])
    assert resume_gate_reject_reason(out) is None


def test_resume_accepts_absent_gate_keys():
    # a pre-change outcome has NO gate_reasons key — resumes identically
    # (the .get [] default; never a required key).
    out = _outcome()
    assert "gate_reasons" not in out
    assert resume_gate_reject_reason(out) is None


def test_gate_reasons_not_in_required_keys():
    assert "gate_reasons" not in REQUIRED_OUTCOME_KEYS["hybrid"]
    assert "gate_reasons" not in REQUIRED_OUTCOME_KEYS["vector"]
    assert "post_retrieval_reasons" not in REQUIRED_OUTCOME_KEYS["hybrid"]


def test_resume_refuses_gated_abstention():
    # the abstention exemption is a GATE-side presence skip; a truncated
    # graph on an abstention question is still integrity loss and IS
    # refused on resume (sessionless exemption runs AFTER the gate check).
    out = _outcome(gate_reasons=[GATE_REASON_GRAPH_TRUNCATED])
    out["turn_recall@k"] = {"5": None, "20": None}  # _legit_sessionless shape
    reason = resume_gate_reject_reason(out)
    assert reason is not None


# ── old-format checkpoint resumes identically ───────────────────────────────

def test_old_format_checkpoint_resumes_identically(tmp_path):
    cp = tmp_path / "state.json"
    outcome = _outcome("mini_ie_user_001")  # no gate_reasons key
    _save_checkpoint(str(cp), [outcome], [],
                     run_key="embedded__hybrid__minilm__default",
                     surface="embedded", retriever="hybrid",
                     model="minilm", prompt=None)
    done, _ = runner._load_checkpoint(
        str(cp), run_key="embedded__hybrid__minilm__default",
        retriever="hybrid")
    assert "mini_ie_user_001" in done


# ── run-level abort markers refuse the runner's own resume path ─────────────

def test_degraded_aborted_marker_refuses_resume(tmp_path):
    cp = tmp_path / "state.json"
    outcome = _outcome("mini_ie_user_001")
    _save_checkpoint(str(cp), [outcome], [],
                     run_key="embedded__hybrid__minilm__default",
                     surface="embedded", retriever="hybrid",
                     model="minilm", prompt=None,
                     degraded_aborted={"reason": "gate_red"})
    with pytest.raises(runner.CheckpointStaleError, match="degraded-aborted"):
        runner._load_checkpoint(
            str(cp), run_key="embedded__hybrid__minilm__default",
            retriever="hybrid")


def test_checkpoint_abort_marker_refuses_resume(tmp_path):
    cp = tmp_path / "state.json"
    outcome = _outcome("mini_ie_user_001")
    _save_checkpoint(str(cp), [outcome], [],
                     run_key="embedded__hybrid__minilm__default",
                     surface="embedded", retriever="hybrid",
                     model="minilm", prompt=None,
                     checkpoint_abort={"reason": "persist failed"})
    with pytest.raises(runner.CheckpointStaleError, match="checkpoint-abort"):
        runner._load_checkpoint(
            str(cp), run_key="embedded__hybrid__minilm__default",
            retriever="hybrid")


def test_corrupt_checkpoint_still_quarantines(tmp_path):
    """A corrupt checkpoint quarantines + refuses (existing #1786 pin) —
    at resume of a revalidate run a corrupt checkpoint must NOT silently
    become an indistinguishable fresh re-run (plan cycle2-P2-26)."""
    cp = tmp_path / "state.json"
    cp.write_text("{not json", encoding="utf-8")
    with pytest.raises(runner.CheckpointStaleError, match="corrupt"):
        runner._load_checkpoint(
            str(cp), run_key="embedded__hybrid__minilm__default",
            retriever="hybrid")


# ── fingerprint-excluded gate knobs ─────────────────────────────────────────

def test_gate_knobs_excluded_from_fingerprint():
    """The gate-config knobs (read-verify retry N, floor tolerance, query
    budget, timeout) and the per-session-census replay flag are EXCLUDED
    from the run fingerprint — a knob change must never alter resume-
    eligibility of pre-change checkpoints (plan Task 2 / §5 artifacts)."""
    base = _build_fingerprint(
        reader_model="r", judge_model="j", ks=(5,), top_k=5,
        split="s", ingest_mode="deterministic", extractor_model=None,
        max_retries=3, dataset_fingerprint="d", rerank_config={})
    # the fingerprint builder takes no gate knobs — the assertion pins that
    # no TORTOISE_LME_GATE_* env or per-session-census flag can enter it.
    for knob in ("TORTOISE_LME_GATE_RETRY_N", "TORTOISE_LME_GATE_FLOOR_T",
                 "TORTOISE_LME_GATE_QUERY_Q", "TORTOISE_LME_GATE_TIMEOUT_MS"):
        assert knob not in base
    assert "per_session_census" not in base


def test_resume_eligibility_independent_of_per_session_census(tmp_path):
    """The per-session-census debug flag is fingerprint-excluded — its
    presence/absence does not change resume eligibility (plan Task 3 P3)."""
    cp = tmp_path / "state.json"
    outcome = _outcome("mini_ie_user_001")
    _save_checkpoint(str(cp), [outcome], [],
                     run_key="embedded__hybrid__minilm__default",
                     surface="embedded", retriever="hybrid",
                     model="minilm", prompt=None)
    # the checkpoint carries no per_session_census field
    data = json.loads(cp.read_text(encoding="utf-8"))
    assert "per_session_census" not in data
    done, _ = runner._load_checkpoint(
        str(cp), run_key="embedded__hybrid__minilm__default",
        retriever="hybrid")
    assert "mini_ie_user_001" in done
