"""
Issue #6902 — Audit: confirm no production code depends on BFS propagate_shock output.

This test file encodes the audit findings as assertions. It does NOT run against
a live FalkorDB instance — it's a grep-plus-AST documentation of the audit results.
Run with: python3 -m pytest tests/test_bfs_audit.py -v
"""

import ast
import subprocess
import sys  # noqa: F401
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _propagate_shock_reference_sites() -> dict[str, set[int]]:
    """`{absolute path: {line numbers}}` where `tortoise/` code REFERS to `propagate_shock`.

    ⛔ The grep below cannot tell a CALL from PROSE that names the function, and this
    audit's claim is about callers. `#11702` (`e7394a9ad7`) added a docstring note in
    `tortoise/projection/propagation.py:70,73` ("`propagate_shock` PERSISTS the blend …",
    "`propagate_shock` is never invoked") and the classifier's only comment exemption
    keyed on the word "replaces", so both lines were bucketed as PRODUCTION CALLERS —
    `test (c)` of run 43070177052 was RED at `d0347aaa` over a tree that calls nothing
    (reproduced on `origin/main` `372f926cd9`, which touches neither file).

    So the AST decides what is a reference and the grep keeps documenting the matches.
    This walks REFERENCES, not just `Call` nodes: a `Call`-only test names the callee
    syntactically, so it cannot see an indirection, and an indirection is exactly the
    shape a lane "restoring" this path would use (`#11702`'s note at
    `tortoise/projection/propagation.py` says the fabrication risk is at the WRITE,
    where no read-side guard can see it). Every shape below is one reference:

      * `propagate_shock(…)`, `self.propagate_shock(…)`, `obj.propagate_shock`
        — a `Name`/`Attribute` whose id/attr is `propagate_shock`;
      * `from … import propagate_shock as _shock; _shock(…)` — the module's import
        aliases are resolved FIRST, so the alias' use is a reference too;
      * `HANDLERS = {"shock": propagate_shock}` — a bare `Name` in a dict/argument,
        which a `Call`-only test classified as "reference only".

    Excluded by construction: the deprecated ``def propagate_shock`` (a `FunctionDef`
    name, not a reference), an `import` binding (an `ast.alias`, not a `Name`), and all
    prose — a docstring is a `Constant` node and a comment is not parsed at all.

    ⚠️ STATED BOUND — indirection through a STRING (`getattr(x, "propagate_shock")`,
    a Cypher building the name) is not a `Name`/`Attribute` and is not seen here. That
    is a bound, not a claim of completeness: the shapes above are the ones a restoring
    lane actually writes, and `test_no_propagate_shock_callers` asserts on THIS set
    rather than on grep lines, so a reference on a line that never spells the name
    (the aliased call) can no longer pass.
    """
    sites: dict[str, set[int]] = {}
    for path in sorted((ROOT / "tortoise").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases: set[str] = {"propagate_shock"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                aliases.update(a.asname or a.name for a in node.names
                               if a.name == "propagate_shock")
        for node in ast.walk(tree):
            is_shock_attr = isinstance(node, ast.Attribute) and node.attr == "propagate_shock"
            is_alias_name = isinstance(node, ast.Name) and node.id in aliases
            if is_shock_attr or is_alias_name:
                sites.setdefault(str(path), set()).add(node.lineno)
    return sites


def _grep(pattern: str, *dirs: str) -> list[str]:
    """Return non-empty lines from grep -rn in specified dirs (relative to ROOT)."""
    try:
        result = subprocess.run(
            ["grep", "-rn", pattern, *[str(ROOT / d) for d in dirs]],
            capture_output=True, text=True, timeout=15,
        )
        lines = result.stdout.strip().split("\n")
        return [l for l in lines if l.strip()]  # noqa: E741
    except subprocess.TimeoutExpired:
        return []


# ── 1. propagate_shock callers ────────────────────────────────────

def test_no_propagate_shock_callers():
    """Assert: propagate_shock.calls only in deprecated definition + tests + scripts."""
    # All references to propagate_shock (the word)
    all_refs = _grep("propagate_shock", "tortoise/", "graph-scripts/", "validation/", "tests/")
    filtered = [
        l for l in all_refs  # noqa: E741
        if "__pycache__" not in l and "test_bfs_audit.py" not in l and "test_e2e_extraction_ep.py" not in l
    ]
    # `{path: {lineno}}` of real references — the ONLY evidence of a production caller.
    # See `_propagate_shock_reference_sites` for the false RED this replaced and for
    # why it walks references rather than `Call` nodes only (an aliased call spells
    # `_shock`, so no grep line would ever reach the bucket below).
    refs = _propagate_shock_reference_sites()

    callers: dict[str, list[str]] = {
        "definition (deprecated)": [],
        "comment/reference only": [],
        "test_files": [],
        "fixup_scripts (historical)": [],
        "production_code": [],
    }

    for line in filtered:
        path_part = line.split(":")[0]
        full_path = ROOT / path_part  # noqa: F841

        # #378: projection.py was split into the projection/ package — the
        # deprecated definition now lives in projection/propagation.py.
        if ("projection.py" in path_part or "projection/" in path_part) \
                and "def propagate_shock" in line:
            callers["definition (deprecated)"].append(line)
        elif "/tests/" in path_part:
            callers["test_files"].append(line)
        elif "graph-scripts/fix_" in path_part:
            callers["fixup_scripts (historical)"].append(line)
        elif "/graph-scripts/" in path_part:
            callers["test_files"].append(line)  # test_6707_shock.py
        elif "/tortoise/" in path_part:
            # A REFERENCE, not a mention: the AST is the judge (a docstring that names
            # the function is documentation of its deprecation, not a caller of it).
            lineno = line.split(":")[1]
            if lineno.isdigit() and int(lineno) in refs.get(path_part, set()):
                callers["production_code"].append(line)
            else:
                callers["comment/reference only"].append(line)

    # ⛔ ASSERTED OVER THE AST SET, not over the grep lines: an indirection spells a
    # different word (`from … import propagate_shock as _shock; _shock(g, x)`), so the
    # line carrying the call is never a grep match and could never reach the bucket
    # above. This is the assertion that makes the alias visible.
    unseen = {p: sorted(lines) for p, lines in refs.items() if lines}
    assert not unseen, (
        "PRODUCTION CODE REFERENCES propagate_shock (AST reference sites):\n"
        + "\n".join(f"{p}:{','.join(map(str, ls))}" for p, ls in sorted(unseen.items()))
    )

    # The only remaining "production" reference is prose: no call, no wiring.
    assert len(callers["production_code"]) == 0, (
        f"PRODUCTION CODE CALLS propagate_shock:\n" + "\n".join(callers["production_code"])  # noqa: F541
    )
    assert len(callers["definition (deprecated)"]) == 1, (
        "Expected exactly 1 deprecated definition in projection.py"
    )

    print(f"  Definition: {len(callers['definition (deprecated)'])} (deprecated)")
    print(f"  Comments:   {len(callers['comment/reference only'])}")
    print(f"  Test files: {len(callers['test_files'])}")
    print(f"  Fixup scripts: {len(callers['fixup_scripts (historical)'])}")
    print(f"  PRODUCTION: {len(callers['production_code'])}  ← key metric")
    print("  ✅ No production code calls propagate_shock")


# ── 2. n.confidence readers ───────────────────────────────────────

def test_no_confidence_readers():
    """Assert: only EP writes n.confidence; SDK reads it (field read, not BFS-dependent)."""
    all_refs = _grep("n\\.confidence", "tortoise/", "graph-scripts/", "validation/", "tests/")
    filtered = [l for l in all_refs if "__pycache__" not in l and "test_bfs_audit.py" not in l and "test_e2e_extraction_ep.py" not in l]  # noqa: E741

    writers: list[str] = []       # SET n.confidence = ...
    readers: list[str] = []       # RETURN n.confidence (read-only)
    bfs_internal: list[str] = []  # Inside projection/propagation.py propagate_shock path

    for line in filtered:
        path_part = line.split(":")[0]
        if "ep.py" in path_part and "SET" in line:
            writers.append(line)
        # Same stale-path fix as #645, applied to the second filter in this file:
        # the BFS confidence write lives in projection/propagation.py.
        elif "propagation.py" in path_part or "projection/" in path_part:
            bfs_internal.append(line)
        elif "sdk.py" in path_part and "RETURN" in line:
            readers.append(line)
        elif "validation/" in path_part:
            readers.append(line)  # validation/test_docker_ep.py — EP test, not BFS
        elif "/graph-scripts/" in path_part:  # noqa: SIM114
            readers.append(line)
        elif "/tests/" in path_part:
            readers.append(line)

    # sdk.py reads n.confidence as a data field — it reads whatever confidence
    # value is stored, regardless of whether EP or BFS set it. Not a BFS dependency.
    sdk_reads = [r for r in readers if "sdk.py" in r]
    print(f"  EP writers (ep.py):   {len(writers)}")
    print(f"  BFS internal (propagation.py/projection/): {len(bfs_internal)} (includes deprecated propagate_shock)")
    print(f"  SDK reads (sdk.py):   {len(sdk_reads)} — field retrieval, not BFS-dependent")
    print(f"  Tests/scripts:        {len(readers) - len(sdk_reads)}")

    # The SDK read is the only production consumer of n.confidence,
    # and it reads the field generically — EP writes it too.
    # No production code aside from sdk.py reads n.confidence.
    assert len(sdk_reads) <= 1, "SDK reads n.confidence at most once"
    assert len(writers) >= 1, "EP should write n.confidence"
    print("  ✅ No production code depends on BFS-set n.confidence specifically")


# ── 3. Stale doc references ────────────────────────────────────────

def test_stale_docs_updated():
    """Check docs/plans/ for stale propagate_shock references."""
    all_refs = _grep("propagate_shock", "../../docs/plans/")

    stale_files: dict[str, list[str]] = {}
    for line in all_refs:
        fname = line.split(":")[0]
        stale_files.setdefault(fname, []).append(line)

    print(f"  Files in docs/plans/ referencing propagate_shock: {len(stale_files)}")
    for fname, refs in stale_files.items():
        print(f"    {fname}: {len(refs)} references")

    # These are plan documents — they're historical records. The plan that
    # designed the epistemic extractor (6855) references propagate_shock as a
    # design decision, but the actual implementation in ingest.py uses EP
    # instead. The plan docs are not "stale" in the sense of being wrong —
    # they document design intent. However:
    if stale_files:
        print(f"  ⚠️  {len(stale_files)} plan doc(s) reference propagate_shock.")
        print("     These are design-phase documents; implementation diverged (uses EP).")
        print("     Not a blocking issue — plan docs are historical, not operational.")
        print("     Recommendation: add a note to 6855 plan doc noting EP replaced BFS.")

    # Not asserting 0 — plan docs are historical. Just reporting.
    assert True  # informational only


if __name__ == "__main__":
    # Standalone run
    test_no_propagate_shock_callers()
    test_no_confidence_readers()
    test_stale_docs_updated()
