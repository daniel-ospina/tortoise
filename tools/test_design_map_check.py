#!/usr/bin/env python3
"""Check epic #5090's integration-surface map against its own rules (#11084).

**What this is.** `docs/epics/2026-11-04-5090-vector-layer/TEST-DESIGN-surface-map.md`
is the Test-Design artifact that epic #5090 never had. Its consumer is the
capstone (**#11089**), whose checklist *is* "walk every surface through its
assigned layer". A checklist read by a human can shrink silently: delete a row,
blank a layer cell, or drop a child, and the capstone still looks complete. This
tool is what makes that impossible — it parses the map and refuses when the map
stops being a map.

**It checks the document, not the product.** No imports of `tortoise`, no
database, no network: the whole point is that #11089 can run it at the closing
head cost-free, and that any lane editing the map gets an answer in ~0.1 s.

Rules (the map's own §8 states them; this file is the enforcement):

* **R1** every capability `S1`–`S5` names at least one surface; every surface
  named in the capability or child tables has a row in the surface table; and
  the surface POPULATION is pinned `SF1`–`SF15` **both ways** — a row deleted
  together with every mention of it passes every referential check (measured),
  so only a pin catches the consistent shrink.
* **R2** every surface row's layer is non-empty and drawn only from
  `{unit, contract, live-docker, doc-assert, guard}`.
* **R3** every surface row states a failure in at least `MIN_FAILURE_WORDS`
  words — a restated mechanism is not a named failure.
* **R4** every surface row is touched by at least one child (the issue's
  "no surface is listed that no child touches"), and every child `#11084`–`#11089`
  appears in the child table.
* **R5** every seam named by `04-plan.md` §5–§6 appears in the crosswalk.

**Exit codes:** `0` clean · `1` violations (printed, one per line) · `2` the map
could not be read or a required section is missing (an unparseable map is not a
clean one — same posture as the census's unreadable-catalogue refusal).

Usage:
    uv run python tools/test_design_map_check.py
    uv run python tools/test_design_map_check.py --doc path/to/map.md --json
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # #4053: this is an ENVIRONMENT failure, not a map verdict. `SystemExit(<str>)`
    # exits 1, and this tool's contract gives 1 to "violations found" (see the
    # docstring: `0` clean · `1` violations · `2` unreadable) — so a check that
    # never read the map would report "the map has violations", and a lane would
    # hunt violations that do not exist. Print the message on stderr and exit 2,
    # the code `main()` already gives a `Refusal` (docstring: "the document could
    # not be parsed — exit 2, never a silent pass").
    print(
        f"tools/test_design_map_check.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/test_design_map_check.py …`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOC = (
    REPO_ROOT
    / "docs"
    / "epics"
    / "2026-11-04-5090-vector-layer"
    / "TEST-DESIGN-surface-map.md"
)

#: The layers a surface may be assigned. "none" is deliberately absent: the
#: skill's rule is that an *identified* surface always has a layer, and a surface
#: with no layer is a surface nobody will test.
ALLOWED_LAYERS = frozenset({"unit", "contract", "live-docker", "doc-assert", "guard"})

#: A failure cell below this many words is almost always the mechanism restated
#: ("it uses db.indexes()"). The map's contract is a consequence, not a verb.
MIN_FAILURE_WORDS = 8

#: The scope items, the children the decomposition names, and the surface
#: population. All three are pinned rather than discovered, so deleting one from
#: the map is a violation instead of a smaller map. The surface pin is what stops
#: the CONSISTENT shrink — delete a row and every mention of it and the
#: referential checks all still pass (measured: exactly that on SF13 exited 0
#: before the pin existed).
CAPABILITIES = ("S1", "S2", "S3", "S4", "S5")
CHILD_ISSUES = ("11084", "11085", "11086", "11087", "11088", "11089")
SURFACES = tuple(f"SF{n}" for n in range(1, 16))

#: The seams `04-plan.md` names in §5 and §6, as the crosswalk must carry them.
#: Keyed by the token the crosswalk's first column uses.
PLAN_SEAMS = (
    "§5 row 1",
    "§5 row 2",
    "§5 row 3",
    "§5 row 4",
    "§5 row 5",
    "§6.1",
    "§6.2",
    "§6.3",
    "§6.4",
)

SF_ROW = re.compile(r"^\|\s*\*\*(SF\d+)\*\*\s*\|")
SF_ANY = re.compile(r"\bSF(\d+)\b")
SECTION = re.compile(r"^##\s+(\d+)\.\s", re.MULTILINE)


class Refusal(Exception):
    """The document could not be parsed — exit 2, never a silent pass."""


def read_doc(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # missing, unreadable, a directory — all exit 2
        raise Refusal(f"cannot read the surface map at {path}: {exc}") from exc


def split_sections(text: str) -> dict[str, str]:
    """Split on `## <n>.` headings. Returns {"1": body, ...}."""
    marks = list(SECTION.finditer(text))
    if not marks:
        raise Refusal("no `## <n>.` sections found — the map's structure is gone")
    sections: dict[str, str] = {}
    for i, match in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        sections[match.group(1)] = text[match.start() : end]
    return sections


def cells(line: str) -> list[str]:
    """Split a markdown table row into trimmed cells."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_surface_rows(section: str) -> dict[str, dict[str, str]]:
    """Parse the surface table.

    Column order is the map's own header:
    `| SF | surface | type | flow | layer | contract | failure |`
    """
    rows: dict[str, dict[str, str]] = {}
    for line in section.splitlines():
        match = SF_ROW.match(line)
        if not match:
            continue
        row = cells(line)
        if len(row) < 7:
            raise Refusal(
                f"{match.group(1)} has {len(row)} column(s); the table needs 7 "
                "(SF · surface · type · flow · layer · contract · failure)"
            )
        sf = match.group(1)
        if sf in rows:
            raise Refusal(f"{sf} appears twice in the surface table")
        rows[sf] = {
            "surface": row[1].replace("**", "").strip(),
            "layer": row[4].replace("**", "").strip(),
            "contract": row[5],
            "failure": row[6],
        }
    if not rows:
        raise Refusal("no `| **SF<n>** |` rows found — the surface table is empty")
    return rows


