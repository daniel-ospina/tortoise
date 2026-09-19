"""The agent-facing surface list and the expansion gate (#3863).

These tests are the machine half of the acceptance criteria:

* AC1  — the list is generated from the declaration and is in sync with it
* AC11 — the baseline covers the declaration, and the gate fails closed
* AC13 — the ordering lint's eight properties hold

They are deliberately fast and dependency-free: they execute the declaration and
compare it to the checked-in baseline. No database, no network, no subprocess
pytest run.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "config" / "surface-manifest.yml"
RENDERED = ROOT / "docs" / "product" / "mcp-sdk-surface.md"


def _public_sdk_methods() -> set[str]:
    from tortoise.sdk import TortoiseSDK

    return {
        name
        for name in dir(TortoiseSDK)
        if not name.startswith("_") and callable(getattr(TortoiseSDK, name))
    }


def _manifest() -> dict:
    with MANIFEST.open() as fh:
        return yaml.safe_load(fh)


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_baseline_exists_and_is_a_frozen_snapshot():
    doc = _manifest()
    assert doc["issue"] == 3863
    assert doc["cut_at_commit"], "the baseline must record the commit it was cut at"
    assert doc["rows"], "the baseline cannot be empty"


def test_every_registered_tool_is_in_the_baseline():
    from tortoise.tool_registry import TOOL_REGISTRY

    declared = {entry.name for entry in TOOL_REGISTRY}
    baseline = {r["name"] for r in _manifest()["rows"] if not str(r["name"]).startswith("sdk:")}
    assert declared == baseline, (
        "the registry and the approved baseline have diverged — a new tool needs an "
        f"explicit human decision (#3863). added={sorted(declared - baseline)} "
        f"removed={sorted(baseline - declared)}"
    )


def test_every_public_sdk_method_is_in_the_baseline():
    baseline = {
        r["method"] for r in _manifest()["rows"] if str(r["name"]).startswith("sdk:")
    }
    exempt = {
        r["method"]
        for r in _manifest()["rows"]
        if str(r["name"]).startswith("sdk:") and r.get("exemption") is True
    }
    declared = _public_sdk_methods()
    assert declared == baseline - exempt | (declared & exempt), (
        "a public SDK method is an endpoint; the baseline must carry every one. "
        f"added={sorted(declared - baseline - exempt)} removed={sorted(baseline - declared)}"
    )


def test_the_guard_passes_on_the_checked_in_baseline():
    result = _run("tools/surface-guard.py")
    assert result.returncode == 0, f"the gate reds on the checked-in baseline:\n{result.stdout}\n{result.stderr}"


def test_the_order_lint_passes():
    result = _run("tools/surface_manifest.py", "check")
    assert result.returncode == 0, f"the ordering lint fails:\n{result.stdout}\n{result.stderr}"


def test_the_guard_fails_closed_when_the_baseline_is_missing(tmp_path):
    result = _run("tools/surface-guard.py", "--manifest", str(tmp_path / "nope.yml"))
    assert result.returncode == 1, "a gate that cannot read its evidence must not report success"
    assert "not found" in result.stdout


def test_the_guard_reds_when_a_tool_is_added(tmp_path):
    """The whole point: a registry entry the owner never approved is a failure."""
    doc = _manifest()
    doc["rows"] = [r for r in doc["rows"] if r["name"] != "tortoise_search"]
    stripped = tmp_path / "stripped.yml"
    stripped.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(stripped))
    assert result.returncode == 1, "an added tool did not red the gate"
    assert "tortoise_search" in result.stdout


def test_the_guard_reds_when_a_public_method_is_added(tmp_path):
    doc = _manifest()
    doc["rows"] = [r for r in doc["rows"] if r.get("method") != "close"]
    stripped = tmp_path / "stripped.yml"
    stripped.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(stripped))
    assert result.returncode == 1, "an added public SDK method did not red the gate"
    assert "close" in result.stdout


def test_an_exemption_cannot_outlive_the_fact_it_records(tmp_path):
    """A method marked exempt that has since become reachable must be un-exempted."""
    doc = _manifest()
    target = next(r for r in doc["rows"] if r.get("method") == "create_point")
    target["exemption"] = True
    modified = tmp_path / "exempt.yml"
    modified.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(modified))
    assert result.returncode == 1
    assert "exemption" in result.stdout


def test_approval_cannot_be_claimed_without_recording_it(tmp_path):
    doc = _manifest()
    doc["approval_status"] = "approved"
    modified = tmp_path / "approved.yml"
    modified.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(modified))
    assert result.returncode == 1, "`approved` with no per-row approval must red"
    assert "approval" in result.stdout


def test_unknown_approval_status_is_a_failure(tmp_path):
    doc = _manifest()
    doc["approval_status"] = "mostly-fine"
    modified = tmp_path / "odd.yml"
    modified.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(modified))
    assert result.returncode == 1


def _baseline_tool_count() -> int:
    """The approved tool count, read from the baseline rather than hardcoded."""
    return sum(1 for r in _manifest()["rows"] if not str(r["name"]).startswith("sdk:"))


def test_the_rendered_list_agrees_with_the_manifest():
    """AC1: the document the owner reads is generated from the manifest and stays in sync."""
    assert RENDERED.exists(), "docs/product/mcp-sdk-surface.md must exist"
    doc = _manifest()
    text = RENDERED.read_text(encoding="utf-8")
    tools = [r for r in doc["rows"] if not str(r["name"]).startswith("sdk:")]
    sdk = [r for r in doc["rows"] if str(r["name"]).startswith("sdk:")]
    assert doc["cut_at_commit"][:9] in text
    assert f"**{len(tools)} MCP tools · {len(sdk)} SDK methods.**" in text
    missing = [r["name"] for r in tools if f"`{r['name']}`" not in text]
    assert not missing, f"tools in the manifest but not in the rendered list: {missing[:10]}"
    missing_sdk = [r["method"] for r in sdk if f"`TortoiseSDK.{r['method']}`" not in text]
    assert not missing_sdk, f"SDK methods missing from the rendered list: {missing_sdk[:10]}"

    # The DEPENDENCY column, and every marker the legend promises. An earlier version
    # computed `reaches` and `flags` and then never emitted them, so the owner-facing
    # document documented a column that did not exist and the five false declarations were
    # only discoverable by reading free text.
    assert "| Reaches |" in text, "the dependency column is not rendered"
    for row in tools:
        expected = row.get("sdk_method") or "handler-served"
        assert expected in text or f"~~{expected}~~" in text, (
            f"{row['name']}'s declared dependency ({expected}) never renders"
        )
    if any("WHICH DOES NOT EXIST" in (r.get("dependency") or "") for r in tools):
        assert "⚠ FALSE DECLARATION" in text, "a false declaration renders with no marker"
    if any(r.get("lifecycle") == "deprecated alias" for r in tools):
        assert "DEPRECATED" in text, "a deprecated alias renders with no marker"


def test_split_clusters_carry_one_proposed_family_each():
    """AC13's cluster-family agreement, stated as a property over the declared set."""
    doc = _manifest()
    rows = [r for r in doc["rows"] if not str(r["name"]).startswith("sdk:")]
    clusters: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("cluster"):
            clusters.setdefault(row["cluster"], []).append(row)
    for cluster, members in clusters.items():
        families = {m["family"] for m in members}
        if len(families) > 1:
            proposals = {m.get("recommended_family") for m in members}
            assert len(proposals) == 1 and None not in proposals, (
                f"cluster {cluster!r} is split across {sorted(families)} but its members do "
                f"not agree on one recommended family: {proposals}"
            )
            assert all(m.get("proposed") for m in members)


