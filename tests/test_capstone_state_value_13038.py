"""Hermetic pins for the #13038 value assessment (no graph, no network, no model).

WHAT THESE PREVENT, AND WHY THEY ARE NOT THE CAPSTONE
-----------------------------------------------------
The capstone itself is `tools/capstone_state_value.py run` — a slow, on-demand,
graph-backed measurement that cannot run in CI and must not. These pins guard the
instrument's *joinery*, which fails silently in exactly the way this repo keeps
getting bitten: a typo in the scenario or in a row-rendering rule makes the run
produce a **plausible scoreboard of the wrong thing**.

The two failures worth a check, both measured shapes of drift:

1. **The scenario and the runner drift apart.** A question whose `recipe` no longer
   exists, an ingestion op with no handler, or a `requires` capability that is not in
   the declared set makes that row `blocked` forever — the scoreboard then reports the
   product as unable to answer, when in fact the *instrument* could not ask. Nothing
   reds; the run exits 1 with a `blocked` row that reads like a product finding.
2. **The verdict vocabulary widens or flattens.** `blocked` folded into `wrong` (or a
   fourth verdict invented on the fly) turns "we could not ask" into a product defect,
   and a scoreboard that loses that distinction is worse than no scoreboard.

They deliberately do NOT pin: the scoreboard's contents, the verdicts, the target, or
whether any given question passes. Those are what the run measures, and pinning them
would make the instrument a rubber stamp — the one thing #13038 forbids.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.capstone_state_value as cap

ROOT = Path(__file__).resolve().parents[1]
NARRATIVE = ROOT / "docs" / "evidence" / "13038-capstone" / "narrative.json"


@pytest.fixture(scope="module")
def narrative() -> dict:
    return json.loads(NARRATIVE.read_text(encoding="utf-8"))


# ── 1. the scenario describes a run this tool can actually perform ──────────────


def test_schema_and_the_six_questions_of_record(narrative):
    assert narrative["schema"] == "capstone-state-value-narrative/v1"
    assert narrative["issue"] == 13038
    ids = [q["id"] for q in narrative["questions"]]
    assert ids == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"], (
        "the six customer questions are the instrument's fixed population; adding or "
        "dropping one re-baselines every earlier scoreboard"
    )
    for q in narrative["questions"]:
        for key in ("question", "key", "wrong_means", "recipe"):
            assert q.get(key), f"{q['id']} carries no {key}"
        assert q["recipe"] in cap.RECIPES, (
            f"{q['id']} names recipe {q['recipe']!r}, which the runner does not "
            "implement — that row would silently read as `blocked`"
        )


def test_every_recipe_declares_its_capability_needs():
    assert set(cap.RECIPE_NEEDS) == set(cap.RECIPES), (
        "a recipe with no declared needs is attempted on every target, including one "
        "that cannot express the shape — and its failure then reads as a product defect"
    )
    for recipe, needs in cap.RECIPE_NEEDS.items():
        unknown = set(needs) - set(cap.CAPABILITIES)
        assert not unknown, f"{recipe} requires undeclared capabilities: {sorted(unknown)}"


def test_every_ingest_op_has_a_handler_and_declared_capabilities(narrative):
    ops = narrative["ingest"]["ops"]
    assert ops, "the narrative ingests nothing"
    for op in ops:
        name = op.get("op")
        assert hasattr(cap.Ingestor, f"_op_{name}"), (
            f"narrative op {name!r} has no handler — the Ingestor records it as a "
            "failed write and every dependent question becomes `blocked`"
        )
        unknown = set(op.get("requires") or []) - set(cap.CAPABILITIES)
        assert not unknown, f"op {name!r} requires undeclared capabilities: {sorted(unknown)}"


def test_every_question_names_scenario_refs_that_exist(narrative):
    refs = {op.get("ref") for op in narrative["ingest"]["ops"] if op.get("ref")}
    for q in narrative["questions"]:
        named = set(q.get("must_appear") or []) | set(q.get("must_not_appear") or [])
        assert named <= refs, (
            f"{q['id']} names scenario refs that no op defines: {sorted(named - refs)} "
            "— the signal would silently never fire"
        )


def test_the_narrative_contains_every_shape_it_claims(narrative):
    """The six shapes of #13038 are only covered if the ops actually perform them."""
    ops = narrative["ingest"]["ops"]
    shapes = {s["id"] for s in narrative["shapes"]}
    assert shapes == {
        "plan",
        "superseded",
        "deprecated",
        "terminal_without_occurrence",
        "goal_target",
        "withdrawn",
    }
    performed = {op["op"] for op in ops}
    assert {"object", "advance_stage"} <= performed  # the plan / terminal stage
    assert "supersede_point" in performed  # the named successor
    assert "deprecate_object" in performed  # the successorless ending
    assert "judge_goal" in performed  # achievement from a reading
    assert "retract_point" in performed  # the withdrawn claim
    stages = [s for op in ops if op["op"] == "declare_pipeline" for s in op["stages"]]
    terminal = [s for s in stages if s["slot"] == "terminal"]
    assert len(terminal) >= 2, "a plan needs a terminal stage to cancel into"
    assert all(s.get("outcome") for s in terminal), (
        "a terminal stage without an outcome is refused by pipeline.validate_pipeline_decl"
    )


