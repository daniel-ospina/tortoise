"""Issue #13450 — a node ended on the POSITION axis must not vote as an operator INPUT.

The currency rule has TWO sources since #13034 (design §6, and the shared
predicate ``live._terminal_excluded`` / ``is_terminal_status``): the lifecycle
WORD (``status`` in the terminal vocabulary, or the legacy ``outdated`` flag)
and the POSITION axis (``isClosed``, or a stage in the ``terminal`` pipeline
slot). Either one alone is an ending.

EP's factor assembly reads both axes everywhere it decides currency in CYPHER
(``_live_only`` covers the affected-set expansion and the direct-edge path),
but ONE arm decides an operator INPUT's participation in PYTHON, from the two
columns its Batch-2 query projected:

    is_terminal = status in TERMINAL_EXCLUDED_STATUSES or bool(outdated)

so a node ended by ``isClosed`` or a terminal stage still voted, with whatever
posterior it held. Measured at ``69dda95ab9`` (base), private lane, throwaway
graph, ``IMPL(source -> live Point)``, source prior ``(8, 2)``:

| source shape | target posterior | verdict at base |
|---|---|---|
| live Point, prior ``(8,2)`` | ``1.63906 / 1.03267`` | control |
| ``status='superseded'`` | untouched | stripped — the WORD axis worked |
| ``isClosed=true``, ``status='live'`` | ``1.63906 / 1.03267`` | CONTRIBUTED (defect) |
| ``pipelineSlot='terminal'`` | ``1.63906 / 1.03267`` | CONTRIBUTED (defect) |
| ``:Object`` ``isClosed=true``, posterior ``(9,1)`` | ``1.77685 / 0.95610`` | CONTRIBUTED (defect) |

Every row below is the inversion of that table: the three contributed rows must
stop voting at the SAME priors, the control must not move, and the diagnostics
must name the axis that stripped the input.

Lane: needs a live FalkorDB (``uv run python tools/test_lane.py run --
tests/test_13450_ep_position_axis_participation.py -v``). The
``ending_axes`` truth-table class is pure Python and needs no database.
"""
from __future__ import annotations

import ast
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tests import _live_utils
from tortoise import live
from tortoise.live import (
    TERMINAL_EXCLUDED_STATUSES,
    TERMINAL_STAGE_SLOT,
    ending_axes,
    is_position_ended,
    is_terminal_status,
)
from tortoise.sdk import TortoiseSDK

GRAPH = f"tortoise_test_13450_{uuid.uuid4().hex[:8]}"
DB_URI = _live_utils.docker_uri(GRAPH)
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
try:
    os.environ["TORTOISE_DB_URI"] = DB_URI
    _probe = TortoiseSDK()
    # ⛔ `ro_query`, not `.query`: `query` is a name on the retired public SDK
    # surface, so `sdk_privatise`'s ratchet counts every ordinary-word `.query`
    # receiver it cannot type as residue — and this file is NEW, so the two such
    # calls in it RAISED that count (`surface-guard` red at baseline 11913 ->
    # 11915). `ro_query` is the wrapper's own read verb (#3595 `=~` guard
    # included) and is not on that map, which is the gate's own "convert the
    # call" remedy; nothing about the probe's meaning changes.
    _probe._get_proj().g.ro_query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
    # ⛔ A rename or typo in a seam this probe touches must NOT be read as
    # "storage unavailable" — that is how a retired door hid for a whole wave
    # (#14114 class 2, #14125). Programming errors are re-raised; only genuine
    # transport/driver failures fall through to the unavailable arm.
except (AttributeError, NameError, TypeError, ImportError):
    raise
except Exception:
    pass
finally:
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

_needs_db = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")


@pytest.fixture(autouse=True)
def _isolated_db_uri():
    _old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = DB_URI
    yield
    if _old is not None:
        os.environ["TORTOISE_DB_URI"] = _old
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


def _fresh_sdk() -> TortoiseSDK:
    sdk = TortoiseSDK(db_path=None, namespace=None)
    sdk._db_uri = DB_URI
    sdk._proj = None  # force re-init on first use
    return sdk


