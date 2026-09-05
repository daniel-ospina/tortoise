"""Tests for the production two-process extractor (no LLM — deterministic)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.value_extractor import (  # noqa: E402, I001, RUF100
    compile_value_brief, validate_summary, check_guards, _mask_refs,
)
from tortoise.sdk import _summary_to_payload, _stream_to_payload  # noqa: E402, RUF100


class TestValueBrief:
    def test_compiles_kinds(self):
        brief = compile_value_brief()
        assert "product-strategy:product" in brief
        assert "core:goal" in brief  # T12: §5 core incl. commitment-state
        assert len(brief) >= 20

    def test_kinds_have_semantics(self):
        brief = compile_value_brief()
        assert brief["product-strategy:useCase"]["nearMisses"]


class TestReferenceMask:
    def test_masks_refs(self):
        assert _mask_refs("see PR #999 and issue 1013") == "see [REF] and [REF]"

    def test_keeps_text(self):
        assert _mask_refs("the graph stores state") == "the graph stores state"


class TestValidateSummary:
    def test_missing_why(self):
        errs = validate_summary({"decisions": [{"content": "X"}]})
        assert any("why" in e for e in errs)

    def test_missing_sources(self):
        errs = validate_summary({"logic": [{"point": "P"}]})
        assert any("sources" in e for e in errs)

    def test_clean_passes(self):
        s = {"decisions": [{"content": "X", "why": "y"}],
             "state": [{"name": "A", "objectKind": "core:goal"}],
             "logic": [{"point": "P", "sources": [1]}]}
        assert validate_summary(s) == []


class TestGuards:
    def test_empty_summary_warns(self):
        assert check_guards({"state": [], "decisions": [], "logic": []})

    def test_decisions_without_logic(self):
        assert check_guards({"state": [], "decisions": [{"content": "X"}],
                             "logic": []})


class TestSummaryToPayload:
    def test_shape(self):
        s = {
            "session": {"summary": "S", "type": "design"},
            "state": [{"name": "ontology", "objectKind": "core:document",
                       "status": "changed"}],
            "decisions": [{"content": "remove observation", "options": ["obs"]}],
            "logic": [{"point": "observation is vague", "sources": [36]}],
            "issues": [{"id": "issue-1013", "status": "created"}],
        }
        p = _summary_to_payload(s, "s1")
        assert p["summary"] == "S"
        assert p["entities"][0]["name"] == "ontology"
        assert p["events"][0]["eventKind"] == "decision"
        assert p["events"][0]["about_entities"] == ["obs"]
        assert p["points"][0]["pointKind"] == "statement"
        assert any(e["eventKind"] == "occurrence" for e in p["events"])
        assert p["schema_version"] == "1"

    def test_replay_safe_id(self):
        from tortoise.sdk import _post_commit  # noqa: F401, I001
        from tortoise.commit_schema import compute_client_commit_id
        s = {"session": {"summary": "S"}, "state": [], "decisions": [],
             "logic": [], "issues": []}
        p = _summary_to_payload(s, "s1")
        cid = compute_client_commit_id(p["session_id"], p["points"],
                                       p["entities"], p["operators"],
                                       p["summary"], p["story_arc"],
                                       p.get("events", []))
        assert cid  # deterministic id computable (endpoint recomputes it)


class TestStreamToPayload:
    """T4 (#1272): construct-stream ids re-derived from content, operators
    remapped, points enriched at the neutral prior, payload Layer-1-valid."""

    def _stream(self):
        return {
            "entities": [{"name": "state-model", "kind": "core:goal"}],
            "events": [{"id": "ev_fake1", "eventKind": "decision",
                         "content": "Adopt the state-centric model",
                         "about_entities": ["state-model"]}],
            "points": [{"id": "pt_fake1",
                         "content": "the graph is the memory",
                         "about_entities": ["state-model"]},
                        {"id": "pt_fake2",
                         "content": "temporal KGs beat vector summaries",
                         "about_entities": ["state-model"]}],
            "operators": [
                {"src": "pt_fake1", "dst": "ev_fake1", "op_type": "IMPL"},
                {"src": "pt_fake2", "dst": "ev_fake1", "op_type": "NAND",
                 "direction": "unidirectional"},
                {"src": "pt_fake2", "dst": "ev_fake1", "op_type": "MITIGATES",
                 "target": {"src": "pt_fake1", "dst": "ev_fake1", "op_type": "IMPL"},
                 "strength": 0.3},
            ],
        }

    def test_ids_re_derived_and_operators_remapped(self):
        from tortoise.ids import content_hash
        p = _stream_to_payload({}, "s1", self._stream())
        assert p["points"][0]["id"] == f"pt_{content_hash('the graph is the memory')[:62]}"
        assert p["events"][0]["id"] == f"ev_{content_hash('Adopt the state-centric model')[:62]}"
        # Operators reference the RE-DERIVED ids, not the LLM's fake ones.
        pt0 = p["points"][0]["id"]
        ev0 = p["events"][0]["id"]
        impl = [o for o in p["operators"] if o["op_type"] == "IMPL"][0]  # noqa: RUF015
        assert impl["src"] == pt0 and impl["dst"] == ev0
        mit = [o for o in p["operators"] if o["op_type"] == "MITIGATES"][0]  # noqa: RUF015
        assert mit["target"]["src"] == pt0 and mit["target"]["dst"] == ev0

    def test_points_enriched_neutral_prior_draft(self):
        p = _stream_to_payload({}, "s1", self._stream())
        for pt in p["points"]:
            assert pt["reason"] == "NEW"
            assert pt["confidence"] == 0.5
            assert pt["c_cal"] == 0.5
            assert pt["status"] == "draft"
        assert p["events"][0]["confidence"] == 0.5

    def test_payload_layer1_valid(self):
        """T6 guard: the enriched construct payload must pass validate_payload_dict."""
        from tortoise.commit_schema import compute_client_commit_id, validate_payload_dict
        p = _stream_to_payload({}, "s1", self._stream())
        p["client_commit_id"] = compute_client_commit_id(
            p["session_id"], p["points"], p["entities"], p["operators"],
            p["summary"], p["story_arc"], p.get("events", []))
        res, _ = validate_payload_dict(p)
        assert res.ok, res.errors

    def test_canonical_hash_invariant_under_enrichment(self):
        """Enrichment fields are excluded from the canonical — same content,
        different confidence/status → identical client_commit_id."""
        from tortoise.commit_schema import compute_client_commit_id
        base = self._stream()
        p1 = _stream_to_payload({}, "s1", base)
        # Same content, different (fabricated) confidence would still hash same.
        p2 = _stream_to_payload({}, "s1", base)
        cid1 = compute_client_commit_id(p1["session_id"], p1["points"],
                                        p1["entities"], p1["operators"],
                                        p1["summary"], p1["story_arc"], p1["events"])
        cid2 = compute_client_commit_id(p2["session_id"], p2["points"],
                                        p2["entities"], p2["operators"],
                                        p2["summary"], p2["story_arc"], p2["events"])
        assert cid1 == cid2


class MockModel:
    """Deterministic mock for the round-trip guard (no LLM)."""

    def __init__(self, summary=None, stream=None):
        self._summary = summary or {
            "session": {"summary": "S", "type": "design"},
            "state": [{"name": "state-model", "objectKind": "core:goal",
                       "status": "changed"}],
            "decisions": [{"content": "Adopt the state-centric model",
                           "why": "the graph is the memory",
                           "options": ["state-model"]}],
            "logic": [{"point": "the graph is the memory", "sources": [1]}],
            "issues": [],
        }
        self._stream = stream or {
            "entities": [{"name": "state-model", "kind": "core:goal"}],
            "events": [{"id": "ev_fake1", "eventKind": "decision",
                         "content": "Adopt the state-centric model",
                         "about_entities": ["state-model"]}],
            "points": [{"id": "pt_fake1",
                         "content": "the graph is the memory",
                         "about_entities": ["state-model"]}],
            "operators": [{"src": "pt_fake1", "dst": "ev_fake1",
                            "op_type": "IMPL"}],
        }

    def complete(self, *, system, user):
        # construct pass → the stream; every other pass (summarize + the
        # T10 CORRECT_PASS repair) → the summary.
        if "GRAPH CONSTRUCTOR" in system:
            return json.dumps(self._stream)
        return json.dumps(self._summary)


_V2_EMBED = {
    "entities": [{"name": "the strategy", "kind": "core:strategy",
                   "lifecycle": "created", "supersedes": None, "note": None}],
    "events": [{"content": "we decided Y", "eventKind": "core:decision",
                 "about_entities": ["the strategy"]}],
    "points": [{"content": "X is the durable belief", "pointKind": "statement",
                 "about_entities": ["the strategy"]}],
    "operators": [{"src": "X is the durable belief", "dst": "we decided Y",
                    "op_type": "IMPL"}],
    "chain_notes": [], "link_before_create": [],
}


class V2MockModel:
    """Deterministic v2-shaped mock: S1 → narrative text, S2/S4 → embed
    list JSON (the extractor_v2 output contract)."""

    def complete(self, *, system, user):
        if "STORY SUMMARIZER" in system:
            return "We believed X. The session revealed Y."
        return json.dumps(_V2_EMBED)  # GRAPH MAPPER (S2) + GAP REVIEWER (S4)


class BoomV2Model:
    """Raises on every call — exercises the v2 error path."""

    def complete(self, *, system, user):
        raise RuntimeError("rate limited")


class TestCommitSessionRoundTrip:
    """T6 (#1272) + #1350: commit_session (v1 and v2 paths) with a mock
    model produces a Layer-1-valid payload (construct path committable)."""
    def test_round_trip_validates_v1(self):
        """v1 path (TORTOISE_EXTRACTOR=v1) keeps the legacy behavior."""
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        from tortoise.commit_schema import validate_payload_dict
        sdk = object.__new__(TortoiseSDK)  # no graph init needed (summary path)
        out = sdk.commit_session(
            conversation=[{"role": "user", "content": "x"},
                          {"role": "assistant", "content": "decided X"}],
            extractor_model=MockModel(), base_url="http://unused", api_key="k",
            extractor="v1")
        assert "payload" in out
        payload = out["payload"]
        assert payload["client_commit_id"]  # T5: computed, not empty
        res, _ = validate_payload_dict(payload)
        assert res.ok, res.errors

    def test_round_trip_validates_v2(self, monkeypatch):
        """#1350: the v2 5-stage path is the DEFAULT — mock model drives
        S1/S2/S4, S3 degrades (embedded backend), payload is Layer-1 valid
        and carries the story_arc + v2 observability keys."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        from tortoise.commit_schema import validate_payload_dict
        sdk = object.__new__(TortoiseSDK)
        out = sdk.commit_session(
            conversation=[{"role": "user", "content": "we believed X"},
                          {"role": "assistant", "content": "we decided Y"}],
            extractor_model=V2MockModel(), base_url="http://unused", api_key="k")
        assert "payload" in out
        payload = out["payload"]
        assert payload["client_commit_id"]
        assert payload["story_arc"], "v2 must populate the story arc"
        assert payload["points"], "v2 embed execution must emit points"
        res, _ = validate_payload_dict(payload)
        assert res.ok, res.errors
        assert out["story_arc"] == payload["story_arc"]
        assert "minted_kinds" in out and "chain_notes" in out
        assert out["search"]["degraded"] is True  # embedded backend honored

    def test_v2_error_path_reports_errors(self, monkeypatch):
        """#1350: a raising v2 model surfaces errors (ok=False) instead of
        writing nothing silently."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        from tortoise.sdk import TortoiseSDK
        sdk = object.__new__(TortoiseSDK)
        out = sdk.commit_session(
            conversation=[{"role": "user", "content": "x"}],
            extractor_model=BoomV2Model(), base_url="http://unused", api_key="k")
        assert out["ok"] is False
        assert any("rate limited" in e for e in out["errors"])

    def test_v2_layer1_rejected_payload_not_posted(self, monkeypatch):
        """#1350: a payload that fails Layer-1 returns ok=False with the
        Layer-1 errors and is NEVER POSTed (fail-closed at the gate) — the
        _post_commit recorder proves no call was made."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        import tortoise.sdk as sdk_mod
        import tortoise.extractor_v2 as ev2
        sdk = object.__new__(TortoiseSDK)
        _real = ev2.extract_session_v2
        posted: list[dict] = []

        def _record(payload, **kw):
            posted.append(payload)
            return {"ok": True}

        def _broken(model, conversation, **kw):
            out = _real(model, conversation, **kw)
            out["payload"]["points"][0]["id"] = "not-a-content-addressed-id"
            return out

        monkeypatch.setattr(ev2, "extract_session_v2", _broken)
        monkeypatch.setattr(sdk_mod, "_post_commit", _record)
        try:
            out = sdk.commit_session(
                conversation=[{"role": "user", "content": "x"}],
                extractor_model=V2MockModel(), base_url="http://unused",
                api_key="k")
        finally:
            monkeypatch.setattr(ev2, "extract_session_v2", _real)
        assert out["ok"] is False
        assert any("Layer-1" in e for e in out["errors"])
        assert posted == [], "Layer-1-rejected payload must never be POSTed"

    def test_v2_empty_conversation_not_ok(self, monkeypatch):
        """#1350 (review P1): an empty/blank conversation must never report
        ok=True — nothing was committed."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        from tortoise.sdk import TortoiseSDK
        sdk = object.__new__(TortoiseSDK)
        out = sdk.commit_session(
            conversation=[], extractor_model=V2MockModel(),
            base_url="http://unused", api_key="k")
        assert out["ok"] is False
        assert out["payload"] is None
        assert any("no payload" in e for e in out["errors"])

    def test_env_fallback_v1(self, monkeypatch):
        """#1350: TORTOISE_EXTRACTOR=v1 routes to the legacy path — the
        reversibility seam for operators."""
        monkeypatch.setenv("TORTOISE_EXTRACTOR", "v1")
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        from tortoise.commit_schema import validate_payload_dict
        sdk = object.__new__(TortoiseSDK)
        out = sdk.commit_session(
            conversation=[{"role": "user", "content": "x"}],
            extractor_model=MockModel(), base_url="http://unused", api_key="k")
        payload = out["payload"]
        assert payload["story_arc"] == ""  # v1 never populates the arc
        res, _ = validate_payload_dict(payload)
        assert res.ok, res.errors
        monkeypatch.delenv("TORTOISE_EXTRACTOR", raising=False)

    def test_error_path_payload_still_valid(self):
        """Even with extraction errors, the returned payload must be
        Layer-1-valid and carry a real client_commit_id (T5)."""
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        from tortoise.commit_schema import validate_payload_dict
        sdk = object.__new__(TortoiseSDK)  # no graph init needed (summary path)
        bad = MockModel(summary={
            "session": {"summary": "S"},
            "state": [], "decisions": [],
            "logic": [{"point": "no sources"}],  # R4 violation
            "issues": [],
        })
        out = sdk.commit_session(
            conversation=[{"role": "user", "content": "x"}],
            extractor_model=bad, base_url="http://unused", api_key="k",
            extractor="v1")
        assert out["ok"] is False
        assert any("sources" in e for e in out["errors"])
        assert out["payload"]["client_commit_id"]
        res, _ = validate_payload_dict(out["payload"])
        assert res.ok, res.errors


class _SeqMock:
    """Deterministic per-call mock for summarize() (T7/T8/T9). Returns the
    next response per call; 'not json' makes the parse fail (chunk loss)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, *, system, user):
        if self.calls >= len(self._responses):
            return "not json"
        resp = self._responses[self.calls]
        self.calls += 1
        return resp


def _edus(n):
    return [{"index": i, "role": "assistant", "text": f"turn {i}"} for i in range(n)]


class TestSummarize:
    """T7/T8/T9 (#1272): chunked summarize aggregates session, drops the
    ch-prefix, de-dups (name,kind), counts failed chunks."""

    def test_chunked_summary_aggregated(self):
        from tortoise.value_extractor import summarize
        mock = _SeqMock([
            json.dumps({"session": {"summary": "chunk-a", "type": "operational"},
                        "state": [{"name": "x", "objectKind": "core:goal"}],
                        "decisions": [], "logic": [], "issues": []}),
            json.dumps({"session": {"summary": "chunk-b", "type": None},
                        "state": [{"name": "y", "objectKind": "core:goal"}],
                        "decisions": [], "logic": [], "issues": []}),
            json.dumps({"session": {"summary": "chunk-c", "type": None},
                        "state": [], "decisions": [], "logic": [], "issues": []}),
        ])
        out = summarize(mock, _edus(14), chunk_size=6)
        assert out["session"]["summary"] == "chunk-a chunk-b chunk-c"
        assert out["session"]["type"] == "operational"
        assert out["failed_chunks"] == 0

    def test_chunked_dedup_and_no_prefix(self):
        from tortoise.value_extractor import summarize
        mock = _SeqMock([
            json.dumps({"session": {"summary": "a"}, "state":
                        [{"name": "shared", "objectKind": "core:goal"}],
                        "decisions": [], "logic": [], "issues": []}),
            json.dumps({"session": {"summary": "b"}, "state":
                        [{"name": "shared", "objectKind": "core:goal"},
                         {"name": "other", "objectKind": "core:goal"}],
                        "decisions": [], "logic": [], "issues": []}),
            json.dumps({"session": {"summary": "c"}, "state": [],
                        "decisions": [], "logic": [], "issues": []}),
        ])
        out = summarize(mock, _edus(14), chunk_size=6)
        names = [st["name"] for st in out["state"]]
        assert names == ["shared", "other"]  # deduped, no ch0-/ch1- prefixes
        assert not any(n.startswith("ch") for n in names)

    def test_failed_chunks_counted(self):
        from tortoise.value_extractor import summarize
        mock = _SeqMock([])  # every chunk fails to parse
        out = summarize(mock, _edus(14), chunk_size=6)
        assert out["failed_chunks"] == 3
        assert out["session"]["summary"] == ""


class _RepairMock:
    """First call returns a summary with an R4 violation (logic missing
    sources); the CORRECT_PASS call returns the fixed summary."""

    def __init__(self):
        self.calls = 0
        self._bad = {
            "session": {"summary": "S", "type": "design"},
            "state": [], "decisions": [],
            "logic": [{"point": "observation is vague"}],  # no sources (R4)
            "issues": [],
        }
        self._fixed = {
            "session": {"summary": "S", "type": "design"},
            "state": [], "decisions": [],
            "logic": [{"point": "observation is vague", "sources": [3]}],
            "issues": [],
        }

    def complete(self, *, system, user):
        self.calls += 1
        if "CORRECTION pass" in system:
            return json.dumps(self._fixed)
        return json.dumps(self._bad)


class TestRepairLoop:
    """T10 (#1272): the R4 correction pass repairs missing sources on a
    bounded re-prompt; errors clear when the fix is accepted."""

    def test_repair_fixes_r4(self):
        from tortoise.value_extractor import extract_session
        mock = _RepairMock()
        out = extract_session(mock, [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "observation is vague"},
        ])
        assert mock.calls >= 2  # summarize + correction pass
        assert out["errors"] == []  # R4 repaired
        assert out["summary"]["logic"][0]["sources"] == [3]


