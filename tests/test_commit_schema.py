"""Layer-1 contract tests for the commit endpoint (epic #909 slice 5a, #952).

Covers: §4.5 constraint-table positives/negatives (422 field reasons, 400
class reserved for missing required fields), canonicalization determinism,
L1/L2 reconciliation + the DE2E-7 budget fixtures (Sessions A/B/C), the
supersede-only exemption (R-14), replay duplicate detection, and the
:CommitRecord store (MERGE serialization point — concurrency test is
skip-guarded on live FalkorDB, DE2E-7 negative case a).
"""
from __future__ import annotations

import pytest

from tortoise.commit_schema import (
    CommitPayload,
    CommitRecordState,
    Layer1Result,
    MAX_PAYLOAD_POINTS,
    atomicity_violations,
    canonical_payload,
    compute_client_commit_id,
    is_first_adjudication,
    is_l1_replay,
    plan_commit,
    point_content_id,
    reconcile_payload,
    validate_payload_dict,
    GraphState,
)

# ── Payload factory ───────────────────────────────────────────────────────

_TELEMETRY = {
    "extractor": {"version": "value@1.0.0+abc+def", "mode": "byok"},
    "model": {"provider": "anthropic", "id": "claude-3-7", "cfg_hash": "h1"},
    "counts": {"kept": 5, "candidate": 10, "segment": 12, "window": 3,
               "empty_windows": 0},
    "keep_ratio": 0.5,
    "dedup_hits": 0,
    "frontier_calls": 1,
    "llm_cost_usd": 0.02,
    "extraction_ms": 1234,
    "retry_count": 0,
    "last_error_code": None,
    "confidence_histogram": [0, 0, 0, 0, 0, 0, 0, 1, 2, 2],
}


def _point(i: int, **overrides) -> dict:
    p = {
        "id": f"pt_{i:064d}",
        "content": f"point {i}",
        "pointKind": "decision",
        "reason": "NEW",
        "confidence": 0.9,
        "c_cal": 0.8,
        "about_entities": ["Alpha"],
        "source_ref": "session.md",
        "quote": "",
        "status": "live",
    }
    p.update(overrides)
    return p


def _raw_payload(n_points: int = 1, *, session_id: str = "s1",
                 operators=None, **overrides) -> dict:
    """Raw §6.1 dict with an EMPTY client_commit_id (finalize computes it)."""
    payload = {
        "schema_version": "1",
        "session_id": session_id,
        "client_commit_id": "",
        "captured_at": "2026-08-11T10:00:00Z",
        "extractor": {"version": "value@1.0.0+abc+def", "mode": "byok",
                      "calibration_version": "v3"},
        "summary": "summary text",
        "story_arc": "arc text",
        "provenance_refs": [{"path": "session.md", "spans": ["0-10"]}],
        "sources": [],
        "entities": [{"name": "Alpha", "kind": "Project",
                      "passes_frequency_gate": True}],
        "points": [_point(i) for i in range(n_points)],
        "operators": operators or [],
        "telemetry": _TELEMETRY,
    }
    payload.update(overrides)
    return payload


def _finalize(raw: dict) -> dict:
    """Compute the canonical client_commit_id over the (possibly mutated)
    payload — mirrors the client's serializer (W-3)."""
    raw["client_commit_id"] = compute_client_commit_id(
        raw["session_id"], raw["points"], raw["entities"], raw["operators"],
        raw["summary"], raw["story_arc"])
    return raw


def _check(raw: dict) -> tuple[Layer1Result, CommitPayload | None]:
    """Finalize + run the full Layer-1 gate."""
    return validate_payload_dict(_finalize(raw))


def _valid(n_points: int = 1, **overrides) -> CommitPayload:
    result, payload = _check(_raw_payload(n_points, **overrides))
    assert result.ok, f"fixture payload must validate: {result.errors}"
    return payload


# ── Layer-1: 400 class (missing required fields) ──────────────────────────

