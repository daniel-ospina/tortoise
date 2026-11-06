"""#5095 (§4.4 G3) — S3's FAILURE POLICY: an unresolved identity is never asserted `new`.

THE DEFECT THIS PINS
--------------------
§4.4 declares invariant **I1** — *"An unresolved identity is NEVER asserted
`new`"* — and says of itself that it *"is a SPEC, not a description of what
runs"*. The shipped code asserted the **inverse** in two places, one layer apart:

* `_render_search_results` answered a degraded lookup with *"no existing items
  available; assume everything is new"* — a fact it never measured (the lookup
  did not run), and an instruction to act on it. That render reaches the S4 GAP
  REVIEWER, the pass whose output becomes the committed candidate list.
* `execute_embed`'s `link_before_create` — the record the caller keeps — said
  `found: false, "no match — created"` for every created candidate, including
  when the lookup had never run.

WHAT THIS PINS
--------------
1. **The lookup's state is read from COVERAGE, not from `degraded`.**
   `degraded: True` implies zero priors (measured, `#5095`), so the interesting
   incomplete shape is the *swallowed later failure*: `degraded: False` with a
   partial prior set. `lookup_state` returns `unresolved` / `partial` /
   `resolved`, and `search_graph` now reports `query_coverage` +
   `query_results` so the state is measured rather than assumed.
2. **The prompt clause states what is true** and keeps the emission
   requirement — forbidding the false claim must not read as forbidding the S4
   pass its job.
3. **The record's polarity is per CANDIDATE**: `found: false` only when that
   candidate's own query ran; otherwise `found: None`. A run-level answer would
   fold candidates whose queries did run into the ones whose did not.
4. **The run reports it** — `identity` is always present (both return paths of
   `extract_session_v2`), and a non-resolved state raises a loud warning, because
   I2 forbids a silent anything.

⚠️ What this does NOT pin, on purpose: *"never mint it"* — I1's enforcement needs
a create gate inside S5 (see the module docstring). This suite pins the RECORD,
not the suppression; a test asserting no write would be pinning something the
change deliberately does not do.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100
from tortoise import failure_policy as fp  # noqa: E402, RUF100

#: The pre-#5095 degraded render, verbatim. Its ABSENCE is the point of the
#: whole change, and it is asserted as a string here rather than by eye.
_INVERTED_CLAUSE = "assume everything is new"

#: A complete lookup: every derived query ran, none failed.
_RESOLVED_COVERAGE = {
    "derived": {"object": 2, "event": 1, "point": 2},
    "ran": {"object": 2, "event": 1, "point": 2},
    "failed": {"object": 0, "event": 0, "point": 0},
}


def _search(**overrides) -> dict:
    """A healthy, COMPLETE search result — the `resolved` baseline."""
    base = {
        "mode": "real",
        "degraded": False,
        "reason": None,
        "entities": [],
        "points": [],
        "events": [],
        "queries_run": 5,
        "query_coverage": {k: dict(v) for k, v in _RESOLVED_COVERAGE.items()},
        # A complete lookup deliberately omits `query_results` here: with no log
        # the per-candidate question falls back to the run-level state, which is
        # the documented fallback this fixture is meant to exercise.
    }
    base.update(overrides)
    return base


# ── 1. the state ───────────────────────────────────────────────────────────


class TestLookupState:
    def test_a_missing_or_empty_search_established_nothing(self):
        for value in ({}, None, [], "search"):
            assert fp.lookup_state(value) == fp.LOOKUP_UNRESOLVED, value

    def test_degraded_is_unresolved(self):
        assert fp.lookup_state(
            _search(degraded=True, reason="S3 skipped: no graph client")
        ) == fp.LOOKUP_UNRESOLVED

    def test_a_complete_lookup_is_resolved(self):
        assert fp.lookup_state(_search()) == fp.LOOKUP_RESOLVED

    def test_a_budget_truncated_leg_is_PARTIAL(self):
        """The measured common shape: 18 derived vs a budget of 15 starves the
        point leg — the dedup one. Reading only `degraded` calls this complete."""
        coverage = {
            "derived": {"object": 8, "event": 1, "point": 9},
            "ran": {"object": 8, "event": 1, "point": 6},
            "failed": {"object": 0, "event": 0, "point": 0},
        }
        state = fp.lookup_state(_search(query_coverage=coverage))
        assert state == fp.LOOKUP_PARTIAL, (
            "a leg whose queries never ran must not read as a complete lookup — "
            "that is the claim this change exists to stop making")

    def test_a_swallowed_query_failure_is_PARTIAL_even_though_degraded_is_false(self):
        """`search_graph` swallows a later query failure (`except: continue`) and
        returns `degraded: False`. That is the REACHABLE incomplete shape."""
        coverage = {
            "derived": {"object": 2, "event": 1, "point": 2},
            "ran": {"object": 2, "event": 1, "point": 1},
            "failed": {"object": 0, "event": 0, "point": 1},
        }
        search = _search(query_coverage=coverage)
        assert search["degraded"] is False
        assert fp.lookup_state(search) == fp.LOOKUP_PARTIAL

    def test_a_malformed_coverage_report_is_PARTIAL_not_resolved(self):
        """Unknown completeness must never read as complete — fail-closed."""
        for coverage in (
            {"derived": {"point": 3}, "ran": {"point": "3"}, "failed": {}},
            {"derived": {"point": 3}, "ran": {"point": -1}, "failed": {}},
            {"derived": {"point": 3}, "ran": {"point": True}, "failed": {}},
            {"derived": {"point": 3}, "ran": None, "failed": {}},
        ):
            assert fp.lookup_state(_search(query_coverage=coverage)) == \
                fp.LOOKUP_PARTIAL, coverage

    def test_absent_coverage_falls_back_to_the_historical_bit(self):
        """A `search` built by anything but `search_graph` carries no coverage.

        Documented fallback, pinned here so it cannot become an accident: the
        producer always supplies coverage, so this is only reachable off the
        pipeline — and the alternative (labelling every such dict `partial`)
        would relabel every existing caller without telling anyone anything.
        """
        search = _search()
        del search["query_coverage"]
        assert fp.lookup_state(search) == fp.LOOKUP_RESOLVED
        del search["degraded"], search["reason"]
        search["degraded"] = True
        assert fp.lookup_state(search) == fp.LOOKUP_UNRESOLVED

    def test_an_incomplete_report_is_PARTIAL_not_resolved(self):
        """A report with `ran` but no `derived`/`failed` defaulted those to 0 and
        read "the whole derived set ran" from a report that never said what was
        derived — the unmeasured-absence claim this module removes (#5095 review
        round 3). All three parts are required."""
        for coverage in ({"ran": {"point": 3}},
                         {"derived": {"point": 3}, "ran": {"point": 3}},
                         {"derived": {"point": 3}, "failed": {"point": 0}}):
            assert fp.lookup_state(_search(query_coverage=coverage)) == \
                fp.LOOKUP_PARTIAL, coverage

    def test_no_derived_queries_is_resolved(self):
        """An empty candidate set derives nothing — there is nothing to establish."""
        empty = {"derived": {}, "ran": {}, "failed": {}}
        assert fp.lookup_state(_search(query_coverage=empty)) == fp.LOOKUP_RESOLVED


# ── 2. the prompt clause ───────────────────────────────────────────────────


class TestLookupNote:
    def test_resolved_renders_nothing(self):
        assert fp.lookup_note(_search()) == ""

    def test_unresolved_states_the_truth_and_not_the_inverted_claim(self):
        note = fp.lookup_note(_search(degraded=True, reason="S3 skipped: no sdk"))
        assert _INVERTED_CLAUSE not in note, (
            "the clause still invites the model to treat everything as new — "
            "that is the defect (#5095)")
        assert "did NOT run" in note
        assert "UNRESOLVED" in note and "not absent" in note
        # I1's second half: say what NOT to claim, and how to report it.
        assert "link_before_create" in note and "omit the entry" in note

    def test_unresolved_keeps_the_EMISSION_requirement(self):
        """R3: forbidding the false claim must not read as forbidding the work.

        The clause is read by the S4 GAP REVIEWER — the pass whose job is to
        COMPLETE the candidate list. A clause that only forbids would invite the
        silent memory loss I2 names.
        """
        note = fp.lookup_note(_search(degraded=True, reason="skipped"))
        assert "Emit EVERY candidate" in note
        assert "complete the list as usual" in note

    def test_partial_names_the_starved_leg(self):
        coverage = {
            "derived": {"object": 8, "event": 1, "point": 9},
            "ran": {"object": 8, "event": 1, "point": 6},
            "failed": {"object": 0, "event": 0, "point": 0},
        }
        note = fp.lookup_note(_search(query_coverage=coverage))
        assert "PARTIAL" in note and "point" in note
        assert _INVERTED_CLAUSE not in note
        assert "Emit EVERY candidate" in note

    def test_the_clause_carries_the_producers_reason(self):
        note = fp.lookup_note(_search(degraded=True, reason="S3 skipped: no graph client"))
        assert "S3 skipped: no graph client" in note, (
            "an unresolved clause that drops the producer's reason makes the "
            "reader diagnose from scratch")


# ── 3. the record's polarity ───────────────────────────────────────────────


class TestCreatedRecord:
    def test_a_query_that_ran_licenses_no_match(self):
        rec = fp.created_record("entity 'x'", query_ran=True)
        assert rec["found"] is False
        assert rec["note"] == "no match — created"

    def test_a_query_that_did_not_run_asserts_nothing(self):
        rec = fp.created_record("entity 'x'", query_ran=False)
        assert rec["found"] is None, (
            "`found: false` claims a search happened and matched nothing; when "
            "it did not happen the honest value is the third state")
        assert "UNRESOLVED" in rec["note"] and "not 'absent'" in rec["note"]

    def test_the_polarity_argument_must_be_a_bool(self):
        for value in ("yes", 0, 1, None):
            with pytest.raises(TypeError):
                fp.created_record("entity 'x'", query_ran=value)

    def test_a_record_needs_what_it_searched_for(self):
        for value in ("", "   ", None):
            with pytest.raises(ValueError):
                fp.created_record(value, query_ran=True)


# ── 4. the run-level report ────────────────────────────────────────────────


class TestPlanIdentity:
    def test_unresolved_reports_state_reason_and_counts(self):
        block = fp.plan_identity(
            _search(degraded=True, reason="boom"),
            candidates={"entities": 3, "events": 0, "points": 2,
                        "operators": 1, "retractions": 0},
            unresolved={"entities": 3, "events": 0, "points": 2},
        )
        assert block["state"] == fp.LOOKUP_UNRESOLVED
        assert block["reason"] == "boom"
        assert block["unresolved_total"] == 5
        assert block["candidates"]["operators"] == 1

    def test_a_COMPLETE_lookup_with_unchecked_candidates_is_PARTIAL(self):
        """The measured #5095-review-B1 shape: `search_graph` runs over the S2
        list and S4 then COMPLETES it, so a candidate S4 added has no derived
        query at all. Reporting `resolved` there would make the block contradict
        its own `unresolved` counter — and silence the caller's warning."""
        block_out = fp.plan_identity(_search(), candidates={"points": 3},
                                     unresolved={"points": 1})
        assert block_out["state"] == fp.LOOKUP_PARTIAL, block_out
        assert block_out["unresolved_total"] == 1
        # ...and a fully-covered run stays resolved: the elevation is not a default.
        assert fp.plan_identity(_search(), candidates={"points": 3},
                                unresolved={"points": 0})["state"] == fp.LOOKUP_RESOLVED

    def test_a_partial_lookup_reports_partial_and_no_reason(self):
        """`reason` is the producer's failure text — a bounded lookup has none,
        and inventing one would report a failure that did not happen."""
        coverage = {"derived": {"point": 9}, "ran": {"point": 6},
                    "failed": {"point": 0}}
        block = fp.plan_identity(_search(query_coverage=coverage),
                                 candidates={"points": 6},
                                 unresolved={"points": 2})
        assert block["state"] == fp.LOOKUP_PARTIAL
        assert block["reason"] is None
        assert block["lookup"]["ran"] == {"point": 6}

    def test_more_unresolved_than_candidates_is_refused(self):
        with pytest.raises(ValueError):
            fp.plan_identity(_search(), candidates={"points": 1},
                             unresolved={"points": 2})

    def test_an_unattributable_class_is_refused(self):
        with pytest.raises(ValueError):
            fp.plan_identity(_search(), candidates={"points": 1},
                             unresolved={"operators": 1})

    def test_malformed_counts_are_refused(self):
        for bad in ({"points": -1}, {"points": "2"}, {"points": True}):
            with pytest.raises(ValueError):
                fp.plan_identity(_search(), candidates=bad)


