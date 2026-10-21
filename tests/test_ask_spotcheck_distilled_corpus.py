"""#9095 — the ask spot-check must be able to OBSERVE the distilled corpus.

Measured defect (verified by execution at the pre-fix head): the spot-check
harness seeded only RAW episodic turns and had no supersession case at all —
``git grep -n "supersed|distilled|planted" -- tools/ask_spotcheck.py`` was
empty. So epic #2281's **D7** (distilled-corpus ask measurement) and its
**D8** product gate (*"0% superseded points appear as live answers"*) had **no
instrument**: the epic could not say whether distilling knowledge and
superseding it actually flags the dead claim, because no fixture ever planted
one.

The defect is *the missing instrument*, not a wrong answer from the reader —
so these tests pin the INSTRUMENT:

  1. the planted corpus is COMMITTED, hermetic and small (3 hand-authored
     old→new pairs: residence, diet, employer), reviewable row by row;
  2. ``seed_distilled_corpus`` writes the distilled claims through the
     product's OWN distilled-claim write (``create_point(kind='statement',
     is_episodic=False, validFrom=…, session_id=…, quote=…,
     source_turn_id=…)`` + the ``CONTAINS`` edge capture wires) and the
     supersession through the product's own ``supersede_point``;
  3. the superseded-live check reads the ASSEMBLED READER EVIDENCE and is
     immune to the SNIPPET TRAP (the successor's ``[SUPERSEDES: <old>]``
     marker carries the old claim's text — a whole-line substring test would
     read the successor's block as "the old claim, rendered live");
  4. one invocation reports BOTH arms (raw turns / distilled + supersession)
     as two numbers from one run (D4: no parallel benchmark), with the
     ``DISTILLED_JSON`` machine line;
  5. MUTATION PROOF — drop the supersession and the D8 gate goes RED. Without
     this the gate could pass on any corpus at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_spotcheck import (  # noqa: E402, RUF100
    _SUPERSESSION_FIXTURE,
    _detector_attributes_the_claim,
    _distilled_gate_failures,
    _evidence_blocks,
    _seed_memory,
    _strip_annotations,
    _superseded_live_state,
    _superseded_pool_probe,
    load_supersession_cases,
    main,
    run_distilled_leg,
    seed_distilled_corpus,
    superseded_claim_retrievable,
)
from tools.longmem_eval.judge import MockJudge  # noqa: E402, RUF100
from tortoise.ask_lane import _reset_ask_reader_cache_for_tests
from tortoise.sdk import TortoiseSDK


class _AnswerReader:
    """The ask lane's ONE reader call, stubbed (local lane, no provider).

    Deterministic and answering from a QUESTION→gold map rather than from the
    evidence: the reader's quality is not what these tests measure (the defect
    is *the missing instrument*, not a wrong answer from the reader). What they
    measure is whether the instrument can SEE the planted corpus and whether it
    reports a flagged claim as flagged.
    """

    last_completion_tokens = 8

    #: question fragment → the current state a competent reader would commit.
    ANSWERS = (
        ("Where do I live?", "I live in Porto."),
        ("What diet am I on?", "I eat a vegan diet."),
        ("Who do I work for?", "I work at Northwind Traders."),
    )

    def complete(self, *, system: str, user: str) -> str:
        del system
        for fragment, answer in self.ANSWERS:
            if fragment in user:
                return answer
        return ""

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_ask_state(monkeypatch):
    # The ambient shell may carry TORTOISE_API_URL (fleet env) — the local
    # lane is the surface under test here.
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    _reset_ask_reader_cache_for_tests()
    yield
    _reset_ask_reader_cache_for_tests()


def _install_reader(monkeypatch) -> None:
    import tortoise.ask_lane as ask_lane_mod

    fake = _AnswerReader()
    monkeypatch.setattr(ask_lane_mod, "_default_ask_reader_factory",
                        lambda: fake)


# ── 1. the corpus is committed, hermetic and hand-authored ────────────────

def test_planted_corpus_is_committed_with_three_hand_authored_cases():
    assert Path(_SUPERSESSION_FIXTURE).exists(), (
        "the planted corpus must be COMMITTED — an instrument whose input "
        "lives outside the repo cannot be re-run")
    cases = load_supersession_cases()
    assert [c["case_id"] for c in cases] == ["residence", "diet", "employer"]
    for case in cases:
        old, new = case["old_claim"], case["new_claim"]
        # A knowledge-update case is exactly one old→new pair, and the gold
        # IS the current state (the KU class the D7 number reports).
        assert case["answer"] == new["text"], case["case_id"]
        assert old["text"] != new["text"], case["case_id"]
        assert old["session_id"] != new["session_id"], case["case_id"]
        assert old["valid_from"] < new["valid_from"], case["case_id"]
        # The raw-turn leg must exist too, or the two arms cannot be paired
        # from ONE run (D4).
        assert len(case["haystack_sessions"]) == 2, case["case_id"]
        assert len(case["haystack_session_ids"]) == 2, case["case_id"]
        assert len(case["haystack_dates"]) == 2, case["case_id"]


# ── 2. the seeder writes the PRODUCT's distilled shape ────────────────────

def test_seed_distilled_corpus_writes_live_distilled_claims_and_supersession(
        tmp_path):
    """Distilled claims go through the capture extraction writer's own call
    (``create_point`` + ``is_episodic=False`` + ``validFrom`` + ``session_id``
    + ``quote``/``source_turn_id``) and the supersession through the product's
    own ``supersede_point`` (CORRECTS + outdated + ``validTo``)."""
    case = load_supersession_cases()[0]
    sdk = TortoiseSDK(str(tmp_path / "distilled.db"))
    try:
        _seed_memory(sdk, case)
        ids = seed_distilled_corpus(sdk, case)
        proj = sdk._get_proj()

        rows = proj.g.query(
            "MATCH (p:Point) WHERE p.is_episodic = false "
            "RETURN p.id, p.pointKind, p.status, p.validFrom, "
            "       p.extractedFrom, p.quote, p.source_turn_id "
            "ORDER BY p.validFrom").result_set
        assert [r[0] for r in rows] == [ids["old"], ids["new"]], rows
        for (pid, kind, status, valid_from, extracted, quote,
             source_turn) in rows:
            assert kind == "statement", (pid, kind)
            # D8 is about LIVE claims: the promise covers post-promotion
            # knowledge, not drafts. The successor stays live; the predecessor
            # is the one the supersession retires.
            assert status == ("superseded" if pid == ids["old"] else "live"), \
                (pid, status)
            assert valid_from, (pid, valid_from)
            claim = case["old_claim"] if pid == ids["old"] else case["new_claim"]
            assert extracted == f"session:{claim['session_id']}", (pid, extracted)
            assert quote == claim["text"], (pid, quote)
            # The quote is anchored to the turn the seeder actually wrote.
            assert source_turn == f"{claim['session_id']}_t0", (pid, source_turn)

        # The claims are CONTAINS-wired to their own :Session — the
        # provenance mechanism capture's extraction writer wires, and the one
        # the shipping read resolves identity from.
        edges = proj.g.query(
            "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
            "WHERE p.is_episodic = false RETURN s.id, p.id ORDER BY s.id"
        ).result_set
        assert [list(r) for r in edges] == sorted(
            [[case["old_claim"]["session_id"], ids["old"]],
             [case["new_claim"]["session_id"], ids["new"]]]), edges

        # The supersession itself: CORRECTS points successor → predecessor,
        # and the predecessor's window is CLOSED (contiguous, per §4.7).
        corrects = proj.g.query(
            "MATCH (a:Point)-[:CORRECTS]->(b:Point) RETURN a.id, b.id"
        ).result_set
        assert [list(r) for r in corrects] == [[ids["new"], ids["old"]]], corrects
        old_status, old_valid_to = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.status, p.validTo",
            params={"id": ids["old"]}).result_set[0]
        assert old_status == "superseded", old_status
        assert old_valid_to == case["new_claim"]["valid_from"], old_valid_to
    finally:
        sdk.close()


def test_seeding_the_distilled_corpus_leaves_the_raw_turn_leg_untouched(
        tmp_path):
    """Additive, not a mode flag: ``_seed_memory``'s raw leg writes the same
    store whether or not the distilled corpus is seeded on top of it (the
    21-question composition keeps its store and its number)."""
    case = load_supersession_cases()[0]
    sdk = TortoiseSDK(str(tmp_path / "additive.db"))
    try:
        _seed_memory(sdk, case)
        proj = sdk._get_proj()
        query = ("MATCH (p:Point) WHERE p.is_episodic = true "
                 "RETURN p.id, p.content, p.createdAt, p.embedding IS NULL "
                 "ORDER BY p.id")
        before = [list(r) for r in proj.g.query(query).result_set]
        assert before, "precondition: the raw turn leg must write turns"
        seed_distilled_corpus(sdk, case)
        after = [list(r) for r in proj.g.query(query).result_set]
        assert after == before
    finally:
        sdk.close()


# ── 3. the check reads the ASSEMBLED EVIDENCE and defeats the snippet trap ─

def test_evidence_blocks_split_rendered_context_into_one_block_per_hit():
    evidence = (
        "Current Date: 2023-06-01\n\n"
        "[session s1] (session date 2023-03-02) [user] I live in Lisbon.\n\n"
        "[session s2] (session date 2023-05-20) [user] I live in Porto.")
    blocks = _evidence_blocks(evidence)
    # The "Current Date:" header is not a hit block — counting it would make
    # every case look like it rendered something.
    assert blocks == [
        "[session s1] (session date 2023-03-02) [user] I live in Lisbon.",
        "[session s2] (session date 2023-05-20) [user] I live in Porto.",
    ], blocks


def test_snippet_trap_the_successor_marker_is_not_the_old_claim_rendered_live():
    """⛔ THE SNIPPET TRAP. The successor's block carries the OLD claim verbatim
    inside its ``[SUPERSEDES: …]`` marker. A whole-line substring test reads
    that block as "the old claim, rendered live" and the gate is wrong in the
    direction that matters: it would demand the successor stop naming what it
    replaced. Stripping the annotation prefix first is what tells the two
    apart."""
    old = "I live in Lisbon."
    successor_block = (
        "[session s2] (session date 2023-05-20) [user] "
        "[SUPERSEDES: I live in Lisbon.] [valid since 2023-05-20] "
        "I live in Porto.")

    prefix, body = _strip_annotations(successor_block)
    assert "I live in Lisbon." in prefix, prefix   # ... only inside the marker
    assert "I live in Lisbon." not in body, body   # ... never as the body

    seen, rendered_live, _flagged = _superseded_live_state(successor_block, old)
    assert rendered_live is False, (
        "the successor's [SUPERSEDES:] marker is a FLAG, not the old claim "
        "rendered live — a whole-line substring test would fail here")
    assert seen is True, "the old claim IS named in the evidence"


def test_a_superseded_claim_renders_flagged_and_is_not_counted_live():
    """The product's flagging vocabulary (``[SUPERSEDED BY: …]`` and the ended
    window ``[valid <from> → <to>]``) is what the check couples to."""
    old = "I live in Lisbon."
    flagged = (
        "[session s1] (session date 2023-03-02) [user] "
        "[SUPERSEDED BY: I live in Porto.] "
        "[valid 2023-03-02 → 2023-05-20; expired 2026-10-19] I live in Lisbon.")
    seen, rendered_live, flagged_flag = _superseded_live_state(flagged, old)
    assert (seen, rendered_live, flagged_flag) == (True, False, True)


def test_an_ended_window_alone_is_a_flag():
    """A hit whose supersession state was lost but whose window END survived
    still tells the reader the claim stopped being true — the window IS a
    flag (``[valid <from> → <to>]``), and the check must not call it live."""
    old = "I live in Lisbon."
    ended_window = (
        "[session s1] (session date 2023-03-02) [user] "
        "[valid 2023-03-02 → 2023-05-20] I live in Lisbon.")
    assert _superseded_live_state(ended_window, old) == (True, False, True)


def test_an_unflagged_claim_rendered_as_its_body_IS_live():
    """The failure shape the D8 gate exists to catch: the old claim reaches
    the reader as a plain live claim (its window still asserts it is true)."""
    old = "I live in Lisbon."
    live = (
        "[session s1] (session date 2023-03-02) [user] "
        "[valid since 2023-03-02] I live in Lisbon.")
    seen, rendered_live, flagged_flag = _superseded_live_state(live, old)
    assert (seen, rendered_live, flagged_flag) == (True, True, False)


# ── 4. one run, two arms, two numbers ────────────────────────────────────

def test_distilled_leg_reports_both_arms_and_the_d8_gate_from_one_run(
        monkeypatch, capsys):
    _install_reader(monkeypatch)
    report = run_distilled_leg(judge=MockJudge())

    # Two numbers, both from THIS run (D4: no parallel benchmark).
    assert report["current_state"]["n"] == 3, report
    assert report["current_state"]["committed"] == 3, report
    assert report["superseded_live"]["rendered_live"] == 0, report
    assert report["superseded_live"]["seen"] == 3, report
    # The paired arm is reported alongside, not instead.
    assert report["raw_arm"]["superseded_live"]["seen"] == 0, report
    assert len(report["cases"]) == 3
    for case in report["cases"]:
        assert set(case["arms"]) == {"raw", "distilled"}
        assert case["arms"]["distilled"]["rendered_live"] is False

    # The machine-readable line, and the human lines, from the SAME stdout.
    out = capsys.readouterr().out
    payload = [ln for ln in out.splitlines() if ln.startswith("DISTILLED_JSON ")]
    assert len(payload) == 1, out
    parsed = json.loads(payload[0][len("DISTILLED_JSON "):])
    assert parsed["gate_arm"] == "distilled"
    assert parsed["superseded_live"] == report["superseded_live"]
    assert parsed["current_state"] == report["current_state"]
    assert "distilled" in out and "residence" in out


# ── 5. MUTATION PROOF: lose the supersession, the gate must go RED ────────

def test_mutation_dropping_the_supersession_turns_the_d8_gate_red(
        monkeypatch):
    """The D8 gate must not be a guaranteed green. Drop the CORRECTS edge and
    the predecessor's window end (the shape of "the supersession was never
    recorded"), and the old claim reaches the reader as a live claim."""
    _install_reader(monkeypatch)
    report = run_distilled_leg(
        judge=MockJudge(),
        mutate=lambda sdk: sdk._get_proj().g.query(
            "MATCH (a:Point)-[r:CORRECTS]->(b:Point) DELETE r "
            "WITH b SET b.status = 'live', b.validTo = null, b.expiredAt = null"),
    )
    assert report["superseded_live"]["rendered_live"] == 3, report
    assert report["ok"] is False, (
        "a corpus whose supersession was lost MUST fail the D8 gate — "
        "otherwise the gate passes on any corpus at all")


