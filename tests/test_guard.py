"""Import-time loud-fail guard tests (plan Task 8).

The guard wraps redislite.falkordb_client.FalkorDB by subclassing it and
re-exporting as tortoise.FalkorDB. It is BEST-EFFORT: only code importing
tortoise's re-export (or importing redislite AFTER `import tortoise`) is
guarded. Direct redislite imports before tortoise are documented bypasses
covered by pre-commit grep (Task 13).
"""
from __future__ import annotations

import pytest

pytest.importorskip("redislite")


def test_tortoise_reexport_raises_on_relative():
    """`import tortoise; from tortoise import FalkorDB; FalkorDB('relative.db')`
    raises RuntimeError (guards code importing tortoise's re-export)."""
    import tortoise
    from tortoise import FalkorDB
    with pytest.raises(RuntimeError):
        FalkorDB("relative.db")


def test_absolute_path_passes_through_wrapper():
    """Absolute path passes through the guarded re-export."""
    import tempfile
    import os
    from tortoise import FalkorDB
    path = os.path.join(tempfile.mkdtemp(), "guard-abs.db")
    db = FalkorDB(path)
    db.close()
    for suffix in (".db", ".db.settings"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def test_no_arg_passes_through_wrapper():
    """No-arg FalkorDB() passes through (no path to validate)."""
    from tortoise import FalkorDB
    db = FalkorDB()
    db.close()


def test_projection_still_works_with_guard_active():
    """FalkorProjection('/tmp/test.db') still works — the wrapper does NOT
    break the projection module (Task 7's own hard-reject handles paths)."""
    import tempfile
    import os
    from tortoise.projection import FalkorProjection
    path = os.path.join(tempfile.mkdtemp(), "guard-proj.db")
    proj = FalkorProjection(path)
    proj.close()
    for suffix in (".db", ".db.settings"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def test_direct_redislite_import_before_tortoise_bypasses():
    """Importing redislite.falkordb_client.FalkorDB BEFORE tortoise yields the
    unwrapped original — documented bypass (pre-commit grep covers it)."""
    import redislite.falkordb_client as rfc
    OriginalFalkorDB = rfc.FalkorDB
    import tortoise  # guard activates, but local ref is already unwrapped
    # The original does NOT raise on relative paths (it spawns a server) —
    # but we must NOT create a server in a test. Instead assert the class
    # identity: the pre-import reference is NOT tortoise's guarded subclass.
    from tortoise import FalkorDB as GuardedFalkorDB
    assert OriginalFalkorDB is not GuardedFalkorDB


def test_redislite_redis_direct_import_bypasses():
    """redislite.Redis() (parent class) also bypasses the wrapper — locks in
    the documented limitation (pre-commit grep covers it). Must NOT raise."""
    import redislite
    import tortoise  # noqa: F401 — guard active
    # Just verify import works and Redis is importable; do NOT instantiate
    # (it would spawn a server). The guard must not have patched redislite.
    assert hasattr(redislite, "Redis")


def test_guard_is_subclass_not_monkeypatch():
    """The guard subclasses FalkorDB and re-exports; it does NOT monkeypatch
    the redislite module globally (non-tortoise users unaffected)."""
    import redislite.falkordb_client as rfc
    import tortoise
    from tortoise import FalkorDB as Guarded
    assert issubclass(Guarded, rfc.FalkorDB)
    # redislite module untouched
    assert rfc.FalkorDB is not Guarded


def test_placeholder_branch_raises_clear_import_error():
    """Forces the dep-missing branch of the import guard (issue #716).

    In a dep-installed CI environment the `except ModuleNotFoundError` branch
    of tortoise/__init__.py is dead code — reverting the guard to a bare
    import would pass every test. Hide redislite in sys.modules (None raises
    ImportError on import) and reload tortoise: the placeholder FalkorDB must
    raise the clear ImportError that `_cmd_init` catches to print install
    guidance. Reload once more after the patch to restore the guarded
    subclass, so no test pollution leaks into the rest of the suite.
    """
    import importlib
    import sys
    from unittest import mock

    import tortoise
    import redislite.falkordb_client as rfc

    # Sanity: in this (dep-installed) env the real guarded subclass is loaded,
    # not the placeholder — proving the placeholder is dead code here.
    assert issubclass(tortoise.FalkorDB, rfc.FalkorDB)

    try:
        with mock.patch.dict(
            sys.modules,
            {
                # None in sys.modules makes import raise, forcing the guard's
                # dep-missing branch. The child module is cached from earlier
                # tests, so it (not just the parent) must be hidden too.
                "redislite": None,
                "redislite.falkordb_client": None,
                "falkordblite": None,
            },
        ):
            importlib.reload(tortoise)
            with pytest.raises(ImportError, match="falkordblite is not installed"):
                tortoise.FalkorDB()
    finally:
        # sys.modules restored by patch.dict on exit; reload to rebuild the
        # real guarded subclass before any other test imports tortoise.
        importlib.reload(tortoise)

    # Restored: the guarded subclass is back and constructible.
    assert issubclass(tortoise.FalkorDB, rfc.FalkorDB)
