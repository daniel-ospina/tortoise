"""W3 harness runner (issue #2099 W3-a) — hermetic REAL-seam replay.

Replays the committed Cat-34-style corpus through the REAL product seams:

* **write path** — fixture turns render to the ``pi`` store shape, round-trip
  through the REAL ``session_import`` parser, then land via the REAL
  ``capture_session`` write path (deterministic M2 echo lane in CI via
  ``TORTOISE_SESSION_LLM_MOCK=1`` + ``TORTOISE_SESSION_EXTRACTOR=m2``; the
  LLM product lane otherwise).
* **recall path** — continuity READER sessions query the pair's graph with
  the REAL ``recall_state``; the surfaced content grades the reader cell.
* **reflex decision seam** — the graded seam for the know_to_ask / push
  suites.  The W4 delivery issue builds the graded reflex on this seam; the
  initial runner ships a NULL reflex (never injects) and publishes the
  HONEST first numbers (fix-wave protocol) with the failure class named.

Cells: each session replays into a hermetic per-run graph keyed by its
CELL (team name for the isolation suite, the continuity pair's writer id,
else the session id) — per-team/per-pair graphs make the source-isolation
gate (E2E-4, this issue's own pass gate) assert the rig's routing, and the
multi-turn writer/reader continuity pairs share one graph.

Run topology mirrors write_path/runner.py (preflight → replay → grade →
aggregate → compare → receipt; the umbrella aggregates receipts, never exit
codes; a skipped session never counts as pass).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # harness pkg
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # tests/

from eval.harness import corpus, grading, schema
from eval.write_path import runner as wp

HARNESS_DIR = corpus.HARNESS_DIR
REPO_ROOT = HARNESS_DIR.parent.parent.parent

# The mechanical grading protocol id — the deterministic graders (schema
# vocabulary + grading.py) are the "judge"; a change to either is a
# protocol change (protocol_bless), never a silent compare.
JUDGE_PIN = "w3-volunteering-memory-mechanical-v1"

RUN_STATUS_VALUES = frozenset({"pending", "completed", "failed"})
FAILURE_ORIGIN_VALUES = frozenset(
    {None, "runner_error", "hash_mismatch", "config_mismatch",
     "judge_pin_mismatch", "gate_regression"}
)

REPLAY_HARNESS = "pi"  # the seam's store shape for role/content conversations


class RunError(Exception):
    """A harness-run control error (preflight, parser drift, replay fault)."""


def _env_posture() -> str:
    """The extractor posture comes from the ENV seam only: TORTOISE_SESSION_
    EXTRACTOR=m2 selects the deterministic echo lane; anything else is the
    LLM product lane.  A caller config may never relabel the run's posture."""
    if os.environ.get("TORTOISE_SESSION_EXTRACTOR", "").strip() == "m2":
        return "m2"
    return "llm"


def _cell_key(session_id: str, fixture: dict, gold: dict) -> str:
    """The graph cell a session replays into.

    * isolation suite — the team id (both teams' sessions keep SEPARATE
      graphs; routing is what the isolation grader asserts).
    * continuity suite — the WRITER session id (writer + reader share one
      graph so the reader's recall sees the writer's write-back).
    * otherwise — the session id itself.
    """
    team = fixture.get("team")
    if team:
        return f"team_{team}"
    if fixture.get("suite") == "continuity" or gold.get("suite") == "continuity":
        spec = (gold.get("continuity") or {})
        writer = spec.get("writer_session") or session_id
        return f"pair_{writer}"
    return f"sess_{session_id}"


