#!/usr/bin/env python3
"""The declared public surface of `TortoiseSDK` — derived from the AST, and checked.

WHY THIS EXISTS (tortoise #4282, Phase 0.4 + 1.1)
------------------------------------------------
#4282 describes this phase as *"`__all__` in `tortoise/__init__.py` — the root cause of the
150"*, and 1.1 as *"`tortoise/__all__` lists exactly the 41 approved methods"*. **Both are
misdirected**, and each claim is wrong for a reason that is checkable:

  * `tortoise/__init__.py` binds six names (`__version__`, `os`, `RELATIVE_PATH_ERROR`,
    `enforce_embedded_fork_safety`, `fork_safe_serverconfig`, `atexit_fast_close`) and
    carries **no `__all__`** — so does `tortoise/sdk.py`.
  * The 150 are **methods on the `TortoiseSDK` class** in `tortoise/sdk.py` (284 `def`
    statements in the class body, 150 of them public). A module-level `__all__` physically
    cannot name them, so adding one would declare nothing about the surface that drifted.
  * The surface consumed in production is the CLASS body: `tests/test_surface_resolution.py`
    resolves `getattr(TortoiseSDK, entry.sdk_method)`.

So the deliverable is not an `__all__`; it is **a declaration of the `TortoiseSDK` public
method set, derived from the code, plus a check that it cannot drift in either direction.**

WHAT IS DECLARED, AND FROM WHAT
-------------------------------
The declaration is `config/sdk-surface.json`: the public method set of `TortoiseSDK`. It is
GENERATED, never typed — hand-written counts and line numbers in this epic were repeatedly
caught drifting, so nothing authoritative here is hand-maintained.

The declaration is the **name set only**. It deliberately carries no `def` line numbers: this
epic already paid for that lesson once (Phase 3.3 — `co_firstlineno` was removed from the MCP
fingerprint because a pure code move shifted every handler below it and reddened the gate on
an unchanged surface). A line number would make this gate red on a comment insertion.

The one AST walk of the class body is `tools.bridge_table._sdk_targets` (merged with Phase
0.1). It is IMPORTED rather than re-implemented on purpose, exactly as the Phase 0.3b
sibling does: a second walk would be a second opinion about what the public surface IS, and
the two would eventually disagree.

FOUR VIEWS, RECONCILED IN BOTH DIRECTIONS
-----------------------------------------
A declaration checked only against itself proves nothing, so `--check` reconciles the frozen
declaration against three views derived by genuinely different means, and fails closed on any
disagreement in either direction:

  1. **AST** — `_sdk_targets()`, a class-body walk of `tortoise/sdk.py`. Sees what is
     *defined* in the class.
  2. **REFLECTION** — `dir(TortoiseSDK)` + `callable`, the view the production surface is
     consumed through (`test_surface_resolution.py`) and the one `tools/surface-guard.py` and
     `config/surface-manifest.yml` were cut from. Sees what is *reachable* on the class,
     including anything attached outside the class body — `tortoise/sdk.py` already contains
     exactly that pattern (`TortoiseSDK._EVENT_PURGE_LAST = now`, line 2376).
  3. **APPROVED BASELINE** — the `sdk:` rows of `config/surface-manifest.yml`, the frozen,
     owner-approved set (#3863).

AST and REFLECTION are not the same question, and nothing reconciled them until this file.
They agree exactly today (150 / 150). They are not guaranteed to keep agreeing: a public
attribute attached to the class OUTSIDE its body is visible to reflection and invisible to the
AST walk, which is precisely where a `dir()`-only gate and an AST-only gate would silently
part company.

WHAT THIS IS NOT
----------------
It is not a second approval gate. `config/sdk-surface.json` carries no approval semantics —
`config/surface-manifest.yml` remains the approved baseline, and its gate
(`tools/surface-guard.py`) remains the merge-blocking one. This declaration records
*identity*; the baseline records *approval*. `--check` asserts the two agree, so they cannot
drift apart.

Neither artifact proves approval. `--check` is a CONSISTENCY check: a change that updates the
`TortoiseSDK` class and this declaration together passes it. Adding or removing a public
method needs Daniel's approval FIRST — the #4282 mandate (`tortoise/sdk.py`, `CONTRIBUTING.md`).
A green run is not consent.

USAGE
    uv run python tools/sdk_surface.py            # write the declaration + its doc
    uv run python tools/sdk_surface.py --check    # verify only, non-zero on any drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The ONE AST walk of the `TortoiseSDK` class body. Imported, not re-implemented: a second
# copy would be a second answer to "what is the SDK surface", and the two would drift.
from tools import bridge_table  # noqa: E402
from tools.bridge_table import _sdk_targets  # noqa: E402

DECLARATION_FILE = ROOT / "config" / "sdk-surface.json"
MANIFEST_FILE = ROOT / "config" / "surface-manifest.yml"
DOC_FILE = ROOT / "docs" / "product" / "sdk-surface-declaration.md"

CLASS_NAME = "TortoiseSDK"
DERIVATION_RULE = (
    "every `def`/`async def` in the AST class body of `TortoiseSDK` whose name does not "
    "begin with `_`"
)


class SurfaceEvidenceUnreadable(Exception):
    """A gate that cannot read its evidence must refuse, never skip (#1382 class)."""


def _read_text_or_refuse(path: Path, what: str) -> str:
    """Read an evidence text file, or refuse in the same contract as the other surfaces.

    The doc staleness read is an evidence read exactly like `load_declaration` /
    `approved_from_manifest`: a path that exists but cannot be read (a directory, a permission
    error, bad encoding) must produce the documented `SURFACE CHECK REFUSED`, not a raw
    traceback (#4516 review). The declaration's own text is captured by `load_declaration`'s
    single read, so it needs no second read here.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SurfaceEvidenceUnreadable(f"{what} {path} does not exist") from None
    except Exception as exc:
        raise SurfaceEvidenceUnreadable(
            f"{what} {path} could not be read: {type(exc).__name__}: {exc}"
        ) from exc


def _refuse(exc: SurfaceEvidenceUnreadable) -> int:
    """The one documented refusal: message + remedial command, exit 1."""
    print(f"SURFACE CHECK REFUSED — {exc}", file=sys.stderr)
    print(
        "A gate that cannot read its evidence must fail, not skip. "
        "Run: uv run python tools/sdk_surface.py",
        file=sys.stderr,
    )
    return 1


def is_public(name: str) -> bool:
    """The public-surface predicate, applied identically to every view.

    `not name.startswith("_")` is deliberately the SAME rule the existing reflection
    derivations use (`surface_manifest.py`, `surface-guard.py`, `test_surface_manifest.py`),
    so the AST and reflection views are compared on equal terms rather than on two
    definitions that would trivially agree.
    """
    return not name.startswith("_")


def declared_from_ast() -> set[str]:
    """The declaration's SOURCE: public methods defined in the `TortoiseSDK` class body.

    Fail closed (#1382 class): a source file that cannot be read, decoded, or parsed is
    unreadable EVIDENCE, not an empty surface. It is re-raised as the same
    `SurfaceEvidenceUnreadable` the other two surfaces raise, so all four refuse identically
    rather than one of them escaping as a raw `FileNotFoundError`/`SyntaxError` traceback.
    """
    try:
        targets = _sdk_targets()
    except Exception as exc:
        raise SurfaceEvidenceUnreadable(
            f"the AST view could not be read from {bridge_table.SDK_SRC}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return {name for name in targets if is_public(name)}


def reflected_from_runtime() -> set[str]:
    """The REACHABLE public surface — what `getattr(TortoiseSDK, name)` can resolve.

    This is the view `tests/test_surface_resolution.py` consumes and the view the approved
    baseline was cut from. It sees attributes attached outside the class body; the AST walk
    does not.

    Fail closed (#1382 class): an import that fails, a class that is missing, or a `dir()`/
    `getattr` that raises is unreadable EVIDENCE, not an empty surface — re-raised as
    `SurfaceEvidenceUnreadable` like the other three.
    """
    try:
        from tortoise.sdk import TortoiseSDK

        return {
            name
            for name in dir(TortoiseSDK)
            if is_public(name) and callable(getattr(TortoiseSDK, name))
        }
    except Exception as exc:
        raise SurfaceEvidenceUnreadable(
            f"the reflection view could not be read from `tortoise.sdk`: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def approved_from_manifest(path: Path = MANIFEST_FILE) -> set[str]:
    """The approved baseline's SDK rows — the frozen, owner-approved set (#3863)."""
    import yaml

    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SurfaceEvidenceUnreadable(f"the approved baseline {path} does not exist") from None
    except Exception as exc:  # any read failure is a fail-closed failure
        raise SurfaceEvidenceUnreadable(
            f"the approved baseline {path} could not be read: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        raise SurfaceEvidenceUnreadable(
            f"the approved baseline {path} is malformed: expected a mapping with a `rows:` key"
        )
    return {
        str(row["method"])
        for row in doc["rows"]
        if isinstance(row, dict)
        and str(row.get("name", "")).startswith("sdk:")
        and row.get("method")
    }


def load_declaration(path: Path = DECLARATION_FILE) -> tuple[set[str], str]:
    """The frozen declaration as written — the declared view, plus its RAW text.

    The raw text is returned alongside the name set so `--check` can compare the file's
    bytes against a freshly rendered declaration from the SAME read. A second read would be
    a second, unguarded evidence read (and a TOCTOU window), which is exactly the class
    #4516 closed.
    """
    try:
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text)
    except FileNotFoundError:
        raise SurfaceEvidenceUnreadable(f"the declaration {path} does not exist") from None
    except Exception as exc:  # any read failure is a fail-closed failure
        raise SurfaceEvidenceUnreadable(
            f"the declaration {path} could not be read: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("methods"), list):
        raise SurfaceEvidenceUnreadable(
            f"the declaration {path} is malformed: expected a mapping with a `methods` list"
        )
    return {str(name) for name in doc["methods"]}, text


def render_json(declared: set[str]) -> str:
    """The machine-readable declaration. Deterministic: sorted list, fixed indent, final NL."""
    payload = {
        "declaration": "tortoise-sdk-public-surface",
        "issue": 4282,
        "class": CLASS_NAME,
        "source": "tortoise/sdk.py",
        "derivation": "AST class-body walk (tools.bridge_table._sdk_targets)",
        "derivation_rule": DERIVATION_RULE,
        "count": len(declared),
        "methods": sorted(declared),
    }
    return json.dumps(payload, indent=2) + "\n"


AST_VIEW = "the live AST class body of `TortoiseSDK`"
REFLECTION_VIEW = "runtime reflection (`dir(TortoiseSDK)` + `callable`)"
APPROVED_VIEW = "the approved baseline (`config/surface-manifest.yml` `sdk:` rows)"


def _disagreements(reference: set[str], views: tuple[tuple[str, set[str]], ...]) -> list[str]:
    """Every direction in which `reference` disagrees with each view. Empty == they agree.

    Each comparison is reported in BOTH directions, and there is no partial credit: a single
    non-empty list is the red.
    """
    problems: list[str] = []

    for label, other in views:
        gone = sorted(reference - other)
        if gone:
            problems.append(
                f"the declaration names {len(gone)} method(s) that {label} does not have: "
                f"{gone}. A method that no longer exists must not stay declared — re-render, "
                "or restore it."
            )
        undeclared = sorted(other - reference)
        if undeclared:
            problems.append(
                f"{label} has {len(undeclared)} public method(s) the declaration does not "
                f"name: {undeclared}. Declare them (re-render) — an undeclared method is "
                "exactly the silent drift this declaration exists to stop."
            )

    return problems


def reconcile_declaration(
    frozen: set[str],
    live_ast: set[str],
    reflected: set[str],
    approved: set[str],
) -> list[str]:
    """`--check`: the frozen declaration vs all three views, in both directions."""
    return _disagreements(
        frozen,
        ((AST_VIEW, live_ast), (REFLECTION_VIEW, reflected), (APPROVED_VIEW, approved)),
    )


def reconcile_code_views(live_ast: set[str], reflected: set[str]) -> list[str]:
    """`--render`: do the two views derived FROM THE CODE agree about what the surface is?

    This is the only precondition for writing. The approved baseline is deliberately NOT
    part of it: approval is a human act that happens *after* a method exists, so requiring
    it here would make the documented order (define → render → get approved) impossible.
    The baseline is cross-checked by `--check`, which is what CI runs.
    """
    return _disagreements(live_ast, ((REFLECTION_VIEW, reflected),))


def render_doc(frozen: set[str], reflected: set[str], approved: set[str]) -> str:
    """The human-facing half: the declaration, and the one procedure a contributor needs."""
    agreement = (
        " — all agree."
        if len(frozen) == len(reflected) == len(approved) and frozen == reflected == approved
        else ". They do NOT all agree; see `tools/sdk_surface.py --check`."
    )
    return f"""# The declared public surface of `TortoiseSDK`

**GENERATED** by `tools/sdk_surface.py` (tortoise #4282, Phase 0.4 + 1.1). Never edit by
hand — edit the generator, or the declaration it derives from. `--check` reds if this file
is stale.

**{len(frozen)} public methods**, declared in `config/sdk-surface.json`.

## What is declared, and where it comes from

The declaration is machine-readable (`config/sdk-surface.json`) and is **derived from the
code, never typed**: {DERIVATION_RULE}.

The walk itself lives in `tools.bridge_table._sdk_targets` (Phase 0.1) and is *imported*
here, not re-implemented. A second walk would be a second opinion about what the SDK surface
is, and the two would eventually disagree.

The declaration holds the **name set only** — no `def` line numbers. That is deliberate: this
epic already removed `co_firstlineno` from the MCP fingerprint (Phase 3.3) because a pure code
move shifted every handler below it and reddened the gate on an unchanged surface. A line
number here would red this gate on a comment insertion.

### Why not `tortoise/__all__`

#4282's Phase 0.4 asked for `__all__` in `tortoise/__init__.py` as *"the root cause of the
150"*. It is not, and cannot be:

| Claim | Measured |
|---|---|
| "the public surface is declared by convention" | `tortoise/__init__.py` binds six names (`__version__`, `os`, `RELATIVE_PATH_ERROR`, `enforce_embedded_fork_safety`, `fork_safe_serverconfig`, `atexit_fast_close`) and has no `__all__`. `tortoise/sdk.py` has none either. |
| "`__all__` lists the 41 approved methods" | The methods are **class members**. A module-level `__all__` physically cannot name them. |
| "the surface is the module's names" | `tests/test_surface_resolution.py` resolves `getattr(TortoiseSDK, entry.sdk_method)` — the **class body** is the surface production consumes. |

## Why three comparisons, not one

A declaration checked only against itself proves nothing. `--check` compares the frozen
declaration against three views derived by genuinely different means, in both directions each:

| view | derivation | sees |
|---|---|---|
| **AST** | `_sdk_targets()` class-body walk of `tortoise/sdk.py` | what is **defined** in the class |
| **REFLECTION** | `dir(TortoiseSDK)` + `callable` | what is **reachable**, incl. attributes attached outside the class body |
| **APPROVED** | `config/surface-manifest.yml` `sdk:` rows | what an **owner approved** (#3863) |

At the render that produced this file: AST **{len(frozen)}** · reflection **{len(reflected)}**
· approved **{len(approved)}**{agreement}

AST and reflection are *not* the same question, and nothing reconciled them before this file.
`tortoise/sdk.py` already attaches one attribute outside the class body
(`TortoiseSDK._EVENT_PURGE_LAST = now`); it is private, so it does not surface today, but a
**public** attach would be visible to reflection and invisible to the AST walk. That is
exactly the divergence this reconciliation makes loud. A public attach would land here, in the
gap between the two counts — which is why both are reported.

## How to add a method to the surface

1. **Define it** in the `TortoiseSDK` class body in `tortoise/sdk.py` (a `def`/`async def`
   with no leading `_`). That alone makes it public — which is the point: it is now *caught*,
   not absorbed.
2. **Re-render**: `uv run python tools/sdk_surface.py`.
3. **Get it approved — from Daniel, FIRST.** You may not add or remove a public SDK method
   without human approval (the #4282 mandate): the surface is the contract every agent and
   customer integration is built on, so a change materially affects customer outcomes. Ask
   Daniel (repo `AGENTS.md` → "USER QUESTIONS" / "DECISION RELAY") before you write the code
   or re-cut anything. Only then add the method's `sdk:` row to `config/surface-manifest.yml`
   and set `approval` on that row to the PR number and Daniel's handle. This declaration
   records *identity*; the manifest records *approval*. `uv run python tools/surface-guard.py`
   is RED until that row exists — but a green guard is a CONSISTENCY result, not consent: a
   change that updates the class and the manifest together passes it, so it cannot tell an
   approved addition from an unapproved one. Daniel's review is what carries the approval.
4. **Verify**: `uv run python tools/surface-guard.py` and
   `uv run pytest tests/test_sdk_surface.py tests/test_surface_manifest.py -q`.

Adding a method without steps 2–3 reds `--check`. Adding it without step 3 reds the guard.
Neither reds silently.

**Removing or renaming** a method is the mirror image, and it carries the same approval gate:
you may not remove or rename a public SDK method without Daniel's approval (the #4282 mandate).
The retired name then **fails and names its replacement** (#3836 (c) ruling — there is no SDK
alias layer, no warning shim, and no call telemetry). Do not delete a `def` from the class body
until the replacement it points a caller at exists.

## Related

- `docs/product/mcp-sdk-surface.md` — the curated list the owner reads, and the approved
  baseline's human form (#3863).
- `docs/product/bridge-table.md` — Phase 0.1: every tool and its single SDK destination.
- `docs/product/canonical-sdk-methods.md` — all {len(frozen)} methods in groups (#1521).
- `docs/product/beta-sdk-surface.md` — the **target** set. A target is not a description of
  today; this declaration describes **today**.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Declare and check the TortoiseSDK public surface.")
    ap.add_argument("--check", action="store_true", help="verify only; do not write")
    ap.add_argument("--declaration", type=Path, default=DECLARATION_FILE)
    ap.add_argument("--doc", type=Path, default=DOC_FILE)
    ap.add_argument("--manifest", type=Path, default=MANIFEST_FILE)
    args = ap.parse_args(argv)

    try:
        live_ast = declared_from_ast()
        reflected = reflected_from_runtime()
        approved = approved_from_manifest(args.manifest)
        # `--render` CREATES the declaration, so reading it is only a precondition of
        # `--check`. Requiring it to pre-exist would make the first render impossible.
        frozen, declaration_text = (
            load_declaration(args.declaration) if args.check else (set(), "")
        )
    except SurfaceEvidenceUnreadable as exc:
        return _refuse(exc)

    json_text = render_json(live_ast)
    doc_text = render_doc(live_ast, reflected, approved)

    if args.check:
        try:
            problems = reconcile_declaration(frozen, live_ast, reflected, approved)
            # Text staleness is a separate check from the name sets: it catches a hand-edited
            # metadata field, which the name-set comparison cannot see. It is performed on
            # WHATEVER path was actually read — never gated on Path equality with ANY default.
            # Gating on equality failed OPEN: a relative path (or a symlink, or a `./`-prefixed
            # spelling) that resolves to the SAME file compared unequal, so a corrupted `count`
            # was silently accepted (#4516 review). Compare the file that was read (`frozen`
            # and `declaration_text` come from ONE read), or resolve both paths; never compare
            # spellings. The doc read is an EVIDENCE read and refuses in the same contract as
            # the other four surfaces — a directory or unreadable file is a refusal, not a
            # traceback.
            if declaration_text != json_text:
                problems.append(
                    f"{args.declaration} is stale — it does not match the code. "
                    "Run: uv run python tools/sdk_surface.py"
                )
            if not args.doc.exists() or _read_text_or_refuse(args.doc, "the doc") != doc_text:
                problems.append(
                    f"{args.doc} is stale. Run: uv run python tools/sdk_surface.py"
                )
        except SurfaceEvidenceUnreadable as exc:
            return _refuse(exc)

        if problems:
            print("::error::the SDK declaration and the code disagree.")
            for i, problem in enumerate(problems, 1):
                print(f"{i}. {problem}\n")
            return 1
        print(
            f"OK — the declaration matches the code and the approved baseline: "
            f"{len(frozen)} public {CLASS_NAME} methods "
            f"(AST {len(live_ast)}, reflection {len(reflected)}, approved {len(approved)})."
        )
        return 0

    # `--render` REFUSES to write when the two code-derived views disagree. Writing the doc
    # would freeze the disagreement into an artifact, and the next reader could not tell
    # which of the two was right (the bridge_table.py precedent).
    problems = reconcile_code_views(live_ast, reflected)
    if problems:
        print("::error::the SDK surface refuses to render — the derivations disagree.")
        for i, problem in enumerate(problems, 1):
            print(f"{i}. {problem}\n")
        return 1

    args.declaration.parent.mkdir(parents=True, exist_ok=True)
    args.doc.parent.mkdir(parents=True, exist_ok=True)
    args.declaration.write_text(json_text, encoding="utf-8")
    args.doc.write_text(doc_text, encoding="utf-8")
    print(f"wrote {args.declaration}")
    print(f"wrote {args.doc}")
    print(f"  declared public {CLASS_NAME} methods: {len(live_ast)}")
    print(f"  AST {len(live_ast)} · reflection {len(reflected)} · approved {len(approved)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
