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
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIR = REPO_ROOT / "client"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

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


# --- wheel shared-module enumeration drift pins (#3805 / PR #4044 review) ----
#
# The client wheel's shared-module set has ONE source of truth: the
# `cp "$REPO_ROOT/tortoise/<mod>.py"` lines in client/build_client.sh — the
# copies that literally ship. client/shared_modules.sh derives the set from
# them for its consumers (the ci.yml `client` path gate, the ci.yml wheel
# whitelist, verify_client.sh Gate 0).
#
# Before PR #4044 the path gate carried its own literal list of three modules.
# Staging tortoise/status_vocabulary.py into the wheel therefore left the
# gate computing `client=false`, SILENTLY skipping client-build for the very
# module both client surfaces import — and client-build's acceptance gate is
# the only thing proving the thin-client boundary holds. These tests pin the
# derivation so a literal list cannot re-grow.

_BUILD_CP = re.compile(r'^cp\s+"\$REPO_ROOT/tortoise/([A-Za-z0-9_]+\.py)"', re.M)
_GATE_START = "cat > /tmp/ci-gates.py <<'PY'\n"
_WHITELIST_START = "      - name: Wheel content check — whitelist (thin driver only)\n"
_WHITELIST_END = "      - name: Acceptance gate (clean venv, client only)"


def _staged_shared_modules() -> list[str]:
    """The canonical modules client/build_client.sh copies into the wheel."""
    script = (CLIENT_DIR / "build_client.sh").read_text(encoding="utf-8")
    found = sorted(set(_BUILD_CP.findall(script)))
    assert found, "no `cp \"$REPO_ROOT/tortoise/*.py\"` copies in client/build_client.sh"
    return found


def _derived_shared_modules() -> list[str]:
    """What client/shared_modules.sh — the single extractor — derives."""
    res = subprocess.run(
        ["bash", str(CLIENT_DIR / "shared_modules.sh")],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60,
    )
    assert res.returncode == 0, f"client/shared_modules.sh failed: {res.stderr}"
    return res.stdout.split()


def _ci_gate_source() -> str:
    """The Python per-surface path gate embedded in ci.yml's `changes` job."""
    yml = CI_YML.read_text(encoding="utf-8")
    start = yml.index(_GATE_START) + len(_GATE_START)
    end = yml.index("\n          PY\n", start)
    return textwrap.dedent(yml[start:end])


def _run_ci_gate(changed: list[str]) -> dict[str, bool]:
    """Execute the extracted gate over a changed-file set; return its outputs."""
    with tempfile.TemporaryDirectory(prefix="tw-ci-gate-") as td:
        script = Path(td) / "ci-gates.py"
        script.write_text(_ci_gate_source(), encoding="utf-8")
        out = Path(td) / "out.txt"
        res = subprocess.run(
            [sys.executable, str(script), str(out)],
            input="\n".join(changed), capture_output=True, text=True,
            cwd=str(REPO_ROOT), timeout=120,
        )
        assert res.returncode == 0, f"ci path gate failed: {res.stdout}\n{res.stderr}"
        written = out.read_text(encoding="utf-8")
    return {
        k: v == "true"
        for k, v in (ln.strip().split("=", 1) for ln in written.splitlines() if "=" in ln)
    }


def _ci_client_gate_entry() -> str:
    """The `"client": any_file(...)` entry of the extracted path gate."""
    gate = _ci_gate_source()
    start = gate.index('"client": any_file(')
    end = gate.index("\n    ),\n", start)
    return gate[start:end]


def test_client_shared_modules_extractor_matches_build_script():
    """The extractor and the build script's copy list are the same set."""
    assert _derived_shared_modules() == _staged_shared_modules()


def test_shared_modules_extractor_fails_closed():
    """An underivable set exits non-zero — it must never print nothing.

    A silent empty set is how a "derived" gate could skip the client leg
    while looking like it had simply nothing to do.
    """
    with tempfile.TemporaryDirectory(prefix="tw-shared-") as td:
        tmp = Path(td)
        shutil.copy(CLIENT_DIR / "shared_modules.sh", tmp / "shared_modules.sh")
        cmd = ["bash", str(tmp / "shared_modules.sh")]

        # (a) no build script at all
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert res.returncode != 0 and not res.stdout.strip()
        assert "not found" in res.stderr

        # (b) a build script whose copies are client SHIMS, not canonical modules
        (tmp / "build_client.sh").write_text(
            'cp "$SCRIPT_DIR/tortoise/__init__.py" "$STAGE/tortoise/__init__.py"\n',
            encoding="utf-8",
        )
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert res.returncode != 0 and not res.stdout.strip()
        assert "no canonical shared-module copies" in res.stderr


@pytest.mark.parametrize("module", _staged_shared_modules())
def test_client_gate_fires_for_every_staged_module(module: str):
    """PR #4044 review: touching ONE staged module alone must run client-build.

    RED before the fix for `status_vocabulary.py`: the gate's literal list
    named only mcp_client/config/exceptions, so the single-file diff computed
    `client=false` and the wheel build + verify_client.sh acceptance gate were
    skipped for a module the wheel ships.
    """
    assert _run_ci_gate([f"tortoise/{module}"])["client"] is True, (
        f"tortoise/{module} is staged into the client wheel but does not trip "
        f"the `client` path gate — client-build would be silently skipped"
    )


def test_client_gate_ignores_a_non_staged_module():
    """Negative control — the gate is not trivially true for every path."""
    assert _run_ci_gate(["tortoise/sdk.py"])["client"] is False


def test_client_gate_and_allowlists_derive_their_module_set():
    """No client consumer may re-grow a hand-maintained literal list (PR #4044)."""
    gate = _ci_gate_source()
    entry = _ci_client_gate_entry()
    assert "client/shared_modules.sh" in gate, (
        "the ci.yml gate must derive the wheel's shared-module set "
        "(client/shared_modules.sh)"
    )
    assert "SHARED" in entry, (
        "the ci.yml `client` gate entry must use the derived SHARED set"
    )
    for module in _staged_shared_modules():
        assert f"tortoise/{module}" not in entry, (
            f"the ci.yml `client` gate hard-codes tortoise/{module} — derive the "
            f"set instead so a newly staged module cannot be missed"
        )

    yml = CI_YML.read_text(encoding="utf-8")
    whitelist = yml[
        yml.index(_WHITELIST_START):yml.index(_WHITELIST_END, yml.index(_WHITELIST_START))
    ]
    assert "client/shared_modules.sh" in whitelist, (
        "the ci.yml wheel whitelist must derive its module set"
    )
    for module in _staged_shared_modules():
        assert module not in whitelist, (
            f"the ci.yml wheel whitelist hard-codes {module} — derive it instead"
        )

    verify = (CLIENT_DIR / "verify_client.sh").read_text(encoding="utf-8")
    assert "shared_modules.sh" in verify, (
        "verify_client.sh Gate 0 must derive its allowlist"
    )
    for module in _staged_shared_modules():
        assert module not in verify, (
            f"verify_client.sh Gate 0 hard-codes {module} — derive the allowlist instead"
        )
