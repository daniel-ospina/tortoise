"""Coverage gap fillers for __main__ guards + edge cases."""
from __future__ import annotations

import os
import sys
import tempfile
import subprocess

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


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall gap-filler tests passed")


if __name__ == "__main__":
    _run_all()
