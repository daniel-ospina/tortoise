"""Shared hosted-API fixture: ``patched_tortoise_sdk`` (the #1950/#2090
keepalive-anchor pattern, issue #2127).

The patch-fixture family (hosted-api test doubles) lives here — beside
``tests/fake_control_plane.py`` — NOT in ``tests/_embedded.py`` (whose charter
is centralized embedded CONSTRUCTION + the #1647 lane machinery; conftest
eagerly imports it every session). This module is import-pure and on-demand:
migrated test files ``from tests._http_fixtures import patched_tortoise_sdk``
(namespace-package import, same style as ``tests.fake_control_plane``).

Replaces ~24 per-file local copies of the churn-shaped fixture pattern
(``TortoiseSDK.__init__`` patch + ``_FALLBACK_KEEPALIVE.clear()`` WITHOUT a
``TORTOISE_DB_PATH`` pin and WITHOUT deterministic anchor close at restore) —
the latent class that produced the #2090 403s (empty respawn after redislite
daemon death) and the #2049-b dead-socket sibling.
"""
from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator


def _close_keepalive_anchors(module) -> None:
    """Deterministically close every keepalive anchor (SHUTDOWN SAVE).

    # mirrors tests/test_hosted_api.py:144-153 — keep in sync.
    """
    for ns in list(module._FALLBACK_KEEPALIVE):
        anchor = module._FALLBACK_KEEPALIVE.pop(ns, None)
        if anchor is not None:
            try:  # noqa: SIM105  (mirrors tests/test_hosted_api.py:149)
                anchor.close()
            except Exception:
                pass


@contextlib.contextmanager
def patched_tortoise_sdk(db_path: str) -> Iterator[None]:
    """Run one test's hosted-API SDK constructions against a temp embedded DB.

    Enter:
    - patch ``tortoise.hosted_api.TortoiseSDK.__init__`` so every no-path
      construction binds ``db_path`` (mirrors tests/test_hosted_api.py:104-120).
    - deterministically close + clear ``_FALLBACK_KEEPALIVE`` (#1497/#1950:
      the module-level anchor dict survives test files, so a stale anchor
      bound to a PREVIOUS test's temp DB leaks state into this test — the
      anchor's socket dies when that tempdir is removed → redis.socket
      ConnectionError, or the previous graph's rows appear in the "fresh"
      temp DB). Close-then-clear: never clear-without-close at the enter site
      (the #2090 close-not-clear lesson — a stray anchor from a prior file in
      the same session is reaped deterministically, not GC'd later).
    - pin ``TORTOISE_DB_PATH`` to the SAME temp DB (#1950,
      tests/test_hosted_api.py:174-202): so ``_make_sdk``'s keepalive anchor
      path matches and the anchor is REUSED instead of evicted + closed on
      every registry access (each eviction shut the redislite daemon down
      mid-test, losing the seed between a fixture's write and the gate read
      → #2090 403).

    Exit (exception-safe):
    - pop the pin FIRST, restore ``__init__``, then close every keepalive
      anchor deterministically (clear-without-close leaked anchors — #1950
      lesson, tests/test_hosted_api.py:135-153; SHUTDOWN SAVE, not GC-timed
      NOSAVE), then clear ``app.dependency_overrides`` (fixture teardown
      convention, mirrors test_export_delete ``_restore_sdk_init``).

    Import-pure module: no module-level env writes, no hosted_api import at
    module level — the lazy ``import tortoise.hosted_api`` here is deliberate
    so pytest collection never side-effects (issue #2127 scope).
    """
    import tortoise.hosted_api as ha_mod

    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None, **kwargs):
        # Deliberately drop caller path args (db_path_arg / a db_path kwarg):
        # _make_sdk's embedded-lane fallback constructs
        # TortoiseSDK(db_path=<shared path>) — forwarding the caller path
        # would re-bind the SHARED fallback DB and re-enable the #2090
        # churn/leak class (the pass-through hazard the wave-2 onboarding
        # :272/:309 audit names). event_log_path and other kwargs are also
        # dropped in the fixture context (no event files in temp DBs) —
        # mirrors the #1950 canonical (tests/test_hosted_api.py:110-113).
        _orig_init(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched_init
    _close_keepalive_anchors(ha_mod)
    # clear() kept for parity with the #1497/#1950 precedents (a no-op after
    # the deterministic close above).
    ha_mod._FALLBACK_KEEPALIVE.clear()
    # Snapshot-restore the pin (code-review P3): a pre-existing
    # TORTOISE_DB_PATH from the outer env is restored at exit, never deleted
    # for the rest of the pytest session. CI never sets it (the precedents
    # pop unconditionally); the snapshot is strictly safer for dev shells
    # that export it.
    _prev_db_path = os.environ.get("TORTOISE_DB_PATH")
    os.environ["TORTOISE_DB_PATH"] = db_path
    try:
        yield
    finally:
        if _prev_db_path is None:
            os.environ.pop("TORTOISE_DB_PATH", None)
        else:
            os.environ["TORTOISE_DB_PATH"] = _prev_db_path
        ha_mod.TortoiseSDK.__init__ = _orig_init
        _close_keepalive_anchors(ha_mod)
        ha_mod.app.dependency_overrides.clear()
