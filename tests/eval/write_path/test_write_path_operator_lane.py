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
it feeds the ontology-correct operator emission (the F1/F2-validated mapping
in ``docs/scoping/2026-09-07-2514-operator-corpus.md``, derived here from the
sealed gold) through the REAL capture write path and grades it with the REAL
planted-operator audit.  If the write path persists the operators and the
audit resolves the edges, ``edge_correct >= 3/4`` — the #2552 target.

The lane deliberately does NOT claim a behavioral number: per-kind detection
quality is what the product-lane re-run measures.  The point here is the
WIRE: a correctly-formed operator node must enter the retrievable graph and
be graded.
"""
from __future__ import annotations

import contextlib
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.eval.write_path import corpus, runner  # noqa: E402

# The two sessions that carry the planted operator gold (wp06: op_01, wp07:
# op_02/03/04).  wp06 is captured FIRST — the cross-session SUPERSEDE (op_04)
# resolves its target against the earlier session's graph.
OPERATOR_SESSIONS = ["wp06_quarry_rollout", "wp07_bluepeak_followup"]

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


def _extractor(golds: dict[str, dict]):
    """The deterministic extractor seam: a per-session payload derived from
    the sealed gold, shaped exactly like ``extractor_v2.extract_session_v2``'s
    output so ``sdk._extract_session_v2`` consumes it unchanged."""

    def _run(_model, _conversation=None, *, session_id=None, **_kw):
        payload = _gold_operator_payload(
            session_id, golds.get(session_id, {}), golds)
        return {
            "session_id": session_id, "story_arc": "", "embed_list": {},
            "search": {"mode": "embedded", "degraded": True},
            "payload": payload, "chain_notes": [], "link_before_create": [],
            "supersessions": [], "warnings": [], "minted_kinds": [],
            "stats": {}, "errors": [],
        }

    return _run


def _tmp_corpus(tmp_path: Path) -> Path:
    dst = tmp_path / "corpus"
    shutil.copytree(corpus.WRITE_PATH_DIR, dst, ignore=shutil.ignore_patterns(
        "runs", "__pycache__", "test_*.py"))
    return dst


@pytest.fixture()
def _deterministic_lane(monkeypatch, tmp_path):
    """A hermetic SDK + the deterministic operator extractor installed."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    import tortoise.extractor_v2 as ev2

    golds = {sid: corpus.load_gold(sid) for sid in OPERATOR_SESSIONS}
    monkeypatch.setattr(ev2, "extract_session_v2", _extractor(golds))
    from tortoise.sdk import TortoiseSDK

    workdir = tmp_path / "graph"
    workdir.mkdir()
    # A UNIQUE namespace per test (mirrors the benchmark fixture) so a
    # server-lane redirect never folds onto a previous run's graph.
    import uuid

    sdk = TortoiseSDK(db_path=str(workdir / "lane.db"),
                      namespace=f"w2552lane_{uuid.uuid4().hex[:8]}")
    with contextlib.suppress(Exception):
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    yield {"sdk": sdk, "root": _tmp_corpus(tmp_path), "workdir": workdir}
    with contextlib.suppress(Exception):
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    with contextlib.suppress(Exception):
        sdk.close()


def _failure_detail(audit: dict) -> str:
    """Readable failure detail — the per-edge verdicts."""
    detail = {eid: (d.get("verdict"), d.get("forms_found"))
              for eid, d in (audit.get("results") or {}).items()}
    return (f"edge_correct={audit.get('edge_correct')}/"
            f"{audit.get('planted')}: {detail}")


def test_deterministic_planted_operator_lane_grades_at_least_three(
        _deterministic_lane):
    """#2552 target: with the ontology-correct operator emission, the REAL
    write path + audit must grade >= 3/4 planted operator edges
    ``edge_correct`` AND persist every operator into the retrievable memory
    layer (eventId).  Before the structural fix this lane graded 0/4 because
    the operator nodes were not eventId-stamped (the audit could still see
    them via ``is_operator``, but the retrievability surface was empty)."""
    lane = _deterministic_lane
    report = runner.run_benchmark(
        root=lane["root"], sdk=lane["sdk"], session_ids=OPERATOR_SESSIONS,
    )
    assert report["run_status"] == "completed", report.get("log")
    audit = report["operator_audit"]
    assert audit is not None
    assert audit["planted"] == 4
    assert audit["content_ok"] == 4, _failure_detail(audit)
    # The #2552 target on the deterministic lane.
    assert audit["edge_correct"] >= 3, _failure_detail(audit)
    # The cross-session SUPERSEDE is the audit leg #2552 also repairs (the
    # session-scoped edge query could not see a CORRECTS whose target lives
    # in the earlier session).
    assert audit["results"]["wp07_bluepeak_followup_op_04"]["edge_correct"], \
        _failure_detail(audit)
    # STRUCTURAL leg: every operator the write path committed entered the
    # eventId-keyed memory layer.
    assert audit["operators_total"] > 0
    assert audit["operators_provenanced"] == audit["operators_total"]
    # The receipt carries both the edge and the persistence audit.
    receipt = runner.build_receipt(report)
    assert runner.validate_receipt(receipt) == []
    assert receipt["operator_audit"]["edge_correct"] >= 3
    assert (receipt["operator_audit"]["operators_provenanced"]
            == receipt["operator_audit"]["operators_total"])


def test_operator_nodes_enter_retrievable_layer_and_survive_strip(
        _deterministic_lane):
    """The structural fix pinned directly: operator nodes written by a capture
    carry the sessionCaptured ``eventId`` and are returned by the eventId-keyed
    memory query.  Stripping the eventId (the pre-fix state) removes them from
    the retrievable layer AND from the operator_counts surface — the exact
    silent-drop signature #2552 measured."""
    lane = _deterministic_lane
    sdk = lane["sdk"]
    sid = OPERATOR_SESSIONS[0]
    fixture = corpus.load_fixture(sid)
    conv = runner.parse_roundtrip(
        sid, fixture["conversation"], fixture["harness"], workdir=lane["workdir"])
    cap = sdk.capture_session(conv, session_id=sid, harness=fixture["harness"])
    assert cap.get("ok") is True, cap

    snap = runner.snapshot_session(sdk, sid)
    assert snap["operators_total"] > 0, "the capture must commit operators"
    assert snap["operators_provenanced"] == snap["operators_total"]
    assert snap["operator_counts"].get("IMPL", 0) > 0

    # Pre-fix mutation: strip the eventId off the operators.  They become
    # invisible to the retrievable layer — the measured 0/4 signature.
    sdk._get_proj().g.query(
        "MATCH (o:Point {is_operator:true}) REMOVE o.eventId")
    stripped = runner.snapshot_session(sdk, sid)
    assert stripped["operators_total"] == 0
    assert stripped["operator_counts"] == {}
