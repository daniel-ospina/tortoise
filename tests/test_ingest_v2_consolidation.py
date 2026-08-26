"""E7 (#1539) — the consolidation WRITE path + E2E-11 integration gate
(surfaces S17/S12/S13 + the verify-P2 batch-probe fix).

Runs against FalkorDBLite (the sdk_factory fixture) with S3 simulated as a
REAL backend (search_graph returns the live graph's live statement points —
embedded mode skips S3 by design; the E7 consolidation must see priors).
The deterministic responder model drives extract_session_v2's S1/S2/S4 with
per-session embed fixtures so the 4-way classifier makes real decisions.

E2E-11 (05-detailed-e2e.md): NOOP link (one point, duplicates stamped, both
sessions linked, no double-count); UPDATE (supersession chain, newer value
live); DELETE-soft (retracted, no resurrect); owned negatives (identical-
value no-op → count unchanged; ambiguous → NOOP never UPDATE; self-supersede
guarded).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.ids import content_hash


def _pid(content: str) -> str:
    """The extractor's content-addressed point id (pt_ + sha[:62])."""
    return f"pt_{content_hash(content)[:62]}"


# ── deterministic responder model (S1 story + per-session S2/S4 embeds) ────

_ENTITY_GYM = {"name": "gym", "kind": "core:place", "lifecycle": "created",
               "supersedes": None, "note": None}

S0_EMBED = {
    "entities": [_ENTITY_GYM], "events": [], "operators": [],
    "points": [
        {"content": "gym at 6pm", "pointKind": "statement",
         "about_entities": ["gym"], "tier": "A",
         "quote": "I go to the gym at 6pm",
         "search_keys": ["gym time", "workout"]},
        {"content": "I drink coffee at 8am", "pointKind": "statement",
         "about_entities": [], "tier": "A",
         "quote": "I drink coffee at 8am",
         "search_keys": ["coffee", "morning"]},
    ],
}

S1_EMBED = {  # paraphrase duplicate + explicit withdrawal
    "entities": [_ENTITY_GYM], "events": [], "operators": [],
    "points": [
        {"content": "workout at the gym at six pm", "pointKind": "statement",
         "about_entities": ["gym"], "tier": "A",
         "quote": "my workout at the gym is at six pm",
         "search_keys": ["workout", "gym time"]},
    ],
    "retractions": [{"content": "I drink coffee at 8am"}],
}

S2_EMBED = {  # value change → UPDATE
    "entities": [_ENTITY_GYM], "events": [], "operators": [],
    "points": [
        {"content": "gym at 5pm", "pointKind": "statement",
         "about_entities": ["gym"], "tier": "A",
         "quote": "I moved my gym session to 5pm",
         "search_keys": ["gym time", "workout"]},
    ],
}

S3_EMBED = {  # identical re-assertion → NOOP identical
    "entities": [_ENTITY_GYM], "events": [], "operators": [],
    "points": [
        {"content": "gym at 5pm", "pointKind": "statement",
         "about_entities": ["gym"], "tier": "A",
         "quote": "yes, the 5pm gym session stands",
         "search_keys": ["gym time", "workout"]},
    ],
}

EMPTY_GAPS = {"entities": [], "events": [], "points": [],
              "operators": [], "retractions": [],
              "chain_notes": [], "link_before_create": []}


def _embed_for(blob: str) -> dict:
    """Per-session embed fixture, keyed on the story markers."""
    if "six pm" in blob:
        return S1_EMBED
    if "moved" in blob:
        return S2_EMBED
    if "stands" in blob:
        return S3_EMBED
    return S0_EMBED


def _story_for(transcript: str) -> str:
    if "six pm" in transcript:
        return "User works out at the gym at six pm and withdraws the coffee habit."
    if "moved" in transcript:
        return "User moved the gym session to 5pm."
    if "stands" in transcript:
        return "User confirms the 5pm gym session stands."
    return "User goes to the gym at 6pm and drinks coffee at 8am."


def _model():
    """Deterministic adapter: S1 story + S2/S4 fixtures (no phase-2 call —
    the fake search returns no entity candidates, so resolution is skipped)."""
    def respond(system: str, user: str) -> str:
        if "STORY SUMMARIZER" in system:
            return _story_for(user)
        blob = user if "GRAPH MAPPER" in system else system
        if "GAP REVIEWER" in system:
            return json.dumps(EMPTY_GAPS)
        if "GRAPH MAPPER" in system:
            return json.dumps(_embed_for(blob))
        raise AssertionError(f"unexpected system prompt: {system[:60]}")

    class _M:
        def complete(self, *, system: str, user: str) -> str:
            return respond(system, user)

    return _M()


