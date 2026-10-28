"""#4716 Part 1 — the v2 capture commit remaps payload endpoint refs.

Two id spaces meet at the capture commit. The payload's points carry
content-addressed ``pt_<sha>`` ids; the commit loop re-resolves each one with
``_find_point_by_content`` and keeps the EXISTING node's id on a hit
(``resolved = hit_id``, ``create_point`` deliberately not called). Operator
``src``/``dst`` and the MITIGATES ``target`` triple used to travel to
``create_operator`` verbatim, so a payload id that resolved to a different
graph id named nothing: ``create_operator`` raised and
``apply_payload_operators`` swallowed it as ``operator write skipped (inputs
missing?)`` — the silent edge drop of #4654.

The fix restores, on the v2 capture path, the invariant the v1 payload builder
``_stream_to_payload`` already enforces (#1272). The #4716 review **falsified**
an earlier claim that the hosted commit was immune: its re-key was then not fed
back into the operator refs, and that residual — tracked in #4970 — is now
CLOSED. The hosted §5 point loop surfaces the resolved ids and §7 passes the
refs through the SAME shared ``commit_ops`` helpers these tests cover, so both
write paths satisfy the graph-id precondition.

These tests drive the REAL capture commit (``sdk.capture_session`` →
``_extract_session_v2``) with the extractor seam monkeypatched to a fixed
payload — no LLM, no network. The pre-existing node is created with a
non-payload id so ``_find_point_by_content`` genuinely re-keys.
"""
from __future__ import annotations

import pytest

from tortoise.commit_ops import (
    remap_operator_endpoint_refs,
    remap_supersession_point_refs,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture(autouse=True)
def _mock_provider(monkeypatch):
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


CONV = [
    {"role": "user", "content": "a session whose extraction seam is fixed"},
    {"role": "assistant", "content": "noted"},
]


def _pt(pid: str, content: str, kind: str = "statement") -> dict:
    return {"id": pid, "content": content, "pointKind": kind,
            "reason": "NEW", "confidence": 0.5, "c_cal": 0.5,
            "about_entities": [], "source_ref": "session.md", "quote": "",
            "status": "draft", "search_keys": []}


def _payload(points, operators, supersessions=None) -> dict:
    return {"session_id": "s4716", "client_commit_id": "",
            "captured_at": "2026-09-23T00:00:00Z",
            "extractor": {"version": "t", "mode": "byok",
                          "calibration_version": "v2"},
            "summary": "", "story_arc": "", "provenance_refs": [],
            "sources": [], "entities": [], "points": points, "events": [],
            "operators": operators, "supersessions": supersessions or [],
            "telemetry": {"counts": {"kept": len(points)}}}


def _extract_returning(payload, noops=None):
    def _run(_model, _conversation=None, *, session_id=None, **_kw):
        return {"payload": payload, "errors": [], "warnings": [],
                "stats": {}, "noops": list(noops or []), "chain_notes": [],
                "link_before_create": [], "supersessions": [],
                "minted_kinds": [], "story_arc": "", "search": {},
                "error_census": {}}

    return _run


def _pre_existing_id(sdk, content: str, kind: str = "statement") -> str:
    """The graph id of a Point holding ``content`` — created OUTSIDE the
    capture (a non-payload id, e.g. the m2/ULID shape). ``kind`` must match the
    payload point's kind or the commit's kind-scoped resolver treats it as new
    (#784) and no re-key happens."""
    sdk.create_point(kind, content)
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {content:$c}) RETURN p.id",
        params={"c": content}).result_set
    assert rows, "pre-created Point not found"
    return rows[0][0]


def _impl_edges(proj) -> list[tuple[str, str]]:
    rows = proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
        "MATCH (o)-[:IMPL {idx:0}]->(s) MATCH (o)-[:IMPL {idx:1}]->(d) "
        "RETURN s.id, d.id").result_set
    return [(r[0], r[1]) for r in rows]


# ── Part 1: the capture commit remap ────────────────────────────────────────


# ── Part 1: the pure helpers ────────────────────────────────────────────────


class TestRemapHelpers4716:
    def test_dict_operator_src_dst_and_target(self):
        ops = [{"src": "pt_a", "dst": "pt_b", "op_type": "MITIGATES",
                "target": {"src": "pt_b", "dst": "pt_a", "op_type": "IMPL"}}]
        out = remap_operator_endpoint_refs(
            ops, {"pt_a": "graph_a", "pt_b": "graph_b"})
        assert out[0]["src"] == "graph_a"
        assert out[0]["dst"] == "graph_b"
        assert out[0]["target"] == {"src": "graph_b", "dst": "graph_a",
                                    "op_type": "IMPL"}
        # the input is never mutated
        assert ops[0]["src"] == "pt_a"

    def test_unknown_refs_pass_through_untouched(self):
        """Total, not selective: an Event id, an already-canonical graph id or
        an empty ref is byte-identical — so the remap cannot damage the class
        of operators that never shared the hole."""
        ops = [{"src": "ev_123", "dst": "", "op_type": "IMPL"}]
        assert remap_operator_endpoint_refs(ops, {"pt_a": "graph_a"}) == ops

    def test_empty_map_is_identity(self):
        ops = [{"src": "pt_a", "dst": "pt_b", "op_type": "IMPL"}]
        assert remap_operator_endpoint_refs(ops, {}) == ops

    def test_accepts_commit_schema_operator_models(self):
        """The helper normalizes both shapes (like `_op_attr` / `_target_attr`
        in the same module) — the hosted path passes models, and a copy is
        returned rather than mutated."""
        from tortoise.commit_schema import Operator, OperatorTarget

        op = Operator(src="pt_a", dst="pt_b", op_type="MITIGATES",
                      target=OperatorTarget(src="pt_b", dst="pt_a"))
        out = remap_operator_endpoint_refs(
            [op], {"pt_a": "graph_a", "pt_b": "graph_b"})
        assert out[0].src == "graph_a" and out[0].dst == "graph_b"
        assert out[0].target.src == "graph_b"
        assert op.src == "pt_a"

    def test_supersession_remap_moves_only_supersedes_by(self):
        """Only the ref that is a payload id BY CONSTRUCTION is rewritten.
        ``superseded`` is the record's LANE DISCRIMINATOR downstream
        (``startswith("pt_")``), so it must NOT be remapped even when it IS
        in the map — otherwise a payload id mapping to a non-``pt_`` graph id
        could silently flip a CORRECTS fold onto the entity lane."""
        records = [{"superseded": "pt_old", "supersedes_by": "pt_new",
                    "evidence": "x"}]
        out = remap_supersession_point_refs(
            records, {"pt_old": "graph_old", "pt_new": "graph_new"})
        assert out[0] == {"superseded": "pt_old", "supersedes_by": "graph_new",
                          "evidence": "x"}
        assert records[0]["supersedes_by"] == "pt_new"

    def test_supersession_remap_empty_map_is_identity(self):
        records = [{"superseded": "a", "supersedes_by": "b"}]
        assert remap_supersession_point_refs(records, {}) == records
