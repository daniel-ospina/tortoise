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

So this file pins three properties, all mechanical and offline (no network, no
`npm` binary):

1. every declared override is RECORDED below with what it clears — a new
   override cannot land as an unexplained mitigation (fails on the pre-fix
   tree: blog-admin declared nine, five of which clear nothing);
2. the two shared transitive floors are declared by EVERY SPA — parity, so the
   fix exists in the manifest and not only in a lock resolution (fails on the
   pre-fix tree: graph-viz had no `overrides` block at all);
3. every declared floor is actually met by the committed lock — the floor and
   the resolution cannot drift apart.

The measurements behind the records below were taken on this branch head with
`npm audit --package-lock-only` against each app's `package.json` +
`package-lock.json` (dashboard 5 -> 0; blog-admin 13 -> 5, and the residual five
are one Tailwind-3 chain; apps/graph-viz 3 -> 0). Re-measure before changing a
record: the number is the point of the record, not decoration.
"""

from __future__ import annotations

import json
import re
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

# Every override that may legitimately appear, with WHAT it clears. A package
# whose advisory has no fixed version in the declared major does not belong here
# — it belongs in RETIRED_NO_OPS (finding I1).
RECORDED_OVERRIDES = {
    "nanoid": (
        "GHSA-2v37-7h3g-55p8 (nanoid, fixed 3.3.18): dashboard's lock sat at "
        "3.3.16 and now resolves 3.3.20; blog-admin already resolved 3.3.18, so "
        "there the entry is the FLOOR that keeps it there."
    ),
    "source-map-js": (
        "GHSA-68fv-2mgg-jv7q (source-map-js, fixed 1.2.2): blog-admin "
        "1.2.1 -> 1.2.2, dashboard and graph-viz resolve 1.2.2 under the floor."
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

_FLOOR_RE = re.compile(r"^\^(\d+)\.(\d+)\.(\d+)$")


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


def _resolved_versions(slug: str, package: str) -> list[str]:
    """Every resolved version of `package` in the app's lock, at any depth.

    Nested copies are the ones an override most often exists to remove, so the
    whole `packages` map is walked rather than just the top-level entry.
    """
    packages = _lock(slug)["packages"]
    found = []
    for key, entry in packages.items():
        if not key:
            continue
        if key.rsplit("node_modules/", 1)[-1] == package:
            version = entry.get("version")
            if version:
                found.append(version)
    return found


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


def test_no_recorded_override_is_stale() -> None:
    """Every record above must still describe something a SPA declares."""
    declared = {pkg for slug in SPAS for pkg in _overrides(slug)}
    stale = sorted(set(RECORDED_OVERRIDES) - declared)
    assert not stale, (
        f"RECORDED_OVERRIDES documents overrides no SPA declares: {stale}. Drop "
        "the record with the override, or the file becomes a second, drifting "
        "source of truth for the override set."
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
