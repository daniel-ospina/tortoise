"""W5 Phase D (#2104, epic #2080) — per-point dedup classification tests.

Phase D closes indicator (4) on the CAPTURE write seams: content-hash
idempotency (a re-ingested claim = ``content_hash_hit``, 0 duplicate
points) + REPHRASE-linked dedup (a paraphrase of an existing claim =
``rephrase_linked`` — counts as survival of the existing unit, never a new
point).  Scope per the Phase D pin (tortoise/dedup_classify.py + the
issue's scope doc): resolution is in-capture on the m2 seam and
content-addressed re-ingest on the v2 seam; paraphrase detection is the
committed deterministic token-overlap band (extractor_v2.NOOP_MIN_OVERLAP)
— zero LLM, zero embeddings (the m2 mock lane runs keyless).  Anti-gaming:
verdicts are asserted against the GRAPH (node counts / provenance), never
just the response.

Runnable with the docker lane (default) or via an embedded db_path SDK —
the sdk fixture uses a temp embedded FalkorDBLite like
test_capture_session.py.
"""

from __future__ import annotations

import pytest

# Hosted-surface tests reuse test_hosted_api's authenticated TestClient
# fixture (same temp-DB patching + consent seed — repo test style, see
# test_hosted_auth.py helper reuse).
from tests.test_hosted_api import (
    client as client,
)
from tortoise.sdk import TortoiseSDK
from tortoise.write_verb import (
    DEDUP_CONTENT_HASH_HIT,
)


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """Offline mock seam (#822) — deterministic echo, zero network.  The v2
    mock is the default (TORTOISE_SESSION_EXTRACTOR unset); the m2 mock is
    the deterministic W2 CI lane."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def _claim_nodes(proj) -> list:
    """Non-turn, non-operator Point nodes (the capture-minted memory)."""
    return proj.g.query(
        "MATCH (p:Point) WHERE NOT p.id CONTAINS '_t' "
        "AND p.id <> 'session_' AND p.is_operator <> true "
        "RETURN p.id, p.content, p.eventId"
    ).result_set


def _stamped_claim_ids(proj) -> list[str]:
    rows = proj.g.query(
        "MATCH (p:Point) WHERE NOT p.id CONTAINS '_t' AND p.eventId IS NOT NULL RETURN p.id"
    ).result_set
    return [r[0] for r in rows]


# ── m2 lane: in-capture content-hash + paraphrase fold ──────────────────────
















# ── v2 lane: content-addressed re-ingest idempotency ───────────────────────




# ── v2 seam via a stubbed extractor (S3-bypass coverage) ────────────────────


def _stub_extractor(payload_points, noops=None, operators=None):
    """Deterministic extract_session_v2 stub: fixed payload + noops/operators."""
    from tortoise.ids import content_hash

    def _fake(model, conversation, **kw):
        pts = []
        for content in payload_points:
            pid = f"pt_{content_hash(content)[:62]}"
            pts.append({"id": pid, "content": content,
                        "pointKind": "statement", "about_entities": [],
                        "search_keys": [], "quote": content[:200]})
        return {
            "payload": {"points": pts, "entities": [], "events": [],
                        "operators": list(operators or []),
                        "supersessions": []},
            "noops": list(noops or []), "errors": [], "warnings": [],
            "minted_kinds": [], "chain_notes": [], "link_before_create": [],
            "supersessions": [], "story_arc": "", "search": {},
            "stats": {},
        }

    return _fake










# ── EP pass targeting (first-time calibration of folded canonicals) ─────────


def test_capture_ep_target_ids_first_time_fold_calibration(sdk):
    """Review fix (P2): a folded canonical that never got calibrated (its
    own ingest's EP pass failed fail-open) must receive its FIRST calibration
    on a folded re-ingest; a live/calibrated canonical is never re-calibrated;
    minted entries always calibrate. The selector keys on the EP MARKERS, not
    on status (#1088 review P2) — the explicit-draft case is covered
    end-to-end by test_capture_never_promotes_a_folded_explicit_draft."""
    from tortoise.sdk import _capture_ep_target_ids
    proj = sdk._get_proj()
    live = sdk.create_point("statement", "live calibrated claim")
    # No promotion call: create_point's implicit status is live since #1088
    # (the old update_point(status="live") here was a redundant no-op).
    # Mark the live node as EP-calibrated (the state a successful ingest EP
    # pass leaves behind — the has_ep read the enrichment uses)
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=1.0, n.ep_beta=1.0",
        params={"id": live["id"]})
    # folded + live + calibrated → never re-calibrated (no EP churn)
    assert _capture_ep_target_ids(
        [{"id": live["id"], "dedup": DEDUP_CONTENT_HASH_HIT}], proj
    ) == []
    # a live node WITHOUT EP (its ingest EP pass failed fail-open) gets its
    # FIRST calibration on a folded re-ingest
    uncal = sdk.create_point("statement", "live but uncalibrated claim")
    assert _capture_ep_target_ids(
        [{"id": uncal["id"], "dedup": DEDUP_CONTENT_HASH_HIT}], proj
    ) == [uncal["id"]]
    # minted entries always calibrate
    assert _capture_ep_target_ids([{"id": live["id"]}], proj) == [live["id"]]








# ── Pure classifier ─────────────────────────────────────────────────────────


class TestDedupClassifyPure:
    def test_exact_hit_by_content_hash(self):
        from tortoise.dedup_classify import exact_hit_id
        from tortoise.ids import content_hash

        canon = {content_hash("same claim text"): "pt_a"}
        assert exact_hit_id(canon, "same claim text") == "pt_a"
        assert exact_hit_id(canon, "same claim text!") is None  # not byte-equal
        assert exact_hit_id({}, "x") is None

    def test_rephrase_band_excludes_exact_and_low_overlap(self):
        from tortoise.dedup_classify import rephrase_hit

        canon = [("pt_a", "We should ship the web server first to unblock the mobile team.")]
        # high-overlap restatement → paraphrase hit
        hit = rephrase_hit(
            canon, "We should ship the web server first and unblock the mobile team now."
        )
        assert hit is not None and hit[0] == "pt_a"
        # unrelated → no hit
        assert rephrase_hit(canon, "Quarry reingest runs at 62 percent since 3am.") is None
        # byte-identical text belongs to the content-hash leg, never the band
        assert rephrase_hit(canon, canon[0][1]) is None


# ── Hosted surface byte-parity + verb preservation ─────────────────────────






def test_hosted_capture_promotes_operator_with_no_ep_target(client, monkeypatch):
    """#1088: the HOSTED capture mirror promotes a capture's own operator even
    when there is no EP target — the byte-parity half of
    ``sdk.capture_session``'s gate.

    Both capture call sites must run the pass on ``ep_ids or operator_ids``:
    a capture whose points ALL fold onto ALREADY-calibrated canonicals has
    ``ep_ids == []`` yet can still have wired a NEW IMPL operator. Gating on
    ``ep_ids`` alone leaves it ``draft`` (EP-inert, no public promote path).

    MUTATION THAT REDS THIS TEST: restore ``if ep_ids:`` at the hosted call
    site (``tortoise/hosted_api.py`` — ``_capture_session_impl``).
    """
    import tortoise.extractor_v2 as ev2
    import tortoise.hosted_api as ha_mod
    from tortoise.ids import content_hash

    a = "the schema migration lands before the release cut"
    b = "the release cut waits on the schema migration"
    pid_a = f"pt_{content_hash(a)[:62]}"
    pid_b = f"pt_{content_hash(b)[:62]}"

    # 1st hosted capture mints the two canonicals (no operator wired).
    monkeypatch.setattr(ev2, "extract_session_v2", _stub_extractor([a, b]))
    r1 = client.post("/v1/sessions",
                     json={"conversation": [{"role": "user",
                                             "content": "seed"}]})
    assert r1.status_code == 200, r1.text

    sdk = ha_mod._make_sdk(namespace="team-001")
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "SET n.ep_alpha=1.0, n.ep_beta=1.0",
        params={"ids": [pid_a, pid_b]})

    # 2nd hosted capture: both points fold onto the calibrated canonicals and
    # the payload wires a NEW operator between them.
    operators = [{"src": pid_a, "dst": pid_b, "op_type": "IMPL",
                  "direction": "unidirectional"}]
    monkeypatch.setattr(ev2, "extract_session_v2",
                        _stub_extractor([a, b], operators=operators))
    r2 = client.post("/v1/sessions",
                     json={"conversation": [{"role": "user",
                                             "content": "second"}]})
    assert r2.status_code == 200, r2.text

    rows = proj.g.query(
        "MATCH (o:Point {is_operator:true, op_type:'IMPL'})-[:IMPL]->(c:Point) "
        "WHERE c.id IN $ids RETURN DISTINCT o.id, o.status",
        params={"ids": [pid_a, pid_b]}).result_set
    assert rows, "the hosted capture must have wired an operator"
    assert all(r[1] == "live" for r in rows), rows