# ── 2. the scoring rules keep their distinctions ────────────────────────────────


def test_the_verdict_vocabulary_is_closed():
    assert cap.VERDICTS == ("right", "wrong", "blocked", "unjudged")
    assert "unjudged" in cap.VERDICTS, (
        "a failed judge must have a row value; without it a caller invents `blocked` "
        "or `wrong` for 'no verdict', which is the fabrication #13038 forbids"
    )


@pytest.mark.parametrize("verdict", ["wrong", "right", "unjudged", "blocked", None])
def test_blocked_is_never_folded_into_wrong(narrative, verdict):
    """A question the instrument could not ask is `blocked`, whatever the judge said.

    `right` is in the list on purpose (#13303 review P1): it was the ONE verdict the
    guard used to let through, so a reader that answered `right` over a row the
    instrument could not ask scored the product for a question never put to it — and
    the run exited 0, because nothing was `blocked` either.
    """
    blocked = cap.Answer("Q1", status="blocked", reason="target lacks capability: events")
    board = cap.build_scoreboard(
        narrative={"questions": [narrative["questions"][0]]},
        header={},
        answers=[blocked],
        judged={"Q1": {"verdict": verdict, "because": "the judge guessed"}}
        if verdict
        else {},
        ingest=[],
    )
    assert board["rows"][0]["verdict"] == "blocked"
    assert board["rows"][0]["verdict"] not in ("wrong", "right")
    assert board["tally"]["blocked"] == 1
    assert board["tally"]["wrong"] == board["tally"]["right"] == 0
    # …and the run must not report as a completed score over it.
    assert cap.exit_code(board["tally"]) != 0


def test_a_reader_that_could_not_run_leaves_every_row_unjudged_and_exits_nonzero(narrative):
    """The reader-failure path, pinned end to end without a model (#13303 review P2).

    `judge_fresh` returns `verdicts={}` when it cannot run or parse, so the scoreboard
    is built with no verdicts: every row must be `unjudged` and the run non-zero. That
    is the claim README.md makes; this is the pin behind it.
    """
    answers = [cap.Answer(q["id"]) for q in narrative["questions"]]
    board = cap.build_scoreboard(
        narrative=narrative, header={}, answers=answers, judged={}, ingest=[]
    )
    assert {r["verdict"] for r in board["rows"]} == {"unjudged"}
    assert board["tally"]["unjudged"] == len(narrative["questions"])
    assert cap.exit_code(board["tally"]) != 0
    # The other direction, so the pin cannot pass on a constant.
    assert cap.exit_code({"right": 6, "wrong": 0, "blocked": 0, "unjudged": 0}) == 0


def test_no_verdict_is_unjudged_never_a_default_verdict(narrative):
    answered = cap.Answer("Q1")
    board = cap.build_scoreboard(
        narrative={"questions": [narrative["questions"][0]]},
        header={},
        answers=[answered],
        judged={},
        ingest=[],
    )
    assert board["rows"][0]["verdict"] == "unjudged"
    assert board["tally"]["right"] == board["tally"]["wrong"] == 0


# ── 3. the mechanical signals, and the fresh-reader parse ───────────────────────


def test_overlap_separates_an_answer_from_a_corpus_ranking():
    """The measure Q6 turned on: rows that share no word with the question."""
    on_topic = cap._overlap(
        "beach club booking", [{"what": "The beach club confirmed the booking."}]
    )
    unrelated = cap._overlap(
        "has anything been withdrawn",
        [
            {"what": "Q4 pricing: the entry tier becomes $39 per seat."},
            {"what": "Q4 uptime: unmet against 99.9"},
        ],
    )
    assert on_topic["zero_overlap_rows"] == 0
    assert unrelated["zero_overlap_rows"] == 2


