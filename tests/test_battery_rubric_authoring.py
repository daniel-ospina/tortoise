"""#2292 Task 1 — arm-neutral itemized rubric JSON + loader + neutrality lint."""
from __future__ import annotations

import json  # noqa: F401  (schema smoke kept explicit)
from pathlib import Path

import pytest

from battery.judge.rubric import (  # noqa: I001
    lint_evidence_neutral,
    lint_rubric_items,
    load_rubric_spec,
    render_rubric_prompt,
)

CONFIG = Path(__file__).resolve().parents[1] / "battery/config"


def test_r2_rubric_exists_and_itemized():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    assert len(spec.items) >= 4
    for it in spec.items:
        assert it["id"] and it["text"] and it["decision_rule"]
        assert it["decision_rule"] in ("yes", "no")  # anchored yes/no, binary


def test_r2_items_are_arm_neutral():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    for it in spec.items:
        lint_rubric_items([it])   # raises on banned tokens (verb names, arm ids)
    # banned-token lint covers graph-only phrasing MECHANICALLY (verb names /
    # arm ids / edge counts). The old "not X or Y" check was vacuous (the or
    # branch was true whenever the phrase was absent) — absence alone is not
    # enough: the item texts must POSITIVELY name the deliberation constructs
    # they grade (next test), so a rubric that merely omits graph language but
    # grades nothing cannot pass.


def test_r2_items_anchor_deliberation_constructs_positively():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    texts = " ".join(it["text"] for it in spec.items).lower()
    anchors = {
        "counter-argument weighing": ("counter-argument", "counterargument",
                                      "opposing", "objection"),
        "what-could-be-wrong specificity": ("what-could-be-wrong",
                                             "what could be wrong", "risk",
                                             "downside", "pitfall"),
        "support-vs-oppose weighing": ("support", "oppose"),
        "revision after counter-evidence": ("revis", "revised",
                                             "revisiting", "update"),
    }
    for construct, words in anchors.items():
        assert any(w in texts for w in words), \
            f"no item names the {construct} construct: {texts}"
    assert all(it["decision_rule"] in ("yes", "no") for it in spec.items)


def test_rubric_rendered_prompt_canonical_stable():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    p1 = render_rubric_prompt(spec)
    p2 = render_rubric_prompt(spec)
    assert p1 == p2 and "yes" in p1.lower() and "no" in p1.lower()


def test_evidence_neutrality_lint_rejects_leaks():
    good = ("The agent weighed the risk of vendor lock-in against the cost of "
            "migration and revised its earlier position.")
    lint_evidence_neutral(good)                      # no raise
    for leak in ("filed a mitigation against the support edge",
                 "create_point create_operator file_nand register_conflict",
                 "arm a4 retrieved 3 memories with 12 edges",
                 "the graph arm surfaced the contradiction at turn 6"):
        with pytest.raises(ValueError):
            lint_evidence_neutral(leak)              # banned tokens / arm id / edge count


def test_loader_json_first_md_fallback(tmp_path):
    rub = tmp_path / "rubrics"
    rub.mkdir()
    (rub / "legacy.md").write_text("legacy rubric: judge coverage.",
                                   encoding="utf-8")
    spec = load_rubric_spec(tmp_path, "legacy")      # .md fallback kept
    assert "coverage" in render_rubric_prompt(spec)
