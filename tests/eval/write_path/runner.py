"""W2-b write-path measurement helpers (issue #2098, epic #2080 W2-b).

**The replay leg this module was built around is GONE (#4282).** The runner
used to drive each committed session through the REAL write path
(store-file render → ``session_import`` parser round-trip →
``sdk.capture_session`` on a hermetic graph → dream EP pass), grade the
written graph and publish a receipt.  #4282 deleted ``capture_session`` from
the SDK surface. No surviving PUBLIC SDK entry point reaches an in-memory
conversation capture (``index_sessions`` / ``reconcile_sessions`` index session
*files*; the hosted ``POST /v1/sessions`` door is a server route, not an SDK
method, and its async ``_capture_session_impl`` is the only door that survives
whole). ⚠️ The PRIVATE ``_extract_session_v2`` *does* capture in-process — measured,
four probes, in `tests/test_graph_census_7949.py`'s header — but it mints no
``:Session`` and writes no ``(Source)-[:references]->(Event)`` link, so a fixture
driving it reads 0 points through ``snapshot_session``; closing that gap means
re-implementing the deleted door's ordering contract inside a fixture.  ``run_benchmark`` and the ``run`` CLI sub-command were
therefore removed with it, and so was every probe that drove them
(``test_write_path_benchmark.py`` — including the #4860 rotating-key
failover guard — and the ``run``-gate tests in
``test_write_path_runner.py``).  The line that produced the receipt's
per-session capture diagnostics went with the same leg.

What is LEFT is the measurement scaffolding that never called the write path,
kept because surviving tests and the CLI still use it:

* the **real parser seam** — ``render_store_lines`` + ``parse_roundtrip``
  (byte-identical round-trip over the committed corpus);
* ``preflight`` — manifest / hash / baseline / resolved-config checks for a
  run over the committed corpus;
* ``snapshot_session`` + the **#8444 report-only graph census** (E1 Object
  composition, E3 edge anchors: ``graph_census``, ``census_or_error``,
  ``aggregate_graph_census``);
* the **additive banded semantic-judge arm** (#5085) and its #5106
  pre-screen audit seam (``_run_semantic_judge``, ``_validate_semantic_units``);
* ``build_receipt`` / ``validate_receipt`` — the epic §6.6 receipt contract
  (run_status / verdict / failure_origin / commit / corpus_hash / judge_pin /
  resolved_config / cost_usd);
* operator drop/audit note helpers, which read a report rather than produce
  one.

CLI (``_main``): ``bless`` (promote a receipt's numbers into a posture
baseline), ``validate-receipt``, ``calibrate`` (build/report the banded-judge
calibration sample) and ``prescreen-audit`` (recompute the #5106 pre-screen
over a judged receipt).  There is deliberately NO ``run`` sub-command: it
would have nothing to measure.

⚠️ **Cost / key notes that still apply to any receipt you bless.** Receipts
record ``cost_usd`` from capture telemetry only when a seam reports it; today
no seam reports ``llm_cost_usd``, so a receipt carries the explicit
cost-not-tracked note rather than a silently-faked 0.0.  Provider keys are
read straight from the process env and never from the repo ``.env`` — start
any real-key lane through ``tools/run-with-eval-keys.sh`` (#2718 / #4860) and
paste its key-source block into the receipt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Module root helpers — this file doubles as the CLI entry
# (``python -m tests.eval.write_path.runner``), so the repo root must be
# importable whether it is run from the repo root or via a path.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.eval.write_path import corpus, grading, judge, schema  # noqa: E402
from tests.eval.write_path import prescreen as prescreen_mod  # noqa: E402

RUNS_DIR = Path(__file__).resolve().parent / "runs"

# §6.6 receipt vocabulary
RUN_STATUS_VALUES = frozenset({"completed", "failed", "skipped"})
VERDICT_VALUES = schema.VERDICT_VALUES
FAILURE_ORIGIN_VALUES = frozenset(
    {"config_mismatch", "hash_mismatch", "judge_pin_mismatch",
     "runner_error", "gate_regression", None}
)

# CLI exit codes
EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_INCONCLUSIVE = 2
EXIT_RUNNER_ERROR = 1


class RunError(Exception):
    """Runner-level failure with a §6.6 failure_origin (runner_error by default)."""

    def __init__(self, message: str, *, origin: str = "runner_error") -> None:
        super().__init__(message)
        self.origin = origin


# ── Pre-flight (S4: manifest/hash/baseline/config) ──────────────────────────




def preflight(root: Path = corpus.WRITE_PATH_DIR, *, posture: str = "llm") -> dict:
    """Pre-flight gate: manifest coverage, corpus validity, baseline validity.

    Returns ``{"ok": bool, "issues": [...], "fixtures_hash": str,
    "baseline": dict}``.  Every committed fixture + gold validates against
    its schema (cross-checked pair-wise), the manifest covers the on-disk
    corpus byte-for-byte, the committed baseline for the run's ``posture``
    validates (first-run-pending or published), and every gold carries ≥1
    graded salient unit (a vacuum 1.0 denominator would rubber-stamp).
    """
    issues: list[str] = []
    verify = corpus.verify_manifest(root)
    if not verify["ok"]:
        detail = verify.get("malformed") or ""
        issues.append(
            f"manifest verification failed (missing={verify['missing']}, "
            f"extra={verify['extra']}, mismatched={verify['mismatched']}){detail}"
        )
    else:
        manifest = corpus.load_manifest(root)
        m_issues = schema.validate_manifest(manifest)
        issues.extend(f"manifest: {i}" for i in m_issues)
    baseline = corpus.load_baseline(root, posture=posture)
    b_issues = schema.validate_baseline(baseline)
    issues.extend(f"baseline ({posture}): {i}" for i in b_issues)
    for session_id in corpus.session_ids(root):
        fixture = corpus.load_fixture(session_id, root)
        issues.extend(f"fixture {session_id}: {i}" for i in schema.validate_fixture(fixture))
        gold = corpus.load_gold(session_id, root)
        issues.extend(
            f"gold {session_id}: {i}" for i in schema.validate_gold(gold, fixture)
        )
        if not gold.get("salient_units"):
            issues.append(f"gold {session_id}: has no graded salient units (vacuum 1.0)")
    fixtures_hash = corpus.compute_fixtures_hash(root)
    if baseline.get("fixtures_hash") != fixtures_hash:
        issues.append(
            "baseline.fixtures_hash != on-disk corpus hash "
            f"({baseline.get('fixtures_hash')} vs {fixtures_hash}) — corpus drift"
        )
    return {
        "ok": not issues,
        "issues": issues,
        "fixtures_hash": fixtures_hash,
        "baseline": baseline,
    }


# ── Store-file render + parser round-trip (S1 real parser seam) ─────────────


def render_store_lines(session_id: str, conversation: list[dict], harness: str) -> str:
    """Deterministic store-format render of the fixture conversation.

    Renders the canonical conversation into the harness's REAL session-store
    shape so the REAL ``session_import`` parser (not a fixture parser) is the
    replay seam.  One record per turn; content is a single text part so the
    parser's flatten is byte-identical (a multi-part render would exercise
    the flatten join — byte-parity with the fixture is what the round-trip
    asserts).
    """
    lines: list[str] = []
    for turn in conversation:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if harness == "codex":
            part_type = "input_text" if role == "user" else "output_text"
            record = {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": role,
                    "content": [{"type": part_type, "text": content}],
                },
            }
        elif harness == "pi":
            # #3667: Pi has its OWN store shape (type == "message" →
            # message.{role,content}), NOT codex's `payload` shape. The pi
            # branch must render the REAL Pi record or the round-trip parses
            # 0 turns and the graded pi lane is vacuous.
            record = {
                "type": "message",
                "message": {
                    "role": role,
                    "content": [{"type": "text", "text": content}],
                },
            }
        elif harness == "claude-desktop":
            record = {
                "message": {
                    "role": role,
                    "content": [{"type": "text", "text": content}],
                }
            }
        else:
            raise RunError(f"no store render for harness {harness!r}")
        lines.append(json.dumps(record, sort_keys=True))
    return "\n".join(lines) + "\n"


def parse_roundtrip(
    session_id: str,
    conversation: list[dict],
    harness: str,
    *,
    workdir: Path,
    log: list[str] | None = None,
) -> list[dict]:
    """Render → REAL parser → assert byte-parity with the fixture conversation.

    Returns the parser's canonical turns.  Raises RunError when the parser
    round-trip is not byte-identical with the fixture conversation (parser
    drift would silently change what the graded write path ingests).
    """
    from tortoise.session_import import parsers

    store = workdir / f"{session_id}.jsonl"
    store.write_text(render_store_lines(session_id, conversation, harness), encoding="utf-8")
    parsed = parsers.parse_transcript(store, harness)
    expected = [{"role": t.get("role"), "content": t.get("content")} for t in conversation]
    if parsed != expected:
        raise RunError(
            f"session {session_id}: parser round-trip drift ({harness}) — parsed "
            f"{len(parsed)} turns vs fixture {len(conversation)}"
        )
    if log is not None:
        log.append(f"parser round-trip ok ({harness}, {len(parsed)} turns)")
    return parsed


# ── Graph snapshot (memory layer of one session) ────────────────────────────


def _row_to_point(row: tuple) -> dict:
    """Map a MEMORY_ROW_QUERY result row to a SessionPoint for grading.

    Column order (see MEMORY_ROW_QUERY): id, content, eventId, extractedFrom,
    status, confidence, lastDreamedAt, pointKind, is_episodic, is_operator.
    """
    (pid, content, event_id, extracted_from, status,
     confidence, last_dreamed, point_kind, is_episodic, is_operator) = row
    return {
        "point_id": pid,
        "content": content or "",
        "provenance_present": bool(event_id) or bool(extracted_from),
        "ep_updated": confidence is not None or last_dreamed is not None,
        "status": status,
        "point_kind": point_kind,
        "is_episodic": is_episodic,
        "is_operator": bool(is_operator),
        "event_id": event_id,
    }


SESSION_EVENT_QUERY = (
    # The sessionCaptured Event carries no sessionId — the capture path
    # links it through the typed agentSession Source it materializes
    # (sdk.capture_session: Source {sessionId} -[references]-> Event); the
    # extracted points are stamped with the Event's eventId (#1417).
    "MATCH (src:Source {sessionId: $sid})-[:references]->"
    "(e:Event {eventKind: 'sessionCaptured'}) "
    "RETURN coalesce(e.eventId, e.id)"
)
SESSION_TURN_QUERY = (
    "MATCH (s:Session {id: $sid})-[:CONTAINS]->(p:Point) "
    "RETURN p.id"
)
# Turn points carry the deterministic ``{sid}_t{i}`` id the capture loop
# writes (sdk.capture_session).  The Session ALSO CONTAINS extracted claims
# on some paths — the id pattern is the reliable turn/claim discriminator.
def _turn_id_pattern(session_id: str) -> str:
    return re.compile(rf"^{re.escape(session_id)}_t\d+$")
# The single projection every consumer of _row_to_point must share.  Keep it a
# named constant: a duplicated copy in another module silently drops a column the
# moment this one grows (harness cell_points did exactly that when is_operator
# landed — p.id..p.is_operator is TEN columns, and a 9-column copy raises
# "ValueError: not enough values to unpack (expected 10, got 9)").
MEMORY_ROW_COLUMNS = (
    "p.id, p.content, p.eventId, p.extractedFrom, p.status, "
    "p.confidence, p.lastDreamedAt, p.pointKind, p.is_episodic, p.is_operator"
)
MEMORY_ROW_QUERY = (
    "MATCH (p:Point) WHERE p.eventId IN $eids "
    f"RETURN {MEMORY_ROW_COLUMNS}"
)
OPERATOR_EDGE_QUERY = (
    "MATCH (a:Point)-[r]->(b:Point) "
    "WHERE a.id IN $ids AND b.id IN $ids "
    "RETURN type(r), a.id, b.id"
)
# #2552: cross-session CORRECTS — the planted SUPERSEDE is CROSS-SESSION by
# construction (a point-level supersession only forms when the superseded
# claim already exists in-graph; the scoping note 2026-09-07-2514-).  The
# session-scoped OPERATOR_EDGE_QUERY above requires BOTH endpoints in the
# session's ``seen`` set, so a CORRECTS whose TARGET lives in the earlier
# session was invisible to the audit — the cross-session SUPERSEDE graded
# ``edge_missing`` even when the write path wired it correctly.  Surface
# every CORRECTS whose SOURCE is in this session (the new/active endpoint).
CORRECTS_EDGE_QUERY = (
    "MATCH (a:Point)-[r:CORRECTS]->(b:Point) "
    "WHERE a.id IN $ids "
    "RETURN type(r), a.id, b.id"
)
# #2514: reified operator-mediated edges — operator nodes are :Point
# {is_operator:true} WITHOUT eventId, so they never enter the eventId-keyed
# memory layer and the query above (both endpoints in the seen set) never
# matches their (op)-[:IMPL|NAND]->(endpoint) fan-out.  This query surfaces
# them: every operator node that touches a session memory point, with its
# mechanism (op_type property), direction, semantic label, and per-endpoint
# INPUT index (idx 0 = the epistemically active source).
OPERATOR_NODE_EDGE_QUERY = (
    "MATCH (o:Point {is_operator:true})-[r]->(p:Point) "
    "WHERE p.id IN $ids AND type(r) <> 'INPUT' "
    "RETURN o.id, o.op_type, o.direction, o.label, type(r) AS rtype, p.id, r.idx"
)
# #2514: mitigation Points on operators that touch the session —
# (op)-[:mitigated_by]->(m) with m.content the mitigation reason (the write
# path passes the src point's content as mitigate_operator's reason).
MITIGATION_QUERY = (
    "MATCH (o:Point {is_operator:true})-[r]->(p:Point) "
    "WHERE p.id IN $ids AND type(r) <> 'INPUT' "
    "WITH DISTINCT o "
    "MATCH (o)-[mb:mitigated_by]->(m:Point) "
    "RETURN o.id, m.id, m.content"
)


def snapshot_session(sdk, session_id: str) -> dict:
    """Snapshot one session's memory layer + operator edges from the graph.

    Returns ``{"points": [SessionPoint...], "rephrase_edges": [(a, b)...],
    "turn_ids": [...]}``.  Memory points EXCLUDE the episodic turn echo
    (the graded layer is what the write path minted on top of the
    transcript).

    The session→memory link is the PROVENANCE surface the capture stamps:
    every extracted point carries ``eventId`` = the id of the session's
    ``sessionCaptured`` Event (sdk.capture_session, #1417 — provenance is
    the point's eventId property, shared by BOTH extractor branches).  Turn
    points are the transcript, not memory — excluded by id (``{sid}_t{i}``)
    and by the ``is_turn_echo`` discriminator (belt + braces).
    """
    proj = sdk._get_proj()
    g = proj.g
    eids = [r[0] for r in g.query(
        SESSION_EVENT_QUERY, params={"sid": session_id}
    ).result_set]
    turn_pattern = _turn_id_pattern(session_id)
    turn_ids = {
        r[0] for r in g.query(
            SESSION_TURN_QUERY, params={"sid": session_id}
        ).result_set
        if r[0] and turn_pattern.match(r[0])
    }
    points: list[dict] = []
    # #2552 (layer-2 WIRE — the structural leg): reified operator Points are
    # stamped with the sessionCaptured eventId by the capture path (sdk.
    # capture_session) so they ENTER the eventId-keyed memory layer — but they
    # are structure, not claims. They are split out here so the claim-level
    # survival/leakage/provenance metrics keep their pinned semantics while
    # the operator surface carries the retrievability assertion
    # (``operator_nodes`` — every operator node, with its eventId).
    operator_nodes: list[dict] = []
    seen: set[str] = set()
    if eids:
        rows = g.query(
            MEMORY_ROW_QUERY, params={"eids": eids}
        ).result_set
        for row in rows:
            point = _row_to_point(row)
            pid = point["point_id"]
            if pid in seen or pid in turn_ids:
                continue
            seen.add(pid)
            if grading.is_turn_echo(point.get("content") or ""):
                continue
            if point.get("is_operator"):
                operator_nodes.append(point)
                continue
            points.append(point)
    # Operator edges among the memory layer (the REPHRASE dedup surface + the
    # raw IMPL/NAND counts the report audits).
    rephrase_edges: list[tuple[str, str]] = []
    operator_counts: dict[str, int] = {}
    direct_edges: list[dict] = []
    if seen:
        edge_rows = g.query(
            OPERATOR_EDGE_QUERY, params={"ids": list(seen)}
        ).result_set
        # #2552: operators are structure, not claims — excluded from the
        # point→point ``direct_edges`` surface (an unfiltered query would
        # admit every point→operator INPUT edge once operators are in
        # ``seen``).
        operator_id_set = {p["point_id"] for p in operator_nodes}
        for etype, a, b in edge_rows:
            operator_counts[etype] = operator_counts.get(etype, 0) + 1
            if etype == "REPHRASE":
                rephrase_edges.append((a, b))
            if a not in operator_id_set and b not in operator_id_set:
                direct_edges.append(
                    {"rel_type": etype, "from_id": a, "to_id": b})
        # #2552: cross-session CORRECTS among this session's source points
        # (deduped against the session-scoped edges above).
        known_edges = {(e["rel_type"], e["from_id"], e["to_id"])
                       for e in direct_edges}
        for etype, a, b in g.query(
                CORRECTS_EDGE_QUERY, params={"ids": list(seen)}).result_set:
            key = (etype, a, b)
            if key in known_edges:
                continue
            known_edges.add(key)
            direct_edges.append({"rel_type": etype, "from_id": a, "to_id": b})
    # #2514: the reified-operator surface (operator nodes + mitigation Points
    # touching this session's memory points) — additive snapshot keys the
    # planted-operator grader consumes (see grading.py).
    operator_edges: list[dict] = []
    mitigations: list[dict] = []
    if seen:
        op_rows = g.query(
            OPERATOR_NODE_EDGE_QUERY, params={"ids": list(seen)}
        ).result_set
        by_op: dict[str, dict] = {}
        for oid, op_type, direction, label, _rtype, pid, idx in op_rows:
            entry = by_op.setdefault(
                oid,
                {"op_id": oid, "op_type": op_type, "direction": direction,
                 "label": label, "endpoints": {}, "source_id": None},
            )
            entry["endpoints"][pid] = idx
        for entry in by_op.values():
            idx0 = [pid for pid, i in entry["endpoints"].items() if i == 0]
            entry["source_id"] = idx0[0] if len(idx0) == 1 else None
            operator_edges.append(entry)
        mit_rows = g.query(
            MITIGATION_QUERY, params={"ids": list(seen)}
        ).result_set
        for oid, mid, content in mit_rows:
            mitigations.append({"op_id": oid, "point_id": mid, "content": content or ""})
    return {
        "points": points,
        "rephrase_edges": rephrase_edges,
        "turn_ids": sorted(turn_ids),
        "operator_counts": operator_counts,
        "direct_edges": direct_edges,
        "operator_edges": operator_edges,
        "mitigations": mitigations,
        # #2552: operators that entered the retrievable memory layer
        # (eventId-stamped) — the structural surface the runner audit
        # asserts on. An operator node ABSENT here on a capture that wrote
        # operators is the pre-fix drop (no eventId → not retrievable).
        "operator_nodes": operator_nodes,
        "operators_total": len(operator_nodes),
        "operators_provenanced": sum(
            1 for p in operator_nodes if p.get("event_id")),
    }


# ── #8444 report-only graph census (E1 Object composition, E3 edge anchors) ──
#
# `#7949` E1 and E3 are the two criteria whose numbers had NO home in a run's
# own artifact (`#8444`): the gated `METRIC_VALUES` vocabulary has no
# object-composition member, `provenance_accuracy` is a Point-level PRESENCE
# test (`bool(eventId) or bool(extractedFrom)`, `_row_to_point`) that never
# reads `sourceVersion`, and `snapshot_session` reads `:Point` only — so no
# receipt could say whether the `^the <X>` Object class shrank or whether a
# derivation edge carried its #5199/#5256 version anchor.
#
# This census is REPORT-ONLY, on the `operator_audit`/`semantic_judge`
# precedent: it is NOT a `METRIC_VALUES` member (that would force a baseline
# re-bless on every existing receipt), it never enters `metrics`, and it moves
# no verdict. It answers two questions off the graph the run itself wrote:
#
#   E1 — how many `:Object`s were written for this session, and what share
#        carry a definite description (`^the <X>`, the pre-registered
#        instrument).
#   E3 — how many derivation edges the capture wrote, and how many carry a
#        non-empty `sourceVersion`. The anchored predicate is the writer's own
#        (`projection/edges.py::_anchor_on_create`: NULL and `''` are both
#        "not anchored" — `''` is the auto-created-Source placeholder and
#        compares equal to a missing hash).
#
# BOUNDS, stated because a census that reports a zero from an instrument that
# cannot see the writer is the `#8501` defect class:
#
#   * Objects are scoped to this session by the `(Point)-[:aboutObject]->
#     (Object)` edge from the session's own memory Points. `:Object` carries
#     no session property, so an Object no surviving Point references is
#     INVISIBLE here — this is a session-scoped census, not a graph-wide one.
#   * An empty denominator reports `None`, never `0.0`: a `0.0` share would
#     read as "the class is gone" when the truth is that the session emitted
#     no Objects to divide by.
#   * A graph error is REPORTED (`{"error": ...}` + a run note), never
#     flattened into zeros.
#
# `objects.with_derivation_link` exists because E3's criterion is "every
# derived node carries its version anchor — `:Object`/`:Event` included, not
# just `Point`s". Whether an Object HAS a derivation link at all is a fact this
# census measures and the Point-level anchor count cannot: an Object reached
# only by `aboutObject` has no link to mark stale when its transcript is
# revised (#7949 E3).

#: `#7949` E1's instrument — the SAME predicate, character for character, that
#: produced the measured figure the epic reports:
#: `docs/evidence/7949-capstone/probes/e1_e2_probe.py::THE_PREFIX`
#: (`re.compile(r"^the\s", re.IGNORECASE)`), which restates the architecture
#: document's rule (§1:85 — a definite description is an Object whose name
#: begins with `the `). There is deliberately ONE definition per side and NO
#: second, subtly stricter copy: an earlier `the\s+\S` here made the census a
#: strict SUBSET of the probe (a name of exactly `"the "` matched the probe and
#: not the census) — two instruments under one name, with E1's whole claim to
#: authority being that there is one. They are pinned identical by
#: `tests/test_graph_census_7949.py`,
#: `test_the_census_uses_the_same_instrument_as_the_e1_probe`.
#:
#: The `^` is carried by `.match` below as well as by the pattern, so an
#: unanchored read — "policy on the rollout" counted as a definite description
#: — needs a visible `.search` change.
#:
#: NOT checkable against a pre-registration artifact: the `PREREG_ARTIFACT` the
#: probe names (`docs/evidence/7949-capstone/preregistration.json`) is ABSENT
#: from the tree, so the probe reports the run as `UNPREREGISTERED` pending
#: `#9075`. "Pre-registered instrument" means the frozen rule the probe
#: implements, not a frozen-thresholds file.
_THE_DEFINITE_DESCRIPTION = re.compile(r"^the\s", re.IGNORECASE)

#: Objects are deduped and re-addressed by their BACKEND id, not by `o.id`:
#: `:Object` always carries an `id` on the write path, but a legacy or
#: connector-minted node may not, and `DISTINCT NULL` would then collapse two
#: real Objects into one row (an undercount that would read as E1 moving).
SESSION_OBJECT_QUERY = (
    "MATCH (p:Point)-[:aboutObject]->(o:Object) WHERE p.id IN $pids "
    "RETURN DISTINCT ID(o), o.name"
)
#: Does the Object carry a link to the artifact it was derived from at all?
#: `extractedFrom` is the #5256 derivation predicate; `references` is the
#: #5199 identity/mention surface (property-free for Objects by the owner's
#: option-A ruling — this query counts the LINK, never its properties).
OBJECT_DERIVATION_LINK_QUERY = (
    "MATCH (o:Object) WHERE ID(o) IN $nids "
    "MATCH (o)-[:extractedFrom|references]->() "
    "RETURN count(DISTINCT ID(o))"
)
#: Anchored/total for one derivation predicate out of this session's nodes.
#: The anchored predicate is the writer's own (`_anchor_on_create`).
DERIVATION_ANCHOR_QUERY = (
    "MATCH (a)-[r:%s]->() WHERE a.id IN $ids "
    "RETURN count(r), sum(CASE WHEN r.sourceVersion IS NULL "
    "OR r.sourceVersion = '' THEN 0 ELSE 1 END)"
)
#: The `references` leg, scoped by the session's `:Source` rather than by an
#: `id`. A session Source is addressed by `url`/`sessionId` and its `id`
#: property is NULL — the shape `(s:Source {sessionId:$sid})-[r:references]->`
#: is the one that finds the edge, and an `a.id IN $ids` filter over the
#: session's Source silently yields 0/0 (a "nothing to anchor" reading of a
#: graph that HAS an anchor — measured on the session-capture path).
#:
#: ⚠️ THE PARTITION IS THE WRITER'S RULE, NOT THE TARGET'S LABEL. The label
#: alone cannot answer "is this edge a derivation":
#:
#:   * a DERIVATION link is written for `_DERIVATION_REFERENCES_LABELS` =
#:     `{Event, Document}`, and D10 remaps `Document` onto `:Source` — so a
#:     DOCUMENT derivation link is stored as `(Source)-[:references]->(Source)`
#:     and it CARRIES `sourceVersion` (`projection/edges.py::link_source_to_entity`
#:     decides `_is_derivation` on the CALLER's label, before the remap).
#:     Excluding every `:Source` target would hide that anchored derivation
#:     edge — the one the hosted lane writes (`hosted_api` →
#:     `link_source_to_entity(…, "Document")`) — from BOTH sides, which is the
#:     `#8501` failure mode one label over: an anchor a later regression could
#:     drop without moving any number.
#:   * an IDENTITY/MENTION link is written for `Object` with NO property at all
#:     (`edges.py:2015` — "this writer sets no property on them at all"), so
#:     counting one as a failed anchor is a FALSE RED on an edge the model
#:     deliberately leaves bare (`entities.py` writes
#:     `(Source)-[:references]->(:Object)` on the connector path).
#:   * REFERENTIAL CONTAINMENT — `(Source)-[:references]->(:Source)` with NO
#:     `sourceVersion`, written by the `hosted_api` session→external MERGE
#:     outside the anchored path — is likewise non-derivation by design.
#:
#: So the derivation population is "every `references` edge EXCEPT those two
#: named, anchor-free classes", each excluded class reported separately and by
#: COUNT (`containment_excluded`, `identity_excluded`) so neither can vanish
#: silently, and any UNKNOWN target label stays IN the population (reported as
#: a failure) instead of dropping out of it. A `:Source` target that carries an
#: anchor is by definition a `Document` derivation and lands in
#: `total`/`anchored`.
#:
#: ⚠️ NAMED RESIDUAL — this partition is correct where the graph can decide, and
#: it CANNOT decide everywhere. The stored edge does not record the relation
#: spelling that made it derivation or containment, so an UNANCHORED
#: `(Source)-[:references]->(:Source)` edge is reported under
#: `containment_excluded` whichever of the two it is. The consequence is
#: one-directional and stated rather than hidden: a regression that DROPS the
#: anchor from a `Document` derivation link moves `anchored` and `total` down
#: by one and `containment_excluded` up by one — visible to a reader, but not
#: counted as an E3 failure. Closing it needs a graph-side discriminator; a
#: target `documentKind` is a CANDIDATE and not an answer (the `Document` stub
#: mint does not guarantee one), so it is not asserted here. Pinned by
#: `test_an_unanchored_source_target_is_reported_as_containment_a_named_residual`.
SESSION_REFERENCES_ANCHOR_QUERY = (
    "MATCH (s:Source {sessionId: $sid})-[r:references]->(t) "
    "RETURN "
    # total: not identity/mention, and not an UNANCHORED Source target.
    "sum(CASE WHEN t:Object THEN 0 WHEN t:Source AND "
    "(r.sourceVersion IS NULL OR r.sourceVersion = '') THEN 0 ELSE 1 END), "
    # anchored: of that same population, the ones carrying the writer's anchor.
    "sum(CASE WHEN t:Object THEN 0 WHEN t:Source AND "
    "(r.sourceVersion IS NULL OR r.sourceVersion = '') THEN 0 "
    "WHEN r.sourceVersion IS NULL OR r.sourceVersion = '' THEN 0 ELSE 1 END), "
    # containment_excluded: Source targets with NO anchor (by design).
    "sum(CASE WHEN t:Source AND "
    "(r.sourceVersion IS NULL OR r.sourceVersion = '') THEN 1 ELSE 0 END), "
    # identity_excluded: Object targets — anchor-free by design.
    "sum(CASE WHEN t:Object THEN 1 ELSE 0 END)"
)


def graph_census(sdk, session_id: str, point_ids: list[str]) -> dict:
    """The report-only graph census for ONE session (#8444, for `#7949`).

    ``point_ids`` is the session's memory layer (claims AND reified operator
    Points — the operator surface is `:Point` too, so an Object it binds is
    the session's Object). The census reads the graph the run wrote; it
    returns

        {"session_id",
         "objects": {"total", "the_prefixed", "the_share",
                     "with_derivation_link"},
         "derivation_edges": {
             "extractedFrom": {"total", "anchored"},
             "references": {"total", "anchored",
                            "containment_excluded", "identity_excluded"}}}

    ``references.total`` is the DERIVATION population only: the two anchor-free,
    non-derivation classes are excluded by the WRITER's rule and reported by
    count (see ``SESSION_REFERENCES_ANCHOR_QUERY``).

    and does not swallow a graph failure: the runner ALWAYS reaches it through
    ``census_or_error`` precisely so that a failure is reported and can never
    be read as a measured zero.
    """
    g = sdk._get_proj().g
    pids = sorted({pid for pid in point_ids if pid})
    object_rows: list[tuple] = []
    if pids:
        object_rows = list(g.query(
            SESSION_OBJECT_QUERY, params={"pids": pids}).result_set)
    names = [name or "" for _oid, name in object_rows]
    the_prefixed = sum(
        1 for name in names if _THE_DEFINITE_DESCRIPTION.match(name))
    node_ids = [nid for nid, _name in object_rows if nid is not None]
    with_derivation_link = 0
    if node_ids:
        rows = g.query(
            OBJECT_DERIVATION_LINK_QUERY,
            params={"nids": node_ids}).result_set
        with_derivation_link = int(rows[0][0] or 0) if rows else 0
    derivation_edges: dict[str, dict[str, int]] = {}
    extracted_rows = g.query(
        DERIVATION_ANCHOR_QUERY % "extractedFrom", params={"ids": pids}
    ).result_set if pids else []
    references_rows = g.query(
        SESSION_REFERENCES_ANCHOR_QUERY, params={"sid": session_id}
    ).result_set
    for rel, rows in (("extractedFrom", extracted_rows),
                      ("references", references_rows)):
        total = anchored = containment = identity = 0
        if rows:
            total, anchored = int(rows[0][0] or 0), int(rows[0][1] or 0)
            if len(rows[0]) > 3:
                containment = int(rows[0][2] or 0)
                identity = int(rows[0][3] or 0)
        derivation_edges[rel] = {
            "total": total, "anchored": anchored,
            **({"containment_excluded": containment,
                "identity_excluded": identity}
               if rel == "references" else {}),
        }
    return {
        "session_id": session_id,
        "objects": {
            "total": len(object_rows),
            "the_prefixed": the_prefixed,
            # A zero denominator is None, never 0.0 (see the block comment).
            "the_share": (the_prefixed / len(object_rows))
            if object_rows else None,
            "with_derivation_link": with_derivation_link,
        },
        "derivation_edges": derivation_edges,
    }


def census_or_error(sdk, session_id: str, point_ids: list[str]) -> dict:
    """``graph_census`` with the graph error REPORTED instead of raised.

    Fail-closed in the direction that matters: an unreadable graph yields
    ``{"session_id", "error": "<type>: <message>"}`` — never a census of
    zeros, which would be indistinguishable from a measured empty session.
    """
    try:
        return graph_census(sdk, session_id, point_ids)
    except Exception as exc:  # reported, never swallowed
        return {"session_id": session_id,
                "error": f"{type(exc).__name__}: {exc}"}


def aggregate_graph_census(per_session: dict[str, dict]) -> dict:
    """Sum the per-session censuses; recompute the share from the sums.

    A ratio of sums, never a mean of shares: sessions emit different Object
    counts, so averaging their shares would weight a one-Object session the
    same as a twenty-Object one. Sessions that reported an error contribute
    nothing and are NAMED, so a partial aggregate can never be read as whole.
    """
    objects_total = objects_the = objects_linked = 0
    edges = {rel: {"total": 0, "anchored": 0}
             for rel in ("extractedFrom", "references")}
    errored = sorted(
        sid for sid, census in per_session.items() if "error" in census)
    sessions_with_objects = 0
    for census in per_session.values():
        obj = census.get("objects") or {}
        objects_total += int(obj.get("total") or 0)
        objects_the += int(obj.get("the_prefixed") or 0)
        objects_linked += int(obj.get("with_derivation_link") or 0)
        if obj.get("total"):
            sessions_with_objects += 1
        for rel, counts in (census.get("derivation_edges") or {}).items():
            if rel not in edges:
                continue
            for key, value in counts.items():
                # EVERY per-session counter is summed, not just total/anchored:
                # the excluded populations (`containment_excluded`,
                # `identity_excluded`) are part of the measurement, and an
                # aggregate that dropped them would leave the corpus-level
                # figure — the one the receipt carries — silent about exactly
                # the edges the census chose not to judge.
                edges[rel][key] = edges[rel].get(key, 0) + int(value or 0)
    return {
        "sessions": len(per_session),
        "sessions_with_objects": sessions_with_objects,
        "sessions_errored": errored,
        "objects": {
            "total": objects_total,
            "the_prefixed": objects_the,
            "the_share": (objects_the / objects_total)
            if objects_total else None,
            "with_derivation_link": objects_linked,
        },
        "derivation_edges": edges,
    }


def build_graph_census(sdk, surfaces_by_session: dict[str, dict],
                       snapshot_errors: dict[str, str] | None = None) -> dict:
    """The report-level census: per session plus one aggregate (#8444).

    ``surfaces_by_session`` is the runner's ``snapshot_session`` output per
    session (post-dream), so the census reads exactly the graph the graded
    numbers were taken from. An unreadable session is carried as an ``error``
    entry, never dropped and never zeroed.

    ``snapshot_errors`` maps a session id to the failure that stopped the
    runner reading its snapshot at all. Such a session has NO surface to
    census: the runner substitutes an empty stand-in so the rest of the run can
    continue, and a census over that stand-in would report `objects.total = 0`,
    `the_share = None` and `0/0` edges — a MEASURED-EMPTY entry with no
    ``error`` key, because ``census_or_error`` guards the GRAPH read and this
    failure is one level up. So the fact is carried into the census instead of
    being re-derived from a zeroed snapshot, and the run note names the session
    like any other unreadable one.
    """
    snapshot_errors = dict(snapshot_errors or {})
    per_session: dict[str, dict] = {}
    for sid, snap in surfaces_by_session.items():
        if sid in snapshot_errors:
            per_session[sid] = {
                "session_id": sid,
                "error": f"snapshot raised: {snapshot_errors[sid]}",
            }
            continue
        per_session[sid] = census_or_error(
            sdk, sid,
            [p["point_id"]
             for p in snap.get("points", []) + snap.get("operator_nodes", [])],
        )
    return {
        "sessions": per_session,
        "aggregate": aggregate_graph_census(per_session),
    }


def graph_census_notes(census: dict | None) -> list[str]:
    """The census note(s) a run report carries; ``[]`` when every session read.

    The m2/llm asymmetry the operator audit has does NOT apply here: the census
    counts what the write path wrote on either lane, so it needs no posture
    caveat. What it does need is the FAILURE note — a receipt whose E1/E3 rows
    are silently absent because a graph read failed must say so, or a reader
    takes an unmeasured session for a measured empty one.
    """
    if not census:
        return []
    aggregate = census.get("aggregate") or {}
    errored = list(aggregate.get("sessions_errored") or [])
    if not errored:
        return []
    details = "; ".join(
        f"{sid}: {(census.get('sessions') or {}).get(sid, {}).get('error')}"
        for sid in errored)
    return [
        "graph census unavailable for "
        f"{len(errored)} of {aggregate.get('sessions')} session(s) — E1/E3 are "
        "NOT answerable from this receipt for them (reported, never zeroed): "
        f"{details}"
    ]


# ── Additive banded semantic judge arm (issue #5085) ───────────────────────


def _semantic_memory_contents(points: list[dict]) -> list[str]:
    """The graded memory layer as judge-visible content strings.

    Identical surface to the one the mechanical arm grades (the snapshot's
    memory points, turn echo and operator nodes already excluded).
    """
    return [
        p["content"]
        for p in points
        if isinstance(p.get("content"), str) and p["content"].strip()
    ]


def _model_id_family(model_id: str) -> str:
    """Normalise a wire model id for the judge/extractor independence check.

    The extractor's ``TORTOISE_EXTRACT_MODEL`` may name the direct lane
    (``deepseek-v4-flash``) while a judge adapter carries the OpenRouter id
    (``upstage/solar-pro4``) — only the final path segment identifies the
    model, so comparisons run on that, lowercased.  Comparing a MODELS
    registry KEY to a wire id instead is a namespace mismatch that can
    assert an independence that does not hold.
    """
    return model_id.rsplit("/", 1)[-1].strip().lower()


def _run_semantic_judge(
    sdk,
    selected: list[str],
    root: Path,
    *,
    samples: int,
    judge_model: str | None = None,
    paraphrase_model: str | None = None,
    temperature: float = judge.DEFAULT_JUDGE_TEMPERATURE,
    judge_factory=None,
    notes: list[str] | None = None,
    prescreen: str = prescreen_mod.PRESCREEN_OFF,
    prescreen_model: str | None = None,
) -> dict:
    """Run the ADDITIVE blind banded judge over every session's memory layer.

    Returns the ``semantic_judge`` block recorded in the run report + receipt.
    The mechanical metrics are untouched: this arm publishes BESIDE them (the
    #5085 D14 "two-number split"), never inside ``metrics``, so the baseline
    comparability surface is unchanged.

    Raises ``judge.JudgeBlindnessError`` — the caller converts it into a
    runner error, because judge-blindness is load-bearing (plan §J4: a
    violation is a HARNESS failure, never a soft note).  Any other judge
    exception is the caller's to capture into ``status: "failed"`` so an LLM
    outage cannot invalidate the authoritative mechanical numbers.
    """
    notes = notes if notes is not None else []
    model_name = judge_model or judge.DEFAULT_JUDGE_MODEL
    para_name = paraphrase_model or model_name
    # #5106: the deterministic first stage.  ``off`` (the default) builds no
    # stage at all, so the arm — and its receipt block — is exactly the #5085
    # shape.  The stage is consulted INSIDE ``judge_units`` (after the
    # blindness guard, over the same probe surface) and can only skip
    # confident work; it can never drop a unit or fail the arm closed.
    screen = prescreen_mod.build_prescreen(
        prescreen,
        **({"model": prescreen_model} if prescreen_model else {}),
    )
    units: list[dict] = []
    memory_by_session: dict[str, list[str]] = {}
    cost_usd = 0.0
    priced_calls = 0
    judge_calls = 0
    paraphrase_calls = 0
    leak_retries = 0
    leak_rejections = 0
    logprob_samples: list[float] = []
    judge_model_id = ""
    prescreen_totals = {
        "units_screened": 0,
        "screened_same_fact": 0,
        "screened_likely_not": 0,
        "screen_errors": 0,
        "probe_chars_removed": 0,
        "sessions_fully_screened": 0,
        "judge_calls_avoided": 0,
    }
    for session_id in selected:
        gold = corpus.load_gold(session_id, root)
        fixture = corpus.load_fixture(session_id, root)
        snapshot = snapshot_session(sdk, session_id)
        memory = _semantic_memory_contents(snapshot.get("points", []))
        memory_by_session[session_id] = memory
        anchors = [
            u.get("survival", {}).get("via_anchor")
            or u.get("verbatim_anchor")
            or ""
            for u in gold.get("salient_units", [])
            if isinstance(u, dict)
        ]
        arm = judge.BandedSalienceJudge(
            model_name=model_name,
            model_factory=judge_factory,
            paraphrase_model_name=para_name,
            samples=samples,
            temperature=temperature,
            anchors=[a for a in anchors if a],
        )
        probes = arm.synthesize_probes(gold, fixture)
        verdicts = arm.judge_units(probes, memory, prescreen=screen)
        per_session_screen = arm.prescreen_audit()
        for unit_id, record in verdicts.items():
            units.append(
                {
                    "session_id": session_id,
                    "unit_id": unit_id,
                    "probe": probes.get(unit_id),
                    **record,
                }
            )
        cost_usd += arm.cost_usd
        priced_calls += arm.cost_priced_calls
        judge_calls += arm.judge_call_count
        paraphrase_calls += arm.paraphrase_call_count
        leak_retries += arm.leak_retries_used
        leak_rejections += arm.leak_rejections
        logprob_samples.extend(arm.logprob_samples)
        judge_model_id = arm.judge_model_id
        if per_session_screen is not None:
            prescreen_totals["units_screened"] += per_session_screen["units_screened"]
            prescreen_totals["screened_same_fact"] += per_session_screen["screened_same_fact"]
            prescreen_totals["screened_likely_not"] += per_session_screen["screened_likely_not"]
            prescreen_totals["screen_errors"] += per_session_screen["screen_errors"]
            prescreen_totals["probe_chars_removed"] += per_session_screen["probe_chars_removed"]
            prescreen_totals["sessions_fully_screened"] += per_session_screen["sessions_fully_screened"]
            prescreen_totals["judge_calls_avoided"] += per_session_screen["judge_calls_avoided"]
    # Two DIFFERENT error counters must both reach the receipt.  The stage's own
    # ``audit()["errors"]`` catches failures the stage swallowed internally
    # (both built-in stages fail OPEN inside ``screen()``); ``prescreen_errors``
    # catches an exception that escaped a foreign stage.  Reporting only the
    # latter read 0 on a run where the stage errored on EVERY unit.
    screen_stage_audit = screen.audit() if screen is not None else None
    screen_errors_total = prescreen_totals["screen_errors"] + int(
        (screen_stage_audit or {}).get("errors") or 0
    )
    aggregate = judge.aggregate_banded(units)
    # Self-preference bias (documented): the judge must not grade a model's
    # own output.  Record the extractor model the run used, and state the
    # independence honestly — including residual family overlap, if any.
    extractor_model = (
        os.environ.get("TORTOISE_EXTRACT_MODEL", "").strip()
        or "deepseek/deepseek-v4-flash"
    )
    # The comparison must be wire-id-vs-wire-id (see _model_id_family): the
    # judge adapter's resolved id vs the extractor's resolved id.
    independent = _model_id_family(judge_model_id) != _model_id_family(extractor_model)
    independence = (
        "judge model differs from the extractor's resolved model — the "
        "default judge (upstage/solar-pro4) is a different provider family "
        "from the deepseek extractor default; residual overlap is a shared "
        "vendor only if an operator overrides --judge-model"
        if independent
        else "WARNING: judge model == extractor model — self-preference bias "
             "is NOT mitigated on this run"
    )
    if (judge_calls + paraphrase_calls) and not priced_calls:
        notes.append(
            "semantic judge cost unavailable: the serving route reported no "
            "charge (last_cost_usd is None) — recorded as 0.0, never a "
            "fabricated figure"
        )
    notes.append(
        "semantic judge (additive, NOT a gated metric): "
        f"band-weighted={aggregate['band_weighted_score']} "
        f"probability_mean={aggregate['probability_mean']} "
        f"survival>=0.50={aggregate['semantic_survival_rate']} "
        f"dist={aggregate['band_distribution']}"
    )
    return {
        "status": "completed",
        "pin": (
            judge.SEMANTIC_JUDGE_PIN if screen is None
            else judge.SEMANTIC_JUDGE_PIN_PRESCREEN
        ),
        "protocol": judge.SEMANTIC_PROMPT_VERSION,
        "model": model_name,
        "model_id": judge_model_id,
        "paraphrase_model": para_name,
        "extractor_model": extractor_model,
        "model_independence": independence,
        "temperature": temperature,
        "samples": samples,
        "orders": list(judge.PROMPT_ORDERS),
        "band_thresholds": {
            band: lower for lower, band in judge.BAND_THRESHOLDS
        },
        "band_weights": dict(judge.BAND_WEIGHTS),
        "units": units,
        "memory_by_session": memory_by_session,
        "logprobs": judge.logprob_crosscheck_from_samples(logprob_samples),
        "cost_usd": round(cost_usd, 6),
        "cost_priced_calls": priced_calls,
        # ``judge_calls`` counts VERDICT calls only (the paraphrase stage's
        # calls are ``paraphrase_calls``); ``prompt_count`` would overstate it.
        "judge_calls": judge_calls,
        "paraphrase_calls": paraphrase_calls,
        # Blindness audit: a paraphrase that echoed an anchor was rejected and
        # re-synthesized this many times (0 on a clean run); a leak that
        # survives the bounded retries raises and fails the run instead.
        "paraphrase_leak_retries": leak_retries,
        "paraphrase_leak_rejections": leak_rejections,
        # #5106: present ONLY when the pre-screen ran — a default (screen-off)
        # receipt block is byte-identical to the #5085 shape.  ``abstained``,
        # ``unavailable`` and ``errors`` are the fail-open audit: an abstain is
        # a unit the LLM judge still decided.
        **({"prescreen": {
            "name": prescreen,
            **prescreen_totals,
            # The SUM of the stage's internal errors and the arm's escape
            # counter — see ``screen_errors_total`` above.
            "screen_errors": screen_errors_total,
            "units_total": len(units),
            "abstained": len(units) - prescreen_totals["units_screened"],
            "experimental": True,
            "note": (
                "deterministic first-stage pre-screen (off by default). It "
                "only SKIPS the confident ends — same_fact requires an "
                "entailment verdict AND the claim-critical guard; likely_not "
                "requires a CONTRADICTION verdict, never merely the absence "
                "of entailment. Any abstain/error/unavailable result goes to "
                "the LLM judge (fail-open): no unit is dropped. No dependency "
                "was added — the NLI stage reuses the already-declared "
                "embeddings extra and reports unavailable when absent."
            ),
            "stage": screen_stage_audit,
        }} if screen is not None else {}),
        **aggregate,
    }


def _validate_semantic_units(units: object, issues: list[str]) -> None:
    """Per-unit consistency for the additive ``semantic_judge`` block (#5085).

    The band is DERIVED from the probability (``aggregate_banded`` writes it
    back), so a receipt whose stored band disagrees with the banding function
    is self-inconsistent — exactly the band-mapping drift the two-number
    split must not publish.
    """
    if not isinstance(units, list):
        return
    labelled = "receipt.semantic_judge.units"
    for index, unit in enumerate(units):
        where = f"{labelled}[{index}]"
        if not isinstance(unit, dict):
            issues.append(f"{where}: expected an object, got {unit!r}")
            continue
        probability = unit.get("probability")
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not (0.0 <= float(probability) <= 1.0)
        ):
            issues.append(
                f"{where}.probability: expected a fraction in [0, 1], "
                f"got {probability!r}"
            )
            continue
        expected_band = judge.band_for_probability(float(probability))
        if unit.get("band") != expected_band:
            issues.append(
                f"{where}.band: {unit.get('band')!r} disagrees with "
                f"band_for_probability({probability}) = {expected_band!r}"
            )
        votes_yes, votes_total = unit.get("votes_yes"), unit.get("votes_total")
        if (
            isinstance(votes_yes, int)
            and not isinstance(votes_yes, bool)
            and isinstance(votes_total, int)
            and not isinstance(votes_total, bool)
            and (votes_yes < 0 or votes_total < 0 or votes_yes > votes_total)
        ):
            issues.append(
                f"{where}.votes: {votes_yes}/{votes_total} is not a valid "
                "vote tally"
            )




def extractor_identity() -> dict:
    """Which extractor the run actually used — the #9077 anti-monkeypatch leg.

    The module docstring of ``test_write_path_operator_lane`` states the limit
    that makes this necessary: that lane **monkeypatches ``extract_session_v2``**
    with a gold-derived emission, so its 15/15 grades the WIRE and says nothing
    about whether the extractor FORMS the edges. A receipt produced that way
    would publish a wire result as a product number.

    ``resolved_config.extractor_posture`` cannot tell the difference — a patched
    run is still ``llm``. The identity below can: ``sdk._extract_session_v2``
    imports the symbol from ``tortoise.extractor_v2`` at CALL time, so the
    module attribute IS the object that ran. ``real`` is true only for the
    shipped function, and ``tools/operator_receipt_check.py`` rejects a receipt
    whose ``real`` is false or absent.
    """
    import inspect

    try:
        import tortoise.extractor_v2 as ev2
    except Exception as exc:  # pragma: no cover - import-time failure
        return {"module": None, "qualname": None, "source_file": None,
                "real": False, "error": f"{type(exc).__name__}: {exc}"}
    fn = getattr(ev2, "extract_session_v2", None)
    module = getattr(fn, "__module__", None)
    qualname = getattr(fn, "__qualname__", None)
    try:
        source_file = inspect.getsourcefile(fn) if fn is not None else None
    except (TypeError, OSError):  # pragma: no cover - closures/frozen modules
        source_file = None
    return {
        "module": module,
        "qualname": qualname,
        "source_file": source_file,
        # The shipped function lives in ``tortoise/extractor_v2.py`` and is a
        # module-level def; a test seam is a closure or a wrapper defined
        # elsewhere, so BOTH checks must hold.
        "real": bool(
            module == "tortoise.extractor_v2"
            and qualname == "extract_session_v2"
            and (source_file or "").endswith("tortoise/extractor_v2.py")
        ),
    }


def operator_drop_notes(drops: dict, posture: str) -> list[str]:
    """The drop-attribution note(s) for a run (#8503).

    ⛔ THE OPERATOR SENTENCE IS CONDITIONAL ON THE OPERATOR COUNT, ALWAYS. A
    report with drops AND supersede refusals reached a "logged no operator
    drop" branch in an earlier cut (review round 5), so the wording is built
    from the two counts rather than from which branch was entered. Pure (no
    graph, no env) so every branch is unit-testable.

    ⛔ AND ON THE LANE, because the count is only a measurement where the pass
    RUNS. Every marker the classifier looks for is emitted by
    ``tortoise/extractor_v2.py``, and the ``m2`` posture routes capture to the
    M2 extractor, which never runs the product operator pass — so on that lane
    the count is zero BY CONSTRUCTION and "an edge_missing is not an
    EXTRACTOR-side drop" is an inference the measurement cannot make. Printing
    it beside ``operator_audit_notes``' own m2 caveat ("no product relation
    extraction … never a bar") shipped one receipt carrying two notes that
    disagreed about what its zero meant (code-review round 7).
    """
    operator_drops = int(drops.get("operator_drops") or 0)
    refusals = int(drops.get("supersede_refusals") or 0)
    if posture == "m2" and not operator_drops and not refusals:
        return [
            "operator drops (#8503): NOT MEASURED on the m2 echo lane — its "
            "capture routes to the M2 extractor, which never runs the "
            "product operator pass, so this zero (0 drops, 0 refusals) is "
            "structural and says NOTHING about this receipt's edge_missing "
            "rows; the m2 caveat below is the bound that applies"
        ]
    if not operator_drops and not refusals:
        return [
            "operator drops (#8503): the EXTRACTOR logged no operator drop and "
            "no supersede refusal in any session — so an edge_missing verdict "
            "in this receipt is not an EXTRACTOR-side drop. ⛔ This does NOT "
            "cover two seams: the commit layer (apply_payload_operators logs "
            "its skips through _logger, with no warnings sink on the capture); "
            "and a session whose snapshot failed to read (it emits no warnings "
            "at all; graph_census's per-session error names those). Both are "
            "INVISIBLE to this block, which is a named blind spot, not a clean "
            "bill. (#9906 closed the third this note used to name: a "
            "point-level `supersedes` ref on a point that FOLDS — NOOP, MERGE "
            "or a same-session duplicate — is now carried onto the surviving "
            "point and reaches the resolver, so its refusals are counted here "
            "like any emitted point's.)"
        ]
    if not refusals:
        # ⛔ THE DROPS-ONLY CASE IS THE ONE THIS NOTE EXISTS FOR: returning []
        # here shipped a receipt with real operator drops and no drop note at
        # all (review round 6).
        return [
            f"operator drops (#8503): the operator pass logged "
            f"{operator_drops} drop warning(s) — an edge_missing may be a drop "
            "at the operator pass rather than the model never forming the "
            "edge; every line is carried in `extraction_warnings`"
        ]
    operator_clause = (
        "the operator pass logged no drop, but"
        if not operator_drops
        else f"the operator pass logged {operator_drops} drop warning(s), and")
    return [
        f"operator drops (#8503): {operator_clause} the replace/retraction "
        f"channel logged {refusals} refusal(s) — a refused replace or "
        "retraction record is an extractor-side cause of a missing edge too, "
        "so an edge_missing on a SUPERSEDE row may be the resolver rather than "
        "the model"
    ]


def operator_audit_notes(audit: dict | None, posture: str) -> list[str]:
    """The operator-audit note(s) for a run report (#2514/#2552/#4807/#8503).

    The m2-echo-lane caveat — "the m2 echo lane has no product relation
    extraction, so its edge score is not comparable with the llm lane's and is
    never a bar" — is **posture-scoped**. The m2 lane's relation stage is a
    cue-word heuristic, not the product extractor, so its edge score is not
    comparable with the llm lane's; on the llm lane the score IS a genuine
    behavioural signal about emission fidelity, and printing the m2 lane's
    excuse verbatim in that receipt frames a real result as a non-result in the
    very artifact a reader consults.

    #4807: the caveat is DERIVED, never a constant. Its numerator is
    interpolated (``{audit['edge_correct']}/{audit['planted']}``), so the prose
    must stay true for ANY value — a lane grading 2/15 must not sit beside a
    hardcoded "0" (the pre-fix text read "2/15 ... so 0 is structural there",
    a self-contradiction three words apart). The mechanism is likewise the
    **product** extraction the lane lacks (``baselines/m2.json``'s own
    justification wording), never "no relation extraction": the m2 lane's
    cue-word stage DOES emit IMPL/NAND edges, and the edges it grades correct
    are SUPPORTS edges.

    The caveat is emitted ONLY when ``posture == "m2"``; any other value
    (including a future lane) takes the llm-shaped note, whereas the
    pre-refactor code emitted the caveat on every lane. The persistence
    assertion (operators provenanced) rides BOTH lanes and is emitted whenever
    an audit is passed. Pure (no graph, no env) so the posture gate is
    unit-testable without a bench run.
    """
    notes: list[str] = []
    if audit is None:
        return notes
    # ``audit["planted"]`` rather than ``.get`` deliberately preserves the
    # pre-refactor fail-loud behaviour: an audit missing the key must not
    # silently drop the edge note from a receipt whose job is honesty about
    # the audit.
    if audit["planted"]:
        # The m2 clause names the mechanism the lane LACKS (product relation
        # extraction — ``baselines/m2.json``'s wording) and refuses to compare
        # its edge score with the llm lane's. It asserts no constant, so it
        # stays true beside any interpolated numerator (#4807). The separator
        # `); ` and the terminating `.` are load-bearing: the note must read as
        # prose on both lanes. The committed m2 receipt keeps the OLD text; no
        # test reads the receipts and no baseline carries `notes`, so this
        # prose change reaches no hashed surface (#4807 verification).
        tail = (
            "; the m2 echo lane has no product relation extraction, so its "
            "edge score is not comparable with the llm lane's and is never a "
            "bar."
            if posture == "m2" else "."
        )
        notes.append(
            f"operator-edge audit (#2514): {audit['edge_correct']}/"
            f"{audit['planted']} planted operator edges graded edge_correct "
            f"(audit dimension only — not yet a gated metric){tail} "
            "#2552: endpoint anchors + mitigation reasons grade verbatim-first "
            "with the #2405-style paraphrase band — a correctly wired edge "
            "whose endpoint claim was distilled still grades edge_correct"
        )
    notes.append(
        "operator persistence (#2552): "
        f"{audit['operators_provenanced']}/{audit['operators_total']} "
        "reified operator Points entered the retrievable memory layer "
        "(eventId-stamped) — a lower numerator is the structural drop the "
        "layer-2 WIRE fix closed"
    )
    return notes


# #8503: the extractor's operator/edge DROP warnings. An ``edge_missing``
# verdict (both endpoints present, no qualifying edge) has two possible
# mechanisms and they need different fixes: the model never FORMED the edge
# (selection) or the seam DROPPED an edge the model did emit (a write-path
# bug). The receipts carried no extraction warnings at all, so a reader could
# not tell them apart — which is exactly the ambiguity #8503 was filed on.
#
# TWO classes, counted separately, because they answer different questions
# (a first cut merged them and attributed five ENTITY-lifecycle notices as
# "operator drops" — the exact false attribution this block exists to
# prevent):
#   * OPERATOR drops — an operator the model emitted did not reach the graph
#     (unknown op_type, an unresolved src/dst, an unminted entity endpoint, a
#     MITIGATES whose declared target IMPL is absent).
#   * SUPERSEDE refusals — the point/entity replace channel dropped a record
#     (a `supersedes` ref that resolved to nothing, an entity lifecycle the
#     Layer-1 payload cannot express). This is the #8503 `SUPERSEDE` row's
#     mechanism, and it is NOT an operator drop: the operator pass never ran.
# Everything else the extractor warns about (value spans, slot binding,
# search_keys) is neither.
# ⛔ The count is of WARNING LINES, not of operators: one operator whose src
# names an emitted ENTITY logs both `endpoint NOT minted` (the mint refusal)
# and `operator dropped: src/dst did not resolve` (the pass consuming it), and
# a malformed entry never reaches the pass at all. Both of those are drops and
# both markers are needed — a classifier that recognised only the pass's line
# reported `operator_drops == 0` on a run where a non-dict operator entry had
# been skipped (code-review round 3) — but the column must never be read as an
# operator count.
_OPERATOR_DROP_MARKERS = (
    "operator dropped:",
    "operator with unknown op_type",
    "non-dict operator entry",
    "endpoint NOT minted",
    "MITIGATES target edge not emitted",
)
# ⛔ Refusal TAILS, never the `point supersedes=` PREFIX: the recall leg's own
# SUCCESS warning used to share that prefix, so the prefix form counted a
# resolution as a refusal — the same false-attribution class this block exists
# to prevent (code-review finding, #8503). A success line names no refusal.
# ⛔ AND THE FORM MUST BE THE REFUSING ONE: "is not expressible in the
# Layer-1 payload" also matches the `lifecycle='changed'` notice, which drops
# NO supersedes ref (a changed entity's record is still appended), so the
# marker is the superseded-no-ref form only — otherwise the replace channel
# reads ~4x as refusing as it is (code-review round 2, #8503).
_SUPERSEDE_REFUSAL_MARKERS = (
    "matches no S3 prior",
    "is the point itself",
    "supersedes itself",
    # ⛔ NOT the bare "is ambiguous": `entity '<x>' kind '<y>' is ambiguous
    # across namespaces … created with the explicit kind` REFUSES NOTHING (the
    # entity is created), so the bare word reported a replace-channel drop for a
    # successful write (review round 6). Only the resolver's own ambiguous forms.
    # every resolver ambiguity form is `is ambiguous (<detail>)` — the entity
    # name-collision notice is `is ambiguous across namespaces`, which refuses
    # nothing (the entity IS created), so the parenthesised form is the
    # discriminator. Both resolver branches (direction unclear, N priors) and
    # the retraction branch are covered by it.
    "is ambiguous (",
    "matches no S3 prior or same-session point",
    # the retraction branch's own two refusals: a retraction record that is
    # never created is the same extractor-side drop class, and the no-match
    # text above already counts one of its forms — counting two of four is
    # worse than counting none (review round 7).
    "non-dict retraction ref",
    "empty retraction ref",
    "does not resolve to an S3 search result",
    "lifecycle=superseded with no supersedes ref",
    # #9906: the fold-carry's one "cannot carry" form — a ``supersedes`` ref on
    # a point that folded (NOOP/MERGE/duplicate) onto a survivor with no graph
    # id. The ref is NAMED there precisely so the receipt cannot report a false
    # zero; without this marker the warning exists and the count does not see it.
    "with no surviving point id",
)


def operator_drop_warnings(session_results: list[dict]) -> dict:
    """Attribute ``edge_missing``: which sessions had a seam-level drop (#8503).

    Returns ``{"total": int, "operator_drops": int, "supersede_refusals":
    int, "by_session": {sid: [warning, ...]}}``.

    ⛔ ``operator_drops == 0`` means the EXTRACTOR logged no drop — it does
    NOT mean the write path dropped nothing. TWO seams are outside this
    instrument's reach, and the run note names both: the commit layer
    (`commit_ops.apply_payload_operators`) logs its skips through
    ``_logger`` only, with no warnings sink on the capture; and a snapshot that
    never read a session produces no warnings at all (``graph_census``'s
    per-session ``error`` names the ones that did not).
    So a zero is evidence about the operator pass and about nothing else.
    (This docstring counted THREE while ``operator_drop_notes`` enumerated
    three — code-review round 8; the note is the copy a receipt reader sees, so
    the docstring was the stale half. #9906 closed the third: a point-level
    ``supersedes`` ref on a folding point is now CARRIED to the survivor, so it
    reaches the resolver like any other ref and a refusal there is counted.)
    """
    by_session: dict[str, list[str]] = {}
    total = operator_drops = supersede_refusals = 0
    for result in session_results:
        sid = result.get("session_id")
        hits: list[str] = []
        for w in result.get("extraction_warnings") or []:
            if not isinstance(w, str):
                continue
            if any(marker in w for marker in _OPERATOR_DROP_MARKERS):
                operator_drops += 1
            elif any(marker in w for marker in _SUPERSEDE_REFUSAL_MARKERS):
                supersede_refusals += 1
            else:
                continue
            hits.append(w)
        if hits:
            by_session[sid] = hits
            total += len(hits)
    return {"total": total, "operator_drops": operator_drops,
            "supersede_refusals": supersede_refusals,
            "by_session": by_session}




def _failed_report(
    run_id: str,
    date: str,
    commit: str,
    fixtures_hash: str,
    resolved_config: dict,
    *,
    origin: str,
    detail: str,
    log: list[str] | None = None,
    session_results: list[dict] | None = None,
) -> dict:
    log = log or []
    log.append(f"failed: {detail}")
    return {
        "run_id": run_id,
        "date": date,
        "run_status": "failed",
        "verdict": schema.VERDICT_INCONCLUSIVE,
        "failure_origin": origin,
        "commit": commit,
        "corpus_hash": fixtures_hash,
        "judge_pin": None,
        "resolved_config": resolved_config,
        "cost_usd": 0.0,
        "metrics": {},
        "session_results": session_results or [],
        "notes": [],
        "log": log,
    }


#: How many failure items the ``detail`` summary carries.  The remainder is
#: carried in full by the run log, so no cause depends on fitting this bound.
SUMMARY_BOUND = 8
















# ── Receipt build + validation (§6.6) ───────────────────────────────────────


def build_receipt(report: dict, *, justification: str | None = None) -> dict:
    """The §6.6 receipt for a run report (additive detail beyond §6.6 keys).

    The receipt is the audit record the epic's J4/J7 read: run_status,
    verdict, failure_origin, exact commit, corpus hash, judge pin, resolved
    config, cost — plus the metrics snapshot and the per-session results
    (unit-level detail) an auditor needs to reproduce the number.
    """
    receipt = {
        "receipt_version": 1,
        "run_id": report.get("run_id"),
        "date": report.get("date"),
        "run_status": report.get("run_status"),
        "verdict": report.get("verdict"),
        "failure_origin": report.get("failure_origin"),
        "commit": report.get("commit"),
        "corpus_hash": report.get("corpus_hash"),
        "judge_pin": report.get("judge_pin"),
        "resolved_config": report.get("resolved_config"),
        "cost_usd": report.get("cost_usd"),
        "metrics": report.get("metrics"),
        "quote_spans_total": report.get("quote_spans_total"),
        "justification": justification,
    }
    detail = {
        "session_results": [
            {
                "session_id": r["session_id"],
                "emitted": r["emitted"],
                "macro": {
                    "survived": r["macro"]["survived"],
                    "total": r["macro"]["total"],
                },
                "strict": {
                    "survived": r["strict"]["survived"],
                    "total": r["strict"]["total"],
                },
                "leaked": r["leaked"],
                "quotes": {
                    "grounded": r["quotes"]["grounded"],
                    "total": r["quotes"]["total"],
                },
                "provenance": r["provenance"],
                "memory_points": r["memory_point_count"],
                "operator_counts": r.get("operator_counts", {}),
                # #8503: the extractor's own warnings, so the operator verdicts
                # are attributable from the receipt alone.
                "extraction_warnings": list(
                    r.get("extraction_warnings") or []),
                "planted_operators": [
                    {k: d.get(k) for k in ("id", "expected_kind", "verdict",
                                          "edge_correct", "forms_found")}
                    for d in r.get("planted_operators", []) if isinstance(d, dict)
                ],
                "failed_units": [
                    {"id": uid, "failure": d["failure"]}
                    for uid, d in r.get("unit_detail", {}).items()
                    if d.get("failure")
                ],
            }
            for r in report.get("session_results", [])
        ],
        "notes": report.get("notes", []),
        "log": report.get("log", []),
    }
    receipt["session_results"] = detail["session_results"]
    receipt["notes"] = detail["notes"]
    receipt["log"] = detail["log"]
    # #2514 additive operator-edge audit (a completed-run dimension only).
    audit = report.get("operator_audit")
    if audit is not None:
        receipt["operator_audit"] = {
            "planted": audit.get("planted"),
            "edge_correct": audit.get("edge_correct"),
            "content_ok": audit.get("content_ok"),
            # #2552: operator persistence — operators that entered the
            # retrievable memory layer (eventId-stamped).
            "operators_total": audit.get("operators_total"),
            "operators_provenanced": audit.get("operators_provenanced"),
            # #8503: the drop attribution — `total == 0` is a measurement that
            # no operator was dropped at the seam, which is what turns an
            # `edge_missing` verdict into a selection finding instead of an
            # unexplained miss.
            # ⛔ OMIT the block when the audit carries none: defaulting to
            # zeros would make a receipt that measured nothing assert "0 drops"
            # — the fail-open shape this block exists to prevent.
            **({"drop_warnings": audit["drop_warnings"]}
               if audit.get("drop_warnings") is not None else {}),
            # #9077 (E4): the PER-EDGE evidence the epic's closing rule asks
            # for — every planted edge either wired+persisted or carrying a
            # named cause, each wired edge carrying its pre/post-commit
            # endpoint refs. A numerator alone restates the structural lane and
            # is exactly the gap the issue exists to close.
            "results": {
                eid: {
                    "id": d.get("id"),
                    "expected_kind": d.get("expected_kind"),
                    "verdict": d.get("verdict"),
                    "edge_correct": d.get("edge_correct"),
                    "cause": d.get("cause"),
                    "forms_found": d.get("forms_found") or [],
                    "edge_op_ids": d.get("edge_op_ids") or [],
                    "endpoints": d.get("endpoints") or {"from": [], "to": []},
                    "persisted": d.get("persisted"),
                    "from_session": d.get("from_session"),
                    "to_session": d.get("to_session"),
                    "owner_session": d.get("owner_session"),
                }
                for eid, d in (audit.get("results") or {}).items()
                if isinstance(d, dict)
            },
        }
    # #8444 additive graph census (a completed-run dimension only): the E1
    # object-composition and E3 edge-anchor numbers a `#7949` row reads. NOT a
    # gated metric — no baseline re-bless, no verdict movement.
    census = report.get("graph_census")
    if census is not None:
        receipt["graph_census"] = census
    # #9077 (E4): the extractor that actually ran. ``extractor_posture`` is
    # lane-level (llm | m2) and a monkeypatched seam reads as ``llm`` too, so
    # the identity is what lets the receipt check reject a wire-only number.
    if report.get("extractor_identity") is not None:
        receipt["extractor"] = report["extractor_identity"]
    # #5085 additive banded semantic judge block.  Added ONLY when the arm ran
    # (``--judge semantic``), so a mechanical-lane receipt is byte-identical to
    # the pre-#5085 runner's — the gated ``metrics``/``judge_pin`` surface is
    # untouched and the committed baselines stay comparable.
    semantic = report.get("semantic_judge")
    if semantic is not None:
        receipt["semantic_judge"] = semantic
        receipt["semantic_judge_pin"] = semantic.get("pin")
    return receipt


def validate_receipt(receipt: dict) -> list[str]:
    """Shape-validate a runner receipt (§6.6 + the runner's additive detail)."""
    issues: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt: not an object"]
    for key in ("run_id", "date", "commit", "corpus_hash", "judge_pin"):
        value = receipt.get(key)
        if key == "judge_pin":
            # null judge_pin only on failed/skipped runs (nothing published)
            if receipt.get("run_status") == "completed" and (
                not isinstance(value, str) or not value.strip()
            ):
                issues.append(f"receipt.{key}: completed run requires a pinned judge")
        elif not isinstance(value, str) or not value.strip():
            issues.append(f"receipt.{key}: expected a non-empty string, got {value!r}")
    if receipt.get("run_status") not in RUN_STATUS_VALUES:
        issues.append(
            f"receipt.run_status: expected one of {sorted(RUN_STATUS_VALUES)}, "
            f"got {receipt.get('run_status')!r}"
        )
    if receipt.get("verdict") not in VERDICT_VALUES:
        issues.append(
            f"receipt.verdict: expected one of {sorted(VERDICT_VALUES)}, "
            f"got {receipt.get('verdict')!r}"
        )
    origin = receipt.get("failure_origin")
    if origin not in FAILURE_ORIGIN_VALUES:
        issues.append(
            f"receipt.failure_origin: expected one of "
            f"{sorted(v for v in FAILURE_ORIGIN_VALUES if v is not None)} or null, "
            f"got {origin!r}"
        )
    metrics = receipt.get("metrics")
    if not isinstance(metrics, dict):
        issues.append("receipt.metrics: expected an object")
    elif receipt.get("run_status") == "completed":
        missing = sorted(schema.METRIC_VALUES - set(metrics))
        if missing:
            issues.append(f"receipt.metrics: completed run missing metrics {missing}")
    if not isinstance(receipt.get("resolved_config"), dict):
        issues.append("receipt.resolved_config: expected an object")
    if isinstance(receipt.get("cost_usd"), bool) or not isinstance(
        receipt.get("cost_usd"), (int, float)
    ):
        issues.append("receipt.cost_usd: expected a number")
    elif receipt.get("cost_usd") < 0:
        issues.append(f"receipt.cost_usd: expected ≥ 0, got {receipt.get('cost_usd')!r}")
    # #5085 additive semantic block — validated only when present, so the
    # mechanical-lane receipt's shape contract is unchanged.
    semantic = receipt.get("semantic_judge")
    if semantic is not None:
        issues.extend(_validate_semantic_block(semantic, receipt))
    # #9077 (E4) additive extractor identity — validated only when present, for
    # the same back-compat reason. SHAPE only: whether the extractor being the
    # real one is a PASS/FAIL verdict, and that belongs to
    # ``tools/operator_receipt_check.py``, not to shape validation (a wire-lane
    # receipt is legitimately shape-valid and legitimately not an E4 number).
    extractor = receipt.get("extractor")
    if extractor is not None:
        if not isinstance(extractor, dict):
            issues.append("receipt.extractor: expected an object")
        elif not isinstance(extractor.get("real"), bool):
            issues.append(
                "receipt.extractor.real: expected a bool, got "
                f"{extractor.get('real')!r}"
            )
    return issues


def _validate_semantic_block(semantic: object, receipt: dict) -> list[str]:
    """Shape-validate the additive ``semantic_judge`` receipt block (#5085)."""
    issues: list[str] = []
    if not isinstance(semantic, dict):
        return ["receipt.semantic_judge: expected an object"]
    for key in ("pin", "status"):
        value = semantic.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(
                f"receipt.semantic_judge.{key}: expected a non-empty string, got {value!r}"
            )
    top_pin = receipt.get("semantic_judge_pin")
    if top_pin is not None and top_pin != semantic.get("pin"):
        issues.append(
            "receipt.semantic_judge_pin: does not match "
            f"semantic_judge.pin ({top_pin!r} vs {semantic.get('pin')!r})"
        )
    if semantic.get("status") == "failed":
        if not isinstance(semantic.get("error"), str) or not semantic["error"].strip():
            issues.append("receipt.semantic_judge.error: a failed arm must name its error")
        return issues
    distribution = semantic.get("band_distribution")
    if not isinstance(distribution, dict) or set(distribution) != set(judge.BAND_ORDER):
        issues.append(
            "receipt.semantic_judge.band_distribution: expected exactly the "
            f"bands {list(judge.BAND_ORDER)}, got {distribution!r}"
        )
    elif any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in distribution.values()
    ):
        issues.append(
            "receipt.semantic_judge.band_distribution: counts must be "
            f"non-negative integers, got {distribution!r}"
        )
    else:
        units_total = semantic.get("units_total")
        if (
            isinstance(units_total, int)
            and not isinstance(units_total, bool)
            and sum(distribution.values()) != units_total
        ):
            issues.append(
                "receipt.semantic_judge.band_distribution: counts sum to "
                f"{sum(distribution.values())}, which disagrees with "
                f"units_total={units_total}"
            )
    _validate_semantic_units(semantic.get("units"), issues)
    for key in ("band_weighted_score", "probability_mean",
                "semantic_survival_rate", "same_fact_rate"):
        value = semantic.get(key)
        if value is None:
            continue  # an empty corpus has no fraction — null, never a fake 0
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not (0.0 <= float(value) <= 1.0):
            issues.append(
                f"receipt.semantic_judge.{key}: expected a fraction in [0, 1] "
                f"or null, got {value!r}"
            )
    return issues


# ── CLI ─────────────────────────────────────────────────────────────────────


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="write-path-runner",
        description="W2-b write-path benchmark runner (epic #2080).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # #5085 additive banded semantic judge (opt-in; off in the CI lane).
    # #5106 deterministic first-stage pre-screen (opt-in; OFF by default so
    # the semantic_judge numbers stay comparable to #5085).

    p_bless = sub.add_parser("bless", help="bless a baseline from a run receipt")
    p_bless.add_argument("--receipt", type=Path, required=True)
    p_bless.add_argument("--justification", required=True)
    p_bless.add_argument("--corpus-bless", action="store_true",
                         help="accept an INTENTIONAL corpus regeneration "
                              "(fixtures_hash change) — the justification "
                              "must name the corpus change; records it in history")
    p_bless.add_argument("--protocol-bless", action="store_true",
                         help="accept an INTENTIONAL judge-protocol re-pin "
                              "(judge_pin change on an unchanged corpus) — the "
                              "justification must name the protocol change; "
                              "records it in history (round-3 G2: the sanctioned "
                              "re-pin path for a judge bump)")
    p_bless.add_argument("--write", action="store_true",
                         help="write the blessed baseline to its posture file "
                              "(main.json for llm receipts, m2.json for m2)")
    p_bless.add_argument("--root", type=Path, default=corpus.WRITE_PATH_DIR)

    p_val = sub.add_parser("validate-receipt", help="validate a receipt document")
    p_val.add_argument("receipt", type=Path)

    # #5085 calibration: build the ready-to-label band sample from a semantic
    # run receipt, and/or report κ/α once the labels are filled in.
    p_cal = sub.add_parser(
        "calibrate",
        help="build/verify the banded-judge calibration sample (κ/α pending "
             "until a human labels it)",
    )
    p_cal.add_argument("--receipt", type=Path, default=None,
                       help="a run receipt carrying a semantic_judge block")
    p_cal.add_argument("--sample", type=Path, default=None,
                       help="an existing calibration sample to report on")
    p_cal.add_argument("--out", type=Path, default=None,
                       help="write the built sample JSON here")
    p_cal.add_argument("--n", type=int, default=30,
                       help="sample size (owner floor: n >= 30)")

    # #5106 measurement: recompute the pre-screen over a committed judged
    # receipt.  The reference verdict is the LLM judge's own earned band, so
    # the report carries the screen's accuracy loss, not just its coverage.
    p_audit = sub.add_parser(
        "prescreen-audit",
        help="recompute the #5106 pre-screen over a judged receipt: units "
             "screened, judge calls removed, agreement with the judge",
    )
    p_audit.add_argument("--receipt", type=Path, required=True,
                         help="a run receipt carrying a semantic_judge block")
    p_audit.add_argument("--prescreen",
                         choices=(prescreen_mod.PRESCREEN_LEXICAL,
                                  prescreen_mod.PRESCREEN_NLI),
                         default=prescreen_mod.PRESCREEN_LEXICAL)
    p_audit.add_argument("--prescreen-model", default=None)
    p_audit.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)


    if args.command == "bless":
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        r_issues = validate_receipt(receipt)
        if r_issues:
            print("receipt invalid: " + "; ".join(r_issues), file=sys.stderr)
            return EXIT_RUNNER_ERROR
        if receipt["run_status"] != "completed":
            print(f"cannot bless a {receipt['run_status']!r} run", file=sys.stderr)
            return EXIT_RUNNER_ERROR
        # REVIEW-FIX (findings 1+4): bless targets the posture's baseline
        # file (llm → baselines/main.json, m2 → baselines/m2.json) — the
        # receipt's resolved_config names the lane, and cross-posture bless
        # is a config mismatch caught below by bless_baseline.
        bless_posture = (receipt.get("resolved_config") or {}).get(
            "extractor_posture", "llm")
        previous = corpus.load_baseline(args.root, posture=bless_posture)
        previous_metrics = previous.get("metrics") or {}
        corpus_bless = args.corpus_bless
        protocol_bless = args.protocol_bless
        hash_changed = bool(previous.get("fixtures_hash")) and \
            receipt["corpus_hash"] != previous.get("fixtures_hash")
        pin_changed = bool(previous.get("judge_pin")) and \
            receipt.get("judge_pin") != previous.get("judge_pin")
        if receipt["verdict"] == schema.VERDICT_INCONCLUSIVE:
            # Inconclusive is blessable ONLY as the first publish against the
            # pending baseline (no committed targets yet — benchmark-first)
            # or as a --corpus-bless / --protocol-bless of an INTENTIONAL
            # change (new corpus or new judge protocol ⇒ no comparability ⇒
            # re-pin, recorded in history).
            if previous_metrics and not (corpus_bless or protocol_bless):
                print("cannot bless an inconclusive run against committed targets "
                      "(use --corpus-bless for a corpus regeneration or "
                      "--protocol-bless for a judge re-pin)",
                      file=sys.stderr)
                return EXIT_RUNNER_ERROR
            if not (corpus_bless or protocol_bless):
                if receipt["corpus_hash"] != previous.get("fixtures_hash"):
                    print("cannot bless first publish: corpus hash mismatch", file=sys.stderr)
                    return EXIT_RUNNER_ERROR
                if receipt["resolved_config"] != previous.get("config"):
                    print("cannot bless first publish: resolved-config mismatch", file=sys.stderr)
                    return EXIT_RUNNER_ERROR
            elif corpus_bless and not hash_changed:
                print("cannot bless: --corpus-bless given but the run is on the "
                      "SAME corpus (no fixtures_hash change)" +
                      (" and the --protocol-bless flag is redundant here"
                       if protocol_bless else "") +
                      " — use the ordinary bless", file=sys.stderr)
                return EXIT_RUNNER_ERROR
            elif protocol_bless and not pin_changed:
                print("cannot bless: --protocol-bless given but the run uses the "
                      "SAME judge pin" +
                      (" and the --corpus-bless flag is redundant here"
                       if corpus_bless else "") +
                      " — use the ordinary bless", file=sys.stderr)
                return EXIT_RUNNER_ERROR
            elif corpus_bless and protocol_bless and not (hash_changed and pin_changed):
                # A legitimate corpus regeneration PLUS a judge bump needs BOTH
                # flags (schema requires both changes blessed deliberately); a
                # run that changes only one with both flags falls through to the
                # schema-level rejection below with its precise message.
                pass
        run = {
            "date": receipt["date"],
            "fixtures_hash": receipt["corpus_hash"],
            "config": receipt["resolved_config"],
            "metrics": receipt["metrics"],
            "judge_pin": receipt["judge_pin"],
            "failure_classes": [
                d.get("failure")
                for r in receipt.get("session_results", [])
                for d in r.get("failed_units", [])
            ],
        }
        try:
            blessed = schema.bless_baseline(
                previous, run, justification=args.justification,
                corpus_bless=corpus_bless,
                protocol_bless=protocol_bless,
            )
        except ValueError as exc:
            print(f"cannot bless: {exc}", file=sys.stderr)
            return EXIT_RUNNER_ERROR
        b_issues = schema.validate_baseline(blessed)
        if b_issues:
            print("blessed baseline invalid: " + "; ".join(b_issues), file=sys.stderr)
            return EXIT_RUNNER_ERROR
        print("blessed baseline:")
        print(json.dumps(blessed, indent=2))
        if args.write:
            target = corpus.baseline_path(args.root, posture=bless_posture)
            target.write_text(json.dumps(blessed, indent=2) + "\n", encoding="utf-8")
            print(f"written: {target}")
        return EXIT_OK

    if args.command == "validate-receipt":
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        issues = validate_receipt(receipt)
        if issues:
            print("invalid:" + "\n  ".join(["", *issues]))  # #2174-lint RUF005
            return EXIT_RUNNER_ERROR
        print("valid")
        return EXIT_OK

    if args.command == "calibrate":
        from tests.eval.write_path import calibration  # lazy: CLI-only

        if args.receipt is None and args.sample is None:
            parser.error("calibrate needs --receipt and/or --sample")
        if args.receipt is not None:
            receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
            semantic = receipt.get("semantic_judge")
            if not isinstance(semantic, dict) or semantic.get("status") != "completed":
                print(
                    "receipt carries no completed semantic_judge block — "
                    "run with --judge semantic first",
                    file=sys.stderr,
                )
                return EXIT_RUNNER_ERROR
            sample = calibration.build_calibration_sample(
                semantic, n=args.n, run_id=receipt.get("run_id")
            )
            if args.out is not None:
                calibration.write_sample(sample, args.out)
                print(f"calibration sample written: {args.out}")
            print(f"bands_present={sample['bands_present']} "
                  f"bands_missing={sample['bands_missing']} "
                  f"n_units={sample['n_units']} band_span={sample['band_span']}")
            report = calibration.calibration_report(sample)
        else:
            sample = calibration.load_sample(args.sample)
            report = calibration.calibration_report(sample)
        print("calibration report:")
        print(json.dumps(report, indent=2))
        print("labels_pending" if report["labels_pending"]
              else "labels complete — κ/α reported above")
        return EXIT_OK

    if args.command == "prescreen-audit":
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        semantic = receipt.get("semantic_judge")
        if not isinstance(semantic, dict) or semantic.get("status") != "completed":
            print(
                "receipt carries no completed semantic_judge block — run with "
                "--judge semantic first",
                file=sys.stderr,
            )
            return EXIT_RUNNER_ERROR
        screen = prescreen_mod.build_prescreen(
            args.prescreen,
            **({"model": args.prescreen_model} if args.prescreen_model else {}),
        )
        report = prescreen_mod.audit_receipt(semantic, screen)
        print(f"prescreen={report['screen']} "
              f"units_screened={report['units_screened']}/{report['units_total']} "
              f"(same_fact={report['screened_same_fact']} "
              f"likely_not={report['screened_likely_not']})")
        print(f"judge_calls: {report['judge_calls_total_without_screen']} → "
              f"{report['judge_calls_total_without_screen'] - report['judge_calls_saved']} "
              f"(saved {report['judge_calls_saved']}; "
              f"sessions_fully_screened={report['sessions_fully_screened']}/{report['sessions_total']}, "
              f"{report['judge_calls_per_session']} calls/session)")
        stage_audit = report["screen_audit"]
        screen_errors = int(stage_audit.get("errors") or 0)
        print(f"probe_chars_removed={report['probe_chars_removed']} "
              f"agreement={report['agreement']} "
              f"({report['agreement_n']}/{report['agreement_denominator']} judged) "
              f"screen_errors={screen_errors}")
        if report["reference_note"]:
            print(f"⚠ {report['reference_note']}", file=sys.stderr)
        if report["agreement"] is not None and report["agreement"] < 1.0:
            print(f"disagreements (max 20): {report['disagreements']}")
        # Fail CLOSED on the measurement.  A stage that cannot load abstains
        # EVERY unit; a stage that loads but raises inside ``screen()`` (e.g. a
        # ``--prescreen-model`` that is not a 3-class NLI head) is swallowed by
        # the stage's own fail-open handler.  In BOTH cases the report reads
        # ``units_screened=0, agreement=None`` — indistinguishable, by exit
        # code, from a genuine "this stage screens nothing" measurement, which
        # is exactly the number this tool exists to get right.  A PARTIAL fault
        # does not refuse (the report is still informative); it is surfaced by
        # the ``screen_errors`` count on every line.
        vacuous = not stage_audit.get("available", True) or (
            screen_errors > 0 and report["units_screened"] == 0
        )
        if vacuous:
            reason = stage_audit.get("load_error") or (
                f"the stage produced no verdict and failed on all "
                f"{screen_errors} units it was asked about"
            )
            print("⚠ the stage produced NO verdicts, so this report is NOT a "
                  f"measurement — {reason}", file=sys.stderr)
            return EXIT_RUNNER_ERROR
        if args.json:
            print(json.dumps(report, indent=2))
        return EXIT_OK

    parser.error(f"unknown command {args.command!r}")
    return EXIT_RUNNER_ERROR


if __name__ == "__main__":
    raise SystemExit(_main())