class TestRequiredFields:
    """400 is RESERVED for missing required payload fields (issue #952 cap
    resolution; plan §4.5)."""

    @pytest.mark.parametrize("field", [
        "schema_version", "session_id", "client_commit_id", "captured_at",
        "extractor", "points", "telemetry",
    ])
    def test_missing_required_field_is_400_class(self, field):
        raw = _raw_payload()
        del raw[field]
        result, payload = validate_payload_dict(raw)
        assert not result.ok
        assert result.code == "missing_required_fields"
        assert any(field in reason for reason in result.errors["required"])
        assert payload is None

    def test_null_required_field_is_400_class(self):
        raw = _raw_payload()
        raw["session_id"] = None
        result, _ = validate_payload_dict(raw)
        assert result.code == "missing_required_fields"

    def test_non_object_body(self):
        result, _ = validate_payload_dict(["not", "a", "dict"])
        assert not result.ok
        assert "payload" in result.errors


# ── Layer-1: 422 class (shape + semantic violations, §4.5 table) ──────────

class TestLayer1Shape:
    """Pydantic shape violations → 422 field reasons."""

    def test_content_over_1000(self):
        result, _ = _check(_raw_payload(
            points=[_point(0, content="x" * 1001)]))
        assert not result.ok
        assert result.errors["points[0].content"]

    def test_quote_over_200(self):
        result, _ = _check(_raw_payload(
            points=[_point(0, quote="q" * 201)]))
        assert not result.ok
        assert result.errors["points[0].quote"]

    def test_confidence_and_c_cal_are_plain_floats(self):
        # contract says float — no invented range
        assert _valid(points=[_point(0, confidence=1.05, c_cal=-0.2)]) \
            is not None

    def test_reason_cut_values_422(self):
        # CONNECTS/RESOLVES CUT in v1 — no defined server behavior (§6.1)
        result, _ = _check(_raw_payload(
            points=[_point(0, reason="CONNECTS")]))
        assert not result.ok
        assert result.errors["points[0].reason"]

    def test_bad_status_422(self):
        result, _ = _check(_raw_payload(points=[_point(0, status="retracted")]))
        assert not result.ok
        assert result.errors["points[0].status"]

    def test_non_pt_id_422(self):
        result, _ = _check(_raw_payload(points=[_point(0, id="op_123")]))
        assert not result.ok
        assert result.errors["points[0].id"]

    def test_bad_schema_version_422(self):
        result, _ = validate_payload_dict(_finalize(
            _raw_payload(schema_version="2")))
        assert not result.ok
        assert result.errors["schema_version"]

    def test_bad_captured_at_422(self):
        result, _ = _check(_raw_payload(captured_at="yesterday"))
        assert not result.ok
        assert result.errors["captured_at"]

    def test_histogram_must_have_10_buckets(self):
        raw = _raw_payload()
        raw["telemetry"] = {**_TELEMETRY, "confidence_histogram": [1, 2]}
        result, _ = validate_payload_dict(_finalize(raw))
        assert not result.ok
        assert result.errors["telemetry.confidence_histogram"]

    def test_unknown_extra_field_422(self):
        # extra="forbid" — the contract is exact; drift must not be silent
        result, _ = _check(_raw_payload(unexpected="nope"))
        assert not result.ok
        assert any(k.startswith("unexpected") for k in result.errors)

    def test_empty_payload_is_valid(self):
        # extract-nothing commit (DE2E-8): empty commit is normal and valid
        result, payload = _check(_raw_payload(
            n_points=0, entities=[], operators=[]))
        assert result.ok
        assert payload is not None