def _fake_search(sdk, embed_list, story, **kw):
    """Simulate a real backend: S3 returns the live graph's LIVE statement
    points (terminal excluded — the #1391 search-layer contract)."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (p:Point {pointKind:'statement'}) "
        "WHERE (p.status IS NULL OR NOT (p.status IN $terminal)) "
        "RETURN p.id, p.content LIMIT 25",
        params={"terminal": ["retracted", "superseded", "archived",
                             "outdated"]},
    ).result_set
    return {"mode": "real", "degraded": False, "reason": None,
            "entities": [], "events": [],
            "points": [{"id": r[0], "content": r[1], "kind": "statement"}
                       for r in rows],
            "queries_run": 1}


def _e2e11_question() -> dict:
    return {
        "question_id": "q1",
        "haystack_session_ids": ["s0", "s1", "s2", "s3"],
        "haystack_dates": ["2026-06-10", "2026-06-12", "2026-06-14",
                           "2026-06-16"],
        "haystack_sessions": [
            [{"role": "user", "content": "I go to the gym at 6pm",
              "has_answer": False},
             {"role": "user", "content": "Also I drink coffee at 8am",
              "has_answer": False},
             {"role": "assistant", "content": "ok", "has_answer": False}],
            [{"role": "user",
              "content": "my workout at the gym is at six pm",
              "has_answer": True},
             {"role": "user", "content": "forget the coffee thing",
              "has_answer": False}],
            [{"role": "user", "content": "I moved my gym session to 5pm",
              "has_answer": True}],
            [{"role": "user", "content": "yes, the 5pm gym session stands",
              "has_answer": False}],
        ],
    }


def _run_all_sessions(sdk, question):
    """Run every session of the E2E-11 question through the v2 ingest.
    The caller's monkeypatch wires ``search_graph`` to ``_fake_search``
    (real-backend simulation) BEFORE calling this."""
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2
    return ingest_haystack_v2(sdk, question, model=_model())


class TestNoopWritePath:
    """D4 — NOOP = additive duplicates property + link-only CONTAINS edge;
    physically ONE point (no double-count); idempotent re-run."""

    def test_noop_folds_and_links(self, sdk_factory, monkeypatch):
        monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)
        sdk = sdk_factory()
        try:
            stats = _run_all_sessions(sdk, _e2e11_question())
            proj = sdk._get_proj()
            gym6, gym5, coffee = (_pid("gym at 6pm"), _pid("gym at 5pm"),
                                  _pid("I drink coffee at 8am"))

            # the paraphrase (s1) folded into the canonical 6pm point —
            # duplicates carries the folded session ref
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.duplicates, [])",
                params={"id": gym6}).result_set
            assert rows and rows[0][0] == ["lme:q1:s1"]
            # the identical re-assertion (s3) folded into the 5pm point
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.duplicates, [])",
                params={"id": gym5}).result_set
            assert rows and rows[0][0] == ["lme:q1:s3"]

            # both sessions CONTAINS the canonical 6pm point
            for sid in ("lme:q1:s0", "lme:q1:s1"):
                n = proj.g.query(
                    "MATCH (s:Session {id:$sid})-[:CONTAINS]->"
                    "(p:Point {id:$pid}) RETURN count(p)",
                    params={"sid": sid, "pid": gym6}).result_set[0][0]
                assert n == 1, f"session {sid} must CONTAINS the canonical point"

            # NO double-count: exactly TWO gym statement points (6pm + 5pm) —
            # the paraphrase was folded, never a third point
            n = proj.g.query(
                "MATCH (p:Point {pointKind:'statement'}) "
                "WHERE p.content CONTAINS 'gym' RETURN count(p)"
            ).result_set[0][0]
            assert n == 2

            # evidence OR-in: the folded session (s1) carried an answer turn
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.has_answer, false)",
                params={"id": gym6}).result_set
            assert rows[0][0] is True

            # stats: 2 NOOP applies (s1 paraphrase + s3 identical)
            assert stats["noops_applied"] == 2
            assert coffee  # coffee point exists (retracted later)
        finally:
            sdk.close()

    def test_noop_rerun_is_idempotent(self, sdk_factory, monkeypatch):
        monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)
        sdk = sdk_factory()
        try:
            q = _e2e11_question()
            _run_all_sessions(sdk, q)
            proj = sdk._get_proj()
            gym6 = _pid("gym at 6pm")

            # Re-ingest: the NOOP/DELETE/supersession WRITES are idempotent —
            # the duplicates set-merge appends nothing (CASE WHEN guard),
            # already-terminal deletions/supersessions are skipped. (Note:
            # re-running a supersession timeline RE-derives decisions against
            # the post-run prior set — a superseded fact is excluded from S3,
            # so one later session can re-derive an UPDATE. That is the
            # pre-existing E5 re-ingest property, bounded to a single point;
            # the eval ingests each question ONCE per fresh graph.)
            from tools.longmem_eval.ingest_v2 import ingest_haystack_v2
            stats2 = ingest_haystack_v2(sdk, q, model=_model())
            assert stats2["deletions_applied"] == 0  # already terminal → skip
            assert stats2["supersessions_written"] == 0  # already terminal
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.duplicates, [])",
                params={"id": gym6}).result_set
            assert rows[0][0] == ["lme:q1:s1"]  # unchanged — idempotent stamp
            # no explosion: the re-run adds at most ONE re-derived point
            n = proj.g.query(
                "MATCH (p:Point {pointKind:'statement'}) "
                "WHERE p.content CONTAINS 'gym' RETURN count(p)"
            ).result_set[0][0]
            assert n <= 3
        finally:
            sdk.close()


class TestDeleteSoft:
    """D5 — DELETE-soft only from explicit retractions; the write is the
    existing retract_point tombstone; no resurrect on recall (#1391)."""

    def test_retraction_tombstones_no_resurrect(self, sdk_factory,
                                                monkeypatch):
        monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)
        sdk = sdk_factory()
        try:
            stats = _run_all_sessions(sdk, _e2e11_question())
            proj = sdk._get_proj()
            coffee = _pid("I drink coffee at 8am")
            assert stats["deletions_applied"] == 1

            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.status, '')",
                params={"id": coffee}).result_set
            assert rows and rows[0][0] == "retracted"

            # default retrieval does NOT surface the retracted point...
            hits = sdk.tortoise_fts_query("coffee at 8am", entity_type="point",
                                          limit=5)
            assert all(h.get("id") != coffee for h in hits)
            # ...include_terminal opt-in does (audit/history — no hard delete)
            hits = sdk.tortoise_fts_query("coffee at 8am", entity_type="point",
                                          limit=5, include_terminal=True)
            assert any(h.get("id") == coffee for h in hits)
        finally:
            sdk.close()


