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
        judged={"Q1": {"verdict": verdict, "because": "the judge guessed"}} if verdict else {},
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


def test_a_question_that_was_never_asked_is_blocked_whatever_the_judge_said(narrative):
    """No recorded Answer is the SAME case as a blocked one (#13303 review cycle 2, P2).

    The row already renders `answer_status: "blocked"` and `product_answer: "(not
    asked)"`, so letting a judge verdict stand there made a single row contradict
    itself and scored the product for a question never put to it. Reachable through
    `cmd_judge`: the answers come from `packet["product_answers"]`, so a packet whose
    saved answers predate a question added to the narrative has none for that id.
    """
    q1, q2 = narrative["questions"][0], narrative["questions"][1]
    board = cap.build_scoreboard(
        narrative={"questions": [q1, q2]},
        header={},
        answers=[cap.Answer(q1["id"])],  # only Q1 was asked
        judged={q2["id"]: {"verdict": "right", "because": "the judge guessed"}},
        ingest=[],
    )
    unasked = board["rows"][1]
    assert unasked["answer_status"] == "blocked"
    assert unasked["product_answer"] == "(not asked)"
    assert unasked["verdict"] == "blocked", "the row must not contradict its own answer_status"
    assert board["tally"]["right"] == 0 and board["tally"]["blocked"] == 1
    assert cap.exit_code(board["tally"]) != 0


def test_an_empty_population_is_neither_scored_nor_successful():
    """A scoreboard that scored nothing is not a completed run (#13303 cycle 2, P1).

    Every count is 0, so the blocked/unjudged test passed vacuously and a 0-row
    scoreboard reported exit 0 — measured over the committed baseline: `judge
    --packet run-local.json` wrote `0 right · 0 wrong · 0 blocked · 0 unjudged` in
    place of the real table while keeping the correct identity header.
    """
    assert cap.exit_code({v: 0 for v in cap.VERDICTS}) != 0
    # …and a non-empty, fully-scored tally is still a success.
    assert cap.exit_code({"right": 0, "wrong": 6, "blocked": 0, "unjudged": 0}) == 0


def test_judge_refuses_a_document_that_is_not_a_judge_packet(tmp_path):
    """`judge` must not render (and overwrite) from a run or verdicts file.

    #13303 review cycle 2, P1. `_judge_packet` always wrote a `schema` and NOTHING read
    it, while `judge` writes `run-{target}.json` / `scoreboard-{target}.md` into the
    packet's OWN directory by default — so a mistyped `--packet` silently replaced the
    committed baseline with a 0-row board and exited 0.

    The canary sits in the SAME directory as the packets and is named `run-local.json`,
    because that is exactly what a write-through would clobber (`--out` omitted →
    `packet_path.parent`); a canary anywhere else passes over a guard that writes first
    and refuses after (#13303 review cycle 3, P1).
    """
    d = tmp_path / "canary"
    d.mkdir()
    baseline = d / "run-local.json"
    baseline.write_text(json.dumps({"schema": "capstone-state-value-scoreboard/v1"}))
    before = baseline.read_bytes()

    for i, (name, payload) in enumerate(
        (
            # a RUN file: scoreboard schema, no questions
            ("a-run.json", {"schema": "capstone-state-value-scoreboard/v1"}),
            # a verdicts map that is not a packet
            ("b-verdicts.json", {"Q1": {"verdict": "right"}}),
            # right schema, NO questions — the second guard
            ("c-noquestions.json", {"schema": cap.PACKET_SCHEMA, "questions": []}),
            # WRONG schema WITH questions — only the schema guard can refuse this one,
            # which is what keeps that guard from being dead code
            (
                "d-wrongschema.json",
                {
                    "schema": "capstone-state-value-scoreboard/v1",
                    "questions": [
                        {"id": "Q1", "question": "q", "key": "k",
                         "what_a_wrong_answer_means": "m"}
                    ],
                },
            ),
        )
    ):
        p = d / f"{i}-{name}"
        p.write_text(json.dumps(payload))
        args = type("A", (), {"packet": str(p), "verdicts": None, "judge_cmd": None, "out": None})()
        assert cap.cmd_judge(args) == 2, name
        # It refused BEFORE writing: the run beside it is untouched.
        assert baseline.read_bytes() == before, name
        assert not (d / "scoreboard-local.md").exists(), name

    # A real judge packet is still accepted (the guard must not block the real path).
    real = json.loads(
        (ROOT / "docs" / "evidence" / "13038-capstone" / "judge-packet.json").read_text()
    )
    assert real["schema"] == cap.PACKET_SCHEMA and real["questions"]