def _q(sdk, cypher, params=None):
    # ⛔ `_query`, not `.query` — the same `surface-guard` ratchet as the probe
    # above, on the arm that has to WRITE (the fixtures set `isClosed` /
    # `pipelineSlot` directly). `_query` is the converted spelling the sweep
    # itself names, it still carries the #3595 `=~` guard, and this module only
    # ever addresses a per-run throwaway graph (`GRAPH = tortoise_test_13450_*`)
    # with statements targeted by `id`, so the bulk-wipe check it does not run
    # is not a guard these fixtures depend on.
    return sdk._get_proj().g._query(cypher, params=params or {}).result_set


def _belief(sdk, node_id):
    """``[confidence, posterior_alpha, posterior_beta]`` as floats."""
    row = _q(sdk, "MATCH (n) WHERE n.id = $id RETURN n.confidence, "
                  "coalesce(n.posterior_alpha, 0.0), "
                  "coalesce(n.posterior_beta, 0.0)", {"id": node_id})[0]
    return [float(x) if x is not None else None for x in row]


def _wire_and_run(sdk, source_id, *, direction="unidirectional"):
    """Wire ``IMPL(source -> fresh live target)`` and price it ONCE.

    Returns ``(target_id, belief, skipped_factors)``. A fresh target per call is
    what makes "untouched" readable: the posterior rows exist only because this
    run's factor priced something.
    """
    target = sdk._create_point(
        "statement", f"t-{uuid.uuid4().hex[:8]}", status="live")["id"]
    op_id = sdk._create_operator(
        "IMPL", source_id, [target], direction=direction)["id"]
    run = sdk._compute_confidence(
        factors=[op_id], require_calibration=False) or {}
    return target, _belief(sdk, target), run.get("skipped_factors") or []


def _assert_not_priced(target_belief, alpha_beta):
    """A factor that did NOT price leaves a SYMMETRIC posterior (§780 convention).

    The control's posterior is asymmetric — the two are the discriminating pair.
    """
    alpha, beta = target_belief[1], target_belief[2]
    assert alpha == beta, (
        f"an ended input still moved the target: posterior {target_belief} "
        f"vs the live control {alpha_beta}")


# ══════════════════════════════════════════════════════════════════════════
# 1. The ONE declaration of the rule, and its axis names (no database)
# ══════════════════════════════════════════════════════════════════════════


class TestTheAxesAreOneDeclaration:
    """``ending_axes`` names WHAT ended; the two predicates read it.

    The diagnostics need the axis, not just the boolean, so the boolean is
    derived from the axes rather than re-tested beside them: a third spelling of
    the rule is what #13450 is about.
    """

    def test_a_live_record_carries_no_axis(self):
        assert ending_axes("live") == ()
        assert ending_axes(None) == ()
        assert ending_axes("draft") == ()

    @pytest.mark.parametrize("status", sorted(TERMINAL_EXCLUDED_STATUSES))
    def test_the_lifecycle_word_is_named(self, status):
        assert ending_axes(status) == ("status",)

    def test_the_flag_and_the_position_markers_are_named(self):
        assert ending_axes("live", True) == ("outdated",)
        assert ending_axes("live", is_closed=True) == ("isClosed",)
        assert ending_axes("live", stage_slot=TERMINAL_STAGE_SLOT) == (
            "pipelineSlot",)

    def test_every_set_axis_is_named_at_once(self):
        # Not first-match-wins: the diagnostic must not hide an axis.
        assert ending_axes("superseded", True, is_closed=True,
                           stage_slot=TERMINAL_STAGE_SLOT) == (
            "status", "outdated", "isClosed", "pipelineSlot")

    def test_a_non_terminal_slot_is_not_an_axis(self):
        for slot in ("entry", "active", "suspended", "", None):
            assert ending_axes("live", stage_slot=slot) == ()
        # ⛔ AND NEITHER IS A PADDED ONE (#13450 review, P3). This used to be
        # the ONE value where the Python arm and the Cypher twin disagreed: the
        # arm stripped the slot (`" terminal "` was an ending) while every
        # Cypher reader compared it exactly (current). The comparison is exact
        # on both sides now, so `" terminal "` is not an ending anywhere, and
        # the parity pin in `test_13034_…` enumerates it rather than leaving the
        # agreement to inspection.
        assert ending_axes("live", stage_slot=" terminal ") == ()

    def test_the_marker_is_read_by_truthiness_like_advance_stage(self):
        assert ending_axes("live", is_closed="yes") == ("isClosed",)
        assert ending_axes("live", is_closed=1) == ("isClosed",)
        assert ending_axes("live", is_closed=0) == ()
        assert ending_axes("live", is_closed=False) == ()

    def test_the_two_predicates_are_the_axes_and_cannot_disagree(self):
        """The whole cross product: bool(axes) == is_terminal_status, and the
        position sub-tuple == is_position_ended. Derived, not re-tested."""
        words = [None, "live", "draft", "superseded", "retracted", "bogus"]
        flags = [False, True]
        closeds = [None, False, True]
        slots = [None, "active", TERMINAL_STAGE_SLOT, " terminal "]
        for word in words:
            for flag in flags:
                for closed in closeds:
                    for slot in slots:
                        axes = ending_axes(word, flag, is_closed=closed,
                                           stage_slot=slot)
                        assert is_terminal_status(
                            word, flag, is_closed=closed, stage_slot=slot
                        ) is bool(axes)
                        position = tuple(
                            a for a in axes if a in ("isClosed", "pipelineSlot"))
                        assert is_position_ended(
                            is_closed=closed, stage_slot=slot
                        ) is bool(position)


