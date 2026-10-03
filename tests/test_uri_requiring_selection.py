"""#6884: a module that collects NOTHING must not be handed to a test leg.

The defect this pins: the tier-2 PR legs run with an **EMPTY `TORTOISE_DB_URI`**
(present and empty, never unset) by design — both the URI and `EXPECT_URI` are
emptied, which is what keeps the E2E-6 tripwire **inert** on the tier-2 shape (it
arms only when `full==true`, where both are set; `python-ci.yml:600-602`
and `:1367-1368`; epic #1647 Task 9, cycle-6 P2-8), so "provision the URI" is a
reversal of a recorded decision, not a fix. A selected test module that
module-skips at import on an empty URI therefore collects ZERO tests in such a
leg: `pytest --collect-only` exits 5 and the fail-closed manifest step kills the
leg BEFORE any test runs. That is outside the test step, so the run carries no
`FAILED <nodeid>` for the merge rail to attribute, and the rail refuses with
"BLOCKED - 1 of 1 failing PR run(s) yielded NO parseable failure identity". The
measured harm is not a red leg but an unlandable one: #6390 was
`mergeable=true`, clean-reviewed AT HEAD, `behind=0`, and still could not land.

The fix is the MIRROR of the existing `carve_out` subtraction: a `carve_out` file
cannot run in a URI-SET leg, so it is subtracted; these cannot run in a URI-EMPTY
one, so they must be too.

**Why this file runs pytest instead of parsing the tree.** Four review cycles
tried to decide statically whether a module aborts collection (module-level `if`s,
alias renames, local wrappers, class bodies, decorators, lambdas, `**_KW`
forwarding, constructed env names, transitive helpers, default arguments, guard
polarity...). Each cycle found more shapes: an AST predicate is an approximation
of a question pytest already answers exactly. So the census now asks pytest —
the same command the failing CI step runs, in the same environment shape — and
treats "this module produced no nodeids" as the single rule. That is the
invariant itself, not a model of it:

    a module that collects NOTHING in a leg's own configuration
    must not be handed to that leg.

It also cannot remove coverage: whatever the reason a module yields no nodeids in
that leg, it was not going to run a test there.

**Exact over the candidate set, never over the tree.** The probe is authoritative
for every file it is given, but the set it is given is bounded — and the bound is
deliberately a DISJUNCTION, because each half alone leaks, and both leaks were
measured under review:

* **URI-relevance** — the module NAMES the URI (the env var, or `is_db_uri`).
  This is the property the lane is about, and it catches what a mechanism filter
  misses: a module-level `raise unittest.SkipTest(...)`, which carries no
  `allow_module_level` literal at all.
* **mechanism** — the module itself carries an abort spelling
  (`allow_module_level`, a `raise …SkipTest`, or the positional
  `pytest.skip(reason, True)`). This catches the reverse, which the URI half
  alone missed: a caller that holds the abort mechanism but never names the URI,
  because it reads the URI through a helper.

The union is cheap — the mechanism candidates are a subset of the URI candidates
today — and the PROBE, not the filter, decides the answer either way.

Two consequences, both stated rather than implied:

* `pytest.importorskip(...)` is excluded **structurally, not by special case**: a
  module that calls it spells neither half. That is the right outcome — that skip
  means a MISSING DEPENDENCY, so the leg should go RED and say so, not have the
  module silently subtracted into this lane; folding it in would convert an
  environment break into a quiet coverage hole. (`importorskip` also aborts on
  every leg, not just the URI-less one.)
* the residual is a test module that aborts collection through a spelling NEITHER
  half carries (e.g. an aliased helper). The tripwire below is deliberately
  BROADER than the candidate filter for exactly that reason — it flags any
  non-handler `SkipTest` reference, not just the raise-anchored spelling, because
  nothing else would surface a helper-only abort. An abort via
  `pytest.exit`/`sys.exit` is a collection ERROR (rc≠5), not this rc=5 shape, and
  belongs to the attribution half tracked on the issue.

A module emptied by some other cause (the manifest's own `-m` marker, a
collection error) is the *attribution* half of #6884, recorded separately on the
issue, and is out of scope here.
"""
from __future__ import annotations

