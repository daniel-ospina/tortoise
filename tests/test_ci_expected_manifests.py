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

import re
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


def _bare_test_name(nid_suffix: str) -> str:
    """`TestGroup::test_x[1]` -> `test_x`: the function name pytest would collect.

    Both sides of the registry comparison must be normalised, or an HONESTLY
    regenerated manifest can never satisfy it: pytest spells a class-scoped test
    `Class::name` and a parametrized one `name[param]`, while `def` gives the bare
    name — so the pin would red a correct tree, and the only way out would be to
    weaken it (the same "a source scan cannot close the class" disease this PR is
    about).
    """
    return nid_suffix.split("::")[-1].split("[")[0]


def _strip_docstrings(text: str) -> str:
    """Remove triple-quoted blocks, so a `def test_x` written inside a docstring or an
    example block is not read as a real test."""
    return re.sub(r'"""[\s\S]*?"""', "", re.sub(r"'''[\s\S]*?'''", "", text))


def _module_dotted(file: str) -> str:
    """`tests/test_x.py` -> `tests.test_x` (the junit `classname` prefix)."""
    assert file.endswith(".py"), file
    return file[: -len(".py")].replace("/", ".")


def _junit_for(nodeids: list[str], path: Path) -> None:
    """Write a junit (xunit1 shape) that contains exactly `nodeids`.

    pytest's own junit is the only writer in CI, so the attributes that
    `skip-guard._read_junitxml` reads are reproduced exactly: `file`, `classname`,
    `name`. A collected test gets the module-dotted path (plus any class) as
    `classname` and the bare name in `name`; a module-level collection-abort marker
    has NO class, so pytest writes `classname=""` and the dotted module as the NAME.
    Both shapes are reproduced here — writing only the first made 32 of the 69
    entries a shape pytest never emits, so the marker reconstruction the guard
    depends on went untested (cycle-4 finding).
    """
    suite = ET.Element("testsuite", {"name": "pytest", "tests": str(len(nodeids))})
    for nodeid in nodeids:
        file, *parts = nodeid.split("::")
        name = parts[-1]
        # `path::dotted.module` -- the marker spelling (`_module_dotted`).
        marker = len(parts) == 1 and name == _module_dotted(file)
        classname = "" if marker else _module_dotted(file)
        if marker:
            name = _module_dotted(file)
        elif len(parts) > 1:
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
        # Case-insensitively: the point is that provenance IS recorded, not that one
        # word is spelled in capitals — `Provenance` is an honest header (cycle-3
        # finding: this red a correct tree on the casing of a single word).
        assert "provenance" in header.lower(), f"{path.name} has no provenance header"
        # A run ID, not the bare words: the earlier check passed on a header saying
        # "NOT FROM A CI RUN", which is exactly the failure it exists to catch. Accept
        # the spellings a real header may use (`run:`, `run-id`, `job`, a `.../runs/<id>`
        # URL) — over-constraining this to one phrase would red an honest header.
        assert re.search(
            r"(?:run|runs|job)[\s:/#_=-]*(?:id[\s:=]*)?\d{6,}", header, re.I
        ) or re.search(r"/runs/\d{6,}", header), (
            f"{path.name}'s header names no CI run id — the next reader cannot re-derive the set"
        )
        assert "provenance" in header.lower() and "junit" in header.lower(), (
            f"{path.name}'s header does not name the artifact the set came from"
        )


