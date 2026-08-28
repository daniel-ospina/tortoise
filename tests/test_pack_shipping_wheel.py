"""Slow wheel-install smoke test (#1929, epic #1891 slice 1).

Builds the REAL wheel + sdist, rebuilds the wheel FROM the sdist (guards
the MANIFEST.in stub include — a wheel rebuilt from an sdist that lacks
the stub ships ZERO packs, the G1 defect resurfacing on the sdist install
path), installs the sdist-rebuilt wheel into a clean venv, and asserts the
packaged catalog loads the full starter set.

Builder detection chain (skips if none available):
  1. ``python -m build`` (build module present)
  2. ``uv run --with build python -m build`` (uv present)
  3. skip — the CI publish smoke is the authoritative wheel gate.

Registered under ``slow_files:`` in config/ci-surfaces.yml — runs in the
test-slow CI job (90m budget), never in the fast gate.
"""
from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.slow


def _build_backend(dist_dir: Path) -> list[str]:
    """Pick the first available builder invocation or raise for skip.

    Probes `python -m build --version` rather than importlib: a stray
    ``build/`` directory in CWD (a leftover build artifact) imports as a
    namespace package and would make find_spec() lie."""
    import shutil
    if subprocess.run([sys.executable, "-m", "build", "--version"],
                      capture_output=True).returncode == 0:
        return [sys.executable, "-m", "build"]
    if shutil.which("uv"):
        return ["uv", "run", "--with", "build", "python", "-m", "build"]
    pytest.skip("no wheel builder available (build module or uv) — "
                "CI publish smoke is the authoritative gate")


def _importable(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def _which(bin: str) -> str | None:
    import shutil
    return shutil.which(bin)


def _wheel_manifest_yamls(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as z:
        return [n for n in z.namelist() if n.startswith("tortoise/packs/") and n.endswith("manifest.yaml")]


def _sdist_has_packs(sdist: Path) -> bool:
    with tarfile.open(sdist) as t:
        names = t.getnames()
    return any("/packs/" in n and n.endswith("manifest.yaml") for n in names)


@pytest.fixture(scope="module")
def built_dist(tmp_path_factory):
    """Build wheel + sdist, rebuild wheel from the sdist, return the rebuilt wheel."""
    tmp = tmp_path_factory.mktemp("wheelbuild")
    dist_dir = tmp / "dist"
    dist_dir.mkdir()
    cmd = _build_backend(dist_dir)
    subprocess.run([*cmd, "--sdist", "--wheel", "-o", str(dist_dir), "."],
                   cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    assert wheels and sdists, f"build produced no artifacts in {dist_dir}"
    wheel, sdist = wheels[0], sdists[0]

    # The P1 gate: a wheel rebuilt from the sdist must still ship packs.
    assert _wheel_manifest_yamls(wheel), f"primary wheel ships no packs: {wheel}"
    assert _sdist_has_packs(sdist), f"sdist ships no packs: {sdist}"
    rebuild_dir = tmp / "rebuild"
    rebuild_dir.mkdir()
    subprocess.run([*cmd, "--wheel", "-o", str(rebuild_dir), str(sdist)],
                   cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    rebuilt = sorted(rebuild_dir.glob("*.whl"))
    assert rebuilt, "sdist rebuild produced no wheel"
    assert _wheel_manifest_yamls(rebuilt[0]), (
        f"sdist-rebuilt wheel ships no packs — MANIFEST.in stub include missing: {rebuilt[0]}")
    return rebuilt[0]


class TestWheelInstall:
    """Clean-venv install of the built (sdist-rebuilt) wheel loads the starter set."""

    def test_wheel_install_loads_starter_set(self, built_dist, tmp_path):
        venv = tmp_path / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        pip = venv / "bin" / "pip"
        # cwd=tmp_path: `python -c` puts CWD on sys.path[0], so running from
        # the repo root would shadow the wheel-installed package with the
        # source tree (editable) — the test must resolve the WHEEL's copy.
        subprocess.run([str(pip), "install", "--quiet", str(built_dist)],
                       check=True, capture_output=True, text=True, timeout=600,
                       cwd=tmp_path)
        python = venv / "bin" / "python"
        code = """
import sys
from pathlib import Path
import tortoise
from tortoise.pack_registry import PackRegistry, default_packs_dir
from tortoise.pack_state import DEFAULT_STARTER_PACKS

packaged = Path(tortoise.__file__).resolve().parent / "packs"
assert packaged.is_dir(), f"packaged dir missing: {packaged}"
reg = PackRegistry(packaged)
n = reg.load_all()
bound = len(DEFAULT_STARTER_PACKS)
assert n >= bound, f"wheel ships {n} packs < starter {bound}"
assert set(reg.packs) >= set(DEFAULT_STARTER_PACKS), (
    f"wrong set: {sorted(reg.packs)} vs starter {sorted(DEFAULT_STARTER_PACKS)}")
assert default_packs_dir() == packaged, f"resolver picked {default_packs_dir()}"

# Real consumer path (the G1 class): domain_loader must see the catalog.
from tortoise.domain_loader import _get_registry
r = _get_registry()
assert r is not None and len(r.packs) >= bound, "domain_loader empty on wheel install"

# Sample transcript ships (package-data) where _cmd_demo resolves it.
t = Path(tortoise.__file__).resolve().parent.parent / "tests" / "sample_transcript.txt"
assert t.exists(), f"sample transcript missing: {t}"
print(f"wheel smoke OK: {n} >= {bound}; transcript at {t}")
"""
        res = subprocess.run([str(python), "-c", code], capture_output=True,
                             text=True, timeout=600, cwd=tmp_path)
        assert res.returncode == 0, f"in-venv assertion failed:\n{res.stdout}\n{res.stderr}"
        assert "wheel smoke OK" in res.stdout
