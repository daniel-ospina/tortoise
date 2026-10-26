#!/usr/bin/env python3
"""The approved-surface declaration is a GATE, not a comment (#4282 phase 1.1).

WHAT IT CHECKS
    `tortoise/__all__.py` declares the **40 approved SDK methods** and nothing else. This
    tool reconciles that declaration against the two things it can be wrong about, in BOTH
    directions each:

      1. THE OWNER'S DOCUMENT (`docs/product/beta-sdk-surface.md` rows 1-40) — the contract.
         A name in `__all__` that the owner did not approve, or an approved name dropped
         from `__all__`, is a CONTRACT violation. This must never be red; it is what makes
         the declaration the owner's list rather than a re-derivation.
      2. THE LIVE CLASS (`dir(TortoiseSDK)`, executed) — the migration gap.
         * a public SDK method that is NOT in `__all__`  ->  an ADDITION or backlog that the
           approved surface does not cover (147 live − 9 approved-and-live = 138 today);
         * a name in `__all__` with NO `def` on the class   ->  an approved method that does
           not exist yet (30 today; `Tortoise` is the constructor and always resolves).

WHY BOTH DIRECTION-2 CHECKS FAIL, HONESTLY (#10034 rework)
    An earlier draft of this gate carried two extra ledger blocks — an `OFF_SURFACE`
    removal backlog and an `APPROVED_PENDING` gap list — inside `tortoise/__all__.py`, so
    that a live-but-unapproved method could be *listed* and the run stayed green. That
    defeated the purpose: the declaration then described **210 names** (40 approved + 30
    pending + 140 backlog), the manifest was cut from `approved | backlog`, and "there is
    a method I never approved" became indistinguishable from "there is a method the
    migration has not removed yet" at the exact place the line has to be held. The
    approved target has to be the 40 alone, and the gap has to be REPORTED, not absorbed.

    So today this gate is RED — by design, with every offender named. That is the true
    state of #4282: 40 approved, 147 live. It goes green when Phase 2 removes the surplus
    and implements the 30 approved-but-absent methods, not before.

WHAT RUNS IN REQUIRED CI (`check --contract-only`)
    `tools/surface_manifest.py check` runs in the required `surface-guard` job and calls
    the CONTRACT half only (`contract_problems`): the declaration must still be the
    owner's exact 40. The gap is not silently dropped there — it is printed with its
    counts, and it is red in this tool's full `check` and in `tools/surface-target-check.py`
    (the compliance instrument, deliberately outside the required gate while the gap is
    open — see that tool's docstring). A permanently red required job would just be a
    permanently ignored one.

WHAT IT IS NOT
    Not an approval. A green run means the declaration is the owner's document; it cannot
    tell an approved addition from an unapproved one, because both look like "doc +
    declaration + code updated together". Daniel's approval is what carries the approval
    (`docs/product/canonical-mcp-tools.md`); this gate makes a surface change VISIBLE.

Usage:
    uv run python tools/sdk_surface_declaration.py check                    # contract + gap (red today)
    uv run python tools/sdk_surface_declaration.py check --contract-only    # the required-CI contract
    uv run python tools/sdk_surface_declaration.py show                     # the declared sets
    uv run python tools/sdk_surface_declaration.py baseline                 # re-render the baseline doc
    uv run python tools/sdk_surface_declaration.py baseline --check         # is the baseline doc stale?

Exit 0 = the check that was asked for holds. Exit 1 = it does not, or the evidence cannot be
read (a refusal, never a skip).
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
BASELINE_DOC = ROOT / "docs" / "product" / "sdk-surface-baseline.md"

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
        raise DeclarationUnreadable(f"{path} lists a name more than once: {duplicates[:5]}")
    return names


def load_declaration(path: Path | None = None) -> dict:
    """Import the declaration module from a path, and return the declared set.

    Loaded by PATH, never as `import tortoise.__all__`: the package import binds the name
    `tortoise.__all__` to this module object, and a test (or a doctored copy) points the
    gate at a different file. Loading by path keeps the gate reading the evidence it was
    given.

    The module must declare `__all__` and NOTHING ELSE that looks like a second surface.
    The previous draft's `OFF_SURFACE` / `APPROVED_PENDING` blocks are refused by name
    here rather than merely deleted: a re-introduction is the exact regression this gate
    exists to catch, and an error that names the defect is cheaper than a review.
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
    for banned in ("OFF_SURFACE", "APPROVED_PENDING"):
        if hasattr(module, banned):
            raise DeclarationUnreadable(
                f"{path} declares `{banned}` — the declaration is the APPROVED 40 and may "
                "not also carry a ledger of what exists (that would bless the surplus, "
                "#10044). The baseline of what exists lives in "
                f"{BASELINE_DOC.relative_to(ROOT)}."
            )
    value = getattr(module, "__all__", None)
    if not isinstance(value, (tuple, list)) or not all(isinstance(n, str) for n in value):
        raise DeclarationUnreadable(
            f"{path} does not declare `__all__` as a tuple/list of str "
            f"(got {type(value).__name__})"
        )
    return {"__all__": list(value), "path": path}


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


