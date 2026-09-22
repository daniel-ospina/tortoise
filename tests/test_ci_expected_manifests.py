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
2. the platform-gated manifest still covers every file `tests/test_markers.py`
   registers, so the source scan and the runtime check cannot drift apart;
3. a frozen test that is collected and then SKIPPED reds, unless its file's
   declared `# allow-skipped:` budget covers it - the OUTCOME half of #4215,
   which is the shape a test-level `if ...: pytest.skip(...)` produces;

and it BITES: a junit missing one expected nodeid must fail, a junit whose only
skip exceeds the declared budget must fail and name it, and a junit containing all
of them must pass. A pin that cannot fail is not a pin.

An earlier revision of this file ALSO asserted that the workflow invokes the guard
with `--manifest-only`, by reading the step's shell as text. That half was removed
in PR #4351's scope reduction: thirteen review cycles each produced a spelling in
which the text reading and shell execution disagreed (the script path inside a
quoted `echo`, a command substitution in argument position, the manifest as the
guard's log positional, a duplicate `--manifest` in either spelling, a reassignment
through `export`/`env`, a flag that is an argument of a redirect) - a text scanner
cannot be completed by more text rules. That enforcement belongs to execution:
#4494 (run the step's real shell under `bash -e` with a recording interpreter stub
on PATH; assert the recorded argv and the step's exit status - the pattern
`tests/test_pages_bindings.py` already uses) and #4463 (prove the CI job reds when a
frozen nodeid is mutated). Until those land, nothing here asserts the workflow
wiring; the runtime check the workflow performs is unaffected.
"""


from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "config" / "ci-expected-nodeids"
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


def _strip_comments(text: str) -> str:
    """Drop `#` comments, quote-aware, one line at a time.

    Classification by token presence has to read CODE, not prose: a comment saying
    "RUN_FOO_E2E is unrelated" reclassified a docker-lane marker module as opt-in
    e2e, which red a correct tree and pushed the header toward a FALSE split
    (cycle-5 finding — the same "classification by string" hazard cycle 3 fixed for
    docstrings, one text class over).
    """
    out = []
    for line in text.splitlines():
        quote: str | None = None
        i = 0
        while i < len(line):
            ch = line[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == "#":
                break
            i += 1
        out.append(line[:i])
    return "\n".join(out)


# Words that re-dispatch to another command (so the interesting word comes later) or
# that introduce a command without being one. A CLOSED vocabulary, deliberately not a
# denylist of printers: an unknown head word is simply not an invocation, which is the
# safe direction for a pin whose job is to prove ENFORCEMENT.
_CMD_PREFIXES = frozenset({"env", "sudo", "command", "exec", "nohup", "time", "xargs"})
_SHELL_KEYWORDS = frozenset({"if", "elif", "else", "then", "do", "while", "until", "!"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})


def _module_dotted(file: str) -> str:
    """`tests/test_x.py` -> `tests.test_x` (the junit `classname` prefix)."""
    assert file.endswith(".py"), file
    return file[: -len(".py")].replace("/", ".")


def _junit_for(
    nodeids: list[str], path: Path, skipped: Iterable[str] = (), xfailed: Iterable[str] = ()
) -> None:
    """Write a junit (xunit1 shape) that contains exactly `nodeids`.

    pytest's own junit is the only writer in CI, so the attributes that
    `skip-guard._read_junitxml` reads are reproduced exactly: `file`, `classname`,
    `name`. A collected test gets the module-dotted path (plus any class) as
    `classname` and the bare name in `name`; a module-level collection-abort marker
    has NO class, so pytest writes `classname=""` and the dotted module as the NAME.
    Both shapes are reproduced here — writing only the first made 32 of the 69
    entries a shape pytest never emits, so the marker reconstruction the guard
    depends on went untested (cycle-4 finding).

    `skipped` emits a `<skipped>` child on those testcases — the OUTCOME half of
    #4215: pytest writes the reason in `message` (and the repr in the element text),
    but the check reads whether the child is there and its `type` (an XFAIL is
    `type="pytest.xfail"`, and it must NOT count as a hidden test).
    """
    skipped = set(skipped)
    xfailed = set(xfailed)
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
        case = ET.SubElement(
            suite,
            "testcase",
            {"file": file, "classname": classname, "name": name, "time": "1.0"},
        )
        if nodeid in skipped or nodeid in xfailed:
            ET.SubElement(
                case,
                "skipped",
                {
                    "message": "guarded by the platform gate",
                    "type": "pytest.xfail" if nodeid in xfailed else "pytest.skip",
                },
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


def _load_guard():
    """`tools/skip-guard.py` is not importable by name (the hyphen), so load it by
    path: a test that re-implemented the manifest's directive parsing could drift
    from the guard's and certify a budget the guard does not honour."""
    spec = importlib.util.spec_from_file_location("_skip_guard_under_test", SKIP_GUARD)
    assert spec is not None and spec.loader is not None, f"cannot load {SKIP_GUARD}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skip_budget(path: Path) -> dict[str, int]:
    """The manifest's own `# allow-skipped:` budget, parsed by the GUARD's parser."""
    allowances, invalid = _load_guard()._parse_skip_allowances(path.read_text())
    assert not invalid, f"{path.name}: unreadable allow-skipped directive(s) {invalid}"
    return allowances


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
    # Pin the FACTS with tolerant wording, not one spelling of them: requiring the exact
    # phrase red a fact-preserving reword ("still aborts during collection", "37 are
    # actual test nodeids") while the docstring claimed to pin the claim (cycle-6 finding).
    # But the anchor must be the ASSERTION, not any mention: a bare
    # `aborts?[^\n]*collection` was satisfied by the REGENERATE paragraph ("…for a module
    # that aborts at collection…"), so deleting the sentence that states the weaker claim
    # stayed green — the vacuity cycle 3 fixed, re-created by cycle 6's tolerance
    # (caught by a mutation check, not by review).
    assert re.search(r"(?:pins?|asserts?|means)\b[\s\S]{0,160}?\baborts?\b[\s\S]{0,160}?\bcollection", header, re.I), (
        "the header must state what a marker asserts (a weaker claim than a collected test)"
    )
    # The RECORDED count must be the actual one, and no other count may be stated.
    # Anchor the number to its CLAUSE: `(\d+)[^\n]*\bmarkers?\b` spanned the whole
    # line, so an honest rewrite ("Of the 69 entries, 32 are module-level … markers")
    # captured 69 and red a correct header (cycle-4 finding — the over-constrained
    # regex class cycle 2 fixed for provenance, one line over). Case-insensitive since
    # cycle 9: `32 Are module-level` is the same claim.
    recorded = [int(n) for n in re.findall(r"(\d+)\s+are\s+module-level", header, re.I)]
    assert recorded and all(n == len(markers) for n in recorded), (
        f"the header records {recorded} markers but the manifest has {len(markers)}"
    )
    # The headline TOTAL is the most prominent number in the file, and it was pinned by
    # nothing: three nodeids could be deleted and the total left at 69 (cycle-6 finding).
    total = [int(n) for n in re.findall(r"(\d+)\s+entries", header, re.I)]
    assert total and all(n == len(nodeids) for n in total), (
        f"the header says {total} entries but the manifest has {len(nodeids)} — a narrowed "
        "manifest must not be able to keep claiming its old size"
    )
    # The OTHER count was unchecked, and it is the one that can hide a silent
    # NARROWING: `--manifest-only` compares expected-minus-observed, so a manifest
    # whose real-test entries were deleted is still satisfied by its own junit. The
    # header's real-test count is the only written record of how many there were,
    # so it is pinned against the manifest (cycle-3 finding). `ID` is accepted as a
    # synonym for `nodeid`, and the match is case-insensitive: `37 are real test IDs`
    # is the same claim as `37 are real test nodeids` (cycle-9 finding).
    recorded_tests = [
        int(n) for n in re.findall(r"(\d+)\s+are\s+(?:\w+\s+)?test\s+(?:node\s*ids?|ids?)", header, re.I)
    ]
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
        "hosted E2E": (r"(\d+)\s+hosted E2E", "skip_unless_hosted_e2e"),
        "opt-in e2e": (r"(\d+)\s+opt-in", "RUN_"),
        "docker-lane": (r"(\d+)\s+docker-lane", "TORTOISE_DB_URI"),
    }
    stated: dict[str, int] = {}
    for label, (pattern, token) in families.items():
        assert token in header, (
            f"the header does not name the {label} gate family ({token!r}) — a marker whose "
            "mechanism is unnamed reads as spurious"
        )
        # Each family's count is read from ITS OWN clause. Resolving the family to the
        # first line containing its token and taking that line's first integer meant an
        # honest rewrite that merged the three bullets into one line red the tree with a
        # bogus split (all three tokens resolve to that line) — cycle-7 finding. The
        # match is case-insensitive, so `14 hosted e2e` / `8 Docker-lane modules` are the
        # same claim (cycle-9 finding).
        counts = [int(n) for n in re.findall(pattern, header, re.I)]
        assert counts, f"the header names {label} but records no marker count for it"
        assert all(n == counts[0] for n in counts), (
            f"the header states the {label} marker count more than once, with conflicting "
            f"values {counts} — the per-family split must be unambiguous"
        )
        stated[label] = counts[0]
    # …and the numbers must be TRUE, not merely consistent: three counts that add up
    # are satisfied by a wrong split (20/6/6 passed — cycle-4 finding), and the split
    # is the fat a maintainer acts on ("this marker is spurious, clean it up").
    # Classify every marker module by the gate it really has.
    actual: dict[str, int] = {}
    for marker in markers:
        # Comments are stripped too: a comment naming another gate's token must not
        # reclassify a module (cycle-5 finding).
        src = _strip_comments(_strip_docstrings((ROOT / marker.split("::")[0]).read_text()))
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
        # against the distinct pinned PATHS — full `Class::test_name` with any
        # parameter suffix removed — not against the raw nodeid count: a parametrized
        # test (one def, several nodeids) buffered the count, so a colliding def was
        # invisible again (`5 >= 5` passed while a fifth def existed — cycle-9
        # finding). Two classes that legitimately share a method name are distinct
        # paths, so they still count twice.
        n_defs = len(
            re.findall(r"^\s*(?:async )?def (test_\w+)", _strip_docstrings(path.read_text()), re.M)
        )
        n_pinned = len(
            {
                re.sub(r"\[[^\]]*\]$", "", nid.split("::", 1)[1])
                for nid in _nodeids(PLATFORM_GATED)
                if nid.startswith(f"{rel}::")
            }
        )
        assert n_pinned >= n_defs, (
            f"{rel} defines {n_defs} test functions but platform-gated.txt pins only "
            f"{n_pinned} distinct test paths for it — a def whose bare name collides with "
            "a pinned one would otherwise be invisible (#4215)"
        )
    missing = sorted(set(PLATFORM_GATED_TESTS) - listed)
    assert not missing, (
        f"{missing} are registered as platform-gated but have no nodeid in "
        "config/ci-expected-nodeids/platform-gated.txt — the file's tests would "
        "stop being collected with nothing to notice (#4215)"
    )


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

    The step's `passed >= 30` floor cannot see this (34 would remain), and the
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
    listed = [line for line in result.stdout.splitlines() if line.startswith("   - ")]
    assert listed == [f"   - {victim}"], (
        f"the check must LIST exactly the vanished nodeid {victim!r} as missing, so the "
        f"reader does not have to diff junits; it listed {listed}.\n{result.stdout}"
    )