import copy
import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_selection import (  # noqa: I001
    classify_test_file, load_manifest, on_demand_files, select,
    slow_leg_by_surface, uri_requiring_files,
)

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

# The module-level aborts that produce the rc=5 "no tests collected" shape:
# `raise unittest.SkipTest(...)` (no `allow_module_level` literal at all), and
# the positional form `pytest.skip("reason", True)`. Anchored so a legitimate
# `except unittest.SkipTest:` handler or a plain `pytest.skip(reason)` inside a
# test body is not read as an abort mechanism.
_MODULE_LEVEL_SKIP_RE = re.compile(r"raise\s*\(?\s*[\w.]*SkipTest\b")
_POSITIONAL_SKIP_RE = re.compile(r"pytest\.skip\(\s*[^,()]+,\s*True\s*[,)]")


def _carries_module_abort(src: str) -> bool:
    """The abort spellings a module can carry ITSELF, all yielding the rc=5 shape.

    Shared by the candidate filter and the tripwire so the two cannot drift into
    disagreeing about what an abort mechanism is.
    """
    return (
        "allow_module_level" in src
        or bool(_MODULE_LEVEL_SKIP_RE.search(src))
        or bool(_POSITIONAL_SKIP_RE.search(src))
    )


def _references_skip_mechanism(src: str) -> bool:
    """The tripwire's predicate: BROADER than `_carries_module_abort`.

    The candidate filter can afford a narrow spelling list — a candidate it
    misses surfaces as a red census over the probe's own output. The tripwire
    guards the RESIDUAL, where nothing else would surface, so it must be broad:
    an aliased `_SKIP = unittest.SkipTest; raise _SKIP(...)` or a parenthesised
    `raise(unittest.SkipTest(...))` in a helper aborts collection identically.
    Only bare `except` / `import` / `from` lines are exempt, so a legitimate
    handler is not read as an abort mechanism.
    """
    if _carries_module_abort(src):
        return True
    for line in src.splitlines():
        code = line.split("#", 1)[0].strip()
        if "SkipTest" not in code:
            continue
        if code.startswith(("except", "import ", "from ")):
            continue
        return True
    return False


def _is_candidate_source(src: str) -> bool:
    """The candidate bound: NAMES the URI, or CARRIES the abort mechanism.

    A disjunction because each half alone was measured to leak under review — see
    the module docstring. Unit-tested by
    `test_candidate_bound_covers_both_halves_of_the_disjunction`.
    """
    return (
        "TORTOISE_DB_URI" in src
        or "is_db_uri" in src
        or _carries_module_abort(src)
    )

# Must mirror the CI collect step's marker (`python-ci.yml`, the
# "Generate coverage manifest" run block) or the probe measures a different leg
# than the one that breaks.
COLLECT_MARKER = "not track_b and not live and not integration"

# The real vector: #6390 is a one-file dashboard comment change (`+4/-3`) whose
# tier-2 leg selected `test_onboarding_state_split.py` and died on rc=5. Kept
# verbatim so the regression is pinned against the input that actually failed.
DASHBOARD_ONLY_CHANGE = ["website/apps/dashboard/src/harnesses.js"]
DOCS_ONLY_CHANGE = ["docs/00_index.md"]


@functools.lru_cache(maxsize=1)
def _MANIFEST() -> dict:
    """The manifest, read ONCE. `load_manifest()` re-reads and re-parses the
    YAML (~0.4s) and these tests call it ~15 times; this file is a new fast-pool
    file with no measured duration, so its wall clock is weighted at the ~2s
    default until the owned writer (`tools/ci_timing.py --refresh-durations`)
    records it. Callers MUST deepcopy before mutating."""
    return load_manifest()