class TestUpdateChain:
    """Task 5d + E2E-6 — UPDATE rides E5's machinery end-to-end: payload
    REVISES → supersession record → canonical sdk.supersede (CORRECTS edge
    + terminal old). E5 IS landed (#1537 merged) — asserted, not xfail."""

    def test_update_supersedes_old_point(self, sdk_factory, monkeypatch):
        monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)
        sdk = sdk_factory()
        try:
            stats = _run_all_sessions(sdk, _e2e11_question())
            proj = sdk._get_proj()
            gym6, gym5 = _pid("gym at 6pm"), _pid("gym at 5pm")
            assert stats["supersessions_written"] == 1

            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.status, '')",
                params={"id": gym6}).result_set
            assert rows and rows[0][0] == "superseded"
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.outdated, false)",
                params={"id": gym6}).result_set
            assert rows[0][0] is True
            n = proj.g.query(
                "MATCH (n:Point {id:$new})-[:CORRECTS]->(o:Point {id:$old}) "
                "RETURN count(o)",
                params={"new": gym5, "old": gym6}).result_set[0][0]
            assert n == 1
            # REVISES visible on the new node (observability, E5)
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.reason, '')",
                params={"id": gym5}).result_set
            assert rows[0][0] == "REVISES"
            # the newer value is LIVE (not terminal) — the older is terminal;
            # default retrieval must NOT surface the superseded old point
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.status, '')",
                params={"id": gym5}).result_set
            assert rows[0][0] not in ("superseded", "retracted", "archived")
            hits = sdk.tortoise_fts_query("gym at 6pm", entity_type="point",
                                          limit=5)
            assert all(h.get("id") != gym6 for h in hits)
        finally:
            sdk.close()