def test_judge_refuses_a_verdicts_file_that_is_not_a_verdicts_map(tmp_path):
    """The sibling input to the same destructive write (#13303 review cycle 3, P3).

    `verdicts-baseline.json` is documented as "copied from `run-local.json`'s rows", so
    `--verdicts run-local.json` is a plausible typo — and it used to score nothing and
    write an all-`unjudged` board into the packet's directory.
    """
    d = tmp_path / "v"
    d.mkdir()
    packet = d / "packet.json"
    packet.write_text(
        (ROOT / "docs" / "evidence" / "13038-capstone" / "judge-packet.json").read_text()
    )
    baseline = d / "run-local.json"
    baseline.write_text("{\"keep\": true}")
    before = baseline.read_bytes()

    bad = d / "run-as-verdicts.json"
    bad.write_text(json.dumps({"schema": "capstone-state-value-scoreboard/v1"}))
    args = type("A", (), {"packet": str(packet), "verdicts": str(bad), "judge_cmd": None,
                          "out": None})()
    assert cap.cmd_judge(args) == 2
    assert baseline.read_bytes() == before
    assert not (d / "scoreboard-local.md").exists()

    # …and a REAL verdicts map is still accepted.
    real = json.loads(
        (ROOT / "docs" / "evidence" / "13038-capstone" / "verdicts-baseline.json").read_text()
    )
    assert isinstance(real, dict) and all(
        isinstance(v, dict) and v.get("verdict") in cap.VERDICTS
        for k, v in real.items()
        if not k.startswith("_")
    )


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


# ── 5. the fail-open paths a fresh reviewer measured (PR #13303 review) ─────────


def test_every_recipe_declares_the_refs_it_needs(narrative):
    """`RECIPE_REFS` is what makes "a refused door blocks the question" true.

    `must_appear`/`must_not_appear` cannot see a ref the recipe reads by id and never
    names, so before this map a typo in `q3_pricing` or `uptime_target` reddened nothing:
    the call was skipped and the positional payload lookup then handed the row a DIFFERENT
    call's node (measured: the successor served as `the_replaced_node`, printing
    `status='live', names-its-successor=False` — evidence inverted).
    """
    assert set(cap.RECIPE_REFS) == set(cap.RECIPES), (
        "every recipe must declare its refs (empty list when it needs none), or the "
        "drift this map exists to catch is invisible for that recipe"
    )
    produced = {op.get("ref") for op in narrative["ingest"]["ops"] if op.get("ref")}
    for recipe, refs in cap.RECIPE_REFS.items():
        unknown = set(refs) - produced
        assert not unknown, (
            f"{recipe} needs refs the narrative never produces: {sorted(unknown)} — it "
            "would read as `blocked` on every run, including a healthy one"
        )


def test_an_unresolved_ref_is_reported_and_never_silently_ignored():
    full = {"launch_plan": "p1"}
    assert cap.refs_unresolved("plan_vs_occurrence", full) == []
    assert cap.refs_unresolved("plan_vs_occurrence", {}) == ["launch_plan"]
    assert cap.refs_unresolved("why_changed", {"q4_pricing": "p2"}) == ["q3_pricing"]
    # Q1's answer key names four commitments and two identities that must not appear, so
    # it waits for all of them — `[]` here was the hole the second review found.
    assert cap.refs_unresolved("current_commitments", {}) == [
        "q3_pricing",
        "q4_pricing",
        "old_onboarding",
        "launch_plan",
        "uptime_goal",
    ]
    assert cap.refs_unresolved("unknown_recipe", {}) == []


