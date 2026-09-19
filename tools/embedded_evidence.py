#!/usr/bin/env python3
"""embedded_evidence.py — the embedded-lane evidence producer (#3827, RED half).

One command runs a named, recorded selection N times in fresh subprocesses at a
pinned commit and **classifies every run (never counts it)**, demonstrates a RED
for the same selection at a named pre-fix ref in the same lane, and **enforces**
`closes_issue` as an exit code — so the embedded family's exit evidence becomes a
receipt a reviewer can falsify.

This is the RED half (PR #1). The GREEN half (N consecutive green at the fixed
commit) has no referent while the family lane is red; see the plan doc
`docs/plans/2026-09-17-3827-embedded-lane-evidence-producer.md`.

It is a COMPOSITION, not a rebuild: it calls the existing primitives
(`tools/skip-guard.py::emit_manifest`, `tools/ci_selection.py::select` +
`load_manifest` + `carve_out_files`, `tools/embedded_orphans.py::census`,
`tools/testdb_canary_classify.py`'s vocabulary) and adds the pin, the paired red,
the cause label and the closing rule.

⛔ Certification rules (verdict 2026-09-18):
  R1 (D23) — certification binds to the SHIPPING surface (`tortoise_search` /
             `tortoise_recall`), never an internal helper.
  R2 (D24) — the certificate is bound to the reviewed head SHA and re-run after
             any post-review edit; the required mutation operator is statement
             deletion of the fix's own population/edit branch.
  Context: issue #3888 shipped 17 tests + a clean review + VGATE PASS, yet
  deleting the population branch at `sdk.py:13202`/`:13239-13245` left the suite
  GREEN while `tortoise_search` and `tortoise_recall` both returned
  `sessionId:''`. The manual proof covered the READ path only.

Usage:
  python3 tools/embedded_evidence.py run  --selection family [--n 3] [--ref <sha>]
  python3 tools/embedded_evidence.py red  --selection family [--ref <sha>]
  python3 tools/embedded_evidence.py classify --redis-log <path>

Exit codes: 0 = all conjuncts hold (closing); 1 = violation (a red / moved tree /
unattributable); 2 = environment error (measurement impossible); 3 = NOT-CLOSING.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# The DECLARED LOCAL N (D3827-b / D13). No source makes any N canonical: the
# four practitioner sources describe detect -> quarantine -> fix and do NOT fix
# N. N=10 is OURS, used to CERTIFY where the field uses N to DENY.
# ---------------------------------------------------------------------------
DEFAULT_N = 10
DEFAULT_MAX_RUNS = 50
DEFAULT_RUN_TIMEOUT_S = 900

# ---------------------------------------------------------------------------
# Selection (D5) — the family's evidence selection.
# ---------------------------------------------------------------------------
FAMILY_REPRODUCERS = (
    "tests/test_dr_endpoints.py",
    "tests/test_hosted_backup.py",
    "tests/test_backup_e2e.py",
)
MANDATORY_REPRODUCER = "tests/test_dr_endpoints.py"
DEFAULT_MARKER = "not track_b and not live"

# ---------------------------------------------------------------------------
# Load bands (D14) — half-open, deterministic at the boundaries.
# ---------------------------------------------------------------------------
LOAD_BANDS: tuple[tuple[str, float, float], ...] = (
    ("L-A", 0.0, 12.0),
    ("L-B", 12.0, 24.0),
    ("L-C", 24.0, float("inf")),
)
DEFAULT_LOAD_CEILING = 60.0


def load_band(value: float) -> str:
    """The declared half-open band for a load1 value (12.0 -> L-B, 24.0 -> L-C)."""
    for name, lo, hi in LOAD_BANDS:
        if lo <= value < hi:
            return name
    return LOAD_BANDS[-1][0]


def load1() -> float:
    """The host's 1-minute load average."""
    try:
        return float(os.getloadavg()[0])
    except (OSError, AttributeError):
        return -1.0


# ---------------------------------------------------------------------------
# Cause classes for the red (D10 / F15(i)).
#
# `GRAPH.COPY failed, could not fork` is emitted by TWO mechanically distinct
# causes, and the module-fork refusal line is BYTE-IDENTICAL between them:
#   - save/child-slot: a background RDB save (or AOF rewrite) child occupies the
#     child slot, so RedisModule_Fork refuses with "File exists".
#   - module-fork-hang (#3845): a PREVIOUS module fork child never exited, so
#     the next RM_Fork refuses with the same "File exists".
# The discriminator is the presence of the save/rewrite lines AND the ABSENCE of
# a matching `Module fork exited pid:` for every `Module fork started pid:`
# (for the module-fork-hang class).
# ---------------------------------------------------------------------------
CAUSE_PRECEDENCE = ("module-fork-hang", "module-fork-eexist", "aof-rewrite-fork", "save-child-slot")

