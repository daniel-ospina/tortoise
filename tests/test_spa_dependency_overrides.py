"""Guard: every npm `overrides` entry in the shipped SPAs names what it clears.

Spec: PR #9524's review (2026-10-21), findings I1 and I3.

WHY a test and not just a tidy diff. `overrides` is the one place a SPA states a
SECURITY FLOOR, and `package-lock.json` is the place the resolved version lands.
When the two disagree — or when an override names a package whose advisory has no
fixed version in that major — the manifest reads as a mitigation that is not one:
a reader of the merge record sees `"braces": "^3.0.3"` and concludes the `braces`
advisory is handled. I1 was exactly that (four entries that clear nothing:
`braces` has no fixed 3.x, `npm view braces dist-tags` -> `latest: 3.0.3`, and the
advisory's affected range is `<=3.0.3`; `micromatch`/`fast-glob`/`chokidar`
inherit it). I3 was the mirror image: `apps/graph-viz` audits to 0 only because
its LOCK refreshed (`nanoid 3.3.16 -> 3.3.20`, `source-map-js 1.2.1 -> 1.2.2`,
`vite 8.1.5 -> 8.3.5`), stating no floor at all — so the next lock refresh can
walk it straight back below the fixed version, silently.

So this file pins five properties, all mechanical, no `npm` binary and no DB:

1. every declared override is RECORDED below with what it clears — a new
   override cannot land as an unexplained mitigation (reds on an earlier
   REVISION OF THIS BRANCH, which declared nine overrides in blog-admin, five
   of which cleared nothing: that state is not in the base tree — `main`
   declares no `overrides` block at all, so on the base tree this leg is
   vacuously green and proves nothing);
2. the two shared transitive floors are declared by EVERY SPA — parity, so the
   fix exists in the manifest and not only in a lock resolution (reds on the
   pre-fix tree: graph-viz had no `overrides` block at all);
3. every declared floor is actually met by the committed lock — the floor and
   the resolution cannot drift apart;
4. the DIRECT dependencies carrying the same advisory wave are floored too
   (`DIRECT_DEPENDENCY_FLOORS`) — property 2 above only sees the two *transitive*
   packages, so a direct `vite` floor below the fixed version stayed green
   forever: `apps/graph-viz` sat on `^8.1.1`, which admits the advisory-named
   `8.1.5`, while its audit read 0 only because the committed lock resolved
   `8.3.5` (PR #9524 review, round 5 ISSUE 1 — I3's finding one layer down);
5. the manifest records that describe this file's COST state the fetch it
   performs — `config/ci-surfaces.yml`'s two registration comments and its
   `durations` row said "no network" for a file that runs `git fetch`, the exact
   record class `test_9397_about_edge_properties.py` pins for the sibling this
   helper was adopted from (round 5 ISSUE 2);
6. the two figures quoted ABOUT this file — `RECORDED_CASE_COUNT` and
   `RECORDED_NON_VACUITY_REDS` — are held to the file itself: the count against
   `pytest`'s own collection, the red set against the non-vacuity experiment RUN
   rather than reasoned about. Round 6 found both stale in the PR body ("23
   cases … 4 failed"), each describing an earlier revision, so neither figure is
   prose any more: one literal each, and a leg that recomputes it.

Exactly one corner is not offline, and it is named rather than hidden: the
`base` column of `RESOLUTIONS` is read from `PRE_FIX_REVISION`'s locks, and a
SHALLOW checkout (which is what CI's `test` job checks out) does not contain
that revision. `_ensure_commit` therefore runs `git fetch --depth={120,400,1500}
origin main` there — a NETWORK call with a side effect on the checkout under
test — and the leg SKIPS, never passes, when the revision stays unreachable. A
`git show` that merely returned `None` in CI left this whole half dark on every
PR run (PR #9524 review, round 3 I3 / round 4 ISSUE 1). The summary lines
the PR body may quote are then DERIVED, not copied: there are exactly two
shapes, and both are stated here as expressions over this file's own records so
they cannot drift the way a literal did in rounds 3 and 6. The ladder reaches
the revision (a full clone; and CI, where `origin main` still carries it within
120 commits) → every collected case runs, `RECORDED_CASE_COUNT` passed and 0
skipped. It does not reach it (no git, no network, or a history that cannot
reach it) → 5 cases SKIP and never pass, `len(RESOLUTIONS)` base-lock cases +
this record's own experiment leg, which needs the same revision's manifests. Both
shapes were measured 2026-10-22 in real depth-1 clones of this branch (one whose
`origin` had a reachable `main`, one whose did not), not inferred.

The ladder is MEMOISED
per revision (`_ENSURE_COMMIT_RESULTS`), so the eight per-(package, app)
readers cost ONE ladder per run rather than eight — which is what makes the
manifest's `durations` weight a bounded claim instead of an assertion.

`test_the_base_half_is_reachable_in_a_shallow_checkout` pins the fetch's
structure (the `_is_shallow` gate BEFORE the loop, so a full clone is never
truncated) and the memo; `test_the_manifest_states_the_fetch_it_cannot_hide`
pins the manifest records against the code, so neither can drift back to
claiming a hermetic suite.

The measurements behind the records below were taken on this branch head with
`npm audit --package-lock-only` against each app's `package.json` +
`package-lock.json` (dashboard 5 -> 0; blog-admin 13 -> 5, and the residual five
are one Tailwind-3 chain; apps/graph-viz 3 -> 0). Re-measure before changing a
record: the number is the point of the record, not decoration.

A per-app figure carried in prose is exactly what got one wrong: round 3 of the
#9524 review found `nanoid`'s dashboard BASE recorded as `3.3.16` when the
dashboard lock resolved `3.3.17` — `3.3.16` is graph-viz's base, carried over to
the wrong app (the security conclusion held: 3.3.17 is below the 3.3.18 fix, so
it was a record defect, not a shipped one). So the base and head resolutions are
no longer prose only: `RESOLUTIONS` below keys them BY APP, and two tests hold
the table to the two locks that can be read locally — this tree's lock, and
`PRE_FIX_REVISION`'s lock through `git show` (reached through `_ensure_commit`
where the checkout is shallow; skipped, with its reason, only where the history
is genuinely unreachable: an unreadable revision is not evidence of no copies).
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# The three SPAs in this repo. `react` is declared by these and only these, so
# this map is also the complete surface of the React bump in #9524.
SPAS = {
    "website/apps/dashboard": "tortoise-dashboard",
    "website/apps/blog-admin": "tortoise-blog-admin",
    "apps/graph-viz": "graph-viz",
}

# Floors that ALL THREE apps must declare. Both packages are transitive build
# dependencies (vite/nanoid, postcss/source-map-js) that no app depends on
# directly, so a manifest that states no floor leaves the advisory closed by the
# lock resolution alone — I3's finding.
SHARED_TRANSITIVE_FLOORS = ("nanoid", "source-map-js")

# Direct (DECLARED, not transitive) dependencies whose own advisory wave this
# branch closes, with the FIXED version per app. `SHARED_TRANSITIVE_FLOORS`
# cannot see these — `vite` is a devDependency of all three apps, so its range is
# not an `overrides` entry and `test_every_declared_floor_is_met_by_the_committed_lock`
# never reads it. That blind spot is round 5's ISSUE 1: `apps/graph-viz` was left
# on `^8.1.1`, whose floor admits the advisory-named `8.1.5`, while the app's
# audit read 0 purely because the committed LOCK had refreshed to `8.3.5` — no
# declared intent, exactly the asymmetry the I3 correction was written to remove
# (and the other two apps already had their floor raised in the same commit).
#
# The figures are MEASURED, 2026-10-22, `npm audit --package-lock-only` on each
# pre-fix lock (`git show 69390341f:<app>/package-lock.json`): the SAME five
# advisories on both vite lines — GHSA-9jrq-w75r-8gcw, GHSA-rq7h-c2jc-7f22,
# GHSA-698v-4r3x-3vcv, GHSA-r825-w8mm-5pxw, GHSA-587h-7wmj-h5pm — affected
# `<=6.4.3` on the 6.x line (dashboard's base lock resolved 6.4.3) and
# `>=8.0.0 <=8.1.5` on the 8.x line (graph-viz's base lock resolved 8.1.5). The
# fixed version per app is what this branch ships: `6.4.5` / `8.3.5`.
DIRECT_DEPENDENCY_FLOORS = {
    "vite": {
        "website/apps/dashboard": (6, 4, 5),
        "website/apps/blog-admin": (6, 4, 5),
        "apps/graph-viz": (8, 3, 5),
    },
}

# Every override that may legitimately appear, with WHAT it clears. A package
# whose advisory has no fixed version in the declared major does not belong here
# — it belongs in RETIRED_NO_OPS (finding I1).
RECORDED_OVERRIDES = {
    "nanoid": (
        "GHSA-2v37-7h3g-55p8 (nanoid, fixed 3.3.18): dashboard's lock sat at "
        "3.3.17 and now resolves 3.3.20 (graph-viz 3.3.16 -> 3.3.20); "
        "blog-admin already resolved 3.3.18, so there the entry is the FLOOR "
        "that keeps it there."
    ),
    "source-map-js": (
        "GHSA-68fv-2mgg-jv7q (source-map-js, fixed 1.2.2): all three apps "
        "1.2.1 -> 1.2.2, so the floor is what keeps them above the fix."
    ),
    "markdown-it": (
        "4 high advisories, the last fixed in 14.3.3 (GHSA-vfgh-jcmw-g37q, "
        "GHSA-gfr2-rv44-r23c): blog-admin 14.3.0 -> 14.3.3."
    ),
    "postcss-selector-parser": (
        "GHSA-rj75-hqrm-r3gf (postcss quadratic selector parsing, fixed 7.1.6). "
        "Forced to a single hoisted 7.1.7, replacing tailwindcss's 6.1.4 and "
        "@tailwindcss/typography's exact 6.0.10 pin — which is what clears the "
        "moderates those two inherit from it (incl. `postcss-nested`). Verified "
        "by a real `tsc --noEmit && vite build` of blog-admin on this override; "
        "the v7 break ('insertions during iteration') does not reach them."
    ),
}

# Overrides that were measured to clear NOTHING and were removed with the body
# correction (I1). Kept as a record so the next lane does not re-add them as an
# apparent fix:
#   * braces / micromatch / fast-glob / chokidar — `braces` has no fixed 3.x
#     (latest IS the affected 3.0.3), so a `^3.0.3` floor cannot clear it and
#     the other three inherit its advisory. Measured: blog-admin's audit is the
#     SAME five high entries with and without these four lines.
#   * postcss-nested — its moderate is inherited from postcss-selector-parser
#     <7.1.6 (npm prints it as `2.0.3 - 6.2.0 via postcss-selector-parser`), so
#     the selector-parser override is what clears it; the separate entry resolved
#     the identical 6.2.0 as the base lock and changed nothing.
RETIRED_NO_OPS = {
    "braces": "no fixed 3.x exists; floor cannot clear the advisory (I1)",
    "micromatch": "inherits the braces advisory; clears nothing (I1)",
    "fast-glob": "inherits the micromatch/braces advisory; clears nothing (I1)",
    "chokidar": "inherits the braces advisory; clears nothing (I1)",
    "postcss-nested": "its moderate is inherited from postcss-selector-parser (I1)",
}

# The pre-fix revision of #9524: the PARENT of this branch's first commit, i.e.
# the base tree of this PR's own work (`f2451b7c1`), NOT the branch's merge base
# with `main` — the branch has since merged `main`, so `git merge-base origin/main
# origin/chore/security-2026-10-21-npm` moves and is the wrong anchor for a
# pre-fix figure. It is an ANCESTOR OF `main`, which is what lets
# `_ensure_commit` reach it in a shallow CI clone with a bounded `origin main`
# fetch. The `base` column of RESOLUTIONS is measured from THIS commit's locks —
# never from a recollection of another app's lock.
PRE_FIX_REVISION = "69390341fb152feb4c20287709634f68c5b4004c"

# Depths tried, in order, to make PRE_FIX_REVISION reachable in a shallow clone.
# Measured 2026-10-22: PRE_FIX_REVISION is 5 commits behind `origin/main`, so the
# first depth already covers it; the ladder is the #9397 helper's, unchanged.
_SHALLOW_DEPTHS = (120, 400, 1500)

# package -> app -> (pre-fix at PRE_FIX_REVISION, head in this tree's lock).
# Tuples, and every copy at every depth: an override's job is usually to
# collapse SEVERAL nested copies (`postcss-selector-parser` went from a hoisted
# 6.0.10 plus two nested 6.1.4 copies to one 7.1.7).
RESOLUTIONS: dict[str, dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "nanoid": {
        "apps/graph-viz": (("3.3.16",), ("3.3.20",)),
        "website/apps/blog-admin": (("3.3.18",), ("3.3.18",)),
        "website/apps/dashboard": (("3.3.17",), ("3.3.20",)),
    },
    "source-map-js": {
        "apps/graph-viz": (("1.2.1",), ("1.2.2",)),
        "website/apps/blog-admin": (("1.2.1",), ("1.2.2",)),
        "website/apps/dashboard": (("1.2.1",), ("1.2.2",)),
    },
    "markdown-it": {
        "website/apps/blog-admin": (("14.3.0",), ("14.3.3",)),
    },
    "postcss-selector-parser": {
        "website/apps/blog-admin": (("6.0.10", "6.1.4", "6.1.4"), ("7.1.7",)),
    },
}

_FLOOR_RE = re.compile(r"^\^(\d+)\.(\d+)\.(\d+)$")

# The retracted manifest claims (round 5 ISSUE 2). `config/ci-surfaces.yml`
# described this file as network-free in two registration comments and in its
# `durations` row, while `_ensure_commit` fetches `origin main`; the row went
# further and called the flat default an OVER-estimate, which is the inverse of
# the truth on a shallow checkout. Matched case-insensitively over the flattened
# text.
_RETRACTED_NETWORK_FREE = (
    "no network",
    "network-free",
    "over-estimates rather than under-estimates",
)

# ---- The verification record, COMPUTED rather than remembered -------------- #
# Both figures below are what a reader (and the PR body) quotes about THIS file,
# and both were wrong once in exactly the class of defect this file exists to
# stop: round 6 of the #9524 review found the body saying "23 cases … 4 failed"
# while the shipped file collected 26 and red 5 on the same experiment — each
# figure describing an earlier revision of the file. So neither number is prose:
# one literal each, and a leg at the bottom of this file RECOMPUTES it — the
# count from `pytest`'s own collection, the red set by RUNNING the experiment
# against `PRE_FIX_REVISION`'s manifests instead of describing it.

# Every collected case in this file: test functions + their parametrisations.
RECORDED_CASE_COUNT = 28

# The non-vacuity experiment: `PRE_FIX_REVISION`'s manifests restored over THIS
# tree's locks (the locks are NOT restored — a manifest-only diff is the state
# the review asked about). Named by CASE ID because a case id is what a summary
# line prints, and the two parametrisations differ in granularity:
# `test_shared_transitive_floors_are_declared` is per APP (three reds), while
# `test_direct_dependency_floors_clear_their_advisory` is per PACKAGE — ONE
# `vite` case whose slug loop stops at the first red, so the three apps that fail
# its floor compare appear here as a single case. A "7 failed" figure counts arms
# nobody's summary line prints; 5 is what the run says.
RECORDED_NON_VACUITY_REDS = (
    "test_direct_dependency_floors_clear_their_advisory[vite]",
    "test_no_recorded_override_is_stale",
    "test_shared_transitive_floors_are_declared[apps/graph-viz]",
    "test_shared_transitive_floors_are_declared[website/apps/blog-admin]",
    "test_shared_transitive_floors_are_declared[website/apps/dashboard]",
)

# Every test in this file sits in exactly one bucket, so a NEW leg cannot escape
# the model silently: a leg that reads an app manifest has a base-tree behaviour
# the experiment below must model, and adding one without recording it reds
# `test_the_recorded_non_vacuity_experiment_matches_the_shipped_file`. The
# buckets are what make the record refutable instead of merely plausible.
_MANIFEST_READING_LEGS = (
    "test_direct_dependency_floors_clear_their_advisory",
    "test_every_declared_floor_is_met_by_the_committed_lock",
    "test_every_override_names_what_it_clears",
    "test_no_recorded_override_is_stale",
    "test_no_retired_no_op_override_is_reintroduced",
    "test_shared_transitive_floors_are_declared",
)
_LOCK_OR_STRUCTURE_LEGS = (
    "test_every_recorded_override_has_a_measured_resolution",
    "test_recorded_head_resolution_matches_the_committed_lock",
    "test_recorded_pre_fix_resolution_matches_the_pre_fix_lock",
    "test_the_base_half_is_reachable_in_a_shallow_checkout",
    "test_the_fetch_ladder_runs_at_most_once_per_revision",
    "test_the_manifest_states_the_fetch_it_cannot_hide",
    "test_the_recorded_case_count_matches_the_shipped_file",
    "test_the_recorded_non_vacuity_experiment_matches_the_shipped_file",
)

_MARKUP_RE = re.compile(r"[*`\"]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _manifest(slug: str) -> dict:
    return json.loads((ROOT / slug / "package.json").read_text(encoding="utf-8"))


def _lock(slug: str) -> dict:
    return json.loads((ROOT / slug / "package-lock.json").read_text(encoding="utf-8"))


def _overrides(slug: str) -> dict:
    return _manifest(slug).get("overrides") or {}


def _floor(range_str: str) -> tuple[int, int, int]:
    """Parse the `^X.Y.Z` floors this repo declares — anything else is refused.

    A floor is only a floor if it is a concrete version: `*`, `latest` or a
    `>=` form would satisfy every assertion below while pinning nothing.
    """
    match = _FLOOR_RE.match(range_str)
    assert match, (
        f"override range {range_str!r} is not a concrete `^X.Y.Z` floor — a "
        "floor that cannot be read cannot be verified against the lock"
    )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _versions_in(packages: dict, package: str) -> list[str]:
    """Every resolved version of `package` in a lock's `packages` map, any depth."""
    found = []
    for key, entry in packages.items():
        if not key:
            continue
        if key.rsplit("node_modules/", 1)[-1] == package:
            version = entry.get("version")
            if version:
                found.append(version)
    return found


