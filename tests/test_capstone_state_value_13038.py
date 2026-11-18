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

import inspect
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
                        {"id": "Q1", "question": "q", "key": "k", "what_a_wrong_answer_means": "m"}
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
    baseline.write_text('{"keep": true}')
    before = baseline.read_bytes()

    bad = d / "run-as-verdicts.json"
    bad.write_text(json.dumps({"schema": "capstone-state-value-scoreboard/v1"}))
    args = type(
        "A", (), {"packet": str(packet), "verdicts": str(bad), "judge_cmd": None, "out": None}
    )()
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
    # The SAME key set `Ingestor._record` attributes a log row to — `ref` OR `old` OR
    # `goal` (and `pipeline`): reading only `op["ref"]` hid the `old`/`goal`-shaped ops.
    # TWO sets, deliberately (#13303 cycle 7): `identities` is what `Ingestor.refs` can
    # actually hold — only `object`/`point`/`reading` ops write a ref there — so it is the
    # set a recipe's declaration is graded against. `produced` is the WIDER attribution set
    # `_record` files a log row under (`ingest_ref_key`, including `pipeline`), used for the
    # coverage union below. Grading the typo guard on `produced` accepted `core:plan` as a
    # resolvable ref, and a recipe declaring it would be `blocked` on every healthy run with
    # the guard green (measured: 51 passed with `withdrawn = [..., "core:plan"]`).
    identities = {
        op["ref"]
        for op in narrative["ingest"]["ops"]
        if op.get("op") in ("object", "point", "reading") and op.get("ref")
    }
    produced = {cap.ingest_ref_key(op) for op in narrative["ingest"]["ops"]} - {""}
    for recipe, refs in cap.RECIPE_REFS.items():
        unknown = set(refs) - identities
        assert not unknown, (
            f"{recipe} needs refs the narrative never produces: {sorted(unknown)} — it "
            "would read as `blocked` on every run, including a healthy one"
        )
    # The other direction, and the one that cost a P1 (#13303 review cycle 5): an ingest
    # ref that NO recipe waits for can be refused without blocking anything, so the
    # question whose answer key depends on it is asked against a graph the scenario never
    # created. `uptime_nov` was exactly that — a refused November reading left the
    # product's own judgement `met` from October, and the reader scored the instrument's
    # failed write as a product defect.
    watched = set().union(*cap.RECIPE_REFS.values())
    allowed = set(cap.SCENARIO_ONLY_REFS)
    assert produced <= watched | allowed, (
        "these ingest refs are waited for by no question, so their refusal blocks nothing: "
        f"{sorted(produced - watched - allowed)} — declare them on the question whose answer "
        "key needs them, or add them to SCENARIO_ONLY_REFS with the reason"
    )
    assert allowed <= produced, (
        f"SCENARIO_ONLY_REFS names refs the narrative no longer creates: {sorted(allowed - produced)} "
        "— a stale allowlist silently licenses a future unwatched ref"
    )


def test_an_unresolved_ref_is_reported_and_never_silently_ignored():
    full = {"launch_plan": "p1"}
    assert cap.refs_unresolved("plan_vs_occurrence", full) == []
    assert cap.refs_unresolved("plan_vs_occurrence", {}) == ["launch_plan"]
    assert cap.refs_unresolved("why_changed", {"q4_pricing": "p2"}) == ["q3_pricing"]
    # Q1's answer key names four commitments and two identities that must not appear, so
    # it waits for all of them — `[]` here was the hole the second review found.
    # `uptime_TARGET`, not `uptime_GOAL` (#13303 cycle 6, P2): the key names the 99.9 %
    # target as the commitment, so declaring the goal object let a refused `judge_goal`
    # write — a Q4 concern — block Q1 and drop its measured `wrong`.
    # October is declared deliberately: the scenario is not the scenario without both
    # readings, so the fail-closed direction wins (#13303 cycle 6, the lane's recorded
    # decision). November is the one whose absence would make the judgement STALE.
    assert cap.refs_unresolved("goal_met", {"uptime_goal": "g", "uptime_target": "t"}) == [
        "uptime_oct",
        "uptime_nov",
    ]
    assert cap.refs_unresolved(
        "goal_met", {"uptime_goal": "g", "uptime_target": "t", "uptime_nov": "r"}
    ) == ["uptime_oct"]
    all_goal = {"uptime_goal": "g", "uptime_target": "t", "uptime_nov": "r", "uptime_oct": "r"}
    assert cap.refs_unresolved("goal_met", all_goal) == []
    # Both readings are declared, and the two reasons differ: NOVEMBER's absence would make
    # the product's judgement STALE (October's 99.95 becomes newest → `met`), while OCTOBER
    # is declared because a scenario missing one of its readings is not the frozen scenario
    # — the fail-closed direction. (A reviewer proposed dropping October on the ground that
    # its absence cannot stale the judgement; that reading is measured and true, and the
    # declaration is a deliberate decision to prefer declining the question to scoring a
    # thinner graph. Recorded here so the next reader does not re-litigate it as an oversight.)
    assert {"uptime_oct", "uptime_nov"} <= set(cap.RECIPE_REFS["goal_met"])
    assert cap.refs_unresolved("current_commitments", {}) == [
        "q3_pricing",
        "q4_pricing",
        "old_onboarding",
        "launch_plan",
        "uptime_target",
    ]
    # …and a refused JUDGEMENT write blocks Q4 (whose key is the judged state) without
    # blocking Q1 (whose key is the commitment).
    all_refs = {r: r for r in cap.RECIPE_REFS["current_commitments"]}
    all_refs.update({r: r for r in cap.RECIPE_REFS["goal_met"]})
    assert cap.refs_unresolved("current_commitments", all_refs, {"uptime_goal"}) == []
    assert cap.refs_unresolved("goal_met", all_refs, {"uptime_goal"}) == ["uptime_goal"]
    # Q4's key is the LATEST reading (November's 99.72): with that write refused, October's
    # 99.95 would be judged newest and the product would report `met` — the instrument's own
    # failed write scored as a product defect (#13303 cycle 6, P1).
    assert cap.refs_unresolved("goal_met", all_refs, {"uptime_nov"}) == ["uptime_nov"]
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
    assert (
        cap.cmd_run(
            type(
                "A",
                (),
                {
                    "narrative": str(tmp_path / "n.json"),
                    "out": str(tmp_path),
                    "judge": "file",
                    "verdicts": None,
                },
            )()
        )
        == 2
    )
    assert (
        cap.cmd_judge(
            type(
                "A",
                (),
                {
                    "packet": str(tmp_path / "p.json"),
                    "verdicts": None,
                    "judge_cmd": None,
                    "out": None,
                },
            )()
        )
        == 2
    )


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


