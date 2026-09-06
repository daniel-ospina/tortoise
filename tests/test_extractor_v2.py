"""Tests for the v2 5-stage extractor pipeline (issue #1350).

Covers the verification checklist surfaces: chunker+compiler (unit),
S2 map-to-embed (unit, mock), S3 graph search (graceful degradation +
integration with the real backend, skip-if-unavailable), S5 embed execution
(dependency order, link-before-create, supersession, minted kinds),
and the extract_session_v2 orchestrator (mock model, no LLM). Chain
enforcement scenarios live in tests/test_chain_enforcer.py (issue #1695,
Task 1 — the deterministic validate_and_rewire superset; this module keeps
validate_chains' warn-only backstop unit surface).
"""
from __future__ import annotations

import json
import os  # noqa: F401
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100


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

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
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

    def test_master_list_user_personal_state_section(self):
        """E2 (#1534): the master list gains the user-personal-state vocabulary
        — personal bests, schedules, preferences — the operative Tier-A
        criterion. The VALUE is the fact; retain it verbatim."""
        m = v2.build_master_list()
        section = m["user_personal_state"]
        assert set(section) == {"personal_best", "schedule", "preference"}
        assert "27:12" in section["personal_best"]          # value = the fact
        assert ("verbatim" in section["schedule"].lower()
                or "verbatim" in section["preference"].lower())

    def test_vocabulary_not_a_kind(self):
        """E2 (D1/D2): the hint vocabulary NEVER becomes a kind —
        master_kind_forms iterates a fixed section tuple; no vocabulary
        entry may leak into the closed-kind set."""
        m = v2.build_master_list()
        forms = v2.master_kind_forms(m)
        for cat in m["user_personal_state"]:
            assert cat not in forms            # hint vocabulary never becomes a kind
            assert f"core:{cat}" not in forms

    def test_render_master_marks_vocab_as_hint_not_kind(self):
        """E2 (D2): the rendered master list marks the vocabulary as a
        classification hint, explicitly NOT kinds."""
        rendered = v2._render_master(v2.build_master_list())
        assert "USER-PERSONAL-STATE VOCABULARY" in rendered
        assert "NOT kinds" in rendered

    def test_granularity_carve_out_surfaces(self):
        """E2 (D3): the state-value carve-out surfaces in S1's
        memory_granularity slot AND in the S2/S4 rendered master list —
        state values are protected from the mechanics-token filter."""
        # S1's memory_granularity slot carries the carve-out
        assert "STATE-VALUE CARVE-OUT" in v2._granularity_text()
        assert "27:12" in v2._granularity_text()
        # and S2/S4's rendered master list carries it too
        assert "STATE-VALUE CARVE-OUT" in v2._render_master(v2.build_master_list())


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

    def test_no_false_dedup_of_token_subset(self):
        """Review fix: a shorter NEW sentence that is a token-subset of an
        earlier one must NOT be dropped (asymmetric-overlap false dedup)."""
        s1 = "We chose the cleaning tier for the extraction pipeline."
        s2 = "We chose the tier."
        compiled = v2.compile_stories([s1, s2])
        assert "We chose the tier." in compiled
        assert "We chose the cleaning tier for the extraction pipeline." in compiled

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
        # S2 retries once on parse failure (pilot #1549 fix) then raises.
        # #1746 (D3): the retry is ERROR-INFORMED — the attempt-2 user
        # message carries the parse-error block ("did not parse" + the
        # offending region), never a same-prompt echo.
        model = MockModel(["no json here", "no json here"])
        with pytest.raises(ValueError):
            v2.run_s2(model, "STORY")
        assert len(model.calls) == 2  # one parse-retry happened before raising
        user2 = model.calls[1][1]
        assert "did not parse" in user2
        assert "no json here" in user2  # the offending region rides along

    def test_parse_retry_recovers(self):
        """Parse-retry (pilot #1549 + #1746 D3): an unparseable first output
        is re-prompted ERROR-INFORMED and a valid second output succeeds."""
        model = MockModel(["not json", json.dumps(S2_FIXTURE)])
        out = v2.run_s2(model, "STORY")
        assert out  # recovered after the parse retry
        user2 = model.calls[1][1]
        assert "did not parse" in user2
        assert "not json" in user2  # the offending region rides along

    def test_prompt_contains_master_and_chains(self):
        model = MockModel([json.dumps(S2_FIXTURE)])
        v2.run_s2(model, "STORY")
        system = model.calls[0][0]
        assert "MASTER LIST" in system
        assert "productDelivery" in system
        assert "TRUTH vs WEIGHT" in system
        assert "LINK-BEFORE-CREATE" in system
        assert "STRICT EXCLUSION" in system

    def test_prompt_strip_dont_drop_operational(self):
        """Parity-tuning (issue #1350): S1/S2/S4 must capture durable
        operational lessons + strip-not-drop mechanics-bearing decisions."""
        assert "STRIP, DON'T DROP" in v2.S1_TMPL
        assert "OPERATIONAL KNOWLEDGE" in v2.S1_TMPL
        assert "STRIP, DON'T DROP" in v2.S2_TMPL
        assert "cause-effect" in v2.S2_TMPL
        assert "operational/process lessons" in v2.S4_TMPL

    def test_output_contract_has_tier_and_quote(self):
        """E2 (D4): the OUTPUT_CONTRACT points carry the tier marker
        ("A|B") and the verbatim quote field."""
        assert '"tier": "A|B"' in v2.OUTPUT_CONTRACT
        assert '"quote"' in v2.OUTPUT_CONTRACT

    def test_s2_prompt_value_filter_carve_out(self):
        """E2 (D3): S2's VALUE FILTER excludes PROCESS ARTIFACTS only —
        user-personal-state values are the FACT, not mechanics tokens."""
        assert "CARVE-OUT" in v2.S2_TMPL
        assert "not a mechanics token" in v2.S2_TMPL
        assert "27:12" in v2.S2_TMPL

    def test_s2_prompt_tier_a_classification_rule(self):
        """E2 (D1/D4): S2's Tier-A rule — statement kind, verbatim content
        + quote, hint-not-kind / no-per-tier-pipeline language."""
        assert 'tier:"A"' in v2.S2_TMPL
        assert "NOT a kind" in v2.S2_TMPL or "not a kind" in v2.S2_TMPL
        assert "no per-tier" in v2.S2_TMPL or "per-tier pipeline" in v2.S2_TMPL
        assert 'pointKind stays "statement"' in v2.S2_TMPL
        assert "verbatim" in v2.S2_TMPL

    def test_s4_prompt_carve_out_and_keep_tier_a(self):
        """E2 (D5): S4 applies the same carve-out and NEVER drops Tier-A
        state-value points from the S2 list (merge-not-replace at the
        prompt layer; E4's deterministic union supersedes this later)."""
        assert "CARVE-OUT" in v2.S4_TMPL
        assert "NEVER dropped" in v2.S4_TMPL or "never dropped" in v2.S4_TMPL
        assert "Tier-A" in v2.S4_TMPL

    def test_prompt_supersession_rules(self):
        """#1386: S2/S4 carry the supersession mapping rule + decision-event
        discipline (never fabricate) + recoup done-things as occurrences."""
        assert "SUPERSESSION (state objects/subjects" in v2.S2_TMPL
        assert '"supersedes" set to the superseded' in v2.S2_TMPL
        assert "never invent an" in v2.S2_TMPL
        assert "NEVER fabricate a decision event" in v2.S2_TMPL
        assert "RECOUP DONE-THINGS as occurrence events" in v2.S2_TMPL
        assert "SUPERSESSION (state objects/subjects" in v2.S4_TMPL
        assert "never fabricate one for" in v2.S4_TMPL

    def test_prompt_date_anchor_rules(self):
        """T3 (#1533): OUTPUT_CONTRACT gains the when/startedAt fields and
        S2/S4 render the D3 emission rules when a session date is present."""
        assert '"when"' in v2.OUTPUT_CONTRACT
        assert '"startedAt"' in v2.OUTPUT_CONTRACT
        assert "YYYY-MM-DD|null" in v2.OUTPUT_CONTRACT
        assert "{date_anchor}" in v2.S2_TMPL
        assert "{date_anchor}" in v2.S4_TMPL
        s2 = v2.render_s2_prompt(session_date="2026-08-01")
        assert "DATE ANCHOR" in s2
        assert "EVENT `startedAt`" in s2
        assert "POINT `when`" in s2
        assert "default to the session date" in s2
        s4 = v2.render_s4_prompt("STORY", {}, S2_FIXTURE,
                                 session_date="2026-08-01")
        assert "DATE ANCHOR" in s4
        assert "EVENT `startedAt`" in s4
        assert "POINT `when`" in s4

    def test_minted_kind_report(self):
        bad = json.loads(json.dumps(S2_FIXTURE))
        bad["entities"].append({"name": "worktree", "kind": "worktree",
                                "lifecycle": "created", "supersedes": None,
                                "note": None})
        minted = v2._minted_kind_report(bad)
        assert any("worktree" in m for m in minted)
        assert v2._minted_kind_report(S2_FIXTURE) == []


# ── S3 graph search ────────────────────────────────────────────────────────

