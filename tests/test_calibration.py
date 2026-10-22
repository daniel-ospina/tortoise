"""Tests for EP calibration pipeline — Issue #7478.

Runs on the embedded falkordblite fixture (no Docker needed, AGENTS.md) —
migrated in #398; the stale live-FalkorDB skip probe was removed as part of
#344 so the fail-closed default-flip tests actually execute.
"""
import os  # noqa: I001
import shutil

import pytest
from tortoise.sdk import TortoiseSDK
from tortoise.sdk import BASELINE_SOURCE_INHERITED
from tortoise.source_credibility import AUTHOR_ASSERTION_WEAK_PC
from tortoise.exceptions import CalibrationError
from tortoise.exceptions import UnaddressableClaimError


@pytest.fixture
def sdk():
    """Fresh embedded TortoiseSDK on an isolated file DB (no Docker needed).

    Uses the ``TortoiseSDK(db_path)`` embedded pattern (issue #398 Task 3 —
    migrated from the Docker-bound fixture so the calibration suite runs with
    falkordblite, per AGENTS.md "no Docker needed").
    """
    import tempfile
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_calib_"), "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


# ── Pipeline E2E ────────────────────────────────────────────────

def test_calibration_pipeline_e2e(sdk):
    """End-to-end: Source → Points → calibrate_summary → EP passes."""
    src_url = "https://doi.org/10.1234/systematic-review"
    # Create Point with extractedFrom — this creates a Source node
    p1 = sdk.create_point("statement", "X causes Y",
                          credibility="gold", extractedFrom=src_url)
    p2 = sdk.create_point("statement", "Y prevents Z",
                          extractedFrom=src_url)
    # Set credibilityTier on the Source so p2 inherits it
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = 'T1'",
        params={"url": src_url}
    )
    
    # Link them
    sdk.create_operator("IMPL", p1["id"], [p2["id"]])
    
    # calibrate_summary shows calibrated
    summary = sdk.calibrate_summary()
    calibrated_ids = {item["id"] for item in summary if item["calibrated"]}
    assert p1["id"] in calibrated_ids  # explicit credibility="gold"
    # p2 not yet calibrated — source inheritance happens inside compute_confidence
    
    # EP with gate passes
    result = sdk.compute_confidence(require_calibration=True)
    assert result["converged"] is True


# ── require_calibration gate ────────────────────────────────────

def test_require_calibration_raises(sdk):
    """require_calibration=True on uncalibrated LIVE graph raises CalibrationError.

    #780/PR #1212: draft points are excluded from EP (factor extraction +
    propagation), so the gate only guards live evidence — drafts must be
    promoted (create_operator / update_point draft→live) before the
    fail-closed gate applies to them.
    """
    sdk.create_point("statement", "Uncalibrated claim", status="live")
    sdk.create_point("statement", "Another uncalibrated", status="live")
    
    with pytest.raises(CalibrationError, match="uncalibrated"):
        sdk.compute_confidence(require_calibration=True)


def test_require_calibration_partial(sdk):
    """One calibrated, one live-uncalibrated → still raises."""
    p1 = sdk.create_point("statement", "Calibrated", credibility="gold")
    # Live uncalibrated point — a DRAFT would be excluded from the gate
    # (#780/PR #1212); since #1088 a no-status create is LIVE, so the live
    # status is stated explicitly here.
    sdk.create_point("statement", "Not calibrated", status="live")
    
    sdk.create_operator("IMPL", p1["id"], [sdk.create_point("statement", "target")["id"]])
    
    with pytest.raises(CalibrationError):
        sdk.compute_confidence(require_calibration=True)


def test_require_calibration_default(sdk):
    """Default require_calibration=True is fail-closed (#344).

    A genuinely uncalibrated graph (no credibility → baseline_set=false)
    must raise CalibrationError under the default instead of silently
    running EP on topology alone; a calibrated graph succeeds.
    """
    # Uncalibrated LIVE graph → fail-closed under the flipped default.
    sdk.create_point("statement", "Uncalibrated claim", status="live")
    with pytest.raises(CalibrationError, match="calibrate_summary"):
        sdk.compute_confidence()  # default True

    # Companion: a calibrated graph succeeds under the default. The gate is
    # graph-wide, so this needs a fresh graph (the uncalibrated point above
    # would still trip it).
    import tempfile
    s2 = TortoiseSDK(os.path.join(tempfile.mkdtemp(prefix="tt_calib_"), "test.db"))
    try:
        p1 = s2.create_point("statement", "Calibrated claim", credibility="gold")
        p2 = s2.create_point("statement", "Related claim", credibility="gold")
        s2.create_operator("IMPL", p1["id"], [p2["id"]])
        result = s2.compute_confidence()  # default True, graph is calibrated
        assert result["converged"] is True
    finally:
        s2.close()


def test_require_calibration_ignores_drafts(sdk):
    """Draft evidence points do NOT trip the fail-closed gate (#780, PR #1212).

    Draft is EXPLICIT since #1088 — a no-status create_point is LIVE — and
    drafts are excluded from factor extraction + EP propagation
    (include_draft=False). A graph whose only uncalibrated points are drafts
    must not demand calibration of points EP will never use — the gate guards
    live evidence only.
    """
    sdk.create_point("statement", "Draft staging claim", status="draft")
    sdk.create_point("statement", "Another draft", status="draft")
    # Draft-only graph → gate passes (nothing live to calibrate); EP finds
    # no live factors and returns a no-op result instead of raising.
    result = sdk.compute_confidence(require_calibration=True)
    assert result.get("diagnostic") == "no_factors"

    # Mixed: one live uncalibrated point still fails closed.
    import tempfile
    s2 = TortoiseSDK(os.path.join(tempfile.mkdtemp(prefix="tt_calib_"), "test.db"))
    try:
        s2.create_point("statement", "Draft staging claim", status="draft")
        s2.create_point("statement", "Live uncalibrated", status="live")
        with pytest.raises(CalibrationError, match="uncalibrated"):
            s2.compute_confidence(require_calibration=True)
    finally:
        s2.close()


# ── calibrate_summary ───────────────────────────────────────────

def test_calibrate_summary_empty(sdk):
    """Empty graph returns empty list."""
    summary = sdk.calibrate_summary()
    assert isinstance(summary, list)


def test_calibrate_summary_source_hint(sdk):
    """Uncalibrated Point with Source → suggestion mentions Source."""
    # Create a Source node via _link_source (creates Source + extractedFrom edge)
    src_url = "https://example.com/blog-post"
    p = sdk.create_point("statement", "Claim from blog", extractedFrom=src_url)  # noqa: F841
    
    summary = sdk.calibrate_summary()
    uncal = [s for s in summary if not s["calibrated"]]
    assert len(uncal) >= 1
    # The suggestion should mention Source since the Point has an extractedFrom edge
    assert "Source" in str(uncal[0].get("suggestion", ""))


# ── create_point credibility ────────────────────────────────────

def test_create_point_credibility(sdk):
    """credibility='gold' stamps the WEAK authored-assertion body, not T0.

    CHANGED ASSERTION (pre-existing, main): this used to pin gold -> Beta(10,1).
    The #398 owner ruling (2026-10-12) supersedes that: the declared word is a
    LABEL, not the claim's strength, and an authored unsourced assertion is ONE
    evidence body at the weakest registered weight. A weak refutation must be
    able to move it — a 10:1 self-declared prior could not (0.9091 -> 0.8992).
    """
    p = sdk.create_point("statement", "Gold tier claim", credibility="gold")
    point = sdk.get_point(p["id"])
    assert point.get("ep_alpha") == pytest.approx(1.0 + AUTHOR_ASSERTION_WEAK_PC)
    assert point.get("ep_beta") == 1
    assert point.get("baseline_set") is True
    assert point.get("credibility") == "T0"  # the word, kept as a label


def test_create_point_dedup_no_overwrite(sdk):
    """Dedup doesn't overwrite existing baseline."""
    p1 = sdk.create_point("statement", "X", credibility="gold", dedup=True)
    # Same content, different credibility — dedup returns existing
    p2 = sdk.create_point("statement", "X", credibility="unverified", dedup=True)
    assert p1["id"] == p2["id"]
    point = sdk.get_point(p1["id"])
    # original authored-assertion body preserved (same weak body for every word,
    # so this asserts the marker/label rather than a word-specific number)
    assert point.get("ep_alpha") == pytest.approx(1.0 + AUTHOR_ASSERTION_WEAK_PC)
    assert point.get("credibility") == "T0"


def test_create_point_no_credibility(sdk):
    """Omit credibility → Beta(1,1), baseline_set=false."""
    p = sdk.create_point("statement", "No credibility set")
    point = sdk.get_point(p["id"])
    # baseline_set should be false/missing
    assert point.get("baseline_set") in (None, False)


# ── set_point_baseline persistence ──────────────────────────────

def test_set_baseline_persistence(sdk):
    """Baseline survives graph round-trip (persisted to node)."""
    p = sdk.create_point("statement", "Will be baselined")
    sdk.set_point_baseline(p["id"], 8, 2)
    point = sdk.get_point(p["id"])
    assert point.get("ep_alpha") == 8
    assert point.get("ep_beta") == 2
    assert point.get("baseline_set") is True


# ── Source inheritance ──────────────────────────────────────────

def test_source_inheritance(sdk):
    """Source T1 → Point gets Beta(5,1) via inheritance (validated model)."""
    # Create Point with extractedFrom — this creates a Source node
    p = sdk.create_point("statement", "Inherited claim", 
                         extractedFrom="https://doi.org/10.1234/peer-reviewed")
    proj = sdk._get_proj()
    # Set credibilityTier on the auto-created Source
    proj.g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = 'T1'",
        params={"url": "https://doi.org/10.1234/peer-reviewed"}
    )
    
    sdk._apply_source_inheritance(recency_decay=1.0)
    point = sdk.get_point(p["id"])
    # T1 = (5, 1) per docs/ep-source-credibility-experiment.md §1.1 (stale (8,2) removed)
    assert point.get("ep_alpha") == 5
    assert point.get("ep_beta") == 1
    assert point.get("baseline_set") is True
    assert point.get("baseline_source") == BASELINE_SOURCE_INHERITED


