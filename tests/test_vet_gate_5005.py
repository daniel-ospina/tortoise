"""S2.2 VET — the adversarial selection gate (#5005).

What this file pins, and why each is load-bearing:

- the module exists and carries the **O4 vocabulary** (and NOT the A4-defective
  ``MERGE-INTO-EXISTING``);
- **fail-open is structural** — no arbiter, a raising arbiter, and an unknown
  outcome all keep every candidate;
- ``apply_vet`` removes **only** an explicit ``DISCARD`` (the same-pass
  Layer-1 guard);
- **the Layer-1 guard** — a referenced entity proposed for discard is
  downgraded to KEEP, because ``commit_schema`` requires
  ``about_entities ⊆ entities`` and a removal would 422 the whole session
  (the same-pass referential guard);
- the pipeline wiring: with ``TORTOISE_VET=1`` a discarded candidate never
  reaches the classifier/resolver/embedder; with the flag off the gate is
  off-path.

The **removal must survive ``execute_embed``'s MINT-BEFORE-WIRE pre-pass**.
Each defect class below is pinned by the named test:

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
    drifting SILENTLY: a new or renamed section/kind-field in the extractor
    fails here instead of passing unvetted with no warning."""
    from tortoise import extractor_v2 as v2
    assert [(s, kf, fam) for s, _t, kf, fam in vg.SECTIONS] == \
        list(v2._CLASSIFY_SECTIONS)


def test_text_field_matches_extractor_item_identity():
    """The extractor's section table carries no text field, so the pin above
    cannot see a renamed ``content``/``name`` — and that rename would make
    ``_item_text`` empty for every point/event, i.e. VET would silently gate
    nothing. Pin VET's text rule to the extractor's OWN item-identity function,
    including the item that carries BOTH keys (the case a section-specific rule
    gets wrong)."""
    from tortoise import extractor_v2 as v2
    probes = ({"name": "NameVal", "content": "ContentVal"},
              {"name": "NameVal"}, {"content": "ContentVal"})
    for section, _text_field, _kf, _fam in vg.SECTIONS:
        for probe in probes:
            key = v2._classify_item_id(section, probe)
            text = vg._item_text(section, probe)
            if text:
                assert text.lower() in key, (
                    f"{section}: VET reads {text!r}, the extractor's item "
                    f"identity reads {key!r}")


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
    """``_operator_endpoint_text`` reads a MITIGATES' target as ``target`` **or**
    ``target_edge``, exactly as ``execute_embed`` does — so an operator whose
    ONLY reference to a discarded point is its ``target_edge`` is pruned. A
    read of ``target`` alone keeps the operator, and the mint pre-pass then puts
    the discarded text back as a NEW Point — the audit surface saying
    "discarded" while the payload ships it."""
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
    """With the entity gone from the output, ``emitted_entity_names`` does not
    contain it, so #2552's guard cannot fire and the mint pre-pass would
    fabricate a claim Point out of a participant name. The operator is pruned
    instead."""
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
    """The prune fires only when no surviving item provides the endpoint, so
    discarding one of two identical-content points keeps the SURVIVOR's
    operator — its endpoint still resolves, and the edge is not lost."""
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


def test_target_precedence_matches_execute_embed_no_over_prune():
    """execute_embed resolves a MITIGATES target as ``target or target_edge``
    — the *first* one present, not a union. An operator carrying both (where
    ``target_edge`` names a discarded point but ``target`` is the one the
    embedder honours) must NOT be pruned on a field execute_embed ignores."""
    el = {"entities": [], "events": [],
          "points": [{"content": "drop me", "pointKind": "statement"},
                     {"content": "a", "pointKind": "statement"},
                     {"content": "b", "pointKind": "statement"}],
          "operators": [{"src": "a", "dst": "b", "op_type": "MITIGATES",
                         "target": {"src": "a", "dst": "b"},
                         "target_edge": {"src": "drop me", "dst": "b"}}]}
    out = vg.vet_candidates(el, narrative="n",
                            arbiter=_discard_matching("drop me"))
    new, _warnings = vg.apply_vet(el, out["decisions"])
    assert [p["content"] for p in new["points"]] == ["a", "b"]
    assert len(new["operators"]) == 1
    payload, _res = _payload_of(new)
    assert "drop me" not in [p["content"] for p in payload["points"]]


