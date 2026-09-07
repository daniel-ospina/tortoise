"""W2-b grading + judge semantics (issue #2098) — pure, hermetic.

Unit/contract layer over ``grading.py`` + ``judge.py``: the pinned metric
semantics (macro/strict/leakage/quote-fidelity/provenance/sessions-emit),
the point-level survival rule incl. REPHRASE-linked acceptance and the
turn-echo exclusion (the transcript is never graded as memory), the verbatim
control lane (=1.0 by construction), and the BLIND salience judge protocol
(the judge's prompt never contains a verbatim anchor — asserted, not hoped).

No DB, no network, no LLM (test-design #2093 S4 contract): all fixtures are
synthetic session snapshots.
"""
from __future__ import annotations

import pytest

from tests.eval.write_path import corpus, grading, judge, schema

# ── Synthetic gold builder ─────────────────────────────────────────────────


def _unit(uid: str, anchor: str, *, rephrase: bool = True,
          provenance: bool = True, ep: bool = True) -> dict:
    return {
        "id": uid,
        "survival": {
            "via_anchor": anchor,
            "accepts_rephrase_linked": rephrase,
            "provenance_required": provenance,
            "ep_update_required": ep,
        },
    }


def gold_with(units, distractors=(), hazards=()) -> dict:
    gold = {
        "schema_version": 1,
        "session_id": "synthetic",
        "scenario": "test",
        "planted_units": [
            {"id": u["id"], "kind": "fact", "verbatim_anchor": u["survival"]["via_anchor"],
             "notability": "high", "depth_bucket": "early", "planted_turn": 1}
            for u in units
        ],
        "distractors": list(distractors),
        "attribution_hazards": list(hazards),
        "salient_units": units,
        "distractor_leakage_tolerance": 1,
    }
    return gold


def _point(pid: str, content: str, *, prov: bool = True, ep: bool = True) -> dict:
    return {"point_id": pid, "content": content,
            "provenance_present": prov, "ep_updated": ep}


CONV = [
    {"role": "user", "content": "The deploy shipped a config that split query traffic "
     "unevenly across the search shards and one shard saturated — the root cause."},
    {"role": "assistant", "content": "So we should make each batch claim a single-owner "
     "lease within the ingest queue."},
]


# ── Macro survival ─────────────────────────────────────────────────────────


def test_macro_anchor_hit_survives():
    gold = gold_with([_unit("u1", "one shard saturated")])
    points = [_point("p1", "The deploy split traffic and one shard saturated, root cause.")]
    counts = grading.macro_survival_counts(gold, points)
    assert counts == {"survived": 1, "total": 1,
                      "survived_ids": ["u1"], "hit_point_ids": ["p1"]}


def test_macro_missing_anchor_fails():
    gold = gold_with([_unit("u1", "the October 15 freeze shipped")])
    points = [_point("p1", "one shard saturated the queue")]
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 0 and counts["total"] == 1


def test_turn_echo_never_grades_as_memory():
    """The transcript echo is excluded even when it carries the anchor."""
    gold = gold_with([_unit("u1", "one shard saturated")])
    echo = [_point("t0", "[user] The deploy split traffic and one shard saturated."),
            _point("t1", "[assistant] single-owner lease")]
    counts = grading.macro_survival_counts(gold, echo)
    assert counts["survived"] == 0
    detail = grading.unit_level_detail(gold, echo)
    assert detail["u1"]["failure"] == "content_missing"


def test_rephrase_link_counts_when_neighbor_carries_anchor():
    """A paraphrase point deduped onto the verbatim original via REPHRASE
    counts for units that accept the link (dedup-without-deletion)."""
    gold = gold_with([_unit("u1", "one shard saturated", rephrase=True)])
    # p2 is a paraphrase; p1 (verbatim) carries the anchor; edge p2→p1.
    points = [
        _point("p1", "split traffic so one shard saturated", prov=False, ep=False),
        _point("p2", "the query load imbalance overwhelmed a single shard"),
    ]
    counts = grading.macro_survival_counts(gold, points, [("p2", "p1")])
    assert counts["survived"] == 1
    # p1 (verbatim anchor) and p2 (deduped paraphrase, linked to p1) both hit.
    assert counts["hit_point_ids"] == ["p1", "p2"]


def test_rephrase_link_alone_never_rubber_stamps():
    gold = gold_with([_unit("u1", "the October 15 freeze shipped", rephrase=True)])
    points = [_point("p1", "unrelated content"), _point("p2", "more unrelated")]
    counts = grading.macro_survival_counts(gold, points, [("p2", "p1")])
    assert counts["survived"] == 0


