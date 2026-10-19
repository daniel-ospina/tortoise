#!/usr/bin/env python3
"""`tools/record_wiring_check.py` — is the record written by PRODUCTION? (#9078)

THE CLAIM THIS CHECKS. `#7950`'s capstone returned S2 **NOT-GREEN** for one
reason: `tortoise/record_projection.py` (Interface A: journal → Postgres) and
its siblings had **zero production importers** — the only importer was the
PGlite test harness — so "Postgres is the system of record" (§2.0) was a claim
about a test fixture. #9078 wires the live path and its acceptance names this
tool as the command that proves it.

WHAT IT ASSERTS, exactly (and no more):

  1. some module under `tortoise/` OTHER than `record_projection` itself imports
     `record_projection` — i.e. there is a production caller, not a definition;
  2. that importer is not inside the probe tree (`docs/evidence/**`) and not a
     test: a probe import is the DEFECT the issue names, not the fix;
  3. some module in the package **CALLS** `record_writer.project(...)` — a call,
     not an import. Both earlier cuts of this check were false PASSes: the first
     accepted "the writer module exists" (it imports the engine the moment it
     exists, so an uncalled library reported WIRED), and the second accepted an
     IMPORT of the seam (a module can import it and never call it). Reproduced
     both times against a synthetic tree; this leg pins the call site itself.

WHAT IT DOES NOT ASSERT. That any specific record reaches Postgres. That is a
runtime fact, pinned by `tests/test_9078_record_written_by_production_path.py`
(the production path's statement set, through a recording connection) and by
the PGlite harness for the SQL itself. A static check must not pretend to be a
runtime one — that would be a false PASS, which is the failure class this whole
issue series exists to remove.

USAGE

    python tools/record_wiring_check.py            # exit 0 = wired, 1 = not
    python tools/record_wiring_check.py --json     # machine-readable report

Exit codes: 0 = a production importer exists and the writer seam is one of
them; 1 = the record has no production caller (the #7950 S2 finding); 2 = the
check could not run (the package directory is missing).
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "tortoise"
RECORD_MODULE = "record_projection"
WRITER_MODULE = "record_writer"
#: Trees whose imports do NOT count as production: a probe or a test import is
#: the defect (`docs/evidence/**` was the capstone's own module set).
NON_PRODUCTION_PARTS = ("tests", "evidence")


@dataclass
class Report:
    """The importers found, and whether the record is wired in production."""

    importers: list[str] = field(default_factory=list)
    callers: list[str] = field(default_factory=list)
    non_production: list[str] = field(default_factory=list)
    missing_package: bool = False

    @property
    def ok(self) -> bool:
        # BOTH legs: the record engine has a production importer, AND the writer
        # seam that importer is has a production caller.
        return (bool(self.importers) and WRITER_MODULE in self.importers
                and bool(self.callers))

    def render(self) -> str:
        if self.missing_package:
            return f"cannot check: no {PACKAGE}/ package at {REPO_ROOT}"
        lines = [
            f"record module: {PACKAGE}/{RECORD_MODULE}.py",
            f"production importers ({len(self.importers)}): "
            + (", ".join(sorted(self.importers)) or "NONE"),
            f"callers of the writer seam ({len(self.callers)}): "
            + (", ".join(sorted(self.callers)) or "NONE"),
        ]
        if self.non_production:
            lines.append(
                "ignored (not production): " + ", ".join(sorted(self.non_production))
                + " — a probe/test import is the #7950 S2 defect, not the fix")
        if not self.callers:
            lines.append(
                f"NOT WIRED: nothing in {PACKAGE}/ CALLS "
                f"{WRITER_MODULE}.project(...), so the record has a library and "
                "no caller (#9078)")
        elif not self.ok:
            lines.append(
                f"NOT WIRED: {PACKAGE}/{WRITER_MODULE}.py is not among the "
                "importers, so nothing in production writes the record (#9078)")
        else:
            lines.append("WIRED: the live write path imports the record engine")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "record_module": f"{PACKAGE}/{RECORD_MODULE}.py",
            "importers": sorted(self.importers),
            "callers": sorted(self.callers),
            "non_production": sorted(self.non_production),
            "missing_package": self.missing_package,
        }


def _imports(path: Path, module: str) -> bool:
    """Does *path* import *module* (either `from . import x` or `import x`)?

    Parsed with `ast` rather than grepped: a docstring that MENTIONS
    `record_projection` (several do — `tortoise/consistency.py` calls the
    journal's own state by a similar name) must not count as a caller, and a
    grep cannot tell the two apart.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module or alias.name.endswith(f".{module}"):
                    return True
        elif isinstance(node, ast.ImportFrom):
            # `from .record_projection import x` / `from tortoise.record_projection import x`
            if node.module and node.module.split(".")[-1] == module:
                return True
            # `from . import record_projection` / `from tortoise import record_projection`
            if node.module in (None, "") or node.level == 0:
                for alias in node.names:
                    if alias.name == module or alias.name.split(".")[-1] == module:
                        return True
    return False


def _calls_project(path: Path) -> bool:
    """Does *path* contain a CALL to the seam — not an import, not a mention?

    Two spellings are accepted, because both are real callers:
    ``record_writer.project(...)`` (the three live doors' form) and a bare
    ``project(...)`` guarded by ``from … record_writer import project``. An
    earlier cut matched only the first, which would have let a fourth door
    written as a from-import append records while this tool reported WIRED.

    Matched on the AST, so a docstring or a comment that names the seam cannot
    satisfy it (the two earlier false PASSes of this tool were both satisfied by
    a mere mention/import).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return False
    aliased = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module and node.module.split(".")[-1] == WRITER_MODULE
        for alias in node.names
        if alias.name == "project"
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "project":
            owner = func.value
            if isinstance(owner, ast.Name) and owner.id == WRITER_MODULE:
                return True
        elif isinstance(func, ast.Name) and func.id in aliased:
            return True
    return False


def check(root: Path = REPO_ROOT) -> Report:
    """Scan the package for production importers of the record engine."""
    package = root / PACKAGE
    if not package.is_dir():
        return Report(missing_package=True)
    report = Report()
    for path in sorted(package.rglob("*.py")):
        stem = path.stem
        relative = path.relative_to(root)
        test_shaped = (any(part in NON_PRODUCTION_PARTS for part in relative.parts)
                       or stem.startswith("test_"))
        if stem != RECORD_MODULE and _imports(path, RECORD_MODULE):
            (report.non_production if test_shaped else report.importers).append(stem)
        if stem != WRITER_MODULE and _calls_project(path):
            (report.non_production if test_shaped else report.callers).append(stem)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--root", default=str(REPO_ROOT), help="repo root to scan")
    args = parser.parse_args(argv)

    report = check(Path(args.root))
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(report.render())
    if report.missing_package:
        return 2
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