def test_target_is_ignored_for_non_mitigates_operators():
    """``execute_embed`` reads a target ONLY under ``if op_type == MITIGATES``.
    Reading it for every operator dropped a valid edge on a field the embedder
    never looks at — an IMPL carrying a stray ``target`` naming a
    discarded point was pruned)."""
    for op_type in ("IMPL", "NAND"):
        el = {"entities": [], "events": [],
              "points": [{"content": "drop me", "pointKind": "statement"},
                         {"content": "a", "pointKind": "statement"},
                         {"content": "b", "pointKind": "statement"}],
              "operators": [{"src": "a", "dst": "b", "op_type": op_type,
                             "target": {"src": "drop me", "dst": "b"}}]}
        out = vg.vet_candidates(el, narrative="n",
                                arbiter=_discard_matching("drop me"))
        new, _warnings = vg.apply_vet(el, out["decisions"])
        assert len(new["operators"]) == 1, (
            f"{op_type}: the embedder ignores `target`, so the edge must survive")
        payload, _res = _payload_of(new)
        assert "drop me" not in [p["content"] for p in payload["points"]]


def test_empty_text_item_id_cannot_be_used_to_discard():
    """``vet_candidates`` never emits an id for an empty-text item, so no
    legitimate verdict can address one. The id space must not be a way to
    remove something the arbiter was never shown."""
    el = {"entities": [], "events": [],
          "points": [{"content": "real", "pointKind": "statement"},
                     {"content": "", "pointKind": "statement"}],
          "operators": []}
    new, _warnings = vg.apply_vet(el, {"points:1:": {"outcome": vg.DISCARD}})
    assert len(new["points"]) == 2


def test_non_string_endpoint_is_not_left_to_be_re_minted():
    """``execute_embed`` str()-coerces every endpoint, so a non-string endpoint
    (an LLM can emit ``src: 42``) is recognised under its coerced form. Skipping
    it keeps the operator, and the mint pre-pass then adds ``42`` to the payload
    as a point."""
    el = {"entities": [], "events": [],
          "points": [{"content": 42, "pointKind": "statement"},
                     {"content": "keep", "pointKind": "statement"}],
          "operators": [{"src": 42, "dst": "keep", "op_type": "IMPL"}]}
    out = vg.vet_candidates(el, narrative="n",
                            arbiter=_discard_matching("42"))
    new, warnings = vg.apply_vet(el, out["decisions"])
    assert [p["content"] for p in new["points"]] == ["keep"]
    assert new["operators"] == []
    assert any("pruned 1 operator" in w for w in warnings)
    payload, _res = _payload_of(new)
    assert [p["content"] for p in payload["points"]] == ["keep"]


def test_cross_pass_reference_with_different_spelling_is_reconciled():
    """``validate_layer1`` compares ``about_entities`` by EXACT string, so
    restoring ``pytest`` while S4's point names ``PyTest`` left the 422 in
    place while the warning claimed restoration. The reference's
    spelling is reconciled to the restored name."""
    from tortoise.extractor_v2 import merge_embed_lists
    s2 = {"entities": [{"name": "pytest", "kind": "core:tool"},
                       {"name": "d", "kind": "core:document"}],
          "events": [],
          "points": [{"content": "d is stale", "pointKind": "statement",
                      "about_entities": ["d"]}],
          "operators": []}
    out = vg.vet_candidates(
        s2, narrative="n",
        arbiter=lambda c, st: {"verdicts": [
            {"id": x["id"], "outcome": vg.DISCARD} for x in c
            if x["text"] == "pytest"]})
    s2_vetted, _w = vg.apply_vet(s2, out["decisions"])
    pool = vg.removal_pool(s2, s2_vetted)
    s4 = {"entities": [], "events": [], "operators": [],
          "points": [{"content": "P is slow", "pointKind": "statement",
                      "about_entities": ["PyTest"]}]}
    union = merge_embed_lists(s2_vetted, s4)
    final, _w2 = vg.apply_vet(union, vg.vet_candidates(
        union, narrative="n")["decisions"], prior=pool)
    assert "pytest" in [e["name"] for e in final["entities"]]
    payload, _res = _payload_of(final)
    l1, _model = validate_payload_dict(payload)
    assert l1.ok, l1.errors