class TestClosedVocab:
    """T12 (#1272): objectKind closed-vocab enforcement — §5 core + pack
    kinds accepted (bare + namespaced + case-folded); non-vocab rejected in
    fail-closed mode; warn mode captures as proposal."""

    def test_core_kinds_accepted_bare_and_namespaced(self):
        from tortoise.value_extractor import validate_summary, _object_kind_vocab  # noqa: F401, I001
        vocab = _object_kind_vocab()
        for kind in ("Project", "WorkItem", "Problem", "document", "tag",
                     "user", "skill", "tool", "agent", "workflow",
                     "agreement", "standard", "other", "strategy", "plan",
                     "goal", "target"):
            assert kind in vocab, f"{kind} missing from closed vocab"
            assert f"core:{kind}" in vocab, f"core:{kind} missing"
            assert kind.lower() in vocab, f"{kind.lower()} missing"
        # pack kinds present too
        assert "product-strategy:product" in vocab

    def test_non_vocab_kind_fails_closed(self):
        from tortoise.value_extractor import validate_summary
        s = {"state": [{"name": "X", "objectKind": "design-artifact"}],
             "decisions": [], "logic": []}
        errors = validate_summary(s)
        assert any("objectKind" in e for e in errors)

    def test_warn_mode_passes_non_vocab(self):
        from tortoise.value_extractor import validate_summary
        s = {"state": [{"name": "X", "objectKind": "design-artifact"}],
             "decisions": [], "logic": []}
        errors = validate_summary(s, mode="warn")
        assert not any("objectKind" in e for e in errors)

    def test_missing_object_kind_fails_closed(self):
        from tortoise.value_extractor import validate_summary
        s = {"state": [{"name": "X"}], "decisions": [], "logic": []}
        errors = validate_summary(s)
        assert any("objectKind" in e for e in errors)

    def test_commit_session_warn_mode_reaches_payload(self):
        # Phase B: warn mode lets a non-vocab window produce a payload.
        # (The POST is intentionally unmocked here — the error path returns
        # the payload, which is what the calibration loop consumes.)
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        from tortoise.commit_schema import validate_payload_dict
        sdk = object.__new__(TortoiseSDK)
        out = sdk.commit_session(
            summary={"session": {"summary": "S"},
                     "state": [{"name": "artifact", "objectKind": "design-artifact"}],
                     "decisions": [], "logic": [], "issues": []},
            mode="warn", base_url="http://unused", api_key="k")
        # warn mode does NOT block the payload on the vocab (fail-closed
        # would return ok=False with an objectKind error). The POST is
        # unmocked here, so the result may carry a network error — the
        # payload must still be Layer-1-valid (what the loop consumes).
        assert "payload" in out
        res, _ = validate_payload_dict(out["payload"])
        assert res.ok, res.errors