# ── 6. the RENDERER's contract: a multi-paragraph claim is ONE block ──────
#
# ``_render_block`` fences a multi-paragraph body (``_fence_body`` marks every
# non-blank continuation line with ``"  | "``, leaving the blank line blank).
# A blank-line split therefore tears ONE served claim into pseudo-blocks, and
# the claim becomes unmatchable — which, read against a retrievability probe,
# is the fail-OPEN direction: "not matched" would be reported as "excluded",
# a D8 PASS, for a claim the reader was actually served. These tests drive the
# REAL renderer (never a hand-written evidence string), so a renderer change
# that breaks the contract fails here instead of silently greening the gate.

def _rendered(**hit) -> str:
    from tortoise.retrieval import _render_block

    return _render_block(hit)


def test_a_multi_paragraph_claim_is_one_block_and_is_not_read_as_unserved():
    old = "I live in Lisbon.\n\nI have a balcony there."
    # The old claim's own render: superseded (flagged), closed window.
    hit = {
        "content": old,
        "status": "superseded",
        "superseded_by": {"content_snippet": "I live in Porto."},
        "valid_from": "2023-03-02",
        "valid_to": "2023-05-20",
    }
    evidenced = _rendered(**hit)
    assert "  | I have a balcony there." in evidenced, evidenced

    blocks = _evidence_blocks(evidenced)
    assert len(blocks) == 1, blocks   # ONE hit, not two pseudo-blocks

    # ... and the claim IS observed, and flagged: a multi-paragraph claim must
    # never read as "never served".
    seen, rendered_live, flagged = _superseded_live_state(evidenced, old)
    assert (seen, rendered_live, flagged) == (True, False, True), (
        seen, rendered_live, flagged, evidenced)