def test_total_on_malformed_decisions_and_prior():
    """The module claims totality for direct callers, not only for the
    pipeline-wrapped path. A non-Mapping decision value and a malformed
    ``prior`` must not raise."""
    el = {"entities": [{"name": "e", "kind": "core:other"}],
          "points": [{"content": "p", "pointKind": "statement",
                      "about_entities": 5}],
          "events": [], "operators": []}
    for decisions in ({"points:0:p": None}, {"points:0:p": "DISCARD"}, None):
        new, _w = vg.apply_vet(el, decisions)
        assert isinstance(new, dict)
    for prior in (5, "x", {"removed_texts": 5},
                  {"removed_entities": 5},
                  {"removed_entities": {"e": "NOT-A-MAPPING"}}):
        new, _w = vg.apply_vet(el, {}, prior=prior)
        assert isinstance(new, dict)
        for ent in new.get("entities") or []:
            assert isinstance(ent, dict), "a non-Mapping must never be emitted"
    for bad in (None, "x", 5, {"points:0:p": "DISCARD"}):
        vg.audit_candidates(el, bad)


def test_prior_with_only_removed_texts_still_prunes():
    """The early return must consider a prior carrying ONLY removed
    points/events (``removed_entities == {}`` — the common case); keying it on
    ``prior_entities`` alone skips the operator prune and leaves an operator to
    be re-minted."""
    el = {"entities": [], "events": [],
          "points": [{"content": "keep", "pointKind": "statement"}],
          "operators": [{"src": "C", "dst": "keep", "op_type": "IMPL"}]}
    new, warnings = vg.apply_vet(
        el, {}, prior={"removed_texts": {"c"}, "removed_entities": {}})
    assert new["operators"] == []
    assert any("pruned 1 operator" in w for w in warnings)


def test_both_keys_item_records_content_for_the_prune():
    """``execute_embed`` resolves endpoints on CONTENT while a candidate's
    identity is ``name or content``, so the pool carries BOTH forms — the #2552
    mint pre-pass can materialise either. Recording only the name leaves an
    operator on the discarded point's content to be re-minted."""
    el = {"entities": [], "events": [],
          "points": [{"name": "N", "content": "C", "pointKind": "statement"},
                     {"content": "keep", "pointKind": "statement"}],
          "operators": []}
    out = vg.vet_candidates(el, narrative="n",
                            arbiter=_discard_matching("N"))
    vetted, _w = vg.apply_vet(el, out["decisions"])
    assert vg.removal_pool(el, vetted)["removed_texts"] == {"c", "n"}
    for endpoint in ("C", "N"):
        union = {"entities": [], "events": [],
                 "points": [{"content": "keep", "pointKind": "statement"}],
                 "operators": [{"src": endpoint, "dst": "keep",
                                "op_type": "IMPL"}]}
        final, _w2 = vg.apply_vet(
            union, {}, prior=vg.removal_pool(el, vetted))
        assert final["operators"] == [], endpoint
        payload, _res = _payload_of(final)
        assert [p["content"] for p in payload["points"]] == ["keep"], endpoint


def test_survivor_name_does_not_shield_a_removed_items_content():
    """The removal side is the MINT surface (identity + content) but the
    survivor side must be the RESOLUTION surface (content only): a survivor's
    *name* does not resolve an endpoint in ``execute_embed``, so it must not
    shield one. Applying the union to both sides left `B` — the discarded
    item's content — to be minted back."""
    el = {"entities": [], "events": [],
          "points": [{"name": "A", "content": "B", "pointKind": "statement"},
                     {"content": "A", "pointKind": "statement"},
                     {"name": "B", "content": "C", "pointKind": "statement"}],
          "operators": [{"src": "A", "dst": "B", "op_type": "IMPL"}]}
    decisions = vg.vet_candidates(el, narrative="n")["decisions"]
    qid = next(i for i, d in decisions.items()
               if d["section"] == "points" and d["index"] == 0)
    decisions[qid] = {"outcome": vg.DISCARD}
    new, _w = vg.apply_vet(el, decisions)
    assert new["operators"] == []
    payload, _res = _payload_of(new)
    assert "B" not in [p["content"] for p in payload["points"]]


