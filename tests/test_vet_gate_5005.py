"""S2.2 VET — the adversarial selection gate (#5005).

What this file pins, and why each is load-bearing:

- the module exists and carries the **O4 vocabulary** (and NOT the A4-defective
  ``MERGE-INTO-EXISTING``);
- **fail-open is structural** — no arbiter, a raising arbiter, and an unknown
  outcome all keep every candidate;
- ``apply_vet`` removes **only** an explicit ``DISCARD`` (the P2 from the
  verify cycle);
- **the Layer-1 guard** — a referenced entity proposed for discard is
  downgraded to KEEP, because ``commit_schema`` requires
  ``about_entities ⊆ entities`` and a removal would 422 the whole session
  (the P0 from the verify cycle);
- the pipeline wiring: with ``TORTOISE_VET=1`` a discarded candidate never
  reaches the classifier/resolver/embedder; with the flag off the gate is
  off-path.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import vet_gate as vg

# ── helpers ────────────────────────────────────────────────────────────────

def _cand(entity: str, point: str | None = None, kind: str = "core:other") -> dict:
    el: dict = {"entities": [{"name": entity, "kind": kind}],
                "points": [], "events": [], "operators": []}
    if point:
        el["points"].append({"content": point, "pointKind": "statement",
                             "about_entities": [entity]})
    return el


def _discard_all(cands, _story):
    return {"verdicts": [{"id": c["id"], "outcome": vg.DISCARD,
                          "rule_id": "test.rule", "reason": "test"}
                         for c in cands]}


def _assert_layer1_invariant(embed_list: dict) -> None:
    """The exact invariant ``commit_schema.validate_layer1`` enforces:
    every surviving point's ``about_entities`` names an emitted entity."""
    names = {str(e.get("name")) for e in embed_list.get("entities") or []
             if isinstance(e, dict)}
    for pt in embed_list.get("points") or []:
        for a in (pt.get("about_entities") or []):
            assert a in names, (
                f"Layer-1 violation: point references {a!r} which is not an "
                "emitted entity — this 422s the whole session")


# ── vocabulary ─────────────────────────────────────────────────────────────

def test_vocabulary_is_the_owner_adopted_O4_set():
    assert {"KEEP", "NOOP", "DISCARD", "MERGE"} == vg.OUTCOMES
    assert vg.RENARRATE in vg.BATCH_OUTCOMES
    # ⛔ A4 (§16.2): MERGE-INTO-EXISTING is the uncorrected VET/S3 circularity
    # and must never be emitted from this position.
    assert "MERGE-INTO-EXISTING" not in vg.OUTCOMES
    assert "MERGE-INTO-EXISTING" not in vg.BATCH_OUTCOMES


# ── fail-open ──────────────────────────────────────────────────────────────

def test_no_arbiter_keeps_everything_and_reports_coverage():
    el = _cand("the plan doc", "the plan doc is stale")
    out = vg.vet_candidates(el, narrative="One thing. Two things.")
    assert {d["outcome"] for d in out["decisions"].values()} == {vg.KEEP}
    assert out["stats"]["arbiter"] == "none"
    assert out["stats"]["discarded"] == 0
    assert out["batch"]["narrative_statements"] == 2
    assert out["batch"]["candidates"] == 2
    assert out["batch"]["coverage_suspect"] is False
    new, warnings = vg.apply_vet(el, out["decisions"])
    assert new["entities"] == el["entities"]      # untouched
    assert warnings == []


def test_arbiter_raising_fails_open_and_warns():
    el = _cand("the plan doc")

    def boom(_cands, _story):
        raise RuntimeError("vendor down")

    out = vg.vet_candidates(el, narrative="n", arbiter=boom)
    assert {d["outcome"] for d in out["decisions"].values()} == {vg.KEEP}
    assert any("fail-open" in w for w in out["warnings"])
    new, _ = vg.apply_vet(el, out["decisions"])
    assert new["entities"] == el["entities"]


def test_unknown_outcome_and_missing_verdict_fail_open():
    el = _cand("the plan doc")

    def weird(cands, _story):
        # first an out-of-vocabulary outcome, then nothing for candidate 2
        return {"verdicts": [{"id": cands[0]["id"], "outcome": "BOGUS"}]}

    out = vg.vet_candidates(el, narrative="n", arbiter=weird)
    assert {d["outcome"] for d in out["decisions"].values()} == {vg.KEEP}
    new, warnings = vg.apply_vet(el, out["decisions"])
    assert new["entities"] == el["entities"]
    assert warnings == []