def test_a_multi_paragraph_claim_served_unflagged_is_still_counted_live():
    """The red half at the same shape: a fenced multi-paragraph body whose
    window still asserts it is true is the D8 failure, not an unserved claim."""
    old = "I live in Lisbon.\n\nI have a balcony there."
    evidenced = _rendered(content=old, status="live",
                          valid_from="2023-03-02")
    seen, rendered_live, _flagged = _superseded_live_state(evidenced, old)
    assert (seen, rendered_live) == (True, True), (seen, rendered_live,
                                                  evidenced)


# ── 7. D8's "flagged OR EXCLUDED" — and never a fail-open ────────────────
#
# ``excluded`` is the ONE pass that is not an observed flag, so it may only be
# claimed on POSITIVE evidence: the claim is in the lane's own pool AND its
# body appeared nowhere in the reader's evidence. Everything else — a claim
# that never came out of the store, or a probe that could not read it — is
# UNMEASURED and fails, because an unmeasured gate is never a pass.

@pytest.fixture
def _one_case_report():
    """A one-case leg run, so an exclusion/gap can be driven in isolation."""
    def _run(monkeypatch, **kwargs):
        _install_reader(monkeypatch)
        return run_distilled_leg(cases=[load_supersession_cases()[0]],
                                 judge=MockJudge(), **kwargs)
    return _run