class TestLayer1Semantic:
    """Cross-field deterministic checks (plan §4.5 / §6.1 Layer-1 block)."""

    def test_unknown_point_kind_422_calibration_mismatch(self):
        result, _ = _check(_raw_payload(
            points=[_point(0, pointKind="bogusKind")]))
        assert not result.ok
        assert result.errors["points[0].pointKind"]
        assert result.code == "calibration_mismatch"

    def test_pack_point_kinds_accepted(self):
        # closed vocab compiled from PackRegistry at RUNTIME: bare pack kind
        # + namespaced + core event kind
        assert _valid(points=[_point(0, pointKind="requirement")])
        assert _valid(points=[_point(0, pointKind="dev:bug")])
        assert _valid(points=[_point(0, pointKind="event")])

    def test_unknown_source_kind_422(self):
        result, _ = _check(_raw_payload(
            sources=[{"sourceKind": "bogusKind", "url": "https://x",
                      "credibilityTier": "T2", "contentHash": "h"}],
            points=[_point(0, source_ref="https://x")]))
        assert not result.ok
        assert result.errors["sources[0].sourceKind"]
        assert result.code == "calibration_mismatch"

    def test_agent_session_source_kind_accepted(self):
        assert _valid(
            sources=[{"sourceKind": "agentSession", "url": "https://x",
                      "credibilityTier": "T3", "contentHash": "h"}],
            points=[_point(0, source_ref="https://x")])

    def test_missing_source_ref(self):
        result, _ = _check(_raw_payload(points=[_point(0, source_ref="")]))
        assert not result.ok
        assert result.errors["points[0].source_ref"]

    def test_dangling_source_ref(self):
        result, _ = _check(_raw_payload(points=[_point(0, source_ref="ghost")]))
        assert not result.ok
        assert "points[0].source_ref" in result.errors

    def test_source_ref_resolves_to_emitted_source(self):
        # source_ref ∈ {session source} ∪ emitted sources[] (§4.5)
        assert _valid(
            sources=[{"sourceKind": "github_issue", "url": "https://github.com/x",
                      "credibilityTier": "T2", "contentHash": "h"}],
            points=[_point(0, source_ref="https://github.com/x")])

    def test_about_entities_subset(self):
        result, _ = _check(_raw_payload(
            points=[_point(0, about_entities=["Alpha", "Ghost"])]))
        assert not result.ok
        assert result.errors["points[0].about_entities"]

    def test_dangling_operator_src(self):
        pt0 = "pt_0000000000000000000000000000000000000000000000000000000000000000"
        pt1 = "pt_0000000000000000000000000000000000000000000000000000000000000001"
        result, _ = _check(_raw_payload(operators=[
            {"src": "pt_missing", "dst": pt1, "op_type": "IMPL"}]))
        assert not result.ok
        assert result.errors["operators[0].src"]
        assert result.errors["operators[0].dst"]  # pt_..01 not emitted either
        # src/dst must resolve against EMITTED point ids — a valid ref passes
        result, _ = _check(_raw_payload(n_points=2, operators=[
            {"src": pt0, "dst": pt1, "op_type": "IMPL"}]))
        assert result.ok

    def test_impl_operator_ok(self):
        assert _valid(n_points=2, operators=[
            {"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
             "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
             "op_type": "IMPL"}])

    def test_nand_direction_required(self):
        raw = _raw_payload(operators=[
            {"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
             "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
             "op_type": "NAND"}])
        result, _ = _check(raw)
        assert not result.ok
        assert result.errors["operators[0].direction"]

    def test_nand_direction_present_ok(self):
        assert _valid(n_points=2, operators=[
            {"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
             "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
             "op_type": "NAND", "direction": "unidirectional"}])

    def test_mitigates_shape_violations(self):
        pt0 = "pt_0000000000000000000000000000000000000000000000000000000000000000"
        pt1 = "pt_0000000000000000000000000000000000000000000000000000000000000001"
        base_ops = [
            {"src": pt0, "dst": pt1, "op_type": "IMPL"},
        ]
        # target not emitted → semantic violation
        result, _ = _check(_raw_payload(n_points=2, operators=base_ops + [
            {"src": pt0, "dst": pt1, "op_type": "MITIGATES",
             "target": {"src": pt1, "dst": pt0, "op_type": "IMPL"},
             "strength": 0.3}]))
        assert not result.ok
        assert result.errors["operators[1].target"]

        # target not IMPL → pydantic Literal (shape)
        result, _ = _check(_raw_payload(n_points=2, operators=base_ops + [
            {"src": pt0, "dst": pt1, "op_type": "MITIGATES",
             "target": {"src": pt0, "dst": pt1, "op_type": "NAND"},
             "strength": 0.3}]))
        assert not result.ok
        assert result.errors["operators[1].target.op_type"]

        # strength out of range [0.10, 0.50] → shape (pydantic ge/le)
        result, _ = _check(_raw_payload(n_points=2, operators=base_ops + [
            {"src": pt0, "dst": pt1, "op_type": "MITIGATES",
             "target": {"src": pt0, "dst": pt1, "op_type": "IMPL"},
             "strength": 0.05}]))
        assert not result.ok
        assert result.errors["operators[1].strength"]

        # strength missing → semantic
        result, _ = _check(_raw_payload(n_points=2, operators=base_ops + [
            {"src": pt0, "dst": pt1, "op_type": "MITIGATES",
             "target": {"src": pt0, "dst": pt1, "op_type": "IMPL"}}]))
        assert not result.ok
        assert result.errors["operators[1].strength"]

        # target missing → semantic
        result, _ = _check(_raw_payload(n_points=2, operators=base_ops + [
            {"src": pt0, "dst": pt1, "op_type": "MITIGATES",
             "strength": 0.3}]))
        assert not result.ok
        assert result.errors["operators[1].target"]

    def test_mitigates_valid(self):
        pt0 = "pt_0000000000000000000000000000000000000000000000000000000000000000"
        pt1 = "pt_0000000000000000000000000000000000000000000000000000000000000001"
        assert _valid(n_points=2, operators=[
            {"src": pt0, "dst": pt1, "op_type": "IMPL"},
            {"src": pt0, "dst": pt1, "op_type": "MITIGATES",
             "target": {"src": pt0, "dst": pt1, "op_type": "IMPL"},
             "strength": 0.5}])