def test_every_row_declares_whether_it_decides_or_merely_observes() -> None:
    """A recommendation that rests on usage is evidence, never a verdict.

    The distinction is load-bearing (owner ruling: an owner decision outranks a convergent
    standard). If a `keep` were ever presented as a decision, or a `merge` as an observation,
    the list would start arguing from what is done instead of what we decided to be.
    """
    manifest = _manifest()
    rows = [r for r in manifest["rows"] if not str(r["name"]).startswith("sdk:")]
    assert rows, "no tool rows"
    for row in rows:
        expected = "decided" if row["recommendation"] in ("merge", "kill", "fix-declaration") else "observed"
        assert row.get("basis") == expected, f"{row['name']}: {row['recommendation']} must be {expected}"
    decided = [r for r in rows if r["basis"] == "decided"]
    observed = [r for r in rows if r["basis"] == "observed"]
    assert len(decided) + len(observed) == len(rows)
    assert decided, "at least one recommendation must derive from a decision already made"


def _write(doc: dict, tmp_path) -> str:
    p = tmp_path / "mutated.yml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    return str(p)


def test_approved_status_requires_approval_on_SDK_rows_too(tmp_path):
    """P1 found by review: the approval check skipped every `sdk:` row.

    Half the surface (152 of 251 rows) was exempt from the one human-approval control
    the gate was built to enforce, so `approved` could be set with no SDK approval at all.
    """
    doc = _manifest()
    doc["approval_status"] = "approved"
    for row in doc["rows"]:
        if not str(row["name"]).startswith("sdk:"):
            row["approval"] = "PR#1 @owner"          # tools approved …
        else:
            row["approval"] = None                   # … SDK rows deliberately not
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "an unapproved SDK row did not red `approved`"
    assert "sdk:" in result.stdout