def test_a_failed_or_absent_call_blocks_the_question_rather_than_scoring_it():
    """`blocked` is "we could not ask" — it must win over an empty answer."""
    ans = cap.Answer("Q2")
    ans.calls.append(cap.Call("search", {"query": "launch event"}, True, rows=[]))
    ans.calls.append(
        cap.Call("read_entity", {"id": "p1"}, False, "StoreUnavailable", role="plan_node")
    )
    cap._require_call(ans, "search", "plan_node", "events")
    assert ans.status == "blocked"
    assert "plan_node" in ans.reason and "events" in ans.reason

    ok = cap.Answer("Q2")
    ok.calls.append(cap.Call("search", {"query": "launch event"}, True, rows=[], role="search"))
    ok.calls.append(
        cap.Call("read_entity", {"id": "p1"}, True, rows=[{"id": "p1"}], role="plan_node")
    )
    ok.calls.append(cap.Call("read_events", {"limit": 1}, True, rows=[], role="events"))
    cap._require_call(ok, "search", "plan_node", "events")
    assert ok.status == "answered", (
        "a read that SUCCEEDED with zero rows is an answer ('the product served "
        "nothing'), not a blocked question — folding it into blocked hides a real gap"
    )


def test_blocked_is_never_folded_into_wrong_or_overridden_by_a_right(narrative):
    """A judge's `right` cannot turn an unasked question into a product success."""
    blocked = cap.Answer(
        "Q1", status="blocked", reason="the narrative's ingestion did not produce: q4_pricing"
    )
    board = cap.build_scoreboard(
        narrative={"questions": [narrative["questions"][0]]},
        header={},
        answers=[blocked],
        judged={"Q1": {"verdict": "right", "because": "looks fine to me"}},
        ingest=[],
    )
    row = board["rows"][0]
    assert row["verdict"] == "blocked", (
        "the reader's `right` overrode a question the instrument could not ask"
    )
    assert board["tally"]["blocked"] == 1 and board["tally"]["right"] == 0
    assert any("could not be asked" in n for n in row["notes"]), (
        "the disagreement must stay visible in the ROW THAT SHIPS — `ans.notes` is the "
        "in-memory object, and a note written only there reaches no artifact (#13303 "
        "cycle 4, P2)"
    )


def test_a_judge_file_without_verdicts_is_a_refusal_not_a_traceback(tmp_path):
    """`--verdicts` is optional in argparse; the refusal must still be the documented one.

    #13303 cycle 5: `_load_verdicts_map(None)` hit `Path(None)` → `TypeError`, which the
    callers do not catch, so `run --judge file` with the flag omitted printed a Python
    traceback where EXIT CODES promises "2 usage".
    """
    (tmp_path / "n.json").write_text(json.dumps({"questions": []}))
    assert cap.cmd_run(
        type("A", (), {"narrative": str(tmp_path / "n.json"), "out": str(tmp_path),
                      "judge": "file", "verdicts": None})()
    ) == 2
    assert cap.cmd_judge(
        type("A", (), {"packet": str(tmp_path / "p.json"), "verdicts": None,
                      "judge_cmd": None, "out": None})()
    ) == 2


def test_a_credential_in_the_deployed_api_url_never_reaches_the_recorded_header():
    """The deployed half of the redaction promise (#13303 cycle 5, coverage gap).

    `run-deployed-refused.json` is written into the COMMITTED evidence directory, and a
    deployed API URL can carry userinfo exactly as a store URI can. Constructing the
    target is hermetic (no network); the header copies `provenance()['api']` into `store`.
    """
    target = cap.DeployedTarget(
        api_url="https://user:hunter2@127.0.0.1:9", api_key="tt_abcdefghijkl"
    )
    prov = target.provenance()
    assert "hunter2" not in prov["api"] and "<redacted>" in prov["api"]
    assert "hunter2" not in str(prov) and "tt_abcdefghijkl" not in str(prov)