def test_a_degraded_read_is_recorded_as_evidence_not_as_a_verdict():
    ans = cap.Answer("Q6")
    ans.calls.append(
        cap.Call(
            "search",
            {"query": "withdrawn"},
            True,
            read_status={"status": "degraded"},
            rows=[{"id": "p1", "what": "Q4 pricing"}],
            legs=[{"leg": "vector", "degraded": True}],
            overlap={"rows": 1, "zero_overlap_rows": 1},
        )
    )
    cap._signal_refs(ans, {}, {})
    assert ans.degraded is True
    joined = " | ".join(ans.signals)
    assert "DEGRADED-READ" in joined and "vector" in joined
    assert "NO-RELATION-TO-QUESTION" in joined


def test_a_retired_id_served_as_current_is_a_signal():
    ans = cap.Answer("Q5")
    ans.calls.append(
        cap.Call(
            "search",
            {"query": "cancelled call"},
            True,
            rows=[{"id": "abc", "what": "Thursday partner call"}],
        )
    )
    cap._signal_refs(ans, {"partner_call": "abc"}, {"must_not_appear": ["partner_call"]})
    assert any(s.startswith("RETIRED-AS-CURRENT") for s in ans.signals)


@pytest.mark.parametrize(
    "text",
    [
        '{"verdicts": [{"id": "Q1", "verdict": "right", "because": "x"}]}',
        '```json\n{"verdicts": [{"id": "Q1", "verdict": "right"}]}\n```',
        'Here you go:\n{"verdicts": [{"id": "Q1", "verdict": "right"}]}\nDone.',
    ],
)
def test_the_fresh_reader_reply_is_parsed_from_the_shapes_a_model_returns(text):
    parsed = cap._extract_json(text)
    assert parsed and parsed["verdicts"][0]["id"] == "Q1"


@pytest.mark.parametrize("text", ["", "I could not decide.", '{"other": 1}'])
def test_an_unparseable_judge_reply_yields_nothing(text):
    assert cap._extract_json(text) is None, (
        "the caller must record `unjudged`; a permissive parser is how a fabricated "
        "verdict enters a scoreboard"
    )


def test_credentials_never_reach_a_recorded_line():
    assert "tt_abcdefghijkl" not in cap._redact("key tt_abcdefghijkl here")
    assert (
        cap._redact("Authorization: Bearer tt_0123456789abcdef")
        == "Authorization: Bearer tt_<redacted>"
    )


# ── 4. the scoreboard a human reads ─────────────────────────────────────────────


def test_the_rendered_scoreboard_carries_verdict_answer_reason_and_meaning(narrative):
    answered = cap.Answer("Q1")
    answered.calls.append(
        cap.Call(
            "search",
            {"query": "what are we committed to", "entity_type": "point"},
            True,
            rows=[{"id": "x", "what": "Q4 pricing", "status": "live", "score": 1.0}],
            overlap={"rows": 1, "zero_overlap_rows": 0},
        )
    )
    board = cap.build_scoreboard(
        narrative={"questions": [narrative["questions"][0]]},
        header={
            "started": "t0",
            "finished": "t1",
            "duration_s": 1.0,
            "target": "local",
            "product": "p",
            "store": "s",
            "graph": "g",
            "narrative": "n",
            "narrative_frozen": "2026-11-16",
        },
        answers=[answered],
        judged={
            "Q1": {
                "verdict": "wrong",
                "because": "it omits the launch plan",
                "cause": "point-scope read",
            }
        },
        ingest=[],
    )
    md = cap.render_markdown(board)
    for token in (
        "Q1",
        "wrong",
        "it omits the launch plan",
        "point-scope read",
        narrative["questions"][0]["wrong_means"],
        "search('what are we committed to'",
    ):
        assert token in md, f"the scoreboard dropped {token!r}"


def test_the_top_table_is_short_and_the_detail_keeps_the_payload(narrative):
    answered = cap.Answer("Q1")
    answered.calls.append(
        cap.Call("search", {"query": "q"}, True, rows=[{"id": "x", "what": "A" * 400}])
    )
    board = cap.build_scoreboard(
        narrative={"questions": [narrative["questions"][0]]},
        header={},
        answers=[answered],
        judged={},
        ingest=[],
    )
    summary = cap._calls_summary(board["rows"][0]["calls"])
    assert "A" * 400 not in summary, "the summary table must stay readable in a minute"
    assert len(summary) < 400
