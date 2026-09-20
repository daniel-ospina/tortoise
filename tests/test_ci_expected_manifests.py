"""The frozen expected-nodeid manifests and the CI checks that consume them.

#4207 and #4215 are the same defect seen twice: a check that reads the tree
cannot notice a test the tree stopped collecting. `tools/skip-guard.py
--manifest` generates its expectation from `--collect-only` of the *same* tree,
so a gate or flag that removes a test removes it from both sides of the
comparison and CI stays green.

The fix is a FROZEN set: `config/ci-expected-nodeids/*.txt` is checked in, and
every nodeid in it must still appear as a junitxml `<testcase>` (passed OR
skipped) in the lane that runs it. This file pins three things:

1. the manifests are well-formed, their files exist, and they are non-empty;
2. the workflow actually invokes them, with `--manifest-only` (without that flag
   a URI-less lane false-reds on its EXPECTED skips — measured: 24 collection
   violations in the d14 job);
3. the platform-gated manifest still covers every file `tests/test_markers.py`
   registers, so the source scan and the runtime check cannot drift apart;

and it BITES: a junit missing one expected nodeid must fail, and a junit
containing all of them must pass. A pin that cannot fail is not a pin.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "config" / "ci-expected-nodeids"
WORKFLOW = ROOT / ".github" / "workflows" / "python-ci.yml"
SKIP_GUARD = ROOT / "tools" / "skip-guard.py"

EMBEDDED = MANIFEST_DIR / "embedded-only.txt"
PLATFORM_GATED = MANIFEST_DIR / "platform-gated.txt"


def _nodeids(path: Path) -> list[str]:
    """The manifest's nodeids, ignoring '#' comments and blank lines."""
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _module_dotted(file: str) -> str:
    """`tests/test_x.py` -> `tests.test_x` (the junit `classname` prefix)."""
    assert file.endswith(".py"), file
    return file[: -len(".py")].replace("/", ".")


def _junit_for(nodeids: list[str], path: Path) -> None:
    """Write a junit (xunit1 shape) that contains exactly `nodeids`.

    pytest's own junit is the only writer in CI, so the attributes that
    `skip-guard._read_junitxml` reads are reproduced exactly: `file`, `classname`
    (module-dotted, plus any class), `name`. The class part is joined with '.'
    in classname and '::' in the nodeid, which is what that reader inverts.
    """
    suite = ET.Element("testsuite", {"name": "pytest", "tests": str(len(nodeids))})
    for nodeid in nodeids:
        file, *parts = nodeid.split("::")
        name = parts[-1]
        classname = _module_dotted(file)
        if len(parts) > 1:
            classname = f"{classname}." + ".".join(parts[:-1])
        ET.SubElement(
            suite,
            "testcase",
            {"file": file, "classname": classname, "name": name, "time": "1.0"},
        )
    ET.ElementTree(suite).write(path)


def _run_guard(manifest: Path, junit: Path) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as log:
        log.write("")  # the log is only the human-readable view in this mode
        log_path = log.name
    return subprocess.run(
        [sys.executable, str(SKIP_GUARD), log_path,
         f"--junitxml={junit}", f"--manifest={manifest}", "--manifest-only"],
        capture_output=True, text=True, cwd=ROOT,
    )


# ── the manifests themselves ──────────────────────────────────────────────

@pytest.mark.parametrize("path", [EMBEDDED, PLATFORM_GATED], ids=["embedded", "platform-gated"])
def test_manifest_is_present_non_empty_and_well_formed(path: Path) -> None:
    assert path.exists(), f"{path.relative_to(ROOT)} is missing — the frozen set is the whole point"
    nodeids = _nodeids(path)
    assert nodeids, f"{path.name} has no nodeids (an empty expectation passes vacuously)"
    for nodeid in nodeids:
        assert "::" in nodeid, (
            f"{path.name}: {nodeid!r} is not a pytest nodeid — skip-guard fails closed on a "
            "malformed line, so this would red CI rather than test anything"
        )
        file = nodeid.split("::")[0]
        assert (ROOT / file).is_file(), (
            f"{path.name} lists {nodeid} but {file} does not exist — a renamed/moved file must "
            "be reflected here deliberately (regenerate, do not hand-edit)"
        )
    assert len(nodeids) == len(set(nodeids)), f"{path.name} contains duplicate nodeids"