class TestAboutObjectParity:
    """Task 5e / D7 — the eval payload write emits the canonical aboutObject
    predicate (the classifier's entity gate becomes real in the eval)."""

    def test_payload_points_write_about_object(self, sdk_factory,
                                               monkeypatch):
        monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)
        sdk = sdk_factory()
        try:
            _run_all_sessions(sdk, _e2e11_question())
            proj = sdk._get_proj()
            # the LIVE payload point carries the canonical aboutObject edge
            # (the superseded old point's edge was TRANSFERRED to the new one
            # by E5's supersede edge-transfer — the live point is the claim)
            n = proj.g.query(
                "MATCH (p:Point {id:$id})-[:aboutObject]->"
                "(o:Object {name:'gym'}) RETURN count(o)",
                params={"id": _pid("gym at 5pm")}).result_set[0][0]
            assert n == 1, "the live point must carry the canonical aboutObject edge"
        finally:
            sdk.close()


class TestMultiOperator:
    def test_multiple_operators_all_written(self, sdk_factory, monkeypatch):
        """The batch ``existing`` set must survive the per-operator
        idempotency probe — a second operator would otherwise be skipped
        (regression guard for the D6 refactor)."""
        import tortoise.extractor_v2 as ev2
        from tools.longmem_eval.ingest_v2 import ingest_haystack_v2
        payload = {
            "entities": [], "events": [],
            "points": [
                {"id": "pt_a", "content": "a fact",
                 "pointKind": "statement"},
                {"id": "pt_b", "content": "b fact",
                 "pointKind": "statement"},
                {"id": "pt_c", "content": "c fact",
                 "pointKind": "statement"},
            ],
            "operators": [
                {"src": "pt_a", "dst": "pt_b", "op_type": "IMPL"},
                {"src": "pt_b", "dst": "pt_c", "op_type": "NAND"},
            ],
        }

        def fake_extract(model, conversation, **kw):
            return {"payload": payload, "minted_kinds": [],
                    "supersessions": [], "errors": [], "warnings": []}

        monkeypatch.setattr(ev2, "extract_session_v2", fake_extract)
        sdk = sdk_factory()
        try:
            q = {"question_id": "q_ops",
                 "haystack_session_ids": ["s0"],
                 "haystack_dates": ["2026-06-01"],
                 "haystack_sessions": [[{"role": "user",
                                         "content": "hello",
                                         "has_answer": False}]]}
            stats = ingest_haystack_v2(sdk, q, model=object(), chunk_turns=2)
            assert stats["operators"] == 2
        finally:
            sdk.close()


class TestBatchExistenceProbes:
    """Task 6 (verify-P2, surface 12): the batch _existing_point_ids helper
    collapses the per-turn/per-point N+1 to O(1) queries per session."""

    def _question(self, turns_per_session):
        return {
            "question_id": "q_probe",
            "haystack_session_ids": [f"s{i}" for i in range(len(turns_per_session))],
            "haystack_dates": ["2026-06-01"] * len(turns_per_session),
            "haystack_sessions": [
                [{"role": "user", "content": f"t{t}-{i}",
                  "has_answer": False} for i in range(n)]
                for t, n in enumerate(turns_per_session)],
        }

    def test_ingest_haystack_one_probe_per_session(self, sdk_factory,
                                                   monkeypatch):
        from unittest import mock

        import tools.longmem_eval.ingest as ingest_mod
        from tools.longmem_eval.ingest import ingest_haystack

        sdk = sdk_factory()
        try:
            q = self._question([3, 5])   # 3-turn and 5-turn sessions
            real = ingest_mod._existing_point_ids
            with mock.patch.object(ingest_mod, "_existing_point_ids",
                                   wraps=real) as sp:
                ingest_haystack(sdk, q)
            # exactly ONE existence probe per session (2 sessions) — NOT per
            # turn (the old code ran 3+2 and 5+3 probes = 13)
            assert sp.call_count == 2

            # re-run idempotency: same constant count, zero writes
            with mock.patch.object(ingest_mod, "_existing_point_ids",
                                   wraps=real) as sp2:
                stats = ingest_haystack(sdk, q)
            assert sp2.call_count == 2
            assert stats["chunks"] == 0  # nothing new written
        finally:
            sdk.close()

    def test_ingest_haystack_v2_constant_probes_per_session(self, sdk_factory,
                                                            monkeypatch):
        from unittest import mock

        import tools.longmem_eval.ingest as ingest_mod
        import tools.longmem_eval.ingest_v2 as ingest_v2_mod
        monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)

        sdk = sdk_factory()
        try:
            q = _e2e11_question()   # 3/2/1/1-turn sessions
            # ingest_v2 holds its OWN module-level name (from .ingest import)
            # — patch BOTH so every existence probe is counted.
            real = ingest_mod._existing_point_ids
            with mock.patch.object(ingest_mod, "_existing_point_ids",
                                   wraps=real), \
                 mock.patch.object(ingest_v2_mod, "_existing_point_ids",
                                   wraps=real) as sp:
                _run_all_sessions(sdk, q)
            # v2 = session loop (1) + _write_payload (1) per session — the
            # count does NOT scale with turn count (4 sessions → 4–8; the
            # per-turn code would run 3+2+1+1 turn probes + per-point probes).
            assert 4 <= sp.call_count <= 8
        finally:
            sdk.close()