def test_source_inheritance_multi_source(sdk):
    """Point with Sources T1 and T3 → log-scale aggregated prior (#398).

    pc = T1: 4.0 * log2(2) + T3: 1.0 * log2(2) = 5.0 → Beta(6, 1).
    Replaces the old highest-tier-wins behavior (T1 alone → Beta(5,1)).
    """
    url1 = "https://doi.org/10.1234/peer-reviewed"
    url2 = "https://example.com/anecdotal"
    
    # Create Point with both Sources via _link_source
    p = sdk.create_point("statement", "Multi-source claim")
    proj = sdk._get_proj()
    proj._link_source(p["id"], url1)
    proj._link_source(p["id"], url2)
    
    # Set credibilityTiers on the auto-created Sources
    proj.g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = 'T1'",
        params={"url": url1}
    )
    proj.g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = 'T3'",
        params={"url": url2}
    )
    
    sdk._apply_source_inheritance(recency_decay=1.0)
    point = sdk.get_point(p["id"])
    assert point.get("ep_alpha") == 6  # log-scale aggregation: 1 + 4*1 + 1*1
    assert point.get("ep_beta") == 1
    assert point.get("baseline_source") == BASELINE_SOURCE_INHERITED


# ── baseline_set flag ───────────────────────────────────────────

def test_baseline_set_flag(sdk):
    """credibility → baseline_set=true, omit → baseline_set=false."""
    p1 = sdk.create_point("statement", "With credibility", credibility="high")
    p2 = sdk.create_point("statement", "Without credibility")
    
    assert sdk.get_point(p1["id"]).get("baseline_set") is True
    assert sdk.get_point(p2["id"]).get("baseline_set") in (None, False)


# ── Non-evidence kinds don't block gate ─────────────────────────

def test_non_evidence_kinds_ignored_by_gate(sdk):
    """Diary/checkpoint points with no baseline shouldn't block EP."""
    sdk.create_point("diary", "Today's note")
    p = sdk.create_point("statement", "A claim", credibility="medium")  # noqa: F841
    
    # Gate should pass — diary is not an evidence kind
    result = sdk.compute_confidence(require_calibration=True)
    assert result["converged"] is True


# ── #1157: un-gated EP surfaces (dream / get_confidence) ────────

def test_dream_require_calibration_raises(sdk):
    """dream(require_calibration=True) on uncalibrated graph raises
    CalibrationError BEFORE any EP write (#1157)."""
    # Since #1088 a no-status create_point is LIVE, and a DRAFT is excluded
    # from the #1157 gate (#780/PR #1212), so the point must be explicitly
    # live for the gate to see it.
    sdk.create_point("statement", "Uncalibrated claim", status="live")

    with pytest.raises(CalibrationError, match="dream.*uncalibrated"):  # noqa: RUF043
        sdk.dream(require_calibration=True)


def test_dream_gated_passes_when_calibrated(sdk):
    """dream(require_calibration=True) runs on a calibrated graph."""
    p = sdk.create_point("statement", "Calibrated", credibility="gold")
    sdk.create_operator("IMPL", p["id"],
                        [sdk.create_point("statement", "target",
                                          credibility="gold")["id"]])

    # Mark dirty so the dream path actually runs EP work
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator = false SET n.confidence = 0.4")
    sdk._dirty_roots.update(
        r[0] for r in proj.g.query(
            "MATCH (n:Point) WHERE n.is_operator = false RETURN n.id").result_set)

    result = sdk.dream(dirty_only=True, require_calibration=True)
    assert result["converged"] is True


def test_get_confidence_require_calibration_raises(sdk):
    """get_confidence(require_calibration=True) on uncalibrated graph raises
    CalibrationError (#1157) — the per-claim read is an EP surface."""
    p = sdk.create_point("statement", "Uncalibrated claim", status="live")

    with pytest.raises(CalibrationError, match="get_confidence.*uncalibrated"):  # noqa: RUF043
        sdk.get_confidence(p["id"], require_calibration=True)


def test_ep_require_calibration_env_default(sdk, monkeypatch):
    """TORTOISE_EP_REQUIRE_CALIBRATION=1 flips the shared default for the
    #1157 surfaces (dream, get_confidence) — the #344 flip semantics applied
    to the once-un-gated surfaces, one knob for the #7478 target. The
    explicit compute_confidence surface keeps its own (still-False) default;
    #344 flips it separately."""
    monkeypatch.setenv("TORTOISE_EP_REQUIRE_CALIBRATION", "1")
    p = sdk.create_point("statement", "Uncalibrated claim", status="live")

    # #1157 surfaces refuse by default (no explicit arg)
    with pytest.raises(CalibrationError, match="dream"):
        sdk.dream()
    with pytest.raises(CalibrationError, match="get_confidence"):
        sdk.get_confidence(p["id"])

    # Explicit False is still the documented escape hatch on dream
    result = sdk.dream(require_calibration=False)
    assert result["converged"] is True


# ── #7739: the gate is scoped to the run's selection ────────────
#
# One uncalibrated point ANYWHERE used to freeze every caller because the
# gate was graph-wide. The gate must instead be evaluated against the SAME
# selection the run consumes, and only for points EP can actually consume
# (not draft AND not terminal, EP's ``_live_only`` set).

def _anchored_subgraph(sdk, *, calibrated_target=True):
    """Anchor A <-(op)- target B, both live; B calibrated unless told not to."""
    p1 = sdk.create_point("statement", "Anchor A", credibility="gold",
                          status="live")
    p2 = sdk.create_point(
        "statement", "Target B",
        credibility="gold" if calibrated_target else None,
        status="live")
    op = sdk.create_operator("IMPL", p1["id"], [p2["id"]])
    return p1, p2, op


def test_foreign_uncalibrated_point_does_not_block_anchored_run(sdk):
    """#7739 (required): an uncalibrated point OUTSIDE the selection is not a blocker.

    The anchored subgraph is fully calibrated; the foreign live point is
    reachable from NOTHING. Before #7739 the whole-graph gate raised for
    this caller; now it runs the selection it was asked for.
    """
    p1, _p2, _op = _anchored_subgraph(sdk)
    sdk.create_point("statement", "Foreign uncalibrated", status="live")

    result = sdk.compute_confidence(anchors=[p1["id"]],
                                    require_calibration=True)
    assert result["converged"] is True
    assert p1["id"] in result["confidences"]


def test_uncalibrated_point_inside_selection_still_blocks(sdk):
    """#7739 (required): the guard is NOT weakened — an in-scope point raises.

    Target B is a factor participant of the selected operator and is
    uncalibrated, so the gate must still fail closed (#7478/#1157);
    ``require_calibration=False`` remains the only bypass.
    """
    p1, p2, _op = _anchored_subgraph(sdk, calibrated_target=False)

    with pytest.raises(CalibrationError, match="uncalibrated") as exc:
        sdk.compute_confidence(anchors=[p1["id"]], require_calibration=True)
    assert p2["id"] in str(exc.value)

    # The explicit opt-out is untouched.
    result = sdk.compute_confidence(anchors=[p1["id"]],
                                    require_calibration=False)
    assert result["converged"] is True


def test_uncalibrated_point_beyond_the_selected_operators_still_blocks(sdk):
    """#7739: an uncalibrated point the RUN reaches still raises.

    The gate is scoped to the run's consumed set, not merely to the selected
    operators' immediate endpoints. ``ep.run`` re-expands ``max_hops`` from
    the selection's seeds, so an anchored ``max_hops=1`` run on
    A-op1-B-op2-C consumes C — C is an endpoint of no SELECTED operator — and
    an uncalibrated C must still fail closed. Deriving the scope from
    ``_select_subgraph``'s operators alone misses C; this test pins the exact
    scope, so it fails if the gate is narrowed to those endpoints.
    """
    a = sdk.create_point("statement", "A", credibility="gold", status="live")
    b = sdk.create_point("statement", "B", credibility="gold", status="live")
    c = sdk.create_point("statement", "C", status="live")  # uncalibrated
    sdk.create_operator("IMPL", a["id"], [b["id"]])
    sdk.create_operator("IMPL", b["id"], [c["id"]])

    with pytest.raises(CalibrationError, match="uncalibrated") as exc:
        sdk.compute_confidence(anchors=[a["id"]], max_hops=1,
                               require_calibration=True)
    assert c["id"] in str(exc.value)


def test_terminal_uncalibrated_point_does_not_block(sdk):
    """#7739: a TERMINAL point is never an EP blocker (whole-graph surfaces too).

    EP's live set is ``_live_only`` = not draft AND NOT terminal, so a
    superseded/retracted/outdated point is one EP will never consume.
    Pre-#7739 only drafts were excused, so an uncalibrated ``superseded``
    point froze every caller (the measured ``1a10706a0-1884perSuite``).

    Since #7739 the no-arg path is SCOPED (dirty roots and their closure), and
    the run's own consumed set already strips a terminal point — so what this
    test pins is that a dead point never reaches the caller as a blocker, not
    the ``_live_only`` clause itself. #9063 moved that clause from the audit
    summary's Python mirror into the gate's own Cypher predicate; the clause is
    pinned directly by ``test_legacy_draft_and_terminal_points_do_not_block``
    and ``test_require_calibration_ignores_drafts``.
    """
    sdk.create_point("statement", "Live calibrated", credibility="gold",
                     status="live")
    sdk.create_point("statement", "Dead uncalibrated", status="superseded")

    result = sdk.compute_confidence(require_calibration=True)
    assert isinstance(result, dict)