def layer_tokens(cell: str) -> list[str]:
    """`unit + doc-assert` / `unit (negative) + guard` → ["unit", "guard"].

    A parenthetical is a QUALIFIER on the layer ("(negative)", "(skip-if-
    unavailable)"), not a second layer — it is dropped before splitting so the
    allowed-set test stays strict about the layer name itself.
    """
    stripped = re.sub(r"\([^)]*\)", " ", cell.replace("**", ""))
    tokens = [t.strip() for t in re.split(r"[+,/]| and ", stripped)]
    return [t for t in tokens if t]


def surface_tokens(text: str) -> set[str]:
    return {f"SF{n}" for n in SF_ANY.findall(text)}


def check(text: str) -> tuple[list[str], dict[str, object]]:
    """Return (violations, stats). Never raises for a content violation."""
    sections = split_sections(text)
    for needed in ("1", "3", "4", "5"):
        if needed not in sections:
            raise Refusal(f"section `## {needed}.` is missing — cannot check the map")

    violations: list[str] = []
    surfaces = parse_surface_rows(sections["1"])

    # R1a — the surface POPULATION is pinned: a row plus every mention of it can
    # be deleted consistently, and every cross-reference below would still
    # resolve. Only the pin catches that. Both directions: a missing pinned
    # surface, and a surface nobody pinned.
    for sf in SURFACES:
        if sf not in surfaces:
            violations.append(f"R1 {sf}: pinned surface is missing from the surface table")
    for sf in sorted(set(surfaces) - set(SURFACES)):
        violations.append(f"R1 {sf}: surface is not in the pinned population {SURFACES[0]}..{SURFACES[-1]}")

    # R2 / R3 — layers and named failures, per surface row.
    for sf, row in sorted(surfaces.items()):
        tokens = layer_tokens(row["layer"])
        if not tokens:
            violations.append(f"R2 {sf}: no test layer assigned")
        for token in tokens:
            if token not in ALLOWED_LAYERS:
                violations.append(
                    f"R2 {sf}: layer {token!r} is not one of {sorted(ALLOWED_LAYERS)}"
                )
        failure = row["failure"].replace("**", "").strip()
        words = len(failure.split())
        if words < MIN_FAILURE_WORDS:
            violations.append(
                f"R3 {sf}: failure cell is {words} word(s) (min {MIN_FAILURE_WORDS}) — "
                "a restated mechanism is not a named failure"
            )

    # R1 — capabilities → surfaces, both directions.
    capability_section = sections["3"]
    for capability in CAPABILITIES:
        row = next(
            (
                line
                for line in capability_section.splitlines()
                if re.match(rf"^\|\s*\*\*{capability}\*\*", line)
            ),
            None,
        )
        if row is None:
            violations.append(f"R1 {capability}: no row in the capability table")
            continue
        named = surface_tokens(row)
        if not named:
            violations.append(f"R1 {capability}: names no surface")
        for sf in sorted(named - set(surfaces)):
            violations.append(f"R1 {capability}: names {sf}, which has no surface row")

    # R4 — every surface touched by ≥1 child; every child present.
    child_section = sections["4"]
    touched: set[str] = set()
    for issue in CHILD_ISSUES:
        row = next(
            (
                line
                for line in child_section.splitlines()
                if re.match(rf"^\|\s*\*\*C\d\*\*\s*\|\s*\*\*#{issue}\*\*", line)
            ),
            None,
        )
        if row is None:
            violations.append(f"R4 #{issue}: no row in the child table")
            continue
        named = surface_tokens(row)
        if not named:
            violations.append(f"R4 #{issue}: touches no surface")
        for sf in sorted(named - set(surfaces)):
            violations.append(f"R4 #{issue}: names {sf}, which has no surface row")
        touched |= named
    for sf in sorted(set(surfaces) - touched):
        violations.append(
            f"R4 {sf}: no child touches it — the issue forbids a surface no child touches"
        )

    # R5 — the plan's seams are all crosswalked.
    crosswalk = sections["5"]
    present = {
        seam
        for seam in PLAN_SEAMS
        if re.search(r"^\|\s*" + re.escape(seam), crosswalk, re.MULTILINE)
    }
    for seam in PLAN_SEAMS:
        if seam not in present:
            violations.append(f"R5 {seam}: missing from the §5/§6 crosswalk")

    stats: dict[str, object] = {
        "surfaces": len(surfaces),
        "layers": sorted({t for row in surfaces.values() for t in layer_tokens(row["layer"])}),
        "capabilities": len(CAPABILITIES),
        "children": len(CHILD_ISSUES),
        "plan_seams": len(present),
        "violations": len(violations),
    }
    return violations, stats


