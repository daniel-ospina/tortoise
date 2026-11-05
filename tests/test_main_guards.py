"""Coverage gap fillers for __main__ guards + edge cases."""
from __future__ import annotations  # noqa: I001

import ast
import os
import sys
import tempfile
import subprocess
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_m0_main():
    """Run m0.py main() directly with args."""
    from tortoise import m0

    d = tempfile.mkdtemp(prefix="tortoise_m0_")
    try:
        transcript = os.path.join(d, "test.txt")
        out_path = os.path.join(d, "graph.html")
        log_path = os.path.join(d, "events.jsonl")

        with open(transcript, "w") as f:
            f.write("Alice: This is a test because we need coverage.\n"
                    "Bob: But however we should verify everything.\n")

        m0.main(argv=[transcript, "--out", out_path, "--log", log_path])

        assert os.path.exists(out_path)
        with open(out_path) as f:
            html = f.read()
            assert "<svg" in html
        print("PASS test_m0_main")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def _guard_subprocess_budget() -> float:
    """The subprocess budget: the embedder's OWN declared cold-load bound + work.

    #3349: a fixed 120 s was SMALLER than the bound the product itself declares
    for a cold load (``EmbeddingModel._LOAD_TIMEOUT_S`` = 90 s) plus the ingest
    work that follows it, so on a loaded self-hosted runner the subprocess
    redded the shard with a bare ``TimeoutExpired`` while the code under test
    was fine — the test's own title says it hits the ``__main__`` guard, not a
    performance target. Deriving the budget instead of picking a second number
    means it cannot drift back below the bound it has to clear: raise
    ``_LOAD_TIMEOUT_S`` and this moves with it.

    ⛔ It is a CLASS attribute of ``EmbeddingModel``, NOT a module global of
    ``tortoise.embeddings`` (``tortoise/embeddings.py:552``; the loader reads
    ``self._LOAD_TIMEOUT_S``). A ``getattr(module, "_LOAD_TIMEOUT_S", 90.0)``
    derivation misses it and silently pins a SECOND hard-coded number — the
    defect #3349 names, restored. Pinned by
    ``test_the_guard_subprocess_budget_clears_the_declared_cold_load_bound``.
    """
    load_bound = 90.0  # the documented bound, used only if it cannot be read
    try:
        from tortoise.embeddings import EmbeddingModel
        load_bound = float(EmbeddingModel._LOAD_TIMEOUT_S)
    except Exception:  # pragma: no cover — a missing bound must not SHRINK it
        pass
    # The work allowance: the ingest itself, on a host slow enough that the
    # cold load alone approached its bound.
    return load_bound + 180.0


def test_the_guard_subprocess_budget_clears_the_declared_cold_load_bound():
    """#3349: the budget must be DERIVED from the product's cold-load bound.

    Two independent ways to re-open #3349, both pinned here: the budget falling
    back under the declared ``EmbeddingModel._LOAD_TIMEOUT_S`` (a second
    hard-coded number), or the derivation reading a module that does not carry
    the attribute at all — which silently yields the fallback and makes the
    "derives from" claim in the docstring false. The second is why the source of
    the constant is asserted and not just the sum.
    """
    from tortoise.embeddings import EmbeddingModel

    declared = float(EmbeddingModel._LOAD_TIMEOUT_S)
    budget = _guard_subprocess_budget()
    assert budget > declared, (
        f"a {budget}s subprocess budget cannot absorb a {declared}s cold load "
        "plus the ingest work (#3349)")
    assert budget > 120.0, (
        "the pre-fix 120 s budget is what redded the shard (#3349) — a "
        "derivation that lands at or below it is the same defect")
    assert hasattr(EmbeddingModel, "_LOAD_TIMEOUT_S"), (
        "the cold-load bound moved; re-point the derivation before the "
        "fallback silently pins a second number (#3349)")
    assert budget == declared + 180.0, (
        "the budget must BE the declared bound plus the work allowance, so "
        "raising the bound moves it (#3349)")