def test_a_frozen_test_that_only_skips_fails_and_is_named() -> None:
    """#4215's acceptance shape: the gate spelled as a test-level `pytest.skip(…)`.

    `if os.name == "posix" and sys.platform != "darwin": pytest.skip(…)` keeps the
    nodeid COLLECTED, so the coverage comparison is satisfied while the test never
    ran — and every scan for the gate's spelling loses that race. The manifest's
    OUTCOME half closes it from the run's own report: a file's declared budget is the
    only skips it may absorb, and anything above that reds, naming the nodeids.
    """
    nodeids = _nodeids(EMBEDDED)
    markers = {n for n in nodeids if n.partition("::")[2] == _module_dotted(n.split("::")[0])}
    budget = _skip_budget(EMBEDDED)
    victims = [
        n for n in sorted(nodeids) if n not in markers and budget.get(n.split("::", 1)[0], 0) == 0
    ]
    assert victims, (
        "every real nodeid in the manifest belongs to a file with a skip budget — this "
        "test needs one with none, or it exercises the allowance instead of the check"
    )
    victim = victims[0]
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        _junit_for(nodeids, junit, skipped={victim})
        result = _run_guard(EMBEDDED, junit)
    assert result.returncode != 0, (
        f"a frozen test ({victim}) was COLLECTED and then SKIPPED and the check stayed "
        "green — the coverage half cannot see it, and the outcome half must (#4215).\n"
        f"{result.stdout}"
    )
    assert victim in result.stdout, (
        f"the check must NAME the skipped nodeid {victim!r}; it said:\n{result.stdout}"
    )


