"""W3 harness reflex-seam tests (issue #2103 — know-to-ask / false-fire arm).

The W3 harness (tests/eval/harness/) grades the reflex DECISION via per-turn
``injected: {turn_index: [pointer ids]}`` against sealed gold
(``grading.grade_kta`` / ``grade_push``).  The harness itself is NOT modified
here (its no-reflex baseline stays the orchestrator's); this file exposes the
graded seam (tortoise/volunteer.decide) and replays the REAL committed kta
fixtures through the REAL write seam + the reflex, reporting the numbers
EXACTLY in the harness's grading vocabulary.  The numbers are honest first
measurements of this reflex over the frozen corpus — no baseline is blessed.

The corpus write path (m2 echo lane) leaves cell points UNMEASURED (no
posterior α/β — capture does not run the dream EP pass), so the EP gate
(0.7 default on the canonical posterior mean, unmeasured = neutral 0.5) is
expected to under-fire on the frozen corpus: that is the named failure class
of the first graded run (fix-wave protocol — the fix is an EP warm-start on
the graded cells, owned by the W3 orchestrator, not this issue).
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
    """Per-turn reflex replay: for each user turn, decide with the window so
    far + the prior injected block as prior_context (cross-turn dedupe)."""
    from tortoise.volunteer import decide

    proj = sdk._get_proj()
    window: list[dict] = []
    prior = None
    injected: dict[int, list[str]] = {}
    for i, turn in enumerate(fixture_turns, start=1):
        window.append(turn)
        if turn.get("role") != "user":
            continue
        d = decide(proj, window, prior_context=prior,
                   min_confidence=min_confidence, max_pointers=max_pointers,
                   _search_fn=sdk.tortoise_fts_query)
        if d["fire"]:
            injected[i] = d["pointer_ids"]
            # Canonical prior-context grammar for the next turn.
            from tortoise.volunteer import build_block
            prior = build_block(d["pointers"],
                                [{"label": p["label"], "band": "medium"}
                                 for p in d["pointers"]])
    return injected


# ── Seam mechanics over the REAL corpus (honest first numbers) ─────────────

KTA_CORPUS = ("kta01_reminder_turns", "kta02_aurora_status")


def test_reflex_replays_kta_corpus_with_honest_numbers():
    """Replay the committed W3 know-to-ask fixtures through the REAL write
    seam (m2 capture) + the reflex; grade in the harness vocabulary. The
    numbers are reported, not blessed: this is the first graded measurement
    (expected bad per the fix-wave protocol — corpus cells are unmeasured
    by the write path the reflex reads)."""
    import json
    import sys
    sys.path.insert(0, str(HARNESS_ROOT.parent.parent))
    from eval.harness import grading
    from eval.write_path import runner as wp

    sdk = _fresh_sdk()
    results = []
    workdir = Path(tempfile.mkdtemp(prefix="vc_harness_wd_"))
    log: list[str] = []
    for session_id in KTA_CORPUS:
        fx, gold = _load_fixture_gold(session_id)
        # Real write seam: parser round-trip + capture_session (the runner's
        # exact cell shape — m2 echo lane via the mock seam).
        parsed = wp.parse_roundtrip(
            session_id, fx["turns"], "pi", workdir=workdir, log=log)
        capture = sdk.capture_session(
            parsed, session_id=session_id, harness="pi")
        assert capture.get("ok") is True, capture.get("errors")
        injected = _replay_turns(sdk, fx["turns"],
                                 min_confidence=0.7, max_pointers=3)
        result = grading.grade_kta(session_id, gold, injected)
        results.append(result)
        # The session emits honest numbers in the grader's own vocabulary.
        kta = result["kta"]
        ff = result["false_fire"]
        assert isinstance(kta["missed"], int) and isinstance(ff["fires"], int)
    # Both sessions graded; no exceptions — the seam is wired. The EXPECTED
    # first-run outcome is documented (not asserted as a pass): the m2 write
    # lane leaves points unmeasured, so the EP gate under-fires. We assert
    # the *seam shape* only: results exist in the harness vocabulary and the
    # reflex never crashes a replay.
    assert len(results) == len(KTA_CORPUS)
    summary = {
        r["session_id"]: {
            "missed": r["kta"]["missed"],
            "should": r["kta"]["should"],
            "false_fires": r["false_fire"]["fires"],
            "silent_required": r["false_fire"]["silent_required"],
        }
        for r in results
    }
    # Printed for the PR body (anti-gaming: real numbers from this run).
    print(f"HONEST KTA FIRST-NUMBERS: {json.dumps(summary)}")
    # Bounded, non-tautological assertions: the reflex may never FALSE-FIRE
    # on this corpus (courtesy / below-notability turns — a fire here is a
    # seam regression regardless of the EP-gate under-fire class), and the
    # grader's own bounds hold.  The expected first-run UNDER-fire (missed)
    # is reported, not gated (the fix-wave owner re-blesses).
    for r in results:
        assert r["false_fire"]["fires"] == 0, r
        assert 0 <= r["kta"]["missed"] <= r["kta"]["should"], r


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
