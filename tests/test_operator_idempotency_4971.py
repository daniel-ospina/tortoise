"""#4971 — ``apply_payload_operators`` needs a ``(op_type, src, dst)`` guard.

``sdk.create_operator`` mints a FRESH ULID node on every call with no existence
probe, so a commit that reaches the operator pass twice mints a SECOND operator
Point for one bridge. The shared helper already carries the MITIGATES
deep-miss rule; the sibling writers each carry an idempotency probe (the eval
lane's dup-edge probe, ``tools/longmem_eval/ingest_v2.py``; v1's
content-addressed refs) — the helper did not.

Two shapes are asserted here:

1. the issue's own acceptance — ``apply_payload_operators`` run TWICE over the
   same resolved payload writes exactly one operator Point per
   ``(op_type, src, dst)`` triple, and the second call returns no new ids;
2. the id-space half (comment 3) — a capture that RE-KEYS an endpoint onto a
   pre-existing legacy-id node must not grow the operator count on a repeated
   commit. The capture commit remaps payload refs to the resolved graph ids
   (#4996 / #4716 Part 1), so the guard sees the same triple across commits and
   fires; a guard keyed on the payload id would never match (the reason the key
   is the RESOLVED triple, not the payload id — the same reasoning documented
   at ``tools/longmem_eval/ingest_v2.py`` "NEVER the payload ``id``").

The guard must be narrow: a genuinely different edge (different ``op_type``,
``src`` or ``dst``) is still written, so a guard that blocks everything fails
the control tests.
"""
from __future__ import annotations

from tortoise.commit_ops import apply_payload_operators
from tortoise.sdk import TortoiseSDK

# ── fixtures / helpers ──────────────────────────────────────────────────────

def _sdk(tmp_path) -> TortoiseSDK:
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def _impl_operator_count(proj) -> int:
    return proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
        "RETURN count(o)").result_set[0][0]


def _impl_edges(proj) -> list[tuple[str, str]]:
    rows = proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
        "MATCH (o)-[:IMPL {idx:0}]->(s) MATCH (o)-[:IMPL {idx:1}]->(d) "
        "RETURN s.id, d.id").result_set
    return [(r[0], r[1]) for r in rows]


def _raw_op(src: str, dst: str, op_type: str = "IMPL") -> dict:
    return {"src": src, "dst": dst, "op_type": op_type,
            "direction": "unidirectional"}


# ── 1. the issue's own acceptance: run the helper twice ─────────────────────

def test_apply_payload_operators_twice_writes_one_operator(tmp_path):
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    a = sdk.create_point("statement", "claim a")["id"]
    b = sdk.create_point("statement", "claim b")["id"]
    ops = [_raw_op(a, b)]

    first = apply_payload_operators(proj, sdk, ops)
    assert len(first) == 1, f"the first pass must create the bridge: {first!r}"

    second = apply_payload_operators(proj, sdk, ops)
    assert second == [], (
        "a repeat pass must be a NO-OP and must NOT claim the pre-existing "
        f"operator as created (return contract = CREATED ids): {second!r}")

    assert _impl_operator_count(proj) == 1
    assert _impl_edges(proj) == [(a, b)]


def test_apply_payload_operators_idempotent_across_many_repeats(tmp_path):
    """The three-distinct-commit growth shape, driven directly on the helper:
    1 -> 1 -> 1, never 1 -> 2 -> 3."""
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    a = sdk.create_point("statement", "claim a")["id"]
    b = sdk.create_point("statement", "claim b")["id"]
    ops = [_raw_op(a, b)]

    counts = []
    for _ in range(3):
        apply_payload_operators(proj, sdk, ops)
        counts.append(_impl_operator_count(proj))
    assert counts == [1, 1, 1], f"operator count grew: {counts!r}"


# ── 2. control: a genuinely different edge is still written ─────────────────

def test_control_distinct_src_dst_still_written(tmp_path):
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    a = sdk.create_point("statement", "claim a")["id"]
    b = sdk.create_point("statement", "claim b")["id"]
    c = sdk.create_point("statement", "claim c")["id"]

    apply_payload_operators(proj, sdk, [_raw_op(a, b)])
    apply_payload_operators(proj, sdk, [_raw_op(a, c)])   # different dst
    apply_payload_operators(proj, sdk, [_raw_op(b, a)])   # different src
    apply_payload_operators(proj, sdk, [_raw_op(a, b)])   # the repeat: no-op

    assert _impl_operator_count(proj) == 3
    assert sorted(_impl_edges(proj)) == sorted([(a, b), (a, c), (b, a)])


