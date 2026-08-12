"""DE2E-1 + negative cases — Phase-2 entity extraction (issue #782).

Plan §7 (epic #264) DE2E-1: Session → Entity Objects with provenance.
Deterministic-fixture preamble: EntityStageMock injected everywhere — no LLM,
no network. Runs against FalkorDBLite (embedded) projections.

Also covers DE2E-N1 (malformed session), DE2E-N5 (empty corpus), DE2E-N8
(duplicate session file) via the extended mine_corpus entry point, and the
back-compatible extended return keys of mine_conversation/mine_corpus.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI              # noqa: E402
from tortoise.extractor import entity_stage_fixture   # noqa: E402
from tortoise.log import EventLog              # noqa: E402
from tortoise.mining import mine_conversation  # noqa: E402
from tortoise.projection import FalkorProjection  # noqa: E402
from tortoise.sdk import TortoiseSDK           # noqa: E402


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_de2e1_"), name)


def _proj_api():
    """FalkorDBLite projection + EventAPI (mining path, idempotency off)."""
    proj = FalkorProjection(_tmp("t.db"))
    log = EventLog(_tmp("events.jsonl"))
    api = EventAPI(log, initiated_by="extractor", agent_id="test", projection=proj)
    api._ingest_cache = {}
    return api, proj


TRANSCRIPT = (
    "Alice: We decided to move the FalkorDB default port to 16379.\n"
    "Bob: I disagree because changing port 16379 breaks the redis config.\n"
    "Alice: But tortoise#123 tracks the migration work.\n"
)

EXPECTED = {
    "port 16379": "other",
    "FalkorDB": "tool",
    "tortoise#123": "workitem",
}


def _expected_object_id(name: str) -> str:
    """Plan §4.1: obj_ + sha256(domain-separated canonical name)[:16] (64-bit
    prefix — DE2E-review: the old [:12] 48-bit truncation collided once
    punctuation-stripping canonicalization was added)."""
    canonical = name.lower().strip()
    for ch in ",.!?;:'\"()[]{}":
        canonical = canonical.replace(ch, "")
    return "obj_" + hashlib.sha256(f"obj:{canonical}".encode()).hexdigest()[:16]


# ── DE2E-1: Session → Entity Objects with provenance ──────────────

def test_de2e1_session_to_entity_objects_with_provenance():
    api, proj = _proj_api()
    try:
        res = mine_conversation(TRANSCRIPT, "s1", api,
                                entity_stage=entity_stage_fixture())
        g = proj.g

        # Extended return contract (plan §6.1, back-compatible)
        assert res["entities"] == 3
        assert res["objects"] == 3
        assert res["dedup_hits"] == 0
        assert res["drafts"] == res["points"] > 0
        assert {"events", "points", "operators", "event_ids"} <= set(res)

        # Step 2: ≥1 Object per entity with the right objectKind + canonical id
        for name, kind in EXPECTED.items():
            rows = g.query(
                "MATCH (o:Object {name:$n}) "
                "RETURN o.objectKind, o.id, o.canonical_name",
                params={"n": name},
            ).result_set
            assert rows, f"no Object node for {name!r}"
            assert rows[0][0] == kind, f"{name}: expected objectKind {kind}"
            assert rows[0][1] == _expected_object_id(name), \
                f"{name}: deterministic canonical id"
            assert rows[0][2] is not None, f"{name}: canonical_name present"

        # Step 3: aboutObject edges on BOTH Point and Event sides
        names = list(EXPECTED)
        point_side = g.query(
            "MATCH (p:Point)-[:aboutObject]->(o:Object) "
            "WHERE o.name IN $names RETURN DISTINCT o.name",
            params={"names": names},
        ).result_set
        event_side = g.query(
            "MATCH (e:Event)-[:aboutObject]->(o:Object) "
            "WHERE o.name IN $names RETURN DISTINCT o.name",
            params={"names": names},
        ).result_set
        assert {r[0] for r in point_side} == set(names), \
            f"Point-side aboutObject missing: {point_side}"
        assert {r[0] for r in event_side} == set(names), \
            f"Event-side aboutObject missing: {event_side}"

        # Step 4: aboutEvent provenance anchor (session-occurrence Points)
        pe = g.query(
            "MATCH (p:Point)-[:aboutEvent]->(e:Event {eventId:'meeting-s1'}) "
            "RETURN count(p)",
        ).result_set
        assert pe[0][0] >= 1, "no aboutEvent edges for occurrence Points"

        # Step 5: full provenance chain
        #   (p:Point)-[:extractedFrom]->(:Source)-[:references]->(:Event)
        chain = g.query(
            "MATCH (p:Point)-[:extractedFrom]->(:Source)-[:references]->(e:Event) "
            "RETURN count(DISTINCT p)",
        ).result_set
        assert chain[0][0] >= 1, "extractedFrom → Source → references chain missing"

        # Step 6: no Subject stubs for any extracted entity
        for name in names:
            r = g.query("MATCH (s:Subject {name:$n}) RETURN count(s)",
                        params={"n": name}).result_set
            assert r[0][0] == 0, f"Subject stub created for extracted entity {name!r}"
    finally:
        proj.close()
    print("PASS test_de2e1_session_to_entity_objects_with_provenance")


def test_de2e1_remining_is_idempotent_for_objects():
    """Re-running the same session adds no new Object nodes (MERGE by name)."""
    api, proj = _proj_api()
    try:
        mine_conversation(TRANSCRIPT, "s1", api, entity_stage=entity_stage_fixture())
        g = proj.g
        first = g.query("MATCH (o:Object) WHERE o.canonical_name IS NOT NULL "
                        "RETURN count(o)").result_set[0][0]
        mine_conversation(TRANSCRIPT, "s1", api, entity_stage=entity_stage_fixture())
        second = g.query("MATCH (o:Object) WHERE o.canonical_name IS NOT NULL "
                         "RETURN count(o)").result_set[0][0]
        assert second == first == 3
    finally:
        proj.close()
    print("PASS test_de2e1_remining_is_idempotent_for_objects")


# ── Negative cases (mine_corpus path) ─────────────────────────────

_SESSION_FM = "---\nsessionId: {sid}\nkeywords: [redis]\n---\n"


def test_de2e_n5_empty_corpus():
    """Empty corpus → {sessions:0, ...} no error (plan §7 DE2E-N5)."""
    tmp = tempfile.mkdtemp(prefix="tortoise_de2e1_n5_")
    os.makedirs(os.path.join(tmp, "corpus"), exist_ok=True)
    sdk = TortoiseSDK(os.path.join(tmp, "n5.db"))
    try:
        res = sdk.mine_corpus(os.path.join(tmp, "corpus"))
        assert res["sessions"] == 0
        assert res["ingested"] == 0 and res["skipped"] == 0 and res["failed"] == 0
        assert res["entities"] == 0 and res["objects"] == 0
        assert res["dedup_hits"] == 0 and res["drafts"] == 0
        assert res["errors"] == []
    finally:
        sdk.close()
    print("PASS test_de2e_n5_empty_corpus")


def test_de2e_n1_malformed_session_skipped_batch_continues():
    """Malformed file → counted in failed/errors; batch continues (DE2E-N1)."""
    tmp = tempfile.mkdtemp(prefix="tortoise_de2e1_n1_")
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus, exist_ok=True)
    good = os.path.join(corpus, "good.md")
    with open(good, "w", encoding="utf-8") as f:
        f.write(_SESSION_FM.format(sid="s1"))
        f.write("Alice: We decided to fix tortoise#123 today.\n")
    bad = os.path.join(corpus, "bad.md")
    with open(bad, "wb") as f:
        f.write(b"\xff\xfe\x80 invalid utf-8 body \x00")

    sdk = TortoiseSDK(os.path.join(tmp, "n1.db"))
    try:
        res = sdk.mine_corpus(corpus)
        assert res["sessions"] == 2
        assert res["failed"] >= 1, f"bad file not counted as failed: {res}"
        assert res["errors"], "bad file surfaced in errors"
        # batch continued: the good file was ingested AND mined (rule fallback
        # extracts the known tortoise#123 ref — no LLM)
        assert res["ingested"] >= 1
        assert res["entities"] >= 1 and res["objects"] >= 1
    finally:
        sdk.close()
    print("PASS test_de2e_n1_malformed_session_skipped_batch_continues")


def test_de2e_n8_duplicate_session_file_skipped_via_file_hash():
    """Second mine_corpus run → skipped via file_hash, no new entities/objects."""
    tmp = tempfile.mkdtemp(prefix="tortoise_de2e1_n8_")
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus, exist_ok=True)
    with open(os.path.join(corpus, "session.md"), "w", encoding="utf-8") as f:
        f.write(_SESSION_FM.format(sid="dup1"))
        f.write("Alice: We decided to fix tortoise#123 today.\n")

    sdk = TortoiseSDK(os.path.join(tmp, "n8.db"))
    try:
        first = sdk.mine_corpus(corpus)
        assert first["ingested"] == 1
        assert first["entities"] >= 1 and first["objects"] >= 1

        second = sdk.mine_corpus(corpus)
        assert second["skipped"] >= 1, f"second run should skip: {second}"
        assert second["entities"] == 0, "re-run added entities"
        assert second["objects"] == 0, "re-run added objects"
        assert second["dedup_hits"] == 0
    finally:
        sdk.close()
    print("PASS test_de2e_n8_duplicate_session_file_skipped_via_file_hash")


# ── Code-review round 1 repros (PR #994) ──────────────────────────

def test_de2e1_run_boundary_two_sessions_no_cross_wiring():
    """Two distinct session files mined through ONE SDK: session B must not
    inherit session A's points (run boundary per file — the shared EventAPI
    must not leak points across files), and re-mine must not stack points
    (file_hash skip).
    """
    tmp = tempfile.mkdtemp(prefix="tortoise_de2e1_rb_")
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus, exist_ok=True)
    with open(os.path.join(corpus, "a.md"), "w", encoding="utf-8") as f:
        f.write(_SESSION_FM.format(sid="sA"))
        f.write("Alice: We decided to fix tortoise#123 today.\n")
    with open(os.path.join(corpus, "b.md"), "w", encoding="utf-8") as f:
        f.write(_SESSION_FM.format(sid="sB"))
        f.write("Bob: We decided to ship the FalkorDB connector now.\n")

    sdk = TortoiseSDK(os.path.join(tmp, "rb.db"))
    try:
        first = sdk.mine_corpus(corpus)
        assert first["sessions"] == 2
        g = sdk._get_proj().g

        # Exactly one utterance per session → exactly one Point per session.
        # (Without the run boundary, the second file's mine re-collects the
        # first file's points from the shared log.)
        for sid in ("session_sA", "session_sB"):
            rows = g.query("MATCH (p:Point {provenanceSource:$sid}) RETURN count(p)",
                           params={"sid": sid}).result_set
            assert rows[0][0] == 1, f"{sid}: expected 1 point, got {rows[0][0]}"

        # No cross-wiring: each session's decision event is about its OWN content.
        rows = g.query("MATCH (e:Event {eventId:'decision-session_sB-1'}) RETURN e.object").result_set
        assert rows, "sB decision event missing"
        assert "tortoise#123" not in rows[0][0], \
            f"cross-wiring: sB event contains sA content: {rows[0][0]!r}"
        rows = g.query("MATCH (e:Event {eventId:'decision-session_sA-1'}) RETURN e.object").result_set
        assert rows, "sA decision event missing"
        assert "FalkorDB" not in rows[0][0], \
            f"cross-wiring: sA event contains sB content: {rows[0][0]!r}"

        # Re-mine: unchanged files skip via file_hash — no point stacking.
        def count_points():
            return g.query("MATCH (p:Point) RETURN count(p)").result_set[0][0]
        before = count_points()
        second = sdk.mine_corpus(corpus)
        assert second["skipped"] >= 1, f"re-run should skip: {second}"
        assert count_points() == before, "re-mine stacked points"
    finally:
        sdk.close()
    print("PASS test_de2e1_run_boundary_two_sessions_no_cross_wiring")


def test_de2e1_symlink_entry_skipped():
    """A symlinked *.md inside the corpus is never read (R17 — host-file read
    + LLM exfiltration when model= is set): surfaced as a non-retryable error,
    and its target content is never mined.
    """
    tmp = tempfile.mkdtemp(prefix="tortoise_de2e1_sym_")
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus, exist_ok=True)
    with open(os.path.join(corpus, "real.md"), "w", encoding="utf-8") as f:
        f.write(_SESSION_FM.format(sid="sReal"))
        f.write("Alice: We decided to fix tortoise#123 today.\n")
    # host file OUTSIDE the corpus — the file a symlink would exfiltrate
    secret = os.path.join(tmp, "secret.md")
    with open(secret, "w", encoding="utf-8") as f:
        f.write(_SESSION_FM.format(sid="sEvil"))
        f.write("Mallory: Exfiltrate the port 16379 credentials now.\n")
    os.symlink(secret, os.path.join(corpus, "evil.md"))

    sdk = TortoiseSDK(os.path.join(tmp, "sym.db"))
    try:
        res = sdk.mine_corpus(corpus)
        sym_errors = [e for e in res["errors"] if str(e["file"]).endswith("evil.md")]
        assert sym_errors, f"symlink not surfaced in errors: {res['errors']}"
        assert sym_errors[0]["retryable"] is False
        # the symlink's target content was never read/mined
        g = sdk._get_proj().g
        rows = g.query(
            "MATCH (p:Point) WHERE p.content CONTAINS 'Exfiltrate' RETURN count(p)"
        ).result_set
        assert rows[0][0] == 0, "symlink target content was read and mined"
        # the real file still mined normally
        rows = g.query("MATCH (p:Point {provenanceSource:'session_sReal'}) RETURN count(p)").result_set
        assert rows[0][0] >= 1, "real file was not mined"
    finally:
        sdk.close()
    print("PASS test_de2e1_symlink_entry_skipped")


def test_de2e1_duplicate_session_primary_only():
    """Two files sharing a sessionId with DIFFERENT content: only the primary
    (first in sorted order) is mined; the non-primary copy is skipped and
    surfaced as a non-retryable error (no event flapping / LLM spend).
    """
    tmp = tempfile.mkdtemp(prefix="tortoise_de2e1_dup_")
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus, exist_ok=True)
    with open(os.path.join(corpus, "a.md"), "w", encoding="utf-8") as f:
        f.write(_SESSION_FM.format(sid="dupS"))
        f.write("Alice: We decided to fix tortoise#123 today.\n")
    with open(os.path.join(corpus, "b.md"), "w", encoding="utf-8") as f:
        f.write(_SESSION_FM.format(sid="dupS"))
        f.write("Bob: We decided to ship eldato#45 tomorrow.\n")

    sdk = TortoiseSDK(os.path.join(tmp, "dup.db"))
    try:
        res = sdk.mine_corpus(corpus)
        dup_errors = [e for e in res["errors"] if "duplicate sessionId" in e["error"]]
        assert dup_errors, f"non-primary copy not surfaced: {res['errors']}"
        assert dup_errors[0]["retryable"] is False
        g = sdk._get_proj().g
        # only the primary (a.md) was mined
        rows = g.query(
            "MATCH (p:Point) WHERE p.content CONTAINS 'tortoise#123' RETURN count(p)"
        ).result_set
        assert rows[0][0] >= 1, "primary file was not mined"
        rows = g.query(
            "MATCH (p:Point) WHERE p.content CONTAINS 'eldato' RETURN count(p)"
        ).result_set
        assert rows[0][0] == 0, "non-primary copy was mined"
    finally:
        sdk.close()
    print("PASS test_de2e1_duplicate_session_primary_only")


def test_de2e1_canonical_whitespace_collapse():
    """'port  16379' (doubled whitespace) canonicalizes to the SAME Object as
    'port 16379' — whitespace-collapsed canonicalization + ≥16-hex id scheme.
    """
    from tortoise.extractor import _canonical_name
    from tortoise.mining import ConversationMiner

    assert _canonical_name("port  16379") == _canonical_name("port 16379") == "port 16379"
    assert ConversationMiner._object_id("port  16379") == \
        ConversationMiner._object_id("port 16379")
    oid = ConversationMiner._object_id("port 16379")
    assert oid.startswith("obj_") and len(oid) - len("obj_") >= 16, oid

    # graph-level: a variant-mention reification resolves to the same Object
    # node as the base mention (identical deterministic id, MERGE-by-name)
    api, proj = _proj_api()
    try:
        mine_conversation(TRANSCRIPT, "s1", api, entity_stage=entity_stage_fixture())
        g = proj.g
        rows = g.query("MATCH (o:Object {name:'port 16379'}) RETURN o.id").result_set
        assert rows, "base Object missing"
        base_id = rows[0][0]
        # reify the doubled-whitespace variant with the same canonical id
        api2 = EventAPI(api.log, initiated_by="extractor", agent_id="test",
                        projection=proj)
        api2.add_object("port  16379", "other", id=ConversationMiner._object_id("port  16379"),
                        canonical_name=_canonical_name("port  16379"), title="port  16379")
        rows = g.query("MATCH (o:Object {id:$id}) RETURN count(o)",
                       params={"id": base_id}).result_set
        assert rows[0][0] >= 1, "variant did not resolve to the same Object"
    finally:
        proj.close()
    print("PASS test_de2e1_canonical_whitespace_collapse")


if __name__ == "__main__":
    test_de2e1_session_to_entity_objects_with_provenance()
    test_de2e1_remining_is_idempotent_for_objects()
    test_de2e_n5_empty_corpus()
    test_de2e_n1_malformed_session_skipped_batch_continues()
    test_de2e_n8_duplicate_session_file_skipped_via_file_hash()
    test_de2e1_run_boundary_two_sessions_no_cross_wiring()
    test_de2e1_symlink_entry_skipped()
    test_de2e1_duplicate_session_primary_only()
    test_de2e1_canonical_whitespace_collapse()
