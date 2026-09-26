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
import logging
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.eval.write_path import corpus, generate_corpus, runner  # noqa: E402

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
    # An INDEPENDENT floor alongside the corpus tie above: that equality moves
    # with the golds, so a committed-corpus shrink would leave BOTH sides
    # smaller and stay green (this lane never calls validate_committed).
    assert audit["planted"] >= generate_corpus.MIN_PLANTED_OPERATOR_EDGES
    assert audit["content_ok"] == PLANTED_OPERATOR_EDGES, _failure_detail(audit)
    # The #2552 WIRE target on the deterministic lane.
    assert audit["edge_correct"] >= PLANTED_OPERATOR_EDGES - 1, \
        _failure_detail(audit)
    # The cross-session SUPERSEDE is the audit leg #2552 also repairs (the
    # session-scoped edge query could not see a CORRECTS whose target lives
    # in the earlier session). Located by its PROPERTY (kind + endpoints in
    # different sessions), never by its audit key: that key is the id STRING the
    # corpus spec hand-declares (``op_id = f"{prefix}_{op['id']}"`` in
    # ``generate_corpus._build_planted_operators`` — the ``relation_turn`` sort
    # there fixes list ORDER only, it does not derive ids). Pinning it therefore
    # couples this lane to a spec-authoring literal, and renaming or renumbering
    # the spec's op ids reddens the lane for no regression. The property is what
    # the engine must satisfy, so assert on that instead.
    cross_session_supersedes = [
        d for d in audit["results"].values()
        if d.get("expected_kind") == "SUPERSEDE"
        and d.get("from_session") != d.get("to_session")
    ]
    assert cross_session_supersedes, _failure_detail(audit)
    assert all(d["edge_correct"] for d in cross_session_supersedes), \
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

def _fold_commit_extractor(payloads: dict[str, dict]):
    """The commit leg's seam: hand the REAL ``execute_embed`` payload for this
    session to the REAL capture commit — no gold-derived payload."""
    def _run(_model, _conversation=None, *, session_id=None, **_kw):
        return {
            "session_id": session_id, "story_arc": "", "embed_list": {},
            "search": {"mode": "embedded", "degraded": True},
            "payload": payloads[session_id], "chain_notes": [],
            "link_before_create": [], "supersessions": [],
            "warnings": [], "minted_kinds": [], "stats": {}, "errors": [],
        }

    return _run


def _expected_operator_counts(payload: dict) -> tuple[int, int]:
    """(operator nodes, mitigations) a payload should commit.

    A MITIGATES payload entry does NOT create its own operator node —
    ``apply_payload_operators`` attaches a mitigation Point to the declared
    target IMPL instead, so the graph operator-node count is the IMPL/NAND
    count and the dampeners are counted separately (their deep-miss drop is
    the failure mode the count alone would hide)."""
    ops = payload.get("operators") or []
    impl_nand = sum(1 for o in ops if o.get("op_type") in ("IMPL", "NAND"))
    mitigates = sum(1 for o in ops if o.get("op_type") == "MITIGATES")
    return impl_nand, mitigates

def _fold_payloads(ev2) -> dict[str, dict]:
    """The REAL fold output per operator session (endpoints minted, no emitted
    points) — the input to both commit-leg tests."""
    golds = {sid: corpus.load_gold(sid) for sid in OPERATOR_SESSIONS}
    payloads: dict[str, dict] = {}
    for sid in OPERATOR_SESSIONS:
        embed, _expected, _ops = _fold_emission(golds[sid])
        payloads[sid] = ev2.execute_embed(
            embed, {"points": []}, session_id=sid, story_arc="",
            summary="")["payload"]
    return payloads


