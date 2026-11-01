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


def _run_guard_subprocess(argv, cwd, timeout=120):
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