def test_surviving_both_keys_item_still_provides_its_content():
    """The converse of the previous test: a SURVIVING item carrying both keys
    still provides its CONTENT as an endpoint, so the operator must NOT be
    pruned on the name-based surviving set ("a wrong drop is memory loss")."""
    el = {"entities": [], "events": [],
          "points": [{"name": "Ndis", "content": "C",
                      "pointKind": "statement"},
                     {"name": "Nsur", "content": "C",
                      "pointKind": "statement"},
                     {"content": "D", "pointKind": "statement"}],
          "operators": [{"src": "C", "dst": "D", "op_type": "IMPL"}]}
    decisions = vg.vet_candidates(el, narrative="n")["decisions"]
    first = next(i for i, d in decisions.items()
                 if d["section"] == "points" and d["index"] == 0)
    decisions[first] = {"outcome": vg.DISCARD}
    new, _w = vg.apply_vet(el, decisions)
    assert len(new["operators"]) == 1, (
        "the surviving twin still provides the content endpoint")


def test_downgraded_entity_reference_spelling_is_reconciled():
    """The spelling fix once covered only the RESTORE path. A
    same-pass Layer-1 downgrade (the entity is kept) left the identical
    exact-string mismatch, so 'kept (Layer-1 referential integrity)' was still a
    422 when the reference was spelled differently."""
    el = {"entities": [{"name": "pytest", "kind": "core:tool"}],
          "events": [],
          "points": [{"content": "P", "pointKind": "statement",
                      "about_entities": ["PyTest"]}],
          "operators": []}
    out = vg.vet_candidates(
        el, narrative="n",
        arbiter=lambda c, s: {"verdicts": [
            {"id": x["id"], "outcome": vg.DISCARD} for x in c
            if x["section"] == "entities"]})
    new, warnings = vg.apply_vet(el, out["decisions"])
    assert [e["name"] for e in new["entities"]] == ["pytest"]
    assert any("downgraded" in w for w in warnings)
    assert new["points"][0]["about_entities"] == ["pytest"]
    payload, _res = _payload_of(new)
    l1, _model = validate_payload_dict(payload)
    assert l1.ok, l1.errors


def test_immutable_slot_ref_does_not_raise():
    """An immutable ``Mapping`` slot ref must be skipped, not
    raise, when the spelling reconciliation runs."""
    from types import MappingProxyType
    el = {"entities": [], "events": [],
          "points": [{"content": "P", "pointKind": "statement",
                      "slots": {"subject": [MappingProxyType(
                          {"name": "PyTest", "kind": "core:other"})]}}],
          "operators": []}
    new, _w = vg.apply_vet(
        el, {}, prior={"removed_entities": {
            "pytest": {"name": "pytest", "kind": "core:tool"}}})
    assert isinstance(new, dict)


def test_padded_entity_name_is_stripped_when_reconciled():
    """``execute_embed`` emits ``str(name).strip()`` and ``validate_layer1``
    matches THAT exact string, so the spelling map stores the stripped name.
    Rewriting a padded name verbatim into a reference turns a passing payload
    into a 422."""
    el = {"entities": [{"name": " pytest ", "kind": "core:tool"}],
          "events": [],
          "points": [{"content": "P", "pointKind": "statement",
                      "about_entities": ["pytest"]}],
          "operators": []}
    out = vg.vet_candidates(
        el, narrative="n",
        arbiter=lambda c, s: {"verdicts": [
            {"id": x["id"], "outcome": vg.DISCARD} for x in c
            if x["section"] == "entities"]})
    new, _warnings = vg.apply_vet(el, out["decisions"])
    assert new["points"][0]["about_entities"] == ["pytest"]
    payload, _res = _payload_of(new)
    l1, _model = validate_payload_dict(payload)
    assert l1.ok, l1.errors


