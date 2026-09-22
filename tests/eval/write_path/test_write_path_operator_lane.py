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

import contextlib
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.eval.write_path import corpus, runner  # noqa: E402

# Every session in the corpus now carries planted operator gold (#2552 grew
# the gold 4 -> 15 edges and spread it across all seven sessions).  wp06 is
# captured BEFORE wp07 — the cross-session SUPERSEDE (wp07 op_04) resolves its
# target against the earlier session's graph.
OPERATOR_SESSIONS = [
    "wp01_quarry_debug", "wp02_lumen_refactor", "wp03_ember_design",
    "wp04_aurora_perf", "wp05_retro_writeup", "wp06_quarry_rollout",
    "wp07_bluepeak_followup",
]
PLANTED_OPERATOR_EDGES = 15

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
    """#2552 WIRE target: with the ontology-correct operator emission, the REAL
    write path + audit must grade the planted operator edges ``edge_correct``
    AND persist every operator into the retrievable memory layer (eventId).
    Before the structural fix (#3071) this lane graded 0/N because the operator
    nodes were not eventId-stamped (the audit could still see them via
    ``is_operator``, but the retrievability surface was empty).

    ⛔ This grades the WIRE, not extractor behaviour — see the module
    docstring. Keep one edge of headroom (``>= planted - 1``) so a single
    form-regression is reported as a number rather than a hard error, exactly
    as the 4-edge version of this lane did.
    """
    lane = _deterministic_lane
    report = runner.run_benchmark(
        root=lane["root"], sdk=lane["sdk"], session_ids=OPERATOR_SESSIONS,
    )
    assert report["run_status"] == "completed", report.get("log")
    audit = report["operator_audit"]
    assert audit is not None
    assert audit["planted"] == PLANTED_OPERATOR_EDGES
    assert audit["content_ok"] == PLANTED_OPERATOR_EDGES, _failure_detail(audit)
    # The #2552 WIRE target on the deterministic lane.
    assert audit["edge_correct"] >= PLANTED_OPERATOR_EDGES - 1, \
        _failure_detail(audit)
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
    assert receipt["operator_audit"]["edge_correct"] >= \
        PLANTED_OPERATOR_EDGES - 1
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
    ``supersedes`` ref → CORRECTS), so 14 of the 15 planted edges are in scope.
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
    assert total_expected == PLANTED_OPERATOR_EDGES - 1  # SUPERSEDE is a ref
