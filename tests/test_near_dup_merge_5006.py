"""#5006 — near-duplicate MERGE (D12/O4): the FUSION, and its REFUSALS.

WHAT THIS PINS
--------------
The design of record (`EXTRACTOR-V4-ARCHITECTURE.md` §4.2) splits the
near-duplicate concern across three sites and says so explicitly — *"so no
operation has two homes"*:

    DETECT  S2.2a-β  mechanical   `extractor_v2._boundary`        (#5080)
    VERDICT S2.2b    the arbiter  `vet_gate.OUTCOMES``MERGE`      (#5005)
    FUSION  S3       this test    `tortoise/near_dup_merge.py`    (#5006)

Before this change the third site had no implementation and the verdict had no
consumer, which is why `config/v4-mechanisms.yml` recorded `near_dup_merge` as
`state: not-implemented, tracking_issue: 5006`.

TWO ARMS, AND THE SECOND IS THE ONE THAT MATTERS
------------------------------------------------
1. **The merge arm** — a flagged, near-identical pair lands as ONE entity
   carrying the UNION of both sides' attachments, and the losing document's
   evidence is still reachable (its quote on the merge record, its session on
   the survivor). *Mutant it catches: a fusion that keeps the prior and DROPS
   the candidate's attachments — which is the de-facto merge #5006 says the
   `NOOP_MIN_OVERLAP` band was already performing by accident.*
2. **The refusal arm** — the same pair is NOT merged when the verdict is
   absent, when a value or identity difference is present, when the overlap is
   below the ~0.95 bar, or when the union cannot be computed. *Mutant it
   catches: a merge that fires on the mechanical gate alone (no arbiter), or
   one that fuses across a differing number — the §16.4 never-across list, and
   the outcome the ruling says costs more than a kept near-duplicate.*

⚠️ **The `0.95` bar is deliberately far above the NOOP band (`0.45`).** The
refusal these mechanisms differ on is narrow on purpose: `#9424` made the
paraphrase band REFUSE a fold that would drop an entity, and this mechanism is
the authorised alternative for that case only when the pair is near-IDENTICAL.
A mid-band paraphrase pair still falls through to `ADD` (both claims survive),
and `test_a_mid_band_paraphrase_is_still_not_merged` pins that boundary.

A THIRD PROPERTY, PINNED BECAUSE IT IS THE ONE THE FUSION SILENTLY LOSES
-----------------------------------------------------------------------
The union is COMPARED case-folded and WRITTEN case-preserved. `Object.name` is
stored verbatim (`projection/entities.py`, and capture's own attachment pass
resolves with `name.strip()` at `sdk.py:12123`), so a union emitted in the
lowered form resolves NOTHING on ordinary data: the `MATCH (o:Object {name:$n})`
returns no rows, the candidate's exclusive attachment lands in `unresolved`, and
the record still claims a union that never reached the graph. Every graph-level
fixture here is therefore CAPITALISED, which is the shape real data carries —
lowercase names make the defect an identity operation and hide it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100
from tortoise import near_dup_merge as ndm  # noqa: E402, RUF100
from tortoise import vet_gate  # noqa: E402, RUF100

#: ONE claim, two documents. The prior names `team`; the second document names
#: `team AND office`. Identical wording is the near-identical case the bar
#: admits — the #9424 guard refuses it as a fold (the candidate's `office` link
#: would be dropped), and the fusion is the authorised alternative.
PRIOR_CONTENT = "the team meets weekly in main office"
CANDIDATE_CONTENT = "the team meets weekly in main office"

#: A candidate LONGER than `extractor_v2._MAX_CONTENT` (1000). `execute_embed`
#: keys its consolidation on `content[:_MAX_CONTENT]` while VET's `_item_text`
#: never truncates, so this length is the boundary at which a text-only routing
#: key silently stops matching (#5006 review, F1). No sentence punctuation: the
#: length is the variable under test, not E3's multi-sentence warning.
LONG_CONTENT = ("the team meets weekly in the main office " * 40).strip()


def _prior(**kw) -> dict:
    row = {"id": "pt_prior", "content": PRIOR_CONTENT,
           "about_entities": ["team"]}
    row.update(kw)
    return row


def _candidate(**kw) -> dict:
    row = {"content": CANDIDATE_CONTENT, "about_entities": ["team", "office"]}
    row.update(kw)
    return row


def _classify(prior: dict, candidate: dict, *, flagged: bool):
    return v2.classify_consolidation(
        candidate, [prior], entity_mentions=["team"], merge_flagged=flagged)


# ── 0. The vocabulary is shared, not respelled ─────────────────────────────


def test_the_verdict_token_matches_vet_gate():
    """`near_dup_merge` CONSUMES the S2.2b vocabulary; it does not own it.

    The constant is duplicated (this module must stay importable without
    `vet_gate`) so the drift is possible — this is the pin that makes it loud
    instead of silent.
    """
    assert ndm.MERGE == vet_gate.MERGE
    assert ndm.MERGE in vet_gate.OUTCOMES


def test_the_bar_is_the_evidence_driven_one():
    """§16.4: *"Practical thresholds cluster at ~0.95"* — and the direction of
    error is asymmetric. Lowering this is a DECISION (it re-opens "merging can
    lose information"), not a tuning knob, so the value is pinned."""
    assert ndm.MERGE_MIN_OVERLAP == 0.95
    assert ndm.MERGE_MIN_OVERLAP > v2.NOOP_MIN_OVERLAP


# ── 1. THE MERGE ARM ───────────────────────────────────────────────────────


class TestThePlanMergesANearIdenticalFlaggedPair:
    def test_union_of_both_attachment_sets(self):
        plan = ndm.near_dup_merge(
            _candidate(), _prior(), overlap=1.0, difference=None,
            identity=frozenset(), flagged=True)
        assert plan.merge is True, plan.evidence
        assert plan.prior_id == "pt_prior"
        assert plan.union_entities == ("office", "team")
        assert plan.added_entities == ("office",)
        assert plan.reason == "near_identical"

    def test_the_union_carries_the_spelling_the_graph_stores(self):
        """COMPARISON is case-folded; the WRITE is not.

        `Object.name` is stored case-preserved (`projection/entities.py` MERGEs
        on the verbatim name, capture's own attachment pass resolves with
        `name.strip()` — `sdk.py:12123`). A union carrying the lowered form is
        a union that resolves NOTHING on ordinary data, so the candidate's
        exclusive attachment is dropped while the record claims it landed.
        *Mutant it catches: `_entity_names` returning the normalised form as
        the emitted name — a one-line change that turns every real merge into
        an `unresolved` no-op.*
        """
        plan = ndm.near_dup_merge(
            _candidate(about_entities=["Team", "Office"]),
            _prior(about_entities=["Team"]),
            overlap=1.0, difference=None, identity=frozenset(), flagged=True)
        assert plan.merge is True, plan.evidence
        assert plan.union_entities == ("Office", "Team")
        assert plan.added_entities == ("Office",)

    def test_whitespace_is_stripped_and_case_is_only_identity(self):
        """The two halves of `_entity_names`, each doing its own job.

        Whitespace is presentation and is stripped; case is identity for the
        COMPARISON (a `Team` the prior already carries adds nothing, so the
        pair is NOOP's) but must survive into the spelling that gets written.
        """
        plan = ndm.near_dup_merge(
            _candidate(about_entities=["Team", " OFFICE "]), _prior(),
            overlap=1.0, difference=None, identity=frozenset(), flagged=True)
        assert plan.merge is True, plan.evidence
        # `Team` is the prior's own entity in another case -> not "added";
        # `OFFICE` is new, and it is emitted without its padding.
        assert plan.added_entities == ("OFFICE",)
        assert plan.union_entities == ("OFFICE", "team")

        # …and the prior's spelling wins for a name both sides carry.
        same = ndm.near_dup_merge(
            _candidate(about_entities=["TEAM"]), _prior(),
            overlap=1.0, difference=None, identity=frozenset(), flagged=True)
        assert same.merge is False
        assert same.reason == "nothing_to_union"


class TestTheClassifierProducesMerge:
    #: The PRIOR's claim, and a PARAPHRASE of it: the same words in a different
    #: order. Not an exact normalized match (so it cannot take step 1's
    #: exact-content shortcut), `_boundary` reports no distinguishing difference,
    #: and `_token_overlap` is 1.0 — so this is the shape the ~0.95 BAR exists
    #: for. A *truncated* paraphrase is NOT usable as a fixture: removing a word
    #: is a `scope` difference to `_boundary`, so it is refused for a different
    #: (also correct) reason and would not exercise the band at all.
    PARAPHRASE_PRIOR = "the team meets weekly in the main office"
    PARAPHRASE_TEXT = "the team meets weekly in the office main"

    def test_the_paraphrase_band_merges_a_near_identical_flagged_pair(self):
        """The BAR's own arm, not the exact-content shortcut.

        Every positive MERGE test in this file used IDENTICAL content, so only
        step 1's exact normalized match was ever exercised end to end. Measured:
        disabling the band's `return DecisionRecord("MERGE", …)` at
        `best_merge is not None` left 95 tests green — i.e. the bar #5006 says
        must sit at ~0.95 was unpinned, and a near-identical paraphrase whose
        fold would drop an attachment would silently take ADD and mint the
        second Point this whole mechanism exists to prevent.
        """
        prior = {"id": "pt_p", "content": self.PARAPHRASE_PRIOR,
                 "about_entities": ["Team"]}
        candidate = {"content": self.PARAPHRASE_TEXT,
                     "about_entities": ["Team", "Office"]}
        # the three premises, asserted so the fixture cannot rot into an
        # accidental exact match (which would pass this test for the wrong arm)
        assert v2._norm(candidate["content"]) != v2._norm(prior["content"])
        assert v2._boundary(prior["content"],
                            candidate["content"]) == (None, frozenset())
        assert v2._token_overlap(prior["content"],
                                 candidate["content"]) >= ndm.MERGE_MIN_OVERLAP

        d = v2.classify_consolidation(
            candidate, [prior], entity_mentions=["Team", "Office"],
            merge_flagged=True)
        assert d.decision == "MERGE", d.evidence
        assert d.reason == "near_dup_merge"
        assert d.prior_id == "pt_p"
        assert set(d.union_entities) == {"Office", "Team"}

        # …and the VERDICT is what admits it: the same pair, no verdict, keeps
        # both claims (the pre-#5006 behaviour, deliberately unchanged)
        unflagged = v2.classify_consolidation(
            candidate, [prior], entity_mentions=["Team", "Office"])
        assert unflagged.decision == "ADD", unflagged.evidence

    def test_a_tie_between_a_fold_and_a_merge_goes_to_the_merge(self):
        """The documented tie-break (`best_noop[0] > best_merge[0]`), pinned.

        Two priors at the SAME overlap: one whose attachment set already covers
        the candidate (a lossless fold — NOOP), one whose does not (a merge with
        a union — MERGE). The comment at the comparison says ties go to MERGE,
        because it preserves the union where NOOP preserves only the prior's set
        and both are lossless. Unpinned, `>` could become `>=` and silently
        prefer the narrower write.
        """
        covered = {"id": "pt_covered", "content": self.PARAPHRASE_PRIOR,
                   "about_entities": ["Team", "Office"]}
        lossy = {"id": "pt_lossy", "content": self.PARAPHRASE_PRIOR,
                 "about_entities": ["Team"]}
        candidate = {"content": self.PARAPHRASE_TEXT,
                     "about_entities": ["Team", "Office"]}

        d = v2.classify_consolidation(
            candidate, [covered, lossy], entity_mentions=["Team", "Office"],
            merge_flagged=True)
        assert d.decision == "MERGE", d.evidence
        assert d.prior_id == "pt_lossy"
        assert set(d.union_entities) == {"Office", "Team"}

    def test_flagged_pair_merges_onto_the_prior_and_carries_the_union(self):
        d = _classify(_prior(), _candidate(), flagged=True)
        assert d.decision == "MERGE", d.evidence
        assert d.prior_id == "pt_prior"
        assert d.reason == "near_dup_merge"
        # the union is on the DECISION, because the write path needs it to
        # stamp the survivor's attachments — a MERGE without it would fuse the
        # claim and drop the evidence, which is the forbidden outcome.
        assert d.union_entities == ("office", "team")

    def test_the_claim_is_never_fused(self):
        """§S2.2: *"union the attachments, NEVER fuse the claim"*.

        The decision names the PRIOR and emits no content of its own — the
        survivor's text stays byte-identical. A `MERGE` that produced merged
        text would be `FUSE`, which O4 forbids.
        """
        d = _classify(_prior(), _candidate(), flagged=True)
        assert d.decision == "MERGE"
        assert not hasattr(d, "content")
        # the prior's text is untouched by the decision, and `execute_embed`
        # records exactly that text (pinned below in the write-path test).


class TestTheWritePathEmitsOneEntityAndKeepsTheLosersEvidence:
    """THE END-TO-END CASE: two near-duplicate documents → ONE entity."""

    def test_payload_creates_no_second_point(self):
        # ⚠️ Capitalised on purpose: the store's `Object.name` is
        # case-preserved, so a union that lowercases resolves nothing. Lowercase
        # fixture names made the case bug an identity operation and hid it.
        prior = _prior(pointKind="statement", about_entities=["Team"])
        embed = {
            "entities": [{"name": "Team", "kind": "core:team"},
                         {"name": "Office", "kind": "core:location"}],
            "points": [{"content": CANDIDATE_CONTENT,
                        "pointKind": "statement",
                        "about_entities": ["Team", "Office"],
                        "quote": CANDIDATE_CONTENT}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
        }
        result = v2.execute_embed(
            embed, {"points": [prior], "entities": []},
            session_id="s_second_document", session_date="2026-10-24",
            merge_flagged=frozenset({v2._norm(CANDIDATE_CONTENT)}))

        # ONE entity: no payload point is emitted for the candidate at all.
        assert result["payload"]["points"] == []
        assert result["stats"]["merges"] == 1
        (record,) = result["merges"]

        # …and the survivor is the PRIOR, with the union the write path needs.
        assert record["point_id"] == "pt_prior"
        # the spelling is the GRAPH's, not a normalised form: these names are
        # what `apply_merge_union` resolves with an exact `Object {name:$n}`.
        assert record["about_entities"] == ["Office", "Team"]

        # THE LOSING DOCUMENT IS NOT SILENTLY DROPPED. Its evidence goes:
        #   1. its own quote + content survive on the merge record, so the
        #      source span that argued for the claim is still readable;
        #   2. its session is named (`session_ref`), and the write path turns
        #      that into a `(Session)-[:CONTAINS]->(Point)` edge — the losing
        #      document becomes a member of the claim's memory rather than a
        #      discarded row;
        #   3. the entity it alone named (`Office`) is in the union, so the
        #      attachment it contributed is not dropped either.
        assert record["merged_quote"] == CANDIDATE_CONTENT
        assert record["merged_content"] == CANDIDATE_CONTENT
        assert record["session_ref"] == "s_second_document"
        assert "Office" in record["about_entities"]
        # the surviving claim is the prior's, byte-identical — never fused
        assert record["content"] == PRIOR_CONTENT

    def test_a_verdict_on_a_candidate_over_the_cap_still_merges(self):
        """F1's END-TO-END arm: route → `execute_embed` really fuses.

        The unit test above pins the key; this pins the CONSEQUENCE the finding
        named. Pre-fix the verdict routed (unclipped) and the write path probed
        (clipped), so the two never met and the pair was ADDed — one extra Point
        for a pair the arbiter had already ruled identical.

        The PRIOR carries the clipped content on purpose: `execute_embed` stores
        `content[:_MAX_CONTENT]`, so a real graph point is never longer than the
        cap. The divergence the finding is about is therefore in the ROUTING
        key alone (1720-char verdict vs 1000-char probe), not in the claim
        comparison — the classifier compares 1000 against 1000 and sees 1.0.
        """
        prior = _prior(pointKind="statement",
                       content=LONG_CONTENT[:v2._MAX_CONTENT],
                       about_entities=["Team"])
        embed = {
            "entities": [{"name": "Team", "kind": "core:team"},
                         {"name": "Office", "kind": "core:location"}],
            "points": [{"content": LONG_CONTENT,
                        "pointKind": "statement",
                        "about_entities": ["Team", "Office"],
                        "quote": LONG_CONTENT}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
        }
        # The verdict as `_run_vet_pass` emits it — the UNCLIPPED text keyed
        # through the real routing function, not a hand-built key.
        flagged = v2._merge_flagged_contents({"decisions": {"points:0": {
            "section": "points", "outcome": "MERGE",
            "text": LONG_CONTENT}}})
        result = v2.execute_embed(
            embed, {"points": [prior], "entities": []},
            session_id="s_second_document", session_date="2026-10-24",
            merge_flagged=flagged)

        assert result["stats"]["merges"] == 1, result.get("warnings")
        assert result["payload"]["points"] == []
        (record,) = result["merges"]
        assert record["point_id"] == "pt_prior"

    def test_a_candidate_carrying_a_name_is_keyed_the_way_vet_keys_it(self):
        """F1's other half: `_item_text` is `name or content`.

        `vet_gate._item_text` follows the extractor's own rule (`name or
        content`) for every section, while the point write path keys on
        `content`. An item carrying BOTH is therefore keyed on its NAME by VET,
        and a consumer probing only `content` drops the verdict — the same
        silently-minted duplicate as the clip above, reached by a different
        field.
        """
        item_text = "the team meets weekly in main office, per Dana"
        flagged = v2._merge_flagged_contents({"decisions": {"points:0": {
            "section": "points", "outcome": "MERGE", "text": item_text}}})
        embed = {
            "entities": [], "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"name": item_text,          # ← what VET keyed on
                        "content": CANDIDATE_CONTENT,
                        "pointKind": "statement",
                        "about_entities": ["Team", "Office"],
                        "quote": CANDIDATE_CONTENT}],
        }
        result = v2.execute_embed(
            embed,
            {"points": [_prior(pointKind="statement",
                                about_entities=["Team"])], "entities": []},
            session_id="s_second_document", session_date="2026-10-24",
            merge_flagged=flagged)

        assert result["stats"]["merges"] == 1, result.get("warnings")
        assert result["payload"]["points"] == []

    def test_a_falsy_content_is_coerced_not_dropped(self):
        """#5060's contract, pinned through the line this change SPLIT.

        `str(p.get("content", ""))` is deliberate: #5060 made a falsy content
        WRITABLE as its coerced text (`None` → the claim "None", `0` → "0"),
        because `_parse_json_robust` rung 1 returns unvalidated JSON and the
        write gate, not the report, is the authority on what is writable.
        `tests/test_extractor_embed_coerce_5060.py` pins it.

        This test exists because the split into `_raw_content` (for the verdict
        key) + `content` is one keystroke from `str(p.get("content") or "")`,
        which collapses every falsy value to "" so the `if not content: continue`
        guard DROPS the item with no warning — silently reversing #5060. That
        regression was measured on this branch: the #5060 battery went 5 red.
        """
        embed = {"entities": [], "events": [], "operators": [],
                 "chain_notes": [], "link_before_create": [],
                 "points": [{"content": 0, "pointKind": "statement"},
                            {"content": None, "pointKind": "statement"}]}
        result = v2.execute_embed(
            embed, {"points": [], "entities": []},
            session_id="s_falsy", session_date="2026-10-24")
        assert {p["content"] for p in result["payload"]["points"]} == {
            "0", "None"}, result["payload"]["points"]

    def test_an_unflagged_embed_list_emits_no_merge_record(self):
        """The default is inert — the fusion is verdict-gated, not automatic."""
        prior = _prior(pointKind="statement")
        embed = {
            "entities": [], "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"content": CANDIDATE_CONTENT,
                        "pointKind": "statement",
                        "about_entities": ["team", "office"],
                        "quote": CANDIDATE_CONTENT}],
        }
        result = v2.execute_embed(
            embed, {"points": [prior], "entities": []},
            session_id="s_second_document", session_date="2026-10-24")
        assert result["merges"] == []
        assert result["stats"]["merges"] == 0