# ── 5. the seam: one key, one home ─────────────────────────────────────────


class TestQueryKey:
    def test_derive_queries_and_the_candidate_key_agree(self):
        """`_derive_queries` derives with `_query_key` and the record asks the
        same question with it. Two copies would let a candidate's key drift from
        the query that actually ran — and the drift is invisible (the record
        would just say "not run" about a query that ran)."""
        embed = {
            "entities": [{"name": "ACME Corp"}],
            "events": [{"content": "the team shipped v4"}],
            "points": [{"content": "the budget is 40k, not 90k"}],
        }
        queries = v2._derive_queries(embed, "")
        assert queries["object"] == ["ACME Corp"]
        assert v2._query_key("object", "ACME Corp") == \
            f"object:{v2._norm_sent(queries['object'][0])}"
        assert v2._query_key("point", "the budget is 40k, not 90k") == \
            f"point:{v2._norm_sent(queries['point'][0])}"

    def test_the_sanitiser_is_the_stored_form(self):
        """#1350 D3's sanitisation and the key share ONE home: what runs is what
        is keyed, including the `word:` field-prefix strip and punctuation."""
        assert v2._sanitise_query_text("issue: the budget isn't 40k!") == \
            "the budget isn t 40k"
        assert v2._query_key("point", "issue: the budget isn't 40k!") == \
            "point:the budget isn t 40k"
        assert v2._query_key("point", "   ") is None


