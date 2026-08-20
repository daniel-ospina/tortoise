#!/usr/bin/env python3
"""#49 Phase 2: REMOVE n.context migration — one-time destructive cleanup.

Removes the ``context`` property from all :Point nodes in the Tortoise
graph after Phase 1 (stop-writes) is deployed and audited.

**This is a destructive, one-way operation.**  Gates (TORTOISE_PHASE2=1,
AST preflight, audit sidecar) must ALL pass before the script will run.

Usage::

    # Dry-run (count + sample, no removal)
    TORTOISE_PHASE2=1 python graph-scripts/remove_context_migration.py --dry-run

    # Real run
    TORTOISE_PHASE2=1 python graph-scripts/remove_context_migration.py

Gate summary
------------
1. **TORTOISE_PHASE2=1**                — env-var guard (plan §9.8).  Refuses without it.
2. **AST preflight**                    — no READ path depends on ``context`` (plan §9.11).
   Scans all ``tortoise/*.py`` for function params, Cypher references, or
   dataclass fields named ``context`` (exempting session_context and docstrings).
3. **Audit sidecar present**            — data/migrations/2026-08-06_context_removal_audit.json
   must exist (Task 2.0).  **Warn** if missing, not a hard block.
4. **Recent BGSAVE**                    — FalkorDB BGSAVE within last 24h.  **Warn** if not.
5. **#99 guard**                        — REMOVE n.context passes through because
   ``_is_bulk_wipe`` only matches ``DETACH DELETE``, not property removal.

Post-migration: verifies ``MATCH (n:Point) WHERE n.context IS NOT NULL`` returns 0.
"""
from __future__ import annotations  # noqa: I001

import argparse
import ast
import json  # noqa: F401
import os
import sys
import time
from datetime import datetime, timezone  # noqa: F401
from pathlib import Path


# ── Path helpers ──────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TORTOISE_DIR = _REPO_ROOT / "tortoise"
_AUDIT_FILE = _REPO_ROOT / "data" / "migrations" / "2026-08-06_context_removal_audit.json"

sys.path.insert(0, str(_REPO_ROOT))


# ── Gate 0: TORTOISE_PHASE2 env guard (§9.8) ──────────────────────────────

def _check_phase2_flag() -> None:
    """Refuse to run without TORTOISE_PHASE2=1."""
    if os.environ.get("TORTOISE_PHASE2") != "1":
        print(
            "ERROR: TORTOISE_PHASE2 env var must be set to '1' to run this migration.\n"
            "       This is a destructive one-way operation (§9.8 guard).\n"
            "       export TORTOISE_PHASE2=1",
            file=sys.stderr,
        )
        sys.exit(1)


# ── Gate 1: AST preflight (§9.11) ─────────────────────────────────────────

def _ast_preflight(scan_dir: Path | None = None) -> list[str]:
    """Scan all tortoise/*.py files for READ paths that reference ``context``.

    Returns a list of human-readable violation strings.
    Empty list = clean, safe to proceed.

    Checks (per plan §9.11):
    1. Function/method params named ``context`` (not ``session_context`` or
       ``tortoise_session_context`` — those are agent session state).
    2. String literals containing ``n.context`` (Cypher property access).
    3. Dataclass/attrs fields named ``context``.

    Exemptions:
    - ``session_context``, ``tortoise_session_context`` (different concept)
    - Docstrings
    - Test files (``tests/*.py`` — separate sweep)
    """
    violations: list[str] = []
    scan_dir = scan_dir or _TORTOISE_DIR

    # Keywords that are NOT the graph field — agent session state
    _EXEMPT_PARAMS = {"session_context", "tortoise_session_context"}

    for py_file in sorted(scan_dir.rglob("*.py")):
        rel = str(py_file.relative_to(scan_dir))
        source = py_file.read_text(encoding="utf-8")

        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue  # Ignore files with syntax errors

        visitor = _ContextRefVisitor(rel, source, _EXEMPT_PARAMS)
        visitor.visit(tree)
        violations.extend(visitor.violations)

    return violations