# --------------------------------------------------------------------------
# The census: ask pytest, don't model pytest
# --------------------------------------------------------------------------
def _candidate_modules() -> list[str]:
    """Selectable modules that could abort collection on an unusable URI.

    A cheap superset used only to bound the probe's cost. The bound is
    `_is_candidate_source` — a DISJUNCTION, because each half alone was measured
    to leak under review: the module NAMES the URI, or it CARRIES the abort
    mechanism. The PROBE below decides the answer; the filter only decides what
    the probe is pointed at.

    The residual — an abort through something neither half spells, e.g. a helper
    that raises — is what the tripwire below guards.
    """
    out: list[str] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        rel = path.relative_to(TESTS)
        if rel.parts[0] == "e2e":
            # e2e is exempt from `integrity()` (#1349) and is not selectable.
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _is_candidate_source(src):
            out.append(str(rel))
    return out


def _paths_with_no_nodeids(stdout: str, candidates: list[str]) -> set[str]:
    """Which candidate modules produced NO collected nodeid in this output.

    Pure parsing, unit-tested below against real pytest output shapes: a
    collected test prints `tests/x.py::test_name`, while a module-level skip
    prints only an `-rs` summary line naming the FILE (`SKIPPED [1]
    tests/x.py:33: reason`) — which must NOT be mistaken for collection.
    """
    collected_prefixes = {
        line.split("::", 1)[0].strip()
        for line in stdout.splitlines()
        if "::" in line
    }
    return {f"tests/{c}" for c in candidates if f"tests/{c}" not in collected_prefixes}


@functools.lru_cache(maxsize=1)
def _collect_nothing_uri_less() -> frozenset[str]:
    """The module set that collects nothing when `TORTOISE_DB_URI` is EMPTY.

    This is not a model of the CI failure — it IS the failing command, run over
    the candidate set in one pytest invocation, with the leg's own environment
    shape: `TORTOISE_DB_URI` PRESENT AND EMPTY (`URI=""` is written into
    `$GITHUB_ENV` and exported by the manifest step, `python-ci.yml:606-624` and
    `:671`), not removed. The distinction is load-bearing — a module gating on
    `"TORTOISE_DB_URI" not in os.environ` collects under one shape and aborts
    under the other — so a maintainer must not "restore" this to `env -u`.
    `TORTOISE_TEST_EXPECT_URI=""` and `TORTOISE_TEST_CARVE_OUT="1"` are also set
    to match the leg's broader shape; they are provably inert under
    `--collect-only` (removing them flags the same set).

    PROVABLY faithful: over the tree at the time of writing it flags exactly the
    eight declared modules, and every other candidate yields nodeids.
    """
    candidates = _candidate_modules()
    if not candidates:
        return frozenset()
    # MATCH THE LEG'S ENV SHAPE EXACTLY. The tier-2 legs do not leave these
    # variables UNSET: "Compute docker URI" writes URI="" into $GITHUB_ENV and
    # the manifest/run steps export it (python-ci.yml:606-624), so the leg sees
    # TORTOISE_DB_URI PRESENT AND EMPTY. The two shapes are not interchangeable —
    # a module gating on `"TORTOISE_DB_URI" not in os.environ`, on `== ""`, or
    # on `os.environ.setdefault(...)`, behaves differently under each — and the
    # probe must measure the leg that actually breaks. Only PYTEST_ADDOPTS is
    # removed (it would inject the parent session's options into the child).
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    env["TORTOISE_DB_URI"] = ""
    env["TORTOISE_TEST_EXPECT_URI"] = ""
    env["TORTOISE_TEST_CARVE_OUT"] = "1"  # the URI-less lane's required opt-in
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            *[f"tests/{c}" for c in candidates],
            "--collect-only", "-q", "-p", "no:cacheprovider",
            "-m", COLLECT_MARKER,
            # One module's collection error must not hide the other modules'
            # nodeids; without this the whole probe would report "no nodeids".
            "--continue-on-collection-errors", "-rs",
        ],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=1800,
    )
    if proc.returncode not in (0, 5):
        pytest.fail(
            "the URI-less collect probe could not run — the census is "
            f"UNOBSERVED, not clean (rc={proc.returncode}).\n"
            f"stderr tail:\n{proc.stderr[-2000:]}"
        )
    return frozenset(_paths_with_no_nodeids(proc.stdout, candidates))