# ── 1b. The VERDICT's route (the gap #5006 calls "no consumer yet") ────────


class TestTheVetVerdictReachesTheFusion:
    """D12/O4's three sites are only joined if the middle one is ROUTED.

    `vet_gate` records `MERGE`; `extractor_v2._merge_flagged_contents` is the
    consumer that carries it to S3. Without this, the verdict stays a note in a
    stats dict — which is exactly the state #5006 recorded as the defect.
    """

    def test_a_merge_verdict_is_routed_by_normalised_content(self):
        surface = {"decisions": {
            "points:0": {"section": "points", "outcome": "MERGE",
                         "text": "The Team meets weekly in main office"},
            "points:1": {"section": "points", "outcome": "KEEP",
                         "text": "something else"},
        }}
        flagged = v2._merge_flagged_contents(surface)
        assert flagged == frozenset({v2._norm(PRIOR_CONTENT)})

    def test_the_arbiter_verdict_reaches_the_fusion_through_the_pipeline(
            self, monkeypatch):
        """THE JOIN: `extract_session_v2`'s hand-off into `execute_embed`.

        `execute_embed(..., merge_flagged=_merge_flagged_contents(vet_s2_stats,
        vet_union_stats))` is the ONE line that carries a S2.2b verdict to S3,
        and every other test here covers one SIDE of it: the producer (unit),
        the consumer (`execute_embed(merge_flagged=…)` called directly), and the
        capture seam — which monkeypatches `extract_session_v2` OUT, so it
        cannot see this line. Measured: replacing the argument with
        `frozenset()` left 677 tests green across this file,
        test_extractor_v2, test_vet_gate_5005, test_capture_session,
        test_ingest_v2_consolidation and test_capture_phase_d_dedup. The
        mechanism then reverts to exactly the defect #5006 closed — a verdict
        with no consumer — while the registry still says `implemented`.

        Layer coverage: the REAL `extract_session_v2` (its S2.2b VET included,
        with an injected arbiter) and the REAL `execute_embed`. Only S3's graph
        search is stubbed (`search_graph` → one prior): the embedded lane has no
        search backend, and the prior is the input the classifier needs. That is
        a stub of the GRAPH, not of the wiring under test.
        """
        import tortoise.extractor_v2 as ev2
        from tortoise import vet_gate as vg

        prior_text = "the team meets weekly in the main office"

        class _Model:
            last_finish_reason = "stop"

            def complete(self, *, system, user, max_tokens=None):
                if "STORY SUMMARIZER" in system:
                    return "A narrative that states one thing."
                if "GAP REVIEWER" in system:
                    return ('{"entities": [], "events": [], "operators": [], '
                            '"points": []}')
                return ('{"entities": [{"name": "Team", "kind": "core:team"},'
                        ' {"name": "Office", "kind": "core:location"}],'
                        ' "points": [{"content": "' + prior_text + '",'
                        ' "pointKind": "statement",'
                        # the candidate names an entity the PRIOR does not, so a
                        # fold would be LOSSY — that is what makes MERGE the
                        # admissible action instead of NOOP (`#9424`)
                        ' "about_entities": ["Team", "Office"]}],'
                        ' "events": [], "operators": []}')

        def _merge_the_point_points(cands, _story):
            return {"verdicts": [{"id": c["id"], "outcome": vg.MERGE,
                                  "rule_id": "test.merge", "reason": "test"}
                                 for c in cands if c["section"] == "points"]}

        monkeypatch.setenv("TORTOISE_VET", "1")
        monkeypatch.setattr(ev2, "search_graph", lambda *a, **k: {
            "mode": "embedded", "degraded": False, "reason": None,
            "entities": [], "events": [], "queries_run": 0,
            "points": [{"id": "pt_prior", "content": prior_text,
                        "about_entities": ["Team"]}]})

        out = ev2.extract_session_v2(
            _Model(), [{"role": "user", "content": prior_text}],
            vet_arbiter=_merge_the_point_points)

        assert out["stats"]["merges"] == 1, out.get("warnings")
        assert out["payload"]["points"] == [], out["payload"]["points"]
        (record,) = out["merges"]
        assert record["point_id"] == "pt_prior"

    def test_a_verdict_on_another_SECTION_does_not_authorise_a_point_merge(
            self):
        """The verdict is per-CANDIDATE, and VET decides three sections.

        `vet_gate.OUTCOMES` carries `MERGE` for entities/events/points alike and
        `_item_text` is `name or content` for all three, so an EVENT whose text
        equals a point's — or an ENTITY whose NAME does — would authorise a
        fusion of a Point the arbiter never marked if the key were text-only.
        That is a write authorised by a verdict recorded for a different
        candidate object: D12's "the verdict is the arbiter's" satisfied by a
        text collision.

        ⚠️ Scoped to a different KIND of candidate. A verdict on a different
        candidate of the SAME kind (two same-text point items) is NOT closed by
        this key — the hand-off drops the index deliberately, so that class is
        `extractor_v2._merge_flagged_contents`'s recorded residual, tracked on
        #9869. Do not read this test as covering it.
        """
        for section in ("events", "entities"):
            surface = {"decisions": {f"{section}:0": {
                "section": section, "outcome": "MERGE",
                "text": PRIOR_CONTENT}}}
            assert v2._merge_flagged_contents(surface) == frozenset(), section
        # …while the SAME text on the point section is the verdict it is
        points = {"decisions": {"points:0": {
            "section": "points", "outcome": "MERGE",
            "text": PRIOR_CONTENT}}}
        assert v2._merge_flagged_contents(points) == frozenset(
            {v2._norm(PRIOR_CONTENT)})

    def test_a_candidate_over_the_content_cap_is_still_routed(self):
        """F1 (#5006 review): the routing key must survive `_MAX_CONTENT`.

        `execute_embed` keys on `content[:_MAX_CONTENT]`; VET's `_item_text`
        never truncates. A text-only key therefore missed EVERY verdict on a
        candidate over 1000 characters — `merge_flagged` came back `False`, the
        `#9424` guard refused the fold, and the pair fell through to `ADD`: a
        SECOND Point minted for a pair the arbiter marked `MERGE`, with no
        warning and `stats["merges"]` still 0. Both spellings must be keyed,
        which is the same clip `vet_gate._resolution_variants` already keeps.
        """
        assert len(LONG_CONTENT) > v2._MAX_CONTENT
        surface = {"decisions": {"points:0": {
            "section": "points", "outcome": "MERGE",
            "text": LONG_CONTENT}}}
        flagged = v2._merge_flagged_contents(surface)
        # both spellings: the write path's clipped key AND VET's unclipped one
        assert v2._norm(LONG_CONTENT[:v2._MAX_CONTENT]) in flagged
        assert v2._norm(LONG_CONTENT) in flagged

    def test_the_point_section_is_vets_own_token(self):
        """The section key is VET's vocabulary, not a local invention.

        A rename in `vet_gate.SECTIONS` would otherwise silently stop every
        real verdict from routing (fail-closed, so the fusion would simply
        never fire) — a green battery and a dead mechanism.
        """
        assert v2._POINT_SECTION in {s[0] for s in vet_gate.SECTIONS}

    def test_both_vet_passes_contribute(self):
        s2 = {"decisions": {"a": {"section": "points", "outcome": "MERGE",
                                   "text": "alpha"}}}
        union = {"decisions": {"b": {"section": "points",
                                      "outcome": "merge", "text": "beta"}}}
        assert v2._merge_flagged_contents(s2, union) == frozenset(
            {v2._norm("alpha"), v2._norm("beta")})

    def test_malformed_surfaces_contribute_nothing(self):
        """Fail-closed: no readable verdict ⇒ no fusion."""
        for bad in (None, {}, {"decisions": None}, {"decisions": []},
                    {"decisions": {"a": "MERGE"}},
                    {"decisions": {"a": {"outcome": "MERGE"}}},
                    # a MERGE with no readable SECTION is not a point verdict
                    {"decisions": {"a": {"outcome": "MERGE",
                                            "text": "alpha"}}}):
            assert v2._merge_flagged_contents(bad) == frozenset(), bad