class TestPayloadCaps:
    """Per-type caps are Layer-1 → 422 (issue #952 cap resolution; the 50
    budget ceiling counts NET-NEW → 402 — different number, §4.4)."""

    def test_51_point_payload_422(self):
        # DE2E-7: raw payload point count > MAX_PAYLOAD_POINTS → 422
        result, _ = _check(_raw_payload(n_points=MAX_PAYLOAD_POINTS + 1))
        assert not result.ok
        assert any("MAX_PAYLOAD_POINTS" in r
                   for r in result.errors["points"])

    def test_exactly_50_points_ok(self):
        result, _ = _check(_raw_payload(n_points=MAX_PAYLOAD_POINTS))
        assert result.ok

    def test_501_entities_422(self):
        result, _ = _check(_raw_payload(entities=[
            {"name": f"e{i}", "kind": "Project", "passes_frequency_gate": True}
            for i in range(501)]))
        assert not result.ok
        assert result.errors["entities"]

    def test_501_operators_422(self):
        pt0 = "pt_0000000000000000000000000000000000000000000000000000000000000000"
        pt1 = "pt_0000000000000000000000000000000000000000000000000000000000000001"
        result, _ = _check(_raw_payload(operators=[
            {"src": pt0 if i % 2 == 0 else pt1,
             "dst": pt1 if i % 2 == 0 else pt0,
             "op_type": "IMPL"} for i in range(501)]))
        assert not result.ok
        assert result.errors["operators"]

    def test_caps_independent_not_summed(self):
        # 40 points + 300 entities + 300 operators — each under its OWN cap
        # (40 ≤ 50, 300 ≤ 500, 300 ≤ 500); the caps are NOT summed, so the
        # combined 640 payload items are valid
        pt0 = "pt_0000000000000000000000000000000000000000000000000000000000000000"
        pt1 = "pt_0000000000000000000000000000000000000000000000000000000000000001"
        result, _ = _check(_raw_payload(
            n_points=40,
            entities=(
                [{"name": "Alpha", "kind": "Project",
                  "passes_frequency_gate": True}]
                + [{"name": f"e{i}", "kind": "Project",
                    "passes_frequency_gate": True} for i in range(299)]
            ),
            operators=[{"src": pt0, "dst": pt1, "op_type": "IMPL"}
                       for _ in range(300)]))
        assert result.ok


class TestCommitIdMismatch:
    def test_recomputed_hash_mismatch(self):
        # the id is NOT opaque — the server recomputes it (§6.1)
        raw = _raw_payload()
        raw["client_commit_id"] = "deadbeef"
        result, _ = validate_payload_dict(raw)
        assert not result.ok
        assert result.errors["client_commit_id"]
        assert result.code == "commit_id_mismatch"

    def test_mismatch_precedes_calibration_code(self):
        raw = _raw_payload(points=[_point(0, pointKind="bogus")])
        raw["client_commit_id"] = "deadbeef"
        result, _ = validate_payload_dict(raw)
        assert result.code == "commit_id_mismatch"
        assert "points[0].pointKind" in result.errors

    def test_confidence_artifacts_do_not_break_id(self):
        # confidence/c_cal/status/reason EXCLUDED from the canonical hash —
        # a client may re-serialize with different LLM artifacts
        a = _raw_payload()
        b = _raw_payload(points=[_point(0, confidence=0.1, c_cal=0.2,
                                        status="draft", reason="REVISES")])
        assert compute_client_commit_id(
            a["session_id"], a["points"], a["entities"], a["operators"],
            a["summary"], a["story_arc"]) == compute_client_commit_id(
            b["session_id"], b["points"], b["entities"], b["operators"],
            b["summary"], b["story_arc"])


