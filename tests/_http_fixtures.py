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
    - restore the pin if it was set at enter (else pop it) FIRST, restore
      ``__init__``, then close every keepalive anchor deterministically
      (clear-without-close leaked anchors — #1950 lesson,
      tests/test_hosted_api.py:135-153; SHUTDOWN SAVE, not GC-timed NOSAVE),
      then clear ``app.dependency_overrides`` (fixture teardown convention,
      mirrors test_export_delete ``_restore_sdk_init``).

    Import-pure module: no module-level env writes, no hosted_api import at
    module level — the lazy ``import tortoise.hosted_api`` here is deliberate
    so pytest collection never side-effects (issue #2127 scope).
    """
    import tortoise.hosted_api as ha_mod

    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None,
                      graph_name=None, **kwargs):
        # Deliberately drop caller path args (db_path_arg / a db_path kwarg):
        # _make_sdk's embedded-lane fallback constructs
        # TortoiseSDK(db_path=<shared path>) — forwarding the caller path
        # would re-bind the SHARED fallback DB and re-enable the #2090
        # churn/leak class (the pass-through hazard the wave-2 onboarding
        # :272/:309 audit names). event_log_path and other kwargs are also
        # dropped in the fixture context (no event files in temp DBs) —
        # mirrors the #1950 canonical (tests/test_hosted_api.py:110-113).
        #
        # graph_name is NOT one of those: it is the C5 #2114 explicit
        # full-name seam (the data-plane tenancy path —
        # `_require_graph_scope`, `_export_graph_snapshot`, the backup
        # restores). Dropping it re-binds the SDK to the DEFAULT graph, so a
        # test exercising that seam silently reads (or writes) an EMPTY graph
        # and passes or fails for the wrong reason. Every other canonical
        # fixture forwards it (test_hosted_api.py:133,
        # test_acl_graph_users.py:91) — this one was the outlier.
        _orig_init(self, db_path, namespace=namespace, graph_name=graph_name)

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


async def quiet_boot_sweeps() -> None:
    """#3472: no-op stand-in for the lifespan's one-shot `_run_boot_sweeps`.

    Must be `async def`: `_lifespan` arms it with
    `create_task(_run_boot_sweeps())`, so a sync stub returns `None` and
    `create_task(None)` raises. `_lifespan` swallows that (it logs
    `boot sweeps did NOT run`) rather than failing the app — measured: a sync
    stub still passes these suites — but it puts a false ERROR line in front of
    every reader on every `TestClient` entry, which is the last thing a
    test-isolation helper should add.
    """


def quiesce_testclient_background_work(monkeypatch) -> None:
    """#3472/#3505: make `TestClient(app)` entry safe for the fixtures that call it
    (``sb_client``, ``reg_client``, ``supabase_client``, and
    ``test_writer_inventory.client``).

    `_lifespan` arms background work for EVERY `TestClient(app)` entry,
    regardless of control-plane mode, and part of it touches the same store
    the test body is driving:

    1. `_run_boot_sweeps()` (armed by `_lifespan` as
       `app.state._boot_sweep_task`, one-shot) and `_event_retention_loop()`
       (armed by `_lifespan` as `app.state._event_retention_task`,
       re-armed every `event_retention_interval()`) BOTH call
       `_purge_deleted_orgs`. A
       background purge landing between a test's seeding and its own
       `ha_mod._purge_deleted_orgs()` call makes both read the row before
       either deletes it: two `_drop_team_graph` calls and two
       `team_delete_purged` audit rows — `assert ['reg-old', 'reg-old'] ==
       ['reg-old']` on the registry fixture. Worse on Supabase mode, where
       the test injects its `_drop_team_graph_strict` fault ONLY AFTER
       seeding: a boot sweep in that window runs the REAL strict drop for
       the past-grace teams and deletes the retry-anchor row the test
       asserts must survive.

       The product behaviour is benign (dropping an already-dropped graph
       is idempotent) — the defect is test isolation: the assertions assume
       exclusive ownership of a sweep production also runs. Every caller is
       therefore quiesced here.

       The COMPLETE membership is the two entry points in `hosted_api.py` —
       `_run_boot_sweeps`'s seven-step `for label, fn in (...)` tuple and
       `_event_retention_loop`'s eight `_guarded_step` awaits (the same seven
       plus `_refresh_cost_allocation`) — and this list exists so the next
       addition is visible, not to be re-derived here. It was INCOMPLETE until
       #4816: it named three of the six and omitted
       `_reconcile_metering_periods` (#4241), the only one whose control-plane
       **RPC** runs on EVERY cycle — it is guarded only by
       `is_supabase_enabled()` and then *queries* for unusable periods, so
       `metering_repair_period_bounds` is recorded on
       `FakeControlPlane.rpc_calls` whether or not the fixture seeded anything.
       The purge sweeps also reach the control plane (`purge_org_record`,
       `account_deletion_claim_org`) but only when an eligible row exists,
       which these fixtures do not seed — so the metering RPC was the one that
       could land ahead of a test's own call unsolicited. That omission is
       exactly the flake this seam now prevents.

       Enumerated for reference: `_sweep_events`, `_purge_deleted_orgs`,
       `_purge_deleted_accounts`, `_sweep_oauth_retention`,
       `_sweep_invite_ghosts`, `_sweep_expired_invitations` (#8089 — the
       Supabase-lane clock-expiry sweep), `_reconcile_metering_periods`
       (boot + loop), and `_refresh_cost_allocation` (loop only). A new sweep
       reachable from either entry point belongs here.

       The CALLEE is deliberately not stubbed: `tests/test_export_delete.py`
       calls `ha_mod._purge_deleted_orgs()` directly and resolves it off the
       module at call time, so a callee stub would silence the very call
       under test. `_run_boot_sweeps` is stubbed with an `async def` because
       `_lifespan` arms it with `create_task(...)` (see
       `quiet_boot_sweeps`). The retention interval is pinned beyond any
       test's lifetime: `_event_retention_loop` invokes BOTH `_sweep_events`
       and `_purge_deleted_orgs` on the same target, so pinning the
       interval quiesces the loop while leaving the directly-called
       `_purge_deleted_orgs` under test.

    2. The app's `_health_probe_loop` (armed by `_lifespan` as
       `app.state._health_probe_task`) is NOT stubbed:
       it runs `_probe_db -> _probe_sdk -> _make_sdk(namespace=None)` and so
       constructs a projection on the SAME pinned db file, concurrently with
       the test body's own constructions (the seeder's and
       `_registry_count`'s SDKs). Quiescing the boot sweeps does NOT remove
       that constructor, so the embedded double-start race would stay live.
       Rather than quiesce a third background caller one caller at a time
       (whack-a-mole — `_lifespan` already grew the probe loop after
       #2850), the CONSTRUCTION is serialized: the invariant redislite
       actually needs is that the first construction on a given db_path
       writes `<db>.settings` before any other opener evaluates the
       fresh-start branch. #3546 moved that serialization to ONE
       process-wide lock for the whole session
       (`tests/_embedded.EMBEDDED_CONSTRUCTION_LOCK`, installed by
       `tests/conftest._serialize_embedded_construction`) — the callers no
       longer install their own,

       LANE SCOPE — the serialization is INERT on the lane CI runs these
       suites on. Under a supported `TORTOISE_DB_URI` (the docker lane, these
       consumers' default) every construction from this module redirects to that server
       (`tortoise/projection/__init__.py`, the #1647 D-1=A test redirect:
       `path` is nulled, so `_is_embedded` is False), so no redislite daemon
       exists and no double-start is possible. NONE of the callers
       (`test_export_delete`, `test_pack_state`, `test_writer_inventory`) is in
       `tests._embedded.TEST_NO_REDIRECT_STEMS`, which is what selects that
       branch. On that lane the protection the callers actually get is item 1
       — the BOOT-SWEEP quiesce, which removes the second caller of
       `_purge_deleted_orgs` — and NOT the serialization. The lock is live
       only on the embedded tier-2 / carve-out lane (no URI), where
       constructions stay local-file and real daemons are spawned; it is
       kept for correctness there, not because the docker lane depends on
       it.
    """
    # (1) quiesce both background callers of the purge sweep (the caller, not
    # the callee — `_purge_deleted_orgs` itself stays under test).
    import tortoise.hosted_api as ha_mod

    monkeypatch.setattr(ha_mod, "_run_boot_sweeps", quiet_boot_sweeps)
    monkeypatch.setattr(ha_mod, "event_retention_interval",
                        lambda *args, **kwargs: 86400.0)
