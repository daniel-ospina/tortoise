"""Unit tests for the P3 canary-streak classifier (epic #1647 Task 9
Step 6, cycle-6 P2-7 int-9).

Fixtures-driven bucket tests: each case drives tools/testdb_canary_classify
with a fake junitxml/manifest/step_wall/divergence-log input set. The
classification must be deterministic (same inputs -> same bucket) and read
ONLY the declared input files.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from tools.testdb_canary_classify import (
    CANARY_DROP_THRESHOLD,
    STEP_WALL_GATE_SECONDS,
    _read_divergence_registry,
    classify,
)

RUN = 424242


def _junit_xml(failures=(), skips=()):
    """Build an xunit1 junitxml: failures = [(nodeid, message)] (converted
    to file/classname/name), skips = [(nodeid, reason)]."""
    cases = []
    for nodeid, message in failures:
        file, _, name = nodeid.partition("::")
        cases.append(
            f'<testcase classname="tests.{file.rsplit("/", 1)[-1][:-3]}'
            f'" name="{name}" file="{file}" line="1" time="0.01">'
            f'<failure message="{message}">{message}</failure></testcase>')
    for nodeid, reason in skips:
        file, _, name = nodeid.partition("::")
        cases.append(
            f'<testcase classname="tests.{file.rsplit("/", 1)[-1][:-3]}'
            f'" name="{name}" file="{file}" line="1" time="0.01">'
            f'<skipped message="{reason}"/></testcase>')
    # one green case so the observed set is non-empty
    return (
        "<testsuite>"
        + "".join(cases)
        + '<testcase classname="tests.test_ok" name="test_ok" '
          'file="tests/test_ok.py" line="1" time="0.01"/>'
        + "</testsuite>")


def _write(tmp_path, name, content):
    p = Path(tmp_path) / name
    p.write_text(content)
    return str(p)


@pytest.fixture
def inputs(tmp_path):
    """A green input set: junitxml + manifest + step_wall + divergence log."""
    nodeid = "tests/test_ok.py::test_ok"
    junit = _write(tmp_path, "junit.xml", _junit_xml())
    manifest = _write(tmp_path, "expected-nodeids.txt",
                      f"# expected\n{nodeid}\n")
    wall = _write(tmp_path, "step_wall.txt", "1200")
    divlog = _write(tmp_path, "divergence.md",
                    "# log\nD6: tests/test_divergence_conformance.py::"
                    "test_d6_freshness_composite_mode_split\n")
    return {"junitxml": junit, "manifest": manifest, "step_wall": wall,
            "divergence_log": divlog}


def _fresh(prev=None):
    return prev or {"runs": [], "consecutive_green": 0, "canary_dropped": False}


def test_green_increments_capped_at_threshold(inputs):
    """(f) green junitxml+manifest+step_wall -> increment, capped at N."""
    prev = _write(inputs["junitxml"].rsplit("/", 1)[0], "prev.json",
                  json.dumps(_fresh()))
    rec = classify(inputs["junitxml"], inputs["manifest"],
                   inputs["step_wall"], inputs["divergence_log"], prev, RUN)
    assert rec["last"]["bucket"] == "green"
    assert rec["consecutive_green"] == 1
    assert rec["canary_dropped"] is False
    # capped at the drop threshold: 5 green runs flips the drop, stays 5
    streak = {"runs": [], "consecutive_green": CANARY_DROP_THRESHOLD,
              "canary_dropped": True}
    prev = _write(inputs["junitxml"].rsplit("/", 1)[0], "prev.json",
                  json.dumps(streak))
    rec = classify(inputs["junitxml"], inputs["manifest"],
                   inputs["step_wall"], inputs["divergence_log"], prev, RUN)
    assert rec["consecutive_green"] == CANARY_DROP_THRESHOLD  # capped


def test_d_entry_failure_non_reset_logged(inputs):
    """(a) a failing nodeid matching the D1–D16 registry -> NON-reset + logged."""
    prev = _write(inputs["junitxml"].rsplit("/", 1)[0], "prev.json",
                  json.dumps({"runs": [], "consecutive_green": 3,
                              "canary_dropped": False}))
    junit = _write(
        inputs["junitxml"].rsplit("/", 1)[0], "junit2.xml",
        _junit_xml(failures=[
            ("tests/test_divergence_conformance.py::"
             "test_d6_freshness_composite_mode_split[server]",
             "assert 1 == 0")]))
    rec = classify(junit, inputs["manifest"], inputs["step_wall"],
                   inputs["divergence_log"], prev, RUN)
    assert rec["last"]["bucket"] == "divergence"
    assert rec["consecutive_green"] == 3  # preserved, not reset


def test_guard_red_resets(inputs):
    """(b) a FalkorDB-reasoned skip (availability-REGRESSION family) -> 0."""
    prev = _write(inputs["junitxml"].rsplit("/", 1)[0], "prev.json",
                  json.dumps({"runs": [], "consecutive_green": 4,
                              "canary_dropped": False}))
    junit = _write(
        inputs["junitxml"].rsplit("/", 1)[0], "junit3.xml",
        _junit_xml(skips=[
            ("tests/test_ep_directional.py::test_x",
             "Live FalkorDB (Docker) not available")]))
    rec = classify(junit, inputs["manifest"], inputs["step_wall"],
                   inputs["divergence_log"], prev, RUN)
    assert rec["last"]["bucket"] == "guard-red"
    assert rec["consecutive_green"] == 0


def test_guard_exempt_family_does_not_red(inputs):
    """The INTENTIONAL skip families stay exempt (requires-URI, 6399,
    embedded-unavailable) — the carve-out / live-utils skips are not
    docker-lane regressions."""
    prev = _write(inputs["junitxml"].rsplit("/", 1)[0], "prev.json",
                  json.dumps({"runs": [], "consecutive_green": 2,
                              "canary_dropped": False}))
    junit = _write(
        inputs["junitxml"].rsplit("/", 1)[0], "junit3b.xml",
        _junit_xml(skips=[
            ("tests/test_live_utils.py::test_y",
             "requires TORTOISE_DB_URI (live FalkorDB sidecar)"),
            ("tests/test_smoke_embedded.py::test_smoke",
             "embedded FalkorDBLite unavailable"),
            ("tests/test_falkordb_compat.py::TestLiveServerCompat::test_z",
             "Live FalkorDB server on localhost:6399 not available")]))
    rec = classify(junit, inputs["manifest"], inputs["step_wall"],
                   inputs["divergence_log"], prev, RUN)
    assert rec["last"]["bucket"] == "green"
    assert rec["consecutive_green"] == 3


def test_manifest_red_resets(inputs):
    """(c) a vanished expected nodeid -> 0."""
    prev = _write(inputs["junitxml"].rsplit("/", 1)[0], "prev.json",
                  json.dumps({"runs": [], "consecutive_green": 2,
                              "canary_dropped": False}))
    manifest = _write(
        inputs["junitxml"].rsplit("/", 1)[0], "manifest2.txt",
        "# expected\ntests/test_ok.py::test_ok\ntests/test_gone.py::test_x\n")
    rec = classify(inputs["junitxml"], manifest, inputs["step_wall"],
                   inputs["divergence_log"], prev, RUN)
    assert rec["last"]["bucket"] == "manifest-red"
    assert rec["consecutive_green"] == 0


def test_step_wall_gate_resets_even_when_green(inputs):
    """(d) step_wall >= 55m -> 0 even with green junitxml+manifest (the
    cycle-4 P2-4 mandatory-input case: a run that silently rides the
    watchdog must break the streak)."""
    prev = _write(inputs["junitxml"].rsplit("/", 1)[0], "prev.json",
                  json.dumps({"runs": [], "consecutive_green": 3,
                              "canary_dropped": False}))
    wall = _write(inputs["junitxml"].rsplit("/", 1)[0], "wall2.txt",
                  str(STEP_WALL_GATE_SECONDS + 1))
    rec = classify(inputs["junitxml"], inputs["manifest"], wall,
                   inputs["divergence_log"], prev, RUN)
    assert rec["last"]["bucket"] == "step-wall-gate"
    assert rec["consecutive_green"] == 0


def test_infra_flake_resets(inputs):
    """(e) infra-flake families -> 0: unparseable junitxml AND a
    connection-refused failure (docker service down)."""
    prev = _write(inputs["junitxml"].rsplit("/", 1)[0], "prev.json",
                  json.dumps({"runs": [], "consecutive_green": 4,
                              "canary_dropped": False}))
    bad = _write(inputs["junitxml"].rsplit("/", 1)[0], "bad.xml", "<not xml")
    rec = classify(bad, inputs["manifest"], inputs["step_wall"],
                   inputs["divergence_log"], prev, RUN)
    assert rec["last"]["bucket"] == "infra-flake"
    assert rec["consecutive_green"] == 0
    # connection-refused failure = the docker service is down (infra)
    junit = _write(
        inputs["junitxml"].rsplit("/", 1)[0], "junit4.xml",
        _junit_xml(failures=[
            ("tests/test_search_engine.py::test_x",
             "redis.exceptions.ConnectionError: Error 111 connecting to "
             "localhost:6379. Connection refused.")]))
    rec = classify(junit, inputs["manifest"], inputs["step_wall"],
                   inputs["divergence_log"], prev, RUN)
    assert rec["last"]["bucket"] == "infra-flake"
    assert rec["consecutive_green"] == 0


def test_unexpected_failure_resets(inputs):
    """A failure NOT in the D1–D16 registry -> reset (unexpected divergence)."""
    prev = _write(inputs["junitxml"].rsplit("/", 1)[0], "prev.json",
                  json.dumps({"runs": [], "consecutive_green": 2,
                              "canary_dropped": False}))
    junit = _write(
        inputs["junitxml"].rsplit("/", 1)[0], "junit5.xml",
        _junit_xml(failures=[
            ("tests/test_ranking.py::test_rerank", "assert 3 == 4")]))
    rec = classify(junit, inputs["manifest"], inputs["step_wall"],
                   inputs["divergence_log"], prev, RUN)
    assert rec["last"]["bucket"] == "unexpected-divergence"
    assert rec["consecutive_green"] == 0


def test_missing_prev_streak_starts_fresh(inputs):
    """No previous streak file (first run after the flip) -> fresh chain."""
    rec = classify(inputs["junitxml"], inputs["manifest"],
                   inputs["step_wall"], inputs["divergence_log"], None, RUN)
    assert rec["consecutive_green"] == 1
    assert rec["run_id"] == RUN
    assert RUN in rec["runs"]


def _marker(tmp_path, **kw):
    data = {"run_id": RUN, "half": "b", "event": "push", "full": "true"}
    data.update(kw)
    return _write(tmp_path, "producer.json", json.dumps(data))


def test_producer_marker_qualifies_green(inputs):
    """The population gate's executable form: a valid full==true half-b
    producer marker lets a green run increment."""
    marker = _marker(inputs["junitxml"].rsplit("/", 1)[0])
    rec = classify(inputs["junitxml"], inputs["manifest"],
                   inputs["step_wall"], inputs["divergence_log"], None, RUN,
                   producer_marker=marker)
    assert rec["last"]["bucket"] == "green"
    assert rec["consecutive_green"] == 1


def test_producer_marker_missing_is_infra(inputs):
    """No producer marker (the half-b leg did not qualify — e.g. a cancelled
    leg) -> infra-flake reset, never a green increment."""
    missing = str(Path(inputs["junitxml"]).parent / "no-such-producer.json")
    rec = classify(inputs["junitxml"], inputs["manifest"],
                   inputs["step_wall"], inputs["divergence_log"], None, RUN,
                   producer_marker=missing)
    assert rec["last"]["bucket"] == "infra-flake"
    assert rec["consecutive_green"] == 0


def test_producer_marker_wrong_shape_is_infra(inputs):
    """A producer marker proving a NON-qualifying leg (half a / full=false)
    -> infra-flake reset (the run is not a valid canary population member)."""
    for kw in ({"half": "a"}, {"full": "false"}):
        marker = _marker(inputs["junitxml"].rsplit("/", 1)[0], **kw)
        rec = classify(inputs["junitxml"], inputs["manifest"],
                       inputs["step_wall"], inputs["divergence_log"], None,
                       RUN, producer_marker=marker)
        assert rec["last"]["bucket"] == "infra-flake"
        assert rec["consecutive_green"] == 0


def test_empty_step_wall_is_infra(inputs):
    """An EMPTY step_wall file is a contract violation like a missing one
    (never a vacuous pass through the gate)."""
    wall = _write(inputs["junitxml"].rsplit("/", 1)[0], "wall-empty.txt", "")
    rec = classify(inputs["junitxml"], inputs["manifest"], wall,
                   inputs["divergence_log"], None, RUN)
    assert rec["last"]["bucket"] == "infra-flake"


def test_malformed_manifest_is_infra(inputs):
    """A manifest line without '::' is not a pytest nodeid — fail closed
    (infra-flake) instead of silently dropping a line that could mask a
    vanished nodeid (skip-guard's invalid-line contract)."""
    manifest = _write(inputs["junitxml"].rsplit("/", 1)[0], "manifest-bad.txt",
                      "# expected\ntests/test_ok.py::test_ok\n12 tests collected\n")
    rec = classify(inputs["junitxml"], manifest, inputs["step_wall"],
                   inputs["divergence_log"], None, RUN)
    assert rec["last"]["bucket"] == "infra-flake"


def test_classifier_reads_only_declared_files(inputs):
    """The classifier must consume ONLY the P1-7 artifact contract — never a
    step-output/$GITHUB_OUTPUT value (cycle-6 P1-7 int-2). Source-inspection
    pin (docstrings/comments stripped): no os.environ reads and no
    GITHUB_OUTPUT references in the code."""
    import re as _re

    src = (Path(__file__).resolve().parents[1] / "tools" /
           "testdb_canary_classify.py").read_text()
    code = _re.sub(r'"""[\s\S]*?"""', "", src)   # strip docstrings
    code = _re.sub(r"#[^\n]*", "", code)            # strip comments
    assert "GITHUB_OUTPUT" not in code, \
        "the classifier must never read a steps-output value"
    assert "os.environ" not in code, \
        "the classifier must consume the declared input files only"


def test_divergence_registry_parses_markdown():
    """The divergence log's `D#: nodeid` lines parse to prefixes (plain and
    `- ` list forms both parse — the registry section of
    divergence-confirmation.md uses the colon form)."""
    text = ("# log\n"
            "- D6: tests/test_divergence_conformance.py::test_d6_x\n"
            "D8: tests/test_divergence_conformance.py::test_d8_y\n")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(text)
        name = fh.name
    try:
        prefixes = _read_divergence_registry(name)
        assert "tests/test_divergence_conformance.py::test_d6_x" in prefixes
        assert "tests/test_divergence_conformance.py::test_d8_y" in prefixes
    finally:
        os.unlink(name)