def test_rephrase_ignored_for_non_accepting_unit():
    gold = gold_with([_unit("u1", "one shard saturated", rephrase=False)])
    points = [
        _point("p1", "split traffic so one shard saturated", prov=False, ep=False),
        _point("p2", "the query load imbalance overwhelmed a single shard"),
    ]
    counts = grading.macro_survival_counts(gold, points, [("p2", "p1")])
    assert counts["survived"] == 1  # verbatim hit only (p1), not the link
    assert counts["hit_point_ids"] == ["p1"]


# ── #2405 paraphrase-survival leg (measurement consistency) ───────────────


def test_paraphrase_survivor_flagged_counts():
    """A FLAGGED unit whose anchor is NOT verbatim in any point but whose
    content token-covers the anchor at the product dedup band (>= 0.45)
    survives — the extractor distills; paraphrase is not content_missing."""
    gold = gold_with([_unit("u1", "search shard saturated")])
    # Verbatim absent; content tokens {lone, search, shard, flooded} cover
    # 2/3 of the anchor's content tokens {search, shard, saturated}.
    points = [_point("p1", "the lone search shard flooded over")]
    assert not schema.anchor_present("search shard saturated", "the lone search "
                                     "shard flooded over")
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 1
    assert counts["hit_point_ids"] == ["p1"]


def test_paraphrase_below_band_does_not_survive():
    """Below the 0.45 band = no claim retention — precision guard."""
    gold = gold_with([_unit("u1", "search shard saturated")])
    points = [_point("p1", "we discussed scaling the cluster instead")]
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 0


def test_paraphrase_ignored_for_non_flagged_unit():
    """Units the corpus marks verbatim-only (dates/numbers/names/mechanics)
    do NOT get the paraphrase leg — a paraphrase stays content_missing even
    at high overlap (the extractor paraphrasing 'October 15 freeze' must
    still be a miss: fidelity-critical units need near-verbatim retention)."""
    gold = gold_with([_unit("u1", "search shard saturated", rephrase=False)])
    points = [_point("p1", "the lone search shard flooded over")]
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 0


def test_paraphrase_leg_excludes_turn_echo():
    """The echo is never graded as memory — even when it paraphrase-covers
    the anchor at the band."""
    gold = gold_with([_unit("u1", "search shard saturated")])
    echo = [_point("t0", "[user] the lone search shard flooded over")]
    counts = grading.macro_survival_counts(gold, echo)
    assert counts["survived"] == 0


def test_verbatim_leg_still_fires_for_flagged_units():
    """Verbatim remains the first (high-precision) leg — unchanged for all
    units, flagged or not."""
    gold = gold_with([_unit("u1", "one shard saturated", rephrase=True)])
    points = [_point("p1", "The deploy split traffic and one shard saturated.")]
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 1 and counts["hit_point_ids"] == ["p1"]




def test_paraphrase_near_band_negative_from_below():
    """A 2/5 = 0.40 coverage (below the 0.45 band) must NOT survive — pins
    the constant from below (a silent drop to <= 0.40 would otherwise pass
    the whole suite)."""
    gold = gold_with([_unit("u1", "single owner lease lock batch")])
    # Point covers exactly {owner, lease} = 2/5 anchor content tokens.
    points = [_point("p1", "the owner holds one active lease today")]
    assert not schema.anchor_present("single owner lease lock batch",
                                     "the owner holds one active lease today")
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 0


def test_paraphrase_negation_contradiction_never_survives():
    """Polarity gate (R1 P1-2): a point asserting the OPPOSITE of a flagged
    anchor must NOT count as retention — 'no lease rows' is not retained by
    'we found lease rows'."""
    gold = gold_with([_unit("u1", "no lease rows at all")])
    points = [_point("p1", "we found lease rows in the table")]
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 0


def test_paraphrase_negation_preserving_paraphrase_survives():
    gold = gold_with([_unit("u1", "no lease rows at all")])
    points = [_point("p1", "found no lease rows anywhere in the registry")]
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 1


def test_survival_stopword_set_pinned():
    """The local stopword snapshot must stay == the M6 set MINUS the
    negators {no, not} (R1 P2-3: the survival band must not silently ride
    M6's calibration knob). Catches drift on either side."""
    from tools.longmem_eval import evidence
    m6 = {t for t in evidence.tokens("a b c")}  # import sanity only
    assert isinstance(m6, set)
    m6_stopwords = set(evidence._STOPWORDS)
    expected = m6_stopwords - {"no", "not"}
    assert frozenset(expected) == grading._SURVIVAL_STOPWORDS
    assert "no" not in grading._SURVIVAL_STOPWORDS
    assert "lease" not in grading._SURVIVAL_STOPWORDS



