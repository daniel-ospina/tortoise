"""Tests for the domain integrity constraint system (issue #405).

Approved scope 2026-08-15: JTBD canonical chain (manifest step-0),
constraint-registration API on domain_loader, import-time validators
(domain_validators.py), additive warn-first warnings[] on the commit 200,
`tortoise validate --domain` CLI (exit 0/1/2/3), MCP tortoise_validate_domain,
actionable messages (rule/kind/ref/fix), draft exclusion.

Runnable with: .venv/bin/python -m pytest tests/test_domain_validators.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from tortoise.domain_loader import (
    SURFACE_GRAPH,
    SURFACE_PAYLOAD_LOCAL,
    domain_chain_spec,
    domain_validator,
    domain_validators,
    known_domains,
    pack_kind_overlap,
    register_domain_validator,
)
from tortoise.sdk import TortoiseSDK

# ── No-DB helpers ──────────────────────────────────────────────────────────

EXPECTED_CHAIN = [
    "jobToBeDone", "useCase", "feature", "userJourney",
    "workflow", "requirement", "architecture",
]


def _mk_point(i: int, kind: str) -> dict:
    return {
        "id": f"pt_{i:064d}", "content": f"point {i}", "pointKind": kind,
        "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
        "about_entities": ["Alpha"], "source_ref": "session.md",
        "quote": "", "status": "live",
    }


_TELEMETRY = {
    "extractor": {"version": "v1", "mode": "byok"},
    "model": {"provider": "x", "id": "y", "cfg_hash": "h"},
    "counts": {"kept": 5, "candidate": 10, "segment": 12, "window": 3,
               "empty_windows": 0},
    "keep_ratio": 0.5, "dedup_hits": 0, "frontier_calls": 1,
    "llm_cost_usd": 0.02, "extraction_ms": 1, "retry_count": 0,
    "last_error_code": None, "confidence_histogram": [0] * 10,
}


def _raw_payload(points: list[dict], operators: list[dict] | None = None,
                 **overrides) -> dict:
    """Raw §6.1 dict with an EMPTY client_commit_id (finalize computes it)."""
    from tortoise.commit_schema import compute_client_commit_id
    payload = {
        "schema_version": "1", "session_id": "s1", "client_commit_id": "",
        "captured_at": "2026-08-11T10:00:00Z",
        "extractor": {"version": "value@1.0.0", "mode": "byok",
                      "calibration_version": "v3"},
        "summary": "summary", "story_arc": "arc",
        "provenance_refs": [{"path": "session.md", "spans": ["0-10"]}],
        "sources": [],
        "entities": [{"name": "Alpha", "kind": "Project",
                      "passes_frequency_gate": True}],
        "points": points, "operators": operators or [],
        "telemetry": _TELEMETRY,
    }
    payload.update(overrides)
    payload["client_commit_id"] = compute_client_commit_id(
        payload["session_id"], payload["points"], payload["entities"],
        payload["operators"], payload["summary"], payload["story_arc"])
    return payload


def _impl_op(src_i: int, dst_i: int) -> dict:
    return {"src": f"pt_{src_i:064d}", "dst": f"pt_{dst_i:064d}",
            "op_type": "IMPL", "direction": "bidirectional"}


# ── 1. Manifest chain (JTBD canonical-chain decision) ──────────────────────

class TestManifestChain:
    def test_product_delivery_steps_have_jtbd_at_step_zero(self):
        """Decision 2026-08-15: jobToBeDone is step-0 of productDelivery."""
        spec = domain_chain_spec("product-strategy")
        chain = spec.get("productDelivery")
        assert chain is not None
        assert chain["steps"] == EXPECTED_CHAIN

    def test_chain_enforcement_is_warn(self):
        spec = domain_chain_spec("product-strategy")
        assert spec["productDelivery"]["enforcement"] == "warn"


# ── 2. Constraint-registration API ─────────────────────────────────────────

class TestRegistry:
    def test_register_and_discover(self):
        seen = []

        def _fn(graph):
            seen.append(graph)
            return []

        register_domain_validator("test-reg-405", chain_id="c1",
                                  surface=SURFACE_GRAPH, fn=_fn)
        specs = domain_validators("test-reg-405", surface=SURFACE_GRAPH)
        assert len(specs) == 1
        assert specs[0]["domain"] == "test-reg-405"
        assert specs[0]["chain_id"] == "c1"
        assert specs[0]["surface"] == SURFACE_GRAPH
        assert specs[0]["fn"] is _fn

    def test_decorator_form(self):
        @domain_validator("test-deco-405", chain_id="c2", surface=SURFACE_GRAPH)
        def _deco(graph):
            return [{"rule": "x", "ref": "y"}]

        specs = domain_validators("test-deco-405")
        assert any(s["fn"] is _deco for s in specs)
        assert _deco(None) == [{"rule": "x", "ref": "y"}]

    def test_surface_filtering(self):
        def _g(graph):
            return []

        def _p(payload):
            return []

        register_domain_validator("test-surf-405", chain_id="g",
                                  surface=SURFACE_GRAPH, fn=_g)
        register_domain_validator("test-surf-405", chain_id="p",
                                  surface=SURFACE_PAYLOAD_LOCAL, fn=_p)
        assert {s["surface"] for s in domain_validators("test-surf-405")} \
            == {SURFACE_GRAPH, SURFACE_PAYLOAD_LOCAL}
        assert [s["surface"] for s in domain_validators(
            "test-surf-405", surface=SURFACE_PAYLOAD_LOCAL)] \
            == [SURFACE_PAYLOAD_LOCAL]

    def test_unknown_domain_empty(self):
        assert domain_validators("no-such-domain-405") == []

    def test_idempotent_registration(self):
        def _fn(graph):
            return []

        register_domain_validator("test-idem-405", chain_id="c", fn=_fn)
        register_domain_validator("test-idem-405", chain_id="c", fn=_fn)
        assert len(domain_validators("test-idem-405")) == 1

    def test_discovery_returns_copy(self):
        def _fn(graph):
            return []

        register_domain_validator("test-copy-405", chain_id="c", fn=_fn)
        specs = domain_validators("test-copy-405")
        specs.clear()
        assert domain_validators("test-copy-405")

    def test_invalid_surface_rejected(self):
        with pytest.raises(ValueError):
            register_domain_validator("x-405", surface="bogus", fn=lambda g: [])
        with pytest.raises(ValueError):
            domain_validators("x-405", surface="bogus")

    def test_requires_fn(self):
        with pytest.raises(ValueError):
            register_domain_validator("x-405")  # type: ignore[call-overload]

    def test_known_domains_includes_product_strategy(self):
        assert "product-strategy" in known_domains()

    def test_pack_kind_overlap(self):
        assert pack_kind_overlap(
            "product-strategy", "pointKind",
            ["product-strategy:useCase", "statement"]) == 1
        assert pack_kind_overlap(
            "product-strategy", "pointKind", ["statement"]) == 0

    def test_import_time_registrations_present(self):
        """Production validators register at import (domain_validators)."""
        import tortoise.domain_validators  # noqa: F401
        graph_fns = {s["fn"].__name__ for s in domain_validators(
            "product-strategy", surface=SURFACE_GRAPH)}
        payload_fns = {s["fn"].__name__ for s in domain_validators(
            "product-strategy", surface=SURFACE_PAYLOAD_LOCAL)}
        assert "validate_chain_integrity" in graph_fns
        assert "validate_payload_intra_chain" in payload_fns


# ── 3. Payload-local rules (no graph I/O) ──────────────────────────────────

class TestPayloadLocalRules:
    def test_leapfrog_warns_but_ok(self):
        from tortoise.commit_schema import validate_payload_dict
        raw = _raw_payload(
            [_mk_point(1, "product-strategy:useCase"),
             _mk_point(2, "product-strategy:userJourney")],
            [_impl_op(1, 2)])
        result, payload = validate_payload_dict(raw)
        assert result.ok is True  # warn-first: the write proceeds
        assert len(result.warnings) == 1
        w = result.warnings[0]
        assert w["rule"] == "leapfrog_chain_step"
        assert w["kind"] == "useCase"
        assert w["ref"].startswith("pt_")
        assert "feature" in w["message"]  # skipped intermediate step
        assert w["fix"]
        assert w["severity"] == "warn"  # productDelivery enforcement: warn

    def test_adjacent_chain_edge_no_warning(self):
        from tortoise.commit_schema import validate_payload_dict
        raw = _raw_payload(
            [_mk_point(1, "product-strategy:jobToBeDone"),
             _mk_point(2, "product-strategy:useCase")],
            [_impl_op(1, 2)])
        result, _ = validate_payload_dict(raw)
        assert result.ok is True
        assert result.warnings == []

    def test_backref_exempt(self):
        """Later-step → earlier-step operator edges are legitimate (exempt)."""
        from tortoise.commit_schema import validate_payload_dict
        raw = _raw_payload(
            [_mk_point(1, "product-strategy:useCase"),
             _mk_point(2, "product-strategy:userJourney")],
            [_impl_op(2, 1)])
        result, _ = validate_payload_dict(raw)
        assert result.ok is True
        assert result.warnings == []

    def test_clean_payload_empty_warnings(self):
        from tortoise.commit_schema import validate_payload_dict
        raw = _raw_payload([_mk_point(1, "decision")])
        result, _ = validate_payload_dict(raw)
        assert result.ok is True
        assert result.warnings == []

    def test_domain_inference_from_kinds(self):
        """A payload of product-strategy kinds infers the domain → rules run."""
        from tortoise.commit_schema import validate_payload_dict
        raw = _raw_payload(
            [_mk_point(1, "product-strategy:useCase"),
             _mk_point(2, "product-strategy:userJourney")],
            [_impl_op(1, 2)])
        result, _ = validate_payload_dict(raw)
        assert result.warnings, "expected inferred-domain leapfrog warning"

    def test_layer1_result_warnings_field_defaults_empty(self):
        from tortoise.commit_schema import Layer1Result
        assert Layer1Result(ok=True).warnings == []

    def test_validate_domain_rules_skips_unknown_domain(self):
        from tortoise.commit_schema import validate_domain_rules
        from tortoise.commit_schema import CommitPayload
        payload = CommitPayload.model_validate(_raw_payload([_mk_point(1, "decision")]))
        assert validate_domain_rules(payload, domain=None) == []


# ── 4. Phase B (block severity — wired-but-inactive in prod) ───────────────

class TestPhaseB:
    def test_domain_block_warnings_selector(self):
        from tortoise.commit_schema import domain_block_warnings
        warnings = [
            {"rule": "a", "severity": "warn"},
            {"rule": "b", "severity": "block"},
            {"rule": "c", "severity": "retry"},
        ]
        blocking = domain_block_warnings(warnings)
        assert [w["rule"] for w in blocking] == ["b"]

    def test_severity_resolves_from_manifest(self, monkeypatch):
        """A synthetic block chain → warnings stamped block (Phase B fires)."""
        from tortoise import domain_validators as dv
        from tortoise.commit_schema import (
            CommitPayload, validate_domain_rules,
        )

        def _block_rule(payload):
            return [{"rule": "synthetic_block", "kind": "useCase",
                     "ref": "pt_x", "message": "synthetic", "fix": "none"}]

        register_domain_validator(
            "test-block-405", chain_id="blockChain",
            surface=SURFACE_PAYLOAD_LOCAL, fn=_block_rule)
        monkeypatch.setattr(
            dv, "domain_chain_spec",
            lambda domain: {"blockChain": {
                "id": "blockChain", "name": "B", "steps": ["a", "b"],
                "enforcement": "block"}})

        payload = CommitPayload.model_validate(
            _raw_payload([_mk_point(1, "decision")]))
        warnings = validate_domain_rules(payload, domain="test-block-405")
        assert warnings[0]["severity"] == "block"


# ── 5. Graph validators (embedded FalkorDBLite) ────────────────────────────

def _make_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_405_"), "t.db")
    return TortoiseSDK(db_path)


def _seed_chain_violations(sdk: TortoiseSDK) -> dict:
    """Seed a fixture with every violation class + the clean control cases.

    Returns {id → point dict} for the seeded points.
    """
    ids = {}
    # Live orphan useCase (fires orphan_use_case)
    ids["orphan_uc"] = sdk.create_point(
        "product-strategy:useCase", "orphan uc", uc_id="UC-ORPH-405",
        status="live")
    # Draft orphan useCase (must be EXCLUDED from orphan_use_case — draft rule)
    ids["draft_uc"] = sdk.create_point(
        "product-strategy:useCase", "draft orphan uc", uc_id="UC-DRAFT-405")
    # Properly parented chain (clean control)
    ids["jtbd"] = sdk.create_point(
        "product-strategy:jobToBeDone", "JTBD", jtbd_id="JTBD-405",
        status="live")
    ids["wired_uc"] = sdk.create_point(
        "product-strategy:useCase", "wired uc", uc_id="UC-WIRED-405",
        status="live")
    sdk.create_operator("composedOf", ids["jtbd"]["id"], [ids["wired_uc"]["id"]])
    # Dangling refs (live)
    ids["uj"] = sdk.create_point(
        "product-strategy:userJourney", "uj dangling",
        covered_use_cases="UC-MISSING-405", status="live")
    ids["wf"] = sdk.create_point(
        "workflow", "wf dangling", enables_jtbd="JTBD-MISSING-405",
        status="live")
    ids["req"] = sdk.create_point(
        "dev:requirement", "req dangling", enabled_workflow="WF-MISSING-405",
        status="live")
    return ids


def _legacy_check_structure(graph) -> list[dict]:
    """VERBATIM copy of the pre-#405 sdk.check_structure (reference for the
    wrapper-equivalence test — the migration must not change behavior on
    live-only fixtures)."""
    violations: list[dict] = []
    from tortoise.sdk import _get_kind_expander
    expander = _get_kind_expander()
    uc_kind = expander.expand_kind("useCase")
    jtbd_kind = expander.expand_kind("jobToBeDone")
    uj_kind = expander.expand_kind("userJourney")
    wf_kind = expander.expand_kind("workflow")
    req_kind = expander.expand_kind("requirement")

    def kind_in(kinds):
        return ", ".join(f"'{k}'" for k in kinds)

    ucs = graph.g.query(
        f"MATCH (uc:Point) WHERE uc.pointKind IN [{kind_in(uc_kind)}] RETURN uc.id, uc.uc_id"
    ).result_set
    for uc_id, uc_ref in ucs:
        parents = graph.g.query(
            f"MATCH (op:Point {{is_operator:true, op_type:'composedOf'}})"
            f"-[:hasPart]->(uc:Point {{id:$id}}), "
            f"(op)-[:hasPart]->(jtbd:Point) WHERE jtbd.pointKind IN [{kind_in(jtbd_kind)}] "
            f"RETURN jtbd.id",
            params={"id": uc_id},
        ).result_set
        if not parents:
            violations.append({
                "type": "orphan_use_case", "id": uc_id,
                "message": f"useCase {uc_ref or uc_id} has no parent JTBD",
            })
    for uj_id, covered in graph.g.query(
        f"MATCH (uj:Point) WHERE uj.pointKind IN [{kind_in(uj_kind)}] RETURN uj.id, uj.covered_use_cases"
    ).result_set:
        if not covered:
            continue
        for uc_ref in covered.split(","):
            uc_ref = uc_ref.strip()
            if not graph.g.query(
                f"MATCH (uc:Point) WHERE uc.pointKind IN [{kind_in(uc_kind)}] AND uc.uc_id=$ref RETURN count(uc) > 0",
                params={"ref": uc_ref},
            ).result_set[0][0]:
                violations.append({
                    "type": "dangling_use_case_ref", "id": uj_id,
                    "message": f"userJourney {uj_id} refs non-existent useCase {uc_ref}",
                })
    for wf_id, enables in graph.g.query(
        f"MATCH (wf:Point) WHERE wf.pointKind IN [{kind_in(wf_kind)}] RETURN wf.id, wf.enables_jtbd"
    ).result_set:
        if not enables:
            continue
        for jtbd_ref in enables.split(","):
            jtbd_ref = jtbd_ref.strip()
            if not graph.g.query(
                f"MATCH (j:Point) WHERE j.pointKind IN [{kind_in(jtbd_kind)}] AND j.jtbd_id=$ref RETURN count(j) > 0",
                params={"ref": jtbd_ref},
            ).result_set[0][0]:
                violations.append({
                    "type": "dangling_jtbd_ref", "id": wf_id,
                    "message": f"workflow {wf_id} refs non-existent JTBD {jtbd_ref}",
                })
    for req_id, wf_ref in graph.g.query(
        f"MATCH (req:Point) WHERE req.pointKind IN [{kind_in(req_kind)}] RETURN req.id, req.enabled_workflow"
    ).result_set:
        if not wf_ref or wf_ref == "ALL":
            continue
        if not graph.g.query(
            f"MATCH (w:Point) WHERE w.pointKind IN [{kind_in(wf_kind)}] AND w.wf_id=$ref RETURN count(w) > 0",
            params={"ref": wf_ref},
        ).result_set[0][0]:
            violations.append({
                "type": "dangling_workflow_ref", "id": req_id,
                "message": f"requirement {req_id} refs non-existent workflow {wf_ref}",
            })
    for row in graph.g.query(
        "MATCH (n:Point {status:'draft'}) "
        "WHERE n.is_operator = false "
        "AND NOT (n)--() "
        "RETURN n.id, n.content, n.pointKind, n.createdAt "
        "ORDER BY n.createdAt"
    ).result_set:
        violations.append({
            "type": "orphaned_draft", "id": row[0],
            "message": (
                f"Draft point '{row[1][:80] if row[1] else ''}' "
                f"of kind '{row[2] or 'unknown'}' has no edges "
                f"(created {row[3] or 'unknown'})"
            ),
        })
    return violations


@pytest.fixture
def graph_sdk():
    """Embedded SDK; skips when FalkorDBLite is unavailable."""
    try:
        from tests._embedded import has_falkor
        if not has_falkor():
            pytest.skip("FalkorDBLite not available")
    except Exception:
        pytest.skip("FalkorDBLite not available")
    sdk = _make_sdk()
    yield sdk
    sdk.close()


class TestGraphValidators:
    def test_orphan_use_case_fires(self, graph_sdk):
        ids = _seed_chain_violations(graph_sdk)
        res = graph_sdk.validate_domain("product-strategy")
        orphans = [v for v in res["violations"]
                   if v["rule"] == "orphan_use_case"]
        assert any(v["ref"] == ids["orphan_uc"]["id"] for v in orphans)

    def test_draft_points_excluded_from_chain_rules(self, graph_sdk):
        """Draft orphan useCase → NOT orphan_use_case (draft exclusion)."""
        ids = _seed_chain_violations(graph_sdk)
        res = graph_sdk.validate_domain("product-strategy")
        draft_id = ids["draft_uc"]["id"]
        chain_rules = [v for v in res["violations"]
                       if v["rule"] in ("orphan_use_case",
                                        "dangling_use_case_ref",
                                        "dangling_jtbd_ref",
                                        "dangling_workflow_ref")]
        assert not any(v["ref"] == draft_id for v in chain_rules)
        # ... but the draft IS flagged by orphaned_draft (its rule's job)
        drafts = [v for v in res["violations"] if v["rule"] == "orphaned_draft"]
        assert any(v["ref"] == draft_id for v in drafts)

    def test_dangling_refs_fire(self, graph_sdk):
        ids = _seed_chain_violations(graph_sdk)
        res = graph_sdk.validate_domain("product-strategy")
        rules = {v["rule"] for v in res["violations"]}
        assert {"dangling_use_case_ref", "dangling_jtbd_ref",
                "dangling_workflow_ref"} <= rules

    def test_wired_use_case_not_orphan(self, graph_sdk):
        ids = _seed_chain_violations(graph_sdk)
        res = graph_sdk.validate_domain("product-strategy")
        wired_v = [v for v in res["violations"]
                   if v["ref"] == ids["wired_uc"]["id"]]
        assert not any(v["rule"] == "orphan_use_case" for v in wired_v)

    def test_actionable_fields(self, graph_sdk):
        ids = _seed_chain_violations(graph_sdk)
        res = graph_sdk.validate_domain("product-strategy")
        assert res["violations"], "expected seeded violations"
        for v in res["violations"]:
            assert {"rule", "kind", "ref", "message", "fix"} <= set(v)

    def test_drift_empty_for_product_strategy(self, graph_sdk):
        res = graph_sdk.validate_domain("product-strategy")
        assert res["drift"] == []

    def test_unknown_domain_raises(self, graph_sdk):
        with pytest.raises(ValueError):
            graph_sdk.validate_domain("no-such-domain-405")

    def test_wrapper_equivalence_with_legacy(self, graph_sdk):
        """sdk.check_structure (registry delegation) == legacy implementation
        on a live-points fixture (output contract + messages preserved)."""
        _seed_chain_violations(graph_sdk)
        proj = graph_sdk._get_proj()
        new = graph_sdk.check_structure()
        legacy = _legacy_check_structure(proj)
        # Draft-exclusion divergence: the draft orphan is orphaned_draft-only
        # in the NEW output; legacy double-flags it as orphan_use_case.
        legacy_without_draft_orphan = [
            v for v in legacy
            if not (v["type"] == "orphan_use_case"
                    and "UC-DRAFT-405" in v["message"])
        ]
        assert new == legacy_without_draft_orphan

    def test_empty_db_clean(self, graph_sdk):
        res = graph_sdk.validate_domain("product-strategy")
        assert res["ok"] is True
        assert res["violations"] == []


# ── 6. CLI (tortoise validate) ─────────────────────────────────────────────

def _cli_args(domain: str, db: str, *, json: bool = False,
              warn_only: bool = False):
    import argparse
    return argparse.Namespace(domain=domain, db=db, json=json,
                              warn_only=warn_only)


class TestValidateCLI:
    def test_clean_exit_zero(self, graph_sdk):
        from tortoise.__main__ import _cmd_validate
        assert _cmd_validate(_cli_args("product-strategy",
                                       graph_sdk._db_path)) == 0

    def test_violations_exit_one(self, graph_sdk):
        from tortoise.__main__ import _cmd_validate
        _seed_chain_violations(graph_sdk)
        assert _cmd_validate(_cli_args("product-strategy",
                                       graph_sdk._db_path)) == 1

    def test_warn_only_exits_zero(self, graph_sdk):
        from tortoise.__main__ import _cmd_validate
        _seed_chain_violations(graph_sdk)
        assert _cmd_validate(_cli_args("product-strategy", graph_sdk._db_path,
                                       warn_only=True)) == 0

    def test_unknown_domain_exit_two(self, graph_sdk):
        from tortoise.__main__ import _cmd_validate
        assert _cmd_validate(_cli_args("nope-405", graph_sdk._db_path)) == 2

    def test_registered_validator_domain_not_unknown(self, graph_sdk):
        """A domain with a registered validator runs even when no pack is
        loaded (pip-installed wheel without packs/ — the registry is the
        source of truth, not the pack dir)."""
        from tortoise.__main__ import _cmd_validate

        def _fn(graph):
            return []

        register_domain_validator("test-cli-405", chain_id="c",
                                  surface=SURFACE_GRAPH, fn=_fn)
        assert _cmd_validate(_cli_args("test-cli-405",
                                       graph_sdk._db_path)) == 0

    def test_missing_domain_exit_two(self, graph_sdk):
        from tortoise.__main__ import _cmd_validate
        assert _cmd_validate(_cli_args("", graph_sdk._db_path)) == 2

    def test_runtime_failure_exit_three(self, graph_sdk, monkeypatch):
        from tortoise import __main__ as m
        from tortoise.__main__ import _cmd_validate
        monkeypatch.setattr(m, "_projection_for",
                            lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
        assert _cmd_validate(_cli_args("product-strategy",
                                       graph_sdk._db_path)) == 3

    def test_json_output_contract(self, graph_sdk, capsys):
        import json
        from tortoise.__main__ import _cmd_validate
        _seed_chain_violations(graph_sdk)
        rc = _cmd_validate(_cli_args("product-strategy", graph_sdk._db_path,
                                     json=True))
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert out["domain"] == "product-strategy"
        assert out["ok"] is False
        assert out["violations"]
        assert "chains" in out
        assert out["chains"]["productDelivery"]["steps"] == EXPECTED_CHAIN


# ── 7. MCP tool ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _transport_context():
    """MCP tools require an initialized transport mode (#236 auth gate)."""
    from tortoise.mcp_auth import (
        _current_team_id, _current_team_limits, _transport_mode,
    )
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_team_id.set(None)
    _current_team_limits.set(None)


class TestMCPValidateTool:
    def test_registry_entry_readonly(self):
        from tortoise.tool_registry import TOOL_REGISTRY
        entry = next(t for t in TOOL_REGISTRY
                     if t.name == "tortoise_validate_domain")
        assert entry.annotations.readOnlyHint is True
        assert entry.sdk_method == "validate_domain"

    def test_handler_returns_violations(self, graph_sdk):
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import tortoise_validate_domain
        ids = _seed_chain_violations(graph_sdk)
        orig_sdk = mcp_mod.sdk
        mcp_mod.sdk = graph_sdk
        try:
            res = tortoise_validate_domain("product-strategy")
        finally:
            mcp_mod.sdk = orig_sdk
        assert res["ok"] is False
        assert any(v["rule"] == "orphan_use_case"
                   and v["ref"] == ids["orphan_uc"]["id"]
                   for v in res["violations"])

    def test_handler_unknown_domain_error(self, graph_sdk):
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import tortoise_validate_domain
        orig_sdk = mcp_mod.sdk
        mcp_mod.sdk = graph_sdk
        try:
            res = tortoise_validate_domain("nope-405")
        finally:
            mcp_mod.sdk = orig_sdk
        assert "error" in res


# ── 8. Commit endpoint: warnings[] on the 200 (additive) ───────────────────

def _patch_tortoise_sdk_init(db_path: str):
    import tortoise.hosted_api as ha_mod
    _orig = ha_mod.TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched
    return _orig


@pytest.fixture
def commit_client():
    from fastapi.testclient import TestClient
    from tortoise.hosted_api import app, get_current_team

    os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
    os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": "test-team-405", "key_id": "k", "tier": "free",
            "max_users": 1, "max_graphs": 1, "max_points": 10000,
            "max_api_keys": 2, "max_sessions": 1000}
        _orig = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore = _orig
            import tortoise.hosted_api as ha_mod
            ha_mod.TortoiseSDK.__init__ = _restore
            app.dependency_overrides.clear()


