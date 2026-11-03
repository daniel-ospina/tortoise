"""#2552 layer-2 WIRE — the deterministic planted-operator lane.

The write-path bench's planted-operator audit (issue #2514) measures whether
the extractor wires the RIGHT operator EDGE between the right claims.  Two
compounding causes made it read 0/4 (receipts ``w2b-phaseg-llm-2026-09-08-
2514corpus``):

1. STRUCTURAL — reified operator Points carried no ``eventId``, so the
   eventId-keyed memory layer never admitted them (``operator_counts`` was
   silently ``{}`` on every real run) and a committed operator node was not
   retrievable.  Closed by ``sdk.capture_session`` stamping the capture's
   operator topology with the ``sessionCaptured`` eventId (mirroring the
   point path) + the ``OperatorPromoted`` durable snapshot.
2. BEHAVIORAL — where operators WERE formed, the emitted KIND did not match
   the ontology mapping (the LLM-emission leg, product lane).

This module pins the STRUCTURAL leg deterministically (no LLM, no network):
it feeds the ontology-correct operator emission (the write path's chosen
mapping, reasoned per planted edge in
``docs/scoping/2026-09-07-2514-operator-corpus.md``, derived here from the
sealed gold) through the REAL capture write path and grades it with the REAL
planted-operator audit.

⛔ WHAT A GREEN RUN HERE DOES AND DOES NOT MEAN (#2552, 2026-09-21)
-----------------------------------------------------------------
This lane **monkeypatches ``extract_session_v2``** with a gold-derived
emission, so it grades the **WRITE PATH (the WIRE)** end to end — capture →
``create_operator`` → the retrievable memory layer → the grader. It does
**not** exercise ``execute_embed`` and therefore says **nothing** about
whether the extractor FORMS the operators. A 15/15 here is the correct WIRE
result and is **never** evidence that the behavioural half of #2552 is fixed;
the behavioural question is measured by the product (llm) lane and, for the
fold itself, by the deterministic fold lane at the bottom of this module.

Corpus note (#2552): the operator gold grew 4 → 15 edges (a 4-edge
denominator swung 0, 1, 1, 1, 2/4 on IDENTICAL code, so it could not separate
a fix from LLM variance). All seven sessions now carry planted operators, so
all seven are captured here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.eval.write_path import corpus  # noqa: E402

# Every session in the corpus now carries planted operator gold (#2552 grew
# the gold 4 -> 15 edges and spread it across all seven sessions).  wp06 is
# captured BEFORE wp07 — the cross-session SUPERSEDE (wp07 op_04) resolves its
# target against the earlier session's graph.
# Derived from the corpus, never a hand-maintained list. A literal list here
# meant a session added with planted operators was never captured — and BOTH
# sides of this lane's denominator assertion came from that same literal, so
# the lane stayed green while silently dropping coverage of the new edges
# (code-review finding). ``corpus.session_ids()`` is sorted, so wp06 is still
# captured before wp07 and the cross-session SUPERSEDE still resolves. The rule
# lives in ``corpus`` next to ``planted_operator_count`` so this lane and the
# corpus suite share ONE home for it instead of two identical comprehensions
# (code-review finding).
OPERATOR_SESSIONS = corpus.operator_session_ids()
# Derived from the sealed golds, never a literal (the THIRD instance of the
# hardcoded-denominator defect the #2552 gold growth exposed).
PLANTED_OPERATOR_EDGES = corpus.planted_operator_count(OPERATOR_SESSIONS)
# A SUPERSEDE is a graph REF, not a foldable edge. Derive the count rather than
# spelling a magic `1`: ``MIN_PLANTED_OPERATOR_KINDS["SUPERSEDE"]`` is a
# MINIMUM, so a second planted SUPERSEDE is a legitimate measurement-power
# extension — and it would have reddened this assertion.
PLANTED_SUPERSEDES = sum(
    1 for s in OPERATOR_SESSIONS
    for op in (corpus.load_gold(s).get("planted_operators") or [])
    if op.get("expected_kind") == "SUPERSEDE"
)

pytestmark = pytest.mark.timeout(900)


def _pt_id(content: str) -> str:
    """The deterministic content-addressed Point id the v2 seam writes
    (extractor_v2._content_id('pt', content) — the SAME id space the real
    payload uses, so the operators reference the written nodes)."""
    from tortoise.extractor_v2 import _content_id

    return _content_id("pt", content)


def _point_kind(anchor: str) -> str:
    low = anchor.lower()
    if any(w in low for w in ("decided", "decision", "ship", "reverse")):
        return "decision"
    if any(w in low for w in ("every batch was claimed", "did not cause",
                              "stable for two hours")):
        return "observation"
    if any(w in low for w in ("must have raced", "cannot re-enter",
                              "can make")):
        return "hypothesis"
    return "statement"


def _gold_operator_payload(session_id: str, gold: dict,
                           all_golds: dict[str, dict] | None = None) -> dict:
    """Build ONE session's deterministic operator payload from the planted
    gold — the ontology-correct emission (kind + endpoints) the product
    extractor is asked to produce.  Points are minted for the anchors whose
    session is THIS session (the cross-session SUPERSEDE targets an earlier
    session's point by id, never re-mints it) — including a cross-session
    anchor owned by an OPERATOR declared in another session's gold (op_04's
    superseded decision is planted in wp06's transcript but named by wp07's
    gold, so it must still be minted when capturing wp06)."""
    points: dict[str, dict] = {}
    operators: list[dict] = []
    supersessions: list[dict] = []

    def _maybe_point(anchor: str, anchor_session: str | None,
                     default_session: str) -> str:
        pid = _pt_id(anchor)
        if (anchor_session or default_session) == session_id:
            points[pid] = {"id": pid, "content": anchor,
                           "pointKind": _point_kind(anchor)}
        return pid

    # Mint every anchor this session OWNS, wherever the operator that names it
    # is declared (cross-session anchors included).
    golds = dict(all_golds or {session_id: gold})
    for other_sid, other in golds.items():
        for op in other.get("planted_operators") or []:
            for side in (op.get("from") or {}, op.get("to") or {}):
                _maybe_point(side.get("verbatim_anchor") or "",
                             side.get("session_id"), other_sid)

    for op in gold.get("planted_operators") or []:
        kind = op.get("expected_kind")
        from_ = op.get("from") or {}
        to_ = op.get("to") or {}
        from_id = _maybe_point(from_.get("verbatim_anchor") or "",
                               from_.get("session_id"), session_id)
        to_id = _maybe_point(to_.get("verbatim_anchor") or "",
                             to_.get("session_id"), session_id)
        if kind == "SUPPORTS":
            operators.append({"src": from_id, "dst": to_id,
                              "op_type": "IMPL", "direction": "unidirectional"})
        elif kind == "NEGATE":
            operators.append({"src": from_id, "dst": to_id,
                              "op_type": "NAND", "direction": "unidirectional"})
        elif kind == "MITIGATES":
            # F1 write-path form: a mitigation Point attached to an operator
            # edge touching the risk (to) point; the reason content is the
            # action (from) anchor.
            operators.append({"src": to_id, "dst": from_id,
                              "op_type": "IMPL", "direction": "unidirectional"})
            operators.append({
                "src": from_id, "dst": to_id, "op_type": "MITIGATES",
                "strength": 0.4,
                "target": {"src": to_id, "dst": from_id, "op_type": "IMPL"},
            })
        elif kind == "SUPERSEDE":
            # Point-level CORRECTS via the supersession channel: the old
            # (superseded) point is the to-anchor, the new is the from-anchor.
            supersessions.append({"superseded": to_id, "supersedes_by": from_id,
                                  "evidence": "decision reversal"})
        else:  # pragma: no cover - schema validates the kind enum
            raise AssertionError(f"unknown planted operator kind {kind!r}")
    return {
        "session_id": session_id, "story_arc": "", "entities": [],
        "points": list(points.values()), "events": [], "operators": operators,
        "supersessions": supersessions, "client_commit_id": f"ccid-{session_id}",
    }














# ── The FOLD lane (#2552): deterministic recall of the operator-form step ───
#
# The WIRE test above cannot see `execute_embed`, and the product lane's
# 4-edge denominator swings 0-2/4 on identical code — so neither could
# resolve whether the fold's endpoint handling was fixed.  This lane measures
# the FOLD directly and deterministically: the sealed gold's operator
# endpoints are handed to `execute_embed` as the model's OWN reference
# strings, with NO endpoint points emitted (the measured product-lane shape:
# the model names the planted claim as an endpoint but does not also emit it
# as a point).  The fold must materialize them and keep every operator.
#
# It is a statement about the FOLD, not about the model: whether a real LLM
# emits the endpoint at all stays the product lane's question.

def _fold_emission(gold: dict) -> tuple[dict, int, int]:
    """The gold's operators as a model emission with NO endpoint points.

    Returns ``(embed_list, planted_in_scope, expected_payload_operators)``.
    ``expected_payload_operators`` counts the MITIGATES's materialized target
    IMPL as well (each MITIGATES contributes TWO payload operators — the
    declared target IMPL plus the dampener itself).  SUPERSEDE is excluded —
    it is not an operator entry on this seam (it rides the point-level
    ``supersedes`` ref → CORRECTS), so ``PLANTED_OPERATOR_EDGES -
    PLANTED_SUPERSEDES`` planted edges are in scope.
    """
    operators: list[dict] = []
    mitigates = 0
    for op in gold.get("planted_operators") or []:
        kind = op.get("expected_kind")
        src = (op.get("from") or {}).get("verbatim_anchor") or ""
        dst = (op.get("to") or {}).get("verbatim_anchor") or ""
        if kind == "SUPERSEDE":
            continue
        if kind == "SUPPORTS":
            operators.append({"src": src, "dst": dst, "op_type": "IMPL"})
        elif kind == "NEGATE":
            operators.append({"src": src, "dst": dst, "op_type": "NAND"})
        elif kind == "MITIGATES":
            mitigates += 1
            operators.append({"src": src, "dst": dst, "op_type": "MITIGATES",
                              "strength": 0.4,
                              "target": {"src": dst, "dst": src,
                                         "op_type": "IMPL"}})
    return ({"entities": [], "events": [], "points": [], "operators": operators,
             "chain_notes": [], "link_before_create": []},
            len(operators), len(operators) + mitigates)


def test_fold_lane_mints_every_planted_operator_endpoint():
    """#2552 fold recall: every in-scope planted operator survives
    ``execute_embed`` even though the emission carries NO endpoint points.

    Pre-fix (origin/main, measured directly): 0 in-scope operators survived —
    each was dropped with "src/dst did not resolve to an emitted point/event".
    Post-fix: every one survives, and the minted Point carries the planted
    anchor verbatim, so the grader's endpoint leg can anchor on it.
    """
    from tortoise.extractor_v2 import execute_embed

    total_expected = 0
    for session_id in OPERATOR_SESSIONS:
        gold = corpus.load_gold(session_id)
        if not gold.get("planted_operators"):
            continue
        embed, expected, expected_ops = _fold_emission(gold)
        total_expected += expected
        result = execute_embed(embed, {"points": []}, session_id=session_id,
                               story_arc="", summary="")
        payload = result["payload"]
        assert len(payload["operators"]) == expected_ops, (
            f"{session_id}: {len(payload['operators'])}/{expected_ops} operators "
            f"survived the fold — {result['warnings']}")
        assert not any("did not resolve" in w for w in result["warnings"]), \
            result["warnings"]
        # Every planted anchor is carried by an emitted Point (the grader's
        # endpoint leg anchors on exactly this).
        carried = {p["content"] for p in payload["points"]}
        for op in gold["planted_operators"]:
            if op.get("expected_kind") == "SUPERSEDE":
                continue
            for side in ("from", "to"):
                anchor = (op.get(side) or {}).get("verbatim_anchor")
                assert anchor in carried, (
                    f"{session_id} {op['id']} {side} anchor not carried: "
                    f"{anchor!r}")
        # MITIGATES must arrive with its declared target IMPL materialized —
        # `commit_ops.apply_payload_operators` resolves the dampener against
        # the payload's IMPL set, so a MITIGATES without it is dropped there.
        for op in payload["operators"]:
            if op["op_type"] == "MITIGATES":
                assert (op["target"]["src"], op["target"]["dst"], "IMPL") in {
                    (o["src"], o["dst"], o["op_type"]) for o in payload["operators"]
                }, f"{session_id}: MITIGATES target IMPL missing"
    assert total_expected == PLANTED_OPERATOR_EDGES - PLANTED_SUPERSEDES


# ── The COMMIT leg (#4716 Part 4) ───────────────────────────────────────────
#
# The FOLD lane above stops at `execute_embed` — which is exactly why #4654
# stayed invisible while the lane reported 22/22 wired: the commit layer can
# drop an operator the fold kept. This leg carries the SAME fold emission all
# the way through `execute_embed` → the REAL capture commit
# (`sdk.capture_session`; the fold's own payload is committed verbatim, not a
# gold-derived one) → the retrievable (eventId-keyed) memory layer.
#
# The endpoint points are pre-created under NON-payload (ULID) ids and are
# DELIBERATELY absent from the fold's S3 prior set — the "S3 is a heuristic
# candidate surface, not an authority" case that makes the commit re-key each
# payload point to a different graph id. Without #4716 Part 1's remap the
# operator refs name the payload ids, `create_operator` raises, and
# `apply_payload_operators` swallows it (`operator write skipped (inputs
# missing?)`) — every planted edge silently lost at exactly the layer this
# lane could not see before.












