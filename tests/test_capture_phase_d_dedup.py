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
    DEDUP_NEW,
    DEDUP_REPHRASE_LINKED,
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


def test_m2_in_capture_repeat_is_content_hash_hit_single_node(sdk, monkeypatch):
    """The M2 mock echo mints one claim per utterance; the SAME sentence in
    two utterances previously minted TWO duplicate points.  Phase D folds the
    repeat onto the canonical — 1 node, second occurrence reported
    ``content_hash_hit`` (0 duplicate points)."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    conv = [
        {"role": "user", "content": "The database schema needs normalization before the release."},
        {
            "role": "assistant",
            "content": "The database schema needs normalization before the release.",
        },
        {"role": "user", "content": "ok"},
    ]
    res = sdk.capture_session(conv)
    verdicts = [p["dedup"] for p in res["points"]]
    assert verdicts == [DEDUP_NEW, DEDUP_CONTENT_HASH_HIT], verdicts
    # both occurrences report the canonical point (same id)
    ids = {p["id"] for p in res["points"]}
    assert len(ids) == 1, ids
    proj = sdk._get_proj()
    claims = _claim_nodes(proj)
    assert len(claims) == 1, f"0 duplicate points — expected 1 claim, got {len(claims)}"
    # provenance stamped once (the minted canonical), never re-stamped
    assert _stamped_claim_ids(proj) == [res["points"][0]["id"]]


def test_m2_in_capture_paraphrase_is_rephrase_linked_single_node(sdk, monkeypatch):
    """A restatement (high token overlap, distinct text) of a claim minted
    earlier in the SAME capture resolves via the committed paraphrase band →
    ``rephrase_linked`` (REPHRASE-linked dedup: counts as survival of the
    existing unit, never a new point).  Deterministic, zero LLM."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    conv = [
        {
            "role": "user",
            "content": "We should ship the web server first to unblock the mobile team.",
        },
        {
            "role": "assistant",
            "content": "We should ship the web server first and unblock the mobile team now.",
        },
        {"role": "user", "content": "ok"},
    ]
    res = sdk.capture_session(conv)
    verdicts = [p["dedup"] for p in res["points"]]
    assert verdicts == [DEDUP_NEW, DEDUP_REPHRASE_LINKED], verdicts
    assert len({p["id"] for p in res["points"]}) == 1
    claims = _claim_nodes(sdk._get_proj())
    assert len(claims) == 1, f"expected 1 claim after rephrase fold, got {len(claims)}"


def test_m2_distinct_claims_stay_new(sdk, monkeypatch):
    """Anti-gaming: unrelated claims never fold (no verdict beyond new)."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    conv = [
        {
            "role": "user",
            "content": "We should ship the web server first to unblock the mobile team.",
        },
        {
            "role": "assistant",
            "content": "Quarry reingest runs at 62 percent since 3am this morning.",
        },
        {"role": "user", "content": "ok"},
    ]
    res = sdk.capture_session(conv)
    assert all(p["dedup"] == DEDUP_NEW for p in res["points"])
    assert len(_claim_nodes(sdk._get_proj())) == len(res["points"])


def test_m2_operator_wired_repeat_kept_distinct_with_warning(sdk, monkeypatch):
    """Honest residual (anti-gaming): a duplicate whose minted node engaged
    operator wiring is NOT folded (folding would orphan the cue-gated edge) —
    it stays a distinct ``new`` point and the response says so."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    # "because" is the m2 cue-gate word — the pair of identical claims is
    # operator-linked, so the second stays distinct.
    conv = [
        {
            "role": "user",
            "content": "We should ship the HTTP server first because the mobile team is blocked.",
        },
        {
            "role": "assistant",
            "content": "We should ship the HTTP server first because the mobile team is blocked.",
        },
        {"role": "user", "content": "ok"},
    ]
    res = sdk.capture_session(conv)
    verdicts = [p["dedup"] for p in res["points"]]
    assert verdicts == [DEDUP_NEW, DEDUP_NEW], verdicts
    assert any("not deduped" in w for w in res["warnings"]), res["warnings"]
    assert len(_claim_nodes(sdk._get_proj())) == 2, "operator-wired duplicate kept — 2 claim nodes"


# ── v2 lane: content-addressed re-ingest idempotency ───────────────────────