# --------------------------------------------------------------------------
# The probe's own correctness, then the census
# --------------------------------------------------------------------------
def test_only_candidate_shaped_files_carry_the_module_skip_mechanism() -> None:
    """The census is exact over its candidate set, so the mechanism may not
    appear outside it.

    `_candidate_modules()` scans `test_*.py` files that READ the URI. A skip
    living in a shared helper that a test module imports and calls at import is
    therefore invisible: the caller never names the URI, the census reports
    "declared == observed" while the module aborts collection in a URI-less leg.
    Rather than chase mechanism spellings across files, this forbids ANY
    module-level collection-abort mechanism anywhere under `tests/` that the
    census does not read — if a helper needs one, the census must be widened
    deliberately at the same time. (A helper carrying it OUTSIDE `tests/`
    remains a stated residual: the scan is scoped to the suite because that is
    where the census and `integrity()` look.)

    The offender set is the spellings that abort collection at import with the
    rc=5 "no tests collected" shape: `allow_module_level` (pytest.skip /
    pytest.importorskip, which all accept it) and a `raise …SkipTest` (which
    carries no `allow_module_level` literal at all — the spelling a mechanism
    filter missed). Both come from `_carries_module_abort`, so the tripwire and
    the candidate filter cannot drift apart.
    """
    offenders = sorted(
        str(p.relative_to(TESTS))
        for p in TESTS.rglob("*.py")
        if not p.name.startswith("test_")
        and p.relative_to(TESTS).parts[0] != "e2e"
        and _references_skip_mechanism(p.read_text(encoding="utf-8"))
    )
    assert not offenders, (
        f"{offenders} carry a module-level collection-abort mechanism but are "
        "not `test_*.py` files the census would scan, so a test module that "
        "imports such a helper and calls it at import aborts collection in a "
        "URI-less leg while the census stays green (#6884). Either move the skip "
        "into the module that needs it, or widen `_candidate_modules()` to cover "
        "this file in the same change. (The `e2e/` subtree is excluded, matching "
        "the census and `integrity()` #1349 — those files are not selectable, so "
        "the mechanism there cannot be handed to a leg.)"
    )


def test_candidate_bound_covers_both_halves_of_the_disjunction() -> None:
    """The candidate bound is a DISJUNCTION, and each half is load-bearing.

    Both halves were measured to leak on their own (review cycles 5–11): a
    mechanism-only filter misses a module-level `raise unittest.SkipTest(...)`,
    and a URI-only filter misses a caller that holds the abort mechanism but
    reads the URI through a helper — which is the escape cycle 11 BUILT and
    measured (rc=5 with the URI empty, rc=0 with it set, caught by neither an
    earlier URI-only bound nor the tripwire).
    """
    # half (a): NAMES the URI. Covers the SkipTest spelling (no `allow_module_level`).
    assert _is_candidate_source("raise unittest.SkipTest('needs a URI')")
    assert _is_candidate_source("if not is_db_uri(URI):\n    raise SystemExit(1)")
    # half (b): CARRIES the mechanism, names no URI — the cycle-11 escape.
    assert _is_candidate_source(
        "from _uri_gate import uri_present\n"
        "if not uri_present():\n"
        "    pytest.skip('docker-lane requires a URI', allow_module_level=True)\n"
    )
    # neither half: the intended STRUCTURAL exclusion for importorskip.
    assert not _is_candidate_source("torch = pytest.importorskip('torch')")
    # the positional spelling of the same abort, which a `raise`-only regex misses.
    assert _is_candidate_source("pytest.skip('docker-lane requires a URI', True)")
    # `SkipTest` anchored to the raise, so a handler is not an abort mechanism.
    assert not _carries_module_abort(
        "try:\n    run()\nexcept unittest.SkipTest:\n    pass\n"
    )
    assert _carries_module_abort("raise unittest.SkipTest('x')")
    assert _carries_module_abort("raise(unittest.SkipTest('x'))")