def test_outdated_flag_point_does_not_block(sdk):
    """#7739: the legacy ``outdated=true`` flag is terminal too.

    ``invalidate_point`` sets the flag WITHOUT touching status, so status-only
    terminal detection would keep a dead point as a whole-graph blocker. The
    no-arg run is scoped (#7739) and ``invalidate_point`` clears the dirty flag,
    so the caller-visible outcome alone would stay green with or without the
    ``outdated`` half — the whole-graph query is asserted directly below, and
    that list is empty ONLY because of the flag (measured).
    """
    sdk.create_point("statement", "Live calibrated", credibility="gold",
                     status="live")
    dead = sdk.create_point("statement", "Flagged uncalibrated",
                            status="live")
    corrector = sdk.create_point("statement", "Corrector", credibility="gold",
                                 status="live")
    sdk.invalidate_point(dead["id"], corrector["id"])
    assert sdk.get_point(dead["id"])["outdated"] is True

    assert dead["id"] not in sdk._uncalibrated_ep_participants(None)
    result = sdk.compute_confidence(require_calibration=True)
    assert isinstance(result, dict)


def _make_foreign(sdk, point_id):
    """Make a point look like one another lane left behind (#7739).

    It stays in the graph — and therefore in ``calibrate_summary()`` — but is
    NOT a dirty root: the ``ep_dirty`` flag is cleared in the graph and the
    in-memory hot set drops it. That is the measured state of the #7739
    blockers: they are live, uncalibrated, and (mostly) not pending work for
    the caller whose run they used to freeze.
    """
    sdk._dirty_roots.discard(point_id)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_dirty = null",
        params={"id": point_id})


def test_no_arg_gate_scopes_to_the_dirty_roots(sdk):
    """#7739: a FOREIGN uncalibrated point no longer freezes the no-arg run.

    The no-arg path's selection IS the dirty roots and their closure, so the
    gate is evaluated against exactly that. A point outside it — not a dirty
    root, reachable from nothing here — must not raise, exactly as the
    anchored path already behaves. Pre-#7739 this path kept the whole-graph
    posture and the caller was frozen.
    """
    a, _b, _op = _anchored_subgraph(sdk)  # my own calibrated dirty roots
    foreign = sdk.create_point("statement", "Foreign uncalibrated",
                               status="live")
    _make_foreign(sdk, foreign["id"])

    result = sdk.compute_confidence(require_calibration=True)
    assert a["id"] in result["confidences"]


def test_no_arg_gate_still_raises_for_an_uncalibrated_dirty_root(sdk):
    """#7739: the guard is NOT weakened on the no-arg path.

    A dirty root is a point this run was explicitly asked to recompute, so it
    stays in the gate's scope: narrowing the scope never excuses the run's
    own seed. ``require_calibration=False`` remains the only bypass.
    """
    sdk.create_point("statement", "Uncalibrated root", status="live")

    with pytest.raises(CalibrationError, match="uncalibrated"):
        sdk.compute_confidence(require_calibration=True)

    # The documented opt-out is untouched, and it does not gate.
    assert isinstance(
        sdk.compute_confidence(require_calibration=False), dict)


def test_dream_local_scopes_to_the_dirty_roots(sdk):
    """#7739: a LOCAL dream is scoped to the roots it seeds.

    ``dream(dirty_only=True, mode="local")`` is the write-drain path; a
    foreign uncalibrated point must not refuse the drain.
    """
    _anchored_subgraph(sdk)
    foreign = sdk.create_point("statement", "Foreign uncalibrated",
                               status="live")
    _make_foreign(sdk, foreign["id"])

    result = sdk.dream(dirty_only=True, mode="local",
                       require_calibration=True)
    assert result["mode"] == "local"


def test_dream_local_still_raises_for_an_uncalibrated_root(sdk):
    """#7739: a local dream still fails closed on its own uncalibrated root."""
    sdk.create_point("statement", "Uncalibrated root", status="live")

    with pytest.raises(CalibrationError, match=r"dream.*uncalibrated"):
        sdk.dream(dirty_only=True, mode="local", require_calibration=True)


def test_dream_local_gate_covers_the_passs_reexpansion(sdk):
    """#7739: the LOCAL dream's scope is the SEED SET the pass runs, not the
    roots' own closure.

    ``Dreamer.dream`` selects operators within ``max_hops`` of the roots and
    ``ep.run`` then re-expands ``max_hops`` from THOSE seeds, so the pass
    consumes claims OUTSIDE the roots' own closure — measured on a plain
    operator chain at ``max_hops=3``: consumed reaches hop 6 while the roots'
    closure stops at hop 5, and at ``max_hops=4`` two hops. A gate scoped to
    the roots' closure therefore lets an uncalibrated point inside the pass's
    REAL consumed set run ungated, which is the #7478 defect the gate exists
    to prevent. The gate is evaluated on the seed ids the pass hands to
    ``ep.run`` (``Dreamer.dream(before_run=...)``), so the two cannot drift.
    """
    # Chain of 7 claims; hops 1-6 calibrated, hop 6 UNcalibrated. Only hop 0
    # is a dirty root, so the remaining points look like another lane's work.
    chain = []
    for i in range(7):
        chain.append(sdk.create_point(
            "statement", f"hop {i}", status="live",
            credibility="gold" if i < 6 else None))
    for i in range(len(chain) - 1):
        sdk.create_operator("IMPL", chain[i]["id"], [chain[i + 1]["id"]])
    for p in chain[1:]:
        _make_foreign(sdk, p["id"])

    target = chain[6]
    # Precondition of the test: the re-expanded point is OUTSIDE the roots'
    # own closure. If this ever stops holding the test is vacuous, so pin it.
    assert target["id"] not in sdk._ep_run_scope([chain[0]["id"]], 3)

    with pytest.raises(CalibrationError, match="dream") as exc:
        sdk.dream(dirty_only=True, mode="local", max_hops=3,
                  require_calibration=True)
    assert target["id"] in str(exc.value), (
        "the gate must fail closed on a point the pass consumes")

    # …and the pass really does consume it — this is the write the gate is
    # protecting, so the scope above is not merely conservative.
    sdk.dream(dirty_only=True, mode="local", max_hops=3,
              require_calibration=False)
    assert target["id"] in sdk._get_ep()._last_affected


def test_dream_whole_graph_modes_keep_whole_graph_scope(sdk):
    """#7739 boundary, pinned deliberately: ``full`` and ``stale-first`` stay
    whole-graph.

    ``full`` is whole-graph by contract. A ``stale-first`` window is SELECTED
    inside the pass (``Dreamer.dream_window``: staleness ranking ∪ dirty
    roots, and a never-dreamed graph degenerates to the whole graph), so
    scoping it at the gate would require a second copy of the scheduler's
    window derivation — exactly the gate/run drift #7739 exists to remove.
    A foreign uncalibrated point therefore still refuses both modes. This is
    a stated boundary, not an oversight.
    """
    _anchored_subgraph(sdk)
    foreign = sdk.create_point("statement", "Foreign uncalibrated",
                               status="live")
    _make_foreign(sdk, foreign["id"])

    for mode in ("full", "stale-first"):
        with pytest.raises(CalibrationError, match="dream"):
            sdk.dream(mode=mode, require_calibration=True)


def test_get_confidence_scopes_to_the_claim(sdk):
    """#7739: a per-claim read is gated on the claim it reads — and ONLY on it.

    ``get_confidence`` puts exactly one claim in a natural parameter
    (``ep.compute_confidence`` reads that node), so a foreign point must not
    freeze the read even when it is CONNECTED to the claim through a factor
    the read never evaluates. That connected case is the discriminating one:
    an isolated foreign point is outside every closure, so it would pass even
    with a neighbourhood-wide scope. An uncalibrated CLAIM still raises (the
    guard is kept; only its scope narrows), and a dirty claim still gets its
    lazy dream gated on the pass's own seeds.
    """
    mine = sdk.create_point("statement", "Mine calibrated",
                            credibility="gold", status="live")
    foreign = sdk.create_point("statement", "Foreign uncalibrated",
                               status="live")
    sdk.create_operator("IMPL", mine["id"], [foreign["id"]])
    # The claim is clean (no lazy dream) and the foreign point is not a dirty
    # root: the read must answer from the claim's own params.
    _make_foreign(sdk, foreign["id"])
    _make_foreign(sdk, mine["id"])

    result = sdk.get_confidence(mine["id"], require_calibration=True)
    assert "mean" in result

    uncal = sdk.create_point("statement", "Uncalibrated claim",
                             status="live")
    with pytest.raises(CalibrationError,
                       match=r"get_confidence.*uncalibrated"):
        sdk.get_confidence(uncal["id"], require_calibration=True)


def test_get_confidence_dirty_claim_still_gates_its_dream(sdk):
    """#7739: a DIRTY claim's lazy dream is still gated on what it consumes.

    Narrowing the read's own scope must not become a hole in the write path:
    when the claim is a dirty root, ``get_confidence`` dreams, and that pass
    consumes the connected uncalibrated point — so it must refuse, naming it.
    """
    mine = sdk.create_point("statement", "Mine calibrated",
                            credibility="gold", status="live")
    foreign = sdk.create_point("statement", "Foreign uncalibrated",
                               status="live")
    sdk.create_operator("IMPL", mine["id"], [foreign["id"]])
    assert mine["id"] in sdk._dirty_roots

    with pytest.raises(CalibrationError, match="dream") as exc:
        sdk.get_confidence(mine["id"], require_calibration=True)
    assert foreign["id"] in str(exc.value)