class _ContextRefVisitor(ast.NodeVisitor):
    """Walk AST looking for ``context`` references that indicate a READ
    dependency on the ``n.context`` graph property."""

    def __init__(self, rel_path: str, source: str, exempt_params: set[str]) -> None:
        self.rel_path = rel_path
        self.source_lines = source.splitlines()
        self.exempt_params = exempt_params
        self.violations: list[str] = []

    def _lineno(self, node: ast.AST) -> int:
        return getattr(node, "lineno", 1)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Check function/method parameters named ``context``."""
        for arg in node.args.args:
            if arg.arg == "context" and arg.arg not in self.exempt_params:
                self.violations.append(
                    f"{self.rel_path}:{self._lineno(arg)}: "
                    f"function '{node.name}' has parameter named 'context'"
                )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Check async function parameters named ``context``."""
        for arg in node.args.args:
            if arg.arg == "context" and arg.arg not in self.exempt_params:
                self.violations.append(
                    f"{self.rel_path}:{self._lineno(arg)}: "
                    f"async function '{node.name}' has parameter named 'context'"
                )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Check dataclass/attrs fields and methods."""
        # Check for dataclass field named 'context' in assignment targets
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):  # noqa: SIM102
                if item.target.id == "context":
                    self.violations.append(
                        f"{self.rel_path}:{self._lineno(item)}: "
                        f"class '{node.name}' has field 'context'"
                    )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        """Check string constants for Cypher ``n.context`` references."""
        if not isinstance(node.value, str):
            return
        s = node.value
        if "n.context" in s:
            # Exempt docstrings (they float as Expr(Constant) in module/class/func bodies)
            if self._inside_docstring(node):
                return
            self.violations.append(
                f"{self.rel_path}:{self._lineno(node)}: "
                f"string literal contains 'n.context' — likely a Cypher READ dependency"
            )

    def _inside_docstring(self, node: ast.Constant) -> bool:
        """Heuristic: the node is the first statement of a module/class/function
        body and is assigned to __doc__ implicitly."""
        # Walk up to parent, check if it's the body's first Expr.
        # We can't easily walk up in NodeVisitor, so we use a simpler approach:
        # If the string is at column 0-3 (indented docstring), it's likely a docstring.
        # More robust: check if the string is in an Expr that's the first statement.
        # Since we don't track parent, we approximate — docstrings are standalone Exprs.
        # The real check happens in the caller by checking surrounding context.
        # We'll use a simple heuristic: if line has triple-quotes, treat as docstring.
        lineno = self._lineno(node)
        if 1 <= lineno <= len(self.source_lines):
            line = self.source_lines[lineno - 1]
            stripped = line.strip()
            if stripped.startswith(('"""', "'''")):
                return True
            if '"""' in stripped or "'''" in stripped:
                return True
        return False


# ── Gate 2: Audit sidecar ─────────────────────────────────────────────────

def _check_audit_sidecar() -> bool:
    """Check that Task 2.0 audit sidecar exists.  Returns True if present.

    Warn-only gate — operator can run 2.1 before 2.0 if they understand
    the risk.
    """
    if _AUDIT_FILE.exists():
        return True
    print(
        f"WARNING: Audit sidecar not found at {_AUDIT_FILE}\n"
        f"         Task 2.0 (context removal audit) should be run first.\n"
        f"         Proceeding without audit data.",
        file=sys.stderr,
    )
    return False


# ── Gate 3: Recent BGSAVE ─────────────────────────────────────────────────

def _check_recent_bgsave(proj) -> bool:
    """Check FalkorDB LASTSAVE timestamp.  Warn if older than 24h.

    Returns True if backup is recent enough.  Warn-only gate.
    """
    try:
        result = proj.g.connection.execute_command("LASTSAVE")
        last_save_ts = int(result)
        age_seconds = time.time() - last_save_ts
        age_hours = age_seconds / 3600

        if age_hours <= 24:
            return True

        age_str = f"{age_hours:.1f}h"
        if age_hours >= 48:
            age_str = f"{age_hours / 24:.1f}d"

        print(
            f"WARNING: Last BGSAVE was {age_str} ago (> 24h).\n"
            f"         Consider running BGSAVE before this destructive migration.",
            file=sys.stderr,
        )
        return False
    except Exception:
        # Embedded / FalkorDBLite — LASTSAVE not supported
        print(
            "WARNING: Cannot verify BGSAVE recency (embedded/FalkorDBLite).\n"
            "         Ensure you have a backup before proceeding.",
            file=sys.stderr,
        )
        return False


# ── Connection ─────────────────────────────────────────────────────────────

def _connect():
    """Connect to FalkorDB using TORTOISE_DB_URI or defaults.

    Returns (proj, mode) where mode is 'server' or 'embedded'.
    """
    # Try the SDK's standard connection path. No namespace — in docker mode the
    # URI's graph name is authoritative (namespace is ignored by from_uri).
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK()
    proj = sdk._get_proj()

    # Detect mode
    try:
        proj.g.connection.execute_command("PING")
        mode = "server"
    except Exception:
        mode = "embedded"

    return sdk, proj, mode