def resolves(name: str, live: set[str]) -> bool:
    """Does a declared name exist? The constructor's row resolves to the class itself."""
    return name in live or name == CONSTRUCTOR


def duplicates(names: list[str]) -> list[str]:
    return sorted({n for n in names if names.count(n) > 1})


def contract_problems(declared: list[str], doc_names: list[str]) -> list[str]:
    """The declaration IS the owner's list — the part that must never be red.

    Both directions: a name added here is an unapproved surface change; a name missing here
    is an approved method the declaration dropped.
    """
    found: list[str] = []
    dup = duplicates(declared)
    if dup:
        found.append(f"__all__ lists a name more than once: {dup[:8]}")
    declared_set = set(declared)
    doc_set = set(doc_names)
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
    return found


def gap_problems(declared: list[str], live: set[str]) -> list[str]:
    """The migration gap, in both directions — red until Phase 2 closes it.

    This is the check whose absence let the earlier draft look green: a live public method
    the owner never approved must FAIL and be NAMED, and an approved method with no `def`
    on the class must FAIL and be NAMED.
    """
    found: list[str] = []
    declared_set = set(declared)
    for name in sorted(live - declared_set):
        found.append(
            f"TortoiseSDK.{name}: PUBLIC on TortoiseSDK but NOT on the approved surface — "
            "either it is migration backlog (Phase 2 removes it) or it is an addition, and "
            "an addition needs Daniel's approval BEFORE it lands (AGENTS.md -> MCP/SDK "
            "Surface Approval)"
        )
    for name in sorted(declared_set - {n for n in declared_set if resolves(n, live)}):
        found.append(
            f"{name}: approved and declared but it does NOT exist on TortoiseSDK — an "
            "approved method may not be absent; implement it (Phase 2) or reopen the "
            "approval, never drop it from the declaration"
        )
    return found


def problems(declared: list[str], live: set[str], doc_names: list[str]) -> list[str]:
    """Every way the declaration, the document and the class can disagree. Pure."""
    return contract_problems(declared, doc_names) + gap_problems(declared, live)


def check(
    declaration_path: Path | None = None,
    doc_path: Path | None = None,
    live: set[str] | None = None,
    *,
    contract_only: bool = False,
) -> list[str]:
    """Read the evidence and return every problem (empty = clean)."""
    declaration = load_declaration(declaration_path)
    doc_names = approved_from_doc(doc_path)
    found = contract_problems(declaration["__all__"], doc_names)
    if contract_only:
        return found
    if live is None:
        live = measure_live()
    return found + gap_problems(declaration["__all__"], live) + baseline_problem()


