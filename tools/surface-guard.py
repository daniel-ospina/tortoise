#!/usr/bin/env python3
"""The D2 expansion gate for the agent-facing surface (#3863).

WHAT THIS CATCHES
    An UNRECORDED change to the agent-facing surface: a new (or removed, or
    re-bound, or re-served) MCP tool or public SDK method that the frozen
    baseline in `config/surface-manifest.yml` does not declare, and a changed
    DECLARATION behind a frozen-baseline name — a parameter default or an annotation,
    which the MCP `inputSchema` publishes and the code object does not contain
    (#4192). The MCP tool surface and the SDK endpoints are the contract every
    agent depends on; drifting in a diff nobody reads is the defect this gate
    exists to prevent.

WHAT THIS DOES NOT DO — READ IT BEFORE TRUSTING A GREEN RUN
    It does NOT prove that a human approved an expansion. It compares two
    artifacts, so an expansion that updates the registry AND the baseline
    consistently PASSES it — the gate cannot tell an approved addition from an
    unapproved one. The approval itself is carried by the #4282 mandate
    (approval from Daniel FIRST; see `tortoise/tool_registry.py`,
    `tortoise/sdk.py` and `CONTRIBUTING.md`) and by Daniel's review. A green run
    is a consistency result, never consent. Do not add a machine control
    (repository ruleset, CODEOWNERS, required reviewer) in place of the mandate:
    the owner REJECTED that direction on #4282 as over-engineering.

HOW IT WORKS
    It EXECUTES the declaration (imports TOOL_REGISTRY, introspects
    TortoiseSDK) and compares it against the FROZEN baseline in
    `config/surface-manifest.yml`. It never reads the declaration as text, so a
    rename that keeps the text similar still shows up as one removal plus one
    addition.

    The baseline is FROZEN. This gate deliberately does not regenerate it: if it
    did, a new registry entry would enter the baseline by itself and the gate
    could never go red. A re-cut (`tools/surface_manifest.py cut`) is a human act
    that must follow the #4282 mandate — approval from Daniel FIRST.

    THE APPROVAL IS READ, NOT TYPED (#4185). The top-level
    `approval_status`/`approval_principal`/`approval_pr`/`approval_date` are READ
    from `docs/product/canonical-mcp-tools.md`. This gate reds when a manifest that
    CLAIMS an approval disagrees with that document (a hand-edit made the copy
    lie). A manifest staged on `pending-owner-approval` with all four fields null
    makes NO claim and is skipped on purpose — a shape no committed manifest carries
    now that the flip has landed (#4185 landed the tooling in #8026; #8144 flipped the
    committed copy). The old PER-ROW
    `approval` requirement is GONE — it belonged to the frozen-snapshot model the
    #4282/#4185 ruling overrides. The per-row field is still carried as an audit
    record and a re-cut still refuses to blank it silently (#4598); it is no
    longer the approval gate.

    RETIRED NAMES (#3883)
    A retired name is removed from `TOOL_REGISTRY` but must still RESOLVE, and must
    still WARN the caller with the replacement. This gate therefore checks the
    `retired:` block of the baseline too: a name recorded as retired must resolve
    through the warning shim, must not be advertised any more, and may not enter or
    leave the retired set without a re-cut.

FAIL-CLOSED
    A missing, unreadable or malformed manifest is a FAILURE, not a skip. A gate
    that cannot read its evidence must not report success — that is how the
    #1382 regression class stayed green for days (see tools/skip-guard.py).

    Exit 0 = declaration matches the FROZEN baseline (the freeze is not the approved
    target — see docs/product/canonical-mcp-tools.md and tools/surface-target-check.py).
    Exit 1 = expansion, removal, a served-surface change, an exemption
             transition, a CLAIMED approval that disagrees with
             docs/product/canonical-mcp-tools.md, a live surface grown past the
             frozen `high_water_counts` pin, an unresolvable retired name, or
             unreadable evidence.

Usage:
    uv run python tools/surface-guard.py [--manifest config/surface-manifest.yml]
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/surface-guard.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/surface-guard.py`"
    )

import argparse
import ast as _ast
import types
from pathlib import Path

# #4828: the served-identity helpers below — `_code_digest`, `_attribute_digest`,
# `_served_attributes` AND `_component_fingerprint` — are DELIBERATE duplicates of the same
# helpers in `tools/surface_manifest.py`. This gate is PINNED TO THE BASE REF, so it must be
# self-contained: a module it imported would be PR-authored, and the PR it measures could
# then hand it a digest returning the baseline's recorded values. In
# `tests/test_surface_manifest.py` the net is
# `test_the_two_drivers_agree_on_the_served_identity_helpers` (code parity over all four),
# `test_both_drivers_fingerprint_the_same_component_identically`,
# `test_the_attribute_encoding_is_pinned_to_measured_values` and
# `test_the_pinned_gate_imports_only_its_allow_listed_modules`: a change to ONE copy reds
# there. FOUR is the whole inventory — `_frontmatter` is legitimately divergent (different
# exception types per side) and `main()` is not shared. Keep the copies in step; do not
# unify them. That net is ARMED for a `tools/`-only diff by the
# `tools/ci_selection.py::CORE_ALSO` entries for both driver paths (pinned by
# `tests/test_ci_selection.py::test_surface_tools_change_selects_core_not_tier1`) — without
# them `tools/` is swallowed by `NON_PYTHON_PREFIXES`, a driver-only diff selects NO
# surface, and this file's whole test file is a `slow_files` member that would never run.


def _code_digest(code) -> str:
    """Move-invariant, implementation-complete digest of a code object.

    `co_code` alone is NOT enough: a constant is loaded as `LOAD_CONST <index>`,
    so two handlers with the same opcode shape but different constants or names
    hash identically (verified: 98 tools collapsed to 62 digests, and a read
    tool shared one with a destructive write tool).  `co_firstlineno` is
    excluded on purpose — a pure code move is not a served change.

    This is the CODE OBJECT only, and it is the `served_from` half of a tool's recorded
    identity. The other half — the parts of the function that live outside its code object
    and are still caller-visible — is `_served_attributes`; a tool is frozen by BOTH cells.
    """
    import hashlib

    h = hashlib.sha256()
    h.update(code.co_code)
    for attr in ("co_names", "co_varnames", "co_freevars", "co_cellvars"):
        h.update(repr(getattr(code, attr)).encode())
    for const in code.co_consts:
        # a nested code object must be recursed, never repr'd (its repr carries
        # a memory address and is not stable)
        h.update(_code_digest(const).encode() if isinstance(const, types.CodeType)
                 else repr(const).encode())
    return h.hexdigest()[:16]


def _attribute_digest(value) -> bytes:
    """A STABLE encoding of one function attribute: a default, a kw-default, an annotation.

    `repr` is the fallback, and it is stable for every value on this surface today (all 53
    defaulted tool functions carry only `None`/`int`/`float`/`str`/`bool`, and every
    annotation is a string under `from __future__ import annotations`). A code-bearing value
    must be RECURSED, never repr'd: a lambda default's repr carries a memory address
    (`<function <lambda> at 0x…>`), so repr'ing it would make the fingerprint differ between
    two runs over an UNCHANGED tree — the same reason `_code_digest` recurses into `co_consts`
    rather than repr'ing a nested code object.
    """
    if isinstance(value, types.CodeType):
        return _code_digest(value).encode()
    if isinstance(value, types.FunctionType):
        # A callable default has BOTH halves too, and its code object is in NEITHER cell by
        # itself: a `def` statement's default is evaluated in the DEFINING scope, so a lambda's
        # code lands in the MODULE's `co_consts` — which nothing here digests. Recursing into
        # both halves is what makes a changed lambda BODY visible; `_served_attributes` alone
        # does not (measured: a test that only compared it stayed green on a changed body).
        return f"{_code_digest(value.__code__)}:{_served_attributes(value)}".encode()
    return repr(value).encode()


def _served_attributes(fn) -> str:
    """The parts of a SERVED function that live OUTSIDE its code object, which a caller sees.

    `_code_digest` reads the code object — bytecode, names, constants. A function's
    `__defaults__`, `__kwdefaults__` and `__annotations__` are NOT in it: they hang off the
    function object, built by the `def` statement's `MAKE_FUNCTION` in the DEFINING module's
    code object. All three are served behaviour — the MCP `tools/list` JSON schema publishes
    each of them as a `type` / `default` / `anyOf` — so

        def tortoise_search(q, limit=10): ...
        def tortoise_search(q, limit=99): ...

    is a different advertised surface under ONE code object. #4192 (verified on this tree: a
    `types.FunctionType` over a served tool's `__code__` with a tampered `__defaults__`
    replaced the handler behind `tortoise_search` while this gate stayed at exit 0).

    WHY ITS OWN MANIFEST CELL, NOT FOLDED INTO `served_from` (#4192). The two halves answer two
    questions — "which implementation" and "which declaration" — so recording them apart
    keeps a diff readable: an edit inside a handler moves `served_from` alone, a changed
    default or annotation moves `served_attributes` alone. The load-bearing reason is CI:
    `surface-guard.py` is PINNED TO THE BASE REF (#5132/#4606), so the run that measures a PR
    uses the BASE ref's copy of this file and cannot know an input added here. Folding these
    attributes into `served_from` would re-stamp all 82 rows, and the pinned guard would then
    red on every one of them — measured on this change: 82 findings and exit 1 while the
    tree's OWN guard was green, i.e. the required check was unsatisfiable by ANY PR. A new
    cell leaves `served_from` byte-identical, so the pin keeps enforcing the implementation
    half while this half is enforced by `tools/surface_manifest.py::check` (the derivation
    against the artifact) at PR time and by this guard on the tree itself.
    """
    import hashlib

    h = hashlib.sha256()
    # Positional defaults, in order: the order IS served (the schema's parameters follow the
    # signature), so the tuple is hashed as given.
    for value in getattr(fn, "__defaults__", None) or ():
        h.update(b"\x00default\x00")
        h.update(_attribute_digest(value))
    # Keyword-only defaults, in DECLARATION order (`__kwdefaults__` is a dict, and the order
    # the schema publishes is the signature's).
    for key, value in (getattr(fn, "__kwdefaults__", None) or {}).items():
        h.update(b"\x00kwdefault\x00")
        h.update(repr(key).encode())
        h.update(b"\x1f")
        h.update(_attribute_digest(value))
    for key, value in (getattr(fn, "__annotations__", None) or {}).items():
        h.update(b"\x00annotation\x00")
        h.update(repr(key).encode())
        h.update(b"\x1f")
        h.update(_attribute_digest(value))
    return h.hexdigest()[:16]


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_MANIFEST = ROOT / "config" / "surface-manifest.yml"
CANONICAL_DOC = ROOT / "docs" / "product" / "canonical-mcp-tools.md"


def _frontmatter(path: Path) -> dict:
    """The YAML frontmatter of a product doc, or raises when it carries none
    (missing / unclosed / non-mapping / unparseable).

    Deliberately NOT a `{}` fallback: a gate that cannot read its evidence must
    not report success, and an empty mapping reads as "nothing to check".
    """
    import yaml

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing — the owner's approval lives there, and a gate that "
            "cannot read its evidence must not report success."
        )
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(
            f"{path} carries no YAML frontmatter — the owner's approval is recorded there, "
            "and a gate that cannot read its evidence must not report success."
        )
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError(f"{path} opens a frontmatter block that is never closed")
    data = yaml.safe_load(text[3:end])
    if not isinstance(data, dict):
        raise ValueError(f"{path}'s frontmatter is not a mapping")
    return data


def _canonical_approval(path: Path | None = None) -> dict:
    """The owner's approval, READ from `docs/product/canonical-mcp-tools.md`.

    Self-contained on purpose: `tools/surface-guard.py` is pinned to the base ref in CI
    precisely so a PR cannot influence the gate through the checkout's own modules, so
    this reader must not import one. `tools/surface_manifest.py::read_approval` is the
    sibling for the generator side; the two are intentionally separate copies, exactly
    as `_read_manifest` already is.

    OVERRIDES: the frozen-snapshot-as-approval-baseline practice — a snapshot of the implementation cannot be the approved target; the target is read from docs/product/canonical-mcp-tools.md (owner, 26/40, re-confirmed 2026-10-10).
    """
    fm = _frontmatter(Path(path) if path else CANONICAL_DOC)
    if not fm.get("approval_status"):
        raise ValueError(
            f"{path or CANONICAL_DOC} records no `approval_status`, so it is not an "
            "approval record."
        )
    pr = fm.get("approval_pr")
    approved = fm.get("approved")
    return {
        "status": str(fm["approval_status"]),
        "principal": str(fm["approvedBy"]) if fm.get("approvedBy") else None,
        # RAW truthiness, matching `surface_manifest.py::read_approval`: a raw `0`, `false`
        # or `[]` is falsy to the generator, so it must be falsy here too. Stringifying
        # first turns `0` into `"0"` and lets this gate honour an approval the generator
        # refuses (found by review, 2026-10-10).
        "pr": str(pr) if pr else None,
        "date": str(approved) if approved is not None else None,
    }


def _component_fingerprint(fn) -> str | None:
    """Code-object identity — see the note in tools/surface_manifest.py.

    Built from `co_filename` and a digest of the bytecode, names and constants
    (`_code_digest`) rather than from `__module__`/`__qualname__`, because those
    are writable strings a shadow can simply copy from the tool it is replacing.
    `co_firstlineno` is deliberately NOT part of the identity: a pure code move
    (an edit elsewhere in the file) is not a change to the served implementation,
    yet it shifts every handler below it and reddened the gate on an unchanged
    surface.

    The half of a served tool that is NOT a code object — its defaults and
    annotations — is fingerprinted separately, by `_served_attributes` and the
    `served_attributes` cell (#4192).

    MODULE LEVEL, not nested in `main()` (#4828 review P2): the nested copy could not
    be seen by `tests/test_surface_manifest.py::_helper_code`, which compares
    module-level definitions by the same NAME in both drivers — so the fourth and
    last duplicate in the served-identity chain (`_served_attributes` ->
    `_attribute_digest` -> `_code_digest` -> this) was the one link nothing compared.
    Hoisting and sharing the manifest side's name makes it addressable by the same
    lockstep contract as the other three; the body is unchanged.
    """
    code = getattr(fn, "__code__", None)
    if code is None:
        return None
    # REPO-RELATIVE, never absolute: the baseline is checked in and CI checks out to a
    # different path, so an absolute `co_filename` would differ there and red the gate
    # on an unchanged tree.
    try:
        rel = Path(code.co_filename).resolve().relative_to(ROOT.resolve())
    except Exception:
        rel = Path(code.co_filename).name
    return f"{rel}:{_code_digest(code)}"


def die(message: str) -> int:
    print(f"::error::{message}")
    print(f"FAIL {message}")
    return 1


def _source_transforms(path: Path | None = None) -> set[str]:
    """`add_transform(...)` call sites in the declaration — the CI-visible source of truth."""
    target = path or (ROOT / "tortoise" / "mcp_server.py")
    found: set[str] = set()
    for node in _ast.walk(_ast.parse(target.read_text())):
        if (
            isinstance(node, _ast.Call)
            and isinstance(node.func, _ast.Attribute)
            and node.func.attr == "add_transform"
            and node.args
        ):
            arg = node.args[0]
            found.add(
                arg.func.id
                if isinstance(arg, _ast.Call) and isinstance(arg.func, _ast.Name)
                else _ast.unparse(arg)
            )
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument(
        "--approval-doc",
        default=str(CANONICAL_DOC),
        help="the owner's approval record the manifest's approval is DERIVED from (#4185)",
    )
    args = parser.parse_args(argv[1:])

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        return die(
            f"frozen surface baseline not found at {manifest_path}. "
            "The gate cannot verify the surface without its baseline, and a gate that "
            "cannot read its evidence must not report success. Restore the baseline "
            "(it is checked in), or cut a new one in a reviewed PR."
        )

    try:
        import yaml

        with manifest_path.open() as fh:
            doc = yaml.safe_load(fh)
    except Exception as exc:
        return die(f"could not read the frozen surface baseline {manifest_path}: {exc}")

    if not isinstance(doc, dict) or "rows" not in doc:
        return die(f"{manifest_path} is malformed: expected a mapping with a `rows:` key.")

    rows = doc.get("rows")
    if not isinstance(rows, list):
        return die(
            f"{manifest_path} is malformed: `rows:` must be a list (got {type(rows).__name__})."
        )
    malformed = [r for r in rows if not isinstance(r, dict) or "name" not in r]
    if malformed:
        return die(f"malformed row(s) in the baseline: {malformed!r:.200}")

    # A ROW'S NAME IS THE KEY EVERY COMPARISON BELOW USES, so it must be a non-empty
    # STRING before anything dereferences it. `name: null` (or an int, or a list)
    # reached `.startswith` at the served-surface check and `{r["name"]}` at
    # `baseline_tools`, and escaped as a bare AttributeError/TypeError traceback — and
    # a traceback is not the fail-closed `die` this file promises. Verified: a row with
    # `name: null`, `name: 1` and `name: [a, b]` each crashed the gate.
    bad_name = [r for r in rows if not isinstance(r.get("name"), str) or not r["name"]]
    if bad_name:
        return die(
            "malformed row name(s) in the baseline: every row's `name` must be a non-empty "
            f"string, got {[(r.get('name'), type(r.get('name')).__name__) for r in bad_name][:5]}."
        )

    # A DUPLICATE NAME is unverified content, not a harmless repetition. The rows below are
    # keyed by name (`baseline_tools`) and the SDK rows by `method` (`baseline_sdk`), and a
    # name-keyed comparison silently DROPS all but the last — a doctored duplicate left the
    # guard green while the baseline carried fabricated content. The predicate is
    # deliberately IDENTICAL to `tools/surface_manifest.py::_read_manifest` (including its
    # `str()` coercion): two readers that disagree about what a duplicate IS are one gate
    # with a gap, and the divergence (a truthiness filter here vs. none there) was measured.
    for field in ("rows", "retired"):
        entries = doc.get(field)
        if not isinstance(entries, list):
            continue  # absence/shape is reported by the dedicated checks
        names = [str(r.get("name")) for r in entries if isinstance(r, dict)]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            return die(
                f"the frozen baseline's `{field}` carries duplicate name(s): {duplicates[:5]}. "
                "A name-keyed comparison drops all but the last, so the duplicate is content "
                "no check here verifies. Re-cut the baseline."
            )

    # AN SDK ROW IS `sdk:<method>` — THE NAME IS NOT FREE TEXT. `baseline_sdk` (below) is a
    # set of `method`s, so a row whose NAME is new but whose METHOD already exists adds
    # nothing to the comparison: a fabricated row for a brand-new public SDK method passed
    # this gate (verified — `name: sdk:totally_new_and_unapproved` with an existing `method`
    # and `exemption: true` → exit 0, while the same method WITHOUT the row was refused), and
    # so did renaming a real SDK row. The derivation emits the name as `sdk:<method>` exactly
    # (verified for all 150 rows), so that identity is enforced here, before the sets are
    # built. It SUBSUMES a duplicate-`method` check: two rows can only share a method if a
    # name is not its method (caught here) or a name repeats (caught above).
    sdk_rows = [r for r in rows if r["name"].startswith("sdk:")]
    stray = [r["name"] for r in sdk_rows if not r.get("method")]
    if stray:
        return die(f"SDK row(s) in the baseline carry no `method`: {stray[:5]}.")
    mismatched = [r["name"] for r in sdk_rows if r["name"] != f"sdk:{r['method']}"]
    if mismatched:
        return die(
            f"SDK row name(s) that do not identify their method: {mismatched[:5]}. An SDK row "
            "is `sdk:<method>` — the derivation emits it that way, and a name that differs from "
            "its method is a row no comparison here can tie to a real method."
        )

    # --- execute the declaration (never read it as text) ---------------------
    try:
        from tortoise import mcp_server
        from tortoise.sdk import TortoiseSDK
        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY
    except Exception as exc:
        return die(f"could not import the surface declaration: {exc}")

    declared_tools = {entry.name for entry in TOOL_REGISTRY}

    # THE ADVERTISED SURFACE, not just the registry. A tool can reach agents without
    # ever entering TOOL_REGISTRY, by two different paths:
    #   * a `@mcp.tool()`-decorated function in tortoise/mcp_server.py, or a direct
    #     `mcp.add_tool(...)` call;
    #   * a server-level **transform** (`mcp.add_transform`) whose `list_tools` appends a
    #     tool and whose `get_tool` routes it. `mcp_server.py` already uses exactly that
    #     API for `_HTTPToolFilter`, so the pattern is live here, not hypothetical.
    #
    # The two paths need two different enumerations, and neither alone is sufficient:
    #   * `_list_tools()` is the pre-transform aggregate — it sees `add_tool` registrations
    #     but NOT transform-injected ones;
    #   * `list_tools()` is the protocol `tools/list` path, which applies transforms — it
    #     sees transform-injected tools, but it ALSO applies auth/visibility middleware, so
    #     outside a live request context it returns a filtered subset (measured: 89 in a
    #     pytest process vs 99 declared, with an `add_tool`-registered tool filtered out).
    #
    # So the served set is the UNION of both. Being undeclared in either view is a
    # violation, which is what makes the check independent of auth context. Both are
    # needed: a `list_tools`-only check was defeated by an `add_tool` registration under
    # filtered middleware, and an `_list_tools`-only check was defeated by a transform
    # (verified against a live `fastmcp.Client`, which received the tool either way).
    # Fail closed if EITHER enumeration cannot be read.
    try:
        import asyncio

        served_tools: set[str] = set()
        for label, lister in (
            ("_list_tools", mcp_server.mcp._list_tools),
            ("list_tools", mcp_server.mcp.list_tools),
        ):
            try:
                served_tools |= {
                    getattr(t, "name", None) or t["name"] for t in asyncio.run(lister())
                }
            except Exception as exc:
                return die(
                    f"could not enumerate the served MCP tool surface via `{label}`: "
                    f"{type(exc).__name__}: {exc}. The gate cannot verify which tools agents "
                    "are actually offered, so it refuses to pass."
                )
    except Exception as exc:
        return die(
            f"could not enumerate the served MCP tool surface: {type(exc).__name__}: {exc}. "
            "The gate cannot verify which tools agents are actually offered, so it refuses "
            "to pass."
        )
    declared_sdk = {
        name
        for name in dir(TortoiseSDK)
        if not name.startswith("_") and callable(getattr(TortoiseSDK, name))
    }

    baseline_tools = {r["name"] for r in rows if not str(r.get("name", "")).startswith("sdk:")}
    baseline_sdk = {
        r["method"] for r in rows if str(r.get("name", "")).startswith("sdk:") and r.get("method")
    }
    exemptions = {
        r["method"]
        for r in rows
        if str(r.get("name", "")).startswith("sdk:") and r.get("exemption") is True
    }

    problems: list[str] = []

    undeclared_served = sorted(served_tools - declared_tools)
    if undeclared_served:
        problems.append(
            f"the MCP server advertises {len(undeclared_served)} tool(s) that are NOT in "
            f"TOOL_REGISTRY, so they bypass this gate entirely: {undeclared_served}. "
            "Register them in TOOL_REGISTRY (and re-cut the baseline) or remove them."
        )

    # --- the TRANSFORM SET: the only route to a `get_tool`-only phantom -----------
    # A transform can route `get_tool` without appending to `list_tools`, so the tool is
    # callable over the protocol while appearing in NEITHER enumeration (verified: a live
    # `fastmcp.Client` called it while the guard exited 0). Since intercepting resolution
    # requires a registered transform, so gating the transform set closes the ACCIDENTAL
    # version of that route (and the frozen-baseline-name check catches a foreign class
    # reusing a frozen-baseline name).
    #
    # THREAT SURFACE, DECLARED — this is what the gate does and does not cover:
    #   in scope:  a tool that becomes reachable as a SIDE EFFECT of a change to the
    #              declaration, the registry, or the registered providers/transforms —
    #              the accidental-expansion class, which is how this surface grew to 99.
    #   out of scope: a party who deliberately writes code in-process to defeat it — a
    #              class that copies a frozen-baseline transform's identity, a function that
    #              forges `__code__`, or simply editing this guard. That party has commit
    #              access to these files, so no gate here can constrain them; the control
    #              for that class is required review, not this check (§ CONTRIBUTING.md).
    #              Residuals of this class are follow-ups, not merge blockers.
    baseline_transforms = doc.get("allowed_transforms")
    if not isinstance(baseline_transforms, list):
        return die(
            "the baseline carries no `allowed_transforms` list, so the guard cannot tell "
            "whether a server transform was added. Re-cut the baseline."
        )
    # A SUBSET test, not equality: `mcp._transforms` is lifecycle-dependent (`_HTTPToolFilter`
    # appears only once the HTTP app is built), so requiring an exact match would red
    # spuriously. What matters is that nothing BEYOND the frozen set is registered.
    #
    # TWO SOURCES, because neither alone can fire in the process CI runs:
    #   * RUNTIME `mcp._transforms` — catches a transform registered in-process;
    #   * SOURCE `add_transform(...)` call sites — catches a transform added to
    #     `mcp_server.py`. This is the one that matters in CI: the only `add_transform`
    #     in the tree sits INSIDE `create_http_app()`, which the guard never calls, so
    #     `_transforms` is `[]` at guard time and a runtime-only check is inert here
    #     (measured: a source-level transform adding a tool was invisible).
    runtime_transforms = {type(_t).__name__ for _t in mcp_server.mcp._transforms}
    source_transforms = _source_transforms()
    live_transforms = sorted(runtime_transforms | source_transforms)
    # The NAME alone is not the identity: a class can be called `_HTTPToolFilter` and do
    # something else, so a frozen-baseline name must also be DEFINED where the declaration lives.
    foreign_transforms = sorted(
        {
            f"{type(_t).__module__}.{type(_t).__qualname__}"
            for _t in mcp_server.mcp._transforms
            if getattr(type(_t), "__name__", None) in set(baseline_transforms)
            and type(_t).__module__ != "tortoise.mcp_server"
        }
    )
    unapproved_transforms = sorted((runtime_transforms | source_transforms) - set(baseline_transforms))
    if foreign_transforms:
        problems.append(
            f"transform class(es) reusing a frozen-baseline name but defined outside "
            f"tortoise.mcp_server: {foreign_transforms}. A class name is not an identity — "
            "a transform doing something else under a frozen-baseline name needs approval."
        )
    if unapproved_transforms:
        problems.append(
            f"unapproved server transform(s): {unapproved_transforms} "
            f"(baseline: {sorted(baseline_transforms)}, live: {live_transforms}). A transform "
            "can route tools without listing them, so it needs explicit approval and a re-cut."
        )

    # --- the SERVED IMPLEMENTATION of each frozen-baseline tool -------------------------
    # `mcp.add_tool(fn, name=<a frozen-baseline name>)` REPLACES that tool's implementation while
    # leaving the name set and the entry count identical, so it passed every check above
    # (verified: a live client then invoked the shadow function). Comparing the identity of
    # the component behind each name catches a count-preserving substitution.
    live_components: dict[str, str] = {}
    live_attributes: dict[str, str | None] = {}
    for _key, _tool in mcp_server.mcp._local_provider._components.items():
        if not _key.startswith("tool:"):
            continue
        _name = getattr(_tool, "name", None) or _key.split(":", 1)[1].split("@")[0]
        _fn = getattr(_tool, "fn", None)
        live_components[_name] = _component_fingerprint(_fn)
        live_attributes[_name] = (
            _served_attributes(_fn) if getattr(_fn, "__code__", None) is not None else None
        )
    # --- a declared tool that is no longer SERVED ---------------------------------
    # The union check above only asks whether anything undeclared is served. The inverse
    # matters too: a tool that stops being offered is a removal, which the guard's
    # docstring already promises to catch — but nothing computed it, so popping a
    # component left the guard green (verified).
    missing_served = sorted(baseline_tools - served_tools)
    if missing_served:
        problems.append(
            f"{len(missing_served)} tool(s) in the frozen baseline are NOT served by the "
            f"MCP server any more: {missing_served}. A declared tool that silently stops "
            "being offered is a removal and needs approval."
        )

    for row in rows:
        name = row.get("name")
        stored = row.get("served_from")
        if not name or str(name).startswith("sdk:"):
            continue
        # A null fingerprint is malformed EVIDENCE, not "nothing to check": treating it as
        # skippable re-opened the exact same-name-substitution hole this check exists to
        # close, reachable by editing only the baseline (verified: a null `served_from` plus
        # an `add_tool` shadow exited 0).
        if stored is None:
            return die(
                f"the baseline records no `served_from` fingerprint for `{name}`, so the guard "
                "cannot tell whether the implementation behind that name was replaced. "
                "Re-cut the baseline."
            )
        if name not in live_components:
            continue  # absence is already reported by the missing-served check above
        live = live_components[name]
        # A PRESENT but unfingerprintable component (no `__code__`, e.g. a
        # functools.partial) is malformed evidence, not absence: treating it as
        # skippable let a same-name substitution with no code object through
        # (verified: a partial shadow of a frozen-baseline tool exited 0).
        if live is None:
            problems.append(
                f"the tool served as `{name}` has no code-object identity (its callable "
                "has no `__code__`), so the guard cannot tell whether the implementation "
                "behind that name was replaced. Register a plain function, or re-cut "
                "the baseline."
            )
            continue
        if live != stored:
            problems.append(
                f"the implementation served as `{name}` changed: the baseline recorded "
                f"{stored!r}, the server now serves {live!r}. Replacing a frozen-baseline tool's "
                "implementation is a surface change and needs explicit approval — rename the "
                "new tool, or re-cut the baseline once the change is approved (CONTRIBUTING.md)."
            )
        # --- the SERVED DECLARATION of each frozen-baseline tool (#4192) --------------------
        # A parameter default / annotation is not in the code object, and the MCP schema
        # publishes it, so `add_tool` over the same `__code__` with a tampered `__defaults__`
        # served different behaviour under ONE `served_from` (verified: exit 0 before this
        # check existed). Same doctrine as the cell above: a null is malformed EVIDENCE, not
        # "nothing to check".
        stored_decl = row.get("served_attributes")
        if stored_decl is None:
            return die(
                f"the baseline records no `served_attributes` fingerprint for `{name}`, so "
                "the guard cannot tell whether the caller-visible defaults or annotations "
                "behind that name changed. Re-cut the baseline."
            )
        live_decl = live_attributes.get(name)
        if live_decl is not None and live_decl != stored_decl:
            problems.append(
                f"the served declaration of `{name}` changed: the baseline recorded "
                f"{stored_decl!r}, the server now serves {live_decl!r}. A parameter default "
                "or an annotation IS served behaviour — the MCP inputSchema is derived from "
                "them — so one of them changed the advertised surface and needs explicit "
                "approval: restore it, or re-cut the baseline in the approved PR."
            )

    # A duplicate-name entry is an unapproved registry entry the name-set comparison
    # cannot see: `declared_tools` is a set, so a second ToolDefinition reusing a
    # frozen-baseline name leaves the guard green while the registry grew.
    baseline_tool_count = sum(
        1 for r in rows if isinstance(r, dict) and not str(r.get("name", "")).startswith("sdk:")
    )
    if len(TOOL_REGISTRY) != baseline_tool_count:
        problems.append(
            f"TOOL_REGISTRY has {len(TOOL_REGISTRY)} entries but the frozen baseline has "
            f"{baseline_tool_count} — the registry changed in a way the name comparison cannot "
            "see (a duplicate name, or a removed-and-added pair)."
        )

    # --- 1. expansion: a tool or SDK method the owner never approved ---------
    for name in sorted(declared_tools - baseline_tools):
        problems.append(
            f"NEW MCP TOOL `{name}` is registered but is not in the frozen baseline. "
            "Adding a tool expands what every agent can call; it needs an explicit human "
            "decision (see #3863). If it is approved, record the approval in "
            "docs/product/canonical-mcp-tools.md and re-cut the baseline (CONTRIBUTING.md)."
        )
    for name in sorted(declared_sdk - baseline_sdk - exemptions):
        problems.append(
            f"NEW PUBLIC SDK METHOD `TortoiseSDK.{name}` is not in the frozen baseline. "
            "A public method is an endpoint; it needs an explicit human decision (see #3863)."
        )

    # --- 2. removal: a frozen-baseline entry that disappeared ---------------------
    for name in sorted(baseline_tools - declared_tools):
        problems.append(
            f"REMOVED MCP TOOL `{name}` was in the frozen baseline and is gone from the "
            "declaration. Silently dropping a surface entry breaks callers; if this is "
            "intended it is an owner decision and the baseline must be updated with it."
        )
    for name in sorted(baseline_sdk - declared_sdk):
        problems.append(
            f"REMOVED SDK METHOD `TortoiseSDK.{name}` was in the frozen baseline and is gone."
        )

    # --- 3. the served surface moved ---------------------------------------
    try:
        from tortoise.mcp_auth import HTTP_ALLOWED

        served_http = set(HTTP_ALLOWED)
    except Exception as exc:
        # FAIL-CLOSED. Every other broad handler in this file returns die(); this
        # one used to set served_http = None and skip, so a broken import turned the
        # check off and the gate still printed OK.
        return die(
            "could not read the served surface (HTTP_ALLOWED): "
            f"{type(exc).__name__}: {exc}. The gate cannot verify which tools are "
            "reachable, so it must not report success."
        )
    SERVED_VALUES = ("http", "stdio-only")
    for row in rows:
            name = row["name"]
            if name.startswith("sdk:"):
                if row.get("served") != "sdk":
                    return die(
                        f"`{name}`: an SDK row must be `served: sdk`, got {row.get('served')!r}."
                    )
                continue
            # A FALSY `served` USED TO SKIP THIS CHECK. `not row.get("served")` `continue`d,
            # so `served: null` (or `""`) turned the served-surface comparison OFF and the
            # gate still printed OK — the same defect class this file already fixed for a
            # broken import. A value the check cannot classify is malformed EVIDENCE.
            if row.get("served") not in SERVED_VALUES:
                return die(
                    f"`{name}`: `served` must be one of {list(SERVED_VALUES)}, got "
                    f"{row.get('served')!r}. A value this comparison cannot classify must not "
                    "be skipped."
                )
            now = "http" if name in served_http else "stdio-only"
            if now != row["served"]:
                problems.append(
                    f"`{name}` changed how it is served: the baseline says {row['served']}, "
                    f"the declaration now says {now}. Which servers expose a tool is part of "
                    "the surface."
                )

    # --- 3b. a tool's declared SDK binding moved ---------------------------
    # CONTRIBUTING promised this and the guard did not do it: `sdk_method` could be
    # silently re-pointed on any tool while the required check stayed green.
    baseline_binding = {
        r["name"]: r.get("sdk_method")
        for r in rows
        if isinstance(r, dict) and not str(r.get("name", "")).startswith("sdk:")
    }
    for entry in TOOL_REGISTRY:
        if entry.name not in baseline_binding:
            continue
        was = baseline_binding[entry.name]
        now = getattr(entry, "sdk_method", None) or None
        if (was or None) != (now or None):
            problems.append(
                f"`{entry.name}` changed its declared SDK binding: the baseline records "
                f"{was!r}, the declaration now says {now!r}. Which method a tool fronts is "
                "part of the surface."
            )

    # --- 4. the exemption list cannot outlive the fact it records ----------
    # A method on the exemption list that HAS become reachable must be removed
    # from the list, not silently carried. This mirrors the sibling resolution
    # test's strict xfail ledger (tests/test_surface_resolution.py).
    bound = {getattr(entry, "sdk_method", "") for entry in TOOL_REGISTRY}
    for method in sorted(exemptions):
        if method in bound:
            problems.append(
                f"`TortoiseSDK.{method}` is marked `exemption: true` but is now bound by a "
                "registered tool. An exemption records a fact that is no longer true; "
                "remove the exemption rather than letting it stand."
            )

    # --- 5. retired names (#3883): a retired name RESOLVES and WARNS ---------
    # Retiring a name is a surface change like adding one, so the name must be in the
    # frozen baseline's `retired:` block or the gate reds. The inverse matters more:
    # #3883 makes the WARNING the contract. If a name is recorded as retired and
    # `get_tool` no longer resolves it, a caller gets a bare "tool not found" — the
    # silent removal this mechanism exists to prevent. So the gate EXECUTES the
    # declaration's resolution path and refuses to pass on a phantom. (That the shim
    # then puts the warning IN the answer is pinned by tests/test_retired_tools.py,
    # which can build a ToolResult without a database; this check pins the wiring:
    # the tool resolves, and the function served behind it is the shim, not the
    # original handler.)
    declared_retired = {
        e.name: (getattr(e, "retired_use_instead", None) or "") for e in RETIRED_TOOL_REGISTRY
    }
    baseline_retired = doc.get("retired")
    if not isinstance(baseline_retired, list):
        return die(
            "the baseline carries no `retired:` list, so the guard cannot tell which names "
            "are retired. Re-cut the baseline (tools/surface_manifest.py cut)."
        )
    baseline_retired_map: dict[str, str] = {}
    for _row in baseline_retired:
        if not isinstance(_row, dict) or not isinstance(_row.get("name"), str) or not _row["name"]:
            return die(f"malformed retired row in the baseline: {_row!r:.200}")
        baseline_retired_map[_row["name"]] = str(_row.get("use_instead") or "")

    for name in sorted(set(declared_retired) - set(baseline_retired_map)):
        problems.append(
            f"NEW RETIRED TOOL `{name}` is declared retired but is not in the frozen "
            "baseline's `retired:` block. Retiring a name removes it from every agent's "
            "surface; it needs an explicit human decision (#3863)."
        )
    for name in sorted(set(baseline_retired_map) - set(declared_retired)):
        problems.append(
            f"`{name}` is retired in the frozen baseline but NOT declared retired. "
            "A frozen-baseline retirement that was reverted is a surface change; re-cut the baseline."
        )
    for name, use_instead in sorted(declared_retired.items()):
        if not use_instead:
            problems.append(
                f"retired tool `{name}` names no replacement. #3883 requires the warning to "
                "name the replacement where one exists."
            )
        if name in served_tools:
            problems.append(
                f"retired tool `{name}` is still ADVERTISED by the MCP server. A retired name "
                "must be resolvable on call but absent from `tools/list`."
            )
        try:
            tool = asyncio.run(mcp_server.mcp.get_tool(name))
        except Exception as exc:
            problems.append(
                f"could not resolve retired tool `{name}`: {type(exc).__name__}: {exc}. "
                "A retired name must resolve so the caller gets a warning, not a bare "
                '"tool not found" (#3883).'
            )
            continue
        if tool is None:
            problems.append(
                f"retired tool `{name}` does NOT resolve — a caller invoking it would get a "
                'silent "tool not found" instead of the #3883 warning.'
            )
            continue
        marker = ((getattr(tool, "meta", None) or {}).get("tortoise") or {}).get("retired") or {}
        if marker.get("retired") is not True:
            problems.append(
                f"retired tool `{name}` resolves but carries no retirement marker, so it would "
                "answer without warning the caller. Serve it through the #3883 shim."
            )
        elif str(marker.get("use_instead") or "") != use_instead:
            problems.append(
                f"retired tool `{name}` warns to use {marker.get('use_instead')!r} but the "
                f"declaration records {use_instead!r}."
            )
        if not (getattr(getattr(tool, "fn", None), "__doc__", "") or "").startswith(
            "RETIRED"
        ):
            problems.append(
                f"retired tool `{name}` resolves to its ORIGINAL handler, not the #3883 shim, "
                "so a caller would get no warning. Serve retired names through "
                "_RetiredToolTransform."
            )
        if baseline_retired_map.get(name) != use_instead:
            problems.append(
                f"retired tool `{name}`'s use_instead {use_instead!r} does not match the "
                f"baseline's {baseline_retired_map.get(name)!r}."
            )

    # --- 6. approval: READ from the canonical doc, never typed here (#4185) ------
    # OVERRIDES: the frozen-snapshot-as-approval-baseline practice — a snapshot of the implementation cannot be the approved target; the target is read from docs/product/canonical-mcp-tools.md (owner, 26/40, re-confirmed 2026-10-10).
    #
    # The manifest's `approval_status` / `approval_principal` / `approval_pr` /
    # `approval_date` are DERIVED from `docs/product/canonical-mcp-tools.md`, and the
    # committed copy now carries that derivation (`approved` / `daniel-ospina` / PR 4120) —
    # the staged two-PR split (#8026 landed the tooling, #8144 flipped the copy) is complete.
    # What this section verifies is
    # the one thing a derived field can still get wrong: a manifest that CLAIMS an approval
    # and was HAND-EDITED away from the source it is supposed to mirror.
    #
    # READ IT HERE, INDEPENDENTLY. The guard is deliberately self-contained, and the doc is
    # the evidence the approval rests on. A manifest claiming `approved` must be backed by a
    # canonical doc that says so, with a principal and a PR — the same three facts, read
    # from the file the owner actually edits.
    #
    # This REPLACES the old per-row `approval` requirement, which belonged to the
    # frozen-snapshot model this change overrides: it forced a fresh owner approval on
    # every surface-touching PR, which is exactly the five-times-re-asked defect (#4185).
    # The per-row field is still carried as an audit record and a re-cut still refuses to
    # blank it silently (#4598); it is no longer the approval GATE.
    #
    # CI's `surface-guard` pins the BASE copy of THIS file: it copies `origin/main`'s guard
    # over the PR's and runs it against the PR tree, so a change to what the guard
    # ENFORCES cannot be green in its own PR. That pin is why the flip was split into two
    # PRs (#8026 landed the derivation, #8144 flipped the copy): the pre-#8026 base guard
    # still carried the per-row `approval` rule, which fires on `approved`. The rule is
    # GONE from this copy, so the base guard no longer demands a recorded approval on
    # every row and every `retired:` entry.
    status = doc.get("approval_status")
    if status not in ("pending-owner-approval", "approved"):
        problems.append(
            f"`approval_status: {status!r}` is not one of "
            "'pending-owner-approval' or 'approved' — the gate cannot tell whether this "
            "surface has been approved, so it must not pass."
        )
    try:
        approval_doc = Path(args.approval_doc)
        canonical = _canonical_approval(approval_doc)
    except Exception as exc:
        return die(
            f"could not read the owner's approval from {approval_doc}: "
            f"{type(exc).__name__}: {exc}. The gate cannot tell whether this surface is "
            "approved, so it must not pass."
        )
    # A claim is either the `approved` STATUS or any non-null copy field. A manifest on
    # `pending-owner-approval` with all four fields null is the PRE-#4185 state and is
    # skipped on purpose — a shape no committed manifest carries now that the flip has
    # landed. The moment a manifest CLAIMS an approval — status or a
    # field — it must be a faithful copy of the canonical doc, and every branch below
    # applies exactly as before. So the skip is scoped to the empty claim, never a
    # tampered one.
    claimed = status == "approved" or any(
        doc.get(f) for f in ("approval_principal", "approval_pr", "approval_date")
    )
    if claimed:
        if status != canonical["status"]:
            problems.append(
                f"`approval_status` is {status!r} in the manifest but {canonical['status']!r} "
                f"in {approval_doc}. The manifest's approval is DERIVED from that document "
                "(#4185) — the manifest holds a stale COPY: re-cut (`tools/surface_manifest.py cut`, "
                "adding `--allow-approval-reset` if it reports recorded per-row references) "
                "so it re-reads the document. Edit the document only if the document is the thing "
                "that is wrong; do not hand-edit the manifest."
            )
        if status == "approved" and (
            canonical["status"] != "approved"
            or not canonical["principal"]
            or not canonical["pr"]
        ):
            problems.append(
                f"`approval_status: approved` is not backed by {approval_doc}: it records "
                f"status={canonical['status']!r}, principal={canonical['principal']!r}, "
                f"pr={canonical['pr']!r}. The approval is READ from that document, not "
                "typed into the manifest."
            )
        # No hand-set field may disagree with the source of truth. The manifest carries a
        # COPY of the approval for readers; this catches an edit that made the copy lie.
        for field, want in (
            ("approval_principal", canonical["principal"]),
            ("approval_pr", canonical["pr"]),
            ("approval_date", canonical["date"]),
        ):
            got = doc.get(field)
            if (got or None) != (want or None):
                problems.append(
                    f"`{field}` is {got!r} in the manifest but {want!r} in "
                    f"{approval_doc}. The manifest's approval is DERIVED from that document — if the "
                    "document itself is missing a field, record it there (a re-cut refuses an `approved` "
                    "with no principal or PR); otherwise re-cut (`tools/surface_manifest.py cut`, adding "
                    "`--allow-approval-reset` if it reports recorded per-row references) to "
                    "re-read it. Do not hand-edit the manifest."
                )

    # --- 7. the growth pin, checked INDEPENDENTLY of the re-cut (#4185 review P1) -----
    # `counts:` is re-derived on every `cut`, so a PR that adds a tool and re-cuts the
    # baseline moves the number every drift check compares against and the growth is
    # invisible — the baseline cannot be its own growth control. This pin is NOT
    # re-derived: `cut` carries it forward (`_carried_high_water_counts`) and the gate
    # reds when the live surface EXCEEDS it, so growth stays visible even across a re-cut.
    # Raising it is a deliberate edit in the approving PR, and it is the machine half of
    # "a re-cut over a grown surface cannot be green by construction".
    high_water = doc.get("high_water_counts")
    if not isinstance(high_water, dict) or not all(
        isinstance(high_water.get(k), int) and not isinstance(high_water.get(k), bool)
        for k in ("tools", "sdk_public_methods")
    ):
        problems.append(
            "the frozen baseline carries no usable `high_water_counts` pin (it needs "
            "integer `tools` and `sdk_public_methods`). Without it a re-cut over a grown "
            "surface is green by construction; restore it (see CONTRIBUTING.md)."
        )
    else:
        for label, live_n, key in (
            ("MCP tools", len(declared_tools), "tools"),
            ("public SDK methods", len(declared_sdk), "sdk_public_methods"),
        ):
            if live_n > high_water[key]:
                problems.append(
                    f"the live surface GREW past the frozen high-water pin: {label} live "
                    f"{live_n} > pin {high_water[key]}. A re-cut CANNOT raise this pin "
                    "silently — raise `high_water_counts` explicitly in the PR that "
                    "records the owner's approval for the growth."
                )

    if problems:
        print(f"::error::{len(problems)} surface violation(s) — the frozen baseline is out of date.")
        print(
            "The agent-facing surface cannot grow without an explicit human decision (#3863).\n"
        )
        for i, problem in enumerate(problems, 1):
            print(f"{i}. {problem}\n")
        print(
            "If the change IS approved: record the approval in "
            f"{approval_doc} (status/principal/PR/date) **and re-cut** "
            "(`tools/surface_manifest.py cut`, adding `--allow-approval-reset` if it reports "
            "recorded per-row references) so the manifest re-reads it — recording it in the "
            "document alone leaves this copy stale. The per-row `approval` field is an audit record, not the gate."
        )
        return 1

    # The banner states the MANIFEST's staged status and the DOCUMENT's recorded status as
    # two separate facts (#4185 review). It used to print `approval_status=<status> is READ
    # from <doc>`, which was wrong twice over: the value printed came from the manifest, and
    # a staged `pending-owner-approval` approves nothing. On a manifest that CLAIMS an
    # approval the claim was compared; on a staged one nothing was compared, and saying so
    # is the difference between a green run that means something and one that does not.
    if claimed:
        approval_line = (
            f"The manifest CLAIMS an approval (approval_status={status}) and it matches "
            f"{approval_doc} (which records {canonical['status']!r})"
        )
    else:
        approval_line = (
            f"The manifest is STAGED on approval_status={status} and makes NO claim, so "
            f"nothing was compared against {approval_doc} (which records "
            f"{canonical['status']!r})"
        )
    print(
        f"OK — the declaration matches the FROZEN baseline: "
        f"{len(declared_tools)} MCP tools, {len(declared_sdk)} public SDK methods "
        f"({len(exemptions)} exempt). {approval_line}. The frozen baseline is NOT the "
        f"owner's approved TARGET — run tools/surface-target-check.py for compliance "
        f"with the target."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