def test_over_long_content_is_pruned_not_re_minted():
    """``execute_embed`` truncates content to 1000 chars before keying it, but
    also registers the untruncated ref — so a removed >1000-char item must be
    recognised under BOTH forms or the endpoint escapes the prune and the text
    is re-materialised as a new Point."""
    long = "x" * 1100
    el = {"entities": [], "events": [],
          "points": [{"content": long, "pointKind": "statement"},
                     {"content": "keep", "pointKind": "statement"}],
          "operators": []}
    out = vg.vet_candidates(el, narrative="n", arbiter=_discard_matching(long))
    vetted, _w = vg.apply_vet(el, out["decisions"])
    union = {"entities": [], "events": [],
             "points": [{"content": "keep", "pointKind": "statement"}],
             "operators": [{"src": long, "dst": "keep", "op_type": "IMPL"},
                           {"src": long[:1000], "dst": "keep",
                            "op_type": "NAND"}]}
    final, warnings = vg.apply_vet(
        union, {}, prior=vg.removal_pool(el, vetted))
    assert final["operators"] == []
    assert any("pruned 2 operator" in w for w in warnings)
    payload, _res = _payload_of(final)
    assert all(len(p["content"]) < 1100 for p in payload["points"])


def test_non_sequence_operators_does_not_raise():
    """``operators`` is a top-level ``embed_list`` key, so a
    non-sequence value is a malformed section shape under the module's own
    totality claim — the prune path must not raise."""
    el = {"entities": [{"name": "A", "kind": "core:other"}],
          "points": [{"content": "B", "pointKind": "statement"}],
          "operators": 5}
    new, _w = vg.apply_vet(el, {}, prior={"removed_texts": {"gone"}})
    assert new["operators"] == 5          # malformed shape passes through


def test_falsy_content_survivor_still_shields_its_endpoint():
    """`execute_embed` emits `str(content)` — a point with `content: 0` becomes
    the point `"0"` and resolves an endpoint on it. VET's content reader used a
    truthiness test, so it saw no survivor and pruned the edge (a lost edge, no
    mint — the module's own 'a wrong drop is memory loss' class)."""
    el = {"entities": [], "events": [],
          "points": [{"content": "0", "pointKind": "statement"},
                     {"content": 0, "pointKind": "statement"},
                     {"content": "X", "pointKind": "statement"}],
          "operators": [{"src": "0", "dst": "X", "op_type": "IMPL"}]}
    decisions = vg.vet_candidates(el, narrative="n")["decisions"]
    pid = next(i for i, d in decisions.items()
               if d["section"] == "points" and d["index"] == 0)
    decisions[pid] = {"outcome": vg.DISCARD}
    new, _w = vg.apply_vet(el, decisions)
    assert len(new["operators"]) == 1, (
        "the surviving `content: 0` point emits `\"0\"` — the edge must stay")
    payload, _res = _payload_of(new)
    assert len(payload.get("operators") or []) == 1, payload.get("operators")


def test_present_null_content_is_the_minted_text():
    """A MISSING ``content`` key emits nothing, but a PRESENT null emits the
    literal point ``"None"`` (``execute_embed`` uses ``str(content)``) — so an
    operator on ``"None"`` is pruned. Treating the two alike leaves it unpruned,
    and the mint re-materialises the discarded item."""
    el = {"entities": [], "events": [],
          "points": [{"name": "T", "content": None, "pointKind": "statement"},
                     {"content": "X", "pointKind": "statement"}],
          "operators": []}
    out = vg.vet_candidates(el, narrative="n",
                            arbiter=_discard_matching("T"))
    vetted, _w = vg.apply_vet(el, out["decisions"])
    assert "none" in vg.removal_pool(el, vetted)["removed_texts"]
    union = {"entities": [], "events": [],
             "points": [{"content": "X", "pointKind": "statement"}],
             "operators": [{"src": "None", "dst": "X", "op_type": "IMPL"}]}
    final, _w2 = vg.apply_vet(
        union, {}, prior=vg.removal_pool(el, vetted))
    assert final["operators"] == []
    payload, _res = _payload_of(final)
    assert [p["content"] for p in payload["points"]] == ["X"]