# ── 6. the prompt render ───────────────────────────────────────────────────


class TestRenderSearchResults:
    def test_the_degraded_render_states_unresolved_not_new(self):
        rendered = v2._render_search_results(
            {"degraded": True, "reason": "S3 degraded"})
        assert _INVERTED_CLAUSE not in rendered
        assert "UNRESOLVED" in rendered
        assert "DEGRADED" in rendered

    def test_a_partial_lookup_keeps_its_real_hits(self):
        coverage = {"derived": {"object": 1, "point": 4}, "ran": {"object": 1, "point": 2},
                    "failed": {"object": 0, "point": 0}}
        rendered = v2._render_search_results(_search(
            query_coverage=coverage,
            entities=[{"id": "o1", "name": "ACME", "kind": "core:org"}]))
        assert "PARTIAL" in rendered
        assert "- o1 | ACME | core:org" in rendered, (
            "a partial lookup's hits are REAL and must still be shown")

    def test_a_resolved_lookup_renders_byte_identically(self):
        search = _search(entities=[{"id": "o1", "name": "ACME", "kind": "core:org"}],
                         points=[{"id": "p1", "content": "the budget is 40k",
                                  "kind": "statement"}])
        assert v2._render_search_results(search) == (
            "EXISTING ENTITIES (id | name | kind):\n- o1 | ACME | core:org"
            "\n\nEXISTING POINTS (id | content | kind):\n"
            "- p1 | the budget is 40k | statement")

    def test_an_empty_search_does_not_claim_a_measured_absence(self):
        """`{}` is reachable (`run_s4` from a tool, a test) and it is UNRESOLVED,
        not "nothing exists" — the same unmeasured absence claim this change
        removed from the degraded branch (#5095 review B3)."""
        rendered = v2._render_search_results({})
        assert "UNRESOLVED" in rendered, rendered
        # Direct, with NO carve-out. An earlier draft of this line read
        # `rendered.replace("no existing items available", "")` — which
        # deletes the very clause it claims to forbid, so it could never fail
        # (#5095 review round 1, ISSUE 2). The companion test below proves this
        # predicate rejects the pre-fix sentence rather than being vacuous.
        assert "no existing items" not in rendered, rendered
        # `None` too: a caller that passed nothing established nothing.
        assert "UNRESOLVED" in v2._render_search_results(None)

    def test_the_empty_branch_guard_bites_on_the_sentence_it_forbids(
            self, monkeypatch):
        """Non-vacuity for the assertion above (#5095 review round 1, ISSUE 2).

        A `not in` is only load-bearing if the phrase it forbids can reach the
        render at all. Route `origin/main`'s pre-fix clause through the note
        seam and show that it lands in the empty branch verbatim — so the guard
        above is a live assertion, not a tautology over a string that could
        never appear.
        """
        pre_fix = ("(graph search DEGRADED — skipped; no existing items "
                   "available; assume everything is new)")
        monkeypatch.setattr(v2._failure_policy, "lookup_note",
                            lambda _search: pre_fix)
        rendered = v2._render_search_results({})
        assert "no existing items" in rendered, (
            "the pre-fix clause must reach the empty branch, else the `not in` "
            "guard cannot bite", rendered)