def test_an_xfail_is_not_an_outcome_violation() -> None:
    """An XFAIL ran and failed as expected — it is not a hidden test.

    pytest writes `@pytest.mark.xfail` as `<skipped type="pytest.xfail">`, so a check
    that reads only the child's presence reds a correct tree with a message ("the test
    did not RUN") that is false, and its documented remedy — raise the file's skip
    budget — would write an allowance for a test that executes (cycle-9 finding).
    """
    nodeids = _nodeids(EMBEDDED)
    markers = {n for n in nodeids if n.partition("::")[2] == _module_dotted(n.split("::")[0])}
    budget = _skip_budget(EMBEDDED)
    victims = [
        n for n in sorted(nodeids) if n not in markers and budget.get(n.split("::", 1)[0], 0) == 0
    ]
    assert victims, "this test needs a budget-less real nodeid"
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        _junit_for(nodeids, junit, xfailed={victims[0]})
        result = _run_guard(EMBEDDED, junit)
    assert result.returncode == 0, (
        f"an xfailed nodeid ({victims[0]}) was reported as a skipped test that never ran — "
        f"an XFAIL ran, and the skip budget is not its remedy.\n{result.stdout}"
    )


def test_the_declared_skip_budget_is_honoured() -> None:
    """The other direction: the manifest's written budget must not false-red.

    Off darwin the two #3845 fork tests skip by design, and #4215's acceptance
    requires the check to be able to express exactly that. The budget is READ from the
    manifest (never pinned here), so this test moves with the declaration instead of
    becoming a second copy of it.
    """
    for manifest in (EMBEDDED, PLATFORM_GATED):
        nodeids = _nodeids(manifest)
        budget = _skip_budget(manifest)
        assert budget, (
            f"{manifest.name} declares no `# allow-skipped:` budget — the outcome half "
            "would then red the darwin-gated tests that legitimately skip off darwin "
            "(#4215's acceptance), so the budget has to be written down"
        )
        skipped: set[str] = set()
        for file, count in budget.items():
            pinned = [n for n in nodeids if n.split("::", 1)[0] == file]
            assert len(pinned) >= count, (
                f"{manifest.name}: the budget for {file} is {count} but only {len(pinned)} "
                "of its nodeids are pinned — the allowance would be absorbed by tests "
                "this manifest does not expect"
            )
            # …and a budget covering EVERY frozen nodeid of a file makes the OUTCOME
            # half unable to fail for it: the guard would allow the whole file to skip
            # while the check still reported success (cycle-8 finding — `=4` of 4 was
            # accepted). No file needs that today; a file whose every test really is
            # platform-gated must change this pin deliberately, and say why in the
            # header, rather than neutralise the check silently.
            assert count < len(pinned), (
                f"{manifest.name}: the budget for {file} covers all {count} of its pinned "
                "nodeids — a full-file budget makes the OUTCOME half vacuous for that "
                "file (every frozen test may skip and the guard still passes)"
            )
            skipped.update(pinned[:count])
        with tempfile.TemporaryDirectory() as tmp:
            junit = Path(tmp) / "junit.xml"
            _junit_for(nodeids, junit, skipped=skipped)
            result = _run_guard(manifest, junit)
        assert result.returncode == 0, (
            f"{manifest.name}: the manifest's own declared skip budget false-reds. #4215 "
            f"requires the check to express 'this file legitimately loses {budget} tests "
            f"off darwin'.\n{result.stdout}\n{result.stderr}"
        )


