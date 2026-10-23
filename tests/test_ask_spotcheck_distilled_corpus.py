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
    main,
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


@pytest.mark.parametrize(
    "marker",
    [
        "[valid]",                        # #9730: open end, unorderable start
        "[valid; end unknown]",           # #9730 + #2350: un-datable end
        "[valid; expired 2026-10-19; end unknown]",
        "[valid since 2023-03-02]",        # the orderable shapes, unchanged
        "[valid 2023-03-02 → 2023-05-20]",
        "[valid → 2023-05-20]",            # orderable END, absent start
    ],
)
def test_every_rendered_window_shape_is_attributed_to_a_claim(marker):
    """The attribution rule is *a distilled claim renders its WINDOW*, so it must
    hold for EVERY window shape the renderer can emit — not just the ones that
    happen to contain a space after ``valid``.

    (1) FAILS pre-#9730-consumer-fix: ``_VALID_WINDOW_OPEN`` was ``r"\\[valid "``,
        so the two no-space shapes were classified as raw turns and
        ``_superseded_live_state`` skipped them — ``seen`` came back False
        (an UNMEASURED claim, which the leg treats as a fail).
    (2) REACHABLE: the strings are the renderer's own output (see
        ``tests/test_2350_validity_precision.py`` and this file's sibling
        cases), fed to the real ``_superseded_live_state``.
    """
    old = "I live in Lisbon."
    block = f"[session s1] (session date 2023-03-02) [user] {marker} {old}"
    seen, _rendered_live, flagged_flag = _superseded_live_state(block, old)
    assert seen is True, f"{marker!r} was not attributed to a claim: {block!r}"
    # A closed window is the product's "stopped being true here" flag; an OPEN
    # window (which every start-less form above is) asserts still-true, so it is
    # not a flag. The arrow alone decides, exactly as before.
    assert flagged_flag == ("\u2192" in marker), marker


def test_the_start_less_window_is_not_a_closed_window_flag():
    """#9730: dropping an unorderable start must NOT read as "stopped being true".

    ``[valid]`` says a window exists with no end asserted — the still-true
    reading — so the D8 gate must count it as ``rendered_live``, not flagged.
    (1) FAILS if the no-space form were folded into ``_FLAG_CLOSED_WINDOW``.
    (2) REACHABLE: the marker is the renderer's own output for
        ``validFrom`` present-but-unorderable and no ``valid_to``.
    """
    old = "I live in Lisbon."
    block = f"[session s1] [user] [valid] {old}"
    assert _superseded_live_state(block, old) == (True, True, False)


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


# ── 6. the claim-detection is RENDER-SHAPE independent ───────────────────
#
# The planted corpus is single-line, but ``--supersession-fixture`` accepts an
# arbitrary corpus and a stored claim may carry a BLANK line. The renderer's
# ``_fence_body`` leaves a blank continuation line blank and fences the rest, so
# such a hit used to split on the ``"\n\n"`` block join AND carry a fence marker
# inside its body — both of which defeated the claim-text match, making a
# superseded claim served LIVE read as an unobservable one. The gate must be
# independent of that shape.

_MULTI_PARA_OLD = ("I live in Lisbon.\n\nI moved here from Madrid last "
                   "spring.")


def _multi_paragraph_case() -> dict:
    """The first planted case with its OLD claim re-authored as two paragraphs.

    The first paragraph is the original claim text, so the case stays
    retrievable by the same question — only the RENDER SHAPE under test
    changes, not what the reader is asked or what the store holds.
    """
    case = json.loads(json.dumps(load_supersession_cases()[0]))
    case["old_claim"]["text"] = _MULTI_PARA_OLD
    return case


def test_a_multi_paragraph_claim_stays_one_block_and_matches_through_the_renderer():
    """The PRODUCT renderer, not a hand-written block: ``render_context``
    fences the body and preserves the blank line, so this pins the fix against
    the real render shape rather than against the test's idea of it."""
    from tortoise.retrieval import _BODY_FENCE, render_context

    hit = {
        "id": "old-1", "content": _MULTI_PARA_OLD, "session_id": "s1",
        "session_date": "2023-03-02", "speaker": "user",
        "valid_from": "2023-03-02", "valid_to": "2023-05-20",
        "superseded_by": {"content_snippet": "I live in Porto."},
    }
    evidence = render_context([hit])
    # Precondition: the rendered body IS fenced and IS multi-paragraph — i.e.
    # the naive whole-string test really is defeated on this shape.
    assert _BODY_FENCE in evidence, evidence
    assert len(evidence.split("\n\n")) > 1, evidence

    # ONE hit ⇒ ONE block, even though its body carries a blank line.
    assert len(_evidence_blocks(evidence)) == 1, _evidence_blocks(evidence)
    # The claim is observed, and observed as FLAGGED (never as live).
    assert _superseded_live_state(evidence, _MULTI_PARA_OLD) == (
        True, False, True), evidence


def test_a_multi_paragraph_claim_served_live_is_a_hard_failure(monkeypatch):
    """End to end, through the real lane and renderer: lose the supersession on
    a MULTI-PARAGRAPH claim and the D8 gate must still go RED.

    On the pre-fix block split this case read ``seen == 0``, so the gate went
    red for the WRONG reason (an unobserved claim) and the ``rendered_live``
    half — the actual violation — was never measured.
    """
    _install_reader(monkeypatch)
    report = run_distilled_leg(
        cases=[_multi_paragraph_case()], judge=MockJudge(),
        mutate=lambda sdk: sdk._get_proj().g.query(
            "MATCH (a:Point)-[r:CORRECTS]->(b:Point) DELETE r "
            "WITH b SET b.status = 'live', b.validTo = null, b.expiredAt = null"),
    )
    assert report["superseded_live"]["seen"] == 1, report
    assert report["superseded_live"]["rendered_live"] == 1, report
    assert report["ok"] is False, report