def test_tripwire_is_broader_than_the_candidate_filter() -> None:
    """The tripwire guards the RESIDUAL, so it must not inherit the filter's
    narrow spellings: an ALIASED or parenthesised `SkipTest` raise in a helper
    aborts collection identically, and nothing else would surface it.

    Both spellings were measured at the rc=5 shape in review cycle 12.
    """
    aliased = "_SKIP = unittest.SkipTest\nraise _SKIP('needs a URI')\n"
    assert _references_skip_mechanism(aliased)
    # A legitimate handler is still exempt — the reason the regex is anchored.
    assert not _references_skip_mechanism(
        "try:\n    run()\nexcept unittest.SkipTest:\n    pass\n"
    )
    assert not _references_skip_mechanism("from unittest import SkipTest\n")


def test_nodeid_parsing_is_not_fooled_by_skip_summary_lines() -> None:
    """The parser's one hazard: an `-rs` SKIPPED line names the file.

    A module-level skip prints `SKIPPED [1] tests/x.py:33: reason`, which
    contains a path but NO `::`, and must not count as collection.
    """
    stdout = (
        "tests/test_a.py::test_one\n"
        "tests/test_a.py::test_two\n"
        "SKIPPED [1] tests/test_b.py:33: docker-lane tests require "
        "TORTOISE_DB_URI (tier-2 embedded legs skip)\n"
        "106 tests collected in 5.43s\n"
    )
    assert _paths_with_no_nodeids(stdout, ["test_a.py", "test_b.py"]) == {
        "tests/test_b.py"
    }


def test_probe_reproduces_the_shipped_failure() -> None:
    """The probe must flag the module that actually stranded #6390.

    `test_onboarding_state_split.py` is the file whose zero-collection killed
    #6390's `test (a)` leg. If the probe ever stops seeing it, the census has
    gone blind and this file is worthless.
    """
    flagged = _collect_nothing_uri_less()
    assert "tests/test_onboarding_state_split.py" in flagged, (
        "the URI-less collect probe no longer flags test_onboarding_state_split.py "
        "— the module that produced #6390's unattributable rc=5. The probe is "
        "measuring the wrong leg (check TORTOISE_DB_URI is really EMPTY, present "
        "not absent, and the marker still matches the CI collect step)."
    )


def test_declared_list_matches_the_observation() -> None:
    """A newly module-skipping file must be REGISTERED, not discovered in CI."""
    declared = uri_requiring_files(_MANIFEST())
    observed = {name.removeprefix("tests/") for name in _collect_nothing_uri_less()}
    assert declared == observed, (
        "config/ci-surfaces.yml `uri_requiring` does not match what pytest "
        "actually collects with an EMPTY TORTOISE_DB_URI.\n"
        f"  declared but DOES collect (stale entry): {sorted(declared - observed)}\n"
        f"  collects nothing but NOT declared: {sorted(observed - declared)}\n"
        "Undeclared modules are handed to a URI-less tier-2 leg, collect zero "
        "tests there, and red it with no attributable failure (#6884). Add them "
        "to `uri_requiring`. The reverse direction means an entry is stale: check "
        "whether the module genuinely collects tests again before removing it."
    )


def test_declared_entries_exist_and_are_classified() -> None:
    """Every declared name must be a real, manifest-classified test file.

    A dead entry (a file no surface owns) is a no-op that protects nothing: it
    would be subtracted from a selection that never contained it.
    """
    manifest = _MANIFEST()
    for name in sorted(uri_requiring_files(manifest)):
        assert (TESTS / name).is_file(), f"uri_requiring names a missing file: {name}"
        assert classify_test_file(name, manifest) is not None, (
            f"uri_requiring names an UNCLASSIFIED file: {name} — no surface owns "
            "it, so no leg ever selects it and the entry protects nothing"
        )


