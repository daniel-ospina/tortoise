"""W3-b why-suite grading unit tests (epic #2080, issue #2100).

Hermetic (no DB, no network): the four why-question graders + the
false-positive arm over SYNTHETIC canonical why-blocks — proving each
surface's A11 semantics (graded from the surfaced context dict alone) and
the anti-gaming guards (a contradiction missing any of the three surfacing
signals is NOT surfaced; a clean point with invented contradiction noise
trips the false-positive arm; a pointer target resolving to the wrong point
fails navigation).
"""

from __future__ import annotations

from eval.why_suite import grading

SUPPORT = {
    "point_id": "pt_support",
    "content_snippet": "supporting record alpha",
    "edge": "IMPL",
    "weight": 0.9231,
}
COUNTER = {
    "point_id": "pt_counter",
    "content_snippet": "counterargument gamma",
    "severity": "high",
}


def _ep(*, contested: bool = False, has_ep: bool = True, mean: float = 0.5) -> dict:
    return {"confidence_mean": mean, "variance": 0.05, "contested": contested, "has_ep": has_ep}


def _block(**over) -> dict:
    block = {
        "point_id": "pt_claim",
        "support_chain": [SUPPORT],
        "ep": _ep(),
        "supersession": {
            "status": "live",
            "superseded_by": None,
            "supersedes": [],
            "successor_label": None,
        },
    }
    block.update(over)
    return block


def _conflicted_block(**over) -> dict:
    block = _block(
        conflicts={"contested": True, "nands": [COUNTER]},
        dig_deeper=[
            {"label": "read supports", "kind": "supports", "target": "pt_support"},
            {"label": "read the counterargument (NAND)", "kind": "nand", "target": "pt_counter"},
        ],
    )
    block.update(over)
    return block


def _expected(**over) -> dict:
    exp = {
        "expected_conflict": True,
        "clean": False,
        "family": "plain",
        "expected_targets": [
            {"kind": "supports", "target_id": "pt_support"},
            {"kind": "nand", "target_id": "pt_counter"},
        ],
        "expected_tradeoff": False,
        "favored_option_id": None,
    }
    exp.update(over)
    return exp


# ── Conflict surfacing (Q1) ────────────────────────────────────────────────


def test_full_conflicted_block_surfaces():
    row = grading.grade_point(_conflicted_block(), _expected())
    assert row["conflict_surfaced"] is True
    assert row["false_positive"] is False
    assert row["nav_correct"] == 2 and row["nav_total"] == 2
    assert row["support_sufficient"] is True
    assert row["tradeoff_sufficient"] is None


def test_conflict_missing_nands_is_not_surfaced():
    block = _conflicted_block(conflicts={"contested": True, "nands": []})
    row = grading.grade_point(block, _expected())
    assert row["conflict_surfaced"] is False


def test_conflict_uncontested_is_not_surfaced():
    block = _conflicted_block(conflicts={"contested": False, "nands": [COUNTER]})
    assert grading.grade_point(block, _expected())["conflict_surfaced"] is False


def test_conflict_missing_nand_pointer_is_not_surfaced():
    # Conflicts present but no dig-deeper nand pointer (the contradiction
    # hidden under an explore affordance violates E2E-1 surfaced-context).
    block = _conflicted_block(
        dig_deeper=[{"label": "read supports", "kind": "supports", "target": "pt_support"}]
    )
    assert grading.grade_point(block, _expected())["conflict_surfaced"] is False


def test_absent_conflicts_key_is_not_surfaced():
    block = _block(
        dig_deeper=[{"label": "read supports", "kind": "supports", "target": "pt_support"}]
    )
    assert grading.grade_point(block, _expected())["conflict_surfaced"] is False


# ── Clean false-positive arm ───────────────────────────────────────────────


def _clean_expected() -> dict:
    return {
        "expected_conflict": False,
        "clean": True,
        "family": "clean",
        "expected_targets": [{"kind": "supports", "target_id": "pt_support_a"}],
        "expected_tradeoff": False,
        "favored_option_id": None,
    }


def test_clean_block_with_supports_pointer_only_is_clean():
    block = _block(
        support_chain=[SUPPORT, dict(SUPPORT, point_id="pt_support_b")],
        ep=_ep(contested=False, mean=0.92),
        dig_deeper=[{"label": "read supports", "kind": "supports", "target": "pt_support_a"}],
    )
    row = grading.grade_point(block, _clean_expected())
    assert row["false_positive"] is False
    assert row["conflict_surfaced"] is None  # not graded (clean)
    assert row["nav_correct"] == 1
    assert row["support_sufficient"] is True