def test_paraphrase_negation_zero_synonym_recall():
    """R2 P3-1: extended negators keep the same-polarity paraphrase alive
    ('no lease rows' -> 'zero lease rows anywhere')."""
    gold = gold_with([_unit("u1", "no lease rows at all")])
    points = [_point("p1", "found zero lease rows anywhere in the registry")]
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 1


def test_paraphrase_incidental_negator_limit_documented():
    """R2 P2-3 (accepted limit): the membership-anywhere gate cannot catch an
    INCIDENTAL negator elsewhere in the point. Documented precision trade-off
    of the lexical band — pinned here so the limit is explicit, not silent."""
    gold = gold_with([_unit("u1", "no lease rows at all")])
    # 'no' is incidental (modifies 'errors', not the lease-row claim).
    points = [_point("p1", "we found lease rows with no errors at all")]
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 1  # the documented limit, not a regression


def test_paraphrase_mirror_direction_not_gated():
    """R2 P2-3 (accepted limit): a POINT-side negation of a POSITIVE anchor is
    not gated — lexical bands cannot scope negations without false-rejecting
    'we shipped the fix, no rollback needed'."""
    gold = gold_with([_unit("u1", "we found lease rows in the table")])
    points = [_point("p1", "found no lease rows anywhere in the registry")]
    counts = grading.macro_survival_counts(gold, points)
    assert counts["survived"] == 1  # anchor positive -> gate off by design



# ── Strict survival (provenance + EP flags) ────────────────────────────────


def test_strict_requires_provenance_on_the_qualifying_point():
    gold = gold_with([_unit("u1", "one shard saturated", provenance=True, ep=True)])
    points = [_point("p1", "one shard saturated", prov=False, ep=True)]
    assert grading.macro_survival_counts(gold, points)["survived"] == 1
    assert grading.strict_survival_counts(gold, points)["survived"] == 0
    detail = grading.unit_level_detail(gold, points)
    assert detail["u1"]["macro"] is True and detail["u1"]["strict"] is False
    assert detail["u1"]["failure"] == "provenance_missing"


def test_strict_requires_ep_update():
    gold = gold_with([_unit("u1", "one shard saturated", provenance=True, ep=True)])
    points = [_point("p1", "one shard saturated", prov=True, ep=False)]
    assert grading.strict_survival_counts(gold, points)["survived"] == 0
    detail = grading.unit_level_detail(gold, points)
    assert detail["u1"]["failure"] == "ep_update_missing"


def test_strict_flags_respected_when_relaxed():
    gold = gold_with([_unit("u1", "one shard saturated", provenance=False, ep=False)])
    points = [_point("p1", "one shard saturated", prov=False, ep=False)]
    assert grading.strict_survival_counts(gold, points)["survived"] == 1
    assert grading.unit_level_detail(gold, points)["u1"]["failure"] is None


def test_strict_prefers_qualified_point_among_hits():
    """Multiple candidate points: a flag-clean hit qualifies even when an
    earlier hit fails the bar (order-independent strict survival)."""
    gold = gold_with([_unit("u1", "one shard saturated", provenance=True, ep=True)])
    points = [
        _point("p1", "one shard saturated", prov=False, ep=True),
        _point("p2", "and then one shard saturated entirely", prov=True, ep=True),
    ]
    assert grading.strict_survival_counts(gold, points)["survived"] == 1


# ── Distractor leakage ─────────────────────────────────────────────────────


def test_distractor_leakage_counts_own_session_anchors():
    gold = gold_with(
        [_unit("u1", "one shard saturated")],
        distractors=[
            {"id": "d1", "statement": "routine", "anchor": "good Wi-Fi", "planted_turn": 1},
            {"id": "d2", "statement": "routine", "anchor": "catering order", "planted_turn": 2},
        ],
    )
    points = [_point("p1", "the meeting room has good Wi-Fi and snacks"),
              _point("p2", "one shard saturated")]
    leaked = grading.distractor_leakage(gold, points)
    assert leaked == ["d1"]


def test_distractor_leakage_empty_when_clean():
    gold = gold_with([_unit("u1", "one shard saturated")],
                     distractors=[{"id": "d1", "statement": "routine",
                                   "anchor": "good Wi-Fi", "planted_turn": 1}])
    assert grading.distractor_leakage(gold, [_point("p1", "one shard saturated")]) == []


