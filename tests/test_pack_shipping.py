"""Pack catalog shipping + default resolution tests (#1929, epic #1891 slice 1).

Covers the packaged-default resolution leg introduced by #1929:
  - ``default_packs_dir()`` resolver: packaged leg (wheel) vs repo root (dev)
  - dev-path regression: repo-root ``packs/`` still resolves on editable/dev
    installs (test-design #1898 surface 4)
  - packaged-layout integration: a wheel-like ``tortoise/packs/`` layout loads
    the full starter set (surface 1, fast non-building variant)

The real wheel build → clean-venv-install test lives in
``test_pack_shipping_wheel.py`` (slow, CI smoke surface).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tortoise.domain_loader as domain_loader
import tortoise.pack_registry as pack_registry
from tortoise.pack_registry import PackRegistry, default_packs_dir
from tortoise.pack_state import DEFAULT_STARTER_PACKS

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_PACKS_DIR = REPO_ROOT / "packs"

# A minimal valid manifest (namespace + name suffice for load_all).
_MINIMAL_MANIFEST = """namespace: {ns}
name: {ns}
tier: free
"""


def _make_wheel_layout(root: Path, namespaces: list[str]) -> Path:
    """Build a wheel-install-like tree: root/tortoise/packs/<ns>/manifest.yaml."""
    packaged = root / "tortoise" / "packs"
    for ns in namespaces:
        d = packaged / ns
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.yaml").write_text(_MINIMAL_MANIFEST.format(ns=ns))
    return packaged


# ── Resolver unit tests ───────────────────────────────────────────────────

class TestDefaultPacksDir:
    """`default_packs_dir()` selects packaged vs repo-root leg (#1929 D2)."""

    def test_packaged_leg_wins_when_manifests_present(self, tmp_path, monkeypatch):
        """Wheel install: <package>/packs has manifests → packaged leg."""
        _make_wheel_layout(tmp_path, ["dev", "marketing"])
        monkeypatch.setattr(pack_registry, "__file__", str(tmp_path / "tortoise" / "x.py"))
        assert default_packs_dir() == (tmp_path / "tortoise" / "packs")

    def test_stub_only_dir_falls_through_to_repo_root(self, tmp_path, monkeypatch):
        """Dev/editable: <package>/packs is the discovery stub (no yamls) →
        repo-root leg wins (packaged leg must not shadow dev)."""
        stub = tmp_path / "tortoise" / "packs"
        stub.mkdir(parents=True)
        (stub / "__init__.py").write_text("")
        root_packs = tmp_path / "packs"
        root_packs.mkdir()
        (root_packs / "dev").mkdir()
        (root_packs / "dev" / "manifest.yaml").write_text(_MINIMAL_MANIFEST.format(ns="dev"))
        monkeypatch.setattr(pack_registry, "__file__", str(tmp_path / "tortoise" / "x.py"))
        assert default_packs_dir() == root_packs

    def test_missing_both_falls_to_repo_root(self, tmp_path, monkeypatch):
        """Neither leg has manifests → repo root (the pre-#1929 path)."""
        monkeypatch.setattr(pack_registry, "__file__", str(tmp_path / "tortoise" / "x.py"))
        assert default_packs_dir() == (tmp_path / "packs")

    def test_repo_root_is_packaged_sibling(self):
        """In this source tree the repo-root leg is the canonical catalog."""
        assert default_packs_dir() == REPO_PACKS_DIR


# ── Dev-path regression (surface 4) ───────────────────────────────────────

class TestDevPathRegression:
    """Repo-root packs still resolve on editable/dev installs (no regression)."""

    def test_get_registry_resolves_repo_root_starter_set(self):
        """`_get_registry()` (no override) must load ≥ the starter set from the
        repo-root catalog — the dev/editable leg the whole pipeline relies on."""
        domain_loader._registry = None
        try:
            reg = domain_loader._get_registry()
        finally:
            domain_loader._registry = None
        assert reg is not None
        assert set(reg.packs) >= set(DEFAULT_STARTER_PACKS), (
            f"dev registry {sorted(reg.packs)} misses starter set "
            f"{sorted(DEFAULT_STARTER_PACKS)}"
        )

    def test_registry_content_matches_repo_catalog(self):
        """PackRegistry on the repo catalog loads every starter namespace."""
        reg = PackRegistry(REPO_PACKS_DIR)
        reg.load_all()
        assert set(reg.packs) >= set(DEFAULT_STARTER_PACKS)


# ── Packaged-layout integration (surface 1, fast variant) ─────────────────

class TestPackagedLayout:
    """A wheel-like layout (tortoise/packs/) loads the full starter set."""

    def test_packaged_layout_loads_full_starter_set(self, tmp_path):
        """Copy the real catalog into a wheel-like layout and load it — the
        content contract the wheel smoke asserts (namespace identity, not
        just count)."""
        # Map catalog dirs → their DECLARED namespaces (the pm pack lives in
        # packs/project-management/ but declares namespace `pm`).
        declared: dict[str, Path] = {}
        for d in REPO_PACKS_DIR.iterdir():
            m = d / "manifest.yaml"
            if m.is_file() and not d.name.startswith(("_", ".")):
                text = m.read_text()
                for line in text.splitlines():
                    if line.startswith("namespace:"):
                        declared[line.split(":", 1)[1].strip()] = m
                        break
        missing = [ns for ns in DEFAULT_STARTER_PACKS if ns not in declared]
        assert not missing, f"catalog missing starter namespaces: {missing}"
        packaged = _make_wheel_layout(tmp_path, list(DEFAULT_STARTER_PACKS))
        for ns in DEFAULT_STARTER_PACKS:
            (packaged / ns / "manifest.yaml").write_text(declared[ns].read_text())
        reg = PackRegistry(packaged)
        n = reg.load_all()
        assert set(reg.packs) >= set(DEFAULT_STARTER_PACKS)
        assert n >= len(DEFAULT_STARTER_PACKS)
        assert not reg.errors