def test_the_deployed_probe_records_a_userinfo_url_without_echoing_the_secret_or_raising():
    """A probe records; it must not raise, and it must not echo the credential.

    #13303 cycle 6, P1: `http.client.InvalidURL` is an `HTTPException`, NOT a `ValueError`,
    so a userinfo URL with no explicit port made `_get_hostport` read the password as a
    port — the traceback AND the recorded `error` string carried it, and the run exited 2
    where EXIT CODES promises a refusal with exit 3. The prior pin used `…:9`, an explicit
    port, so it never reached the crashing shape.
    """
    target = cap.DeployedTarget(
        api_url="https://alice:s3cr3t-pw@api.example.com", api_key="tt_SECRETKEY123"
    )
    prov = target.provenance()
    blob = json.dumps(prov)
    assert "s3cr3t-pw" not in blob, "the password reached a recorded line"
    assert "tt_SECRETKEY123" not in blob
    reach = prov["reachability"]
    assert reach["error"], "the failure must be RECORDED, not swallowed silently"
    assert "s3cr3t" not in str(reach["error"])


def test_judge_refuses_to_write_into_the_evidence_bundle(tmp_path):
    """`judge` must not replace the real run's board with a lower-fidelity copy.

    #13303 cycle 6, P2: `--out` defaults to the packet's directory, so re-scoring the
    committed packet in place rewrote `run-local.json` / `scoreboard-local.md` with a
    header frozen BEFORE the reader ran (`mode: file`, 62.8 s) and exited 0. Measured: both
    bundle files changed md5.
    """
    args = type(
        "A",
        (),
        {
            "packet": str(ROOT / "docs" / "evidence" / "13038-capstone" / "judge-packet.json"),
            "verdicts": str(
                ROOT / "docs" / "evidence" / "13038-capstone" / "verdicts-baseline.json"
            ),
            "judge_cmd": None,
            "out": str(cap.DEFAULT_OUT),
        },
    )()
    before = {
        p.name: p.read_bytes()
        for p in (cap.DEFAULT_OUT / "run-local.json", cap.DEFAULT_OUT / "scoreboard-local.md")
    }
    assert cap.cmd_judge(args) == 2
    after = {
        p.name: p.read_bytes()
        for p in (cap.DEFAULT_OUT / "run-local.json", cap.DEFAULT_OUT / "scoreboard-local.md")
    }
    assert before == after, "the bundle's board was replaced"


def test_render_obeys_the_same_exit_rule_as_run_and_judge():
    """The third producer of a scoreboard must not report success over an unjudged board.

    #13303 cycle 6, P2: `render` returned 0 unconditionally, while EXIT CODES says a board
    with `unjudged` rows is a 1.
    """
    board = {
        "schema": "capstone-state-value-scoreboard/v1",
        "header": {},
        "ingest": [],
        "rows": [],
        "tally": {"right": 0, "wrong": 0, "blocked": 0, "unjudged": 6},
    }
    assert cap.exit_code(board["tally"]) == 1
    assert cap.exit_code({"right": 3, "wrong": 3, "blocked": 0, "unjudged": 0}) == 0


def test_judge_refuses_a_packet_holding_a_subset_of_the_frozen_scenario(tmp_path):
    """A packet with fewer questions than the narrative renders a complete-looking board.

    #13303 cycle 6, P2: three of six questions produced "2 right · 1 wrong · 0 blocked ·
    0 unjudged" and exited 0 — a different denominator, presented as the assessment.
    """
    real = json.loads(
        (ROOT / "docs" / "evidence" / "13038-capstone" / "judge-packet.json").read_text()
    )
    assert cap._same_questions_as_narrative(real) is True
    truncated = dict(real)
    truncated["questions"] = real["questions"][:3]
    truncated["product_answers"] = real["product_answers"][:3]
    assert cap._same_questions_as_narrative(truncated) is False
    p = tmp_path / "truncated.json"
    p.write_text(json.dumps(truncated))
    args = type(
        "A", (), {"packet": str(p), "verdicts": None, "judge_cmd": None, "out": str(tmp_path / "o")}
    )()
    assert cap.cmd_judge(args) == 2


def test_all_three_producers_return_the_same_exit_rule(tmp_path):
    """`run`, `judge` and `render` are all scoreboard producers and must agree (#13303 c7).

    The pin this replaces asserted only `cap.exit_code(...)`, which is a function of a
    hand-built dict — mutating `cmd_render` back to `return 0` left the suite GREEN. So:
    the render half is exercised BEHAVIOURALLY here, and all three call sites are asserted
    structurally (no hermetic run can reach `cmd_run`'s tally: it needs a live graph).
    """
    import inspect

    unjudged = {
        "schema": "capstone-state-value-scoreboard/v1",
        "header": {},
        "ingest": [],
        "rows": [],
        "tally": {"right": 0, "wrong": 0, "blocked": 0, "unjudged": 6},
    }
    clean = dict(unjudged, tally={"right": 3, "wrong": 3, "blocked": 0, "unjudged": 0})
    a = tmp_path / "unjudged.json"
    b = tmp_path / "clean.json"
    a.write_text(json.dumps(unjudged))
    b.write_text(json.dumps(clean))
    mk = lambda p: type("A", (), {"run": str(p), "write": None})()  # noqa: E731
    assert cap.cmd_render(mk(a)) == 1, "render reported success over an unjudged board"
    assert cap.cmd_render(mk(b)) == 0
    for fn in (cap.cmd_run, cap.cmd_judge, cap.cmd_render):
        assert "exit_code(" in inspect.getsource(fn), (
            f"{fn.__name__} does not route its exit through exit_code, so it can report "
            "success over a board the table calls a failure"
        )


def test_a_long_secret_is_redacted_before_truncation(tmp_path):
    """A secret longer than the truncation budget must not survive as a PREFIX.

    #13303 cycle 7, P1: `_cut(secret, 160)` kept 141 characters of a 210-character password,
    and neither the `scheme://user:pass@` regex nor a literal `replace` of the FULL secret
    can match a prefix — so it was written into `run-deployed-refused.json`.
    """
    secret = "J" * 210
    url = f"https://alice:{secret}@api.example.com"
    # The real vector: the exception text carries the FULL secret, and the redaction that can
    # see it is `_strip_userinfo`; truncating first is what kept a 141-character prefix.
    msg = f"/health: InvalidURL: nonnumeric port: '{secret}@api.example.com'"
    out = cap._cut(cap._strip_userinfo(msg, url), 160)
    assert secret[:20] not in out, "a prefix of the secret survived redact-then-truncate"
    assert "alice" not in out
    # …and the keyword form, which `_redact` matches directly, must redact before cutting too.
    assert secret[:20] not in cap._cut("password=" + secret, 160)
    assert secret[:20] not in cap._cut("token: " + secret + " trailing prose", 160)

    # …and the deployed probe path, which is the one that writes a file.
    target = cap.DeployedTarget(api_url=url, api_key="tt_x")
    blob = json.dumps(target.provenance())
    assert secret[:100] not in blob and "alice" not in blob.replace("https://", "")