# ── Quote fidelity ─────────────────────────────────────────────────────────


def test_quote_fidelity_grounds_against_own_transcript():
    gold = gold_with([_unit("u1", "one shard saturated")])
    conversation = [
        {"role": "user", "content": 'Alice pushed back: "the October 15 freeze is real".'},
        {"role": "assistant", "content": "then we trim scope"},
    ]
    points = [
        _point("p1", 'memory: user said "the October 15 freeze is real" — ground truth'),
        _point("p2", 'invented quote: "no such meeting happened"'),
    ]
    counts = grading.quote_fidelity_counts(gold, points, conversation)
    assert counts == {"grounded": 1, "total": 2, "no_quoted_spans": False}


def test_quote_fidelity_no_spans_vacuous_with_flag():
    gold = gold_with([_unit("u1", "one shard saturated")])
    conversation = [{"role": "user", "content": "plain prose, no quotes"}]
    points = [_point("p1", "the memory is paraphrased without quoting")]
    counts = grading.quote_fidelity_counts(gold, points, conversation)
    assert counts == {"grounded": 0, "total": 0, "no_quoted_spans": True}


def test_quoted_span_min_length_pin():
    # The 8-char floor keeps "ok"/"no" noise out while catching claims.
    assert grading.quoted_spans('said "ok" then left') == []
    assert grading.quoted_spans('quoted "fine then do it" verbatim') == ["fine then do it"]


# ── Provenance + sessions-emit ─────────────────────────────────────────────


def test_provenance_counts_over_memory_layer_only():
    points = [_point("p1", "one shard saturated", prov=True),
              _point("p2", "[user] echo is not memory", prov=False),
              _point("p3", "unprovenanced memory", prov=False)]
    counts = grading.provenance_counts(points)
    assert counts == {"provenanced": 1, "total": 2}


def test_session_emitted_requires_memory():
    assert grading.session_emitted([_point("p1", "a memory")]) is True
    assert grading.session_emitted([_point("t0", "[user] echo only")]) is False
    assert grading.session_emitted([]) is False


# ── Aggregation ────────────────────────────────────────────────────────────


def test_aggregate_metrics_pooled_math():
    results = [
        {"session_id": "a", "emitted": True, "gold_total_units": 2,
         "macro": {"survived": 2, "total": 2},
         "strict": {"survived": 1, "total": 2},
         "leaked": [], "quotes": {"grounded": 1, "total": 1},
         "provenance": {"provenanced": 2, "total": 2}},
        {"session_id": "b", "emitted": False, "gold_total_units": 1,
         "macro": {"survived": 0, "total": 1},
         "strict": {"survived": 0, "total": 1},
         "leaked": ["d1"], "quotes": {"grounded": 0, "total": 0},
         "provenance": {"provenanced": 0, "total": 0}},
    ]
    metrics = grading.aggregate_metrics(results)
    assert metrics["salient_unit_survival_macro"] == pytest.approx(2 / 3)
    assert metrics["salient_unit_survival_strict"] == pytest.approx(1 / 3)
    assert metrics["distractor_leakage_per_run"] == 1
    assert metrics["sessions_emitting"] == 0.5
    assert metrics["quote_fidelity"] == 1.0  # session b has no spans (vacuous)
    assert metrics["provenance_accuracy"] == 1.0  # pooled: 2/2 (b has none)


# ── Verbatim control lane ──────────────────────────────────────────────────


def test_control_lane_is_100_percent_on_planted_corpus():
    """Soundness self-check: the planted anchors must be recoverable from the
    verbatim transcript — for every committed gold (corpus-level invariant)."""
    for session_id in corpus.session_ids():
        gold = corpus.load_gold(session_id)
        fixture = corpus.load_fixture(session_id)
        counts = judge.control_macro_counts(gold, fixture["conversation"])
        assert counts["survived"] == counts["total"], (
            f"{session_id}: control lane {counts['survived']}/{counts['total']} — "
            "anchor not recoverable from the verbatim transcript (corpus/grader bug)"
        )
        assert counts["total"] > 0


def test_control_lane_points_shape():
    conv = [{"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi"}]
    points = judge.control_lane_points(conv)
    assert points[0]["content"] == "hello world"
    assert points[0]["provenance_present"] is False
    assert points[0]["ep_updated"] is False


# ── Blind salience judge protocol ──────────────────────────────────────────