class TestAtomicity:
    def test_coordination_cue(self):
        assert atomicity_violations("we will ship A and B and C", "decision")

    def test_serial_list(self):
        assert atomicity_violations(
            "we chose option A, option B, option C", "decision")

    def test_numbered_list(self):
        assert atomicity_violations(
            "1) ship A 2) ship B", "decision")

    def test_two_commissive_predicates_on_decision(self):
        assert atomicity_violations(
            "we will ship A and we will ship B", "decision")

    def test_single_commissive_ok(self):
        assert not atomicity_violations("we will ship A", "decision")

    def test_non_decision_ignores_commissive_count(self):
        # commissive-predicate rule is decision-class only — but the
        # coordination-cue rule applies to ALL points, so avoid and/but/or
        assert not atomicity_violations(
            "we will ship A, we will ship B", "statement")
        assert atomicity_violations(
            "we will ship A, we will ship B", "decision")

    def test_clean_statement_ok(self):
        assert not atomicity_violations(
            "the migration reduced query latency by 40%", "observation")

    def test_compound_point_422_in_layer1(self):
        result, _ = _check(_raw_payload(
            points=[_point(0, content="we will ship A and B and C")]))
        assert not result.ok
        assert result.errors["points[0].atomicity"]

    def test_common_and_phrase_is_flagged_by_design(self):
        # the deterministic E9 mirror is intentionally strict: any and/but/or
        # token flags the point client-side (the extractor should not emit
        # it); a truly clean single assertion passes
        assert atomicity_violations("research and development costs rose",
                                    "observation")
        assert not atomicity_violations(
            "research costs rose 12% in Q2", "observation")


# ── Canonicalization ──────────────────────────────────────────────────────

class TestCanonicalization:
    def test_same_input_same_hash(self):
        entity = {"name": "Alpha", "kind": "Project",
                  "passes_frequency_gate": True}
        a = canonical_payload("s1", [_point(0)], [entity], [], "sum", "arc")
        b = canonical_payload("s1", [_point(0)], [entity], [], "sum", "arc")
        assert a == b
        assert compute_client_commit_id("s1", [_point(0)], [entity], [],
                                        "sum", "arc") == \
            compute_client_commit_id("s1", [_point(0)], [entity], [],
                                     "sum", "arc")

    def test_key_order_independence(self):
        # dict key insertion order must not matter (sorted keys at every level)
        raw1 = _finalize(_raw_payload())
        shuffled = {k: raw1[k] for k in reversed(list(raw1.keys()))}
        raw2 = _finalize(shuffled)
        assert raw1["client_commit_id"] == raw2["client_commit_id"]

    def test_array_order_independence(self):
        pts = [_point(0), _point(1), _point(2)]
        id_a = compute_client_commit_id("s1", pts, [], [], "s", "a")
        id_b = compute_client_commit_id("s1", list(reversed(pts)), [], [],
                                        "s", "a")
        assert id_a == id_b

    def test_about_entities_order_independence(self):
        # about_entities is sorted in the canonical — order must not matter
        pt_a = _point(0, about_entities=["Alpha", "Beta"])
        pt_b = _point(0, about_entities=["Beta", "Alpha"])
        assert compute_client_commit_id("s1", [pt_a], [], [], "s", "a") == \
            compute_client_commit_id("s1", [pt_b], [], [], "s", "a")

    def test_float_rounding_3dp(self):
        op = {"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
              "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
              "op_type": "MITIGATES",
              "target": {"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
                         "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
                         "op_type": "IMPL"},
              "strength": 0.123456}
        canon = canonical_payload("s1", [], [], [op], "s", "a")
        assert '"strength":0.123' in canon
        # 0.123456 and 0.123449 round identically → same canonical
        op2 = {**op, "strength": 0.123449}
        assert canonical_payload("s1", [], [], [op2], "s", "a") == canon

    def test_excluded_fields_not_in_canonical(self):
        pts = [_point(0)]
        canon = canonical_payload("s1", pts, [], [], "s", "a")
        assert "confidence" not in canon
        assert "c_cal" not in canon
        assert "status" not in canon
        assert "reason" not in canon
        assert "captured_at" not in canon

    def test_included_fields_change_hash(self):
        a = compute_client_commit_id("s1", [_point(0, content="X")], [], [],
                                     "s", "a")
        b = compute_client_commit_id("s1", [_point(0, content="Y")], [], [],
                                     "s", "a")
        assert a != b

    def test_models_and_dicts_canonicalize_identically(self):
        payload = _valid()
        canon_model = canonical_payload(
            payload.session_id, payload.points, payload.entities,
            payload.operators, payload.summary, payload.story_arc)
        raw = _raw_payload()
        canon_dict = canonical_payload(
            raw["session_id"], raw["points"], raw["entities"],
            raw["operators"], raw["summary"], raw["story_arc"])
        assert canon_model == canon_dict

    def test_point_content_id(self):
        from tortoise.ids import content_hash
        assert point_content_id("hello") == f"pt_{content_hash('hello')}"
        assert point_content_id("hello") != point_content_id("world")


