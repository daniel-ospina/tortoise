"""Tests for `tools/s4_reeemit_tax.py` — the #1789 re-emit-tax instrument.

The instrument's whole value is that its number is REAL (the provider's own
`usage.completion_tokens` on real recorded geometry, one variable changed).
These tests pin the three ways such a number goes wrong without anyone
noticing:

1. **A vacuous A/B** — both arms render the same prompt, so a "delta" of zero
   looks like a finding. Pinned by the dry-run test (the renders must differ,
   the legacy one must carry the re-emit mandate, the delta one must not).
2. **A mis-attributed cost** — the escalation net re-runs a truncated call at
   2× the cap and the escalated call does not replace the base call's bill.
   Pinned by `arm_out_tokens` (sum, not final-call) plus its fallback basis.
3. **A silent zero** — a dead credential/endpoint producing "0 sessions
   measured" and exiting 0, which reads as "measured, no saving". Pinned by
   the no-key refusal and the all-arms-failed exit code.

The model is a fake (`_FakeModel`), so these run offline and cost nothing; the
LIVE measurement is the artifact in `docs/evidence/1789-s4-reeemit-tax/`.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "s4_reeemit_tax", _REPO / "tools" / "s4_reeemit_tax.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["s4_reeemit_tax"] = mod
    spec.loader.exec_module(mod)
    return mod


tool = _load_tool()


# ── fixtures ───────────────────────────────────────────────────────────────

_S2 = {
    "entities": [{"name": "Halcyon archive", "kind": "core:WorkItem",
                  "lifecycle": "created", "note": "stalled"}],
    "events": [{"content": "re-ingest stalled at 62 percent",
                "eventKind": "core:Occurrence"}],
    "points": [{"content": "The re-ingest stalled at 62 percent",
                "pointKind": "statement",
                "quote": "stuck at 62 percent", "source_turn_id": 0,
                "search_keys": ["62 percent", "stalled"]}],
    "operators": [],
}


class _FakeModel:
    """Minimal adapter: returns a canned payload, reports usage, and can be
    told to fail (the 401 arm)."""

    def __init__(self, payload: dict, out_tokens: int = 100,
                 prompt_tokens: int = 1000, fail: str | None = None):
        self.payload = payload
        self.out_tokens = out_tokens
        self.prompt_tokens = prompt_tokens
        self.fail = fail
        self.last_finish_reason = "stop"
        self.last_completion_tokens = out_tokens
        self.last_prompt_tokens = prompt_tokens
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        self.calls.append({"system": system, "user": user,
                           "max_tokens": max_tokens})
        if self.fail:
            raise RuntimeError(self.fail)
        return json.dumps(self.payload)


def _receipt(tmp_path: Path, *, arm: str = "narrative") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    doc = {
        "session_id": "wp01_quarry_debug",
        "turns": 26,
        "session_date": "2026-10-18",
        "stories": {arm: "A real story about a stalled re-ingest."},
        "embed_list": {arm: _S2},
        "s2_stats": {arm: {"completion_tokens": 4582, "prompt_tokens": 9822}},
    }
    (tmp_path / "wp01_quarry_debug.arms.json").write_text(json.dumps(doc))
    return tmp_path


# ── 1. the pins against a vacuous A/B ──────────────────────────────────────


def test_dry_run_proves_the_two_arms_render_different_documents(tmp_path):
    """The CI pin: if a future edit made both templates identical, every
    measured reduction would be a lie of zero-variance. The dry run is
    offline, so this check runs on every PR at no cost."""
    out = tmp_path / "dry.json"
    rc = tool.main(["dry-run", "--receipt", str(_receipt(tmp_path / "r")),
                    "--out", str(out)])
    assert rc == 0
    row = json.loads(out.read_text())["rows"][0]
    assert row["renders_differ"] is True
    # legacy mandates the complete-list re-emit; the delta template must not
    assert row["legacy_has_reexmit_mandate"] is True
    assert row["delta_has_reexmit_mandate"] is False
    assert row["delta_has_delta_mandate"] is True
    # the input-side cost of the switch is small and POSITIVE (the delta
    # template is longer) — the savings are on the output side, and a reader
    # must be able to see that the prompt did not get cheaper
    assert row["delta_prompt_chars"] > row["legacy_prompt_chars"]
    assert row["delta_minus_legacy_pct"] < 10.0


# ── 2. cost attribution ────────────────────────────────────────────────────


def test_arm_out_tokens_counts_the_truncated_base_call_too():
    """The escalation net's retry does not replace the base call's bill: with no
    cost accumulator, the arm's cost is healthy + truncation, never the final
    call alone."""
    stats = {"recovery": {"s4_out_tokens": 12000,
                          "truncation_completion_tokens_s4": 16000}}
    tokens, basis = tool.arm_out_tokens(stats)
    assert tokens == 28000
    assert basis.startswith("recovery.")


def test_a_tokenless_response_is_unmeasured_never_zero():
    """A provider whose usage is absent (or normalised to 0) must NOT produce a
    "this arm emitted nothing" 0: `_reduction(1000, 0)` is a 100 % saving — the
    most flattering number available, and the one the ticket wants to see."""
    tokens, basis = tool.arm_out_tokens({})
    assert tokens is None
    assert "unmeasured" in basis
    s = tool.compare([_pair("a", 1000, tokens)])
    assert s["sessions_measured"] == 0
    assert s["token_weighted_reduction"] is None


def test_the_compliance_flags_are_derived_from_this_arm(tmp_path):
    """`run_s4` never writes s4_delta_empty/s4_delta_reemit_suspect (they are
    extract_session_v2-stage keys), so they are DERIVED here — an all-empty
    delta must read empty, not "False"."""
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    empty = tool.run_arm(_FakeModel({"entities": [], "events": [], "points": [],
                                     "operators": []}), sess, "delta")
    assert empty["s4_items"] == 0
    assert empty["s4_delta_empty"] is True
    assert empty["s4_delta_reemit_suspect"] is False
    # a verbatim re-emitter of the whole S2 base is flagged
    reemit = tool.run_arm(_FakeModel(_S2), sess, "delta")
    assert reemit["verbatim_reemissions"] == 3
    assert reemit["s4_delta_reemit_suspect"] is True


def test_composition_and_tokens_cover_the_same_population():
    """One population: a failed pair is excluded from BOTH the token totals and
    the composition table, never from only one of them."""
    pairs = [_pair("bad", 1000, None, delta={"error": "401"}),
             _pair("ok", 2000, 200, delta={"added_by_s4": 4})]
    s = tool.compare(pairs)
    assert s["composition"]["delta"]["added_by_s4"] == 4
    assert s["composition"]["legacy"]["s2_items"] == 10      # one pair only


def test_arm_out_tokens_prefers_the_per_call_cost_total():
    """THE under-count this instrument must never have: on a truncated base call
    + an escalated retry (or a parse-retry re-prompt) the recovery keys hold
    only PART of the bill — 16 000 (base) and NOTHING for a partial-accept
    retry — while `stats["cost"]["completion_tokens"]` sums every successful
    call (46 000 here). Reading recovery first would shrink the arm and report
    it with a clean-looking basis string."""
    stats = {
        "cost": {"completion_tokens": 46000},
        "recovery": {"s4_out_tokens": 0,
                     "truncation_completion_tokens_s4": 16000},
        "completion_tokens": 30000,           # the LAST call only
    }
    assert tool.arm_out_tokens(stats) == (46000, "stats.cost.completion_tokens")


def test_arm_out_tokens_falls_back_to_recovery_when_cost_is_absent():
    """No cost accumulator (a fake model, a provider with no usage): the
    recovery sum is complete on the plain truncation path, and the basis string
    says which quantity was used."""
    stats = {"recovery": {"s4_out_tokens": 12000,
                          "truncation_completion_tokens_s4": 16000}}
    assert tool.arm_out_tokens(stats) == (
        28000, "recovery.s4_out_tokens+truncation")


def test_a_rejected_token_count_makes_the_arm_unmeasured():
    """`_accumulate_call_cost` and `_complete` raise THREE disjoint counters for
    a billed call whose completion count is not in `cost["completion_tokens"]`:
    `calls_without_tokens` (usage present, count rejected), `calls_without_usage`
    (no usage block at all — a different branch, the counter one to the left),
    and `deadline_aborts` (killed by the wall clock, counted nowhere). Reading
    only the first was the bug this pins shut for all three."""
    for stats, needle in (
        ({"cost": {"calls": 2, "completion_tokens": 5000,
                   "calls_without_tokens": 1}}, "1 of 2 call(s)"),
        ({"cost": {"calls": 2, "completion_tokens": 5000,
                   "calls_without_usage": 1}}, "1 of 2 call(s)"),
        ({"cost": {"calls": 2, "completion_tokens": 5000},
          "deadline_aborts": 1}, "1 of 2 call(s)"),
    ):
        tokens, basis = tool.arm_out_tokens(stats)
        assert tokens is None
        assert needle in basis


def test_s4_delta_empty_uses_productions_own_predicate(tmp_path):
    """`_s4_section_is_content` over EVERY emitted section, not the 4 tax
    sections: a RETRACTION-ONLY delta carries content the merge keeps, so
    production does not call it empty and neither may the instrument."""
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    retraction_only = tool.run_arm(
        _FakeModel({"entities": [], "events": [], "points": [], "operators": [],
                    "retractions": [{"content": "the 62% claim is withdrawn"}]}),
        sess, "delta")
    assert retraction_only["s4_items"] == 0        # nothing on the tax surface
    assert retraction_only["s4_delta_empty"] is False   # but the delta had content


def test_a_MALFORMED_delta_is_not_productions_no_changes_response(tmp_path):
    """⛔ Production's `s4_delta_empty` has TWO conjuncts, and an earlier revision
    of the instrument tested ONE.

    `extract_session_v2`:
        if any(_s4_section_is_content(k, v) …):  merge
        elif not s4_shape_bad:                   s4_delta_empty = 1

    So a section bearing only MALFORMED items — `{"entities": [{}]}` — is not
    "the correct no-changes response": production records `s4_shape_error` and
    does not merge. Reading the content predicate alone published that arm as
    COMPLIANT, which is the flattering direction on the only compliance signal
    the evidence document leans on (#11385 review P2).
    """
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    for payload in ({"entities": [{}], "events": [], "points": [],
                     "operators": []},
                    {"entities": [42], "events": [], "points": [],
                     "operators": []}):
        row = tool.run_arm(_FakeModel(payload), sess, "delta")
        assert tool.x._s4_malformed_sections(payload) == ["entities"], payload
        assert row["s4_delta_empty"] is False, payload
    # …and the real no-changes response (all four lists empty) IS "empty"
    row = tool.run_arm(_FakeModel({"entities": [], "events": [], "points": [],
                                   "operators": []}), sess, "delta")
    assert row["s4_delta_empty"] is True


def test_both_arms_get_identical_inputs(tmp_path):
    """R1, the load-bearing property: the SAME story / S2 list / search block /
    cap reach both arms. If the delta arm silently got a different search block
    the "tax" would double and every number in the report would be meaningless.

    ⛔ The SEARCH BLOCK is asserted by identity, not by "some shared string is
    present". An earlier revision checked three substrings of the story and the
    S2 embed list — all of which ride both prompts whatever happens to the search
    block — so swapping the delta arm's search block for `{}` (the exact vacuous
    A/B this file's module docstring names as failure mode #1) passed green
    (#11385 review P2).
    """
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    model = _FakeModel({"entities": [], "events": [], "points": [],
                        "operators": []})
    # ⛔ THE CALL SITE, not just its rendered consequence. The README claims
    # `run_s4` threads `delta` and nothing else, and cites THIS test for it —
    # but everything below is satisfied by a call that also passes a second
    # arm-dependent keyword: it would only make `system` differ MORE. Measured:
    # adding `core_only=(arm == "delta")` at the instrument's call left the
    # whole suite green (#11385 review P2). So the kwargs themselves are
    # captured and compared.
    _seen: list[dict] = []
    _real = tool.x.run_s4

    def _spy(*a, **kw):
        _seen.append(dict(kw))
        return _real(*a, **kw)

    tool.x.run_s4 = _spy
    try:
        tool.run_arm(model, sess, "legacy")
        tool.run_arm(model, sess, "delta")
    finally:
        tool.x.run_s4 = _real
    assert len(_seen) == 2, _seen
    _legacy_kw, _delta_kw = _seen
    assert set(_legacy_kw) == set(_delta_kw), (_legacy_kw, _delta_kw)
    assert {k: v for k, v in _legacy_kw.items() if k != "delta"} == {
        k: v for k, v in _delta_kw.items() if k != "delta"}, (
            "`delta` must be the ONLY arm-dependent argument", _legacy_kw, _delta_kw)
    assert _legacy_kw["delta"] is False and _delta_kw["delta"] is True
    assert len(model.calls) == 2
    legacy, delta = model.calls
    # the TEMPLATE lives in `system` (the user message is the same constant)
    assert legacy["system"] != delta["system"]     # the contracts differ …
    assert legacy["max_tokens"] == delta["max_tokens"]
    # … and the story + S2 list + search block ride BOTH system prompts
    for shared in (sess["story"], '"Halcyon archive"', '"stalled"'):
        assert shared in legacy["system"] and shared in delta["system"]
    # the search block itself: every line of the block the arms were handed must
    # be in BOTH renders, and the block must be non-empty (a `{}` search block
    # renders as the degraded-search condition, which changes both arms)
    import tortoise.extractor_v2 as ev2
    rendered = ev2._render_search_results(tool.search_from_s2(sess["s2"]))
    lines = [ln.strip() for ln in rendered.splitlines() if ln.strip()]
    assert len(lines) > 3, "the reconstruction must carry real content"
    for ln in lines:
        assert ln in legacy["system"], ln
        assert ln in delta["system"], ln


def test_the_gap_items_ride_the_artifact(tmp_path):
    """The counts alone cannot support the follow-up the report points at
    (Task 10 Step 5 partitions the CORRECTIONS into content-changing /
    kind-lifecycle-only / re-emission-noise) — that needs the items, and each
    capped list carries the TRUE count so `len()` is never mistaken for it."""
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    gap = {"entities": [{"name": "Relay queue", "kind": "core:WorkItem"}],
           "events": [], "points": [], "operators": []}
    row = tool.run_arm(_FakeModel(gap), sess, "delta")
    assert row["added_by_s4"] == 1
    assert row["added_items"] == {
        "count": 1, "candidates": 1, "recorded": 1, "cap": tool._ITEM_CAP,
        "cap_hit": False, "cap_dropped": 0, "unclassified": 0,
        "items": [{"section": "entities", "key": "enti:entity | relay queue | workitem",
                   "content": "Relay queue", "kind": "core:WorkItem",
                   "lifecycle": None, "supersedes": None}]}


def test_the_cap_and_the_mask_deficit_are_separable():
    """The two ways a list can come up short must not be conflated: a CAP drop
    (the corpus exceeded the cap) and a MASK deficit (the census counts with a
    different basis). Deriving both from the census count reports a cap hit that
    never happened — the exact defect the earlier version shipped."""
    capped = tool._capped(([{"i": n} for n in range(40)], 45), 45)
    assert capped["cap_hit"] is True and capped["cap_dropped"] == 5
    assert capped["unclassified"] == 0
    mask = tool._capped(([{"i": n} for n in range(3)], 3), 45)
    assert mask["cap_hit"] is False and mask["cap_dropped"] == 0
    assert mask["unclassified"] == 42
    assert mask["count"] == 45 and mask["recorded"] == 3


def test_the_CORRECTION_pairs_ride_the_artifact(tmp_path):
    """⚠️ The defect this pins: a correction REUSES the S2 merge key (points and
    events merge on CONTENT), so any "items not in S2" filter drops the entire
    corrections channel. The artifact must carry BOTH sides of the pair so Task
    10 Step 5 can classify it offline. Composed here: a lifecycle-only change
    (a pair) and a pure verbatim re-emit (the tax — which must NOT be a pair).
    A CONTENT change is deliberately not composed: for points/events content IS
    the merge key, so it can never be a correction pair — it lands in
    `added_items` as a new identity, which is exactly the ambiguity the evidence
    document flags rather than hides."""
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    # same key, changed lifecycle -> a CORRECTION
    life = {"entities": [{"name": "Halcyon archive", "kind": "core:WorkItem",
                          "lifecycle": "superseded", "note": "stalled"}],
            "events": [], "points": [], "operators": []}
    row = tool.run_arm(_FakeModel(life), sess, "delta")
    assert row["added_by_s4"] == 0            # nothing new appended
    assert row["corrected_by_s4"] == 1
    assert row["verbatim_reemissions"] == 0   # not a pure re-emit
    (corr,) = row["corrected_items"]["items"]
    assert corr["section"] == "entities"
    assert corr["changed_fields"] == ["lifecycle"]
    assert corr["s2_before"] == {"lifecycle": "created"}
    assert corr["s4_after"] == {"lifecycle": "superseded"}
    # a PURE verbatim re-emit is the tax, and must NOT appear as a correction
    verbatim = tool.run_arm(_FakeModel({"entities": [
        {"name": "Halcyon archive", "kind": "core:WorkItem",
         "lifecycle": "created", "note": "stalled"}]}), sess, "delta")
    assert verbatim["verbatim_reemissions"] == 1
    assert verbatim["corrected_items"]["items"] == []


def test_an_incomplete_recovery_sum_is_unmeasured_not_partial():
    """A truncated base call + an escalated PARTIAL ACCEPT on an adapter with no
    cost accumulator: the escalated call never reaches `s4_out_tokens`, so the
    recovery sum is the base call alone (16 000 of 46 000 emitted). Reporting it
    as the total would under-count in the direction that flatters the delta arm."""
    stats = {"recovery": {"s4_out_tokens": 0,
                          "truncation_completion_tokens_s4": 16000},
             "completion_tokens": 30000}     # a call the accumulator never saw
    tokens, basis = tool.arm_out_tokens(stats)
    assert tokens is None
    assert "incomplete after an escalated retry" in basis


def test_arm_out_tokens_falls_back_and_says_so():
    """A single-call arm with no cost/recovery keys: the last-call count is the
    total, and the basis says so. Falling back silently to 0 would make the
    EXPENSIVE arm look free."""
    tokens, basis = tool.arm_out_tokens({"completion_tokens": 900})
    assert (tokens, basis) == (900, "stats.completion_tokens")


# ── 3. the comparison arithmetic ───────────────────────────────────────────


def _pair(session, legacy_tokens, delta_tokens, **kw):
    row = {"arm": "x", "out_tokens": 0, "s2_items": 10, "s4_items": 10,
           "merged_items": 10, "added_by_s4": 0, "corrected_by_s4": 0,
           "verbatim_reemissions": 0}
    lg = dict(row, arm="legacy", out_tokens=legacy_tokens,
              **kw.get("legacy", {}))
    dl = dict(row, arm="delta", out_tokens=delta_tokens,
              **kw.get("delta", {}))
    return {"session": session, "legacy": lg, "delta": dl}


def test_compare_is_token_weighted_and_reports_the_spread():
    pairs = [_pair("a", 1000, 100), _pair("b", 3000, 600)]
    s = tool.compare(pairs)
    # weighted by tokens: (4000 - 700) / 4000 — NOT the mean of 0.9 and 0.8
    assert s["sum_legacy_out_tokens"] == 4000
    assert s["sum_delta_out_tokens"] == 700
    assert s["token_weighted_reduction"] == pytest.approx(0.825, abs=1e-4)
    assert s["median_session_reduction"] == pytest.approx(0.85)
    assert s["min_session_reduction"] == pytest.approx(0.8)
    assert s["max_session_reduction"] == pytest.approx(0.9)


def test_a_failed_arm_is_unmeasured_never_a_zero():
    """0.0 would read as 'no saving'; None reads as 'not measured' — the two
    must never be confused in a cost report."""
    bad = _pair("a", 1000, None, delta={"error": "HTTPError: 401"})
    ok = _pair("b", 2000, 200)
    s = tool.compare([bad, ok])
    assert s["sessions_measured"] == 1
    assert s["sessions_attempted"] == 2
    assert s["rows"][0]["reduction"] is None
    assert s["sum_legacy_out_tokens"] == 2000      # the failed pair is excluded
    assert s["token_weighted_reduction"] == pytest.approx(0.9)


def test_compare_sums_the_composition_channels_per_arm():
    """The reduction must not be readable as a pure win: the gap channel
    (`added_by_s4`) and the tax itself (`verbatim_reemissions`) are summed per
    arm so a delta that stopped LOOKING is visible next to its token number."""
    pairs = [_pair("a", 1000, 100,
                   legacy={"verbatim_reemissions": 10, "added_by_s4": 2},
                   delta={"verbatim_reemissions": 0, "added_by_s4": 1})]
    comp = tool.compare(pairs)["composition"]
    assert comp["legacy"]["verbatim_reemissions"] == 10
    assert comp["delta"]["verbatim_reemissions"] == 0
    assert comp["legacy"]["added_by_s4"] == 2
    assert comp["delta"]["added_by_s4"] == 1


# ── 4. the arms themselves ─────────────────────────────────────────────────


def test_run_arm_records_tokens_and_composition(tmp_path):
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    # a compliant delta: emit NOTHING (no gaps, no corrections)
    model = _FakeModel({"entities": [], "events": [], "points": [],
                        "operators": []}, out_tokens=42)
    row = tool.run_arm(model, sess, "delta")
    assert "error" not in row
    assert row["out_tokens"] == 42
    assert row["s2_items"] == 3            # the 4 tax sections, real geometry
    assert row["s4_items"] == 0
    assert row["merged_items"] == 3        # the union PRESERVES the omission
    assert row["verbatim_reemissions"] == 0
    assert row["kept_from_s2"] == 3


def test_run_arm_records_a_failure_instead_of_raising(tmp_path):
    """A failed arm is an observation: dropping it would bias the delta
    toward the arm that does not truncate."""
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    row = tool.run_arm(_FakeModel({}, fail="401 Unauthorized"), sess, "legacy")
    assert row["out_tokens"] is None
    assert "401 Unauthorized" in row["error"]


def test_a_failed_arm_keeps_the_SPEND_it_incurred(tmp_path, monkeypatch):
    """⛔ `_complete` accumulates cost on its SUCCESS return, BEFORE
    `_complete_parsed` parses. So an arm that emitted 16k tokens and then failed
    to parse HAS a populated accumulator — and hard-coding `out_tokens: None` on
    the error path threw that away. The arm it threw away is the expensive one (a
    truncating / unparseable arm), so the discarded spend biased the delta toward
    the arm that never fails, i.e. it flattered the very number this instrument
    exists to publish (#11385 review P3).

    A GENUINELY tokenless failure must still be `None` (never a 0 that reads as
    "emitted nothing") — `test_a_failed_arm_is_unmeasured_never_a_zero`.

    Driven through a stubbed `run_s4` that fills the accumulator the way
    `_complete` does and THEN raises, because a fake model never reaches the real
    `_complete` and so would leave `stats["cost"]` empty — measuring the fake
    instead of the error path.
    """
    def _spends_then_fails(model, *a, **kw):
        kw["stats"].setdefault("cost", {}).update(
            {"calls": 1, "completion_tokens": 16384})
        raise ValueError("no JSON object found in response")

    monkeypatch.setattr(tool.x, "run_s4", _spends_then_fails)
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    row = tool.run_arm(object(), sess, "legacy")

    assert "no JSON object" in row["error"]
    assert row["out_tokens"] == 16384, "the spend is data even when the arm fails"
    assert row["basis"] == "stats.cost.completion_tokens"


def test_prompt_tokens_are_the_ACCUMULATED_total_not_the_last_call(
        tmp_path, monkeypatch):
    """⛔ `stats["prompt_tokens"]` is a PER-CALL snapshot (`_complete` overwrites
    it on every call), while `out_tokens` beside it is an accumulated TOTAL with
    a `basis` string precisely because a final-call read under-counts the
    expensive arms. Reading the snapshot published a last-call number under the
    same name a total carries — invisible while every arm makes one call (both
    committed runs do) and wrong on the FIRST retried arm, which is the shape
    this instrument exists to get right. `_accumulate_call_cost` sums the same
    field into `stats["cost"]["prompt_tokens"]`, so the row must read that
    (#11385 review P3).
    """
    def _two_calls(model, *a, **kw):
        stats = kw["stats"]
        # the accumulator sums BOTH calls; the snapshot keeps only the last one
        stats.setdefault("cost", {}).update(
            {"calls": 2, "completion_tokens": 300, "prompt_tokens": 4000})
        stats.update(prompt_tokens=1500, completion_tokens=200, attempts=2)
        return {}

    monkeypatch.setattr(tool.x, "run_s4", _two_calls)
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    row = tool.run_arm(object(), sess, "legacy")

    assert row["prompt_tokens"] == 4000, (
        "the accumulated total (4000), not the last call's snapshot (1500)")


def test_search_block_is_reconstructed_from_the_s2_list(tmp_path):
    """Both arms must see the SAME search results — that is what keeps the A/B
    single-variable.

    ⛔ The reconstruction is NOT production geometry and an earlier revision of
    this docstring (and of the tool's module docstring) said it was.
    `extract_session_v2` runs S3 (`search_graph`) BEFORE S4 and writes the
    session's items only afterwards (`execute_embed`), so production S4 reads
    bounded PREVIOUS-graph hits. Relabelling the session's own list as
    already-present is the maximally gap-SUPPRESSING input for a delta contract,
    which is why the artifact reports `s4_items` / `added_by_s4` beside the token
    number instead of leaving the saving to be read alone (#11385 review P1)."""
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    search = tool.search_from_s2(sess["s2"])
    assert search["entities"][0]["name"] == "Halcyon archive"
    assert search["points"][0]["content"].startswith("The re-ingest")
    assert search["events"][0]["kind"] == "core:Occurrence"


# ── 5. fail-closed behaviour ───────────────────────────────────────────────


def test_run_refuses_without_a_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    rc = tool.main(["run", "--receipt", str(_receipt(tmp_path / "r"))])
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().err


def test_run_with_every_arm_failed_exits_nonzero(monkeypatch, tmp_path,
                                                 capsys):
    """The measured-zero case: a dead endpoint must not exit 0 with a null
    reduction, which reads as a completed measurement."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-used")
    monkeypatch.setattr(tool, "_model_for",
                        lambda _mid: _FakeModel({}, fail="401 Unauthorized"))
    rc = tool.main(["run", "--receipt", str(_receipt(tmp_path / "r")),
                    "--out", str(tmp_path / "out.json")])
    assert rc == 1
    assert "0 sessions produced a usable pair" in capsys.readouterr().err


def test_load_sessions_raises_on_a_receipt_with_no_embed_list(tmp_path):
    """A silently skipped session would shrink the sample without saying so."""
    (tmp_path / "empty.arms.json").write_text(json.dumps({"stories": {}}))
    with pytest.raises(ValueError, match="no 'narrative' embed list"):
        tool.load_sessions(tmp_path)



def test_a_healthy_retried_arm_is_NOT_voided():
    """⛔ `stats["attempts"]` must not be compared against `cost["calls"]`.

    `_complete` writes `stats.update(attempts=attempt, …)` on its SUCCESS path,
    where `attempt` is the attempt number of ONE logical call — so a single
    429-retried call reports `attempts=2` while `_accumulate_call_cost` ran once
    (`calls=1`), and the whole spend IS in the accumulator. An earlier revision
    refused exactly that healthy arm as "partial", which would have silently
    dropped the session from the measurement (and made a fully-measured run exit
    1 with "0 sessions produced a usable pair"). It also overwrites the same key
    on the raise path, so the gap is neither sound nor complete in the other
    direction (#11385 review P2).

    What the instrument owes the reader is the DATA, not a classification: the
    row records `attempts` and `calls` beside the total.
    """
    stats = {"cost": {"calls": 1, "completion_tokens": 5000}, "attempts": 2}
    assert tool.arm_out_tokens(stats) == (5000, "stats.cost.completion_tokens")


_EVIDENCE = _REPO / "docs" / "evidence" / "1789-s4-reeemit-tax"


def test_every_committed_artifact_reconciles_with_its_own_rows():
    """The evidence bundle's headline numbers are re-derived from the artifacts —
    so an artifact-only edit selects this pin in CI, which is the reason
    `tools/ci_selection.py`'s `CORE_ALSO` carries the bundle's path.

    ⛔ What is pinned is the artifact's INTERNAL CONSISTENCY, not a literal
    figure, deliberately: the instrument is re-run and its artifacts replaced
    whenever the measurement is re-taken (#11385 did this twice in one review),
    so a hard-coded number would red on a legitimate regeneration — a false block
    on the very workflow this bundle exists to support. What must never be green
    is an artifact whose published summary has drifted from the rows behind it,
    which is the corruption that makes a number like this un-auditable.
    """
    artifacts = sorted(_EVIDENCE.glob("ab-*.json"))
    assert artifacts, "the evidence bundle lost its artifacts"
    for path in artifacts:
        d = json.loads(path.read_text())
        s = d["summary"]
        rows = s["rows"]
        assert rows, path.name
        # ⛔ An unmeasured arm is a DOCUMENTED shape ("a failed arm is data",
        # `out_tokens: null`), so the arithmetic below runs over MEASURED rows
        # only — mirroring `compare`'s `usable`. An earlier revision assumed every
        # row was measured, so a legitimate regeneration with one failed arm both
        # redded and then crashed on `None` (#11385 review P3).
        measured = [r for r in rows
                    if r["legacy_out_tokens"] is not None
                    and r["delta_out_tokens"] is not None
                    and not r.get("legacy_error") and not r.get("delta_error")]
        assert s["sessions_attempted"] == len(rows), path.name
        assert s["sessions_measured"] == len(measured), path.name
        assert measured, path.name
        # the published totals ARE the sums of the rows
        assert s["sum_legacy_out_tokens"] == sum(
            r["legacy_out_tokens"] for r in measured), path.name
        assert s["sum_delta_out_tokens"] == sum(
            r["delta_out_tokens"] for r in measured), path.name
        # …and the headline is that ratio, not a number typed beside it
        assert round(1 - s["sum_delta_out_tokens"] / s["sum_legacy_out_tokens"],
                     4) == s["token_weighted_reduction"], path.name
        # the per-session statistics ARE the rows' statistics. ⛔ ROUND BEFORE
        # SORTING: `_reduction` rounds to 4 dp and `compare` sorts those rounded
        # values, so sorting unrounded ratios and averaging the middle two
        # diverges whenever the pair rounds in opposite directions (3 of 7
        # six-session subsets of the primary run; measured).
        reductions = sorted(
            round(1 - r["delta_out_tokens"] / r["legacy_out_tokens"], 4)
            for r in measured)
        assert round(min(reductions), 4) == s["min_session_reduction"]
        assert round(max(reductions), 4) == s["max_session_reduction"]
        # ⛔ replicate the PRODUCER's median, including its even-n branch.
        # `compare` averages the two middle values when n is even; taking the
        # upper middle value agrees at n=7 and false-blocks the moment a run
        # covers an even subset (which one `--sessions` flag can produce) — the
        # exact false block this pin's docstring says it must not cause.
        mid = len(reductions) // 2
        expected_median = (round(reductions[mid], 4) if len(reductions) % 2
                           else round((reductions[mid - 1] + reductions[mid]) / 2, 4))
        assert expected_median == s["median_session_reduction"]
        # each row's own `reduction` agrees with the two numbers it came from
        # (over MEASURED rows — `compare` writes `reduction: None` for an
        # unmeasured arm, so dividing here raised the very `TypeError` the
        # header fix above was supposed to close: #11385 review P2)
        for r in measured:
            assert round(1 - r["delta_out_tokens"] / r["legacy_out_tokens"],
                         4) == r["reduction"], (path.name, r["session"])
        # the composition channels reconcile: merged = base + added, and the
        # corrections are a SUBSET of the corrections census (the pair counts
        # the items an items-not-in-S2 filter would drop, the census counts
        # every changed item — the two differ by ~1/run and the README says so)
        for arm in ("legacy", "delta"):
            c = s["composition"][arm]
            assert c["merged_items"] == c["s2_items"] + c["added_by_s4"], (path.name, arm)
            assert c["corrected_by_s4"] <= c["s4_items"], (path.name, arm)
            assert c["verbatim_reemissions"] <= c["corrected_by_s4"], (path.name, arm)
        # a published byte count with no basis string is not auditable
        for a in d["arms"]:
            for arm in ("legacy", "delta"):
                r = a[arm]
                if r.get("out_tokens") is not None:
                    assert r.get("basis"), (path.name, a["session"], arm)

        # ⛔ the summary must be DERIVED from `arms`, not merely self-consistent:
        # an artifact assembled from two different runs is the corruption this
        # pin exists for, and every check above would stay green (#11385 P2).
        assert len(s["rows"]) == len(d["arms"]), path.name
        for row, a in zip(s["rows"], d["arms"], strict=True):
            assert row["session"] == a["session"], path.name
            for arm in ("legacy", "delta"):
                assert row[f"{arm}_out_tokens"] == a[arm].get("out_tokens"), (
                    path.name, a["session"], arm)
                # ⛔ EVERY field the summary copies, not just the token total:
                # `delta_added_by_s4` is what the published "no new tax-surface
                # item in 5 of 7 sessions" rides on, and a summary edited to
                # disagree with its own rows is the corruption this pin exists
                # for (#11385 review P2).
                assert row[f"{arm}_s4_items"] == a[arm].get("s4_items"), (
                    path.name, a["session"], arm, "s4_items")
                assert row[f"{arm}_truncated"] == a[arm].get("truncated"), (
                    path.name, a["session"], arm, "truncated")
                assert row[f"{arm}_error"] == a[arm].get("error"), (
                    path.name, a["session"], arm, "error")
            assert row["s2_items"] == a["legacy"].get("s2_items"), (
                path.name, a["session"], "s2_items")
            for key, field in (("delta_verbatim_reemissions", "verbatim_reemissions"),
                               ("delta_added_by_s4", "added_by_s4"),
                               ("delta_corrected_by_s4", "corrected_by_s4")):
                assert row[key] == a["delta"].get(field), (path.name, a["session"], key)
            assert row["delta_corrected_items"] == (
                (a["delta"].get("corrected_items") or {}).get("count")), (
                path.name, a["session"], "delta_corrected_items")
        # ⛔ over the MEASURED pairs, mirroring `compare`'s `usable_pairs` —
        # summing every arm here would red a legitimate artifact that contains
        # one failed arm, which is the shape this pin must tolerate (a failed
        # arm keeps its spend and its census, but is excluded from the totals).
        measured_arms = [a for a in d["arms"]
                         if a["legacy"].get("out_tokens") is not None
                         and a["delta"].get("out_tokens") is not None
                         and not a["legacy"].get("error")
                         and not a["delta"].get("error")]
        assert len(measured_arms) == s["sessions_measured"], path.name
        for arm in ("legacy", "delta"):
            for k in ("s2_items", "s4_items", "merged_items", "added_by_s4",
                      "corrected_by_s4", "verbatim_reemissions"):
                assert s["composition"][arm][k] == sum(
                    a[arm].get(k) or 0 for a in measured_arms), (path.name, arm, k)
        # ⛔ `_capped`'s OWN contract, per list: `count` is the CENSUS number,
        # `recorded` is len(items), and the two deficits must add up. Without
        # this a list whose `count` drifted from `added_by_s4` stays green.
        for a in d["arms"]:
            for arm in ("legacy", "delta"):
                r = a[arm]
                if r.get("out_tokens") is None:
                    continue
                added = r.get("added_items") or {}
                if added:
                    assert added["count"] == r.get("added_by_s4"), (
                        path.name, a["session"], arm, "added count")
                    assert added["recorded"] == len(added["items"])
                    assert added["cap_dropped"] == max(
                        0, added["candidates"] - added["recorded"])
                    assert added["unclassified"] == max(
                        0, added["count"] - added["candidates"])
                corr = r.get("corrected_items") or {}
                if corr:
                    assert corr["count"] == max(
                        0, (r.get("corrected_by_s4") or 0)
                        - (r.get("verbatim_reemissions") or 0)), (
                        path.name, a["session"], arm, "corrected count")
                    assert corr["recorded"] == len(corr["items"])
                    assert corr["cap_dropped"] == max(
                        0, corr["candidates"] - corr["recorded"])
                    assert corr["unclassified"] == max(
                        0, corr["count"] - corr["candidates"])
        # ⛔ EVERY capped list on EVERY arm of EVERY pair, not one list per arm:
        # the README states the cap never fired, so an unpinned list would let
        # it become false silently (#11385 P2).
        for a in d["arms"]:
            for arm in ("legacy", "delta"):
                for key in ("added_items", "corrected_items"):
                    lst = a[arm].get(key) or {}
                    if not lst:
                        continue
                    assert lst["cap_hit"] is False, (path.name, a["session"], arm, key)
                    assert lst["cap_dropped"] == 0, (path.name, a["session"], arm, key)


def test_every_row_records_the_numbers_the_residual_says_it_does(tmp_path):
    """The docstring's raised-attempt residual says "every row carries `attempts`
    and `calls` beside the total". Both halves were false — the success row
    recorded `attempts` only, and the ERROR row (the one shape the residual is
    about) recorded neither — so deleting the guard on the grounds that the data
    is "recorded rather than classified" was not delivered (#11385 review P2)."""
    sess = tool.load_sessions(_receipt(tmp_path / "r"))[0]
    ok = tool.run_arm(_FakeModel({"entities": [], "events": [], "points": [],
                                  "operators": []}), sess, "legacy")
    bad = tool.run_arm(_FakeModel({}, fail="401 Unauthorized"), sess, "legacy")
    for row in (ok, bad):
        assert "attempts" in row, row
        assert "calls" in row, row
        assert isinstance(row["attempts"], int), row
        assert isinstance(row["calls"], int), row
    assert ok["attempts"] >= 1, "the success row made at least one attempt"
    # ⛔ a VALUE assertion, not just presence: `calls` must come from
    # `stats["cost"]["calls"]` (where `_accumulate_call_cost` counts). Reading
    # the never-populated top-level `stats["calls"]` instead publishes
    # `calls: 0` on every row, which passes a presence check and silently
    # re-creates the "documented but unbacked" state this pin exists to
    # prevent (#11385 review P3).
    assert ok["calls"] >= 1, "the successful call was accumulated into cost[\"calls\"]"


# ── the evidence artifact is the deliverable, so pin it to its README ────────

def _tables(readme: str) -> list[dict[str, list[str]]]:
    """Every pipe table in the document as {first cell: remaining cells}.

    ⛔ A SUBSTRING pin is not a discriminating pin: `201` also occurs in the
    re-run sentence, `12` occurs four times, `62` occurs four times — so
    "the figure appears somewhere" stays green when the CELL that states it is
    wrong (#11385 review P2). Parsing the table makes the assertion about the
    cell the reader actually reads.
    """
    tables: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    for raw in readme.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            if current:
                tables.append(current)
                current = {}
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue                              # the header separator
        current[cells[0]] = cells[1:]
    if current:
        tables.append(current)
    return tables


def _num(cell: str) -> int | None:
    """The integer a table cell states, ignoring emphasis and spacing."""
    digits = re.sub(r"[^0-9]", "", cell.replace("**", ""))
    return int(digits) if digits else None


_EVID = _REPO / "docs" / "evidence" / "1789-s4-reeemit-tax"
_PRIMARY = _EVID / "ab-20261107T023958Z.json"
_RERUN = _EVID / "ab-20261107T024226Z.json"


def _thousands(n: int) -> str:
    """How the README renders an integer: 32 787 (thin/plain space groups)."""
    return f"{n:,}".replace(",", " ")


def _partition(art: dict) -> dict:
    """The Task-10-Step-5 buckets, over one artifact's recorded pairs."""
    pairs = [i for a in art["arms"]
             for i in (a["legacy"].get("corrected_items") or {}).get("items") or []]
    buckets = {"content": 0, "lifecycle": 0, "slots": 0, "supersedes": 0}
    for i in pairs:
        if {"content", "name"} & set(i["changed_fields"]):
            buckets["content"] += 1
        elif i["changed_fields"] == ["lifecycle"]:
            buckets["lifecycle"] += 1
        elif i["changed_fields"] == ["slots"]:
            buckets["slots"] += 1
        elif i["changed_fields"] == ["supersedes"]:
            buckets["supersedes"] += 1
    return buckets


def test_the_readme_figures_reproduce_from_the_committed_artifacts():
    """The numbers ARE the deliverable, and nothing else stops a transcription
    slip: two revisions of this README carried figures the artifacts contradict
    (an arithmetic slip between the two arms' `s4_items`, and a "no kind
    changed" sentence next to a `slots` pair that changed one). This recomputes
    the headline set from the two committed artifacts and asserts the README
    carries each one — drift in either direction fails here — and pins the two
    checkable PROSE claims the retired phrasing got wrong.

    ⛔ This is the LITERAL-FIGURE counterpart of
    `test_every_committed_artifact_reconciles_with_its_own_rows`, which pins the
    artifacts' INTERNAL consistency and deliberately hard-codes no number. The
    two are not redundant and the split is a considered one: that test must
    survive a legitimate regeneration (an artifact-only edit re-runs the
    instrument), while this one must NOT — regenerating the artifacts changes
    the published figures, and a README that silently keeps the old ones is
    exactly the corruption this bundle exists to be auditable against. So a
    regeneration legitimately reds here, and the fix is to update the document."""
    readme = (_EVID / "README.md").read_text()
    flat_all = " ".join(readme.split())
    pri = json.loads(_PRIMARY.read_text())
    rer = json.loads(_RERUN.read_text())

    # ⛔ ANCHOR the two headline sets to their own runs: a presence check stays
    # green when the primary and re-run figures are swapped, which is exactly a
    # transcription slip (#11385 review P2).
    ps, prs = pri["summary"], rer["summary"]
    assert (f"{_thousands(ps['sum_legacy_out_tokens'])} → "
            f"{_thousands(ps['sum_delta_out_tokens'])} output tokens over 7 "
            "sessions") in flat_all
    # the input provenance the README states, from `inputs[]`; the multiples it
    # quotes; the per-row gap channel behind "5 of 7 sessions"; and the test count
    refs = sorted(i["s2_reference"]["completion_tokens"] for i in pri["inputs"])
    assert refs == sorted(i["s2_reference"]["completion_tokens"] for i in rer["inputs"])
    assert (f"{_thousands(refs[0])}–{_thousands(refs[-1])} tokens, median "
            f"{_thousands(round(__import__('statistics').median(refs)))}") in flat_all
    # ⛔ anchored: a bare `2.3` also matches inside a longer number elsewhere.
    assert (f"**MET**, {ps['token_weighted_reduction'] / 0.40:.1f}× the threshold"
            in flat_all), ps["token_weighted_reduction"]
    _spread = round((prs["token_weighted_reduction"]
                     - ps["token_weighted_reduction"]) * 100, 2)
    assert f"differ by {_spread:.2f} points" in flat_all
    _count = sum(1 for k in globals() if k.startswith("test_"))
    assert f"**{_count}** tests" in flat_all, _count
    _zeros = sum(1 for a in pri["arms"] if a["delta"].get("added_by_s4") == 0)
    assert (f"in {_zeros} of {len(pri['arms'])} sessions in both runs") in flat_all
    assert sum(1 for a in rer["arms"] if a["delta"].get("added_by_s4") == 0) == _zeros
    assert (f"`merged_items` is {pri['summary']['composition']['delta']['merged_items']} "
            "vs an S2 base of "
            f"{pri['summary']['composition']['legacy']['s2_items']}") in flat_all

    # ⛔ The README's population figures were the ONE unguarded copy of a claim
    # the index row and the emitted caveats both pin: rewriting every occurrence
    # (231→230, 266-of-311→265-of-311) left the whole suite green, and the README
    # is the document `docs/00_index.md` LINKS TO, so it is the surface most
    # likely to be read (#11385 review P3). Pinned literally here — a
    # regeneration that moves the population REDS this test and forces a doc
    # update — plus the superseded pair banned outright, because a count would
    # couple the pin to the document's prose and false-block a legitimate edit.
    assert "231 of the 266" in flat_all, "the offered population"
    assert "266 of the 311" in flat_all, "the replayed-list narrowing"
    assert "230 of the 265" not in flat_all, "a drifting copy of the population"
    assert "265 of the 311" not in flat_all, "a drifting copy of the narrowing"
    # …and 266 is checkable against the artifacts themselves: it is the replayed
    # S2 list's own total, which the composition must agree with. (311 and 231
    # need the source receipts, not the artifacts, so those two are pinned as
    # figures instead of re-derived here.)
    assert (sum(i["s2_items"] for i in pri["inputs"])
            == pri["summary"]["composition"]["legacy"]["s2_items"])
    # ⛔ BOTH copies of the population, not "at least one": the figure is stated in
    # the verdict cell AND under *What this does not measure*, and rewriting only
    # the second to a THIRD value (231 → 232) stayed green (#11385 review P3).
    assert "231 of the 266 items" in flat_all, "the second copy of the population"

    assert (f"Re-run (`ab-20261107T024226Z`): "
            f"{_thousands(prs['sum_legacy_out_tokens'])} → "
            f"{_thousands(prs['sum_delta_out_tokens'])} tokens, **−"
            f"{prs['token_weighted_reduction'] * 100:.2f} %**") in flat_all

    # ⛔ THE INPUT SIDE (#11385 post-rebase review P2). The artifacts record
    # `prompt_tokens` per arm and the delta template is LONGER, so the switch
    # buys its output saving by paying a small, positive input cost. The README
    # published only the output drop — the one asymmetry a reader cannot recover
    # from the document (−93.8 % in the title, −20.5 % in reality) — while the
    # dry-run pin's own comment already asserted "the input-side cost of the
    # switch is small and POSITIVE … a reader must be able to see that the prompt
    # did not get cheaper". Every figure below is DERIVED from the artifacts, so
    # the prose stays a computed claim rather than a typed one, and the
    # per-session constant is pinned because it is what makes the cost checkable.
    _per = {a["delta"]["prompt_tokens"] - a["legacy"]["prompt_tokens"]
            for a in pri["arms"]}
    assert _per == {a["delta"]["prompt_tokens"] - a["legacy"]["prompt_tokens"]
                    for a in rer["arms"]}, "the input cost is not a constant"
    assert len(_per) == 1, f"the per-session input cost is not one value: {_per}"
    _per = _per.pop()
    _plg_p = sum(a["legacy"]["prompt_tokens"] for a in pri["arms"])
    _pdl_p = _plg_p + _per * len(pri["arms"])
    assert _pdl_p == sum(a["delta"]["prompt_tokens"] for a in pri["arms"])
    assert (f"every one of the 7 sessions pays exactly +{_per} input tokens, in "
            "both runs") in flat_all, _per
    assert (f"{_thousands(_plg_p)} → {_thousands(_pdl_p)} prompt tokens, "
            f"+{_thousands(_pdl_p - _plg_p)}") in flat_all
    # …and the TOTAL, derived from the same two artifacts: the figure the
    # 4.6× and both percentages rest on.
    _plg_t = sum(a["legacy"]["prompt_tokens"] + a["legacy"]["out_tokens"]
                 for a in pri["arms"])
    _pdl_t = sum(a["delta"]["prompt_tokens"] + a["delta"]["out_tokens"]
                 for a in pri["arms"])
    _rlg_t = sum(a["legacy"]["prompt_tokens"] + a["legacy"]["out_tokens"]
                 for a in rer["arms"])
    _rdl_t = sum(a["delta"]["prompt_tokens"] + a["delta"]["out_tokens"]
                 for a in rer["arms"])
    assert (f"{_thousands(_plg_t)} → {_thousands(_pdl_t)} = "
            f"−{(1 - _pdl_t / _plg_t) * 100:.1f} %") in flat_all
    assert (f"{_thousands(_rlg_t)} → {_thousands(_rdl_t)} = "
            f"−{(1 - _rdl_t / _rlg_t) * 100:.1f} %") in flat_all
    assert (f"**{(ps['token_weighted_reduction'] / (1 - _pdl_t / _plg_t)):.1f}×** "
            "smaller in the") in flat_all
    # ⛔ The re-run's OWN multiple, not a spread over a single run's ratio: the
    # two runs give 4.58 and 4.46, so quoting only the primary is the same
    # unqualified-figure class the input-side paragraph exists to remove
    # (#11385 post-rebase review P3).
    assert (f"**{(prs['token_weighted_reduction'] / (1 - _rdl_t / _rlg_t)):.1f}×** "
            "in the re-run") in flat_all
    # ⛔ both directions: the output-only reading must not come back as the
    # unqualified number it was ("not −93.8 %" is the sentence that names it).
    assert "not −93.8 %" in flat_all

    # ⛔ the truncation cell quotes "the largest output in either arm of either run"
    # — so BOTH runs' maxima must be in the document, each attributed. A
    # primary-scoped cell quoting the re-run's maximum mixes runs, which is the
    # transcription-slip class this pin exists for (#11385 review P3).
    max_pri = max(a[arm].get("out_tokens") or 0
                  for a in pri["arms"] for arm in ("legacy", "delta"))
    max_rer = max(a[arm].get("out_tokens") or 0
                  for a in rer["arms"] for arm in ("legacy", "delta"))
    assert max_rer > max_pri, (max_pri, max_rer)   # else the cell's claim is moot
    # ⛔ ANCHORED, not bare: `_thousands(521) == "521"` is a SUBSTRING of the
    # document's own "6 521", so a bare `in` stays green when a regeneration
    # collapses the maximum and the stale figure keeps its place — the same
    # prefix defect as the largest-list pin (#11385 review P3).
    assert f"**{_thousands(max_rer)}** tokens, the re-run's" in flat_all, max_rer
    assert f"tops out at **{_thousands(max_pri)}**" in flat_all, max_pri
    # ⛔ "the largest list is 17" is a published figure too: pinning only
    # `cap_hit`/`cap_dropped` left it stale-proof — a regeneration with a larger
    # recorded list keeps 17 in the document and every test green (#11385 review P3).
    biggest = max(len(lst.get("items") or [])
                  for art in (pri, rer) for a in art["arms"]
                  for arm in ("legacy", "delta")
                  for lst in (a[arm].get("added_items") or {},
                              a[arm].get("corrected_items") or {}))
    # ⛔ ANCHOR on the document's closing delimiter: the bare substring matched
    # a PREFIX, so a regeneration whose real maximum was 1 stayed green
    # against the documented 17 (#11385 review P3).
    assert f"the largest list is {biggest})" in flat_all, biggest

    for art in (pri, rer):
        s, comp = art["summary"], art["summary"]["composition"]
        lg, dl = comp["legacy"], comp["delta"]
        # ⛔ ANCHORED to the run's own sentence: a bare "2 038" is also satisfied by
        # a longer documented figure, and the two runs' sums sit one line apart
        # (#11385 review P3).
        sl, sd = (_thousands(s["sum_legacy_out_tokens"]),
                  _thousands(s["sum_delta_out_tokens"]))
        assert ((f"{sl} → {sd} output tokens over 7 sessions" if art is pri
                 else f"{sl} → {sd} tokens") in " ".join(readme.split())), (sl, sd)
        # ⛔ TWO decimals only. The 1-dp fallback matched the title/verdict `93.8`,
        # so a reduction anywhere in [93.80, 93.89] left the 2-dp figure at the
        # summary line free to go stale — a window half the observed run-to-run
        # spread, i.e. a legitimate re-run can land in it (#11385 review P3).
        # ⛔ the 2-dp reduction is NOT checked here: it appears once, in the
        # spread parenthetical, and the ANCHORED pin below (`(`−a %` / `−b %`)`)
        # is the one that bites — a bare `in` here was satisfied by that same
        # parenthetical whichever run it belonged to (#11385 review P3).
        # ⛔ anchored: a bare "85.16" also matches inside a longer number, and a
        # computed 5.16 would satisfy it (#11385 review P3). The two runs state
        # their worst differently — the table cell (primary) bolds it, the re-run
        # sentence does not — so each is anchored where its own figure lives.
        flat_m = " ".join(readme.split())
        min_txt = f"{s['min_session_reduction'] * 100:.2f} %"
        assert ((f"worst session **−{min_txt}**" if art is pri
                 else f"worst −{min_txt}") in flat_m), s["min_session_reduction"]
        # composition: the legacy tax vs the delta arm's zero
        assert lg["verbatim_reemissions"] > 0 and dl["verbatim_reemissions"] == 0
        # ⛔ anchored on its clause: a bare `272` is satisfied by the document's
        # own `merged_items` 272, so a computed 27 would pass (#11385 review P3).
        # ⛔ LEFT boundary too: right-anchored only, a computed 72 matched inside
        # the document's "272 fewer items per 7 sessions" — the same prefix hole
        # one digit to the left (#11385 review P3).
        few = _thousands(lg["s4_items"] - dl["s4_items"])
        assert (f"| {few} fewer items per 7 sessions |" in readme
                or f"i.e. {few} fewer items" in " ".join(readme.split())), few
        # ⛔ EVERY channel figure, pinned IN ITS TABLE CELL: a substring check
        # stays green when the wrong cell changes, because `201`, `12` and `62`
        # each occur several times in the document (#11385 review P2). The
        # TABLE is the PRIMARY run (its caption says so), so the re-run is
        # pinned by its own sentence instead of being matched against it.
        # ⛔ anchored, per run: a 1-dp bare match also hides the 2-dp neighbours
        # (#11385 review P3), and only the primary table cell bolds its figure.
        best_txt = f"{s['max_session_reduction'] * 100:.1f} %"
        assert ((f"best **−{best_txt}**" if art is pri else f"best −{best_txt}")
                in " ".join(readme.split())), s["max_session_reduction"]
        if art is not pri:
            flat_r = " ".join(readme.split())
            assert (f"Re-run: legacy {lg['verbatim_reemissions']} verbatim / "
                    f"{lg['corrected_by_s4']} collisions / {lg['added_by_s4']} "
                    f"added / **{lg['s4_items']}** emitted") in flat_r, lg
            assert (f"6 added / **{dl['s4_items']}** emitted — i.e. "
                    f"{lg['s4_items'] - dl['s4_items']} fewer items") in flat_r
            continue
        rows = {}
        for table in _tables(readme):
            for label, cells in table.items():
                if "census" in label and len(cells) >= 2:
                    rows["census"] = (_num(cells[0]), _num(cells[1]))
                for key in ("verbatim_reemissions", "corrected_by_s4",
                            "added_by_s4", "s4_items", "merged_items"):
                    if f"`{key}`" in label and len(cells) >= 2:
                        rows[key] = (_num(cells[0]), _num(cells[1]))
        assert rows["verbatim_reemissions"] == (lg["verbatim_reemissions"], 0), rows
        assert rows["corrected_by_s4"] == (lg["corrected_by_s4"], 0), rows
        assert rows["added_by_s4"] == (lg["added_by_s4"], dl["added_by_s4"]), rows
        assert rows["s4_items"] == (lg["s4_items"], dl["s4_items"]), rows
        # the census row's label wraps the subtraction, so match on the word
        assert rows["census"] == (lg["corrected_by_s4"] - lg["verbatim_reemissions"],
                                  0), rows

        # ⛔ THE HEADLINE REDUCTION WAS PINNED IN ONLY ONE OF ITS FOUR RENDERINGS.
        # `−93.78 %` occurs exactly once, in the run-to-run-spread parenthetical,
        # so the TITLE and the VERDICT-TABLE CELL — the two surfaces a reader
        # actually reads — were unguarded. Measured at this head: mutating the
        # title `−93.8` → `−94.8`, or the verdict cell `−93.8` → `−93.4`, each
        # left the suite green — the same false-pass class the 1-dp-fallback
        # removal was supposed to close, because removing a tolerant fallback
        # does not PINS the 1-dp renderings (#11385 review P1). Parse the CELL
        # and pin its figures against the primary summary; pin the title too.
        verdict = None
        for table in _tables(readme):
            for label, cells in table.items():
                if "S4 output size per chunk drops" in label and cells:
                    verdict = cells[0]
        assert verdict is not None, "the verdict row is gone"
        assert (f"**−{s['token_weighted_reduction'] * 100:.1f} %** token-weighted"
                in verdict), verdict
        assert (f"median **−{s['median_session_reduction'] * 100:.1f} %**"
                in verdict), verdict
        assert (f"worst session **−{s['min_session_reduction'] * 100:.2f} %**"
                in verdict), verdict
        assert (f"best **−{s['max_session_reduction'] * 100:.1f} %**"
                in verdict), verdict
        assert (f"{_thousands(s['sum_legacy_out_tokens'])} → "
                f"{_thousands(s['sum_delta_out_tokens'])} output tokens"
                in verdict), verdict
        # the TITLE states the same reduction in its own 1-dp rendering
        assert (f"measured: −{s['token_weighted_reduction'] * 100:.1f} %"
                in flat_all), "the title's reduction drifted"
        # ⛔ the VERDICT CELL is the surface a reader reads, and its population
        # figures were unguarded: `**231 of the 266**` → `**all 266**`, →
        # `**230 of the 266**`, `those 231` → `those 230`, and
        # `the arm took none: 0 collisions` → `2 collisions` each left 36/36
        # green, because the only population pin matched the OTHER copy of the
        # claim (#11385 review P1). The refuted reading is also banned outright,
        # doc-wide, since re-introducing it is the whole failure being guarded.
        # `_tables()` returns the MEASURED column only, so these are pinned on the
        # flattened document, each anchored to wording unique to the VERDICT cell
        # (the second copy of the claim reads "the reconstruction offers 231 …").
        assert ("offers the delta arm **231 of the 266** S2 items as already "
                "existing") in flat_all, "the verdict cell's population drifted"
        assert "for those 231" in flat_all, "the verdict cell's population drifted"
        assert ("the arm took none: 0 collisions" in flat_all), (
            "the verdict cell's collision count drifted")
        assert "all 266" not in flat_all, (
            "the refuted population reading is back in the document")
        # ⛔ the "35 operators" figure was guarded in the INDEX row and nowhere a
        # reader actually reads: 35 -> 36 left 36/36 green in all three README
        # copies, which silently re-states the verdict cell's own 266-231
        # arithmetic (#11385 review P2). Derived, not typed.
        # ⛔ DERIVED FROM THE RECEIPTS, not typed: `231` was a literal, so a
        # `_render_search_results` that started rendering `operators` would make
        # 231/35 false in three README copies, the index row and the caveat while
        # the suite stayed green (#11385 review P3).
        _sess = tool.load_sessions(_REPO / tool._DEFAULT_RECEIPT)
        _offered = sum(len(v) for s in _sess
                       for v in tool.search_from_s2(s["s2"]).values())
        _ops = sum(len(s["s2"]["operators"]) for s in _sess)
        _tax = sum(len(s["s2"][k]) for s in _sess for k in tool._TAX_SECTIONS)
        assert _offered == 231, _offered
        assert _ops == 35, _ops
        assert _tax == lg["s2_items"], (_tax, lg["s2_items"])
        assert _offered == _tax - _ops, (_offered, _tax, _ops)
        ops = _ops
        assert ops == 35, ops
        assert f"so the {ops} `operators` items are never offered" in flat_all, ops
        assert f"the {ops} `operators`" in flat_all
        assert (f"the {ops} `operators` are never rendered, in production too"
                in flat_all), ops
        # ⛔ the FOURTH copy (the caveat-provenance paragraph) was still
        # unguarded: 35 -> 36 there stayed green (#11385 review P3).
        assert f"the {ops} never-offered `operators`" in flat_all, ops
        # ⛔ and the narrowing BREAKDOWN in the DOCUMENT was unpinned while the
        # instrument copies were pinned: `chain_notes` 7 -> 8 and
        # `link_before_create` 38 -> 39 both stayed green, breaking the
        # document's own 266 + 7 + 38 = 311 arithmetic, whose total IS pinned
        # (#11385 review P3).
        assert "`chain_notes` 7 + `link_before_create` 38 dropped" in flat_all
        assert "`chain_notes` (7) and `link_before_create` (38)" in flat_all
        # ⛔ the FLOOR sentence must be denominated in the tax-surface component, not
        # the whole-output total: with a third channel present (5 rows whose
        # `s4_items` is 0 still spend tokens), a floor scoped to two channels
        # cannot bound the total — the previous wording said "production cost is ≥
        # the measured 2 038", which the artifact cannot support (#11385 review
        # P2). Derived here, never typed.
        # ⛔ `out_tokens` is the provider's WHOLE-response token count and the
        # artifact records item COUNTS, never a per-section token split — so these
        # are a ROW PARTITION of whole-output tokens, not a channel split, and the
        # sentence must not call them "tokens the channels account for" (the
        # earlier wording did, which inverted the bound: the component is AT MOST
        # the response). The artifact itself shows the residual: wp05 and wp07
        # emitted the same channel COUNTS (3 gap items, 0 corrections) for 968 vs
        # 530 in the primary, 958 vs 522 in the re-run (#11385 review P2/P3).
        _zero_tok = sum(a["delta"]["out_tokens"] for a in pri["arms"]
                        if (a["delta"].get("s4_items") or 0) == 0)
        _tax_tok = sum(a["delta"]["out_tokens"] for a in pri["arms"]) - _zero_tok
        assert (_zero_tok, _tax_tok) == (540, 1498), (_zero_tok, _tax_tok)
        assert (f"cost **{_thousands(_tax_tok)}**" in flat_all), _tax_tok
        assert (f"the other **{_thousands(_zero_tok)}**" in flat_all), _zero_tok
        # ⛔ BOTH RUNS' halves of the floor sentence. Only the primary's were
        # derived, so the re-run's `1 480` and `567` were typed figures that
        # could drift while `2 047` (which their sum makes) stayed pinned —
        # `1 490` and `568` each left 36/36 green (#11385 review P2).
        _zero_rer = sum(a["delta"]["out_tokens"] for a in rer["arms"]
                        if (a["delta"].get("s4_items") or 0) == 0)
        _tax_rer = sum(a["delta"]["out_tokens"] for a in rer["arms"]) - _zero_rer
        assert (f"cost **{_thousands(_tax_tok)}** (primary) / "
                f"**{_thousands(_tax_rer)}** (re-run)") in flat_all, (
                    _tax_tok, _tax_rer)
        assert (f"the other **{_thousands(_zero_tok)}** / "
                f"**{_thousands(_zero_rer)}** tokens") in flat_all, (
                    _zero_tok, _zero_rer)
        # ⛔ the census total, where the summary paragraph first states it:
        # `65` -> `64` there stayed green because only the LATER copy ("against
        # the census's 65") was pinned (#11385 review P2).
        _census = lg["corrected_by_s4"] - lg["verbatim_reemissions"]
        assert (f"Up to **{_census} corrections per 7 sessions**"
                in flat_all), _census
        assert "the delta RESPONSES that carried tax-surface items" in flat_all
        assert "upper bound on the DELTA ARM's tax-surface component" in flat_all
        assert "an EXACT total for" in flat_all, (
            "1 498 IS those responses' total — calling it a floor on that total"
            " contradicts the neighbouring clause (#11385 review P3)")
        assert "it bounds production" in flat_all
        # ⛔ "production maximum" was the overreach the count fix removed, surviving in
        # a HEADER: a block that can offer MORE items as existing cannot bound the
        # correction channel from above (#11385 review P2).
        assert "measured under a MAXIMALLY CORRECTION-FRIENDLY" in flat_all
        assert "the FLOOR, not a maximum" in flat_all
        assert "cannot go BELOW it" in flat_all
        # ⛔ "better" is ambiguous in exactly the direction this section spends 30 lines
        # retracting: a minimum bounds production from BELOW, and "do better" reads as
        # "emit more corrections" — the sentence now names the direction (#11385 P3).
        assert "cannot emit FEWER than this block did" in flat_all
        assert "cannot do better on this channel" not in flat_all
        # ⛔ and the "more to correct here than in production" clause restated the
        # cross-session "production can only offer fewer" reading the same bullet
        # retracts 4 lines later (#11385 P2).
        assert "than it will usually find in production" not in flat_all
        assert "measured at its *production maximum*" not in flat_all
        assert "it is measured at its production maximum" not in flat_all
        assert "no room to add to it" not in flat_all
        # ⛔ and the RE-RUN's half of the same sentence was a TYPED literal with no
        # assertion at all: a regeneration or a digit edit stating a false partition
        # stayed green (#11385 review P3). Derived from the re-run artifact.
        _r_zero = sum(a["delta"]["out_tokens"] for a in rer["arms"]
                      if (a["delta"].get("s4_items") or 0) == 0)
        _r_tax = sum(a["delta"]["out_tokens"] for a in rer["arms"]) - _r_zero
        assert (_r_zero, _r_tax) == (567, 1480), (_r_zero, _r_tax)
        assert f"**{_thousands(_r_tax)}** (re-run)" in flat_all, _r_tax
        assert f"**{_thousands(_r_zero)}**" in flat_all, _r_zero
        # ⛔ the COUNTS are what match — the ITEMS do not (wp05 emits 3 points, wp07
        # 1 point + 2 operators with empty content), so the document must not say
        # "identical channel content"; and BOTH runs are pinned, because the
        # sentence quotes a primary pair and a re-run pair (#11385 review P3).
        for _art in (pri, rer):
            _wp = {a["session"]: a["delta"] for a in _art["arms"]}
            _a, _b = _wp["wp05_retro_writeup"], _wp["wp07_bluepeak_followup"]
            assert _a["s4_items"] == _b["s4_items"] == 3, "the same-counts case"
            assert (_a["added_by_s4"], _b["added_by_s4"]) == (3, 3), "same counts"
            assert (_a["corrected_by_s4"], _b["corrected_by_s4"]) == (0, 0)
        assert "the same channel COUNTS" in flat_all
        assert "identical channel content" not in flat_all, (
            "the falsified characterisation is back")
        _pa = {a["session"]: a["delta"] for a in pri["arms"]}
        _ra = {a["session"]: a["delta"] for a in rer["arms"]}
        _costs = (_pa["wp05_retro_writeup"]["out_tokens"],
                  _pa["wp07_bluepeak_followup"]["out_tokens"],
                  _ra["wp05_retro_writeup"]["out_tokens"],
                  _ra["wp07_bluepeak_followup"]["out_tokens"])
        assert _costs == (968, 530, 958, 522), _costs
        assert (f"cost {_costs[0]} vs {_costs[1]} " in flat_all), _costs
        assert (f"({_costs[2]} vs {_costs[3]} in the re-run)" in flat_all), _costs
        # ⛔ and the per-session comparison that replaced "Production can only offer
        # fewer": production's cap is PER SESSION (15 x 3 = 45), this block offers
        # 25-45 per session, so the cap is LARGER in 6 of 7 and equal in the seventh.
        # Derived from the receipts + the rendered sections (#11385 review P3).
        _off = [sum(len(v) for v in tool.search_from_s2(x["s2"]).values())
                for x in _sess]
        assert (min(_off), max(_off)) == (25, 45), (min(_off), max(_off))
        assert sum(1 for x in _off if x < 45) == 6, sorted(_off)
        assert "offers 25–45 items" in flat_all
        assert "in 6 of the 7 sessions production's cap is the LARGER number" in flat_all
        assert "Production can only offer fewer" not in flat_all, (
            "the cross-session/per-session overreach is back")
        # ⛔ two more artifact-derivable figures the index row claims are
        # "re-derived": 29-49 -> 30-49 and 12/9 -> 12/8 were both green
        # (#11385 review P3).
        _items = [i["s2_items"] for i in pri["inputs"]]
        assert (f"{min(_items)}–{max(_items)} items per session"
                in flat_all), (min(_items), max(_items))
        assert (f"legacy `added_by_s4` "
                f"{pri['summary']['composition']['legacy']['added_by_s4']} "
                f"(primary) / "
                f"{rer['summary']['composition']['legacy']['added_by_s4']} "
                f"(re-run)") in flat_all, "the gap-channel pair drifted"
        # the census-vs-mask divergence, as the SENTENCE that states it
        census = lg["corrected_by_s4"] - lg["verbatim_reemissions"]
        recorded = sum(a["legacy"]["corrected_items"]["recorded"]
                       for a in art["arms"])
        assert census - recorded in (0, 1)
        flat = " ".join(readme.split())
        assert f"records **{recorded} correction pairs** against the census's " \
               f"{census}" in flat, (recorded, census)
        unclassified = [a["legacy"]["corrected_items"]["unclassified"]
                        for a in art["arms"]]
        n_unclass, n_rows = sum(1 for u in unclassified if u), len(unclassified)
        assert f"`unclassified` is 0 on {n_rows - n_unclass} of the {n_rows} " \
               f"rows and {n_unclass} on the remaining one" in flat, n_unclass
        # ⛔ every capped list of every arm, not one list per pair
        for a in art["arms"]:
            for arm in ("legacy", "delta"):
                for key in ("added_items", "corrected_items"):
                    lst = a[arm].get(key) or {}
                    if lst:
                        assert lst["cap_hit"] is False
                        assert lst["cap_dropped"] == 0
        # the partition the residual rests on: 62 / 1 / 1, no content change —
        # asserted of the ARTIFACT here and of the README's bucket table below
        pairs = [i for a in art["arms"]
                 for i in (a["legacy"].get("corrected_items") or {}).get("items") or []]
        n_lifecycle = sum(i["changed_fields"] == ["lifecycle"] for i in pairs)
        n_slots = sum(i["changed_fields"] == ["slots"] for i in pairs)
        n_super = sum(i["changed_fields"] == ["supersedes"] for i in pairs)
        assert (n_lifecycle, n_slots, n_super) == (62, 1, 1), (
            n_lifecycle, n_slots, n_super)
        assert not any({"content", "name"} & set(i["changed_fields"]) for i in pairs)
        # ⛔ the TRANSITION breakdown beside the total: the README states
        # "(61 + 1)", and `61` -> `60` there stayed green while the total 62 and
        # the index row's own copy were pinned (#11385 review P2).
        _trans: dict[tuple[str, str], int] = {}
        for i in pairs:
            if i["changed_fields"] == ["lifecycle"]:
                _k = (i["s2_before"]["lifecycle"], i["s4_after"]["lifecycle"])
                _trans[_k] = _trans.get(_k, 0) + 1
        assert sum(_trans.values()) == n_lifecycle, (_trans, n_lifecycle)
        assert (f"`unchanged` ({_trans[('created', 'unchanged')]} + "
                f"{_trans[('changed', 'unchanged')]})") in " ".join(readme.split()), (
                    _trans, "the lifecycle transition breakdown drifted")
        # ⛔ BOTH columns: the table states the primary AND the re-run count, so
        # pinning only the first leaves the second editable (#11385 review P2).
        want = _partition(pri)
        want_rer = _partition(rer)
        buckets = {}
        for table in _tables(readme):
            for label, cells in table.items():
                for key, prefix in (("content", "`content`"),
                                    ("lifecycle", "`lifecycle`"),
                                    ("slots", "`slots`"),
                                    ("supersedes", "`supersedes`")):
                    if label.startswith(prefix) and len(cells) >= 2:
                        buckets[key] = (_num(cells[0]), _num(cells[1]))
        assert buckets == {k: (want[k], want_rer[k]) for k in want}, (
            buckets, want, want_rer)
        # ⛔ the operator-gap figure the residual states: `added_items` records
        # an operator's identity only inside its `key`, so the count is worth
        # stating — and worth pinning, since nothing else reads `section`.
        ops_legacy = sum(1 for a in art["arms"]
                         for i in (a["legacy"].get("added_items") or {}).get("items") or []
                         if i["section"] == "operators")
        ops_delta = sum(1 for a in art["arms"]
                        for i in (a["delta"].get("added_items") or {}).get("items") or []
                        if i["section"] == "operators")
        flat_doc = " ".join(readme.split())
        assert (f"In the primary run {ops_legacy} of {lg['added_by_s4']} legacy gaps "
                f"and {ops_delta} of {dl['added_by_s4']} delta gaps are operators"
                ) in flat_doc, (ops_legacy, ops_delta)
        # ⛔ BOTH arms must actually contribute, which is the state the residual
        # claims ("5 of 12 legacy gaps and 2 of 6 delta gaps are operators").
        # A `len(gaps) == ops_legacy + ops_delta` equality built from the same
        # two filters is an IDENTITY — true for any artifact — so it is not a
        # pin; the flat_doc assertion above is the one that bites (#11385 P3).
        assert ops_legacy > 0 and ops_delta > 0, (ops_legacy, ops_delta)
        # the delta arm's honest limitation, not a hidden one
        assert sum(a["delta"].get("s4_delta_empty") is False
                   for a in art["arms"]) == 7
    # ⛔ ANCHORED and run-attributed: these were bare (`96.9` / `96.87`), so the
    # two runs' medians could be swapped and a 1-dp window (0.10 wide against an
    # observed 0.01 median spread) would hide a moved figure; the spread
    # sentence itself could also have its two values transposed (#11385 review
    # P3).
    flat_med = " ".join(readme.split())
    assert (f"median **−{pri['summary']['median_session_reduction'] * 100:.1f} %**"
            in flat_med), pri["summary"]["median_session_reduction"]
    assert (f"median −{rer['summary']['median_session_reduction'] * 100:.2f} %"
            in flat_med), rer["summary"]["median_session_reduction"]
    assert (f"(`−{pri['summary']['token_weighted_reduction'] * 100:.2f} %` / "
            f"`−{rer['summary']['token_weighted_reduction'] * 100:.2f} %`)"
            in flat_med), "the spread sentence must name the runs in order"
    # ⛔ the truncation cell is where the whole "NOT EVIDENCED" argument starts,
    # and its `14/14` was unpinned: flipping one arm to `truncated=True` with
    # `attempts=2` (and mirroring it into `summary.rows`) left 36/36 green while
    # the README asserted a false absence (#11385 review P3).
    for art in (pri, rer):
        n_calls = sum(1 for a in art["arms"] for arm in ("legacy", "delta")
                      if a[arm].get("truncated") is False
                      and a[arm].get("attempts") == 1)
        total = 2 * len(art["arms"])
        assert n_calls == total, (n_calls, total)
        assert (f"`truncated=False` on **{n_calls}/{total}** arm calls, "
                "`attempts=1`") in flat_med, (art["measured_at"], n_calls)

    # the two checkable PROSE claims: the retired absolute ("no kind changed")
    # is contradicted by the `slots` pair, so its absence (in either wording) is
    # asserted, and the re-type it actually is must be stated.
    # Neither the original wording nor its paraphrase may come back. The match
    # is on WHITESPACE-COLLAPSED text: a raw substring pin is typography-
    # dependent and therefore wrong in both directions — a re-assertion wrapped
    # across a line break passes it (false green), while a legitimate reflow of
    # the disowning sentence fails it (false red).
    flat = " ".join(readme.split())
    for retired in ("a name or a kind", "no kind changed"):
        assert retired not in flat, f"the retired claim came back: {retired!r}"
    slots = next(i for a in pri["arms"]
                 for i in a["legacy"]["corrected_items"]["items"]
                 if i["changed_fields"] == ["slots"])
    assert "core:code" in readme and "dev:code" in readme
    assert "core:code" in slots["s2_before"]["slots"]
    assert "dev:code" in slots["s4_after"]["slots"]


def test_a_failed_arm_that_SPENT_is_still_excluded_from_the_reduction():
    """The spend a failed arm keeps (deliberately — it is data) must not enter
    the headline. Direction matters: `_complete` accumulates cost BEFORE the
    parse, so a legacy arm that emitted 32 k and then failed to parse HAS a
    populated total — counting it would INFLATE the reduction, which is the
    flattering direction this instrument exists to prevent (#11385 review P2).

    The row keeps the spend and the error; `sessions_failed` keeps the excluded
    population visible so the exclusion cannot be silently shrunk."""
    bad = _pair("a", 32000, 100, legacy={"error": "ParseError: no JSON"})
    ok = _pair("b", 2000, 200)
    s = tool.compare([bad, ok])
    assert s["sessions_measured"] == 1
    assert s["sessions_attempted"] == 2
    assert s["sessions_failed"] == 1
    # the flattering 32000 never reaches the sum
    assert s["sum_legacy_out_tokens"] == 2000
    assert s["token_weighted_reduction"] == pytest.approx(0.9)
    # …and the spend is still on the row, which is what makes it auditable
    assert s["rows"][0]["legacy_out_tokens"] == 32000
    assert s["rows"][0]["legacy_error"] == "ParseError: no JSON"
    # ⛔ and the value-channel table must describe the SAME population as the
    # headline: a looser filter here would report the excluded session's census
    # beside a one-session reduction (#11385 review P2).
    assert s["composition"]["legacy"]["s4_items"] == 10      # the healthy pair
    assert s["composition"]["delta"]["s4_items"] == 10


def test_a_partial_run_is_flagged_not_silently_published():
    """A headline computed from 1 of 7 sessions is publishable but must never
    read as a complete measurement: `partial` says so in one field, and
    `sessions_failed` keeps the excluded population visible (#11385 review P2)."""
    one = tool.compare([_pair("a", 1000, 100)])
    assert one["partial"] is False and one["sessions_failed"] == 0
    mixed = tool.compare([_pair("a", 1000, 100),
                          _pair("b", 2000, None, delta={"error": "Boom"})])
    assert mixed["partial"] is True
    assert mixed["sessions_failed"] == 1
    assert mixed["sessions_measured"] == 1
    # ⛔ THE case the old `0 < len(usable) < len(rows)` got wrong: a run that
    # measured NOTHING published `partial: False`, i.e. the one field that exists
    # to make incompleteness visible read "complete" exactly when it was empty.
    # Both populations above read identically under that expression, so without
    # this case a revert stays green (#11385 review P3).
    none = tool.compare([_pair("a", 1000, None, delta={"error": "Boom"})])
    assert none["partial"] is True
    assert none["sessions_measured"] == 0
    assert none["sessions_failed"] == 1


def test_the_partial_flag_counts_UNMEASURED_arms_not_only_errored_ones():
    """`sessions_failed` is documented as the EXCLUDED population, so it must
    count an arm that is unmeasured with NO error — the shape the null polarity
    exists to create (a tokenless / rejected-count / deadline-aborted call).
    Counting errors alone reported "nothing excluded" in exactly that shape."""
    mixed = tool.compare([_pair("a", 1000, 100), _pair("b", 2000, None)])
    assert mixed["sessions_failed"] == 1
    assert mixed["sessions_measured"] == 1
    assert mixed["partial"] is True


def test_the_index_row_does_not_restate_the_refuted_all_266_claim():
    """`docs/00_index.md` is the ROUTING surface — the file a lane reads instead
    of this evidence document — so a refuted claim that survives there is read
    FIRST and by more lanes than the corrected one.

    ⛔ The fix round that replaced "all 266" with "231 of the 266" in the README
    and in `search_from_s2`'s docstring missed the index row, which still said
    the correction channel was measured over all 266 items (#11385 review P2).
    The README pin reads `README.md` only, so nothing would have redded: this
    pins the index row against the SAME arithmetic — `search_from_s2` returns
    entities/points/events and `_render_search_results` has no operators
    section, so the 35 operators are never offered."""
    index = (_REPO / "docs" / "00_index.md").read_text()
    rows = [ln for ln in index.splitlines()
            if "1789" in ln and "re-emit tax" in ln]
    assert len(rows) == 1, f"expected exactly one #1789 index row, got {len(rows)}"
    flat = " ".join(rows[0].split())
    # ⛔ DELIMITERS too, not just the wording: an orphan `)` survived TWO
    # consecutive fixes of this very row and every test stayed green, because
    # replacing a parenthesised group with an em-dash construction keeps the old
    # closing delimiter unless something counts brackets (#11385 review P3).
    assert _paren_depth_ok(flat), f"unbalanced parentheses in the #1789 row: {flat}"
    assert flat.count("(") == flat.count(")"), f"unbalanced brackets: {flat}"
    assert "all 266 items offered" not in flat, "the refuted claim is back"
    assert "231 of the 266" in flat, "the corrected population is missing"
    # ⛔ The framing fix stopped at the README: the row still called this block
    # "the correction channel at its production MAXIMUM", which the README's own
    # arithmetic retracts (production's cap is PER SESSION, and larger in 6 of 7).
    # Nothing redded, because the README ban list reads README.md only (#11385 P2).
    assert "production MAXIMUM" not in flat, (
        "the removed upper-bound framing is back on the routing surface")
    assert "no room to add" not in flat, "the removed framing is back"
    assert "MAXIMALLY CORRECTION-FRIENDLY" in flat, (
        "the row must state what the input IS (maximally correction-friendly)")
    assert "the FLOOR, not a maximum" in flat, (
        "the bound that survives must be named as the floor")
    # ⛔ the bare word does not discriminate: the row also says "has no `operators`
    # section", so deleting the explanatory clause left a green test (#11385
    # review P3). Pin the CLAUSE.
    assert "35 `operators` are never offered" in flat, (
        "the row must say WHY it is 231 — the 35 never-offered operators")
    # ⛔ The routing surface's FIGURES were unpinned copies too: the row states
    # `verbatim_reemissions` 201 → 0, `corrected_by_s4` 266 → 0, `s4_items`
    # 278 → 6, the median / worst / best and the token pair. Rewriting `278 → 276`
    # in the row left the suite green — and the docstring above argues the row
    # matters *because* it is read FIRST, which applies to its numbers, not only
    # its prose (#11385 review P3). Recomputed from the primary artifact.
    _pri = json.loads(_PRIMARY.read_text())
    _rer = json.loads(_RERUN.read_text())
    _rs = _rer["summary"]
    _rlg, _rdl = (_rs["composition"]["legacy"], _rs["composition"]["delta"])
    _max_out = max(a[arm].get("out_tokens") or 0
                   for a in _rer["arms"] for arm in ("legacy", "delta"))
    _zeros = sum(1 for a in _pri["arms"]
                 if (a["delta"].get("added_by_s4") or 0) == 0)
    _ps = _pri["summary"]
    _plg = _ps["composition"]["legacy"]
    _pdl = _ps["composition"]["delta"]
    # ⛔ THE INPUT SIDE, the same class one column further out (#11385
    # post-rebase review P2): the row published `−93.78 %` with no hint that the
    # prompt got dearer, so the routing surface — read FIRST and by more lanes
    # than the document — carried the output-only reading. Derived, not typed.
    _per = {a["delta"]["prompt_tokens"] - a["legacy"]["prompt_tokens"]
            for a in _pri["arms"]}
    assert len(_per) == 1, f"the per-session input cost is not one value: {_per}"
    _per = _per.pop()
    _plg_p = sum(a["legacy"]["prompt_tokens"] for a in _pri["arms"])
    _pdl_p = _plg_p + _per * len(_pri["arms"])
    _plg_t = sum(a["legacy"]["prompt_tokens"] + a["legacy"]["out_tokens"]
                 for a in _pri["arms"])
    _pdl_t = sum(a["delta"]["prompt_tokens"] + a["delta"]["out_tokens"]
                 for a in _pri["arms"])
    for _fig in (
        f"`verbatim_reemissions` (items re-typed unchanged — the tax itself) "
        f"**{_plg['verbatim_reemissions']} → 0**",
        f"`corrected_by_s4` **{_plg['corrected_by_s4']} → 0**",
        f"`s4_items` emitted **{_plg['s4_items']} → {_pdl['s4_items']}**",
        f"median −{_ps['median_session_reduction'] * 100:.1f} %",
        f"worst session −{_ps['min_session_reduction'] * 100:.2f} %",
        f"best −{_ps['max_session_reduction'] * 100:.1f} %",
        f"**{_thousands(_ps['sum_legacy_out_tokens'])} → "
        f"{_thousands(_ps['sum_delta_out_tokens'])} output tokens, "
        f"−{_ps['token_weighted_reduction'] * 100:.2f} %**",
        # ⛔ the rest of the row's figures, the same false-pass class one
        # column over: each of these stayed green when mutated on its own
        # (#11385 review P3).
        f"`merged_items` stays {_pdl['merged_items']} against an S2 base of "
        f"{_plg['s2_items']}",
        f"**{_ps['token_weighted_reduction'] / 0.40:.1f}× the 40 % target",
        f"({_plg['added_by_s4']}/{_rlg['added_by_s4']} → "
        f"{_pdl['added_by_s4']}/{_rdl['added_by_s4']})",
        f"cannot truncate the 16 k **default** cap with a "
        f"{_max_out / 1000:.1f} k max output",
        f"−{_rs['token_weighted_reduction'] * 100:.2f} %",
        "266 of the 311 items the receipts record",
        # ⛔ the input-side clause this row now carries: the per-session cost,
        # the prompt pair and the TOTAL, so the row cannot publish the output
        # drop alone again (#11385 post-rebase review P2).
        f"+{_per} prompt tokens on every session",
        f"({_thousands(_plg_p)} → {_thousands(_pdl_p)}, "
        f"+{_thousands(_pdl_p - _plg_p)})",
        f"{_thousands(_plg_t)} → {_thousands(_pdl_t)} = "
        f"−{(1 - _pdl_t / _plg_t) * 100:.1f} %",
        "4.6× smaller than the output drop in the primary run "
        "(4.5× on the re-run)",
        "0 content/name rewrites",
        "62 lifecycle (61 `created`→`unchanged` + 1 `changed`→`unchanged`), "
        "1 `slots` (`core:code`→`dev:code`), 1 `supersedes`",
        f"{_zeros} of {len(_pri['arms'])} sessions",
    ):
        assert _fig in flat, ("the index row's figure drifted: " + _fig)


def _paren_depth_ok(text: str) -> bool:
    """A cheap, structural delimiter check for a one-row markdown table cell.

    ⛔ This exists because an unbalanced `)` survived TWO consecutive fixes in
    `docs/00_index.md` and no pin noticed: a prose edit that replaces a
    parenthesised group with an em-dash construction keeps the old delimiter
    unless something counts it (#11385 review P3)."""
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def test_the_emitted_caveats_carry_the_corrected_population_and_direction():
    """The README keeps the artifacts' one-sided `caveats[2]` sentence by saying
    "the instrument at this head emits a corrected string" — a claim about
    emitted PROSE that nothing pinned: reverting the emitted caveat to the
    refuted CEILING wording passed every other test.

    ⛔ And the artifacts' own caveat list is SHORTER than the instrument's (5 vs
    6), so a reader who trusts the artifact alone gets neither the corrected
    direction nor either population caveat. This pins the emitted strings, so
    the sentence in the README stays true (#11385 review P3)."""
    caveats = " ".join(tool.CAVEATS)
    assert "neither a ceiling nor a floor" in caveats, caveats
    assert "231 of the 266" in caveats, "the offered population is not emitted"
    assert "266 of the 311" in caveats, "the replayed-list narrowing is not emitted"
    assert "is a CEILING" not in caveats, "the refuted one-sided wording is back"
    # ⛔ the README names the SLOT (`CAVEATS[3]`) that absorbed the population
    # fact, and a wrong slot index is the exact defect that fix repaired — yet
    # flipping it to `CAVEATS[2]` left 36/36 green (#11385 review P3).
    _slots = [i for i, c in enumerate(tool.CAVEATS) if "231 of the 266" in c]
    assert len(_slots) == 1, f"the population fact must live in ONE caveat, not {_slots}"
    _slot = _slots[0]
    _readme = " ".join((_EVID / "README.md").read_text().split())
    assert f"`CAVEATS[{_slot}]`" in _readme, (_slot, "the README names a wrong slot")
    # ⛔ The COUNT was unpinned: appending a seventh entry to `tool.CAVEATS` left
    # the whole suite green, so the README's "the instrument emits **6**" had no
    # guard — and the count is exactly what a reader uses to know the artifact
    # list is short, i.e. the part that must not drift silently (#11385 review
    # P3). The artifacts carry 5 and the instrument 6. The mapping, diffed entry
    # by entry against the artifact-producing commit, is ONE ADDED plus TWO
    # EDITED — not "one added plus one rewritten", and nothing was "split out of
    # the first caveat" (there was no `CAVEATS` constant then):
    #   instrument[0] = artifact[0] EXTENDED with "(the 4 tax-surface sections)"
    #   instrument[1] NEW (the 266-of-311 narrowing)
    #   instrument[2] = artifact[1]   (identical)
    #   instrument[3] = artifact[2]   REWRITTEN (absorbed 231-of-266 / the 35
    #                                  operators, replacing the CEILING wording)
    #   instrument[4], [5] = artifact[3], [4]   (identical)
    _artifact_caveats = json.loads(_PRIMARY.read_text())["caveats"]
    assert len(_artifact_caveats) == 5, len(_artifact_caveats)
    assert len(tool.CAVEATS) == 6, len(tool.CAVEATS)
    assert len(tool.CAVEATS) == len(_artifact_caveats) + 1
    # ⛔ THE RE-RUN ARTIFACT IS A THIRD COPY, and comparing it by LENGTH alone
    # left it outside every pin: reverting ONLY `ab-20261107T024226Z.json`'s
    # `caveats[0]` to the refuted sentence stayed 37/37 GREEN (#11385 review
    # P2). Both runs are emitted by ONE instrument, so their caveat lists must
    # be EQUAL — not merely the same length.
    _rer_caveats = json.loads(_RERUN.read_text())["caveats"]
    assert _rer_caveats == _artifact_caveats, (
        "the re-run artifact's caveats must equal the primary's — a divergence "
        "means one copy was edited alone", _rer_caveats)
    # ⛔ the MAPPING, entry by entry — the note has been wrong twice (first
    # "5 + 2", then "one added plus one rewritten"), and neither the README's
    # counts nor the "ONE ADDED plus TWO EDITED" claim was enforced: `emits
    # **6**` → `**7**`, `carry **5**` → `**4**`, and reverting the
    # `CAVEATS[0]` edit all left 36/36 green (#11385 review P2).
    # ⛔ the equality ALONE cannot see the mutation it claims to close: with the
    # scoping absent, `replace` is a no-op and the equality still holds — a
    # faithful revert of `CAVEATS[0]` stayed 36/36 green (#11385 review P2).
    assert " (the 4 tax-surface sections)" in tool.CAVEATS[0], (
        "CAVEATS[0] must carry the tax-surface scoping")
    assert tool.CAVEATS[0].replace(" (the 4 tax-surface sections)", "") == (
        _artifact_caveats[0]), "CAVEATS[0] must be artifact[0] plus the scoping"

    # ⛔ THE INPUT-SIDE CORRECTION (#11385 post-rebase review P1). The equality
    # above only holds whatever the string SAYS, so a revert of this correction
    # in BOTH copies stayed green — and the artifact's copy is the one a reader
    # meets first. Pinned three ways over ALL THREE copies: each must state the
    # input cost with the DERIVED per-session figure (a regeneration that moves
    # it reds here), each must carry the same figure and scope, and each is
    # scanned for the refuted framings.
    # ⛔ CORRECTION (#11385 review P3): the previous wording here said the
    # refuted "identical prompt" claim was banned by `_REFUTED_DIRECTION_PHRASES`.
    # It was not — that tuple's only consumer is
    # `test_the_tool_docstring_and_the_readme_paraphrase_carry_the_floor_direction`,
    # which scans `search_from_s2.__doc__` and so never saw a caveat at all.
    # Deleting the entry left 37/37 GREEN. The ban is applied to the three
    # CAVEAT copies HERE, where the claim actually lived.
    _pri_c = json.loads(_PRIMARY.read_text())
    _per_c = {a["delta"]["prompt_tokens"] - a["legacy"]["prompt_tokens"]
              for a in _pri_c["arms"]}
    assert len(_per_c) == 1, f"the per-session input cost is not one value: {_per_c}"
    _per_c = _per_c.pop()
    assert [a["delta"]["prompt_tokens"] - a["legacy"]["prompt_tokens"]
            for a in json.loads(_RERUN.read_text())["arms"]] == [_per_c] * len(_pri_c["arms"])
    for _copy, _text in (("artifact caveats[0]", _artifact_caveats[0]),
                        ("re-run caveats[0]", _rer_caveats[0]),
                        ("tool.CAVEATS[0]", tool.CAVEATS[0])):
        assert f"+{_per_c} tokens in every session" in _text, (
            f"{_copy} must state the measured input cost", _text)
        assert "POSITIVE input cost" in _text, (
            f"{_copy} must say the input side is a cost, not a saving", _text)
        assert "same INPUT CONTENT" in _text, (
            f"{_copy} must scope what IS identical (content, not length)", _text)
        for _phrase in _REFUTED_DIRECTION_PHRASES:
            assert _phrase not in _text, (
                f"{_copy} still asserts a refuted framing: {_phrase!r}", _text)
    assert tool.CAVEATS[2] == _artifact_caveats[1], "CAVEATS[2] == artifact[1]"
    assert tool.CAVEATS[4:] == tuple(_artifact_caveats[3:]), (
        "the tail must be the artifacts' own entries, unchanged")
    assert "ONE ADDED plus TWO EDITED" in _readme, "the mapping sentence drifted"
    assert f"they carry **{len(_artifact_caveats)}** entries" in _readme, (
        f"the README must state {len(_artifact_caveats)} artifact caveats")
    assert f"emits **{len(tool.CAVEATS)}**" in _readme
    # ⛔ the narrowing BREAKDOWN was unpinned while its total was: 7 -> 8 and
    # 38 -> 39 both stayed green, though 266 + 7 + 38 = 311 is the document's
    # own arithmetic. The two numbers need the source receipts, so they are
    # pinned literally here (as 311 is), in both places that state them.
    assert "chain_notes 7 and link_before_create 38" in caveats, caveats
    assert "chain_notes 7 and link_before_create 38" in tool.CAVEATS[1], (
        "the narrowing caveat must name both counts")
    assert "the 35 operators" in caveats, (
        "the emitted population caveat must name the 35 never-offered operators")

    # ⛔ THE FOURTH COPY, pinned POSITIVELY (#11385 review P2). The README is
    # excluded from `_REFUTED_DIRECTION_PHRASES` because it must QUOTE the
    # refuted sentence to record the correction — but an exclusion is not a pin:
    # deleting `README.md:28-36` (the quote plus the disclosure that BOTH
    # artifacts' `caveats[0]` were hand-edited after the runs) stayed green at
    # 511 passed. The disclosure is the only record that the committed artifacts
    # were edited at all, so its ABSENCE has to red something.
    # ⛔ …and its SUBSTANCE, not only its existence (#11385 review P2): with the
    # three assertions below alone, relaxing "`caveats[0]` in BOTH artifacts" to
    # "ONE artifact" stayed 37/37 GREEN, i.e. the pin passed while the document
    # understated the hand-edit to a single artifact. The count is part of the
    # fact, so it is pinned in both directions below.
    # (`_readme` is this function's flattened README, computed once above —
    # re-reading and re-flattening it here as `_readme_flat` was a duplicate.)
    assert "`caveats[0]` in BOTH artifacts" in _readme, (
        "the disclosure must say BOTH artifacts were edited, not one")
    assert " in ONE artifact" not in _readme, (
        "the disclosure must not understate the hand-edit to one artifact")
    assert "A second artifact field was corrected after the runs" in _readme, (
        "the README must disclose that BOTH artifacts' caveats[0] were edited")
    assert "the prompt inputs are identical across arms" in _readme, (
        "the README must quote the refuted sentence it records the correction of")
    assert f"+{_per_c} tokens in every session" in _readme, (
        "the disclosure must carry the derived input cost, not just the claim")


#: The phrases the direction retraction removed from this PR's prose. They are
#: banned as a CLASS — the README copy, the index row, the emitted caveats and
#: the tool docstring are four copies of the same sentence, and each round of
#: this review found a copy the pins could not see (#11385 review P3). ⚠️ The
#: TUPLE is shared; the CONSUMERS differ — this module's `search_from_s2.__doc__`
#: scan below covers the docstring only, and the three caveat copies are scanned
#: in `test_the_emitted_caveats_carry_the_corrected_population_and_direction`.
#: A reader should not assume one consumer covers every copy (that assumption is
#: exactly the defect #11385 review P3 found).
_REFUTED_DIRECTION_PHRASES = (
    "cannot do better on that channel",
    "cannot do better on this channel",
    "production maximum",
    "production MAXIMUM",
    "no room to add",
    "than it will usually find in production",
    "all 266 items offered",
    # ⛔ The input-side claim, banned over the CAVEAT copies — `_REFUTED_
    # DIRECTION_PHRASES`'s own consumer scans only `search_from_s2.__doc__`, so
    # adding the entry there alone enforced nothing (#11385 review P3); it is
    # applied to all three caveat copies in
    # `test_the_emitted_caveats_carry_the_corrected_population_and_direction`.
    # It is NOT applied to the README on purpose: `README.md:30` has to QUOTE the
    # refuted sentence to record the correction, so a ban there would red the one
    # document that must cite it. ⛔ But an exclusion is not a pin: nothing
    # required that record to EXIST, so deleting `README.md:28-36` — the quote AND
    # the disclosure that both artifacts' `caveats[0]` were hand-edited after the
    # runs — left the suite green (511 passed; #11385 review P2). The fourth copy
    # is therefore pinned POSITIVELY, in
    # `test_the_emitted_caveats_carry_the_corrected_population_and_direction`.
    "the prompt inputs are identical",
)


def test_the_tool_docstring_and_the_readme_paraphrase_carry_the_floor_direction():
    """The LAST two unguarded copies of the corrected direction sentence.

    Round 19 fixed the README bullet and the index row and left two copies
    outside every pin's reach (#11385 review P3):

    1. `tools/s4_reeemit_tax.py`'s `search_from_s2` docstring states the
       direction in its own words (``… that channel cannot emit FEWER than this
       block did``). No test read a docstring, so re-introducing
       ``cannot do better on that channel`` there — the exact ambiguity the
       retraction exists to remove — would have stayed green.
    2. `README.md`'s closing paraphrase (``So production cannot emit LESS of the
       two channels this section names than the delta arm did``) is the same
       comparison stated once more, outside the banned-phrase block.

    A ban list over one document is a pin on one document, not on the claim —
    so this asserts the floor DIRECTION in each copy and the refuted framings in
    none of them. Non-vacuity: on the pre-fix tool docstring the banned list is
    clean but the direction sentence is asserted separately, so editing either
    copy's direction reds this test."""
    flat_doc = " ".join((tool.search_from_s2.__doc__ or "").split())
    assert flat_doc, "search_from_s2 lost its docstring — re-point this pin"
    assert "cannot emit FEWER than this block did" in flat_doc, (
        "the tool docstring must state the floor direction, not a ceiling")
    for _phrase in _REFUTED_DIRECTION_PHRASES:
        assert _phrase not in flat_doc, (
            f"a refuted framing is back in tools/s4_reeemit_tax.py: {_phrase!r}")

    flat_readme = " ".join((_EVID / "README.md").read_text().split())
    assert ("So production cannot emit LESS of the two channels this section "
            "names than" in flat_readme), (
        "the README's closing paraphrase must keep the LESS direction")
    assert "cannot emit MORE of the two channels" not in flat_readme, (
        "the paraphrase was flipped to a ceiling")