# ── 2. THE REFUSAL ARM — the mutation that matters ─────────────────────────


class TestTheSamePairIsNotMergedWithoutTheVerdict:
    """D12: the mechanical guard answers *"is anything distinguishing lost?"*.

    It cannot answer *"are these the same claim?"* — that is the arbiter's, so
    a clean mechanical pair with no `MERGE` verdict must NOT fuse. This is the
    arm that stops the mechanism from becoming "dedup by threshold" again, the
    exact accident #5006 describes.
    """

    def test_unflagged_pair_is_not_merged(self):
        d = _classify(_prior(), _candidate(), flagged=False)
        assert d.decision == "ADD", d.evidence
        assert d.union_entities == ()

    def test_the_plan_refuses_and_names_the_missing_verdict(self):
        plan = ndm.near_dup_merge(
            _candidate(), _prior(), overlap=1.0, difference=None,
            identity=frozenset(), flagged=False)
        assert plan.merge is False
        assert plan.reason == "not_flagged"


class TestTheNeverAcrossListIsAbsolute:
    """§16.4: a merge never crosses a number, a name, a negation, a condition,
    a date or a role inversion. Each case here IS a mergeable pair except for
    the one difference, so each is its own mutation test: a fusion that
    ignored the guard would merge two DISTINCT facts."""

    def test_a_differing_number_is_not_merged(self):
        # same shape, different quantity — `27:12` vs `26:40` are two facts.
        # ⚠️ It must not merely avoid MERGE: a differing value is the
        # SUPERSEDE channel (UPDATE), which keeps both nodes. A merge here
        # would silently collapse one fact into the other.
        d = v2.classify_consolidation(
            {"content": "the gym opens at 6pm", "about_entities": ["gym"]},
            [{"id": "pt_prior", "content": "the gym opens at 7pm",
              "about_entities": ["gym", "office"]}],
            entity_mentions=["gym"], merge_flagged=True)
        assert d.decision == "UPDATE", d.evidence
        assert d.union_entities == ()

    def test_a_negation_is_not_merged(self):
        d = v2.classify_consolidation(
            {"content": "the team does not meet weekly in main office",
             "about_entities": ["team"]},
            [_prior()], entity_mentions=["team"], merge_flagged=True)
        assert d.decision == "ADD", d.evidence
        assert d.union_entities == ()

    def test_the_plan_reports_the_dimension_it_refused_across(self):
        plan = ndm.near_dup_merge(
            _candidate(), _prior(), overlap=1.0, difference=None,
            identity=frozenset({"negation"}), flagged=True)
        assert plan.merge is False
        assert plan.reason == "distinguishing_difference"
        assert "negation" in plan.evidence

    def test_a_value_difference_is_not_merged(self):
        plan = ndm.near_dup_merge(
            _candidate(), _prior(), overlap=1.0, difference="number",
            identity=frozenset(), flagged=True)
        assert plan.merge is False
        assert plan.reason == "value_difference"