def test_clean_block_inventing_conflicts_trips_false_positive():
    # A clean point must not invent a contradiction — a surfaced conflicts
    # block (or nand pointer / contested ep) is a false positive.
    cases = [
        _block(conflicts={"contested": True, "nands": [COUNTER]}),
        _block(conflicts={"contested": False, "nands": [COUNTER]}),
        _block(ep=_ep(contested=True)),
        _block(
            dig_deeper=[
                {"label": "read the counterargument (NAND)", "kind": "nand", "target": "pt_counter"}
            ]
        ),
        _block(
            dig_deeper=[{"label": "see what changed", "kind": "superseded", "target": "pt_succ"}]
        ),
        _block(
            tradeoffs=[{"point_id": "pt_a", "label": "alt", "ep_weight": 0.8, "mitigation": "m"}]
        ),
    ]
    for block in cases:
        assert grading.grade_point(block, _clean_expected())["false_positive"] is True, (
            f"block {block} must trip the false-positive arm"
        )


def test_clean_wrong_supports_pointer_fails_navigation():
    block = _block(
        support_chain=[SUPPORT],
        ep=_ep(contested=False, mean=0.92),
        dig_deeper=[{"label": "read supports", "kind": "supports", "target": "pt_wrong_point"}],
    )
    row = grading.grade_point(block, _clean_expected())
    assert row["nav_correct"] == 0
    assert row["nav_total"] == 1
    assert row["nav_errors"][0]["expected_target"] == "pt_support_a"


# ── Dig-deeper navigation (Q2) ─────────────────────────────────────────────


def test_navigation_wrong_kind_does_not_count():
    # The nand pointer points at the right id but is mislabeled (kind=supports)
    # — the labeled pointer must resolve per its KIND.
    block = _block(
        conflicts={"contested": True, "nands": [COUNTER]},
        dig_deeper=[
            {"label": "read supports", "kind": "supports", "target": "pt_support"},
            {"label": "read supports", "kind": "supports", "target": "pt_counter"},
        ],
    )
    row = grading.grade_point(block, _expected())
    assert row["nav_correct"] == 1  # only the supports target matched
    assert row["nav_total"] == 2
    assert row["nav_errors"][0]["kind"] == "nand"


def test_navigation_wrong_target_fails():
    block = _conflicted_block(
        dig_deeper=[
            {"label": "read supports", "kind": "supports", "target": "pt_support"},
            {
                "label": "read the counterargument (NAND)",
                "kind": "nand",
                "target": "pt_wrong_counter",
            },
        ]
    )
    row = grading.grade_point(block, _expected())
    assert row["nav_correct"] == 1
    assert row["nav_total"] == 2


# ── Support-chain sufficiency (Q3) ─────────────────────────────────────────


def test_support_sufficient_requires_chain_and_measured_ep():
    good = _conflicted_block()
    assert grading.grade_point(good, _expected())["support_sufficient"] is True
    # Empty chain → not answerable.
    empty = _conflicted_block(support_chain=[])
    assert grading.grade_point(empty, _expected())["support_sufficient"] is False
    # Malformed chain entry (no weight) → not answerable.
    malformed = _conflicted_block(support_chain=[{"point_id": "pt_s", "content_snippet": "c"}])
    assert grading.grade_point(malformed, _expected())["support_sufficient"] is False
    # Unmeasured ep (has_ep false) → not answerable.
    unmeasured = _conflicted_block(ep=_ep(has_ep=False))
    assert grading.grade_point(unmeasured, _expected())["support_sufficient"] is False


# ── Trade-off sufficiency (Q4) ─────────────────────────────────────────────


def _decision_block(**over) -> dict:
    block = _conflicted_block(
        tradeoffs=[
            {
                "point_id": "pt_opt_a",
                "label": "alternative one",
                "ep_weight": 0.8,
                "mitigation": "QA gate",
            },
            {
                "point_id": "pt_opt_b",
                "label": "alternative two",
                "ep_weight": 0.71,
                "mitigation": "communicate delay",
            },
        ],
        dig_deeper=[
            {"label": "read supports", "kind": "supports", "target": "pt_support"},
            {"label": "read the counterargument (NAND)", "kind": "nand", "target": "pt_counter"},
            {"label": "weigh the alternatives", "kind": "tradeoff", "target": "pt_opt_a"},
        ],
    )
    block.update(over)
    return block


def _decision_expected() -> dict:
    exp = _expected(family="decision")
    exp["expected_targets"].append({"kind": "tradeoff", "target_id": "pt_opt_a"})
    exp["expected_tradeoff"] = True
    exp["favored_option_id"] = "pt_opt_a"
    return exp


def test_decision_tradeoff_sufficient():
    row = grading.grade_point(_decision_block(), _decision_expected())
    assert row["tradeoff_sufficient"] is True
    assert row["nav_correct"] == 3 and row["nav_total"] == 3


