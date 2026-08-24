"""Unit tests for tools/pair_label_runner.py (#1349 T5) — dual-judge labeling.

The runner classifies labeled pairs with TWO DISTINCT LLM judges
(OpenRouterModel adapters), computes Cohen's κ on their label sets, and
gates the output (KAPPA_GREEN = 0.60). All OpenRouterModel usage is mocked —
no real API calls in tests (OPENROUTER_API_KEY is a dev/evidence-run secret).

Locked contracts under test:
- κ math against hand-computed agreement matrices (identical to
  tools/kappa.py's po/pe formula; the pe == 1.0 degenerate guard).
- single-judge API failure → abort: an exception (or unparseable label)
  from EITHER judge raises PairLabelError and no output is ever emitted —
  κ over one judge is vacuously 1.0.
- κ ≥ 0.60 vs κ < 0.60 decision path (GREEN vs NOT_GREEN).
- adjudication branch: a third judge labels the disagreement pairs and
  resolves them (needs_human_review only when NO adjudicator is available).
- DISTINCT judge models enforced (same-model judges inflate κ).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import pair_label_runner as plr

I, N, U = "IMPLIES", "NEAR_DUPLICATE", "UNRELATED"  # noqa: E741 — test constants


class FakeJudge:
    """Scripted judge: returns one label per pair (in order)."""

    def __init__(self, model_id: str, labels: list[str]):
        self.id = model_id
        self._labels = list(labels)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        if self.calls >= len(self._labels):
            raise AssertionError("judge called more times than scripted labels")
        label = self._labels[self.calls]
        self.calls += 1
        return label


class FailingJudge:
    """Judge whose API call fails (network/HTTP error)."""

    def __init__(self, model_id: str, fail_on_call: int = 0):
        self.id = model_id
        self._fail_on = fail_on_call
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        if self.calls >= self._fail_on:
            raise RuntimeError("simulated OpenRouter API failure (HTTP 429)")
        self.calls += 1
        return I


def pairs(labels: list[tuple[str, str]] | None = None) -> list[dict]:
    """Minimal pair rows (the runner only reads content_a/content_b/band)."""
    if labels is None:
        return [{"content_a": f"a{i}", "content_b": f"b{i}",
                 "band": "noise", "label": U} for i in range(4)]
    return [{"content_a": a, "content_b": b, "band": "noise", "label": U}
            for a, b in labels]


# ── Cohen's κ math (hand-computed agreement matrices) ─────────────────

def test_kappa_perfect_agreement_is_one():
    assert plr.kappa([I, N, U, I], [I, N, U, I]) == pytest.approx(1.0)


def test_kappa_complete_disagreement_is_negative_one():
    # A=[I,I,U,U], B=[U,U,I,I]: po=0, pe=0.5 → κ = (0-0.5)/(1-0.5) = -1.0
    assert plr.kappa([I, I, U, U], [U, U, I, I]) == pytest.approx(-1.0)


def test_kappa_chance_level_agreement_is_zero():
    # A=[I,I,U,U], B=[I,U,I,U]: po=0.5, pe=0.5 → κ = 0.0
    assert plr.kappa([I, I, U, U], [I, U, I, U]) == pytest.approx(0.0)


def test_kappa_middle_agreement_hand_computed():
    # A=[I,I,U], B=[I,U,U]: po=2/3; pe = (2/3·1/3)+(1/3·2/3)=4/9
    # κ = (2/3-4/9)/(1-4/9) = 0.4
    assert plr.kappa([I, I, U], [I, U, U]) == pytest.approx(0.4)


def test_kappa_degenerate_single_category_judges():
    # Both judges use one identical category: pe == 1.0 → κ = 1.0, never NaN.
    assert plr.kappa([I, I, I], [I, I, I]) == pytest.approx(1.0)


def test_kappa_empty_inputs_error():
    with pytest.raises(plr.PairLabelError):
        plr.kappa([], [])
    with pytest.raises(plr.PairLabelError):
        plr.kappa([I, U], [I, U, N])  # length mismatch = misaligned labels


def test_runner_kappa_is_shared_core_adapter():
    """The runner's kappa is a thin adapter: identical results to the shared
    tools.kappa.cohens_kappa core for the same hand-computed matrices — a
    correction to the κ math can never drift between the two gates."""
    from tools.kappa import cohens_kappa
    matrices = [
        ([I, N, U, I], [I, N, U, I]),
        ([I, I, U, U], [U, U, I, I]),
        ([I, I, U, U], [I, U, I, U]),
        ([I, I, U], [I, U, U]),
        ([I, I, I], [I, I, I]),
    ]
    for a, b in matrices:
        assert plr.kappa(a, b) == pytest.approx(cohens_kappa(a, b))


# ── single-judge failure → abort ───────────────────────────────────────

def test_single_judge_api_failure_aborts_and_never_emits(tmp_path):
    out = tmp_path / "labels.json"
    with pytest.raises(plr.PairLabelError, match=r"[Jj]udge"):
        plr.label_pairs(
            pairs(), FailingJudge("model-a", fail_on_call=0),
            FakeJudge("model-b", [I, I, I, I]), out_path=out,
        )
    assert not out.exists(), "no partial output may be emitted on judge failure"


def test_either_judge_failure_aborts():
    with pytest.raises(plr.PairLabelError):
        plr.label_pairs(
            pairs(), FakeJudge("model-a", [I, I, I, I]),
            FailingJudge("model-b", fail_on_call=2),
        )


def test_unparseable_judge_label_aborts():
    class GarbageJudge(FakeJudge):
        def complete(self, *, system, user):
            return "not-a-label"

    with pytest.raises(plr.PairLabelError, match="label"):
        plr.label_pairs(pairs(), GarbageJudge("m-a", []),
                        FakeJudge("m-b", [I, I, I, I]))


# ── label parsing (word-boundary, fail-closed on ambiguity) ────────────

def test_parse_label_bare_token():
    assert plr._parse_label("NEAR_DUPLICATE", "j", 0) == N


def test_parse_label_bare_token_lowercase():
    # The whole answer is exactly the label word — casing is not prose signal.
    assert plr._parse_label("implies", "j", 0) == I


def test_parse_label_explicit_label_colon_form():
    assert plr._parse_label("LABEL: IMPLIES", "j", 0) == I
    assert plr._parse_label("label: unrelated", "j", 0) == U


def test_parse_label_free_text_aborts():
    # Free-text prose that merely CONTAINS one vocab token is NOT a label:
    # "Passage A implies B entirely" is UNRELATED prose but would silently
    # parse as IMPLIES. Fail closed — the answer must BE the label.
    for prose in ("The answer is IMPLIES.",
                  "UNRELATED — no relation here",
                  "Passage A implies B entirely"):
        with pytest.raises(plr.PairLabelError, match="label"):
            plr._parse_label(prose, "j", 0)


def test_parse_label_ambiguous_aborts():
    # "NEAR_DUPLICATE — the passage implies identical meaning" contains TWO
    # label tokens; silently picking the first would corrupt the artifact.
    with pytest.raises(plr.PairLabelError, match="ambiguous"):
        plr._parse_label("NEAR_DUPLICATE — the passage implies identical "
                         "meaning", "j", 3)


def test_parse_label_no_token_aborts():
    with pytest.raises(plr.PairLabelError):
        plr._parse_label("maybe related?", "j", 0)


# ── κ ≥ 0.60 vs κ < 0.60 decision path ─────────────────────────────────

def test_high_kappa_green_path_emits_agreed_labels(tmp_path):
    out = tmp_path / "labels.json"
    report = plr.label_pairs(
        pairs(), FakeJudge("model-a", [I, N, U, I]),
        FakeJudge("model-b", [I, N, U, I]), out_path=out,
    )
    assert report["kappa"] == pytest.approx(1.0)
    assert report["verdict"] == "GREEN"
    assert report["disagreements"] == []
    assert all(p["needs_human_review"] is False for p in report["pairs"])
    assert all(p["final_label"] == p["judge_a"] for p in report["pairs"])
    assert out.exists() and json.loads(out.read_text())["verdict"] == "GREEN"


def test_low_kappa_not_green_flags_disagreements_for_human_review():
    report = plr.label_pairs(
        pairs(), FakeJudge("model-a", [I, I, U, U]),
        FakeJudge("model-b", [I, U, I, U]),
    )
    assert report["kappa"] == pytest.approx(0.0)
    assert report["verdict"] == "NOT_GREEN"
    assert report["disagreements"] == [1, 2]
    for idx, p in enumerate(report["pairs"]):
        if idx in report["disagreements"]:
            assert p["needs_human_review"] is True
            assert p["final_label"] is None
        else:
            assert p["needs_human_review"] is False
            assert p["final_label"] == p["judge_a"]


def test_verdict_band_boundary_semantics():
    # κ = 0.4 (< 0.60) → NOT_GREEN; κ = 0.60 → GREEN (tools.kappa semantics).
    assert plr.decide(0.4)["verdict"] == "NOT_GREEN"
    assert plr.decide(0.6)["verdict"] == "GREEN"
    assert plr.decide(None)["verdict"] == "NOT_GREEN"


# ── adjudication branch ────────────────────────────────────────────────

def test_adjudicator_resolves_disagreements():
    report = plr.label_pairs(
        pairs(), FakeJudge("model-a", [I, I, U, U]),
        FakeJudge("model-b", [I, U, I, U]),
        adjudicator=FakeJudge("model-c", [I, U]),  # labels disagreement pairs
    )
    assert report["kappa"] == pytest.approx(0.0)
    assert report["verdict"] == "NOT_GREEN"  # gate is on the primary judges
    assert report["adjudication"]["used"] is True
    assert report["adjudication"]["n_resolved"] == 2
    assert report["adjudication"]["n_unresolved"] == 0
    assert report["disagreements"] == [1, 2]
    for idx, p in enumerate(report["pairs"]):
        if idx in report["disagreements"]:
            assert p["adjudicated"] is True
            assert p["needs_human_review"] is False
            assert p["final_label"] in (I, U)  # adjudicator's tie-break
        else:
            assert p["adjudicated"] is False
            assert p["final_label"] == p["judge_a"]


def test_adjudicator_called_only_on_disagreements():
    adjudicator = FakeJudge("model-c", [I, U])
    plr.label_pairs(
        pairs(), FakeJudge("model-a", [I, I, U, U]),
        FakeJudge("model-b", [I, U, I, U]), adjudicator=adjudicator,
    )
    assert adjudicator.calls == 2, "adjudicator must only label disagreements"


# ── distinct-judge enforcement ─────────────────────────────────────────

def test_same_judge_model_rejected():
    with pytest.raises(plr.PairLabelError, match=r"(?i)distinct"):
        plr.label_pairs(pairs(), FakeJudge("same-model", [I, I, I, I]),
                        FakeJudge("same-model", [I, I, I, I]))


def test_cli_rejects_identical_judge_models(monkeypatch):
    class _Stub:
        def __init__(self, model_id: str, **kw):
            self.id = model_id

        def complete(self, **kw):
            return I

    monkeypatch.setattr(plr, "OpenRouterModel", _Stub)
    monkeypatch.setattr(plr, "MODELS",
                        {"deepseek-v4-pro": lambda: _Stub("deepseek-v4-pro")})
    rc = plr.main(["--pairs", str(Path(__file__).parent / "fixtures"
                                  / "labeled_pairs.jsonl"),
                   "--judge-a", "deepseek-v4-pro",
                   "--judge-b", "deepseek-v4-pro"])
    assert rc != 0


def test_cli_writes_report_and_exits_zero(tmp_path, monkeypatch):
    class AlwaysImplies:
        """Scripted judge that labels every pair IMPLIES (no length bound)."""

        def __init__(self, model_id: str, **kw):
            self.id = model_id

        def complete(self, *, system: str, user: str) -> str:
            return I

    monkeypatch.setattr(plr, "OpenRouterModel", AlwaysImplies)
    monkeypatch.setattr(plr, "MODELS", {
        "deepseek-v4-pro": lambda: AlwaysImplies("deepseek-v4-pro"),
        "qwen3.8-max": lambda: AlwaysImplies("qwen3.8-max"),
    })
    out = tmp_path / "out.json"
    fixture = Path(__file__).parent / "fixtures" / "labeled_pairs.jsonl"
    rc = plr.main(["--pairs", str(fixture), "--judge-a", "deepseek-v4-pro",
                   "--judge-b", "qwen3.8-max", "--out", str(out)])
    assert rc == 0
    assert out.exists()
    report = json.loads(out.read_text())
    assert report["judges"]["a"] != report["judges"]["b"]
    assert report["n_pairs"] > 0