# ── 7. the producer: search_graph reports coverage ─────────────────────────


class _FakeSDK:
    """A graph client whose query legs behave as the test specifies."""

    def __init__(self, fail_on: int | None = None):
        self.calls = 0
        self.fail_on = fail_on


@pytest.fixture()
def fake_search_backend(monkeypatch):
    """Make `search_graph` think a real backend is live, and stub the FTS leg."""
    monkeypatch.setattr(v2, "_is_searchable_backend", lambda mode, sdk=None: True)
    monkeypatch.setattr(v2, "resolve_backend_mode", lambda: "real")
    monkeypatch.setattr(v2, "_enrich_point_priors", lambda sdk, rows: None)


def _install_fts(monkeypatch, sdk, rows_by_call: int = 1):
    def _fts(_sdk, entity_type, query, limit=3, *, session_id=None):
        sdk.calls += 1
        if sdk.fail_on is not None and sdk.calls == sdk.fail_on:
            raise RuntimeError("graph blip")
        return []
    monkeypatch.setattr(v2, "_fts_rows", _fts)


class TestSearchGraphCoverage:
    def test_a_swallowed_later_failure_is_reported(self, monkeypatch, fake_search_backend):
        """The REACHABLE incomplete shape: query 1 succeeds, query 2 raises,
        `degraded` stays False, and the prior set is partial. Before this change
        that shape was indistinguishable from a complete lookup."""
        sdk = _FakeSDK(fail_on=2)
        _install_fts(monkeypatch, sdk)
        embed = {"entities": [{"name": "ACME"}, {"name": "Initech"}]}
        out = v2.search_graph(sdk, embed, "", max_queries=10)
        assert out["degraded"] is False, (
            "one failed query is not a dead graph — the degrade path must not fire")
        assert out["query_coverage"]["derived"]["object"] == 2
        assert out["query_coverage"]["ran"]["object"] == 1
        assert out["query_coverage"]["failed"]["object"] == 1
        assert out["query_results"]["failed"], "the failed KEY must be recorded"
        assert fp.lookup_state(out) == fp.LOOKUP_PARTIAL

    def test_the_budget_starves_the_last_leg_and_says_so(self, monkeypatch, fake_search_backend):
        sdk = _FakeSDK()
        _install_fts(monkeypatch, sdk)
        embed = {"entities": [{"name": "a"}, {"name": "b"}],
                 "points": [{"content": "p1"}, {"content": "p2"}]}
        out = v2.search_graph(sdk, embed, "", max_queries=2)
        assert out["query_coverage"]["ran"]["object"] == 2
        assert out["query_coverage"]["ran"]["point"] == 0
        assert fp.lookup_state(out) == fp.LOOKUP_PARTIAL
        assert "point" in fp.lookup_note(out)

    def test_a_complete_lookup_is_resolved(self, monkeypatch, fake_search_backend):
        sdk = _FakeSDK()
        _install_fts(monkeypatch, sdk)
        out = v2.search_graph(sdk, {"entities": [{"name": "ACME"}]}, "",
                              max_queries=15)
        assert fp.lookup_state(out) == fp.LOOKUP_RESOLVED

    def test_a_degraded_lookup_reports_zero_coverage(self, monkeypatch, fake_search_backend):
        sdk = _FakeSDK(fail_on=1)
        _install_fts(monkeypatch, sdk)
        out = v2.search_graph(sdk, {"entities": [{"name": "ACME"}]}, "",
                              max_queries=15)
        assert out["degraded"] is True
        assert out["query_results"] == {"ran": [], "failed": []}
        assert fp.lookup_state(out) == fp.LOOKUP_UNRESOLVED
        # and the measured fact the whole design rests on: zero priors
        assert not out["entities"] and not out["points"] and not out["events"]