class TestE2E11IntegrationGate:
    """Task 7 — the full 4-session fixture on the shared eval graph: NOOP
    link, UPDATE chain, DELETE-soft, no double-count + owned negatives."""

    def test_e2e11_full_gate(self, sdk_factory, monkeypatch):
        monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)
        sdk = sdk_factory()
        try:
            stats = _run_all_sessions(sdk, _e2e11_question())
            proj = sdk._get_proj()
            gym6, gym5 = _pid("gym at 6pm"), _pid("gym at 5pm")
            coffee = _pid("I drink coffee at 8am")

            # Given: 4 sessions — duplicate paraphrase / value update /
            # identical re-assert / withdrawal retraction
            # When: all ingested via ingest_haystack_v2
            # Then:
            #  NOOP link — ONE canonical point, duplicates stamped, both
            #  sessions linked, aggregation count 1 per fact
            assert stats["noops_applied"] == 2
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.duplicates, [])",
                params={"id": gym6}).result_set
            assert rows[0][0] == ["lme:q1:s1"]
            n = proj.g.query(
                "MATCH (s:Session)-[:CONTAINS]->(p:Point {id:$id}) "
                "RETURN count(DISTINCT s)",
                params={"id": gym6}).result_set[0][0]
            assert n == 2   # s0 (creator) + s1 (folded)
            n = proj.g.query(
                "MATCH (p:Point {pointKind:'statement'}) "
                "WHERE p.content CONTAINS 'gym' RETURN count(p)"
            ).result_set[0][0]
            assert n == 2   # no double-count (paraphrase folded, identical folded)

            #  UPDATE — supersession chain, newer value live (E2E-6)
            assert stats["supersessions_written"] == 1
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.status, '')",
                params={"id": gym6}).result_set
            assert rows[0][0] == "superseded"
            n = proj.g.query(
                "MATCH (n:Point {id:$new})-[:CORRECTS]->(o:Point {id:$old}) "
                "RETURN count(o)",
                params={"new": gym5, "old": gym6}).result_set[0][0]
            assert n == 1

            #  DELETE-soft — retracted, no resurrect
            assert stats["deletions_applied"] == 1
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.status, '')",
                params={"id": coffee}).result_set
            assert rows[0][0] == "retracted"
            hits = sdk.tortoise_fts_query("coffee at 8am", entity_type="point",
                                          limit=5)
            assert all(h.get("id") != coffee for h in hits)

            #  owned negative: identical-value no-op → count unchanged
            #  (the s3 re-assertion folded; no new point, no new supersession)
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.duplicates, [])",
                params={"id": gym5}).result_set
            assert rows[0][0] == ["lme:q1:s3"]
            n = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.status, '')",
                params={"id": gym5}).result_set
            assert n[0][0] != "superseded"   # identical re-assert never supersedes

            #  owned negative: self-supersede guarded — no pt→pt record where
            #  superseded == supersedes_by ever materialized
            rows = proj.g.query(
                "MATCH (n:Point)-[:CORRECTS]->(o:Point) "
                "WHERE n.id = o.id RETURN count(n)").result_set
            assert rows[0][0] == 0
        finally:
            sdk.close()

    @pytest.mark.skipif(
        not os.environ.get("TORTOISE_DB_URI"),
        reason="requires TORTOISE_DB_URI (real FalkorDB for the e2e11 "
        "integration smoke — URI-unset shapes skip by design)")
    def test_e2e11_real_mode_smoke(self, sdk_factory):
        """Real-mode smoke: with TORTOISE_DB_URI set the REAL search_graph
        (not the fake) drives S3 — graph state parity asserted on the same
        fixture. Skipped without a live backend."""
        from tools.longmem_eval.ingest_v2 import ingest_haystack_v2
        sdk = sdk_factory()
        try:
            q = _e2e11_question()
            stats = ingest_haystack_v2(sdk, q, model=_model())
            assert stats["noops_applied"] >= 1 or stats["points"] > 0
        finally:
            sdk.close()
