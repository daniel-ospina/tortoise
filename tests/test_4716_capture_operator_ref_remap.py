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
``_stream_to_payload`` already enforces (#1272). It is scoped to the capture
commit — a scope decision made after the #4716 review **falsified** the earlier
claim that the hosted commit was immune: the hosted lane passes the raw payload
``Operator`` models and its ``create_point(dedup=True)`` re-key is not fed back
into the operator refs, so it reproduces the same drop (tracked in #4970).

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


def test_capture_commit_remaps_rekeyed_operator_endpoint(sdk, monkeypatch):
    """Indicator: 0 `operator write skipped (inputs missing?)` for an endpoint
    that resolves in-graph under a different id — the edge survives, and no
    duplicate Point is minted for the re-keyed content."""
    import tortoise.extractor_v2 as ev2

    prior = "the ingest queue has no backpressure control"
    other = "backpressure must be added before the next release"
    existing_id = _pre_existing_id(sdk, prior)
    prior_pid = ev2._content_id("pt", prior)
    other_pid = ev2._content_id("pt", other)
    assert existing_id != prior_pid, "the re-key must be genuine"
    payload = _payload(
        [_pt(prior_pid, prior), _pt(other_pid, other)],
        [{"src": prior_pid, "dst": other_pid, "op_type": "IMPL",
          "direction": "unidirectional"}])
    monkeypatch.setattr(ev2, "extract_session_v2", _extract_returning(payload))

    r = sdk.capture_session(CONV, session_id="s4716")
    assert r.get("ok") is True, r
    proj = sdk._get_proj()
    edges = _impl_edges(proj)
    assert edges == [(existing_id, other_pid)], (
        f"the operator edge must commit between the RESOLVED ids: {edges!r}")
    # exactly one Point holds the re-keyed content — no second Point
    assert proj.g.query(
        "MATCH (p:Point {content:$c}) RETURN count(p)",
        params={"c": prior}).result_set[0][0] == 1


def test_capture_commit_remaps_mitigates_target_triple(sdk, monkeypatch):
    """The MITIGATES ``target`` triple is the same id space — the v1 builder
    remaps it explicitly, and the capture commit must too, or the dampener
    deep-misses against a target edge that exists under the resolved id."""
    import tortoise.extractor_v2 as ev2

    risk = "clock skew between regions can make lease expiry unsafe"
    action = "a lagging region's renewal cannot clobber a live lease"
    existing_risk = _pre_existing_id(sdk, risk)
    risk_pid = ev2._content_id("pt", risk)
    action_pid = ev2._content_id("pt", action)
    assert existing_risk != risk_pid
    payload = _payload(
        [_pt(risk_pid, risk), _pt(action_pid, action)],
        [{"src": risk_pid, "dst": action_pid, "op_type": "IMPL",
          "direction": "unidirectional"},
         # the dampener's `src` is the RE-KEYED point — the reason lookup it
         # drives is exactly the P1 case below (a fresh point keeps its
         # payload id, so only a re-keyed `src` can expose the mismatch).
         {"src": risk_pid, "dst": action_pid, "op_type": "MITIGATES",
          "strength": 0.4,
          "target": {"src": risk_pid, "dst": action_pid,
                     "op_type": "IMPL"}}])
    monkeypatch.setattr(ev2, "extract_session_v2", _extract_returning(payload))

    r = sdk.capture_session(CONV, session_id="s4716")
    assert r.get("ok") is True, r
    proj = sdk._get_proj()
    # the declared target IMPL committed between the RESOLVED risk id and the
    # new action point, and the dampener attached to it
    assert _impl_edges(proj) == [(existing_risk, action_pid)]
    assert proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
        "MATCH (o)-[:mitigated_by]->(m:Point) RETURN count(m)"
    ).result_set[0][0] >= 1
    # ⛔ #4716 review P1: the MITIGATES reason is resolved from the SAME ref
    # the remap repointed — so the reason lookup must stay payload-id aware.
    # Without that, the dampener's content degrades to
    # "[MITIGATION] [MITIGATION] <graph-id>" (reproduced end-to-end). A
    # count-only assertion is exactly what let it through.
    contents = [r[0] for r in proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
        "MATCH (o)-[:mitigated_by]->(m:Point) RETURN m.content"
    ).result_set]
    assert contents, "the dampener must have a mitigation Point"
    for c in contents:
        assert risk in c, (
            f"the mitigation reason must resolve to the SOURCE point's content "
            f"(got {c!r})")
        assert c.count("[MITIGATION]") == 1, (
            f"exactly one [MITIGATION] prefix (sdk adds it, why.py strips one): {c!r}")