def _session_graph(cells: dict[str, object], key: str, run_id: str) -> object:
    """Lazily open one hermetic per-cell graph (cached per run).

    Cell isolation is the E2E-4 rig property: each cell (team / pair /
    session) gets its OWN graph — server-namespace under TORTOISE_DB_URI,
    transient embedded file otherwise (mirrors write_path's hermetic SDK
    open, with the cell folded into the namespace)."""
    from tortoise.sdk import TortoiseSDK

    sdk = cells.get(key)
    if sdk is None:
        uri = os.environ.get("TORTOISE_DB_URI", "").strip()
        ns_slug = re.sub(r"[^a-zA-Z0-9_]", "", key)
        namespace = f"w3h_{run_id}_{ns_slug}"
        if uri and os.environ.get("TORTOISE_TEST_MODE") != "1":
            sdk = TortoiseSDK(namespace=namespace)
        else:
            tmp = Path(tempfile.mkdtemp(prefix="w3h_graph_")) / f"{key}.db"
            sdk = TortoiseSDK(db_path=str(tmp), namespace=namespace)
        cells[key] = sdk
    return sdk


def _teardown_cells(cells: dict[str, object]) -> None:
    import contextlib

    for sdk in cells.values():
        with contextlib.suppress(Exception):
            sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
        with contextlib.suppress(Exception):
            sdk.close()
    cells.clear()


# ── Pre-flight (mirrors write_path; harness corpus surface) ────────────────

def preflight(root: Path = HARNESS_DIR, *, posture: str = "llm") -> dict:
    """Corpus integrity + baseline readiness before a run.  Returns
    {"ok", "issues", "fixtures_hash", "baseline"}."""
    issues: list[str] = []
    verification = corpus.verify_manifest(root)
    if not verification["ok"]:
        if verification["malformed"]:
            issues.append(f"manifest verification failed: {verification['malformed']}")
        for rel in verification["missing"]:
            issues.append(f"manifest verification failed: missing {rel}")
        for rel in verification["extra"]:
            issues.append(f"manifest verification failed: extra {rel}")
        for rel in verification["mismatched"]:
            issues.append(f"manifest verification failed: mismatched {rel}")
    for sid in corpus.session_ids(root):
        fixture = corpus.load_fixture(sid, root)
        gold = corpus.load_gold(sid, root)
        issues.extend(f"fixture {sid}: {i}" for i in schema.validate_fixture(fixture))
        issues.extend(f"gold {sid}: {i}" for i in schema.validate_gold(gold, fixture))
        issues.extend(
            f"{sid}: {i}"
            for i in schema.fixture_gold_consistent(fixture, gold, sid)
        )
        if gold.get("suite") in ("know_to_ask", "push") and not gold.get("per_turn"):
            issues.append(f"gold {sid}: suite {gold.get('suite')} requires per_turn labels")
        # Continuity pairing: a reader fixture's gold must name an EXISTING
        # writer fixture, and a writer fixture must have a reader.
        cont = (gold.get("continuity") or {})
        writer_session = cont.get("writer_session")
        if (fixture.get("suite") == "continuity" and writer_session
                and writer_session not in corpus.session_ids(root)):
            issues.append(
                f"gold {sid}: continuity writer {writer_session!r} not in corpus"
            )
        if fixture.get("writer") and gold.get("suite") == "continuity":
            reader_sid = sid.replace("writer", "reader")
            if reader_sid not in corpus.session_ids(root):
                issues.append(f"fixture {sid}: writer has no reader session {reader_sid}")
    n = len(corpus.session_ids(root))
    n_holdout = len(corpus.holdout_ids(root))
    if n and (n_holdout / n) < 0.05:
        issues.append(f"holdout set too small: {n_holdout}/{n} < 5%")
    fixtures_hash = corpus.compute_fixtures_hash(root)
    baseline = corpus.load_baseline(root, posture=posture)
    if baseline.get("fixtures_hash") != fixtures_hash:
        issues.append(
            "baseline.fixtures_hash != on-disk corpus hash "
            f"({baseline.get('fixtures_hash')} vs {fixtures_hash}) — corpus drift"
        )
    cfg_posture = (baseline.get("config") or {}).get("extractor_posture")
    if cfg_posture != posture:
        issues.append(
            f"baseline config posture {cfg_posture!r} != run posture {posture!r}"
        )
    return {
        "ok": not issues,
        "issues": issues,
        "fixtures_hash": fixtures_hash,
        "baseline": baseline,
    }


# ── Snapshot + recall helpers (REAL seams) ─────────────────────────────────

