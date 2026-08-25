#!/usr/bin/env python3
"""Deterministic canary-streak classifier (epic #1647 Task 9 Step 6, D-3=A).

The P3 canary-drop instrumentation: after N=5 consecutive green post-merge
docker runs, the embedded canary lane is retired (the plan's instrumented
streak). This script is the CLASSIFIER half of the mechanism:

  Producer (fast-matrix job, gated half=='b' && post-merge)  -> artifacts
  Classifier (this script, run by the canary-streak job)      -> streak file
  `config/testdb-canary-streak.json` records {runs, consecutive_green,
  canary_dropped} — the drop is gated on `consecutive_green >= 5`.

The classifier consumes the P1-7 ARTIFACT CONTRACT ONLY — the half-b
artifact set (junitxml + expected-nodeids + step_wall, uploaded by Task 6
Step 2 item 7) + the committed divergence-confirmation log + the PREVIOUS
streak file. It NEVER reads a steps-output/$GITHUB_OUTPUT value (cycle-6
P1-7 int-2: $GITHUB_OUTPUT is job-scoped and cannot cross to the
canary-streak job).

Classification is DETERMINISTIC (scripted, no human-in-the-loop): the same
input files always classify the same run. Buckets, in order:

  infra-flake      step_wall missing/unparseable; junitxml missing/
                   unparseable; manifest missing/unreadable; a failure whose
                   message matches the connection-refused / health-check-
                   failed family (docker service down)  -> reset to 0
  step-wall-gate   step_wall >= 3300s (55m — the E2E-5 watchdog gate) even
                   with green junitxml+manifest              -> reset to 0
  guard-red        a junitxml <skipped message> in the FalkorDB
                   availability-REGRESSION family (skip-guard's
                   is_falkor_reason_violation — the intentional families are
                   exempt)                                    -> reset to 0
  manifest-red     an expected nodeid absent from the junitxml testcases
                   (vanished nodeid)                          -> reset to 0
  divergence       failing nodeids ALL match the divergence-confirmation
                   log's expected-divergence registry (D1–D16 table entry)
                   -> NON-reset + logged (documented behavior, not a flake)
  unexpected       any other failure/error -> reset to 0
  green            zero failures, guard clean, manifest clean, step_wall OK
                   -> consecutive_green = prev + 1 (capped at the drop
                   threshold N=5); canary_dropped flips once >= N (sticky).

Only the DOCKER half (half b) is classified — the embedded lane cannot
prove the docker lane. Population: ONLY post-merge full-matrix runs count
(the workflow gates the canary-streak job on push/schedule; PR runs never
reach this script).

Usage:
  python3 tools/testdb_canary_classify.py --run-id <id> \
      --junitxml <path> --manifest <path> --step-wall <path> \
      [--divergence-log <path>] [--prev-streak <path>] [--out <path>]

The write is atomic (tmp + rename) so a concurrent reader never half-reads
the streak.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The E2E-5 watchdog gate: the fast-matrix run step's `timeout -s INT -k 10
# 55m` — a step that silently rides the 55m watchdog and passes green is a
# masked wall regression (cycle-4 P2-4: step_wall is a MANDATORY input; it
# must break the streak like any other failure).
STEP_WALL_GATE_SECONDS = 55 * 60

# The epic's N (epic indicator #2): consecutive green docker runs before the
# embedded canary lane is dropped.
CANARY_DROP_THRESHOLD = 5

# Failure-message family that identifies a docker-service infra flake
# (connection refused / health check failed — the server is down or
# unreachable), as opposed to a code failure.
_INFRA_FAILURE_MARKERS = (
    "connection refused",
    "error 111",
    "error 61",
    "health check failed",
    "failed to connect",
    "connect timeout",
)

# A failing nodeid whose failure text matches this family is infra, not code.
_INFRA_RE = re.compile("|".join(re.escape(m) for m in _INFRA_FAILURE_MARKERS),
                       re.IGNORECASE)


def _load_prev_streak(path: str | None) -> dict:
    """The previous streak file, or a fresh streak when absent/unreadable."""
    fresh = {"runs": [], "consecutive_green": 0, "canary_dropped": False}
    if not path:
        return fresh
    try:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict):
            return fresh
        return {
            "runs": data.get("runs", []) if isinstance(data.get("runs"), list)
            else [],
            "consecutive_green": int(data.get("consecutive_green", 0) or 0),
            "canary_dropped": bool(data.get("canary_dropped", False)),
        }
    except (OSError, ValueError, TypeError):
        return fresh  # a missing prev streak = a fresh chain (first run)


def _read_divergence_registry(path: str | None) -> list[str]:
    """Expected-divergence nodeid prefixes from the divergence-confirmation
    log. Lines of the form `D#: <nodeid-prefix>` (markdown list or plain
    form both parse — the prefix is the token after the colon). A failing
    nodeid matching one of these prefixes is a DOCUMENTED D1–D16 divergence
    (non-reset + logged); anything else breaks the streak.
    """
    if not path:
        return []
    prefixes = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        # `D#: <nodeid-prefix>` — the prefix is the token after the colon.
        # The marker may sit anywhere in the line (plain, `- ` list, or a
        # markdown cell) — search, don't anchor.
        m = re.search(r"\bD\d+\s*:\s*([^\s#|]+)", line)
        if m:
            prefixes.append(m.group(1))
    return prefixes


def _skip_guard_helpers():
    """Load tools/skip-guard.py (hyphenated — not importable as a module)
    and return its reason matcher + nodeid reconstructor. Mirrors
    tests/test_skip_guard.py's importlib loader."""
    import importlib.util

    tool = REPO / "tools" / "skip-guard.py"
    spec = importlib.util.spec_from_file_location("skip_guard", str(tool))
    assert spec and spec.loader, f"cannot load {tool}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.is_falkor_reason_violation, module.reconstruct_nodeid


