"""W3-b why-suite runner (epic #2080, issue #2100) — hermetic A11 grading.

Runs the why-layer suite on ONE hermetic throwaway graph:

1. **seed** — the shared E2E-1 planted-conflict corpus (40 points, mirrored
   from W4-a's deterministic seeding via ``seeding.py`` — composition pinned
   by the jointly-pinned corpus manifest);
2. **assemble** — the W4 why-block assembly (``tortoise.why.
   assemble_why_blocks``) produces the canonical §3.1.4 surfaced context for
   every planted point (A11: this block is ALL the grader sees);
3. **grade** — the deterministic graders (the ``judge_why_suite_v3`` pinned
   rubric) answer the four why-questions from each block ALONE — conflict
   surfacing / dig-deeper navigation (pointer targets resolved against the
   planted role map the harness owns) / support-chain + trade-off
   sufficiency / the clean false-positive arm;
4. **A4 A/B arm** (eval-phase, never gating) — contested-boost vs
   confidence-only ordering over the planted twin pairs;
5. **compare** — vs the committed posture baseline (BPRE-style: judge pin
   asserted pre-step, fixtures_hash + config mismatch ⇒ inconclusive,
   standing E2E-7 bars ≥ 0.95 / 0-false-positive armed — config.reflex ==
   "graded" from day one);
6. **receipt** — validated per-point rows (evidentiality: a completed run's
   receipt ties its aggregate metrics to the graded points).

Run topology mirrors eval/harness/runner.py (preflight → run → grade →
aggregate → compare → receipt).  Hermetic subprocess contract: env-key
posture from the env seam only; one-DB-per-run (embedded transient file when
URI-less or under pytest's TEST_MODE redirect; a wiped server namespace
under an explicit TORTOISE_DB_URI outside pytest).  Zero-LLM end to end —
seeding + assembly + grading are deterministic; no provider key is ever
required (the m2 deterministic lane needs no LLM mock because this suite has
no extractor seam).
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

sys.path.insert(0, str(Path(__file__).resolve().parent))  # why_suite pkg
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # tests/

from eval.why_suite import a4_ab, adversarial, corpus, grading, judge, schema, seeding

REPO_ROOT = corpus.WHY_DIR.parent.parent.parent
WHY_DIR = corpus.WHY_DIR

RUN_STATUS_VALUES = frozenset({"pending", "completed", "failed"})
FAILURE_ORIGIN_VALUES = frozenset(
    {
        None,
        "runner_error",
        "hash_mismatch",
        "config_mismatch",
        "judge_pin_mismatch",
        "gate_regression",
    }
)


class RunError(Exception):
    """A why-suite run control error (preflight, drift, replay fault)."""


def _env_posture() -> str:
    """Posture comes from the ENV seam only (TORTOISE_SESSION_EXTRACTOR=m2 →
    the m2 deterministic lane; anything else is the llm posture).  This
    suite is zero-LLM so both postures run identical code — the posture
    records provenance and selects the committed baseline file (parity with
    the W3-a harness m2/main split)."""
    if os.environ.get("TORTOISE_SESSION_EXTRACTOR", "").strip() == "m2":
        return "m2"
    return "llm"


def _open_graph(run_id: str, log: list[str] | None = None) -> object:
    """ONE hermetic graph for the whole run: an embedded transient file when
    URI-less or under pytest's TEST_MODE redirect; a wiped server namespace
    under an explicit TORTOISE_DB_URI outside pytest (mirrors the W3-a
    harness cell opening).  Wipe-on-open: a crashed prior run's graph must
    never contaminate grading.

    Wipe visibility (review P3b, #2100): the wipe is recorded in the run log
    and — under a SERVER URI, where a stale namespace would silently
    contaminate grading — a FAILED wipe is a RunError, never a suppressed
    skip.  The embedded transient wipe stays best-effort (a fresh temp file
    has nothing stale to clean)."""
    from tortoise.sdk import TortoiseSDK

    log = log or []
    uri = os.environ.get("TORTOISE_DB_URI", "").strip()
    ns_slug = re.sub(r"[^a-zA-Z0-9_]", "", run_id)
    namespace = f"w3b_{ns_slug}"
    if uri and os.environ.get("TORTOISE_TEST_MODE") != "1":
        sdk = TortoiseSDK(namespace=namespace)
        try:
            sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
        except Exception as exc:
            sdk.close()
            raise RuntimeError(
                f"wipe of server namespace {namespace!r} FAILED ({exc}) — a "
                "crashed prior run's graph would contaminate grading; fix "
                "the namespace/ACL before re-running"
            ) from exc
        log.append(f"opened server namespace {namespace} (prior state wiped)")
        return sdk
    tmp = Path(tempfile.mkdtemp(prefix="w3b_graph_")) / f"{run_id}.db"
    sdk = TortoiseSDK(db_path=str(tmp), namespace=namespace)
    import contextlib

    with contextlib.suppress(Exception):
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    log.append(f"opened embedded transient graph {namespace} (fresh)")
    return sdk


def _teardown_graph(sdk) -> None:
    import contextlib

    with contextlib.suppress(Exception):
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    with contextlib.suppress(Exception):
        sdk.close()


# ── Pre-flight (mirrors the harness; why-suite corpus + judge pin) ────────


def preflight(
    root: Path = WHY_DIR, *, posture: str = "llm", corpus_name: str = corpus.DEFAULT_CORPUS
) -> dict:
    """Corpus integrity + baseline readiness + the JUDGE-PIN PRE-STEP.

    Returns {"ok", "issues", "fixtures_hash", "judge_pin", "baseline"}.
    The pinned judge is asserted HERE (issue Indicator 2): the on-disk
    prompt hash must equal the committed protocol anchor; a drift fails the
    run before any grading (never a silent compare under a different
    protocol).  ``corpus_name`` scopes every fixture/baseline read to ONE
    corpus (#2275) — the two corpora never share a hash, a gold file, or a
    baseline lane."""
    issues: list[str] = []
    corpus.profile(corpus_name)  # unknown corpus name → LOUD error
    verification = corpus.verify_manifest(root, corpus=corpus_name)
    if not verification["ok"]:
        if verification["malformed"]:
            issues.append(f"manifest verification failed: {verification['malformed']}")
        for rel in verification["missing"]:
            issues.append(f"manifest verification failed: missing {rel}")
        for rel in verification["extra"]:
            issues.append(f"manifest verification failed: extra {rel}")
        for rel in verification["mismatched"]:
            issues.append(f"manifest verification failed: mismatched {rel}")
    manifest = corpus.load_manifest(root, corpus=corpus_name)
    issues.extend(f"manifest: {i}" for i in schema.validate_manifest(manifest))
    gold = corpus.gold_doc(root, corpus=corpus_name)
    issues.extend(f"gold: {i}" for i in schema.validate_gold(gold, manifest))
    # Judge-pin pre-step (fail-closed protocol drift).
    try:
        pin = judge.assert_prompt_pinned()
    except AssertionError as exc:
        issues.append(f"judge pin: {exc}")
        pin = None
    fixtures_hash = corpus.compute_fixtures_hash(root, corpus=corpus_name)
    baseline = corpus.load_baseline(root, posture=posture, corpus=corpus_name)
    baseline_issues = schema.validate_baseline(baseline)
    if baseline_issues:
        issues.extend(f"baseline ({posture}): {i}" for i in baseline_issues)
    if baseline.get("fixtures_hash") != fixtures_hash:
        issues.append(
            "baseline.fixtures_hash != on-disk corpus hash "
            f"({baseline.get('fixtures_hash')} vs {fixtures_hash}) — corpus drift"
        )
    baseline_corpus = (baseline.get("config") or {}).get("corpus")
    if baseline_corpus is not None and baseline_corpus != corpus_name:
        issues.append(
            f"baseline config corpus {baseline_corpus!r} != run corpus {corpus_name!r}"
        )
    cfg_posture = (baseline.get("config") or {}).get("extractor_posture")
    if cfg_posture != posture:
        issues.append(f"baseline config posture {cfg_posture!r} != run posture {posture!r}")
    return {
        "ok": not issues,
        "issues": issues,
        "fixtures_hash": fixtures_hash,
        "judge_pin": pin,
        "baseline": baseline,
    }


# ── The graded run ─────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


# The role whose point id is the GRADED (assembled) point per family: the
# claim / decision / old predecessor (the E2E-1 corpus's conflicted + clean
# denominators).
GRADED_ROLE: dict[str, str] = {
    "p9": "claim",
    "plain": "claim",
    "decision": "decision",
    "superseded": "old",
    "clean": "claim",
    **adversarial.GRADED_ROLE,  # #2275 adversarial families
}


def _impl(corpus_name: str):
    """The (seed, resolve_expected, grade_point) triple for a corpus.

    The adversarial corpus (#2275) shares the v1 graders and adds its own
    arms; keeping the dispatch in ONE place means the runner has no
    corpus-conditional grading logic of its own."""
    if corpus_name == corpus.ADVERSARIAL_CORPUS:
        return adversarial.seed_corpus, adversarial.resolve_expected, adversarial.grade_point
    return seeding.seed_why_corpus, grading.resolve_expected, grading.grade_point


def grade_all_points(
    sdk,
    seed_result: dict,
    gold: dict,
    log: list[str] | None = None,
    corpus_name: str = corpus.DEFAULT_CORPUS,
) -> list[dict]:
    """Assemble + grade every planted point from the SURFACED CONTEXT ALONE.

    Returns the per-point grade rows consumed by schema.aggregate_metrics.
    The canonical why-block per graded point comes from ONE batched
    ``assemble_why_blocks`` call (the W4 assembly — the same artifact the
    product surfaces consume).  A missing block (the assembly returned
    nothing for the point) grades honestly against an empty context — the
    run never silently skips a point.
    """
    log = log if log is not None else []
    from tortoise.why import assemble_why_blocks

    _, resolve_expected, grade_point = _impl(corpus_name)
    proj = sdk._get_proj()
    roles = seed_result["roles"]
    entries = gold.get("entries") or []
    graded_by_topic: dict[str, str] = {}
    for entry in entries:
        topic = entry.get("point_id")
        family = entry.get("family")
        role = GRADED_ROLE.get(family or "", "claim")
        role_map_entry = roles.get(topic) or {}
        if not role_map_entry:
            raise RuntimeError(
                f"gold entry {topic!r} has NO planted role map — corpus/gold "
                "inconsistency; the runner never grades a topic key as a "
                "point id (review P3c, #2100: a missing role used to "
                "silently grade the topic string as the id → an empty block "
                "→ a false 0)"
            )
        if role not in role_map_entry:
            raise RuntimeError(
                f"gold entry {topic!r} (family {family!r}) expects planted "
                f"role {role!r} but the role map has "
                f"{sorted(role_map_entry)} — corpus/gold inconsistency"
            )
        graded_by_topic[topic] = role_map_entry[role]
    all_ids = sorted(set(graded_by_topic.values()))
    blocks = assemble_why_blocks(proj, all_ids) or {}
    log.append(
        f"assembled {len(blocks)}/{len(all_ids)} canonical why-blocks "
        f"(batched reads; {len(all_ids)} planted graded points)"
    )
    rows: list[dict] = []
    for entry in entries:
        topic = entry.get("point_id")
        role_map_entry = roles.get(topic) or {}
        expected = resolve_expected(entry, role_map_entry)
        point_id = graded_by_topic.get(topic, topic)
        block = dict(blocks.get(point_id) or {})
        block.setdefault("point_id", point_id)
        row = grade_point(block, expected)
        row["topic"] = topic
        row["point_id"] = point_id
        rows.append(row)
    return rows


def run_benchmark(
    *,
    root: Path = WHY_DIR,
    run_id: str | None = None,
    notes: list[str] | None = None,
    log: list[str] | None = None,
    config: dict | None = None,
    progress=None,
    a4: bool = True,
    corpus_name: str = corpus.DEFAULT_CORPUS,
) -> dict:
    """Full why-suite run: preflight → seed → assemble → grade → aggregate →
    A4 arm → compare.  Returns the run report (receipt-ready).

    ``corpus_name`` selects the jointly-pinned corpus (#2275): its fixtures,
    its gold, and its OWN committed baseline lane.  The A4 arm is a v1-corpus
    eval-phase measurement (its twin pairs are planted by ``seeding``), so the
    adversarial lane records ``a4_result=None`` rather than mixing lanes."""
    log = log if log is not None else []
    run_id = run_id or f"w3b-{uuid.uuid4().hex[:12]}"
    notes = list(notes or [])
    profile = corpus.profile(corpus_name)
    seed_corpus, _, _ = _impl(corpus_name)
    env_posture = _env_posture()
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
    resolved_config["holdout_excluded"] = False  # no holdout split in this corpus
    resolved_config["corpus"] = corpus_name
    resolved_config["seed"] = profile.seed
    date = _now_iso()
    commit = _git_head()

    pf = preflight(root, posture=env_posture, corpus_name=corpus_name)
    if not pf["ok"]:
        return _failed_report(
            run_id,
            date,
            commit,
            pf["fixtures_hash"],
            resolved_config,
            origin="judge_pin_mismatch"
            if any("judge pin" in i for i in pf["issues"])
            else (
                "hash_mismatch"
                if any(
                    "corpus drift" in i or "manifest verification failed" in i for i in pf["issues"]
                )
                else (
                    "config_mismatch"
                    if any("posture" in i or "baseline (" in i for i in pf["issues"])
                    else "runner_error"
                )
            ),
            detail="; ".join(pf["issues"][:8]),
            log=log,
        )
    baseline = pf["baseline"]
    fixtures_hash = pf["fixtures_hash"]
    run_pin = pf["judge_pin"]
    if run_pin != judge.judge_pin():
        # Defensive: assert_prompt_pinned already guards drift; keep the
        # report honest if the constant itself changed.
        run_pin = judge.judge_pin()

    sdk = None
    point_results: list[dict] = []
    runner_errors: list[str] = []
    a4_result: dict | None = None
    try:
        sdk = _open_graph(run_id, log=log)
        log.append(
            f"seeding the {corpus_name} corpus ({profile.points} planted points)"
        )
        seed_result = seed_corpus(sdk)
        gold = corpus.gold_doc(root, corpus=corpus_name)
        point_results = grade_all_points(
            sdk, seed_result, gold, log=log, corpus_name=corpus_name
        )
        if len(point_results) != len(gold.get("entries") or []):
            runner_errors.append(
                f"graded {len(point_results)} points but gold has "
                f"{len(gold.get('entries') or [])} entries — a planted point "
                "was silently skipped"
            )
        if a4 and corpus_name == corpus.DEFAULT_CORPUS:
            log.append(
                "A4 arm: contested-boost vs confidence-only over the "
                "planted twin pairs (eval-phase, never gating)"
            )
            a4_result = a4_ab.measure(sdk)
            notes.append(
                f"A4 A/B: measured={a4_result['measured']} "
                f"on_rate={a4_result['contested_first_rate_on']} "
                f"off_rate={a4_result['contested_first_rate_off']} "
                f"delta={a4_result['delta']}"
            )
            notes.extend(a4_result["notes"])
    except Exception as exc:
        runner_errors.append(f"run raised {type(exc).__name__}: {exc}")
    finally:
        if sdk is not None:
            _teardown_graph(sdk)

    if runner_errors:
        report = _failed_report(
            run_id,
            date,
            commit,
            fixtures_hash,
            resolved_config,
            origin="runner_error",
            detail="; ".join(runner_errors[:8]),
            log=log,
            point_results=point_results,
        )
        report["metrics"] = schema.aggregate_metrics(point_results, corpus=corpus_name)
        report["judge_pin"] = run_pin
        return report

    metrics = schema.aggregate_metrics(point_results, corpus=corpus_name)
    log.append(
        "metrics: "
        + json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in metrics.items()})
    )
    fabricated = [r for r in point_results if r.get("fabricated_tradeoffs")]
    if fabricated:
        notes.append(
            f"Q4 anti-fabrication: {len(fabricated)} non-decision points "
            "surfaced fabricated tradeoffs: "
            + ", ".join(sorted(str(r.get("point_id")) for r in fabricated))
        )
    notes.append(
        "graded from surfaced context ONLY (A11): the grader consumed the "
        "canonical why-block per point; no graph access beyond it"
    )
    verdict = schema.compare_run(
        metrics,
        baseline,
        resolved_config=resolved_config,
        run_fixtures_hash=fixtures_hash,
        run_judge_pin=run_pin,
    )
    failure_origin = None
    if verdict == schema.VERDICT_REGRESSION:
        failure_origin = "gate_regression"
    elif verdict == schema.VERDICT_INCONCLUSIVE:
        if fixtures_hash != baseline.get("fixtures_hash"):
            failure_origin = "hash_mismatch"
        elif resolved_config != baseline.get("config"):
            failure_origin = "config_mismatch"
        elif baseline.get("judge_pin") and baseline.get("judge_pin") != run_pin:
            failure_origin = "judge_pin_mismatch"
        elif not (baseline.get("metrics") or {}):
            failure_origin = None  # first-run-pending
    return {
        "run_id": run_id,
        "date": date,
        "run_status": "completed",
        "verdict": verdict,
        "failure_origin": failure_origin,
        "commit": commit,
        "corpus_hash": fixtures_hash,
        "judge_pin": run_pin,
        "resolved_config": resolved_config,
        "cost_usd": 0.0,
        "metrics": metrics,
        "point_results": point_results,
        "a4_result": a4_result,
        "notes": notes,
        "log": log,
    }


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
    point_results: list[dict] | None = None,
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
        "point_results": point_results or [],
        "a4_result": None,
        "notes": [],
        "log": log,
    }


# ── Receipt (same shape discipline as write_path §6.6 / the W3-a harness) ─


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
        # Evidentiality: per-POINT rows an auditor can tie to the aggregate
        # metrics (the why-suite analog of the harness's per-session rows).
        "point_results": [
            {
                "point_id": r["point_id"],
                "topic": r.get("topic"),
                "family": r.get("family"),
                "clean": r.get("clean"),
                "expected_conflict": r.get("expected_conflict"),
                "expected_resolved": r.get("expected_resolved"),
                "conflict_surfaced": r.get("conflict_surfaced"),
                "resolved_ok": r.get("resolved_ok"),
                "nav_correct": r.get("nav_correct"),
                "nav_total": r.get("nav_total"),
                "nav_errors": r.get("nav_errors"),
                "support_sufficient": r.get("support_sufficient"),
                "tradeoff_sufficient": r.get("tradeoff_sufficient"),
                "fabricated_tradeoffs": r.get("fabricated_tradeoffs"),
                "false_positive": r.get("false_positive"),
                # #2275 adversarial arms (None/absent on v1 rows).
                "nand_planted": r.get("nand_planted"),
                "nand_recovered": r.get("nand_recovered"),
                "high_severity_recovered": r.get("high_severity_recovered"),
                "expected_contested": r.get("expected_contested"),
                "reads_contested": r.get("reads_contested"),
                "pointer_kinds": r.get("pointer_kinds"),
            }
            for r in report.get("point_results", [])
        ],
        "point_results_elided": not bool(report.get("point_results")),
        "a4_result": report.get("a4_result"),
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
        issues.append(f"receipt.failure_origin: unexpected {origin!r}")
    metrics = receipt.get("metrics")
    if not isinstance(metrics, dict):
        issues.append("receipt.metrics: expected an object")
    elif receipt.get("run_status") == "completed":
        missing = sorted(schema.METRIC_VALUES - set(metrics))
        if missing:
            issues.append(f"receipt.metrics: completed run missing metrics {missing}")
    rows = receipt.get("point_results")
    elided = receipt.get("point_results_elided")
    if receipt.get("run_status") == "completed":
        if elided is not None and not isinstance(elided, bool):
            issues.append("receipt.point_results_elided: expected a boolean")
        if not isinstance(rows, list) or (not rows and elided is not True):
            issues.append(
                "receipt.point_results: completed run requires per-point rows "
                "(or an explicit point_results_elided=true marker)"
            )
    elif not isinstance(rows, list):
        issues.append("receipt.point_results: expected a list")
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
    path.write_text((json.dumps(doc, indent=2, sort_keys=True) + "\n"), encoding="utf-8")


def _bless_main(
    root: Path,
    posture: str,
    justification: str,
    run_id: str | None,
    corpus_bless: bool,
    protocol_bless: bool,
    notes: list[str],
    corpus_name: str = corpus.DEFAULT_CORPUS,
) -> int:
    """--bless: run then bless the run's metrics into the posture's
    committed baseline (guards authoritative — drift / config-mismatch /
    inconclusive raise; a regression re-publish records its verdict in
    history per the fix-wave protocol)."""
    baseline_path = corpus.baseline_path(root, posture=posture, corpus=corpus_name)
    pending = corpus.load_baseline(root, posture=posture, corpus=corpus_name)
    report = run_benchmark(
        root=root, run_id=run_id, notes=notes, progress=sys.stderr, corpus_name=corpus_name
    )
    if report["run_status"] != "completed":
        print(f"run failed ({report['failure_origin']}): " + "; ".join(report.get("log", [])[-4:]))
        return 2
    try:
        failure_classes = schema.tripped_bars(report["metrics"], corpus_name)
        if corpus_name == corpus.DEFAULT_CORPUS and not (
            report["a4_result"] and report["a4_result"].get("measured")
        ):
            failure_classes.append("a4-not-measured")
        blessed = schema.bless_baseline(
            pending,
            {
                "date": report["date"],
                "fixtures_hash": report["corpus_hash"],
                "judge_pin": report["judge_pin"],
                "config": report["resolved_config"],
                "metrics": report["metrics"],
                "failure_classes": failure_classes[:4],
            },
            justification=justification,
            corpus_bless=corpus_bless,
            protocol_bless=protocol_bless,
        )
    except ValueError as exc:
        print(f"bless rejected: {exc}")
        return 3
    _write_json(baseline_path, blessed)
    receipt = build_receipt(report, justification=justification)
    receipt_issues = validate_receipt(receipt)
    if receipt_issues:
        print("RECEIPT ISSUES: " + "; ".join(receipt_issues))
        return 3
    receipts_dir = corpus.RECEIPTS_DIR
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts_dir / f"{report['run_id']}.json"
    _write_json(receipt_path, receipt)
    print(
        f"blessed {baseline_path.name}: "
        + json.dumps(
            {k: round(v, 4) if isinstance(v, float) else v for k, v in report["metrics"].items()}
        )
    )
    print(f"receipt: {receipt_path}")
    return 0


def _arg_value(args: list[str], flag: str) -> str | None:
    try:
        index = args.index(flag)
    except ValueError:
        return None
    if index + 1 < len(args):
        return args[index + 1]
    return None


def _main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    corpus_name = _arg_value(args, "--corpus") or corpus.DEFAULT_CORPUS
    try:
        corpus.profile(corpus_name)
    except ValueError as exc:
        print(str(exc))
        return 3
    run_only = not any(
        flag in args for flag in ("--bless", "--compare", "--bless-corpus", "--bless-protocol")
    )
    if "--compare" in args or run_only:
        progress = None
        if "--progress" in args:
            progress = sys.stderr
        run_config = None
        if "--full" in args:
            run_config = {"mode": "full"}
        report = run_benchmark(progress=progress, config=run_config, corpus_name=corpus_name)
        print(
            f"run {report['run_id']}: corpus={corpus_name} "
            f"status={report['run_status']} "
            f"verdict={report['verdict']} origin={report['failure_origin']}"
        )
        print(
            "metrics: "
            + json.dumps(
                {
                    k: round(v, 4) if isinstance(v, float) else v
                    for k, v in report["metrics"].items()
                }
            )
        )
        if report["run_status"] != "completed":
            print("log: " + "; ".join(report.get("log", [])[-5:]))
            return 1
        receipt = build_receipt(report)
        issues = validate_receipt(receipt)
        if issues:
            print("RECEIPT ISSUES: " + "; ".join(issues))
            return 1
        if report["verdict"] == schema.VERDICT_PASS:
            return 0
        if report["verdict"] == schema.VERDICT_REGRESSION:
            return 1  # gate regression — CI fails
        return 2  # inconclusive (pending / config / hash / pin mismatch)
    if "--bless" in args or "--bless-corpus" in args or "--bless-protocol" in args:
        posture = "m2" if os.environ.get("TORTOISE_SESSION_EXTRACTOR") == "m2" else "llm"
        justification = _arg_value(args, "--justification")
        if not justification:
            print("--bless requires --justification <text>")
            return 3
        return _bless_main(
            WHY_DIR,
            posture,
            justification,
            run_id=_arg_value(args, "--run-id"),
            corpus_bless="--bless-corpus" in args,
            protocol_bless="--bless-protocol" in args,
            notes=["blessed via CLI"],
            corpus_name=corpus_name,
        )
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