def test_the_guard_reds_when_a_tools_SDK_binding_moves(tmp_path):
    """P2 found by review: CONTRIBUTING promised this; the guard did not do it.

    `sdk_method` could be silently re-pointed on any tool while the required check stayed green.
    """
    doc = _manifest()
    for row in doc["rows"]:
        if row["name"] == "tortoise_search":
            row["sdk_method"] = "definitely_not_the_real_binding"
            break
    else:
        raise AssertionError("tortoise_search missing from the baseline")
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "a moved SDK binding did not red the gate"
    assert "binding" in result.stdout


def _load_guard():
    """Import tools/surface-guard.py (hyphenated filename) as a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("surface_guard", ROOT / "tools" / "surface-guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_guard_reds_on_a_duplicate_registry_entry():
    """P3 from review: a duplicate NAME was invisible to a set-based comparison.

    `declared_tools` is a set, so appending a second ToolDefinition reusing an approved
    name left the guard green while the registry grew. The cardinality check closes it.
    Every other test mutates the MANIFEST; this one must mutate the live registry, which
    is why it runs the guard in-process rather than via subprocess.
    """
    from tortoise.tool_registry import TOOL_REGISTRY

    guard = _load_guard()
    original = list(TOOL_REGISTRY)
    try:
        # ToolDefinition is a frozen dataclass, so a duplicate NAME is produced by
        # appending the entry again — exactly the shape (a name-set comparison sees
        # nothing) the cardinality check exists to catch.
        TOOL_REGISTRY.append(TOOL_REGISTRY[0])
        rc = guard.main([])
        assert rc == 1, "a duplicate registry entry did not red the gate"
    finally:
        TOOL_REGISTRY[:] = original
    assert len(TOOL_REGISTRY) == _baseline_tool_count(), "the registry was not restored"


def test_the_guard_reds_on_a_tool_served_outside_the_registry():
    """The hole that made the gate's own claim false.

    D2 says "the MCP tool surface cannot be expanded without explicit human approval",
    but the guard compared only TOOL_REGISTRY. A tool registered straight on the FastMCP
    instance — a `@mcp.tool()` decorator in mcp_server.py, or any `mcp.add_tool(...)` —
    is served to agents while the registry is untouched. Every other assertion only ever
    checked `registry subset-of registered`, never the reverse, so the expanded surface
    reached a green gate. The guard now enumerates the SERVED set.
    """
    from tortoise import mcp_server

    guard = _load_guard()

    async def tortoise_tool_served_outside_the_registry() -> str:
        return "hi"

    mcp_server.mcp.add_tool(tortoise_tool_served_outside_the_registry)
    try:
        assert guard.main([]) == 1, "a tool served outside TOOL_REGISTRY did not red the gate"
    finally:
        components = mcp_server.mcp._local_provider._components
        for key in [
            k for k in components if k.startswith("tool:tortoise_tool_served_outside_the_registry@")
        ]:
            components.pop(key)
        # If the provider layout changes, fail loudly rather than leaking a served tool
        # into the rest of the suite.
        assert guard.main([]) == 0, "the served-surface check did not recover after cleanup"


def test_the_guard_reds_on_a_tool_injected_by_a_server_transform():
    """The bypass that a registry comparison and `add_tool` both miss.

    A server-level transform (`mcp.add_transform`) can append a tool in `list_tools` and
    route it in `get_tool`, so it is advertised to a real MCP client and callable over the
    protocol while `TOOL_REGISTRY` is untouched. `mcp_server.py` already uses this API for
    `_HTTPToolFilter`, so it is a live pattern here, not a hypothetical.

    Reading `mcp._list_tools()` misses it — that is the pre-transform aggregate. Only
    `mcp.list_tools()` (the protocol `tools/list` path) sees what agents actually receive:
    verified 99 vs 100 against a live `fastmcp.Client`.
    """
    from fastmcp.tools import Tool

    from tortoise import mcp_server

    guard = _load_guard()

    async def tortoise_tool_injected_by_transform() -> str:
        return "hi"

    class _Inject:
        async def list_tools(self, tools):
            tools.append(Tool.from_function(tortoise_tool_injected_by_transform))
            return tools

        async def get_tool(self, name, call_next):
            return await call_next(name)

    transform = _Inject()
    mcp_server.mcp.add_transform(transform)
    try:
        assert guard.main([]) == 1, "a transform-injected tool did not red the gate"
    finally:
        mcp_server.mcp._transforms.remove(transform)
        assert guard.main([]) == 0, "the served-surface check did not recover after cleanup"


def test_the_guard_reds_on_a_transform_that_only_routes_get_tool():
    """A transform that intercepts `get_tool` without listing anything.

    `tools/call` resolves through `get_tool`, so such a transform makes a tool callable
    over the protocol while it appears in NEITHER `_list_tools()` nor `list_tools()` —
    the union alone could not see it (verified: a live `fastmcp.Client` called it while the
    gate exited 0). Intercepting resolution requires a registered transform, so the guard
    gates the transform SET.
    """

    class _Phantom:
        async def list_tools(self, tools):
            return tools  # lists nothing extra — that is what makes this one invisible

        async def get_tool(self, name, call_next):
            return await call_next(name)

    from tortoise import mcp_server

    guard = _load_guard()
    phantom = _Phantom()
    mcp_server.mcp.add_transform(phantom)
    try:
        assert guard.main([]) == 1, "a get_tool-only transform did not red the gate"
    finally:
        mcp_server.mcp._transforms.remove(phantom)
        assert guard.main([]) == 0, "the transform check did not recover after cleanup"


def test_the_guard_reds_on_a_same_name_replacement_of_an_approved_tool():
    """A count-preserving substitution of an approved tool's implementation.

    `mcp.add_tool(fn)` where `fn` reuses an approved tool's name silently REPLACES it —
    fastmcp logs "Component already exists". The name set and the entry count are both
    unchanged, so every set- and count-based check stayed green while a live client
    invoked the shadow implementation. Comparing the identity of the component behind
    each name is what catches it.
    """
    from tortoise import mcp_server

    guard = _load_guard()
    components = mcp_server.mcp._local_provider._components
    key = next(k for k in components if k.startswith("tool:tortoise_search@"))
    original = components[key]
    original_count = len([k for k in components if k.startswith("tool:")])

    async def tortoise_search() -> str:
        """shadow implementation of an approved tool"""
        return "shadow"

    mcp_server.mcp.add_tool(tortoise_search)
    try:
        assert len([k for k in components if k.startswith("tool:")]) == original_count, (
            "this test is meaningless unless the count is preserved"
        )
        assert guard.main([]) == 1, "a same-name replacement did not red the gate"
    finally:
        components[key] = original
        assert guard.main([]) == 0, "the served-implementation check did not recover"


def test_code_digest_is_move_invariant_and_constant_sensitive():
    """The freeze-gate identity must ignore a pure line shift and differ when a
    constant differs. `sha256(co_code)` alone did neither once the line was
    dropped (98 tools collapsed to 62 digests)."""
    guard = _load_guard()

    def const_x() -> str:
        return "X"

    def const_y() -> str:
        return "Y"

    assert guard._code_digest(const_x.__code__) != guard._code_digest(const_y.__code__)

    src = "def f():\n    return 'X'\n"
    spaced = "\n\n\n\n\n" + src
    g1: dict = {}
    g2: dict = {}
    exec(compile(src, "<t>", "exec"), g1)
    exec(compile(spaced, "<t>", "exec"), g2)
    assert g1["f"].__code__.co_firstlineno != g2["f"].__code__.co_firstlineno
    assert guard._code_digest(g1["f"].__code__) == guard._code_digest(g2["f"].__code__)


def test_the_guard_reds_when_the_served_component_has_no_code_object():
    """A PRESENT-but-unfingerprintable component is malformed evidence, not
    absence: a substitution whose callable has no `__code__` must red, not be
    skipped as if the tool were simply not served."""
    import functools

    from tortoise import mcp_server

    guard = _load_guard()
    components = mcp_server.mcp._local_provider._components
    key = next(k for k in components if k.startswith("tool:tortoise_search@"))
    original = components[key]

    def shadow() -> str:
        """shadow implementation of an approved tool"""
        return "shadow"

    partial = functools.partial(shadow)
    partial.__name__ = "tortoise_search"
    mcp_server.mcp.add_tool(partial)
    try:
        assert guard.main([]) == 1, "a no-__code__ substitution did not red the gate"
    finally:
        for k in [k for k in components
                  if k.startswith("tool:tortoise_search@") and k != key]:
            components.pop(k, None)
        components[key] = original
        assert guard.main([]) == 0, "the served-implementation check did not recover"


def test_no_SDK_row_is_classed_unreachable_while_its_own_row_names_an_agent_path():
    """The unverified-negative class, pinned.

    `derive_class` once computed `agent-reachable` from declared `sdk_method` bindings
    alone. That classified CLI-reached methods (apikey_create, close, reconcile_sessions,
    session_index_health, volunteer_context) and handler-reached methods (recall_gaps,
    recall_subgraph, topic_summarize) as `no-caller-found` or `internal` — so the rendered
    doc asserted "reached by no agent path at all (no MCP tool, no CLI verb)" over rows
    whose own text named a CLI verb. A count derived by hand is a prediction until
    something executes it; this test executes the agreement.
    """
    from tortoise.sdk import TortoiseSDK

    doc = yaml.safe_load((ROOT / "config" / "surface-manifest.yml").read_text())
    sdk = [r for r in doc["rows"] if str(r["name"]).startswith("sdk:")]
    assert sdk, "no SDK rows found"

    for row in sdk:
        if row["class"] not in ("no-caller-found", "internal"):
            continue
        names_a_path = any(
            tok in (row.get("reason") or "") or tok in (row.get("dependency") or "")
            for tok in ("cli", "mcp-handler")
        )
        assert not names_a_path, (
            f"{row['name']} is classed {row['class']!r} but its own row names an agent path: "
            f"reason={row.get('reason')!r} dependency={row.get('dependency')!r}"
        )

    by_class = {}
    for row in sdk:
        by_class[row["class"]] = by_class.get(row["class"], 0) + 1
    declared_bindings = {
        r["sdk_method"]
        for r in doc["rows"]
        if not str(r["name"]).startswith("sdk:") and r.get("sdk_method")
    }
    assert by_class["agent-reachable"] >= len(
        {m for m in declared_bindings if hasattr(TortoiseSDK, m)}
    ), (
        "agent-reachable fell below the declared-binding count; the CLI and tool-handler legs are "
        f"not being derived (got {by_class['agent-reachable']})"
    )


def test_the_transform_check_reads_the_source_not_only_the_runtime_list():
    """The hole that made the transform gate inert in CI.

    The only `add_transform` in the tree sits INSIDE `create_http_app()`, which the guard
    never calls — so `mcp._transforms` is `[]` at guard time and a runtime-only check
    cannot fire in CI at all. The gate therefore also scans the SOURCE. Without that, a
    transform added to `mcp_server.py` (which can append to `list_tools` or route
    `get_tool` without listing anything) was invisible.
    """
    import tempfile


    guard = _load_guard()
    # The runtime list is lifecycle-dependent: at guard time in CI (`create_http_app`
    # never called) it is EMPTY, so a runtime-only check cannot fire there at all. The
    # source scan is what CI actually relies on.
    assert guard._source_transforms() == {"_HTTPToolFilter"}, (
        "the source scan must see the transform the declaration registers"
    )

    sample = pathlib.Path(tempfile.mkdtemp()) / "sample.py"
    sample.write_text("def f():\n    mcp.add_transform(_Sneaky())\n")
    assert guard._source_transforms(sample) == {"_Sneaky"}, (
        "a transform added to the declaration's source must be visible without importing it"
    )


def test_the_guard_reds_when_an_approved_tool_stops_being_served():
    """The inverse of the union check: a DECLARED tool that is no longer offered.

    `undeclared_served` only asked whether anything undeclared is served. A tool silently
    ceasing to be offered is a removal — which the guard's own docstring promises to catch
    — but nothing computed it, so popping the component left the guard green.
    """
    from tortoise import mcp_server

    guard = _load_guard()
    components = mcp_server.mcp._local_provider._components
    key = next(k for k in components if k.startswith("tool:tortoise_search@"))
    saved = components.pop(key)
    try:
        assert guard.main([]) == 1, "a declared-but-unserved tool did not red the gate"
    finally:
        components[key] = saved
        assert guard.main([]) == 0, "the served-set check did not recover after cleanup"


def test_a_null_served_from_is_treated_as_malformed_not_as_nothing_to_check():
    """A null fingerprint must fail, not skip.

    Treating `served_from: null` as "nothing to check" re-opened the exact same-name
    substitution the fingerprint exists to close, reachable by editing only the baseline.
    """
    import copy
    import tempfile

    from tortoise import mcp_server

    doc = _manifest()
    mutated = copy.deepcopy(doc)
    for row in mutated["rows"]:
        if row.get("name") == "tortoise_search":
            row["served_from"] = None
    tmp = pathlib.Path(tempfile.mkdtemp()) / "m.yml"
    tmp.write_text(yaml.safe_dump(mutated, sort_keys=False, width=110))

    async def tortoise_search_shadow() -> str:
        """shadow"""
        return "shadow"

    mcp_server.mcp.add_tool(tortoise_search_shadow)
    try:
        # Subprocess, because that is how CI runs it — argparse in-process is a different path.
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "surface-guard.py"), "--manifest", str(tmp)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, "a null served_from let a same-name replacement through"
        assert "no `served_from` fingerprint" in result.stdout, (
            "the guard redded for some other reason than the missing fingerprint"
        )
    finally:
        components = mcp_server.mcp._local_provider._components
        for k in [k for k in components if k.startswith("tool:tortoise_search_shadow@")]:
            components.pop(k)


def test_the_usage_markers_are_not_inverted_and_every_row_is_well_formed_markdown():
    """Two defects the independent review found in the rendered artifact.

    1. The `in use` / `never called` markers were INVERTED: the 64 rows with zero observed
       calls were labelled "in use" and the 35 that actually appear in the call log were
       labelled "never called" — contradicting both the legend and each row's own `used_by`
       cell. The variable holding the observed names was misnamed `never_called`, which is
       how the inversion survived review.
    2. A literal `|` in a tool's description was escaped in `rationale` but not in the
       `does` cell, so six rows split into extra table cells and shifted every later column.
    """
    doc = _manifest()
    tools = [r for r in doc["rows"] if not str(r["name"]).startswith("sdk:")]
    text = RENDERED.read_text(encoding="utf-8")

    true_never = {r["name"] for r in tools if "never called" in (r.get("used_by") or "")}
    true_in_use = {r["name"] for r in tools} - true_never
    assert true_never and true_in_use, "the fixture is degenerate — no usage signal at all"

    rows = [ln for ln in text.split("\n") if ln.startswith("| `tortoise_")]
    assert len(rows) == len(tools), f"rendered {len(rows)} tool rows for {len(tools)} tools"

    malformed = [ln for ln in rows if ln.count("|") != 7]
    assert not malformed, (
        "these rows are not well-formed 6-column tables (a literal pipe in a cell splits "
        f"the row): {[m[:70] for m in malformed[:5]]}"
    )

    marked_never = {ln.split("`")[1] for ln in rows if "never called" in ln}
    marked_in_use = {ln.split("`")[1] for ln in rows if "in use" in ln}
    assert marked_never == true_never, (
        "the 'never called' marker does not match the manifest's usage evidence "
        f"(rendered-only={sorted(marked_never - true_never)[:5]}, "
        f"manifest-only={sorted(true_never - marked_never)[:5]})"
    )
    assert marked_in_use == true_in_use, (
        "the 'in use' marker does not match the manifest's usage evidence "
        f"(rendered-only={sorted(marked_in_use - true_in_use)[:5]})"
    )
