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
    """Plan §4.1: obj_ + sha256(domain-separated canonical name)[:12]."""
    canonical = name.lower().strip()
    for ch in ",.!?;:'\"()[]{}":
        canonical = canonical.replace(ch, "")
    return "obj_" + hashlib.sha256(f"obj:{canonical}".encode()).hexdigest()[:12]


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


if __name__ == "__main__":
    test_de2e1_session_to_entity_objects_with_provenance()
    test_de2e1_remining_is_idempotent_for_objects()
    test_de2e_n5_empty_corpus()
    test_de2e_n1_malformed_session_skipped_batch_continues()
    test_de2e_n8_duplicate_session_file_skipped_via_file_hash()