# ══════════════════════════════════════════════════════════════════════════
# 2. The inverted table — an operator input ended on either axis stops voting
# ══════════════════════════════════════════════════════════════════════════


@_needs_db
class TestAnEndedInputStopsVoting:
    """The four measured rows, inverted; the live control must not move."""

    def test_the_live_control_still_contributes(self):
        """The baseline every other row is read against — unchanged by #13450.

        ``1.63906 / 1.03267`` at the base and after the fix: the fix narrows who
        participates, never what a participating input is worth.
        """
        sdk = _fresh_sdk()
        try:
            src = sdk._create_point(
                "statement", f"c1-{uuid.uuid4().hex[:8]}", status="live")["id"]
            sdk._set_point_baseline(src, 8.0, 2.0)
            _tgt, belief, skipped = _wire_and_run(sdk, src)
            assert skipped == [], skipped
            _alpha, alpha, beta = belief
            assert alpha != beta, (
                f"the live control did not contribute: {belief}")
            assert alpha == pytest.approx(1.63906, rel=1e-3), belief
            assert beta == pytest.approx(1.03267, rel=1e-3), belief
        finally:
            sdk.close()

    def test_the_status_word_still_strips_its_input(self):
        """The WORD axis worked at the base — the fix must not regress it."""
        sdk = _fresh_sdk()
        try:
            src = sdk._create_point(
                "statement", f"c2-{uuid.uuid4().hex[:8]}", status="live")["id"]
            sdk._set_point_baseline(src, 8.0, 2.0)
            _q(sdk, "MATCH (n:Point {id:$id}) SET n.status = 'superseded'",
               {"id": src})
            _tgt, belief, skipped = _wire_and_run(sdk, src)
            assert [s["reason"] for s in skipped] == ["source_stripped"], skipped
            assert belief[1] == belief[2], belief
        finally:
            sdk.close()

    def test_a_closed_source_stops_voting(self):
        """``isClosed`` with the word left ``live`` — contributed 1.63906 at base."""
        sdk = _fresh_sdk()
        try:
            src = sdk._create_point(
                "statement", f"c3-{uuid.uuid4().hex[:8]}", status="live")["id"]
            sdk._set_point_baseline(src, 8.0, 2.0)
            _q(sdk, "MATCH (n:Point {id:$id}) SET n.isClosed = true", {"id": src})
            _tgt, belief, skipped = _wire_and_run(sdk, src)
            assert [s["reason"] for s in skipped] == ["source_stripped"], skipped
            _assert_not_priced(belief, "1.63906 / 1.03267")
        finally:
            sdk.close()

    def test_a_terminal_stage_source_stops_voting(self):
        """``pipelineSlot='terminal'`` — contributed 1.63906 at base."""
        sdk = _fresh_sdk()
        try:
            src = sdk._create_point(
                "statement", f"c4-{uuid.uuid4().hex[:8]}", status="live")["id"]
            sdk._set_point_baseline(src, 8.0, 2.0)
            _q(sdk, "MATCH (n:Point {id:$id}) SET n.pipelineSlot = $slot",
               {"id": src, "slot": TERMINAL_STAGE_SLOT})
            _tgt, belief, skipped = _wire_and_run(sdk, src)
            assert [s["reason"] for s in skipped] == ["source_stripped"], skipped
            _assert_not_priced(belief, "1.63906 / 1.03267")
        finally:
            sdk.close()

    def test_a_closed_object_input_stops_voting(self):
        """A ``:Object`` ended only by ``isClosed``, holding a measured ``(9,1)``.

        The strongest stored posterior an entity can carry, and the row that
        contributed ``1.77685 / 0.95610`` at the base.
        """
        sdk = _fresh_sdk()
        try:
            obj = sdk._create_object(
                f"c5-{uuid.uuid4().hex[:8]}", "core:goal")["id"]
            _q(sdk, "MATCH (n:Object {id:$id}) SET n.posterior_alpha = 9.0, "
                    "n.posterior_beta = 1.0, n.confidence = 0.9, "
                    "n.isClosed = true", {"id": obj})
            _tgt, belief, skipped = _wire_and_run(sdk, obj)
            assert [s["reason"] for s in skipped] == ["source_stripped"], skipped
            _assert_not_priced(belief, "1.77685 / 0.95610")
        finally:
            sdk.close()

    def test_the_axis_is_read_under_the_include_draft_hatch_too(self):
        """``include_draft`` re-includes DRAFTS only (#780) — never an ended node.

        The escape hatch is the one path where a stripped input could come back,
        so the position axis must be read on BOTH modes (#2422's rule, extended
        to the axis it never reached). Asserted on factor ASSEMBLY, which is the
        decision under test: the hatch must not hand the closed draft back.
        """
        sdk = _fresh_sdk()
        try:
            src = sdk._create_point(
                "statement", f"c6-{uuid.uuid4().hex[:8]}", status="draft")["id"]
            _q(sdk, "MATCH (n:Point {id:$id}) SET n.isClosed = true", {"id": src})
            target = sdk._create_point(
                "statement", f"c6t-{uuid.uuid4().hex[:8]}", status="live")["id"]
            # promote_source=True keeps the OPERATOR live, so the only thing the
            # hatch could change is the draft-CLOSED source's participation.
            op_id = sdk._create_operator(
                "IMPL", src, [target], direction="unidirectional")["id"]
            skipped: list[dict] = []
            factors = sdk._get_ep()._affected_factors(
                {target}, include_draft=True, skipped=skipped)
            assert op_id not in {f[0] for f in factors}, (
                "include_draft resurrected a CLOSED input: "
                f"factors={[f[0] for f in factors]} skipped={skipped}")
        finally:
            sdk.close()