def test_capture_commit_remaps_pt_supersession_ref(sdk, monkeypatch):
    """The adjacent hole, answered by analysis and fixed here: a supersession
    record's ``supersedes_by`` is the NEW payload point's ``pt_<sha>`` id, so
    it shares the operators' two-id-space hole. Unremapped, the CORRECTS fold
    is lost with `point supersession ref '<payload id>' not found — skipped
    (fail-open)`; remapped, the fold lands on the resolved successor."""
    import tortoise.extractor_v2 as ev2

    old = "the team decided to ship on friday"
    successor = "the team decided to ship on monday instead"
    sdk.create_point("decision", old, id="pt_old_decision_4716")
    successor_id = _pre_existing_id(sdk, successor, kind="decision")
    successor_pid = ev2._content_id("pt", successor)
    assert successor_id != successor_pid
    payload = _payload(
        [_pt(successor_pid, successor, kind="decision")], [],
        supersessions=[{"superseded": "pt_old_decision_4716",
                        "supersedes_by": successor_pid,
                        "evidence": "decision reversal"}])
    monkeypatch.setattr(ev2, "extract_session_v2", _extract_returning(payload))

    r = sdk.capture_session(CONV, session_id="s4716")
    assert r.get("ok") is True, r
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (o:Point {id:'pt_old_decision_4716'}), "
        "(n:Point {id:$new}) RETURN o.outdated, "
        "EXISTS((o)-[:CORRECTS]->(n))",
        params={"new": successor_id}).result_set
    assert rows and rows[0][0] is True and rows[0][1] is True, rows


def test_capture_commit_resolves_mitigates_reason_for_a_folded_endpoint(
        sdk, monkeypatch):
    """#4716 re-review: a FOLDED (NOOP) endpoint's ref is the PRIOR's graph id
    and has NO payload point — the reverse map cannot resolve it. The
    extractor's noop record carries the canonical content for exactly that
    reason, so the dampener's reason must still resolve to a real claim rather
    than degrading to the bare graph id (a fabricated '[MITIGATION] <id>'
    statement)."""
    import tortoise.extractor_v2 as ev2

    prior = "the migration has no rollback path"
    action = "the migration needs a rollback path before it ships"
    prior_id = _pre_existing_id(sdk, prior)
    action_pid = ev2._content_id("pt", action)
    # the operator's `src` is the folded PRIOR's graph id — not a payload id
    payload = _payload(
        [_pt(action_pid, action)],
        [{"src": prior_id, "dst": action_pid, "op_type": "IMPL",
          "direction": "unidirectional"},
         # the operator's `src` is the folded PRIOR's graph id — not a payload id
         {"src": prior_id, "dst": action_pid, "op_type": "MITIGATES",
          "strength": 0.3,
          "target": {"src": prior_id, "dst": action_pid,
                     "op_type": "IMPL"}}])
    monkeypatch.setattr(ev2, "extract_session_v2", _extract_returning(
        payload,
        noops=[{"point_id": prior_id, "session_ref": "s4716",
                "overlap": 1.0, "evidence": "exact normalized content match",
                "content": prior, "reason": "identical"}]))

    r = sdk.capture_session(CONV, session_id="s4716")
    assert r.get("ok") is True, r
    proj = sdk._get_proj()
    contents = [c for (c,) in proj.g.query(
        "MATCH (o:Point {is_operator:true})-[:mitigated_by]->(m:Point) "
        "RETURN m.content").result_set]
    assert contents, "the dampener must have attached"
    for c in contents:
        assert prior in c, (
            f"a folded endpoint's reason must resolve to the canonical "
            f"content, not its id (got {c!r})")
        assert c.count("[MITIGATION]") == 1, c


def test_capture_commit_single_prefixes_an_unresolvable_mitigates_reason(
        sdk, monkeypatch):
    """#4716 review cycle 4 coverage: the unresolvable-`src` fallback must add
    NO prefix of its own — `sdk.mitigate_operator` already writes
    ``content=f"[MITIGATION] {reason}"`` and ``why.py`` strips exactly one, so
    the old ``f"[MITIGATION] {src}"`` fallback stored a DOUBLE prefix. The
    `src` here resolves to neither a payload point nor a folded noop
    (an Event id), while the target IMPL edge exists."""
    import tortoise.extractor_v2 as ev2

    risk = "the rollout has no canary stage"
    action = "the rollout needs a canary stage first"
    risk_id = _pre_existing_id(sdk, risk)
    action_pid = ev2._content_id("pt", action)
    payload = _payload(
        [_pt(action_pid, action)],
        [{"src": risk_id, "dst": action_pid, "op_type": "IMPL",
          "direction": "unidirectional"},
         {"src": "ev_unresolvable_4716", "dst": action_pid,
          "op_type": "MITIGATES", "strength": 0.25,
          "target": {"src": risk_id, "dst": action_pid,
                     "op_type": "IMPL"}}])
    monkeypatch.setattr(ev2, "extract_session_v2", _extract_returning(payload))

    r = sdk.capture_session(CONV, session_id="s4716")
    assert r.get("ok") is True, r
    contents = [c for (c,) in sdk._get_proj().g.query(
        "MATCH (o:Point {is_operator:true})-[:mitigated_by]->(m:Point) "
        "RETURN m.content").result_set]
    assert contents, "the dampener must attach (the target edge exists)"
    for c in contents:
        assert c.count("[MITIGATION]") == 1, (
            f"exactly one [MITIGATION] prefix (the old fallback doubled it): "
            f"{c!r}")


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