# ── #9063: the gate's universe is EP's participant set, not the audit's ──
#
# The gate used to intersect ``calibrate_summary()``'s rows, so every filter
# that belongs to the human AUDIT surface (a bare ``n.is_operator = false``, the
# ``pointKind <> 'assessment'`` source-assessment exclusion) silently became a
# blind spot in the gate. The audit keeps its own selection (it is a guidance
# surface, not the gate), but its non-operator predicate is now the GATE's —
# absence-or-false with NO ``op_type`` conjunct (EP's factor-input arm has no
# non-operator filter, so an ``op_type``-bearing node is a participant), so the
# guidance the error message points at lists every node the gate names.
# MEASURED before the fix (this suite's fixture, a ``credibility="gold"``
# anchor): a legacy evidence Point whose ``is_operator`` property is ABSENT — a
# shape EP's own participant predicate ADMITS (ep.py, the #3139/#3154 legacy
# shape) — was consumed by the run and STAMPED with a posterior (confidence
# 0.508845603290434, ep_alpha 1.28695089612927) while
# ``compute_confidence(require_calibration=True)`` returned normally. These
# tests pin the universe on EP's predicate instead.
#
# NOT closed here, deliberately — both are the SAME owner question ("which
# node kinds must carry a prior?", #9063 belief model):
#   * :Subject/:Object/:Event participants. EP propagates confidence across the
#     four epistemic labels (#7813 ruling, #7853), but an entity cannot be
#     calibrated at all today (``set_point_baseline`` writes :Point), so gating
#     them would be a gate that can never be satisfied. The constant
#     ``sdk._CALIBRATION_GATED_LABELS`` is where that answer lands.
#   * NON-EVIDENCE Point kinds, including the ``pointKind='assessment'`` record
#     the shipped ``assess_source`` writes (#398). They are EP-consumable and
#     carry no prior, so EP can stamp them — but demanding a prior of a
#     source-reliability record is a belief-model change, not a bug fix; the
#     constant ``sdk._CALIBRATION_EVIDENCE_KINDS`` is where that lands.
# What IS pinned: the writer no longer lies about the entity class.

def _make_legacy_point(sdk, content, **props):
    """An evidence Point with ``is_operator`` ABSENT (legacy graph shape).

    Every writer today stamps ``is_operator``; graphs written before #3139/
    #3154 do not, and EP admits them on purpose — which is why the gate must
    see them too.
    """
    p = sdk.create_point("statement", content, **props)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) REMOVE n.is_operator", params={"id": p["id"]})
    absent = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.is_operator, "
        "EXISTS(n.is_operator)", params={"id": p["id"]}).result_set
    assert absent == [[None, False]], absent
    return p


def test_legacy_point_with_absent_is_operator_is_gated(sdk):
    """#9063 (the measured hole): EP consumes it, so the gate must see it.

    ``create_operator`` accepts the legacy target, EP propagates into it and
    writes ``n.confidence`` — an uncalibrated EP run stamping a belief
    (#7478). Pre-fix this returned normally because the audit summary's
    ``n.is_operator = false`` matches ZERO rows on a property-ABSENT node.
    """
    anchor = sdk.create_point("statement", "Anchor", credibility="gold",
                              status="live")
    legacy = _make_legacy_point(sdk, "Legacy target", status="live")
    sdk.create_operator("IMPL", anchor["id"], [legacy["id"]])

    with pytest.raises(CalibrationError, match="uncalibrated") as exc:
        sdk.compute_confidence(anchors=[anchor["id"]],
                               require_calibration=True)
    assert legacy["id"] in str(exc.value)

    # Not stamped: the refusal happens BEFORE ep.run() (#344 fail-closed).
    stamped = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence", 
        params={"id": legacy["id"]}).result_set
    assert stamped[0][0] is None

    # The documented opt-out still runs, and shows the stamp the gate blocked.
    sdk.compute_confidence(anchors=[anchor["id"]], require_calibration=False)
    assert sdk.get_point(legacy["id"])["confidence"] is not None


def test_guidance_the_error_offers_lists_the_point_it_named(sdk):
    """#9063: following the error's own advice is not an empty page.

    ``_ensure_calibrated`` names the offending ids and says "Run
    calibrate_summary() for full guidance". That audit filtered with the bare
    ``n.is_operator = false``, which matches ZERO rows on the legacy
    property-ABSENT Point the gate now names — so the ONE remedy the message
    offered listed nothing for exactly the class #9063 closes. The audit now
    reads the same canonical non-operator predicate as the gate.
    """
    legacy = _make_legacy_point(sdk, "Legacy needing guidance", status="live")

    with pytest.raises(CalibrationError, match="calibrate_summary") as exc:
        sdk.compute_confidence(require_calibration=True)
    assert legacy["id"] in str(exc.value)

    rows = {row["id"]: row for row in sdk.calibrate_summary()}
    assert legacy["id"] in rows, sorted(rows)
    assert rows[legacy["id"]]["calibrated"] is False
    assert "set_point_baseline" in rows[legacy["id"]]["suggestion"]


def test_guidance_lists_every_consumed_shape_the_gate_demands(sdk):
    """#9063: the audit's universe must COVER the gate's, or the remedy is empty.

    Both shapes here are in EP's factor-input set (the ``(o)->(c)`` arm has no
    non-operator filter), so the gate demands a prior for each; the guidance
    must therefore list each — including the hybrid ``create_point(op_type=...)``
    node, whose ``is_operator = false`` the audit has always matched.
    """
    hybrid = sdk.create_point("statement", "Hybrid", op_type="NAND",
                              status="live")
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:'legacy-op-9063', pointKind:'statement', "
        "op_type:'IMPL', status:'live'})")
    sdk._get_proj().g.query(
        "CREATE (n:Point {pointKind:'statement', status:'live'})")

    ids = {row["id"] for row in sdk.calibrate_summary()}
    assert hybrid["id"] in ids
    assert "legacy-op-9063" in ids
    # An id-less node is not a remedy a caller can run (and not a participant).
    assert None not in ids


def test_legacy_point_outside_the_selection_does_not_block(sdk):
    """#9063 must not undo #7739: the widened universe stays SCOPED.

    The legacy point here is reachable from nothing and is not a dirty root,
    so it is not a blocker for an unrelated run — otherwise closing one blind
    spot would re-open the whole-graph freeze #7739 removed. (It IS found by
    the gate's graph-wide existence probe, so this pins the SCOPE half rather
    than the probe.)
    """
    anchor, _target, _op = _anchored_subgraph(sdk)  # my own, calibrated
    legacy = _make_legacy_point(sdk, "Foreign legacy", status="live")
    _make_foreign(sdk, legacy["id"])
    assert legacy["id"] not in sdk._ep_run_scope([anchor["id"]], None)

    result = sdk.compute_confidence(anchors=[anchor["id"]],
                                    require_calibration=True)
    assert anchor["id"] in result["confidences"]


def test_legacy_draft_and_terminal_points_do_not_block(sdk):
    """#9063 keeps EP's LIVE filter for the legacy shape (draft + terminal).

    A draft never feeds EP and a terminal point is one EP will never consume
    (#780/#7739), so neither is a blocker on the whole-graph surface — the
    predicate reads the same ``_live_only`` EP does, not a status literal.
    """
    sdk.create_point("statement", "Live calibrated", credibility="gold",
                     status="live")
    _make_legacy_point(sdk, "Legacy draft", status="draft")
    _make_legacy_point(sdk, "Legacy dead", status="superseded")

    # The whole-graph query itself (no scope, no run) — this IS the _live_only
    # half: both legacy points are uncalibrated evidence Points by every other
    # clause, and neither is demanded.
    assert sdk._uncalibrated_ep_participants(None) == []
    assert isinstance(sdk.compute_confidence(require_calibration=True), dict)


def test_non_evidence_kind_is_still_not_gated(sdk):
    """#9063 boundary, pinned: the EVIDENCE-KIND filter is doctrine, not drift.

    A ``goal`` is a DERIVED belief whose posterior propagates from evidence;
    demanding a prior of it is a different belief model (the #9063 decision),
    so the universe widening must not silently include it. If that is ever
    ruled in, it changes ``_CALIBRATION_EVIDENCE_KINDS`` — and this test fails
    on purpose, as the record that the change is a decision and not a cleanup.
    """
    goal = sdk.create_point("goal", "Ship it", credibility=None, status="live")
    sdk.create_point("statement", "Live calibrated", credibility="gold",
                     status="live")

    assert isinstance(sdk.compute_confidence(require_calibration=True), dict)
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN coalesce(n.baseline_set, false)",
        params={"id": goal["id"]}).result_set
    assert row == [[False]]


def test_set_point_baseline_refuses_a_non_point(sdk):
    """#9063: the writer must not report a baseline it did not write.

    MEASURED before the fix: ``set_point_baseline(<subject id>, 3.0, 1.0)``
    returned ``{'claim_id': ..., 'alpha': 3.0, 'beta': 1.0, 'source':
    'set-by-author'}`` while ``baseline_set``/``ep_alpha``/``ep_beta`` stayed
    NULL — a caller could believe a node was calibratable when it was not, and
    the in-memory prior diverged from the graph.
    """
    subj = sdk.create_subject("subject B")

    with pytest.raises(ValueError, match=r":Subject"):
        sdk.set_point_baseline(subj["id"], 3.0, 1.0)

    row = sdk._get_proj().g.query(
        "MATCH (n:Subject {id:$id}) RETURN n.baseline_set, n.ep_alpha, n.ep_beta",
        params={"id": subj["id"]}).result_set
    assert row == [[None, None, None]]
    # The refused write leaves no in-memory prior behind either.
    assert subj["id"] not in sdk._evidence


def test_set_point_baseline_refuses_an_event(sdk):
    """#9063: same refusal on the other epistemic labels (':Event' here)."""
    ev = sdk.create_event("launch", "occurrence")
    with pytest.raises(ValueError, match=r":Event"):
        sdk.set_point_baseline(ev["id"], 2.0, 1.0)
    assert ev["id"] not in sdk._evidence


def test_set_point_baseline_still_writes_a_point(sdk):
    """#9063 regression guard: the honest-writer path writes and reports."""
    p = sdk.create_point("statement", "Calibratable", status="live")
    result = sdk.set_point_baseline(p["id"], 4.0, 2.0)

    assert result["claim_id"] == p["id"]
    assert result["alpha"] == 4.0 and result["beta"] == 2.0
    assert sdk.get_point(p["id"])["baseline_set"] is True
    assert sdk._evidence[p["id"]] == (4.0, 2.0)