def test_removal_pool_carries_only_items_actually_absent():
    """A *removal* is an absent ITEM, not a text missing from a surface. Deriving
    the pool as ``identity(before) - content(after)`` carried the NAME of every
    surviving point/event that had one (a name is in the identity surface, not
    the content surface), so the union pass pruned operators naming a survivor —
    with the flag ON, NO arbiter, and nothing discarded on either pass.
    """
    el = {"entities": [], "events": [],
          "points": [{"name": "N", "content": "C", "pointKind": "statement"},
                     {"content": "K", "pointKind": "statement"}],
          "operators": [{"src": "N", "dst": "K", "op_type": "IMPL"}]}
    assert vg.removal_pool(el, el)["removed_texts"] == set()
    final, warnings = vg.apply_vet(el, {}, prior=vg.removal_pool(el, el))
    assert final["operators"] == el["operators"], (
        "an unchanged list must not lose an operator — no verdict named it")
    assert not any("discarded" in w for w in warnings)


def test_an_incomparable_item_is_treated_as_survived():
    """A comparison that RAISES must read as *survived* — the fail-open
    direction the module's failure policy requires (a wrong keep is noise, a
    wrong drop is memory loss)."""
    class Bomb(dict):
        def __eq__(self, other):
            raise RecursionError("cyclic")

    before = {"entities": [], "events": [],
              "points": [Bomb({"content": "C", "pointKind": "statement"})],
              "operators": []}
    after = {"entities": [], "events": [],
             "points": [Bomb({"content": "C", "pointKind": "statement"})],
             "operators": []}
    assert vg.removal_pool(before, after)["removed_texts"] == set()


def test_present_entity_name_does_not_get_its_operator_pruned():
    """A name in ``gone`` from an EARLIER pass's removals — or from a same-pass
    discard of a duplicate-name entity — must not prune an operator when an
    entity of that name is present in the output. The embedder drops an
    entity-named endpoint itself, so the payload is unchanged either way; the
    defect is the false "whose endpoint was discarded" claim, in a module whose
    stated design is that every discard is auditable.
    """
    # Cross-pass: pass 1 removed entity `e`, the union re-emitted it.
    s2 = {"entities": [{"name": "e", "kind": "core:tool"}], "events": [],
          "points": [{"content": "K", "pointKind": "statement"}],
          "operators": [{"src": "e", "dst": "K", "op_type": "IMPL"}]}
    pool = vg.removal_pool(s2, {**s2, "entities": []})
    assert list(pool["removed_entities"]) == ["e"]
    union = {"entities": [{"name": "e", "kind": "core:concept"}], "events": [],
             "points": [{"content": "K", "pointKind": "statement"}],
             "operators": [{"src": "e", "dst": "K", "op_type": "IMPL"}]}
    out, warnings = vg.apply_vet(union, {}, prior=pool)
    assert [x["name"] for x in out["entities"]] == ["e"]
    assert len(out["operators"]) == 1, "the endpoint is present — do not prune"
    assert not any("pruned" in w for w in warnings)

    # Same-pass: two entities share a name, one is discarded.
    twin = {"entities": [{"name": "e", "kind": "core:tool"},
                         {"name": "e", "kind": "core:concept"}],
            "events": [],
            "points": [{"content": "K", "pointKind": "statement"}],
            "operators": [{"src": "e", "dst": "K", "op_type": "IMPL"}]}
    first = next(vg._item_id(*t) for t in vg._iter_items(twin)
                 if t[0] == "entities" and t[1] == 0)
    out2, warnings2 = vg.apply_vet(twin, {first: {"outcome": vg.DISCARD}})
    assert [x["name"] for x in out2["entities"]] == ["e"]
    assert len(out2["operators"]) == 1
    assert not any("pruned" in w for w in warnings2), warnings2

    # A name LONGER than the mint's 1000-char key does NOT shield: the mint
    # compares the truncated ref against the full name, so it would fabricate a
    # claim Point out of a participant name. The operator must be pruned.
    long_name = "A" * 1100
    big = {"entities": [{"name": long_name, "kind": "core:tool"},
                        {"name": long_name, "kind": "core:concept"}],
           "events": [],
           "points": [{"content": "K", "pointKind": "statement"}],
           "operators": [{"src": long_name, "dst": "K", "op_type": "IMPL"}]}
    first_big = next(vg._item_id(*t) for t in vg._iter_items(big)
                     if t[0] == "entities" and t[1] == 0)
    out3, warnings3 = vg.apply_vet(big, {first_big: {"outcome": vg.DISCARD}})
    assert out3["operators"] == [], (
        "a >1000-char name is not what `emitted_entity_names` holds — the mint "
        "would fabricate a Point for the truncated ref")
    assert any("pruned" in w for w in warnings3), warnings3

    # The truncation has to be the MINT's (truncate, then normalise). A name
    # whose 1000th character falls inside a whitespace run normalises to
    # something shorter, so it shields under a normalise-then-truncate test
    # while the mint keys it at the shorter length and fabricates.
    spaced = "a" * 900 + " " * 200 + "b" * 50
    assert len(spaced) > vg._MAX_CONTENT
    assert len(vg._norm(spaced)) <= vg._MAX_CONTENT < len(spaced), (
        "fixture must straddle the boundary the way the mint truncates")
    spaced_el = {"entities": [
                     {"name": spaced, "kind": "core:tool"},
                     {"name": spaced, "kind": "core:concept"}],
                 "events": [],
                 "points": [{"content": "K", "pointKind": "statement"}],
                 "operators": [{"src": spaced, "dst": "K", "op_type": "IMPL"}]}
    first_sp = next(vg._item_id(*t) for t in vg._iter_items(spaced_el)
                    if t[0] == "entities" and t[1] == 0)
    out4, warnings4 = vg.apply_vet(spaced_el,
                                   {first_sp: {"outcome": vg.DISCARD}})
    assert out4["operators"] == [], (
        "the mint truncates before normalising — this name is not shielded")
    assert any("pruned" in w for w in warnings4), warnings4