def test_fold_lane_commit_leg_keeps_every_operator_after_a_rekey(
        _deterministic_lane, monkeypatch, caplog):
    """#4716 Part 4, the rekey leg: the FOLD's operators survive the capture
    commit after every endpoint re-keys to a pre-existing, NON-payload graph
    id.

    This is the leg the FOLD lane could not see before #4716: it stopped at
    ``execute_embed``, so a fold that read 22/22 could still lose every edge
    at the commit — and with all endpoints re-keying, it did (the #4654 silent
    drop). The endpoints are pre-created under ULID ids and DELIBERATELY kept
    out of the fold's S3 prior set (``_fold_emission`` passes
    ``{"points": []}``), so the commit's content-hash resolution genuinely
    re-keys each payload point.

    Asserted at the commit layer, per operator class: every IMPL/NAND operator
    node exists with its endpoints wired to the EXISTING graph nodes, every
    dampener attached to its target edge, and 0 ``operator write skipped
    (inputs missing?)`` — the pre-#4716 signature.

    The no-orphan post-condition (#4654) is the count equality: the fold emits
    no points and the mint prunes unreferenced endpoints, so the payload's
    points ARE its operator endpoints — every operator surviving means every
    endpoint is referenced, i.e. no orphan Point.
    """
    import tortoise.extractor_v2 as ev2

    lane = _deterministic_lane
    sdk = lane["sdk"]
    proj = sdk._get_proj()
    sid = "wp01_quarry_debug"   # 2 IMPL/NAND + 1 MITIGATES (4 endpoints)
    payload = _fold_payloads(ev2)[sid]
    impl_nand, mitigates = _expected_operator_counts(payload)
    assert (impl_nand, mitigates) == (2, 1)

    # every endpoint pre-exists under a NON-payload id
    anchors = sorted({str(pt["content"]).strip()[:1000]
                      for pt in payload["points"]})
    for anchor in anchors:
        sdk.create_point("statement", anchor)
    row = proj.g.query(
        "MATCH (p:Point) WHERE NOT p.is_operator AND NOT p.id CONTAINS '_t' "
        "RETURN count(p)").result_set[0][0]
    assert row == len(anchors) == 4

    monkeypatch.setattr(ev2, "extract_session_v2",
                        _fold_commit_extractor({sid: payload}))
    fixture = corpus.load_fixture(sid)
    conv = runner.parse_roundtrip(
        sid, fixture["conversation"], fixture["harness"],
        workdir=lane["workdir"])
    with caplog.at_level(logging.WARNING, logger="tortoise.commit_ops"):
        cap = sdk.capture_session(
            conv, session_id=sid, harness=fixture["harness"])
    assert cap.get("ok") is True, cap

    # every fold operator committed, wired to the EXISTING (re-keyed) nodes
    by_id = {pt["id"]: str(pt["content"]) for pt in payload["points"]}
    expected_pairs = {
        (by_id[o["src"]], by_id[o["dst"]])
        for o in payload["operators"] if o["op_type"] in ("IMPL", "NAND")}
    actual_pairs = {
        (s, d) for s, d in proj.g.query(
            "MATCH (o:Point {is_operator:true})-[:IMPL {idx:0}]->(s:Point) "
            "MATCH (o)-[:IMPL {idx:1}]->(d:Point) "
            "RETURN s.content, d.content").result_set}
    assert actual_pairs == expected_pairs, (actual_pairs, expected_pairs)
    op_nodes = proj.g.query(
        "MATCH (o:Point) WHERE o.is_operator AND o.op_type IN ['IMPL','NAND'] "
        "RETURN count(o)").result_set[0][0]
    assert op_nodes == impl_nand
    # every dampener found its target edge (the deep-miss drop)
    attached = proj.g.query(
        "MATCH (o:Point {is_operator:true})-[:mitigated_by]->(m:Point) "
        "RETURN count(m)").result_set[0][0]
    assert attached == mitigates
    # ⛔ #4716 review P1: a COUNT is not enough — the dampener's reason is
    # resolved from the same ref the remap repointed, so a map-unaware
    # resolver silently degraded its content to "[MITIGATION] [MITIGATION]
    # <graph-id>". Assert the content resolves to a real endpoint, with
    # exactly the one prefix `sdk.mitigate_operator` adds (`why.py` strips one).
    for (content,) in proj.g.query(
            "MATCH (o:Point {is_operator:true})-[:mitigated_by]->(m:Point) "
            "RETURN m.content").result_set:
        assert "[MITIGATION] [MITIGATION]" not in content, content
        assert content.count("[MITIGATION]") == 1, content
        assert any(a in content for a in anchors), (content, anchors)
    assert "operator write skipped" not in caplog.text, caplog.text
    assert "not found — mitigation dropped" not in caplog.text, caplog.text