def test_only_a_complete_run_may_publish_into_the_bundle(tmp_path):
    """The publish rule is keyed on the BOARD, not on a flag (#13303 cycle 8, P1).

    Three different invocations produce an all-`unjudged` board — `--judge none`, a
    `--judge fresh` run whose reader failed, `--judge file` with an empty map — and the
    flag-keyed guard let the last two replace the committed baseline while the README said
    that could not happen. Measured: `--judge fresh --judge-cmd true` changed all three
    committed files and exited 1 over `0 right · 0 wrong · 0 blocked · 6 unjudged`.
    """
    unjudged = {"tally": {"right": 0, "wrong": 0, "blocked": 0, "unjudged": 6}}
    blocked = {"tally": {"right": 3, "wrong": 2, "blocked": 1, "unjudged": 0}}
    clean = {"tally": {"right": 3, "wrong": 3, "blocked": 0, "unjudged": 0}}
    assert cap.may_publish(unjudged, cap.DEFAULT_OUT) is False
    assert cap.may_publish(blocked, cap.DEFAULT_OUT) is False
    assert cap.may_publish(clean, cap.DEFAULT_OUT) is True
    # …anywhere else is the caller's business, including an unjudged board.
    assert cap.may_publish(unjudged, tmp_path) is True
    # …and every write door consults the bundle rule (none may hash its own copy): `run`
    # asks `may_publish`, and `render` refuses the bundle outright because it is not a
    # producer at all.
    import inspect

    assert "may_publish(" in inspect.getsource(cap.cmd_run), (
        "cmd_run writes the board into the bundle without consulting may_publish"
    )
    assert "DEFAULT_OUT" in inspect.getsource(cap.cmd_render), (
        "cmd_render --write does not consult the bundle at all"
    )
    assert "DEFAULT_OUT" in inspect.getsource(cap.cmd_judge), (
        "cmd_judge does not consult the bundle at all"
    )


def test_product_served_text_is_not_redacted_AT_THE_CALL_SITES():
    """The `redact=False` rule must hold where the text is used, not only in `_cut` (#13303 c10).

    Cycle 8 made `_cut(..., redact=False)` for product-served text, but pinned it only as a
    capability of `_cut` — so reverting any call site left the suite green and the reader went
    back to grading text the instrument had already rewritten.
    """
    import inspect

    src = inspect.getsource(cap)
    for needle, what in (
        # `or r.get("name")` joined this needle with #13302: an ENDED Object row serves
        # its text under `name`, and a projection that dropped it would hand the reader
        # an empty `what` — the same harm the redaction rule guards, one step earlier.
        (
            'r.get("content") or r.get("name") or r.get("snippet") or "", 120, redact=False',
            "excerpt `what`",
        ),
        ('row.get("because") or "", 400, redact=False', "verdict `because`"),
        ('row.get("cause") or "", 240, redact=False', "verdict `cause`"),
        ("r.get('what'), 70, redact=False", "scoreboard row `what`"),
    ):
        assert needle in src, f"{what} is redacted — the reader would grade altered text"


def test_the_packet_is_written_only_after_the_publish_guard(tmp_path):
    """A REFUSED run must not replace the committed `judge-packet.json` (#13303 cycle 9, P1).

    The packet used to be written BEFORE `may_publish`, so a run that refused to publish its
    board still replaced the one committed file that says which run the numbers came from —
    while the refusal message said "Nothing was written".
    """
    import inspect

    src = inspect.getsource(cap.cmd_run)
    guard = src.index("may_publish(board, out_dir)")
    packet = src.index("packet_path.write_text")
    assert guard < packet, "judge-packet.json is written before the publish guard"
    for name in ('f"run-{target.kind}.json"', 'f"scoreboard-{target.kind}.md"'):
        assert guard < src.index(name), f"{name} is written before the publish guard"


def test_verdicts_out_cannot_cross_runs_into_the_bundle(tmp_path):
    """`--verdicts-out` refuses the bundle by IDENTITY, not by completeness (#13303 c9, P2).

    A COMPLETE board from a scratch run still must not land its verdicts beside the committed
    board: `verdicts-baseline.json` would then describe a different run than
    `run-local.json`, and nothing would say so.
    """
    args = type(
        "A",
        (),
        {
            "packet": str(ROOT / "docs" / "evidence" / "13038-capstone" / "judge-packet.json"),
            "verdicts": str(
                ROOT / "docs" / "evidence" / "13038-capstone" / "verdicts-baseline.json"
            ),
            "judge_cmd": None,
            "out": str(tmp_path),
            "verdicts_out": str(cap.DEFAULT_OUT / "verdicts-baseline.json"),
        },
    )()
    before = (cap.DEFAULT_OUT / "verdicts-baseline.json").read_bytes()
    assert cap.cmd_judge(args) == 2
    assert (cap.DEFAULT_OUT / "verdicts-baseline.json").read_bytes() == before


def test_publishing_cannot_be_pointed_at_another_bundle_file(tmp_path):
    """`--verdicts-out` is decided by FILE, not by directory (#13303 cycle 10, P1).

    The cycle-9 guard keyed on the parent directory and exempted a publishing run, so
    `--verdicts-out <bundle>/run-local.json` replaced the machine-readable board with a
    verdicts map and still exited 0.
    """
    board = json.loads(
        (ROOT / "docs" / "evidence" / "13038-capstone" / "run-local.json").read_text()
    )
    assert board["tally"] == {"right": 3, "wrong": 3, "blocked": 0, "unjudged": 0}
    # …the only name the bundle accepts is the baseline, asserted structurally below; the
    # behavioural half (a real run) lives in `test_a_publishing_run_writes_its_own_baseline`
    # because it needs the whole `cmd_run` path.
    import inspect

    src = inspect.getsource(cap.cmd_run)
    gate = src.index("verdicts_target.name")
    assert "verdicts-baseline.json" in src[gate : gate + 400], (
        "the bundle gate no longer restricts the name to verdicts-baseline.json"
    )
    # …and the decision is taken BEFORE the publish guard, so nothing is written first.
    assert src.index("verdicts_target.is_dir()") < src.index("may_publish(board, out_dir)")