def test_an_excluded_supersession_is_a_reported_pass(monkeypatch,
                                                     _one_case_report):
    """D8's own second option: "must be flagged or EXCLUDED". A claim that was
    retrievable but never reached the reader is a PASS — reported as its own
    field so the pass is visible rather than an absence that looks like a miss.

    Mutation: the ASSEMBLY drops terminal hits (the shape any future or
    lane-local "exclude superseded points" change has). The store still holds
    the claim and the lane's retrieval still returns it, so its absence is an
    exclusion, not a retrieval gap.
    """
    import tortoise.retrieval as retrieval

    original = retrieval.assemble_context

    def _exclude_terminal(hits, *args, **kwargs):
        return original([h for h in hits
                         if (h.get("status") or "live") != "superseded"],
                        *args, **kwargs)

    monkeypatch.setattr(retrieval, "assemble_context", _exclude_terminal)
    report = _one_case_report(monkeypatch)
    sl = report["superseded_live"]
    assert sl["rendered_live"] == 0, report
    assert sl["excluded"] == 1, report
    assert sl["observed"] == 0, report
    arm = report["cases"][0]["arms"]["distilled"]
    assert arm["retrievable"] is True, arm
    assert arm["excluded"] is True, arm
    assert report["ok"] is True, report["hard_failures"]
    assert report["hard_failures"] == [], report["hard_failures"]


