"""`tortoise/sdk.py` declares exactly one module-level `_now_iso`, and that one is live.

`sdk.py` carried two module-level `def _now_iso`. The later one shadowed the earlier at
import time, so the earlier definition was unreachable dead code and an edit to only one
of them would have silently changed the clock for every caller. The tests here pin the
single-definition invariant, the binding order that makes shadowing dangerous, and the
helper's UTC-offset contract.

No database is touched: the structural tests read `tortoise/sdk.py` from disk with
`ast.parse`, and the contract test exercises a pure clock helper.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SDK_PATH = ROOT / "tortoise" / "sdk.py"
SDK_SOURCE = SDK_PATH.read_text(encoding="utf-8")

_IMPORT_TIME_COMPOUND = (ast.If, ast.Try, ast.With, ast.For, ast.While)


def _module_level_now_iso_defs(source: str) -> list[int]:
    """Line numbers of `_now_iso` defs that bind the module global at import time.

    Descends into import-time compound statements (`if`/`try`/`with`/`for`/`while`),
    because a def inside one of those still executes at import and rebinds
    `tortoise.sdk._now_iso` — the hazard this guards is binding ORDER, not indentation.
    """
    found: list[int] = []

    def walk(statements: list[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "_now_iso":
                    found.append(node.lineno)
            elif isinstance(node, ast.ClassDef):
                continue  # a method is not a module-global binding
            elif isinstance(node, _IMPORT_TIME_COMPOUND):
                walk(node.body)
                walk(getattr(node, "orelse", []))
                walk(getattr(node, "finalbody", []))
                for handler in getattr(node, "handlers", []):
                    walk(handler.body)

    walk(ast.parse(source).body)
    return found


def test_sdk_declares_exactly_one_module_level_now_iso():
    """Falsifier: a second module-level `def _now_iso` in `tortoise/sdk.py`.

    (1) Failing value: `len(defs) != 1` — the assertion compares against the literal 1.
    (2) Reachable in the fixture: the fixture IS `tortoise/sdk.py` as checked out. On the
        unfixed source the parse of that file yields two defs, at lines 2316 and 22459,
        so the failing value is present and this test reds before the fix.
    """
    defs = _module_level_now_iso_defs(SDK_SOURCE)
    assert len(defs) == 1, (
        f"tortoise/sdk.py has {len(defs)} module-level `_now_iso` defs at lines {defs}; "
        "the later def shadows the earlier at import, making the earlier unreachable "
        "dead code and letting a one-sided edit silently change the clock"
    )


def test_sdk_now_iso_first_module_level_def_is_the_bound_global():
    """Falsifier: a module-level def that is shadowed before import finishes.

    (1) Failing value: `defs[0] != tortoise.sdk._now_iso.__code__.co_firstlineno`. The
        first declared def must be the one import actually binds; if a later def
        overwrites it, the first is dead code and the test's two line numbers diverge.
    (2) Reachable in the fixture: yes — on the unfixed source `defs[0]` is 2316 while the
        bound function is at 22459, so the values differ and this test reds before the
        fix. A single-def file can still fail this (a def shadowed by a later import or
        assignment), so it is not merely a restatement of the count test.
    """
    defs = _module_level_now_iso_defs(SDK_SOURCE)
    assert defs, "tortoise/sdk.py declares no module-level `_now_iso`"

    from tortoise import sdk

    bound_line = sdk._now_iso.__code__.co_firstlineno
    assert defs[0] == bound_line, (
        f"the first module-level `_now_iso` (line {defs[0]}) is not the one bound at "
        f"import (line {bound_line}); the earlier definition is dead code"
    )


def test_sdk_now_iso_returns_utc_offset_string():
    """Falsifier: the helper returning a naive or non-UTC timestamp.

    (1) Failing value: any returned string not ending in `+00:00` — e.g. a body changed
        to `datetime.now().isoformat()` yields a naive value with no offset, and a
        fixed-offset zone yields `+05:00`. Either fails the `endswith("+00:00")`.
    (2) Reachable in the fixture: the assertion runs against the live return value of a
        pure clock helper, not a canned input, so the failing value is producible by
        editing the helper body — the contract is genuinely checked, not assumed.
    """
    from tortoise import sdk

    ts = sdk._now_iso()
    assert isinstance(ts, str), f"expected str, got {type(ts).__name__}"
    assert ts.endswith("+00:00"), f"expected a +00:00 UTC offset, got {ts!r}"

    # The resolved helper must come from the file the AST tests parse — import resolution
    # is exercised here too, since `_now_iso` is private and not re-exported.
    resolved = Path(sdk._now_iso.__code__.co_filename).resolve()
    assert resolved == SDK_PATH, f"resolved `_now_iso` from unexpected file {resolved}"