def snapshot_points(sdk, session_id: str) -> list[dict]:
    """The session's memory points (points stamped with the session's
    sessionCaptured eventId, excluding the episodic turn echo — same surface
    write_path/runner.snapshot_session reads; ported to return points only)."""
    snap = wp.snapshot_session(sdk, session_id)
    return snap["points"]


def recall_texts(sdk, query: str, *, limit: int = 8) -> list[str]:
    """Real recall_state over the cell graph → the surfaced content texts."""
    results = sdk.recall_state(query=query, limit=limit)
    texts: list[str] = []
    for result in results:
        content = result.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content)
    return texts


def cell_points(sdk) -> list[dict]:
    """ALL points in a cell graph (no session filter) — the isolation
    grading surface.  A leak is OTHER-team content anywhere in my team's
    cell graph; per-session eventId snapshots would hide a misrouted write
    (team B content written into team A's graph still stamps team B's
    eventId), so the isolation pass reads the whole cell."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (p:Point) RETURN p.id, p.content, p.eventId, p.extractedFrom, "
        "p.status, p.confidence, p.lastDreamedAt, p.pointKind, p.is_episodic"
    ).result_set
    points: list[dict] = []
    for row in rows:
        point = wp._row_to_point(row)
        points.append(point)
    return points


# ── Per-session replay + grade ─────────────────────────────────────────────

def replay_and_grade(
    session_id: str,
    fixture: dict,
    gold: dict,
    sdk,
    *,
    workdir: Path,
    log: list[str] | None = None,
    null_reflex: bool = True,
) -> dict:
    """Replay ONE session through the real seams and grade it.

    Returns the session result dict consumed by schema.aggregate_metrics.
    ``sdk`` is the cell's SDK (the caller opened it — cell routing is the
    caller's job so pair/team sharing works).
    """
    log = log if log is not None else []
    suite = gold.get("suite")
    conversation = fixture["turns"]
    # Write path: real parser round-trip, then the REAL capture_session.
    parsed = wp.parse_roundtrip(
        session_id, conversation, REPLAY_HARNESS, workdir=workdir, log=log
    )
    capture = sdk.capture_session(
        parsed, session_id=session_id, harness=REPLAY_HARNESS
    )
    if capture.get("ok") is not True:
        raise RunError(
            f"session {session_id}: capture ok={capture.get('ok')} "
            f"(errors={capture.get('errors')})"
        )
    if suite in ("know_to_ask", "push"):
        # The graded seam is the W4 reflex.  The null reflex injects
        # nothing — the honest baseline; W4 re-blesses when it lands.
        injected: dict[int, list[str]] = {}
        if null_reflex:
            log.append(
                f"{session_id}: null reflex — no pointers injected "
                "(graded seam lands with the W4 reflex delivery)"
            )
        if suite == "know_to_ask":
            result = grading.grade_kta(session_id, gold, injected)
            result["capture_ok"] = True
            return result
        result = grading.grade_push(session_id, gold, injected)
        result["capture_ok"] = True
        return result
    if suite == "write_back":
        points = snapshot_points(sdk, session_id)
        result = grading.grade_write_back(session_id, gold, points)
        result["capture_ok"] = True
        result["memory_point_count"] = len(points)
        return result
    if suite == "continuity":
        spec = (gold.get("continuity") or {})
        if fixture.get("writer"):
            # Writer cell: the decision lands via capture; nothing graded
            # on the writer turn itself (the READER cell is graded).
            result = {
                "session_id": session_id,
                "suite": "continuity",
                "continuity": {
                    "surfaced": 0, "total": 0,
                    "writer_session": session_id,
                },
                "emitted": True,
                "capture_ok": True,
            }
            return result
        queries = spec.get("reader_queries", [])
        if not queries:
            raise RunError(f"session {session_id}: reader has no reader_queries")
        transcript: list[str] = []
        for query in queries:
            transcript.extend(recall_texts(sdk, query))
        result = grading.grade_continuity(session_id, gold, transcript)
        result["capture_ok"] = True
        return result
    if suite == "isolation":
        # The write path runs here; the ISOLATION GRADE is a per-CELL
        # post-pass (run_benchmark grades each team cell once against the
        # whole cell graph — see cell_points).  The session result is
        # emitted-but-ungraded; the pass appends the graded result.
        return {
            "session_id": session_id,
            "suite": "isolation",
            "emitted": True,
            "capture_ok": True,
            "isolation": None,  # graded in the cell post-pass
        }
    raise RunError(f"session {session_id}: ungraded suite {suite!r}")


# ── The run ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def run_benchmark(
    *,
    root: Path = HARNESS_DIR,
    session_ids: list[str] | None = None,
    sdk=None,
    run_id: str | None = None,
    notes: list[str] | None = None,
    log: list[str] | None = None,
    null_reflex: bool = True,
    config: dict | None = None,
    progress=None,
) -> dict:
    """Full harness run: preflight → cell replay → grade → aggregate.

    ``progress`` is an optional file-like for per-session progress lines
    (the llm product lane is slow; a silent run is undiagnosable).

    Returns the run report (receipt-ready)::

        {"run_id", "date", "run_status", "verdict", "failure_origin",
         "commit", "corpus_hash", "judge_pin", "resolved_config", "cost_usd",
         "metrics", "session_results": [...], "notes": [...], "log": [...]}
    """
    log = log if log is not None else []
    run_id = run_id or f"w3h-{uuid.uuid4().hex[:12]}"
    notes = list(notes or [])
    env_posture = _env_posture()
    # A3 confound guard (issue indicator 2, plan §6.5): the m2 echo lane is
    # ONLY hermetic when the LLM seam is mocked — an m2-labeled run with an
    # ambient provider key but no mock would silently extract with the REAL
    # LLM (LLM data compared vs an m2 baseline — the exact confound the
    # harness exists to kill). Fail closed: m2 posture requires the mock.
    if env_posture == "m2" and os.environ.get("TORTOISE_SESSION_LLM_MOCK", "").strip().lower() != "1":
        raise RunError(
            "env posture m2 (TORTOISE_SESSION_EXTRACTOR=m2) requires "
            "TORTOISE_SESSION_LLM_MOCK=1 — without the mock seam an ambient "
            "provider key would run the real LLM (A3 confound); set both or "
            "unset EXTRACTOR"
        )
    resolved_config = dict(corpus.BASELINE_CONFIG)
    resolved_config.pop("extractor_posture", None)  # env owns the posture
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
    # BPRE (default): the gate corpus EXCLUDES the pinned holdout fixtures
    # (the frozen evaluation set reserved for the W4 reflex).  --full opts
    # into the whole corpus (mode=full).
    hard_stop_usd = float(os.environ.get("HARD_STOP_USD", "0") or 0)
    if hard_stop_usd:
        notes.append(f"HARD_STOP_USD={hard_stop_usd} armed")
    date = _now_iso()
    commit = _git_head()

    pf = preflight(root, posture=env_posture)
    if not pf["ok"]:
        return _failed_report(
            run_id, date, commit, pf["fixtures_hash"], resolved_config,
            origin="hash_mismatch" if any(
                "corpus drift" in i or i.startswith("manifest verification failed")
                for i in pf["issues"]
            ) else "runner_error",
            detail="; ".join(pf["issues"][:8]), log=log,
        )
    baseline = pf["baseline"]
    fixtures_hash = pf["fixtures_hash"]
    run_mode = resolved_config.get("mode", "BPRE")
    if session_ids:
        selected = list(session_ids)
    elif run_mode == "BPRE":
        # Gate corpus = all sessions minus the pinned holdout (reserved for
        # the W4 reflex's frozen evaluation).  The exclusion is a run-time
        # property of mode — the corpus + hash are mode-independent.
        selected = [s for s in corpus.session_ids(root)
                    if s not in corpus.holdout_ids(root)]
        notes.append(
            f"BPRE mode: {len(corpus.holdout_ids(root))} holdout fixtures "
            f"excluded; gate corpus = {len(selected)} sessions"
        )
    else:
        selected = corpus.session_ids(root)
    missing = [s for s in selected if s not in corpus.session_ids(root)]
    if missing:
        return _failed_report(
            run_id, date, commit, fixtures_hash, resolved_config,
            origin="runner_error", detail=f"unknown sessions: {missing}", log=log,
        )

    cells: dict[str, object] = {}
    session_results: list[dict] = []
    runner_errors: list[str] = []
    total_cost = 0.0
    cost_tracked = True
    try:
        workdir = Path(tempfile.mkdtemp(prefix=f"w3h_{run_id}_"))
        # Order continuity WRITERS before their READERS (a reader's recall
        # must see the writer's write-back): readers sort after everything
        # else; within a suite authoring order holds.
        def _reader_last(sid: str) -> tuple[int, str]:
            fx = corpus.load_fixture(sid, root)
            return (1 if fx.get("suite") == "continuity" and not fx.get("writer") else 0, sid)

        ordered = sorted(
            (s for s in corpus.session_ids(root) if s in selected),
            key=_reader_last,
        )
        for session_id in ordered:
            fixture = corpus.load_fixture(session_id, root)
            gold = corpus.load_gold(session_id, root)
            key = _cell_key(session_id, fixture, gold)
            cell_sdk = _session_graph(cells, key, run_id)
            try:
                result = replay_and_grade(
                    session_id, fixture, gold, cell_sdk,
                    workdir=workdir, log=log, null_reflex=null_reflex,
                )
                result["cell"] = key
                session_results.append(result)
                telemetry = {}
                notes.append(f"{session_id}: cell={key} suite={gold.get('suite')}")
                if progress is not None:
                    print(
                        f"[{date}] {session_id} ({gold.get('suite')}, "
                        f"cell={key}) ok", file=progress, flush=True
                    )
            except Exception as exc:
                runner_errors.append(
                    f"{session_id}: replay raised {type(exc).__name__}: {exc}"
                )
                telemetry = {}
                if progress is not None:
                    print(
                        f"[{date}] {session_id} raised "
                        f"{type(exc).__name__}: {exc}", file=progress, flush=True
                    )
            session_cost = telemetry.get("llm_cost_usd")
            if session_cost is None:
                cost_tracked = False
            else:
                total_cost += float(session_cost)
        # Isolation post-pass: grade each TEAM cell once against the WHOLE
        # cell graph (see cell_points) — the E2E-4 surface.
        for key in sorted(cells):
            if not key.startswith("team_"):
                continue
            team = key[len("team_"):]
            iso_sids = [
                s for s in ordered
                if corpus.load_fixture(s, root).get("team") == team
                and corpus.load_fixture(s, root).get("suite") == "isolation"
            ]
            if not iso_sids:
                continue
            sid = iso_sids[0]
            gold = corpus.load_gold(sid, root)
            try:
                points = cell_points(cells[key])
                result = grading.grade_isolation(
                    sid, gold, points, own_team=team
                )
                result["cell"] = key
                result["capture_ok"] = True
                result["memory_point_count"] = len(points)
                # Drop the session-loop's ungraded placeholder for this sid
                # (grade once per cell, not per session).
                session_results = [
                    r for r in session_results if r["session_id"] != sid
                ]
                session_results.append(result)
                notes.append(
                    f"isolation {key}: own {result['isolation']['own_anchors_present']}/"
                    f"{result['isolation']['own_anchors_total']} present, "
                    f"violations={result['isolation']['violations']}"
                )
            except Exception as exc:
                runner_errors.append(
                    f"isolation pass {key}: raised {type(exc).__name__}: {exc}"
                )
    finally:
        _teardown_cells(cells)

    if runner_errors:
        report = _failed_report(
            run_id, date, commit, fixtures_hash, resolved_config,
            origin="runner_error", detail="; ".join(runner_errors[:8]),
            log=log, session_results=session_results,
        )
        report["metrics"] = _safe_metrics(session_results)
        report["judge_pin"] = JUDGE_PIN
        return report

    metrics = schema.aggregate_metrics(session_results)
    log.append(
        "metrics: " + json.dumps({
            k: round(v, 4) if isinstance(v, float) else v
            for k, v in metrics.items()
        })
    )
    if null_reflex:
        notes.append(
            "no-reflex: know_to_ask/push graded on the NULL reflex (nothing "
            "injected) — the kta failure / push precision numbers are the "
            "honest pre-W4 baseline; the standing kta/false-fire/push bars "
            "activate when a baseline is published with config.reflex=graded"
        )
    verdict = schema.compare_run(
        metrics, baseline,
        resolved_config=resolved_config,
        run_fixtures_hash=fixtures_hash,
        run_judge_pin=JUDGE_PIN,
    )
    failure_origin = None
    if verdict == schema.VERDICT_REGRESSION:
        failure_origin = "gate_regression"
    elif verdict == schema.VERDICT_INCONCLUSIVE:
        if fixtures_hash != baseline.get("fixtures_hash"):
            failure_origin = "hash_mismatch"
        elif resolved_config != baseline.get("config"):
            failure_origin = "config_mismatch"
        elif baseline.get("judge_pin") and baseline.get("judge_pin") != JUDGE_PIN:
            failure_origin = "judge_pin_mismatch"
        elif not (baseline.get("metrics") or {}):
            failure_origin = None  # first-run-pending
    if not cost_tracked:
        notes.append(
            "cost not tracked: the capture seam reported no llm_cost_usd — "
            "receipt cost_usd is 0.0 with this note, never a silently-fake "
            "measured figure"
        )
    return {
        "run_id": run_id,
        "date": date,
        "run_status": "completed",
        "verdict": verdict,
        "failure_origin": failure_origin,
        "commit": commit,
        "corpus_hash": fixtures_hash,
        "judge_pin": JUDGE_PIN,
        "resolved_config": resolved_config,
        "cost_usd": round(total_cost, 6),
        "metrics": metrics,
        "session_results": session_results,
        "notes": notes,
        "log": log,
    }


def _safe_metrics(session_results: list[dict]) -> dict:
    if not session_results:
        return {}
    return schema.aggregate_metrics(session_results)


def _failed_report(
    run_id: str, date: str, commit: str, fixtures_hash: str,
    resolved_config: dict, *, origin: str, detail: str,
    log: list[str] | None = None, session_results: list[dict] | None = None,
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


# ── Receipt (same shape discipline as write_path §6.6) ─────────────────────

def build_receipt(report: dict, *, justification: str | None = None) -> dict:
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
        "session_results": [
            {
                "session_id": r["session_id"],
                "suite": r["suite"],
                "cell": r.get("cell"),
                "emitted": r["emitted"],
                "capture_ok": r.get("capture_ok"),
                "kta": r.get("kta"),
                "false_fire": r.get("false_fire"),
                "push": r.get("push"),
                "write_back": r.get("write_back"),
                "continuity": r.get("continuity"),
                "isolation": r.get("isolation"),
                "memory_point_count": r.get("memory_point_count"),
            }
            for r in report.get("session_results", [])
        ],
        "notes": report.get("notes", []),
        "log": report.get("log", []),
    }
    return receipt


def validate_receipt(receipt: dict) -> list[str]:
    issues: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt: not an object"]
    for key in ("run_id", "date", "commit", "corpus_hash", "judge_pin"):
        value = receipt.get(key)
        if key == "judge_pin":
            if receipt.get("run_status") == "completed" and (
                not isinstance(value, str) or not value.strip()
            ):
                issues.append("receipt.judge_pin: completed run requires a pinned judge")
        elif not isinstance(value, str) or not value.strip():
            issues.append(f"receipt.{key}: expected a non-empty string, got {value!r}")
    if receipt.get("run_status") not in RUN_STATUS_VALUES:
        issues.append(f"receipt.run_status: unexpected {receipt.get('run_status')!r}")
    if receipt.get("verdict") not in schema.VERDICT_VALUES:
        issues.append(f"receipt.verdict: unexpected {receipt.get('verdict')!r}")
    origin = receipt.get("failure_origin")
    if origin not in FAILURE_ORIGIN_VALUES:
        issues.append(
            f"receipt.failure_origin: unexpected {origin!r}"
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
        issues.append(f"receipt.cost_usd: expected >= 0, got {receipt.get('cost_usd')!r}")
    return issues


# ── CLI ────────────────────────────────────────────────────────────────────

def _write_json(path: Path, doc: dict) -> None:
    path.write_text(
        (json.dumps(doc, indent=2, sort_keys=True) + "\n"), encoding="utf-8"
    )


def _bless_main(root: Path, posture: str, justification: str, run_id: str | None,
                corpus_bless: bool, protocol_bless: bool, notes: list[str]) -> int:
    """--bless: run (if needed) then bless the run's metrics into the
    posture's committed baseline."""
    baseline_path = corpus.baseline_path(root, posture=posture)
    pending = corpus.load_baseline(root, posture=posture)
    # No pre-emptive block here: bless_baseline's guards are authoritative
    # (drift / config-mismatch / inconclusive raise; a regression re-publish
    # records its verdict in history per the fix-wave protocol).  A re-bless
    # MUST carry a justification naming the correction.
    report = run_benchmark(root=root, run_id=run_id, notes=notes,
                           progress=sys.stderr)
    if report["run_status"] != "completed":
        print(
            f"run failed ({report['failure_origin']}): "
            + "; ".join(report["log"][-3:])
        )
        return 2
    try:
        blessed = schema.bless_baseline(
            pending, {
                "date": report["date"],
                "fixtures_hash": report["corpus_hash"],
                "judge_pin": report["judge_pin"],
                "config": report["resolved_config"],
                "metrics": report["metrics"],
                "failure_classes": report.get("notes", [])[:4],
            },
            justification=justification,
            corpus_bless=corpus_bless, protocol_bless=protocol_bless,
        )
    except ValueError as exc:
        print(f"bless rejected: {exc}")
        return 3
    _write_json(baseline_path, blessed)
    print(
        f"blessed {baseline_path.name}: "
        + json.dumps({
            k: round(v, 4) if isinstance(v, float) else v
            for k, v in report["metrics"].items()
        })
    )
    return 0