def test_a_retrieval_gap_still_fails_closed(monkeypatch, _one_case_report):
    """The other half of the split: when the claim never comes out of the store
    it is UNMEASURED, never a pass. Mutation: retrieval never returns terminal
    hits at all, so neither the lane NOR the probe can see the claim."""
    original = TortoiseSDK.tortoise_fts_query

    def _live_only(self, *args, **kwargs):
        kwargs["include_terminal"] = False
        return original(self, *args, **kwargs)

    monkeypatch.setattr(TortoiseSDK, "tortoise_fts_query", _live_only)
    report = _one_case_report(monkeypatch)
    arm = report["cases"][0]["arms"]["distilled"]
    assert arm["observed"] is False, arm
    assert arm["retrievable"] is False, arm
    assert arm["excluded"] is False, arm
    assert report["ok"] is False, report
    assert any("GAP" in f for f in report["hard_failures"]), \
        report["hard_failures"]


def test_a_mention_in_the_successor_marker_is_not_an_observation(
        monkeypatch, _one_case_report):
    """⛔ THE FAIL-OPEN THIS CLOSES. ``seen`` (a MENTION) used to be the gate,
    and the successor's ``[SUPERSEDES: <old content>]`` marker mentions the
    predecessor. So a corpus whose OLD CLAIM never rendered at all still
    satisfied ``seen == n`` and the gate passed green on a claim nobody
    observed.

    Read over the successor render alone: the old claim is MENTIONED
    (``old_seen``) but not OBSERVED, and with no evidence it was retrievable
    the leg must FAIL rather than pass on the mention.
    """
    old = "I live in Lisbon."
    successor = {
        "content": "I live in Porto.",
        "status": "live",
        "supersedes": [{"content_snippet": old}],
        "valid_from": "2023-05-20",
    }
    evidenced = _rendered(**successor)
    assert old in evidenced, evidenced     # the marker names it ...
    seen, rendered_live, flagged = _superseded_live_state(evidenced, old)
    assert (seen, rendered_live, flagged) == (True, False, False), (
        "the mention is not an observation — only a BODY render is", evidenced)

    failures = _distilled_gate_failures([{
        "case_id": "residence",
        "arms": {"raw": {}, "distilled": {
            "ok": True, "old_seen": True, "observed": False,
            "rendered_live": False, "flagged": False,
            "retrievable": None, "excluded": False, "note": ""}}}])
    assert failures and "UNMEASURED" in failures[0], failures


