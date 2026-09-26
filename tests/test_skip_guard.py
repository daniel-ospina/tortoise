"""Tests for tools/skip-guard.py — the fail-closed live-FalkorDB skip guard (#1436).

The fast-suite `test` matrix job provisions a falkordb service so the
live-FalkorDB-required tests actually RUN (0 skipped). If a probe ever regresses
(skip reason mentioning FalkorDB appears in the pytest log), the guard must flip
the job RED instead of the historical silent-green.

These tests are pure string parsing — no embedded DB, no Docker.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "skip-guard.py"

# pytest -v progress format (REAL output always ends with the progress marker
# "[ N%]" — pytest 9.1.1, verified)
V_FORMAT_SKIP = (
    "tests/test_ep_directional.py::TestE019DirectionalCascade::test_c1_always_drops "
    "SKIPPED (Live FalkorDB (Docker) not available) [ 25%]\n"
)
# pytest -rs summary format
RS_FORMAT_SKIP = (
    "SKIPPED [14] tests/test_ep_directional.py:35: Live FalkorDB (Docker) not available\n"
)
# Variants of the live-FalkorDB reason family (real -v format with [N%])
OTHER_LIVE_REASONS = [
    "tests/test_hnsw_vector_index.py::test_hnsw_vector_smoke SKIPPED (FalkorDB not available) [ 30%]\n",
    "tests/test_epic903_freshness.py::Test::test_composite SKIPPED (no live non-embedded FalkorDB available) [ 40%]\n",
    "tests/test_ingest.py::test_ingest SKIPPED (live FalkorDB (FALKORDB_HOST:PORT) not reachable) [ 50%]\n",
]
UNRELATED_SKIP = (
    "tests/test_cli_serve.py::test_something SKIPPED (requires network access)\n"
    "tests/test_models.py::test_ml SKIPPED (sklearn not installed)\n"
)
UNRELATED_RS_SKIP = (
    "SKIPPED [2] tests/test_config.py:15: requires network access\n"
)
# pytest -v truncates skip reasons to terminal width (80 cols when redirected
# to a file with COLUMNS unset) — an 81-char test_ep_directional nodeid drops
# the reason entirely. The guard CANNOT see these (no "FalkorDB" in the line);
# the workflow guarantees -rs instead (test_workflow_keeps_rs below).
TRUNCATED_V_SKIP = (
    "tests/test_ep_directional.py::TestE019DirectionalCascade::test_c1_always_drops "
    "SKIPPED [ 25%]\n"
)
PASS_LINES = [
    "tests/test_ep_directional.py::TestE019DirectionalCascade::test_c1_always_drops PASSED\n",
    "1453 passed, 72 skipped, 0 failed in 3.2s\n",
]


def run_guard(log_text: str) -> subprocess.CompletedProcess:
    """Run skip-guard.py against a temp log; returns the completed process."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(log_text)
        log_path = f.name
    try:
        return subprocess.run(
            [sys.executable, str(TOOL), log_path],
            capture_output=True, text=True,
        )
    finally:
        Path(log_path).unlink(missing_ok=True)


def run_guard_with_manifest(
    log_path: str,
    manifest: str | None = None,
    junit: str | None = None,
) -> int:
    """Run skip-guard.py with the coverage-manifest args; returns the rc.

    Only the provided flags are passed; `manifest=None`/`junit=None` omit
    the corresponding --manifest/--junitxml arg.
    """
    argv = [sys.executable, str(TOOL), log_path]
    if manifest is not None:
        argv += ["--manifest", manifest]
    if junit is not None:
        argv += ["--junitxml", junit]
    return subprocess.run(argv, capture_output=True, text=True).returncode