def test_lane_is_disjoint_from_the_uri_unset_only_lanes() -> None:
    """carve-out / tier-1 / on-demand are URI-unset or leg-less by design.

    `slow_files` is deliberately NOT asserted disjoint: the slow lane subtracts
    the lane itself (pinned by test_slow_lane_subtracts_the_lane), so a future
    relocation into `slow_files` is handled rather than forbidden.
    """
    manifest = _MANIFEST()
    uri = uri_requiring_files(manifest)
    assert not (uri & set(manifest.get("carve_out", []))), (
        "a uri_requiring file is also carve_out — the carve-out job is URI-unset "
        "too, so it would abort there instead (#6884)"
    )
    assert not (uri & set(manifest.get("tier1", []))), (
        "a uri_requiring file is in the tier-1 smoke set, which every PR "
        "selection starts from"
    )
    assert not (uri & on_demand_files(manifest))


def test_empty_lane_does_not_crash_the_changes_job() -> None:
    """`uri_requiring:` with NO entries parses to None, not [] (YAML's empty
    value) — `set(None)` would TypeError in the `changes` job, the single path
    all PR CI goes through. Reachable at the lane's terminal state: the day the
    last URI-gated module is fixed."""
    manifest = copy.deepcopy(_MANIFEST())
    manifest["uri_requiring"] = None
    assert uri_requiring_files(manifest) == set()
    for changed in (DOCS_ONLY_CHANGE, DASHBOARD_ONLY_CHANGE):
        sel = select(changed, "pull_request", manifest)  # must not raise
        assert isinstance(sel["test_files"], list)


# --------------------------------------------------------------------------
# Selection arms — every tier-2 exit
# --------------------------------------------------------------------------
# Every tier-2 exit reachable by a PR: the docs/website-only EARLY return, the
# main surface path, and the slow lane. Parametrised so a fix applied to only
# one exit cannot pass.
TIER2_VECTORS = [
    ("docs-only (early return)", DOCS_ONLY_CHANGE),
    ("website-only (early return)", ["website/index.html"]),
    ("dashboard surface (#6390)", DASHBOARD_ONLY_CHANGE),
    ("onboarding surface", ["tortoise/onboarding/SKILL.md"]),
    ("core surface", ["config/ci-surfaces.yml"]),
]


@pytest.mark.parametrize(
    "changed", [v[1] for v in TIER2_VECTORS], ids=[v[0] for v in TIER2_VECTORS]
)
def test_no_tier2_path_leaks_a_uri_requiring_file(changed: list[str]) -> None:
    sel = select(changed, "pull_request", _MANIFEST())
    assert sel["full"] is False, "vector must stay a tier-2 selection"
    uri = uri_requiring_files(_MANIFEST())
    leaked = uri & set(sel["test_files"])
    assert not leaked, (
        f"a URI-less tier-2 leg was handed URI-requiring file(s) {sorted(leaked)} "
        "— each collects zero tests there and reds the leg with no attributable "
        "failure (#6884)"
    )
    leaked_slow = uri & set(sel["slow_selected"])
    assert not leaked_slow, (
        f"the URI-less test-slow leg was handed {sorted(leaked_slow)} (#6884)"
    )