def test_legacy_op_type_only_operator_is_gated(sdk):
    """#9063: a node EP consumes as a FACTOR INPUT needs a prior, operator or not.

    A legacy operator carries ``op_type`` WITHOUT ``is_operator`` (#3139/#3154).
    It is tempting to exclude it with the claim-predicate's ``op_type IS NULL``
    conjunct — but EP's factor-INPUT arm (``ep.py`` Batch 2, the ``(o)->(c)``
    selection) has NO non-operator filter, so such a node IS in
    ``_ep_run_scope`` and DOES get a posterior (measured). An earlier draft
    excluded ``op_type``-bearing nodes and thereby REMOVED coverage the audit
    had before #9063 for the hybrid shape (``is_operator = false`` WITH
    ``op_type``) — a fail-open. The demand is fail-closed and satisfiable: the
    node is a ``:Point``, so ``set_point_baseline`` closes it.
    """
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:'legacy-op-9063', pointKind:'statement', "
        "op_type:'NAND', status:'live'})")

    assert sdk._uncalibrated_ep_participants(None) == ["legacy-op-9063"]
    sdk.set_point_baseline("legacy-op-9063", 2.0, 1.0)
    assert sdk._uncalibrated_ep_participants(None) == []


def test_point_with_absent_point_kind_is_gated(sdk):
    """#9063: an ABSENT kind is evidence — EP stamps it, so the gate sees it.

    EP reads no ``pointKind`` at all, and the repo's own resolution fills an
    absent kind with ``'statement'`` (``backfill_v25``). MEASURED before this
    clause: the run stamped ``confidence 0.508845603290434`` on such a node
    while the gate's allow-list dropped it (``NULL IN [...]`` is NULL).
    """
    anchor = sdk.create_point("statement", "Anchor", credibility="gold",
                              status="live")
    kindless = sdk.create_point("statement", "Kind-less target",
                               status="live")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) REMOVE n.pointKind",
        params={"id": kindless["id"]})
    sdk.create_operator("IMPL", anchor["id"], [kindless["id"]])

    with pytest.raises(CalibrationError, match="uncalibrated") as exc:
        sdk.compute_confidence(anchors=[anchor["id"]],
                               require_calibration=True)
    assert kindless["id"] in str(exc.value)


def test_empty_point_kind_is_gated_like_an_absent_one(sdk):
    """#9592: an EMPTY kind is ABSENCE, so the gate must demand a prior for it.

    The kind arm demanded a prior for an ABSENT ``pointKind`` ("absence is
    evidence") but let an EMPTY-STRING kind fall into the string arm and fail
    membership — so the two spellings of "no kind at all", which mean the same
    thing to a consumer that reads no kind (EP), landed on opposite sides of the
    gate. MEASURED before this clause (embedded engine):

        p = create_point('', 'empty kind', status='live')   # pointKind=''
        create_operator('IMPL', anchor, [p['id']])
        _uncalibrated_ep_participants(None)   -> []          # FAIL-OPEN
        compute_confidence(require_calibration=True)  -> RAN, converged=True
            node stamped  ep_alpha 1.28700840529772, ep_beta 1.24220750014412
        REMOVEd the property instead             -> demanded (the asymmetry)

    …and ``calibrate_summary()`` listed the node as uncalibrated at the same
    time, so the audit and the gate disagreed (#9063's divergence, in a
    spelling #9063 left open). Reachable: ``create_point('', …)`` is accepted
    (an empty kind carries no character for #9287's write-door rule to refuse)
    and the MCP ``tortoise_create_point`` tool forwards a client's ``kind``
    verbatim.

    Both gate arms are asserted, because they are two queries: the whole-graph
    form and the scoped (``ids=…``) form #7739 hands ``compute_confidence``.
    """
    anchor = sdk.create_point("statement", "Anchor", credibility="gold",
                              status="live")
    empty = sdk.create_point("", "Empty kind target", status="live")
    assert sdk.get_point(empty["id"])["pointKind"] == ""
    sdk.create_operator("IMPL", anchor["id"], [empty["id"]])

    assert sdk._uncalibrated_ep_participants(None) == [empty["id"]]
    # the SCOPED arm is a different query and must agree (#7739)
    assert sdk._uncalibrated_ep_participants({empty["id"]}) == [empty["id"]]
    # …and so must the AUDIT surface it used to disagree with
    row = {r["id"]: r for r in sdk.calibrate_summary()}[empty["id"]]
    assert row["calibrated"] is False
    assert "set_point_baseline" in row["suggestion"]

    # The consumed node is refused BEFORE the run stamps it — the #7478 class.
    with pytest.raises(CalibrationError, match="uncalibrated") as exc:
        sdk.compute_confidence(anchors=[anchor["id"]],
                               require_calibration=True)
    assert empty["id"] in str(exc.value)
    stamped = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.ep_alpha, n.ep_beta",
        params={"id": empty["id"]}).result_set
    assert stamped == [[None, None]]

    # Fail-closed AND satisfiable: the node is a :Point, so the writer closes it.
    sdk.set_point_baseline(empty["id"], 2.0, 1.0)
    assert sdk._uncalibrated_ep_participants(None) == []
    sdk.compute_confidence(anchors=[anchor["id"]], require_calibration=True)


def test_whitespace_only_point_kind_is_gated_like_an_absent_one(sdk):
    """#9592: normalisation makes a WHITESPACE-ONLY kind empty, so it is gated.

    The predicate normalises a kind by REMOVING the whitespace family wherever
    it occurs (``_strip_whitespace`` — ``trim()`` strips space only on this
    engine), so ``'   '`` normalises to ``''``. Reading the empty arm off the RAW
    property would have left that spelling fail-open — the same class of hole,
    one spelling over, which is exactly what the normalisation exists to close.
    EP reads no kind at all, so the node is a consumed claim either way.
    """
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:'blank-kind-9592', pointKind:'   ',"
        " status:'live'})")

    assert sdk._uncalibrated_ep_participants(None) == ["blank-kind-9592"]
    assert sdk._uncalibrated_ep_participants({"blank-kind-9592"}) == [
        "blank-kind-9592"]
    sdk.set_point_baseline("blank-kind-9592", 2.0, 1.0)
    assert sdk._uncalibrated_ep_participants(None) == []


def test_gated_labels_are_point_only():
    """#9063 boundary, pinned: widening the LABEL set is the owner's decision.

    EP propagates confidence across Point/Subject/Object/Event (#7813/#7853),
    but an entity cannot be calibrated at all today, so gating them would be a
    gate that can never be satisfied (#8175's trap). This assertion is the
    record: flipping the constant is a PRODUCT decision (owner-reserved, and
    the entity write path must learn to carry a prior first), never a cleanup —
    and it must turn this line red on the way in.
    """
    from tortoise import sdk as sdk_module

    assert sdk_module._CALIBRATION_GATED_LABELS == ("Point",)
    assert set(sdk_module._CALIBRATION_EVIDENCE_KINDS) == {
        "statement", "observation", "hypothesis"}


def test_truthy_non_boolean_baseline_flag_is_still_demanded(sdk):
    """#9063: the demand is "exactly ``true``", not "truthy in Cypher".

    The old gate read the AUDIT's column, which was the raw
    ``coalesce(n.baseline_set, false)`` tested for Python truthiness — truthy
    for the string ``'true'`` — so it waved the run through; the old audit
    likewise reported it calibrated with no suggestion, while
    ``_hydrate_evidence`` (``n.baseline_set = true``) loaded no prior and EP ran
    prior-less. The demand is now the exact complement of what
    ``_hydrate_evidence`` loads, and the audit reads the same predicate. (A
    FALSY flag such as ``0`` was already demanded by the old form — that half is
    unchanged.)
    """
    p = sdk.create_point("statement", "String flag", status="live")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.baseline_set = 'true'",
        params={"id": p["id"]})

    assert sdk._uncalibrated_ep_participants(None) == [p["id"]]
    assert sdk.get_point(p["id"])["baseline_set"] == "true"

    row = {r["id"]: r for r in sdk.calibrate_summary()}[p["id"]]
    assert row["calibrated"] is False
    assert "set_point_baseline" in row["suggestion"]


def test_flag_without_an_alpha_is_still_demanded(sdk):
    """#9063: ``baseline_set = true`` with no ``ep_alpha`` loads NO prior.

    ``_hydrate_evidence`` needs BOTH (``baseline_set = true AND ep_alpha IS NOT
    NULL``), so demand is its exact complement, not ``baseline_set`` alone: a
    MEASURED hand-written shape (flag set, alpha absent — no writer mints it,
    ``ep_alpha`` is deny-listed for the point writers) was waved through and a
    gated run then stamped ``confidence 0.527967791303324`` off the coalesced
    ``Beta(1, 1)`` — the #7478 class this gate exists to refuse, and identical
    on ``origin/main``. ``set_point_baseline`` satisfies it (it writes both).
    """
    p = sdk.create_point("statement", "Flag without alpha", credibility="gold")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.baseline_set = true, n.ep_alpha = null",
        params={"id": p["id"]})

    assert sdk._uncalibrated_ep_participants(None) == [p["id"]]
    row = {r["id"]: r for r in sdk.calibrate_summary()}[p["id"]]
    assert row["calibrated"] is False
    sdk.set_point_baseline(p["id"], 3.0, 1.0)
    assert sdk._uncalibrated_ep_participants(None) == []

    # The BETA half is the same defect one property over: ``_hydrate_evidence``
    # loads ``(3.0, None)`` and EP dies on ``ev[1] - 1.0`` with a bare
    # ``TypeError`` (#9275), so the gate must refuse it with its own diagnosable
    # error rather than let the run crash.
    q = sdk.create_point("statement", "Alpha without beta", status="live")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.baseline_set = true, n.ep_alpha = 3.0, "
        "n.ep_beta = null", params={"id": q["id"]})
    assert q["id"] in sdk._uncalibrated_ep_participants(None)
    assert {r["id"]: r for r in sdk.calibrate_summary()}[q["id"]][
        "calibrated"] is False
    sdk.set_point_baseline(q["id"], 2.0, 1.0)
    assert sdk._uncalibrated_ep_participants(None) == []


