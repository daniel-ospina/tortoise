"""#2976 — the deterministic variant-aware containment judge (hermetic).

The #2976 oracle ceiling (42/52) was graded with **variant-aware containment**
because the official judge (`gpt-4o-2024-08-06`) was blocked by the OpenRouter
monthly limit. That grader was never in the repository, so the number could not
be reproduced and no before/after comparison could be run under one grader.

`judge.grade_variant_aware` / `judge.VariantContainmentJudge` are that grader,
as a first-class measurement instrument:

* the split is the benchmark's OWN multi-variant convention
  (``"is also acceptable"``) — never every sentence, which would inflate a
  grade from an incidental clause;
* the containment rule per variant is the strict `judge.grade_label` the
  official rubric asks for;
* selecting it is a DECLARED substitution — ``model_spec`` records
  ``variant-containment`` in the run methodology and the pre-flight check
  reports "no transport" rather than demanding a judge key.

Pinned against the committed #2976 rows: the whole ceiling is re-derivable
offline from `docs/runbook/2578-oracle-ceiling.jsonl` (42/52 variant-aware,
25/52 the plain FLOOR). Run:

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/longmem_eval/test_variant_containment_judge.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.longmem_eval import dataset as ds
from tools.longmem_eval.judge import (
    VARIANT_CONTAINMENT_SPEC,
    VariantContainmentJudge,
    build_judge,
    gold_variants,
    grade_label,
    grade_variant_aware,
)
from tools.longmem_eval.preflight import check_judge_key

ROOT = Path(__file__).resolve().parents[2]
ROWS = ROOT / "docs" / "runbook" / "2578-oracle-ceiling.jsonl"


def test_variants_split_on_the_benchmark_convention_only():
    assert gold_variants(
        "7 days. 8 days (including the last day) is also acceptable."
    ) == ["7 days", "8 days"]
    # a multi-sentence gold WITHOUT the marker is ONE variant — splitting it
    # would let an incidental clause match and inflate the grade.
    one = gold_variants("He moved to Austin. Then he bought a car.")
    assert len(one) == 1, one
    assert one[0].startswith("He moved to Austin. Then he bought a car")


def test_integer_and_empty_golds_coerce():
    assert gold_variants(4) == ["4"]
    assert gold_variants("") == []
    assert gold_variants(None) == []


def test_variant_aware_beats_the_plain_floor_on_the_benchmark_format():
    gold = "7 days. 8 days (including the last day) is also acceptable."
    hyp = "7 days passed between the two events."
    assert grade_label(gold, hyp) is False        # the plain FLOOR fails
    assert grade_variant_aware(gold, hyp) is True  # variant-aware passes
    # the second variant also counts
    assert grade_variant_aware(gold, "It was 8 days.") is True
    # neither variant present → still wrong
    assert grade_variant_aware(gold, "about two weeks") is False
    # empty hypothesis is never a free pass
    assert grade_variant_aware(gold, "") is False


def test_judge_is_selected_by_name_and_has_no_transport():
    judge = build_judge(VARIANT_CONTAINMENT_SPEC)
    assert isinstance(judge, VariantContainmentJudge)
    assert judge.model_spec == VARIANT_CONTAINMENT_SPEC
    assert judge.no_transport is True
    # the pre-flight judge-key check must NOT demand a key for it (that check
    # is what makes the keyless reproduction possible).
    check = check_judge_key(judge)
    assert check["status"] == "ok", check
    assert check["key_env"] is None
    # …while a transport-backed judge still fails closed with no key
    assert judge.ping("probe") == "variant-containment judge (no transport)"


def test_abstention_questions_keep_the_mock_contract():
    judge = VariantContainmentJudge()
    assert judge.judge(question_type="temporal-reasoning", question="q",
                       answer="", hypothesis="I don't know — the context "
                       "does not contain that information.",
                       abstention=True) is True
    assert judge.judge(question_type="temporal-reasoning", question="q",
                       answer="", hypothesis="7 days", abstention=True) is False


@pytest.mark.skipif(not ROWS.exists(), reason="committed #2976 rows absent")
def test_the_committed_oracle_ceiling_is_reproducible():
    """42/52 variant-aware, 25/52 plain — the issue's own numbers, re-derived
    from committed artifacts with no reader call and no judge key."""
    try:
        instances = ds.load_dataset(split="s", download=False)
    except Exception:
        pytest.skip("LongMemEval split not cached on this host")
    rows = [json.loads(ln) for ln in ROWS.read_text().splitlines() if ln.strip()]
    inst = {i["question_id"]: i for i in instances}
    n = plain = variant = refusals = 0
    for r in rows:
        if not r.get("answerable"):
            continue
        q = inst[r["qid"]]
        n += 1
        plain += grade_label(q["answer"], r["hypothesis"])
        variant += grade_variant_aware(q["answer"], r["hypothesis"])
        refusals += bool(r.get("reader_refusal"))
    assert n == 52, n
    assert plain == 25, plain
    assert variant == 42, variant
    assert refusals == 4, refusals
    # the plain rule reproduces the committed grade column exactly — the
    # agreement check that keeps "25 is a floor" from being a story.
    agree = sum(
        1 for r in rows if r.get("answerable") and
        ((r.get("grade") or "").upper() == "CORRECT")
        == grade_label(inst[r["qid"]]["answer"], r["hypothesis"]))
    assert agree == 52, agree