def test_run_judge_file_refuses_a_verdicts_document_before_measuring(tmp_path, narrative):
    """`run --judge file` has the SAME `--verdicts` door as `judge` (#13303 cycle 4, P1).

    Both write into `--out`, whose default is the committed evidence directory. This
    door was open after cycle 3 fixed only `judge`: a run file scored nothing and wrote
    `0 right · 0 wrong · 0 blocked · 6 unjudged` over the board — after spending the full
    measurement. The refusal must come BEFORE the target is built, so the pin asserts
    exit 2 and an empty output directory.
    """
    out = tmp_path / "out"
    out.mkdir()
    nar = tmp_path / "narrative.json"
    nar.write_text(json.dumps(narrative))
    bad = tmp_path / "run-as-verdicts.json"
    bad.write_text(json.dumps({"schema": "capstone-state-value-scoreboard/v1"}))
    args = type(
        "A",
        (),
        {"narrative": str(nar), "out": str(out), "judge": "file", "verdicts": str(bad)},
    )()
    assert cap.cmd_run(args) == 2
    assert not list(out.iterdir()), "a refused verdicts document must leave the board alone"

    # …and the shared loader accepts the REAL verdicts map, so both doors stay walkable.
    real = cap._load_verdicts_map(ROOT / "docs" / "evidence" / "13038-capstone" / "verdicts-baseline.json")
    assert "_provenance" not in real and all(isinstance(v, dict) for v in real.values())


def test_an_unknown_verdict_is_unjudged_and_never_vanishes_from_the_tally(narrative):
    """`tally` is summed over the closed vocabulary; an unseen word must not escape it."""
    questions = narrative["questions"]
    answers = [cap.Answer(q["id"]) for q in questions]
    judged = {q["id"]: {"verdict": "right", "because": "x"} for q in questions}
    judged["Q1"] = {"verdict": "Excellent", "because": "a fourth word invented at the keyboard"}
    board = cap.build_scoreboard(
        narrative={"questions": questions}, header={}, answers=answers, judged=judged, ingest=[]
    )
    assert board["rows"][0]["verdict"] == "unjudged"
    assert sum(board["tally"].values()) == len(questions), (
        "a row outside the vocabulary left the tally, so the header could report fewer "
        "verdicts than the scoreboard has rows"
    )
    assert set(board["tally"]) == set(cap.VERDICTS)


def test_a_verdict_for_a_question_that_was_never_asked_is_blocked(narrative):
    """A question never put to the product is `blocked`, not `unjudged` and not `right`.

    The row already renders `answer_status: "blocked"` and `product_answer: "(not asked)"`,
    so any other verdict makes the row contradict itself — and `right` would score the
    product for a question the instrument never asked.
    """
    board = cap.build_scoreboard(
        narrative={"questions": [narrative["questions"][0]]},
        header={},
        answers=[],  # the question was never asked
        judged={"Q1": {"verdict": "right", "because": "the reader had no answer to read"}},
        ingest=[],
    )
    assert board["rows"][0]["verdict"] == "blocked"
    assert board["rows"][0]["answer_status"] == "blocked"
    assert board["rows"][0]["product_answer"] == "(not asked)"


def test_credentials_never_reach_a_recorded_line_through_the_store_uri():
    """The header records the store URI and the scoreboard renders it."""
    for uri in (
        "docker://:hunter2@127.0.0.1:6379/db",
        "bolt://falkordb:s3cr3t-pw@db.example.com:6379",
        "redis://:Wq8-xT2pLm@10.0.0.5:6379/0",
    ):
        red = cap._redact(uri)
        assert "hunter2" not in red and "s3cr3t-pw" not in red and "Wq8-xT2pLm" not in red
        assert "<redacted>@" in red
    assert cap._redact("docker://:@127.0.0.1:6379/db").count("@") == 1