# ── 8. the per-candidate attribution ───────────────────────────────────────


class TestCandidateCovered:
    def test_a_ran_key_is_covered_and_an_unrun_one_is_not(self):
        key = v2._query_key("point", "the budget is 40k")
        search = _search(query_results={"ran": [key], "failed": []})
        assert v2._candidate_covered(search, "point", "the budget is 40k") is True
        assert v2._candidate_covered(search, "point", "the budget is 90k") is False, (
            "a candidate whose own query never ran must not inherit another "
            "candidate's coverage — that is a run-level answer to a "
            "candidate-level question")

    def test_no_query_results_falls_back_to_the_run_state(self):
        resolved = _search()
        assert v2._candidate_covered(resolved, "object", "ACME") is True
        assert v2._candidate_covered({}, "object", "ACME") is False

    def test_a_candidate_with_no_searchable_text_was_never_tested(self):
        key = v2._query_key("point", "x")
        search = _search(query_results={"ran": [key], "failed": []})
        assert v2._candidate_covered(search, "point", "   ") is False


class TestTheReportCannotContradictItself:
    """Two ways the run's report could contradict the run's own records.

    *(review B1)* `execute_embed` COERCES a non-string falsy content (`None`,
    `0`, `False`) to its text and writes it, while the query derivation skipped
    it as falsy — so such a candidate is committed and was never checked. The
    report used to skip it, and published `resolved` beside a record saying
    `found: null`.

    *(review B2)* the budget can cut only the story-topic queries that follow
    every candidate query, so `partial` with NOTHING unchecked is the ordinary
    shape — and a warning that then says candidates "were recorded as
    UNRESOLVED" is false about that run's records.
    """

    def test_a_coerced_falsy_candidate_is_reported_unresolved(self):
        candidates = {"entities": [{"name": "ACME Corp"}],
                      "points": [{"content": None, "pointKind": "statement"}]}
        ran = [v2._query_key("object", v2._candidate_query_text("object", "ACME Corp"))]
        report = v2._identity_report(
            _search(query_results={"ran": ran, "failed": []}), candidates)
        assert report["candidates"]["points"] == 1
        assert report["unresolved"]["points"] == 1, report
        assert report["state"] == fp.LOOKUP_PARTIAL, (
            "a Point that S5 will commit as 'None' with no derived query must "
            "not leave the run reading 'resolved'")
        # ...and the record agrees, which is the whole point of the assertion.
        # The search carries a real `query_results` log (what `search_graph`
        # always supplies): the derived query set holds the candidates S3 saw, and
        # the coerced `None` -> "None" candidate is not among them.
        ran = [v2._query_key("object", v2._candidate_query_text("object", "ACME Corp"))]
        result = v2.execute_embed(
            {"entities": [], "events": [], "operators": [], "chain_notes": [],
             "points": [{"content": None, "pointKind": "statement"}]},
            _search(query_results={"ran": ran, "failed": []}), session_id="s1")
        point_recs = [r for r in result["link_before_create"]
                      if str(r["searched_for"]).startswith("point ")]
        assert point_recs and point_recs[0]["found"] is None, point_recs

    def test_a_partial_lookup_with_nothing_unchecked_says_so(self):
        out = v2.extract_session_v2(_FakeModel(), _conv(), session_id="s1")
        assert out["identity"]["unresolved_total"] >= 1
        messages = [w for w in out["warnings"] if "S3 identity" in w]
        assert messages, out["warnings"]
        assert "did not run for" in messages[0]
        # ⛔ and it must NOT claim what the candidate's own record says: a
        # candidate whose query was cut can still be folded onto a prior by S5
        # (`found: true`), so "their record says UNRESOLVED" was a false claim
        # about that record (#5095 review round 3).
        assert "their record says" not in messages[0]
        # the zero-numerator shape is a DIFFERENT sentence, never the one above
        partial_zero = fp.plan_identity(
            _search(query_coverage={"derived": {"point": 13},
                                    "ran": {"point": 12}, "failed": {"point": 0}}),
            candidates={"points": 3}, unresolved={"points": 0})
        assert partial_zero["state"] == fp.LOOKUP_PARTIAL
        assert partial_zero["unresolved_total"] == 0