# REAL junitxml format (pytest 9.1.1, -o junit_family=xunit1 — verified).
JUNIT_PASSED = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="2">
<testcase classname="tests.test_ep_directional.TestX" name="test_y" file="tests/test_ep_directional.py" line="35" time="0.001" />
<testcase classname="tests.test_projection" name="test_something" file="tests/test_projection.py" line="88" time="0.001" />
</testsuite></testsuites>'''
JUNIT_SKIPPED = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="1">
<testcase classname="tests.test_embedded_lifecycle_fast_close" name="test_ephemeral_nosave" file="tests/test_embedded_lifecycle_fast_close.py" line="30" time="0.001"><skipped type="pytest.skip" message="redislite unavailable">/tests/test_embedded_lifecycle_fast_close.py:30: redislite unavailable</skipped></testcase>
</testsuite></testsuites>'''
# Cycle-2 P2-2/4: junitxml entity-escapes ids (&quot; / &lt;). The reader must
# use xml.etree.ElementTree — a regex reader mangles these nodeids and the
# manifest reconciliation silently misses them.
JUNIT_ESCAPED = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="1">
<testcase classname="tests.test_api" name="test_arg_&quot;weird&quot;_&lt;x&gt;" file="tests/test_api.py" line="41" time="0.001" />
</testsuite></testsuites>'''
# Nested test classes: junitxml joins them with "." in classname while pytest
# nodeids use "::" — the reader must split the class part on "." to agree.
JUNIT_NESTED = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="1">
<testcase classname="tests.test_nested.TestOuter.TestInner" name="test_x" file="tests/test_nested.py" line="2" time="0.001" />
</testsuite></testsuites>'''
# Subdirectory file (tests/bench/* — the fast-matrix push_extra lane): the
# module-dotted prefix must be tests.bench.test_smoke_embedded, NOT
# tests.bench.test_smoke_embedded.py-stripped-everywhere.
JUNIT_SUBDIR = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="1">
<testcase classname="tests.bench.test_smoke_embedded" name="test_smoke" file="tests/bench/test_smoke_embedded.py" line="36" time="0.001" />
</testsuite></testsuites>'''


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


class TestGuardAcceptsCleanLog:
    def test_no_skips_at_all(self):
        proc = run_guard("".join(PASS_LINES))
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == ""

    def test_skips_without_falkordb_reason_are_ignored(self):
        proc = run_guard(UNRELATED_SKIP)
        assert proc.returncode == 0, proc.stderr

    def test_rs_summary_without_falkordb_reason_is_ignored(self):
        proc = run_guard(UNRELATED_RS_SKIP)
        assert proc.returncode == 0, proc.stderr

    def test_truncated_v_line_is_not_false_positive(self):
        # Pytest drops the reason for long nodeids at 80 cols — the line carries
        # no "FalkorDB", so the tool cannot flag it. This documents the boundary:
        # the CI workflow MUST pass -rs (test_workflow_keeps_rs) so the reason
        # survives in the summary lines.
        proc = run_guard(TRUNCATED_V_SKIP)
        assert proc.returncode == 0, proc.stderr

    def test_missing_log_is_not_a_failure(self, tmp_path):
        # FLIPPED (epic #1647 Task 3, cycle-3 P2-14): the vacuous early-return
        # is dead in manifest mode. With --manifest passed, a missing log and
        # no junitxml evidence = every expected nodeid absent -> RED (exit 1).
        # The old exit-0 semantics survive only WITHOUT a manifest
        # (test_missing_junitxml_without_manifest_stays_green).
        manifest = _write(
            tmp_path, "manifest.txt",
            "tests/test_ep_directional.py::TestX::test_y\n",
        )
        proc = run_guard_with_manifest("/nonexistent/pytest.log", manifest=manifest)
        assert proc == 1

    def test_summary_line_mentioning_skipped_is_not_a_violation(self):
        proc = run_guard(PASS_LINES[0] + PASS_LINES[1])
        assert proc.returncode == 0, proc.stderr

    def test_mixed_unrelated_and_live_skips_reported(self):
        proc = run_guard(UNRELATED_SKIP + RS_FORMAT_SKIP)
        assert proc.returncode == 1
        assert "test_ep_directional.py" in proc.stdout

    def test_workflow_keeps_rs(self):
        """Pin the skip-summary contract: the fast-suite pytest invocation must
        report skips in the -r summary. pytest truncates -v skip reasons at
        80 cols (drops test_ep_directional's reason, guard would fail open), and
        pytest 9.1.1 REPLACES the report set on repeated -r flags — so a
        trailing -rfE would suppress the skip summary the guard depends on.
        -r fEs is the order-independent superset (f=FAILED, E=ERROR, s=SKIPPED)."""
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" \
            / "python-ci.yml"
        text = workflow.read_text()
        fast_run = [
            l for l in text.splitlines()  # noqa: E741
            # the watchdog duration is intentionally not pinned (it has moved
            # 30m->45m->55m as the corpus grew; only the -r summary contract
            # matters here)
            if re.search(r"timeout -s INT -k 10 \d+m", l) and "-m pytest" in l
        ]
        assert fast_run, "fast-suite pytest invocation not found"
        assert "-r fEs" in fast_run[0], (
            "fast-suite pytest must report skips in the summary (-r fEs): -v "
            "truncates skip reasons at 80 cols and a trailing -rfE replaces the "
            "-rs report set in pytest 9.1.1 (guard would fail open)"
        )
        assert "--junitxml" in fast_run[0] and "-o junit_family=xunit1" in fast_run[0], (
            "fast-suite pytest must emit lossless junitxml (--junitxml + -o "
            "junit_family=xunit1): the coverage manifest (epic #1647 Task 3) "
            "reconciles --collect-only nodeids against junitxml testcases, and "
            "the file/line attributes only exist under junit_family=xunit1"
        )


class TestGuardFailsOnLiveFalkorDBSkip:
    def test_v_format(self):
        proc = run_guard(V_FORMAT_SKIP)
        assert proc.returncode == 1
        assert "test_ep_directional.py" in proc.stdout

    def test_rs_format(self):
        proc = run_guard(RS_FORMAT_SKIP)
        assert proc.returncode == 1
        assert "test_ep_directional.py" in proc.stdout

    def test_all_reason_variants(self):
        for line in OTHER_LIVE_REASONS:
            proc = run_guard(line)
            assert proc.returncode == 1, f"reason variant not caught: {line!r}"

    def test_skips_surfaced_with_count_and_set(self):
        proc = run_guard(V_FORMAT_SKIP + RS_FORMAT_SKIP)
        assert proc.returncode == 1
        # Both nodeids surfaced so the fix is actionable.
        assert proc.stdout.count("test_ep_directional.py") >= 2


# ── Coverage-manifest mode (epic #1647 Task 3 — the skip-guard inversion) ──
# The junitxml is the AUTHORITATIVE observed set: every expected nodeid must
# appear as a junitxml <testcase> (passed OR skipped-with-reason) or the guard
# goes red. Fixtures are REAL pytest junitxml output (pytest 9.1.1,
# -o junit_family=xunit1), not the old file:line fake.


def test_manifest_missing_nodeid_is_red(tmp_path):
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n"
                      "tests/test_projection.py::test_something\n"
                      "tests/test_vanished.py::test_never_ran\n")
    # test_vanished absent from the junitxml testcases (deselected / file
    # dropped from $FILES / early-return with no skip) → red
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 1  # fail-closed — vacuous early-return detected


def test_manifest_escaped_ids_parse(tmp_path):
    # Cycle-2 P2-2/4: an escaped nodeid (&quot; / &lt;) in the junitxml must
    # round-trip through ElementTree and satisfy its manifest entry.
    junit = _write(tmp_path, "junit.xml", JUNIT_ESCAPED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_api.py::test_arg_\"weird\"_<x>\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0


def test_manifest_nested_class_nodeid_matches_pytest(tmp_path):
    # Nested test classes: junitxml classname is dotted (TestOuter.TestInner),
    # pytest's collect-only nodeid uses "::" — the reconstruction must agree
    # (verified against real pytest 9.1.1 output).
    junit = _write(tmp_path, "junit.xml", JUNIT_NESTED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_nested.py::TestOuter::TestInner::test_x\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0


def test_manifest_subdir_file_reconstruction(tmp_path):
    # tests/bench/* files (fast-matrix push_extra lane) have a "/" in their
    # junitxml file attr — the module-dotted prefix must reconstruct
    # tests.bench.test_smoke_embedded and agree with pytest's nodeid.
    junit = _write(tmp_path, "junit.xml", JUNIT_SUBDIR)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/bench/test_smoke_embedded.py::test_smoke\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0


def test_manifest_equals_form_flags_parse(tmp_path):
    # The CI guard invocation (Task 6) passes --junitxml=<path> --manifest=<path>
    # (equals form — the plan's Step 4 contract). Pin the parser for it.
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n"
                      "tests/test_projection.py::test_something\n")
    proc = subprocess.run(
        [sys.executable, str(TOOL), str(tmp_path / "pytest.log"),
         f"--junitxml={junit}", f"--manifest={manifest}"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_manifest_invalid_line_is_red(tmp_path):
    # A non-nodeid line (no "::") in the manifest is a generator bug (e.g. a
    # stray --collect-only summary line piped in) — fail-closed: red loudly
    # rather than silently dropping a line that could mask a vanished nodeid.
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n"
                      "2 tests collected in 0.00s\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 1


def test_manifest_empty_is_red(tmp_path):
    # An empty/comment-only manifest yields an empty expected-set — which would
    # vacuous-green (zero missing by construction) of exactly the #942 class.
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED)
    manifest = _write(tmp_path, "manifest.txt", "# no tests selected\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 1


def test_manifest_junit_without_file_attrs_is_red(tmp_path):
    # A junitxml written WITHOUT -o junit_family=xunit1 lacks file/line attrs —
    # nodeid reconstruction is impossible, so the guard must fail closed with
    # a clear diagnostic rather than report misleading mangled nodeids.
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED.replace(
        ' file="tests/test_ep_directional.py" line="35"', ""))
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 1


def test_manifest_passed_nodeid_satisfies(tmp_path):
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n"
                      "tests/test_projection.py::test_something\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0


def test_manifest_reasoned_skip_satisfies(tmp_path):
    # A reasoned skip (junitxml <skipped>) is an OBSERVED testcase — it
    # satisfies the manifest (the marker-skips in Task 5 must never go red).
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_embedded_lifecycle_fast_close.py::test_ephemeral_nosave\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0  # reasoned skip ≠ vanished nodeid (and no FalkorDB substring)


def test_missing_junitxml_with_manifest_is_red(tmp_path):
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n")
    rc = run_guard_with_manifest(str(tmp_path / "no-such.log"), manifest,
                                 junit=str(tmp_path / "no-such.xml"))
    assert rc == 1  # FLIPPED from the historical exit 0


def test_missing_junitxml_without_manifest_stays_green(tmp_path):
    proc = run_guard(str(tmp_path / "no-such.log"))
    assert proc.returncode == 0  # back-compat: no manifest, no evidence


def test_missing_manifest_file_is_red(tmp_path):
    # Cycle-3 P2-14: --manifest passed but the manifest FILE is absent/
    # unreadable → red with an actionable message (a vanished manifest must
    # never vacuous-green — the expected-set is then unknowable, which is
    # itself the failure).
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 manifest=str(tmp_path / "no-such-manifest.txt"),
                                 junit=str(tmp_path / "junit.xml"))
    assert rc == 1


def test_falkordb_reason_skip_from_junitxml_is_red(tmp_path):
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable", "Live FalkorDB (Docker) not available"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), junit=junit)
    assert rc == 1  # the historical matcher, now reading junitxml reasons


def test_manifest_mode_with_falkor_reason_skip_is_red(tmp_path):
    # The falkor reason check runs INSIDE manifest mode too: a live test that
    # SKIPPED with an availability-REGRESSION reason IS observed (satisfies
    # the manifest) but must still red the guard — coverage ≠ healthy.
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable", "Live FalkorDB (Docker) not available"))
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_embedded_lifecycle_fast_close.py::test_ephemeral_nosave\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 1  # nodeid observed, but availability-REGRESSION reason → red


def test_manifest_mode_with_exempt_reason_skip_is_green(tmp_path):
    # Companion: an EXEMPT reason family (requires-URI) + observed nodeid
    # under manifest mode → green.
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable",
        "requires TORTOISE_DB_URI (live FalkorDB sidecar)"))
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_embedded_lifecycle_fast_close.py::test_ephemeral_nosave\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0


def test_junitxml_only_mode_non_xunit1_with_falkor_skip_is_red(tmp_path):
    # junitxml-only mode (no manifest): a REAL FalkorDB skip must red even
    # when the junitxml lacks file/name attrs (xunit2) — <skipped message>
    # extraction is independent of nodeid reconstruction; never fail-open.
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable", "Live FalkorDB (Docker) not available").replace(
        ' file="tests/test_embedded_lifecycle_fast_close.py" line="30"', ""))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), junit=junit)
    assert rc == 1


def test_live_uri_reason_prefix_is_exempt(tmp_path):
    # Cycle-5 P2-13 (fills the cycle-2 P2-13 gap — the reason-prefix
    # exclusion was re-keyed to "requires TORTOISE_DB_URI" but had NO unit
    # test; the old location-based _live_utils.py exclusion cannot survive
    # junitxml because the skip's `file` attribute is the CALLING test file).
    # The _skip_unless_live_uri reason (tests/_live_utils.py L25-26, verified)
    # CONTAINS the "FalkorDB" substring AND starts with the exempted family
    # prefix — it must NOT trip the guard (the visible URI-gate is
    # intentional):
    _junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable",
        "requires TORTOISE_DB_URI (live FalkorDB sidecar; see CI job "
        "test-concurrency-falkor)"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 junit=str(tmp_path / "junit.xml"))
    assert rc == 0  # reason-family prefix exempts; no manifest → no nodeid check


def test_live_6399_reason_prefix_is_exempt(tmp_path):
    # Cycle-6 P1-1 (FM-1): test_falkordb_compat.TestLiveServerCompat's
    # permanent class skip (reason "Live FalkorDB server on localhost:6399
    # not available") rides fast half b and never matches the provisioned
    # services (6379/16379 only — no 6399 in CI). It is a DOCUMENTED
    # permanent skip (legacy falkordblite 0.10.0 endpoint), so its reason
    # family prefix is exempted alongside "requires TORTOISE_DB_URI" — but
    # the availability-REGRESSION family ("Live FalkorDB (Docker) not
    # available") must STILL red (that is the guard's whole job):
    _junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable",
        "Live FalkorDB server on localhost:6399 not available"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 junit=str(tmp_path / "junit.xml"))
    assert rc == 0  # 6399 family exempt — the class skips by design
    _junit_red = _write(tmp_path, "junit-red.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable",
        "Live FalkorDB (Docker) not available"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 junit=str(tmp_path / "junit-red.xml"))
    assert rc == 1  # provisioned-service family stays RED (availability regression)


def test_embedded_unavailable_reason_prefix_is_exempt(tmp_path):
    # Cycle-7 P2-3: the carve-out/embedded-lane precondition family
    # ("embedded FalkorDBLite unavailable" / "redislite falkordb unavailable")
    # contains the "FalkorDB" substring but is NOT a docker-availability
    # regression — the files emitting it are the exempted carve-out stems +
    # embedded-lane files, which run embedded BY DESIGN under a URI job. The
    # family prefixes are exempted (mirror 6399); the availability-REGRESSION
    # family stays red:
    _junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable", "embedded FalkorDBLite unavailable"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 junit=str(tmp_path / "junit.xml"))
    assert rc == 0  # embedded-unavailability family exempt
    _junit2 = _write(tmp_path, "junit2.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable", "redislite falkordb unavailable"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 junit=str(tmp_path / "junit2.xml"))
    assert rc == 0  # lowercase-falkordb variant exempt too
    _junit_red = _write(tmp_path, "junit-red.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable", "Live FalkorDB (Docker) not available"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 junit=str(tmp_path / "junit-red.xml"))
    assert rc == 1  # availability-REGRESSION family stays RED


def _load_skip_guard_module():
    """Load tools/skip-guard.py (hyphenated — not importable as a module).

    Divergence from the plan text (`from tools.skip_guard import ...`): the
    tool file is hyphenated, so it cannot be imported by that name; load it
    from the file path via importlib instead.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("skip_guard", str(TOOL))
    assert spec and spec.loader, f"cannot load {TOOL}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_matcher_exempts_same_families():
    # Cycle-7 P2-4: half a keeps the LEGACY line matcher at P2 (and P1 CI
    # uses it) — it must exempt the SAME reason families as the junitxml
    # matcher, or a tier-2 PR routing test_falkordb_compat to half a reds on
    # the 6399 class skip. Feed -r fEs-format log lines through
    # tools/skip-guard.find_violations:
    find_violations = _load_skip_guard_module().find_violations

    log = (
        "SKIPPED [2] tests/test_falkordb_compat.py:367: "
        "Live FalkorDB server on localhost:6399 not available\n"
        "SKIPPED [1] tests/test_audit.py:31: embedded FalkorDBLite unavailable\n"
        "SKIPPED [1] tests/test_projection.py:2635: redislite falkordb unavailable\n"
        "SKIPPED [1] tests/test_ep_directional.py:35: "
        "requires TORTOISE_DB_URI (live FalkorDB sidecar; see CI job "
        "test-concurrency-falkor)\n"
    )
    assert find_violations(log) == [], (
        "legacy matcher must exempt the 6399 + embedded-unavailability + "
        "requires-URI families"
    )
    assert find_violations(
        "SKIPPED [1] tests/test_ep_directional.py:35: "
        "Live FalkorDB (Docker) not available\n"
    ) != [], "availability-REGRESSION family stays RED in the legacy matcher too"
    # The -rs regex must not hardcode the tests/ prefix: a skip for a file
    # elsewhere in the tree (integrations/tests/, validation/, ...) with an
    # availability-REGRESSION reason is still a violation.
    assert find_violations(
        "SKIPPED [1] integrations/tests/test_live.py:12: "
        "Live FalkorDB (Docker) not available\n"
    ) != [], "-rs matcher must be path-agnostic (not tests/-hardcoded)"
    # The -v reason regex must fire on REAL pytest -v output (reason followed
    # by the [ N%] progress marker — verified format), not just the bare form.
    assert find_violations(
        "tests/test_ep_directional.py::Test::test_live SKIPPED "
        "(Live FalkorDB (Docker) not available) [ 25%]\n"
    ) != [], "-v matcher must tolerate the trailing [ N%] progress marker"
    # Cycle-7 P2-4 follow-up (deep review): the -v progress line TRUNCATES the
    # reason at 80 cols, so for tests/test_falkordb_compat.py the "FalkorDB"
    # substring survives only in the FILENAME — that must NOT red (the -r fEs
    # summary is the authoritative never-truncated reason source).
    assert find_violations(
        "tests/test_falkordb_compat.py::TestLiveServerCompat::test_full_compat_flow "
        "SKIPPED [ 25%]\n"
    ) == [], "truncated -v line must not red via the filename's FalkorDB substring"


# ── --emit-manifest: the coverage-manifest GENERATOR (epic #1647 Task 6) ──
# Task 3 implemented the consumer (--manifest reconciliation against the
# junitxml). Task 6 adds the producer: `pytest <files> --collect-only -q
# -m 'not track_b'` -> one expected nodeid per line. These tests pin the
# pure filter + the generator's verbatim-file-list contract (plan-review
# P1-7: the generator must consume the run's file list verbatim, never a
# re-derived matrix list). No pytest is spawned — the runner is faked.

import importlib.util as _ilu  # noqa: E402


def _load_skip_guard():
    """Load tools/skip-guard.py in-process for the emit-manifest unit tests.

    The file is dash-named (skip-guard.py), so it cannot be imported as a
    regular module — the existing tests run it via subprocess for that
    reason. importlib loads it under a valid name; it imports only stdlib
    (re/subprocess/sys/xml/pathlib), so exec_module is safe.
    """
    spec = _ilu.spec_from_file_location("skip_guard_under_test", str(TOOL))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_skip_guard = _load_skip_guard()


COLLECT_ONLY_SAMPLE = """\
tests/test_ci_selection.py::test_docs_only_runs_tier1
tests/test_ci_selection.py::test_split_rejects_non_list
39 tests collected in 0.02s
"""


def test_collect_only_nodeids_keeps_nodeids_drops_summary():
    assert _skip_guard.collect_only_nodeids(COLLECT_ONLY_SAMPLE) == [
        "tests/test_ci_selection.py::test_docs_only_runs_tier1",
        "tests/test_ci_selection.py::test_split_rejects_non_list",
    ]


def test_collect_only_nodeids_handles_real_pytest_shapes():
    # Real pytest 9.1.1 outputs (verified 2026-08-24): deselected counts
    # ride the SUMMARY line only (deselected items are filtered at
    # collection and never printed as nodeids); warnings/errors during
    # collection must be dropped too (a stray line would trip the
    # consumer's invalid-line fail-closed check).
    sample = (
        "tests/test_ingest_safety.py::test_e2e8_gated_status_live_violation\n"
        "tests/test_ingest_safety.py::test_e2e17_read_surfaces_reachable_after_ingest\n"
        "9/13 tests collected (4 deselected) in 0.03s\n"
        "tests/test_a.py:12: PytestDeprecationWarning: something\n"
        "ERROR: cannot collect tests/test_b.py\n"
        "no tests collected (39 deselected) in 0.02s\n"
    )
    assert _skip_guard.collect_only_nodeids(sample) == [
        "tests/test_ingest_safety.py::test_e2e8_gated_status_live_violation",
        "tests/test_ingest_safety.py::test_e2e17_read_surfaces_reachable_after_ingest",
    ]


def test_emit_manifest_consumes_verbatim_file_list_and_marker(tmp_path):
    # plan-review P1-7: the spawned command must carry the run step's file
    # list VERBATIM (tests/... paths as given) + the same `-m` filter.
    captured = {}

    def fake_runner(cmd):
        captured["cmd"] = list(cmd)
        return 0, "tests/test_a.py::test_x\n1 tests collected in 0.00s\n"

    out = tmp_path / "expected-nodeids.txt"
    rc = _skip_guard.emit_manifest(
        ["tests/test_a.py", "tests/test_b.py"], "not track_b", out,
        runner=fake_runner)
    assert rc == 0
    cmd = captured["cmd"]
    assert cmd[:3] == [_skip_guard.sys.executable, "-m", "pytest"]
    assert cmd[3:5] == ["tests/test_a.py", "tests/test_b.py"]
    assert "--collect-only" in cmd and "-q" in cmd
    assert cmd[cmd.index("-m", 4) + 1] == "not track_b"  # skip `-m pytest`
    assert "-p" in cmd and cmd[cmd.index("-p") + 1] == "no:cacheprovider"
    assert out.read_text().startswith("#")
    assert "tests/test_a.py::test_x" in out.read_text()


def test_emit_manifest_passes_ignore_flags(tmp_path):
    # Epic #1647 Task 10 Step 1a (cycle-2 P2-14 / cycle-4 P2-11): the pmv
    # manifest must replicate its run's OWN excludes (--ignore=tests/e2e + the
    # $SLOW_IGNORES list) — a manifest without them expects e2e/slow nodeids
    # the pmv run never produces and every merge reds on vanished nodeids.
    captured = {}

    def fake_runner(cmd):
        captured["cmd"] = list(cmd)
        return 0, "tests/test_a.py::test_x\n1 tests collected in 0.00s\n"

    out = tmp_path / "expected-nodeids.txt"
    rc = _skip_guard.emit_manifest(
        ["tests/"], "not track_b", out,
        runner=fake_runner,
        ignores=("tests/e2e", "tests/test_slow_a.py", "tests/test_slow_b.py"))
    assert rc == 0
    cmd = captured["cmd"]
    assert "--ignore=tests/e2e" in cmd
    assert "--ignore=tests/test_slow_a.py" in cmd
    assert "--ignore=tests/test_slow_b.py" in cmd
    assert "--collect-only" in cmd and "-m" in cmd
    assert "tests/test_a.py::test_x" in out.read_text()


def test_emit_manifest_empty_files_writes_nothing(tmp_path):
    out = tmp_path / "expected-nodeids.txt"
    rc = _skip_guard.emit_manifest([], "not track_b", out,
                                   runner=lambda cmd: (0, ""))
    assert rc == 0
    assert not out.exists(), "empty $FILES must not write a manifest (guard skips)"


def test_emit_manifest_collect_failure_writes_no_manifest(tmp_path):
    # fail-closed: a collect-only failure propagates and writes NO manifest
    # (a vanished manifest must never vacuous-green — the consumer reds).
    out = tmp_path / "expected-nodeids.txt"
    rc = _skip_guard.emit_manifest(["tests/test_a.py"], "not track_b", out,
                                   runner=lambda cmd: (2, ""))
    assert rc == 2
    assert not out.exists()


# ── #3290: the live-URI gate has ONE reason string ────────────────────────

def _live_uri_skip_reasons() -> list[tuple[str, int, str]]:
    """Every skip/xfail reason under tests/ that gates on TORTOISE_DB_URI.

    Returns (repo-relative path, lineno, reason). Parsed with `ast` rather than
    a regex so a reason built from a non-constant expression is skipped rather
    than mis-matched.

    Scope is deliberate (code review P2): the walker covers `skipif` DECORATORS
    **and** in-body `pytest.skip(...)` / `pytest.xfail(...)` calls, because the
    #3339 fix itself skips from the body — a skipif-only scanner would be blind
    to the very form it introduced. The `TORTOISE_DB_URI` filter keeps the
    deliberately-RED probe class (`Live FalkorDB (Docker) not available`) out of
    scope: those are SUPPOSED to red the runtime guard when they fire, so
    asserting them here would be wrong.
    """
    import ast

    root = Path(__file__).resolve().parents[1]
    found: list[tuple[str, int, str]] = []
    for path in sorted((root / "tests").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, SyntaxError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name not in ("skipif", "skip", "xfail"):
                continue
            reason: object = None
            for kw in node.keywords:
                if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                    reason = kw.value.value
            # in-body form: pytest.skip("<reason>")
            if reason is None and node.args and isinstance(node.args[0], ast.Constant):
                reason = node.args[0].value
            if isinstance(reason, str) and "TORTOISE_DB_URI" in reason:
                found.append((str(path.relative_to(root)), node.lineno, reason))
    return found


def test_live_uri_skipif_reasons_are_guard_exempt():
    """#3339: no test may invent its own live-URI skip reason.

    tools/skip-guard.py exempts the intentional availability-class families by
    REASON PREFIX, so that a live test legitimately skipping in the tier-2
    URI-less lane does not red `test (a)`.

    An ad-hoc skip reason mentioning TORTOISE_DB_URI ("live FalkorDB required
    (TORTOISE_DB_URI unset)" — the #3339 offender) skips in that lane AND trips
    the guard, redding whichever PR's selection happened to land in the URI-less
    shape. Route through tests/_live_utils.py::_skip_unless_live_uri instead.

    The exemption predicate is the guard's OWN `is_falkor_reason_violation()`,
    not a re-derived startswith (code review P3): re-deriving it drops the
    embedded-prefix family and the case-insensitive handling, so the static and
    runtime checks could disagree.
    """
    scanned = _live_uri_skip_reasons()
    # Anti-vacuity: the exempt live-URI gates really do exist in this tree, so a
    # scan that finds nothing means the walker broke, not that the tree is clean.
    assert scanned, "walker found no live-URI skip reasons — scan is broken"

    offenders = [
        (path, line, reason)
        for path, line, reason in scanned
        if _skip_guard.is_falkor_reason_violation(reason)
    ]
    assert offenders == [], (
        "live-URI skip reason(s) the #1436 guard treats as a REAL violation — "
        "they red `test (a)` in the tier-2 URI-less lane. Use "
        "tests/_live_utils._skip_unless_live_uri() / LIVE_URI_SKIP_REASON:\n"
        + "\n".join(f"  {p}:{ln}: {r!r}" for p, ln, r in offenders)
    )


def test_shared_live_uri_reason_stays_in_the_exempt_family():
    """#3339: the shared reason string is the ONE owner — pin it to the exemption.

    Every other live-URI skip routes through it, so if this string were ever
    edited outside the guard's exempt prefixes the whole tree would start
    redding `test (a)` at once. Cheapest possible place to catch that.
    """
    from tests._live_utils import LIVE_URI_SKIP_REASON

    assert not _skip_guard.is_falkor_reason_violation(LIVE_URI_SKIP_REASON), (
        f"tests/_live_utils.LIVE_URI_SKIP_REASON is no longer exempt: "
        f"{LIVE_URI_SKIP_REASON!r} — tools/skip-guard.py exempts by prefix "
        f"{_skip_guard._EXEMPT_REASON_PREFIXES}; update BOTH together"
    )


# ── #2573: the embedder-unavailable reason class ──────────────────────────
# The dense-retrieval leg degrades silently to keyword-only when the embedding
# model cannot be loaded, and the suite reports green while asserting a
# different meaning (semantic recall changes under TF-IDF). These reasons
# mention neither FalkorDB nor a manifest, so they could not trip the guard at
# all before this class existed. The reasons below are VERBATIM from the tree
# (file:line in each id) — if a reason string changes, the guard silently stops
# matching it, so the assertion is written against the real text.

# (nodeid, verbatim reason) — the -v progress form is built from these.
EMBEDDER_UNAVAILABLE_REASONS = [
    ("tests/test_cross_lens.py::test_real_embedder_smoke",
     "bge-small-en-v1.5 not cached locally — skipping real-embedder test"),  # :455
    ("tests/test_cross_lens.py::test_real_embedder_smoke",
     "bge-small-en-v1.5 unavailable — model load timed out"),  # :457
    ("tests/test_search_engine.py::test_dense_leg",
     "sentence-transformers / all-MiniLM-L6-v2 cache not available — "
     "dense-leg assertion skipped"),  # :198
    ("tests/test_extractor.py::test_multi_source_embedding",
     "sentence-transformers / bge-small cache not available — multi-source "
     "embedding test skipped (embedder-less CI)"),  # :417
    ("tests/test_assembly_pure.py::test_shipped_config",
     "embedder unavailable — the shipped hybrid leg cannot be exercised in "
     "this lane (see #3223)"),  # :694
    ("tests/test_hosted_api.py::test_thread_safety",
     "bge-small-en-v1.5 not cached — skipping thread-safety test"),  # :5721
]

# Reasons that must NOT trip the embedder class. Each is a REAL reason from the
# tree or a minimal probe of a boundary: generic "cache"/"model" words, an
# embedder-context word with no availability claim, and the deliberately
# EXCLUDED collection-time offline-precondition family (a non-shipped alternate
# model with `local_files_only=True`, which fires by design in CI).
NON_EMBEDDER_REASONS = [
    "requires network access",
    "sklearn not installed",
    "frozen LongMemEval-S dataset not cached (CI)",   # "not cached", no model ctx
    "result cache not available for this run",        # availability, no model ctx
    "model checkpoint download disabled",             # the word "model" only
    "no embedder AND no sklearn — probe cannot run",  # context, no availability
    "embedder present — degraded-absence path not exercised",  # context only
    "all-MiniLM-L6-v2 not in HF cache (HF_HUB_OFFLINE in CI)",  # out of class
]


def _v_line(nodeid: str, reason: str) -> str:
    """Real pytest -v progress shape: '<nodeid> SKIPPED (<reason>) [ 25%]'."""
    return f"{nodeid} SKIPPED ({reason}) [ 25%]\n"


class TestGuardFailsOnEmbedderUnavailableSkip:
    def test_real_reasons_all_red_in_v_format(self):
        for nodeid, reason in EMBEDDER_UNAVAILABLE_REASONS:
            proc = run_guard(_v_line(nodeid, reason))
            assert proc.returncode == 1, f"embedder skip not caught: {reason!r}"
            assert nodeid in proc.stdout
            assert "embedder-unavailable" in proc.stdout

    def test_rs_summary_format_red(self):
        # -r fEs summary is the authoritative never-truncated reason source.
        proc = run_guard(
            "SKIPPED [2] tests/test_search_engine.py:198: sentence-transformers "
            "/ all-MiniLM-L6-v2 cache not available — dense-leg assertion "
            "skipped\n"
        )
        assert proc.returncode == 1
        assert "test_search_engine.py" in proc.stdout

    def test_non_embedder_reasons_do_not_trip(self):
        for reason in NON_EMBEDDER_REASONS:
            proc = run_guard(_v_line("tests/test_x.py::test_y", reason))
            assert proc.returncode == 0, (
                f"FALSE TRIP on a non-embedder reason: {reason!r}\n"
                f"stdout={proc.stdout!r}"
            )

    def test_legacy_line_matcher_wires_the_embedder_class(self):
        # Half a / P1 CI uses find_violations directly (no junitxml) — the
        # embedder class must red there too, or the two paths disagree.
        find_violations = _skip_guard.find_violations
        for nodeid, reason in EMBEDDER_UNAVAILABLE_REASONS:
            assert find_violations(_v_line(nodeid, reason)) == [nodeid], (
                f"legacy matcher missed the embedder class: {reason!r}"
            )
        for reason in NON_EMBEDDER_REASONS:
            assert find_violations(_v_line("tests/test_x.py::test_y", reason)) == [], (
                f"legacy matcher false-tripped on: {reason!r}"
            )

    def test_junitxml_matcher_wires_the_embedder_class(self, tmp_path):
        # The junitxml path (the AUTHORITATIVE reason source) must agree with
        # the legacy line matcher — a reason-level skip reds with no manifest.
        junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
            "redislite unavailable",
            "bge-small-en-v1.5 not cached locally — skipping real-embedder test"))
        proc = run_guard_with_manifest(str(tmp_path / "pytest.log"), junit=junit)
        assert proc == 1

    def test_junitxml_non_embedder_reason_stays_green(self, tmp_path):
        # Same path, non-embedder reason → observed skip, no reason violation.
        junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
            "redislite unavailable",
            "result cache not available for this run"))
        rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), junit=junit)
        assert rc == 0

    def test_manifest_mode_embedder_skip_reds_despite_nodeid_observed(
            self, tmp_path):
        # Coverage ≠ healthy: the nodeid IS observed (satisfies the manifest)
        # but the reason is an embedder-availability regression → red, exactly
        # like the FalkorDB availability-REGRESSION family.
        junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
            "redislite unavailable",
            "sentence-transformers / all-MiniLM-L6-v2 cache not available — "
            "dense-leg assertion skipped"))
        manifest = _write(
            tmp_path, "manifest.txt",
            "tests/test_embedded_lifecycle_fast_close.py::test_ephemeral_nosave\n")
        rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest,
                                     junit=junit)
        assert rc == 1

    def test_both_classes_are_independent(self):
        # Regression guard for the two families: each predicate is blind to the
        # other's reasons, so neither can shadow the other.
        falkor_only = "Live FalkorDB (Docker) not available"
        embedder_only = "bge-small-en-v1.5 not cached locally"
        assert _skip_guard.is_falkor_reason_violation(falkor_only)
        assert not _skip_guard.is_embedder_reason_violation(falkor_only)
        assert _skip_guard.is_embedder_reason_violation(embedder_only)
        assert not _skip_guard.is_falkor_reason_violation(embedder_only)

    def test_existing_falkordb_exemptions_still_live(self):
        # The embedder class must not have disturbed the FalkorDB reason-family
        # exemptions (regression guard, mirroring
        # test_legacy_matcher_exempts_same_families).
        for exempt in ("requires TORTOISE_DB_URI (live FalkorDB sidecar)",
                       "Live FalkorDB server on localhost:6399 not available",
                       "embedded FalkorDBLite unavailable",
                       "redislite falkordb unavailable"):
            assert not _skip_guard.is_falkor_reason_violation(exempt), exempt
            assert not _skip_guard.is_embedder_reason_violation(exempt), exempt
        assert _skip_guard.find_violations(
            "SKIPPED [1] tests/test_falkordb_compat.py:367: "
            "Live FalkorDB server on localhost:6399 not available\n") == []

    def test_embedder_reasons_are_verbatim_in_the_tree(self):
        # Anti-drift: the reasons asserted above must still exist verbatim as
        # skip reasons in tests/ — a reworded skip would silently stop being
        # caught, and the guard's own tests would keep passing. Reasons are
        # collected with `ast` so the source's implicit string concatenation is
        # already folded (`ast.Constant` holds the joined value).
        real = _all_skip_reasons()
        assert real, "walker found no skip reasons — scan is broken"
        for _nodeid, reason in EMBEDDER_UNAVAILABLE_REASONS:
            assert reason in real, (
                f"embedder skip reason no longer present in tests/ — the guard "
                f"would silently stop matching it: {reason!r}"
            )


def _all_skip_reasons() -> set[str]:
    """Every literal skip/xfail reason under tests/ (ast, concatenation-folded)."""
    import ast

    root = Path(__file__).resolve().parents[1]
    found: set[str] = set()
    for path in sorted((root / "tests").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, SyntaxError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name not in ("skipif", "skip", "xfail"):
                continue
            reason: object = None
            for kw in node.keywords:
                if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                    reason = kw.value.value
            if reason is None and node.args and isinstance(node.args[0], ast.Constant):
                reason = node.args[0].value
            if isinstance(reason, str):
                found.add(reason)
    return found


# ── #4221: the module-level collection-skip class ─────────────────────────
# The #4221 shape is a skip that aborts a WHOLE MODULE at collection time
# (pytest.skip(..., allow_module_level=True)): tests/test_event_log.py +
# tests/test_crash_recovery_e2e.py swallowed a ModuleNotFoundError into one
# and hid 26 data-integrity tests (SHA-256 hash-chained event log + crash
# recovery). The class is STRUCTURAL — pytest's constant "collection skipped"
# junitxml message — never a reason-text match: the verbatim #4221 reason
# below contains no import text at all, while "import text" matching
# false-positived on every deliberate pytest.importorskip(...) probe (P0 — the
# carve-out lane reddened on botocore).
#
# `git show origin/main:tests/test_event_log.py` L27 (verbatim; the branch
# fixed the import, so this text exists only on origin/main — hence the
# literal here rather than a tree scan):
#     pytest.skip("shared_state package not installed — event log tests require
#                 it", allow_module_level=True)
COLLECTION_SKIP_REASON = (
    "shared_state package not installed — event log tests require it")

# Reasons that must NOT trip the collection-skip class. A PER-TEST
# pytest.importorskip(...) is an optional dependency BY CONSTRUCTION. The REAL
# message it emits is pinned here (P2 — the old "sklearn not installed" was an
# invented string the guard never actually saw, so it asserted a property the
# guard did not have).
OPTIONAL_DEPENDENCY_SKIP_REASONS = [
    "requires network access",
    "could not import 'sklearn': No module named 'sklearn'",
    "could not import 'botocore.exceptions': No module named 'botocore'",
    "frozen LongMemEval-S dataset not cached (CI)",
]


def _collection_skip_junit(reason, *, file="tests/test_modskip.py",
                           module="tests.test_modskip", line=5):
    """REAL pytest 9.1.1 junitxml for a module-level collection skip: empty
    classname, name = dotted module path, constant message="collection
    skipped", and the real reason in the element TEXT as pytest's
    ``(path, line, 'Skipped: <reason>')`` tuple."""
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="1">'
        f'<testcase classname="" name="{module}" file="{file}" time="0.000">'
        '<skipped message="collection skipped">'
        f"('{file}', {line}, 'Skipped: {reason}')"
        "</skipped></testcase></testsuite></testsuites>"
    )


class TestGuardFailsOnModuleLevelCollectionSkip:
    def test_real_pytest_module_skip_fixture_is_caught(self, tmp_path):
        # End-to-end proof with a REAL pytest run: a module that aborts at
        # collection (the #4221 shape + its verbatim reason) writes the
        # junitxml the guard must red on. Not a hand-written string that
        # happens to match a regex.
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_modskip.py").write_text(
            "import pytest\n"
            f"pytest.skip({COLLECTION_SKIP_REASON!r}, allow_module_level=True)\n\n"
            "def test_never_runs():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        junit = tmp_path / "junit.xml"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir / "test_modskip.py"),
             "-p", "no:cacheprovider", "-o", "junit_family=xunit1",
             f"--junitxml={junit}"],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        # pytest exits 5 ("no tests collected") when the ONLY module in the
        # run aborts at collection — the junitxml is still written, which is
        # the evidence the guard reads.
        assert proc.returncode in (0, 5), proc.stdout + proc.stderr
        assert "collection skipped" in junit.read_text(encoding="utf-8"), (
            "the fixture did not produce a real collection-skip junitxml"
        )
        rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), junit=str(junit))
        assert rc == 1, "a real whole-module skip did not red the guard"

    def test_real_4221_reason_is_caught_and_reported(self, tmp_path):
        junit = _write(tmp_path, "junit.xml", _collection_skip_junit(
            COLLECTION_SKIP_REASON, file="tests/test_event_log.py",
            module="tests.test_event_log"))
        proc = subprocess.run(
            [sys.executable, str(TOOL), str(tmp_path / "pytest.log"),
             f"--junitxml={junit}"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 1
        assert "tests/test_event_log.py" in proc.stdout
        assert "collection" in proc.stdout

    def test_uri_gate_module_skip_is_exempt(self, tmp_path):
        # A WHOLE-MODULE skip is not automatically a vacancy: the docker-lane
        # URI gates (tests/test_capabilities_endpoint.py, the onboarding W3-W8
        # suites, test_eval_ingest_cache.py) abort their module BY DESIGN on a
        # URI-less tier-2 leg. Those must not red.
        junit = _write(tmp_path, "junit.xml", _collection_skip_junit(
            "docker-lane capabilities tests require TORTOISE_DB_URI "
            "(tier-2 embedded legs skip)",
            file="tests/test_capabilities_endpoint.py",
            module="tests.test_capabilities_endpoint"))
        rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), junit=junit)
        assert rc == 0

    def test_legacy_line_matcher_does_not_assert_the_absent_signal(self):
        # The -rs/-v line text carries NO module-level signal: a collection
        # skip's summary line is byte-identical to a per-test skip's ("SKIPPED
        # [N] file.py:line: <reason>") and the location can be a helper module.
        # The guard therefore does not assert this class there (see
        # find_violations) — a real whole-module skip is junitxml-only.
        find_violations = _skip_guard.find_violations
        assert find_violations(
            "SKIPPED [1] tests/test_event_log.py:27: "
            f"{COLLECTION_SKIP_REASON}\n") == []

    def test_predicate_is_structural_not_reason_text(self):
        # The trip needs the constant collection-skip message; the #4221
        # reason alone (no structural marker) does NOT trip.
        assert _skip_guard.is_collection_skip_violation(
            _skip_guard._COLLECTION_SKIP_MESSAGE,
            f"(..., 27, 'Skipped: {COLLECTION_SKIP_REASON}')")
        assert not _skip_guard.is_collection_skip_violation(
            COLLECTION_SKIP_REASON, "")
        assert not _skip_guard.is_collection_skip_violation(
            "could not import 'sklearn': No module named 'sklearn'", "")


class TestGuardAcceptsDeliberateOptionalDependencySkips:
    def test_real_importorskip_message_does_not_trip_line_matcher(self):
        for reason in OPTIONAL_DEPENDENCY_SKIP_REASONS:
            proc = run_guard(_v_line("tests/test_x.py::test_y", reason))
            assert proc.returncode == 0, (
                f"FALSE TRIP on an optional-dependency skip: {reason!r}\n"
                f"stdout={proc.stdout!r}"
            )

    def test_real_importorskip_message_does_not_trip_junitxml(self, tmp_path):
        # The botocore shape that reddened the carve-out lane: a PER-TEST
        # importorskip inside tests/test_hosted_backup.py (non-empty classname,
        # a normal <skipped message="could not import ...">), never a
        # whole-module collection skip.
        junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
            "redislite unavailable",
            "could not import 'botocore.exceptions': No module named 'botocore'"))
        rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), junit=junit)
        assert rc == 0

    def test_three_classes_are_independent(self):
        # Regression guard for the three families: the collection predicate is
        # blind to the other two classes' reasons and vice versa.
        falkor_only = "Live FalkorDB (Docker) not available"
        embedder_only = "bge-small-en-v1.5 not cached locally"
        import_message = ("could not import 'shared_state': "
                          "No module named 'shared_state'")
        assert not _skip_guard.is_collection_skip_violation(falkor_only, "")
        assert not _skip_guard.is_collection_skip_violation(embedder_only, "")
        assert not _skip_guard.is_collection_skip_violation(import_message, "")
        assert not _skip_guard.is_falkor_reason_violation(
            _skip_guard._COLLECTION_SKIP_MESSAGE)
        assert not _skip_guard.is_embedder_reason_violation(
            _skip_guard._COLLECTION_SKIP_MESSAGE)