def test_dashboard_vector_is_load_bearing() -> None:
    """The #6390 vector must really exercise the filter.

    Otherwise `test_no_tier2_path_leaks_a_uri_requiring_file` could pass
    vacuously the day this vector stops selecting the onboarding surface. The
    victim is SYNTHESISED and injected into both the lane and the surface, so
    this arm never depends on the live lane's contents — a lane that empties is
    the success state (#6884's terminal state), not a reason to red.
    """
    victim = "test_dashboard_vector_probe.py"
    manifest = copy.deepcopy(_MANIFEST())
    manifest["uri_requiring"] = sorted(set(uri_requiring_files(manifest)) | {victim})
    manifest["surfaces"]["onboarding"] = [*manifest["surfaces"]["onboarding"], victim]
    sel = select(DASHBOARD_ONLY_CHANGE, "pull_request", manifest)
    assert sel["full"] is False, (
        "this vector must stay a tier-2 selection — `test_files` is the sentinel "
        "`\"ALL\"` on a full selection, against which every membership assertion "
        "below would pass vacuously"
    )
    assert "onboarding" in sel["surfaces"], (
        "the #6390 vector no longer selects the onboarding surface, so it no "
        "longer exercises the filter — pick another real vector rather than "
        "deleting this arm"
    )
    assert victim not in set(sel["test_files"]), (
        "a lane member owned by a selected surface survived the tier-2 "
        "subtraction on the #6390 vector (#6884)"
    )


def test_docs_only_early_return_subtracts_the_lane() -> None:
    """A plain docs-only vector cannot fail: `tier1 ∩ uri_requiring == []`, so
    the output is byte-identical with and without the early return's
    subtraction. Inject a SYNTHESISED victim into both tier-1 and the lane to
    make the exit provable without depending on the live lane being non-empty."""
    victim = "test_early_return_probe.py"
    manifest = copy.deepcopy(_MANIFEST())
    manifest["uri_requiring"] = sorted(set(uri_requiring_files(manifest)) | {victim})
    manifest["tier1"] = sorted(set(manifest["tier1"]) | {victim})
    sel = select(DOCS_ONLY_CHANGE, "pull_request", manifest)
    assert sel["full"] is False, "a docs-only change must stay a tier-2 selection"
    assert victim in set(manifest["tier1"]), "setup: injection must be visible"
    assert victim not in set(sel["test_files"]), (
        "the docs-only early return did not subtract `uri_requiring` — it "
        "returns before the main tier-2 subtraction (#6884)"
    )


def test_slow_lane_subtracts_the_lane() -> None:
    """The test-slow legs get an EMPTY `TORTOISE_DB_URI` on a tier-2 PR too (not
    an unset one — `python-ci.yml:1384-1390` writes `URI=""` into `$GITHUB_ENV`,
    the same shape as the fast job), so a relocated
    `uri_requiring` file must not reach them.

    The victim is SYNTHESISED and injected into both `slow_files` and the lane,
    so this arm survives the lane emptying.
    """
    victim = "test_slow_lane_probe.py"
    manifest = copy.deepcopy(_MANIFEST())
    manifest["uri_requiring"] = sorted(set(uri_requiring_files(manifest)) | {victim})
    manifest["slow_files"] = sorted(set(manifest["slow_files"]) | {victim})
    assert victim in (set(manifest["slow_files"]) - set(manifest["carve_out"])), (
        "setup: the file must otherwise reach the slow leg"
    )
    legs: set[str] = set()
    for members in slow_leg_by_surface(manifest).values():
        legs.update(members)
    assert victim not in legs, (
        "a uri_requiring file relocated into `slow_files` reaches the URI-less "
        "test-slow leg (#6884)"
    )


def test_full_selection_keeps_uri_requiring_files() -> None:
    """The push lane HAS the URI, so the subtraction must not leak into it.

    `_full_selection` reports `test_files` as the sentinel `"ALL"` (every fast
    file runs), so the assertion is on the sentinel — proving the tier-2
    subtraction was not applied to the full path.
    """
    sel = select(DASHBOARD_ONLY_CHANGE, "push", _MANIFEST())
    assert sel["full"] is True, "a push must be a full selection"
    assert sel["test_files"] == "ALL", (
        "a full/push selection has the URI and MUST keep running every fast "
        f"file (including the `uri_requiring` ones); got {sel['test_files']!r}, "
        "so the tier-2 subtraction leaked into the full path (#6884)"
    )