def test_a_mention_only_outside_every_body_needs_a_CALIBRATED_detector():
    """A mention is not an observation, and it is not an exclusion either.

    ``excluded`` is only readable from a detector PROVEN able to see the claim:
    a claim whose text reached the evidence only outside every claim body (the
    snippet trap) under a BLIND detector is indistinguishable from a claim
    SERVED in a shape the matcher could not attribute — the exact fail-open
    (#9095) where a live, unflagged, multi-line claim read as a D8 PASS.

    With the detector proven (``detector_attributed``), a mention-only claim
    that is retrievable is the snippet trap and is D8's own second compliant
    outcome, ``excluded`` — a reported PASS, not an absence.
    """
    def _arm(**overrides):
        base = {"ok": True, "old_seen": True, "observed": False,
                "mentioned": True, "rendered_live": False, "flagged": False,
                "retrievable": True, "excluded": False, "note": ""}
        base.update(overrides)
        return {"case_id": "residence",
                "arms": {"raw": {}, "distilled": base}}

    # detector BLIND: the silence says nothing about whether the claim was
    # served, so the gate must not read it as an exclusion.
    blind = _distilled_gate_failures([_arm(detector_attributed=False)])
    assert blind and "BLIND" in blind[0], blind
    assert "UNMEASURED" in blind[0], blind
    assert "MENTION" in blind[0], blind

    # detector PROVEN: the claim's own render IS attributable, so a text that
    # appears only in a supersedes snippet was genuinely not served.
    proven = _distilled_gate_failures([_arm(detector_attributed=True,
                                            excluded=True)])
    assert proven == [], proven


def test_the_detector_proves_it_can_see_the_claim_before_absence_is_read(
        tmp_path):
    """``excluded`` is a claim about ABSENCE, and absence is only evidence from
    a detector PROVEN able to attribute the claim's own render.

    The proof is mechanical: the claim's own pool hit is rendered through the
    product's renderer (``render_context``, the reader's own path) and the
    block detector must OBSERVE the claim in it. Without that half, a claim
    SERVED in a shape the matcher cannot attribute and a claim never served
    are the same observation — and the D8 gate reads green on the failure it
    exists to catch.
    """
    from tools.ask_spotcheck import _to_iso_date

    case = load_supersession_cases()[0]
    sdk = TortoiseSDK(str(tmp_path / "detector.db"))
    try:
        _seed_memory(sdk, case)
        ids = seed_distilled_corpus(sdk, case)
        qdate = _to_iso_date(case["question_date"])
        retrievable, hit = _superseded_pool_probe(sdk, case, ids["old"], qdate)
        assert retrievable is True and isinstance(hit, dict), (retrievable, hit)
        # the product's own render of the claim IS attributable
        assert _detector_attributes_the_claim(case, hit) is True
        # an absent probe row is NOT proof of anything
        assert _detector_attributes_the_claim(case, None) is False
        # a render whose body the corpus text no longer matches (a claim
        # rephrased after the corpus was authored) is not attributable — the
        # calibration must say so instead of leaving the silence readable as
        # an exclusion.
        drifted = dict(hit, content="I am not in the city any more.")
        assert _detector_attributes_the_claim(case, drifted) is False
    finally:
        sdk.close()


def test_a_claim_the_detector_cannot_attribute_is_UNMEASURED_not_excluded(
        monkeypatch, _one_case_report):
    """⛔ THE REMAINING FAIL-OPEN, driven through the real leg.

    A superseded claim whose STORED content no longer matches the corpus (an
    extraction rewrite — the realistic way a claim reaches the reader in a
    form the matcher cannot attribute) is never observed. The old arithmetic
    (``not observed and retrievable``) reported that as ``excluded`` — D8's
    compliant PASS — and the leg exited 0 on a claim nobody saw flagged. With
    the detector calibrated against the claim's own render, the instrument
    cannot attribute it and must report UNMEASURED instead.
    """
    case = load_supersession_cases()[0]

    def _rephrase_the_stored_claim(sdk):
        rows = sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content = $c RETURN p.id AS id",
            params={"c": case["old_claim"]["text"]})
        old_id = rows.result_set[0][0]
        sdk.update_point(old_id, content="I am not in the city any more.")

    report = _one_case_report(monkeypatch, mutate=_rephrase_the_stored_claim)
    arm = report["cases"][0]["arms"]["distilled"]
    assert arm["observed"] is False, arm
    assert arm["retrievable"] is True, arm
    assert arm["excluded"] is False, arm
    assert arm["detector_attributed"] is False, arm
    assert report["ok"] is False, report
    assert any("BLIND" in f for f in report["hard_failures"]), \
        report["hard_failures"]