def _run_guard_subprocess(argv, cwd, timeout=None):
    """Run a __main__-guard subprocess; retry once on failure.

    The subprocess spawns a fresh redislite server for --db paths; under a
    loaded runner (CI fast job mid-suite) the spawn can exceed a tight
    timeout and the crash is otherwise invisible. Retry once, surface
    stderr on failure.

    Import bootstrap: the fresh interpreter's resolution of the checkout's
    tortoise is environment-fragile (CI observed a namespace-package shadow:
    'cannot import name RELATIVE_PATH_ERROR from tortoise.config (unknown
    location)' with tortoise.__file__=None). The bootstrap imports the
    package BY ABSOLUTE PATH from the test file's location and inserts the
    repo root on sys.path, so the checkout's code always wins regardless of
    editable-finder/user-site/namespace quirks (#493).
    """
    env = dict(os.environ)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = os.pathsep.join(
        [repo_root] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    # Rewrite `python -m tortoise.X ...` into a bootstrap that force-imports
    # the checkout's tortoise package by absolute path, then runs the module.
    module = argv[argv.index("-m") + 1] if "-m" in argv else None
    rest = argv[argv.index("-m") + 2:] if module else argv
    pkg_init = os.path.join(repo_root, "tortoise", "__init__.py")
    assert os.path.exists(pkg_init), f"checkout tortoise/__init__.py missing at {pkg_init}"
    code = (
        "import sys, os, importlib.util as _u\n"
        f"_root = {repo_root!r}\n"
        "sys.path.insert(0, _root)\n"
        f"_spec = _u.spec_from_file_location('tortoise', {pkg_init!r}, "
        "submodule_search_locations=[os.path.join(_root, 'tortoise')])\n"
        "_m = _u.module_from_spec(_spec)\n"
        "sys.modules['tortoise'] = _m\n"
        "_spec.loader.exec_module(_m)\n"
        "from runpy import run_module\n"
        f"sys.argv = [{module!r}] + {rest!r}\n"
        f"run_module({module!r}, run_name='__main__')\n"
    )
    full_argv = [sys.executable, "-c", code]
    last = None
    if timeout is None:
        timeout = _guard_subprocess_budget()
    for _ in range(2):
        proc = subprocess.run(full_argv, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout, env=env)
        if proc.returncode == 0:
            return proc
        last = proc
    raise AssertionError(
        f"guard subprocess failed (rc={last.returncode}):\n"
        f"stdout: {last.stdout[-500:]}\nstderr: {last.stderr[-500:]}")


def test_m0_main_guard():
    """Hit the __main__ guard by running m0.py as a script."""
    d = tempfile.mkdtemp(prefix="tortoise_m0g_")
    try:
        transcript = os.path.join(d, "test.txt")
        out_path = os.path.join(d, "graph.html")
        log_path = os.path.join(d, "events.jsonl")

        with open(transcript, "w") as f:
            f.write("Alice: This is a test because we need coverage.\n"
                    "Bob: But however we should verify everything.\n")

        _run_guard_subprocess(
            [sys.executable, "-m", "tortoise.m0", transcript,
             "--out", out_path, "--log", log_path],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert os.path.exists(out_path)
        print("PASS test_m0_main_guard")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_ingest_main():
    """Run ingest.py main() directly with mock models."""
    from tortoise import ingest

    d = tempfile.mkdtemp(prefix="tortoise_ingest_")
    try:
        transcript = os.path.join(d, "test.txt")
        out_path = os.path.join(d, "graph.html")
        log_path = os.path.join(d, "events.jsonl")
        db_path = os.path.join(d, "test.db")

        with open(transcript, "w") as f:
            f.write("Alice: This is a test because we need to verify.\n"
                    "Bob: But however the coverage needs improvement.\n")

        ingest.main(argv=[
            transcript,
            "--point-model", "mock:cheap",
            "--relation-model", "mock:reason",
            "--out", out_path,
            "--log", log_path,
            "--db", db_path,
        ])

        assert os.path.exists(out_path)
        print("PASS test_ingest_main")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_ingest_main_guard():
    """Hit the __main__ guard by running ingest.py as a script."""
    d = tempfile.mkdtemp(prefix="tortoise_ig_")
    try:
        transcript = os.path.join(d, "test.txt")
        out_path = os.path.join(d, "graph.html")
        log_path = os.path.join(d, "events.jsonl")
        db_path = os.path.join(d, "test.db")

        with open(transcript, "w") as f:
            f.write("Alice: This is a test because we need to verify.\n"
                    "Bob: But however the coverage needs improvement.\n")

        _run_guard_subprocess(
            [sys.executable, "-m", "tortoise.ingest", transcript,
             "--point-model", "mock:cheap",
             "--relation-model", "mock:reason",
             "--out", out_path, "--log", log_path, "--db", db_path],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )

        assert os.path.exists(out_path)
        print("PASS test_ingest_main_guard")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def _globals_discovery_guard(path):
    """Locate the file's call-time test-discovery runner and its ``__main__`` guard.

    Returns ``(runner_names, guard, tests_after)`` — ``guard`` is the top-level
    ``if __name__ == "__main__"`` node that calls one of those runners, and
    ``tests_after`` names every ``test_*`` function defined BELOW it. Returns
    ``None`` when the file has no such runner (a runner over an explicit list is
    unaffected: it does not depend on where the guard sits).
    """
    tree = ast.parse(path.read_text())
    # A runner that scans `globals()` resolves its test set AT CALL TIME, so a
    # definition added below the guard is invisible to it and is never executed.
    discovery = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "globals()" in ast.unparse(node)
    }
    if not discovery:
        return None
    for node in tree.body:
        if not isinstance(node, ast.If) or "__main__" not in ast.unparse(node.test):
            continue
        called = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        if not (called & discovery):
            continue
        tests_after = [
            inner.name
            for inner in tree.body
            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
            and inner.name.startswith("test_")
            and inner.lineno > node.end_lineno
        ]
        return sorted(discovery), node.lineno, tests_after
    return None


def test_a_globals_discovery_main_guard_is_last():
    """#10010 F3: a `globals()` runner's guard must be the file's LAST statement.

    These files offer a second execution mode — ``python3 tests/<file>.py`` —
    whose runner discovers tests from ``globals()`` at CALL TIME. A guard placed
    above a later ``def test_`` therefore runs NONE of those tests and still
    prints "all ... tests passed": a silently green standalone mode. That is
    what happened to the six ``#10296`` thread pins appended below
    ``tests/test_embeddings.py``'s guard, and the file's own docstring advertises
    the mode while the PR body cites its output as evidence.

    pytest is unaffected (it collects by name, not through the guard), so no
    other check can catch this: the sweep below is the only thing that keeps the
    second mode honest across the repo.
    """
    tests_dir = Path(__file__).resolve().parent
    violations: dict[str, list[str]] = {}
    seen = 0
    for path in sorted(tests_dir.glob("test_*.py")):
        try:
            found = _globals_discovery_guard(path)
        except SyntaxError:  # pragma: no cover — a broken file reds pytest anyway
            continue
        if found is None:
            continue
        seen += 1
        _, guard_line, tests_after = found
        if tests_after:
            violations[path.name] = [f"guard at :{guard_line}", *tests_after]

    # Non-vacuity: if the AST predicate stops recognising the pattern the sweep
    # would pass over every file and prove nothing, so require it to still see
    # the (many) globals()-discovery runners this repo has.
    assert seen >= 5, (
        f"the sweep recognised only {seen} globals()-discovery `__main__` runner(s) — "
        "the predicate is broken, so its clean result proves nothing")
    assert not violations, (
        "a `globals()` test runner's `if __name__ == '__main__'` guard is not the "
        "last statement, so `python3 tests/<file>.py` silently runs none of the "
        "tests defined below it (#10010 F3). Move the guard to EOF: "
        + "; ".join(f"{name}: {', '.join(where)}" for name, where in violations.items()))


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall gap-filler tests passed")


if __name__ == "__main__":
    _run_all()