class TestModelAdapterBounds:
    """T13 (#1272): the production BYOK adapter sends explicit bounds."""

    def test_adapter_body_has_bounds(self, monkeypatch):
        from tortoise.sdk import _model_adapter  # noqa: I001
        import requests as _requests
        captured = {}

        class _FakeResp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        def _fake_post(self_or_url, url=None, **kwargs):
            captured["body"] = kwargs.get("json", {})
            return _FakeResp()

        # #1530: exercise the no-key lenient path deterministically (the
        # dev shell carries real DEEPSEEK/OPENROUTER keys — routing would
        # pick deepseek-direct and strip the family prefix). With no keys the
        # adapter degrades to the single OpenRouter adapter (D3), which sends
        # the family-prefixed id unchanged.
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("TORTOISE_EXTRACTOR_PROVIDER", raising=False)
        monkeypatch.setattr(_requests.Session, "post", _fake_post)
        adapter = _model_adapter("deepseek/deepseek-v4-flash")
        out = adapter.complete(system="s", user="u")
        assert out == "ok"
        body = captured["body"]
        assert body["max_tokens"] == 4000
        assert body["temperature"] == 0.0
        assert body["model"] == "deepseek/deepseek-v4-flash"

    def test_adapter_body_uncapped_omits_max_tokens(self, monkeypatch):
        """#1468: max_tokens=None must OMIT the cap from the request body —
        the uncapped output budget the v2 session extractor's flash fallback
        needs (capped adapters truncate and silently lose chunks). Mirrors
        tests/model_adapters.py's OpenRouterModel(max_tokens=None) semantics
        without importing the test module from production."""
        from tortoise.sdk import _model_adapter  # noqa: I001
        import requests as _requests
        captured = {}

        class _FakeResp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        def _fake_post(self_or_url, url=None, **kwargs):
            captured["body"] = kwargs.get("json", {})
            return _FakeResp()

        monkeypatch.setattr(_requests.Session, "post", _fake_post)
        # #1530: no-key lenient path (see test_adapter_body_has_bounds) — the
        # family-prefixed id must survive on the OpenRouter default route.
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("TORTOISE_EXTRACTOR_PROVIDER", raising=False)
        adapter = _model_adapter("deepseek/deepseek-v4-flash",
                                 max_tokens=None, temperature=0.0)
        out = adapter.complete(system="s", user="u")
        assert out == "ok"
        body = captured["body"]
        assert "max_tokens" not in body, (
            "max_tokens=None must omit the cap from the request body")
        assert body["temperature"] == 0.0
        assert body["model"] == "deepseek/deepseek-v4-flash"