# ══════════════════════════════════════════════════════════════════════════
# 3. The diagnostics NAME the axis that stripped the input
# ══════════════════════════════════════════════════════════════════════════


@_needs_db
class TestTheDiagnosticNamesTheAxis:
    """A stripped input's record must say WHICH axis ended it.

    ``live`` beside a set ``isClosed`` is an ended node; the pre-#13450 display
    printed it as ``live`` and left the reader to re-derive the ending.
    """

    def test_the_source_stripped_record_names_the_axis(self):
        cases = [
            ("isClosed", "MATCH (n:Point {id:$id}) SET n.isClosed = true",
             "live(isClosed)"),
            ("pipelineSlot",
             "MATCH (n:Point {id:$id}) SET n.pipelineSlot = 'terminal'",
             "live(pipelineSlot)"),
            ("status", "MATCH (n:Point {id:$id}) SET n.status = 'superseded'",
             "superseded"),
            ("outdated", "MATCH (n:Point {id:$id}) SET n.outdated = true",
             "live(outdated)"),
        ]
        sdk = _fresh_sdk()
        try:
            for name, setter, expected in cases:
                src = sdk._create_point(
                    "statement", f"d-{name}-{uuid.uuid4().hex[:6]}",
                    status="live")["id"]
                sdk._set_point_baseline(src, 8.0, 2.0)
                _q(sdk, setter, {"id": src})
                _tgt, _b, skipped = _wire_and_run(sdk, src)
                assert [s["reason"] for s in skipped] == [
                    "source_stripped"], (name, skipped)
                # `inputs[0]` is the idx-0 SOURCE — the slot that stripped.
                labels = [i["status"] for i in skipped[0]["inputs"]]
                assert labels[0] == expected, (name, labels)
        finally:
            sdk.close()

    def test_the_degenerate_record_names_the_axis_on_every_input(self):
        """A bidirectional factor with one ended input is DEGENERATE, not
        source-stripped — the other diagnostic must name the axis too."""
        sdk = _fresh_sdk()
        try:
            src = sdk._create_point(
                "statement", f"e-src-{uuid.uuid4().hex[:8]}", status="live")["id"]
            sdk._set_point_baseline(src, 9.0, 1.0)
            target = sdk._create_point(
                "statement", f"e-tgt-{uuid.uuid4().hex[:8]}", status="live")["id"]
            _q(sdk, "MATCH (n:Point {id:$id}) SET n.isClosed = true",
               {"id": target})
            op_id = sdk._create_operator(
                "IMPL", src, [target], direction="bidirectional")["id"]
            run = sdk._compute_confidence(
                factors=[op_id], require_calibration=False) or {}
            skipped = run.get("skipped_factors") or []
            assert [s["reason"] for s in skipped] == ["degenerate"], skipped
            labels = {i["status"] for i in skipped[0]["inputs"]}
            assert "live(isClosed)" in labels, labels
        finally:
            sdk.close()


