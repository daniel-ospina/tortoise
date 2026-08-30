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

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tortoise.domain_loader as domain_loader
import tortoise.pack_registry as pack_registry
from tortoise.pack_registry import PackRegistry, default_packs_dir, env_packs_dir
from tortoise.pack_state import DEFAULT_STARTER_PACKS

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_PACKS_DIR = REPO_ROOT / "packs"

# A minimal valid manifest (namespace + name suffice for load_all).
_MINIMAL_MANIFEST = """namespace: {ns}
name: {ns}
tier: free
"""

# A manifest violating the camelCase kind rule (R-16 isolation trigger).
_MALFORMED_MANIFEST = """namespace: broken
name: broken
tier: free
ontology:
  objectKinds:
    - BadCase
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


# ── TORTOISE_PACKS_DIR env leg (#1930, epic #1891 WF-2) ───────────────────

class TestPacksDirEnvLeg:
    """`env_packs_dir()` + the env leg in `default_packs_dir()` (#1930).

    Every fallback trigger is boundary-tested (test-design #1898 surface 3):
    env set+valid / missing / not-a-dir / empty-dir / `_template`-only /
    blank / unset. Each test uses a unique tmp_path so the warn-once sentinel
    never suppresses a sibling test's expected warning.
    """

    def test_env_valid_dir_wins_over_packaged(self, tmp_path, monkeypatch):
        """Set+valid: the env dir wins over the packaged leg."""
        env_dir = tmp_path / "custom-packs"
        (env_dir / "tenant-ops").mkdir(parents=True)
        (env_dir / "tenant-ops" / "manifest.yaml").write_text(
            _MINIMAL_MANIFEST.format(ns="tenant-ops"))
        _make_wheel_layout(tmp_path / "wheel", ["dev", "marketing"])
        monkeypatch.setattr(
            pack_registry, "__file__", str(tmp_path / "wheel" / "tortoise" / "x.py"))
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        assert default_packs_dir() == env_dir

    def test_env_missing_dir_warns_and_falls_back(self, tmp_path, monkeypatch, caplog):
        """Missing dir → warn + packaged leg (never silent)."""
        _make_wheel_layout(tmp_path / "wheel", ["dev", "marketing"])
        monkeypatch.setattr(
            pack_registry, "__file__", str(tmp_path / "wheel" / "tortoise" / "x.py"))
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(tmp_path / "does-not-exist"))
        with caplog.at_level(logging.WARNING):
            assert default_packs_dir() == (tmp_path / "wheel" / "tortoise" / "packs")
        assert "TORTOISE_PACKS_DIR" in caplog.text
        assert "does not exist" in caplog.text

    def test_env_not_a_dir_warns_and_falls_back(self, tmp_path, monkeypatch, caplog):
        """Env points at a FILE → warn + packaged leg."""
        _make_wheel_layout(tmp_path / "wheel", ["dev", "marketing"])
        monkeypatch.setattr(
            pack_registry, "__file__", str(tmp_path / "wheel" / "tortoise" / "x.py"))
        env_file = tmp_path / "not-a-dir"
        env_file.write_text("i am a file")
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_file))
        with caplog.at_level(logging.WARNING):
            assert default_packs_dir() == (tmp_path / "wheel" / "tortoise" / "packs")
        assert "not a directory" in caplog.text

    def test_env_empty_dir_warns_and_falls_back(self, tmp_path, monkeypatch, caplog):
        """Set-but-EMPTY (zero manifests) → warn + packaged leg (the G1
        defect class in a new costume — explicitly handled)."""
        _make_wheel_layout(tmp_path / "wheel", ["dev", "marketing"])
        monkeypatch.setattr(
            pack_registry, "__file__", str(tmp_path / "wheel" / "tortoise" / "x.py"))
        env_dir = tmp_path / "empty-packs"
        env_dir.mkdir()
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        with caplog.at_level(logging.WARNING):
            assert default_packs_dir() == (tmp_path / "wheel" / "tortoise" / "packs")
        assert "contains no pack manifests" in caplog.text

    def test_env_template_only_dir_warns_and_falls_back(self, tmp_path, monkeypatch, caplog):
        """A dir holding ONLY `_template/manifest.yaml` is EMPTY (load_all
        skips `_`/`.` dirs) — must warn + fall back, never resolve (the
        resolver's emptiness definition matches the loader's skip rules)."""
        _make_wheel_layout(tmp_path / "wheel", ["dev", "marketing"])
        monkeypatch.setattr(
            pack_registry, "__file__", str(tmp_path / "wheel" / "tortoise" / "x.py"))
        env_dir = tmp_path / "template-only"
        (env_dir / "_template").mkdir(parents=True)
        (env_dir / "_template" / "manifest.yaml").write_text(
            _MINIMAL_MANIFEST.format(ns="_template"))
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        with caplog.at_level(logging.WARNING):
            assert default_packs_dir() == (tmp_path / "wheel" / "tortoise" / "packs")
        assert "contains no pack manifests" in caplog.text

    def test_env_blank_is_unset(self, tmp_path, monkeypatch, caplog):
        """Blank/whitespace env → treated as UNSET (config.py precedent);
        never resolves to the process CWD; no warning."""
        _make_wheel_layout(tmp_path / "wheel", ["dev", "marketing"])
        monkeypatch.setattr(
            pack_registry, "__file__", str(tmp_path / "wheel" / "tortoise" / "x.py"))
        monkeypatch.setenv("TORTOISE_PACKS_DIR", "   ")
        with caplog.at_level(logging.WARNING):
            assert default_packs_dir() == (tmp_path / "wheel" / "tortoise" / "packs")
        assert "TORTOISE_PACKS_DIR" not in caplog.text

    def test_env_unset_default_chain(self, tmp_path, monkeypatch, caplog):
        """Env unset → packaged leg, no env involvement (pins the no-env
        path so precedence regressions cannot hide)."""
        _make_wheel_layout(tmp_path / "wheel", ["dev", "marketing"])
        monkeypatch.setattr(
            pack_registry, "__file__", str(tmp_path / "wheel" / "tortoise" / "x.py"))
        with caplog.at_level(logging.WARNING):
            assert default_packs_dir() == (tmp_path / "wheel" / "tortoise" / "packs")
        assert "TORTOISE_PACKS_DIR" not in caplog.text

    def test_env_packs_dir_helper_boundaries(self, tmp_path, monkeypatch, caplog):
        """`env_packs_dir()` raw boundaries: unset → None; blank → None;
        missing → None + warn; valid → the dir."""
        assert env_packs_dir() is None  # unset (conftest deletes it)
        monkeypatch.setenv("TORTOISE_PACKS_DIR", "")
        assert env_packs_dir() is None
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(tmp_path / "nope"))
        with caplog.at_level(logging.WARNING):
            assert env_packs_dir() is None
        assert "does not exist" in caplog.text
        env_dir = tmp_path / "custom-packs"
        (env_dir / "tenant-ops").mkdir(parents=True)
        (env_dir / "tenant-ops" / "manifest.yaml").write_text(
            _MINIMAL_MANIFEST.format(ns="tenant-ops"))
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        assert env_packs_dir() == env_dir

    def test_warn_once_per_env_value(self, tmp_path, monkeypatch, caplog):
        """The warn-once sentinel suppresses repeats for the SAME broken env
        value (hot-path no-spam) but re-warns for a DIFFERENT value (the key
        is per-value, not global)."""
        broken = str(tmp_path / "missing-packs")
        monkeypatch.setenv("TORTOISE_PACKS_DIR", broken)
        with caplog.at_level(logging.WARNING):
            default_packs_dir()
            default_packs_dir()  # same value → sentinel suppresses
        env_warns = [r for r in caplog.records if "TORTOISE_PACKS_DIR" in r.message]
        assert len(env_warns) == 1
        other = str(tmp_path / "other-missing-packs")
        monkeypatch.setenv("TORTOISE_PACKS_DIR", other)
        with caplog.at_level(logging.WARNING):
            default_packs_dir()
        env_warns = [r for r in caplog.records if "TORTOISE_PACKS_DIR" in r.message]
        assert len(env_warns) == 2  # new value re-warns


# ── _get_registry() env chain (#1930 surface 3c) ──────────────────────────

class TestGetRegistryEnvChain:
    """The daemon registry honors the env leg; every fallback trigger warns
    and the registry is never silently empty (integration, DB-free)."""

    def _env_dir(self, tmp_path, namespaces: dict[str, str], name: str = "custom-packs") -> Path:
        """Build an env packs dir: {ns: manifest_text}."""
        env_dir = tmp_path / name
        for ns, text in namespaces.items():
            (env_dir / ns).mkdir(parents=True)
            (env_dir / ns / "manifest.yaml").write_text(text)
        return env_dir

    def test_env_valid_custom_pack_loads(self, tmp_path, monkeypatch):
        """Set+valid: the custom pack REPLACES the default catalog (one
        resolution rule — env leg wins outright)."""
        env_dir = self._env_dir(tmp_path, {"tenant-ops": _MINIMAL_MANIFEST.format(ns="tenant-ops")})
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        domain_loader._registry = None
        reg = domain_loader._get_registry()
        assert reg is not None
        assert set(reg.packs) == {"tenant-ops"}

    def test_env_missing_dir_falls_back_to_default(self, tmp_path, monkeypatch, caplog):
        """Missing dir → warn + the default (repo-root) starter set loads."""
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(tmp_path / "does-not-exist"))
        domain_loader._registry = None
        with caplog.at_level(logging.WARNING):
            reg = domain_loader._get_registry()
        assert reg is not None
        assert set(reg.packs) >= set(DEFAULT_STARTER_PACKS)
        assert "TORTOISE_PACKS_DIR" in caplog.text
        assert "does not exist" in caplog.text

    def test_env_empty_dir_falls_back_to_default(self, tmp_path, monkeypatch, caplog):
        """Empty dir → warn + default starter set (never silent empty)."""
        env_dir = tmp_path / "empty-packs"
        env_dir.mkdir()
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        domain_loader._registry = None
        with caplog.at_level(logging.WARNING):
            reg = domain_loader._get_registry()
        assert reg is not None
        assert set(reg.packs) >= set(DEFAULT_STARTER_PACKS)
        assert "contains no pack manifests" in caplog.text

    def test_env_template_only_dir_falls_back_to_default(self, tmp_path, monkeypatch, caplog):
        """`_template`-only env dir → warn + default starter set (the
        resolver/loader skip-rule alignment, plan-verify P1)."""
        env_dir = tmp_path / "template-only"
        (env_dir / "_template").mkdir(parents=True)
        (env_dir / "_template" / "manifest.yaml").write_text(
            _MINIMAL_MANIFEST.format(ns="_template"))
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        domain_loader._registry = None
        with caplog.at_level(logging.WARNING):
            reg = domain_loader._get_registry()
        assert reg is not None
        assert set(reg.packs) >= set(DEFAULT_STARTER_PACKS)
        assert "contains no pack manifests" in caplog.text

    def test_env_all_broken_manifests_fall_back_sticky(self, tmp_path, monkeypatch, caplog):
        """ALL manifests malformed → warn + fallback to the default catalog;
        the fallback is STICKY (one rebuild, no per-call reload storm);
        `fallback_note` is queryable."""
        env_dir = self._env_dir(tmp_path, {"broken": _MALFORMED_MANIFEST})
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        domain_loader._registry = None
        counts = {"n": 0}
        orig_init = pack_registry.PackRegistry.__init__

        def counting_init(self, packs_dir):
            counts["n"] += 1
            orig_init(self, packs_dir)

        monkeypatch.setattr(pack_registry.PackRegistry, "__init__", counting_init)
        with caplog.at_level(logging.WARNING):
            reg = domain_loader._get_registry()
        assert reg is not None
        assert set(reg.packs) >= set(DEFAULT_STARTER_PACKS), (
            f"all-broken env dir must fall back, got {sorted(reg.packs)}")
        assert "falling back to the default catalog" in caplog.text
        assert reg.fallback_note is not None
        assert counts["n"] == 2  # env attempt + fallback reload
        assert domain_loader._get_registry() is reg  # sticky short-circuit
        assert counts["n"] == 2  # no third construction

    def test_env_mixed_valid_and_malformed_no_fallback(self, tmp_path, monkeypatch, caplog):
        """Mixed valid+malformed: the valid pack loads, the malformed one is
        isolated (R-16) with the load-errors warning — NO fallback (WF-2:
        'that pack isolated, others load')."""
        env_dir = self._env_dir(tmp_path, {
            "tenant-ops": _MINIMAL_MANIFEST.format(ns="tenant-ops"),
            "broken": _MALFORMED_MANIFEST,
        })
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        domain_loader._registry = None
        with caplog.at_level(logging.WARNING):
            reg = domain_loader._get_registry()
        assert reg is not None
        assert set(reg.packs) == {"tenant-ops"}
        assert "broken" in reg.errors
        assert reg.fallback_note is None  # no fallback
        assert "failed validation and were isolated" in caplog.text
        assert "falling back to the default catalog" not in caplog.text

    def test_packs_dir_test_injection_wins_over_env(self, tmp_path, monkeypatch):
        """`_PACKS_DIR` (test injection) always wins over the env leg."""
        env_dir = self._env_dir(tmp_path, {"env-ns": _MINIMAL_MANIFEST.format(ns="env-ns")})
        injected = self._env_dir(tmp_path, {"inj-ns": _MINIMAL_MANIFEST.format(ns="inj-ns")}, name="injected-packs")
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        monkeypatch.setattr(domain_loader, "_PACKS_DIR", injected)
        domain_loader._registry = None
        reg = domain_loader._get_registry()
        assert reg is not None
        assert "inj-ns" in reg.packs
        assert "env-ns" not in reg.packs

    def test_env_not_a_dir_integration(self, tmp_path, monkeypatch, caplog):
        """Integration leg of the not-a-dir trigger: env points at a FILE →
        warn + default starter set (mirrors the missing-dir path)."""
        env_file = tmp_path / "not-a-dir"
        env_file.write_text("i am a file")
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_file))
        domain_loader._registry = None
        with caplog.at_level(logging.WARNING):
            reg = domain_loader._get_registry()
        assert reg is not None
        assert set(reg.packs) >= set(DEFAULT_STARTER_PACKS)
        assert "not a directory" in caplog.text

    def test_resolver_returns_malformed_env_dir(self, tmp_path, monkeypatch):
        """Resolver-level malformed-dir leg: manifest VALIDITY is not the
        resolver's job — a dir holding a malformed manifest still resolves
        (the registry's R-16 isolation + S1a fallback own the load decision)."""
        env_dir = tmp_path / "broken-packs"
        (env_dir / "broken").mkdir(parents=True)
        (env_dir / "broken" / "manifest.yaml").write_text(_MALFORMED_MANIFEST)
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        assert default_packs_dir() == env_dir

    def test_env_dir_load_raise_falls_back_sticky(self, tmp_path, monkeypatch, caplog):
        """S1b: the env-dir LOAD itself raises → warn + sticky fallback to
        the default catalog (never silent empty even on load failure)."""
        env_dir = self._env_dir(tmp_path, {"tenant-ops": _MINIMAL_MANIFEST.format(ns="tenant-ops")})
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        domain_loader._registry = None
        orig_load = pack_registry.PackRegistry.load_all
        env_path = Path(str(env_dir))

        def raisy(self):
            if self.packs_dir == env_path:
                raise RuntimeError("boom: corrupt catalog")
            return orig_load(self)

        monkeypatch.setattr(pack_registry.PackRegistry, "load_all", raisy)
        with caplog.at_level(logging.WARNING):
            reg = domain_loader._get_registry()
        assert reg is not None
        assert set(reg.packs) >= set(DEFAULT_STARTER_PACKS)
        assert "load failed" in caplog.text
        assert reg.fallback_note is not None
        assert str(env_dir) in reg.fallback_note  # observable, not mechanism
        assert domain_loader._get_registry() is reg  # sticky (no rebuild)


# ── Env-var composition (Indicator 1 catalog join, #1930) ─────────────────

class TestPacksDirComposition:
    """TORTOISE_PACKS_DIR + TORTOISE_STARTER_PACKS compose: a custom pack
    whose namespace is in the starter env activates and is visible via the
    `tortoise_packs_list` join; unknown starter names warn-skip (preserved)."""

    def test_custom_pack_activates_when_in_starter_set(self, tmp_path, monkeypatch, caplog):
        from tortoise import pack_state
        from tortoise.sdk import TortoiseSDK
        env_dir = tmp_path / "custom-packs"
        # The env dir REPLACES the default catalog: include a starter-set
        # namespace (dev) so the E2E-2 "alongside the starter set" list form
        # is demonstrated, not just the replacement form.
        (env_dir / "dev").mkdir(parents=True)
        (env_dir / "dev" / "manifest.yaml").write_text(
            "namespace: dev\nname: dev\ntier: free\n")
        (env_dir / "tenant-ops").mkdir(parents=True)
        (env_dir / "tenant-ops" / "manifest.yaml").write_text(
            "namespace: tenant-ops\nname: Tenant Ops\ntier: free\n")
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        monkeypatch.setenv("TORTOISE_STARTER_PACKS", "dev,tenant-ops,bogus-pack")
        sdk = TortoiseSDK(
            db_path=str(tmp_path / "a.db"),
            namespace=f"test_pack_team_{os.urandom(4).hex()}")
        with caplog.at_level(logging.WARNING):
            activated = pack_state.ensure_tenant_packs(sdk)
        names = sorted(r["namespace"] for r in activated)
        assert "dev" in names and "tenant-ops" in names
        assert "bogus-pack" not in names  # unknown starter name skipped
        # warn half of 3e: the skip is never silent (pack_state warn-skip)
        assert any(
            "bogus-pack" in r.message and "unknown" in r.message
            for r in caplog.records)
        packs = pack_state.get_tenant_packs(sdk)
        by_ns = {p["namespace"]: p for p in packs}
        assert "dev" in by_ns and "tenant-ops" in by_ns  # alongside the starter set
        # catalog join: the operator-visible record carries the ENV-DIR metadata
        # (name != namespace proves provenance — namespace fallback would give
        # name == ns).
        assert by_ns["tenant-ops"]["name"] == "Tenant Ops"
        assert by_ns["tenant-ops"]["tier"] == "free"

    def test_malformed_starter_pack_absent_from_list(self, tmp_path, monkeypatch, caplog):
        """Failure journey (E2E-2): a malformed pack in the env dir whose
        namespace is in TORTOISE_STARTER_PACKS is isolated (R-16) → absent
        from the operator list while the valid pack shows, with a startup
        warning (registry.errors stays the supplementary diagnostic)."""
        from tortoise import pack_state
        from tortoise.sdk import TortoiseSDK
        env_dir = tmp_path / "custom-packs"
        (env_dir / "dev").mkdir(parents=True)
        (env_dir / "dev" / "manifest.yaml").write_text(
            "namespace: dev\nname: dev\ntier: free\n")
        (env_dir / "broken").mkdir(parents=True)
        (env_dir / "broken" / "manifest.yaml").write_text(_MALFORMED_MANIFEST)
        monkeypatch.setenv("TORTOISE_PACKS_DIR", str(env_dir))
        monkeypatch.setenv("TORTOISE_STARTER_PACKS", "dev,broken")
        sdk = TortoiseSDK(
            db_path=str(tmp_path / "a.db"),
            namespace=f"test_pack_team_{os.urandom(4).hex()}")
        with caplog.at_level(logging.WARNING):
            activated = pack_state.ensure_tenant_packs(sdk)
        names = sorted(r["namespace"] for r in activated)
        assert "dev" in names
        assert "broken" not in names  # isolated (R-16) → never activated
        packs = pack_state.get_tenant_packs(sdk)
        ns = [p["namespace"] for p in packs]
        assert "dev" in ns and "broken" not in ns
        assert "failed validation and were isolated" in caplog.text


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