class _RecordingModel:
    """Fake model: records every prompt and returns canned JSON."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.prompts.append((system, user))
        return self.responses.pop(0) if self.responses else '{"x": "FULL"}'


def _anchors_of(gold: dict) -> list[str]:
    return [u["survival"]["via_anchor"] for u in gold["salient_units"]]


def test_salience_judge_prompt_is_blind_to_anchors():
    """The judge's prompt (system + user) must never contain a verbatim
    anchor — blindness is an asserted prompt property, tested on real corpus
    data with a recording fake model."""
    for session_id in corpus.session_ids():
        gold = corpus.load_gold(session_id)
        memory = [_point(f"p{i}", "a plausible retained memory note").get("content")
                  for i in range(3)]
        probes = {u["id"]: f"paraphrase probe for unit {u['id']}"
                  for u in gold["salient_units"]}
        user = judge.build_salience_prompt(probes, memory)
        anchors = _anchors_of(gold)
        for system in (judge._SALIENCE_SYSTEM,):
            leak = judge.prompt_leaks_anchor(system + "\n" + user, anchors)
            assert leak is None, (
                f"{session_id}: judge prompt leaked anchor {leak!r} — blindness violated"
            )
        assert judge.prompt_leaks_anchor(user, [u["survival"]["via_anchor"] for u in gold["salient_units"]][:1]) is None


def test_paraphrase_stage_may_see_anchor_but_output_is_neutral_surface():
    # The paraphrase stage prompt (gold-seeing) NAMES the anchor — that is
    # the intended boundary; the blind surface starts at the salience stage.
    unit = {"id": "u1", "kind": "decision",
            "verbatim_anchor": "shipped before the October 15 freeze"}
    user = judge.paraphrase_stage_prompt(unit, "decision")
    assert "October 15 freeze" in user


def test_full_lane_judge_pin_names_both_stages():
    assert judge.PARAPHRASE_PROMPT_VERSION in judge.JUDGE_PIN_FULL
    assert judge.SALIENCE_PROMPT_VERSION in judge.JUDGE_PIN_FULL
    assert judge.JUDGE_PIN_MECHANICAL != judge.JUDGE_PIN_FULL


def test_salience_parse_and_fold():
    raw = '{"u1": "FULL", "u2": "PARTIAL", "u3": "ABSENT"}'
    labels = judge.parse_salience(raw, ["u1", "u2", "u3"])
    assert labels == {"u1": "FULL", "u2": "PARTIAL", "u3": "ABSENT"}
    folded = judge.judge_survival(labels)
    assert folded == {"survived": 2, "total": 3}


def test_salience_parse_rejects_missing_or_bad_labels():
    with pytest.raises(judge.JudgeProtocolError):
        judge.parse_salience('{"u1": "FULL"}', ["u1", "u2"])
    with pytest.raises(judge.JudgeProtocolError):
        judge.parse_salience('{"u1": "MAYBE"}', ["u1"])


def test_judge_stage_runs_with_blind_prompts_and_tracks_cost():
    gold = gold_with([_unit("u1", "one shard saturated")])
    responses = [
        '{"paraphrase": "the single query shard became overloaded"}',
        '{"u1": "FULL"}',
    ]
    model = _RecordingModel(responses)
    salience = judge.SalienceJudge(model_factory=lambda _name: model)
    probes = salience.synthesize_probes(gold, {})
    assert probes["u1"] == "the single query shard became overloaded"
    coverage = salience.grade_coverage(probes, ["the single query shard is overloaded"])
    assert coverage == {"u1": "FULL"}
    anchors = _anchors_of(gold)
    # Blindness applies to the SALIENCE stage only — the paraphrase stage
    # prompt legitimately names the anchor (the gold-seeing synthesis step).
    assert len(model.prompts) == 2
    salience_prompt = model.prompts[1][1]
    assert judge.prompt_leaks_anchor(salience_prompt, anchors) is None


# ── Shared semantics with the schema ───────────────────────────────────────


def test_metric_vocabulary_roundtrip():
    """aggregate_metrics emits exactly the canonical 6-metric vocabulary
    (a lane that silently under-reports would shrink the CI compare set)."""
    results = [
        {"session_id": "a", "emitted": True, "gold_total_units": 1,
         "macro": {"survived": 1, "total": 1},
         "strict": {"survived": 1, "total": 1},
         "leaked": [], "quotes": {"grounded": 1, "total": 1},
         "provenance": {"provenanced": 1, "total": 1}},
    ]
    metrics = grading.aggregate_metrics(results)
    assert set(metrics) == schema.METRIC_VALUES
