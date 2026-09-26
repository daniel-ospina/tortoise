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
    import tortoise  # noqa: F401
    from tortoise import FalkorDB
    with pytest.raises(RuntimeError):
        FalkorDB("relative.db")


def test_absolute_path_passes_through_wrapper():
    """Absolute path passes through the guarded re-export."""
    import tempfile  # noqa: I001
    import os
    from tortoise import FalkorDB
    path = os.path.join(tempfile.mkdtemp(), "guard-abs.db")
    db = FalkorDB(path)
    db.close()
    for suffix in (".db", ".db.settings"):
        try:  # noqa: SIM105
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
    import tempfile  # noqa: I001
    import os
    from tortoise.projection import FalkorProjection
    path = os.path.join(tempfile.mkdtemp(), "guard-proj.db")
    proj = FalkorProjection(path)
    proj.close()
    for suffix in (".db", ".db.settings"):
        try:  # noqa: SIM105
            os.remove(path + suffix)
        except OSError:
            pass


def test_direct_redislite_import_before_tortoise_bypasses():
    """Importing redislite.falkordb_client.FalkorDB BEFORE tortoise yields the
    unwrapped original — documented bypass (pre-commit grep covers it)."""
    import redislite.falkordb_client as rfc
    OriginalFalkorDB = rfc.FalkorDB
    import tortoise  # guard activates, but local ref is already unwrapped  # noqa: F401, I001
    # The original does NOT raise on relative paths (it spawns a server) —
    # but we must NOT create a server in a test. Instead assert the class
    # identity: the pre-import reference is NOT tortoise's guarded subclass.
    from tortoise import FalkorDB as GuardedFalkorDB
    assert OriginalFalkorDB is not GuardedFalkorDB


def test_redislite_redis_direct_import_bypasses():
    """redislite.Redis() (parent class) also bypasses the wrapper — locks in
    the documented limitation (pre-commit grep covers it). Must NOT raise."""
    import redislite  # noqa: I001
    import tortoise  # noqa: F401 — guard active
    # Just verify import works and Redis is importable; do NOT instantiate
    # (it would spawn a server). The guard must not have patched redislite.
    assert hasattr(redislite, "Redis")


def test_guard_is_subclass_not_monkeypatch():
    """The guard subclasses FalkorDB and re-exports; it does NOT monkeypatch
    the redislite module globally (non-tortoise users unaffected)."""
    import redislite.falkordb_client as rfc  # noqa: I001
    import tortoise  # noqa: F401
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
    import importlib  # noqa: I001
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


def test_tilde_path_expands_and_boots(monkeypatch, tmp_path):
    """#2204 review (round 1): a `~`-prefixed path must be expanduser'd
    BEFORE redislite sees it — redislite derives its config ``dir`` verbatim
    from os.path.dirname(path) and never expands ``~`` itself, so an
    unexpanded tilde dies with the raw FATAL CONFIG FILE ERROR at config
    load. Constructing with '~<sub>/<missing-dir>/tortoise.db' under a
    monkeypatched HOME must succeed, create the expanded data dir + DB, and
    open the store at the EXPANDED path (proving the args tuple rebuild
    forwarded the expanded path to super().__init__)."""
    import os  # noqa: I001
    from tortoise import FalkorDB
    home = str(tmp_path / "home")
    monkeypatch.setenv("HOME", home)
    rel = "~/fresh-dir/tortoise.db"  # parent dir intentionally missing
    db = FalkorDB(rel)
    try:
        expanded = os.path.join(home, "fresh-dir")
        assert os.path.isdir(expanded)  # data dir created before config read
    finally:
        db.close()


def test_tilde_unresolvable_user_rejected_as_relative():
    """#2204 review (round 2): expanduser leaves '~<no-such-user>/...'
    unchanged (still non-absolute) — the relative-path RuntimeError must
    fire instead of creating a literal '~<user>' tree under the CWD
    (Category-3 per-CWD server hazard)."""
    from tortoise import FalkorDB
    with pytest.raises(RuntimeError):
        FalkorDB("~definitely-no-such-user-xyz/tortoise.db")


def test_memory_branch_forwards_original_args():
    """#2204 review (round 2): ':memory:' must bypass the expansion/makedirs
    branch entirely and forward the ORIGINAL args (no dir creation)."""
    from tortoise import FalkorDB
    db = FalkorDB(":memory:")
    db.close()


def _run_in_subprocess(code: str) -> str:
    """Run `code` in a fresh interpreter rooted at this checkout.

    #5386: the assertions below are about what `import tortoise` pulls in, and
    `sys.modules` is process-global — by the time this module's tests run, the
    session has already imported redislite (and so has every other test file).
    Only a fresh interpreter can answer the question.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    # The venv's editable install points at whatever worktree last ran
    # `pip install -e .`; put THIS checkout first so the subprocess imports the
    # code under test rather than that one.
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root, env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"subprocess failed:\n{proc.stderr}"
    return proc.stdout.strip()


def test_import_tortoise_does_not_import_redislite():
    """#5386: `import tortoise` must not pay redislite's import cost.

    `tortoise.FalkorDB` is built lazily on first access, and the embedded
    lifecycle's redislite patches are triggered by redislite's OWN import — so
    no part of `import tortoise` needs to import redislite.
    """
    assert _run_in_subprocess(
        "import sys, tortoise; print('redislite' in sys.modules)") == "False"


def test_lazy_falkordb_returns_the_same_guarded_subclass():
    """#5386: deferral changes WHEN redislite loads, never WHICH class
    `tortoise.FalkorDB` is — it is still the guarded subclass (not a
    monkeypatch of redislite) and still rejects a relative path."""
    import redislite.falkordb_client as rfc  # noqa: I001
    from tortoise import FalkorDB
    assert issubclass(FalkorDB, rfc.FalkorDB)
    assert FalkorDB is not rfc.FalkorDB
    with pytest.raises(RuntimeError):
        FalkorDB("relative.db")


def test_redislite_import_alone_installs_the_lifecycle_guards():
    """#4487/#5386: the lifecycle patches must be installed by redislite's OWN
    import — not by access to `tortoise.FalkorDB` — so a RAW redislite
    construction that never reaches the guarded class is still instrumented."""
    assert _run_in_subprocess(
        "import tortoise, redislite.client as c;"
        "print(getattr(c.RedisMixin, '_tortoise_owner_record_patch', False),"
        " getattr(c.RedisMixin, '_tortoise_partial_init_guard', False),"
        " getattr(c.RedisMixin, '_tortoise_dead_socket_guard', False))"
    ) == "True True True"