def test_mis_cased_evidence_kind_is_demanded(sdk):
    """#9063: the kind filter is case-INSENSITIVE, because the write path is.

    ``create_point('Statement', ...)`` is ACCEPTED (stored verbatim, only a
    warning) and EP reads no kind at all, so the node is a consumed claim; an
    exact-match filter left it ungated while a gated run stamped it (measured:
    ``confidence 0.508856678676027``) and the audit listed it as uncalibrated —
    gate and audit disagreeing, in the fail-open direction. Non-evidence kinds
    (``assessment``) stay out of the universe either way.
    """
    p = sdk.create_point("Statement", "Mis-cased kind", status="live")
    # …and EVERY whitespace padding, not just a space: ``trim()`` strips space
    # only on this engine, so a tab/newline/CR/VT/FF/NBSP-padded kind escaped an
    # exact-membership check while EP — which reads no kind at all — consumed
    # and stamped the node (measured; ``origin/main`` compared the raw kind, so
    # it escaped every spelling there).
    padded = [sdk.create_point(k, f"Padded kind {k!r}", status="live")
              for k in ("\tstatement\t", "\nstatement", "\rstatement\r",
                        "\x0bstatement", "\x0cstatement", "\u00a0statement")]
    want = sorted([p["id"]] + [n["id"] for n in padded], key=str)

    assert sdk._uncalibrated_ep_participants(None) == want
    row = {r["id"]: r for r in sdk.calibrate_summary()}[p["id"]]
    assert row["calibrated"] is False
    assert "set_point_baseline" in row["suggestion"]

    a = sdk.create_point("ASSESSMENT", "Mis-cased assessment", status="live")
    assert a["id"] not in sdk._uncalibrated_ep_participants(None)

    for n in [*padded, p]:
        sdk.set_point_baseline(n["id"], 3.0, 1.0)
    assert sdk._uncalibrated_ep_participants(None) == []


def test_duplicate_point_ids_count_once(sdk):
    """#9063: the #7822 duplicate-id fan-out is ONE node to calibrate.

    ``create_point(..., id=X)`` twice is a tested, graph-reachable shape; the
    gate's message is the caller's only signal of how many nodes need a prior,
    so it must not double-count one id.
    """
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:'dup-9063', pointKind:'statement', "
        "is_operator:false, status:'live'})")
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:'dup-9063', pointKind:'statement', "
        "is_operator:false, status:'live'})")

    assert sdk._uncalibrated_ep_participants(None) == ["dup-9063"]


def test_id_less_point_does_not_break_the_whole_graph_query(sdk):
    """#9063: an id-less (or numeric-id) Point is not reported, and cannot hide.

    An id-less node is never STAMPED — `_affected_claims` now REFUSES a run that
    would consume one (`UnaddressableClaimError`, #9275), which is why it can no
    longer die in the ordering (``sorted(affected)``) — and no
    caller can name it to ``set_point_baseline`` — so it must never make the
    gate raise a TypeError instead of its CalibrationError (the mixed-type
    ``sorted()`` half), and it must not eat the ``limit=1`` existence probe's
    ONE row (the ordering half: the id-less node is created FIRST, so a
    post-filter-only exclusion would answer "nothing to gate" and the anchored
    run below would stamp the target).
    A numeric id is the same hand-written-graph class; ``key=str`` keeps the
    message from raising on it.
    """
    sdk._get_proj().g.query(
        "CREATE (n:Point {pointKind:'statement', is_operator:false, "
        "status:'live', content:'no id'})")
    anchor = sdk.create_point("statement", "Anchor", credibility="gold",
                              status="live")
    target = sdk.create_point("statement", "Target", status="live")
    sdk.create_operator("IMPL", anchor["id"], [target["id"]])

    with pytest.raises(CalibrationError, match="uncalibrated") as exc:
        sdk.compute_confidence(anchors=[anchor["id"]],
                               require_calibration=True)
    assert target["id"] in str(exc.value)

    # A numeric id is the same hand-written-graph class: ``key=str`` keeps the
    # message from raising on a mixed-type list.
    numeric = sdk.create_point("statement", "Numeric id", status="live")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.id = 42", params={"id": numeric["id"]})

    full = sdk._uncalibrated_ep_participants(None)
    assert full == sorted([target["id"], 42], key=str)
    # The guidance surface makes the same exclusion: ``set_point_baseline(None)``
    # is not a remedy a caller can run.
    rows = {row["id"]: row for row in sdk.calibrate_summary()}
    assert None not in rows
    # …and its remedy for the numeric id must be the numeric id, not its repr as
    # a quoted string: ``set_point_baseline('42', …)`` names no node, takes the
    # #9262 phantom-success path, and leaves the gate refusing forever — the
    # same "advice that cannot resolve" loop this PR closed for source-linked
    # points.
    assert "set_point_baseline(42," in rows[42]["suggestion"]
    sdk.set_point_baseline(42, 3.0, 1.0)
    assert 42 not in sdk._uncalibrated_ep_participants(None)
    # The existence probe's invariant: it may answer with ANY one of the
    # offenders, but it must never answer "nothing" while something is there
    # (that is the fail-open the WHERE-clause exclusion closes).
    probe = sdk._uncalibrated_ep_participants(None, limit=1)
    assert probe and set(probe) <= set(full)


def test_set_point_baseline_refuses_a_non_epistemic_node(sdk):
    """#9063: the refusal covers EVERY existing node, not just the four labels.

    A ``:Source`` is addressed by its url (``id`` = url) and can carry no
    baseline; before the fallback probe it fell through to the historical
    "phantom prior + success dict" — the same false PASS, one label over.
    """
    sdk.create_source("https://example.org/9063", "web", tier="T1")

    with pytest.raises(ValueError, match=r":Source"):
        sdk.set_point_baseline("https://example.org/9063", 4.0, 2.0)
    assert "https://example.org/9063" not in sdk._evidence


def test_guidance_for_a_source_linked_legacy_point_actually_works(sdk):
    """#9063: following the audit's advice must not loop back to the refusal.

    The widened audit now LISTS a legacy property-ABSENT Point, and for a
    source-linked one it says "Inherited T1 from Source … — run
    compute_confidence() to apply". ``_apply_source_inheritance`` still
    filtered with the bare ``n.is_operator = false``, so that advice ran EP,
    skipped the point, and the gate refused again — forever. Same canonical
    non-operator predicate one layer down, and the advice resolves.
    """
    src = "https://example.org/9063-source"
    sdk.create_source(src, "web", tier="T1")
    p = _make_legacy_point(sdk, "Legacy sourced claim", status="live",
                           extractedFrom=src)
    row = {r["id"]: r for r in sdk.calibrate_summary()}[p["id"]]
    assert row["calibrated"] is False
    assert "compute_confidence" in row["suggestion"]

    # The advice, taken verbatim: the gate must now let the run through and the
    # inheritance must land a baseline on the legacy point.
    sdk.create_point("statement", "Calibrated companion", credibility="gold",
                     status="live")
    sdk.compute_confidence(require_calibration=True)
    assert sdk.get_point(p["id"])["baseline_set"] is True
    assert sdk._uncalibrated_ep_participants(None) == []


def test_guidance_does_not_offer_an_advice_inheritance_will_refuse(sdk):
    """#9063: a flag with no stored prior is FROZEN, so inheritance cannot help.

    ``_apply_source_inheritance`` freezes any row whose ``baseline_set`` is
    truthy (the #844/#2199 rule protecting an authored baseline), while this
    PR's widened demand now names exactly the rows whose flag is set but whose
    prior is missing. The audit used to answer those with "Inherited T1 from
    Source … — run compute_confidence() to apply", which then refused, re-printed
    the SAME advice, and looped forever — the failure this PR closed for the
    source-linked legacy point, reintroduced by the widened demand (measured).
    The advice is now the writer that actually clears it.
    """
    src = "https://example.org/9063-frozen"
    sdk.create_source(src, "web", tier="T1")
    p = _make_legacy_point(sdk, "Frozen flag, no prior", status="live",
                           extractedFrom=src)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.baseline_set = true, n.ep_alpha = null, "
        "n.ep_beta = null", params={"id": p["id"]})

    assert sdk._uncalibrated_ep_participants(None) == [p["id"]]
    row = {r["id"]: r for r in sdk.calibrate_summary()}[p["id"]]
    assert row["calibrated"] is False
    assert "set_point_baseline" in row["suggestion"]
    assert "compute_confidence" not in row["suggestion"]

    # Take the advice: the gate clears, and the frozen row is NOT recomputed
    # (`baseline_set`/`ep_alpha` were already set by the writer).
    sdk.set_point_baseline(p["id"], 3.0, 1.0)
    assert sdk._uncalibrated_ep_participants(None) == []
    got = sdk.get_point(p["id"])
    assert got["ep_alpha"] == 3.0 and got["ep_beta"] == 1.0


def test_guidance_reports_the_blocking_row_of_a_duplicate_id(sdk):
    """#9063: the gate names an ID, so the audit's row for it must show the block.

    A duplicate-id fan-out (#7822) can hold one calibrated node and one not.
    The audit keeps ONE row per id, and it used to keep whichever the server
    returned first — so the page the gate's error points at could report
    ``calibrated: True`` with no suggestion for an id the gate had just
    refused (measured, row-order dependent).
    """
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:'fanout-9063', pointKind:'statement', "
        "status:'live', baseline_set:true, ep_alpha:3.0, ep_beta:1.0})")
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:'fanout-9063', pointKind:'statement', status:'live'})")

    assert sdk._uncalibrated_ep_participants(None) == ["fanout-9063"]
    rows = [r for r in sdk.calibrate_summary() if r["id"] == "fanout-9063"]
    assert len(rows) == 1
    assert rows[0]["calibrated"] is False
    assert "set_point_baseline" in rows[0]["suggestion"]


