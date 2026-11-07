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


def test_search_block_is_reconstructed_from_the_s2_list(tmp_path):
    """Both arms must see the SAME search results, and they must be the S2
    items (what production S4 sees after S2 reaches the graph) — not an empty
    block, which is the degraded-search condition."""
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