def test_a_genuinely_removed_item_still_fills_the_pool():
    """The other side of the same rule: an item that IS absent from ``after``
    contributes its identity AND its content — the #2552 mint materialises
    whichever form an operator wrote."""
    before = {"entities": [], "events": [],
              "points": [{"name": "N", "content": "C", "pointKind": "statement"},
                         {"content": "K", "pointKind": "statement"}],
              "operators": []}
    after = {"entities": [], "events": [],
             "points": [{"content": "K", "pointKind": "statement"}],
             "operators": []}
    assert vg.removal_pool(before, after)["removed_texts"] == {"c", "n"}


def test_removal_pool_of_an_unchanged_list_is_empty():
    """A surviving ENTITY contributes no content, so `identity(before) -
    content(after)` used to carry its name forward as `removed` — a false pool
    entry that then pruned an operator with a 'discarded' warning for an item
    that was never touched."""
    el = {"entities": [{"name": "pytest", "kind": "core:tool"}],
          "events": [],
          "points": [{"content": "real claim", "pointKind": "statement"}],
          "operators": []}
    assert vg.removal_pool(el, el) == {"removed_texts": set(),
                                      "removed_entities": {}}


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
                {"entities": [None, 7, "x"]}, {"operators": 5},
                {"operators": "oops"}):
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


def test_audit_report_has_one_row_per_candidate_not_per_item():
    """An empty-text item is not a candidate, so it must not render a blank row
    — the report's body has to match ``stats["candidates"]``, or the owner
    reviewing it counts rows the gate never saw."""
    el = {"entities": [], "events": [],
          "points": [{"content": "real"}, {"content": ""},
                     {"pointKind": "statement"}],
          "operators": []}
    out = vg.vet_candidates(el, narrative="n")
    report = vg.audit_candidates(el, out["decisions"])
    assert out["stats"]["candidates"] == 1
    assert len(report.splitlines()) == 2


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