def test_guidance_prefers_the_advice_that_can_actually_clear_a_frozen_id(sdk):
    """#9063: the SOURCE advice must not outrank the writer for a frozen id.

    A fan-out can hold one sibling whose ``baseline_set`` is set with no stored
    prior — frozen against inheritance by the #844/#2199 rule — beside an
    ordinary source-linked sibling. All rows are uncalibrated, so the audit's
    "prefer the Source advice" tie-break promoted "run compute_confidence() to
    apply", which the freeze then refuses: the gate keeps naming the id and the
    advice can never clear it (measured). ``set_point_baseline`` binds EVERY
    node of the fan-out, so it must win.
    """
    src = "https://example.org/9063-frozen-fanout"
    sdk.create_source(src, "web", tier="T1")
    sdk._get_proj().g.query(
        "MATCH (s:Source {id:$src}) "
        "CREATE (n:Point {id:'frozen-fan-9063', pointKind:'statement', "
        "       status:'live', baseline_set:true, "
        "       baseline_source:'set-by-author'})-[:extractedFrom]->(s)",
        params={"src": src})
    sdk._get_proj().g.query(
        "MATCH (s:Source {id:$src}) "
        "CREATE (n:Point {id:'frozen-fan-9063', pointKind:'statement', "
        "       status:'live'})-[:extractedFrom]->(s)",
        params={"src": src})

    assert sdk._uncalibrated_ep_participants(None) == ["frozen-fan-9063"]
    row = {r["id"]: r for r in sdk.calibrate_summary()}["frozen-fan-9063"]
    assert "set_point_baseline" in row["suggestion"]

    # Take the advice — it resolves for the whole fan-out.
    sdk.set_point_baseline("frozen-fan-9063", 3.0, 1.0)
    assert sdk._uncalibrated_ep_participants(None) == []


def test_an_uncalibratable_id_is_excluded_from_both_arms(sdk):
    """#9063: an id the engine cannot resolve by equality is not a demand.

    On this engine a LABELLED id equality misses a blank or whitespace-padded
    id (the unlabelled scan finds it), so ``set_point_baseline`` cannot write
    one and EP's own id-keyed selection misses it too. Demanding a prior for it
    is therefore unsatisfiable — and worse, it made the two arms of the gate
    DISAGREE in the fail-open direction: the whole-graph query (a predicate
    scan) found the node while the scoped, index-anchored query (``n.id IN
    $ids``) dropped it, so a scoped surface ran where the whole-graph one
    refused (measured on both trees once the universe was widened). Excluded in
    both arms, and from the audit — whose ``set_point_baseline`` advice could
    never land on such an id.
    """
    # The unresolvable class is EXACTLY the ASCII whitespace characters —
    # measured in both directions, and ``trim()`` is not usable to find it
    # (this engine's ``trim('\tx\t')`` returns the string unchanged, and the
    # first form of this check therefore left tab/VT/FF-padded ids in the
    # universe while the scoped arm dropped them: the same arm disagreement,
    # one character over).
    ids = [" padded-9063 ", "", "\ttabbed-9063\t", "\nnl-9063\n",
           "\x0bvt-9063\x0b", "\x0cff-9063\x0c", "\rcr-9063\r"]
    for ident in ids:
        sdk._get_proj().g.query(
            "CREATE (n:Point {id:$i, pointKind:'statement', status:'live'})",
            params={"i": ident})
    plain = sdk.create_point("statement", "Plain neighbour", status="live")
    # These are RESOLVABLE, so they must stay in the universe — measured:
    # ``create_point`` ACCEPTS an NBSP-padded id, the labelled equality finds
    # it, and ``set_point_baseline`` satisfies it. Excluding it made the gate
    # pass while EP stamped the node (a fail-open origin/main did not have).
    nbsp = sdk.create_point("statement", "NBSP padded", status="live",
                            id="\u00a0nbsp-9063\u00a0")
    inner = sdk.create_point("statement", "Inner space", status="live",
                             id="inner space 9063")
    # A hand-written ARRAY id is neither resolvable nor hashable; it must not
    # turn the gate into a Python TypeError.
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:['a','b'], pointKind:'statement', status:'live'})")

    found = sdk._uncalibrated_ep_participants(None)
    assert found == sorted([plain["id"], inner["id"], nbsp["id"]], key=str)
    assert sdk._uncalibrated_ep_participants(
        set(ids) | {plain["id"], inner["id"], nbsp["id"]}) == found
    audit_ids = {r["id"] for r in sdk.calibrate_summary()}
    assert not (set(ids) & audit_ids)
    assert inner["id"] in audit_ids and nbsp["id"] in audit_ids

    # …and the NBSP id's remedy actually lands.
    sdk.set_point_baseline(nbsp["id"], 3.0, 1.0)
    assert sdk._uncalibrated_ep_participants(None) == sorted(
        [plain["id"], inner["id"]], key=str)
    with pytest.raises(CalibrationError):
        sdk.compute_confidence(require_calibration=True)


def test_refusal_never_claims_a_point_is_not_a_point(sdk):
    """#9063: the refusal names the label it FOUND, so it cannot contradict itself.

    On a hand-written id the write can miss for an id-RESOLUTION reason (the
    engine's labelled equality misses an empty / whitespace-only id while the
    unlabelled probe hits it), which made the new guard raise "'' is a :Point,
    not a :Point" — a false diagnosis no caller can act on, on a node whose
    label is exactly the one the writer targets. That path keeps the historical
    no-op of a write that bound nothing (#9262's family); the guard fires only
    on a label that genuinely cannot carry a :Point baseline.
    """
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:'', pointKind:'statement', status:'live'})")

    assert sdk.set_point_baseline("", 2.0, 1.0)["claim_id"] == ""
    with pytest.raises(ValueError, match=r":Source, not a :Point"):
        # Control: the guard still fires on a real label mismatch.
        sdk.create_source("https://example.org/9063-refuse", "web", tier="T1")
        sdk.set_point_baseline("https://example.org/9063-refuse", 2.0, 1.0)





def test_out_of_contract_kind_shapes_do_not_abort_the_gate(sdk):
    """#9063: normalising the kind must not turn the gate into a type error.

    ``create_point`` accepts a non-string kind and stores it VERBATIM, and a
    bare ``toLower`` on it aborts the whole query with an engine type mismatch —
    an opaque ``ResponseError`` on a graph ``origin/main`` ran on fine, and one
    the audit's own advice cannot resolve. ``toLower(typeof(…)) = 'string'``
    keeps such a kind out of the universe (it is not an evidence kind), while
    whitespace-padded and mis-cased STRING kinds stay IN it: they are consumed
    claims like any other.
    """
    numeric = sdk.create_point(42, "Numeric kind", status="live")
    padded = sdk.create_point(" statement", "Padded kind", status="live")
    # …and an ARRAY kind is writable through the same passthrough. `toString`
    # (and therefore a bare normalisation) RAISES on a list in this engine, so
    # without the type guard this single hand-written row turned every gated
    # surface into an opaque ResponseError — a graph origin/main ran on fine.
    arr = sdk.create_point("statement", "Array kind", status="live")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.pointKind = ['statement']",
        params={"id": arr["id"]})
    cal = sdk.create_point("statement", "Calibrated companion", status="live",
                           credibility="gold")
    sdk.set_point_baseline(cal["id"], 3.0, 1.0)

    found = sdk._uncalibrated_ep_participants(None)
    assert padded["id"] in found
    assert numeric["id"] not in found
    assert arr["id"] not in found

    with pytest.raises(CalibrationError) as exc:
        sdk.compute_confidence(require_calibration=True)
    assert padded["id"] in str(exc.value)
    assert "ResponseError" not in type(exc.value).__name__

    sdk.set_point_baseline(padded["id"], 3.0, 1.0)
    assert sdk._uncalibrated_ep_participants(None) == []

    # And the run itself still works on that graph (the assertion above would
    # pass even if the query raised inside a caller that swallows it).
    sdk.compute_confidence(require_calibration=True)


# ── #9275 — an unaddressable claim refuses the RUN, not the ordering ────────

def test_id_less_claim_refuses_the_run_before_it_stamps(sdk):
    """#9275: a claim that names no node refuses the pass, BEFORE it stamps.

    MEASURED on ``origin/main`` at ``02e18e752``: the id-less ``:Point`` rode
    ``_affected_claims`` into ``dream.py``'s ``sorted(affected)`` and the pass
    died with ``TypeError: '<' not supported between instances of 'str' and
    'NoneType'`` — AFTER it had flushed the posteriors of both NAMED claims
    (``3.24647986469106``/``1.10312233616541`` and ``3.2020772952``/``1.04032``),
    leaving the run unreturnable and half-written. The same graph now refuses
    with a diagnosis, and asserts the invariant that made the old behaviour
    unacceptable: nothing is stamped.

    The traceback used to name a sort, which is why it could not be diagnosed
    from the SDK surface: an id-less node cannot be named, so it cannot be
    repaired by ``set_point_baseline`` — the message has to carry the repair
    query instead of an id.
    """
    anchor = sdk.create_point("statement", "Anchor", status="live")
    claim = sdk.create_point("statement", "Claim", status="live")
    sdk.set_point_baseline(anchor["id"], 3.0, 1.0)
    sdk.set_point_baseline(claim["id"], 3.0, 1.0)
    op = sdk.create_operator("IMPL", anchor["id"], [claim["id"]])

    proj = sdk._get_proj()
    proj.g.query("CREATE (n:Point {pointKind:'statement', status:'live', "
                 "content:'no id'})")
    proj.g.query(
        "MATCH (o:Point {id:$o}), (c:Point) WHERE c.id IS NULL "
        "CREATE (o)-[:IMPL {id:'e-9275'}]->(c)",
        params={"o": op["id"]})

    with pytest.raises(UnaddressableClaimError) as exc:
        sdk.compute_confidence()
    message = str(exc.value)
    assert "is NULL" in message
    # Actionable, not merely loud: the repair is a graph write because the node
    # has no id to pass to set_point_baseline, and it must mint ONE id per node
    # (a single literal would create the duplicate-id shape #7822).
    assert "SET n.id" in message
    assert "ONE NEW id" in message
    assert "DETACH DELETE" in message
    assert "#9275" in message

    # THE invariant: the refusal comes before any stamp. A partial stamp is what
    # made the old TypeError unrecoverable (a retry stamps the same claims
    # twice), so it is asserted on the SAME namable claims the old run stamped.
    stamped = dict(proj.g.query(
        "MATCH (n:Point) WHERE n.id IS NOT NULL "
        "RETURN n.id, n.posterior_alpha").result_set)
    assert stamped[anchor["id"]] is None
    assert stamped[claim["id"]] is None