def test_a_by_id_read_never_fires_retired_as_current():
    """Seeing a retired node BY ID is the legitimate way to ask what ended."""
    ans = cap.Answer("Q5")
    ans.calls.append(
        cap.Call(
            "read_entity",
            {"id": "abc"},
            True,
            rows=[{"id": "abc", "name": "Thursday partner call"}],
            role="cancelled_plan",
        )
    )
    cap._signal_refs(ans, {"partner_call": "abc"}, {"must_not_appear": ["partner_call"]})
    assert not any(s.startswith("RETIRED-AS-CURRENT") for s in ans.signals), (
        "a by-id read was reported as 'served as current' — the false signal would be "
        "attributed to the read path in the one field a reader uses for that"
    )
    ans.calls.append(
        cap.Call("search", {"query": "partner call"}, True, rows=[{"id": "abc"}], role="by_name")
    )
    cap._signal_refs(ans, {"partner_call": "abc"}, {"must_not_appear": ["partner_call"]})
    assert any(s.startswith("RETIRED-AS-CURRENT") for s in ans.signals), (
        "a SEARCH still serving the retired id must fire — the fix must not disable the signal"
    )


def test_a_role_returns_only_that_calls_rows():
    ans = cap.Answer("Q3")
    ans.calls.append(
        cap.Call("read_entity", {"id": "new"}, True, rows=[{"id": "new"}], role="successor_node")
    )
    assert ans.rows("replaced_node") == [], (
        "a missing role must be EMPTY, never another call's rows — positional indexing "
        "served the successor as the replaced node and inverted the evidence"
    )
    assert ans.first("successor_node")["id"] == "new"
    assert ans.call("replaced_node") is None


def test_the_verdicts_file_the_instrument_writes_is_the_one_it_reads(tmp_path):
    judged = {
        "Q1": {"verdict": "wrong", "because": "omits the launch plan", "cause": "reach"},
        "Q2": {"verdict": "right", "because": "plan vs occurrence", "cause": None},
    }
    out = tmp_path / "verdicts.json"
    cap._write_verdicts(
        out, judged, packet_path=tmp_path / "packet.json", mode="fresh", meta={"elapsed_s": 150.2}
    )
    doc = json.loads(out.read_text())
    assert doc["_provenance"]["reader"] == "fresh"
    for qid, row in judged.items():
        assert doc[qid] == row, "the round-trip lost a verdict"
    # the consumer's own read path: `judged.get(id)` then a closed-vocabulary lookup
    for qid in judged:
        assert doc.get(qid, {}).get("verdict") in cap.VERDICTS


# ── 6. the second review's findings (the merge of two fix chains) ───────────────


def test_a_refused_lifecycle_op_blocks_the_question(narrative):
    """A ref whose IDENTITY write landed but whose state op was REFUSED is not usable.

    `Ingestor.refs` is filled by the identity op, so a refused `advance_stage` leaves the
    plan in `refs` and looking never-cancelled: the question is asked, the product serves a
    `live` row, and the reader attributes the instrument's own failed write to a product
    issue (#13034). Measured shape: `advance_stage(cancelled)` → `applied: false`.
    """
    refs = {"partner_call": "p1", "launch_plan": "p2", "beach_club_claim": "p3"}
    assert cap.refs_unresolved("planned_never_happened", refs) == []
    assert cap.refs_unresolved("planned_never_happened", refs, {"partner_call"}) == [
        "partner_call"
    ], "a refused state-changing write left the question askable"
    assert cap.refs_unresolved("current_commitments", {"launch_plan": "p2"}, {"launch_plan"}) != []


def test_the_ingestor_records_the_ref_a_refused_op_was_about():
    class _Target:
        def __init__(self):
            self.capabilities = set(cap.CAPABILITIES)

        def advance_stage(self, *a, **k):
            return {"applied": False, "reason": "terminal-guard"}

        def create_object(self, name, kind, props=None):
            return {"id": "n1"}

    ing = cap.Ingestor(
        _Target(),
        [
            {"op": "object", "ref": "partner_call", "name": "call", "kind": "plan"},
            {
                "op": "advance_stage",
                "ref": "partner_call",
                "pipeline": "p",
                "stage": "cancelled",
            },
        ],
    )
    log = ing.run()
    assert [r["ok"] for r in log] == [True, False], "the refusal was not recorded"
    assert ing.failed_refs == {"partner_call"}, (
        "a refused state-changing op must mark its ref failed — the ingest log alone let "
        "the question be asked anyway"
    )
    assert cap.refs_unresolved("planned_never_happened", ing.refs, ing.failed_refs) == [
        "partner_call"
    ]