def test_decision_single_alternative_not_sufficient():
    block = _decision_block(
        tradeoffs=[
            {
                "point_id": "pt_opt_a",
                "label": "alternative one",
                "ep_weight": 0.8,
                "mitigation": "QA gate",
            }
        ]
    )
    assert grading.grade_point(block, _decision_expected())["tradeoff_sufficient"] is False


def test_decision_missing_mitigation_not_sufficient():
    block = _decision_block(
        tradeoffs=[
            {"point_id": "pt_opt_a", "label": "a", "ep_weight": 0.8, "mitigation": "QA gate"},
            {"point_id": "pt_opt_b", "label": "b", "ep_weight": 0.71, "mitigation": ""},
        ]
    )
    assert grading.grade_point(block, _decision_expected())["tradeoff_sufficient"] is False


def test_decision_favored_alternative_is_max_ep_weight():
    # The assembly sorts descending; the grader verifies the SURFACED
    # favorite (tradeoffs[0]) is the planted favorite.
    block = _decision_block(
        tradeoffs=[
            {
                "point_id": "pt_wrong",
                "label": "not the planted favorite",
                "ep_weight": 0.99,
                "mitigation": "x",
            },
            {"point_id": "pt_opt_b", "label": "b", "ep_weight": 0.71, "mitigation": "y"},
        ]
    )
    row = grading.grade_point(block, _decision_expected())
    assert row["tradeoff_sufficient"] is False  # EP "favors" the wrong point


def test_decision_missing_tradeoff_pointer_not_sufficient():
    block = _decision_block(
        dig_deeper=[
            {"label": "read supports", "kind": "supports", "target": "pt_support"},
            {"label": "read the counterargument (NAND)", "kind": "nand", "target": "pt_counter"},
        ]
    )
    assert grading.grade_point(block, _decision_expected())["tradeoff_sufficient"] is False


def test_non_decision_fabricated_tradeoffs_flagged():
    """Q4 anti-fabrication (closed world): a NON-decision point whose
    surfaced context carries tradeoffs (the plant never produced them) is
    flagged — the rubric clause 'non-decision points never fabricate
    tradeoffs' is measured, not silent."""
    block = _conflicted_block(
        tradeoffs=[
            {
                "point_id": "pt_fake",
                "label": "invented alternative",
                "ep_weight": 0.8,
                "mitigation": "none",
            }
        ]
    )
    row = grading.grade_point(block, _expected())  # family plain
    assert row["tradeoff_sufficient"] is None
    assert row["fabricated_tradeoffs"] is True
    # A decision-point block is NOT a fabrication (graded for sufficiency).
    assert (
        grading.grade_point(_decision_block(), _decision_expected())["fabricated_tradeoffs"]
        is False
    )


# ── resolve_expected (role → id resolution is runner-side ground truth) ───


def test_resolve_expected_maps_roles_to_ids():
    gold_entry = {
        "point_id": "p9-topic-0",
        "family": "p9",
        "clean": False,
        "expected": {
            "conflict_surfacing": True,
            "dig_deeper_targets": [
                {"kind": "supports", "target_role": "support"},
                {"kind": "nand", "target_role": "counter"},
            ],
            "support_chain_sufficient": True,
            "tradeoff_sufficient": False,
        },
    }
    roles = {"support": "id_support", "counter": "id_counter", "claim": "id_claim"}
    resolved = grading.resolve_expected(gold_entry, roles)
    assert resolved["expected_conflict"] is True
    assert resolved["expected_targets"] == [
        {"kind": "supports", "target_id": "id_support"},
        {"kind": "nand", "target_id": "id_counter"},
    ]
    assert resolved["favored_option_id"] is None


def test_resolve_expected_missing_role_is_skipped_not_crash():
    gold_entry = {
        "point_id": "clean-topic-0",
        "family": "clean",
        "clean": True,
        "expected": {
            "conflict_surfacing": False,
            "dig_deeper_targets": [{"kind": "supports", "target_role": "support_a"}],
            "support_chain_sufficient": True,
            "tradeoff_sufficient": False,
        },
    }
    resolved = grading.resolve_expected(gold_entry, {})
    assert resolved["expected_targets"] == []


# ── A11 boundary ───────────────────────────────────────────────────────────


def test_graders_take_only_surfaced_context_and_expected():
    """The A11 invariant as a signature property: grade_point accepts a
    block dict + resolved expectations — no graph handle, no SDK, nothing
    beyond the surfaced context.  A missing block (the assembly returned
    nothing) grades honestly against an empty context."""
    import inspect

    signature = inspect.signature(grading.grade_point)
    params = list(signature.parameters)
    assert params == ["block", "expected"]
    empty = grading.grade_point({}, _expected())
    assert empty["conflict_surfaced"] is False
    assert empty["nav_correct"] == 0
    assert empty["support_sufficient"] is False
    assert empty["false_positive"] is False  # absent data invents nothing
