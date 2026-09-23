"""Mutation harness for ``tools/collision_preflight.py`` (#4368 / #4375).

A gate has to fail in BOTH directions. A test suite that only ever runs against
the fixed detector cannot tell "the gate is right" from "the assertion is a
tautology" — a test that passes on the fixed detector *and* on a mutant proves
nothing about the mutant direction it is supposed to pin. This harness runs the
REAL ``tests/test_collision_preflight.py`` against deliberately broken detectors
and asserts the expected node IDs FAIL, then asserts those same nodes PASS
against the unmutated tool (the fail-then-pass proof).

Four mutant directions
----------------------
``M1`` — the over-blocking defect, verbatim: ``git show
origin/main:tools/collision_preflight.py``. Every *relaxation* class (the
claim-arm prose noun, the terse work-claim phrases, the merged-PR keyword
advisory, the issue's own PR/invoking checkout) must fail under it.

``M2`` — fail-open verdict: the fixed detector with the entire blocking
decision removed in ``format_report`` (every hit tier AND ``INCOMPLETE`` is
neutered). Every *must-still-block* class must fail under it.

``M3`` — fail-open claim pattern: the fixed detector with
``_CLAIM_WORK_OBJECT_RE`` over-narrowed to ``(?:it)``. The claim-arm
"still matches" class pins the FAIL-OPEN direction (M3) rather than M1: M1's
pattern is a strict SUPERSET of the fixed one, so a positive-match assertion
cannot fail against it.

``M4`` — fail-open terse arm: the fixed detector with the ``_TERSE_CLAIM_RE``
branch dropped from ``scan_issue_surface``. The terse fragments must still be
REPORTED (as weak); silently dropping them loses the advisory signal.

Mechanics (hermetic: no network, no Docker, no FalkorDB)
--------------------------------------------------------
Each mutant is written to ``<tmp>/tools/collision_preflight.py`` with the real
test file exposed at ``<tmp>/tests/test_collision_preflight.py``. The temp tree
is OUTSIDE the repo, so pytest never loads ``tests/conftest.py`` (which would
demand ``TORTOISE_DB_URI``); a minimal ``pytest.ini`` in the tree is passed with
``-c`` so no repo pytest config leaks in. The tool and the test file are both
stdlib-only, so the copied test imports cleanly. Only the selected node IDs are
run — never the whole file.

The mutation is asserted, never assumed: the exact anchor must occur exactly
once, the text must actually change, the marker must be present, the mutant must
compile, and it must run. If an anchor no longer applies the harness FAILS with
that message — it never silently skips.

Run::

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_collision_preflight_mutation.py -q
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "collision_preflight.py"
REAL_TEST = ROOT / "tests" / "test_collision_preflight.py"
CLASS_NAME = "CollisionPreflightTest"
RUN_TIMEOUT_S = 1800

UV = shutil.which("uv")

# ── mutant definitions ─────────────────────────────────────────────────────

# M2: remove the blocking decision from `format_report`. The five assignment
# lines below are the ONLY place the verdict is decided (strong / keyword /
# INCOMPLETE / weak), so neutering them makes the gate fail open.
M2_ANCHOR = (
    '    hits = [h for s in ordered for h in s.hits]\n'
    '    incomplete = [s for s in ordered if s.status == STATUS_INCOMPLETE or s.truncated]\n'
    '    strong = [h for h in hits if h.strength == "strong"]\n'
    '    keyword_hits = [h for h in hits if h.strength == "keyword"]\n'
    '    weak = [h for h in hits if h.strength == "weak"]\n'
)
M2_MARKER = "M2-MUTANT(#4368/#4375)"
M2_MUTANT = (
    '    hits = [h for s in ordered for h in s.hits]\n'
    f'    # {M2_MARKER}: fail-open — the blocking decision is removed entirely.\n'
    '    incomplete = []\n'
    '    strong = []\n'
    '    keyword_hits = []\n'
    '    weak = [h for h in hits]\n'
)

# M3: over-narrow the claim work object (fail-open for the claim surface).
M3_ANCHOR = (
    '_CLAIM_WORK_OBJECT_RE = (\n'
    '    r"(?:"\n'
    '    r"this(?=(?:\\s+(?:" + _CLAIM_DEICTIC_NOUNS + r")s?\\b)|(?:\\s*[^\\w\\s])|$)|"\n'
    '    r"it|#\\d+|"\n'
    '    r"the\\s+(?:" + _CLAIM_WORK_NOUNS + r")s?|"\n'
    '    r"ownership|responsibility"\n'
    '    r")"\n'
    ')\n'
)
M3_MARKER = "M3-MUTANT(#4368)"
M3_MUTANT = (
    f'_CLAIM_WORK_OBJECT_RE = (  # {M3_MARKER}: over-narrowed claim object.\n'
    '    r"(?:it)"\n'
    ')\n'
)

# M4: drop the terse weak arm from `scan_issue_surface` (fail-open: the terse
# fragments stop being reported at all, though they are non-blocking anyway).
M4_ANCHOR = (
    '        elif _TERSE_CLAIM_RE.search(body) or _TERSE_CLAIM_RE.search(stripped):\n'
    '            detail = ("work-claim phrase (ordinary English — advisory, "\n'
    '                      "non-blocking): " + _one_line(body, 90))\n'
    '            strength = "weak"\n'
)
M4_MARKER = "M4-MUTANT(#4368/F3)"
M4_MUTANT = (
    f'        elif False:  # {M4_MARKER}: the terse weak arm is dropped.\n'
    '            pass\n'
)

# ── declared threat-surface coverage ───────────────────────────────────────
M1 = "M1_over_blocking(origin/main)"
M2 = "M2_fail_open(verdict)"
M3 = "M3_fail_open(claim-pattern)"
M4 = "M4_fail_open(terse-weak-arm)"


@dataclass(frozen=True)
class Coverage:
    name: str
    direction: str
    node: tuple[str, ...]


# Each declared class is pinned by >=1 node that must FAIL under its direction.
COVERAGE: tuple[Coverage, ...] = (
    # -- relaxation classes (the defect): must fail under over-blocking M1 ----
    Coverage(
        "relaxation/claim arm: the prose NOUN no longer arms the gate",
        M1,
        ("test_4368_prose_noun_does_not_arm_the_gate",),
    ),
    Coverage(
        "relaxation/F3: the whole TERSE arm is weak (advisory, non-blocking)",
        M1,
        ("test_terse_work_claim_phrases_are_weak_not_blocking",),
    ),
    Coverage(
        "relaxation/merged-PR keyword-only hit is advisory (non-blocking)",
        M1,
        ("test_4375_closed_pr_keyword_only_hit_is_advisory",),
    ),
    Coverage(
        "relaxation/the issue's OWN PR (number==issue) is weak",
        M1,
        ("test_closed_pr_own_number_is_weak_not_blocking",),
    ),
    Coverage(
        "relaxation/the INVOKING checkout's branch/worktree are weak",
        M1,
        ("test_4375_invoking_checkout_branch_is_weak_but_another_lane_blocks",),
    ),
    # -- must-still-block classes: must fail under fail-open M2 --------------
    Coverage(
        "block/non-self OPEN PR keyword hit still blocks",
        M2,
        ("test_4375_open_pr_keyword_hit_still_blocks",),
    ),
    Coverage(
        "block/an UNMERGED closed-PR keyword hit still blocks",
        M2,
        ("test_4375_unmerged_closed_pr_keyword_hit_still_blocks",),
    ),
    Coverage(
        "block/a different lane's number-carrying ref still blocks",
        M2,
        ("test_4375_invoking_checkout_branch_is_weak_but_another_lane_blocks",),
    ),
    Coverage(
        "block/closed-PR closing reference (`Closes #N`) still blocks",
        M2,
        ("test_closed_pr_closing_reference_is_still_a_hit",),
    ),
    Coverage(
        "block/claim-verb arm + assignee surface still block",
        M2,
        ("test_4368_claim_verb_arm_still_blocks_with_a_remedy",
         "test_issue_assignee_hit"),
    ),
    Coverage(
        "block/INCOMPLETE (exit 2) outranks advisory weak hits",
        M2,
        ("test_4375_advisory_hits_do_not_mask_a_hard_incomplete",),
    ),
    # -- must-still-MATCH classes: must fail under over-narrowing M3 ---------
    Coverage(
        "match/claim-arm work objects still match",
        M3,
        ("test_classification_is_claim_re_without_tiers",
         "test_4368_claim_verb_arm_still_blocks_with_a_remedy"),
    ),
    # -- must-still-REPORTED class: must fail under M4 -----------------------
    Coverage(
        "report/the terse fragments are still reported (weak)",
        M4,
        ("test_terse_work_claim_phrases_are_weak_not_blocking",),
    ),
)


def _all_declared_nodes() -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for cov in COVERAGE:
        for node in cov.node:
            seen.setdefault(node, None)
    return tuple(seen)


# ── mutant construction (asserted, never assumed) ──────────────────────────


def _apply_mutation(text: str, anchor: str, mutant: str, marker: str,
                    label: str) -> str:
    """Replace ``anchor`` with ``mutant``, asserting the mutation applied.

    A moved/reformatted anchor is a HARNESS FAILURE (the mutation no longer
    applies), never a silent skip — a skipped mutation would report coverage
    that does not exist.
    """
    count = text.count(anchor)
    if count != 1:
        raise AssertionError(
            f"{label}: mutation anchor no longer applies — found {count} "
            f"occurrence(s), expected exactly 1. The code moved or was "
            f"reformatted; update the anchor. Anchor head: "
            f"{anchor.splitlines()[0]!r}"
        )
    out = text.replace(anchor, mutant, 1)
    if out == text:
        raise AssertionError(f"{label}: mutation produced identical text")
    if marker not in out:
        raise AssertionError(f"{label}: mutation marker {marker!r} missing")
    return out


def _build_m1_mutant() -> str:
    """The real over-blocking detector: ``origin/main``'s tool, verbatim."""
    proc = subprocess.run(
        ["git", "show", "origin/main:tools/collision_preflight.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "M1 mutant unavailable: `git show "
            "origin/main:tools/collision_preflight.py` failed (rc="
            f"{proc.returncode}): {proc.stderr.strip()}"
        )
    text = proc.stdout
    if not text.strip():
        raise AssertionError("M1 mutant unavailable: origin/main copy is empty")
    if text == TOOL.read_text():
        raise AssertionError(
            "M1 mutant is byte-identical to the fixed detector — origin/main "
            "already carries the fix, so M1 pins nothing"
        )
    return text


def _make_tree(parent: Path, tool_text: str) -> Path:
    tree = parent / "tree"
    (tree / "tools").mkdir(parents=True)
    (tree / "tests").mkdir(parents=True)
    (tree / "tools" / "collision_preflight.py").write_text(tool_text)
    # The REAL test file, verbatim: the mutant must be observed by the same
    # assertions CI runs, and the constraint is that the harness does not edit
    # either artifact.
    (tree / "tests" / "test_collision_preflight.py").write_text(
        REAL_TEST.read_text())
    (tree / "pytest.ini").write_text("[pytest]\n")
    return tree


def _assert_mutant_runs(tree: Path, label: str) -> None:
    tool = tree / "tools" / "collision_preflight.py"
    compiled = subprocess.run(
        [sys.executable, "-m", "py_compile", str(tool)],
        capture_output=True, text=True,
    )
    if compiled.returncode != 0:
        raise AssertionError(
            f"{label}: mutant does not compile: {compiled.stderr.strip()}")
    ran = subprocess.run(
        [sys.executable, str(tool), "--help"],
        capture_output=True, text=True, timeout=120,
    )
    if ran.returncode != 0:
        raise AssertionError(
            f"{label}: mutant does not run (--help rc={ran.returncode}): "
            f"{ran.stderr.strip()[:400]}")


def _pytest_cmd(tree: Path, names) -> list[str]:
    test_file = tree / "tests" / "test_collision_preflight.py"
    nodes = [f"{test_file}::{CLASS_NAME}::{n}" for n in names]
    launcher = [UV, "run", "pytest"] if UV else [sys.executable, "-m", "pytest"]
    return launcher + nodes + [
        "-q", "--no-header",
        "-c", str(tree / "pytest.ini"),
        "-p", "no:cacheprovider",
        f"--junit-xml={tree / 'junit.xml'}",
    ]


def _parse_junit(tree: Path) -> dict[str, bool]:
    """{test method name: failed?} — junit collapses subTest failures into
    their parent ``testcase``, which is exactly the granularity we assert."""
    xml = tree / "junit.xml"
    results: dict[str, bool] = {}
    if not xml.exists():
        return results
    for tc in ET.parse(xml).getroot().iter("testcase"):
        name = tc.get("name") or ""
        failed = any(child.tag in ("failure", "error") for child in tc)
        results[name] = results.get(name, False) or failed
    return results


@dataclass
class _Run:
    direction: str
    tree: Path
    proc: subprocess.CompletedProcess
    results: dict[str, bool]

    @property
    def failed_nodes(self) -> set[str]:
        return {n for n, bad in self.results.items() if bad}

    @property
    def passed_nodes(self) -> set[str]:
        return {n for n, bad in self.results.items() if not bad}


class CollisionPreflightMutationTest(unittest.TestCase):
    """Runs once in ``setUpClass``; each test asserts one obligation."""

    _tmpdir: tempfile.TemporaryDirectory
    _runs: dict[str, _Run]
    _fixed: _Run
    _m1_source: str

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory(
            prefix="collision-preflight-mutation-")
        root = Path(cls._tmpdir.name)
        fixed_text = TOOL.read_text()

        # 1. Build every direction's tool text, asserted.
        cls._m1_source = _build_m1_mutant()
        m2_text = _apply_mutation(
            fixed_text, M2_ANCHOR, M2_MUTANT, M2_MARKER, "M2")
        m3_text = _apply_mutation(
            fixed_text, M3_ANCHOR, M3_MUTANT, M3_MARKER, "M3")
        m4_text = _apply_mutation(
            fixed_text, M4_ANCHOR, M4_MUTANT, M4_MARKER, "M4")

        # 2. Materialize the trees. The FIXED tree must be byte-identical to
        #    the real tool, so the only variable between runs is the tool text.
        fixed_tree = _make_tree(root / "fixed", fixed_text)
        if (fixed_tree / "tools" / "collision_preflight.py").read_bytes() != \
                TOOL.read_bytes():
            raise AssertionError("FIXED tree tool is not byte-identical to the repo tool")
        trees = {
            M1: _make_tree(root / "m1", cls._m1_source),
            M2: _make_tree(root / "m2", m2_text),
            M3: _make_tree(root / "m3", m3_text),
            M4: _make_tree(root / "m4", m4_text),
        }

        # 3. Every mutant must actually run.
        for direction, tree in trees.items():
            _assert_mutant_runs(tree, direction)

        # 4. Run only the selected node IDs, one pytest per direction, plus one
        #    fixed run over the union (the fail-then-pass proof).
        cls._runs = {}
        for direction, tree in trees.items():
            names = [n for cov in COVERAGE if cov.direction == direction
                     for n in cov.node]
            cls._runs[direction] = cls._run(tree, direction, names)

        cls._fixed = cls._run(fixed_tree, "FIXED", _all_declared_nodes())

        # 5. Emit the observed table (visible with -s / on failure).
        print("\n" + cls._coverage_table())

    @classmethod
    def _run(cls, tree: Path, direction: str, names) -> _Run:
        cmd = _pytest_cmd(tree, names)
        proc = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True,
            timeout=RUN_TIMEOUT_S,
        )
        return _Run(direction, tree, proc, _parse_junit(tree))

    @classmethod
    def _coverage_table(cls) -> str:
        rows = ["", "class  |  direction  |  node  |  mutant  |  fixed"]
        for cov in COVERAGE:
            run = cls._runs[cov.direction]
            for node in cov.node:
                m = run.results.get(node)
                f = cls._fixed.results.get(node)
                fmt = lambda v: ("FAIL" if v else "pass") if v is not None else "MISSING"  # noqa: E731
                rows.append(f"{cov.name[:60]:60} | {cov.direction:26} | "
                            f"{node:62} | {fmt(m):4} | {fmt(f):4}")
        return "\n".join(rows)

    # ── obligations ────────────────────────────────────────────────────────

    def test_harness_guards_fire_on_synthetic_failures(self) -> None:
        """Meta: the harness's own guards must actually fail, or the coverage
        report is a tautology. Two synthetic failures are fed in: an unpinned
        coverage class (the vacuous-pass guard) and a moved mutation anchor."""

        class _SyntheticRun:
            def __init__(self, results: dict[str, bool]) -> None:
                self.results = results

        fake = Coverage(
            "SYNTHETIC unpinned class", M1, ("test_never_fails_under_m1",))
        module = sys.modules[__name__]
        saved_coverage = module.COVERAGE
        saved_runs = type(self)._runs
        try:
            module.COVERAGE = (*saved_coverage, fake)
            synthetic = _SyntheticRun({"test_never_fails_under_m1": False})
            type(self)._runs = {d: synthetic for d in (M1, M2, M3, M4)}
            with self.assertRaises(AssertionError) as ctx:
                self.test_every_declared_class_is_pinned_by_a_failing_node()
            self.assertIn("SYNTHETIC unpinned class", str(ctx.exception))
            self.assertIn("VACUOUS PASS", str(ctx.exception))
        finally:
            module.COVERAGE = saved_coverage
            type(self)._runs = saved_runs

        # An anchor that no longer applies is a harness failure, never a silent
        # skip — a skipped mutation would report coverage that does not exist.
        with self.assertRaises(AssertionError) as ctx:
            _apply_mutation(
                TOOL.read_text(),
                "# THIS ANCHOR HAS MOVED AND NO LONGER EXISTS\n",
                "x", "marker", "SYNTHETIC-mut",
            )
        self.assertIn("SYNTHETIC-mut", str(ctx.exception))
        self.assertIn("no longer applies", str(ctx.exception))

    def test_declared_node_ids_exist_in_the_real_test_file(self) -> None:
        # The node list is not trusted blindly: every declared node ID must be
        # a real test method of the real test file.
        source = ast.parse(REAL_TEST.read_text())
        real: set[str] = set()
        for node in ast.walk(source):
            if isinstance(node, ast.ClassDef) and node.name == CLASS_NAME:
                for member in node.body:
                    if isinstance(member, ast.FunctionDef):
                        real.add(member.name)
        missing = [n for n in _all_declared_nodes() if n not in real]
        self.assertFalse(
            missing,
            f"declared node ID(s) do not exist in {REAL_TEST.name}: {missing}",
        )

    def test_mutants_are_applied_and_differ_from_the_fixed_detector(self) -> None:
        self.assertNotEqual(self._m1_source, TOOL.read_text(),
                            "M1 mutant equals the fixed detector")
        self.assertIn("VERDICT", self._m1_source)
        for direction, marker in ((M2, M2_MARKER), (M3, M3_MARKER),
                                  (M4, M4_MARKER)):
            text = (self._runs[direction].tree / "tools" /
                    "collision_preflight.py").read_text()
            self.assertIn(marker, text,
                          f"{direction}: marker absent — mutation not applied")
            self.assertNotEqual(text, TOOL.read_text(),
                                f"{direction}: mutant equals the fixed detector")

    def test_each_mutant_run_fails_and_the_fixed_run_passes(self) -> None:
        problems: list[str] = []
        for direction, run in self._runs.items():
            if run.proc.returncode == 0:
                problems.append(
                    f"{direction}: the mutant run PASSED (rc=0) — the gate was "
                    f"not observably broken by this mutation"
                )
            if not (run.failed_nodes or run.passed_nodes):
                problems.append(
                    f"{direction}: no test was collected — this is a "
                    f"collection/environment failure, not a proven mutation. "
                    f"stderr: {run.proc.stderr.strip()[:300]}"
                )
        if self._fixed.proc.returncode != 0:
            problems.append(
                "FIXED: the unmutated detector run FAILED (rc="
                f"{self._fixed.proc.returncode}) — the fail-then-pass baseline "
                f"is broken. stdout/stderr:\n"
                f"{self._fixed.proc.stdout[-1500:]}\n{self._fixed.proc.stderr[-800:]}"
            )
        self.assertFalse(problems, "\n".join(["", *problems]))

    def test_every_declared_class_is_pinned_by_a_failing_node(self) -> None:
        """THE VACUOUS-PASS GUARD.

        For every declared class, at least one of its node IDs must have a
        recorded failure under the class's direction. If a class's nodes all
        pass on the mutant, the assertion is a tautology for that direction and
        this harness FAILS, naming the class.
        """
        problems: list[str] = []
        for cov in COVERAGE:
            run = self._runs[cov.direction]
            failing = [n for n in cov.node if run.results.get(n) is True]
            if failing:
                continue
            not_collected = [n for n in cov.node if n not in run.results]
            observed = {n: run.results.get(n) for n in cov.node}
            problems.append(
                f"VACUOUS PASS: class {cov.name!r} (direction "
                f"{cov.direction}) has NO test that fails against the mutant "
                f"detector. observed={observed}"
                + (f"; not collected={not_collected}" if not_collected else "")
            )
        self.assertFalse(problems, "\n".join(["", *problems]))

    def test_declared_nodes_fail_under_a_mutant_and_pass_on_the_fixed_detector(
            self) -> None:
        """Fail-then-pass proof: every declared node must FAIL under at least
        one mutant and PASS on the unmutated tool. A node that passes on both
        proves nothing."""
        vacuous = [
            node for node in _all_declared_nodes()
            if not any(run.results.get(node) is True for run in self._runs.values())
        ]
        self.assertFalse(
            vacuous,
            "declared node(s) pass on the fixed detector AND every mutant "
            f"(vacuous coverage — not pinned by any direction): {vacuous}",
        )
        problems: list[str] = []
        for node in _all_declared_nodes():
            observed = self._fixed.results.get(node)
            if observed is None:
                problems.append(f"{node}: NOT COLLECTED by the fixed run")
            elif observed is True:
                problems.append(f"{node}: FAILED on the fixed detector")
        self.assertFalse(
            problems,
            "fail-then-pass proof broken — these nodes do not pass on the "
            "unmutated tool:\n" + "\n".join(problems),
        )


if __name__ == "__main__":
    # Standalone: the tree/isolation logic is identical under pytest.
    os.environ.setdefault("TORTOISE_TEST_CARVE_OUT", "1")
    unittest.main()
