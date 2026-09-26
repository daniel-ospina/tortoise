"""The single declared env-truthiness contract (#4097).

Before #4097 the tree carried five divergent conventions for reading a boolean
environment variable, and nothing asserted they agreed — so an operator's
documented ``true``/``TRUE`` silently did nothing wherever a narrow read happened
to exist:

  V1  truthy set {"1","true","yes","on"}     (the de-facto operational standard:
      .github/workflows/deploy-hosted.yml sets BACKUP_SWEEP_ENABLED=true, and
      .env.example documents BACKUP_LOCK_ENABLED as "operator sets TRUE")
  V2  narrow ``== "1"``                       (the reads whose widening would relax a
      guard are frozen in tests/test_env_truthy.py::_KNOWN_NARROW_READS, #4128)
  V3  truthy-minus-``on`` {"1","true","yes"}  (``=on`` silently did nothing)
  V4  falsy list {"0","false","no","off"}     (blank meant OFF in one module, the
      default in another)
  V5  presence (``if os.environ.get(X)``)     (out of scope: for a key/URI/path,
      present-or-absent is genuinely the question)

Stdlib-only by design — no imports from ``tortoise`` — so any module may import
these helpers without creating an import cycle (the ``tortoise/security.py`` /
``tortoise/transport.py`` precedent). This does NOT make an import of this module
cheap for a *standalone* consumer: importing any ``tortoise.<sub>`` runs
``tortoise/__init__.py``, which imports redislite unconditionally.
``tortoise/embedded_reaper.py`` therefore mirrors ``TRUTHY`` instead of delegating,
and ``tests/test_env_truthy.py`` pins both the mirror and the reaper's standalone
import purity.

Two shapes, deliberately distinct:

  * ``is_truthy(raw)`` — a raw-VALUE predicate. Unset/blank/falsy/garbage ->
    False. Pair it with an explicit default VALUE when a knob is default-ON and a
    typo must read OFF: ``is_truthy(os.environ.get("X", "1"))``.
  * ``env_flag(name, default)`` — a tristate RESOLVER. unset/blank/garbage ->
    ``default`` (a typo never flips a knob); explicit truthy -> True; explicit
    falsy -> False.
"""
from __future__ import annotations

import os

#: The one truthy vocabulary. Case-insensitive; surrounding whitespace ignored.
TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})

#: The one falsy vocabulary — the explicit OFF spellings, as opposed to "unset".
FALSY: frozenset[str] = frozenset({"0", "false", "no", "off"})


def _normalized(raw: object | None) -> str:
    """`None` -> the empty string; anything else -> `str(raw)` stripped + lowercased."""
    return "" if raw is None else str(raw).strip().lower()


def is_truthy(raw: object | None) -> bool:
    """Is this *value* a truthy spelling? Unset/blank/falsy/garbage -> False."""
    return _normalized(raw) in TRUTHY


def env_flag(name: str, default: bool) -> bool:
    """Resolve env var `name` as a caller-defaulted flag.

    unset/blank/garbage -> `default`; explicit truthy -> True; explicit falsy ->
    False. A blank value is *unset*, not a statement — the contract
    ``retrieval.ask_env_bool`` implemented.
    """
    raw = _normalized(os.environ.get(name))
    if not raw:
        return default
    if raw in TRUTHY:
        return True
    if raw in FALSY:
        return False
    return default