def _read_junitxml(path: str | None) -> dict | None:
    """Parse the junitxml (xunit1) into observed/skipped/failed sets.

    Returns None on missing/malformed input (infra-flake). Mirrors
    skip-guard's reader contract (file+classname+name reconstruction); the
    reason-level FalkorDB violation check reuses skip-guard's matcher.
    """
    if not path or not os.path.exists(path):
        return None
    is_falkor_reason_violation, reconstruct_nodeid = _skip_guard_helpers()

    observed: set[str] = set()
    guard_violations: list[str] = []
    failures: list[tuple[str, str]] = []  # (nodeid, message)
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return None
    for tc in tree.iter("testcase"):
        file = tc.get("file") or ""
        classname = tc.get("classname") or ""
        name = tc.get("name") or ""
        if not file or not name:
            return None  # not xunit1 — the nodeid set is unusable (infra)
        nodeid = reconstruct_nodeid(file, classname, name)
        observed.add(nodeid)
        skipped = tc.find("skipped")
        if skipped is not None:
            reason = (skipped.get("message") or "").strip()
            if is_falkor_reason_violation(reason):
                guard_violations.append(nodeid)
            continue
        for child in tc:
            if child.tag in ("failure", "error"):
                msg = (child.get("message") or "") + "\n" + \
                      (child.text or "")
                failures.append((nodeid, msg))
    return {"observed": observed, "guard_violations": guard_violations,
            "failures": failures}