def test_an_id_that_is_not_a_string_is_ordered_not_crashed(sdk):
    """#9275 sibling: a numeric id is ADDRESSABLE — only the ordering choked.

    ``create_point(..., id=X)`` mints string ids, so a numeric one is the same
    hand-written-graph class as the id-less node (and ``set_point_baseline(42,
    …)`` — the remedy ``calibrate_summary`` already prints for it — proves it is
    addressable). MEASURED on ``origin/main`` at ``02e18e752``: the pass ran and
    then died in ``sorted(affected)`` with ``TypeError: '<' not supported
    between instances of 'str' and 'int'``, again AFTER stamping. The sort is
    now total (``key=str``, the same guard the calibration gate's own sorted
    message uses) and the claim is computed like any other.
    """
    anchor = sdk.create_point("statement", "Anchor", status="live")
    claim = sdk.create_point("statement", "Claim", status="live")
    sdk.set_point_baseline(anchor["id"], 3.0, 1.0)
    sdk.set_point_baseline(claim["id"], 3.0, 1.0)
    sdk.create_operator("IMPL", anchor["id"], [claim["id"]])

    proj = sdk._get_proj()
    proj.g.query("MATCH (n:Point {id:$id}) SET n.id = 42",
                 params={"id": claim["id"]})

    out = sdk._get_dreamer().dream([anchor["id"], 42], warm_start=False)
    assert 42 in out["affected_claims"]
    # …and on the DEFAULT dream path, which is the one that builds the warm-start
    # snapshot key `tuple(sorted(input_ids))` — the second site this fix makes
    # total. Pinned with warm_start=True so deleting that edit reds a test
    # instead of silently re-introducing the crash on the default path.
    out_warm = sdk._get_dreamer().dream([anchor["id"], 42], warm_start=True)
    assert 42 in out_warm["affected_claims"]
    posterior = proj.g.query(
        "MATCH (n:Point {id:42}) RETURN n.posterior_alpha").result_set
    assert posterior[0][0] is not None, (
        "the numeric-id claim was ordered but never computed")


def test_id_less_factor_input_refuses_even_when_the_operator_is_not_seeded(sdk):
    """#9275 (review round 2): the FACTOR-INPUT half the affected guard cannot see.

    The hop admissions NULL-drop an id-less neighbour (``m.id <> n.id`` is NULL
    under three-valued logic), so the ``affected`` guard fires ONLY when the
    operator itself is seeded. When the operator is not seeded — a legacy
    ``op_type``-only operator (the selector matches ``is_operator = true`` only),
    or one outside the selector's hops — the pass still consumes the node:
    ``_affected_factors`` Batch 2 collects an operator's inputs verbatim, and
    ``_update_factor`` received ``[seed, sibling, None]``, with ``_last_affected``
    holding the two NAMED ids only.

    MEASURED before this guard on the fixture below: ``compute_confidence()``
    returned ``converged: True`` with the named posteriors pulled off a phantom
    ``Beta(1, 1)`` input (``3.2090652206748``/``1.10986962042195`` against a
    ``3.40474195219355``/``1.08063574424178`` control) — the silent mis-key that
    rejecting the issue's option 3 was meant to prevent.
    """
    proj = sdk._get_proj()
    seed = sdk.create_point("statement", "Seed", status="live")
    sibling = sdk.create_point("statement", "Sibling", status="live")
    for p in (seed, sibling):
        sdk.set_point_baseline(p["id"], 3.0, 1.0)
    # A legacy operator: op_type only, is_operator NOT true — _bfs_select_operators
    # matches `is_operator = true`, so this operator is never in the seed set.
    proj.g.query("CREATE (o:Point {id:'op-9275', pointKind:'operator', "
                 "op_type:'IMPL', status:'live', is_operator:false})")
    proj.g.query("MATCH (o:Point {id:'op-9275'}), (a:Point {id:$a}) "
                 "CREATE (o)-[:IMPL {idx:0}]->(a)", params={"a": seed["id"]})
    proj.g.query("MATCH (o:Point {id:'op-9275'}), (b:Point {id:$b}) "
                 "CREATE (o)-[:IMPL {idx:1}]->(b)", params={"b": sibling["id"]})
    proj.g.query("CREATE (n:Point {pointKind:'statement', status:'live'})")
    proj.g.query("MATCH (o:Point {id:'op-9275'}), (n:Point) WHERE n.id IS NULL "
                 "CREATE (o)-[:IMPL {idx:2}]->(n)")

    with pytest.raises(UnaddressableClaimError) as exc:
        sdk.compute_confidence(require_calibration=False)
    assert "factor's inputs" in str(exc.value)

    # Nothing was computed: both named posteriors are still null.
    stamped = dict(proj.g.query(
        "MATCH (n:Point) WHERE n.id IS NOT NULL "
        "RETURN n.id, n.posterior_alpha").result_set)
    assert stamped[seed["id"]] is None
    assert stamped[sibling["id"]] is None


def test_numeric_id_claims_survive_the_trivial_stamp_and_the_window_path(sdk):
    """#9275: the other two `key=str` sites, each reachable with a mixed-type set.

    ``dream``'s seed-empty branch sorts the trivial-stamp result, and
    ``dream_window`` sorts the cross-batch affected set. Both are fed engine ids
    verbatim, so a hand-written numeric id reaches them; without ``key=str`` each
    raises ``TypeError: '<' not supported between instances of 'str' and 'int'``.
    Neither was pinned by the numeric-id test above (that one drives ``dream``'s
    EP branch only), so this test is what keeps the two edits load-bearing.
    """
    proj = sdk._get_proj()
    # Trivial-stamp path: two anchors, NEITHER wired to an operator or a direct
    # edge, so `seed_ids` is empty and `_trivial_stamp` returns both ids — a
    # mixed-type set for `sorted(stamped, key=str)`.
    named = sdk.create_point("statement", "Loose claim", status="live")
    numeric = sdk.create_point("statement", "Numeric loose claim", status="live")
    proj.g.query("MATCH (n:Point {id:$id}) SET n.id = 77",
                 params={"id": numeric["id"]})
    out = sdk._get_dreamer().dream([named["id"], 77])
    assert out["iterations"] == 0
    assert set(out["affected_claims"]) == {77, named["id"]}

    # Window path: both claims reachable through an operator, so the stale-first
    # pass accumulates a mixed-type affected set across its batches.
    other = sdk.create_point("statement", "Windowed", status="live")
    for p in (named, other):
        sdk.set_point_baseline(p["id"], 3.0, 1.0)
    sdk.create_operator("IMPL", named["id"], [other["id"]])
    window = sdk._get_dreamer().dream_window(budget=50)
    assert window["mode"] == "stale-first"
    assert 77 in window["affected_claims"]


def test_the_id_less_refusal_commits_no_evidence_pre_write(sdk):
    """#9275 review (P2): the refusal is before the run's FIRST write.

    ``run()``'s evidence pre-write SETs ``ep_alpha``/``ep_beta`` and (for a
    ``baseline_set``-unset claim) NULLs the posteriors, plus journals the clear.
    With the affected-claim collection AFTER it, a refusal left those committed —
    so ``evidence=`` (a public parameter of ``compute_confidence`` and of
    ``TortoiseEP.run``) turned "refused, nothing written" into a real
    half-write, and the diagnostic said otherwise. The collection now runs
    first: the refusal is the run's first event, and the graph is untouched.

    The witness is deliberately OUTSIDE the affected set — the pre-write
    applies to any id the caller supplies ("direct graph write so evidence is
    visible even when there are no operators / affected claims"), which is what
    makes the pre-write's commitment observable at all.
    """
    anchor = sdk.create_point("statement", "Anchor", status="live")
    claim = sdk.create_point("statement", "Claim", status="live")
    witness = sdk.create_point("statement", "Witness", status="live")
    sdk.set_point_baseline(anchor["id"], 3.0, 1.0)
    sdk.set_point_baseline(claim["id"], 3.0, 1.0)
    op = sdk.create_operator("IMPL", anchor["id"], [claim["id"]])

    proj = sdk._get_proj()
    proj.g.query("CREATE (n:Point {pointKind:'statement', status:'live', "
                 "content:'no id'})")
    proj.g.query(
        "MATCH (o:Point {id:$o}), (c:Point) WHERE c.id IS NULL "
        "CREATE (o)-[:IMPL {id:'e-9275-prewrite'}]->(c)",
        params={"o": op["id"]})

    read = ("MATCH (n:Point {id:$id}) "
            "RETURN n.ep_alpha, n.ep_beta, n.posterior_alpha, n.posterior_beta")
    before = proj.g.query(read, params={"id": witness["id"]}).result_set[0]
    # The witness is baseline-free, so the pre-write WOULD overwrite it — which
    # is what makes this assertion test the ordering rather than the CASE.
    assert before[0] != 7.0

    with pytest.raises(UnaddressableClaimError):
        sdk.compute_confidence(factors=[op["id"]],
                               evidence={witness["id"]: (7.0, 2.0)},
                               require_calibration=False)

    after = proj.g.query(read, params={"id": witness["id"]}).result_set[0]
    assert after == before, (
        "the refusal committed the evidence pre-write — 'nothing is "
        f"half-written' would be false (before={before}, after={after})")