# ── L2 reconciliation + budget (DE2E-7 Sessions A/B/C) ────────────────────

def _ten_new_points() -> list[dict]:
    # 10 fresh points (ids 2..11 — distinct from any prior state ids)
    return [_point(i) for i in range(2, 12)]


def _payload_with(points, **kw) -> CommitPayload:
    """Build a parsed CommitPayload with the given point dicts."""
    raw = _raw_payload(n_points=0, entities=[], operators=[], **kw)
    raw["points"] = points
    return CommitPayload.model_validate(_finalize(raw))


class TestReconcile:
    def test_merge_same_id_same_content(self):
        payload = _payload_with([_point(0)])
        state = GraphState(points={"pt_0000000000000000000000000000000000000000000000000000000000000000": "point 0"})
        rec = reconcile_payload(payload, state)
        assert rec.points[0].action == "merge"
        assert rec.net_new == 0

    def test_supersede_candidate_on_changed_content(self):
        # same id + CHANGED content → supersede CANDIDATE: new content-
        # addressed id, supersede_point WRITE is #953's (PL2)
        payload = _payload_with([_point(0, content="new text")])
        state = GraphState(points={"pt_0000000000000000000000000000000000000000000000000000000000000000": "old text"})
        rec = reconcile_payload(payload, state)
        assert rec.points[0].action == "supersede"
        assert rec.points[0].supersede_id == point_content_id("new text")
        assert rec.net_new == 0  # R-14: supersede-only delta is exempt

    def test_new_point_counts(self):
        payload = _payload_with([_point(0)])
        rec = reconcile_payload(payload, GraphState())
        assert rec.points[0].action == "new"
        assert rec.net_new == 1

    def test_entities_and_operators_merge_keys(self):
        entities = [{"name": "Alpha", "kind": "Project",
                     "passes_frequency_gate": True}]
        ops = [{"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
                "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
                "op_type": "IMPL"}]
        raw = _raw_payload(n_points=0, entities=entities, operators=ops)
        raw["points"] = [_point(0), _point(1)]
        payload = CommitPayload.model_validate(_finalize(raw))
        state = GraphState(
            entities={("Alpha", "Project")},
            operators={("pt_0000000000000000000000000000000000000000000000000000000000000000",
                        "pt_0000000000000000000000000000000000000000000000000000000000000001",
                        "IMPL")})
        rec = reconcile_payload(payload, state)
        assert rec.entities[0].action == "merge"
        assert rec.operators[0].action == "merge"
        assert rec.net_new == 2  # both points are new; entities/operators merge