def test_a_publishing_run_writes_its_own_baseline(monkeypatch, tmp_path):
    """A complete run that publishes its board also publishes its verdicts (#13303 c10, P2).

    Without this, `run --judge fresh` into the bundle left `verdicts-baseline.json`
    describing a DIFFERENT run, with exit 0 and nothing said — falsifying the README's
    "the tally is one value in the three files that carry verdicts".
    """
    import inspect

    src = inspect.getsource(cap.cmd_run)
    assert 'DEFAULT_OUT / "verdicts-baseline.json"' in src or (
        "DEFAULT_OUT" in src and "verdicts-baseline.json" in src
    ), "a publishing run does not default its verdicts target to the baseline"


class _StubTarget:
    """The smallest target that reaches the publish guard without a database (#13303 c11)."""

    kind = "local"
    namespace = "stub"
    uri = "docker://127.0.0.1:6379/stub"

    def __init__(self):
        self.capabilities: set = set()

    def node_count(self):
        return 0

    def provenance(self):
        return {"product": {"commit": "stub"}}

    def drop(self):
        return "dropped"


class _StubIngestor:
    def __init__(self, target, ops):
        self.refs: dict = {}
        self.failed_refs: set = set()

    def run(self):
        return []


def _parse_run(monkeypatch, tmp_path, out, extra=()):
    """(bundle, argv) for a hermetic `cmd_run` invocation."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for f in (ROOT / "docs" / "evidence" / "13038-capstone").iterdir():
        if f.is_file():
            (bundle / f.name).write_bytes(f.read_bytes())
    monkeypatch.setattr(cap, "DEFAULT_OUT", bundle, raising=True)
    monkeypatch.setattr(cap, "build_target", lambda *a, **k: _StubTarget(), raising=True)
    monkeypatch.setattr(cap, "Ingestor", _StubIngestor, raising=True)

    # Every recipe ANSWERS (status "answered") without touching a store, and no recipe
    # declares a ref or a capability — so the rows take the judge's verdicts and the tally is
    # a completed run (3 right / 3 wrong), which is what the publish guard requires.
    def _stub_recipe(target, refs, q):
        return cap.Answer(q["id"])

    narrative = json.loads(cap.DEFAULT_NARRATIVE.read_text())
    names = {q.get("recipe") for q in narrative["questions"]} | set(cap.RECIPES)
    monkeypatch.setattr(cap, "RECIPES", {n: _stub_recipe for n in names if n}, raising=True)
    monkeypatch.setattr(cap, "RECIPE_NEEDS", {}, raising=True)
    monkeypatch.setattr(cap, "RECIPE_REFS", {}, raising=True)
    dest = bundle if out == "bundle" else tmp_path / "scratch"
    return bundle, [
        "run",
        "--target",
        "local",
        "--narrative",
        str(cap.DEFAULT_NARRATIVE),
        "--out",
        str(dest),
        "--judge",
        "file",
        "--verdicts",
        str(bundle / "verdicts-baseline.json"),
        *extra,
    ]


def _snapshot(bundle):
    return {f.name: f.read_bytes() for f in bundle.iterdir() if f.is_file()}


def test_a_publishing_run_cannot_send_its_verdicts_elsewhere(monkeypatch, tmp_path):
    """Publishing the board and putting the verdicts elsewhere leaves a MIXED bundle (#13303 c11, #1)."""
    bundle, argv = _parse_run(
        monkeypatch, tmp_path, "bundle", ["--verdicts-out", str(tmp_path / "v.json")]
    )
    before = _snapshot(bundle)
    assert cap.main(argv) == 1
    assert _snapshot(bundle) == before


def test_a_descendant_path_cannot_write_inside_the_bundle(monkeypatch, tmp_path):
    """`parent == bundle` let `<bundle>/nested/v.json` through; `_write_verdicts` mkdir'd it (#13303 c11, #2).

    Exercised on a NON-PUBLISHING run on purpose: when publishing, the "verdicts must go to
    the baseline" rule refuses first, so a descendant test there would pass without ever
    reaching the containment check (mutation-verified: reverting `is_relative_to` to
    `parent ==` stayed green until this case was added).
    """
    bundle, argv = _parse_run(
        monkeypatch,
        tmp_path,
        "scratch",
        ["--verdicts-out", str(tmp_path / "bundle" / "nested" / "v.json")],
    )
    before = _snapshot(bundle)
    assert cap.main(argv) == 1
    assert _snapshot(bundle) == before
    assert not (bundle / "nested").exists()


def test_judge_refuses_a_directory_or_a_descendant_target(monkeypatch, tmp_path, capsys):
    """Both `--verdicts-out` shapes are refused from `judge` too (#13303 c11, #2 and #7)."""
    bundle, _ = _parse_run(monkeypatch, tmp_path, "scratch")
    base = {
        "packet": str(bundle / "judge-packet.json"),
        "verdicts": str(bundle / "verdicts-baseline.json"),
        "judge_cmd": None,
        "out": str(tmp_path / "scratch"),
    }
    outside_dir = tmp_path / "verdicts_as_a_dir"
    outside_dir.mkdir()
    for target in (str(bundle), str(bundle / "nested" / "v.json"), str(outside_dir)):
        args = type("A", (), {**base, "verdicts_out": target})()
        before = _snapshot(bundle)
        assert cap.cmd_judge(args) == 2, f"{target} was not refused"
        assert _snapshot(bundle) == before
    assert not (bundle / "nested").exists()
    # A directory OUTSIDE the bundle is refused by the same rule. Without it `_write_verdicts`
    # raises `IsADirectoryError`, which surfaces as a usage traceback — exit 2 for the wrong
    # reason, which is why the MESSAGE is asserted, not only the code.
    args = type("A", (), {**base, "verdicts_out": str(outside_dir)})()
    assert cap.cmd_judge(args) == 2
    assert "that is a directory" in capsys.readouterr().err


def test_publishing_refuses_a_board_that_is_not_judged(monkeypatch, tmp_path):
    """The publish guard itself, exercised rather than grepped (#13303 c11, #6)."""
    monkeypatch.setattr(cap, "DEFAULT_OUT", tmp_path / "bundle", raising=True)
    bundle, argv = _parse_run(monkeypatch, tmp_path, "bundle", ["--judge", "none"])
    before = _snapshot(bundle)
    assert cap.main(argv) == 1
    assert _snapshot(bundle) == before, "an unjudged board replaced the committed baseline"


def test_a_publishing_run_writes_the_baseline_of_its_own_run(monkeypatch, tmp_path):
    """No `--verdicts-out` while publishing must still refresh the baseline (#13303 c11, #5)."""
    bundle, argv = _parse_run(monkeypatch, tmp_path, "bundle")
    before = _snapshot(bundle)
    assert cap.main(argv) == 0
    after = _snapshot(bundle)
    assert after["run-local.json"] != before["run-local.json"]
    assert after["verdicts-baseline.json"] != before["verdicts-baseline.json"], (
        "the baseline still describes the previous run"
    )


def test_a_non_publishing_run_cannot_write_the_baseline(monkeypatch, tmp_path):
    """The `not publishing` half of the gate, exercised (#13303 c11, #4)."""
    bundle, argv = _parse_run(
        monkeypatch,
        tmp_path,
        "scratch",
        ["--verdicts-out", str(tmp_path / "bundle" / "verdicts-baseline.json")],
    )
    before = _snapshot(bundle)
    assert cap.main(argv) == 1
    assert _snapshot(bundle) == before


def test_judge_records_that_the_scenario_could_not_be_verified(tmp_path):
    """The `scenario_check` note must exist in the artifact, not only in prose (#13303 c8)."""
    real = json.loads(
        (ROOT / "docs" / "evidence" / "13038-capstone" / "judge-packet.json").read_text()
    )
    bad = dict(real)
    bad["header"] = dict(real["header"], narrative="/nonexistent/elsewhere/narrative.json")
    p = tmp_path / "p.json"
    p.write_text(json.dumps(bad))
    out = tmp_path / "o"
    args = type(
        "A",
        (),
        {
            "packet": str(p),
            "verdicts": str(
                ROOT / "docs" / "evidence" / "13038-capstone" / "verdicts-baseline.json"
            ),
            "judge_cmd": None,
            "out": str(out),
        },
    )()
    assert cap.cmd_judge(args) == 0
    board = json.loads((out / "run-local.json").read_text())
    assert board["header"]["judge"]["scenario_check"] == "unverified"
    assert any("scenario not verified" in n for n in board["header"]["notes"])
    # …and the committed packet records the checked case.
    assert cap._same_questions_as_narrative(real) is True


def test_render_refuses_a_document_that_is_not_a_scoreboard(tmp_path):
    """A verdicts map / run file / packet is not a board (#13303 cycle 8, P7)."""
    for name, payload in (
        ("verdicts.json", {"Q1": {"verdict": "right"}}),
        ("packet.json", {"schema": "capstone-state-value-judge-packet/v1", "questions": []}),
    ):
        p = tmp_path / name
        p.write_text(json.dumps(payload))
        assert cap.cmd_render(type("A", (), {"run": str(p), "write": None})()) == 2
    # …and rendering the committed board INTO the bundle is refused too: a re-render is not
    # a producer, so it must not touch the baseline even when its content is identical.
    assert (
        cap.cmd_render(
            type(
                "A",
                (),
                {
                    "run": str(ROOT / "docs" / "evidence" / "13038-capstone" / "run-local.json"),
                    "write": str(cap.DEFAULT_OUT / "scoreboard-local.md"),
                },
            )()
        )
        == 2
    )
    # The committed board still renders (and to a scratch path, not the bundle).
    out = tmp_path / "r.md"
    assert (
        cap.cmd_render(
            type(
                "A",
                (),
                {
                    "run": str(ROOT / "docs" / "evidence" / "13038-capstone" / "run-local.json"),
                    "write": str(out),
                },
            )()
        )
        == 0
    )
    assert out.read_text().startswith("#")


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
    real = cap._load_verdicts_map(
        ROOT / "docs" / "evidence" / "13038-capstone" / "verdicts-baseline.json"
    )
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


def test_the_reader_is_spawned_without_tools_in_an_empty_sandbox(monkeypatch):
    """The property that makes a later scoreboard meaningful: independence.

    A reader holding tools, sitting in the repo, can re-read `run-{target}.json` /
    `scoreboard-{target}.md` — both still on disk beside the packet when it is spawned — and
    reproduce the PREVIOUS run's verdicts, so a later run could not show that a slice moved
    the product. Pinned hermetically: no model, no real subprocess.
    """
    seen: dict = {}
    orig_run = cap.subprocess.run

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
    # Restoration, PROVED rather than asserted by tautology: the old line compared the
    # module to itself (`real is cap.subprocess` is always True) and read as a
    # restore-check while testing nothing.
    assert cap.subprocess.run is fake_run
    monkeypatch.undo()
    assert cap.subprocess.run is orig_run


def test_the_verdicts_file_write_creates_its_directory(tmp_path):
    """A typo'd `--verdicts-out` used to abort the run AFTER the 150 s reader had run."""
    target = tmp_path / "does" / "not" / "exist" / "verdicts.json"
    cap._write_verdicts(
        target, {"Q1": {"verdict": "right"}}, packet_path=tmp_path / "p.json", mode="fresh", meta={}
    )
    assert target.exists() and json.loads(target.read_text())["Q1"]["verdict"] == "right"


# ── 8. the eighth review's two un-pinned promises ───────────────────────────────


def test_cut_redacts_BEFORE_it_truncates():
    """The order is load-bearing, and was only pinned at a call site (#13303 cycle 8).

    A 210-character password truncated to 160 leaves a 141-character PREFIX that neither
    the `scheme://user:pass@` regex nor a literal replace can match — and truncation eats
    the `@`, so redaction can never run afterwards.
    """
    secret = "J" * 210
    cut = cap._cut(f"docker://alice:{secret}@host:6379/db", 160)
    assert "J" * 20 not in cut, "a truncated prefix of the secret survived: redact AFTER cut"
    assert "<redacted>@" in cut
    assert len(cut) <= 160


def test_cut_does_not_rewrite_PRODUCT_text_or_the_readers_prose():
    """`_cut` now redacts, but the judge packet must carry the product's words verbatim."""
    for text in (
        "password rotation policy was superseded",
        "the goal token: quarterly-review was recorded",
    ):
        assert cap._cut(text, 400, redact=False) == text, (
            "the credential patterns matched ordinary product prose — the reader would be "
            "scoring a payload the instrument had already altered"
        )
    # …while credential-bearing text keeps the default
    assert "hunter2" not in cap._cut("docker://:hunter2@host/db", 160)


def test_the_scenario_check_reaches_the_artifact(tmp_path):
    """A comment promised an unverifiable packet 'is judged with a note'; nothing wrote one.

    Pinned BEHAVIOURALLY through `cmd_judge` — deleting the block must red this.
    """
    packet = {
        "schema": "capstone-state-value-judge-packet/v1",
        "issue": 13038,
        "questions": [
            {
                "id": "Q1",
                "question": "What are we currently committed to?",
                "what_a_wrong_answer_means": "x",
            }
        ],
        "product_answers": [
            {"question": "Q1", "status": "answered", "calls": [], "payload": {}, "signals": []}
        ],
        "rubric": "",
        "header": {
            "target": "local",
            "narrative": "/nope/not-this-repo/narrative.json",
            "narrative_frozen": "2026-11-16",
        },
    }
    p = tmp_path / "packet.json"
    p.write_text(json.dumps(packet))
    v = tmp_path / "verdicts.json"
    v.write_text(json.dumps({"Q1": {"verdict": "right", "because": "x"}}))
    out = tmp_path / "out"
    rc = cap.main(["judge", "--packet", str(p), "--verdicts", str(v), "--out", str(out)])
    assert rc in (0, 1)
    board = json.loads((out / "run-local.json").read_text())
    assert board["header"]["judge"]["scenario_check"] == "unverified", (
        "an unverifiable packet was accepted and the artifact does not say so"
    )
    assert any("scenario" in n.lower() for n in board["header"]["notes"])


def test_the_SERVED_row_keeps_the_products_words_verbatim():
    """`redact=False` at the row projection, pinned through the producer (#13303 cycle 9).

    The credential patterns match ordinary business prose, and this text is what the fresh
    reader scores — so redacting it here would have the instrument judge an answer it had
    already altered. Testing `_cut(..., redact=False)` directly only proves the keyword
    works; it does not prove the projection passes it.
    """
    prose = "password rotation policy was superseded"
    assert cap._served_row({"content": prose})["what"] == prose, (
        "a served product row was redacted on its way into the judge packet"
    )
    assert (
        cap._served_row({"snippet": "the goal token: quarterly-review was recorded"})["what"]
        == "the goal token: quarterly-review was recorded"
    )
    # a credential-bearing EXCEPTION string still goes through the default
    assert "hunter2" not in cap._cut("docker://:hunter2@host/db", 160)


def test_a_bare_USERNAME_in_a_uri_is_stripped():
    """`_redact` needs `user:pass@`, so it leaves `https://alice@host` (#13303 cycle 9).

    `_strip_userinfo(text, url)` scrubs the URL'S OWN credentials literally — which is why
    the url must be passed, and why this cannot be tested with `_redact` alone.
    """
    url = "https://alice@api.example.com/v1"
    assert "alice" not in cap._strip_userinfo(url, url)
    assert "alice" not in cap._strip_userinfo(
        "docker://alice@host:6379/db", "docker://alice@host:6379/db"
    )
    # the scheme-less fragment `urlopen` actually emits — no scheme and no colon, `_redact` blind
    assert "s3cr3t-pw" not in cap._strip_userinfo(
        "nonnumeric port: 's3cr3t-pw@api.example.com'", "https://alice:s3cr3t-pw@api.example.com"
    )
    assert cap._strip_userinfo(url, "https://api.example.com/v1") == url


def test_a_refused_run_still_drops_the_graph_it_ingested(monkeypatch, tmp_path):
    """The refusal returns before `--drop-graph` used to run (#13303 cycle 9, P2)."""
    dropped = []

    class _NoTarget:
        kind = "local"
        uri = "docker://<redacted>@127.0.0.1:6379/x"
        namespace = "fake"

        def __init__(self):
            self.capabilities = set()

        def node_count(self):
            return 0

        def provenance(self):
            return {}

        def drop(self):
            dropped.append(True)
            return "dropped"

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(cap, "DEFAULT_OUT", bundle)
    monkeypatch.setattr(cap, "build_target", lambda *a, **k: _NoTarget())
    rc = cap.main(
        [
            "run",
            "--target",
            "local",
            "--judge",
            "none",
            "--out",
            str(bundle),
            "--drop-graph",
        ]
    )
    assert rc == 1
    assert dropped == [True], "a refused run left its throwaway graph behind"


def test_search_projects_through_the_served_row_helper():
    """`_served_row` must be the LIVE projection, not a helper beside a duplicate copy.

    The previous pin called the helper directly, so restoring the inline copy in `search()`
    and dropping `redact=False` there — the exact defect this instrument must not have —
    left every pin green (#13303 cycle 11).
    """
    # BEHAVIOURAL + AST, not a source substring (#13303 cycle 12): the substring version
    # passed while the inline duplicate for six of seven keys was restored, FALSE-RED-ed a
    # behaviourally identical `list(map(_served_row, rows))`, and reported "redacts product
    # rows inline again" for a re-inline that kept `redact=False` — the opposite of the code.
    # A stub SDK is enough: `search()` touches only `self.sdk` and `self.search_timeout_ms`.
    prose = "password rotation policy was superseded"

    # ALL SEVEN keys, not just `what`: a duplicate that mis-reads `scores.rrf` or drops
    # `supersedes` is invisible if the fixture leaves those fields empty (#13303 cycle 13).
    row = {
        "id": "x",
        "content": prose,
        "point_kind": "fact",
        "status": "live",
        "scores": {"rrf": 0.5},
        "match_source": "fts",
        "supersedes": [{"id": "y"}],
    }

    class _SDK:
        def tortoise_fts_query(self, **_kwargs):
            return [row]

    t = object.__new__(cap.LocalTarget)
    t._sdk = _SDK()
    t.search_timeout_ms = 0
    served = t.search("q").rows
    # The EXPECTED dict is written out, not `_served_row(row)` — comparing the function to
    # itself holds for any implementation, so it pinned none of the seven fields
    # (#13303 cycle 14: dropping `supersedes`, or reading `r.get("score")` instead of
    # `scores.rrf`, left all 75 green).
    assert served == [
        {
            "id": "x",
            "what": prose,
            "kind": "fact",
            "status": "live",
            "score": 0.5,
            "match": "fts",
            "supersedes": ["y"],
        }
    ], "search() served the wrong values for this row"
    assert served[0]["what"] == prose, (
        "the product's words were rewritten on the way into the packet"
    )

    # …and every binding of `served` must go through `_served_row`. A POSITIVE check, not a
    # scan for dict literals: three shapes evaded the negative form (`served, _m = [...]`,
    # `served += [...]`, and a duplicate moved into a helper), and a reformat such as
    # `list(map(_served_row, rows))` must stay green.
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(cap.LocalTarget.search)))

    def _refs(node_ast, name: str, *, store_only: bool = False) -> bool:
        """Does this subtree reference the NAME `name`? Identifiers, never dumps.

        Substring matching was wrong in both directions (#13303 cycle 14): `"served" in
        ast.dump(target)` matched a legitimate `served_overlap = _overlap(query, served)`
        and reported a projection that does not exist, while `"_served_row" in
        ast.dump(value)` accepted a duplicate relocating into `_served_row_legacy` — the
        exact "moved into a helper" shape this check exists to catch.
        """
        return any(
            isinstance(s, ast.Name)
            and s.id == name
            and (not store_only or isinstance(s.ctx, ast.Store))
            for s in ast.walk(node_ast)
        )

    bindings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        if any(_refs(t, "served", store_only=True) for t in targets):
            bindings.append(node)
    assert bindings, "search() no longer binds `served` — this pin has gone stale"
    missing = [ast.unparse(n)[:90] for n in bindings if not _refs(n.value, "_served_row")]
    assert not missing, (
        f"`served` is projected without `_served_row` (a duplicate that can drift): {missing}"
    )

    # A correct BINDING is not enough: bind through the helper for `overlap`/`detail`, then
    # project the rows the `Call` RETURNS inline, and the loop above never looks at it
    # (#13303 cycle 15, measured: 75 green while the served rows came from a duplicate that
    # can drift). Any dict-literal comprehension in this method must go through the helper.
    projections = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.ListComp, ast.GeneratorExp))
        and isinstance(n.elt, ast.Dict)
        and any(
            isinstance(k, ast.Constant) and k.value in ("supersedes", "what") for k in n.elt.keys
        )
    ]
    inline = [ast.unparse(n)[:90] for n in projections if not _refs(n, "_served_row")]
    assert not inline, f"rows are projected inline instead of through `_served_row`: {inline}"