def test_a_multi_paragraph_claim_rendered_flagged_passes(monkeypatch):
    """The green half of the same shape: a multi-paragraph superseded claim IS
    observed and IS flagged, so the gate passes for the measured reason."""
    _install_reader(monkeypatch)
    report = run_distilled_leg(cases=[_multi_paragraph_case()], judge=MockJudge())
    assert report["superseded_live"] == {
        "seen": 1, "rendered_live": 0, "n": 1, "rate": 0.0}, report
    assert report["cases"][0]["arms"]["distilled"]["flagged"] is True, report
    assert report["ok"] is True, report


# ── 7. an UNOBSERVED claim is never a pass (no exclusion bypass) ──────────

def test_an_unobserved_claim_fails_closed_rather_than_passing(monkeypatch):
    """D8's promise is *"flagged or EXCLUDED"*, and an excluded superseded claim
    produces the same observable as a retrieval miss: ``seen == 0``.

    The landed gate refuses that ambiguity in the fail-closed direction — an
    unobserved claim was not shown to be flagged, it was merely not seen — and
    this test is the pin on that polarity. It goes red for any implementation
    that turns ``seen == 0`` into a PASS by inferring an exclusion from the
    matcher's own silence (the rival #9125 head did exactly that: a superseded
    claim served live in a multi-line body and missed by the text matcher was
    scored ``excluded: true`` and the run exited 0)."""
    _install_reader(monkeypatch)

    def _drop_the_distilled_claims(sdk) -> None:
        sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.is_episodic = false DETACH DELETE p")

    report = run_distilled_leg(
        cases=[load_supersession_cases()[0]], judge=MockJudge(),
        mutate=_drop_the_distilled_claims)
    assert report["superseded_live"]["seen"] == 0, report
    assert report["superseded_live"]["rendered_live"] == 0, report
    assert report["ok"] is False, (
        "a claim the instrument could not observe MUST fail the gate — "
        "an unmeasured gate is never a pass")


# ── 8. the leg is key-free ONLY on an explicit mock judge ────────────────

_PROVIDER_KEYS = ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                  "GEMINI_API_KEY", "TORTOISE_LME_JUDGE_MODEL",
                  "TORTOISE_SPOTCHECK_JUDGE")


def test_the_distilled_leg_is_key_free_only_with_an_explicit_mock_judge(
        monkeypatch, capsys):
    """The landed leg called ``_require_judge_key()`` unconditionally, so the D8
    gate could not be run in CI at all (no provider key there). The opt-out is
    ``TORTOISE_SPOTCHECK_JUDGE=mock`` — explicit, and the composition path's
    #2071 fail-fast is untouched by it (see
    ``test_composition_leg_keeps_its_judge_key_fail_fast``)."""
    _install_reader(monkeypatch)
    for key in _PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)

    # No mock, no key ⇒ exit 2 BEFORE any case is graded.
    assert main(["--distilled"]) == 2
    refused = capsys.readouterr()
    assert "DISTILLED_JSON" not in refused.out, refused.out
    assert "judge" in refused.err.lower(), refused.err

    # ⛔ The opt-out is NARROW — it must be spelled `mock`. Anything merely
    # truthy is refused, because a truthy-but-unintended value (`1`, `true`)
    # grading with `MockJudge` would print PASS and a DISTILLED_JSON {"ok": true}
    # from a MOCK containment score — a fake gate that reads green and is
    # exactly the "an unmeasured gate is never a pass" hazard this leg closes.
    # Pinned here rather than trusted to the docstring: a bare-truthiness
    # implementation passes the two cases above (measured, #9125 review).
    # Surrounding whitespace IS tolerated on purpose (`strip()` — a shell can
    # hand us `mock `), which is why the spellings below exclude it.
    for misspelled in ("1", "true", "yes", "MOCKISH", "mocky"):
        monkeypatch.setenv("TORTOISE_SPOTCHECK_JUDGE", misspelled)
        rc = main(["--distilled"])
        stray = capsys.readouterr()
        assert rc == 2, (misspelled, rc, stray.out[:200])
        assert "DISTILLED_JSON" not in stray.out, (misspelled, stray.out)

    # Explicit mock ⇒ the leg runs key-free and reports its number.
    monkeypatch.setenv("TORTOISE_SPOTCHECK_JUDGE", "mock")
    assert main(["--distilled"]) == 0
    out = capsys.readouterr().out
    payload = [ln for ln in out.splitlines() if ln.startswith("DISTILLED_JSON ")]
    assert len(payload) == 1, out
    parsed = json.loads(payload[0][len("DISTILLED_JSON "):])
    assert parsed["ok"] is True, parsed
    assert parsed["judge_model"], parsed


def test_composition_leg_keeps_its_judge_key_fail_fast(monkeypatch, capsys):
    """#2071's fail-fast is NOT widened by the distilled leg's mock opt-out:
    the 21-question path refuses without a provider key even when
    ``TORTOISE_SPOTCHECK_JUDGE=mock`` is set."""
    for key in _PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TORTOISE_SPOTCHECK_JUDGE", "mock")
    assert main([]) == 2
    captured = capsys.readouterr()
    assert "judge" in captured.err.lower(), captured.err
