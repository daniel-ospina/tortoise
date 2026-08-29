# Discovery stub for the `tortoise.packs` package (#1929, epic #1891 slice 1).
#
# This file exists so `packages.find` discovers the `tortoise.packs` package
# (setuptools cannot discover a `package_dir`-mapped dir — `package-dir =
# {"tortoise.packs": "packs"}` in pyproject.toml points the package at the
# repo-root `packs/` catalog, the single source of truth). It also keeps the
# package in the sdist (MANIFEST.in) so an sdist-rebuilt wheel still ships
# the catalog.
#
# NOTE: the stub DOES ship in the wheel (build_py includes the discovered
# package's __init__.py). It is a marker only — every consumer reads the
# catalog via Path resolution (`pack_registry.default_packs_dir()`), never
# via `import tortoise.packs` or `tortoise.packs.__file__` (which in some
# layouts is None — PEP 420 namespace fallback).
