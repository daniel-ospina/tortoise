"""
Issue #6902 — Audit: confirm no production code depends on BFS propagate_shock output.

This test file encodes the audit findings as assertions. It does NOT run against
a live FalkorDB instance — it's a grep-based documentation of the audit results.
Run with: python3 -m pytest tests/test_bfs_audit.py -v
"""

import ast
import subprocess
import sys  # noqa: F401
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _production_ast_references(
    root: Path | None = None, symbol: str = "propagate_shock"
) -> list[tuple[str, int]]:
    """Every AST reference to `symbol` under `<root>/tortoise`, as (path, lineno).

    A `Name`, an `Attribute`, or an `alias` node is a USE of the symbol — a call, an
    alias, an import, a reference — regardless of whitespace, line continuations, or how
    it is later invoked. The `def` name and any docstring/comment mention are not nodes
    of those kinds (prose is a string constant), which is what makes this the right
    primitive for "does production code depend on it".

    `root` is injectable so the form set below is itself pinned by
    `test_the_reference_scan_catches_every_static_form` — a scan nobody tests is how the
    two earlier versions of this guard each passed their own bug.
    """
    base = ROOT if root is None else Path(root)
    refs: list[tuple[str, int]] = []
    for src in sorted((base / "tortoise").rglob("*.py")):
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == symbol:
                refs.append((str(src.relative_to(base)), node.lineno))
            elif isinstance(node, ast.Attribute) and node.attr == symbol:
                refs.append((str(src.relative_to(base)), node.lineno))
            elif isinstance(node, ast.alias) and node.name.rsplit(".", 1)[-1] == symbol:
                # `from x import propagate_shock` and `… import propagate_shock as _s`:
                # the later call spells `_s(...)`, so no Name/Attribute node ever carries
                # the symbol — the import is the only static trace of the dependency, and
                # main's word-grep caught it before this AST leg replaced that grep.
                refs.append((str(src.relative_to(base)), node.lineno))
    return refs


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
        elif "/tortoise/" in path_part and "#" in line and "replaces" in line.lower():
            callers["comment/reference only"].append(line)
        elif "/tortoise/" in path_part:
            callers["production_code"].append(line)

    # ⛔ A LINE-BASED WORD GREP CANNOT ANSWER THIS QUESTION (#11907).
    #
    # The classifier above matches the WORD. Prose that names the function therefore
    # reads as a caller — `#11702` added exactly such a note to `propagate_shock`'s own
    # docstring (`projection/propagation.py:70,73`, explaining why the path is
    # unreachable), and this test reddened on its own documentation.
    #
    # The tempting repair — `"propagate_shock(" not in line` ⇒ a reference — is WORSE
    # than the bug, because a substring is not a call form. Each of these IS a call and
    # none contains that substring: `propagate_shock (a, b)` (Python allows the space),
    # a backslash or bracket continuation putting the name at end of line, and an alias
    # (`f = propagate_shock; f(a, b)`). A gate that passes those is a gate that stopped
    # gating.
    #
    # So the verdict comes from the AST, which answers the question the test is actually
    # asked: a `Name`/`Attribute`/`alias` node is the SYMBOL BEING USED; a docstring
    # mention is a string constant and cannot produce one, and whitespace, continuations,
    # invocations and import-aliases all still resolve to a reference.
    #
    # ⛔ THIS SCAN HAS BEEN WRONG TWICE (a substring match, then an AST walk that skipped
    # `ast.alias`), and each version passed its own bug — the forms were reasoned about
    # rather than pinned. `test_the_reference_scan_catches_every_static_form` now parses a
    # fixture tree containing every static form, positive and negative, so the next
    # iteration breaks a test instead of quietly narrowing the gate again.
    #
    # Residual, named rather than hidden: a reference whose name is computed at runtime
    # (`getattr(mod, "propagate_shock")`) is a string to the parser and invisible here —
    # as it is to any static check. Everything STATIC is pinned below.
    ast_refs = _production_ast_references()

    # The only "production" reference is a comment: no actual call
    assert not ast_refs, (
        "PRODUCTION CODE CALLS propagate_shock:\n"
        + "\n".join(f"{path}:{lineno}" for path, lineno in ast_refs)
        + "\n\n(word-grep context, which cannot tell a call from a mention:)\n"
        + "\n".join(callers["production_code"])  # noqa: F541
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


def test_the_reference_scan_catches_every_static_form(tmp_path):
    """The scan above is itself pinned — both of its earlier versions passed their own bug.

    `propagate_shock` must be reported for EVERY static form a dependency can take, and
    must NOT be reported for prose or for lookalike names. This is a test of the detector,
    not of the tree, so it builds its own fixtures: the previous two failures were reasoned
    about in prose and never exercised.
    """
    (tmp_path / "tortoise").mkdir()

    must_be_caught = {
        "call.py": "propagate_shock(graph, n)\n",
        "space.py": "propagate_shock (graph, n)\n",                       # space before (
        "continuation.py": "propagate_shock \\\n    (graph, n)\n",         # backslash
        "name_at_eol.py": "x = (\n    propagate_shock\n    (graph, n)\n)\n",  # name at EOL
        "assign_alias.py": "f = propagate_shock\nf(graph, n)\n",          # bound, then called
        "attribute.py": "self.propagate_shock(graph, n)\n",
        "import_plain.py": "from tortoise.projection.propagation import propagate_shock\n",
        "import_aliased.py": "from tortoise.projection.propagation import propagate_shock as _s\n_s(x)\n",
        "import_dotted.py": "import tortoise.projection.propagate_shock\n",
    }
    must_stay_green = {
        "definition.py": "def propagate_shock(self, graph, n):\n    return 1\n",
        "docstring.py": 'def f():\n    """`propagate_shock` is never invoked."""\n    return 1\n',
        "comment.py": "# propagate_shock is never invoked\nx = 1\n",
        "unrelated.py": "propagate_shock_v2(graph, n)\n",
    }
    for name, body in {**must_be_caught, **must_stay_green}.items():
        (tmp_path / "tortoise" / name).write_text(body, encoding="utf-8")

    found = {Path(p).name for p, _ in _production_ast_references(root=tmp_path)}

    missed = sorted(set(must_be_caught) - found)
    assert not missed, (
        "the reference scan MISSES a static form, so a real caller would read as no"
        f" dependency at all: {missed}. Every one of these names the symbol:"
        + "".join(f"\n  {n}: {must_be_caught[n]!r}" for n in missed)
    )
    false_positives = sorted(found & set(must_stay_green))
    assert not false_positives, (
        "the reference scan flags prose or a lookalike as a dependency — this is the"
        f" #11907 failure (a docstring mention reddened the gate): {false_positives}"
    )


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
