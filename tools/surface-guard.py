#!/usr/bin/env python3
"""The D2 expansion gate for the agent-facing surface (#3863).

WHAT THIS STOPS
    A new MCP tool, or a new public SDK method, appearing without an explicit
    human decision. The MCP tool surface and the SDK endpoints are the contract
    every agent depends on; growing them silently is the defect this gate
    exists to prevent.

HOW IT WORKS
    It EXECUTES the declaration (imports TOOL_REGISTRY, introspects
    TortoiseSDK) and compares it against the approved baseline in
    `config/surface-manifest.yml`. It never reads the declaration as text, so a
    rename that keeps the text similar still shows up as one removal plus one
    addition.

    The baseline is FROZEN. This gate deliberately does not regenerate it: if it
    did, a new registry entry would enter the baseline by itself and the gate
    could never go red. Regenerating is a human act (`tools/surface_manifest.py
    cut`), reviewed in a PR that carries the owner's approval.

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

    Exit 0 = declaration matches the approved baseline.
    Exit 1 = expansion, removal, a served-surface change, an exemption
             transition, an unapproved row, an unresolvable retired name, or
             unreadable evidence.

Usage:
    python3 tools/surface-guard.py [--manifest config/surface-manifest.yml]
"""

from __future__ import annotations

import argparse
import ast as _ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_MANIFEST = ROOT / "config" / "surface-manifest.yml"


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
    args = parser.parse_args(argv[1:])

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        return die(
            f"approved surface baseline not found at {manifest_path}. "
            "The gate cannot verify the surface without its baseline, and a gate that "
            "cannot read its evidence must not report success. Restore the baseline "
            "(it is checked in), or cut a new one in a reviewed PR."
        )

    try:
        import yaml

        with manifest_path.open() as fh:
            doc = yaml.safe_load(fh)
    except Exception as exc:
        return die(f"could not read the approved surface baseline {manifest_path}: {exc}")

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
    # version of that route (and the approved-name check catches a foreign class reusing an
    # approved name).
    #
    # THREAT SURFACE, DECLARED — this is what the gate does and does not cover:
    #   in scope:  a tool that becomes reachable as a SIDE EFFECT of a change to the
    #              declaration, the registry, or the registered providers/transforms —
    #              the accidental-expansion class, which is how this surface grew to 99.
    #   out of scope: a party who deliberately writes code in-process to defeat it — a
    #              class that copies an approved transform's identity, a function that
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
    # spuriously. What matters is that nothing BEYOND the approved set is registered.
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
    # something else, so an approved name must also be DEFINED where the declaration lives.
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
            f"transform class(es) reusing an approved name but defined outside "
            f"tortoise.mcp_server: {foreign_transforms}. A class name is not an identity — "
            "a transform doing something else under an approved name needs approval."
        )
    if unapproved_transforms:
        problems.append(
            f"unapproved server transform(s): {unapproved_transforms} "
            f"(approved: {sorted(baseline_transforms)}, live: {live_transforms}). A transform "
            "can route tools without listing them, so it needs explicit approval and a re-cut."
        )

    # --- the SERVED IMPLEMENTATION of each approved tool -------------------------
    # `mcp.add_tool(fn, name=<an approved name>)` REPLACES that tool's implementation while
    # leaving the name set and the entry count identical, so it passed every check above
    # (verified: a live client then invoked the shadow function). Comparing the identity of
    # the component behind each name catches a count-preserving substitution.
    import hashlib as _hashlib

    def _fingerprint(fn) -> str | None:
        """Code-object identity — see the note in tools/surface_manifest.py.

        Built from `co_filename`, `co_firstlineno` and a digest of `co_code` rather than
        from `__module__`/`__qualname__`, because those are writable strings a shadow can
        simply copy from the tool it is replacing.
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
        return (
            f"{rel}:{code.co_firstlineno}:"
            f"{_hashlib.sha256(code.co_code).hexdigest()[:16]}"
        )

    live_components: dict[str, str] = {}
    for _key, _tool in mcp_server.mcp._local_provider._components.items():
        if not _key.startswith("tool:"):
            continue
        live_components[getattr(_tool, "name", None) or _key.split(":", 1)[1].split("@")[0]] = (
            _fingerprint(getattr(_tool, "fn", None))
        )
    # --- a declared tool that is no longer SERVED ---------------------------------
    # The union check above only asks whether anything undeclared is served. The inverse
    # matters too: a tool that stops being offered is a removal, which the guard's
    # docstring already promises to catch — but nothing computed it, so popping a
    # component left the guard green (verified).
    missing_served = sorted(baseline_tools - served_tools)
    if missing_served:
        problems.append(
            f"{len(missing_served)} tool(s) in the approved baseline are NOT served by the "
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
        live = live_components.get(name)
        if live is None:
            continue  # absence is already reported by the missing-served check above
        if live != stored:
            problems.append(
                f"the implementation served as `{name}` changed: the baseline recorded "
                f"{stored!r}, the server now serves {live!r}. Replacing an approved tool's "
                "implementation is a surface change and needs explicit approval — rename the "
                "new tool, or re-cut the baseline in the approved PR."
            )

    # A duplicate-name entry is an unapproved registry entry the name-set comparison
    # cannot see: `declared_tools` is a set, so a second ToolDefinition reusing an
    # approved name leaves the guard green while the registry grew.
    baseline_tool_count = sum(
        1 for r in rows if isinstance(r, dict) and not str(r.get("name", "")).startswith("sdk:")
    )
    if len(TOOL_REGISTRY) != baseline_tool_count:
        problems.append(
            f"TOOL_REGISTRY has {len(TOOL_REGISTRY)} entries but the approved baseline has "
            f"{baseline_tool_count} — the registry changed in a way the name comparison cannot "
            "see (a duplicate name, or a removed-and-added pair)."
        )

    # --- 1. expansion: a tool or SDK method the owner never approved ---------
    for name in sorted(declared_tools - baseline_tools):
        problems.append(
            f"NEW MCP TOOL `{name}` is registered but is not in the approved baseline. "
            "Adding a tool expands what every agent can call; it needs an explicit human "
            "decision (see #3863). If it is approved, add the row to "
            "config/surface-manifest.yml in a PR that records the approval."
        )
    for name in sorted(declared_sdk - baseline_sdk - exemptions):
        problems.append(
            f"NEW PUBLIC SDK METHOD `TortoiseSDK.{name}` is not in the approved baseline. "
            "A public method is an endpoint; it needs an explicit human decision (see #3863)."
        )

    # --- 2. removal: an approved entry that disappeared ---------------------
    for name in sorted(baseline_tools - declared_tools):
        problems.append(
            f"REMOVED MCP TOOL `{name}` was in the approved baseline and is gone from the "
            "declaration. Silently dropping a surface entry breaks callers; if this is "
            "intended it is an owner decision and the baseline must be updated with it."
        )
    for name in sorted(baseline_sdk - declared_sdk):
        problems.append(
            f"REMOVED SDK METHOD `TortoiseSDK.{name}` was in the approved baseline and is gone."
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
    for row in rows:
            name = row.get("name", "")
            if name.startswith("sdk:") or not row.get("served"):
                continue
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
    # approved baseline's `retired:` block or the gate reds. The inverse matters more:
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
        if not isinstance(_row, dict) or "name" not in _row:
            return die(f"malformed retired row in the baseline: {_row!r:.200}")
        baseline_retired_map[str(_row["name"])] = str(_row.get("use_instead") or "")

    for name in sorted(set(declared_retired) - set(baseline_retired_map)):
        problems.append(
            f"NEW RETIRED TOOL `{name}` is declared retired but is not in the approved "
            "baseline's `retired:` block. Retiring a name removes it from every agent's "
            "surface; it needs an explicit human decision (#3863)."
        )
    for name in sorted(set(baseline_retired_map) - set(declared_retired)):
        problems.append(
            f"`{name}` is retired in the approved baseline but NOT declared retired. "
            "An approved retirement that was reverted is a surface change; re-cut the baseline."
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

    # --- 6. approval, once the list has been approved ----------------------
    status = doc.get("approval_status")
    if status not in ("pending-owner-approval", "approved"):
        problems.append(
            f"`approval_status: {status!r}` is not one of "
            "'pending-owner-approval' or 'approved' — the gate cannot tell whether this "
            "surface has been approved, so it must not pass."
        )
    if status == "approved":
        for row in rows:
            if not isinstance(row, dict):
                problems.append(f"malformed row (not a mapping): {row!r}")
                continue
            approval = row.get("approval")
            text = str(approval).strip()
            # "a PR number plus the approving principal's handle" — enforced, not merely
            # stated. A bare date and a bare boolean both used to pass.
            if (
                not approval
                or text.lower() in ("tbd", "todo", "yes", "true", "approved")
                or "@" not in text
                or not any(ch.isdigit() for ch in text)
            ):
                problems.append(
                    f"`{row.get('name')}` has no recorded approval "
                    f"(approval={approval!r}) while `approval_status: approved`. Approval is a "
                    "PR number plus the approving principal's handle — never `yes`, never a "
                    "bare date."
                )

    if problems:
        print(f"::error::{len(problems)} surface violation(s) — the approved baseline is out of date.")
        print(
            "The agent-facing surface cannot grow without an explicit human decision (#3863).\n"
        )
        for i, problem in enumerate(problems, 1):
            print(f"{i}. {problem}\n")
        print(
            "If the change IS approved: update config/surface-manifest.yml in this PR and set "
            "`approval` on each affected row to the PR number and the approving principal's "
            "handle. See docs/product/mcp-sdk-surface.md for the list the owner reads."
        )
        return 1

    print(
        f"OK — the declaration matches the approved baseline: "
        f"{len(declared_tools)} MCP tools, {len(declared_sdk)} public SDK methods "
        f"({len(exemptions)} exempt), approval_status={status}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
