#!/usr/bin/env python3
"""Fail-closed live-FalkorDB skip guard (issue #1436) + coverage manifest (epic #1647 Task 3).

The fast-suite `test` matrix job provisions a falkordb service so the
live-FalkorDB-required tests actually RUN. If any of them SKIP anyway (a probe
regression, a service outage, a new live test file landing in the matrix without
a matching probe), the run must flip RED — the historical silent-green masked the
#1382 EP regression class for days.

Usage:
  python3 tools/skip-guard.py <path-to-pytest.log> [--manifest <expected-nodeids.txt>] [--junitxml <path>]
  python3 tools/skip-guard.py --emit-manifest "<space-joined $FILES>" [--marker <expr>] [--output <path>] [--ignore <path>]...

Manifest GENERATION mode (epic #1647 Task 6 — the coverage-manifest
producer):
  --emit-manifest "tests/a.py tests/b.py"  run `pytest <files> --collect-only
      -q -m <marker> -p no:cacheprovider` (the SAME file list the CI run step
      passes to pytest, verbatim — plan-review P1-7: never a re-derived
      matrix list) and write one expected nodeid per line (with a '#' header
      comment) to --output (default /tmp/expected-nodeids.txt). The -m filter
      defaults to 'not track_b' — the run step's own marker filter, so a
      track_b nodeid the run deliberately deselects can never be expected.
      Empty file list -> exit 0 with NO output (the CI guard step skips the
      whole manifest mode on empty files); a collect-only failure propagates
      its rc and writes no manifest (fail-closed: a vanished manifest must
      never vacuous-green).
  Repeatable --ignore <path> flags are passed through to the collect-only
  command as --ignore=<path> (epic #1647 Task 10 Step 1a): the
  post-merge-validation run's manifest must replicate its OWN excludes
  (`--ignore=tests/e2e` + the $SLOW_IGNORES file list, cycle-2 P2-14) or it
  expects e2e/slow nodeids the pmv run never produces and every merge reds
  on vanished nodeids.

Semantics:
  - Log missing/unreadable  -> exit 0 (no evidence, nothing to fail on)
  - No SKIPPED lines whose reason mentions "FalkorDB" -> exit 0 (clean)
  - Any such line -> print the skipped set (nodeids) + count, exit 1

Matches BOTH pytest output formats:
  -v progress:  "tests/test_ep_directional.py::TestX::test_y SKIPPED (Live FalkorDB (Docker) not available)"
  -rs summary:  "SKIPPED [14] tests/test_ep_directional.py:35: Live FalkorDB (Docker) not available"

NOTE on -v truncation: pytest truncates -v skip reasons to the terminal width
(80 cols when redirected to a file with COLUMNS unset), so long nodeids drop the
reason entirely. The CI fast job therefore reports skips via -r fEs (pinned by
tests/test_skip_guard.py::test_workflow_keeps_rs) — pytest 9.1.1 replaces the
report set on repeated -r flags, so a trailing -rfE would suppress the skip
summary; -r fEs is the order-independent superset. The -r summary lines are
never truncated and carry the reason.

Fail-closed by design: ANY SKIPPED line whose reason mentions "FalkorDB" trips
the guard (every current skip reason in tests/ is availability-class; a future
feature-gate skip in a matrix file would also correctly go red).
The "resolved URI ... not a test graph" safety skip does NOT contain "FalkorDB"
and is a different class (graph-name safety, not availability) — out of scope.

Coverage-manifest mode (epic #1647 Task 3 — the skip-guard inversion):
  --manifest <expected-nodeids.txt>  one expected nodeid per line, '#' comments
                                     allowed (the --collect-only manifest).
  --junitxml <path>                  junitxml from the same pytest run, written
                                     with -o junit_family=xunit1 so every
                                     <testcase> carries file/line attributes.

  Every expected nodeid must appear as a junitxml <testcase> — passed OR
  skipped-with-reason; a missing nodeid (deselected, file dropped from $FILES,
  vacuous early-return) -> print + exit 1. This kills the vacuous-pass class
  (#942): on migrated halves a vanished nodeid can no longer green.

  The junitxml is the AUTHORITATIVE observed set (lossless per-testcase nodeid
  via file+classname+name reconstruction; lossless skip reason in <skipped
  message>). The -r fEs summary stays for the human-readable log only — its
  file:line summaries never match --collect-only full nodeids.

  The FalkorDB-reason matcher reads <skipped message> from the junitxml
  (lossless, never truncated). Intentional availability-class reason families
  are EXEMPT in BOTH the junitxml matcher and the legacy line matcher:
    - "requires TORTOISE_DB_URI"           (_live_utils._skip_unless_live_uri —
                                           the visible URI-gate for the
                                           test-concurrency-falkor job)
    - "Live FalkorDB server on localhost:6399"  (test_falkordb_compat's
                                           TestLiveServerCompat permanent class
                                           skip — legacy falkordblite 0.10.0
                                           endpoint, no 6399 service in CI)
    - "embedded FalkorDBLite unavailable" / "redislite falkordb unavailable"
                                           (carve-out/embedded-lane precondition
                                           family — runs embedded BY DESIGN
                                           under a URI job)
  The availability-REGRESSION family ("Live FalkorDB (Docker) not available" —
  the provisioned-service probes) stays RED; that is the guard's whole job.

  Fail-closed corners:
    - --manifest passed but the junitxml is missing/unreadable -> every expected
      nodeid absent -> exit 1 (the flip: missing evidence used to green).
    - --manifest passed but the MANIFEST FILE itself is missing/unreadable ->
      exit 1 (a vanished manifest must never vacuous-green).
  Without --manifest the historical behavior is preserved (missing
  junitxml/log -> exit 0).
"""
from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The run step's marker filter — the manifest's collect-only MUST use the
# same expression or it expects nodeids the run deliberately deselects and
# every run reds on vanished nodeids (Task 6 Step 2, cycle-2 P2-11).
_MANIFEST_MARKER_DEFAULT = "not track_b"