# ── 9. the record the caller keeps ─────────────────────────────────────────

_EMBED = {
    "entities": [{"name": "ACME Corp", "kind": "core:org", "lifecycle": "created"}],
    "events": [],
    "points": [{"content": "the budget is 40k", "pointKind": "statement"}],
    "operators": [],
    "chain_notes": [],
    "link_before_create": [],
}


def _records(result: dict) -> list[dict]:
    return [rec for rec in result["link_before_create"]
            if rec.get("note") == "no match — created"
            or str(rec.get("found")) == "None"]


class TestTheSharedCandidateText:
    """#5095 review B2: one home for the text a candidate's query is built from.

    `_derive_queries` used to cut the RAW text to 80 and strip afterwards, while
    `execute_embed` stripped first and then took `content[:80]`. For a long point
    whose raw text begins with whitespace those are different strings, so the
    query that RAN and the key asked for at commit time differed — and the record
    then said *"the lookup did not run"* about a lookup that ran.
    """

    def test_strip_then_cut_is_the_derivation_order(self):
        raw = "   " + "x" * 100
        assert v2._candidate_query_text("point", raw) == "x" * 80
        assert v2._candidate_query_text("point", ("  " + "y" * 70)) == "y" * 70
        # an entity name is not cut: two long names must not collapse onto one key
        long_a = "ACME Corporation International Holdings Division " + "A" * 60
        long_b = "ACME Corporation International Holdings Division " + "B" * 60
        assert v2._candidate_query_text("object", long_a) != \
            v2._candidate_query_text("object", long_b)

    def test_a_whitespace_prefixed_long_point_is_still_covered(self):
        """The exact reproduction: the derived/run key is built from the SAME
        window, so the record says `no match` — not `never looked`."""
        raw = "   " + "the budget is 40k " + "x" * 90
        key = v2._query_key("point", v2._candidate_query_text("point", raw))
        assert key is not None
        search = _search(query_results={"ran": [key], "failed": []})
        assert v2._candidate_covered(
            search, "point", v2._candidate_query_text("point", raw)) is True
        embed = {"entities": [], "events": [], "operators": [], "chain_notes": [],
                 "points": [{"content": raw, "pointKind": "statement"}]}
        result = v2.execute_embed(embed, search, session_id="s1")
        rec = next(r for r in result["link_before_create"]
                   if r["searched_for"].startswith("point "))
        assert rec["found"] is False, rec