def _resolved_versions(slug: str, package: str) -> list[str]:
    """Every resolved version of `package` in the app's lock, at any depth.

    Nested copies are the ones an override most often exists to remove, so the
    whole `packages` map is walked rather than just the top-level entry.
    """
    return _versions_in(_lock(slug)["packages"], package)


def _declared_range(slug: str, package: str) -> str:
    """The app's DECLARED range for a direct dependency (any dependency block).

    Returns `""` when the app does not declare the package at all, so the caller
    can say that instead of raising `KeyError` — an app that drops a direct
    dependency must not crash the floor leg.
    """
    manifest = _manifest(slug)
    for block in ("dependencies", "devDependencies", "optionalDependencies"):
        declared = manifest.get(block) or {}
        if package in declared:
            return declared[package]
    return ""


def _has_commit(rev: str) -> bool:
    """True when `rev` resolves to a commit in this checkout's history."""
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-e", f"{rev}^{{commit}}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _is_shallow() -> bool:
    """True when this checkout is a SHALLOW clone."""
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--is-shallow-repository"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.strip() == "true"


# Memo for `_ensure_commit`: revision -> reachable. `_resolved_versions_at_revision`
# is called once per (package, app) pair — eight times — and each call would
# otherwise re-run the `_SHALLOW_DEPTHS` ladder, i.e. up to 24 fetches at a 120 s
# timeout each when the revision stays unreachable (round 5 ISSUE 2). Memoised,
# the ladder runs at most ONCE per revision per process, which is what makes the
# bound the manifest's `durations` note states true rather than asserted.
_ENSURE_COMMIT_RESULTS: dict[str, bool] = {}