def test_every_post_ingestion_refusal_drops_the_graph(monkeypatch, tmp_path):
    """All four refusal gates route through ONE path, so `--drop-graph` cannot be skipped."""
    dropped = []

    class _NoTarget:
        kind = "local"
        uri = "docker://<redacted>@127.0.0.1:6379/x"
        namespace = "fake"

        def __init__(self):
            self.capabilities = set()

        def node_count(self):
            return 0

        def provenance(self):
            return {}

        def drop(self):
            dropped.append(True)
            return "dropped"

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(cap, "DEFAULT_OUT", bundle)
    monkeypatch.setattr(cap, "build_target", lambda *a, **k: _NoTarget())

    # `--judge file` with a valid map is REQUIRED to reach the gate: `verdicts_target` is
    # only computed when `judged` is truthy, so `--judge none` made this pin VACUOUS — it
    # ran a SUCCESSFUL run (exit 1 from `exit_code`, graph dropped on the success path) and
    # called that coverage (#13303 cycle 12).
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"Q1": {"verdict": "right"}}))
    scratch = tmp_path / "scratch"
    rc = cap.main(
        [
            "run",
            "--target",
            "local",
            "--judge",
            "file",
            "--verdicts",
            str(verdicts),
            "--out",
            str(scratch),
            "--verdicts-out",
            str(tmp_path),  # a DIRECTORY: the sibling gate
            "--drop-graph",
        ]
    )
    assert rc == 1, "the --verdicts-out directory gate did not fire"
    assert dropped == [True], (
        "a run refused at the --verdicts-out gate left its throwaway graph behind"
    )
    # kind-independent: `run-local.json` is `f"run-{target.kind}.json"`, so keying the
    # assert to `kind` let a two-line edit restore this pin's vacuity (#13303 cycle 13).
    assert not scratch.exists() or not list(scratch.iterdir()), (
        "the refusal happened AFTER artifacts were written: "
        f"{sorted(p.name for p in scratch.iterdir())}"
    )


