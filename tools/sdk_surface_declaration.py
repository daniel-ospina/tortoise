#!/usr/bin/env python3
"""The approved-surface declaration is a GATE, not a comment (#4282 phase 1.1).

WHAT IT CHECKS
    `tortoise/__all__.py` declares three things, and this tool reconciles all three against
    the live class, in **both directions** each:

      1. `__all__` — the **40 approved** SDK names, transcribed from
         `docs/product/beta-sdk-surface.md` rows 1-40. Compared against that document, so
         the declaration cannot be padded, reordered away from, or silently re-derived.
      2. `APPROVED_PENDING` — approved names with no `def` on `TortoiseSDK` yet (the tracked
         gaps). Must equal `approved − live`, exactly: a declared name that vanished and a
         name that was quietly marked "pending" are different defects and are reported
         differently.
      3. `OFF_SURFACE` — the removal backlog: live public methods that are NOT approved.
         Must equal `live − approved`, exactly. This is what makes an **ADDITION** visible.

WHY THE THIRD SET IS THE POINT (#10034)
    A declaration of the 40 alone can only ever say "149 live vs 40 target, over target" —
    which is true of a surface that is half-migrated and equally true of a surface somebody
    added to yesterday. Nothing in that comparison can name an added method. Declaring the
    backlog too makes the two observations distinct, and the gate names the offender:

        live − approved − OFF_SURFACE  ->  an ADDITION that was not approved
        OFF_SURFACE − (live − approved) ->  a removal that the ledger was not decremented for

    The backlog is a statement of what EXISTS off-target, not an approval and not a removal
    decision. It may only SHRINK (`OFF_SURFACE_HIGH_WATER`).

WHY THE PINS (#10034, and the reason both are needed)
    `PENDING_HIGH_WATER` stops the cheapest bypass of all: deleting an approved method and
    marking it "pending" satisfies the set equality above and would otherwise be green.
    `OFF_SURFACE_HIGH_WATER` stops the other: adding a method and listing it in the backlog.
    Both pins may only be LOWERED by a migration that lands; raising one is a deliberate,
    reviewable act in an approving PR. Neither is derivable — they are ceilings on a
    decision, which is the same construction `config/surface-manifest.yml`'s
    `high_water_counts` already uses.

WHAT IT IS NOT
    Not an approval. A green run means the declaration, the document and the class agree —
    it cannot tell an approved addition from an unapproved one, because both look like
    "doc + declaration + code updated together". Daniel's approval is what carries the
    approval (`docs/product/canonical-mcp-tools.md`); this is the invariant that makes the
    surface change VISIBLE, and the reason it can no longer happen by accident.

Usage:
    uv run python tools/sdk_surface_declaration.py check
    uv run python tools/sdk_surface_declaration.py check --declaration path/to/__all__.py
    uv run python tools/sdk_surface_declaration.py show      # the three declared sets

Exit 0 = declaration, document and class agree.
Exit 1 = they disagree, or the evidence cannot be read (a refusal, never a skip).
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below (the D9 shape — a
# version-gated import fails first and blames the wrong thing).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/sdk_surface_declaration.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/sdk_surface_declaration.py check`"
    )

import argparse
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DECLARATION_FILE = ROOT / "tortoise" / "__all__.py"
BETA_DOC = ROOT / "docs" / "product" / "beta-sdk-surface.md"

#: The name under which the SDK constructor is served. `beta-sdk-surface.md` row 1 is
#: written `Tortoise(...)` — the class the caller constructs — while every other row is a
#: method on it. The declaration keeps the document's spelling; this is the ONE mapping
#: from a declared name to the attribute that must exist.
CONSTRUCTOR = "Tortoise"

#: Rows 1-40 of the approved table: `| 3 | \`search_knowledge\` | ... |`. Scoped to the
#: recommended-surface section ON PURPOSE — the document carries other tables, and a
#: whole-document scan would pick up sizes and dates in prose.
_SURFACE_SECTION = ("## The recommended surface", "## Tenancy is not on the MCP")
_SURFACE_ROW = re.compile(
    r"^\|\s*(\d{1,2})\s*\|\s*`([A-Za-z_][A-Za-z0-9_]*)(?:\([^`]*\))?`", re.M
)


class DeclarationUnreadable(Exception):
    """The evidence a check needs could not be read — a failure, never a skip."""


def _read(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise DeclarationUnreadable(f"could not read {path}: {exc}") from exc


def approved_from_doc(path: Path | None = None) -> list[str]:
    """The 40 approved names, read from the owner's document — the authority, not a copy.

    Refuses unless the table yields rows numbered 1..40 contiguously, each with a distinct
    name. A partial read would make the declaration look padded (or the document look
    amended); a check that cannot read its evidence must not report success.
    """
    path = Path(path or BETA_DOC)
    text = _read(path)
    try:
        start = text.index(_SURFACE_SECTION[0])
        end = text.index(_SURFACE_SECTION[1], start)
    except ValueError as exc:
        raise DeclarationUnreadable(
            f"{path} no longer carries the {_SURFACE_SECTION[0]!r} .. "
            f"{_SURFACE_SECTION[1]!r} section — the approved table cannot be located."
        ) from exc
    rows = [(int(n), name) for n, name in _SURFACE_ROW.findall(text[start:end])]
    numbers = [n for n, _ in rows]
    if numbers != list(range(1, 41)):
        raise DeclarationUnreadable(
            f"{path} yields row numbers {numbers[:5]}… ({len(rows)} rows) where 1..40 were "
            "expected — the approved table cannot be read unambiguously."
        )
    names = [name for _, name in rows]
    if len(set(names)) != len(names):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        raise DeclarationUnreadable(
            f"{path} lists a name more than once: {duplicates[:5]}"
        )
    return names


def load_declaration(path: Path | None = None) -> dict:
    """Import the declaration module from a path, and return its three declared sets.

    Loaded by PATH, never as `import tortoise.__all__`: the package import binds the name
    `tortoise.__all__` to this module object, and a test (or a doctored copy) points the
    gate at a different file. Loading by path keeps the gate reading the evidence it was
    given.
    """
    path = Path(path or DECLARATION_FILE)
    if not path.exists():
        raise DeclarationUnreadable(
            f"the approved-surface declaration is missing at {path}. The gate cannot hold a "
            "line that was never drawn; a check that cannot read its evidence must not "
            "report success."
        )
    spec = importlib.util.spec_from_file_location("_tortoise_surface_declaration", path)
    if spec is None or spec.loader is None:  # pragma: no cover — unloadable path
        raise DeclarationUnreadable(f"could not load {path} as a module")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise DeclarationUnreadable(
            f"could not execute {path}: {type(exc).__name__}: {exc}"
        ) from exc
    out = {}
    for field in ("__all__", "APPROVED_PENDING", "OFF_SURFACE"):
        value = getattr(module, field, None)
        if not isinstance(value, (tuple, list)) or not all(
            isinstance(n, str) for n in value
        ):
            raise DeclarationUnreadable(
                f"{path} does not declare `{field}` as a tuple/list of str "
                f"(got {type(value).__name__})"
            )
        out[field] = list(value)
    for field in ("PENDING_HIGH_WATER", "OFF_SURFACE_HIGH_WATER"):
        value = getattr(module, field, None)
        if isinstance(value, bool) or not isinstance(value, int):
            raise DeclarationUnreadable(
                f"{path} does not declare `{field}` as an int (got {value!r}) — a ceiling "
                "that cannot be read must not be treated as absent."
            )
        out[field] = value
    out["path"] = path
    return out


def measure_live() -> set[str]:
    """The live public method names of `TortoiseSDK`, EXECUTED — never read as text."""
    try:
        from tortoise.sdk import TortoiseSDK
    except Exception as exc:
        raise DeclarationUnreadable(
            f"could not import the SDK: {type(exc).__name__}: {exc}"
        ) from exc
    return {
        name
        for name in dir(TortoiseSDK)
        if not name.startswith("_") and callable(getattr(TortoiseSDK, name))
    }


def resolve(name: str, live: set[str]) -> str:
    """The declared name a row resolves to, or "" when it does not.

    Row 1 of the approved table is written `Tortoise(...)` — the CONSTRUCTOR — while every
    other row is a method on it. The declaration keeps the document's spelling, and the
    attribute that must exist for that row is the class the caller constructs. This is
    evidence-bearing, not a constant `True`: it returns `TortoiseSDK` only because the
    class is importable, so a tree with no SDK fails the row like any other.
    """
    if name in live:
        return name
    if name == CONSTRUCTOR:
        try:
            from tortoise.sdk import TortoiseSDK
        except Exception:  # pragma: no cover — measured_live() already refused first
            return ""
        return CONSTRUCTOR if callable(TortoiseSDK) else ""
    return ""


def resolves(name: str, live: set[str]) -> bool:
    """Does a declared name exist? The constructor's row resolves to the class itself."""
    return bool(resolve(name, live))