def _ensure_commit(rev: str) -> bool:
    """Make `rev` reachable IF the checkout is shallow — bounded, best effort.

    Adopted from `tests/test_9397_about_edge_properties.py::_ensure_commit`, and
    for the same reason: without it the `base` half of `RESOLUTIONS` never runs
    in CI. The `test` job checks out with `actions/checkout`'s default
    `fetch-depth: 1`, a depth-1 PR merge ref contains no ancestors, `git show
    PRE_FIX_REVISION:...` fails, and the leg SKIPS — a green run that verifies
    nothing, which is exactly the record class (a base figure recalled from
    another app's lock) the leg exists to stop.

    ⛔ A non-shallow clone is NEVER touched: `git fetch --depth=N` on a full
    clone converts it to shallow and truncates its history (measured in #9397:
    6106 -> 120 commits, `.git/shallow` created). So the fetch is gated on
    `_is_shallow()` FIRST; a full clone that somehow lacks the rev returns False
    and the caller skips honestly.

    Memoised in `_ENSURE_COMMIT_RESULTS`, so the eight readers cost one ladder.
    """
    if rev in _ENSURE_COMMIT_RESULTS:
        return _ENSURE_COMMIT_RESULTS[rev]
    if _has_commit(rev):
        _ENSURE_COMMIT_RESULTS[rev] = True
        return True
    if not _is_shallow():
        _ENSURE_COMMIT_RESULTS[rev] = False
        return False
    for depth in _SHALLOW_DEPTHS:
        try:
            subprocess.run(
                [
                    "git", "-C", str(ROOT), "fetch", "--quiet",
                    f"--depth={depth}", "origin", "main",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            _ENSURE_COMMIT_RESULTS[rev] = False
            return False
        if _has_commit(rev):
            _ENSURE_COMMIT_RESULTS[rev] = True
            return True
    _ENSURE_COMMIT_RESULTS[rev] = False
    return False


def _resolved_versions_at_revision(
    slug: str, package: str, revision: str
) -> list[str] | None:
    """Every resolved version of `package` in the app's lock AT a git revision.

    Returns None — never an empty list — when git or the revision is
    unavailable: an unreadable history is not evidence that the lock resolved no
    copies, and reporting `[]` would let a wrong recorded figure pass as
    "nothing to compare" (the measured-absence-from-an-unreadable-source trap).
    """
    _ensure_commit(revision)
    try:
        done = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "show",
                f"{revision}:{slug}/package-lock.json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):  # no git binary
        return None
    if done.returncode != 0:
        return None
    return _versions_in(json.loads(done.stdout)["packages"], package)


@pytest.mark.parametrize("slug", sorted(SPAS))
def test_every_override_names_what_it_clears(slug: str) -> None:
    """An override nobody can justify is an unlabelled security claim (I1)."""
    unexplained = [pkg for pkg in _overrides(slug) if pkg not in RECORDED_OVERRIDES]
    assert not unexplained, (
        f"{slug} declares overrides that record no mitigation: {unexplained}. "
        "Either state what the floor clears in RECORDED_OVERRIDES above, or "
        "remove the entry — an override that clears nothing reads as a fix to "
        "every later reader (PR #9524 review I1)."
    )


@pytest.mark.parametrize("slug", sorted(SPAS))
def test_no_retired_no_op_override_is_reintroduced(slug: str) -> None:
    """The five entries removed for I1 must not come back as apparent fixes."""
    reintroduced = sorted(set(_overrides(slug)) & set(RETIRED_NO_OPS))
    assert not reintroduced, (
        f"{slug} re-declares retired no-op overrides "
        f"{[(p, RETIRED_NO_OPS[p]) for p in reintroduced]}. They were removed "
        "because they clear nothing; re-adding one restores the false record "
        "(PR #9524 review I1)."
    )


@pytest.mark.parametrize("slug", sorted(SPAS))
def test_shared_transitive_floors_are_declared(slug: str) -> None:
    """Parity: a shared floor lives in the manifest, not only in a lock (I3)."""
    declared = _overrides(slug)
    missing = [pkg for pkg in SHARED_TRANSITIVE_FLOORS if pkg not in declared]
    assert not missing, (
        f"{slug} declares no override for {missing}. Its audit may be 0 today "
        "purely because the lock happens to resolve above the fixed version — "
        "nothing then stops a future lock refresh from walking it back below "
        "that version (PR #9524 review I3)."
    )


@pytest.mark.parametrize("package", sorted(RESOLUTIONS))
def test_recorded_head_resolution_matches_the_committed_lock(package: str) -> None:
    """The `head` half of RESOLUTIONS is measured, not remembered."""
    for slug, (_base, head) in sorted(RESOLUTIONS[package].items()):
        measured = _resolved_versions(slug, package)
        assert sorted(measured) == sorted(head), (
            f"{slug}'s lock resolves {package} as {sorted(measured)}, but the "
            f"record says {sorted(head)} — re-measure and correct the record "
            "(the number is the point of the record, not decoration)."
        )


@pytest.mark.parametrize("package", sorted(RESOLUTIONS))
def test_recorded_pre_fix_resolution_matches_the_pre_fix_lock(package: str) -> None:
    """The `base` half is per-APP: a figure carried over from another app reds here.

    This is the round-3 finding: `nanoid`'s dashboard base was recorded as
    `3.3.16` (graph-viz's) when the dashboard lock resolved `3.3.17`.
    """
    for slug, (base, _head) in sorted(RESOLUTIONS[package].items()):
        measured = _resolved_versions_at_revision(slug, package, PRE_FIX_REVISION)
        if measured is None:
            pytest.skip(
                f"git cannot read {PRE_FIX_REVISION}:{slug}/package-lock.json "
                "even after the bounded `_ensure_commit` fetch (no git binary, "
                "no network, or a full clone that lacks the revision) — the "
                f"recorded base {sorted(base)} for {package} is unverified "
                "here; `git fetch --unshallow` verifies it"
            )
        assert sorted(measured) == sorted(base), (
            f"{slug}'s pre-fix lock ({PRE_FIX_REVISION[:9]}) resolved {package} "
            f"as {sorted(measured)}, but the record says {sorted(base)}. A "
            "figure recalled from another app's lock is how round 3 got "
            "nanoid/dashboard wrong (PR #9524 review)."
        )


def test_no_recorded_override_is_stale() -> None:
    """Every record above must still describe something a SPA declares."""
    declared = {pkg for slug in SPAS for pkg in _overrides(slug)}
    stale = sorted(set(RECORDED_OVERRIDES) - declared)
    assert not stale, (
        f"RECORDED_OVERRIDES documents overrides no SPA declares: {stale}. Drop "
        "the record with the override, or the file becomes a second, drifting "
        "source of truth for the override set."
    )


def test_every_recorded_override_has_a_measured_resolution() -> None:
    """A record with no MEASURED figure behind it is how a wrong number survives."""
    unmeasured = sorted(set(RECORDED_OVERRIDES) - set(RESOLUTIONS))
    assert not unmeasured, (
        f"RECORDED_OVERRIDES claims a mitigation for {unmeasured}, but "
        "RESOLUTIONS holds no measured base/head pair for them, so the numbers "
        "in that prose are unchecked. Add the per-app resolutions to "
        "RESOLUTIONS."
    )


@pytest.mark.parametrize("slug", sorted(SPAS))
def test_every_declared_floor_is_met_by_the_committed_lock(slug: str) -> None:
    """The floor and the resolution cannot drift apart."""
    for package, range_str in sorted(_overrides(slug).items()):
        floor = _floor(range_str)
        resolved = _resolved_versions(slug, package)
        assert resolved, (
            f"{slug} overrides {package!r} but its lock resolves no copy of it — "
            "the override binds nothing"
        )
        for version in resolved:
            parts = tuple(int(part) for part in version.split(".")[:3])
            assert parts >= floor, (
                f"{slug}: {package} resolves {version} in package-lock.json, "
                f"below the {range_str} floor declared in package.json — "
                "re-run the install so the fix is actually shipped"
            )
            assert parts[0] == floor[0], (
                f"{slug}: {package} resolves {version}, outside the {range_str} "
                "floor's major — a caret floor cannot express a cross-major pin"
            )


@pytest.mark.parametrize("package", sorted(DIRECT_DEPENDENCY_FLOORS))
def test_direct_dependency_floors_clear_their_advisory(package: str) -> None:
    """A DIRECT dependency's declared range must not admit the bad version.

    Red before the round-5 fix: `apps/graph-viz` declared `vite: ^8.1.1`, whose
    floor (8.1.1) is below the recorded fixed version (8.3.5) and admits the
    advisory-named 8.1.5 — so a from-scratch resolution could walk that app back
    on to the advisory while every other leg stayed green, because the audit read
    0 off the committed LOCK, not off any declared intent (PR #9524 review,
    round 5 ISSUE 1).
    """
    for slug, fixed in sorted(DIRECT_DEPENDENCY_FLOORS[package].items()):
        declared = _declared_range(slug, package)
        assert declared, (
            f"{slug} no longer declares {package!r}, so the fixed version this "
            "record carries has nothing to hold — drop the record with the "
            "dependency, or restore the dependency"
        )
        floor = _floor(declared)
        assert floor >= fixed, (
            f"{slug} declares {package} {declared!r}, whose floor "
            f"{'.'.join(str(part) for part in floor)} is below the fixed "
            f"version {'.'.join(str(part) for part in fixed)}: a from-scratch "
            "resolution may pick the advisory-named version again (PR #9524 "
            "review, round 5 ISSUE 1)"
        )
        resolved = _resolved_versions(slug, package)
        assert resolved, (
            f"{slug} declares {package!r} but its lock resolves no copy of it — "
            "the declared floor binds nothing"
        )
        for version in resolved:
            parts = tuple(int(part) for part in version.split(".")[:3])
            assert parts >= fixed, (
                f"{slug}: {package} resolves {version} in package-lock.json, "
                f"below the fixed {'.'.join(str(part) for part in fixed)} — "
                "re-run the install so the fix is actually shipped"
            )


_MANIFEST = ROOT / "config/ci-surfaces.yml"


def _flat(text: str) -> str:
    """Marker-insensitive, single-spaced, lower-case form of a claim.

    `**no network**` and `no network` are the SAME claim; matching raw
    substrings is how a pin becomes dead. Emphasis and code markers are
    decoration on a claim, not part of it.
    """
    return _WHITESPACE_RE.sub(" ", _MARKUP_RE.sub("", text)).strip().lower()


def _comment_block_above(lines: list[str], index: int) -> str:
    """The contiguous `#` comment block directly above `lines[index]`."""
    block: list[str] = []
    for position in range(index - 1, -1, -1):
        if not lines[position].lstrip().startswith("#"):
            break
        block.append(lines[position])
    return "\n".join(reversed(block))


def test_the_manifest_states_the_fetch_it_cannot_hide() -> None:
    """`config/ci-surfaces.yml` may not call this suite network-free.

    The manifest is the record a later lane reads to decide whether a fast-lane
    file is hermetic and how much to weight it in a shard. All three of its
    notes for this file said "no network" — and the `durations` row concluded
    that the flat default OVER-estimates — while `_ensure_commit` runs a
    `git fetch` on exactly the checkout shape CI uses (`fetch-depth: 1`), so
    both claims were inverted for the lane that matters (round 5 ISSUE 2; the
    sibling `test_9397_about_edge_properties.py` pins the same property for the
    file this helper was adopted from).

    Re-derived from the SOURCE and from the manifest text, so the record can
    only say what the code does — and it reds if the fetch is removed without
    the records being corrected with it.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    assert '"fetch"' in src and "--depth=" in src, (
        "`_ensure_commit` no longer fetches `origin main` — re-derive this "
        "pin's premise before trusting the manifest notes it guards"
    )
    manifest_lines = _MANIFEST.read_text(encoding="utf-8").splitlines()
    registrations = [
        index
        for index, line in enumerate(manifest_lines)
        if line.strip() == "- test_spa_dependency_overrides.py"
    ]
    assert registrations, (
        "this file is not registered in config/ci-surfaces.yml at all, so the "
        "surface records this leg measures are gone"
    )
    for line_number in registrations:
        block = _flat(_comment_block_above(manifest_lines, line_number))
        assert block, (
            f"the registration at config/ci-surfaces.yml:{line_number + 1} "
            "carries no rationale comment — nothing then states the fetch it "
            "performs"
        )
        assert "git fetch" in block, (
            f"the registration comment above config/ci-surfaces.yml:"
            f"{line_number + 1} does not name the `git fetch --depth=… origin "
            "main` this file performs on a shallow checkout, so the record "
            "again reads as a hermetic suite (PR #9524 review, round 5 ISSUE 2)"
        )
        for claim in _RETRACTED_NETWORK_FREE:
            assert claim not in block, (
                f"the registration comment above config/ci-surfaces.yml:"
                f"{line_number + 1} claims {claim!r} while `_ensure_commit` runs "
                "`git fetch --depth=… origin main` (PR #9524 review, round 5 "
                "ISSUE 2)"
            )

    duration_rows = [
        line
        for line in manifest_lines
        if line.strip().startswith("test_spa_dependency_overrides.py:")
    ]
    assert len(duration_rows) == 1, (
        f"expected exactly one `durations` row for this file, found "
        f"{len(duration_rows)} — the weight the shard packer reads is ambiguous"
    )
    row = _flat(duration_rows[0])
    assert "git fetch" in row, (
        "the `durations` row does not name the bounded `git fetch` this file "
        "performs in a shallow checkout, so its weight reads as a plain "
        "JSON-parse cost (PR #9524 review, round 5 ISSUE 2)"
    )
    for claim in _RETRACTED_NETWORK_FREE:
        assert claim not in row, (
            f"the `durations` row claims {claim!r} while the file fetches on the "
            "CI checkout shape — the weight justification is then the inverse "
            "of what the code does (PR #9524 review, round 5 ISSUE 2)"
        )


def test_the_fetch_ladder_runs_at_most_once_per_revision(monkeypatch) -> None:
    """Eight readers, ONE ladder — the memo the `durations` bound rests on.

    `_resolved_versions_at_revision` calls `_ensure_commit` once per (package,
    app) pair, eight times. Unmemoised, the unreachable case re-runs the
    three-depth ladder each time: up to 24 fetches at a 120 s timeout before the
    four `base` cases skip, which is what made the manifest's small weight an
    assertion rather than a bound (round 5 ISSUE 2).
    """
    module = sys.modules[__name__]
    fetches: list[list[str]] = []

    def _counting_run(args, *rest, **kwargs):
        fetches.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(module, "_has_commit", lambda rev: False)
    monkeypatch.setattr(module, "_is_shallow", lambda: True)
    monkeypatch.setattr(module, "_ENSURE_COMMIT_RESULTS", {})
    monkeypatch.setattr(module.subprocess, "run", _counting_run)

    for _ in range(len(RESOLUTIONS) * len(SPAS)):  # the per-pair reader count
        assert module._ensure_commit(PRE_FIX_REVISION) is False
    assert len(fetches) == len(_SHALLOW_DEPTHS), (
        f"`_ensure_commit` ran {len(fetches)} fetches across "
        f"{len(RESOLUTIONS) * len(SPAS)} calls; memoised it must run one ladder "
        f"({len(_SHALLOW_DEPTHS)} fetches) at most — unmemoised it repeated the "
        "ladder on every call and the unreachable case could spend 24 fetch "
        "timeouts before the base half skips (PR #9524 review, round 5 ISSUE 2)"
    )


def test_the_base_half_is_reachable_in_a_shallow_checkout() -> None:
    """The base-half leg must TRY to reach `PRE_FIX_REVISION`, not skip first.

    Structural on purpose. In CI the `test` job checks out at
    `actions/checkout`'s default `fetch-depth: 1`, so before this pin the leg
    called `git show 69390341f:...`, got a non-zero exit, and SKIPPED — all four
    `base` cases dark on every PR run, inside an otherwise green shard, with
    nothing red (`tools/skip-guard.py` only guards the live-FalkorDB reason
    family). A skip is indistinguishable from a pass in the summary line, so it
    has to be pinned structurally: the behaviour it replaces is only reachable
    in a shallow clone (PR #9524 review, round 3 I3 / round 4 ISSUE 1).

    Red before the fix: `_ensure_commit` / `_is_shallow` / `_SHALLOW_DEPTHS` did
    not exist in this file at all, so the first assertion below fails.
    """
    module = ast.parse(
        (ROOT / "tests/test_spa_dependency_overrides.py").read_text(encoding="utf-8")
    )
    funcs = {
        node.name: node for node in module.body if isinstance(node, ast.FunctionDef)
    }
    assert {"_ensure_commit", "_is_shallow", "_has_commit"} <= set(funcs), (
        "the shallow-checkout helpers are gone: the base half of RESOLUTIONS is "
        "back to a bare `git show` + `pytest.skip`, i.e. unverified on every CI "
        "run (PR #9524 review, round 4 ISSUE 1)"
    )

    # The base-half leg must CALL the helper, not merely have it in the file.
    reader = funcs["_resolved_versions_at_revision"]
    assert "_ensure_commit" in ast.dump(reader), (
        "`_resolved_versions_at_revision` no longer calls `_ensure_commit`, so a "
        "shallow clone cannot reach PRE_FIX_REVISION and the base half skips "
        "green again"
    )

    # `git fetch --depth=N` TRUNCATES a full clone (measured in #9397: 6106 ->
    # 120 commits, `.git/shallow` created), so the `_is_shallow` gate must come
    # BEFORE the fetch loop — order is what is pinned, not intent.
    body = funcs["_ensure_commit"].body
    gate_at = next(
        (
            index
            for index, node in enumerate(body)
            if isinstance(node, ast.If)
            and "_is_shallow" in ast.dump(node.test)
            and any(
                isinstance(inner, ast.Return)
                and isinstance(inner.value, ast.Constant)
                and inner.value.value is False
                for inner in node.body
            )
        ),
        None,
    )
    fetch_at = next(
        (
            index
            for index, node in enumerate(body)
            if isinstance(node, ast.For) and "_SHALLOW_DEPTHS" in ast.dump(node.iter)
        ),
        None,
    )
    assert gate_at is not None, (
        "`_ensure_commit` no longer returns False on a NON-shallow clone, so the "
        "fetch would shallow (and truncate) a developer's full clone"
    )
    # The MEMO must be consulted BEFORE the ladder: round 5 ISSUE 2 measured that
    # the eight per-(package, app) readers each re-ran the ladder (up to 24
    # fetches), which is why the manifest's weight could not be stated as a bound.
    memo_at = next(
        (
            index
            for index, node in enumerate(body)
            if isinstance(node, ast.If)
            and "_ENSURE_COMMIT_RESULTS" in ast.dump(node.test)
        ),
        None,
    )
    assert memo_at is not None, (
        "`_ensure_commit` no longer memoises, so the `RESOLUTIONS` readers re-run "
        "the fetch ladder (up to 24 fetches before the base half skips) — the "
        "bound the manifest's `durations` note states is then false (PR #9524 "
        "review, round 5 ISSUE 2)"
    )
    assert fetch_at is None or memo_at < fetch_at, (
        f"the memo is consulted at statement {memo_at} but the fetch ladder runs "
        f"at {fetch_at}: a memo read AFTER the ladder saves nothing"
    )
    assert fetch_at is not None, (
        "`_ensure_commit` has no bounded `_SHALLOW_DEPTHS` fetch loop, so it "
        "cannot make PRE_FIX_REVISION reachable in CI"
    )
    assert gate_at < fetch_at, (
        f"the `_is_shallow` gate is statement {gate_at} and the fetch loop "
        f"{fetch_at}: the gate must come FIRST or the fetch runs on a full clone"
    )
    assert "is-shallow-repository" in ast.dump(funcs["_is_shallow"]), (
        "`_is_shallow` no longer asks git `--is-shallow-repository`, so the gate "
        "above does not mean what it assumes"
    )


def _manifest_at_revision(slug: str, revision: str) -> dict | None:
    """The app's `package.json` AT a git revision, or None where git cannot read it.

    `None` — never `{}` — for the reason `_resolved_versions_at_revision` gives:
    a manifest that could not be read is not a manifest that declares no
    `overrides`, and reporting the latter would make the non-vacuity experiment
    below pass on an unreadable history.
    """
    _ensure_commit(revision)
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{revision}:{slug}/package.json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):  # no git binary
        return None
    if done.returncode != 0:
        return None
    return json.loads(done.stdout)


def _cases_of(func) -> list[tuple[str, tuple]]:
    """`(pytest id suffix, args)` for every case `func` contributes.

    The suffix is what a summary line prints after the function name (`[vite]`,
    `[apps/graph-viz]`), so a record keyed by case ID is a record of the run.
    This file parametrises ONE argument per leg; anything richer raises rather
    than guessing at pytest's id synthesis.
    """
    marks = [mark for mark in getattr(func, "pytestmark", ()) if mark.name == "parametrize"]
    if not marks:
        return [("", ())]
    assert len(marks) == 1, (
        f"{func.__name__} stacks parametrisations — teach `_cases_of` the id "
        "synthesis before trusting a count derived from it"
    )
    names, values = marks[0].args
    assert isinstance(names, str), (
        f"{func.__name__} parametrises several arguments at once — the case id is "
        "then synthetic (`a-b`), and a record built from this helper would not "
        "read like a summary line"
    )
    return [(f"[{value}]", (value,)) for value in values]


def test_the_recorded_case_count_matches_the_shipped_file() -> None:
    """The count a record quotes is `pytest`'s collection, never a recollection.

    Round 6 of the #9524 review: the PR body said "23 cases" for a file that
    collects 26 — the 23 described the pre-`RESOLUTIONS` revision. Counting from
    the module's own test functions and their parametrisations means a leg cannot
    be added without the figure moving, so the stale copy reds instead of shipping
    (the same record discipline as `RESOLUTIONS` and the manifest pins above).
    """
    module = sys.modules[__name__]
    counted = {
        name: len(_cases_of(obj))
        for name, obj in sorted(vars(module).items())
        if name.startswith("test_") and callable(obj)
    }
    total = sum(counted.values())
    assert total == RECORDED_CASE_COUNT, (
        f"this file collects {total} cases ({counted}), but RECORDED_CASE_COUNT "
        f"says {RECORDED_CASE_COUNT}. Re-measure with `pytest --collect-only -q "
        "tests/test_spa_dependency_overrides.py` and correct the record: a count "
        "carried in prose is exactly what went stale in round 6 (PR #9524 review, "
        "ISSUE 1)"
    )


def test_the_recorded_non_vacuity_experiment_matches_the_shipped_file(
    monkeypatch,
) -> None:
    """RUN the non-vacuity experiment instead of describing it (round 6, ISSUE 2).

    The record has now been wrong three ways in prose: round 3's "3 failed" (the
    13-case file), round 6's "4 failed" (true only before the round-5 `vite` floor
    leg existed) and round 6's "7 failed" (arms a summary line never prints — the
    app loop lives INSIDE one `[vite]` case). So the experiment is code: restore
    `PRE_FIX_REVISION`'s MANIFESTS over this tree's locks (the locks stay as
    shipped — a manifest-only diff is the state under review), call the
    manifest-reading legs for real, and compare the failures observed with
    `RECORDED_NON_VACUITY_REDS`.

    The legs are CALLED, not re-implemented, so a leg whose base-tree behaviour
    changes moves this record with it; the two buckets above are what keep a NEW
    manifest-reading leg from being invisible to the experiment.
    """
    module = sys.modules[__name__]
    defined = {
        name
        for name, obj in vars(module).items()
        if name.startswith("test_") and callable(obj)
    }
    bucketed = list(_MANIFEST_READING_LEGS) + list(_LOCK_OR_STRUCTURE_LEGS)
    assert sorted(bucketed) == sorted(defined), (
        "every test in this file must sit in exactly one of the two buckets, so a "
        f"manifest-reading leg cannot skip the experiment. Unbucketed: "
        f"{sorted(defined - set(bucketed))}; bucketed but absent: "
        f"{sorted(set(bucketed) - defined)}; declared twice: "
        f"{sorted({name for name in bucketed if bucketed.count(name) > 1})}"
    )

    base = {slug: _manifest_at_revision(slug, PRE_FIX_REVISION) for slug in sorted(SPAS)}
    if any(manifest is None for manifest in base.values()):
        pytest.skip(
            f"git cannot read {PRE_FIX_REVISION}'s app manifests even after the "
            "bounded `_ensure_commit` fetch (no git binary, no network, or a full "
            "clone that lacks the revision) — the non-vacuity record is "
            "unverified here; `git fetch --unshallow` verifies it"
        )
    monkeypatch.setattr(module, "_manifest", lambda slug: base[slug])

    observed: list[str] = []
    for name in _MANIFEST_READING_LEGS:
        func = getattr(module, name)
        for suffix, args in _cases_of(func):
            try:
                func(*args)
            except AssertionError:
                observed.append(f"{name}{suffix}")

    assert sorted(observed) == sorted(RECORDED_NON_VACUITY_REDS), (
        f"with PRE_FIX_REVISION's manifests over this tree's locks the "
        f"manifest-reading legs fail {sorted(observed)}, but the record says "
        f"{sorted(RECORDED_NON_VACUITY_REDS)}. Correct the record from a run, not "
        "from reasoning: round 6 quoted a red set belonging to an earlier "
        "revision of this file (PR #9524 review, ISSUE 2)"
    )