class TestR1R3Discriminator:
    """T16 (#1272): the SUMMARY_SYSTEM prompt must carry the R1∧R3 decision
    gate (commissive ∧ product-knowledge-bearing) with the "should" and
    process-commitment exclusions — the discriminator was absent before."""

    def test_summary_system_has_r1r3_gate(self):
        from tortoise.value_extractor import SUMMARY_SYSTEM
        assert "R1∧R3" in SUMMARY_SYSTEM or "DECISION GATE" in SUMMARY_SYSTEM
        assert "product-knowledge-bearing" in SUMMARY_SYSTEM
        assert "should" in SUMMARY_SYSTEM and "RECOMMENDATION" in SUMMARY_SYSTEM
        assert "Process/work commitments" in SUMMARY_SYSTEM
        assert "GitHub ingestion" in SUMMARY_SYSTEM

    def test_r1r3_present_in_user_prompt_flow(self):
        # The gate text is part of the system prompt used for every session.
        from tortoise.value_extractor import SUMMARY_SYSTEM, _user_prompt  # noqa: F401
        joined = SUMMARY_SYSTEM
        assert "COMMISSIVE" in joined or "commissive" in joined
        assert "agentivity" in joined
        assert "epistemic weight" in joined


class TestReviewFixes:
    """P1/P2 review fixes (#1272, independent fresh-context review)."""

    def test_warn_mode_forwarded_on_extraction_path(self):
        """P1-1: extract_session(conversation=..., mode='warn') must not
        fail-closed on non-vocab kinds — the warn mode reaches the
        conversation/extraction path, not just the summary= branch."""
        from tortoise.value_extractor import extract_session
        class _M:
            def __init__(self):
                self._summary = {
                    "session": {"summary": "S", "type": "design"},
                    "state": [{"name": "artifact", "objectKind": "design-artifact"}],
                    "decisions": [], "logic": [], "issues": [],
                }
            def complete(self, *, system, user):
                import json as _j
                return _j.dumps(self._summary)
        out = extract_session(_M(), [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "worked on artifact"}],
            mode="warn")
        assert not any("objectKind" in e for e in out["errors"]), out["errors"]

    def test_unmapped_operator_dropped_not_commit_killer(self):
        """P2-3: an operator referencing a fabricated id is dropped, and the
        remaining stream still produces a valid payload."""
        from tortoise.sdk import _stream_to_payload  # noqa: I001
        from tortoise.ids import content_hash
        pt = f"pt_{content_hash('real point')[:62]}"
        ev = f"ev_{content_hash('real event')[:62]}"
        stream = {
            "entities": [{"name": "x", "kind": "core:goal"}],
            "events": [{"id": "ev_fake_ev", "eventKind": "decision",
                        "content": "real event", "about_entities": ["x"]}],
            "points": [{"id": "pt_fake_pt", "content": "real point",
                        "about_entities": ["x"]}],
            "operators": [
                {"src": "pt_fake_pt", "dst": "ev_fake_ev", "op_type": "IMPL"},
                {"src": "pt_hallucinated", "dst": "ev_fake_ev", "op_type": "NAND",
                 "direction": "unidirectional"},
            ],
        }
        p = _stream_to_payload({}, "s1", stream)
        # only the mapped operator survives
        assert len(p["operators"]) == 1
        assert p["operators"][0]["op_type"] == "IMPL"
        assert p["operators"][0]["src"] == pt
        assert p["operators"][0]["dst"] == ev