def problems(
    declared: list[str],
    pending: list[str],
    off_surface: list[str],
    live: set[str],
    doc_names: list[str],
    pending_high_water: int,
    off_surface_high_water: int,
) -> list[str]:
    """Every way the declaration, the document and the class can disagree.

    Pure, so the three verdicts (an addition, a vanished declared method, a no-op) are
    exercised against the REAL declaration with one injected difference, instead of each
    test rehearsing a whole copy of the artifact.
    """
    found: list[str] = []

    for label, names in (
        ("__all__", declared),
        ("APPROVED_PENDING", pending),
        ("OFF_SURFACE", off_surface),
    ):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            found.append(f"{label} lists a name more than once: {duplicates[:8]}")

    # 1. The declaration IS the owner's list — including its ORDER. Both directions, and in
    #    sequence: a name added here is an unapproved surface change, a name missing here is
    #    an approved method the declaration has dropped, and a REORDER is not a surface
    #    change but it makes the diff of the next real change unreadable, so it is refused
    #    rather than shrugged at (measured on review: a swap of rows 3 and 4 was green while
    #    the docstring claimed the declaration "cannot be reordered away from").
    declared_set = set(declared)
    doc_set = set(doc_names)
    if declared_set == doc_set and list(declared) != list(doc_names):
        found.append(
            "the declaration lists the 40 approved methods in a different ORDER than "
            f"{BETA_DOC.name} — restore the document's order (rows 1-40)"
        )
    for name in sorted(declared_set - doc_set):
        found.append(
            f"{name}: declared in __all__ but NOT in {BETA_DOC.name} — the declaration "
            "cannot add to the owner's list"
        )
    for name in sorted(doc_set - declared_set):
        found.append(
            f"{name}: approved in {BETA_DOC.name} but MISSING from __all__ — the "
            "declaration dropped an approved method"
        )

    # 2. Status is MEASURED, never claimed: `APPROVED_PENDING` is exactly the approved
    #    names that do not resolve on the class today.
    live_declared = {n for n in declared_set if resolves(n, live)}
    absent_declared = declared_set - live_declared
    pending_set = set(pending)
    for name in sorted(absent_declared - pending_set):
        found.append(
            f"{name}: declared in __all__ and NOT listed in APPROVED_PENDING, but it does "
            "not exist on TortoiseSDK — an approved method may not vanish"
        )
    for name in sorted(pending_set & live_declared):
        found.append(
            f"{name}: listed in APPROVED_PENDING but it DOES exist on TortoiseSDK — move it "
            "out of the gap list in the same change that implements it"
        )
    for name in sorted(pending_set - declared_set):
        found.append(
            f"{name}: listed in APPROVED_PENDING but it is not on the approved surface at "
            "all — the gap list may only draw from __all__"
        )

    # 3. The backlog is a FROZEN RECORD of what existed OFF-target at the cut, and every live
    #    method must be accounted for by it or by `__all__`.
    #
    #    NOT "OFF_SURFACE == live − approved". That equality looks tighter and is WEAKER: it
    #    is satisfied by a SWAP (one name out of the class, one unapproved name in, the same
    #    length), so a rename — precisely what requirement 4 says the gate must catch — was
    #    invisible (measured on review: swapping `annotate_ask_hits` for a new method
    #    returned []). Keeping the record FROZEN makes the swap visible, because the new
    #    name is in neither set:
    #
    #        live − approved − OFF_SURFACE  ->  an ADDITION or a RENAME, named
    #
    #    and it also removes the hand edit a removal used to require: a method that leaves
    #    the class stays in the record (that is what "was here at the cut" means), so the
    #    migration direction needs no declaration edit at all.
    off_set = set(off_surface)
    for name in sorted(live - declared_set - off_set):
        found.append(
            f"{name}: PUBLIC on TortoiseSDK but on neither the approved surface nor the "
            "frozen off-surface record — an addition or a rename needs Daniel's approval "
            "BEFORE it lands (AGENTS.md -> MCP/SDK Surface Approval)"
        )
    overlap = sorted(off_set & declared_set)
    if overlap:
        found.append(
            f"{overlap[:8]}: listed in OFF_SURFACE AND on the approved surface — a method "
            "is one or the other"
        )

    # 4. The pins. Both sets may only shrink, so neither can absorb the other's defect.
    if len(pending_set) > pending_high_water:
        found.append(
            f"APPROVED_PENDING grew to {len(pending_set)} (high-water "
            f"{pending_high_water}) — an approved method cannot be turned into a gap; "
            "removing one is a surface change that needs Daniel's approval"
        )
    if len(off_set) > off_surface_high_water:
        found.append(
            f"OFF_SURFACE grew to {len(off_set)} (high-water {off_surface_high_water}) — "
            "the backlog may only shrink; an addition cannot be absorbed by listing it here"
        )
    return found


