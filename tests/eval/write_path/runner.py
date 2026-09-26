"""W2-b write-path benchmark runner (issue #2098, epic #2080 W2-b).

Replays the committed planted-gold corpus (W2-a, merged via #2155) through
the REAL session→graph write path and grades the written graph against the
sealed gold:

    preflight → per-session replay (store-file render → REAL session-import
    parser round-trip → SDK ``capture_session`` on a hermetic graph → dream
    EP pass) → mechanical grading (grading.py, authoritative) → aggregate
    the canonical 6-metric vocabulary → ``--compare`` verdict vs the
    committed baseline (schema.compare_run) → validated receipt.

Blindness / grading discipline:

* **Mechanical checks are authoritative** over judge output (issue grading
  hierarchy).  BPRE (the default mode) runs the mechanical arm only and
  records ``JUDGE_PIN_MECHANICAL`` — numbers are publishable against a
  pinned judge (the schema requires non-null judge_pin on publish).
  ADDITIVE (issue #5085, D14 "two-number split"): ``--judge semantic`` runs
  the blind banded judge BESIDE the mechanical arm and records a
  ``semantic_judge`` block (per-unit bands + band-weighted score + band
  distribution + its own ``SEMANTIC_JUDGE_PIN``).  It is opt-in, refused on
  the deterministic m2 posture, and never enters ``metrics`` — the judged
  lane cannot silently enter the CI gate.  (The earlier note that the
  salience judge was "not yet wired" is now stale: the BLIND banded arm is
  the wired one; the older FULL/PARTIAL/ABSENT ``SalienceJudge`` remains the
  unit-tested protocol from #2098 and is not what the runner invokes.)
* **Verbatim control lane**: every session's gold is ALSO graded against a
  control memory (the conversation written back verbatim).  Control macro
  survival is 1.0 by construction; anything less is a CORPUS/GRADER bug
  (the anchor is not recoverable from the verbatim transcript) and aborts
  the run as a runner_error — the pre-flight never lets a broken corpus
  silently punish (or flatter) the pipeline.
* **Sessions-emitting invariant**: a replayed session that produced no
  memory points (capture error / silent extraction skip) is a runner-level
  failure with a named origin — a session is NEVER dropped from the report
  (skipped never counts as pass).
* **Hermeticity**: replay runs on a per-run hermetic graph (namespace-scoped
  server graph under TORTOISE_DB_URI, transient embedded file otherwise);
  deterministic offline replay is the ``TORTOISE_SESSION_LLM_MOCK=1`` +
  ``TORTOISE_SESSION_EXTRACTOR=m2`` seam (content-preserving echo, no
  network — the CI lane); the real v2 extractor (provider key) is the
  product-parity lane used for first-baseline publishes.  The extractor
  posture (``llm`` | ``m2``) is part of the resolved-config comparability
  surface (REVIEW-FIX, PR #2183 findings 1+4): a run on one lane never
  compares against a baseline on the other (config mismatch ⇒
  inconclusive); the m2 lane blesses its own committed m2.json baseline
  (determinism gate) while the llm lane owns main.json + the standing
  leakage quality bar.
* **Cost (G9, round-3)**: receipts record ``cost_usd`` from capture
  telemetry ``llm_cost_usd`` when the extractor seam reports it, with an
  explicit cost-not-tracked / cost-partial note otherwise. Today NO seam
  reports ``llm_cost_usd`` (the mock never does; the real v2 extractor's
  telemetry does not yet surface it), so real-LLM-lane runs record 0.0 with
  the note — HARD_STOP_USD / per-run cost guarding cannot operate until the
  extractor seam reports cost (fix-wave item for the W5 wave; disclosed,
  never silently faked).

Receipts follow the epic §6.6 contract (run_status / verdict /
failure_origin / commit / corpus_hash / judge_pin / resolved_config /
cost_usd) plus the run detail an auditor needs to reproduce the number.
Validated by ``validate_receipt`` before any commit.

⚠️ **Key source — start the llm lane through the wrapper (#2718 / #4860).**
This runner reads provider keys straight from the process env and never loads
the repo ``.env`` (the repo's only ``.env`` loader,
``mcp_server._load_dotenv``, is not imported here — and where it does run it
only fills keys that are ABSENT, never overriding an ambient one). So a shell
that exported ``OPENROUTER_API_KEY`` / ``DEEPSEEK_API_KEY`` is what gets
billed. On 2026-09-23 the sealed #2552 run billed an exhausted ambient
OpenRouter key (HTTP 403, 7/7 sessions aborted) while a healthy evals key sat
in ``.env`` — and the receipt named no key. Always start the llm lane with
``tools/run-with-eval-keys.sh``: it strips the ambient provider keys, loads
the repo-root ``.env`` with override, prints the ``source`` + fingerprint of
each key it set, and execs the command. Paste that block into the receipt::

    PYTHONPATH=$PWD TORTOISE_TEST_CARVE_OUT=1 \
      tools/run-with-eval-keys.sh \
        .venv/bin/python -m tests.eval.write_path.runner run --out <receipt>

Exit contract (CLI): 0 = completed non-regression, 1 = regression or
runner_error (the CI-gate signal), 2 = inconclusive (nothing committed yet /
config, corpus, or judge-pin drift — the umbrella aggregates receipts, never exit
codes; a CI wiring must map 2 explicitly, never treat it as pass).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Module root helpers — this file doubles as the CLI entry
# (``python -m tests.eval.write_path.runner``), so the repo root must be
# importable whether it is run from the repo root or via a path.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.eval.write_path import corpus, grading, judge, schema  # noqa: E402

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


def _env_posture() -> str:
    """Extractor posture for the current run (REVIEW-FIX, PR #2183 findings
    1+4): ``TORTOISE_SESSION_EXTRACTOR=m2`` selects the deterministic echo
    lane; anything else (incl. absent) is the product ``llm`` lane.
    """
    if os.environ.get("TORTOISE_SESSION_EXTRACTOR", "").strip() == "m2":
        return "m2"
    return "llm"


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


# ── Per-session grading ─────────────────────────────────────────────────────


def grade_session(
    session_id: str,
    gold: dict,
    conversation: list[dict],
    points: list[dict],
    rephrase_edges: list[tuple[str, str]] | None = None,
) -> dict:
    """Grade one session's gold against its memory layer → session result.

    The result feeds ``grading.aggregate_metrics``; see grading.py for the
    pinned metric semantics.  Every session contributes its unit-level detail
    (macro/strict + named failure) so the run report names failure classes.
    """
    rephrase_edges = rephrase_edges or []
    macro = grading.macro_survival_counts(gold, points, rephrase_edges)
    strict = grading.strict_survival_counts(gold, points, rephrase_edges)
    leaked = grading.distractor_leakage(gold, points)
    quotes = grading.quote_fidelity_counts(gold, points, conversation)
    provenance = grading.provenance_counts(points)
    emitted = grading.session_emitted(points)
    unit_detail = grading.unit_level_detail(gold, points, rephrase_edges)
    control = judge.control_macro_counts(gold, conversation)
    return {
        "session_id": session_id,
        "gold_total_units": macro["total"],
        "macro": macro,
        "strict": strict,
        "leaked": leaked,
        "quotes": quotes,
        "provenance": provenance,
        "emitted": emitted,
        "unit_detail": unit_detail,
        "control_macro_survived": control["survived"],
        "control_macro_total": control["total"],
        "memory_point_count": len([p for p in points if not grading.is_turn_echo(p.get("content") or "")]),
        "turn_count": len(conversation),
    }


# ── The run ─────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


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
        verdicts = arm.judge_units(probes, memory)
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
        "pin": judge.SEMANTIC_JUDGE_PIN,
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


def run_benchmark(
    *,
    root: Path = corpus.WRITE_PATH_DIR,
    config: dict | None = None,
    session_ids: list[str] | None = None,
    sdk=None,
    workdir: Path | None = None,
    ep_pass: bool = True,
    run_id: str | None = None,
    notes: list[str] | None = None,
    log: list[str] | None = None,
    judge_mode: str = "mechanical",
    judge_samples: int = judge.DEFAULT_JUDGE_SAMPLES,
    judge_model: str | None = None,
    paraphrase_model: str | None = None,
    judge_temperature: float = judge.DEFAULT_JUDGE_TEMPERATURE,
    judge_factory=None,
) -> dict:
    """Full benchmark run: preflight → replay → grade → aggregate.

    ``sdk`` may be an existing open SDK (tests own their hermetic graph) or
    None (the CLI opens one from the ambient env posture — namespace-scoped
    server graph under TORTOISE_DB_URI, transient embedded file otherwise).

    ``judge_mode`` (issue #5085): ``"mechanical"`` (default) is the gated
    BPRE lane — byte-identical to the pre-#5085 runner.  ``"semantic"``
    ADDITIONALLY runs the blind banded judge and records its additive
    ``semantic_judge`` block (per-unit bands + band-weighted score + band
    distribution) in the report/receipt; it never touches ``metrics`` or the
    ``judge_pin``, and it is REFUSED on the deterministic ``m2`` posture so
    the CI lane can never silently acquire a non-reproducible number.

    Returns ``{"run_id", "date", "run_status", "verdict", "failure_origin",
     "commit", "corpus_hash", "judge_pin", "resolved_config", "cost_usd",
     "metrics", "session_results": [...], "notes": [...]}`` — plus, on a
    completed run, the additive ``operator_audit`` (issue #2514 planted-
    operator layer-2 grading: ``{planted, edge_correct, content_ok,
    operators_total, operators_provenanced, by_session}`` — NOT a gated
    metric in this change). ``operators_*`` is the #2552 structural probe:
    reified operator Points that entered the retrievable memory layer
    (eventId-stamped by the capture path).
    On a pre-flight failure or a control-lane violation the report comes back
    with run_status "failed" + the named origin — it NEVER raises mid-run
    (the umbrella aggregates receipts).
    """
    log = log if log is not None else []
    run_id = run_id or f"w2b-{uuid.uuid4().hex[:12]}"
    notes = list(notes or [])
    # REVIEW-FIX (round-3 F3): the extractor posture is derived EXCLUSIVELY
    # from the env seam (TORTOISE_SESSION_EXTRACTOR=m2 selects the echo
    # lane). BASELINE_CONFIG carries a posture DEFAULT (llm) for snapshot
    # shape, but it is NOT part of the caller-overridable surface: a
    # caller-supplied ``config`` may NOT relabel the run's posture (an
    # llm-labeled run executed under m2 env, or an m2-labeled run under llm
    # env, would compare against the wrong lane's baseline and/or dodge the
    # llm standing leakage bar — the dishonest cross-extractor compare
    # finding 4 was meant to forbid). Any config posture that contradicts
    # the env seam is rejected; the env always wins.
    env_posture = _env_posture()
    resolved_config = dict(corpus.BASELINE_CONFIG)
    resolved_config.pop("extractor_posture", None)  # env owns it (round-3 F3)
    if config is not None:
        config_posture = config.get("extractor_posture")
        if config_posture is not None and config_posture != env_posture:
            raise ValueError(
                "extractor_posture in config "
                f"({config_posture!r}) contradicts the env lane selector "
                f"({env_posture!r}) — the env seam owns the posture"
            )
        resolved_config.update(config)
    resolved_config["extractor_posture"] = env_posture
    posture = env_posture
    date = _now_iso()
    commit = _git_head_short()

    pf = preflight(root, posture=posture)
    if not pf["ok"]:
        # REVIEW-FIX (PR #2183 review finding 6): corpus-hash/manifest drift
        # is the audit-grade ``hash_mismatch`` origin (E2E-2 — a gold-only
        # edit must surface as the hash failure it is), not a generic
        # runner_error. Everything else stays runner_error. Round-3 F7: the
        # classifier keys on the ACTUAL drift issue prefixes (the preflight
        # drift checks that report ``fixtures_hash ... corpus drift`` and the
        # manifest verification failures) rather than substring-matching any
        # issue text that happens to contain "hash"/"manifest" (a malformed
        # manifest schema issue is a corpus-validity failure, not drift).
        drift_issues = [i for i in pf["issues"]
                        if "corpus drift" in i
                        or i.startswith("manifest verification failed")]
        origin = "hash_mismatch" if drift_issues else "runner_error"
        return _failed_report(
            run_id, date, commit, pf["fixtures_hash"], resolved_config,
            origin=origin,
            detail="; ".join(pf["issues"][:8]), log=log,
        )
    baseline = pf["baseline"]
    fixtures_hash = pf["fixtures_hash"]
    if judge_mode not in ("mechanical", "semantic"):
        raise ValueError(
            f"unknown judge_mode {judge_mode!r} — one of ('mechanical', 'semantic')"
        )
    if judge_mode == "semantic" and judge_samples < 3:
        # The probability IS an agreement fraction; the owner floor is >=3
        # independent judgements per unit per order.  Below 3 the grid cannot
        # reach every band (and 1 sample collapses it to 0/1), so refuse rather
        # than publish a degenerate number.
        raise ValueError(
            "judge_samples must be >= 3 for the semantic arm (the owner floor "
            f"for an earned agreement fraction), got {judge_samples!r}"
        )
    if judge_mode == "semantic" and posture != "llm":
        # Determinism: the CI lane replays byte-reproducibly on the m2 echo
        # posture; an LLM-judged arm there would be neither meaningful (the
        # echo memory carries the verbatim transcript) nor reproducible.
        # Fail closed rather than silently admit a non-deterministic number.
        return _failed_report(
            run_id, date, commit, fixtures_hash, resolved_config,
            origin="runner_error",
            detail=(
                "--judge semantic requires the llm extractor posture; the "
                f"m2 deterministic lane is refused (posture={posture!r}) — "
                "the judged lane must never enter the CI gate"
            ),
            log=log,
        )
    selected = session_ids or corpus.session_ids(root)
    missing = [s for s in selected if s not in corpus.session_ids(root)]
    if missing:
        return _failed_report(
            run_id, date, commit, fixtures_hash, resolved_config,
            origin="runner_error",
            detail=f"unknown sessions: {missing}", log=log,
        )

    judge_pin = judge.JUDGE_PIN_MECHANICAL
    owned_sdk = sdk is None
    if owned_sdk:
        sdk = _open_hermetic_sdk(run_id)
    session_results: list[dict] = []
    runner_errors: list[str] = []
    semantic_block: dict | None = None
    total_cost: float = 0.0
    cost_tracked: bool = True  # flipped False if any capture lacks a cost figure
    quote_spans_total: int = 0
    try:
        workdir = workdir or Path(tempfile.mkdtemp(prefix=f"w2b_{run_id}_"))
        for session_id in selected:
            fixture = corpus.load_fixture(session_id, root)
            gold = corpus.load_gold(session_id, root)
            conversation = None  # REVIEW-FIX (F5): never leak a prior
            # iteration's parsed conversation into a failed capture's grading.
            try:
                conversation = parse_roundtrip(
                    session_id, fixture["conversation"], fixture["harness"],
                    workdir=workdir, log=log,
                )
                capture = sdk.capture_session(
                    conversation, session_id=session_id, harness=fixture["harness"]
                )
                notes.append(
                    f"{session_id}: capture ok={capture.get('ok')} "
                    f"extraction_mode={capture.get('extraction_mode')} "
                    f"extracted={capture.get('extracted')}"
                )
                if capture.get("ok") is not True:
                    runner_errors.append(
                        f"{session_id}: capture ok=False "
                        f"(errors={capture.get('errors')})"
                    )
                # REVIEW-FIX (cost honesty): accumulate the extractor's
                # reported cost when the capture telemetry carries it; a
                # None/missing figure (mock seam, unknown adapter) flips
                # cost_tracked False so the receipt records an explicit
                # note rather than a silently-fake 0.0.
                telemetry = capture.get("telemetry") or {}
                session_cost = telemetry.get("llm_cost_usd")
                if session_cost is None:
                    cost_tracked = False
                else:
                    total_cost += float(session_cost)
            except Exception as exc:
                runner_errors.append(f"{session_id}: capture raised {type(exc).__name__}: {exc}")
                capture = {}
            snapshot = snapshot_session(sdk, session_id)
            grade_conv = conversation if conversation is not None \
                else fixture["conversation"]
            result = grade_session(
                session_id, gold, grade_conv,
                snapshot["points"], snapshot["rephrase_edges"],
            )
            result["operator_counts"] = snapshot["operator_counts"]
            result["capture_ok"] = capture.get("ok")
            session_results.append(result)
        if ep_pass:
            try:
                dream = sdk.dream(
                    full=True, require_calibration=False, warm_start=False
                )
                notes.append(
                    "dream EP pass: "
                    f"total_affected={dream.get('total_affected')} "
                    f"coverage={dream.get('coverage')} "
                    f"converged_all={dream.get('converged_all')}"
                )
                # Re-grade AFTER the EP pass: strict survival reads the EP
                # state the dream pass left behind.
                re_snapshots = {
                    sid: snapshot_session(sdk, sid) for sid in selected
                }
                fresh: list[dict] = []
                for result in session_results:
                    snap = re_snapshots[result["session_id"]]
                    refreshed = grade_session(
                        result["session_id"],
                        corpus.load_gold(result["session_id"], root),
                        corpus.load_fixture(result["session_id"], root)["conversation"],
                        snap["points"], snap["rephrase_edges"],
                    )
                    refreshed["operator_counts"] = snap["operator_counts"]
                    refreshed["capture_ok"] = result["capture_ok"]
                    fresh.append(refreshed)
                session_results = fresh
            except Exception as exc:
                runner_errors.append(f"dream EP pass raised {type(exc).__name__}: {exc}")

        # #2514 planted-operator (layer-2) grading — corpus-wide (the cross-
        # session SUPERSEDE resolves its to-anchor in another session's memory
        # layer).  The EP pass only rewrites confidence, so the operator
        # surface is re-snapshotted once here (post-dream state).  Additive
        # AUDIT dimension carried on every run (both lanes) — NOT a
        # METRIC_VALUES member in this change (scoping note 2026-09-07-2514-).
        # On the m2 echo lane the planted edges are graded by a cue-word
        # relation stage, so the score is structurally low and not comparable
        # with the llm lane's — expected and noted, never a quality bar.
        operator_audit = None
        if not runner_errors:
            golds = {sid: corpus.load_gold(sid, root) for sid in selected}
            points_by_session: dict[str, list] = {}
            surfaces_by_session: dict[str, dict] = {}
            for sid in selected:
                try:
                    snap = snapshot_session(sdk, sid)
                except Exception as exc:
                    runner_errors.append(
                        f"{sid}: operator snapshot raised {type(exc).__name__}: {exc}"
                    )
                    snap = {"points": [], "direct_edges": [],
                            "operator_edges": [], "mitigations": []}
                points_by_session[sid] = snap.get("points", [])
                surfaces_by_session[sid] = snap
            if not runner_errors:
                operator_audit = grading.grade_planted_operators(
                    golds, points_by_session, surfaces_by_session
                )
                # #2552 (layer-2 WIRE — the structural leg): operator nodes
                # that entered the retrievable memory layer. The structural
                # fix stamps the capture's operators with the
                # sessionCaptured eventId, so every operator the write path
                # committed is retrievable and provenanced; a total > 0 with
                # provenanced < total is the pre-fix drop signature.
                operator_audit["operators_total"] = sum(
                    s.get("operators_total", 0)
                    for s in surfaces_by_session.values()
                )
                operator_audit["operators_provenanced"] = sum(
                    s.get("operators_provenanced", 0)
                    for s in surfaces_by_session.values()
                )
                for result in session_results:
                    sid = result["session_id"]
                    owned = [
                        d for d in operator_audit["results"].values()
                        if d.get("owner_session") == sid
                    ]
                    result["planted_operators"] = owned
                # ``operator_audit_notes`` handles the None case itself, so this
                # needs no guard of its own.
                notes.extend(operator_audit_notes(operator_audit, posture))

        # #5085 additive blind banded judge: runs INSIDE the try (it needs the
        # open SDK and the post-dream memory layer) and only when the
        # mechanical replay is sound — a broken capture must fail the run, not
        # publish a judged number over a memory layer that was never written.
        if judge_mode == "semantic" and not runner_errors:
            try:
                semantic_block = _run_semantic_judge(
                    sdk, selected, root,
                    samples=judge_samples,
                    judge_model=judge_model,
                    paraphrase_model=paraphrase_model,
                    temperature=judge_temperature,
                    judge_factory=judge_factory,
                    notes=notes,
                )
            except judge.JudgeBlindnessError as exc:
                # LOAD-BEARING (plan §J4): a blindness violation is a harness
                # failure — fail the run, never publish a lexically-matched
                # number produced while the judge could see the anchor.
                runner_errors.append(f"semantic judge blindness violation: {exc}")
            except Exception as exc:
                semantic_block = {
                    "status": "failed",
                    "pin": judge.SEMANTIC_JUDGE_PIN,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                notes.append(
                    "semantic judge FAILED (additive arm only — the "
                    f"mechanical metrics are unaffected): {type(exc).__name__}: {exc}"
                )
    finally:
        if owned_sdk:
            _close_and_wipe(sdk)

    # Control-lane self-check: verbatim macro survival must be 1.0 across the
    # corpus — a lower value means the corpus/grader cannot even recover the
    # planted anchors from the verbatim transcript (broken corpus, not
    # pipeline), and the run must not publish a number against it.
    control_bad = [
        f"{r['session_id']}: control macro {r['control_macro_survived']}/"
        f"{r['control_macro_total']}"
        for r in session_results
        if r["control_macro_total"]
        and r["control_macro_survived"] < r["control_macro_total"]
    ]
    if control_bad:
        runner_errors.append("control lane < 1.0 (corpus/grader bug): " + "; ".join(control_bad))

    # Sessions that were replayed but did not emit violate the 100% invariant
    # AND any session whose capture failed is a runner error (never dropped).
    no_gold = [r["session_id"] for r in session_results if r["gold_total_units"] == 0]
    if no_gold:
        runner_errors.append(f"sessions with no graded gold units: {no_gold}")

    if runner_errors:
        report = _failed_report(
            run_id, date, commit, fixtures_hash, resolved_config,
            origin="runner_error",
            detail="; ".join(runner_errors[:8]), log=log,
            session_results=session_results,
        )
        report["metrics"] = _safe_metrics(session_results)
        report["judge_pin"] = judge_pin
        return report

    metrics = grading.aggregate_metrics(session_results)
    # REVIEW-FIX (F3): surface quote vacuity — a gold corpus that never
    # quotes yields a vacuous quote_fidelity 1.0 (floor, not bar). The
    # graded snapshot stays canonical-6; the REPORT carries the raw span
    # count + a note so the committed 1.0 is never misread.
    quote_spans_total = sum(r["quotes"]["total"] for r in session_results)
    if quote_spans_total == 0:
        notes.append(
            "quote_fidelity 1.0 is VACUOUS: the corpus gold contains no "
            "double-quoted spans (quote_spans_total=0) — this number is a "
            "floor, not a fidelity bar; it will not hold under a future "
            "corpus that quotes"
        )
    log.append(f"metrics: {json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in metrics.items()})}")
    verdict = schema.compare_run(
        metrics, baseline,
        resolved_config=resolved_config,
        run_fixtures_hash=fixtures_hash,
        run_judge_pin=judge_pin,  # round-3 G2: protocol-honest compare
    )
    failure_origin = None
    if verdict == schema.VERDICT_REGRESSION:
        failure_origin = "gate_regression"
    elif verdict == schema.VERDICT_INCONCLUSIVE:
        # Distinguish config / hash / judge-pin / pending for the receipt
        # origin (round-3 N3: a pin-drifted inconclusive must not be
        # indistinguishable from a benign first-run-pending one).
        if fixtures_hash != baseline.get("fixtures_hash"):
            failure_origin = "hash_mismatch"
        elif resolved_config != baseline.get("config"):
            failure_origin = "config_mismatch"
        elif baseline.get("judge_pin") and judge_pin != baseline.get("judge_pin"):
            failure_origin = "judge_pin_mismatch"
        elif not (baseline.get("metrics") or {}):
            failure_origin = None  # first-run-pending — nothing to compare yet
    if not cost_tracked:
        if total_cost > 0.0:
            notes.append(
                "cost partial: some sessions reported llm_cost_usd and others "
                "did not — receipt cost_usd is the tracked PARTIAL sum "
                f"({round(total_cost, 6)}), understating true cost; never read "
                "it as a full measured figure"
            )
        else:
            notes.append(
                "cost not tracked: the extractor seam reported no llm_cost_usd "
                "in any session (mock/unknown adapter) — receipt cost_usd is "
                "0.0 with this note, never a silently-fake measured figure"
            )
    return {
        "run_id": run_id,
        "date": date,
        "run_status": "completed",
        "verdict": verdict,
        "failure_origin": failure_origin,
        "commit": commit,
        "corpus_hash": fixtures_hash,
        "judge_pin": judge_pin,
        "resolved_config": resolved_config,
        "cost_usd": round(total_cost, 6),  # partial-sum when mixed; 0.0 + note when untracked
        "metrics": metrics,
        "quote_spans_total": quote_spans_total,
        "operator_audit": operator_audit,
        "semantic_judge": semantic_block,
        "session_results": session_results,
        "notes": notes,
        "log": log,
    }


def operator_audit_notes(audit: dict | None, posture: str) -> list[str]:
    """The operator-audit note(s) for a run report (#2514/#2552).

    The m2-echo-lane caveat — "no relation extraction, so 0 is structural
    there, never a bar" — is **posture-scoped**. (The quoted wording is itself
    stale on BOTH counts: the lane's cue-word stage does extract relations, and
    its grading is no longer 0. It is preserved verbatim here only because this
    refactor is scoped to posture, not to the note's prose; the prose fix is
    tracked by #4807.) The m2 lane's relation stage is a cue-word heuristic,
    not the product extractor, so its edge score is not comparable with the llm
    lane's; on the llm lane the score IS a genuine behavioural signal about
    emission fidelity, and printing the m2 lane's excuse verbatim in that
    receipt frames a real result as a non-result in the very artifact a reader
    consults.

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
        # The m2 clause keeps main's EXACT wording and separator `); ` so an
        # m2 run's note is byte-identical to the pre-refactor text — the
        # blessed m2 receipt text is provably unchanged by this refactor. The
        # llm lane terminates its sentence with a bare `.`, so the note reads
        # as prose either way.
        tail = (
            "; the m2 echo lane has no relation extraction, so 0 is structural "
            "there, never a bar."
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


def _safe_metrics(session_results: list[dict]) -> dict:
    """Metrics for a FAILED run — partial, but only when sessions were graded."""
    if not session_results:
        return {}
    return grading.aggregate_metrics(session_results)


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


def _git_head_short() -> str:
    try:
        import subprocess

        out = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def _open_hermetic_sdk(run_id: str) -> object:
    """Open a hermetic per-run graph from the ambient env posture.

    With TORTOISE_DB_URI set: a namespace-scoped server graph named after the
    run (team_w2bench_<run_id>).  Without: a transient embedded file under a
    temp dir.  TORTOISE_TEST_MODE/TORTOISE_TEST_SESSION, when present in the
    environment (pytest session), route the construction through the
    redirect seam like any other test construction.
    """
    from tortoise.sdk import TortoiseSDK

    uri = os.environ.get("TORTOISE_DB_URI", "").strip()
    namespace = f"w2bench_{re.sub(r'[^a-zA-Z0-9_]', '', run_id)}"
    if uri and os.environ.get("TORTOISE_TEST_MODE") != "1":
        # CLI docker posture: team-scoped graph on the URI server.
        return TortoiseSDK(namespace=namespace)
    # Embedded (or test-session redirect): explicit temp path.
    tmp = Path(tempfile.mkdtemp(prefix="w2b_graph_")) / f"{run_id}.db"
    return TortoiseSDK(db_path=str(tmp), namespace=namespace)


def _close_and_wipe(sdk) -> None:
    """Best-effort teardown: detach-delete the run's graph, then close.

    Never raises — hygiene failures must not fail a completed run (the graph
    name is still namespace-scoped + session-journaled under pytest, so the
    session sweep is the backstop).
    """
    with contextlib.suppress(Exception):  # #2174-lint SIM105
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
    with contextlib.suppress(Exception):  # #2174-lint SIM105
        sdk.close()


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
        }
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

    p_run = sub.add_parser("run", help="preflight + replay + grade + compare")
    p_run.add_argument("--root", type=Path, default=corpus.WRITE_PATH_DIR)
    p_run.add_argument("--session", action="append", default=None,
                       help="restrict to session ids (repeatable)")
    p_run.add_argument("--no-ep-pass", action="store_true",
                       help="skip the dream EP pass")
    p_run.add_argument("--out", type=Path, default=None,
                       help="write the run receipt JSON to this path")
    p_run.add_argument("--json", action="store_true",
                       help="emit the run report JSON on stdout")
    # #5085 additive banded semantic judge (opt-in; off in the CI lane).
    p_run.add_argument("--judge", choices=("mechanical", "semantic"),
                       default="mechanical",
                       help="mechanical (default, gated BPRE lane) or semantic "
                            "(additionally run the blind banded judge and "
                            "record its additive semantic_judge block)")
    p_run.add_argument("--judge-samples", type=int,
                       default=judge.DEFAULT_JUDGE_SAMPLES,
                       help="independent judgements per unit per order "
                            f"(default {judge.DEFAULT_JUDGE_SAMPLES}; the "
                            "agreement fraction is the probability)")
    p_run.add_argument("--judge-model", default=None,
                       help="MODELS registry key for the blind judge "
                            f"(default {judge.DEFAULT_JUDGE_MODEL}; keep it "
                            "different from the extractor to blunt "
                            "self-preference bias)")
    p_run.add_argument("--paraphrase-model", default=None,
                       help="MODELS registry key for the paraphrase stage "
                            "(default: the judge model)")
    p_run.add_argument("--judge-temperature", type=float,
                       default=judge.DEFAULT_JUDGE_TEMPERATURE,
                       help="sampling temperature for the judge's repeated "
                            "judgements (0.0 makes every sample identical)")

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

    args = parser.parse_args(argv)

    if args.command == "run":
        if args.judge == "semantic" and args.judge_samples < 3:
            parser.error(
                "--judge-samples must be >= 3 for --judge semantic "
                f"(got {args.judge_samples}) — the unit probability is an "
                "agreement fraction over independent judgements, and fewer "
                "than 3 votes cannot reach every band"
            )
        report = run_benchmark(
            root=args.root,
            session_ids=args.session,
            ep_pass=not args.no_ep_pass,
            judge_mode=args.judge,
            judge_samples=args.judge_samples,
            judge_model=args.judge_model,
            paraphrase_model=args.paraphrase_model,
            judge_temperature=args.judge_temperature,
        )
        print(f"run_status={report['run_status']} verdict={report['verdict']} "
              f"failure_origin={report['failure_origin']} run_id={report['run_id']}")
        if report.get("metrics"):
            for key, value in report["metrics"].items():
                print(f"  {key}: {value}")
        semantic = report.get("semantic_judge")
        if semantic and semantic.get("status") == "completed":
            print("  semantic_judge (additive, NOT a gated metric):")
            print(f"    band_weighted_score: {semantic['band_weighted_score']}")
            print(f"    probability_mean:    {semantic['probability_mean']}")
            print(f"    survival>=0.50:      {semantic['semantic_survival_rate']}")
            print(f"    band_distribution:   {semantic['band_distribution']}")
            print(f"    pin:                 {semantic['pin']}")
        elif semantic:
            print(f"  semantic_judge: {semantic.get('status')} "
                  f"({semantic.get('error')})")
        if report.get("notes"):
            for note in report["notes"]:
                print(f"  note: {note}")
        receipt = build_receipt(report)
        issues = validate_receipt(receipt)
        print(f"receipt valid: {not issues}" + (f" ({'; '.join(issues)})" if issues else ""))
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            print(f"receipt written: {args.out}")
        if args.json:
            print(json.dumps(report, indent=2))
        if report["run_status"] != "completed":
            return EXIT_RUNNER_ERROR
        if report["verdict"] == schema.VERDICT_REGRESSION:
            return EXIT_REGRESSION
        if report["verdict"] == schema.VERDICT_INCONCLUSIVE:
            return EXIT_INCONCLUSIVE
        return EXIT_OK

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

    parser.error(f"unknown command {args.command!r}")
    return EXIT_RUNNER_ERROR


if __name__ == "__main__":
    raise SystemExit(_main())