def rendered_baseline(
    declared: list[str] | None = None, live: set[str] | None = None
) -> str:
    """The baseline-of-what-exists document, rendered from a measurement.

    This is the artifact the previous draft folded into `tortoise/__all__.py`. It is a
    SEPARATE, generated document on purpose: it describes today, it is not an approval, and
    it may only shrink.
    """
    declared = list(declared if declared is not None else load_declaration()["__all__"])
    live = set(live if live is not None else measure_live())
    approved_live = sorted(n for n in declared if resolves(n, live))
    approved_absent = sorted(n for n in declared if not resolves(n, live))
    backlog = sorted(live - set(declared))
    lines = [
        "# SDK surface baseline — what exists today, NOT the target",
        "",
        "**GENERATED** by `tools/sdk_surface_declaration.py baseline` (tortoise #4282 Phase",
        "0.4/1.1). Never edit by hand — `baseline --check` reds if this file is stale.",
        "",
        "> ⚠️ **This is a baseline, not an approval, and not a second declaration.** It records",
        "> the public SDK surface as it exists today so the gap to the owner's target can be",
        "> tracked while #4282 Phase 2 removes it. The **approved target is",
        "> `tortoise/__all__.py`** — 40 methods, owner ruling 2026-09-21",
        "> (`docs/product/beta-sdk-surface.md`, approval record in",
        "> `docs/product/canonical-mcp-tools.md`). Nothing is approved by being listed here.",
        "> **This list may only SHRINK.**",
        "",
        "| measured | count |",
        "|---|---|",
        f"| approved methods (the target) | {len(declared)} |",
        f"| live public methods on `TortoiseSDK` | {len(live)} |",
        f"| approved and implemented (row 1 `Tortoise(...)` resolves to the `TortoiseSDK` class) | {len(approved_live)} |",
        f"| approved but not implemented yet | {len(approved_absent)} |",
        f"| live and NOT approved (migration backlog) | {len(backlog)} |",
        "",
        "Row 1 of the owner's table is `Tortoise(...)` — the class a caller constructs. It resolves",
        "to `TortoiseSDK` (the only constructible entry point); it is not a method on it.",
        "",
        "`docs/product/beta-sdk-surface.md` states \"only four of the 40 exist in the current SDK\".",
        # DERIVED, never a literal (#4282). The literal used to say `nine` while this
        # same table said 13 — and because `baseline --check` compares committed vs
        # rendered, both sides said nine and the gate stayed green over a document
        # that contradicted itself one paragraph above. Row 1 is the constructor, so
        # the METHOD count is the implemented row minus one.
        f"That sentence is STALE: {len(approved_live) - 1} exist, plus the constructor."
        " It is recorded here rather than",
        "edited there, because the approved document is the owner's ruling and this baseline is not",
        "the place to amend it (#10044).",
        "",
        "## Approved but not implemented yet",
        "",
        "The owner approved these; no `def` exists on `TortoiseSDK` under the canonical name.",
        "Phase 2 implements them.",
        "",
    ]
    lines += [f"- `{name}`" for name in approved_absent] or ["*(none)*"]
    lines += [
        "",
        "## Live and NOT approved — the migration backlog",
        "",
        "Public on `TortoiseSDK` today, absent from the approved 40. Phase 2 removes them;",
        "the count above is the honest measure of the gap.",
        "",
    ]
    lines += [f"- `{name}`" for name in backlog] or ["*(none)*"]
    lines.append("")
    return "\n".join(lines)


def baseline_problem(path: Path | None = None) -> list[str]:
    """Is the committed baseline document stale? A drifting baseline tracks nothing.

    Called from `tools/surface_manifest.py check` too, so a PR that changes the SDK surface
    without re-rendering `docs/product/sdk-surface-baseline.md` reds the required job
    instead of silently leaving the gap understated.
    """
    path = Path(path or BASELINE_DOC)
    try:
        committed = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"the SDK-surface baseline {path} cannot be read: {exc}"]
    if committed != rendered_baseline():
        return [
            f"the SDK-surface baseline {path} is STALE — re-render it with `uv run python "
            "tools/sdk_surface_declaration.py baseline` (the gap it tracks would otherwise be "
            "understated)"
        ]
    return []


