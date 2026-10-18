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
``tortoise/transport.py`` precedent). This does NOT make an import of this
module cheap for a *standalone* consumer: importing any ``tortoise.<sub>`` runs
``tortoise/__init__.py``, whose ``FalkorDB`` re-export is deferred (PEP 562
``__getattr__``, #5386) but whose package import still carries side effects
(atexit registration, fork-safety arming) and needs ``tortoise`` importable in
the first place — which it is not for the Hermes plugin, loaded by the HOST
interpreter from a copied-out directory. ``tortoise/embedded_reaper.py`` and
``tortoise/hermes-plugin/__init__.py`` therefore mirror ``TRUTHY`` instead of
delegating, and ``tests/test_env_truthy.py`` pins both the mirrors and the
standalone import purity.

Three shapes, deliberately distinct:

  * ``is_truthy(raw)`` — a raw-VALUE predicate. Unset/blank/falsy/garbage ->
    False. Pair it with an explicit default VALUE when a knob is default-ON and a
    typo must read OFF: ``is_truthy(os.environ.get("X", "1"))``.
  * ``env_flag(name, default)`` — a tristate RESOLVER. unset/blank/garbage ->
    ``default`` (a typo never flips a knob); explicit truthy -> True; explicit
    falsy -> False.
  * ``env_flag_from_value(raw, default)`` — the SAME resolver over a raw VALUE
    instead of a variable name, for a caller that takes an injectable value
    (``tortoise.time_aware.prefer_latest_enabled(raw=…)``). It lives HERE, and
    ``env_flag`` delegates to it, so the resolution order has exactly ONE copy:
    #8487 review round 8 found the resolver duplicated in ``time_aware``, where
    nothing pinned the two copies together — the sixth spelling of the
    resolution this module exists to prevent.
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
    return env_flag_from_value(os.environ.get(name), default)


def env_flag_from_value(raw: object, default: bool) -> bool:
    """`env_flag`'s resolution order over a RAW VALUE instead of a name.

    The one copy of the order, so an INJECTED value cannot resolve differently
    from the environment (#8487 review round 8): unset/blank/garbage ->
    `default`; explicit truthy -> True; explicit falsy -> False.
    """
    value = _normalized(raw)
    if not value:
        return default
    if value in TRUTHY:
        return True
    if value in FALSY:
        return False
    return default