# ══════════════════════════════════════════════════════════════════════════
# 4. The two WRITE DOORS — their posture is STATED, and pinned
# ══════════════════════════════════════════════════════════════════════════


@_needs_db
class TestTheWriteDoorsStateTheirPosture:
    """Both doors KNOWINGLY accept a position-axis-ended endpoint (#13450).

    Stated, not accidental — and pinned here so a future widening of either door
    is a decision somebody makes rather than a drift:

    * ``create_operator`` has no terminal endpoint guard on EITHER axis (it
      accepts ``status='superseded'`` too). It does not need one to keep EP
      honest: an ended endpoint is stripped on the READ (``_affected_factors``
      Batch 2), and the diagnostic the read owes is ``skipped_factors`` — a
      record naming the ended input's axis, emitted only on a DROP, with a
      ``reason`` from the read's closed set (``source_stripped``,
      ``degenerate``, ``derived_over_asserted``). A factor that SURVIVES on its
      remaining inputs is priced without the ended one and reports nothing —
      the same silence the word axis has had since #2422. WHICH strips drop the
      factor is Batch 2's guards' call (including whether a directional source
      is provably the stripped slot, and whether the operator is selected at
      all), so the guards — not this sentence — are the authority
      (PR #14600 review, P3: this docstring previously claimed the report
      unconditionally).
    * ``create_direct_edge`` refuses on the lifecycle word (#2422) and does not
      refuse on the position axis. Widening it means widening
      ``_endpoint_is_terminal``, which is ingest Phase-1's *prediction* of that
      primitive — a separate, wider decision about which bundles abort.
    """

    @pytest.mark.parametrize("shape,statement", [
        ("status", "SET n.status = 'superseded'"),
        ("isClosed", "SET n.isClosed = true"),
        ("pipelineSlot", "SET n.pipelineSlot = 'terminal'"),
    ])
    def test_create_operator_accepts_every_ending(self, shape, statement):
        sdk = _fresh_sdk()
        try:
            src = sdk._create_point(
                "statement", f"w-{shape}-{uuid.uuid4().hex[:6]}",
                status="live")["id"]
            _q(sdk, f"MATCH (n:Point {{id:$id}}) {statement}", {"id": src})
            tgt = sdk._create_point(
                "statement", f"wt-{uuid.uuid4().hex[:6]}", status="live")["id"]
            # ACCEPTED on every axis — the stated posture, on both axes.
            sdk._create_operator("IMPL", src, [tgt], direction="unidirectional")
            _tgt, belief, _skipped = _wire_and_run(sdk, src)
            assert belief[1] == belief[2], (
                "the door accepted the edge but the READ must still strip it "
                f"({shape}): {belief}")
        finally:
            sdk.close()

    @pytest.mark.parametrize("shape,statement,refused", [
        ("status", "SET n.status = 'superseded'", True),
        ("isClosed", "SET n.isClosed = true", False),
        ("pipelineSlot", "SET n.pipelineSlot = 'terminal'", False),
    ])
    def test_create_direct_edge_posture_per_axis(self, shape, statement, refused):
        sdk = _fresh_sdk()
        try:
            src = sdk._create_point(
                "statement", f"x-{shape}-{uuid.uuid4().hex[:6]}",
                status="live")["id"]
            _q(sdk, f"MATCH (n:Point {{id:$id}}) {statement}", {"id": src})
            tgt = sdk._create_point(
                "statement", f"xt-{uuid.uuid4().hex[:6]}", status="live")["id"]
            if refused:
                with pytest.raises(ValueError, match="is terminal"):
                    sdk._create_direct_edge("IMPL", src, tgt)
            else:
                # ACCEPTED — and the WRITE is what this arm asserts.
                # ⛔ It does NOT assert the read side, and it used to claim to:
                # `_compute_confidence(factors=[...])` prices OPERATOR ids
                # (`operator_ids = [f if isinstance(f, str) else f[0] …]`,
                # `sdk.py:41760-41762`), so handing it the two POINT ids this
                # test creates priced NO factor and left
                # `belief[1] == belief[2]` true whatever the edge did — a
                # consequent that could not fail (#14600 review N1). Removed
                # rather than kept as reassurance. The read-side strip IS priced
                # through a real operator by
                # `test_create_operator_accepts_every_ending` above, which is why
                # nothing is lost here.
                sdk._create_direct_edge("IMPL", src, tgt)
                edges = _q(
                    sdk,
                    "MATCH (a:Point {id:$s})-[r:IMPL]->(b:Point {id:$t}) "
                    "RETURN count(r)",
                    {"s": src, "t": tgt})
                assert edges[0][0] == 1, (
                    f"the {shape} door ACCEPTED the edge and did not write it: "
                    f"{edges}")
        finally:
            sdk.close()


