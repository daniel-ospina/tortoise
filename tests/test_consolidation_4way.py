"""E7 (#1539) — the 4-way consolidation classifier + entity resolution +
S3 batch enrichment (surfaces S17/S16/S12).

Unit + integration coverage for the D1 decision table (ADD/UPDATE/NOOP-
identical/NOOP-paraphrase/DELETE), the execute_embed result-level records
(noops/deletions, retractions resolution, fail-open), the D3 two-phase
entity resolution (deterministic-first, bounded LLM fallback, degrade-to-
ADD, never blocks), and the D7 S3 batch enrichment (one query, row shape,
degraded-mode guards).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100


class TestValueSignature:
    """D2/Q3: deterministic value-token normalization."""

    def test_clock_forms_normalize(self):
        assert v2._value_signature("gym at 6pm") == "p06:00"
        assert v2._value_signature("gym at six pm") == "p06:00"
        assert v2._value_signature("gym at 6:00 pm") == "p06:00"
        assert v2._value_signature("gym at 6:30 pm") == "p06:30"
        assert v2._value_signature("gym at six thirty pm") == "p06:30"

    def test_compound_and_quantity_tokens(self):
        assert v2._value_signature("27:12") == "27:12"
        assert v2._value_signature("27m12s") == "27m12s"
        assert v2._value_signature("my 5k time") == "5k"
        assert v2._value_signature("10km run") == "10km"

    def test_no_value_token_returns_none(self):
        assert v2._value_signature("the team meets weekly") is None
        assert v2._value_signature("single-flash with granularity") is None


class TestClassifyConsolidation:
    """D1 — the pure 4-way decision table (Task 1 acceptance)."""

    def test_add_no_prior(self):
        d = v2.classify_consolidation({"content": "gym at 6pm"}, [])
        assert d.decision == "ADD"
        assert d.prior_id == ""
        assert d.overlap == 0.0

    def test_update_same_entity_attr_later_date(self):
        """(b) UPDATE — "gym at 6pm" → "gym at 5pm", later date, overlap >=
        0.6 with the entity+attribute gate."""
        d = v2.classify_consolidation(
            {"content": "gym at 5pm", "about_entities": ["gym"]},
            [{"id": "pt1", "content": "gym at 6pm"}],
            entity_mentions=["gym"], current_date="2026-06-16")
        assert d.decision == "UPDATE"
        assert d.prior_id == "pt1"
        assert d.overlap >= v2.REVISES_MIN_OVERLAP
        assert d.reason == "value_change"

    def test_update_older_date_never_supersedes(self):
        """CG-2 negative: an OLDER session date never updates a newer prior."""
        d = v2.classify_consolidation(
            {"content": "gym at 5pm", "about_entities": ["gym"]},
            [{"id": "pt1", "content": "gym at 6pm", "when": "2026-06-16"}],
            entity_mentions=["gym"], current_date="2026-06-02")
        assert d.decision != "UPDATE"

    def test_noop_identical(self):
        """(c) NOOP identical — exact normalized equality."""
        d = v2.classify_consolidation(
            {"content": "gym at 6pm"},
            [{"id": "pt1", "content": "Gym at 6pm"}])
        assert d.decision == "NOOP"
        assert d.reason == "identical"
        assert d.prior_id == "pt1"
        assert d.overlap == 1.0

    def test_noop_paraphrase_value_signature(self):
        """(d) NOOP paraphrase — "workout at the gym at six pm" vs
        "gym at 6pm": equal value-signature short-circuits the bands."""
        d = v2.classify_consolidation(
            {"content": "workout at the gym at six pm",
             "about_entities": ["gym"]},
            [{"id": "pt1", "content": "gym at 6pm"}],
            entity_mentions=["gym"])
        assert d.decision == "NOOP"
        assert d.reason == "paraphrase"
        assert d.prior_id == "pt1"
        assert d.overlap < v2.NOOP_MIN_OVERLAP  # sig path, not the band

    def test_noop_band_with_entity_gate(self):
        """(e) NOOP band — overlap in [0.45, 0.6) + entity mention gate.
        The candidate's frame is a strict SUBSET of the prior's (no new
        value token), so E5's contradiction pass cannot fire (value does
        not differ) and the band decides."""
        d = v2.classify_consolidation(
            {"content": "the team meets weekly",
             "about_entities": ["team"]},
            [{"id": "pt1", "content": "the team meets weekly in main office"}],
            entity_mentions=["team"])
        assert d.decision == "NOOP"
        assert d.reason == "paraphrase"
        assert v2.NOOP_MIN_OVERLAP <= d.overlap < v2.REVISES_MIN_OVERLAP

    def test_length_guard_short_vs_long_add(self):
        """(f) length guard — a 5-token point sharing 3 tokens with a
        50-token point is neither REVISES nor NOOP (E5 max-denominator)."""
        short = "gym 6pm schedule changed today"          # 5 tokens
        long_ = "gym 6pm schedule " + " ".join(f"word{i}" for i in range(47))
        d = v2.classify_consolidation(
            {"content": short},
            [{"id": "pt_long", "content": long_}])
        assert d.decision == "ADD"

    def test_ambiguous_entity_high_overlap_noop_never_update(self):
        """(g) ambiguous entity + high overlap → NOOP, NEVER UPDATE (E2E-11
        owned negative — supersede would wrongly terminalize a fact)."""
        d = v2.classify_consolidation(
            {"content": "yoga at 6pm", "about_entities": ["yoga"]},
            [{"id": "pt1", "content": "gym at 6pm"}],
            entity_mentions=["yoga"], current_date="2026-06-16")
        assert d.decision == "NOOP"
        assert "ambiguous" in d.evidence

    def test_no_self_match_identical_not_update(self):
        """(h) no self-match — identical content → NOOP, never UPDATE."""
        d = v2.classify_consolidation(
            {"content": "gym at 6pm", "about_entities": ["gym"]},
            [{"id": "pt1", "content": "gym at 6pm"}],
            entity_mentions=["gym"], current_date="2026-06-16")
        assert d.decision == "NOOP"
        assert d.reason == "identical"

    def test_equal_value_signature_noop_not_update(self):
        """E2E-11 MECE boundary — a reworded-identical VALUE ("6pm" vs
        "six pm") is NOOP, never UPDATE (the E5 value-diff guard)."""
        d = v2.classify_consolidation(
            {"content": "gym at six pm", "about_entities": ["gym"]},
            [{"id": "pt1", "content": "gym at 6pm"}],
            entity_mentions=["gym"], current_date="2026-06-16")
        assert d.decision == "NOOP"
        assert d.reason == "paraphrase"

    def test_update_via_fact_value_contradiction_below_band(self):
        """E5's entity-grounded contradiction pass still REVISES when the
        length-guarded overlap misses ("gym 6pm" → "gym 5pm": 1 shared
        token, ov 0.5 < 0.6)."""
        d = v2.classify_consolidation(
            {"content": "gym 5pm", "about_entities": ["gym"]},
            [{"id": "pt1", "content": "gym 6pm"}],
            entity_mentions=["gym"], current_date="2026-06-16")
        assert d.decision == "UPDATE"

    def test_delete_never_from_content(self):
        """D5 — DELETE is NEVER produced from content alone."""
        d = v2.classify_consolidation(
            {"content": "gym at 5pm", "about_entities": ["gym"]},
            [{"id": "pt1", "content": "gym at 6pm"}],
            entity_mentions=["gym"])
        assert d.decision != "DELETE"

    def test_search_keys_attribute_gate(self):
        """D2 attribute gate — search_keys overlap anchors the attribute when
        both sides carry keys; a candidate with DIFFERENT keys and no value
        signature stays conservative (no UPDATE on an unrelated attribute)."""
        d = v2.classify_consolidation(
            {"content": "gym at 5pm", "about_entities": ["gym"],
             "search_keys": ["gym time", "workout"]},
            [{"id": "pt1", "content": "gym at 6pm",
              "search_keys": ["membership cost"]}],
            entity_mentions=["gym"], current_date="2026-06-16")
        # no shared attribute AND different value sig → no UPDATE; high
        # overlap still folds as NOOP (never UPDATE on ambiguity)
        assert d.decision == "NOOP"


class TestExecuteEmbedConsolidation:
    """Task 2 — execute_embed emits result-level noops/deletions records;
    NOOP stays OUT of the payload (D8); retractions resolve fail-open."""

    def test_noop_record_not_in_payload(self):
        content = "workout at the gym at six pm"
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt_old", "content": "gym at 6pm",
                              "kind": "statement"}]}
        embed = {"points": [{"content": content, "pointKind": "statement",
                             "about_entities": ["gym"]}]}
        result = v2.execute_embed(embed, search, session_id="s1")
        assert len(result["noops"]) == 1
        rec = result["noops"][0]
        assert rec["point_id"] == "pt_old"
        assert rec["session_ref"] == "s1"
        assert rec["reason"] == "paraphrase"
        assert rec["overlap"] > 0.0 and rec["evidence"]
        assert all(p["content"] != content
                   for p in result["payload"]["points"])
        assert result["stats"]["noops"] == 1

    def test_noop_identical_record_and_operator_resolution(self):
        """A NOOP'd point stays resolvable as an operator endpoint — the
        content maps to the EXISTING id (canonical point, no double-count)."""
        content = "gym at 6pm"
        other = "the gym is great"
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt_old", "content": content,
                              "kind": "statement"}]}
        embed = {"points": [{"content": content, "pointKind": "statement",
                             "about_entities": []},
                             {"content": other, "pointKind": "statement",
                             "about_entities": []}],
                 "operators": [{"src": content, "dst": other,
                                "op_type": "IMPL"}]}
        result = v2.execute_embed(embed, search, session_id="s1")
        noop = result["noops"][0]
        assert noop["reason"] == "identical"
        assert noop["point_id"] == "pt_old"
        ops = result["payload"]["operators"]
        assert ops and ops[0]["src"] == "pt_old"
        assert result["stats"]["points"] == 1  # only the ADD point

    def test_retraction_resolves_to_deletion(self):
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt_old", "content": "gym at 6pm",
                              "kind": "statement"}]}
        embed = {"retractions": [{"content": "gym at 6pm"}]}
        result = v2.execute_embed(embed, search, session_id="s1")
        assert len(result["deletions"]) == 1
        rec = result["deletions"][0]
        assert rec["point_id"] == "pt_old"
        assert "retraction" in rec["evidence"]
        assert result["stats"]["deletions"] == 1

    def test_retraction_by_id(self):
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt_x", "content": "coffee at 8am",
                              "kind": "statement"}]}
        embed = {"retractions": [{"id": "pt_x"}]}
        result = v2.execute_embed(embed, search, session_id="s1")
        assert result["deletions"][0]["point_id"] == "pt_x"

    def test_ambiguous_retraction_skipped_never_guess(self):
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt_a", "content": "gym at 6pm",
                              "kind": "statement"},
                             {"id": "pt_b", "content": "gym at 6pm",
                              "kind": "statement"}]}
        embed = {"retractions": [{"content": "gym at 6pm"}]}
        result = v2.execute_embed(embed, search, session_id="s1")
        assert result["deletions"] == []
        assert any("ambiguous" in w for w in result["warnings"])

    def test_unresolvable_retraction_fails_open(self):
        embed = {"retractions": [{"content": "no such fact anywhere"}]}
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert result["deletions"] == []
        assert any("no S3 prior" in w for w in result["warnings"])
        assert result["payload"] is not None  # pipeline proceeds

    def test_retractions_not_in_layer1_payload(self):
        """D8 — noops/deletions stay result-level; the payload schema is
        byte-identical (client_commit_id canonical unchanged)."""
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt_old", "content": "gym at 6pm",
                              "kind": "statement"}]}
        embed = {"entities": [{"name": "gym", "kind": "core:place"}],
                 "points": [{"content": "gym at 5pm", "pointKind": "statement",
                             "about_entities": ["gym"]}],
                 "retractions": [{"content": "gym at 6pm"}]}
        result = v2.execute_embed(embed, search, session_id="s1")
        payload = result["payload"]
        for key in ("noops", "deletions", "retractions"):
            assert key not in payload
        from tortoise.commit_schema import validate_payload_dict
        l1, _model = validate_payload_dict(payload)
        assert l1.ok, l1.errors


class TestEntityResolution:
    """D3 — two-phase entity resolution (deterministic first, bounded LLM
    fallback; degrade-to-ADD; never blocks)."""

    def test_phase1_exact_no_model_call(self):
        search = {"entities": [{"id": "obj1", "name": "Joseph",
                                "kind": "core:person"}],
                  "points": [], "events": []}

        class Spy:
            def complete(self, **kw):
                raise AssertionError("phase-1 exact match must not call the model")

        res = v2.resolve_entities([{"name": "Joseph", "kind": "core:person"}],
                                  search, model=Spy())
        assert res["map"]["Joseph"]["id"] == "obj1"
        assert res["records"][0]["mode"] == "exact"
        assert not res["warnings"]

    def test_phase2_alias_rewrites_embed_list(self):
        search = {"entities": [{"id": "obj1", "name": "Joseph",
                                "kind": "core:person"}],
                  "points": [], "events": []}

        class FakeModel:
            def complete(self, *, system, user, max_tokens=None):
                assert "Joseph" in user and "Joe" in user  # candidates pinned
                assert "resolutions" in user               # JSON contract pinned
                return json.dumps({"resolutions": [
                    {"name": "Joe", "resolves_to": "Joseph"}]})

        res = v2.resolve_entities([{"name": "Joe", "kind": "core:person"}],
                                  search, model=FakeModel())
        assert res["map"]["Joe"]["name"] == "Joseph"
        assert res["records"][0]["mode"] == "llm"
        embed = {"entities": [{"name": "Joe", "kind": "core:person"}],
                 "points": [{"content": "Joe runs daily",
                             "about_entities": ["Joe"]}]}
        out = v2._apply_entity_resolution(embed, res["map"])
        assert out["entities"][0]["name"] == "Joseph"
        assert out["points"][0]["about_entities"] == ["Joseph"]
        # the raw list is untouched (deep copy — provenance preserved)
        assert embed["entities"][0]["name"] == "Joe"

    def test_phase2_bare_form_resolves_without_llm(self):
        """Phase-1 bare-form fallback fires when unambiguous — no model call."""
        search = {"entities": [{"id": "obj1", "name": "Joseph",
                                "kind": "core:person"}],
                  "points": [], "events": []}

        class Spy:
            def complete(self, **kw):
                raise AssertionError("bare-form match must not call the model")

        res = v2.resolve_entities(
            [{"name": "Joseph", "kind": "person"}],   # bare kind
            search, model=Spy())
        assert res["map"]["Joseph"]["id"] == "obj1"
        assert res["records"][0]["mode"] == "bare"

    def test_invalid_resolution_dropped_with_warning(self):
        search = {"entities": [{"id": "obj1", "name": "Joseph",
                                "kind": "core:person"}],
                  "points": [], "events": []}

        class FakeModel:
            def complete(self, **kw):
                return json.dumps({"resolutions": [
                    {"name": "Joe", "resolves_to": "NotAnEntity"}]})

        res = v2.resolve_entities([{"name": "Joe", "kind": "core:person"}],
                                  search, model=FakeModel())
        assert "Joe" not in res["map"]
        assert any("does not match an existing entity" in w
                   for w in res["warnings"])

    def test_model_failure_degrades_to_phase1_never_raises(self):
        search = {"entities": [{"id": "obj1", "name": "Joseph",
                                "kind": "core:person"}],
                  "points": [], "events": []}

        class Boom:
            def complete(self, **kw):
                raise TimeoutError("wedged resolver call")

        res = v2.resolve_entities(
            [{"name": "Joseph", "kind": "core:person"},
             {"name": "Joe", "kind": "core:person"}],
            search, model=Boom())
        assert res["map"]["Joseph"]["id"] == "obj1"   # phase-1 survives
        assert "Joe" not in res["map"]                 # unresolved → ADD
        assert any("failed" in w for w in res["warnings"])

    def test_no_candidates_skips_phase2(self):
        """Phase-2 gating: no real-candidate search → resolver never fires."""
        search = {"entities": [], "points": [], "events": []}

        class Boom:
            def complete(self, **kw):
                raise AssertionError("phase 2 must not fire with no candidates")

        res = v2.resolve_entities([{"name": "Joe", "kind": "core:person"}],
                                  search, model=Boom())
        assert res["map"] == {}
        assert not res["warnings"]

    def test_prompt_pins_candidates_and_contract(self):
        prompt = v2._resolution_prompt(
            [{"id": "o1", "name": "Joseph", "kind": "core:person"}], ["Joe"])
        assert "Joseph" in prompt and "o1" in prompt and "Joe" in prompt
        assert '"resolutions"' in prompt
        assert "resolves_to" in prompt


class TestResolutionOrchestration:
    """D3 in extract_session_v2 — between S4 and S5; rewrites the embed
    list; evidence in link_before_create + the result's resolution list;
    failure never blocks capture (P1)."""

    def _model(self, resolution_response=None):
        """Callable responder: S1 story + S2/S4 fixtures + optional phase-2
        resolution response."""
        embed = {"entities": [{"name": "Joe", "kind": "core:person"}],
                 "events": [], "operators": [],
                 "points": [{"content": "Joe runs every morning at 6am",
                             "pointKind": "statement",
                             "about_entities": ["Joe"],
                             "quote": "Joe runs every morning at 6am",
                             "search_keys": ["joe run"]}]}
        empty_gaps = {"entities": [], "events": [], "points": [],
                      "operators": [], "retractions": [],
                      "chain_notes": [], "link_before_create": []}

        def responder(system, user):
            if "STORY SUMMARIZER" in system:
                return "Joe runs every morning at 6am."
            if "GRAPH MAPPER" in system:
                return json.dumps(embed)
            if "GAP REVIEWER" in system:
                return json.dumps(empty_gaps)
            if "ENTITY RESOLUTION" in system:
                if callable(resolution_response):
                    return resolution_response(system, user)
                return json.dumps({"resolutions": [
                    {"name": "Joe", "resolves_to": "Joseph"}]})
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        class _M:
            def complete(self, *, system: str, user: str):
                return responder(system, user)

        return _M()

    def test_resolution_applied_between_s4_and_s5(self, monkeypatch):
        def fake_search(sdk, embed_list, story, **kw):
            return {"mode": "real", "degraded": False, "reason": None,
                    "entities": [{"id": "obj1", "name": "Joseph",
                                  "kind": "core:person"}],
                    "points": [], "events": [], "queries_run": 1}
        monkeypatch.setattr(v2, "search_graph", fake_search)
        conv = [{"role": "user", "content": "Joe runs every morning at 6am."}]
        out = v2.extract_session_v2(self._model(), conv, session_id="s1",
                                    session_date="2026-06-16")
        assert out["payload"] is not None
        names = [e["name"] for e in out["payload"]["entities"]]
        assert "Joseph" in names and "Joe" not in names
        assert out["resolution"] and out["resolution"][0]["mode"] == "llm"
        assert any("resolved via entity resolution" in n["note"]
                   for n in out["link_before_create"])

    def test_resolution_failure_never_blocks_capture(self, monkeypatch):
        def fake_search(sdk, embed_list, story, **kw):
            return {"mode": "real", "degraded": False, "reason": None,
                    "entities": [{"id": "obj1", "name": "Joseph",
                                  "kind": "core:person"}],
                    "points": [], "events": [], "queries_run": 1}
        monkeypatch.setattr(v2, "search_graph", fake_search)

        def boom(system, user):
            raise RuntimeError("resolver backend down")

        conv = [{"role": "user", "content": "Joe runs every morning at 6am."}]
        out = v2.extract_session_v2(self._model(boom), conv, session_id="s1")
        # P1: the pipeline still returns a payload (degrade-to-ADD)
        assert out["payload"] is not None
        assert out["payload"]["points"], "capture must not be blocked"
        assert any("failed" in w for w in out["warnings"])

    def test_phase1_resolution_no_llm_call(self, monkeypatch):
        def fake_search(sdk, embed_list, story, **kw):
            return {"mode": "real", "degraded": False, "reason": None,
                    "entities": [{"id": "obj1", "name": "Joe",
                                  "kind": "core:person"}],
                    "points": [], "events": [], "queries_run": 1}
        monkeypatch.setattr(v2, "search_graph", fake_search)
        conv = [{"role": "user", "content": "Joe runs every morning at 6am."}]
        out = v2.extract_session_v2(self._model(), conv, session_id="s1")
        assert out["payload"] is not None
        assert out["resolution"][0]["mode"] == "exact"


class TestS3BatchEnrichment:
    """D7 — _enrich_point_priors: ONE batched Cypher; row shape; guards."""

    def test_enrichment_merges_about_entities_in_one_query(self, tmp_path):
        from types import SimpleNamespace

        from tortoise.sdk import TortoiseSDK
        db = os.path.join(tmp_path, "s3.db")
        sdk = TortoiseSDK(db)
        try:
            sdk.create_entity("object", "gym", objectKind="core:place")
            sdk.create_point("statement", "gym at 6pm", id="pt1",
                             status="draft")
            sdk._get_proj().g.query(
                "MATCH (p:Point {id:$pid}), (o:Object {name:$name}) "
                "MERGE (p)-[:aboutObject]->(o)",
                params={"pid": "pt1", "name": "gym"})
            # GuardedGraph.query is read-only — spy via a projection proxy.
            inner = sdk._get_proj()
            counts = {"enrich": 0}
            def spy_query(cypher, params=None, **kw):
                if "aboutObject" in cypher:
                    counts["enrich"] += 1
                return inner.g.query(cypher, params=params, **kw)
            sdk._get_proj = lambda: SimpleNamespace(
                g=SimpleNamespace(query=spy_query))
            pts = [{"id": "pt1", "content": "gym at 6pm",
                    "kind": "statement"}]
            v2._enrich_point_priors(sdk, pts)
            assert counts["enrich"] == 1, \
                "enrichment must be ONE batched query"
            assert pts[0]["about_entities"] == ["gym"]
        finally:
            sdk.close()

    def test_enrichment_without_projection_is_noop(self):
        class NoProjection:
            pass

        pts = [{"id": "x", "content": "c", "kind": "statement"}]
        v2._enrich_point_priors(NoProjection(), pts)  # must not raise
        assert "about_entities" not in pts[0]

    def test_enrichment_empty_points_noop(self):
        class NoProjection:
            pass

        v2._enrich_point_priors(NoProjection(), [])  # must not raise

    def test_search_graph_keeps_degraded_shape_without_enrichment(
            self, monkeypatch):
        """A MockSDK without a projection still degrades gracefully (the
        existing search_graph contract — enrichment is additive)."""
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@localhost:6379/g")

        class MockSDK:
            def tortoise_fts_query(self, query, *, entity_type, limit=3):
                return [{"id": "pt-1", "content": "flash is the path",
                         "kind": "statement"}]

        res = v2.search_graph(MockSDK(), {"points": []}, "story")
        assert res["degraded"] is False
        assert res["points"][0]["id"] == "pt-1"
        assert "about_entities" not in res["points"][0]  # no enrichment needed