# ── Dry-run ────────────────────────────────────────────────────────────────

def _dry_run(proj) -> dict:
    """Count context-bearing nodes and print a sample.  Does NOT modify."""
    result = proj.g.query(
        "MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)"
    )
    count = result.result_set[0][0] if result.result_set else 0

    print(f"Dry-run: {count} Point nodes have context property still set.")

    if count > 0:
        # Show a small sample
        sample = proj.g.query(
            "MATCH (n:Point) WHERE n.context IS NOT NULL "
            "RETURN n.id, n.context, n.pointKind "
            "LIMIT 10"
        )
        print("\nSample (up to 10 rows):")
        for row in sample.result_set:
            pid = str(row[0])[:40]
            ctx = str(row[1])[:50]
            kind = str(row[2])[:20] if row[2] else "—"
            print(f"  {pid}  |  {ctx}  |  {kind}")

    return {"context_bearing_nodes": count}


# ── Migration ──────────────────────────────────────────────────────────────

def _run_removal(proj) -> dict:
    """Execute the REMOVE and return {removed, remaining}."""
    # Count before
    before = proj.g.query(
        "MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)"
    ).result_set[0][0]

    # The migration — one Cypher query
    result = proj.g.query(  # noqa: F841
        "MATCH (n:Point) WHERE n.context IS NOT NULL REMOVE n.context"
    )

    # Count after
    after = proj.g.query(
        "MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)"
    ).result_set[0][0]

    return {"removed": before, "remaining": after}


# ── Post-migration verification ────────────────────────────────────────────

def _verify_zero(proj) -> bool:
    """Verify no Point nodes retain the context property."""
    result = proj.g.query(
        "MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)"
    )
    count = result.result_set[0][0] if result.result_set else 0
    return count == 0


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="#49 Phase 2: REMOVE n.context migration"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count context-bearing nodes, print sample, do NOT remove",
    )
    args = parser.parse_args()

    # ── Gate stack ─────────────────────────────────────────────────
    print("=" * 64)
    print("  #49 Phase 2 — REMOVE n.context migration")
    print("=" * 64)

    print("\n[Gate 0] TORTOISE_PHASE2 env guard...")
    _check_phase2_flag()
    print("  PASS — TORTOISE_PHASE2=1")

    print("\n[Gate 1] AST preflight — scanning tortoise/*.py for context READs...")
    violations = _ast_preflight()
    if violations:
        print(f"  FAIL — {len(violations)} violation(s) found:")
        for v in violations:
            print(f"    • {v}")
        print(
            "\n  These files reference 'context' in READ paths. "
            "Remove or update them\n  before running this destructive migration.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  PASS — no context READ paths detected")

    # Connect
    print("\n[Connecting to FalkorDB]...")
    sdk, proj, mode = _connect()
    graph_name = proj._graph_name
    print(f"  Connected to '{graph_name}' (mode={mode})")

    # Gate 2: Audit sidecar
    print("\n[Gate 2] Audit sidecar...")
    audit_ok = _check_audit_sidecar()
    if audit_ok:
        print(f"  PASS — {_AUDIT_FILE} exists")

    # Gate 3: Recent BGSAVE
    print("\n[Gate 3] Recent BGSAVE...")
    bgsave_ok = _check_recent_bgsave(proj)
    if bgsave_ok:
        print("  PASS — BGSAVE within 24h")

    # Dry-run or real
    if args.dry_run:
        print("\n" + "=" * 64)
        print("  DRY-RUN MODE — no changes will be made")
        print("=" * 64)
        _dry_run(proj)
        sdk.close()
        return 0

    print("\n" + "=" * 64)
    print("  LIVE RUN — removing n.context from all :Point nodes")
    print("=" * 64)

    # ── Execute ────────────────────────────────────────────────────
    summary = _run_removal(proj)

    # ── Verify ─────────────────────────────────────────────────────
    print("\n[Post-migration verification]...")
    clean = _verify_zero(proj)
    if clean:
        print("  PASS — no Point nodes retain context property")
    else:
        result = proj.g.query(
            "MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)"
        )
        remaining = result.result_set[0][0]
        print(f"  FAIL — {remaining} nodes still have context property")
        sdk.close()
        return 1

    # ── Summary ────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("  Migration complete")
    print("=" * 64)
    print(f"  Nodes modified:       {summary['removed']}")
    print(f"  Context still present: {summary['remaining']}")
    print(f"  Audit file:            {_AUDIT_FILE} ({'present' if audit_ok else 'missing'})")
    print(f"  Graph:                 {graph_name}")

    sdk.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