def test_v2_cross_session_reingest_is_content_hash_hit(sdk):
    """v2 payload ids are deterministic pt_<sha> keys: re-capturing the SAME
    claim in a NEW session is a content-hash re-ingest → the second capture
    reports ``content_hash_hit``, mints ZERO new nodes, and never re-stamps
    the canonical's provenance (first-writer eventId preserved)."""
    conv = [
        {"role": "user", "content": "The database schema needs normalization before the release."},
        {"role": "user", "content": "ok"},
    ]
    r1 = sdk.capture_session(conv)
    r2 = sdk.capture_session(conv)  # fresh auto session id, same content
    assert all(p["dedup"] == DEDUP_NEW for p in r1["points"])
    assert r2["points"] and all(p["dedup"] == DEDUP_CONTENT_HASH_HIT for p in r2["points"]), [
        p["dedup"] for p in r2["points"]
    ]
    # same canonical id across captures
    assert {p["id"] for p in r1["points"]} == {p["id"] for p in r2["points"]}
    proj = sdk._get_proj()
    claims = _claim_nodes(proj)
    assert len(claims) == len(r1["points"]), (
        f"0 duplicate points across re-ingest — expected {len(r1['points'])}, got {len(claims)}"
    )
    # provenance belongs to the FIRST ingest only (never clobbered)
    stamped = _stamped_claim_ids(proj)
    assert stamped == [p["id"] for p in r1["points"]], stamped


# ── v2 seam via a stubbed extractor (S3-bypass coverage) ────────────────────


def _stub_extractor(payload_points, noops=None):
    """Deterministic extract_session_v2 stub: fixed payload + noops."""
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
                        "operators": [], "supersessions": []},
            "noops": list(noops or []), "errors": [], "warnings": [],
            "minted_kinds": [], "chain_notes": [], "link_before_create": [],
            "supersessions": [], "story_arc": "", "search": {},
            "stats": {},
        }

    return _fake


def test_v2_seam_in_capture_payload_repeat_folds(sdk, monkeypatch):
    """S3-bypass seam coverage: a payload carrying the same claim twice
    (extractor backstop) folds the second onto the first — 1 node, second
    occurrence content_hash_hit, no phantom."""
    content = "The schema needs normalization before the release."
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(
        ev2, "extract_session_v2", _stub_extractor([content, content]))
    res = sdk.capture_session([{"role": "user", "content": "hello"}])
    verdicts = [p["dedup"] for p in res["points"]]
    assert verdicts == [DEDUP_NEW, DEDUP_CONTENT_HASH_HIT], verdicts
    assert len({p["id"] for p in res["points"]}) == 1
    claims = _claim_nodes(sdk._get_proj())
    assert len(claims) == 1, f"expected 1 claim node, got {len(claims)}"


def test_v2_seam_cross_id_content_hit_no_phantom_no_churn(sdk, monkeypatch):
    """Review fix (P1): a re-ingest whose identical content exists under a
    DIFFERENT id (a non-pt_/older node) must resolve to THAT node — the
    response reports the id the graph actually holds (never a phantom
    pt_<sha> that was never created) and the canonical's props/provenance
    are untouched (no first-writer churn)."""
    content = "The database schema needs normalization before the release."
    prior = sdk.create_point("statement", content, session_id="prior-session")
    proj = sdk._get_proj()
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2",
                        _stub_extractor([content]))
    res = sdk.capture_session([{"role": "user", "content": "hello"}])
    assert res["points"]
    entry = res["points"][0]
    assert entry["dedup"] == DEDUP_CONTENT_HASH_HIT, entry
    assert entry["id"] == prior["id"], (
        "response id must be the node the graph holds — got a phantom")
    # the seeded canonical's session attribution is untouched (first-writer)
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.session_id",
        params={"id": prior["id"]},
    ).result_set
    assert rows[0][0] == "prior-session", rows[0]
    # no phantom pt_ node was created
    phantom = proj.g.query(
        "MATCH (n:Point) WHERE n.id STARTS WITH 'pt_' RETURN count(n)"
    ).result_set[0][0]
    assert phantom == 0, "no phantom content-addressed node may exist"


def test_v2_noop_gate_only_surfaces_capture_provenanced_identical(sdk, monkeypatch):
    """Review fix (P1/P2): consolidation noops are surfaced ONLY for the
    sanctioned content-addressed re-ingest — reason=identical AND a
    capture-minted (eventId-carrying) pt_ canonical.  Paraphrase folds and
    priors without capture provenance stay extractor-side with an additive
    warning (cross-session rephrase dedup is deferred; never a fabricated
    verdict)."""
    import tortoise.extractor_v2 as ev2
    # paraphrase fold → NOT surfaced
    para = _stub_extractor([], noops=[
        {"point_id": "pt_deadbeef", "reason": "paraphrase",
         "overlap": 0.8, "evidence": "value-signature equal"}])
    monkeypatch.setattr(ev2, "extract_session_v2", para)
    res = sdk.capture_session([{"role": "user", "content": "hello"}])
    assert res["points"] == [], res["points"]
    assert any("not surfaced" in w for w in res["warnings"]), res["warnings"]

    # identical fold onto a node WITHOUT capture provenance → NOT surfaced
    orphan = sdk.create_point("statement", "unprovenanced identical claim")
    noops = [{"point_id": orphan["id"], "reason": "identical",
              "overlap": 1.0, "evidence": "exact"}]
    monkeypatch.setattr(ev2, "extract_session_v2", _stub_extractor([], noops))
    res2 = sdk.capture_session([{"role": "user", "content": "hello"}])
    assert res2["points"] == [], res2["points"]
    assert any("not surfaced" in w for w in res2["warnings"]), res2["warnings"]


