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

These tests used to drive the REAL capture commit (``sdk.capture_session`` →
``_extract_session_v2``) with the extractor seam monkeypatched to a fixed
payload. ⚠️ #4282 deleted that SDK door, so those five end-to-end tests are gone;
what survives is ``TestRemapHelpers4716``, which pins the two shared
``commit_ops`` helpers directly — the invariant (a payload id that resolves to a
different graph id must be remapped before an operator edge is written) is what
those helpers own, and the excised arms are recorded in #10226.
"""
from __future__ import annotations

from tortoise.commit_ops import (
    remap_operator_endpoint_refs,
    remap_supersession_point_refs,
)

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