class TestExecuteEmbedRecord:
    def test_an_unresolved_lookup_records_NO_match_claim(self):
        result = v2.execute_embed(_EMBED, {}, session_id="s1")
        assert result["link_before_create"], "every created candidate is recorded"
        for rec in result["link_before_create"]:
            assert rec["found"] is None, rec
            assert "UNRESOLVED" in rec["note"], rec

    def test_a_complete_lookup_still_records_no_match(self):
        """The other polarity, and the reason both are tested: a change that
        always said `None` would hide a real duplicate."""
        search = _search()
        result = v2.execute_embed(_EMBED, search, session_id="s1")
        assert result["link_before_create"]
        for rec in result["link_before_create"]:
            assert rec["found"] is False, rec
            assert rec["note"] == "no match — created", rec

    def test_a_partial_lookup_mixes_the_two_polarities(self):
        """The mixed run is the shape a run-level trigger cannot express: the
        entity's query ran, the point's did not."""
        ran = v2._query_key("object", "ACME Corp")
        search = _search(
            query_coverage={"derived": {"object": 1, "point": 4},
                            "ran": {"object": 1, "point": 0},
                            "failed": {"object": 0, "point": 0}},
            query_results={"ran": [ran], "failed": []},
        )
        result = v2.execute_embed(_EMBED, search, session_id="s1")
        by_ref = {rec["searched_for"]: rec for rec in result["link_before_create"]}
        entity = by_ref["entity 'ACME Corp'"]
        point = by_ref["point 'the budget is 40k'"]
        assert entity["found"] is False, entity
        assert point["found"] is None, point


class TestTheRemainingAbsence:
    """The MINT half of §4.4's I1, pinned as an ABSENCE — kept behind a call.

    `#5095` made the prompt and the record truthful; it deliberately did NOT
    suppress a write (the correct seam is a create gate inside S5, and
    `execute_embed({})` also drops retractions, supersessions and chain notes).
    So the run still mints a candidate whose identity was never established, and
    that is the whole of what remains missing from `failure_policy`.

    ⛔ This test exists to make the absence FALSIFIABLE rather than narrated: the
    day a create gate lands, the payload below is empty for an unresolved lookup,
    this reds, and `config/v4-mechanisms.yml`'s row must flip to `implemented`.
    A register whose absence claim no call can see is the drift the register
    exists to prevent (#5095 review A3).
    """

    def test_an_unresolved_lookup_still_mints(self):
        result = v2.execute_embed(_EMBED, {}, session_id="s1")
        payload = result["payload"]
        assert payload is not None
        assert payload["points"], (
            "no Point was committed for an UNRESOLVED lookup — if a create gate "
            "landed, flip `failure_policy` to `implemented` in "
            "config/v4-mechanisms.yml and re-point its note")
        assert payload["entities"], "no entity was committed for an UNRESOLVED lookup"


class TestS5sOwnAdmissionTest:
    """A candidate S5 DROPS is not a candidate; one it COERCES and writes is.

    (#5095 review round 3.) `execute_embed` writes `str(value).strip()` when that
    is non-empty — so a `None` content is committed as `"None"` — and drops `""`
    and whitespace-only content on `if not content: continue`. The report must use
    the SAME test, or it fires on a healthy run and asserts a record the run never
    emitted (that was the empty case), or it hides a committed candidate (that was
    the coerced one, fixed in round 2).
    """

    def test_a_whitespace_or_empty_candidate_is_not_counted(self):
        candidates = {"points": [{"content": "the budget is 40k", "pointKind": "statement"},
                                 {"content": "   ", "pointKind": "statement"},
                                 {"content": "", "pointKind": "statement"}]}
        report = v2._identity_report(_search(), candidates)
        assert report["candidates"]["points"] == 1, report
        assert report["unresolved"]["points"] == 0, report
        assert report["state"] == fp.LOOKUP_RESOLVED, (
            "a candidate S5 will DROP must not flip a healthy run to `partial`")

    def test_a_whitespace_candidate_gets_no_record_and_no_point(self):
        embed = {"entities": [], "events": [], "operators": [], "chain_notes": [],
                 "points": [{"content": "   ", "pointKind": "statement"}]}
        result = v2.execute_embed(embed, _search(), session_id="s1")
        assert result["payload"]["points"] == []
        assert result["link_before_create"] == []

    def test_the_two_shapes_are_one_predicate(self):
        """`_candidate_text` IS the writer's coercion: `None` -> "None", empty -> ""."""
        assert v2._candidate_text(None) == "None"
        assert v2._candidate_text("") == ""
        assert v2._candidate_text("   ") == ""
        assert v2._candidate_text(" claim ") == "claim"