# A collected nodeid is always <path>::[<Class>::]<name> — starts with a
# non-space/non-colon path token followed by "::". pytest's collect-only
# summary lines ("39 tests collected in 0.02s", "no tests collected (4
# deselected) in 0.03s", "9/13 tests collected (4 deselected)") never
# contain "::".
_COLLECT_NODEID_RE = re.compile(r"^[^\s:]+::")


# A skip line: "SKIPPED" + a reason mentioning FalkorDB (both formats above).
_SKIPPED_MARK = "SKIPPED"
_FALKORDB_RE = re.compile(r"FalkorDB", re.IGNORECASE)

# Intentional availability-class reason families, exempt from the FalkorDB
# trip. Prefix match on the raw reason (case-sensitive for these two).
_EXEMPT_REASON_PREFIXES = (
    "requires TORTOISE_DB_URI",
    "Live FalkorDB server on localhost:6399",
)
# Embedded-lane / carve-out precondition family — case-insensitive prefix.
_EMBEDDED_UNAVAILABLE_PREFIXES = (
    "embedded falkordblite unavailable",
    "redislite falkordb unavailable",
)

_RS_REASON_RE = re.compile(
    r"^\s*SKIPPED\s+\[\d+\]\s+[^\s:]+\.py:\d+:\s*(.+)$"
)
# Real pytest -v skip lines always end with a progress marker: "SKIPPED
# (reason) [ 25%]" — the marker is optional here (a bare "SKIPPED (reason)"
# line is tolerated too), and a truncated reason ending "..." still matches
# (the "FalkorDB" substring survives in the visible part).
_V_REASON_RE = re.compile(r"SKIPPED\s+\((.+)\)\s*(?:\[\s*\d+%\])?\s*$")


def extract_nodeid(line: str) -> str:
    """Pull the test nodeid / file:line from a skip line, best-effort."""
    # -v progress: nodeid appears before " SKIPPED".
    m = re.match(r"^\s*([^\s]+) SKIPPED", line)
    if m:
        return m.group(1)
    # -rs summary: "SKIPPED [N] file.py:line: reason"
    m = re.match(r"^\s*SKIPPED\s+\[\d+\]\s+([^\s:]+\.py:\d+)", line)
    if m:
        return m.group(1)
    return line.strip()[:120]


def _extract_reason(line: str) -> str | None:
    """Pull the skip reason out of a line, or None if not parseable."""
    m = _RS_REASON_RE.match(line)
    if m:
        return m.group(1).strip()
    m = _V_REASON_RE.search(line)
    if m:
        return m.group(1).strip()
    return None


