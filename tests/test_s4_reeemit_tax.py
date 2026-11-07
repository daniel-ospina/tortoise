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
    tool.run_arm(model, sess, "legacy")
    tool.run_arm(model, sess, "delta")
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
        assert s["sessions_measured"] == s["sessions_attempted"] == len(rows)
        # the published totals ARE the sums of the rows
        assert s["sum_legacy_out_tokens"] == sum(
            r["legacy_out_tokens"] for r in rows), path.name
        assert s["sum_delta_out_tokens"] == sum(
            r["delta_out_tokens"] for r in rows), path.name
        # …and the headline is that ratio, not a number typed beside it
        assert round(1 - s["sum_delta_out_tokens"] / s["sum_legacy_out_tokens"],
                     4) == s["token_weighted_reduction"], path.name
        # the per-session statistics ARE the rows' statistics
        reductions = sorted(
            1 - r["delta_out_tokens"] / r["legacy_out_tokens"] for r in rows)
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
        for r in rows:
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


def test_a_raised_retry_makes_the_arm_unmeasured():
    """⛔ A FOURTH path to a partial total, and the only one no counter sees: a
    call that RAISED never reaches `_accumulate_call_cost` at all, so it is in
    neither `cost["completion_tokens"]` nor any `calls_without_*` counter —
    `attempts` is its only trace. `stats["attempts"]` is the true running attempt
    count and `cost["calls"]` counts the attempts that actually contributed, so a
    gap between them means part of the spend is missing and `cost` is a PARTIAL
    sum. An earlier revision merely *documented* this gap while still returning
    the partial sum with a total-looking basis (#11385 review P2)."""
    stats = {"cost": {"calls": 1, "completion_tokens": 5000}, "attempts": 2}
    tokens, basis = tool.arm_out_tokens(stats)
    assert tokens is None, "1 of 2 attempts contributed — not the arm's total"
    assert "1 of 2 attempt(s)" in basis
    # …and the healthy case (every attempt contributed) still publishes the total
    stats = {"cost": {"calls": 2, "completion_tokens": 9000}, "attempts": 2}
    assert tool.arm_out_tokens(stats) == (9000, "stats.cost.completion_tokens")