def _rewrite_row(text: str, sf: str, column: int, value: str) -> str:
    """Replace one cell of one surface row — the mutation primitive."""
    out: list[str] = []
    hit = False
    for line in text.splitlines(keepends=True):
        if line.startswith(f"| **{sf}**"):
            row = line.strip().strip("|").split("|")
            row[column] = f" {value} "
            line = "|" + "|".join(row) + "|\n"
            hit = True
        out.append(line)
    if not hit:
        raise AssertionError(f"self-test mutation target {sf} not found")
    return "".join(out)


def _drop_line(text: str, prefix: str) -> str:
    """Delete every line starting with `prefix` — the removal primitive."""
    kept = [line for line in text.splitlines(keepends=True) if not line.startswith(prefix)]
    if len(kept) == len(text.splitlines(keepends=True)):
        raise AssertionError(f"self-test mutation target {prefix!r} not found")
    return "".join(kept)


#: Each entry is `(name, mutator, expected)`, where `expected` is `"violation"`
#: (a rule fires, exit 1) or `"refusal"` (the map no longer parses, exit 2).
#: These are the deletions an editor actually makes — remove a row, blank a cell,
#: drop a child, forget a seam — and a checker that stayed green through any of
#: them would be a rubber stamp. Run with `--self-test`.
SELF_TEST_MUTATIONS: tuple[tuple[str, object, str], ...] = (
    (
        "delete the SF13 row (the surface-change surface)",
        lambda t: _drop_line(t, "| **SF13**"),
        "violation",
    ),
    (
        "blank SF10's layer (the new state S4 exists for)",
        lambda t: _rewrite_row(t, "SF10", 4, ""),
        "violation",
    ),
    (
        "drop the #11088 child row (SF11/SF13 become orphans)",
        lambda t: _drop_line(t, "| **C5**"),
        "violation",
    ),
    (
        "delete the S3 capability row",
        lambda t: _drop_line(t, "| **S3**"),
        "violation",
    ),
    (
        "drop the §6.4 surface-statement seam from the crosswalk",
        lambda t: _drop_line(t, "| §6.4"),
        "violation",
    ),
    (
        "restate SF2's layer as a mechanism name",
        lambda t: _rewrite_row(t, "SF2", 4, "encode_for_row"),
        "violation",
    ),
    (
        "shorten SF2's failure to a mechanism restatement",
        lambda t: _rewrite_row(t, "SF2", 6, "it calls encode_for_row"),
        "violation",
    ),
    (
        "delete the SF13 row AND every mention of it (a consistent shrink)",
        lambda t: _drop_line(t, "| **SF13**").replace("SF13", "SF1"),
        "violation",
    ),
    (
        "delete the child table's section heading",
        lambda t: t.replace("## 4. Every surface", "### Every surface"),
        "refusal",
    ),
)