CAUSE_CLASSES: dict[str, dict] = {
    # #3845: a previous RM_Fork child never exited, so the next refusal is
    # EEXIST. Discriminator: a `Module fork started pid:` with NO matching
    # `Module fork exited pid:` (the ABSENCE is the proof).
    "module-fork-hang": {
        "requires_lines": [r"Can't fork for module:"],
        "requires_absent": [],
        "requires_unexited_fork": True,
    },
    # EEXIST refusal with NO save/AOF discriminator present.
    #
    # Observed on the family reproducer: `Can't fork for module: File exists`
    # appears with NO `Background saving` and NO BGREWRITEAOF line, so both
    # discard-candidates above are excluded and this fell through to
    # `unattributed` — a cause the log DOES state, thrown away.
    #
    # It is deliberately NOT folded into `module-fork-hang`: that class's
    # discriminator is the ABSENCE of a matching `Module fork exited pid:`, and
    # the reviewer's Devil's-Advocate finding is precisely that the two
    # mechanically distinct causes emit a BYTE-IDENTICAL refusal. Folding this in
    # would re-conflate what the discriminator exists to separate. A refusal with
    # no `Module fork started` in THIS log means the slot was held by a child of a
    # PRIOR instance — a different run — so its started line is not in this file.
    # That is a distinct, honestly-labellable fact, so it gets its own name rather
    # than being forced into a neighbour or lost.
    "module-fork-eexist": {
        "requires_lines": [r"Can't fork for module:"],
        "requires_absent": [r"Background saving", r"Starting BGREWRITEAOF"],
        "requires_unexited_fork": False,
    },
    # appendonly yes -> a background AOF rewrite child occupies the slot.
    "aof-rewrite-fork": {
        "requires_lines": [
            r"(Starting BGREWRITEAOF|Background AOF rewrite (started|finished))",
        ],
        "requires_absent": [],
        "requires_unexited_fork": False,
    },
    # The RDB save child (`--save ''` asymmetry) occupies the slot; the refusal
    # line is BYTE-IDENTICAL to module-fork-hang, so the save lines are the
    # discriminator. No stale module fork is required (and none may be present
    # with an unexited pid, else precedence labels it module-fork-hang).
    "save-child-slot": {
        "requires_lines": [r"Background saving (started|terminated)"],
        "requires_absent": [r"Starting BGREWRITEAOF"],
        "requires_unexited_fork": False,
    },
    "unattributed": {
        "requires_lines": [], "requires_absent": [],
        "requires_unexited_fork": False,
    },
}

# The set of causes a RED may be labelled with — DERIVED from CAUSE_CLASSES, never a
# hand-written copy. The copy is how `module-fork-eexist` (added to DETECT a real
# cause) reached CAUSE_CLASSES while `expected_causes` kept the old three: the class
# that exists to detect the cause became the one that invalidated the record
# (`cause-not-expected`) the moment it fired, so that cause could never produce a
# valid RED. Deriving makes the omission unrepresentable — a class added to detect a
# cause IS, by construction, a cause the record expects. `unattributed` is excluded:
# it is the absence of a cause, and closes_issue() rejects it separately
# (`cause-unattributed`).
EXPECTED_CAUSES: tuple[str, ...] = tuple(c for c in CAUSE_CLASSES if c != "unattributed")

# The sibling of the same bug, one line away: a class in CAUSE_CLASSES but absent from
# CAUSE_PRECEDENCE is UNREACHABLE — label_cause() only visits CAUSE_PRECEDENCE, so the
# class would be declared, documented and never emitted. Fail closed here rather than
# ship a class that can never fire.
assert set(CAUSE_PRECEDENCE) | {"unattributed"} == set(CAUSE_CLASSES), (
    "CAUSE_CLASSES and CAUSE_PRECEDENCE disagree — declared but unreachable: "
    f"{sorted(set(CAUSE_CLASSES) - set(CAUSE_PRECEDENCE) - {'unattributed'})}"
)