class TestTheBarAndTheUnionAreEnforcedMechanically:
    def test_a_mid_band_paraphrase_is_still_not_merged(self):
        """⚠️ The boundary of the mechanism, pinned.

        `#9424` refuses this fold (the candidate names `office`, the prior does
        not) and the pair is NOT near-identical, so it is NOT merge-eligible
        either — it falls through to `ADD` and BOTH claims survive. A future
        "lower the bar until the band merges" change reds here, which is the
        intent: §16.4 says a false merge costs more than a kept near-duplicate.
        """
        d = v2.classify_consolidation(
            {"content": "the team meets weekly",
             "about_entities": ["team", "office"]},
            [_prior()], entity_mentions=["team"], merge_flagged=True)
        assert d.decision == "ADD", d.evidence

    def test_below_the_bar_is_refused_by_the_plan(self):
        plan = ndm.near_dup_merge(
            _candidate(), _prior(), overlap=0.90, difference=None,
            identity=frozenset(), flagged=True)
        assert plan.merge is False
        assert plan.reason == "below_bar"

    def test_an_unreadable_attachment_set_refuses_the_merge(self):
        """FAIL-CLOSED here — the opposite of the `#9424` guard's fail-open.

        That guard only DECLINES to fold, so unknown ⇒ allow. This one WRITES:
        an unreadable set means the union cannot be computed, so there is
        nothing safe to write and both claims survive.
        """
        for bad in ({"team": 1}, None, "team"):
            plan = ndm.near_dup_merge(
                _candidate(about_entities=bad), _prior(), overlap=1.0,
                difference=None, identity=frozenset(), flagged=True)
            assert plan.merge is False, bad
            assert plan.reason == "unreadable_attachments", bad

    def test_nothing_to_union_falls_back_to_noop(self):
        """A pair the prior already covers is NOOP's — lossless and cheaper.

        Merging it would take the riskier write path for no gain, which is the
        `#9424` guard's own verdict: the fold is admissible, so it stays a fold.
        """
        plan = ndm.near_dup_merge(
            _candidate(about_entities=["team"]), _prior(), overlap=1.0,
            difference=None, identity=frozenset(), flagged=True)
        assert plan.merge is False
        assert plan.reason == "nothing_to_union"
        d = _classify(_prior(), _candidate(about_entities=["team"]),
                      flagged=True)
        assert d.decision == "NOOP", d.evidence