def test_the_probe_reads_the_lane_pool_and_discriminates(tmp_path):
    """The probe behind the exclusion split must ANSWER, not always say yes: the
    superseded claim's own id is in the lane's ``include_terminal=True`` pool,
    an id that is not in the store is not, and a missing id is UNKNOWN
    (``None`` — the caller fails closed on it). A probe that returned ``True``
    unconditionally would turn every retrieval gap into a silent pass.
    """
    from tools.ask_spotcheck import _to_iso_date

    case = load_supersession_cases()[0]
    sdk = TortoiseSDK(str(tmp_path / "probe.db"))
    try:
        _seed_memory(sdk, case)
        ids = seed_distilled_corpus(sdk, case)
        qdate = _to_iso_date(case["question_date"])
        # a TERMINAL point is IN scope: the lane's own retrieval runs with
        # include_terminal=True, so a filtered probe would call every
        # supersession a retrieval gap.
        assert superseded_claim_retrievable(sdk, case, ids["old"], qdate) is True
        assert superseded_claim_retrievable(sdk, case, ids["new"], qdate) is True
        assert superseded_claim_retrievable(
            sdk, case, "point-not-in-this-store", qdate) is False
        assert superseded_claim_retrievable(sdk, case, None, qdate) is None
    finally:
        sdk.close()


# ── 8. the machine line, and the key-free CI path ───────────────────────

def test_distilled_leg_emits_one_json_line_and_exits_clean(monkeypatch,
                                                           capsys):
    """``TORTOISE_SPOTCHECK_JUDGE=mock`` is the ONLY key-free opt-out (#2071's
    fail-fast is not widened), and the run reports its gate branch per case, so
    ``rendered_live == 0`` is a MEASURED zero rather than an absence."""
    _install_reader(monkeypatch)
    monkeypatch.setenv("TORTOISE_SPOTCHECK_JUDGE", "mock")
    rc = main(["--distilled"])
    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines()
             if ln.startswith("DISTILLED_JSON ")]
    assert len(lines) == 1, captured.out
    payload = json.loads(lines[0][len("DISTILLED_JSON "):])
    assert payload["ok"] is True, payload["hard_failures"]
    assert payload["hard_failures"] == [], payload["hard_failures"]
    assert set(payload["superseded_live"]) == {
        "seen", "observed", "mentioned", "rendered_live", "excluded",
        "n", "rate"}
    assert payload["superseded_live"]["rendered_live"] == 0
    assert payload["superseded_live"]["excluded"] == 0
    assert payload["superseded_live"]["observed"] == 3
    assert payload["current_state"]["n"] == 3
    # every case is REPORTED as retrievable, so "rendered_live == 0" is a
    # measured flag rather than an absence of evidence
    assert all(c["arms"]["distilled"]["retrievable"] is True
               for c in payload["cases"]), payload["cases"]
    assert rc == 0, (rc, captured.out)


def test_distilled_leg_without_a_judge_key_fails_fast(monkeypatch, capsys):
    """The distilled leg is not key-free by accident: only an EXPLICIT
    ``TORTOISE_SPOTCHECK_JUDGE=mock`` opts out of the judge-key rule, and a
    missing key names the prerequisite instead of grading nothing."""
    for key in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
                "GEMINI_API_KEY", "VENICE_API_KEY", "TORTOISE_LME_JUDGE_MODEL",
                "TORTOISE_SPOTCHECK_JUDGE"):
        monkeypatch.delenv(key, raising=False)
    rc = main(["--distilled"])
    captured = capsys.readouterr()
    assert rc == 2, captured
    assert "DISTILLED_JSON" not in captured.out
    assert "judge" in captured.err.lower(), captured.err
