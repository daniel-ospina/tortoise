"""#2520 (C6) — the run-layer wiring of the time-aware arm.

Covers the three run-surface contracts of the arm (the same shape the
sibling C-arms use, so a future reader can diff them):

  * the CLI tri-state ``--time-aware-qe`` / ``--no-time-aware-qe``
    (``None`` default so the env still applies; the pair is mutually
    exclusive),
  * the fingerprint carries the arm with CONDITIONAL presence (a
    fingerprint-bearing checkpoint is arm-isolated), and
  * the resolved arm + the per-question reorder stats survive the Layer-1
    outcome projection and the methodology.

The parser + fingerprint + projection tests are hermetic (no graph, no
network). The env/CLI resolution is exercised end-to-end through
``run_main`` against the mini fixture — docker lane only (skipped when
FalkorDB is unavailable), because ``run_main`` ingests.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.longmem_eval import run as runner

MINI = Path(__file__).resolve().parent / "fixtures" / "longmemeval_mini.json"

BASE_FP = dict(reader_model="mock-reader", judge_model="mock-judge",
               ks=(5,), top_k=5, split="s", ingest_mode="deterministic",
               extractor_model=None, max_retries=3,
               dataset_fingerprint="x", rerank_config={})


# ── CLI tri-state ───────────────────────────────────────────────────────

def test_cli_parser_tristate_and_mutual_exclusion():
    """FAIL VALUE: a two-valued flag (no ``None`` default → the env can
    never apply), or a pair that accepts both at once. Reachable:
    ``_build_parser`` is the production parser."""
    p = runner._build_parser()
    assert p.parse_args(["--time-aware-qe"]).time_aware_qe is True
    assert p.parse_args(["--no-time-aware-qe"]).time_aware_qe is False
    assert p.parse_args([]).time_aware_qe is None
    with pytest.raises(SystemExit):
        p.parse_args(["--time-aware-qe", "--no-time-aware-qe"])


# ── fingerprint (arm isolation, conditional presence) ───────────────────

def test_fingerprint_carries_the_arm_with_conditional_presence():
    """FAIL VALUE: the arm absent from the fingerprint when passed (an
    armed checkpoint could be resumed without the arm), or present when not
    passed (a fingerprint change for every pre-feature caller). Reachable:
    ``_build_fingerprint`` conditional-presence contract."""
    fp_default = runner._build_fingerprint(**BASE_FP)
    assert "time_aware_qe" not in fp_default, (
        "the arm is conditional-presence — an unarmed caller's fingerprint "
        "must stay byte-identical")
    fp_on = runner._build_fingerprint(**BASE_FP, time_aware_qe=True)
    assert fp_on["time_aware_qe"] is True
    fp_off = runner._build_fingerprint(**BASE_FP, time_aware_qe=False)
    assert fp_off["time_aware_qe"] is False
    assert fp_on != fp_off, "the two arms must fingerprint differently"
    # The arm does not force its siblings into the fingerprint either.
    assert "entity_key_expansion" not in fp_on


# ── the Layer-1 projection carries the arm + stats ──────────────────────

def test_outcome_projection_carries_arm_and_stats():
    """FAIL VALUE: the arm/stats dropped by the projection (the report could
    not reconstruct which arm a question ran on). Reachable:
    ``outcomes_to_report``'s o.get-based allow-list."""
    from tools.longmem_eval.dataset_audit import audit_dataset
    from tools.longmem_eval.run import outcomes_to_report
    outcome = {
        "question_id": "q-time-aware-1",
        "question_type": "single-session-user",
        "question_date": "2026-09-25",
        "label": True,
        "hypothesis": "h",
        "session_recall@k": {"5": 1.0},
        "turn_recall@k": {"5": 1.0},
        "evidence_recall@k": {"5": 1.0},
        "chunk_evidence_recall@k": {"5": 1.0},
        "n_ingest_errors": 0,
        "time_aware_qe": True,
        "time_aware_stats": {"applied": True, "reason": "latest-first",
                             "live": 1, "stale": 1, "tr_excluded": False,
                             "intent": "prefer-latest"},
    }
    report = outcomes_to_report(
        [outcome], reader_model="r", judge_model="j", ks=(5,), top_k=20,
        split="s", r1_knobs={"time_aware_qe": True},
        dataset_semantics_audit=audit_dataset([{
            "question_id": "q-audit", "haystack_session_ids": ["s0"],
            "answer_session_ids": ["s0"],
            "haystack_sessions": [[
                {"role": "user", "content": "x", "has_answer": True}]],
        }]))
    proj = report["outcomes"][0]
    assert proj["time_aware_qe"] is True
    assert proj["time_aware_stats"] == {
        "applied": True, "reason": "latest-first", "live": 1, "stale": 1,
        "tr_excluded": False, "intent": "prefer-latest"}
    # The arm also rides the methodology (published numbers carry the arm).
    assert report["methodology"]["time_aware_qe"] is True


# ── the run-level env/CLI resolution (docker lane) ──────────────────────

def _falkordb_available() -> bool:
    uri = os.environ.get(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")
    old = os.environ.get("TORTOISE_DB_URI")
    try:
        os.environ["TORTOISE_DB_URI"] = f"{uri}_probe2520run"
        from tortoise.sdk import TortoiseSDK as _ProbeSDK
        _probe = _ProbeSDK()
        _probe._get_proj().g.query("RETURN 1")
        _probe.close()
        return True
    except Exception:
        return False
    finally:
        if old is not None:
            os.environ["TORTOISE_DB_URI"] = old
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT and the arm env unset (the OFF-arm tests assert
    the documented default). Matches the sibling C-arm test modules."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))
    monkeypatch.delenv("TORTOISE_LME_TIME_AWARE_QE", raising=False)


@pytest.mark.skipif(not _falkordb_available(),
                    reason="requires TORTOISE_DB_URI (the run ingests)")
def test_run_level_tristate_and_methodology(monkeypatch, tmp_path):
    """CLI flag > env > OFF at the run level, and the resolved arm reaches
    the methodology (methodology == actual)."""
    monkeypatch.setenv("TORTOISE_LME_TIME_AWARE_QE", "1")
    base = ["--data", str(MINI), "--limit", "1", "--split", "s", "--mock",
            "--skip-preflight", "--output", str(tmp_path / "r.json")]
    off = runner.run_main([*base, "--no-time-aware-qe"])
    assert off["methodology"]["time_aware_qe"] is False
    monkeypatch.delenv("TORTOISE_LME_TIME_AWARE_QE", raising=False)
    on = runner.run_main([*base, "--time-aware-qe",
                          "--output", str(tmp_path / "r2.json")])
    assert on["methodology"]["time_aware_qe"] is True


@pytest.mark.skipif(not _falkordb_available(),
                    reason="requires TORTOISE_DB_URI (the run ingests)")
def test_run_level_env_gate_fail_safe(monkeypatch, tmp_path):
    """FAIL VALUE: a garbage env value arming the run. Reachable: the
    run-level resolver's fail-safe (only 1/true/yes/on)."""
    monkeypatch.setenv("TORTOISE_LME_TIME_AWARE_QE", "garbage")
    rep = runner.run_main([
        "--data", str(MINI), "--limit", "1", "--split", "s", "--mock",
        "--skip-preflight", "--output", str(tmp_path / "r.json")])
    assert rep["methodology"]["time_aware_qe"] is False