# ── 3. The FUSION against a real graph ─────────────────────────────────────


class TestApplyMergeUnionWritesTheUnionAndTheLostProvenance:
    """The half that makes "ONE merged entity with a preserved evidence trail"
    TRUE of the store, not just of the decision record."""

    @pytest.fixture
    def sdk(self, tmp_path):
        from tortoise.sdk import TortoiseSDK
        s = TortoiseSDK(str(tmp_path / "merge.db"))
        yield s
        s.close()

    def test_union_edges_and_the_losing_session_are_written(self, sdk):
        # ⚠️ The objects are CAPITALISED because that is the shape the graph
        # stores (`Object.name` is case-preserved) and the shape real data
        # carries. The merge record's `about_entities` is taken from a REAL
        # `near_dup_merge` plan, not hand-written, so this test walks the same
        # spelling the capture path emits: lowercase fixture names made the
        # case defect an identity operation, and a hand-written record could
        # not see it either way.
        # #9964: these fixtures need a real `:Object` (the merge resolves
        # `aboutObject` by name), and `core:team`/`core:naturalPerson` are
        # §5 SUBJECT kinds — the object door now routes them to a `:Subject`
        # (the kind decides the layer). The kind was incidental to what these
        # tests assert, so it is left unsupplied (the fold's `other` default).
        team = sdk.create_object("Team")
        office = sdk.create_object("Office", objectKind="core:location")
        pt = sdk.create_point("statement", PRIOR_CONTENT)
        prior_id = pt["id"]
        # The :Session node is S6's to create; the fusion only writes the edge
        # INTO it, so the test mints it directly (the same scoping the other
        # edge-class tests use).
        sdk._get_proj().g.query(
            "CREATE (s:Session {id:$sid})",
            params={"sid": "s_second_document"})

        from tortoise.near_dup_merge import apply_merge_union

        plan = ndm.near_dup_merge(
            {"content": CANDIDATE_CONTENT,
             "about_entities": ["Team", "Office"]},
            {"id": prior_id, "content": PRIOR_CONTENT,
             "about_entities": ["Team"]},
            overlap=1.0, difference=None, identity=frozenset(), flagged=True)
        assert plan.merge is True, plan.evidence

        res = apply_merge_union(
            sdk._get_proj(),
            {"point_id": prior_id,
             "about_entities": list(plan.union_entities),
             "content": PRIOR_CONTENT},
            session_id="s_second_document", sdk=sdk)

        assert res["point_id"] == prior_id
        assert res["contained"] == 1
        assert res["warnings"] == [], res["warnings"]
        # ⚠️ THE ASSERTION THAT MAKES THE SPELLING CONTRACT DISCRIMINATING:
        # every unioned name must RESOLVE. A lowered union reports them here
        # and links nothing, which is the fusion silently not happening.
        assert res["unresolved"] == [], res["unresolved"]

        # the union: the survivor now carries BOTH attachments
        rows = sdk._get_proj().g.query(
            "MATCH (p:Point {id:$pid})-[:aboutObject]->(o:Object) "
            "RETURN o.id ORDER BY o.id", params={"pid": prior_id}).result_set
        linked = {r[0] for r in rows}
        assert linked == {team["id"], office["id"]}, linked

        # …and the resolution is EXACT-match on purpose: the same name in
        # another case is not the same node, and conflating them by
        # `toLower()` would be a behaviour nothing else in the code base uses.
        mismatched = apply_merge_union(
            sdk._get_proj(),
            {"point_id": prior_id, "about_entities": ["office"]},
            session_id="s_second_document", sdk=sdk)
        assert mismatched["linked"] == 0
        assert mismatched["unresolved"] == ["office"]

        # the losing document's provenance: its session now CONTAINS the claim
        contains = sdk._get_proj().g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point {id:$pid}) "
            "RETURN count(*)",
            params={"sid": "s_second_document", "pid": prior_id}).result_set
        assert contains[0][0] == 1

        # ONE node, not two — the claim was never duplicated
        same = sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content = $c RETURN p.id",
            params={"c": PRIOR_CONTENT}).result_set
        assert len(same) == 1, same

    def test_a_subject_kind_name_is_skipped_not_bound_to_its_object_twin(
            self, sdk):
        """F2 (#5006 review): the COINCIDENCE case, which capture refuses.

        `about_entities` is not object-only — the subject channel (team/role/
        person, the gated `aboutSubject` edge) contributes names too, and an
        `:Object` may legitimately share a subject's EXACT name (that is why
        capture's skip is by name and not case-folded, `sdk.py:11849-11857`).
        The `MATCH (o:Object {name:$n})` therefore RESOLVES, and a fusion with no
        skip writes the un-gated `aboutObject` edge the same capture refuses on
        the normal route (`sdk.py:12124`, #1370 / the #4934 label leak).

        The control assertion below is what makes this discriminating: without
        `subject_names` the very same write DOES link — so the skip, not the
        absence of a node, is what prevents the edge.
        """
        from tortoise.near_dup_merge import apply_merge_union

        proj = sdk._get_proj()
        # The Object that shares the SUBJECT's name — the coincidence case.
        # #9964: these fixtures need a real `:Object` (the merge resolves
        # `aboutObject` by name), and `core:team`/`core:naturalPerson` are
        # §5 SUBJECT kinds — the object door now routes them to a `:Subject`
        # (the kind decides the layer). The kind was incidental to what these
        # tests assert, so it is left unsupplied (the fold's `other` default).
        twin = sdk.create_object("Dana")
        pt = sdk.create_point("statement", PRIOR_CONTENT)
        proj.g.query("MERGE (s:Session {id:$sid})", params={"sid": "s_sub"})
        record = {"point_id": pt["id"], "about_entities": ["Dana"]}

        CONTROL = apply_merge_union(proj, record, session_id="s_sub", sdk=sdk)
        assert CONTROL["linked"] == 1, CONTROL   # ← the edge a skip prevents
        proj.g.query(
            "MATCH (:Point {id:$pid})-[r:aboutObject]->(:Object {name:'Dana'}) "
            "DELETE r", params={"pid": pt["id"]})

        res = apply_merge_union(proj, record, session_id="s_sub", sdk=sdk,
                                subject_names={"Dana"})
        assert res["skipped_subject"] == ["Dana"], res
        assert res["linked"] == 0, res
        # NOT `unresolved`: the name DID resolve. That is precisely the hazard —
        # a skip reported as `unresolved` would misdescribe a resolved node.
        assert res["unresolved"] == [], res
        assert res["warnings"] == [], res["warnings"]
        rows = proj.g.query(
            "MATCH (:Point {id:$pid})-[:aboutObject]->(:Object {name:'Dana'}) "
            "RETURN count(*)", params={"pid": pt["id"]}).result_set
        assert rows[0][0] == 0, rows             # ← no un-gated edge minted
        assert proj.g.query(
            "MATCH (o:Object {id:$oid}) RETURN count(*)",
            params={"oid": twin["id"]}).result_set[0][0] == 1

    def test_the_subject_skip_is_exact_and_case_preserved(self, sdk):
        """A case-folded skip would drop a legitimately-cased Object.

        `subject_entity_names` is derived from payload entities whose KIND is a
        subject kind and compared with `in` — exact, case-preserved — because
        the resolution it guards (`Object {name:$n}`) is case-SENSITIVE too
        (`sdk.py:11849-11857`, F6). A `.lower()` fold on either side would
        conflate two names the graph keeps distinct.
        """
        from tortoise.near_dup_merge import apply_merge_union

        proj = sdk._get_proj()
        # #9964: these fixtures need a real `:Object` (the merge resolves
        # `aboutObject` by name), and `core:team`/`core:naturalPerson` are
        # §5 SUBJECT kinds — the object door now routes them to a `:Subject`
        # (the kind decides the layer). The kind was incidental to what these
        # tests assert, so it is left unsupplied (the fold's `other` default).
        sdk.create_object("Dana")
        pt = sdk.create_point("statement", PRIOR_CONTENT)
        proj.g.query("MERGE (s:Session {id:$sid})", params={"sid": "s_sub"})

        res = apply_merge_union(
            proj, {"point_id": pt["id"], "about_entities": ["Dana"]},
            session_id="s_sub", sdk=sdk,
            subject_names={"dana"})          # ← a different name
        assert res["skipped_subject"] == [], res
        assert res["linked"] == 1, res

    def test_a_missing_survivor_is_reported_not_fabricated(self, sdk):
        """Fail-closed: no point ⇒ no edges, and the caller is told."""
        from tortoise.near_dup_merge import apply_merge_union

        res = apply_merge_union(
            sdk._get_proj(), {"point_id": "pt_does_not_exist",
                              "about_entities": ["team"]},
            session_id="s_second_document", sdk=sdk)
        assert res["contained"] == 0
        assert res["linked"] == 0

    def test_an_empty_record_is_total(self, sdk):
        from tortoise.near_dup_merge import apply_merge_union

        res = apply_merge_union(sdk._get_proj(), {}, session_id="s_x")
        assert res["point_id"] == ""
        assert res["warnings"]

    def test_the_idless_stub_counts_only_an_edge_that_exists(self, sdk):
        """`linked` is a MEASUREMENT, on every branch of this write.

        The CONTAINS branch counts the edge (`RETURN count(*)`) precisely
        because a bare `MERGE` after a `MATCH` that resolves nothing is a
        silent no-op. The id-less `Object` stub branch is the same write shape
        and must obey the same rule: against an ABSENT survivor the MATCH
        resolves nothing, no edge exists, and reporting `linked == 1` is the
        false-PASS the record would carry to every reader of the fusion.

        `unresolved` stays empty here on purpose: the NAME resolved (the stub
        exists) — what did not happen is the edge, and the `contained` warning
        already names the absent endpoint. `linked`/`contained` are the two
        flags that separate a written union from an attempted one.
        """
        from tortoise.near_dup_merge import apply_merge_union

        proj = sdk._get_proj()
        # the id-less stub shape hosted_api.py mints (`MERGE (o:Object {name})`)
        proj.g.query("MERGE (o:Object {name:'Stubbed'})")
        pt = sdk.create_point("statement", PRIOR_CONTENT)
        proj.g.query("MERGE (s:Session {id:$sid})", params={"sid": "s_x"})

        ok = apply_merge_union(
            proj, {"point_id": pt["id"], "about_entities": ["Stubbed"]},
            session_id="s_x", sdk=sdk)
        assert ok["contained"] == 1, ok
        assert ok["linked"] == 1, ok
        rows = proj.g.query(
            "MATCH (p:Point {id:$pid})-[:aboutObject]->(o:Object "
            "{name:'Stubbed'}) RETURN count(*)",
            params={"pid": pt["id"]}).result_set
        assert rows[0][0] == 1, rows        # the edge this count claimed

        absent = apply_merge_union(
            proj, {"point_id": "pt_absent", "about_entities": ["Stubbed"]},
            session_id="s_x", sdk=sdk)
        assert absent["contained"] == 0, absent
        assert absent["linked"] == 0, absent   # ← the false-PASS this pins
        assert absent["unresolved"] == [], absent
        assert absent["warnings"], absent
        still = proj.g.query(
            "MATCH (:Point)-[r:aboutObject]->(:Object {name:'Stubbed'}) "
            "RETURN count(r)").result_set
        assert still[0][0] == 1, still     # no edge was minted by the miss


# ── 4. The SEAM: the ONE production invocation of the fusion ───────────────




if __name__ == "__main__":  # pragma: no cover - manual mutation aid
    raise SystemExit(pytest.main([__file__, "-q"]))