def test_fold_lane_commit_leg_reaches_the_retrievable_layer(
        _deterministic_lane, monkeypatch, caplog):
    """#4716 Part 4, the retrievable-layer leg: with an EMPTY graph every
    payload id is created as-is (identity map), so the commit must commit
    exactly the fold's operators AND stamp them into the eventId-keyed memory
    layer — the structural surface #2552 measured at 0/4.

    This is the complement of the rekey leg (the remap is total: an EMPTY
    id_map returns the operator list unchanged, so the identity case cannot be
    perturbed)."""
    import tortoise.extractor_v2 as ev2

    lane = _deterministic_lane
    sdk = lane["sdk"]
    payloads = _fold_payloads(ev2)
    monkeypatch.setattr(ev2, "extract_session_v2",
                        _fold_commit_extractor(payloads))
    with caplog.at_level(logging.WARNING, logger="tortoise.commit_ops"):
        for sid in OPERATOR_SESSIONS:
            payload = payloads[sid]
            impl_nand, mitigates = _expected_operator_counts(payload)
            if not payload.get("operators"):
                continue
            fixture = corpus.load_fixture(sid)
            conv = runner.parse_roundtrip(
                sid, fixture["conversation"], fixture["harness"],
                workdir=lane["workdir"])
            cap = sdk.capture_session(
                conv, session_id=sid, harness=fixture["harness"])
            assert cap.get("ok") is True, cap
            snap = runner.snapshot_session(sdk, sid)
            assert snap["operators_total"] == impl_nand, (
                f"{sid}: {snap['operators_total']}/{impl_nand} operator nodes")
            # every survivor entered the retrievable (eventId) layer
            assert snap["operators_provenanced"] == impl_nand
            assert len(snap["mitigations"]) == mitigates, (
                f"{sid}: {len(snap['mitigations'])}/{mitigates} mitigations")
    assert "operator write skipped" not in caplog.text, caplog.text


def test_fold_lane_commit_leg_rekey_stamps_the_operators_it_created(
        _deterministic_lane, monkeypatch, caplog):
    """#4936: the RE-KEYED commit leg must stamp the operators it CREATED
    with the session's ``sessionCaptured`` eventId — the retrievable-layer leg
    the rekey test above deliberately does not assert.

    #4716 Part 1 made the operator EDGE survive a re-key (pre-fix every
    operator was dropped as ``operator write skipped``), which exposed this
    state: the operator node is created and wired correctly, but the capture's
    provenance stamp joined on the MINTED point set and every endpoint here
    RE-KEYED to a pre-existing graph node — so ``minted_ids`` is empty, the
    join never runs, and the operator stays ``eventId IS NULL``. It is then
    invisible to the eventId-keyed retrievable memory layer (the #2552
    ``operator_counts == {}`` signature) even though the edge is in the graph.

    The fix stamps exactly the operator ids ``apply_payload_operators``
    CREATED (surfaced on the extraction meta), not a join on the minted set —
    the topology's own provenance handle. Only RE-keyed here: the identity leg
    above already pins the minted-endpoint path, and the fix must not
    perturb it.

    ⛔ Scope note (measured, not assumed): ``operator_counts`` /
    ``operator_edges`` / ``mitigations`` STAY empty in this all-endpoints-
    re-keyed scenario, before AND after the fix. That is NOT an operator
    stamping gap: the runner's edge queries require the edge ENDPOINT to be in
    the eventId-keyed ``seen`` set, and a re-keyed endpoint is a pre-existing
    canonical placed OUTSIDE any capture (no prior eventId) which the fold
    discipline forbids re-stamping (#2104 Phase D — a claim's provenance stays
    minted-only; the issue's own proposed direction keeps it). The operator
    NODE is the retrievable artifact this issue files, and the identity leg
    shows the same commit populates ``operator_counts``
    (``{'IMPL': 4, 'INPUT': 4}``) once the endpoints are minted. The
    assertions below are therefore on the eventId-keyed NODE surface.
    """
    import tortoise.extractor_v2 as ev2

    lane = _deterministic_lane
    sdk = lane["sdk"]
    proj = sdk._get_proj()
    sid = "wp01_quarry_debug"   # 2 IMPL/NAND + 1 MITIGATES (4 endpoints)
    payload = _fold_payloads(ev2)[sid]
    impl_nand, mitigates = _expected_operator_counts(payload)
    assert (impl_nand, mitigates) == (2, 1)

    # every endpoint pre-exists under a NON-payload id (the re-key)
    anchors = sorted({str(pt["content"]).strip()[:1000]
                      for pt in payload["points"]})
    for anchor in anchors:
        sdk.create_point("statement", anchor)

    monkeypatch.setattr(ev2, "extract_session_v2",
                        _fold_commit_extractor({sid: payload}))
    fixture = corpus.load_fixture(sid)
    conv = runner.parse_roundtrip(
        sid, fixture["conversation"], fixture["harness"],
        workdir=lane["workdir"])
    with caplog.at_level(logging.WARNING, logger="tortoise.commit_ops"):
        cap = sdk.capture_session(
            conv, session_id=sid, harness=fixture["harness"])
    assert cap.get("ok") is True, cap
    assert "operator write skipped" not in caplog.text, caplog.text

    # the session's own sessionCaptured eventId
    eid = proj.g.query(
        "MATCH (src:Source {sessionId: $sid})-[:references]->"
        "(e:Event {eventKind: 'sessionCaptured'}) "
        "RETURN coalesce(e.eventId, e.id)",
        params={"sid": sid},
    ).result_set[0][0]
    # every operator node this capture created carries it...
    rows = proj.g.query(
        "MATCH (o:Point {is_operator:true}) RETURN o.id, o.eventId"
    ).result_set
    assert len(rows) == impl_nand, rows
    assert all(r[1] == eid for r in rows), rows
    # ... and the eventId-keyed retrievable memory layer (MEMORY_ROW_QUERY /
    # ``MATCH (p:Point) WHERE p.eventId = $eid``) admits every one of them.
    claimed = proj.g.query(
        "MATCH (p:Point) WHERE p.eventId = $eid AND p.is_operator = true "
        "RETURN count(p)", params={"eid": eid},
    ).result_set[0][0]
    assert claimed == impl_nand, (claimed, impl_nand)
    snap = runner.snapshot_session(sdk, sid)
    assert snap["operators_total"] == impl_nand, (
        f"{snap['operators_total']}/{impl_nand} operator nodes retrievable")
    assert snap["operators_provenanced"] == impl_nand, snap