def _main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    run_only = not any(
        flag in args for flag in
        ("--bless", "--compare", "--bless-corpus", "--bless-protocol")
    )
    if "--compare" in args or run_only:
        progress = None
        if "--progress" in args:
            progress = sys.stderr
        run_config = None
        if "--full" in args:
            run_config = {"mode": "full"}  # include the pinned holdout
        report = run_benchmark(progress=progress, config=run_config)
        print(
            f"run {report['run_id']}: status={report['run_status']} "
            f"verdict={report['verdict']} origin={report['failure_origin']}"
        )
        print(
            "metrics: " + json.dumps({
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in report["metrics"].items()
            })
        )
        if report["run_status"] != "completed":
            print("log: " + "; ".join(report["log"][-5:]))
            return 2 if report["failure_origin"] else 0
        receipt = build_receipt(report)
        issues = validate_receipt(receipt)
        if issues:
            print("RECEIPT ISSUES: " + "; ".join(issues))
            return 2
        return 0 if report["verdict"] == schema.VERDICT_PASS else 2
    if "--bless" in args or "--bless-corpus" in args or "--bless-protocol" in args:
        posture = "m2" if os.environ.get("TORTOISE_SESSION_EXTRACTOR") == "m2" else "llm"
        justification = _arg_value(args, "--justification")
        if not justification:
            print("--bless requires --justification <text>")
            return 3
        return _bless_main(
            HARNESS_DIR, posture, justification,
            run_id=_arg_value(args, "--run-id"),
            corpus_bless="--bless-corpus" in args,
            protocol_bless="--bless-protocol" in args,
            notes=["blessed via CLI"],
        )
    print(__doc__)
    return 0


def _arg_value(args: list[str], flag: str) -> str | None:
    try:
        index = args.index(flag)
    except ValueError:
        return None
    if index + 1 < len(args):
        return args[index + 1]
    return None


if __name__ == "__main__":
    raise SystemExit(_main())
