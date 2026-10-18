"""#8920 review P3 — a renamed harness must keep every pointer to it resolvable.

THE DEFECT THIS PINS
--------------------
PR #8920 moved the `TestClient` background-work quiesce OUT of
`tests/test_export_delete.py` into `tests/_http_fixtures.py` and dropped its
leading underscore (`_quiesce_testclient_background_work` →
`quiesce_testclient_background_work`; `_quiet_boot_sweeps` →
`quiet_boot_sweeps`). The DEFINITIONS moved and were renamed. The REFERENCES
were not, so five of them named symbols that no longer existed anywhere. The
moved docstring had the same problem one level down: it named its callee
`_purge_deleted_teams`, and the callee is `_purge_deleted_orgs` (seven
occurrences) — a docstring that misnames the function under test is the worst
case of the same defect, because the enumeration argument below is what keeps
the seam complete.

The one that matters is production source. `tortoise/hosted_api.py` records
this helper as THE harness pattern for the #3472 duplicate-purge overlap — it
is the stated reason the helper is a canonical seam rather than a per-file
copy — and it still read `tests/test_export_delete._quiesce_testclient_background_work`.
A lane following that pointer found nothing, and the enumeration argument the
helper's own docstring makes ("keep it complete: a new sweep reachable from
either entry point belongs in this list") depends on a reader being able to
FIND the list. This is the "relay the answer to the artifact the next lane
reads" discipline, in code: a record that names a deleted symbol is not a
record.

WHAT THIS PINS
--------------
1. Every `tests.<module>.<symbol>` / `tests/<module>.py.<symbol>` pointer in
   production source RESOLVES — the module imports and the attribute exists.
   Scoped to the shape a RENAME breaks; `tests/<file>.py` existence pointers
   and `::node_id` pointers are not resolved here.
2. No source file still names the pre-move symbols — a stale name in a
   comment or docstring is exactly what made (1) un-followable, and prose is
   not reachable by any other guard.
3. The seam is SHARED, not re-copied: the helper is DEFINED exactly once
   (`tests/_http_fixtures.py`) and each consumer IMPORTS it (AST, not grep).
   The consumer list included a FALSE premise until the second review: it
   excluded `test_writer_inventory.py` on the claim that the boot sweep cannot
   reach that module's fake. It can — the same #4816 `rpc_calls` race, merely
   never asserted on. `CONSUMERS` now names it, so this check reddens on the
   pre-fix module (no import) and on any future consumer that copies instead.

NON-VACUITY: on the pre-fix head (`f0d5144`) check 1 reddens on the
`hosted_api.py` pointer (`tests.test_export_delete` has no attribute
`_quiesce_testclient_background_work`) and check 2 reddens on five stale
`_quiesce...`/`_quiet...` occurrences plus seven `_purge_deleted_teams` ones,
across `tortoise/hosted_api.py`, `tests/test_export_delete.py` and
`tests/_http_fixtures.py`. Verified by restoring those three files from
`f0d5144` and re-running this module against it.

Static: no network, no DB, no graph.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
PRODUCTION_DIR = ROOT / "tortoise"

#: `tests.<module>.<symbol>` or `tests/<module>[.py].<symbol>`, inside
#: backticks. Deliberately narrow: a bare file pointer
#: (`tests/test_x.py` — the `.py` lands in the symbol group, filtered by
#: `_NOT_A_SYMBOL`), a data file (`tests/x.json`), an ellipsis
#: (`tests/...boundary.py`) and a node id (`tests/test_x.py::node`) are out of
#: scope, so this guard needs no per-token escape hatch.
_POINTER = re.compile(
    r"`(?:tests\.(?P<dotted>[A-Za-z0-9_]+)\.(?P<dsym>[A-Za-z0-9_]+)"
    r"|tests/(?P<path>[A-Za-z0-9_/]+?)(?:\.py)?\.(?P<psym>[A-Za-z0-9_]+))`"
)

#: A trailing token that is a file EXTENSION, not a symbol — i.e. the pointer
#: names a whole file, which is outside this guard's scope (a file pointer
#: does not break on a rename unless the file itself moves).
_NOT_A_SYMBOL = frozenset({"py", "json", "yml", "yaml", "md", "txt", "toml"})

#: Names the #8920 move renamed away or never existed: the two pre-rename
#: helpers, and the callee the moved docstring misspelled (`_purge_deleted_orgs`
#: is the real function — see `tortoise/hosted_api.py`). A tree that still
#: mentions any of them carries a reference the rename left behind.
STALE_NAMES = (
    "_quiesce_testclient_background_work",
    "_quiet_boot_sweeps",
    "_purge_deleted_teams",
)

SHARED_MODULE = "_http_fixtures"
HELPER = "quiesce_testclient_background_work"
#: Every fixture module that opens `TestClient(app)` on a shared store and
#: therefore needs the quiesce. THREE today, not two: `test_writer_inventory.py`
#: was excluded on a FALSE premise ("the boot sweep does not reach their
#: fake"). It does — its `supabase_env` fixture monkeypatches
#: `supabase_control.get_control_plane` to `FakeControlPlane`, and the sweep's
#: metering step resolves that name at call time, so
#: `metering_repair_period_bounds` landed in `fake.rpc_calls` there too; that
#: module simply never noticed because every pin is name/filter-selected. The
#: premise is the defect a future reader would trust before adding an index
#: assertion to that module, so the module now applies the seam and this guard
#: covers it.
#: `test_supabase_control.py` is the one real exclusion: those tests drive
#: `provision_org` / `_session_user_org` directly with no `TestClient`, so no
#: lifespan, so no sweep.
CONSUMERS = (
    "test_export_delete.py",
    "test_pack_state.py",
    "test_writer_inventory.py",
)


def _production_pointers() -> list[tuple[Path, str, str, str]]:
    """Every (file, token, module, symbol) dotted pointer in production source."""
    found: list[tuple[Path, str, str, str]] = []
    for path in sorted(PRODUCTION_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _POINTER.finditer(text):
            dotted = match.group("dotted")
            module = dotted if dotted else match.group("path").replace("/", ".")
            symbol = match.group("dsym") or match.group("psym")
            if symbol in _NOT_A_SYMBOL:
                continue
            found.append((path, match.group(0), f"tests.{module}", symbol))
    return found


def test_production_pointers_into_the_test_tree_resolve():
    pointers = _production_pointers()
    assert pointers, (
        "no `tests.<module>.<symbol>` pointer found under tortoise/ — the "
        "scan itself broke, so this guard would pass vacuously"
    )
    broken: list[str] = []
    for path, token, modname, symbol in pointers:
        try:
            module = importlib.import_module(modname)
        except Exception as exc:
            broken.append(f"{path.relative_to(ROOT)}: {token} → {modname}: {exc}")
            continue
        if not hasattr(module, symbol):
            broken.append(
                f"{path.relative_to(ROOT)}: {token} → {modname}.{symbol} "
                f"does not exist (rename without a relay?)"
            )
    assert not broken, (
        "production source names test symbols that do not resolve:\n  "
        + "\n  ".join(broken)
    )


def test_no_source_still_names_the_pre_move_symbols():
    # This guard must SPELL the names it forbids, so it is the one file
    # exempt from its own scan; every other module — and the production tree —
    # is checked.
    itself = Path(__file__).name
    hits: list[str] = []
    for root in (PRODUCTION_DIR, TESTS_DIR):
        for path in sorted(root.rglob("*.py")):
            if path.name == itself:
                continue
            text = path.read_text(encoding="utf-8")
            if not any(name in text for name in STALE_NAMES):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                for name in STALE_NAMES:
                    if name in line:
                        hits.append(f"{path.relative_to(ROOT)}:{lineno}: {name}")
    assert not hits, (
        "the #8920 move renamed these symbols; a comment or docstring still "
        "naming the old ones points a reader at nothing:\n  "
        + "\n  ".join(hits)
    )


def test_the_seam_is_defined_once_and_imported_by_its_consumers():
    shared = importlib.import_module(f"tests.{SHARED_MODULE}")
    assert callable(getattr(shared, HELPER, None)), (
        f"tests.{SHARED_MODULE}.{HELPER} must exist and be callable — "
        "tortoise/hosted_api.py records it as the canonical harness pattern"
    )

    define = re.compile(rf"^[ \t]*def[ \t]+{HELPER}[ \t]*\(", re.MULTILINE)
    definers = [
        path.name
        for path in sorted(TESTS_DIR.rglob("*.py"))
        if define.search(path.read_text(encoding="utf-8"))
    ]
    assert definers == [f"{SHARED_MODULE}.py"], (
        f"{HELPER} must be DEFINED exactly once (tests/{SHARED_MODULE}.py); "
        f"found definitions in {definers}. A per-consumer copy stops being "
        "updated when `_lifespan` grows a new background caller — the "
        "#3472/#3505 defect."
    )

    missing: list[str] = []
    for consumer in CONSUMERS:
        source = (TESTS_DIR / consumer).read_text(encoding="utf-8")
        imported = any(
            isinstance(node, ast.ImportFrom)
            and node.module == f"tests.{SHARED_MODULE}"
            and any(alias.name == HELPER for alias in node.names)
            for node in ast.walk(ast.parse(source))
        )
        called = f"{HELPER}(" in source
        if not imported or not called:
            missing.append(
                f"{consumer}: imported={imported} called={called}"
            )
    assert not missing, (
        "these fixtures must IMPORT the shared quiesce and apply it:\n  "
        + "\n  ".join(missing)
    )
