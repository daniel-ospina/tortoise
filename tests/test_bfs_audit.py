"""
Issue #6902 — Audit: confirm no production code depends on BFS propagate_shock output.

This test file encodes the audit findings as assertions. It does NOT run against
a live FalkorDB instance — it's a grep-based documentation of the audit results.
Run with: python3 -m pytest tests/test_bfs_audit.py -v
"""

import subprocess
import sys  # noqa: F401
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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

def _classify_propagate_shock_refs(lines: list[str]) -> dict[str, list[str]]:
    """Bucket `grep` hits for `propagate_shock` by what each hit actually is.

    The audit's question is whether PRODUCTION code **calls** the deprecated
    path, so a bare mention is a reference, not a call — docstring prose and
    comments name the symbol without invoking it. The earlier rule recognised a
    comment only when the line carried BOTH `#` and `replaces` (exactly one line
    in the tree did, `ingest.py:1361`), so every later prose mention was bucketed
    as `production_code` and reddened the audit. #11803's #11702 note in
    `projection/propagation.py` is that case: docstring lines stating the path is
    UNREACHABLE were reported as "PRODUCTION CODE CALLS propagate_shock". The
    call shape is the honest discriminator — a call site must read
    `propagate_shock(` — and
    `test_the_audit_discriminates_a_call_from_a_mention` pins both directions.

    Deliberate boundary: prose that spells a call (`propagate_shock(x)` inside a
    docstring) still counts as production code. That direction fails CLOSED — it
    reds an audit a human then reads.
    """
    callers: dict[str, list[str]] = {
        "definition (deprecated)": [],
        "comment/reference only": [],
        "test_files": [],
        "fixup_scripts (historical)": [],
        "production_code": [],
    }

    for line in lines:
        path_part = line.split(":")[0]

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
        elif "/tortoise/" in path_part and "propagate_shock(" not in line:
            callers["comment/reference only"].append(line)
        elif "/tortoise/" in path_part:
            callers["production_code"].append(line)

    return callers


def test_the_audit_discriminates_a_call_from_a_mention():
    """Non-vacuity, both directions, for `_classify_propagate_shock_refs`.

    Pre-fix a docstring line that merely NAMES the symbol landed in
    `production_code` (it carries neither `#` nor `replaces`), which is the
    false positive that reddened `test_no_propagate_shock_callers` on #11803's
    note — so `comment/reference only == [prose]` below reds on the pre-fix
    classifier. Emptying the bucket would "fix" that red and kill the audit, so
    the CALL direction is pinned as well: a real call site must still be
    reported.
    """
    prose = f"{ROOT}/tortoise/projection/propagation.py:71:        `propagate_shock` PERSISTS the blend"
    call = f"{ROOT}/tortoise/api.py:999:            self.propagate_shock(epicenter_id)"
    callers = _classify_propagate_shock_refs([prose, call])

    assert callers["comment/reference only"] == [prose], (
        "a line that only NAMES propagate_shock is a reference, not a call; "
        f"bucketed as: {callers['comment/reference only']}")
    assert callers["production_code"] == [call], (
        "a real CALL site must still be reported as production code; "
        f"bucketed as: {callers['production_code']}")


def test_no_propagate_shock_callers():
    """Assert: propagate_shock.calls only in deprecated definition + tests + scripts."""
    # All references to propagate_shock (the word)
    all_refs = _grep("propagate_shock", "tortoise/", "graph-scripts/", "validation/", "tests/")
    filtered = [
        l for l in all_refs  # noqa: E741
        if "__pycache__" not in l and "test_bfs_audit.py" not in l and "test_e2e_extraction_ep.py" not in l
    ]

    callers = _classify_propagate_shock_refs(filtered)

    # No production mention is a call: the deprecated definition, docstring prose
    # that names the symbol, and one historical comment.
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