# ── 10. the run's own report ───────────────────────────────────────────────


class _FakeModel:
    """Enough of the adapter for a full `extract_session_v2` run, no network."""

    last_finish_reason = None

    def complete(self, *, system, user, max_tokens=None):
        self.last_finish_reason = "stop"
        if "STORY SUMMARIZER" in system:
            return "A narrative."
        if "GAP REVIEWER" in system:
            return ('{"entities": [], "events": [], "operators": [], "points": [], '
                    '"chain_notes": [], "link_before_create": []}')
        return ('{"entities": [{"name": "ACME Corp", "kind": "core:org", '
                '"lifecycle": "created"}], "events": [], "operators": [], '
                '"points": [{"content": "the budget is 40k", '
                '"pointKind": "statement"}], "chain_notes": [], '
                '"link_before_create": []}')


def _conv():
    return [{"role": "user", "content": "the budget is 40k"},
            {"role": "assistant", "content": "noted"}]


class TestIdentityOnTheResult:
    def test_an_unresolved_run_reports_and_warns(self):
        out = v2.extract_session_v2(_FakeModel(), _conv(), session_id="s1")
        block = out["identity"]
        assert block["state"] == fp.LOOKUP_UNRESOLVED
        assert block["candidates"]["entities"] >= 1
        assert block["candidates"]["points"] >= 1
        assert block["unresolved_total"] >= 1
        assert any("S3 identity" in w and "did not run for" in w
                   for w in out["warnings"]), out["warnings"]
        # I2: nothing was DROPPED — the candidates are still in the list.
        assert out["embed_list"]["entities"], (
            "the change must never suppress the candidate list")
        assert out["embed_list"]["points"]

    def test_the_block_is_present_on_the_empty_conversation_path(self):
        """The always-present rule (the same one `error_census` obeys) — this
        branch hand-enumerates its keys, and it is the MOST unresolved run."""
        out = v2.extract_session_v2(_FakeModel(), [], session_id="s1")
        assert "identity" in out, sorted(out)
        assert out["identity"]["state"] == fp.LOOKUP_UNRESOLVED
        assert out["identity"]["unresolved_total"] == 0
        assert not any("S3 identity" in w for w in out["warnings"]), (
            "an empty run has no candidates to warn about")

    def test_a_budget_cut_that_spares_every_candidate_gets_its_own_sentence(self):
        """The ordinary shape: the budget cuts only trailing story-topic queries,
        so `partial` with NOTHING unchecked. Claiming "the lookup did not run over
        the candidate set" there contradicted the block's own counters (#5095
        review round 3)."""
        original_search = v2.search_graph
        cover = {"derived": {"object": 1, "point": 3},
                 "ran": {"object": 1, "point": 2}, "failed": {"object": 0, "point": 0}}
        keys = [v2._query_key("object", "ACME Corp"),
                v2._query_key("point", "the budget is 40k")]
        v2.search_graph = lambda *a, **k: _search(
            query_coverage=cover,
            query_results={"ran": [k for k in keys if k], "failed": []})
        try:
            out = v2.extract_session_v2(_FakeModel(), _conv(), session_id="s1")
        finally:
            v2.search_graph = original_search
        assert out["identity"]["state"] == fp.LOOKUP_PARTIAL
        assert out["identity"]["unresolved_total"] == 0, out["identity"]
        messages = [w for w in out["warnings"] if "S3 identity" in w]
        assert messages, out["warnings"]
        assert "part of the lookup did not run or did not complete" in messages[0], messages[0]
        assert "no candidate was left unchecked" in messages[0], messages[0]
        assert "did not run over the whole candidate set" not in messages[0]

    def test_a_resolved_run_reports_resolved_and_does_not_warn(self):
        """The flag-side of R1: the disposition must not fire on a healthy run.

        `sdk=None` degrades by construction, so a real graph is simulated with a
        stub client whose FTS leg answers with nothing.
        """
        class _SDK:
            pass

        original_search = v2.search_graph
        v2.search_graph = lambda *a, **k: _search()
        try:
            out = v2.extract_session_v2(_FakeModel(), _conv(), sdk=_SDK(),
                                        session_id="s1")
        finally:
            v2.search_graph = original_search
        assert out["identity"]["state"] == fp.LOOKUP_RESOLVED
        assert out["identity"]["unresolved_total"] == 0
        assert not any("S3 identity" in w for w in out["warnings"])