# ══════════════════════════════════════════════════════════════════════════
# 5. The census — ep.py no longer answers currency itself
# ══════════════════════════════════════════════════════════════════════════


#: The terminal WORD vocabularies a currency read used to be spelled from. Each
#: is asserted to still exist in ``live`` before the pin below trusts the set, so
#: a rename in the leaf reds here instead of quietly emptying the population.
_TERMINAL_VOCABULARY_NAMES = (
    "TERMINAL_EXCLUDED_STATUSES",
    "TERMINAL_STATUS_VALUES",
    "LEGACY_NON_CURRENT_STATUS_VALUES",
)


def _shared_answer_calls(source: str) -> list[str]:
    """Every real CALL to the one declaration in ``source`` — comments excluded.

    ⛔ STRUCTURAL rather than a substring test, and that is the whole point: a raw
    ``"ending_axes" in source`` is satisfied by a COMMENT (this repo's ``ep.py``
    comments name the function), so it stayed green over a mutant that deleted the
    call and restored the two-column test — MEASURED on PR #14600's review (P2):
    ``"ending_axes" in source`` was ``True`` on that mutant while the call was
    gone. Comments and docstrings cannot produce an ``ast.Call`` node.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if name == "ending_axes":
            found.append(ast.unparse(node))
    return found


def _terminal_vocabulary_refs(source: str) -> list[str]:
    """Every REAL reference to a terminal word vocabulary — comments excluded.

    This is what catches the pre-fix spelling an AST-unaware or text-only pin
    cannot see: ``status in set(TERMINAL_EXCLUDED_STATUSES) or bool(outdated)``
    both dodges the bare substrings AND contains only real ``ast.Name`` /
    ``ast.Attribute`` nodes, so a structural look finds it either way.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in _TERMINAL_VOCABULARY_NAMES:
            found.append(f"line {node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute) and \
                node.attr in _TERMINAL_VOCABULARY_NAMES:
            found.append(f"line {node.lineno}: {ast.unparse(node)}")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _TERMINAL_VOCABULARY_NAMES:
                    found.append(f"line {node.lineno}: import {alias.name}")
    return found


def test_ep_imports_the_shared_answer_and_spells_no_filter():
    """The defect was a hand-written two-column test. Pin its absence AND the
    presence of the shared answer, so deleting the call reds here (#13034's
    census convention, extended to this module).

    Both arms are STRUCTURAL (see the helpers), because the first cut of this
    test asserted ``"ending_axes" in source`` — which the module's own comments
    satisfy — and two bare forbidden substrings, which the ``set(...)``-wrapped
    mutant slips past. The AST pairs below are what make the reversion trip.
    """
    source = Path("tortoise/ep.py").read_text(encoding="utf-8")
    for vocabulary in _TERMINAL_VOCABULARY_NAMES:
        assert vocabulary in dir(live), (
            f"live no longer declares {vocabulary!r}, so the population this pin "
            "guards has silently emptied")
    assert _shared_answer_calls(source), (
        "ep.py no longer CALLS `ending_axes` — a mention in a comment or "
        "docstring is not an answer, and the participation test must read the "
        "ONE declaration of the currency rule")
    assert _terminal_vocabulary_refs(source) == [], (
        "ep.py names a terminal WORD vocabulary directly: "
        f"{_terminal_vocabulary_refs(source)} — the participation test answers "
        "from `live.ending_axes`, which reads BOTH axes; a word-only arm here is "
        "exactly the defect #13450 removed")
    assert "is_terminal_status" in dir(live) and "ending_axes" in dir(live)


class TestThePresencePinCannotBeSatisfiedByAComment:
    """Non-vacuity for the pin above, against the mutants the review MEASURED.

    ⛔ These arms exist because the pin they guard was itself the defect: a
    comment-satisfiable assertion reads as coverage while covering nothing, so
    the replacement must be shown to bite on the shapes that defeated it.
    """

    def test_a_comment_mentioning_the_answer_is_not_a_call(self):
        source = (
            "# ``ending_axes`` is the ONE declaration of the rule\n"
            "def f(status, outdated):\n"
            "    return status in TERMINAL_EXCLUDED_STATUSES or bool(outdated)\n")
        assert _shared_answer_calls(source) == [], (
            "a comment mentioning `ending_axes` satisfied the call pin")
        assert _terminal_vocabulary_refs(source), (
            "the membership spelling must be visible to the structural pin")

    def test_both_measured_mutant_spellings_are_caught(self):
        for mutant in (
            "    is_terminal = status in set(TERMINAL_EXCLUDED_STATUSES) or "
            "bool(outdated)\n",
            "    is_terminal = TERMINAL_EXCLUDED_STATUSES or bool(outdated)\n",
        ):
            source = ("def f(status, outdated):\n" + mutant
                      + "    return is_terminal\n")
            assert _shared_answer_calls(source) == [], mutant
            assert _terminal_vocabulary_refs(source), (
                "this spelling defeated the substring pins and the structural "
                f"pin did not see it: {mutant!r}")

    def test_a_live_qualified_reference_is_also_seen(self):
        source = ("from tortoise import live\n"
                  "def f(status):\n"
                  "    return status in live.TERMINAL_EXCLUDED_STATUSES\n")
        assert _terminal_vocabulary_refs(source), (
            "the `live.`-qualified spelling must be seen too — it is the other "
            "way a module reaches the vocabulary")