def test_control_distinct_op_type_still_written(tmp_path):
    """``op_type`` is part of the key: an IMPL A->B does not suppress NAND A->B."""
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    a = sdk.create_point("statement", "claim a")["id"]
    b = sdk.create_point("statement", "claim b")["id"]

    apply_payload_operators(proj, sdk, [_raw_op(a, b, "IMPL")])
    apply_payload_operators(proj, sdk, [_raw_op(a, b, "NAND")])
    apply_payload_operators(proj, sdk, [_raw_op(a, b, "IMPL")])  # repeat

    impl = proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
        "RETURN count(o)").result_set[0][0]
    nand = proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'NAND'}) "
        "RETURN count(o)").result_set[0][0]
    assert (impl, nand) == (1, 1)


def test_repeat_pass_keeps_mitigation_attached_once(tmp_path):
    """The guard skips the IMPL on a repeat, so the MITIGATES second pass
    must still resolve the pre-existing operator (its Cypher fallback) and
    ``mitigate_operator`` must update — never duplicate — the dampener."""
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    a = sdk.create_point("statement", "claim a")["id"]
    b = sdk.create_point("statement", "claim b")["id"]
    z = sdk.create_point("statement", "reason z")["id"]
    ops = [
        _raw_op(a, b, "IMPL"),
        {"src": z, "dst": a, "op_type": "MITIGATES", "strength": 0.4,
         "target": {"src": a, "dst": b, "op_type": "IMPL"}},
    ]
    for _ in range(2):
        apply_payload_operators(
            proj, sdk, ops,
            point_content_by_id=lambda pid: "the dampener's reason")

    assert _impl_operator_count(proj) == 1
    mitigations = proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'IMPL'})-"
        "[:mitigated_by]->(m:Point) RETURN count(m)").result_set[0][0]
    assert mitigations == 1


# ── 3. the re-key lane: repeated distinct capture commits ───────────────────

def _payload(points, operators):
    return {"session_id": "s4971", "client_commit_id": "",
            "captured_at": "2026-09-26T00:00:00Z",
            "extractor": {"version": "t", "mode": "byok",
                          "calibration_version": "v2"},
            "summary": "", "story_arc": "", "provenance_refs": [],
            "sources": [], "entities": [], "points": points, "events": [],
            "operators": operators, "supersessions": [],
            "telemetry": {"counts": {"kept": len(points)}}}


def _pt(pid, content, kind="statement"):
    return {"id": pid, "content": content, "pointKind": kind,
            "reason": "NEW", "confidence": 0.5, "c_cal": 0.5,
            "about_entities": [], "source_ref": "session.md", "quote": "",
            "status": "draft", "search_keys": []}


def _extract_returning(payload):
    def _run(_model, _conversation=None, *, session_id=None, **_kw):
        return {"payload": payload, "errors": [], "warnings": [],
                "stats": {}, "noops": [], "chain_notes": [],
                "link_before_create": [], "supersessions": [],
                "minted_kinds": [], "story_arc": "", "search": {},
                "error_census": {}}
    return _run


CONV = [
    {"role": "user", "content": "a session whose extraction seam is fixed"},
    {"role": "assistant", "content": "noted"},
]


def test_rekeyed_recapture_does_not_duplicate_operator(tmp_path, monkeypatch):
    """Comment-3 acceptance extension: commit 1 writes the bridge over a
    pre-seeded LEGACY-id endpoint; each further distinct commit re-sends the
    identical operator and re-keys onto that same legacy node. The operator
    Point count must stay 1 (it grows 1 -> 2 -> 3 without the guard)."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    sdk = _sdk(tmp_path)

    prior = "the ingest queue has no backpressure control"
    other = "backpressure must be added before the next release"
    # a non-payload (legacy ULID) id holding the payload point's content, so the
    # capture commit's content-hash resolver re-keys the payload id onto it
    sdk.create_point("statement", prior)
    legacy_id = sdk._get_proj().g.query(
        "MATCH (p:Point {content:$c}) RETURN p.id",
        params={"c": prior}).result_set[0][0]
    prior_pid = ev2._content_id("pt", prior)
    other_pid = ev2._content_id("pt", other)
    assert legacy_id != prior_pid, "the re-key must be genuine"

    payload = _payload(
        [_pt(prior_pid, prior), _pt(other_pid, other)],
        [_raw_op(prior_pid, other_pid)])
    monkeypatch.setattr(ev2, "extract_session_v2", _extract_returning(payload))

    proj = sdk._get_proj()
    counts = []
    for sid in ("s1", "s2", "s3"):
        r = sdk.capture_session(CONV, session_id=sid)
        assert r.get("ok") is True, r
        counts.append(_impl_operator_count(proj))
    assert counts == [1, 1, 1], (
        f"the re-keyed repeat commits must not grow the operator count: "
        f"{counts!r}")
    # and the single bridge sits between the RESOLVED ids
    assert _impl_edges(proj) == [(legacy_id, other_pid)]