def test_fold_lane_commit_leg_stamps_every_node_when_a_triple_repeats(
        _deterministic_lane, monkeypatch):
    """#4936 (code-review P1): two payload operator records that resolve onto
    the SAME ``(src, dst, op_type)`` triple each create their own node —
    ``sdk.create_operator`` mints unconditionally, with no idempotency guard
    (#4971) — so the capture's provenance set must be the CREATION LIST, not
    the MITIGATES lookup dict. Building it from ``target_op_ids.values()``
    collapses the duplicate and silently drops the earlier node's id: that
    node stays ``eventId IS NULL`` and invisible to the eventId-keyed layer,
    re-introducing the exact #4936 defect the fix removes.

    A duplicate triple is reachable two ways, both real: two identical
    emitted records (``extractor_v2`` guards MITIGATES against
    ``emitted_edges`` but appends IMPL/NAND unconditionally), and two
    DISTINCT payload records that re-key onto one graph pair (the #4716 remap
    is not injective). Both create two nodes; both must be stamped.
    """
    import tortoise.extractor_v2 as ev2

    lane = _deterministic_lane
    sdk = lane["sdk"]
    proj = sdk._get_proj()
    sid = "wp01_quarry_debug"
    payload = _fold_payloads(ev2)[sid]
    # Duplicate one IMPL/NAND record verbatim — after the remap the two
    # records collapse onto the same graph triple.
    dup = next(o for o in payload["operators"]
               if o["op_type"] in ("IMPL", "NAND"))
    payload = {**payload,
               "operators": [*payload["operators"], dict(dup)]}
    impl_nand, _mitigates = _expected_operator_counts(payload)
    assert impl_nand == 3, impl_nand   # 2 distinct + the duplicate

    anchors = sorted({str(pt["content"]).strip()[:1000]
                      for pt in payload["points"]})
    for anchor in anchors:
        sdk.create_point("statement", anchor)

    monkeypatch.setattr(ev2, "extract_session_v2",
                        _fold_commit_extractor({sid: payload}))
    fixture = corpus.load_fixture(sid)
    conv = runner.parse_roundtrip(
        sid, fixture["conversation"], fixture["harness"],
        workdir=lane["workdir"])
    cap = sdk.capture_session(
        conv, session_id=sid, harness=fixture["harness"])
    assert cap.get("ok") is True, cap

    rows = proj.g.query(
        "MATCH (o:Point {is_operator:true}) RETURN o.id, o.eventId"
    ).result_set
    assert len(rows) == impl_nand, (
        f"{len(rows)}/{impl_nand} operator nodes created")
    unstamped = [r[0] for r in rows if not r[1]]
    assert not unstamped, (
        f"duplicate-triple operator nodes left unstamped: {unstamped}")
