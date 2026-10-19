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
    _evidence_blocks,
    _seed_memory,
    _strip_annotations,
    _superseded_live_state,
    load_supersession_cases,
    run_distilled_leg,
    seed_distilled_corpus,
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