def test_every_question_declares_the_refs_its_ANSWER_KEY_judges(narrative):
    """The refs a question is scored against must be refs it waits for.

    Q1's key names four commitments and two identities that must not appear; declaring `[]`
    asked it against a graph the scenario had not created. This is the general form, so a
    future edit cannot re-open the hole for a different question.
    """
    for q in narrative["questions"]:
        judged = set(q.get("must_appear") or []) | set(q.get("must_not_appear") or [])
        declared = set(cap.RECIPE_REFS[q["recipe"]])
        assert judged <= declared, (
            f"{q['id']} is scored against refs its recipe never waits for: "
            f"{sorted(judged - declared)} — a refused write for one of them would not "
            "block the question, and the reader would score a graph missing it"
        )


def test_a_ref_that_was_never_ingested_is_said_out_loud():
    """Absence used to be silent (`if pid:`), so the reader saw nothing about the gap."""
    ans = cap.Answer("Q1")
    cap._signal_refs(ans, {}, {"must_appear": ["q4_pricing"], "must_not_appear": ["q3_pricing"]})
    joined = " | ".join(ans.signals)
    assert "NOT-INGESTED: q4_pricing" in joined and "NOT-INGESTED: q3_pricing" in joined


def test_the_reader_is_spawned_without_tools_in_an_empty_sandbox(monkeypatch):
    """The property that makes a later scoreboard meaningful: independence.

    A reader holding tools, sitting in the repo, can re-read `run-{target}.json` /
    `scoreboard-{target}.md` — both still on disk beside the packet when it is spawned — and
    reproduce the PREVIOUS run's verdicts, so a later run could not show that a slice moved
    the product. Pinned hermetically: no model, no real subprocess.
    """
    import subprocess as real_subprocess

    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["cwd"] = kwargs.get("cwd")
        seen["cwd_exists"] = Path(kwargs.get("cwd") or "/").is_dir()
        seen["cwd_is_repo"] = Path(kwargs.get("cwd") or "/").resolve() == cap.ROOT

        class _P:
            stdout = "I could not decide."
            stderr = ""
            returncode = 0

        return _P()

    monkeypatch.setattr(cap.shutil, "which", lambda _: "/usr/bin/pi")
    monkeypatch.setattr(cap.subprocess, "run", fake_run)

    out = cap.judge_fresh({"narrative": {}, "questions": [], "product_answers": [], "rubric": {}})
    assert "--no-tools" in seen["argv"], "the reader can still read the repo's artifacts"
    assert "--no-session" in seen["argv"]
    assert seen["cwd"] != str(cap.ROOT) and not seen["cwd_is_repo"], (
        "the reader was spawned inside the repo, where the previous run's scoreboard sits"
    )
    assert seen["cwd_exists"], "the sandbox did not exist when the reader was spawned"
    assert out["ok"] is False and out["verdicts"] == {}, (
        "an unparseable reply must yield no verdicts — never a default"
    )
    assert isinstance(real_subprocess, type(cap.subprocess))


def test_the_verdicts_file_write_creates_its_directory(tmp_path):
    """A typo'd `--verdicts-out` used to abort the run AFTER the 150 s reader had run."""
    target = tmp_path / "does" / "not" / "exist" / "verdicts.json"
    cap._write_verdicts(
        target, {"Q1": {"verdict": "right"}}, packet_path=tmp_path / "p.json", mode="fresh", meta={}
    )
    assert target.exists() and json.loads(target.read_text())["Q1"]["verdict"] == "right"
