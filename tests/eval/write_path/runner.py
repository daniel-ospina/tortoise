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
  ``--judge salience`` (full mode, cost-tracked) adds the pinned blind
  salience judge (judge.py) whose output is reported SEPARATELY and never
  overwrites a mechanical verdict.
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
  posture is an ENV property of the run (recorded in the report's notes),
  never part of the corpus config snapshot.

Receipts follow the epic §6.6 contract (run_status / verdict /
failure_origin / commit / corpus_hash / judge_pin / resolved_config /
cost_usd) plus the run detail an auditor needs to reproduce the number.
Validated by ``validate_receipt`` before any commit.

Exit contract (CLI): 0 = completed non-regression, 1 = regression or
runner_error (the CI-gate signal), 2 = inconclusive (nothing committed yet /
config or corpus drift — the umbrella aggregates receipts, never exit
codes; a CI wiring must map 2 explicitly, never treat it as pass).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
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
    {"config_mismatch", "hash_mismatch", "runner_error", "gate_regression", None}
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


def preflight(root: Path = corpus.WRITE_PATH_DIR) -> dict:
    """Pre-flight gate: manifest coverage, corpus validity, baseline validity.

    Returns ``{"ok": bool, "issues": [...], "fixtures_hash": str,
    "baseline": dict}``.  Every committed fixture + gold validates against
    its schema (cross-checked pair-wise), the manifest covers the on-disk
    corpus byte-for-byte, the committed baseline validates (first-run-pending
    or published), and every gold carries ≥1 graded salient unit (a vacuum
    1.0 denominator would rubber-stamp).
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
    baseline = corpus.load_baseline(root)
    b_issues = schema.validate_baseline(baseline)
    issues.extend(f"baseline: {i}" for i in b_issues)
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
        if harness in ("codex", "pi"):
            part_type = "input_text" if role == "user" else "output_text"
            record = {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": role,
                    "content": [{"type": part_type, "text": content}],
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
    status, confidence, lastDreamedAt, pointKind, is_episodic.
    """
    (pid, content, event_id, extracted_from, status,
     confidence, last_dreamed, point_kind, is_episodic) = row
    return {
        "point_id": pid,
        "content": content or "",
        "provenance_present": bool(event_id) or bool(extracted_from),
        "ep_updated": confidence is not None or last_dreamed is not None,
        "status": status,
        "point_kind": point_kind,
        "is_episodic": is_episodic,
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
MEMORY_ROW_QUERY = (
    "MATCH (p:Point) WHERE p.eventId IN $eids "
    "RETURN p.id, p.content, p.eventId, p.extractedFrom, p.status, "
    "p.confidence, p.lastDreamedAt, p.pointKind, p.is_episodic"
)
OPERATOR_EDGE_QUERY = (
    "MATCH (a:Point)-[r]->(b:Point) "
    "WHERE a.id IN $ids AND b.id IN $ids "
    "RETURN type(r), a.id, b.id"
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
            points.append(point)
    # Operator edges among the memory layer (the REPHRASE dedup surface + the
    # raw IMPL/NAND counts the report audits).
    rephrase_edges: list[tuple[str, str]] = []
    operator_counts: dict[str, int] = {}
    if seen:
        edge_rows = g.query(
            OPERATOR_EDGE_QUERY, params={"ids": list(seen)}
        ).result_set
        for etype, a, b in edge_rows:
            operator_counts[etype] = operator_counts.get(etype, 0) + 1
            if etype == "REPHRASE":
                rephrase_edges.append((a, b))
    return {
        "points": points,
        "rephrase_edges": rephrase_edges,
        "turn_ids": sorted(turn_ids),
        "operator_counts": operator_counts,
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
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
) -> dict:
    """Full benchmark run: preflight → replay → grade → aggregate.

    ``sdk`` may be an existing open SDK (tests own their hermetic graph) or
    None (the CLI opens one from the ambient env posture — namespace-scoped
    server graph under TORTOISE_DB_URI, transient embedded file otherwise).

    Returns the run report (receipt-ready)::

        {"run_id", "date", "run_status", "verdict", "failure_origin",
         "commit", "corpus_hash", "judge_pin", "resolved_config", "cost_usd",
         "metrics", "session_results": [...], "notes": [...]}

    On a pre-flight failure or a control-lane violation the report comes back
    with run_status "failed" + the named origin — it NEVER raises mid-run
    (the umbrella aggregates receipts).
    """
    log = log if log is not None else []
    run_id = run_id or f"w2b-{uuid.uuid4().hex[:12]}"
    notes = list(notes or [])
    resolved_config = dict(corpus.BASELINE_CONFIG)
    if config is not None:
        resolved_config.update(config)
    date = _now_iso()
    commit = _git_head_short()

    pf = preflight(root)
    if not pf["ok"]:
        return _failed_report(
            run_id, date, commit, pf["fixtures_hash"], resolved_config,
            origin="runner_error",
            detail="; ".join(pf["issues"][:8]), log=log,
        )
    baseline = pf["baseline"]
    fixtures_hash = pf["fixtures_hash"]
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
    try:
        workdir = workdir or Path(tempfile.mkdtemp(prefix=f"w2b_{run_id}_"))
        for session_id in selected:
            fixture = corpus.load_fixture(session_id, root)
            gold = corpus.load_gold(session_id, root)
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
            except Exception as exc:  # noqa: BLE001 — the run report carries it
                runner_errors.append(f"{session_id}: capture raised {type(exc).__name__}: {exc}")
                capture = {}
            snapshot = snapshot_session(sdk, session_id)
            result = grade_session(
                session_id, gold, conversation if "conversation" in locals() else fixture["conversation"],
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
            except Exception as exc:  # noqa: BLE001
                runner_errors.append(f"dream EP pass raised {type(exc).__name__}: {exc}")
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
    log.append(f"metrics: {json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in metrics.items()})}")
    verdict = schema.compare_run(
        metrics, baseline,
        resolved_config=resolved_config,
        run_fixtures_hash=fixtures_hash,
    )
    failure_origin = None
    if verdict == schema.VERDICT_REGRESSION:
        failure_origin = "gate_regression"
    elif verdict == schema.VERDICT_INCONCLUSIVE:
        # Distinguish config vs hash mismatch for the receipt origin.
        if fixtures_hash != baseline.get("fixtures_hash"):
            failure_origin = "hash_mismatch"
        elif resolved_config != baseline.get("config"):
            failure_origin = "config_mismatch"
        elif not (baseline.get("metrics") or {}):
            failure_origin = None  # first-run-pending — nothing to compare yet
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
        "cost_usd": 0.0,
        "metrics": metrics,
        "session_results": session_results,
        "notes": notes,
        "log": log,
    }


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
    except Exception:  # noqa: BLE001
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
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
    except Exception:  # noqa: BLE001
        pass
    try:
        sdk.close()
    except Exception:  # noqa: BLE001
        pass


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

    p_bless = sub.add_parser("bless", help="bless a baseline from a run receipt")
    p_bless.add_argument("--receipt", type=Path, required=True)
    p_bless.add_argument("--justification", required=True)
    p_bless.add_argument("--write", action="store_true",
                         help="write the blessed baseline to baselines/main.json")
    p_bless.add_argument("--root", type=Path, default=corpus.WRITE_PATH_DIR)

    p_val = sub.add_parser("validate-receipt", help="validate a receipt document")
    p_val.add_argument("receipt", type=Path)

    args = parser.parse_args(argv)

    if args.command == "run":
        report = run_benchmark(
            root=args.root,
            session_ids=args.session,
            ep_pass=not args.no_ep_pass,
        )
        print(f"run_status={report['run_status']} verdict={report['verdict']} "
              f"failure_origin={report['failure_origin']} run_id={report['run_id']}")
        if report.get("metrics"):
            for key, value in report["metrics"].items():
                print(f"  {key}: {value}")
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
        previous = corpus.load_baseline(args.root)
        previous_metrics = previous.get("metrics") or {}
        if receipt["verdict"] == schema.VERDICT_INCONCLUSIVE:
            # Inconclusive is blessable ONLY as the first publish against the
            # pending baseline (no committed targets yet — benchmark-first)
            # and only when the run is on the same frozen corpus + resolved
            # config (a drifted run must not silently re-pin the baseline).
            if previous_metrics:
                print("cannot bless an inconclusive run against committed targets",
                      file=sys.stderr)
                return EXIT_RUNNER_ERROR
            if receipt["corpus_hash"] != previous.get("fixtures_hash"):
                print("cannot bless first publish: corpus hash mismatch", file=sys.stderr)
                return EXIT_RUNNER_ERROR
            if receipt["resolved_config"] != previous.get("config"):
                print("cannot bless first publish: resolved-config mismatch", file=sys.stderr)
                return EXIT_RUNNER_ERROR
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
            blessed = schema.bless_baseline(previous, run, justification=args.justification)
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
            target = args.root / "baselines" / "main.json"
            target.write_text(json.dumps(blessed, indent=2) + "\n", encoding="utf-8")
            print(f"written: {target}")
        return EXIT_OK

    if args.command == "validate-receipt":
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        issues = validate_receipt(receipt)
        if issues:
            print("invalid:" + "\n  ".join([""] + issues))
            return EXIT_RUNNER_ERROR
        print("valid")
        return EXIT_OK

    parser.error(f"unknown command {args.command!r}")
    return EXIT_RUNNER_ERROR


if __name__ == "__main__":
    raise SystemExit(_main())