# The refusal line that both save-child-slot and module-fork-hang emit.
FORK_REFUSAL_RE = re.compile(r"Can't fork for module:")
MODULE_FORK_STARTED_RE = re.compile(r"Module fork started pid:\s*(\d+)")
MODULE_FORK_EXITED_RE = re.compile(r"Module fork exited pid:\s*(\d+)")
BGSAVE_RE = re.compile(r"Background saving (started|terminated)")
AOF_REWRITE_RE = re.compile(r"(Starting BGREWRITEAOF|Background AOF rewrite (started|finished))")


def label_cause(lines: list[str]) -> tuple[str, dict]:
    """Label the red's cause from server-side `redis.log` lines (D10).

    Returns `(cause, evidence)`. `evidence` carries the matched lines, whether a
    fork refusal appeared, and whether every `Module fork started pid:` has a
    matching `Module fork exited pid:` (the module-fork-hang discriminator).

    NOTE (GAP-5): the regexes are pinned against a captured real redis.log; a log
    matching none of the declared patterns is `unattributed` and can never close.
    """
    text = "\n".join(lines)
    started = set(MODULE_FORK_STARTED_RE.findall(text))
    exited = set(MODULE_FORK_EXITED_RE.findall(text))
    unexited = started - exited

    for cause in CAUSE_PRECEDENCE:
        spec = CAUSE_CLASSES[cause]
        hits = [
            ln for ln in lines
            if any(re.search(p, ln) for p in spec["requires_lines"])
        ]
        if not hits:
            continue
        if spec.get("requires_unexited_fork") and not unexited:
            # The refusal came from a save/AOF child, not a stale module child.
            continue
        if any(re.search(p, text) for p in spec["requires_absent"]):
            continue
        return cause, {
            "matched_lines": hits[:20],
            "fork_refusal": bool(FORK_REFUSAL_RE.search(text)),
            "module_forks_started": sorted(started),
            "module_forks_exited": sorted(exited),
            "module_forks_unexited": sorted(unexited),
            "module_fork_exited_absent": bool(started) and not exited,
        }

    return "unattributed", {
        "matched_lines": [],
        "fork_refusal": bool(FORK_REFUSAL_RE.search(text)),
        "module_forks_started": sorted(started),
        "module_forks_exited": sorted(exited),
        "module_forks_unexited": sorted(unexited),
        "module_fork_exited_absent": bool(started) and not exited,
    }


def attributable(cause: str | None) -> bool:
    """Whether a red's cause is an ATTRIBUTED one — the DERIVATION behind
    `verdict.attributable` (it was the literal `True`, so a record with
    `red.cause == null` claimed an attribution it did not have).

    `None` = no red run at all; `"unattributed"` = the label_cause() fallback, i.e.
    the log stated no cause this tool recognises. Neither is attributable.
    """
    return bool(cause) and cause != "unattributed"


# ---------------------------------------------------------------------------
# Shipping surfaces (D23 / R1) — where a consumer actually looks.
# ---------------------------------------------------------------------------
SHIPPING_SURFACES = ("tortoise_search", "tortoise_recall")


# ---------------------------------------------------------------------------
# Selection + manifest.
# ---------------------------------------------------------------------------
def _resolve_selection(name: str, manifest: dict) -> list[str]:
    if name == "family":
        return list(FAMILY_REPRODUCERS)
    if name == "carve-out":
        from tools.ci_selection import carve_out_files
        return sorted(f"tests/{f}" for f in carve_out_files(manifest))
    if name == "whole-suite":
        from tools.ci_selection import load_manifest
        m = load_manifest()
        core = set(m.get("surfaces", {}).get("core", []))
        return sorted(f"tests/{f}" for f in core)
    raise ValueError(f"unknown selection: {name!r}")