def test_manifests_carry_provenance() -> None:
    """The value came from a real CI run; the header must say which, so the next
    reader can re-derive it instead of trusting it (#4290's stale-finding class)."""
    for path in (EMBEDDED, PLATFORM_GATED):
        header = "\n".join(
            line for line in path.read_text().splitlines() if line.startswith("#")
        )
        assert "PROVENANCE" in header, f"{path.name} has no provenance header"
        assert "run" in header.lower() and "CI" in header, (
            f"{path.name}'s header does not name the CI run it was measured from"
        )


def test_platform_gated_manifest_covers_the_registry() -> None:
    """The runtime half must cover the source scan's files.

    `PLATFORM_GATED_TESTS` is where a platform gate is DECLARED; this manifest is
    where it is OBSERVED. If a new gated file is registered there without a
    runtime expectation, the registry would look protected while nothing checked
    at runtime that its tests still exist.
    """
    from tests.test_markers import PLATFORM_GATED_TESTS

    listed = {Path(nid.split("::")[0]).name for nid in _nodeids(PLATFORM_GATED)}
    missing = sorted(set(PLATFORM_GATED_TESTS) - listed)
    assert not missing, (
        f"{missing} are registered as platform-gated but have no nodeid in "
        "config/ci-expected-nodeids/platform-gated.txt — the file's tests would "
        "stop being collected with nothing to notice (#4215)"
    )


# ── the CI wiring ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "manifest", [EMBEDDED, PLATFORM_GATED], ids=["embedded", "platform-gated"]
)
def test_workflow_invokes_the_manifest_with_manifest_only(manifest: Path) -> None:
    """A frozen set nobody checks is a comment.

    Pins the invocation AND the mode: `--manifest-only` is what keeps a URI-less
    lane (whose skips are expected) from reding on the skip-anomaly matchers
    calibrated for the docker lane — measured at 24 collection violations in the
    d14 job before the flag existed.
    """
    text = WORKFLOW.read_text()
    rel = str(manifest.relative_to(ROOT))
    assert rel in text, f"{rel} is not referenced by {WORKFLOW.name} — nothing consumes it"
    invocations = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        if rel not in line:
            continue
        # The command may continue on the next line(s) (YAML line continuations).
        window = "\n".join(text.splitlines()[lineno - 1: lineno + 2])
        assert "--manifest-only" in window, (
            f"{WORKFLOW.name}:{lineno} consumes {rel} without --manifest-only — in a URI-less "
            "lane that false-reds on the EXPECTED skips (the docker-calibrated matchers)"
        )
        invocations += 1
    assert invocations >= 1


# ── the check bites (non-vacuity) ─────────────────────────────────────────

def test_the_frozen_set_passes_against_a_junit_that_contains_it() -> None:
    """Control: the manifest is satisfiable. Without this, a manifest whose
    nodeids are spelled wrong would look like a working check that always reds —
    or, worse, a later 'fix' could weaken the comparison to make it green."""
    nodeids = _nodeids(EMBEDDED)
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        _junit_for(nodeids, junit)
        result = _run_guard(EMBEDDED, junit)
    assert result.returncode == 0, (
        f"the frozen set does not match its own junit — the nodeid spelling diverges.\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_a_single_vanished_nodeid_fails_and_is_named() -> None:
    """The #4207 bite, exactly: one test subtracted from a 69-test run.

    The step's `passed >= 30` floor cannot see this (35 would remain), and the
    step's text pin cannot see a flag passed through a shell variable. The frozen
    set can, because the vanished test is absent from the run's own report.
    """
    nodeids = _nodeids(EMBEDDED)
    assert len(nodeids) > 10, "the bite test needs a manifest that is not trivially small"
    victim = next(nid for nid in nodeids if nid.endswith("::test_env_override_is_honoured_and_loud"))
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        _junit_for([nid for nid in nodeids if nid != victim], junit)
        result = _run_guard(EMBEDDED, junit)
    assert result.returncode != 0, "a vanished nodeid did NOT fail the check"
    assert victim in result.stdout, (
        f"the check failed but did not name the vanished nodeid {victim!r}; it must name it "
        f"so the reader does not have to diff junits.\n{result.stdout}"
    )
