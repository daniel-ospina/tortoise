"""Client re-export surface + end-to-end wheel acceptance gate (#526, PR #1313).

(a) The `tortoise_client` shim must re-export exactly the canonical driver
    API from `tortoise.mcp_client` (available, call_tool, get_client,
    list_tools, status) — the client-first import surface. The re-exported
    names must BE the mcp_client objects (identity), not copies.
(b) The fixed `client/verify_client.sh` acceptance gate must PASS against a
    freshly built wheel, run from a NEUTRAL CWD (the repo tree must never
    shadow the installed wheel). Network + slow — marked `slow` (excluded
    from the fast suite) and skipped when a wheel build isn't feasible.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIR = REPO_ROOT / "client"

REEXPORTS = ("available", "call_tool", "get_client", "list_tools", "status")
ENGINE_ONLY_SYMBOLS = ("sdk", "projection", "ep", "FalkorDB")


def test_client_shim_reexports_match_mcp_client():
    """The shim re-exports the canonical driver defs — same objects, not copies."""
    import tortoise.mcp_client as mcp

    # Import the shim from client/ AFTER the engine package is cached, so
    # its `from tortoise.mcp_client import ...` binds the canonical driver.
    sys.path.insert(0, str(CLIENT_DIR))
    try:
        import tortoise_client as tc
    finally:
        sys.path.remove(str(CLIENT_DIR))

    assert str(Path(tc.__file__).resolve()).startswith(str(CLIENT_DIR.resolve()))
    assert "__version__" in tc.__all__  # explicit surface declaration
    for name in REEXPORTS:
        assert name in tc.__all__, f"{name} missing from tortoise_client.__all__"
        assert hasattr(tc, name), f"{name} missing from tortoise_client"
        assert getattr(tc, name) is getattr(mcp, name), (
            f"tortoise_client.{name} is not tortoise.mcp_client.{name}"
        )
    # Engine surface must NOT leak into the client-first namespace.
    for engine_only in ENGINE_ONLY_SYMBOLS:
        assert not hasattr(tc, engine_only), (
            f"engine symbol {engine_only} leaked into tortoise_client"
        )


@pytest.mark.slow
def test_verify_client_wheel_gate():
    """Build a fresh wheel and run client/verify_client.sh from a neutral CWD.

    The gate must pass for the RIGHT reasons: import checks resolve the
    installed wheel (neutral CWD + `python -I` + venv __file__ assertion),
    and the wheel-content whitelist rejects anything outside the client
    module set. Skipped when a wheel build isn't feasible in this env.
    """
    if not shutil.which("bash"):
        pytest.skip("bash not available")
    if not shutil.which("python3.12"):
        pytest.skip("python3.12 not available")

    env = dict(os.environ)
    shim_dir = None
    if not _python3_build_usable():
        # build_client.sh resolves `python3` from PATH — shim it to 3.12 so
        # the canonical build path is exercised on hosts whose default
        # python3 is missing/old/pip-less (e.g. uv venvs), and skip early
        # when even 3.12 is unusable.
        shim_dir = Path(tempfile.mkdtemp(prefix="tw-pyshim-"))
        (shim_dir / "python3").symlink_to(shutil.which("python3.12"))
        env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"

    out_dir = Path(tempfile.mkdtemp(prefix="tw-client-wheel-"))
    neutral = Path(tempfile.mkdtemp(prefix="tw-neutral-"))
    try:
        # Canonical build (client/build_client.sh stages the shared modules).
        res = subprocess.run(
            ["bash", str(CLIENT_DIR / "build_client.sh"), str(out_dir)],
            capture_output=True, text=True, timeout=600, env=env,
        )
        wheels = sorted(out_dir.glob("*.whl"))
        if res.returncode != 0 or not wheels:
            pytest.skip(f"wheel build not feasible in this env: {res.stderr[-500:]}")

        res = subprocess.run(
            ["bash", str(CLIENT_DIR / "verify_client.sh"), str(wheels[0])],
            capture_output=True, text=True, timeout=900,
            cwd=str(neutral),
        )
        assert res.returncode == 0, (
            f"verify_client.sh FAILED against {wheels[0].name}:\n"
            f"--- stdout ---\n{res.stdout}\n--- stderr ---\n{res.stderr}"
        )
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
        shutil.rmtree(neutral, ignore_errors=True)
        if shim_dir is not None:
            shutil.rmtree(shim_dir, ignore_errors=True)


def _python3_build_usable() -> bool:
    """True when the `python3` on PATH can build the client (>=3.12 + pip)."""
    exe = shutil.which("python3")
    if not exe:
        return False
    try:
        r = subprocess.run(
            [exe, "-c", "import sys; print(f'{sys.version_info.major} {sys.version_info.minor}')"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False
        major, minor = (int(x) for x in r.stdout.split())
        if (major, minor) < (3, 12):
            return False
        r = subprocess.run(
            [exe, "-m", "pip", "--version"], capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0
    except (ValueError, OSError, subprocess.SubprocessError):
        return False