def test_embedded_manifest_markers_are_documented_and_real() -> None:
    """The 69 entries are TWO kinds with two different claims.

    A `path::dotted.module` entry is pytest's synthetic module-level
    collection-abort marker, produced by `pytest.skip(..., allow_module_level=True)`
    at import time. Three gate families produce them in this lane: the hosted E2E
    suite (`tests/e2e/hosted/…`, gated by `skip_unless_hosted_e2e()`), the opt-in
    e2e suites (gated on a `RUN_*_E2E` variable), and docker-lane modules (gated on
    `TORTOISE_DB_URI`). It pins that the module still ABORTS AT COLLECTION — a
    strictly weaker claim than "its tests are collected", which is why the header
    has to say so. Pin the spelling (so a marker cannot be mistaken for a test),
    that the files exist, and that BOTH counts are recorded in the header — a
    marker silently "cleaned up" into a test nodeid would change what the set
    asserts, and a narrowed manifest must not be able to shrink its own claim.
    """
    nodeids = _nodeids(EMBEDDED)
    markers = [n for n in nodeids if n.partition("::")[2] == _module_dotted(n.split("::")[0])]
    tests = [n for n in nodeids if n not in markers]
    assert markers and tests, f"expected both kinds; markers={len(markers)} tests={len(tests)}"
    for marker in markers:
        assert (ROOT / marker.split("::")[0]).is_file(), marker
    header = "\n".join(line for line in EMBEDDED.read_text().splitlines() if line.startswith("#"))
    # Pin the CLAIM, not a substring of it: `"collection" in header.lower()` was
    # satisfied by the regenerate paragraph alone, so the sentence stating what a
    # marker asserts could be deleted (cycle-3 finding).
    assert "still aborts at collection" in header.lower(), (
        "the header must state what a marker asserts (a weaker claim than a collected test)"
    )
    # The RECORDED count must be the actual one, and no other count may be stated.
    # Anchor the number to its CLAUSE: `(\d+)[^\n]*\bmarkers?\b` spanned the whole
    # line, so an honest rewrite ("Of the 69 entries, 32 are module-level … markers")
    # captured 69 and red a correct header (cycle-4 finding — the over-constrained
    # regex class cycle 2 fixed for provenance, one line over).
    recorded = [int(n) for n in re.findall(r"(\d+)\s+are\s+module-level", header)]
    assert recorded and all(n == len(markers) for n in recorded), (
        f"the header records {recorded} markers but the manifest has {len(markers)}"
    )
    # The OTHER count was unchecked, and it is the one that can hide a silent
    # NARROWING: `--manifest-only` compares expected-minus-observed, so a manifest
    # whose real-test entries were deleted is still satisfied by its own junit. The
    # header's real-test count is the only written record of how many there were,
    # so it is pinned against the manifest (cycle-3 finding).
    recorded_tests = [int(n) for n in re.findall(r"(\d+)\s+are\s+real test", header)]
    assert recorded_tests and all(n == len(tests) for n in recorded_tests), (
        f"the header records {recorded_tests} real test nodeids but the manifest has {len(tests)}"
    )
    # A marker's mechanism is per-family, and naming only one of the three made the
    # header FALSE for 18 of the 32 markers (a maintainer checking
    # `test_capabilities_endpoint.py` against it read "this marker is spurious" —
    # exactly the cleanup the frozen set exists to prevent). Require all three to be
    # named, and require the stated per-family counts to add up to every marker, so
    # the numbers are load-bearing rather than decorative.
    families = {
        "hosted E2E": "skip_unless_hosted_e2e",
        "opt-in e2e": "RUN_",
        "docker-lane": "TORTOISE_DB_URI",
    }
    stated: dict[str, int] = {}
    for label, token in families.items():
        line = next((l for l in header.splitlines() if token in l), None)
        assert line is not None, (
            f"the header does not name the {label} gate family ({token!r}) — a marker whose "
            "mechanism is unnamed reads as spurious"
        )
        nums = [int(n) for n in re.findall(r"\b(\d+)\b", line)]
        assert nums, f"the header names {label} but records no marker count for it"
        stated[label] = nums[0]
    # …and the numbers must be TRUE, not merely consistent: three counts that add up
    # are satisfied by a wrong split (20/6/6 passed — cycle-4 finding), and the split
    # is the fat a maintainer acts on ("this marker is spurious, clean it up").
    # Classify every marker module by the gate it really has.
    actual: dict[str, int] = {}
    for marker in markers:
        src = _strip_docstrings((ROOT / marker.split("::")[0]).read_text())
        if "skip_unless_hosted_e2e" in src:
            fam = "hosted E2E"
        elif re.search(r"RUN_[A-Z_]*E2E", src):
            fam = "opt-in e2e"
        elif "TORTOISE_DB_URI" in src:
            fam = "docker-lane"
        else:
            raise AssertionError(
                f"{marker} aborts at collection via no gate this pin knows — a fourth "
                "gate family must be named in the header and classified here"
            )
        actual[fam] = actual.get(fam, 0) + 1
    assert actual == stated, (
        f"the header states {stated} but the markers actually split {actual} — the "
        "per-family split is what tells a maintainer whether a marker is spurious"
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
    # Basenames alone verified only that the FILE appears — not that its tests do.
    # Compare the nodeids against the file's own `def test_*` names, so a test added
    # to a gated file without regenerating the manifest is caught (a gate hiding only
    # the NEW test would otherwise slip through).
    for name in PLATFORM_GATED_TESTS:
        file = ROOT / "tests" / name if not name.startswith("tests/") else ROOT / name
        path = file if file.is_file() else next(ROOT.rglob(Path(name).name))
        rel = str(path.relative_to(ROOT))
        defs = set(
            re.findall(r"^\s*(?:async )?def (test_\w+)", _strip_docstrings(path.read_text()), re.M)
        )
        pinned = {
            _bare_test_name(nid.split("::", 1)[1])
            for nid in _nodeids(PLATFORM_GATED)
            if nid.startswith(f"{rel}::")
        }
        assert defs <= pinned, (
            f"{rel} defines {sorted(defs - pinned)} with no nodeid in platform-gated.txt — "
            "regenerate the manifest so a vanished test is noticed (#4215)"
        )
        # A bare-NAME subset is satisfied by a colliding new test: a new
        # `class TestZ: def test_x` where `test_x` is already pinned adds no name to
        # `defs` and no nodeid to `pinned`, so the new (collected) test could be
        # hidden by the gate with the pin still green (cycle-4 finding). Count DEFS
        # and NODEIDS, not distinct names — one def may pin several nodeids
        # (parametrization), so the manifest must list at least one per def.
        n_defs = len(
            re.findall(r"^\s*(?:async )?def (test_\w+)", _strip_docstrings(path.read_text()), re.M)
        )
        n_pinned = len([nid for nid in _nodeids(PLATFORM_GATED) if nid.startswith(f"{rel}::")])
        assert n_pinned >= n_defs, (
            f"{rel} defines {n_defs} test functions but platform-gated.txt has only "
            f"{n_pinned} nodeids for it — a def whose bare name collides with a pinned "
            "one would otherwise be invisible (#4215)"
        )
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
        if rel not in line or line.lstrip().startswith("#"):
            continue  # a comment mentioning the path is not a consumer
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
    markers = {n for n in nodeids if n.partition("::")[2] == _module_dotted(n.split("::")[0])}
    tests = sorted(n for n in nodeids if n not in markers)
    assert len(tests) > 3, f"the bite test needs real test nodeids; only {tests} present"
    # Pick the victim FROM THE MANIFEST, never by a literal test name: renaming or
    # removing a privileged test is an honest regeneration of this file, and doing
    # so used to make this pin raise StopIteration instead of asserting (cycle-3
    # finding — the same "cannot be satisfied honestly" defect one call deeper).
    victim = tests[len(tests) // 2]
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        _junit_for([nid for nid in nodeids if nid != victim], junit)
        result = _run_guard(EMBEDDED, junit)
    assert result.returncode != 0, "a vanished nodeid did NOT fail the check"
    # Pin the SHAPE of the report, not just the presence of the string: a guard that
    # dumps the whole expected set would satisfy `victim in stdout` while leaving the
    # reader to diff junits — the thing this message exists to prevent (cycle-4
    # finding). Exactly ONE nodeid may be listed as missing, and it must be the
    # victim; `   - <nodeid>` is the guard's missing-list bullet.
    listed = [l for l in result.stdout.splitlines() if l.startswith("   - ")]
    assert listed == [f"   - {victim}"], (
        f"the check must LIST exactly the vanished nodeid {victim!r} as missing, so the "
        f"reader does not have to diff junits; it listed {listed}.\n{result.stdout}"
    )