def self_test(text: str) -> int:
    """Prove the checker is not a rubber stamp. Prints one line per mutation."""
    failures = 0
    for name, mutate, expected in SELF_TEST_MUTATIONS:
        try:
            mutated = mutate(text)  # type: ignore[operator]
        except AssertionError as exc:
            print(f"  UNTESTABLE {name}: {exc}", file=sys.stderr)
            failures += 1
            continue
        try:
            violations, _stats = check(mutated)
            actual = "violation" if violations else "clean"
            detail = violations[0] if violations else "no rule fired"
        except Refusal as exc:
            actual, detail = "refusal", str(exc)
        if actual == expected:
            print(f"  ok   {name} → {actual} ({detail})")
        else:
            print(
                f"  FAIL {name} → expected {expected}, got {actual} ({detail})",
                file=sys.stderr,
            )
            failures += 1
    total = len(SELF_TEST_MUTATIONS)
    if failures:
        print(f"self-test: {failures}/{total} mutation(s) NOT caught — the checker is a rubber stamp", file=sys.stderr)
        return 1
    print(f"self-test: {total}/{total} mutation(s) refused — the checker is not a rubber stamp")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check epic #5090's integration-surface map against its own rules.",
    )
    parser.add_argument(
        "--doc",
        type=Path,
        default=DEFAULT_DOC,
        help=f"surface map to check (default: {DEFAULT_DOC.relative_to(REPO_ROOT)})",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="apply targeted mutations to the map and require each to be caught",
    )
    args = parser.parse_args(argv)

    try:
        text = read_doc(args.doc)
    except Refusal as exc:
        if args.json:
            print(json.dumps({"ok": False, "refused": str(exc)}))
        else:
            print(f"test-design-map-check: REFUSED — {exc}", file=sys.stderr)
        return 2

    if args.self_test:
        return self_test(text)

    try:
        violations, stats = check(text)
    except Refusal as exc:
        if args.json:
            print(json.dumps({"ok": False, "refused": str(exc)}))
        else:
            print(f"test-design-map-check: REFUSED — {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"ok": not violations, "stats": stats, "violations": violations}))
        return 0 if not violations else 1

    print(
        "test-design-map-check: "
        f"{stats['surfaces']} surface(s) · "
        f"{stats['capabilities']} capabilit(y/ies) · "
        f"{stats['children']} child(ren) · "
        f"{stats['plan_seams']}/{len(PLAN_SEAMS)} plan seam(s) crosswalked · "
        f"layers={','.join(stats['layers'])}"
    )
    if violations:
        for violation in violations:
            print(f"  VIOLATION {violation}", file=sys.stderr)
        print(
            f"test-design-map-check: {len(violations)} violation(s) — the map is not a map",
            file=sys.stderr,
        )
        return 1
    print("test-design-map-check: CLEAN — every rule holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