def check(
    declaration_path: Path | None = None,
    doc_path: Path | None = None,
    live: set[str] | None = None,
) -> list[str]:
    """Read the evidence and return every problem (empty = clean)."""
    declaration = load_declaration(declaration_path)
    doc_names = approved_from_doc(doc_path)
    if live is None:
        live = measure_live()
    return problems(
        declared=declaration["__all__"],
        pending=declaration["APPROVED_PENDING"],
        off_surface=declaration["OFF_SURFACE"],
        live=live,
        doc_names=doc_names,
        pending_high_water=declaration["PENDING_HIGH_WATER"],
        off_surface_high_water=declaration["OFF_SURFACE_HIGH_WATER"],
    )


def declared_live_methods(declaration_path: Path | None = None) -> set[str]:
    """The declared LIVE public method set — `approved ∩ live` plus the backlog.

    This is what `tools/surface_manifest.py cut` derives the manifest's `sdk:` rows from,
    so the frozen baseline is cut FROM the declaration rather than from a bare `dir()`.
    Names that are declared but do not exist are not invented here: `cut` refuses when this
    set disagrees with the class (see `check`).
    """
    declaration = load_declaration(declaration_path)
    return (set(declaration["__all__"]) | set(declaration["OFF_SURFACE"])) & measure_live()