def _read_manifest(path: str | None) -> set[str] | None:
    """Expected nodeids from the manifest (one per line, '#' comments).

    Mirrors skip-guard's fail-closed contract: a non-comment line with no
    '::' is not a pytest nodeid and must surface LOUDLY (return None -> the
    caller buckets it as infra-flake) — a silently dropped line could mask a
    vanished nodeid (the #942 vacuity class the manifest exists to kill).
    """
    if not path or not os.path.exists(path):
        return None
    expected: set[str] = set()
    try:
        lines = Path(path).read_text(encoding="utf-8",
                                     errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "::" not in stripped:
            return None  # malformed manifest — unusable, fail closed
        expected.add(stripped)
    return expected


def _read_step_wall(path: str | None) -> int | None:
    """The recorded pytest-step wall in SECONDS (the run step writes
    `echo \"$SECONDS\" > /tmp/step_wall.txt`). None when unreadable OR
    empty — both mean the P1-7 contract was violated (a missing/empty wall
    must fail the streak like a missing file, never vacuous-pass)."""
    if not path or not os.path.exists(path):
        return None
    try:
        text = Path(path).read_text().strip()
        if not text:
            return None
        return int(text)
    except (OSError, ValueError):
        return None


def _read_producer_marker(path: str | None) -> dict | None:
    """The canary-producer marker (half-b leg, post-merge). None when
    missing/unreadable. The marker is the population gate's executable form:
    only a full==true half-b leg qualifies for the streak — a marker proving
    any other shape (or none) is an infra-flake (the run is not a valid
    canary population member)."""
    if not path or not os.path.exists(path):
        return None
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _bucket_failures(failures: list[tuple[str, str]],
                     registry: list[str]) -> dict:
    """Split failing nodeids into infra / expected-divergence / unexpected."""
    infra = []
    expected = []
    unexpected = []
    for nodeid, msg in failures:
        if _INFRA_RE.search(msg):
            infra.append(nodeid)
        elif any(nodeid.startswith(p) for p in registry):
            expected.append(nodeid)
        else:
            unexpected.append(nodeid)
    return {"infra": sorted(infra), "expected": sorted(expected),
            "unexpected": sorted(unexpected)}


def classify(junitxml: str | None, manifest: str | None, step_wall: str | None,
             divergence_log: str | None, prev_streak: str | None,
             run_id: int, threshold: int = CANARY_DROP_THRESHOLD,
             step_wall_gate: int = STEP_WALL_GATE_SECONDS,
             producer_marker: str | None = None) -> dict:
    """Deterministic classification of one post-merge docker-half run.

    Returns the NEW streak record + the classification bucket. Reads ONLY
    the declared input files (P1-7 artifact contract).
    """
    prev = _load_prev_streak(prev_streak)
    bucket = "green"
    detail = ""

    # Population gate (review finding #7): the producer marker is the proof
    # this run is a full==true half-b post-merge leg. Missing or wrong shape
    # -> infra-flake reset (the run never qualified for the streak).
    marker = _read_producer_marker(producer_marker)
    if producer_marker and marker is None:
        bucket = "infra-flake"
        detail = "canary-producer marker missing/unreadable (half-b leg did not qualify)"
        return _settle(prev, bucket, detail, run_id, threshold)
    if marker is not None and (
            str(marker.get("half")) != "b"
            or str(marker.get("full")) != "true"):
        bucket = "infra-flake"
        detail = (f"canary-producer marker proves a non-qualifying leg "
                  f"(half={marker.get('half')!r}, full={marker.get('full')!r}) — "
                  f"only full==true half-b runs populate the streak")
        return _settle(prev, bucket, detail, run_id, threshold)

    wall = _read_step_wall(step_wall)
    if wall is None:
        bucket = "infra-flake"
        detail = "step_wall missing/unparseable (P1-7 contract violated)"
        return _settle(prev, bucket, detail, run_id, threshold)

    if wall >= step_wall_gate:
        bucket = "step-wall-gate"
        detail = f"step_wall {wall}s >= {step_wall_gate}s (E2E-5 watchdog gate)"
        return _settle(prev, bucket, detail, run_id, threshold)

    parsed = _read_junitxml(junitxml)
    if parsed is None:
        bucket = "infra-flake"
        detail = "junitxml missing/unparseable (not xunit1)"
        return _settle(prev, bucket, detail, run_id, threshold)

    expected = _read_manifest(manifest)
    if expected is None:
        bucket = "infra-flake"
        detail = "manifest missing/unreadable (a vanished manifest must not vacuous-green)"
        return _settle(prev, bucket, detail, run_id, threshold)

    if parsed["guard_violations"]:
        bucket = "guard-red"
        detail = (f"{len(parsed['guard_violations'])} FalkorDB-reasoned skip(s): "
                  f"{parsed['guard_violations'][:5]}")
        return _settle(prev, bucket, detail, run_id, threshold)

    missing = sorted(expected - parsed["observed"])
    if missing:
        bucket = "manifest-red"
        detail = f"{len(missing)} expected nodeid(s) vanished: {missing[:5]}"
        return _settle(prev, bucket, detail, run_id, threshold)

    splits = _bucket_failures(parsed["failures"],
                              _read_divergence_registry(divergence_log))
    if splits["infra"]:
        bucket = "infra-flake"
        detail = (f"{len(splits['infra'])} connection-refused/health-check "
                  f"failure(s): {splits['infra'][:5]}")
        return _settle(prev, bucket, detail, run_id, threshold)
    if splits["unexpected"]:
        bucket = "unexpected-divergence"
        detail = (f"{len(splits['unexpected'])} failure(s) not in the D1–D16 "
                  f"registry: {splits['unexpected'][:5]}")
        return _settle(prev, bucket, detail, run_id, threshold)
    if splits["expected"]:
        bucket = "divergence"
        detail = (f"{len(splits['expected'])} documented D1–D16 divergence(s): "
                  f"{splits['expected'][:5]} (logged, streak preserved)")
        return _settle(prev, bucket, detail, run_id, threshold)

    bucket = "green"
    detail = "manifest + tripwire + step-wall green"
    return _settle(prev, bucket, detail, run_id, threshold)


def _settle(prev: dict, bucket: str, detail: str, run_id: int,
            threshold: int) -> dict:
    """Apply the bucket to the previous streak and return the new record."""
    runs = [r for r in prev.get("runs", []) if isinstance(r, int)]
    if run_id not in runs:
        runs = [run_id] + runs
    runs = runs[:20]  # bounded history — the artifact stays small

    reset_buckets = {"infra-flake", "step-wall-gate", "guard-red",
                     "manifest-red", "unexpected-divergence"}
    if bucket in reset_buckets:
        consecutive = 0
    elif bucket == "green":
        consecutive = min(prev.get("consecutive_green", 0) + 1, threshold)
    else:  # "divergence" — documented D1–D16 entry, streak preserved
        consecutive = prev.get("consecutive_green", 0)

    canary_dropped = prev.get("canary_dropped", False) or \
        (consecutive >= threshold)
    return {
        "run_id": run_id,
        "runs": runs,
        "consecutive_green": consecutive,
        "canary_dropped": canary_dropped,
        "last": {"bucket": bucket, "detail": detail},
    }


def _write_atomic(record: dict, out: str | None) -> None:
    """Write the streak record via tmp + atomic rename (a concurrent writer
    can never half-read — the plan's producer atomicity contract)."""
    path = Path(out) if out else REPO / "config" / "testdb-canary-streak.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True, type=int)
    ap.add_argument("--junitxml", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--step-wall", required=True)
    ap.add_argument("--divergence-log", default=None)
    ap.add_argument("--prev-streak", default=None)
    ap.add_argument("--producer-marker", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--threshold", type=int, default=CANARY_DROP_THRESHOLD)
    ap.add_argument("--step-wall-gate", type=int,
                    default=STEP_WALL_GATE_SECONDS)
    args = ap.parse_args(argv)

    record = classify(
        args.junitxml, args.manifest, args.step_wall, args.divergence_log,
        args.prev_streak, args.run_id,
        threshold=args.threshold, step_wall_gate=args.step_wall_gate,
        producer_marker=args.producer_marker)
    _write_atomic(record, args.out)

    dropped = " — CANARY DROPPED (consecutive green >= N)" \
        if record["canary_dropped"] else ""
    print(f"run {args.run_id}: bucket={record['last']['bucket']} "
          f"({record['last']['detail']})")
    print(f"streak: consecutive_green={record['consecutive_green']}, "
          f"canary_dropped={record['canary_dropped']}{dropped}")
    print(f"streak written -> {args.out or 'config/testdb-canary-streak.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