class TestCommitEndpointWarnings:
    def test_clean_commit_has_empty_warnings(self, commit_client):
        raw = _raw_payload([_mk_point(1, "decision")])
        r = commit_client.post("/v1/sessions/commit", json=raw)
        assert r.status_code == 200
        body = r.json()
        assert "warnings" in body
        assert body["warnings"] == []

    def test_leapfrog_commit_200_with_warnings(self, commit_client):
        """Warn-first: a leapfrog payload WRITES (200) and carries warnings[]."""
        raw = _raw_payload(
            [_mk_point(1, "product-strategy:useCase"),
             _mk_point(2, "product-strategy:userJourney")],
            [_impl_op(1, 2)])
        r = commit_client.post("/v1/sessions/commit", json=raw)
        assert r.status_code == 200
        body = r.json()
        assert body["warnings"], "expected leapfrog warnings on the 200"
        assert body["warnings"][0]["rule"] == "leapfrog_chain_step"
        # the write went through
        assert body["duplicate"] is False

    def test_block_severity_rejects_422(self, commit_client, monkeypatch):
        """Phase B (wired-but-inactive in prod): block-severity warning → 422."""
        import tortoise.commit_schema as cs
        from tortoise import hosted_api as ha

        def _blocking(payload, domain=None):
            return [{"rule": "synthetic_block", "kind": "useCase",
                     "ref": "pt_x", "message": "block", "fix": "none",
                     "severity": "block"}]

        monkeypatch.setattr(cs, "validate_domain_rules", _blocking)
        raw = _raw_payload([_mk_point(1, "decision")])
        r = commit_client.post("/v1/sessions/commit", json=raw)
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "domain_rule_block"