# ── EP pass targeting (first-time calibration of folded canonicals) ─────────


def test_capture_ep_target_ids_first_time_fold_calibration(sdk):
    """Review fix (P2): a folded canonical that never got calibrated (its
    own ingest's EP pass failed fail-open — still draft) must receive its
    FIRST calibration on a folded re-ingest; a live/calibrated canonical is
    never re-calibrated; minted entries always calibrate."""
    from tortoise.sdk import _capture_ep_target_ids
    proj = sdk._get_proj()
    draft = sdk.create_point("statement", "draft uncalibrated claim")
    live = sdk.create_point("statement", "live calibrated claim")
    sdk.update_point(live["id"], status="live")
    # mark the live node as EP-calibrated (the state a successful ingest EP
    # pass leaves behind — the has_ep read the enrichment uses)
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=1.0, n.ep_beta=1.0",
        params={"id": live["id"]})
    # folded + still draft → first-time calibration target
    assert _capture_ep_target_ids(
        [{"id": draft["id"], "dedup": DEDUP_CONTENT_HASH_HIT}], proj
    ) == [draft["id"]]
    # folded + live + calibrated → never re-calibrated (no EP churn)
    assert _capture_ep_target_ids(
        [{"id": live["id"], "dedup": DEDUP_CONTENT_HASH_HIT}], proj
    ) == []
    # a live node WITHOUT EP (its ingest EP pass failed fail-open) gets its
    # FIRST calibration on a folded re-ingest
    uncal = sdk.create_point("statement", "live but uncalibrated claim")
    sdk.update_point(uncal["id"], status="live")
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


def test_hosted_and_mirror_reingest_parity(client):
    """The hosted write-verb enrichment keeps the seam's Phase D verdicts
    (a re-ingest reports ``content_hash_hit``, never a fabricated ``new``)
    and the SDK mirror speaks the SAME dedup contract for the same
    conversation against the same graph: first ingest = ``new``, every
    subsequent ingest = ``content_hash_hit`` with 0 new nodes and the
    first-writer provenance never re-stamped."""
    conv = [
        {"role": "user", "content": "The database schema needs normalization before the release."},
        {"role": "user", "content": "ok"},
    ]
    r1 = client.post("/v1/sessions", json={"conversation": conv})
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["points"] and all(p["dedup"] == DEDUP_NEW for p in b1["points"]), b1["points"]
    r2 = client.post("/v1/sessions", json={"conversation": conv})
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["points"] and all(p["dedup"] == DEDUP_CONTENT_HASH_HIT for p in b2["points"]), b2[
        "points"
    ]
    # frozen-verb per-point surface + canonical id preserved across the hit
    for p in b2["points"]:
        assert p.get("point_id") == p.get("id")
        assert "status" in p and "ep_updated" in p
    assert {p["id"] for p in b1["points"]} == {p["id"] for p in b2["points"]}

    # Mirror parity: the same conversation against the same graph (the
    # client fixture pins the SDK to one temp DB) reports the SAME contract.
    import tortoise.hosted_api as ha_mod

    sdk = ha_mod._make_sdk(namespace="team-001")
    m1 = sdk.capture_session(conv)
    m2 = sdk.capture_session(conv)
    assert m1["points"] and all(p["dedup"] == DEDUP_CONTENT_HASH_HIT for p in m1["points"]), m1[
        "points"
    ]
    assert m2["points"] and all(p["dedup"] == DEDUP_CONTENT_HASH_HIT for p in m2["points"]), m2[
        "points"
    ]
    proj = sdk._get_proj()
    claims = _claim_nodes(proj)
    assert len(claims) == len(b1["points"]), (
        f"0 duplicate points across 4 ingests — expected "
        f"{len(b1['points'])} claims, got {len(claims)}"
    )
    assert _stamped_claim_ids(proj) == [p["id"] for p in b1["points"]], (
        "first-writer provenance never re-stamped"
    )