def test_apply_vet_removes_only_explicit_discard():
    el = _cand("the plan doc")
    iid = next(iter(vg.vet_candidates(el)["decisions"]))
    for outcome in (vg.KEEP, vg.NOOP, vg.MERGE, "UNKNOWN", ""):
        new, warnings = vg.apply_vet(el, {iid: {"outcome": outcome}})
        assert new["entities"] == el["entities"], outcome
        assert warnings == [], outcome
    # and a decision map that does not mention the item at all
    new, warnings = vg.apply_vet(el, {})
    assert new["entities"] == el["entities"]


# ── the Layer-1 guard (the P0) ─────────────────────────────────────────────

def test_referenced_entity_discard_is_downgraded_to_keep():
    el = _cand("the plan doc", "the plan doc is stale")
    out = vg.vet_candidates(el, narrative="n",
                            arbiter=lambda c, s: {
                                "verdicts": [{"id": x["id"],
                                              "outcome": vg.DISCARD}
                                             for x in c
                                             if x["section"] == "entities"]})
    new, warnings = vg.apply_vet(el, out["decisions"])
    assert [e["name"] for e in new["entities"]] == ["the plan doc"]
    assert any("Layer-1" in w for w in warnings)
    assert any("downgraded" in w for w in warnings)
    _assert_layer1_invariant(new)


def test_unreferenced_entity_is_removed_and_operator_pruned():
    el = {"entities": [{"name": "the unused thing", "kind": "core:other"}],
          "points": [{"content": "keep me", "pointKind": "statement"}],
          "events": [], "operators": [
              {"src": "keep me", "dst": "keep me", "op_type": "IMPL"}],
          "chain_notes": [{"note": "preserved"}]}
    out = vg.vet_candidates(
        el, narrative="n",
        arbiter=lambda c, s: {"verdicts": [
            {"id": x["id"], "outcome": vg.DISCARD} for x in c
            if x["section"] == "entities"]})
    new, _warnings = vg.apply_vet(el, out["decisions"])
    assert new["entities"] == []
    # non-VET keys survive the rewrite (the operators-preservation fix)
    assert new["chain_notes"] == [{"note": "preserved"}]
    assert len(new["operators"]) == 1        # endpoints survived
    _assert_layer1_invariant(new)


def test_discarded_point_prunes_its_operator():
    el = {"entities": [], "events": [], "points": [
              {"content": "drop me", "pointKind": "statement"},
              {"content": "keep me", "pointKind": "statement"}],
          "operators": [
              {"src": "drop me", "dst": "drop me", "op_type": "IMPL"},
              {"src": "keep me", "dst": "keep me", "op_type": "NAND"}]}
    out = vg.vet_candidates(
        el, narrative="n",
        arbiter=lambda c, s: {"verdicts": [
            {"id": x["id"], "outcome": vg.DISCARD} for x in c
            if x["text"] == "drop me"]})
    new, warnings = vg.apply_vet(el, out["decisions"])
    assert [p["content"] for p in new["points"]] == ["keep me"]
    assert [o["src"] for o in new["operators"]] == ["keep me"]
    assert any("pruned 1 operator" in w for w in warnings)


def test_discard_everything_still_satisfies_layer1():
    el = {"entities": [{"name": "the plan doc", "kind": "core:document"}],
          "points": [{"content": "the plan doc is stale",
                      "pointKind": "statement",
                      "about_entities": ["the plan doc"]}],
          "events": [], "operators": [
              {"src": "the plan doc is stale", "dst": "x", "op_type": "IMPL"}]}
    out = vg.vet_candidates(el, narrative="n", arbiter=_discard_all)
    new, _warnings = vg.apply_vet(el, out["decisions"])
    assert new["entities"] == [] and new["points"] == []
    assert new["operators"] == []
    _assert_layer1_invariant(new)


# ── the batch signal + the audit instrument ────────────────────────────────

def test_coverage_suspect_only_on_the_unambiguous_loss():
    empty = {"entities": [], "points": [], "events": [], "operators": []}
    b = vg.check_batch(empty, "The narrative stated three separate things.")
    assert b["coverage_suspect"] is True
    assert b["candidates"] == 0
    # a narrative-less batch is NOT a coverage failure
    assert vg.check_batch(empty, "")["coverage_suspect"] is False