def _manifest_receipt(files: list[str], marker: str, out_dir: Path) -> dict:
    """Reuse skip_guard.emit_manifest for the resolved nodeid manifest (D5.4)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "skip_guard", str(REPO_ROOT / "tools" / "skip-guard.py")
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    out = out_dir / "manifest.txt"

    def _runner(cmd: list[str]):
        # The manifest collect-only MUST run under the same interpreter the runs
        # do, or a 3.9 child fails in conftest and reports it as rc=4.
        if cmd and cmd[0] != _python():
            cmd = [_python(), *cmd[1:]]
        env = _child_env(out_dir)
        (out_dir / "pi3827_capture.py").write_text(_CAPTURE_PLUGIN)
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
        # A fail-closed guard must not discard its own diagnostic. `rc` alone says
        # "pytest usage error" and nothing about WHY — and rc=4 is also what a
        # missing path yields, so the cause is unrecoverable without the stream.
        # The first version of this dropped p.stderr, which made a deterministic
        # manifest failure take a dozen probes to localize. Persist the child's
        # argv, cwd and stderr so the failure is self-describing.
        if p.returncode != 0:
            (out_dir / "collect-diagnostic.txt").write_text(
                "argv: " + " ".join(cmd) + "\n"
                + "cwd: " + str(REPO_ROOT) + "\n"
                + "TMPDIR: " + str(env.get("TMPDIR")) + "\n"
                + "PYTHONPATH: " + str(env.get("PYTHONPATH")) + "\n"
                + "--- child stderr ---\n" + (p.stderr or "(empty)")
                + "--- child stdout (tail) ---\n" + (p.stdout or "")[-2000:]
            )
            print(
                "emit-manifest: collect-only failed rc=%d — child stderr:\n%s"
                % (p.returncode, (p.stderr or "(empty stderr)").strip()),
                file=sys.stderr,
            )
        return p.returncode, p.stdout

    rc = mod.emit_manifest(files, marker, out, runner=_runner)
    if rc != 0 or not out.exists():
        raise RuntimeError(f"emit_manifest failed rc={rc}")
    nodeids = [
        ln for ln in out.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    ]
    digest = "sha256:" + hashlib.sha256(out.read_bytes()).hexdigest()
    return {
        "path": str(out),
        "digest": digest,
        "count": len(nodeids),
        "unique_count": len(set(nodeids)),
        "marker": marker,
    }


# ---------------------------------------------------------------------------
# The capture plugin — captures the real redis.log BEFORE fixture teardown.
#
# The embedded daemon's `redis_dir` is rmtree'd by redislite `_cleanup` when the
# test's client fixture tears down — i.e. milliseconds after the fork refusal is
# logged. An out-of-process poller (even at 30ms) misses it. This plugin runs
# IN the pytest process and snapshots every `redis.log` at the moment a test's
# call phase reports failure, which is BEFORE its fixtures tear down.
# ---------------------------------------------------------------------------
_CAPTURE_PLUGIN = '''\
"""pi3827_capture — snapshot redis.log at failure time (#3827)."""
import os
from pathlib import Path

_EVID = Path(os.environ.get("PI3827_EVIDENCE_DIR", "."))


def _snap():
    import tempfile
    base = Path(tempfile.gettempdir())
    for p in base.glob("tmp*/redis.log"):
        try:
            data = p.read_bytes()
        except Exception:
            continue
        try:
            (_EVID / (p.parent.name + ".redis.log")).write_bytes(data)
        except Exception:
            pass


def pytest_runtest_makereport(item, call):
    if call.when == "call" and call.excinfo is not None:
        _snap()


def pytest_sessionfinish(session, exitstatus):
    _snap()
'''


def _python() -> str:
    """The interpreter the CHILDREN must run under.

    `sys.executable` is whatever launched this tool — and on this box `python3` is
    3.9 from the Command Line Tools while the repo's .venv is 3.12. The suite
    imports `enum.StrEnum` (3.11+), so a 3.9 child dies inside tests/conftest.py
    with `ImportError: cannot import name 'StrEnum'`, and pytest reports that as a
    USAGE-class rc=4 — which reads like a bad path and is not. Prefer the repo
    venv so the child matches the environment the suite is actually installed
    into; a launch flag must not decide whether the evidence run works.
    """
    for cand in (REPO_ROOT / ".venv" / "bin" / "python",):
        if cand.exists():
            return str(cand)
    return sys.executable


def _short_tmp_root(run_root: Path) -> Path:
    """A SHORT, stable TMPDIR for the child — deliberately NOT under run_root.

    The embedded Redis binds a Unix socket below its own working dir, and macOS caps
    that path at 104 bytes. Nesting the child's TMPDIR under the harness's run root
    (`$TMPDIR/pi-embedded-evidence-XXXX/tmp/tmpYYY/` = 14 dir chars + the socket
    name) produced a 107-byte path, so Redis NEVER STARTED:

        # Failed opening Unix socket: unix socket path too long (107), must be under 104

    The run then hung at fixture setup with executed=0, and the harness reported that
    as `timeout-red` — an ENVIRONMENT failure presented as the family RED. A temp
    root is a budget, and the harness was spending it on directory names.
    """
    tag = hashlib.sha256(str(run_root).encode()).hexdigest()[:8]
    return Path("/tmp") / f"pi3827-{tag}"


def _child_env(run_root: Path) -> dict:
    env = dict(os.environ)
    # Pop every lane variable so a dev shell cannot flip the child's lane.
    for var in (
        "TORTOISE_DB_URI", "TORTOISE_TEST_EXPECT_URI", "TORTOISE_TEST_ALLOW_REMOTE",
        "TORTOISE_TEST_NO_REDIRECT", "TORTOISE_TEST_JOURNAL_FILE", "TORTOISE_DB_PATH",
        "TORTOISE_EMBEDDED_AOF", "TORTOISE_ALLOW_NONSTANDARD_PATH",
    ):
        env.pop(var, None)
    tmp = _short_tmp_root(run_root) / "t"
    tmp.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(tmp)
    env["TORTOISE_TEST_CARVE_OUT"] = "1"
    env["PI3827_EVIDENCE_DIR"] = str(run_root / "evidence")
    (run_root / "evidence").mkdir(parents=True, exist_ok=True)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(run_root), str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    return env


def _read_junit_counts(path: Path) -> dict:
    import xml.etree.ElementTree as ET
    if not path.exists():
        return {"executed": 0, "skipped": 0, "failed": 0, "observed": 0}
    root = ET.parse(path).getroot()
    cases = root.iter("testcase")
    executed = skipped = failed = observed = 0
    for c in cases:
        observed += 1
        if c.find("skipped") is not None:
            skipped += 1
        else:
            executed += 1
        if c.find("failure") is not None or c.find("error") is not None:
            failed += 1
    return {"executed": executed, "skipped": skipped, "failed": failed, "observed": observed}


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=str(cwd or REPO_ROOT)
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _porcelain_digest(cwd: Path, exclude: Path | None = None) -> tuple[str, bool]:
    status = _git("status", "--porcelain=v2", cwd=cwd)
    diff = _git("diff-index", "HEAD", cwd=cwd)
    blob = (status + "\n" + diff + "\n").encode()
    if exclude is not None:
        # The record-out path is excluded from the pin (M5): strip its line.
        ex = str(exclude.relative_to(cwd)) if exclude.is_relative_to(cwd) else str(exclude)
        blob = b"\n".join(
            ln for ln in blob.splitlines() if ex.encode() not in ln
        ) + b"\n"
    return "sha256:" + hashlib.sha256(blob).hexdigest(), bool(status.strip())


def _snapshot_redis_logs(run_root: Path) -> list[Path]:
    """Copy every redis.log the child's TMPDIR holds into evidence/.

    Mirrors the capture plugin's `_snap`, but callable from the HARNESS. The plugin
    can only fire from a pytest hook (`pytest_runtest_makereport` / sessionfinish),
    and the case that needs this most is the one where no hook ever runs — a hang
    before any test executes. Both measured runs hit the bound with executed=0, so
    the plugin was silent and `red_cause` was null by construction, not by absence
    of a cause. Same class as the rest of this lane: an observer waiting on a proxy
    that is silent in exactly the case it exists to cover.
    """
    evid = run_root / "evidence"
    evid.mkdir(parents=True, exist_ok=True)
    found: list[Path] = []
    # The child's TMPDIR is the SHORT root, not run_root/tmp — see _short_tmp_root.
    roots = [_short_tmp_root(run_root), run_root / "tmp"]
    for root in roots:
        for p in sorted(root.glob("**/redis.log")):
            try:
                (evid / (p.parent.name + ".redis.log")).write_bytes(p.read_bytes())
                found.append(p)
            except OSError:
                continue
    return found


def _run_once(
    files: list[str],
    measured_root: Path,
    run_root: Path,
    run_id: int,
    marker: str,
    timeout: int,
) -> dict:
    junit = run_root / f"junit-{run_id}.xml"
    cmd = [
        _python(), "-m", "pytest", *files,
        "-q", "-p", "no:cacheprovider",
        f"--timeout={max(30, timeout // 3)}",
        f"--junitxml={junit}",
        "-m", marker,
        "-p", "pi3827_capture",
    ]
    env = _child_env(run_root)
    (run_root / "pi3827_capture.py").write_text(_CAPTURE_PLUGIN)
    before = load1()
    started = time.time()
    timed_out = False
    child_out = ""
    # Popen + communicate, NOT subprocess.run(timeout=...): run() re-raises
    # TimeoutExpired and NEVER retrieves the captured pipes, so the child's output —
    # the only diagnostic when a run is killed — is destroyed by the exact path that
    # needs it. Both measured runs died this way with no output kept.
    proc = subprocess.Popen(
        cmd, cwd=str(measured_root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        child_out, _ = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        rc = 124
        proc.kill()
        try:
            child_out, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            child_out = ""
        _snapshot_redis_logs(run_root)
        (run_root / f"child-output-{run_id}.txt").write_text(child_out or "")
    wall = time.time() - started
    after = load1()
    counts = _read_junit_counts(junit)
    bucket = "green" if rc == 0 and counts["failed"] == 0 else "unexpected-divergence"
    if timed_out:
        bucket = "timeout-red"
    elif not junit.exists():
        bucket = "selection-red"
    # Cause label from any captured redis.log (best-effort per run).
    cause = None
    evidence: dict = {}
    for log in sorted((run_root / "evidence").glob("*.redis.log")):
        try:
            lines = log.read_text(errors="replace").splitlines()
        except OSError:
            continue
        c, ev = label_cause(lines)
        if c != "unattributed" or ev.get("fork_refusal"):
            cause, evidence = c, {"redis_log": str(log), **ev}
            if c != "unattributed":
                break
    return {
        "run_id": run_id,
        "bucket": bucket,
        "returncode": rc,
        "step_wall_s": round(wall, 2),
        "observed": counts["observed"],
        "executed": counts["executed"],
        "skipped": counts["skipped"],
        "load": {"before": before, "after": after, "band": load_band(after)},
        "tree_moved": False,
        "redis_log_cause": cause,
        "cause_evidence": evidence,
        "timed_out": timed_out,
    }


# ---------------------------------------------------------------------------
# Closing rule (D9) — the subset that the RED half can evaluate honestly.
# ---------------------------------------------------------------------------
def closes_issue(rec: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    def conj(name: str, value: bool) -> bool:
        if not value:
            reasons.append(name)
        return value

    ok = True
    ok &= conj("runs-empty", bool(rec["runs"]))
    ok &= conj("non-green-bucket",
               all(r["bucket"] in ("green", "slow-run") for r in rec["runs"]))
    ok &= conj("no-test-executed", all(r["executed"] >= 1 for r in rec["runs"]))
    ok &= conj("selection-not-family", rec["selection"]["name"] == "family")
    ok &= conj("reproducer-absent",
               any(MANDATORY_REPRODUCER.endswith(f) or MANDATORY_REPRODUCER in f
                   for f in rec["selection"]["files"]))
    ok &= conj("pin-not-airtight",
               rec["pin"]["worktree_clean"] and all(not r["tree_moved"] for r in rec["runs"]))
    ok &= conj("cause-unattributed",
               rec["red"]["cause"] in CAUSE_CLASSES and rec["red"]["cause"] != "unattributed")
    ok &= conj("cause-not-expected",
               rec["red"]["cause"] in rec["selection"]["expected_causes"])
    ok &= conj("red-file-list-differs", rec["red"]["same_file_list"])
    ok &= conj("load-bands-do-not-overlap", rec["load"]["overlap"])
    ok &= conj("no-rate-change",
               (rec["red"]["at_fixed_commit"]["attempted"]
                and not rec["red"]["at_fixed_commit"]["appeared"]
                and rec["red"]["at_fixed_commit"]["rate_change"])
               or (bool(rec["red"]["at_fixed_commit"]["mutation"])
                   and str(rec["red"]["at_fixed_commit"]["mutation_operator"]).startswith("statement-deletion:")
                   and rec["red"]["at_fixed_commit"]["mutation_target_is_fix_branch"]
                   and not rec["red"]["at_fixed_commit"]["appeared"]
                   and rec["red"]["at_fixed_commit"]["mutation_red_returned"]))
    ok &= conj("record-role-not-closing", rec["record_role"] == "closing")
    # R1 (D23): certification binds to the shipping surface.
    ok &= conj("certification-not-on-shipping-surface",
               rec["red"]["at_fixed_commit"]["surface"] in SHIPPING_SURFACES
               and bool(rec["red"]["at_fixed_commit"]["surface_assertion"]))
    # R2 (D24): certificate bound to the reviewed head SHA.
    ok &= conj("certificate-not-bound-to-review-head",
               rec["pin"]["head_sha"] == rec["pin"]["commit"]
               and not rec["pin"]["post_review_dirty"])
    return ok, reasons


def exit_code(rec: dict) -> int:
    ok, _ = closes_issue(rec)
    if ok:
        return 0
    if any(r["bucket"] not in ("green", "slow-run") for r in rec["runs"]):
        return 1
    if rec["verdict"].get("environment_error"):
        return 2
    return 3


def _tool_version() -> str:
    blob = _git("hash-object", str(Path(__file__).resolve()))
    return blob


def _write_record(rec: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=".rec-", suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(rec, fh, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, out)


def _build_record(args: argparse.Namespace) -> dict:
    from tools.ci_selection import load_manifest

    run_root = Path(tempfile.mkdtemp(prefix="pi-embedded-evidence-"))
    evidence = run_root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (run_root / "pi3827_capture.py").write_text(_CAPTURE_PLUGIN)

    manifest = load_manifest()
    files = _resolve_selection(args.selection, manifest)
    mrec = _manifest_receipt(files, args.marker, run_root)

    requested_ref = None
    measured_root = REPO_ROOT
    worktree_added = False
    if args.ref:
        requested_ref = _git("rev-parse", f"{args.ref}^{{commit}}")
        head = _git("rev-parse", "HEAD")
        if requested_ref != head:
            wt = run_root / "worktree"
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(wt), requested_ref],
                capture_output=True, text=True, cwd=str(REPO_ROOT), check=True,
            )
            measured_root = wt
            worktree_added = True
    commit = _git("rev-parse", "HEAD", cwd=measured_root)
    tree = _git("rev-parse", "HEAD^{tree}", cwd=measured_root)

    ceiling = args.load_ceiling
    cur_load = load1()
    if args.environment_error:
        raise RuntimeError(args.environment_error)

    runs: list[dict] = []
    porcelain = ""
    dirty = False
    try:
        if cur_load > ceiling:
            raise RuntimeError(f"load {cur_load} exceeds ceiling {ceiling}")
        for i in range(1, args.n + 1):
            runs.append(_run_once(files, measured_root, run_root, i, args.marker,
                                  args.run_timeout))
        # The cleanliness digest MUST be taken while the measured tree still
        # EXISTS. It used to run after this `finally`, which removes the detached
        # worktree — so a --ref measurement stat'd a path that was already gone
        # and died with FileNotFoundError, losing the one field that says the tree
        # did not move. Read state before the code that deletes it.
        porcelain, dirty = _porcelain_digest(measured_root, exclude=args.record_out)
    finally:
        if worktree_added:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(measured_root)],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
            )

    red_run = next((r for r in runs if r["bucket"] not in ("green", "slow-run")), None)
    bands = {r["load"]["band"] for r in runs}
    green_runs = [r for r in runs if r["bucket"] in ("green", "slow-run")]
    red_runs = [r for r in runs if r["bucket"] not in ("green", "slow-run")]
    red_band = (red_run or runs[-1])["load"]["band"]
    green_band = (green_runs[0]["load"]["band"] if green_runs else red_band)
    cause = red_run["redis_log_cause"] if red_run else None
    cause_evidence = red_run["cause_evidence"] if red_run else {}
    # DERIVED from the label it summarises, never a literal (it was `True`).
    # `attributable` is a claim ABOUT `red.cause`, so a record with `red.cause ==
    # null` (no red run) or `unattributed` was claiming an attribution it does not
    # have.
    attributable_ = attributable(cause)

    rec = {
        "schema": "embedded-evidence/1",
        "attestation": "self-declared",
        "record_role": args.record_role,
        "tool_version": _tool_version(),
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selection": {
            "name": args.selection,
            "files": files,
            "expected_causes": list(EXPECTED_CAUSES),
        },
        "manifest": mrec,
        "pin": {
            "commit": commit,
            "head_sha": commit,
            "head_sha_verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "post_review_dirty": False,
            "tree_object": tree,
            "requested_ref": requested_ref,
            "pairing_ref": None,
            "worktree_clean": not dirty,
            "porcelain_digest": porcelain,
            "record_out_excluded": str(args.record_out) if args.record_out else None,
            "measured_root": str(measured_root),
            "environment_pinned": False,
        },
        "n": {
            "requested": args.n,
            "mode": "explicit",
            "observed_failure_rate": (len(red_runs) / len(runs)) if runs else 0.0,
            "max_runs": DEFAULT_MAX_RUNS,
            "declared_local": True,
            "note": "N=10 is DECLARED LOCAL — no source makes any N canonical.",
        },
        "runs": runs,
        "load": {
            "bands": {n: f"{lo} <= x < {hi if hi != float('inf') else 'inf'}"
                      for n, lo, hi in LOAD_BANDS},
            "ceiling": ceiling,
            "ceiling_source": "DEFAULT_LOAD_CEILING / operator --load-ceiling",
            "declared_band": red_band,
            "green_band": green_band,
            "red_band": red_band,
            "overlap": bool(bands) and len(bands) == 1,
        },
        "environment": {
            "lane": {"uri_unset": True, "carve_out": "1", "expect_uri": False},
            "invocation_id": uuid.uuid4().hex,
            "tmpdir": str(run_root / "tmp"),
            "ledger_root": str(run_root),
        },
        "red": {
            "ref": requested_ref or commit,
            "ref_role": "pinned-head-pre-fix" if not args.pairing_ref else "last-before-first-family-fix",
            "ref_tree_object": tree,
            "cause": cause,
            "cause_evidence": cause_evidence,
            "red_green_mix": {"red": len(red_runs), "green": len(green_runs)},
            "at_fixed_commit": {
                "attempted": False,
                "appeared": None,
                "rate_change": False,
                "mutation": None,
                "mutation_operator": None,
                "mutation_target_is_fix_branch": False,
                "mutation_red_returned": False,
                "surface": None,
                "surface_assertion": None,
            },
            "same_file_list": True,
        },
        "verdict": {
            "status": "RED-AT-PINNED-REF" if red_runs else "ALL-GREEN",
            "green_only": not red_runs,
            "attributable": attributable_,
            "environment_error": False,
            "closes_issue": False,
            "violations": [],
            "reasons": [],
        },
        "reproduce": (
            f"python3 tools/embedded_evidence.py {args.cmd} --selection {args.selection} "
            f"--n {args.n} --ref {commit} --marker \"{args.marker}\""
        ),
    }
    ok, reasons = closes_issue(rec)
    rec["verdict"]["closes_issue"] = ok
    rec["verdict"]["violations"] = reasons
    rec["verdict"]["status"] = (
        "PAIRED-RED-DEMONSTRATED" if ok else
        ("RED-AT-PINNED-REF" if red_runs else "ALL-GREEN")
    )
    rec["exit_code"] = exit_code(rec)
    return rec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="embedded_evidence")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("run", "red"):
        p = sub.add_parser(name)
        p.add_argument("--selection", default="family",
                       choices=["family", "carve-out", "whole-suite"])
        p.add_argument("--n", type=int, default=DEFAULT_N)
        p.add_argument("--ref", default=None)
        p.add_argument("--pairing-ref", default=None)
        p.add_argument("--marker", default=DEFAULT_MARKER)
        p.add_argument("--load-ceiling", type=float, default=DEFAULT_LOAD_CEILING)
        p.add_argument("--run-timeout", type=int, default=DEFAULT_RUN_TIMEOUT_S)
        p.add_argument("--record-role", default="historical-attestation",
                       choices=["historical-attestation", "closing"])
        p.add_argument("--record-out", default=None)
    c = sub.add_parser("classify")
    c.add_argument("--redis-log", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "classify":
        lines = Path(args.redis_log).read_text(errors="replace").splitlines()
        cause, ev = label_cause(lines)
        print(json.dumps({"cause": cause, "evidence": ev}, indent=2))
        return 0

    args.record_out = Path(args.record_out).expanduser() if args.record_out else None
    args.environment_error = None
    if args.n < 2:
        print("error: --n must be >= 2 (a single run is not N consecutive)", file=sys.stderr)
        return 2
    if args.n > DEFAULT_MAX_RUNS:
        print(f"error: --n {args.n} exceeds --max-runs {DEFAULT_MAX_RUNS}", file=sys.stderr)
        return 2

    try:
        rec = _build_record(args)
    except RuntimeError as exc:
        print(f"environment error: {exc}", file=sys.stderr)
        return 2

    out = args.record_out or Path(tempfile.gettempdir()) / "pi-embedded-evidence" / "record.json"
    _write_record(rec, out)
    print(json.dumps({
        "record": str(out),
        "status": rec["verdict"]["status"],
        "closes_issue": rec["verdict"]["closes_issue"],
        "red_cause": rec["red"]["cause"],
        "red_band": rec["load"]["red_band"],
        "reasons": rec["verdict"]["violations"],
        "exit_code": rec["exit_code"],
    }, indent=2))
    return rec["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
