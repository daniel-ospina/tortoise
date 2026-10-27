"""W3 harness reflex-seam tests (issue #2103 — know-to-ask / false-fire arm).

The W3 harness (tests/eval/harness/) grades the reflex DECISION via per-turn
``injected: {turn_index: [pointer ids]}`` against sealed gold
(``grading.grade_kta`` / ``grade_push``).  This file exposes the graded seam
(``tortoise.volunteer.decide``) and replays the REAL committed kta fixtures
through the REAL write seam + the reflex, reporting the numbers EXACTLY in
the harness's grading vocabulary.

#8125 closed the two gaps this file originally reported:

1. **the under-fire class is fixed, and pinned here.**  The corpus write path
   leaves cell points unmeasured (no posterior α/β — the echo lane's capture
   mints no claim/operator, so ``_promote_and_dream`` no-ops and the EP gate
   reads the Beta(1,1) neutral 0.5 under its 0.7 floor).  The fix is the
   harness's ``runner.ep_warm_start`` (#2199 system-default starting belief +
   the REAL EP pass), which this file exercises through the SAME primitives
   the runner uses — the cold replay still under-fires, the warmed replay does
   not.  A drift in either the reflex or the warm-start fails here.
2. **the numbers are graded, not merely reported.**  ``config.reflex=graded``
   in the committed baselines makes the standing kta / false-fire bars live.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parent / "eval" / "harness"
sys_path_inserted = False

# ── Replay helpers (mirror the harness runner's real-seam shape) ──────────

def _fresh_sdk():
    import sys
    global sys_path_inserted
    if not sys_path_inserted:
        sys.path.insert(0, str(HARNESS_ROOT.parent.parent))  # tests/
        sys_path_inserted = True
    from tortoise.sdk import TortoiseSDK
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_vc_harness_"),
                           "test.db")
    sdk = TortoiseSDK(db_path)
    try:  # noqa: SIM105
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


def _load_fixture_gold(session_id: str):
    import json
    fx = json.loads((HARNESS_ROOT / "fixtures" / f"{session_id}.json").read_text())
    gold = json.loads((HARNESS_ROOT / "gold" / f"{session_id}.gold.json").read_text())
    return fx, gold


def _replay_turns(sdk, fixture_turns, *, min_confidence, max_pointers):
    """Per-turn reflex replay — the HARNESS's own primitive (#8125): the
    seam test must exercise the exact call shape the runner grades under, or
    it stops being a seam test."""
    from eval.harness import runner as harness_runner

    assert min_confidence == harness_runner.REFLEX_MIN_CONFIDENCE
    assert max_pointers == harness_runner.REFLEX_MAX_POINTERS
    return harness_runner.replay_reflex(sdk, fixture_turns)


# ── Seam mechanics over the REAL corpus (honest first numbers) ─────────────

KTA_CORPUS = ("kta01_reminder_turns", "kta02_aurora_status")


def test_reflex_replays_kta_corpus_with_honest_numbers(monkeypatch):
    """Replay the committed W3 know-to-ask fixtures through the REAL write
    seam (m2 capture) + the reflex, COLD and EP-WARMED (#8125).

    The cold replay is the named under-fire class this file first reported
    (4 of 4 know-to-ask turns missed: the echo-lane cell carries no persisted
    prior, so the 0.7 EP gate reads the neutral 0.5).  The warmed replay —
    the harness's own ``runner.ep_warm_start`` — must meet the epic target
    (kta failure 0.00, false fires 0).  Both halves are asserted: the pass is
    not allowed to drift, and the mechanism that produces it is not allowed
    to become a no-op."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")  # offline MockModel extractor (the #822 seam — capture never calls an LLM)
    import json
    import sys
    sys.path.insert(0, str(HARNESS_ROOT.parent.parent))
    from eval.harness import grading
    from eval.harness import runner as harness_runner
    from eval.write_path import runner as wp

    sdk = None
    cold: list[dict] = []
    warm: list[dict] = []
    warm_starts: list[dict] = []
    workdir = Path(tempfile.mkdtemp(prefix="vc_harness_wd_"))
    log: list[str] = []
    for session_id in KTA_CORPUS:
        fx, gold = _load_fixture_gold(session_id)
        # A FRESH cell per session (the runner's own cell routing): a shared
        # graph would let session 1's warmed points answer session 2's
        # questions, and the cold/warm contrast below would be measuring the
        # leak instead of the warm-start.
        sdk = _fresh_sdk()
        # Real write seam: parser round-trip + capture_session (the runner's
        # exact cell shape — m2 echo lane via the mock seam).
        parsed = wp.parse_roundtrip(
            session_id, fx["turns"], "pi", workdir=workdir, log=log)
        capture = sdk.capture_session(
            parsed, session_id=session_id, harness="pi")
        assert capture.get("ok") is True, capture.get("errors")
        # 1. COLD: the untouched cell — the honest under-fire class.
        cold.append(grading.grade_kta(
            session_id, gold,
            _replay_turns(sdk, fx["turns"], min_confidence=0.7,
                          max_pointers=3)))
        # 2. WARM: the harness's EP warm-start, then the same replay.
        warm_starts.append(harness_runner.ep_warm_start(sdk, log=log))
        warm.append(grading.grade_kta(
            session_id, gold,
            _replay_turns(sdk, fx["turns"], min_confidence=0.7,
                          max_pointers=3)))
    assert len(warm) == len(KTA_CORPUS)
    summary = {
        "cold": {r["session_id"]: r["kta"] for r in cold},
        "warm": {r["session_id"]: r["kta"] for r in warm},
        "warm_start": warm_starts,
    }
    # Printed for the PR body (anti-gaming: real numbers from this run).
    print(f"KTA REFLEX COLD/WARM: {json.dumps(summary)}")
    for start in warm_starts:
        assert start["seeded"] > 0, start
    for r in warm:
        # THE FIX: every should-retrieve turn retrieves, nothing false-fires.
        assert r["kta"]["missed"] == 0, r
        assert r["false_fire"]["fires"] == 0, r
    # ...and the warm-start is what does it: cold, the same cell under-fires
    # (this is the failure class #8125 names, kept as a live guard).
    for r in cold:
        assert r["kta"]["missed"] > 0, r
        assert r["false_fire"]["fires"] == 0, r