# ── #13302: the two questions that were unaskable now have a door ───────────────


class _EndedRecordingTarget:
    """A target that records the calls a recipe makes and serves the ended shape.

    A stub, not a graph: what is pinned here is that the RECIPE asks the question
    the ended-scope read answers, and that its answer reaches the payload — the read
    itself is pinned against a real store in `tests/test_13302_ended_read.py`.
    """

    capability = "ended"

    def __init__(self):
        self.calls = []

    def search(self, query=None, *, entity_type="point", limit=10,
               include_terminal=False, ended_scope=False, role=""):
        self.calls.append({"query": query, "entity_type": entity_type,
                           "ended_scope": ended_scope, "role": role})
        rows = []
        if ended_scope:
            rows = [{"id": "dead-1", "what": "withdrawn thing", "kind": "statement",
                     "status": "retracted"}] if entity_type == "point" else [
                {"id": "plan-1", "what": "cancelled plan", "kind": "core:plan",
                 "stage": {"id": "cancelled", "slot": "terminal",
                           "outcome": "withdrawn"},
                 "linkedEvents": []}]
        return cap.Call("search", {"query": query, "entity_type": entity_type,
                                   "ended_scope": ended_scope},
                        True, f"{len(rows)} rows", {}, rows, role=role)

    def read_entity(self, entity_id, *, role=""):
        return cap.Call("read_entity", {"id": entity_id}, True, "one node", {},
                        [{"id": entity_id, "status": "retracted"}], role=role)