def is_falkor_reason_violation(reason: str) -> bool:
    """True when a FalkorDB-mentioning skip reason is a REAL violation.

    The intentional availability-class families (requires-URI, 6399, embedded/
    redislite-unavailable) are exempt — their skips are the visible design, not
    evidence the provisioned docker service is down. The availability-REGRESSION
    family stays red.
    """
    if not _FALKORDB_RE.search(reason):
        return False
    for prefix in _EXEMPT_REASON_PREFIXES:
        if reason.startswith(prefix):
            return False
    lowered = reason.lower()
    for prefix in _EMBEDDED_UNAVAILABLE_PREFIXES:
        if lowered.startswith(prefix):
            return False
    return True


def find_violations(log_text: str) -> list[str]:
    """Legacy line-based matcher (half a / P1 CI — no junitxml available).

    Excludes _live_utils.py-sourced skips: _skip_unless_live_uri's reason
    ("requires TORTOISE_DB_URI (live FalkorDB sidecar…)") legitimately contains
    "FalkorDB" but is the INTENTIONAL URI-gate for the test-concurrency-falkor
    job — those tests skip VISIBLY in every other surface by design (#942
    vacuity-kill), and the fast matrix must not go red for them. The guard
    targets AVAILABILITY regressions (a probe should have found the provisioned
    falkordb service and didn't), which is a different reason family.

    The same reason-family exemptions as the junitxml matcher apply (epic #1647
    Task 3, cycle-7 P2-4): a tier-2 PR can route test_falkordb_compat to half a,
    where its 6399 class skip must not red the legacy matcher either.
    """
    violations = []
    for line in log_text.splitlines():
        if _SKIPPED_MARK not in line or not _FALKORDB_RE.search(line):
            continue
        if "_live_utils.py" in line:
            # Legacy location-based exclusion (kept for back-compat; the
            # canonical exclusion is the reason-family prefix below).
            continue
        reason = _extract_reason(line)
        if reason is None:
            # -v progress line truncated at 80 cols dropped the reason
            # entirely (module docstring): the "FalkorDB" match can then only
            # come from the nodeid/filename — e.g. tests/test_falkordb_compat.py
            # — which is NOT a reason-level violation (cycle-7 P2-4: the 6399
            # class skip must not red the legacy matcher). The -r fEs summary
            # (never truncated) is the authoritative reason source; a real
            # regression skip always appears there with its reason intact.
            continue
        if not is_falkor_reason_violation(reason):
            continue
        violations.append(extract_nodeid(line))
    return violations


def _module_prefix(file: str) -> str:
    """file="tests/test_foo.py" -> "tests.test_foo" (the module-dotted prefix).

    Mirrors pytest's own derivation (final .py suffix only, path parts joined
    with ".") so the reconstruction agrees with pytest's junitxml classname
    module part even for paths whose non-final segments contain ".py" or "."
    (e.g. tests/bench/test_smoke_embedded.py).
    """
    return ".".join(Path(file).with_suffix("").parts)


def reconstruct_nodeid(file: str, classname: str, name: str) -> str:
    """Rebuild the pytest nodeid from junitxml attributes (junit_family=xunit1).

    file + "::" + (classname minus its module-dotted prefix) + "::" + name;
    module-level tests have classname == prefix -> file::name. Parametrized ids
    ride in `name` (e.g. test_param[1] — class-level parametrization too).
    Nested classes: junitxml joins them with "." in classname
    (TestOuter.TestInner) while pytest nodeids use "::" — the class part is
    split on "." so both forms agree (Python class names cannot contain dots;
    junitxml classname dots come only from nesting).
    """
    prefix = _module_prefix(file)
    if classname and classname != prefix:
        cls = classname
        if cls.startswith(prefix + "."):
            cls = cls[len(prefix) + 1:]
        cls = cls.replace(".", "::")
        return f"{file}::{cls}::{name}"
    return f"{file}::{name}"