def cmd_check(args: argparse.Namespace) -> int:
    try:
        found = check(args.declaration, args.doc, contract_only=args.contract_only)
    except DeclarationUnreadable as exc:
        print(f"::error::{exc}")
        print(f"REFUSED {exc}")
        return 1
    declared = load_declaration(args.declaration)["__all__"]
    if found:
        print(
            "::error::the declared SDK surface, the approved document and the class disagree"
            if not args.contract_only
            else "::error::the declaration is not the owner's approved list"
        )
        for problem in found:
            print(f"FAIL {problem}")
        print(
            "\nThe declaration is tortoise/__all__.py; the authority is "
            f"{BETA_DOC.relative_to(ROOT)}. Adding or removing a public SDK method needs "
            "Daniel's approval FIRST (AGENTS.md -> MCP/SDK Surface Approval)."
        )
        return 1
    try:
        live = measure_live()
    except DeclarationUnreadable as exc:
        print(f"::error::{exc}")
        print(f"REFUSED {exc}")
        return 1
    if args.contract_only:
        print(
            f"OK CONTRACT — the declaration is the owner's {len(declared)} approved methods, "
            "unchanged."
        )
        # The gap is PRINTED even in contract mode so a green required job can never read
        # as "the surface is on target".
        over = sorted(live - set(declared))
        absent = sorted(n for n in declared if not resolves(n, live))
        print(
            f"GAP (expected until #4282 Phase 2) — {len(over)} live method(s) off the approved "
            f"surface, {len(absent)} approved method(s) not implemented. Full red: "
            "`uv run python tools/sdk_surface_declaration.py check`."
        )
        return 0
    print(
        f"OK — the declaration is the owner's {len(declared)} approved methods, and the live "
        f"class matches it exactly ({len(live)} public methods)."
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
    absent = [n for n in declaration["__all__"] if not resolves(n, live)]
    print(f"declaration: {declaration['path']}")
    print(f"  __all__             {len(declaration['__all__'])} approved (doc says {len(doc_names)})")
    print(f"  implemented today   {len(approved_live)}: {approved_live}")
    print(f"  approved, absent    {len(absent)}")
    print(f"  live off-surface    {len(live - set(declaration['__all__']))}")
    print(f"  live on TortoiseSDK {len(live)}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    try:
        text = rendered_baseline()
    except DeclarationUnreadable as exc:
        print(f"::error::{exc}")
        print(f"REFUSED {exc}")
        return 1
    if args.check:
        try:
            committed = Path(args.doc_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"::error::could not read {args.doc_file}: {exc}")
            return 1
        if committed != text:
            print(
                f"FAIL {args.doc_file} is STALE — it does not match the measurement. Re-render "
                "it with `uv run python tools/sdk_surface_declaration.py baseline`."
            )
            return 1
        print(f"OK — {args.doc_file} matches the measured surface.")
        return 0
    Path(args.doc_file).write_text(text, encoding="utf-8")
    print(f"wrote {args.doc_file}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("check", cmd_check), ("show", cmd_show)):
        p = sub.add_parser(name)
        p.add_argument("--declaration", default=None)
        p.add_argument("--doc", default=None)
        if name == "check":
            p.add_argument(
                "--contract-only",
                action="store_true",
                help="check only that the declaration is the owner's list (the required-CI half)",
            )
        p.set_defaults(func=func)
    b = sub.add_parser("baseline", help="render the baseline-of-what-exists document")
    b.add_argument("--doc-file", default=str(BASELINE_DOC))
    b.add_argument("--check", action="store_true", help="verify the committed file is not stale")
    b.set_defaults(func=cmd_baseline)
    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
