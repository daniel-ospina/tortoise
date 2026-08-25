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
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED)
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
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
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
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable",
        "Live FalkorDB server on localhost:6399 not available"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 junit=str(tmp_path / "junit.xml"))
    assert rc == 0  # 6399 family exempt — the class skips by design
    junit_red = _write(tmp_path, "junit-red.xml", JUNIT_SKIPPED.replace(
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
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable", "embedded FalkorDBLite unavailable"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 junit=str(tmp_path / "junit.xml"))
    assert rc == 0  # embedded-unavailability family exempt
    junit2 = _write(tmp_path, "junit2.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable", "redislite falkordb unavailable"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 junit=str(tmp_path / "junit2.xml"))
    assert rc == 0  # lowercase-falkordb variant exempt too
    junit_red = _write(tmp_path, "junit-red.xml", JUNIT_SKIPPED.replace(
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


_skip_guard = _load_skip_guard()  # noqa: E402


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