def test_q5_and_q6_ask_the_read_whose_question_they_are():
    """Both questions must reach the ENDED-SCOPE read, with no query and no id.

    Without these calls the instrument keeps measuring a surface that cannot answer
    them at all, and a `wrong` row would blame the product for a question nobody
    asked it (#13302).
    """
    for recipe in ("planned_never_happened", "withdrawn"):
        target = _EndedRecordingTarget()
        cap.RECIPES[recipe](target, {}, {})
        ended = [c for c in target.calls if c["ended_scope"]]
        assert ended, f"{recipe} never asks the ended-scope read"
        assert all(c["query"] is None for c in ended), (
            "an ended-scope read takes no query (a state question is not a text match)"
        )
        assert any(c["role"] == "ended" for c in ended), (
            "the call needs a role, or `_require_call` cannot block the question when "
            "the read fails and a missing call would be scored as a product defect"
        )
        assert "ended_scope" in cap.RECIPE_NEEDS[recipe]


def test_the_ended_rows_reach_the_payload_a_reader_scores():
    """The reader must SEE how it ended — the payload, not the call list, is scored."""
    for recipe, key in (("planned_never_happened", "ended_objects"),
                        ("withdrawn", "ended_points")):
        answer = cap.RECIPES[recipe](_EndedRecordingTarget(), {}, {})
        rows = answer.payload[key]
        assert rows, f"{recipe} drops the ended rows from its payload"
        if recipe == "withdrawn":
            assert rows[0]["status"] == "retracted"
        else:
            assert rows[0]["stage"]["slot"] == "terminal"
            assert rows[0]["linkedEvents"] == []


