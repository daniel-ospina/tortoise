from __future__ import annotations

"""DEPRECATED — This flat file is dead code.

`tortoise.projection` resolves to the canonical package at
`tortoise/projection/__init__.py`. This shim delegates to the
package via importlib to avoid circular imports (this file IS
`tortoise.projection` during its own loading, so a direct
`from tortoise.projection import *` would be circular).

All implementations, entity handlers, and edge handlers live in
the package submodules (entities.py, edges.py, grounding.py,
propagation.py) — never modify this shim.
"""
import sys as _sys  # noqa: E402, I001
import importlib as _importlib  # noqa: E402

# Remove this shim from sys.modules temporarily so importlib finds
# the canonical package at tortoise/projection/__init__.py
_shim_module = _sys.modules.pop('tortoise.projection', None)

# Load the canonical package
_pkg = _importlib.import_module('tortoise.projection')

# Re-export canonical public symbols
FalkorProjection = _pkg.FalkorProjection
InMemoryProjection = _pkg.InMemoryProjection
Projection = _pkg.Projection
fold = _pkg.fold
split = _pkg.split
_apply_one = _pkg._apply_one
_now_iso = _pkg._now_iso
_norm = _pkg._norm

# Restore this shim as tortoise.projection so downstream relative
# imports (from .projection import ...) resolve correctly
_sys.modules['tortoise.projection'] = _sys.modules[__name__]