def test_reflex_fires_on_measured_graph_mirroring_kta_gold():
    """MECHANISM probe (not a corpus claim): when the graph IS EP-measured
    (the real product write path runs the dream EP pass), the reflex decides
    per the gold semantics — direct asks fire, courtesy/opener turns stay
    silent. Proves the graded seam works when the cell is EP-warmed."""

    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    # Mirror kta01's fact: Alice/WidgetCo pricing as a MEASURED statement.
    claim = sdk.create_point(
        "statement", "Alice flagged the Widget Co pricing as too aggressive")
    ev = sdk.create_point("evidence", "widgetco supporting record")
    sdk.create_operator("IMPL", ev["id"], [claim["id"]])
    for pid, a, b in ((claim["id"], 12.0, 1.0), (ev["id"], 12.0, 1.0)):
        mean = round(a / (a + b), 4)
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence=$c, "
            "n.posterior_alpha=$a, n.posterior_beta=$b",
            params={"id": pid, "a": a, "b": b, "c": mean})
    fx, _gold = _load_fixture_gold("kta01_reminder_turns")
    injected = _replay_turns(sdk, fx["turns"],
                             min_confidence=0.7, max_pointers=3)
    # Gold turns: 3 + 6 should_retrieve true; 1/2/4/5 silent (courtesy +
    # below-notability openers + assistant turns).
    fired = {t for t, ids in injected.items() if ids}
    assert 3 in fired, f"direct ask must fire: {fired}"
    assert 6 in fired, f"explicit remind-re-ask must re-fire: {fired}"
    assert fired <= {3, 6}, f"false fires on courtesy/below-notability: {fired}"
    # The fired pointer is the measured claim (the gold's pt_alice_widgetco
    # analog — real graph ids, graded by truthiness in know_to_ask).
    assert injected[3] and claim["id"] in injected[3]


def test_reflex_silent_below_notability_opener():
    """Below-notability openers never fire even with a measured graph —
    the reflex stays silent on graph-free/greeting turns (indicator 4)."""
    sdk = _fresh_sdk()
    claim = sdk.create_point("statement", "Ember onboarding ships next week")
    proj = sdk._get_proj()
    for pid, a, b in ((claim["id"], 12.0, 1.0),):
        mean = round(a / (a + b), 4)
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence=$c, "
            "n.posterior_alpha=$a, n.posterior_beta=$b",
            params={"id": pid, "a": a, "b": b, "c": mean})
    courtesy = [{"role": "user", "content": "Morning! Hope the weekend was "
                                            "restful."}]
    from tortoise.volunteer import decide
    d = decide(proj, courtesy)
    assert d["fire"] is False
    thanks = [{"role": "user", "content": "Thanks, that helps a lot."}]
    assert decide(proj, thanks)["fire"] is False
