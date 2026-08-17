"""Tests for the v2 5-stage extractor pipeline (issue #1350).

Covers the verification checklist surfaces: chunker+compiler (unit),
S2 map-to-embed (unit, mock), S3 graph search (graceful degradation +
integration with the real backend, skip-if-unavailable), S5 embed execution
(dependency order, link-before-create, supersession, chains, minted kinds),
and the extract_session_v2 orchestrator (mock model, no LLM).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402


class MockModel:
    """Deterministic adapter with the complete(system=, user=) interface.

    ``responses`` may be a list (consumed in order) or a callable
    (system, user) -> str.
    """

    def __init__(self, responses):
        self._responses = responses
        self._i = 0
        self.calls: list[tuple[str, str]] = []
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        if callable(self._responses):
            return self._responses(system, user)
        if self._i >= len(self._responses):
            raise AssertionError("MockModel exhausted")
        resp = self._responses[self._i]
        self._i += 1
        return resp


S2_FIXTURE = {
    "entities": [
        {"name": "single-flash pipeline", "kind": "core:plan", "lifecycle": "created",
         "supersedes": None, "note": None},
        {"name": "cleaning-pass tier", "kind": "core:plan", "lifecycle": "superseded",
         "supersedes": None, "note": None},
    ],
    "events": [
        {"content": "The owner paused the solar cleaning tier",
         "eventKind": "core:decision", "about_entities": ["cleaning-pass tier"]},
    ],
    "points": [
        {"content": "single-flash with granularity is the working path",
         "pointKind": "statement", "about_entities": ["single-flash pipeline"]},
    ],
    "operators": [
        {"src": "single-flash with granularity is the working path",
         "dst": "The owner paused the solar cleaning tier", "op_type": "IMPL"},
        {"src": "single-flash with granularity is the working path",
         "dst": "The owner paused the solar cleaning tier", "op_type": "MITIGATES",
         "target_edge": {"src": "single-flash with granularity is the working path",
                         "dst": "The owner paused the solar cleaning tier",
                         "op_type": "IMPL"},
         "strength": 0.3},
    ],
    "chain_notes": [],
    "link_before_create": [],
}


# ── Master list ────────────────────────────────────────────────────────────

class TestMasterList:
    def test_sections_present(self):
        m = v2.build_master_list()
        for sec in ("objects", "subjects", "points", "events", "pack_kinds",
                    "chains", "memory_granularity"):
            assert sec in m
        assert "core:organization" in m["subjects"]
        assert "statement" in m["points"]
        assert "core:decision" in m["events"]
        assert "productDelivery" in m["chains"]

    def test_chains_have_paths_and_notes(self):
        m = v2.build_master_list()
        for name, c in m["chains"].items():
            assert c["path"], f"chain {name} has an empty path"
            assert c["note"]
        assert m["chains"]["productDelivery"]["path"][-1] == "architecture"

    def test_master_kind_forms(self):
        m = v2.build_master_list()
        forms = v2.master_kind_forms(m)
        assert "core:goal" in forms
        assert "goal" in forms
        assert "core:occurrence" in forms

    def test_memory_granularity_rides_through(self):
        m = v2.build_master_list()
        assert m["memory_granularity"], "packs must declare memory_granularity"


# ── Chunker + compiler ─────────────────────────────────────────────────────

class TestChunker:
    def test_splits_contiguous(self):
        edus = [{"index": i, "role": "user", "text": f"t{i}"}
                for i in range(7)]
        chunks = v2.chunk_transcript(edus, target=3)
        assert [len(c) for c in chunks] == [3, 3, 1]
        assert chunks[1][0]["index"] == 3

    def test_target_validation(self):
        with pytest.raises(ValueError):
            v2.chunk_transcript([], target=0)


class TestCompiler:
    def test_stitches_arc_and_dedups_cross_chunk(self):
        s1 = ("We believed X. The session revealed Y. "
              "The reasoning supports Z.")
        s2 = ("We believed X. The session revealed Y and changed our "
              "approach to W. A new fact supports Q.")
        compiled = v2.compile_stories([s1, s2])
        # Cross-chunk duplicates dropped (entity preserved once).
        assert compiled.count("We believed X.") == 1
        # The arc is preserved — the new/changed parts survive.
        assert "changed our approach to W" in compiled
        assert "supports Q" in compiled
        assert compiled.index("We believed X.") < compiled.index("supports Q")

    def test_empty(self):
        assert v2.compile_stories([]) == ""
        assert v2.compile_stories(["", ""]) == ""


# ── S2 map-to-embed (mock) ─────────────────────────────────────────────────

class TestS2:
    def test_parses_embed_list(self):
        model = MockModel([json.dumps(S2_FIXTURE)])
        out = v2.run_s2(model, "STORY")
        assert out["entities"][0]["name"] == "single-flash pipeline"
        assert out["events"][0]["eventKind"] == "core:decision"
        assert out["points"][0]["pointKind"] == "statement"

    def test_rejects_unparseable(self):
        model = MockModel(["no json here"])
        with pytest.raises(ValueError):
            v2.run_s2(model, "STORY")

    def test_prompt_contains_master_and_chains(self):
        model = MockModel([json.dumps(S2_FIXTURE)])
        v2.run_s2(model, "STORY")
        system = model.calls[0][0]
        assert "MASTER LIST" in system
        assert "productDelivery" in system
        assert "TRUTH vs WEIGHT" in system
        assert "LINK-BEFORE-CREATE" in system
        assert "STRICT EXCLUSION" in system

    def test_minted_kind_report(self):
        bad = json.loads(json.dumps(S2_FIXTURE))
        bad["entities"].append({"name": "worktree", "kind": "worktree",
                                "lifecycle": "created", "supersedes": None,
                                "note": None})
        minted = v2._minted_kind_report(bad)
        assert any("worktree" in m for m in minted)
        assert v2._minted_kind_report(S2_FIXTURE) == []


# ── S3 graph search ────────────────────────────────────────────────────────

class TestS3:
    def test_backend_mode_embedded_when_unset(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)
        assert v2.resolve_backend_mode() == "embedded"

    def test_backend_mode_real_on_uri(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@localhost:6379/g")
        assert v2.resolve_backend_mode() == "real"

    def test_degrades_when_embedded(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)
        res = v2.search_graph(None, S2_FIXTURE, "STORY")
        assert res["degraded"] is True
        assert "FalkorDBLite" in (res["reason"] or "")

    def test_degrades_without_client(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@localhost:6379/g")
        res = v2.search_graph(None, S2_FIXTURE, "STORY")
        assert res["degraded"] is True
        assert "no graph client" in (res["reason"] or "")

    def test_queries_derived_and_results_formatted(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@localhost:6379/g")

        class MockSDK:
            def __init__(self):
                self.calls = []

            def tortoise_fts_query(self, query, *, entity_type, limit=3):
                self.calls.append((query, entity_type))
                if entity_type == "object":
                    return [{"id": "obj-1", "content": "single-flash pipeline",
                             "kind": "core:plan"}]
                if entity_type == "event":
                    return [{"id": "ev-1", "content": "owner paused solar tier",
                             "kind": "core:decision"}]
                return [{"id": "pt-1", "content": "flash is the path",
                         "kind": "statement"}]

        sdk = MockSDK()
        res = v2.search_graph(sdk, S2_FIXTURE, "The story. First para.")
        assert res["degraded"] is False
        assert res["entities"] == [{"id": "obj-1", "name": "single-flash pipeline",
                                    "kind": "core:plan"}]
        assert res["events"][0]["id"] == "ev-1"
        assert res["points"][0]["id"] == "pt-1"
        # both object and event queries were run
        types = {t for _, t in sdk.calls}
        assert "object" in types and "event" in types

    def test_degrades_on_backend_error(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@localhost:6379/g")

        class BoomSDK:
            def tortoise_fts_query(self, query, *, entity_type, limit=3):
                raise ConnectionError("graph unreachable")

        res = v2.search_graph(BoomSDK(), S2_FIXTURE, "STORY")
        assert res["degraded"] is True
        assert "graph unreachable" in (res["reason"] or "")
        assert res["queries_run"] == 1  # first query attempted, then aborted


# ── S5 embed execution ─────────────────────────────────────────────────────

class TestS5:
    def test_dependency_order_and_layer1(self):
        result = v2.execute_embed(S2_FIXTURE, {}, session_id="s1",
                                  story_arc="arc", summary="sum")
        payload = result["payload"]
        ids = {p["id"] for p in payload["points"]} | {e["id"] for e in payload["events"]}
        for op in payload["operators"]:
            assert op["src"] in ids and op["dst"] in ids, op
            if op["op_type"] == "MITIGATES":
                assert op["target"]["src"] in ids
                assert op["target"]["dst"] in ids
        assert payload["schema_version"] == "1"
        assert payload["client_commit_id"], "replay-safe id must be computed"
        assert payload["story_arc"] == "arc"
        assert payload["telemetry"]["counts"]["kept"] == len(payload["points"])

    def test_payload_passes_layer1(self):
        """The S5 payload is the POST /v1/sessions/commit body — it must pass
        the server-side Layer-1 gate (400/422 class)."""
        from tortoise.commit_schema import validate_payload_dict
        result = v2.execute_embed(S2_FIXTURE, {}, session_id="s1",
                                  story_arc="arc", summary="sum")
        l1, _model = validate_payload_dict(result["payload"])
        assert l1.ok, l1.errors

    def test_entities_dedup_and_validate(self):
        dup = json.loads(json.dumps(S2_FIXTURE))
        dup["entities"].append({"name": "single-flash pipeline",
                                "kind": "core:plan", "lifecycle": "created",
                                "supersedes": None, "note": None})
        result = v2.execute_embed(dup, {}, session_id="s1")
        names = [e["name"] for e in result["payload"]["entities"]]
        assert names.count("single-flash pipeline") == 1

    def test_link_before_create_existing_entity(self):
        search = {"entities": [{"id": "obj-9", "name": "cleaning-pass tier",
                                "kind": "core:plan"}], "points": [], "events": []}
        result = v2.execute_embed(S2_FIXTURE, search, session_id="s1")
        notes = result["link_before_create"]
        entity_note = next(n for n in notes
                           if "cleaning-pass tier" in n["searched_for"])
        assert entity_note["found"] is True
        assert "obj-9" in entity_note["note"]

    def test_point_supersession_revises(self):
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt-old", "content":
                              "single flash with granularity is the working path",
                              "kind": "statement"}]}
        result = v2.execute_embed(S2_FIXTURE, search, session_id="s1")
        pts = result["payload"]["points"]
        assert pts[0]["reason"] == "REVISES"
        note = next(n for n in result["link_before_create"]
                    if "single-flash with granularity" in n["searched_for"])
        assert note["found"] is True

    def test_exact_point_match_dedups(self):
        content = "single-flash with granularity is the working path"
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt-same", "content": content,
                              "kind": "statement"}]}
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["points"] = [{"content": content, "pointKind": "statement",
                            "about_entities": []}]
        result = v2.execute_embed(embed, search, session_id="s1")
        assert result["payload"]["points"][0]["id"] == "pt-same"

    def test_minted_kind_repair(self):
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["entities"].append({"name": "worktree", "kind": "worktree",
                                  "lifecycle": "created", "supersedes": None,
                                  "note": None})
        result = v2.execute_embed(embed, {}, session_id="s1")
        e = next(e for e in result["payload"]["entities"] if e["name"] == "worktree")
        assert e["kind"] == "core:other"
        assert any("worktree" in w for w in result["warnings"])
        assert any("worktree" in m for m in result["minted_kinds"])

    def test_mitigates_strength_clamped(self):
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["operators"][1]["strength"] = 0.9
        result = v2.execute_embed(embed, {}, session_id="s1")
        op = result["payload"]["operators"][1]
        assert op["strength"] == 0.5
        assert any("0.10, 0.50" in w for w in result["warnings"])

    def test_unresolved_operator_dropped(self):
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["operators"].append({"src": "not a real content",
                                   "dst": "also not", "op_type": "IMPL"})
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert len(result["payload"]["operators"]) == 2  # dropped
        assert any("did not resolve" in w for w in result["warnings"])

    def test_mitigates_unresolved_target_dropped(self):
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["operators"] = [
            {"src": "single-flash with granularity is the working path",
             "dst": "The owner paused the solar cleaning tier", "op_type": "MITIGATES",
             "target_edge": {"src": "ghost", "dst": "ghost2", "op_type": "IMPL"},
             "strength": 0.3}]
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert result["payload"]["operators"] == []
        assert any("MITIGATES target edge not emitted" in w
                   for w in result["warnings"])


class TestChains:
    def _embed_with_pair(self, about, kinds):
        embed = {"entities": [
            {"name": "arch", "kind": kinds[0], "lifecycle": "created",
             "supersedes": None, "note": None},
            {"name": "useCase", "kind": kinds[1], "lifecycle": "created",
             "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"content": "arch connects to useCase",
                        "pointKind": "statement", "about_entities": about}]}
        return embed

    def test_reverse_chain_repaired_when_intermediate_exists(self):
        # architecture (pos 6) connects to useCase (pos 1) — reverse chain
        # order in productDelivery; a feature (pos 2) in the list is the
        # nearest valid intermediate → repair is possible.
        embed = self._embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"])
        embed["entities"].append({"name": "feature", "kind": "product-strategy:feature",
                                  "lifecycle": "created", "supersedes": None,
                                  "note": None})
        notes = v2.validate_chains(embed)
        assert notes, "reverse architecture→useCase must be flagged"
        assert notes[0]["action"] == "repaired"
        assert "feature" in notes[0]["note"]

    def test_reverse_chain_warned_without_intermediate(self):
        embed = self._embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"])
        notes = v2.validate_chains(embed)
        assert notes[0]["action"] == "warned"
        assert "do NOT invent" in notes[0]["note"]

    def test_chain_order_ok_not_flagged(self):
        embed = self._embed_with_pair(["useCase", "feature"],
                                      ["product-strategy:useCase",
                                       "product-strategy:feature"])
        assert v2.validate_chains(embed) == []

    def test_never_blocks(self):
        # chain violations surface as notes; execute_embed never raises
        embed = self._embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"])
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert any(n["action"] in ("repaired", "warned")
                   for n in result["chain_notes"])
        assert result["payload"]["points"]  # still emitted


# ── The orchestrator (mock model, no LLM) ──────────────────────────────────

class TestOrchestrator:
    def test_full_pipeline_mock(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)

        def resp(system, user):
            if "STORY SUMMARIZER" in system:
                return "We believed X. The session revealed Y."
            if "GRAPH MAPPER" in system:
                return json.dumps(S2_FIXTURE)
            if "GAP REVIEWER" in system:
                return json.dumps(S2_FIXTURE)  # no gaps found
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        conv = [{"role": "user", "content": "we decided X"},
                {"role": "assistant", "content": "and Y follows"}]
        out = v2.extract_session_v2(MockModel(resp), conv)
        assert out["story_arc"], "S1 compiled story must exist"
        assert out["payload"] is not None
        assert out["payload"]["session_id"] == out["session_id"]
        assert out["payload"]["points"]
        assert out["search"]["degraded"] is True  # embedded backend honored
        assert out["errors"] == []
        assert out["stats"]["chunks"] == 1

    def test_empty_conversation(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        out = v2.extract_session_v2(MockModel([]), [])
        assert out["payload"] is None
        assert any("empty conversation" in w for w in out["warnings"])

    def test_chunked_compile_pipeline(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        edus = [{"role": "user", "content": f"fact {i}"} for i in range(6)]

        def resp(system, user):
            if "STORY SUMMARIZER" in system:
                return "We believed X. The session revealed Y."
            return json.dumps(S2_FIXTURE)

        out = v2.extract_session_v2(MockModel(resp), edus, chunk_size=2)
        assert out["stats"]["chunks"] == 3
        # compiled story dedups the repeated sentence
        assert out["story_arc"].count("We believed X.") == 1
        assert out["payload"] is not None


# ── S3 integration with the real backend (skip-if-unavailable) ─────────────

def test_s3_real_backend_search(tmp_path):
    """Integration: S3 against the REAL graph backend. Skipped unless a live
    TORTOISE_DB_URI (docker:// / redis://) is configured (#942 convention) —
    FalkorDBLite never passes this gate (owner confirmation #3)."""
    from tests._live_utils import _skip_unless_live_uri
    _skip_unless_live_uri()

    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(namespace="test_extractor_v2_s3")
    try:
        sdk.create_point("statement", "single flash granularity working path",
                         status="live")
        embed = {"entities": [], "events": [], "points": [
            {"content": "single flash granularity working path",
             "pointKind": "statement", "about_entities": []}],
            "operators": [], "chain_notes": [], "link_before_create": []}
        res = v2.search_graph(sdk, embed, "The story.")
        assert res["degraded"] is False
        assert res["points"], "seeded point must be found by the real backend"
    finally:
        sdk.close()
