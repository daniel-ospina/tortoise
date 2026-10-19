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

    This drives the WHOLE-GRAPH scope (no-arg compute_confidence), where
    ``calibrate_summary()`` DOES return the dead point — so the
    ``is_terminal_status`` clause is what excuses it (stub that clause to
    always-False and this test fails; a scoped run would not exercise it,
    because the run's own consumed set already strips terminal points).
    """
    sdk.create_point("statement", "Live calibrated", credibility="gold",
                     status="live")
    sdk.create_point("statement", "Dead uncalibrated", status="superseded")

    result = sdk.compute_confidence(require_calibration=True)
    assert isinstance(result, dict)


def test_outdated_flag_point_does_not_block(sdk):
    """#7739: the legacy ``outdated=true`` flag is terminal too.

    ``invalidate_point`` sets the flag WITHOUT touching status, so status-only
    terminal detection would keep a dead point as a whole-graph blocker. This
    pins both the ``outdated`` column ``calibrate_summary`` now returns and
    the shared ``is_terminal_status`` mirror honouring its second argument.
    """
    sdk.create_point("statement", "Live calibrated", credibility="gold",
                     status="live")
    dead = sdk.create_point("statement", "Flagged uncalibrated",
                            status="live")
    corrector = sdk.create_point("statement", "Corrector", credibility="gold",
                                 status="live")
    sdk.invalidate_point(dead["id"], corrector["id"])
    assert sdk.get_point(dead["id"])["outdated"] is True

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
