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


def _run_guard_subprocess(argv, cwd, timeout=60):
    """Run a __main__-guard subprocess; retry once on failure.

    The subprocess spawns a fresh redislite server for --db paths; under a
    loaded runner (CI fast job mid-suite, concurrent suites locally) the
    spawn can exceed a tight timeout and the crash is otherwise invisible
    (the tests only asserted the output file). Retry once, surface stderr
    on failure. The env pins the repo root on PYTHONPATH (the editable
    install's meta-path finder + runner user-site can otherwise shadow the
    checkout's tortoise in the fresh interpreter — CI: ImportError
    RELATIVE_PATH_ERROR from 'tortoise.config' (unknown location), #493).
    """
    env = dict(os.environ)
    repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    env["PYTHONPATH"] = os.pathsep.join(
        [repo_root] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    last = None
    for _ in range(2):
        proc = subprocess.run(argv, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout, env=env)
        if proc.returncode == 0:
            return proc
        last = proc
    diag = ""
    try:
        d = subprocess.run(
            [sys.executable, "-c", (
                "import sys, os; "
                "print('PYTHONPATH=', sys.path[:5]); "
                "import tortoise; "
                "print('tortoise.file=', tortoise.__file__); "
                "print('tortoise.path=', list(tortoise.__path__)); "
                "p=os.path.join(os.path.dirname(tortoise.__file__),'config.py'); "
                "print('config.py exists=', os.path.exists(p)); "
                "import importlib.util as u; "
                "s=u.find_spec('tortoise.config'); "
                "print('config.spec.origin=', s.origin if s else None, 'loc=', s.submodule_search_locations if s else None)"
            )],
            cwd=cwd, capture_output=True, text=True, timeout=30, env=env,
        )
        diag = f"\nDIAG (rc={d.returncode}): {d.stdout[-700:]} {d.stderr[-300:]}"
    except Exception as e:
        diag = f"\nDIAG failed: {e}"
    raise AssertionError(
        f"guard subprocess failed (rc={last.returncode}):\n"
        f"stdout: {last.stdout[-500:]}\nstderr: {last.stderr[-500:]}{diag}")


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
            cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."),
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
            cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."),
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