def test_arbiter_batch_renarrate_is_recorded_not_acted_on():
    el = _cand("the plan doc")
    out = vg.vet_candidates(
        el, narrative="n",
        arbiter=lambda c, s: {"verdicts": [], "batch": {
            "outcome": "RENARRATE", "reason": "abstraction too low"}})
    assert out["batch"]["outcome"] == vg.RENARRATE
    assert out["batch"]["outcome_reason"] == "abstraction too low"
    # recorded, never applied: the candidates are untouched
    new, _ = vg.apply_vet(el, out["decisions"])
    assert new["entities"] == el["entities"]


def test_audit_candidates_renders_rule_and_reason_per_candidate():
    el = _cand("the plan doc", "the plan doc is stale")
    out = vg.vet_candidates(el, narrative="n", arbiter=_discard_all)
    report = vg.audit_candidates(el, out["decisions"])
    lines = report.splitlines()
    assert lines[0] == "section\toutcome\trule_id\ttext\treason"
    assert len(lines) == 1 + out["stats"]["candidates"]
    assert all("\tDISCARD\t" in ln for ln in lines[1:])
    assert all("test.rule" in ln for ln in lines[1:])


def test_every_discard_carries_a_counterfactual():
    el = _cand("the plan doc")
    out = vg.vet_candidates(el, narrative="n", arbiter=_discard_all)
    for d in out["decisions"].values():
        if d["outcome"] == vg.DISCARD:
            assert d["counterfactual"], "a silent discard is forbidden"
            assert "would have shipped" in d["counterfactual"]


# ── pipeline wiring ────────────────────────────────────────────────────────

class _Model:
    """Minimal v2 pipeline model: S1 story, S2 list, empty S4."""

    last_finish_reason = "stop"

    def complete(self, *, system, user, max_tokens=None):
        if "STORY SUMMARIZER" in system:
            return "A narrative that states one thing."
        if "GAP REVIEWER" in system:
            return ('{"entities": [], "events": [], "operators": [], '
                    '"points": []}')
        return ('{"entities": ['
                '{"name": "the plan doc", "kind": "core:document"}, '
                '{"name": "pytest", "kind": "core:tool"}], '
                '"points": [{"content": "the plan doc is stale", '
                '"pointKind": "statement", '
                '"about_entities": ["the plan doc"]}], '
                '"events": [], "operators": []}')


def _conv():
    return [{"role": "user", "content": "the plan doc is stale; pytest ran"}]


def _discard(cands, _story):
    """Discard every entity whose name is exactly `pytest`."""
    return {"verdicts": [{"id": c["id"], "outcome": vg.DISCARD,
                          "rule_id": "test.entity", "reason": "unreferenced"}
                         for c in cands if c["text"] == "pytest"]}


def test_flag_off_is_off_path(monkeypatch):
    monkeypatch.delenv("TORTOISE_VET", raising=False)
    from tortoise import extractor_v2 as v2
    out = v2.extract_session_v2(_Model(), _conv())
    assert out["vet"] == {"enabled": False, "s2": {}, "union": {}}
    names = [e["name"] for e in out["embed_list"]["entities"]]
    assert "pytest" in names and "the plan doc" in names


def test_flag_on_removes_discarded_candidate_before_the_tail(monkeypatch):
    monkeypatch.setenv("TORTOISE_VET", "1")
    from tortoise import extractor_v2 as v2
    out = v2.extract_session_v2(_Model(), _conv(), vet_arbiter=_discard)
    assert out["vet"]["enabled"] is True
    # the S2 pass is where `pytest` is removed (S4 adds nothing here, so the
    # union pass sees it already gone) — the authoritative union pass re-checks
    # whatever the final list holds.
    assert out["vet"]["s2"]["stats"]["discarded"] == 1
    assert out["vet"]["union"]["stats"]["discarded"] == 0
    names = [e["name"] for e in out["embed_list"]["entities"]]
    # the DISCARD is applied: `pytest` never reaches the classifier, the
    # resolver or the embedder (it is absent from the final embed list).
    assert names == ["the plan doc"]
    assert any("pytest" in w for w in out["warnings"])
    _assert_layer1_invariant(out["embed_list"])


def test_flag_on_without_arbiter_changes_nothing(monkeypatch):
    monkeypatch.setenv("TORTOISE_VET", "1")
    from tortoise import extractor_v2 as v2
    out = v2.extract_session_v2(_Model(), _conv())
    assert out["vet"]["enabled"] is True
    assert out["vet"]["union"]["stats"]["arbiter"] == "none"
    assert out["vet"]["union"]["stats"]["discarded"] == 0
    names = [e["name"] for e in out["embed_list"]["entities"]]
    assert "pytest" in names and "the plan doc" in names