class TestParseLadder:
    """#1746 (D4/D5): the parse-boundary recovery ladder — sanitize (H2
    control-char contamination) → bounded repair → schema-validated
    partial-accept (H3 truncation) → error-informed re-prompt; every
    recovery is a recorded event, failures keep their mechanism class."""

    def test_sanitize_rung_recovers_control_chars(self):
        """A raw newline INSIDE a string value (H2 output-side
        contamination) breaks json.loads; the string-aware sanitize escapes
        it → parses; the value round-trips (newline preserved); the
        recovery is recorded in stats["recovery"]["sanitize"] == 1."""
        contaminated = ('{"entities": [{"name": "gym' + chr(10) +
                        'maintenance", "kind": "core:plan", '
                        '"lifecycle": "created", "supersedes": null, '
                        '"note": null}], "events": [], "operators": [], '
                        '"points": []}')
        stats: dict = {}
        out = v2.run_s2(MockModel([contaminated]), "STORY", stats=stats)
        assert out["entities"][0]["name"] == "gym\nmaintenance"
        assert out["entities"][0]["kind"] == "core:plan"
        assert stats["recovery"]["sanitize"] == 1

    def test_sanitize_preserves_structural_whitespace(self):
        """Control chars BETWEEN tokens (pretty-printed structural
        whitespace) are untouched — rung 1 parses directly, no sanitize
        fires; recovery stays empty."""
        pretty = ('{\n  "entities": [],\n  "events": [],\n  '
                  '"operators": [],\n  "points": []\n}')
        stats: dict = {}
        out = v2.run_s2(MockModel([pretty]), "STORY", stats=stats)
        assert out == {"entities": [], "events": [], "operators": [],
                       "points": []}
        assert stats.get("recovery") in (None, {})

    def test_sanitize_insufficient_counts_contamination_gap(self):
        """Sanitize that alters but cannot fully repair (a contaminated
        string cut mid-item — no complete embed item boundary exists after
        it) records ``sanitize_insufficient`` and falls through to raise —
        never corrupting (the D5 schema gate backstops any mis-tracked
        scan). A length-finish model skips the deterministic retry (D3),
        keeping this to one call."""
        # #2134 migration (Task 0 Step 5): escalation fires BEFORE the ladder
        # on a pre-escalation `length`, so sanitize never runs on a length
        # input — the rung is driven with a `stop` contaminated input (the
        # ladder path; D3 error-informed re-prompt on stop → 2 calls).
        class _Bad:
            last_finish_reason = "stop"

            def __init__(self):
                self.calls = 0

            def complete(self, *, system, user, max_tokens=None):
                self.calls += 1
                self.last_finish_reason = "stop"
                return ('{"entities": [], "events": [], "operators": [], '
                        '"points": [{"content": "x' + chr(10) + 'y", '
                        '"pointKind": "statement"')

        stats: dict = {}
        with pytest.raises(ValueError):
            v2.run_s2(_Bad(), "STORY", stats=stats)
        assert stats["recovery"]["sanitize_insufficient"] >= 1

    def test_repair_rung_missing_comma(self):
        """A missing comma at a boundary join (`}"{`) is repaired by the
        bounded rule list → the FULL output is recovered (schema-validated),
        recovery["repair"] == 1."""
        missing = ('{"entities": [], "events": [], "operators": [], '
                   '"points": [{"content": "a", "pointKind": "statement"}'
                   '{"content": "b", "pointKind": "statement"}]}')
        stats: dict = {}
        out = v2.run_s2(MockModel([missing]), "STORY", stats=stats)
        assert [p["content"] for p in out["points"]] == ["a", "b"]
        assert stats["recovery"]["repair"] == 1

    def test_repair_rung_trailing_brace(self):
        """An unterminated object (missing top-level closers — H3
        truncation) is recovered by the bounded closer append →
        recovery["repair"] == 1; the output is schema-valid."""
        unterminated = ('{"entities": [], "events": [], "operators": [], '
                        '"points": [{"content": "a", '
                        '"pointKind": "statement"}]')
        stats: dict = {}
        out = v2.run_s2(MockModel([unterminated]), "STORY", stats=stats)
        assert [p["content"] for p in out["points"]] == ["a"]
        assert stats["recovery"]["repair"] == 1

    def test_repair_rule_string_aware_repairs_boundary_only(self):
        """#1780 (F5): a missing comma at a REAL boundary is repaired while
        an in-string occurrence of a rule pattern (`}{` inside a content
        value) is NEVER mutated — the string value round-trips unchanged
        (the old whole-text ``replace`` corrupted it to ``a},{b``)."""
        missing = ('{"entities": [], "events": [], "operators": [], '
                   '"points": [{"content": "a}{b", "pointKind": "statement"}'
                   '{"content": "c", "pointKind": "statement"}]}')
        stats: dict = {}
        out = v2.run_s2(MockModel([missing]), "STORY", stats=stats)
        assert [p["content"] for p in out["points"]] == ["a}{b", "c"]
        assert stats["recovery"]["repair"] == 1

    def test_schema_validator_accepts_contract_shape(self):
        """A valid embed-list shape passes; unknown keys, empty arrays and
        extra fields ride through (permissive-on-extras structural gate)."""
        ok, issues = v2._validate_output_shape({
            "entities": [{"name": "x", "kind": "y",
                           "lifecycle": "created"}],
            "events": [],
            "points": [{"content": "p", "pointKind": "statement"}],
            "operators": [{"src": "a", "dst": "b", "op_type": "IMPL"}],
            "chain_notes": [{"chain": "c", "finding": "f",
                              "action": "warned", "note": "n"}],
            "link_before_create": [{"searched_for": "s", "found": True}],
            "retractions": [{"id": "r1"}],  # content | id
            "unknown_section": [1, 2],
        })
        assert ok is True and issues == []

    def test_schema_validator_rejects_shape_mismatch(self):
        """A section as a dict (not a list), a missing required key, and a
        non-primitive value all fail the structural gate."""
        ok, issues = v2._validate_output_shape(
            {"points": {"content": "dict-not-list"}})
        assert ok is False
        assert any("points" in i for i in issues)

        ok2, issues2 = v2._validate_output_shape(
            {"entities": [{"name": "x"}]})  # missing kind
        assert ok2 is False
        assert any("kind" in i for i in issues2)

        ok3, _ = v2._validate_output_shape(
            {"points": [{"content": ["non-primitive"]}]})
        assert ok3 is False

        ok4, _ = v2._validate_output_shape("not-an-object")
        assert ok4 is False

        ok5, issues5 = v2._validate_output_shape(
            {"retractions": [{"note": "neither content nor id"}]})
        assert ok5 is False
        assert any("content or id" in i for i in issues5)

        ok6, issues6 = v2._validate_output_shape(
            {"link_before_create": [{"searched_for": "s", "found": "yes"}]})
        assert ok6 is False  # found is declared bool (per-key type gate)
        assert any("found" in i for i in issues6)

    def test_partial_accept_recovers_truncated_list(self):
        """D4 rung 4: an S4 output cut mid-points-item is recovered as the
        longest schema-valid prefix → ``partial_parse`` in census + error
        string; the partial list IS used (merged over the S2 base)."""
        from tests.test_extractor_reliability import _conv

        class _Model:
            last_finish_reason = None

            def __init__(self):
                self.calls = 0

            def complete(self, *, system, user, max_tokens=None):
                self.calls += 1
                if "STORY SUMMARIZER" in system:
                    self.last_finish_reason = "stop"
                    return "A narrative."
                if "GAP REVIEWER" in system:
                    # #2134: the BASE call truncates at 16K (length); the
                    # ESCALATED call (max_tokens > 16000) returns the SAME
                    # mid-`points`-item-2 cut but with finish=stop — the
                    # terminal ladder parse (escalated_partial) recovers
                    # item 1 as the longest valid prefix. No re-prompt fires
                    # post-escalation (R3-1).
                    self.last_finish_reason = ("length"
                                               if (max_tokens or 0) <= 16000
                                               else "stop")
                    return ('{"entities": [], "events": [], "operators": [], '
                            '"points": [{"content": "s4 point 1", '
                            '"pointKind": "statement"}, {"content": "s4 point 2"')
                # S2 (GRAPH MAPPER): the S2 base with one point.
                self.last_finish_reason = "stop"
                return ('{"entities": [], "events": [], "operators": [], '
                        '"points": [{"content": "s2 base", '
                        '"pointKind": "statement"}]}')

        out = v2.extract_session_v2(_Model(), _conv())
        # the honest classed partial path: escalation fired (length), the
        # escalated stop+cut response partial-accepted via the terminal
        # ladder → escalated_partial + partial_parse at the caller
        assert out["error_census"]["partial_parse"] == 1  # S4 partial
        assert any("partial" in e for e in out["errors"])
        # the partial list IS used — merged over the S2 base (never replaced)
        contents = [p["content"] for p in out["embed_list"]["points"]]
        assert "s2 base" in contents and "s4 point 1" in contents
        assert "s4 point 2" not in contents  # the truncated tail was dropped
        assert out["stats"]["llm"]["truncated"] == 1  # S4 truncated
        rec = out["stats"]["recovery"]
        assert rec["escalated"] == 1 and rec["escalated_partial"] == 1
        assert rec["escalated"] == (rec.get("escalated_recovered", 0)
                                    + rec.get("escalated_residual", 0)
                                    + rec.get("escalated_abort", 0)
                                    + rec["escalated_partial"])
        # the partial-accept never credits the repair rung (review-fix pin:
        # a data-dropping accept is partial_parse, never recovery["repair"]).
        assert rec.get("repair", 0) == 0

    def test_partial_accept_rejects_empty_prefix(self):
        """Truncation before any item (no complete embed item exists) →
        failure, never a partial — a truncated-to-empty prefix never counts.
        Uses a length-finish model so the deterministic retry-skip applies
        (one call only)."""
        class _Trunc:
            last_finish_reason = "length"

            def __init__(self):
                self.calls = 0

            def complete(self, *, system, user, max_tokens=None):
                self.calls += 1
                return ('{"entities": [], "events": [], "operators": [], '
                        '"points": [')

        model = _Trunc()
        with pytest.raises(ValueError):
            v2.run_s2(model, "STORY")
        # #2134: the length now escalates ONCE (32K), still length -> residual
        # fail-loud at exactly 2 calls (the empty-prefix head is still never
        # a partial)
        assert model.calls == 2
        assert model.last_finish_reason == "length"

    def test_truncated_skips_same_prompt_retry(self):
        """D3 (#1746): a first parse-failing attempt with finish_reason ==
        "length" SKIPS the same-prompt retry (deterministic failure) →
        exactly ONE call, census ``truncated_parse_error``."""
        class _Trunc:
            last_finish_reason = "length"

            def __init__(self):
                self.calls = 0

            def complete(self, *, system, user, max_tokens=None):
                self.calls += 1
                return "this is not JSON at all"

        model = _Trunc()
        stats: dict = {}
        with pytest.raises(ValueError):
            v2.run_s2(model, "STORY", stats=stats)
        # #2134: the one-shot escalation fires at attempt-1 (length -> esc
        # 32K), the escalated call is ALSO length -> residual fail-loud at
        # exactly 2 calls; the 4-bucket invariant holds (escalated==1,
        # escalated_residual==1)
        assert model.calls == 2
        assert stats["truncated"] is True
        rec = stats["recovery"]
        assert rec["escalated"] == 1
        assert rec["escalated_residual"] == 1
        assert rec["escalated"] == (rec.get("escalated_recovered", 0)
                                    + rec["escalated_residual"]
                                    + rec.get("escalated_abort", 0)
                                    + rec.get("escalated_partial", 0))

    def test_complete_records_prompt_and_completion_tokens_in_stats(self):
        """#2134 Task 0: ``_complete`` writes the per-call token counts into
        ``stats`` next to ``finish_reason`` (captured in the calling thread
        by ``_call_once`` — never the shared adapter attrs read post-hoc).
        A model that DOES NOT set the token attrs (all mocks lacking them)
        normalizes to 0 via the None-guard — never a TypeError."""
        class _WithTokens:
            last_finish_reason = "stop"
            def complete(self, *, system, user, max_tokens=None):
                self.last_prompt_tokens = 1234
                self.last_completion_tokens = 567
                return '{"ok": true}'

        stats: dict = {}
        out = v2._complete(_WithTokens(), "s", "u", max_tokens=16000,
                           stats=stats)
        assert out == '{"ok": true}'
        assert stats["prompt_tokens"] == 1234
        assert stats["completion_tokens"] == 567
        assert stats["finish_reason"] == "stop"
        assert stats["attempts"] == 1

        # None-attr arm (P2-36): a mock without the token attrs (the norm —
        # _Bad/_Trunc/CapAware backstops) contributes 0, never a TypeError.
        class _NoTokens:
            last_finish_reason = "stop"
            def complete(self, *, system, user, max_tokens=None):
                return '{"ok": true}'

        stats2: dict = {}
        v2._complete(_NoTokens(), "s", "u", max_tokens=16000, stats=stats2)
        assert stats2["prompt_tokens"] == 0
        assert stats2["completion_tokens"] == 0

    def test_complete_truncation_accumulates_recovery_tokens(self):
        """#2134 Task 0 (P1-22): a ``length``-truncated call accumulates its
        emitted token counts into the recovery-carried combined keys
        (``truncation_prompt_tokens``/``truncation_completion_tokens``) so
        the per-call overage rides the roll-up to the outcome — the
        lower-bound read surface Task 1 consumes."""
        class _TruncWithTokens:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                self.last_prompt_tokens = 200
                self.last_completion_tokens = 16000  # filled the 16K budget
                return '{"entities": []'

        stats: dict = {}
        v2._complete(_TruncWithTokens(), "s", "u", max_tokens=16000,
                     stats=stats)
        assert stats["truncated"] is True
        assert stats["recovery"]["truncation_prompt_tokens"] == 200
        assert stats["recovery"]["truncation_completion_tokens"] == 16000
        # a non-truncated call never accumulates truncation tokens
        class _StopNoTokens:
            last_finish_reason = "stop"
            def complete(self, *, system, user, max_tokens=None):
                return '{"ok": true}'

        stats2: dict = {}
        v2._complete(_StopNoTokens(), "s", "u", max_tokens=16000,
                     stats=stats2)
        assert "recovery" not in stats2  # no recovery dict is ever created
        # on a non-truncated call

    def test_complete_parsed_seam_accumulates_per_seam_tokens(self):
        """#2134 Task 0 (R3-6): ``_complete_parsed`` with ``seam="s2"``
        (run_s2's wiring) accumulates the truncating call's tokens into the
        PER-SEAM recovery keys in addition to the combined keys — keeping
        the S2 overage separable from S4 for the Task-1 calibration read.
        The kind_classifier path (seam=None) stays combined-only."""
        class _S2Trunc:
            last_finish_reason = "length"
            def __init__(self):
                self.calls = 0
            def complete(self, *, system, user, max_tokens=None):
                self.calls += 1
                self.last_prompt_tokens = 400
                self.last_completion_tokens = 16000
                # no recoverable prefix (mirrors the reject-empty-prefix
                # fixture) → _parse_json_robust raises; the seam accumulation
                # fired BEFORE the parse (right after finish is read).
                return ('{"entities": [], "events": [], "operators": [], '
                        '"points": [')

        model = _S2Trunc()
        stats: dict = {}
        with pytest.raises(ValueError):
            v2.run_s2(model, "STORY", stats=stats)  # seam="s2" wired in run_s2
        # #2134 post-Task-3: the length escalates ONCE (32K), still length →
        # residual fail-loud at exactly 2 calls; both truncating calls
        # (base 16K + escalated 32K — the mock emits 16000 tokens each) land
        # in the combined + per-seam s2 keys; s4 never fires.
        assert model.calls == 2
        rec = stats["recovery"]
        assert rec["truncation_completion_tokens"] == 32000
        assert rec["truncation_completion_tokens_s2"] == 32000
        assert "truncation_completion_tokens_s4" not in rec
        assert rec["escalated"] == 1 and rec["escalated_residual"] == 1
        assert rec["escalation_base_output_tokens"] == 16000

    def test_extractor_escalation_tokens_env_semantics(self, monkeypatch):
        """#2134 Task 2 (D2): the escalation knob mirrors ask_env_int —
        default 32000; a valid [16000..64000] value used AS-IS (never
        saturated); below/above/garbage falls back to the default."""
        monkeypatch.delenv("TORTOISE_EXTRACTOR_ESCALATION_TOKENS",
                           raising=False)
        assert v2._extractor_escalation_tokens(16000) == 32000
        for raw, expect in (("16000", 16000), ("64000", 64000),
                            ("32000", 32000)):
            monkeypatch.setenv("TORTOISE_EXTRACTOR_ESCALATION_TOKENS", raw)
            assert v2._extractor_escalation_tokens(16000) == expect
        # out-of-range / garbage → default (never clamped to a bound)
        for raw in ("15999", "64001", "0", "999999", "abc", "32.5", ""):
            monkeypatch.setenv("TORTOISE_EXTRACTOR_ESCALATION_TOKENS", raw)
            assert v2._extractor_escalation_tokens(16000) == 32000

    def test_extractor_escalation_tokens_warns_when_esc_le_base(self,
                                                                monkeypatch):
        """#2134 P2-6: a resolved escalation value <= the base cap warns —
        an un-escalatable truncation is RESIDUAL fail-loud, never a silent
        partial (P2-15)."""
        monkeypatch.setenv("TORTOISE_EXTRACTOR_ESCALATION_TOKENS", "16000")
        with pytest.warns(UserWarning):
            assert v2._extractor_escalation_tokens(16000) == 16000
        monkeypatch.setenv("TORTOISE_EXTRACTOR_ESCALATION_TOKENS", "32000")
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert v2._extractor_escalation_tokens(16000) == 32000

    def test_parse_canonical_strict_balanced(self):
        """#2134 Task 2 (D3): full balanced JSON returns the dict
        (fence-stripped); balanced complete-but-shorter JSON is ALSO
        returned — the CALLER classifies a length-truncated shorter dict,
        never this parser."""
        ok = v2._parse_canonical_strict(
            '{"entities": [{"name": "a"}]}')
        assert ok == {"entities": [{"name": "a"}]}
        fenced = v2._parse_canonical_strict(
            '```json\n{"points": [{"content": "x"}]}\n```')
        assert fenced == {"points": [{"content": "x"}]}
        # balanced complete-but-shorter (a section-boundary-shaped cut that
        # happens to be complete JSON) → the dict IS returned
        shorter = v2._parse_canonical_strict(
            '{"entities": [], "events": [], "operators": [], "points": []}')
        assert shorter == {"entities": [], "events": [], "operators": [],
                           "points": []}
        assert v2._parse_canonical_strict(None) is None

    def test_parse_canonical_strict_no_tail_cut_no_repair(self):
        """#2134 Task 2 (D3/P2-9): a truncated-mid-list response that rung-1
        (progressive tail-cut) WOULD reconstruct into a valid shorter dict
        returns None here — this parser never tail-cuts, never repairs, and
        an unterminated input is never accepted."""
        # mid-list cut (unterminated points array) — _parse_json recovers it
        # via tail-cut; _parse_canonical_strict must reject it
        resp = ('{"entities": [], "events": [], "operators": [], '
                '"points": [{"content": "p1", "pointKind": "statement"}, ')
        assert v2._parse_canonical_strict(resp) is None
        # garbage / no JSON block / unbalanced
        assert v2._parse_canonical_strict("not json at all") is None
        assert v2._parse_canonical_strict('{"entities": [') is None
        assert v2._parse_canonical_strict('{"entities": ]}') is None
        assert v2._parse_canonical_strict('') is None

    def test_parse_error_with_stop_still_retries(self):
        """D3 (#1746): a stop-class parse failure STILL gets the single
        error-informed re-prompt — only the truncation class skips."""
        model = MockModel(["no json here", "no json here"])
        with pytest.raises(ValueError):
            v2.run_s2(model, "STORY")
        assert len(model.calls) == 2


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

    def test_payload_carries_supersessions_and_commit_id_agrees(self):
        """E5 DD-3/DD-4: execute_embed emits payload["supersessions"] with
        point-level records (pt_ refs) and includes them in client_commit_id
        — site-1 (execute_embed) == site-2 (_post_commit recompute shape).
        Today execute_embed omits supersessions from the id → the hosted
        path 422s (commit_id_mismatch) on any payload with supersessions.
        """
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt_old", "content": "gym at 6pm",
                              "kind": "statement"}]}
        embed = {"points": [{"content": "gym at 5pm", "pointKind": "statement",
                             "about_entities": ["gym"]}]}
        result = v2.execute_embed(embed, search, session_id="s1")
        payload = result["payload"]
        assert payload["supersessions"] == result["supersessions"]
        from tortoise.commit_schema import compute_client_commit_id
        recomputed = compute_client_commit_id(
            payload["session_id"], payload["points"], payload["entities"],
            payload["operators"], payload["summary"], payload["story_arc"],
            payload.get("events", []), payload.get("supersessions", []))
        assert payload["client_commit_id"] == recomputed
        pts = [s for s in payload["supersessions"]
               if s["superseded"].startswith("pt_")]
        assert pts, "point-level supersession record must ride the payload"
        assert pts[0]["supersedes_by"].startswith("pt_")
        assert payload["points"][0]["reason"] == "REVISES"

    def test_client_commit_id_empty_supersessions_backward_compat(self):
        """pre-#1350 id stability: a payload with EMPTY supersessions keeps
        its pre-#1350 client_commit_id (canonical omits the empty key)."""
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt_same", "content": "gym at 6pm",
                              "kind": "statement"}]}
        embed = {"points": [{"content": "gym at 6pm", "pointKind": "statement",
                             "about_entities": []}]}
        result = v2.execute_embed(embed, search, session_id="s1")
        payload = result["payload"]
        from tortoise.commit_schema import compute_client_commit_id
        no_supersessions = compute_client_commit_id(
            payload["session_id"], payload["points"], payload["entities"],
            payload["operators"], payload["summary"], payload["story_arc"],
            payload.get("events", []))
        assert payload["client_commit_id"] == no_supersessions
        assert payload.get("supersessions") == []

    def test_point_supersession_revises(self):
        """E7 (D1): a later-session VALUE change for the same entity+attribute
        (length-guarded overlap >= REVISES_MIN_OVERLAP) is UPDATE — the
        payload point rides reason REVISES + a point-level supersession
        record (the E5 machinery, unchanged). A same-VALUE re-wording is
        NOOP (E2E-11 MECE boundary), never REVISES — covered in the
        consolidation tests."""
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt_old", "content": "gym at 6pm",
                              "kind": "statement"}]}
        embed = {"points": [{"content": "gym at 5pm",
                             "pointKind": "statement",
                             "about_entities": ["gym"]}]}
        result = v2.execute_embed(embed, search, session_id="s1")
        pts = result["payload"]["points"]
        assert pts[0]["reason"] == "REVISES"
        assert pts[0]["id"] != "pt_old"     # new content-addressed id
        ss = [s for s in result["supersessions"] if s["superseded"] == "pt_old"]
        assert ss, "point-level supersession record must ride the payload"
        assert result["stats"]["points"] == 1

    def test_exact_point_match_dedups(self):
        content = "single-flash with granularity is the working path"
        search = {"entities": [], "events": [],
                  "points": [{"id": "pt-same", "content": content,
                              "kind": "statement"}]}
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["points"] = [{"content": content, "pointKind": "statement",
                            "about_entities": []}]
        result = v2.execute_embed(embed, search, session_id="s1")
        # E7 (D1/D4): identical content → NOOP(identical) — NO payload point;
        # the prior id + session ref ride the RESULT-level noops record
        # (the E2E-11 MECE boundary: identical-value re-assertion → NOOP).
        assert len(result["noops"]) == 1
        noop = result["noops"][0]
        assert noop["point_id"] == "pt-same"
        assert noop["reason"] == "identical"
        assert noop["session_ref"] == "s1"
        assert all(p["content"] != content
                   for p in result["payload"]["points"])
        assert result["stats"]["noops"] == 1

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

    def test_events_default_to_session_date(self):
        """T5 (#1533): events in a dated session always get started_at —
        explicit startedAt preserved, else the session date; junk dropped
        with a warning; undated sessions write no started_at key."""
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["events"] = [
            {"content": "we decided X", "eventKind": "core:decision",
             "about_entities": []},
            {"content": "we decided Y", "eventKind": "core:decision",
             "about_entities": [], "startedAt": "2026-07-01"},
            {"content": "we decided Z", "eventKind": "core:decision",
             "about_entities": [], "startedAt": "soon"},
        ]
        dated = v2.execute_embed(embed, {}, session_id="s1",
                                 session_date="2026-08-01")
        evs = {e["content"]: e for e in dated["payload"]["events"]}
        assert evs["we decided X"]["started_at"] == "2026-08-01"
        assert evs["we decided Y"]["started_at"] == "2026-07-01"
        assert "started_at" not in evs["we decided Z"]
        assert any("not a valid ISO date" in w for w in dated["warnings"])
        # undated session: no default, explicit startedAt still honored
        undated = v2.execute_embed(embed, {}, session_id="s1")
        evs_u = {e["content"]: e for e in undated["payload"]["events"]}
        assert "started_at" not in evs_u["we decided X"]
        assert evs_u["we decided Y"]["started_at"] == "2026-07-01"
        assert "started_at" not in evs_u["we decided Z"]

    def test_points_carry_when(self):
        """T4 (#1533): points carry `when` ONLY when the model anchored a
        valid ISO date; junk dropped with a warning; timeless points get no
        key."""
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["points"] = [
            {"content": "we adopted the new strategy on 2026-08-01",
             "pointKind": "statement", "about_entities": [],
             "when": "2026-08-01"},
            {"content": "timeless durable belief", "pointKind": "statement",
             "about_entities": [], "when": None},
            {"content": "junk dated point", "pointKind": "statement",
             "about_entities": [], "when": "next tuesday"},
            {"content": "over-long dated point", "pointKind": "statement",
             "about_entities": [], "when": "2026-08-01T10:00:00.000000+05:30 (India Standard Time)"},
        ]
        result = v2.execute_embed(embed, {}, session_id="s1")
        pts = {p["content"]: p for p in result["payload"]["points"]}
        assert pts["we adopted the new strategy on 2026-08-01"]["when"] == "2026-08-01"
        assert "when" not in pts["timeless durable belief"]
        assert "when" not in pts["junk dated point"]
        # over-long ISO-prefixed values (> commit_schema max_length=40) are
        # dropped at the gate — they must NOT sink the whole payload at
        # Layer-1 (code-review fix)
        assert "when" not in pts["over-long dated point"]
        assert any("not a valid ISO date" in w for w in result["warnings"])
        # undated session → dated payload points still validated when anchored
        undated = v2.execute_embed(embed, {}, session_id="s1")
        pts_u = {p["content"]: p for p in undated["payload"]["points"]}
        assert pts_u["we adopted the new strategy on 2026-08-01"]["when"] == "2026-08-01"

    def test_mitigates_strength_clamped(self):
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["operators"][1]["strength"] = 0.9
        result = v2.execute_embed(embed, {}, session_id="s1")
        op = result["payload"]["operators"][1]
        assert op["strength"] == 0.5
        assert any("0.10, 0.50" in w for w in result["warnings"])

    def test_mitigates_non_numeric_strength_defaults(self):
        """Review fix: float('high') must not crash S5 (never-block)."""
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["operators"][1]["strength"] = "high"
        result = v2.execute_embed(embed, {}, session_id="s1")
        op = result["payload"]["operators"][1]
        assert op["strength"] == 0.3
        assert any("not numeric" in w for w in result["warnings"])

    def test_mitigates_before_target_impr_order_independent(self):
        """Review fix: MITIGATES may precede its target IMPL in the model
        output — two-pass processing must keep it."""
        embed = json.loads(json.dumps(S2_FIXTURE))
        imp = embed["operators"][0]
        mit = embed["operators"][1]
        embed["operators"] = [mit, imp]  # MITIGATES first, IMPL second
        result = v2.execute_embed(embed, {}, session_id="s1")
        types = [o["op_type"] for o in result["payload"]["operators"]]
        assert "MITIGATES" in types
        assert not any("target edge not emitted" in w for w in result["warnings"])

    def test_non_dict_entries_skipped(self):
        """Review fix: non-dict array entries must not crash S3/S5."""
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["entities"].append("not a dict")
        embed["points"].append(42)
        embed["events"].append(None)
        embed["operators"].append("bogus")
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert any("non-dict" in w for w in result["warnings"])
        # _derive_queries (S3) also survives
        queries = v2._derive_queries(embed, "story")
        assert sum(len(q) for q in queries.values()) >= 2

    def test_bare_kind_link_before_create_matches(self):
        """Review fix: model emits bare 'plan'; backend stores 'core:plan' —
        kind-form normalization must make link-before-create match."""
        search = {"entities": [{"id": "obj-9", "name": "cleaning-pass tier",
                                "kind": "core:plan"}], "points": [], "events": []}
        embed = json.loads(json.dumps(S2_FIXTURE))
        for e in embed["entities"]:
            if e["name"] == "cleaning-pass tier":
                e["kind"] = "plan"  # bare form
        result = v2.execute_embed(embed, search, session_id="s1")
        note = next(n for n in result["link_before_create"]
                    if "cleaning-pass tier" in n["searched_for"])
        assert note["found"] is True

    def test_ambiguous_bare_kind_warns_never_guesses(self):
        """Review fix: two namespaces share a bare kind ('workflow') — the
        bare-form fallback must NOT guess; it warns and creates with the
        explicit kind instead."""
        search = {"entities": [
            {"id": "obj-dev", "name": "code review", "kind": "dev:workflow"},
            {"id": "obj-ps", "name": "code review", "kind": "product-strategy:workflow"},
        ], "points": [], "events": []}
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["entities"] = [{"name": "code review", "kind": "workflow",
                               "lifecycle": "created", "supersedes": None,
                               "note": None}]
        result = v2.execute_embed(embed, search, session_id="s1")
        note = next(n for n in result["link_before_create"]
                    if "code review" in n["searched_for"])
        assert note["found"] is False
        assert any("ambiguous" in w for w in result["warnings"])

    def test_entity_lifecycle_changed_warns(self):
        """Review fix: changed/superseded lifecycle is not expressible in the
        Layer-1 payload (Entity = name/kind only) — warn, don't silently drop."""
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["entities"][0]["lifecycle"] = "superseded"
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert any("lifecycle=superseded" in w for w in result["warnings"])

    def test_supersession_recorded_from_s3(self):
        """#1386: a NEW entity carrying supersedes=<existing S3 id> is recorded
        deterministically in supersessions + link_before_create."""
        search = {"entities": [{"id": "obj-old", "name": "strategy-A",
                                "kind": "core:strategy"}], "points": [], "events": []}
        embed = {"entities": [
            {"name": "strategy-B", "kind": "core:strategy", "lifecycle": "created",
             "supersedes": "obj-old", "note": None}],
            "events": [], "points": [], "operators": [],
            "chain_notes": [], "link_before_create": []}
        result = v2.execute_embed(embed, search, session_id="s1")
        assert result["supersessions"] == [{
            "superseded": "obj-old", "supersedes_by": "strategy-B",
            "evidence": "entity lifecycle supersedes (conversation-driven)"}]
        assert result["stats"]["supersessions"] == 1
        note = next(n for n in result["link_before_create"]
                    if "strategy-B" in n["searched_for"])
        assert note["found"] is False  # new entity created

    def test_supersession_name_ref_resolves(self):
        """#1386: supersedes may reference the existing entity by NAME
        (anaphora resolution) — resolves to the S3 id."""
        search = {"entities": [{"id": "obj-old", "name": "strategy-A",
                                "kind": "core:strategy"}], "points": [], "events": []}
        embed = {"entities": [
            {"name": "strategy-B", "kind": "core:strategy", "lifecycle": "created",
             "supersedes": "strategy-A", "note": None}],
            "events": [], "points": [], "operators": [],
            "chain_notes": [], "link_before_create": []}
        result = v2.execute_embed(embed, search, session_id="s1")
        assert result["supersessions"][0]["superseded"] == "obj-old"

    def test_superseded_lifecycle_with_ref_warns_never_guesses(self):
        """Final-review P2 (#2164): an OLD entity emitted lifecycle='superseded'
        with supersedes=<its replacement> is direction-ambiguous — recording
        it would INVERT (superseded=<the LIVE replacement>, supersedes_by=<the
        old name>) and capture would fold the live successor to superseded,
        hiding it from recall_state. Never-guess: warn and record nothing;
        the canonical shape is the NEW entity with lifecycle='created' +
        supersedes=<old>."""
        search = {"entities": [
            {"id": "obj-old", "name": "strategy-A",
             "kind": "core:strategy"},
            {"id": "obj-new", "name": "strategy-B",
             "kind": "core:strategy"},
        ], "points": [], "events": []}
        embed = {"entities": [
            {"name": "strategy-A", "kind": "core:strategy",
             "lifecycle": "superseded", "supersedes": "strategy-B",
             "note": None}],
            "events": [], "points": [], "operators": [],
            "chain_notes": [], "link_before_create": []}
        result = v2.execute_embed(embed, search, session_id="s1")
        assert result["supersessions"] == [], (
            "superseded-with-ref must never form a record — the ref target "
            "is the LIVE successor; the record would invert the fold")
        assert any("lifecycle='superseded' with supersedes" in w
                   and "never-guess" in w for w in result["warnings"])
        # canonical shape (NEW entity lifecycle='created' + ref) still records
        embed["entities"] = [
            {"name": "strategy-B", "kind": "core:strategy",
             "lifecycle": "created", "supersedes": "strategy-A",
             "note": None}]
        result2 = v2.execute_embed(embed, search, session_id="s1")
        assert result2["supersessions"] == [{
            "superseded": "obj-old", "supersedes_by": "strategy-B",
            "evidence": "entity lifecycle supersedes (conversation-driven)"}]

    def test_unresolvable_supersedes_warns_no_record(self):
        """#1386: a supersedes ref that matches nothing in S3 warns and is
        NOT recorded (never guess a graph id)."""
        embed = {"entities": [
            {"name": "strategy-B", "kind": "core:strategy", "lifecycle": "created",
             "supersedes": "obj-hallucinated", "note": None}],
            "events": [], "points": [], "operators": [],
            "chain_notes": [], "link_before_create": []}
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert result["supersessions"] == []
        assert any("does not resolve" in w for w in result["warnings"])

    def test_derive_supersessions_projection_mapping(self):
        """#1386: the status-derivation mapping — event-structure →
        'A superseded by B' — works standalone (the read-side projection
        input)."""
        search = {"entities": [{"id": "obj-old", "name": "strategy-A",
                                "kind": "core:strategy"}], "points": [], "events": []}
        embed = {"entities": [
            {"name": "strategy-B", "kind": "core:strategy", "lifecycle": "created",
             "supersedes": "strategy-A", "note": None}],
            "events": [], "points": [], "operators": [],
            "chain_notes": [], "link_before_create": []}
        out = v2.derive_supersessions(embed, search)
        assert out == [{"superseded": "obj-old", "supersedes_by": "strategy-B",
                        "evidence": "entity lifecycle supersedes (conversation-driven)"}]
        # never-guess parity with execute_embed's collection: a 'superseded'
        # entity carrying a ref (the OLD side) must NOT derive an inverted
        # record. The search fixture must include BOTH sides so the guard
        # regression is RED-capable (a ref to an unresolvable name returns []
        # even with the guard removed).
        both = {"entities": [
            {"id": "obj-old", "name": "strategy-A", "kind": "core:strategy"},
            {"id": "obj-new", "name": "strategy-B", "kind": "core:strategy"}],
            "points": [], "events": []}
        old_side = {"entities": [
            {"name": "strategy-A", "kind": "core:strategy",
             "lifecycle": "superseded", "supersedes": "strategy-B",
             "note": None}],
            "events": [], "points": [], "operators": [],
            "chain_notes": [], "link_before_create": []}
        assert v2.derive_supersessions(old_side, both) == []

    def test_supersession_kind_collision_warns_no_wrong_record(self):
        """Review fix (P1): a name colliding across kinds resolves to the
        RIGHT kind (kind-filtered); an ambiguous SAME-kind duplicate warns
        and records nothing (mirror _find_existing_entity's discipline)."""
        # cross-kind collision → kind-filtered resolution picks the strategy
        search = {"entities": [
            {"id": "obj-feature", "name": "strategy-A", "kind": "core:feature"},
            {"id": "obj-strategy", "name": "strategy-A", "kind": "core:strategy"},
        ], "points": [], "events": []}
        embed = {"entities": [
            {"name": "strategy-B", "kind": "core:strategy", "lifecycle": "created",
             "supersedes": "strategy-A", "note": None}],
            "events": [], "points": [], "operators": [],
            "chain_notes": [], "link_before_create": []}
        result = v2.execute_embed(embed, search, session_id="s1")
        assert result["supersessions"][0]["superseded"] == "obj-strategy"
        # SAME-kind duplicate (ambiguous) → warn, no record, never guess
        search2 = {"entities": [
            {"id": "obj-a1", "name": "strategy-A", "kind": "core:strategy"},
            {"id": "obj-a2", "name": "strategy-A", "kind": "core:strategy"},
        ], "points": [], "events": []}
        result2 = v2.execute_embed(embed, search2, session_id="s1")
        assert result2["supersessions"] == []
        assert any("does not resolve" in w for w in result2["warnings"])

    def test_self_supersession_skipped(self):
        """Review fix (P2): supersedes resolving to the entity ITSELF is
        skipped with a warning (no self-referential cycle)."""
        search = {"entities": [{"id": "obj-b", "name": "strategy-B",
                                "kind": "core:strategy"}], "points": [], "events": []}
        embed = {"entities": [
            {"name": "strategy-B", "kind": "core:strategy", "lifecycle": "created",
             "supersedes": "strategy-B", "note": None}],
            "events": [], "points": [], "operators": [],
            "chain_notes": [], "link_before_create": []}
        result = v2.execute_embed(embed, search, session_id="s1")
        assert result["supersessions"] == []
        assert any("supersedes itself" in w for w in result["warnings"])

    def test_failure_paths_carry_supersessions_key(self, monkeypatch):
        """Review fix (P2): the empty-conversation and S5-failed paths return
        the same contract (supersessions present) as the happy path."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setattr(v2.time, "sleep", lambda _: None)
        out = v2.extract_session_v2(MockModel([]), [])
        assert "supersessions" in out and out["supersessions"] == []

        class BoomModel(MockModel):
            def complete(self, *, system, user, max_tokens=None):
                raise RuntimeError("boom")

        out2 = v2.extract_session_v2(BoomModel([]), [{"role": "user",
                                                      "content": "x"}])
        assert "supersessions" in out2

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

    def test_tier_a_point_passes_through_with_quote(self):
        """E2 (D4/D6): a Tier-A embed point yields a payload point with
        tier:"A", the verbatim value in content AND quote, pointKind still
        statement — and counts in stats["tier_a_points"] (surface 17)."""
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["points"] = [{"content": "my personal best 5K time is 27:12",
                            "pointKind": "statement", "about_entities": [],
                            "tier": "A",
                            "quote": "my personal best 5K time is 27:12"}]
        result = v2.execute_embed(embed, {}, session_id="s1")
        pt = result["payload"]["points"][0]
        assert pt["content"] == "my personal best 5K time is 27:12"  # verbatim
        assert pt["quote"] == "my personal best 5K time is 27:12"    # verbatim
        assert pt["tier"] == "A"
        assert pt["pointKind"] == "statement"                        # still a statement
        assert result["stats"]["tier_a_points"] == 1

    def test_tier_b_absent_by_default(self):
        """E2 (D1/O3): tier is A-only emission — a Tier-B/no-tier point
        yields NO tier key (zero-diff payloads for non-Tier-A sessions)."""
        embed = json.loads(json.dumps(S2_FIXTURE))  # no tier on the point
        result = v2.execute_embed(embed, {}, session_id="s1")
        pt = result["payload"]["points"][0]
        assert "tier" not in pt
        assert result["stats"]["tier_a_points"] == 0

    def test_tier_a_without_quote_warns(self):
        """E2 (D4): a Tier-A point with an empty quote warns (fail-loud,
        never silent) but is still written (S5 never blocks)."""
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["points"] = [{"content": "my personal best 5K time is 27:12",
                            "pointKind": "statement", "about_entities": [],
                            "tier": "A"}]          # quote omitted
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert any("verbatim quote" in w for w in result["warnings"])  # loud, not silent
        assert result["payload"]["points"]          # still written — never blocks

    def test_tier_not_minted_kind(self):
        """E2 (D1): tier is a classification hint, NOT a kind — the
        minted-kind gate stays clean for a Tier-A point."""
        embed = json.loads(json.dumps(S2_FIXTURE))
        embed["points"] = [{"content": "gym at 6pm", "pointKind": "statement",
                            "about_entities": [], "tier": "A",
                            "quote": "gym at 6pm"}]
        assert v2._minted_kind_report(embed) == []  # tier is not a kind
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert result["minted_kinds"] == []

    def test_ingest_create_point_calls_pass_tier(self):
        """E2 (Task 4, P4 parity): BOTH create_point call sites in the
        hosted commit points-reconcile pass thread `tier` — the marker must
        behave identically on SDK commit and hosted capture (structural
        guard; live read-back is gated on a real TORTOISE_DB_URI)."""
        import tortoise.hosted_api
        src = Path(tortoise.hosted_api.__file__).read_text()
        assert src.count("tier=pr.point.tier") >= 2


class TestE5FactValueContradiction:
    """E5 (#1537) — length-guarded token overlap + fact-value contradiction
    detection. E2E-6 positives/negatives at the detection level: a 5-token
    point sharing 3 tokens with a 50-token point is NOT a REVISES (the old
    min-denominator 3/5=0.6 false-positive); a short-value change that raw
    overlap misses is caught via the entity-grounded contradiction pass.
    """

    def test_token_overlap_length_guard_short_vs_long(self):
        """E2E-6 negative: a 5-token point sharing 3 tokens with a 50-token
        point is NOT a REVISES. Old min-denominator code: 3/5 = 0.6 → false
        REVISES (the bug). New max-denominator: 3/50 = 0.06 → none."""
        short = "gym 6pm schedule changed today"        # 5 tokens
        long = "gym 6pm schedule " + " ".join(f"word{i}" for i in range(47))  # 3 + 47 = 50
        assert len(short.split()) == 5 and len(long.split()) == 50
        assert v2._token_overlap(long, short) < 0.6
        assert v2._find_point_match([{"id": "pt_long", "content": long}],
                                    short)[0] == "none"

    def test_token_overlap_short_revision_still_matches(self):
        """BVA 0.6: a short revision sharing its full frame still REVISES."""
        assert v2._token_overlap("gym at 6pm", "gym at 5pm") >= 0.6
        assert v2._find_point_match([{"id": "pt_old", "content": "gym at 6pm"}],
                                    "gym at 5pm")[0] == "revises"

    def test_token_overlap_single_shared_token_never_revises(self):
        """floor-2 guard: a single shared content token is never a revision."""
        assert v2._token_overlap("gym 6pm", "gym membership") == 0.0

    def test_fact_value_contradiction_same_entity_value_change(self):
        existing = {"id": "pt_old", "content": "gym at 6pm"}
        assert v2._fact_value_contradiction("gym at 5pm", ["gym"], existing)

    def test_fact_value_contradiction_identical_value_no_supersession(self):
        """E2E-6 negative — identical-value re-assertion must NOT supersede:
        the exact-match pass dedups it BEFORE the contradiction pass runs
        (NOOP link is E2E-11's; reworded-identical values are that lane too
        — the value-diff guard also rejects it directly)."""
        existing = {"id": "pt_old", "content": "gym at 6pm"}
        assert v2._find_point_match([existing], "gym at 6pm",
                                    about_entities=["gym"])[0] == "exact"
        assert not v2._fact_value_contradiction("gym at 6pm", ["gym"], existing)

    def test_fact_value_contradiction_different_entity_no(self):
        existing = {"id": "pt_old", "content": "gym at 6pm"}
        assert not v2._fact_value_contradiction("yoga at 5pm", ["yoga"], existing)

    def test_find_point_match_contradiction_below_overlap_threshold(self):
        """short-value change that raw overlap misses ("gym 6pm" →
        "gym 5pm": 1 shared token) is caught by the contradiction pass."""
        existing = {"id": "pt_old", "content": "gym 6pm"}
        assert v2._find_point_match([existing], "gym 5pm",
                                    about_entities=["gym"])[0] == "revises"

    def test_fact_value_contradiction_older_when_rejected(self):
        """CG-2 with-date mode: the new point's `when` must be >= the old
        point's when/createdAt — an older date never supersedes."""
        existing = {"id": "pt_old", "content": "gym at 6pm",
                    "when": "2026-06-16"}
        assert not v2._fact_value_contradiction(
            "gym at 5pm", ["gym"], existing, when="2026-06-02")
        assert v2._fact_value_contradiction(
            "gym at 5pm", ["gym"], existing, when="2026-06-16")
        assert v2._fact_value_contradiction(
            "gym at 5pm", ["gym"], existing, when="2026-06-20")

    def test_fact_value_contradiction_undated_session_order(self):
        """CG-2 without-date mode: when either side carries no date, the
        session-ingest-order invariant supplies the guard (no rejection)."""
        existing = {"id": "pt_old", "content": "gym at 6pm"}
        assert v2._fact_value_contradiction("gym at 5pm", ["gym"], existing)
        assert v2._fact_value_contradiction(
            "gym at 5pm", ["gym"], existing, when="")


# ── Participant slots (#1418: object + event-as-content) ─────────────────────

SLOTS_FIXTURE = {
    "entities": [
        {"name": "single-flash pipeline", "kind": "core:plan",
         "lifecycle": "created", "supersedes": None, "note": None},
        {"name": "cleaning-pass tier", "kind": "core:plan",
         "lifecycle": "created", "supersedes": None, "note": None},
        {"name": "the team", "kind": "core:team",
         "lifecycle": "created", "supersedes": None, "note": None},
        {"name": "the owner", "kind": "core:role",
         "lifecycle": "created", "supersedes": None, "note": None},
    ],
    "events": [
        {"content": "The owner paused the solar cleaning tier",
         "eventKind": "core:decision",
         "about_entities": ["cleaning-pass tier"],
         "slots": {"subject": [{"name": "the owner", "kind": "core:role",
                                 "confidence": 0.85}],
                    "object": [{"name": "cleaning-pass tier",
                                 "kind": "core:plan", "confidence": 0.9}]}},
    ],
    "points": [
        {"content": "single-flash with granularity is the working path",
         "pointKind": "statement",
         "about_entities": ["single-flash pipeline", "cleaning-pass tier"],
         "slots": {"subject": [{"name": "the team", "kind": "core:team",
                                 "confidence": 0.8}],
                    "object": [{"name": "single-flash pipeline",
                                 "kind": "core:plan", "confidence": 0.9}],
                    "event": [{"name": "the Aug 3 meeting",
                                "kind": "core:meeting", "confidence": 0.7}]}},
    ],
    "operators": [],
    "chain_notes": [],
    "link_before_create": [],
}


class TestParticipantSlots:
    """#1418 — the v2 extractor emits object + event slots (with per-slot
    confidence) in addition to subject, per the #1370 S1 re-validation
    contract: OUTPUT_CONTRACT → execute_embed → commit_schema."""

    def test_output_contract_carries_slot_shape(self):
        # one shared constant consumed by BOTH S2 (MAP) and S4 (REVIEW)
        assert '"slots"' in v2.OUTPUT_CONTRACT
        for role in ("subject", "object", "event"):
            assert role in v2.OUTPUT_CONTRACT
        assert "confidence" in v2.OUTPUT_CONTRACT

    def test_s2_and_s4_templates_teach_slots(self):
        for tmpl in (v2.S2_TMPL, v2.S4_TMPL):
            assert "PARTICIPANT SLOTS" in tmpl
            for role in ("subject", "object", "event"):
                assert role in tmpl
            assert "provenance" in tmpl.lower()  # event slot binds content, never provenance

    def test_execute_embed_carries_point_slots_with_confidence(self):
        result = v2.execute_embed(SLOTS_FIXTURE, {}, session_id="s1")
        pt = result["payload"]["points"][0]
        slots = pt["slots"]
        assert slots["subject"] == [{"name": "the team", "kind": "core:team",
                                     "confidence": 0.8}]
        assert slots["object"] == [{"name": "single-flash pipeline",
                                     "kind": "core:plan", "confidence": 0.9}]
        # event-as-content: the point claims the Aug 3 meeting concluded X
        assert slots["event"] == [{"name": "the Aug 3 meeting",
                                    "kind": "core:meeting", "confidence": 0.7}]

    def test_execute_embed_carries_event_slots(self):
        result = v2.execute_embed(SLOTS_FIXTURE, {}, session_id="s1")
        ev = result["payload"]["events"][0]
        assert ev["slots"]["subject"] == [{"name": "the owner",
                                             "kind": "core:role",
                                             "confidence": 0.85}]
        assert ev["slots"]["object"] == [{"name": "cleaning-pass tier",
                                             "kind": "core:plan",
                                             "confidence": 0.9}]

    def test_slots_payload_passes_layer1(self):
        from tortoise.commit_schema import validate_payload_dict
        result = v2.execute_embed(SLOTS_FIXTURE, {}, session_id="s1")
        l1, _model = validate_payload_dict(result["payload"])
        assert l1.ok, l1.errors

    def test_slots_absent_when_embed_has_none(self):
        # S5 must not fabricate slots for embed entries that never had them
        result = v2.execute_embed(S2_FIXTURE, {}, session_id="s1")
        assert "slots" not in result["payload"]["points"][0]
        assert "slots" not in result["payload"]["events"][0]

    def test_slot_sanitization(self):
        embed = json.loads(json.dumps(SLOTS_FIXTURE))
        embed["points"][0]["slots"]["subject"] = [
            {"name": "the team", "kind": "core:team", "confidence": "high"},
            {"name": "", "kind": "core:team", "confidence": 0.9},
            "not-a-dict",
        ]
        embed["points"][0]["slots"]["agent"] = [
            {"name": "the agent", "kind": "core:role", "confidence": 0.9},
        ]
        embed["points"][0]["slots"]["object"][0]["confidence"] = 1.7
        result = v2.execute_embed(embed, {}, session_id="s1")
        slots = result["payload"]["points"][0]["slots"]
        # non-numeric confidence → 0.0 (deterministic, never a crash)
        assert slots["subject"] == [{"name": "the team", "kind": "core:team",
                                     "confidence": 0.0}]
        # unknown role key dropped
        assert "agent" not in slots
        # confidence clamped into [0, 1]
        assert slots["object"][0]["confidence"] == 1.0
        warns = " ".join(result["warnings"])
        assert "agent" in warns

    def test_slot_minted_kind_repaired(self):
        # P2-1 review fix: slot kinds gate against the same master_kind_forms
        # vocabulary S5 applies to entities — a near-miss kind is repaired
        # (never silently divergent), and the repaired (name, kind) then
        # resolves against the emitted entity
        embed = json.loads(json.dumps(SLOTS_FIXTURE))
        embed["entities"].append({"name": "mystery thing",
                                   "kind": "core:other", "lifecycle": "created",
                                   "supersedes": None, "note": None})
        embed["points"][0]["slots"]["subject"] = [
            {"name": "mystery thing", "kind": "minted:garbage",
             "confidence": 0.8}]
        result = v2.execute_embed(embed, {}, session_id="s1")
        slots = result["payload"]["points"][0]["slots"]
        assert slots["subject"] == [{"name": "mystery thing",
                                      "kind": "core:other",
                                      "confidence": 0.8}]
        assert any("minted" in w and "minted:garbage" in w
                   for w in result["warnings"])

    def test_slot_unresolved_entity_dropped_with_warning(self):
        # P2-2 review fix: a stray subject/object slot must never sink the
        # whole commit — dropped with a warning (operator-drop pattern);
        # event slots pass through untouched (write-path resolved)
        embed = json.loads(json.dumps(SLOTS_FIXTURE))
        embed["points"][0]["slots"]["subject"] = [
            {"name": "ghost entity", "kind": "core:role", "confidence": 0.8}]
        result = v2.execute_embed(embed, {}, session_id="s1")
        slots = result["payload"]["points"][0]["slots"]
        assert "subject" not in slots          # dropped
        assert slots["event"]                 # event role untouched
        warns = " ".join(result["warnings"])
        assert "ghost entity" in warns and "dropped" in warns

    def test_non_list_role_value_warns(self):
        embed = json.loads(json.dumps(SLOTS_FIXTURE))
        embed["points"][0]["slots"]["subject"] = {
            "name": "the team", "kind": "core:team", "confidence": 0.8}
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert "subject" not in result["payload"]["points"][0]["slots"]
        assert any("must be a list" in w for w in result["warnings"])


# ── E3 (issue #1535): atomic points + search_keys + speaker via source-turn ──

class TestE3Contract:
    def test_output_contract_has_e3_keys(self):
        for key in ("quote", "search_keys", "source_turn_id"):
            assert key in v2.OUTPUT_CONTRACT, f"contract missing {key}"
        # the contract's points block must carry all three STRUCTURALLY (the
        # schema tokens, not the trailing comments — comment text cannot
        # satisfy these assertions)
        pts_block = v2.OUTPUT_CONTRACT.split('"points":', 1)[1]
        pts_block = pts_block.split('\n  "operators":', 1)[0]
        assert '"quote": str|null' in pts_block
        assert '"search_keys": [str, ...]' in pts_block
        assert '"source_turn_id": int|null}' in pts_block

    def test_source_role_is_never_emitted(self):
        # review-gate fix (2026-08-20): plan docs patched to remove source_role
        for src in (v2.OUTPUT_CONTRACT, v2.S2_TMPL, v2.S4_TMPL):
            assert "source_role" not in src

    def test_atomicity_and_verbatim_value_rules_present(self):
        for tmpl in (v2.S2_TMPL, v2.S4_TMPL):
            assert "ATOMIC POINTS" in tmpl or "one claim per point" in tmpl
            assert "verbatim" in tmpl and "quote" in tmpl
            assert "USER VS ASSISTANT" in tmpl or "not a user fact" in tmpl


class TestE3SourceTranscript:
    def _edus(self):
        return [{"index": 0, "role": "user", "text": "my 5K best is 27:12"},
                {"index": 1, "role": "assistant", "text": "nice time"}]

    def test_transcript_injected_when_edus_present(self):
        p = v2.render_s2_prompt(edus=self._edus())
        assert "SOURCE TRANSCRIPT" in p
        assert "0: user: my 5K best is 27:12" in p
        assert "1: assistant: nice time" in p

    def test_s4_transcript_injected(self):
        p = v2.render_s4_prompt("story", {}, {}, edus=self._edus())
        assert "0: user:" in p

    def test_none_edus_renders_identical(self):
        base = v2.render_s2_prompt()
        # the injected BLOCK is absent (the rules text mentions the SOURCE
        # TRANSCRIPT concept — assert on the block header, not the phrase)
        assert "SOURCE TRANSCRIPT (turn-indexed" not in base

    def test_cap_omits_block(self, monkeypatch):
        monkeypatch.setattr(v2, "_SOURCE_TRANSCRIPT_CAP", 10)
        assert "SOURCE TRANSCRIPT (turn-indexed" not in v2.render_s2_prompt(edus=self._edus())

    def test_cap_boundary_at_length_included(self, monkeypatch):
        # over-cap omits; AT the cap the block is still included (> cap)
        edus = self._edus()
        text = v2._edus_to_text(edus)
        monkeypatch.setattr(v2, "_SOURCE_TRANSCRIPT_CAP", len(text))
        assert "SOURCE TRANSCRIPT (turn-indexed" in v2.render_s2_prompt(edus=edus)
        monkeypatch.setattr(v2, "_SOURCE_TRANSCRIPT_CAP", len(text) - 1)
        assert "SOURCE TRANSCRIPT (turn-indexed" not in v2.render_s2_prompt(edus=edus)

    def test_over_cap_render_identical_to_none(self, monkeypatch):
        # P2-4 fix: when the block is omitted (over cap) the render must be
        # byte-identical to the edus=None render — no stray "\n\n" tail
        monkeypatch.setattr(v2, "_SOURCE_TRANSCRIPT_CAP", 1)
        base = v2.render_s2_prompt()
        assert v2.render_s2_prompt(edus=self._edus()) == base
        s4 = v2.render_s4_prompt("story", {}, {}, edus=self._edus())
        assert s4 == v2.render_s4_prompt("story", {}, {})


class TestE3Resolution:
    # The quote anchors on a NON-first turn (index 1) so a degenerate
    # first-turn-default resolver cannot pass the resolution tests.
    EDUS = [{"index": 0, "role": "assistant", "text": "maybe try intervals for speed"},  # noqa: RUF012
            {"index": 1, "role": "user", "text": "my 5K best is 27:12"}]

    def _embed(self, **point_kwargs):
        p = {"content": "the user's 5K best is 27:12", "pointKind": "statement"}
        p.update(point_kwargs)
        return {"entities": [], "points": [p], "events": [], "operators": []}

    def test_quote_resolves_to_turn(self):
        r = v2.execute_embed(self._embed(quote="my 5K best is 27:12"),
                             {}, session_id="s1", edus=self.EDUS)
        pt = r["payload"]["points"][0]
        assert pt["quote"] == "my 5K best is 27:12"
        assert pt["source_turn_id"] == 1
        assert pt["search_keys"] == []
        # atomicity negative control: a single-sentence point does NOT warn
        assert not any("ONE claim per point" in w for w in r["warnings"])

    def test_conflicting_model_index_deterministic_wins(self):
        r = v2.execute_embed(self._embed(quote="my 5K best is 27:12",
                                         source_turn_id=0),
                             {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] == 1
        assert any("source_turn_id" in w for w in r["warnings"])

    def test_agreeing_model_index_no_warning(self):
        # advisory-confirm case: the model's index agrees with the verbatim
        # anchor → used, and no contradiction warning fires
        r = v2.execute_embed(self._embed(quote="maybe try intervals",
                                         source_turn_id=0),
                             {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] == 0
        assert not any("contradicts" in w for w in r["warnings"])

    def test_out_of_range_model_index_ignored(self):
        # branch 2's range guard: an out-of-range model index is skipped and
        # the deterministic anchor wins (silently — nothing to contradict)
        r = v2.execute_embed(self._embed(quote="my 5K best is 27:12",
                                         source_turn_id=99),
                             {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] == 1

    def test_non_int_model_index_ignored(self):
        # a string model index is not an int → treated as absent; det wins
        r = v2.execute_embed(self._embed(quote="my 5K best is 27:12",
                                         source_turn_id="1"),
                             {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] == 1

    def test_boolean_model_index_ignored(self):
        # isinstance(True, int) is True in Python — a JSON boolean must NOT
        # be treated as index 1 (type() is int guard; never guess)
        r = v2.execute_embed(self._embed(quote="my 5K best is 27:12",
                                         source_turn_id=True),
                             {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] == 1

    def test_token_overlap_fallback_resolves(self):
        # branch 3: quote is a genuine paraphrase — NOT a verbatim substring
        # of any turn ("speed intervals" appears nowhere verbatim) — with
        # >= 0.6 token overlap against exactly one turn → that turn wins
        r = v2.execute_embed(self._embed(quote="speed intervals"),
                             {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] == 0

    def test_token_overlap_exactly_threshold_resolves(self):
        # the >= 0.6 boundary: exactly 0.6 overlap (3 of 5 tokens) resolves
        edus = [{"index": 0, "role": "user",
                 "text": "one two three six seven"},
                {"index": 1, "role": "user", "text": "a b c d e"}]
        r = v2.execute_embed(self._embed(quote="one two three four five"),
                             {}, session_id="s1", edus=edus)
        assert r["payload"]["points"][0]["source_turn_id"] == 0

    def test_token_overlap_tie_earlier_turn_wins(self):
        # equal max overlap across two turns → the EARLIER index wins (the
        # `ov > best_ov` first-match determinism contract)
        edus = [{"index": 0, "role": "user",
                 "text": "maybe try intervals for speed"},
                {"index": 1, "role": "user", "text": "intervals speed zzz"}]
        r = v2.execute_embed(self._embed(quote="speed intervals x"),
                             {}, session_id="s1", edus=edus)
        assert r["payload"]["points"][0]["source_turn_id"] == 0

    def test_below_threshold_fallback_is_none(self):
        # branch 3/4: no verbatim match and overlap < 0.6 everywhere → fail-open
        r = v2.execute_embed(self._embed(quote="totally unrelated topic"),
                             {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] is None
        assert any("no resolvable source turn" in w for w in r["warnings"])

    def test_duplicate_quote_first_turn_wins(self):
        # the same quote in two turns resolves to the EARLIER index (the
        # deterministic first-match contract)
        edus = [{"index": 0, "role": "user", "text": "my 5K best is 27:12"},
                {"index": 1, "role": "user", "text": "my 5K best is 27:12 again"}]
        r = v2.execute_embed(self._embed(quote="my 5K best is 27:12"),
                             {}, session_id="s1", edus=edus)
        assert r["payload"]["points"][0]["source_turn_id"] == 0

    def test_no_quote_no_index_is_none(self):
        r = v2.execute_embed(self._embed(), {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] is None
        assert any("no resolvable source turn" in w for w in r["warnings"])

    def test_empty_quote_in_range_model_index_used_with_warning(self):
        # D4 step 3 (plan): quote absent but the model emitted a plausible
        # in-range index → use it, with an "unverified" warning (never
        # silently trust an unanchored index; never guess beyond the turns)
        r = v2.execute_embed(self._embed(source_turn_id=1),
                             {}, session_id="s1", edus=self.EDUS)
        pt = r["payload"]["points"][0]
        assert pt["source_turn_id"] == 1
        assert pt["quote"] == ""
        assert any("unverified" in w for w in r["warnings"])

    def test_empty_quote_out_of_range_index_is_none(self):
        # D4 step 3 boundary: an out-of-range model index with no quote is
        # NOT trusted — fail-open None (never guess)
        r = v2.execute_embed(self._embed(source_turn_id=99),
                             {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["source_turn_id"] is None

    def test_model_index_resolves_by_value_not_position(self):
        # P2-3: _edus_from_conversation drops empty-content turns while
        # preserving original indices — list position and index value can
        # diverge. The model's index must resolve to the TURN with that
        # index value, not the list slot. Here edus has indices [0, 2] and
        # len=2, so model_idx=1 is positionally in-range but names NO turn.
        # A positional lookup would read index-2's text ("my 5K best is
        # 27:12") and raise a SPURIOUS contradiction warning against the
        # correct deterministic match (det=2); the by-value fix sees an
        # empty m_turn and stays silent.
        edus = [{"index": 0, "role": "user", "text": "hello"},
                {"index": 2, "role": "user", "text": "my 5K best is 27:12"}]
        r = v2.execute_embed(self._embed(quote="my 5K best is 27:12",
                                         source_turn_id=1),
                             {}, session_id="s1", edus=edus)
        pt = r["payload"]["points"][0]
        assert pt["source_turn_id"] == 2
        assert not any("contradicts" in w for w in r["warnings"])

    def test_agreeing_model_index_by_value_no_warning(self):
        # P2-3 positive control: the model's index names a turn BY VALUE that
        # contains the quote → deterministic match, no contradiction warning
        edus = [{"index": 0, "role": "user", "text": "hello"},
                {"index": 2, "role": "user", "text": "my 5K best is 27:12"}]
        r = v2.execute_embed(self._embed(quote="my 5K best is 27:12",
                                         source_turn_id=2),
                             {}, session_id="s1", edus=edus)
        pt = r["payload"]["points"][0]
        assert pt["source_turn_id"] == 2
        assert not any("contradicts" in w for w in r["warnings"])

    def test_search_keys_cleaned(self):
        # dedup + empty-drop are SILENT; the >60 entry exercises the warning
        # plumbing while the cleaned list stays deterministic
        r = v2.execute_embed(
            self._embed(search_keys=["personal best 5K", "27:12", "27:12", "",
                                     "x" * 61]),
            {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["search_keys"] == ["personal best 5K", "27:12"]
        assert any("search_keys" in w for w in r["warnings"])

    def test_search_keys_capped_at_four(self):
        r = v2.execute_embed(
            self._embed(search_keys=["a", "b", "c", "d", "e"]),
            {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["search_keys"] == ["a", "b", "c", "d"]
        assert any("capped at 4" in w for w in r["warnings"])

    def test_search_keys_non_list_warns(self):
        r = v2.execute_embed(self._embed(search_keys="personal best"),
                             {}, session_id="s1", edus=self.EDUS)
        assert r["payload"]["points"][0]["search_keys"] == []
        assert any("must be a list" in w for w in r["warnings"])

    def test_quote_capped_at_200(self):
        r = v2.execute_embed(self._embed(quote="q" * 250), {}, session_id="s1",
                             edus=self.EDUS)
        assert len(r["payload"]["points"][0]["quote"]) == 200

    def test_atomicity_soft_guard_warns_multi_sentence(self):
        # D1: the executable atomicity guard — a 2-sentence point warns
        # (atomicity is prompt-enforced + warn-guarded, never hard-blocked)
        r = v2.execute_embed(
            self._embed(content="the user's 5K best is 27:12. He trains daily."),
            {}, session_id="s1", edus=self.EDUS)
        assert any("ONE claim per point" in w for w in r["warnings"])
        # never hard-block: the compound point SURVIVES in the payload
        assert len(r["payload"]["points"]) == 1
        assert r["payload"]["points"][0]["content"] == \
            "the user's 5K best is 27:12. He trains daily."


# ── The orchestrator (mock model, no LLM) ──────────────────────────────────

class TestOrchestrator:
    def test_model_exception_recorded_not_silent(self, monkeypatch):
        """Review fix (P1): a raising model must surface in errors, not be
        masked by the completion thread wrapper. M3 (#1524): an UNKNOWN-class
        exception is transient-safe → retried with backoff (sleep patched out
        here); the surfaced error is the final attempt's exception."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setattr(v2.time, "sleep", lambda _: None)

        class BoomModel(MockModel):
            def complete(self, *, system, user, max_tokens=None):
                raise RuntimeError("rate limited")

        conv = [{"role": "user", "content": "we decided X"}]
        out = v2.extract_session_v2(BoomModel([]), conv)
        assert any("rate limited" in e for e in out["errors"])
        assert out["error_census"]["transient_unknown"] >= 1

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

    def test_session_date_anchors_s1_prompt(self, monkeypatch):
        """T1 (#1533): session_date threads into the S1 prompt — the DATE
        ANCHOR block carries the session date; S2/S4 render it too (with the
        D3 emission rules)."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)

        def resp(system, user):
            if "STORY SUMMARIZER" in system:
                return "We shipped the migration on 2026-08-01."
            if "GRAPH MAPPER" in system:
                return json.dumps(S2_FIXTURE)
            if "GAP REVIEWER" in system:
                return json.dumps(S2_FIXTURE)
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        model = MockModel(resp)
        conv = [{"role": "user", "content": "we decided X"}]
        out = v2.extract_session_v2(model, conv, session_date="2026-08-01")
        assert out["errors"] == []
        s1 = model.calls[0][0]
        assert "DATE ANCHOR" in s1
        assert "today is `2026-08-01`" in s1
        s2 = next(s for s, _ in model.calls if "GRAPH MAPPER" in s)
        s4 = next(s for s, _ in model.calls if "GAP REVIEWER" in s)
        assert "DATE ANCHOR" in s2 and "today is `2026-08-01`" in s2
        assert "EVENT `startedAt`" in s2
        assert "DATE ANCHOR" in s4 and "today is `2026-08-01`" in s4
        assert "POINT `when`" in s4

    def test_undated_rendering_byte_identical(self, monkeypatch):
        """T2 (#1533): session_date=None/"" keeps prompts byte-identical to
        pre-E1 — no date text in S1/S2/S4; the anchor renders to "" when
        undated (the regression guard for the prompt insert)."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)

        def resp(system, user):
            if "STORY SUMMARIZER" in system:
                return "We believed X."
            if "GRAPH MAPPER" in system:
                return json.dumps(S2_FIXTURE)
            if "GAP REVIEWER" in system:
                return json.dumps(S2_FIXTURE)
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        conv = [{"role": "user", "content": "we decided X"}]
        for sd in (None, ""):
            model = MockModel(resp)
            out = v2.extract_session_v2(model, conv, session_date=sd)
            assert out["errors"] == []
            for system, _ in model.calls:
                assert "DATE ANCHOR" not in system
                assert "today is" not in system
                assert "2026-08-01" not in system
        # byte-identical rendering at the prompt level: the {date_anchor}
        # placeholder renders to ZERO bytes when undated (S1 exactly equals
        # the template with the placeholder erased)
        assert v2._date_anchor(None) == ""
        assert v2._date_anchor("") == ""
        baseline_s1 = (v2.S1_TMPL
                       .replace("{memory_granularity}", v2._granularity_text())
                       .replace("{date_anchor}", ""))
        undated_s1 = (v2.S1_TMPL
                      .replace("{memory_granularity}", v2._granularity_text())
                      .replace("{date_anchor}", v2._date_anchor(None)))
        assert undated_s1 == baseline_s1, "undated S1 must be byte-identical"
        assert v2.render_s2_prompt(session_date=None) == \
            v2.render_s2_prompt(session_date="")
        assert v2.render_s4_prompt("STORY", {}, S2_FIXTURE,
                                   session_date=None) == \
            v2.render_s4_prompt("STORY", {}, S2_FIXTURE, session_date="")

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

    def test_verbatim_state_value_survives_pipeline(self, monkeypatch):
        """E2 (Task 5, E2E-5 unit analog): the KU needle — "my personal
        best 5K time is 27:12" — survives extraction VERBATIM: value in
        content + quote, tier:"A", survives the S4 pass (empty gap list →
        S2 kept), the assistant-suggestion-only negative holds, and the
        owner invariant is honored (the raw conversation is NEVER mutated
        by S1–S5 — extraction never replaces verbatim evidence)."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)

        def resp(system, user):
            if "STORY SUMMARIZER" in system:
                return ("The user stated a personal best: my personal best 5K "
                        "time is 27:12. The assistant suggested interval training.")
            if "GRAPH MAPPER" in system:
                return json.dumps({
                    "entities": [], "events": [],
                    "points": [{
                        "content": "my personal best 5K time is 27:12",
                        "pointKind": "statement", "about_entities": [],
                        "tier": "A",
                        "quote": "my personal best 5K time is 27:12",
                    }],
                    "operators": [], "chain_notes": [], "link_before_create": []})
            if "GAP REVIEWER" in system:
                # empty gap list → the pipeline keeps the S2 list (existing
                # deterministic backstop) — the Tier-A point survives S4
                return json.dumps({"entities": [], "events": [], "points": [],
                                   "operators": [], "chain_notes": [],
                                   "link_before_create": []})
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        conv = [{"role": "user", "content": "my personal best 5K time is 27:12"},
                {"role": "assistant", "content": "consider interval training"}]
        out = v2.extract_session_v2(MockModel(resp), conv)
        assert out["errors"] == []
        pt = next(p for p in out["payload"]["points"])
        assert "27:12" in pt["content"]                       # value verbatim, not compressed
        assert "27:12" in pt["quote"]                         # verbatim source retained
        assert pt["tier"] == "A"
        # owned negative: the assistant suggestion is NOT a user fact here
        assert "interval training" not in pt["content"]
        # owner invariant: extraction NEVER replaces verbatim evidence —
        # the raw conversation is untouched by S1–S5
        assert conv == [{"role": "user", "content": "my personal best 5K time is 27:12"},
                        {"role": "assistant", "content": "consider interval training"}]


# ── A′ (#1695 Task 2): label-order randomization hook ────────────────────

# The canonical kind order (insertion order of build_master_list) — the
# golden the env-unset render must match byte-for-byte.
def _canonical_verbose_render():
    return v2._render_master_verbose(v2.build_master_list())


class TestLabelOrder:
    def test_env_unset_default_order_byte_identical(self, monkeypatch):
        """Regression pin: TORTOISE_LABEL_ORDER unset → the render is
        byte-identical to the canonical (pre-shuffle) order, through the
        _render_master dispatcher."""
        monkeypatch.delenv("TORTOISE_LABEL_ORDER", raising=False)
        monkeypatch.delenv("TORTOISE_LABEL_ORDER_SEED", raising=False)
        assert v2._label_order_rng("any story") is None
        assert v2._render_master(v2.build_master_list()) == _canonical_verbose_render()

    def test_seeded_shuffle_deterministic_per_story(self, monkeypatch):
        """Same story → same seeded shuffle; different story → different
        order (the A′ paired re-run contract)."""
        monkeypatch.setenv("TORTOISE_LABEL_ORDER", "shuffle")
        m = v2.build_master_list()
        r1 = v2._render_master_verbose(m, v2._label_order_rng("story A"))
        r2 = v2._render_master_verbose(m, v2._label_order_rng("story A"))
        assert r1 == r2, "same story must reproduce the same kind order"
        r3 = v2._render_master_verbose(m, v2._label_order_rng("story B"))
        keys = lambda r: [ln for ln in r.split("\n") if ln.startswith("- core:")]  # noqa: E731
        assert keys(r1) != keys(r3), "different stories must shuffle differently"
        assert len(keys(r1)) == len(keys(r3))

    def test_env_seed_override_deterministic_across_stories(self, monkeypatch):
        """TORTOISE_LABEL_ORDER_SEED pins the order across stories (the
        A/B's per-arm order confound control)."""
        monkeypatch.setenv("TORTOISE_LABEL_ORDER", "shuffle")
        monkeypatch.setenv("TORTOISE_LABEL_ORDER_SEED", "7")
        m = v2.build_master_list()
        r1 = v2._render_master_verbose(m, v2._label_order_rng("story A"))
        r2 = v2._render_master_verbose(m, v2._label_order_rng("story B"))
        assert r1 == r2, "the env seed overrides the story-derived seed"

    def test_shuffle_reorders_kind_groups_only(self, monkeypatch):
        """Only the KIND vocabulary randomizes — hint blocks (chains,
        user-personal-state, memory-granularity, carve-out) keep their
        canonical positions (byte-identical outside the kind groups)."""
        monkeypatch.setenv("TORTOISE_LABEL_ORDER", "shuffle")
        m = v2.build_master_list()
        plain = v2._render_master_verbose(m)
        shuffled = v2._render_master_verbose(m, v2._label_order_rng("s"))
        for marker in ("CHAINS (the business logic",
                       "USER-PERSONAL-STATE VOCABULARY",
                       "MEMORY GRANULARITY (what to keep",
                       "STATE-VALUE CARVE-OUT"):
            assert plain.index(marker) == shuffled.index(marker)

    def test_compact_render_shuffles_too(self, monkeypatch):
        """Label-order randomization ships in ALL render modes; the compact
        env-unset render stays byte-identical to its canonical form."""
        m = v2.build_master_list()
        compact_canonical = v2._render_master_compact(m, "story")
        monkeypatch.setenv("TORTOISE_LABEL_ORDER", "shuffle")
        r1 = v2._render_master_compact(m, "a", v2._label_order_rng("x"))
        r2 = v2._render_master_compact(m, "a", v2._label_order_rng("x"))
        assert r1 == r2
        assert r1 != compact_canonical

    def test_s2_prompt_uses_shuffled_master(self, monkeypatch):
        """The end-to-end hook: render_s2_prompt picks up the shuffle via
        _render_master when the env is set; byte-identical when unset."""
        m = v2.build_master_list()
        baseline = v2.render_s2_prompt(m)
        monkeypatch.setenv("TORTOISE_LABEL_ORDER", "shuffle")
        a = v2.render_s2_prompt(m)
        b = v2.render_s2_prompt(m)
        assert a == b
        assert a != baseline  # the shuffle reorders the kind vocabulary


# ── #1695 Task 5: the classify-later stage (flag-on pipeline) ─────────────

CLASSIFY_SPEC = {
    "dev:issue": {"text": "dev:issue: A tracked work item (synonyms: ticket)",
                   "section": "objects", "description": "A tracked work item",
                   "synonyms": ["ticket"], "examples": [],
                   "nearMisses": ["dev:code"]},
    "dev:code": {"text": "dev:code: Source code that implements features",
                  "section": "objects", "description": "Source code",
                  "synonyms": [], "examples": [], "nearMisses": []},
    "core:plan": {"text": "core:plan: A plan state (commitment-state family)",
                   "section": "objects", "description": "A plan state",
                   "synonyms": [], "examples": [], "nearMisses": []},
    "core:workflow": {"text": "core:workflow: A reusable procedural sequence",
                       "section": "objects", "description": "A reusable sequence",
                       "synonyms": [], "examples": [], "nearMisses": []},
    "core:occurrence": {"text": "core:occurrence: A done-state event",
                         "section": "events", "description": "An occurrence",
                         "synonyms": [], "examples": [], "nearMisses": []},
    "statement": {"text": "statement: A durable belief or claim",
                   "section": "points", "description": "A claim",
                   "synonyms": [], "examples": [], "nearMisses": []},
}

_CLASSIFY_KEYWORDS = ("ticket", "code", "plan", "workflow", "occurrence", "claim")


class _KeywordEncoder:
    """One-hot fixture encoder shared with tests/test_kind_classifier.py."""

    def encode(self, texts):
        import numpy as np
        out = np.zeros((len(texts), len(_CLASSIFY_KEYWORDS)))
        for i, t in enumerate(texts):
            low = str(t).lower()
            for j, kw in enumerate(_CLASSIFY_KEYWORDS):
                if kw in low:
                    out[i, j] = 1.0
        return out, False


class _BoomEncoder:
    """Fail-open pin: encode() raises — the pipeline must never break."""

    def encode(self, texts):
        raise RuntimeError("embedder down")


def _stub_classifier(model=None, encoder=None, llm_tail=False):
    from tortoise.kind_classifier import KindClassifier
    from tortoise.kind_index import KindIndex
    return KindClassifier(encoder=encoder or _KeywordEncoder(),
                          index=KindIndex.build(CLASSIFY_SPEC,
                                                encoder=_KeywordEncoder(),
                                                persist=False),
                          model=model, llm_tail=llm_tail)


def _hand_built_master() -> dict:
    """The core-only golden's source of truth: a HAND-BUILT master list with
    FIXED dict order — zero dependency on installed packs or on the pack-
    manifest glob order (build_master_list()'s memory_granularity order
    follows the filesystem readdir order of packs/*/manifest.yaml, which
    differs across platforms: macOS HFS/APFS vs Linux ext4). The byte-pinned
    golden fixture is generated from THIS dict and nothing else, so the
    golden test is platform-independent. pack_kinds and chains are retained
    for build_master_list() shape parity — the core-only render omits them
    (asserted in test_core_only_render_byte_pinned_golden)."""
    return {
        "objects": {
            "core:Project": "A project",
            "core:WorkItem": "A unit of work",
            "core:document": "A document artifact",
            "core:tag": "A tag",
            "core:user": "A user",
            "core:skill": "A skill",
            "core:tool": "A tool, CLI, or utility",
            "core:agent": "An agent",
            "core:workflow": "A reusable procedural sequence",
            "core:agreement": "An agreement",
            "core:standard": "A standard, spec, or canonical reference",
            "core:other": "No fitting kind - the explicit uncertain bucket",
            "core:strategy": "A strategy state (commitment-state family)",
            "core:plan": "A plan state (commitment-state family)",
            "core:goal": "A goal state (commitment-state family)",
            "core:target": "A target state (commitment-state family)",
        },
        "subjects": {
            "core:organization": "An organization — a company, agency, or "
                                 "other collective entity",
            "core:team": "A team — a group of people working together toward "
                         "shared goals",
            "core:role": "A role — a defined function or position held by a "
                         "person or a team",
            "core:legalPerson": "A legal person — an entity with legal "
                                "standing (company, foundation, org)",
            "core:naturalPerson": "A natural person — an individual human "
                                  "being",
        },
        "points": {
            "statement": "A durable belief, claim, or proposition — the "
                          "extraction write kind",
        },
        "events": {
            "core:decision": "A commitment event — a choice made with "
                              "reasons that resolves confidence",
            "core:occurrence": "An occurrence — something that happened at a "
                                "point in time",
            "core:deployment": "A deployment event — a product or release "
                               "shipped to an environment",
            "core:review": "A review event — a review of work, code, plan, or "
                           "content",
            "core:meeting": "A meeting event — a gathering that changed or "
                            "confirmed state",
            "core:experiment": "An experiment event — a test or calibration "
                               "run with measured results",
            "core:friction": "A friction event — a discovered obstacle, pain "
                             "point, or workflow failure",
        },
        "pack_kinds": {
            # Representative only — the core-only render drops the whole
            # section (pack vocabulary is typed by the classify-later stage).
            "dev:issue": "A unit of tracked work",
        },
        "chains": {
            "epicToCode": {"path": ["epic", "issue", "code"],
                            "note": "Work decomposition"},
        },
        "user_personal_state": {
            "personal_best": "A personal record/achievement VALUE — times, "
                             "distances, scores, quantities ('my personal best "
                             "5K time is 27:12'). The VALUE is the fact; "
                             "retain it verbatim.",
            "schedule": "A recurring commitment VALUE — regular times, days, "
                         "frequencies ('gym at 6pm', 'standup at 9:30'). The "
                         "TIME is the fact; retain it verbatim.",
            "preference": "A stated preference/choice VALUE — likes, "
                           "dislikes, defaults, chosen options ('prefers dark "
                           "mode', 'coffee not tea'). The CHOICE is the fact; "
                           "retain it verbatim.",
        },
        # FIXED order (agent-ops, dev, marketing, pm, product-strategy) — the
        # golden pins THIS order; it is independent of the packs' readdir order.
        "memory_granularity": {
            "agent-ops": "Durable: the rule text, the situation that created it, "
                          "and the reasoning that supports or undermines it. "
                          "Ephemeral: rule mechanics, approval logistics, "
                          "tool-specific workarounds.",
            "dev": "Durable: root-cause analysis, the chosen fix approach vs "
                   "rejected alternatives, durable constraints (e.g. preserve "
                   "production semantics), and environment beliefs that affect "
                   "future work (e.g. subagents stall under load). Ephemeral: "
                   "issue/PR numbers, CI status, test counts, commit hashes, "
                   "tool workarounds.",
            "marketing": "Durable: campaign strategy — which "
                          "campaigns/channels/audiences are being pursued and "
                          "why, the positioning decisions, the reasoning, the "
                          "actual content pieces (they ARE the product), and "
                          "the metric snapshots showing whether a strategy is "
                          "working. Ephemeral: publishing mechanics, "
                          "scheduling logistics.",
            "pm": "Durable: plan/goal/target state — what was decided, what's "
                   "committed, the reasoning, and milestone outcomes. "
                   "Ephemeral: card/board status, sprint burndown, assignment "
                   "logistics, issue triage.",
            "product-strategy": "Durable: the productDelivery chain itself — "
                                 "which JTBDs/use-cases/features are being "
                                 "pursued, the chosen vs rejected options at "
                                 "each step, and the reasoning (what supports, "
                                 "undermines, tempers each choice). A customer "
                                 "/ competitor / market fact is durable if it "
                                 "changes a decision. Ephemeral: ticket status, "
                                 "sprint mechanics, meeting logistics.",
        },
    }


class TestClassifyStage:
    """The flag-on pipeline: stage order, kind-preservation re-stamp, slot
    re-key, sentinel terminal, census wiring; and the flag-off
    byte-identity regression."""

    def _run(self, monkeypatch, s2_body, s4_body=None, session_id="fixed-s1",
             kind_classifier=None, story="We shipped the ticket fix."):
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)

        def resp(system, user):
            if "STORY SUMMARIZER" in system:
                return story
            if "GRAPH MAPPER" in system:
                return json.dumps(s2_body)
            if "GAP REVIEWER" in system:
                return json.dumps(s4_body if s4_body is not None else {
                    "entities": [], "events": [], "points": [],
                    "operators": [], "chain_notes": [],
                    "link_before_create": []})
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        conv = [{"role": "user", "content": story}]
        return v2.extract_session_v2(MockModel(resp), conv, session_id=session_id,
                                     kind_classifier=kind_classifier)

    def test_flag_on_happy_path_assigns_pack_kinds(self, monkeypatch):
        """S2 emits pack-domain entities as 'unclassified'; the classifier
        assigns real kinds that land in the payload (no minted kinds)."""
        s2 = {"entities": [
            {"name": "the ticket fix", "kind": "unclassified",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"content": "the claim is durable",
                         "pointKind": "unclassified", "about_entities": []}]}
        out = self._run(monkeypatch, s2, kind_classifier=_stub_classifier())
        assert out["classify_later"]["enabled"] is True
        assert out["classify_later"]["s2"]["assigned_knn"] >= 1
        kinds = {e["kind"] for e in out["payload"]["entities"]}
        assert "dev:issue" in kinds  # the classifier's kind, not unclassified
        assert "unclassified" not in kinds
        assert not out["minted_kinds"]
        assert out["errors"] == []

    def test_pack_event_kind_survives_execute_embed(self):
        """FIX A candidate/write-gate alignment: a classifier-assigned pack
        event kind (pm eventKinds — declared but kindDefs-less) is writable
        at execute_embed — it survives unwritten-repaired (and is not flagged
        minted), while a genuinely minted kind still repairs + flags."""
        embed = {"entities": [], "events": [
            {"content": "a card was created",
             "eventKind": "pm:cardCreated", "about_entities": []},
            {"content": "totally minted",
             "eventKind": "totally:madeup", "about_entities": []},
        ], "points": [], "operators": [], "chain_notes": [],
            "link_before_create": []}
        res = v2.execute_embed(embed, {}, session_id="s1")
        kinds = {e["content"]: e["eventKind"] for e in res["payload"]["events"]}
        assert kinds["a card was created"] == "cardCreated", \
            "pack event kind survives execute_embed unwritten-repaired"
        assert kinds["totally minted"] == "occurrence"
        assert not any("pm:cardCreated" in m for m in res["minted_kinds"]), \
            "the writable pack event kind is not flagged minted"
        assert any("totally:madeup" in m for m in res["minted_kinds"])

    def test_pack_object_kind_survives_execute_embed(self):
        """FIX M candidate/write-gate alignment: a classifier-assigned pack
        object/document kind (dev:apiSpec, pm:milestone, marketing:keyword —
        declared but kindDefs-less, synthesized into the index's "objects"
        section) is writable at execute_embed — it survives un-repaired and
        is not flagged minted, while a genuinely minted kind still repairs
        + flags."""
        embed = {"entities": [
            {"name": "the api spec", "kind": "dev:apiSpec",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "the milestone", "kind": "pm:milestone",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "the keyword", "kind": "marketing:keyword",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "totally minted", "kind": "totally:madeup",
             "lifecycle": "created", "supersedes": None, "note": None},
        ], "events": [], "points": [], "operators": [],
            "chain_notes": [], "link_before_create": []}
        res = v2.execute_embed(embed, {}, session_id="s1")
        kinds = {e["name"]: e["kind"] for e in res["payload"]["entities"]}
        assert kinds["the api spec"] == "dev:apiSpec", \
            "synthesized object kind survives execute_embed un-repaired"
        assert kinds["the milestone"] == "pm:milestone"
        assert kinds["the keyword"] == "marketing:keyword"
        assert kinds["totally minted"] == "core:other"
        assert not any(m for m in res["minted_kinds"]
                       if "dev:apiSpec" in m or "pm:milestone" in m
                       or "marketing:keyword" in m), \
            "the writable pack object kinds are not flagged minted"
        assert any("totally:madeup" in m for m in res["minted_kinds"])
        # the report alone agrees: only the genuine minted kind is flagged
        clean = {"entities": [
            {"name": "the api spec", "kind": "dev:apiSpec"},
            {"name": "the milestone", "kind": "pm:milestone"},
            {"name": "the keyword", "kind": "marketing:keyword"},
        ], "events": [], "points": []}
        assert v2._minted_kind_report(clean) == []

    def test_point_item_never_assigned_pack_point_kind(self, monkeypatch):
        """FIX A: a point item is never assigned a pack point kind — the
        index's "points" section contains ONLY "statement", so the point
        classification is trivial (design doc) and the write gate never
        sees a pack point kind on a point."""
        from tortoise.value_extractor import _clear_kind_spec_cache, compile_kind_index_spec
        _clear_kind_spec_cache()
        spec = compile_kind_index_spec()
        points = {k for k, md in spec.items()
                  if md.get("section") == "points"}
        assert points == {"statement"}
        assert "dev:requirement" not in spec
        assert "dev:bug" not in spec

    def test_flag_off_byte_identical_no_telemetry_growth(self, monkeypatch):
        """kind_classifier=None + env unset → the pipeline is byte-
        identical: fixed session_id, canonical payload equality across runs,
        and the Layer-1 payload key set does NOT grow."""
        monkeypatch.delenv("TORTOISE_CLASSIFY_LATER", raising=False)
        r1 = self._run(monkeypatch, S2_FIXTURE)
        r2 = self._run(monkeypatch, S2_FIXTURE)
        assert r1["classify_later"]["enabled"] is False
        assert r1["classify_later"]["s2"] == {} \
            and r1["classify_later"]["union"] == {}
        p1, p2 = r1["payload"], r2["payload"]
        for skip in ("captured_at",):
            p1.pop(skip), p2.pop(skip)
        assert p1 == p2, "flag-off payloads must be canonically identical"
        assert set(p1) == {
            "schema_version", "session_id", "client_commit_id", "extractor",
            "summary", "story_arc", "provenance_refs", "sources",
            "entities", "points", "events", "operators", "supersessions",
            "telemetry"}

    def test_flag_off_prompts_byte_identical(self, monkeypatch):
        """The flag-off S2/S4 prompts are byte-identical to the base
        templates (the core-only variants are separate constants). The
        exact-fragment pins catch template regressions (e.g. a joined
        newline) that self-referential compares miss."""
        monkeypatch.delenv("TORTOISE_CLASSIFY_LATER", raising=False)
        assert v2.render_s2_prompt() == v2.render_s2_prompt()
        base = (v2.S2_TMPL
                .replace("{master_list}", v2._render_master(v2.build_master_list()))
                .replace("{chains_text}", v2._render_chains(v2.build_master_list()))
                .replace("{date_anchor}", v2._date_anchor(None, include_emission_rules=True))
                .replace("{output_contract}", v2.OUTPUT_CONTRACT))
        assert v2.render_s2_prompt() == base
        assert v2.OUTPUT_CONTRACT_CORE_ONLY != v2.OUTPUT_CONTRACT
        # exact-fragment pins (byte-regression guard for the templates)
        s2 = v2.render_s2_prompt()
        assert "OPERATOR REFERENCING (hard rule)\nOperators wire POINTS" in s2
        assert "- CHAINS — mapping must respect the chain positions" in s2
        assert "PACK-DOMAIN CONTENT" not in s2

    def test_kind_preservation_restamp_observable(self, monkeypatch):
        """S4 re-types an S2 classifier-typed entity as 'unclassified' → the
        re-stamp folds the duplicate and the S2 kind survives; the override
        is counted (census-observable)."""
        s2 = {"entities": [
            {"name": "the ticket fix", "kind": "unclassified",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [], "points": []}
        # S4 re-emits the SAME entity re-typed as unclassified (violating
        # the re-emit clause) — the re-stamp must fold it
        s4 = {"entities": [
            {"name": "the ticket fix", "kind": "unclassified",
             "lifecycle": "unchanged", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [], "points": []}
        out = self._run(monkeypatch, s2, s4_body=s4,
                        kind_classifier=_stub_classifier())
        assert out["classify_later"]["restamp_overrides"] >= 1
        entities = out["payload"]["entities"]
        assert len(entities) == 1, "no duplicate :Object on kind mismatch"
        assert entities[0]["kind"] == "dev:issue"
        assert any("re-typed by S4" in w for w in out["warnings"])

    def test_section_aware_freeze(self, monkeypatch):
        """The kind-freeze is section-aware: an entity named 'plan' with a
        classifier kind does NOT freeze a point with the same content."""
        s2 = {"entities": [
            {"name": "plan", "kind": "unclassified",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"content": "plan", "pointKind": "unclassified",
                         "about_entities": []}]}
        out = self._run(monkeypatch, s2, kind_classifier=_stub_classifier())
        # the entity freezes to core:plan; the POINT is classified
        # independently (statement — the only point kind) — never re-stamped
        # as core:plan by the entity's freeze
        ent = out["payload"]["entities"][0]
        pt = out["payload"]["points"][0]
        assert ent["kind"] == "core:plan"
        assert pt["pointKind"] == "statement"

    def test_embedder_down_fail_open(self, monkeypatch):
        """The embedder is down → the classifier falls back to best-core
        kinds; the pipeline never raises; census counts embedding_error."""
        s2 = {"entities": [
            {"name": "the ticket fix", "kind": "unclassified",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [], "points": []}
        out = self._run(monkeypatch, s2,
                        kind_classifier=_stub_classifier(encoder=_BoomEncoder()))
        assert out["payload"]["entities"][0]["kind"] == "core:other"
        assert out["error_census"]["embedding_error"] >= 1
        assert out["classify_later"]["s2"]["embedding_errors"] >= 1

    def test_adjudication_fail_falls_back(self, monkeypatch):
        """The LLM adjudication tail fails → kNN top-1 fallback + census
        classify_error; kinds still land."""
        class BoomModel(MockModel):
            def complete(self, *, system, user, max_tokens=None):
                if "KIND ADJUDICATOR" in system:
                    raise RuntimeError("adjudicator down")
                return "x"

        s2 = {"entities": [
            {"name": "the plan workflow", "kind": "unclassified",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [], "points": []}
        clf = _stub_classifier(model=BoomModel([]), llm_tail=True)
        out = self._run(monkeypatch, s2, kind_classifier=clf)
        kinds = {e["kind"] for e in out["payload"]["entities"]}
        assert "unclassified" not in kinds
        assert out["error_census"]["classify_error"] >= 1

    def test_unclassified_terminal_resolved_at_write(self, monkeypatch):
        """A below-floor item keeps the sentinel in the list; execute_embed
        repairs it to the best core kind + warning; the census counts the
        terminal."""
        s2 = {"entities": [
            {"name": "xyzzy no keyword", "kind": "unclassified",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [], "points": []}
        out = self._run(monkeypatch, s2, kind_classifier=_stub_classifier())
        assert out["payload"]["entities"][0]["kind"] == "core:other"
        assert any("unclassified" in w for w in out["warnings"])
        assert out["error_census"]["unclassified_terminal"] >= 1

    def test_s4_reemit_clause_in_core_only_template(self):
        """The S4 core-only template teaches the re-emit clause; the flag-
        off template never mentions it."""
        assert "S2 ITEMS ARE TYPED" in v2.S4_TMPL_CORE_ONLY
        assert "classifier" in v2.S4_TMPL_CORE_ONLY.lower()
        assert "S2 ITEMS ARE TYPED" not in v2.S4_TMPL

    def test_core_only_render_golden_structure(self):
        """The core-only render is a pinned structural golden: kind groups
        in order, no pack kinds, no chains; hint blocks retained."""
        m = v2.build_master_list()
        r = v2._render_master_core_only(m)
        assert r.startswith("MASTER LIST — the closed vocabulary (CORE ONLY)")
        order = [r.index(s) for s in
                 ("OBJECTS (core)", "SUBJECTS (core)", "POINTS", "EVENTS",
                  "USER-PERSONAL-STATE VOCABULARY", "MEMORY GRANULARITY",
                  "STATE-VALUE CARVE-OUT")]
        assert order == sorted(order), "section order is pinned"
        assert "PACK KINDS" not in r and "CHAINS" not in r
        assert "product-strategy:product" not in r
        assert r == v2._render_master_core_only(m)  # deterministic (no shuffle)

    def test_core_only_render_byte_pinned_golden(self):
        """Task 5's byte-pinned golden fixture: the core-only master render
        equals the committed golden byte-for-byte. The golden is rendered
        from a HAND-BUILT master (fixed dict order — see _hand_built_master),
        never from the installed packs, so the test is platform-independent
        (build_master_list()'s memory-granularity order follows the pack-
        manifest glob order, which differs between macOS and Linux). Also
        pins determinism (render twice → equal) and the absence of the
        PACK KINDS / CHAINS sections (the classify-later split).

        FIX K (cycle 3): production coverage — the byte-pin never saw
        build_master_list()'s readdir-order memory_granularity. Render BOTH
        the fixture and the production master; assert byte-equality of every
        section EXCEPT MEMORY GRANULARITY, whose entries must be
        SET-EQUAL (order-independent) to the golden's."""
        m = _hand_built_master()
        r = v2._render_master_core_only(m)
        assert r == v2._render_master_core_only(m)  # deterministic (no shuffle)
        assert "PACK KINDS" not in r and "CHAINS" not in r
        golden = (Path(__file__).resolve().parent / "fixtures"
                  / "core_only_master.golden.txt")
        assert golden.read_text(encoding="utf-8") == r, \
            "core-only master render drifted from the golden fixture " \
            "(regenerate via _hand_built_master: see the golden test docstring)"

        # FIX K: production-render coverage — the granularity block is the
        # ONLY readdir-order-dependent section; everything else must be
        # byte-identical to the golden, and the granularity entries must be
        # set-equal (same entries, any order).
        marker = "MEMORY GRANULARITY (what to keep, what to strip)"
        carve = "\nSTATE-VALUE CARVE-OUT"

        def _split(render: str):
            head, sep, tail = render.partition(marker)
            assert sep, "granularity section present in the render"
            g_lines, _, rest = tail.partition(carve)
            return head, sorted(g_lines.split("\n")), carve + rest

        prod = v2._render_master_core_only(v2.build_master_list())
        p_head, p_g, p_rest = _split(prod)
        g_head, g_g, g_rest = _split(r)
        assert p_head == g_head, "production render (pre-granularity) must " \
            "match the golden byte-for-byte"
        assert p_rest == g_rest, "production render (post-granularity) must " \
            "match the golden byte-for-byte"
        assert p_g == g_g, "production granularity entries must be set-equal " \
            "to the golden (readdir order differs across platforms)"
        assert len(p_g) >= 4, "production render carries the installed packs"

    def test_core_only_ups_block_keeps_not_kinds_guard(self):
        """The core-only USER-PERSONAL-STATE block carries the E2 (#1534)
        guard — the vocabulary is a classification hint, never a kind."""
        r = v2._render_master_core_only(v2.build_master_list())
        assert "USER-PERSONAL-STATE VOCABULARY (Tier-A classification " \
               "hint — the VALUE is the fact, retain verbatim; these are " \
               "NOT kinds: do NOT emit them as entity/event/point kinds)" in r

    def test_core_only_s2_prompt_pack_namespaces_dynamic(self):
        """The core-only S2 prompt's pack-namespace list is DYNAMIC —
        derived from the INSTALLED packs (never hardcoded: epistemic-team
        is not installed, and a future pack must route to unclassified)."""
        m = v2.build_master_list()
        prompt = v2.render_s2_prompt(m, core_only=True)
        ns = sorted({k.rsplit(":", 1)[0] + ":" for k in m["pack_kinds"]})
        assert ns, "installed pack namespaces must be non-empty"
        assert "({})".format("/".join(ns)) in prompt
        assert "epistemic-team:" not in prompt
        assert "{pack_namespaces}" not in prompt, "placeholder must be filled"

    def test_core_only_contract_sentinel_only_top_level(self):
        """The sentinel lands on the TOP-LEVEL entities/events/points kind
        fields — never on the participant-slot kind fields (the slots
        schema keeps plain str; the write path would otherwise silently
        undo the contract's slots advertisement)."""
        c = v2.OUTPUT_CONTRACT_CORE_ONLY
        # top-level fields widen
        assert '"name": str, "kind": str|"unclassified", "lifecycle"' in c
        assert '"eventKind": str|"unclassified",' in c
        assert '"pointKind": "statement"|"unclassified",' in c
        # slot schemas keep plain str — never advertise the sentinel
        assert '"subject": [{"name": str, "kind": str' in c
        assert '"object": [{"name": str, "kind": str' in c
        assert '"event": [{"name": str, "kind": str' in c
        assert '"kind": str|"unclassified", "confidence"' not in c

    def test_core_only_derivations_anchors_present_in_base(self):
        """The _CORE_ONLY constants are .replace-derivations of the base
        templates/contract; a future base edit that breaks an anchor would
        silently no-op the derives (a half-applied flag-on template). These
        pins make that fail loudly in CI."""
        # S2 anchors
        assert ("- CHAINS — mapping must respect the chain positions "
                "(WARN, then TRY TO REPAIR):\n"
                "{chains_text}\n"
                "  If a mapping would connect across a chain in a way that "
                "violates it, WARN in\n"
                "  chain_notes and TRY TO REPAIR by re-mapping toward the "
                "nearest valid chain\n"
                "  position. NEVER invent entities to satisfy a chain."
                in v2.S2_TMPL)
        assert ("MASTER LIST\n{master_list}\n\nCONDENSED SEMANTIC CORE"
                in v2.S2_TMPL)
        # S4 anchors
        assert ("MASTER LIST (same closed vocabulary as S2 — no minted "
                "kinds)\n{master_list}\n\n"
                "CHAINS\n{chains_text}\n\nS1 STORY" in v2.S4_TMPL)
        assert ("- Re-emit the S2 items you keep, corrected where the "
                "search results show they\n"
                "  already exist (lifecycle changed/unchanged + supersedes "
                "= the existing id)." in v2.S4_TMPL)
        # OUTPUT_CONTRACT anchors
        assert '"name": str, "kind": str, "lifecycle"' in v2.OUTPUT_CONTRACT
        assert '"eventKind": str,' in v2.OUTPUT_CONTRACT
        assert '"pointKind": "statement",' in v2.OUTPUT_CONTRACT
        # FIX D (cycle 3): the base's advisory "TRY TO REPAIR" chain_notes
        # bullet exists EXACTLY ONCE in S4_TMPL, and the core-only
        # derivation swaps it for the deterministic-enforcement wording
        # ("CHAIN ENFORCEMENT IS DETERMINISTIC" must not be contradicted).
        assert v2.S4_TMPL.count(
            "- chain_notes: flag violations, TRY TO REPAIR toward the nearest "
            "valid chain\n  position, never invent entities.") == 1
        assert "deterministic post-extraction" in v2.S4_TMPL_CORE_ONLY
        assert "do NOT attempt repairs yourself" in v2.S4_TMPL_CORE_ONLY
        assert "TRY TO REPAIR toward the nearest valid chain" not in \
            v2.S4_TMPL_CORE_ONLY
        assert "TRY TO REPAIR toward the nearest valid chain" in v2.S4_TMPL

    def test_unclassified_constant_shared_with_classifier(self):
        """The sentinel constant must not drift between modules (a mismatch
        would silently break the classify-later sentinel round-trip)."""
        from tortoise.kind_classifier import UNCLASSIFIED as CL_UNCLASSIFIED

        assert v2.UNCLASSIFIED == CL_UNCLASSIFIED == "unclassified"

    def test_sentinel_never_flagged_minted(self):
        """Below-floor items carry kind='unclassified' through to the
        minted-kind report — the reserved sentinel is a terminal, not a
        minted kind (final-review P2 false positive)."""
        embed = {"entities": [
            {"name": "xyzzy", "kind": "unclassified",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [{"content": "e", "eventKind": "unclassified"}],
            "points": [{"content": "p", "pointKind": "unclassified"}],
            "operators": [], "chain_notes": [], "link_before_create": []}
        assert v2._minted_kind_report(embed) == []
        # a real minted kind is still flagged (the report is not gutted)
        bad = dict(embed)
        bad["entities"] = [{"name": "x", "kind": "worktree",
                             "lifecycle": "created", "supersedes": None,
                             "note": None}]
        assert any("worktree" in m for m in v2._minted_kind_report(bad))

    def test_point_kind_report_agrees_with_point_gate(self):
        """FIX P: the report's points lane agrees with the point write gate
        — pack point kinds WITH kindDefs (dev:requirement,
        product-strategy:useCase/...) sit in the master's pack_kinds
        (the master-wide ``master_kind_forms`` bare forms would hide them)
        but the gate repairs them to statement — the report must FLAG them."""
        embed = {"entities": [], "events": [],
                 "points": [
                     {"content": "p1",
                      "pointKind": "dev:requirement"},
                     {"content": "p2",
                      "pointKind": "product-strategy:useCase"},
                     {"content": "p3", "pointKind": "statement"}],
                 "operators": [], "chain_notes": [],
                 "link_before_create": []}
        minted = v2._minted_kind_report(embed)
        assert any("dev:requirement" in m for m in minted), minted
        assert any("product-strategy:useCase" in m for m in minted), minted
        assert not any("statement" in m for m in minted)
        # and execute_embed's point gate agrees: those kinds repair
        res = v2.execute_embed(embed, {}, session_id="s1")
        pk = {p["content"]: p["pointKind"] for p in res["payload"]["points"]}
        assert pk["p1"] == "statement" and pk["p2"] == "statement"

    def test_slot_survives_for_pack_object_kind(self):
        """FIX M slot-lane consistency (reviewer P2): _clean_slots' subject/
        object lane gates against the SAME widened object vocabulary as
        execute_embed's entity gate — a slot referencing an emitted
        dev:apiSpec entity keeps its kind and resolves (previously it was
        repaired to core:other and dropped, silently losing the relation
        for exactly the synthesized kinds FIX M preserves)."""
        embed = {"entities": [
            {"name": "the api spec", "kind": "dev:apiSpec",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"content": "the api spec is v2",
                         "pointKind": "statement",
                         "about_entities": ["the api spec"],
                         "slots": {"subject": [
                             {"name": "the api spec",
                              "kind": "dev:apiSpec",
                              "confidence": 0.9}]}}]}
        res = v2.execute_embed(embed, {}, session_id="s1")
        pt = res["payload"]["points"][0]
        assert pt["slots"]["subject"][0]["kind"] == "dev:apiSpec", \
            "the slot kind survives _clean_slots + _resolve_slot_refs"
        assert not any("minted slot kind" in w for w in res["warnings"]), \
            res["warnings"]

    def test_fold_never_drops_different_non_sentinel_kind(self):
        """A same-name duplicate carrying a DIFFERENT non-sentinel kind is
        a distinct (name, kind) :Object (Layer-1) — the name-collision
        fold must NOT delete it AND the S2 re-stamp must NOT clobber it
        (only sentinel/missing/identical duplicates fold; only LOST kinds
        re-stamp; cycle-3 P2)."""
        def ent(name, kind):
            return {"name": name, "kind": kind, "lifecycle": "created",
                    "supersedes": None, "note": None}

        merged = {"entities": [ent("plan", "core:plan"),
                                ent("plan", "core:goal")],
                  "events": [], "points": [], "operators": [],
                  "chain_notes": [], "link_before_create": []}
        warnings: list[str] = []
        n = v2._restamp_s2_kinds(merged, {"entities:plan": "core:plan"},
                                 warnings)
        assert len(merged["entities"]) == 2, \
            "the different-kind duplicate must survive the fold"
        kinds = {e["kind"] for e in merged["entities"]}
        assert kinds == {"core:plan", "core:goal"}, \
            "the S2-registered copy keeps core:plan; the re-typed survivor " \
            "keeps its original kind (core:goal)"
        assert not any("folded into the S2" in w for w in warnings)
        assert any("preserved as distinct" in w for w in warnings), \
            "the preserved re-typed :Object is announced (observable census)"
        assert n == 0, "a valid non-sentinel kind is never re-stamped"

    def test_clean_slots_sentinel_no_minted_warning(self):
        """FIX G: a participant slot kind 'unclassified' (the classify-later
        sentinel) is carried WITHOUT the spurious 'minted slot kind'
        repair warning — the sentinel is a terminal, not a minted kind
        (as _rekey_slots already treats it)."""
        warnings: list[str] = []
        master = v2.build_master_list()
        slots = v2._clean_slots(
            {"subject": [{"name": "x", "kind": "unclassified",
                            "confidence": 0.9}]},
            warnings, "ctx", master)
        assert not any("minted slot kind" in w for w in warnings), warnings
        assert slots == {"subject": [
            {"name": "x", "kind": "unclassified", "confidence": 0.9}]}
        # a genuinely minted slot kind still warns + repairs (not gutted)
        warnings2: list[str] = []
        slots2 = v2._clean_slots(
            {"subject": [{"name": "x", "kind": "worktree",
                            "confidence": 0.9}]},
            warnings2, "ctx", master)
        assert any("minted slot kind" in w for w in warnings2)
        assert slots2["subject"][0]["kind"] == "core:other"

    def test_no_slot_kind_ever_carries_sentinel(self):
        """FIX O: after _clean_slots + _resolve_slot_refs, NO payload slot
        ever carries 'unclassified' — subject/object sentinel slots fail
        closed at _resolve_slot_refs (no emitted entity resolves them),
        and an EVENT-role sentinel slot (which passes through untouched)
        is repaired to core:occurrence SILENTLY (the sentinel is only
        advertised for top-level fields; 'sentinel never written' holds for
        slot kinds too)."""
        warnings: list[str] = []
        master = v2.build_master_list()
        slots = v2._clean_slots(
            {"subject": [{"name": "s", "kind": "unclassified",
                            "confidence": 0.8}],
             "object": [{"name": "o", "kind": "unclassified",
                          "confidence": 0.8}],
             "event": [{"name": "e", "kind": "unclassified",
                         "confidence": 0.8}]},
            warnings, "ctx", master)
        assert not any("minted slot kind" in w for w in warnings), warnings
        resolved = v2._resolve_slot_refs(slots, set(), warnings, "ctx")
        assert resolved is not None and "event" in resolved
        # the event slot survives but with the fallback kind — never the
        # sentinel
        assert resolved["event"][0]["kind"] == "core:occurrence"
        assert "subject" not in resolved and "object" not in resolved, \
            "sentinel subject/object slots fail closed downstream"
        # sweeping assertion: no slot kind across any role is the sentinel
        for refs in resolved.values():
            for r in refs:
                assert r["kind"] != "unclassified"

    def test_rekey_slots_skips_sentinel_entity_kind(self):
        """An entity still carrying the 'unclassified' sentinel must not
        copy it into slot kinds (_clean_slots would otherwise emit a
        spurious 'minted slot kind' warning for the sentinel)."""
        embed = {"entities": [
            {"name": "xyzzy", "kind": "unclassified",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"content": "the claim", "pointKind": "statement",
                         "about_entities": ["xyzzy"],
                         "slots": {"subject": [
                             {"name": "xyzzy", "kind": "core:other",
                              "confidence": 0.9}]}}]}
        n = v2._rekey_slots(embed)
        assert n == 0, "the sentinel is never copied into a slot kind"
        slot = embed["points"][0]["slots"]["subject"][0]
        assert slot["kind"] == "core:other"

    def test_slot_rekey_follows_classified_kind(self):
        """Participant slot kinds follow the classified entity kind."""
        embed = {"entities": [
            {"name": "the ticket fix", "kind": "dev:issue",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"content": "the claim", "pointKind": "statement",
                         "about_entities": ["the ticket fix"],
                         "slots": {"subject": [
                             {"name": "the ticket fix",
                              "kind": "core:other", "confidence": 0.9}]}}]}
        n = v2._rekey_slots(embed)
        assert n == 1
        slot = embed["points"][0]["slots"]["subject"][0]
        assert slot["kind"] == "dev:issue"

    def test_flag_on_via_env_toggle_only(self, monkeypatch):
        """The env toggle alone (no injected classifier) enables the
        classify-later pipeline — the single choke point. The default
        classifier builder is monkeypatched so the test never waits on a
        real index build (bge cold load / TF-IDF degrade)."""
        monkeypatch.setenv("TORTOISE_CLASSIFY_LATER", "1")
        monkeypatch.setattr(v2, "_default_kind_classifier",
                            lambda model: _stub_classifier())
        s2 = {"entities": [
            {"name": "the ticket fix", "kind": "unclassified",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [], "points": []}
        out = self._run(monkeypatch, s2)  # no injected classifier
        assert out["classify_later"]["enabled"] is True
        assert out["payload"]["entities"][0]["kind"] == "dev:issue"

    def test_env_toggle_case_insensitive(self, monkeypatch):
        """FIX B: TORTOISE_CLASSIFY_LATER matches case-insensitively —
        True/TRUE/ON/yes all enable; only unset/0/off disable."""
        for val in ("1", "true", "TRUE", "True", "yes", "on", "ON"):
            monkeypatch.setenv("TORTOISE_CLASSIFY_LATER", val)
            assert v2._classify_later_enabled() is True, val
        monkeypatch.setenv("TORTOISE_CLASSIFY_LATER", "0")
        assert v2._classify_later_enabled() is False
        monkeypatch.setenv("TORTOISE_CLASSIFY_LATER", "off")
        assert v2._classify_later_enabled() is False
        monkeypatch.delenv("TORTOISE_CLASSIFY_LATER")
        assert v2._classify_later_enabled() is False

    def test_label_order_toggle_case_insensitive(self, monkeypatch):
        """FIX B: TORTOISE_LABEL_ORDER=shuffle matches case-insensitively
        (SHUFFLE/Shuffle enable the A′ label-order hook)."""
        monkeypatch.delenv("TORTOISE_LABEL_ORDER", raising=False)
        assert v2._label_order_rng("s") is None
        for val in ("shuffle", "SHUFFLE", "Shuffle"):
            monkeypatch.setenv("TORTOISE_LABEL_ORDER", val)
            assert v2._label_order_rng("s") is not None, val

    def test_numeric_name_survives_union_classify(self, monkeypatch):
        """FIX C: a numeric (non-str) entity name must not raise
        AttributeError in the union-classify block (_identity_key /
        _restamp_s2_kinds / _rekey_slots coerce via str())."""
        s2 = {"entities": [
            {"name": 42, "kind": "unclassified",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [], "points": []}
        out = self._run(monkeypatch, s2, kind_classifier=_stub_classifier())
        assert out["errors"] == [], "no AttributeError through the union pass"
        assert out["payload"]["entities"][0]["name"] == "42"

    def test_norm_coerces_non_str(self):
        """FIX C: _norm coerces non-str input instead of raising (the
        falsy-or semantics mirror _collect_classify_items' defensive str():
        a falsy value coerces to '')."""
        assert v2._norm(42) == "42"
        assert v2._norm(None) == ""
        assert v2._norm(0) == ""  # falsy-or: 0 or "" → ""
        assert v2._norm("  Mixed CASE ") == "mixed case"


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


# ── E4 (#1536): S4 merges-not-replaces — unit + orchestrator ───────────────

def _pt(content: str) -> dict:
    return {"content": content, "pointKind": "statement",
            "about_entities": [], "slots": None}


class TestS4Merge:
    def test_s2_item_s4_dropped_survives(self):
        """E2E-5 core regression: an early-asserted S2 point S4 fails to
        re-emit must NOT be lost (complete_list=s4 dropped it pre-E4)."""
        s2 = {"entities": [], "events": [], "points": [
            _pt("my personal best 5K time is 27:12")],
            "operators": [], "chain_notes": [], "link_before_create": []}
        s4 = {"entities": [], "events": [], "points": [
            _pt("single-flash with granularity is the working path")],
            "operators": [], "chain_notes": [], "link_before_create": []}
        merged = v2.merge_embed_lists(s2, s4)
        contents = {p["content"] for p in merged["points"]}
        assert "my personal best 5K time is 27:12" in contents
        assert "single-flash with granularity is the working path" in contents

    def test_s4_correction_wins_on_conflict(self):
        """Same identity key in S2 and S4 → the reviewer's version (graph-
        informed lifecycle/supersedes) wins, and S4's name normalization
        matches S2's (case/space drift is identity, not a new item)."""
        s2 = {"entities": [{"name": "strategy  A", "kind": "core:plan",
                            "lifecycle": "created", "supersedes": None,
                            "note": None}], "events": [], "points": [],
              "operators": [], "chain_notes": [], "link_before_create": []}
        s4 = {"entities": [{"name": "Strategy A", "kind": "core:plan",
                            "lifecycle": "unchanged", "supersedes": None,
                            "note": "already in graph"}], "events": [],
              "points": [], "operators": [], "chain_notes": [],
              "link_before_create": []}
        merged = v2.merge_embed_lists(s2, s4)
        assert len(merged["entities"]) == 1
        assert merged["entities"][0]["lifecycle"] == "unchanged"
        assert merged["entities"][0]["name"] == "Strategy A"

    def test_identical_s4_is_identity(self):
        assert v2.merge_embed_lists(S2_FIXTURE, S2_FIXTURE) == S2_FIXTURE

    def test_empty_s4_returns_s2(self):
        assert v2.merge_embed_lists(S2_FIXTURE, {}) == S2_FIXTURE

    def test_gap_items_appended_after_s2(self):
        def ent(name):
            return {"name": name, "kind": "core:plan", "lifecycle": "created",
                    "supersedes": None, "note": None}
        s2 = {"entities": [ent("A")], "events": [], "points": [],
              "operators": [], "chain_notes": [], "link_before_create": []}
        s4 = {"entities": [ent("B")], "events": [], "points": [],
              "operators": [], "chain_notes": [], "link_before_create": []}
        merged = v2.merge_embed_lists(s2, s4)
        assert [e["name"] for e in merged["entities"]] == ["A", "B"]

    def test_operator_dedup_includes_target_edge(self):
        """Two MITIGATES with identical src/dst but different target edges are
        distinct; a re-emitted operator with the SAME edge collapses (S4's
        strength wins)."""
        op1 = {"src": "p", "dst": "e", "op_type": "MITIGATES",
               "target_edge": {"src": "p", "dst": "e", "op_type": "IMPL"},
               "strength": 0.3}
        op2 = {"src": "p", "dst": "e", "op_type": "MITIGATES",
               "target_edge": {"src": "p", "dst": "e", "op_type": "NAND"},
               "strength": 0.5}
        s2 = {"entities": [], "events": [], "points": [],
              "operators": [op1], "chain_notes": [], "link_before_create": []}
        s4 = {"entities": [], "events": [], "points": [],
              "operators": [op1, op2], "chain_notes": [], "link_before_create": []}
        merged = v2.merge_embed_lists(s2, s4)
        assert len(merged["operators"]) == 2          # op1 collapsed, op2 added
        assert any(o["strength"] == 0.3 for o in merged["operators"])

    def test_none_sections_and_non_dict_entries_skipped(self):
        s2 = {"entities": None, "events": [{"content": "E"}], "points": [],
              "operators": None, "chain_notes": [], "link_before_create": []}
        s4 = {"entities": [None, {"name": "X", "kind": "core:plan",
                                  "lifecycle": "created", "supersedes": None,
                                  "note": None}], "events": [], "points": [],
              "operators": [], "chain_notes": [], "link_before_create": []}
        merged = v2.merge_embed_lists(s2, s4)
        assert [e["name"] for e in merged["entities"]] == ["X"]
        assert merged["events"][0]["content"] == "E"   # S2 event survived

    def test_stats_recorded(self):
        s2 = {"entities": [], "events": [], "points": [_pt("A"), _pt("B")],
              "operators": [], "chain_notes": [], "link_before_create": []}
        s4 = {"entities": [], "events": [], "points": [_pt("A"), _pt("C")],
              "operators": [], "chain_notes": [], "link_before_create": []}
        merged = v2.merge_embed_lists(s2, s4)
        stats = v2._s4_merge_stats(s2, s4, merged)
        assert stats == {"s2_items": 2, "s4_items": 2, "merged_items": 3,
                         "corrected_by_s4": 1, "kept_from_s2": 1,
                         "added_by_s4": 1}


class TestE4Orchestrator:
    def test_s4_drop_does_not_lose_s2_point(self, monkeypatch):
        """The E2E-5 scenario at pipeline level: S2 extracts the early-asserted
        5K fact; S4's gap-review response omits it; the fact must still reach
        the Layer-1 payload and the s4_merge stats must show the keep."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)
        s2_body = {"entities": [], "events": [],
                   "points": [_pt("my personal best 5K time is 27:12")],
                   "operators": [], "chain_notes": [], "link_before_create": []}
        s4_body = {"entities": [], "events": [],
                   "points": [_pt("single-flash with granularity is the working path")],
                   "operators": [], "chain_notes": [], "link_before_create": []}

        def resp(system, user):
            if "STORY SUMMARIZER" in system:
                return "The user stated their 5K record early in the session."
            if "GAP REVIEWER" in system:
                return json.dumps(s4_body)
            return json.dumps(s2_body)

        conv = [{"role": "user", "content": "my personal best 5K time is 27:12"},
                {"role": "assistant", "content": "that's a great time"}]
        out = v2.extract_session_v2(MockModel(resp), conv)
        contents = {p["content"] for p in out["payload"]["points"]}
        assert "my personal best 5K time is 27:12" in contents
        assert out["stats"]["s4_merge"]["kept_from_s2"] == 1
        assert out["stats"]["s4_merge"]["added_by_s4"] == 1

    def test_s4_empty_keeps_s2_with_warning(self, monkeypatch):
        """Existing graceful degradation preserved: S4 empty → S2 stands + warning."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)

        def resp(system, user):
            if "STORY SUMMARIZER" in system:
                return "We believed X."
            if "GAP REVIEWER" in system:
                return json.dumps({})   # empty list
            return json.dumps(S2_FIXTURE)

        conv = [{"role": "user", "content": "we decided X"}]
        out = v2.extract_session_v2(MockModel(resp), conv)
        assert any("kept S2 output" in w for w in out["warnings"])
        assert out["payload"]["points"]   # S2 output embedded

    def test_s4_exception_keeps_s2_with_error(self, monkeypatch):
        """Existing failure path preserved: S4 raises → S2 stands + error.
        Sleep patched (M3 precedent): an unknown-class exception is
        transient-retried with backoff; real sleeps would slow the test."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setattr(v2.time, "sleep", lambda _: None)

        class BoomModel(MockModel):
            def complete(self, *, system, user, max_tokens=None):
                if "GAP REVIEWER" in system:
                    raise RuntimeError("s4 exploded")
                return "We believed X." if "STORY SUMMARIZER" in system \
                    else json.dumps(S2_FIXTURE)

        conv = [{"role": "user", "content": "we decided X"}]
        out = v2.extract_session_v2(BoomModel([]), conv)
        assert any("S4 failed" in e for e in out["errors"])
        assert out["payload"]["points"]   # S2 output embedded


def test_parse_json_handles_markdown_fences():
    """Pilot #1549 research: the model intermittently wraps the S2/S4 embed
    list in ```json code fences — the v2 strict regex reported 'no JSON block
    in output' for PERFECT JSON, driving the 666-parse_error census. The
    robust parser strips the fences."""

    from tortoise.extractor_v2 import _parse_json
    fenced = '```json\n{"entities": [], "points": [{"content": "x"}]}\n```'
    r = _parse_json(fenced)
    assert r["points"][0]["content"] == "x"
    prose_fenced = 'Here you go:\n```json\n{"a": 1}\n```\nDone!'
    assert _parse_json(prose_fenced) == {"a": 1}
    # pure prose still raises -> the caller's parse-retry path
    try:
        _parse_json("I cannot produce that.")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ── #1787 Task 2: S2/S4 cap raise 8000→16000 — dense-list completeness ──────

def test_s2_s4_cap_raised_to_16k_completes_dense_list(monkeypatch):
    """#1787 — a dense-session embed list that overflows the old 8000 cap
    must complete in full at the 16000 default: no partial_parse, no tail
    loss, and every emitted point lands in the payload. The mock is
    cap-aware: <=8000 → truncated JSON (finish=length), >8000 → full list."""
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)
    dense_points = [
        {"content": f"durable claim number {i} with a verbatim quote "
                    f"\"{'word ' * 40}\" and search_keys [\"k{i}\", \"k{i}b\"]",
         "pointKind": "statement", "about_entities": [f"entity-{i}"],
         "quote": f"quote {i}: " + ("lorem ipsum dolor " * 25),
         "search_keys": [f"k{i}", f"k{i}b"], "tier": "B",
         "slots": {"subject": [{"name": f"entity-{i}", "kind": "core:thing",
                                "confidence": 0.9}]}}
        for i in range(45)
    ]
    full = json.dumps({"entities": [{"name": f"entity-{i}", "kind": "core:thing",
                                     "lifecycle": "created", "supersedes": None}
                                    for i in range(45)],
                       "points": dense_points,
                       "events": [], "operators": [], "chain_notes": [],
                       "link_before_create": []})
    # calibration asserts — the fixture must BOTH overflow 8K under real
    # tokenization (or the test proves plumbing on a sub-8K fixture) AND FIT
    # the 16000 default. 45 points = 47,577 bytes → ≥ 11.9K tokens at the
    # pessimistic 4 chars/token bound (clears 8192) and ≤ 13.6K at 3.5
    # chars/token (fits 16K) — inside [8K, 16K], the observed reval band:
    assert len(full.encode("utf-8")) // 4 >= 8192, \
        "fixture too small: the 45-point dense list must exceed 8K tokens " \
        "(raise point count / quote length until the 4 chars/token bound clears)"
    assert len(full.encode("utf-8")) // 3.5 <= 14000, \
        "fixture too dense: the 45-point list must FIT the 16000 default " \
        "(~3.5 chars/token packing; shrink point count / quote length until " \
        "the upper bound clears — the observed reval lists are 8-16K, which " \
        "is what 16K claims to cover)"
    # the truncated form: cut GENUINELY mid-points-list, inside the points
    # array at an item boundary after point k — leaving the array + outer
    # object UNTERMINATED (rung-3 repair's `+ "}"` closers then cannot produce
    # valid JSON, so rung 4 `_longest_valid_prefix` must recover the head).
    k = 40
    points_json = json.dumps(dense_points)
    depth, closed = 0, 0
    boundary = len(points_json)
    for i, ch in enumerate(points_json):  # walk to the k-th point's close
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                closed += 1
                if closed == k:
                    boundary = i + 1
                    break
    truncated = full[:full.index('"points":') + len('"points":')] \
        + points_json[:boundary]

    class CapAwareModel:
        def __init__(self):
            self.captured = []
            self.last_finish_reason = "stop"
        def complete(self, *, system, user, max_tokens=None):
            self.captured.append(max_tokens)
            self.last_finish_reason = "length" if max_tokens and max_tokens <= 8000 \
                else "stop"
            return truncated if max_tokens and max_tokens <= 8000 else full

    m = CapAwareModel()
    stats = {"llm": {}}
    out = v2.run_s2(m, story="a very dense session story " * 200, stats=stats)
    assert v2._S2_S4_MAX_TOKENS > 8000              # the raise happened
    assert m.captured[-1] == v2._S2_S4_MAX_TOKENS   # the default cap is used
    assert len(out["points"]) == 45                 # full list, no tail loss
    # clean at 16K: run_s2 returns the RAW parsed embed dict — there is NO
    # `error_census` key on it. The partial_parse census bump lives in the
    # S2/S4 stage callers, keyed on stats["partial"] — assert the same
    # signal the callers check:
    assert stats.get("partial") is not True          # clean at 16K
    # #2134 backstop re-pin (Task 0 Step 5): force the OLD cap through
    # _complete_parsed directly WITH the escalation knob monkeypatched to
    # <= base — an un-escalatable truncation is RESIDUAL fail-loud (P2-15:
    # never a silent rung-4 partial of the truncating attempt; the env range
    # [16000..64000] makes esc=8000 unreachable via env — the helper is the
    # only lever).
    monkeypatch.setattr(v2, "_extractor_escalation_tokens", lambda b: 8000)
    m2 = CapAwareModel()
    stats2 = {"llm": {}}
    with pytest.raises(ValueError) as ei:
        v2._complete_parsed(m2, "sys", "usr", max_tokens=8000,
                            stats=stats2)
    assert ei.value.truncated is True   # fail-loud, classed truncated
    assert stats2.get("partial") is not True  # never a silent partial


def test_s2_s4_census_clean_at_16k_through_session(monkeypatch):
    """#1787 P2-12 — MANDATORY mirror of the extract_session_v2-level census
    test at the NEW cap: a dense session through extract_session_v2 with a
    cap-aware mock at the 16000 default must produce NO partial_parse bump
    (error_census["partial_parse"] absent/0) — asserted where the bump
    actually lives (the S2/S4 stage callers)."""
    from tests.test_extractor_reliability import _conv
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)

    class CapAwareSessionModel:
        def __init__(self):
            self.calls = 0
            self.last_finish_reason = "stop"
        def complete(self, *, system, user, max_tokens=None):
            self.calls += 1
            self.last_finish_reason = ("length"
                                       if max_tokens and max_tokens <= 8000
                                       else "stop")
            if "GAP REVIEWER" in system:
                # S4: dense re-emit — full at 16000, truncated at <=8000
                pts = [{"content": f"gap point {i} " + "word " * 40,
                        "pointKind": "statement"} for i in range(40)]
                if max_tokens and max_tokens <= 8000:
                    # cut at the first point's closing brace (item boundary)
                    # so rung 4 _longest_valid_prefix partial-accepts (a cut
                    # with zero complete items falls through to _ParseError).
                    pts_json = json.dumps(pts)
                    boundary = pts_json.index('}') + 1
                    return ('{"entities": [], "events": [], "operators": [], '
                            '"points": ' + pts_json[:boundary])
                return json.dumps({"entities": [], "events": [],
                                   "operators": [], "points": pts,
                                   "link_before_create": []})
            if "STORY SUMMARIZER" in system:
                return "A narrative."
            return ('{"entities": [], "events": [], "operators": [], '
                    '"points": [{"content": "s2 base", '
                    '"pointKind": "statement"}]}')

    out = v2.extract_session_v2(CapAwareSessionModel(), _conv())
    assert out["error_census"].get("partial_parse", 0) == 0  # clean at 16K
    # the old-cap path through the SAME callers still bumps the census —
    # `_stage_cap` is read at call time by the S2/S4 callers, so
    # monkeypatching it to 8000 exercises the genuine truncation →
    # partial_parse path:
    # #2134 re-pin (Task 0 Step 5): at the old cap with the escalation knob
    # monkeypatched to <= base (the env range [16000..64000] makes esc=8000
    # unreachable — the helper is the only lever), the genuine truncation is
    # RESIDUAL fail-loud (truncated_parse_error, never a silent rung-4
    # partial of the truncating attempt — P2-15).
    monkeypatch.setattr(v2, "_stage_cap", lambda default: 8000)
    monkeypatch.setattr(v2, "_extractor_escalation_tokens", lambda b: 8000)
    out_old = v2.extract_session_v2(CapAwareSessionModel(), _conv())
    assert out_old["error_census"].get("partial_parse", 0) == 0
    assert out_old["error_census"].get("truncated_parse_error", 0) >= 1


def test_multi_session_haystack_truncation_escalates_or_fails_loud(
        monkeypatch):
    """#2134 Task 6 Step 2 — the mock-only CI guard for the multi-session
    haystack shape (extract_session_v2 runs per haystack session; a dense
    session's S4 emit overflows the 16K cap). POST-fix: every truncating
    session either ESCALATES-RECOVERS (aggregate: ZERO partial_parse /
    ZERO truncated_parse_error, recovery.escalated == sessions, the full
    list survives — never a silent shorter valid=true list) or FAILS LOUD
    when no escalation headroom exists (esc<=base → residual
    truncated_parse_error per session — the census, never a silent
    partial-accept of the truncating attempt)."""
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    from tests.test_extractor_reliability import _conv

    def _dense_s4(max_tokens):
        pts = [{"content": f"gap point {i} " + "word " * 40,
                "pointKind": "statement"} for i in range(40)]
        pts_json = json.dumps(pts)
        boundary = pts_json.index('}') + 1
        return ('{"entities": [], "events": [], "operators": [], '
                '"points": ' + pts_json[:boundary])

    class _MultiSession:
        """S4 emits a dense 40-item list: length-truncated at <=16000 (the
        16K base cap), full at the 32000 escalation. S2 + S1 stay small."""
        last_finish_reason = "stop"

        def __init__(self):
            self.calls = 0

        def complete(self, *, system, user, max_tokens=None):
            self.calls += 1
            if "GAP REVIEWER" in system:
                if max_tokens and max_tokens <= 16000:
                    self.last_finish_reason = "length"
                    return _dense_s4(max_tokens)
                self.last_finish_reason = "stop"
                pts = [{"content": f"gap point {i} " + "word " * 40,
                        "pointKind": "statement"} for i in range(40)]
                return json.dumps({"entities": [], "events": [],
                                   "operators": [], "points": pts,
                                   "link_before_create": []})
            if "STORY SUMMARIZER" in system:
                self.last_finish_reason = "stop"
                return "A narrative."
            self.last_finish_reason = "stop"
            return ('{"entities": [], "events": [], "operators": [], '
                    '"points": [{"content": "s2 base", '
                    '"pointKind": "statement"}]}')

    # three truncating haystack sessions → three escalation-recoveries
    # (the per-session stats are already stage-rolled by extract_session_v2 —
    # accumulate them the way ingest_v2 does, directly off stats["llm"] /
    # stats["recovery"])
    llm_agg = {"calls": 0, "retries": 0, "truncated": 0, "deadline_aborts": 0}
    rec_agg: dict = {}
    census_agg: dict = {}
    for _ in range(3):
        out = v2.extract_session_v2(_MultiSession(), _conv())
        assert out["errors"] == []            # every truncation recovered
        assert out["error_census"] == {}      # no partial, no residual
        contents = [p["content"] for p in out["embed_list"]["points"]]
        assert "s2 base" in contents
        assert any(c.startswith("gap point 39") for c in contents)
        _llm = out["stats"].get("llm") or {}
        for _k in ("calls", "retries", "truncated", "deadline_aborts"):
            llm_agg[_k] += _llm.get(_k, 0)
        for _k, _v in (out["stats"].get("recovery") or {}).items():
            rec_agg[_k] = rec_agg.get(_k, 0) + _v
        for k, v in out["error_census"].items():
            census_agg[k] = census_agg.get(k, 0) + v
    assert llm_agg["truncated"] == 3          # criterion 3: recorded
    assert rec_agg["escalated"] == 3
    assert rec_agg["escalated_recovered"] == 3
    assert rec_agg["escalated"] == (rec_agg["escalated_recovered"]
                                    + rec_agg.get("escalated_residual", 0)
                                    + rec_agg.get("escalated_abort", 0)
                                    + rec_agg.get("escalated_partial", 0))
    assert census_agg.get("partial_parse", 0) == 0

    # fail-loud arm: no escalation headroom (esc == base 16000) → each
    # session's S4 length is RESIDUAL — the census catches it (never a
    # silent shorter valid=true list, never a rung-4 partial of the
    # truncating attempt)
    monkeypatch.setattr(v2, "_extractor_escalation_tokens", lambda b: 16000)
    census_loud: dict = {}
    for _ in range(3):
        out = v2.extract_session_v2(_MultiSession(), _conv())
        assert out["errors"]                   # fail-loud at the caller
        for k, v in out["error_census"].items():
            census_loud[k] = census_loud.get(k, 0) + v
    assert census_loud.get("truncated_parse_error", 0) == 3
    assert census_loud.get("partial_parse", 0) == 0


def test_over_32k_residual_census_killer_not_partial_parse(monkeypatch):
    """#2134 (plan Task 3 arm): a genuine >32K mega-session — S2 AND S4
    BOTH still length-truncated after the ONE 32000 escalation — is
    DOUBLE-RESIDUAL fail-loud: census truncated_parse_error per stage AND
    the empty_embed_list killer class (the #1987/#2335 signal grades HARD
    per report.py, never a silent partial-accept of the truncating
    attempt, never partial_parse from a truncation-attributed response —
    the conscious, reviewed migration from the #1746 recoverable-partial
    net, pinned here)."""
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)
    from tests.test_extractor_reliability import _conv

    class _MegaSession:
        """S2 + S4 truncate at EVERY budget (a >32K list never completes)."""
        last_finish_reason = "length"
        def complete(self, *, system, user, max_tokens=None):
            if "STORY SUMMARIZER" in system:
                self.last_finish_reason = "stop"
                return "A narrative."
            self.last_finish_reason = "length"
            return ('{"entities": [], "events": [], "operators": [], '
                    '"points": [{"content": "p1", '
                    '"pointKind": "statement"}')

    out = v2.extract_session_v2(_MegaSession(), _conv())
    census = out["error_census"]
    assert census.get("partial_parse", 0) == 0       # never a silent partial
    assert census.get("truncated_parse_error", 0) == 2  # S2 + S4 residuals
    assert census.get("empty_embed_list", 0) == 1    # killer class: HARD
    rec = out["stats"]["recovery"]
    assert rec["escalated"] == 2 and rec["escalated_residual"] == 2
    assert rec["escalated"] == (rec.get("escalated_recovered", 0)
                                + rec["escalated_residual"]
                                + rec.get("escalated_abort", 0)
                                + rec.get("escalated_partial", 0))
    assert out["stats"]["llm"]["truncated"] == 2  # both recorded


def test_s2_residual_then_s4_recovers(monkeypatch):
    """#2134 (plan Task 3 arm): the designed rescue net — S2's emit is a
    genuine >32K truncation (residual fail-loud, truncated_parse_error,
    NO partial_parse) but S4's gap review fits the 32000 knob and RECOVERS
    the list — the session still embeds (never an empty_embed_list on the
    S2-only case)."""
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)
    from tests.test_extractor_reliability import _conv

    class _Rescue:
        last_finish_reason = "stop"
        def complete(self, *, system, user, max_tokens=None):
            if "STORY SUMMARIZER" in system:
                self.last_finish_reason = "stop"
                return "A narrative."
            if "GAP REVIEWER" in system and max_tokens and max_tokens > 16000:
                self.last_finish_reason = "stop"
                pts = [{"content": f"gp{i}",
                        "pointKind": "statement"} for i in range(3)]
                return ('{"entities": [], "events": [], "operators": '
                        '[], "points": ' + json.dumps(pts) + '}')
            self.last_finish_reason = "length"
            return ('{"entities": [], "events": [], "operators": [], '
                    '"points": [{"content": "p1", '
                    '"pointKind": "statement"}')

    out = v2.extract_session_v2(_Rescue(), _conv())
    census = out["error_census"]
    assert census.get("partial_parse", 0) == 0
    assert census.get("truncated_parse_error", 0) == 1  # S2 residual only
    assert census.get("empty_embed_list", 0) == 0       # S4 rescued
    contents = [p["content"] for p in out["embed_list"]["points"]]
    assert "gp0" in contents and "gp2" in contents  # S4 recovery embedded
    rec = out["stats"]["recovery"]
    assert rec["escalated"] == 2  # S2 (residual) + S4 (recovered)
    assert rec["escalated_residual"] == 1
    assert rec["escalated_recovered"] == 1


def test_s4_dense_emit_completes_at_16k(monkeypatch):
    """#1787 P1-C (cycle 5) — the S4 re-emit surface (output ≈ 2× S2 — the
    DOMINANT truncation source) must be exercised by a genuinely dense S4
    output at the NEW cap: full list, no partial_parse — then force the OLD
    cap on the SAME fixture to prove the S4 partial-accept backstop still
    fires."""
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)
    dense_pts = [
        {"content": f"s4 re-emit point {i} " + ("lorem ipsum dolor " * 30),
         "pointKind": "statement", "about_entities": [f"entity-{i}"],
         "quote": f"quote {i}: " + ("word " * 40),
         "search_keys": [f"k{i}"], "tier": "B",
         "slots": {"subject": [{"name": f"entity-{i}", "kind": "core:thing",
                                "confidence": 0.9}]}}
        for i in range(45)
    ]
    full = json.dumps({"entities": [{"name": f"entity-{i}", "kind": "core:thing",
                                     "lifecycle": "created", "supersedes": None}
                                    for i in range(45)],
                       "points": dense_pts, "events": [], "operators": [],
                       "chain_notes": [], "link_before_create": []})
    # calibration asserts (same discipline as the S2 fixture): 45 points =
    # 48,327 bytes → ≥ 12.1K tokens at 4 chars/token (clears 8192) and
    # ≤ 13.8K at 3.5 (fits 16K):
    assert len(full.encode("utf-8")) // 4 >= 8192, \
        "S4 fixture too small: the 45-point dense re-emit list must exceed " \
        "8K tokens (the S4 re-emit tax surface; raise point count / quote " \
        "length until the 4 chars/token bound clears)"
    assert len(full.encode("utf-8")) // 3.5 <= 14000, \
        "S4 fixture too dense: must FIT the 16000 default (shrink until the " \
        "upper bound clears)"

    class S4CapAwareModel:
        def __init__(self):
            self.last_finish_reason = "stop"
        def complete(self, *, system, user, max_tokens=None):
            self.last_finish_reason = ("length" if max_tokens and max_tokens <= 8000
                                       else "stop")
            if max_tokens and max_tokens <= 8000:
                # old-cap failure mode: cut mid-points-array (rung-4
                # partial-accept recovers the head) — depth-walk to the
                # 20th point's closing brace, leaving the array unterminated
                # so rung-4 recovers the head with partial=True.
                pts_json = json.dumps(dense_pts)
                depth, closed, boundary = 0, 0, len(pts_json)
                for i, ch in enumerate(pts_json):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            closed += 1
                            if closed == 20:
                                boundary = i + 1
                                break
                return ('{"entities": [], "events": [], "operators": [], '
                        '"points": ' + pts_json[:boundary])
            return full

    m = S4CapAwareModel()
    stats = {"llm": {}}
    # _complete_parsed is the seam the S4 caller uses — drive the dense S4
    # emit at the 16000 default:
    out = v2._complete_parsed(m, "sys", "usr", max_tokens=16000, stats=stats)
    assert len(out["points"]) == 45           # full S4 list, no tail loss
    assert stats.get("partial") is not True   # clean at 16K — no partial_parse
    # #2134 backstop re-pin (Task 0 Step 5): force the old 8000 cap WITH the
    # escalation knob monkeypatched to <= base → an un-escalatable
    # truncation is RESIDUAL fail-loud (P2-15: never a silent rung-4
    # partial of the truncating attempt; env range [16000..64000] makes
    # esc=8000 unreachable via env — the helper is the only lever).
    monkeypatch.setattr(v2, "_extractor_escalation_tokens", lambda b: 8000)
    m2 = S4CapAwareModel()
    stats2 = {"llm": {}}
    with pytest.raises(ValueError) as ei:
        v2._complete_parsed(m2, "sys", "usr", max_tokens=8000, stats=stats2)
    assert ei.value.truncated is True   # fail-loud, classed truncated
    assert stats2.get("partial") is not True  # never a silent partial


class TestEscalation2134:
    """#2134 Task 3 — the one-shot S2/S4 escalation net + fail-loud residual
    (R2/R3 contract: ONE escalated `_complete`; four buckets
    recovered/residual/abort/partial; the escalated response is parsed ONCE
    terminally — the #1746 error-informed re-prompt NEVER runs
    post-escalation)."""

    # a length-truncating S2 emit (mid-points cut) with an optional token
    # attrs payload
    TRUNC = ('{"entities": [], "events": [], "operators": [], '
             '"points": [{"content": "p1", "pointKind": "statement"}, '
             '{"content": "p2", "pointKind": "statement"}')
    FULL = ('{"entities": [], "events": [], "operators": [], '
            '"points": [{"content": "p1", "pointKind": "statement"}, '
            '{"content": "p2", "pointKind": "statement"}, '
            '{"content": "p3", "pointKind": "statement"}]}')

    def _cap_aware(self, tokens=(16000, 32000)):
        """attempt-1 (base) length+TRUNC; escalated call (esc) returns full
        with stop."""
        captured = []

        class _M:
            last_finish_reason = "stop"
            def complete(self, *, system, user, max_tokens=None):
                captured.append(max_tokens)
                if max_tokens == tokens[1]:
                    self.last_finish_reason = "stop"
                    self.last_prompt_tokens = 900
                    self.last_completion_tokens = tokens[1] - 200
                    return self.__class__.FULL
                self.last_finish_reason = "length"
                self.last_prompt_tokens = 500
                self.last_completion_tokens = tokens[0]
                return self.__class__.TRUNC
        _M.FULL = self.FULL
        _M.TRUNC = self.TRUNC
        return _M(), captured

    def test_length_escalates_once_and_recovers(self):
        """attempt-1 length at the 16K base → ONE escalated call at 32K
        returns the full list: exactly 2 calls, recovered bucket, no
        partial at the caller."""
        m, captured = self._cap_aware()
        stats: dict = {}
        out = v2.run_s2(m, "STORY", stats=stats)
        assert len(captured) == 2
        assert captured[0] == 16000 and captured[1] == 32000
        assert len(out["points"]) == 3  # full list recovered
        rec = stats["recovery"]
        assert rec["escalated"] == 1 and rec["escalated_recovered"] == 1
        assert stats.get("partial") is not True
        assert stats["truncated"] is True  # the truncation is RECORDED

    def test_length_residual_fails_loud(self):
        """The escalated call is STILL length → fail-loud truncated raise,
        residual bucket, never a partial."""
        calls = {"n": 0}

        class _M:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                calls["n"] += 1
                self.last_finish_reason = "length"
                self.last_completion_tokens = max_tokens or 16000
                return '{"entities": []'

        stats: dict = {}
        with pytest.raises(ValueError) as ei:
            v2.run_s2(_M(), "STORY", stats=stats)
        assert calls["n"] == 2  # base + ONE escalated call
        assert ei.value.truncated is True
        rec = stats["recovery"]
        assert rec["escalated"] == 1 and rec["escalated_residual"] == 1
        assert stats.get("partial") is not True

    def test_length_residual_cut_at_section_boundary_fails_loud(self):
        """A residual response cut cleanly BETWEEN sections (balanced
        complete-but-shorter JSON that rungs 1-3 WOULD clean-parse) is STILL
        fail-loud truncated — never a silent shorter valid dict (P1-7)."""
        calls = {"n": 0}
        BALANCED_SHORT = ('{"entities": [], "events": [], "operators": [], '
                          '"points": [], "chain_notes": []}')

        class _M:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                calls["n"] += 1
                self.last_finish_reason = "length"
                return BALANCED_SHORT

        stats: dict = {}
        with pytest.raises(ValueError) as ei:
            v2.run_s2(_M(), "STORY", stats=stats)
        assert ei.value.truncated is True  # balanced-but-shorter STILL fails
        assert calls["n"] == 2
        assert stats["recovery"]["escalated_residual"] == 1

    def test_length_truncated_clean_parseable_still_escalates(self):
        """attempt-1 is section-boundary-truncated such that rungs 1-3 would
        clean-parse → escalation fires BEFORE any parse of the truncating
        attempt (exactly 2 calls, full list, never a swallowed shorter
        return — P2-10b)."""
        BALANCED_SHORT = ('{"entities": [], "events": [], "operators": [], '
                          '"points": [], "chain_notes": []}')

        class _M:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                if max_tokens == 32000:
                    self.last_finish_reason = "stop"
                    return self.__class__.FULL
                self.last_finish_reason = "length"
                return BALANCED_SHORT
        _M.FULL = self.FULL

        m = _M()
        stats: dict = {}
        out = v2.run_s2(m, "STORY", stats=stats)
        assert len(out["points"]) == 3
        assert stats["recovery"]["escalated_recovered"] == 1
        assert stats.get("partial") is not True

    def test_non_length_no_escalation(self):
        """A stop-finish call makes exactly one _complete call (common path
        byte-identical)."""
        calls = {"n": 0}

        class _M:
            last_finish_reason = "stop"
            def complete(self, *, system, user, max_tokens=None):
                calls["n"] += 1
                self.last_finish_reason = "stop"
                return self.__class__.FULL
        _M.FULL = self.FULL

        stats: dict = {}
        out = v2.run_s2(_M(), "STORY", stats=stats)
        assert calls["n"] == 1
        assert len(out["points"]) == 3
        assert "escalated" not in (stats.get("recovery") or {})

    def test_length_on_reparse_attempt_escalates(self):
        """[base stop-unparseable → error-informed re-prompt at base cap →
        attempt-2 length] → the length escalates (3 calls total
        [base, base-reprompt, esc]) AND llm.calls == 3 (R3-3 running totals)."""
        calls = {"n": 0}

        class _M:
            last_finish_reason = "stop"
            def complete(self, *, system, user, max_tokens=None):
                calls["n"] += 1
                if calls["n"] == 1:  # base: stop-unparseable
                    self.last_finish_reason = "stop"
                    return "not json"
                if calls["n"] == 2:  # re-prompt at base: length
                    self.last_finish_reason = "length"
                    return '{"entities": []'
                # escalated call: full
                self.last_finish_reason = "stop"
                return self.__class__.FULL
        _M.FULL = self.FULL

        stats: dict = {}
        out = v2.run_s2(_M(), "STORY", stats=stats)
        assert calls["n"] == 3
        assert len(out["points"]) == 3
        assert stats["recovery"]["escalated_recovered"] == 1
        assert stats["attempts"] == 3  # llm.calls==3 at the roll-up

    def test_escalated_stop_malformed_ladder_terminal(self):
        """R2/R3: escalated response is stop-but-malformed WITH a valid
        rung-4 prefix → the head is returned + partial_parse +
        escalated_partial (NOT recovered), exactly 2 calls, NO re-prompt."""
        calls = {"n": 0}

        class _M:
            last_finish_reason = "stop"
            def complete(self, *, system, user, max_tokens=None):
                calls["n"] += 1
                if calls["n"] == 1:  # base length
                    self.last_finish_reason = "length"
                    return '{"entities": []'
                # escalated: stop + malformed with a valid prefix
                self.last_finish_reason = "stop"
                return ('{"entities": [{"name": "e1", "kind": "core:thing"}, '
                        '{"name": "e2"')  # rung-4 recovers e1

        stats: dict = {}
        out = v2.run_s2(_M(), "STORY", stats=stats)
        assert calls["n"] == 2  # NO base-cap third call (R3-1/R3-2 pin)
        assert stats.get("partial") is True
        assert stats["recovery"]["escalated_partial"] == 1
        assert stats["recovery"].get("escalated_recovered", 0) == 0
        names = [e["name"] for e in out["entities"]]
        assert "e1" in names and "e2" not in names

    def test_escalated_call_abort_falls_back_to_head_reparse(self):
        """The escalated call RAISES (transient-after-retries) →
        escalated_abort + canonical-then-rung-4 head-reparse of the retained
        truncating response, always classed partial_parse (never a silent
        discard)."""
        class _EscRaises:
            last_finish_reason = "length"
            def __init__(self):
                self.calls = 0
            def complete(self, *, system, user, max_tokens=None):
                self.calls += 1
                if max_tokens == 32000:
                    raise TimeoutError("model call exceeded deadline")
                self.last_finish_reason = "length"
                # retained response: mid-list cut with ONE recoverable item
                return ('{"entities": [], "events": [], "operators": [], '
                        '"points": [{"content": "p1", '
                        '"pointKind": "statement"}, {"content": "p2"')

        stats: dict = {}
        out = v2.run_s2(_EscRaises(), "STORY", stats=stats)
        assert stats["recovery"]["escalated_abort"] == 1
        assert stats["recovery"]["escalated"] == 1
        assert stats.get("partial") is True
        contents = [p["content"] for p in out["points"]]
        assert contents == ["p1"]  # the recoverable head was used
        # R3-3/P1-39 (review round): the escalated call's except-branch
        # overwrote stats["attempts"] before raising (retries=1 → 2 esc
        # attempts) — the abort arm re-accumulates base + esc so the session
        # llm.calls roll-up counts all 3 calls (1 base + 2 esc).

    def test_escalated_abort_llm_calls_counts_all_calls(self):
        """R3-3/P1-39 (code-review round 2): the escalated-call RAISE path
        re-accumulates base + esc attempts into the running totals — the
        abort arm is not allowed to undercount `llm.calls` (base 1 + esc
        retries=1 → 2 esc attempts = 3 total at the roll-up)."""
        class _EscRaises:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                if max_tokens == 32000:
                    raise TimeoutError("model call exceeded deadline")
                self.last_finish_reason = "length"
                return ('{"entities": [], "events": [], "operators": [], '
                        '"points": [{"content": "p1", '
                        '"pointKind": "statement"}, {"content": "p2"')

        stats: dict = {}
        v2.run_s2(_EscRaises(), "STORY", stats=stats)
        assert stats["attempts"] == 3  # 1 base + 2 escalated (retries=1)
        assert stats["recovery"]["escalated_abort"] == 1

    def test_length_truncation_at_base_ge_esc_still_fails_loud(
            self, monkeypatch):
        """#2134: when the base cap is RAISED to >= the escalation knob
        (the #1787 cap lever not in lockstep with the escalation budget),
        an S2/S4 length is RESIDUAL fail-loud with NO escalation episode
        recorded (escalated stays 0 — the guard fires before any counter
        bump, matching the invariant)."""
        monkeypatch.setattr(v2, "_extractor_escalation_tokens",
                            lambda b: 20000)

        class _M:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                self.last_finish_reason = "length"
                return '{"entities": []'

        stats: dict = {}
        with pytest.raises(ValueError) as ei:
            v2._complete_parsed(_M(), "sys", "usr", max_tokens=20000,
                                stats=stats)
        assert ei.value.truncated is True
        rec = stats["recovery"]
        assert rec.get("escalated", 0) == 0  # no episode fired
        assert rec.get("escalated_residual", 0) == 0

    def test_s1_esc_le_base_no_escalation_event(self, monkeypatch):
        """run_s1 with no escalation headroom records NO escalation episode
        (escalated == 0 == buckets — the 3-bucket invariant stays literal
        under the no-headroom config; the per-seam s1 truncation keys DO
        record the base truncation, matching _complete_parsed's ordering)."""
        monkeypatch.setattr(v2, "_extractor_escalation_tokens",
                            lambda b: b)  # esc == base -> no headroom
        calls = {"n": 0}

        class _M:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                calls["n"] += 1
                self.last_finish_reason = "length"
                self.last_completion_tokens = 1500
                return "A truncated narrative."

        stats: dict = {}
        out = v2.run_s1(_M(), "CONV", stats=stats)
        assert calls["n"] == 1  # no escalated call
        assert out == "A truncated narrative."
        rec = stats["recovery"]
        assert rec.get("escalated", 0) == 0
        assert rec.get("escalated_residual", 0) == 0
        assert rec.get("escalated_recovered", 0) == 0
        # the truncation itself IS recorded per-seam (R3-6) + combined
        assert rec["truncation_completion_tokens_s1"] == 1500

    def test_s1_recovered_escalation_does_not_inflate_seam_overage(self):
        """R3-6/plan contract: the per-seam s1 keys measure TRUNCATED calls
        only — a RECOVERED escalated call's full output lands in the
        escalation_*_tokens delta, never in the truncation overage (a
        recovered call was never length-truncated)."""
        class _M:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                if max_tokens == 32000:
                    self.last_finish_reason = "stop"
                    self.last_completion_tokens = 6000
                    return "A full recovered narrative."
                self.last_finish_reason = "length"
                self.last_completion_tokens = 1500
                return "A truncated narrative."

        stats: dict = {}
        out = v2.run_s1(_M(), "CONV", stats=stats)
        assert out == "A full recovered narrative."
        rec = stats["recovery"]
        assert rec["escalated_recovered"] == 1
        # seam keys: base truncation only (1500), NEVER base + recovered
        assert rec["truncation_completion_tokens_s1"] == 1500
        # the recovered call's spend IS captured by the D6 delta
        assert rec["escalation_output_tokens"] == 6000

    def test_escalation_token_accumulation_single_call(self):
        """D6/R3: the escalation delta == the escalated call's post-return
        in-stats tokens; the base call's wasted output is the separate
        escalation_base_* fields. (Abort-arm accumulate-NONE is exercised by
        the abort test: no escalation_* delta keys are written there.)"""
        m, _ = self._cap_aware()
        stats: dict = {}
        v2.run_s2(m, "STORY", stats=stats)
        rec = stats["recovery"]
        assert rec["escalation_output_tokens"] == 32000 - 200
        assert rec["escalation_prompt_tokens"] == 900
        assert rec["escalation_base_output_tokens"] == 16000
        assert rec["escalation_base_prompt_tokens"] == 500

    def test_escalated_call_deadline_scales(self):
        """D5: the escalated call resolves a deadline >= 0.05 x esc — the
        _complete seam computes _scaled_deadline(600, esc)."""
        seen = {}

        class _M:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                return "{}"
        # patch _complete to observe the escalated call's deadline
        import tortoise.extractor_v2 as _v2
        orig = _v2._complete

        def spy(model, system, user, *, max_tokens=None, retries=None,
                **kw):
            if max_tokens == 32000:
                seen["deadline"] = _v2._scaled_deadline(600, max_tokens)
            return orig(model, system, user, max_tokens=max_tokens,
                        retries=retries, **kw)
        _v2._complete = spy
        try:
            stats: dict = {}
            with pytest.raises(ValueError):
                v2.run_s2(_M(), "STORY", stats=stats)
        finally:
            _v2._complete = orig
        assert seen["deadline"] >= 0.05 * 32000  # 1600s scaled


class TestS1Escalation2134:
    """#2134 Task 4 — the S1 one-shot escalation wrap (three buckets:
    recovered/residual/abort; no partial bucket — S1 has no parse ladder)."""

    def _s1_model(self, mode, calls_box):
        """mode: recover (esc returns stop+full) | residual (esc still
        length) | abort (esc raises)."""
        class _M:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                calls_box["n"] += 1
                if max_tokens == 32000:  # escalated call
                    if mode == "recover":
                        self.last_finish_reason = "stop"
                        return "A full recovered narrative."
                    if mode == "residual":
                        self.last_finish_reason = "length"
                        self.last_completion_tokens = 32000
                        return "A still-truncated narrative."
                    raise TimeoutError("model call exceeded deadline")
                # base call truncates
                self.last_finish_reason = "length"
                self.last_prompt_tokens = 700
                self.last_completion_tokens = 1500
                return "A truncated narrative."
        return _M()

    def test_s1_length_escalates_once_and_recovers(self):
        calls = {"n": 0}
        stats: dict = {}
        out = v2.run_s1(self._s1_model("recover", calls), "CONV",
                        stats=stats)
        assert calls["n"] == 2
        assert out == "A full recovered narrative."
        rec = stats["recovery"]
        assert rec["escalated"] == 1 and rec["escalated_recovered"] == 1
        assert stats["truncated"] is True   # the truncation is RECORDED (P1-2)
        # R3-3 mirror: llm.calls counts BOTH calls at the roll-up
        assert stats["attempts"] == 2
        # per-seam s1 keys (R3-6) + the escalation delta
        assert rec["truncation_completion_tokens_s1"] >= 1500

    def test_s1_non_length_single_call(self):
        calls = {"n": 0}
        class _Ok:
            last_finish_reason = "stop"
            def complete(self, *, system, user, max_tokens=None):
                calls["n"] += 1
                self.last_finish_reason = "stop"
                return "A clean narrative."
        out = v2.run_s1(_Ok(), "CONV", stats={})
        assert calls["n"] == 1
        assert out == "A clean narrative."

    def test_s1_escalated_residual_returns_truncated_summary(self):
        calls = {"n": 0}
        stats: dict = {}
        out = v2.run_s1(self._s1_model("residual", calls), "CONV",
                        stats=stats)
        assert calls["n"] == 2
        assert out == "A still-truncated narrative."  # kept, not a raise
        rec = stats["recovery"]
        assert rec["escalated"] == 1 and rec["escalated_residual"] == 1
        assert stats["truncated"] is True

    def test_s1_escalated_call_abort_returns_attempt_summary(self):
        calls = {"n": 0}
        stats: dict = {}
        out = v2.run_s1(self._s1_model("abort", calls), "CONV", stats=stats)
        assert calls["n"] == 2
        assert out == "A truncated narrative."  # attempt-1 kept (P1-21)
        rec = stats["recovery"]
        assert rec["escalated"] == 1 and rec["escalated_abort"] == 1

    def test_s1_bucket_equality_at_rollup(self):
        """escalated == recovered + residual + abort at the real
        _rollup_recovery boundary across all three arms."""
        llm_stats = {"calls": 0, "retries": 0, "truncated": 0,
                     "deadline_aborts": 0}
        recovery_stats: dict = {}
        for mode in ("recover", "residual", "abort"):
            stats: dict = {}
            v2.run_s1(self._s1_model(mode, {"n": 0}), "CONV", stats=stats)
            v2._rollup_llm(llm_stats, stats)
            v2._rollup_recovery(recovery_stats, stats)
        assert recovery_stats["escalated"] == 3
        assert recovery_stats["escalated"] == (
            recovery_stats.get("escalated_recovered", 0)
            + recovery_stats.get("escalated_residual", 0)
            + recovery_stats.get("escalated_abort", 0))
        assert llm_stats["calls"] == 6  # 2 per escalated chunk
        assert llm_stats["truncated"] == 3  # every escalation is recorded


class TestKindClassifierAdjudication2134:
    def test_kind_classifier_adjudication_length_no_escalation(self):
        """#2134 P1-30: the kind_classifier adjudication seam passes
        escalate=False — a length at ADJUDICATION_MAX_TOKENS makes exactly
        ONE _complete call (no 21x escalation) and today's ladder behavior
        is preserved verbatim."""
        calls = {"n": 0}

        class _M:
            last_finish_reason = "length"
            def complete(self, *, system, user, max_tokens=None):
                calls["n"] += 1
                self.last_finish_reason = "length"
                return "not json at all"

        stats: dict = {}
        with pytest.raises(ValueError) as ei:
            v2._complete_parsed(_M(), "sys", "usr", max_tokens=1500,
                                stats=stats, escalate=False)
        assert calls["n"] == 1  # escalation is scoped OUT of this seam
        assert ei.value.truncated is True
        # wiring guard: the adjudication call site passes escalate=False
        import tortoise.kind_classifier as kc
        with open(kc.__file__) as _f:
            src = _f.read()
        assert "escalate=False" in src