def cmd_check(args: argparse.Namespace) -> int:
    try:
        found = check(args.declaration, args.doc)
    except DeclarationUnreadable as exc:
        print(f"::error::{exc}")
        print(f"REFUSED {exc}")
        return 1
    if found:
        print("::error::the declared SDK surface, the approved document and the class disagree")
        for problem in found:
            print(f"FAIL {problem}")
        print(
            "\nThe declaration is tortoise/__all__.py; the authority is "
            f"{BETA_DOC.relative_to(ROOT)}. Adding or removing a public SDK method needs "
            "Daniel's approval FIRST (AGENTS.md -> MCP/SDK Surface Approval)."
        )
        return 1
    print(
        "OK — 40 approved methods declared, the gap list and the removal backlog match the "
        "live class."
    )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        declaration = load_declaration(args.declaration)
        doc_names = approved_from_doc(args.doc)
        live = measure_live()
    except DeclarationUnreadable as exc:
        print(f"REFUSED {exc}")
        return 1
    approved_live = sorted(n for n in declaration["__all__"] if resolves(n, live))
    print(f"declaration: {declaration['path']}")
    print(f"  __all__             {len(declaration['__all__'])} approved (doc says {len(doc_names)})")
    print(f"  implemented today   {len(approved_live)}: {approved_live}")
    print(f"  APPROVED_PENDING    {len(declaration['APPROVED_PENDING'])} tracked gap(s)")
    print(f"  OFF_SURFACE         {len(declaration['OFF_SURFACE'])} off-target method(s)")
    print(f"  live on TortoiseSDK {len(live)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("check", cmd_check), ("show", cmd_show)):
        p = sub.add_parser(name)
        p.add_argument("--declaration", default=None)
        p.add_argument("--doc", default=None)
        p.set_defaults(func=func)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
