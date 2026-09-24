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

The **removal must survive ``execute_embed``'s MINT-BEFORE-WIRE pre-pass** —
the four defects the first review cycle found and verified end-to-end (each
reproduced against the pre-fix code):

- a MITIGATES whose only reference to a discarded point is its ``target_edge``
  kept the operator, and the pre-pass re-minted the discarded text as a NEW
  Point (``_operator_endpoint_text`` read only ``target``);
- an operator endpoint naming a *removed entity* no longer hit #2552's
  ``emitted_entity_names`` guard, so a claim Point was fabricated from the
  participant name;
- discarding one of two identical-content items pruned the SURVIVOR's operator
  (the prune matched text, not the surviving endpoint set);
- the Layer-1 guard was per-pass, so an entity the S2 pass removed and S4 later
  referenced reached ``validate_payload_dict`` → ``ok=False`` → the whole
  session 422s. The union pass now consumes the S2 pass's ``removal_pool``.

Plus two drift pins (the ``SECTIONS`` table and ``_norm`` are deliberate copies
of the extractor's, made non-silent) and a ``check_batch`` count fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import vet_gate as vg
from tortoise.commit_schema import validate_payload_dict

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


def _discard_matching(text: str):
    return lambda cands, _story: {"verdicts": [
        {"id": c["id"], "outcome": vg.DISCARD} for c in cands
        if c["text"] == text]}


def _payload_of(embed_list: dict):
    """Run the downstream S5 embedder over a list — the ONLY way to prove a
    DISCARD actually reached the payload (or was re-minted into it)."""
    import copy

    from tortoise.extractor_v2 import execute_embed
    res = execute_embed(copy.deepcopy(embed_list), {}, session_id="vet-test")
    return res["payload"], res


def _assert_layer1_invariant(embed_list: dict) -> None:
    """A fast pre-check of the referential half of the Layer-1 invariant:
    every surviving point's ``about_entities`` and typed ``slots`` name an
    emitted entity. The CANONICAL gate is
    ``commit_schema.validate_payload_dict`` over the built payload — see
    ``test_cross_pass_reference_restores_entity_and_payload_validates``."""
    def _bare(kind: object) -> str:
        return str(kind or "").strip().rsplit(":", 1)[-1].lower()

    ents = [e for e in embed_list.get("entities") or [] if isinstance(e, dict)]
    names = {str(e.get("name")) for e in ents}
    keys = {(str(e.get("name")), _bare(e.get("kind"))) for e in ents}
    for pt in embed_list.get("points") or []:
        for a in (pt.get("about_entities") or []):
            assert a in names, (
                f"Layer-1 violation: point references {a!r} which is not an "
                "emitted entity — this 422s the whole session")
        slots = pt.get("slots")
        if isinstance(slots, dict):
            for role, refs in slots.items():
                if role == "event" or not isinstance(refs, list):
                    continue
                for r in refs:
                    if isinstance(r, dict) and r.get("name"):
                        key = (r["name"], _bare(r.get("kind")))
                        assert key in keys, (
                            f"Layer-1 violation: slot {role} {key!r} does not "
                            "resolve to an emitted (name, kind) entity")


# ── vocabulary ─────────────────────────────────────────────────────────────

def test_vocabulary_is_the_owner_adopted_O4_set():
    assert {"KEEP", "NOOP", "DISCARD", "MERGE"} == vg.OUTCOMES
    assert vg.RENARRATE in vg.BATCH_OUTCOMES
    # ⛔ A4 (§16.2): MERGE-INTO-EXISTING is the uncorrected VET/S3 circularity
    # and must never be emitted from this position.
    assert "MERGE-INTO-EXISTING" not in vg.OUTCOMES
    assert "MERGE-INTO-EXISTING" not in vg.BATCH_OUTCOMES


# ── the shape/normaliser copies are pinned to the extractor's ──────────────

def test_section_table_matches_extractor():
    """``vet_gate.SECTIONS`` is a deliberate second copy (it cannot import
    ``extractor_v2`` — that would cycle). This test is what keeps the copy from
    drifting SILENTLY: a new or renamed section/field in the extractor fails
    here instead of passing unvetted with no warning."""
    from tortoise import extractor_v2 as v2
    assert [(s, kf, fam) for s, _t, kf, fam in vg.SECTIONS] == \
        list(v2._CLASSIFY_SECTIONS)


def test_norm_matches_extractor_norm():
    """The two normalisers meet on operator endpoints and removed-item text.
    ``.casefold()`` vs ``.lower()`` diverge on ('Straße', 'İ') — a divergence
    here makes a prune silently miss or hit."""
    from tortoise import extractor_v2 as v2
    for value in ("Straße", "İstanbul", "  A\n B\tC  ", "", None, 7,
                  "Mixed CASE text"):
        assert vg._norm(value) == v2._norm(value), value


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


def test_unreferenced_entity_is_removed_and_non_vet_keys_preserved():
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
    # this operator's endpoints are the surviving point, so it is NOT pruned
    assert len(new["operators"]) == 1
    _assert_layer1_invariant(new)


# ── the removal must survive execute_embed's MINT-BEFORE-WIRE pre-pass ─────

def test_discarded_target_edge_is_pruned_and_not_resurrected():
    """Verified defect: ``_operator_endpoint_text`` read only ``target`` while
    ``execute_embed`` reads ``target`` **or** ``target_edge`` — so a MITIGATES
    whose only reference to a discarded point was its ``target_edge`` kept the
    operator, and the mint pre-pass put the discarded text back as a NEW
    Point. The audit surface said "discarded" while the payload shipped it."""
    el = {"entities": [], "events": [],
          "points": [{"content": "drop me", "pointKind": "statement"},
                     {"content": "survivor", "pointKind": "statement"},
                     {"content": "risk", "pointKind": "statement"}],
          "operators": [{"src": "survivor", "dst": "risk",
                         "op_type": "MITIGATES",
                         "target_edge": {"src": "drop me", "dst": "risk"}}]}
    out = vg.vet_candidates(el, narrative="n",
                            arbiter=_discard_matching("drop me"))
    new, warnings = vg.apply_vet(el, out["decisions"])
    assert [p["content"] for p in new["points"]] == ["survivor", "risk"]
    assert new["operators"] == []
    assert any("pruned 1 operator" in w for w in warnings)
    payload, _res = _payload_of(new)
    assert "drop me" not in [p["content"] for p in payload["points"]]


def test_removed_entity_endpoint_does_not_fabricate_a_point():
    """Verified defect: with the entity gone, ``emitted_entity_names`` no
    longer contains it, so #2552's guard did not fire and the mint pre-pass
    fabricated a claim Point out of the participant name. The operator must be
    pruned instead."""
    el = {"entities": [{"name": "pytest", "kind": "core:tool"}],
          "events": [],
          "points": [{"content": "real claim", "pointKind": "statement"}],
          "operators": [{"src": "pytest", "dst": "real claim",
                         "op_type": "IMPL"}]}
    out = vg.vet_candidates(
        el, narrative="n",
        arbiter=lambda c, s: {"verdicts": [
            {"id": x["id"], "outcome": vg.DISCARD} for x in c
            if x["section"] == "entities"]})
    new, warnings = vg.apply_vet(el, out["decisions"])
    assert new["entities"] == []
    assert new["operators"] == []
    assert any("pruned 1 operator" in w for w in warnings)
    payload, _res = _payload_of(new)
    assert [p["content"] for p in payload["points"]] == ["real claim"]


def test_discarding_one_of_two_identical_items_keeps_the_edge():
    """Verified defect: the prune matched removed item TEXT, so discarding one
    of two identical-content points pruned the SURVIVOR's operator — an edge
    lost although the endpoint still resolved."""
    el = {"entities": [], "events": [],
          "points": [{"content": "same text", "pointKind": "statement"},
                     {"content": "same text", "pointKind": "statement"},
                     {"content": "other", "pointKind": "statement"}],
          "operators": [{"src": "same text", "dst": "other",
                         "op_type": "IMPL"}]}
    decisions = vg.vet_candidates(el, narrative="n")["decisions"]
    first = next(i for i, d in decisions.items()
                 if d["section"] == "points" and d["index"] == 0)
    decisions[first] = {"outcome": vg.DISCARD}
    new, _warnings = vg.apply_vet(el, decisions)
    assert [p["content"] for p in new["points"]] == ["same text", "other"]
    assert len(new["operators"]) == 1, (
        "the surviving twin still provides the endpoint — the edge must stay")


def test_cross_pass_reference_restores_entity_and_payload_validates():
    """Verified defect (the strongest one): the Layer-1 guard is per-pass, so
    an entity the S2 pass removed and S4 later referenced reached
    ``validate_payload_dict`` → ``ok=False`` → the WHOLE session 422s. The
    union pass now consumes the S2 pass's ``removal_pool`` and restores it.
    The canonical gate (``validate_payload_dict``) is what this asserts."""
    from tortoise.extractor_v2 import merge_embed_lists
    s2 = {"entities": [{"name": "pytest", "kind": "core:tool"},
                       {"name": "the plan doc", "kind": "core:document"}],
          "events": [],
          "points": [{"content": "the plan doc is stale",
                      "pointKind": "statement",
                      "about_entities": ["the plan doc"]}],
          "operators": []}
    out = vg.vet_candidates(
        s2, narrative="n",
        arbiter=lambda c, st: {"verdicts": [
            {"id": x["id"], "outcome": vg.DISCARD} for x in c
            if x["text"] == "pytest"]})
    s2_vetted, _w = vg.apply_vet(s2, out["decisions"])
    assert [e["name"] for e in s2_vetted["entities"]] == ["the plan doc"]
    pool = vg.removal_pool(s2, s2_vetted)
    assert set(pool["removed_entities"]) == {"pytest"}

    # S4's gap-fill references the entity the S2 pass discarded.
    s4 = {"entities": [], "events": [], "operators": [],
          "points": [{"content": "pytest is slow", "pointKind": "statement",
                      "about_entities": ["pytest"]}]}
    union = merge_embed_lists(s2_vetted, s4)
    union_decisions = vg.vet_candidates(union, narrative="n")["decisions"]
    final, warnings = vg.apply_vet(union, union_decisions, prior=pool)

    assert "pytest" in [e["name"] for e in final["entities"]], (
        "a restorable cross-pass reference must be restored (fail-open)")
    assert any("restored" in w for w in warnings)
    _assert_layer1_invariant(final)
    payload, _res = _payload_of(final)
    l1, _model = validate_payload_dict(payload)
    assert l1.ok, l1.errors


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


def test_check_batch_counts_what_vet_candidates_saw():
    """The batch signal must not count raw list entries: an empty-text entity
    is not a candidate, and counting it made ``coverage_suspect`` stay False
    on the one case it exists to flag (prose in, nothing out)."""
    el = {"entities": [{"name": "", "kind": "core:other"}],
          "points": [], "events": [], "operators": []}
    batch = vg.check_batch(el, "One. Two. Three.")
    assert batch["candidates"] == 0
    assert batch["coverage_suspect"] is True
    assert vg.vet_candidates(el, narrative="One. Two. Three.")["stats"][
        "candidates"] == batch["candidates"]


def test_functions_are_total_on_malformed_shapes():
    """The module's contract is fail-open on a bad shape, not an exception —
    a direct caller outside ``_run_vet_pass`` must not raise either."""
    for bad in (None, {"entities": 5}, {"points": "not a list"},
                {"entities": [None, 7, "x"]}):
        out = vg.vet_candidates(bad, narrative="n")
        assert out["stats"]["candidates"] == 0
        assert vg.check_batch(bad, "one. two.")["candidates"] == 0
        new, _ = vg.apply_vet(bad, {})
        assert isinstance(new, dict)
        vg.audit_candidates(bad, {})


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