def test_an_empty_linked_events_list_survives_the_served_projection():
    """`linkedEvents` is keyed on PRESENCE, not truthiness — and that is pinned here.

    #13302 review F3.3: the payload case above reads the STUB target's rows, which
    bypass `_served_row`/`_ended_fields` entirely, so `if r.get("linkedEvents")`
    (dropping the empty list) left everything green. An empty list is the product's
    own statement that nothing is linked — half of "planned and never happened" —
    so it must reach a reader, and a current-scope row (which carries no ending at
    all) must stay byte-identical.
    """
    served = cap._served_row({"id": "plan-1", "name": "cancelled call",
                              "linkedEvents": []})
    assert "linkedEvents" in served, (
        "an EMPTY linkedEvents is evidence, not absence: dropping it as falsy "
        "deletes the 'nothing happened' half of the plan answer"
    )
    assert served["linkedEvents"] == []
    # The pre-#13302 shape: a current-scope row carries no ending, so the ended
    # half contributes nothing at all.
    assert "linkedEvents" not in cap._served_row({"id": "live-1", "name": "live"})


def test_a_query_less_call_is_not_reported_as_a_corpus_ranking():
    """`NO-RELATION-TO-QUESTION` describes a failed TEXT match; an enumeration is not one.

    The signal is read by a fresh judge as evidence of a degraded read. Firing it on a
    call that took no query would point a reader at a corpus ranking that never
    happened — a false signal in the one field a failure is attributed to.
    """
    ans = cap.Answer("Q6")
    ans.calls.append(
        cap.Call("search", {"query": None, "ended_scope": True}, True,
                 rows=[{"id": "p1", "what": "the beach club confirmed our booking"}],
                 overlap={"rows": 1, "zero_overlap_rows": 1})
    )
    cap._signal_refs(ans, {}, {})
    assert not any("NO-RELATION-TO-QUESTION" in s for s in ans.signals)


# ── #13302 review F3: the SCOREBOARD's settled renderings ──────────────────────
#
# The board is the artifact a human reads to decide whether the product answered,
# and the pre-fix inline renderings made an ended-scope enumeration read exactly
# like a plain default-scope search. The diff replaced them with one builder per
# rendering; nothing pinned that, so restoring the inline copies kept every pin
# green and left a human to notice (#13302 review F3).


def test_an_ended_scope_call_is_labelled_as_one():
    """A reader must never have to guess WHICH call produced a row.

    Both renderers spell a call through `_search_label`, so the marker has to live
    in that one builder. Without it the committed evidence shows a retired object
    served `status=deprecated` by a lookup that looks like a plain read — the trust
    failure the scoreboard exists to catch.
    """
    label = cap._search_label(
        {"query": None, "entity_type": "point", "ended_scope": True})
    plain = cap._search_label({"query": None, "entity_type": "point"})

    assert "ended_scope" in label, (
        "an ended-scope enumeration must be LABELLED as one — it renders "
        "identically to a query-less default search otherwise"
    )
    assert "ended_scope" not in plain, (
        "a default-scope read must not claim a scope it did not ask for"
    )
    # The detail sections pass `tick=False`; the FLAGS may not depend on that, or
    # the same call renders two ways in one document.
    assert label == plain.replace(")", ", ended_scope)")
    assert "ended_scope" in cap._search_label(
        {"query": None, "entity_type": "point", "ended_scope": True}, tick=False)


def test_every_search_label_comes_from_the_one_builder():
    """A re-inlined `f"search(…)"` in a renderer is the defect, so scan for it.

    The behavioural pin above cannot see an inline duplicate: it calls the builder
    directly, which stays correct while a renderer stops using it. This is the
    AST shape of the same check `test_search_projects_through_the_served_row_helper`
    applies to the served-row projection.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(cap)))
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name == "_search_label":  # the ONE builder, by definition
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.JoinedStr):
                continue
            head = node.values[0].value if node.values else None
            if isinstance(head, ast.Constant) and str(head.value).lstrip().startswith(
                "search("
            ):
                offenders.append(f"{fn.name}:{node.lineno}")
    assert not offenders, (
        "a `search(...)` label is built inline instead of through "
        f"`_search_label` (it can drop `ended_scope`): {offenders}"
    )


def test_the_ended_suffix_renders_how_a_row_ended():
    """The bracket is the reason a row is the answer — and it is not decorative."""
    suffix = cap._ended_suffix(
        {"stage": {"slot": "terminal", "outcome": "withdrawn"}, "linkedEvents": []})
    assert "stage=terminal/withdrawn" in suffix
    assert "no linked event" in suffix, (
        "the 'planned and never happened' half must render, or a plan that ended "
        "and a plan that happened look identical on the board"
    )
    assert "successor=Onboarding v2" in cap._ended_suffix(
        {"successorName": "Onboarding v2"}), "a NAME must render under the name"
    assert "successor=abc-123" in cap._ended_suffix({"successorId": "abc-123"})
    assert cap._ended_suffix({"id": "live-1", "status": "live"}) == "", (
        "a current-scope row carries no ending, so it gains no bracket"
    )


def test_the_ended_signal_states_the_rows_own_ending_and_mints_none():
    """The signal is the ONE field a reader attributes a failure to.

    `withdrawn record(s)` is a claim the product never made — the ended population
    is EVERY ending — and a served row's own `status`/`stage.outcome` is where
    "how" is stated. Both halves are pinned: the wording, and that the word comes
    from the row rather than from the label.
    """
    answer = cap.RECIPES["withdrawn"](_EndedRecordingTarget(), {}, {})
    signal = next(s for s in answer.signals if "ended record(s)" in s)
    assert "retracted" in signal, (
        "the row's OWN ending word must reach the signal — it is the fact the "
        "reader scores"
    )
    assert "withdrawn record" not in signal, (
        "`withdrawn record(s)` would be a claim the product never made: the ended "
        "population is every ending, not only withdrawals"
    )

    class _SupersededTarget(_EndedRecordingTarget):
        def search(self, query=None, *, entity_type="point", limit=10,
                   include_terminal=False, ended_scope=False, role=""):
            call = super().search(query, entity_type=entity_type, limit=limit,
                                  include_terminal=include_terminal,
                                  ended_scope=ended_scope, role=role)
            if ended_scope and entity_type == "point":
                call.rows = [dict(call.rows[0], status="superseded")]
            return call

    signal2 = next(
        s for s in cap.RECIPES["withdrawn"](_SupersededTarget(), {}, {}).signals
        if "ended record(s)" in s
    )
    assert "superseded" in signal2, (
        "a superseded row must state `superseded`, not inherit the label's word"
    )