class TestBudgetDE2E7:
    """The DE2E-7 budget fixtures: Sessions A/B/C + R-14 exemption."""

    def test_session_a_prior_20_plus_10_held(self):
        # Session A: prior commit of 20 → +10 → cumulative 30 → held[10]
        state = GraphState(value_nodes_created=20)
        payload = _payload_with(_ten_new_points())
        plan = plan_commit(payload, state, record=None)
        assert not plan.duplicate
        assert plan.first_adjudication
        assert plan.reconcile.net_new == 10
        assert plan.budget.outcome == "held"
        assert len(plan.budget.held_point_ids) == 10
        assert plan.budget.cumulative_after == 30

    def test_session_a_resubmission_writes_ceiling_only(self):
        # PL3 promotion: re-submission of a held commit is checked against
        # the 50-ceiling ONLY → 20+10=30 ≤ 50 → written (no infinite hold)
        state = GraphState(value_nodes_created=20)
        payload = _payload_with(_ten_new_points())
        held_record = CommitRecordState(
            client_commit_id="cid", session_id="s1", status="held",
            write_ops_billed=0)
        assert not is_l1_replay(held_record)
        assert not is_first_adjudication(held_record)
        plan = plan_commit(payload, state, record=held_record)
        assert plan.budget.outcome == "ok"
        assert plan.budget.held_point_ids == []
        assert plan.budget.cumulative_after == 30

    def test_session_b_prior_45_plus_10_402(self):
        # Session B: prior 45 → +10 → cumulative 55 → 402, nothing written
        state = GraphState(value_nodes_created=45)
        payload = _payload_with(_ten_new_points())
        plan = plan_commit(payload, state, record=None)
        assert plan.budget.outcome == "fail"
        assert plan.budget.held_point_ids == []

    def test_session_c_held_resubmission_exceeding_ceiling_stays_held(self):
        # Session C: held re-submission that would push cumulative past 50 →
        # 402; items remain held client-side (never dropped)
        state = GraphState(value_nodes_created=45)
        payload = _payload_with(_ten_new_points())
        held_record = CommitRecordState(
            client_commit_id="cid", session_id="s1", status="held",
            write_ops_billed=0)
        plan = plan_commit(payload, state, record=held_record)
        assert plan.budget.outcome == "fail"
        assert "ceiling" in (plan.budget.reason or "")

    def test_soft_warn_at_15(self):
        # soft 15 → WARN telemetry; items still written (crossing 15 = >15)
        state = GraphState(value_nodes_created=14)
        payload = _payload_with([_point(99), _point(98)])
        plan = plan_commit(payload, state, record=None)
        assert plan.budget.outcome == "ok"
        assert plan.budget.warn is True

    def test_at_15_no_warn(self):
        # warn fires strictly ABOVE the soft band (15 == soft → no warn)
        payload = _payload_with([_point(99)])
        plan = plan_commit(payload, GraphState(value_nodes_created=14),
                           record=None)
        assert plan.budget.outcome == "ok"
        assert plan.budget.warn is False
        plan2 = plan_commit(payload, GraphState(value_nodes_created=13),
                            record=None)
        assert plan2.budget.warn is False

    def test_supersede_only_delta_exempts_budget(self):
        # R-14 bump-then-re-capture: after a brief/calibration change,
        # re-extract an old session — supersede-only delta does NOT increment
        # net-new → budget does not exhaust
        state = GraphState(
            value_nodes_created=45,
            points={"pt_0000000000000000000000000000000000000000000000000000000000000000": "old X"})
        payload = _payload_with([_point(0, content="new Y")])
        plan = plan_commit(payload, state, record=None)
        assert plan.reconcile.net_new == 0
        assert plan.budget.outcome == "ok"
        assert plan.budget.cumulative_after == 45

    def test_merge_dedup_burns_zero(self):
        state = GraphState(
            value_nodes_created=49,
            points={"pt_0000000000000000000000000000000000000000000000000000000000000000": "point 0"})
        payload = _payload_with([_point(0)])
        plan = plan_commit(payload, state, record=None)
        assert plan.reconcile.net_new == 0
        assert plan.budget.outcome == "ok"
        assert plan.budget.cumulative_after == 49

    def test_episodic_session_flag_is_not_a_budget_exemption(self):
        """The Session's is_episodic flag is the QUOTA discriminator — NOT a
        budget exemption (review fix, PR #953: the previous exemption let a
        held re-submission bypass the ceiling — Session C, DE2E-7). Value
        points from the commit endpoint are non-episodic and always count."""
        state = GraphState(is_episodic=True, value_nodes_created=45)
        payload = _payload_with(_ten_new_points())
        plan = plan_commit(payload, state, record=None)
        assert plan.budget.outcome == "fail"  # 45 + 10 = 55 > 50 ceiling
        assert "ceiling" in (plan.budget.reason or "")

    def test_held_payload_reported_point_ids(self):
        state = GraphState(value_nodes_created=20)
        payload = _payload_with(_ten_new_points())
        plan = plan_commit(payload, state, record=None)
        assert plan.budget.held_point_ids == [p.id for p in payload.points]