def _read_manifest(path: str) -> tuple[set[str], list[str]] | None:
    """Expected nodeids + invalid lines from the manifest (one per line).

    '#'-prefixed and blank lines are skipped. Returns None when the file is
    unreadable; otherwise (expected, invalid_lines). A non-comment line with no
    "::" is not a pytest nodeid (every nodeid is file::name at minimum) — e.g.
    a stray `--collect-only -q` summary line ("N tests collected in Xs")
    piped into the manifest — and must surface LOUDLY (fail-closed), never be
    dropped silently: a dropped line could mask a vanished nodeid, which is
    exactly the #942 vacuity class this guard exists to kill.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:  # noqa: SIM115
            lines = f.read().splitlines()
    except OSError:
        return None
    expected: set[str] = set()
    invalid: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "::" not in stripped:
            invalid.append(stripped)
        else:
            expected.add(stripped)
    return expected, invalid


def _read_junitxml(path: str) -> tuple[set[str], list[str], str | None]:
    """Parse a junitxml (xunit1) into observed nodeids + falkor violations.

    Returns (observed, falkor_violations, contract_error). Raises
    OSError/ET.ParseError when the file is missing or malformed.

    contract_error is set (non-None) when any <testcase> lacks the file/name
    attributes nodeid reconstruction requires — i.e. the junitxml was NOT
    written with -o junit_family=xunit1. Reason extraction (<skipped
    message>) is independent of those attrs, so falkor_violations are still
    complete and meaningful; only the nodeid set is unusable.
    """
    observed: set[str] = set()
    falkor_violations: list[str] = []
    contract_error: str | None = None
    tree = ET.parse(path)
    for tc in tree.iter("testcase"):
        file = tc.get("file") or ""
        classname = tc.get("classname") or ""
        name = tc.get("name") or ""
        skipped = tc.find("skipped")
        if skipped is not None:
            reason = (skipped.get("message") or "").strip()
            if is_falkor_reason_violation(reason):
                # Reason-level violation: report best-effort nodeid — under
                # xunit2 there is no file attr, so fall back to class::name.
                if file and name:
                    nodeid = reconstruct_nodeid(file, classname, name)
                else:
                    nodeid = f"{classname}::{name}".strip(":") or "<unknown>"
                falkor_violations.append(nodeid)
        if not file or not name:
            # junit_family=xunit2 (pytest's default) emits no file/line attrs —
            # nodeid reconstruction is impossible, and a silently-mangled nodeid
            # would false-red with a misleading report. Fail loud instead.
            contract_error = (
                "junitxml contains testcases without file/name attributes — "
                "was it written with -o junit_family=xunit1? (the file/line "
                "attrs nodeid reconstruction needs only exist under xunit1)"
            )
            continue
        observed.add(reconstruct_nodeid(file, classname, name))
    return observed, falkor_violations, contract_error


def _parse_args(argv: list[str]) -> tuple[str | None, str | None, str | None]:
    """Parse argv into (log_path, manifest_path, junit_path).

    Accepts both `--flag <value>` and `--flag=<value>` forms.
    """
    log_path: str | None = None
    manifest_path: str | None = None
    junit_path: str | None = None
    i = 1
    while i < len(argv):
        arg = argv[i]
        value: str | None = None
        if arg.startswith("--manifest="):
            flag, value = "--manifest", arg.split("=", 1)[1]
        elif arg.startswith("--junitxml="):
            flag, value = "--junitxml", arg.split("=", 1)[1]
        elif arg in ("--manifest", "--junitxml"):
            flag = arg
        else:
            flag = None
        if flag is not None:
            if value is None:
                if i + 1 >= len(argv):
                    return None, None, None
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            if flag == "--manifest":
                manifest_path = value
            else:
                junit_path = value
        else:
            if log_path is not None:
                return None, None, None
            log_path = arg
            i += 1
    return log_path, manifest_path, junit_path


def _report(violations: list[str], falkor_violations: list[str]) -> int:
    if not violations and not falkor_violations:
        return 0

    if violations:
        print("❌ skip-guard failed (coverage manifest, epic #1647):")
        cap = 50
        shown = violations if len(violations) <= cap else violations[:cap]
        for nodeid in sorted(set(shown)):
            print(f"   - {nodeid}")
        if len(violations) > cap:
            print(f"   … and {len(violations) - cap} more")
        print(f"{len(violations)} expected nodeid(s) absent from the junitxml "
              "testcases (PASSED + SKIPPED-reasoned) — CI would be green while "
              "these tests never ran.")
    if falkor_violations:
        print("❌ live-FalkorDB tests SKIPPED in this run — CI would be green "
              "while testing nothing (issue #1436):")
        for nodeid in sorted(set(falkor_violations)):
            print(f"   - {nodeid}")
        print(f"{len(falkor_violations)} skip line(s) matching the live-FalkorDB "
              "reason family.")
    return 1


def collect_only_nodeids(text: str) -> list[str]:
    """Filter a `pytest --collect-only -q` output into expected nodeids.

    pytest prints one nodeid per line for the tests that WILL run — the -m
    filter is applied AT collection, so deselected items never appear as
    nodeids (verified 2026-08-24: "9/13 tests collected (4 deselected) in
    0.03s" lists only the 9 selected items) — then a summary line that must
    be dropped. Warnings/errors emitted during collection never match the
    leading-path-:: shape either, so they are dropped too (a stray line
    piped into the manifest would otherwise trip the consumer's
    invalid-line fail-closed check, redding every run).
    """
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if "::" not in stripped:
            continue
        if _COLLECT_NODEID_RE.match(stripped):
            out.append(stripped)
    return out


def emit_manifest(files: list[str], marker: str, output: Path,
                  runner=None, ignores: tuple[str, ...] = ()) -> int:
    """Generate the expected-nodeid manifest for the given file list.

    Spawns `pytest <files> --collect-only -q -m <marker> -p no:cacheprovider`
    (the same construction the CI run step builds) and writes one expected
    nodeid per line to ``output``, with a '#' header comment (the consumer's
    _read_manifest skips comment/blank lines). ``runner`` is injectable for
    tests: ``runner(cmd) -> (rc, stdout)``; the default spawns pytest.
    ``ignores`` (repeatable --ignore paths, epic #1647 Task 10 Step 1a) are
    passed through as --ignore=<path> so a manifest can replicate its run's
    own excludes (the post-merge-validation full-`tests/` run ignores
    tests/e2e + the slow_files list).

    Fail-closed corners:
    - empty files -> exit 0, NO output file (the CI guard skips manifest
      mode on empty $FILES — a "no selected files" run writes no junitxml,
      so a manifest would false-red every expected nodeid).
    - collect-only failure -> propagate rc, NO output file (a vanished
      manifest must never vacuous-green; the consumer reds on it).
    """
    if not files:
        print("emit-manifest: no files — no manifest written (guard skips)")
        return 0
    cmd = [sys.executable, "-m", "pytest", *files, "--collect-only", "-q",
           "-m", marker, "-p", "no:cacheprovider",
           *[f"--ignore={ig}" for ig in ignores]]
    if runner is None:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        rc, collected = proc.returncode, proc.stdout
    else:
        rc, collected = runner(cmd)
    if rc != 0:
        print(f"emit-manifest: collect-only failed (rc={rc}) — no manifest "
              f"written (fail-closed)", file=sys.stderr)
        return rc
    nodeids = collect_only_nodeids(collected)
    lines = [
        f"# expected nodeids — epic #1647 Task 6 coverage manifest "
        f"(from the run step's verbatim $FILES x `-m {marker}` collect-only)",
        *nodeids,
        "",
    ]
    output.write_text("\n".join(lines))
    print(f"emit-manifest: {len(nodeids)} expected nodeids -> {output}")
    return 0


def _main_emit_manifest(argv: list[str]) -> int:
    """--emit-manifest mode: parse args, generate, write."""
    files: list[str] = []
    marker = _MANIFEST_MARKER_DEFAULT
    output = Path("/tmp/expected-nodeids.txt")
    ignores: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--emit-manifest":
            i += 1
            continue
        if arg.startswith("--marker="):
            marker, i = arg.split("=", 1)[1], i + 1
        elif arg == "--marker":
            if i + 1 >= len(argv):
                print("--marker requires a value", file=sys.stderr)
                return 2
            marker, i = argv[i + 1], i + 2
        elif arg.startswith("--output="):
            output, i = Path(arg.split("=", 1)[1]), i + 1
        elif arg == "--output":
            if i + 1 >= len(argv):
                print("--output requires a value", file=sys.stderr)
                return 2
            output, i = Path(argv[i + 1]), i + 2
        elif arg.startswith("--ignore="):
            ignores.append(arg.split("=", 1)[1])
            i += 1
        elif arg == "--ignore":
            if i + 1 >= len(argv):
                print("--ignore requires a value", file=sys.stderr)
                return 2
            ignores.append(argv[i + 1])
            i += 2
        else:
            # The space-joined $FILES string the run step passes to pytest
            # (may arrive as one token or many). Test paths never contain
            # spaces, so whitespace-splitting is lossless.
            files += arg.split()
            i += 1
    return emit_manifest(files, marker, output, ignores=tuple(ignores))


def main(argv: list[str]) -> int:
    if "--emit-manifest" in argv:
        return _main_emit_manifest(argv)
    log_path, manifest_path, junit_path = _parse_args(argv)
    if log_path is None:
        print(
            f"usage: {argv[0]} <path-to-pytest.log> "
            "[--manifest <expected-nodeids.txt>] [--junitxml <path>]\n"
            f"       {argv[0]} --emit-manifest \"<space-joined $FILES>\" "
            "[--marker <expr>] [--output <path>]\n"
            "exit 0 = no live-FalkorDB skips (or no log / no manifest); "
            "exit 1 = coverage gap or live tests skipped (fail-closed, #1436)",
            file=sys.stderr,
        )
        return 2

    if manifest_path is not None:
        # ── Coverage-manifest mode (the inversion) ──────────────────────
        manifest = _read_manifest(manifest_path)
        if manifest is None:
            print(f"❌ manifest file {manifest_path!r} missing or unreadable — "
                  "the expected-nodeid set is unknowable, so every expected "
                  "test is treated as absent (fail-closed; a vanished manifest "
                  "must never vacuous-green).", file=sys.stderr)
            return 1
        expected, invalid_lines = manifest
        if invalid_lines:
            print(f"❌ manifest contains {len(invalid_lines)} line(s) that are "
                  "not pytest nodeids (no '::' separator) — a manifest "
                  "generation bug (e.g. a --collect-only summary line piped "
                  "in); failing closed instead of silently dropping lines that "
                  "could mask a vanished nodeid:", file=sys.stderr)
            for line in invalid_lines[:10]:
                print(f"   - {line!r}", file=sys.stderr)
            return 1
        if not expected:
            print("❌ manifest contains no expected nodeids (empty or "
                  "comment-only) — an empty expected-set must not "
                  "vacuous-green (it would report zero missing nodeids by "
                  "construction); the manifest generator emitted nothing.",
                  file=sys.stderr)
            return 1
        observed: set[str] = set()
        falkor_violations: list[str] = []
        contract_error: str | None = None
        if junit_path is not None:
            try:
                observed, falkor_violations, contract_error = _read_junitxml(
                    junit_path
                )
            except (OSError, ET.ParseError) as exc:
                print(f"❌ junitxml {junit_path!r} missing or unreadable "
                      f"({exc}) — no observed testcases, so every one of the "
                      f"{len(expected)} expected nodeids is absent (fail-closed: "
                      "missing evidence is itself the failure).",
                      file=sys.stderr)
                observed = set()
            if contract_error:
                print(f"❌ {contract_error}", file=sys.stderr)
                observed = set()  # reconstruction impossible → all absent
        missing = sorted(expected - observed)
        return _report(missing, falkor_violations)

    if junit_path is not None:
        # ── junitxml mode without a manifest: reason matcher only ────────
        try:
            _, falkor_violations, contract_error = _read_junitxml(junit_path)
        except (OSError, ET.ParseError):
            falkor_violations = []
            contract_error = None
        if contract_error:
            # Reason extraction (<skipped message>) works without file/name
            # attrs, so a real FalkorDB skip under a non-xunit1 junitxml must
            # still red — never swallow it (fail-open). The diagnostic names
            # the root cause alongside any violations.
            print(f"❌ {contract_error}", file=sys.stderr)
        return _report([], falkor_violations)

    # ── Legacy line-matcher mode (back-compat) ───────────────────────────
    try:
        log_text = open(log_path, encoding="utf-8", errors="replace").read()  # noqa: SIM115
    except OSError:
        # No log (pytest never wrote one / step cancelled) — nothing to fail on.
        return 0

    violations = find_violations(log_text)
    if not violations:
        return 0

    print("❌ live-FalkorDB tests SKIPPED in this run — CI would be green "
          "while testing nothing (issue #1436):")
    for nodeid in sorted(set(violations)):
        print(f"   - {nodeid}")
    print(f"{len(violations)} skip line(s) matching the live-FalkorDB reason family.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