def test_a_malformed_skip_budget_fails_closed() -> None:
    """A directive the guard cannot parse must not silently mean 'no budget'.

    The fail-closed direction of the same rule: an unreadable allowance would leave
    the strict reading in force while the reader believes a budget was declared, so
    the two disagreeing artifacts would both look fine. The colon is optional in the
    MATCH so a typo'd directive is REPORTED rather than skipped (cycle-8 finding).
    """
    nodeids = _nodeids(EMBEDDED)
    for directive in (
        "# allow-skipped: tests/test_fork_safety_3845.py = two",  # count is not a number
        "# allow-skipped tests/test_fork_safety_3845.py=2",  # no colon
        "# allow-skipped: tests/test_fork_safety_3845.py",  # no count
    ):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.txt"
            manifest.write_text(directive + "\n" + "\n".join(nodeids) + "\n")
            junit = Path(tmp) / "junit.xml"
            _junit_for(nodeids, junit)
            result = _run_guard(manifest, junit)
        assert result.returncode != 0 and "allow-skipped" in result.stderr, (
            f"the unreadable directive {directive!r} was ignored — an unknowable budget "
            f"must not default to 'allow' (fail-closed).\nrc={result.returncode}\n"
            f"{result.stderr}"
        )


def test_a_duplicate_skip_budget_fails_closed() -> None:
    """Two directives for one file must not silently last-win.

    Last-wins let a copy-pasted `=3` under a documented `=2` make the OUTCOME half
    allow the very skip the header calls a gate evasion — while the header, and every
    pin, still said 2 (cycle-8 finding). A second declaration for one file is never
    legitimate, so it is refused.
    """
    nodeids = _nodeids(EMBEDDED)
    with tempfile.TemporaryDirectory() as tmp:
        manifest = Path(tmp) / "manifest.txt"
        manifest.write_text(
            "# allow-skipped: tests/test_fork_safety_3845.py=2\n"
            "# allow-skipped: tests/test_fork_safety_3845.py=3\n"
            + "\n".join(nodeids)
            + "\n"
        )
        junit = Path(tmp) / "junit.xml"
        _junit_for(nodeids, junit)
        result = _run_guard(manifest, junit)
    assert result.returncode != 0 and "duplicate" in result.stderr, (
        "a duplicate `# allow-skipped:` directive was accepted (last wins) — the written "
        f"budget and the guard's behaviour would diverge with nothing red.\n"
        f"rc={result.returncode}\n{result.stderr}"
    )