class TestL1Replay:
    def test_fully_written_is_duplicate(self):
        record = CommitRecordState(
            client_commit_id="cid", session_id="s1", status="fully_written",
            write_ops_billed=1)
        assert is_l1_replay(record)
        state = GraphState(value_nodes_created=30)
        payload = _payload_with(_ten_new_points())
        plan = plan_commit(payload, state, record=record)
        assert plan.duplicate is True
        assert plan.reconcile is None
        assert plan.budget.outcome == "ok"
        assert "duplicate" in (plan.budget.reason or "")

    def test_held_is_not_replay(self):
        record = CommitRecordState(
            client_commit_id="cid", session_id="s1", status="held")
        assert not is_l1_replay(record)

    def test_partial_is_not_replay(self):
        record = CommitRecordState(
            client_commit_id="cid", session_id="s1", status="partial")
        assert not is_l1_replay(record)
        assert not is_first_adjudication(record)

    def test_no_record_is_first_adjudication(self):
        assert is_first_adjudication(None)


# ── :CommitRecord store (embedded; concurrency = live-only) ───────────────

@pytest.fixture
def store_sdk(tmp_path, monkeypatch):
    """Embedded tenant SDK on a fresh temp DB (conftest convention)."""
    import os

    from tortoise.sdk import TortoiseSDK
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "commit.db"))
    sdk = TortoiseSDK(str(tmp_path / "commit.db"), namespace="test_952")
    yield sdk
    sdk.close()


class TestCommitRecordStore:
    def test_acquire_creates_and_returns(self, store_sdk):
        from tortoise.commit_idempotency import CommitRecordStore
        store = CommitRecordStore(store_sdk)
        record, created = store.acquire(
            "cid-1", session_id="s1", status="held", write_ops_billed=0)
        assert created is True
        assert record.client_commit_id == "cid-1"
        assert record.session_id == "s1"
        assert record.status == "held"
        assert record.write_ops_billed == 0
        assert record.written_at is not None

    def test_acquire_existing_returns_winner(self, store_sdk):
        from tortoise.commit_idempotency import CommitRecordStore
        store = CommitRecordStore(store_sdk)
        store.acquire("cid-2", session_id="s1", status="held",
                      write_ops_billed=0)
        # concurrent identical commit → loser sees the winner's record
        record, created = store.acquire(
            "cid-2", session_id="s1", status="held", write_ops_billed=0)
        assert created is False
        assert record.status == "held"
        assert store.get("cid-2").status == "held"

    def test_get_missing_returns_none(self, store_sdk):
        from tortoise.commit_idempotency import CommitRecordStore
        store = CommitRecordStore(store_sdk)
        assert store.get("nope") is None

    def test_update_transition_held_to_fully_written(self, store_sdk):
        from tortoise.commit_idempotency import CommitRecordStore
        store = CommitRecordStore(store_sdk)
        store.acquire("cid-3", session_id="s1", status="held",
                      write_ops_billed=0)
        updated = store.update("cid-3", status="fully_written")
        assert updated.status == "fully_written"
        assert store.get("cid-3").status == "fully_written"

    def test_update_missing_returns_none(self, store_sdk):
        from tortoise.commit_idempotency import CommitRecordStore
        store = CommitRecordStore(store_sdk)
        assert store.update("nope", status="fully_written") is None

    def test_invalid_status_fails_fast(self, store_sdk):
        from tortoise.commit_idempotency import CommitRecordStore
        store = CommitRecordStore(store_sdk)
        with pytest.raises(ValueError):
            store.acquire("cid-4", session_id="s1", status="bogus")
        with pytest.raises(ValueError):
            store.update("cid-4", status="bogus")


class TestConcurrency:
    """DE2E-7 negative case (a): concurrent identical commits — the loser of
    the :CommitRecord MERGE sees duplicate:true. LIVE FalkorDB only
    (skip-guarded on embedded — redislite is not multi-connection-safe)."""

    def test_concurrent_identical_acquire_single_winner(self, tmp_path):
        from _live_utils import _skip_unless_live_uri
        _skip_unless_live_uri()

        import os
        from concurrent.futures import ThreadPoolExecutor

        from tortoise.commit_idempotency import CommitRecordStore
        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK(namespace="test_commit_schema")
        try:
            store = CommitRecordStore(sdk)
            cid = "concurrent-cid"

            def hammer(_):
                record, created = store.acquire(
                    cid, session_id="s1", status="fully_written",
                    write_ops_billed=1)
                return created

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(hammer, range(16)))
            assert results.count(True) == 1, (
                f"exactly one MERGE winner expected, got {results.count(True)}"
            )
            assert results.count(False) == 15
            assert store.get(cid).status == "fully_written"
        finally:
            sdk.close()
