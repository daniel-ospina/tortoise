"""HTTP tests for the #302 security-baseline remainder — data export +
account/team deletion (E2E-6-D), on BOTH control planes.

Supabase mode (FakeControlPlane, mirroring test_auth_flip):
- GET /v1/organizations/{id}/export — owner-only JSON export (graph + control plane)
- DELETE /v1/organizations/{id} — owner-only soft delete → 7-day grace → hard purge

Registry mode (temp FalkorDBLite, mirroring test_dr_endpoints): the same
surface over registry Membership/APIKey/Team nodes.

Covers: auth failures (401/403), unknown team (404), deleted team (410),
happy paths, idempotency, fail-closed key auth after delete, audit events,
per-IP rate limits, and the post-grace purge sweep.
"""
from __future__ import annotations

import os
import tempfile
import time
import warnings
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
# Global middleware + sensitive-op limiter opt out in tests (mirrors
# test_hosted_api); the rate-limit test re-enables the sensitive limiter.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import tortoise.hosted_api as ha_mod  # noqa: I001
from tortoise.hosted_api import app, get_current_user
from tortoise.retention import RESTORE_WINDOW_HOURS
from tortoise.sdk import TortoiseSDK

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane
from tests.test_supabase_control import (
    FREE_TEAM, TOKEN, _key_row, _membership_row,
)

ORG_ID = "team-free-001"

# ═══════════════════════════════════════════════════════════════════════
# #2090 — keepalive-anchor churn instrumentation.
# `_make_sdk`/`_registry_anchor` (tortoise/hosted_api.py) evict a keepalive
# anchor whose `_db_path` no longer matches the env-resolved embedded path; the
# evicted anchor is closed, and a dropped seed SDK's GC-NOSAVE
# (`register_gc_close`/`_gc_close` in embedded_lifecycle.py,
# TORTOISE_FAST_ATEXIT=1) can kill the redislite daemon → empty respawn →
# 403 "Requires owner role in team". The counter asserts ZERO mid-test drift
# evictions — the property the pinned fixture must hold.
# ═══════════════════════════════════════════════════════════════════════

_EXPECTED_DRIFT_EVICTIONS = 0  # the pinned fixture must evict none of its own anchors
_DRAIN_ATTEMPTS = 5  # teardown drain retries before the completeness guard

# #2090 (Task 3) — held seed SDKs: never dropped, closed deterministically
# per-test by _close_seed_sdks (function-scoped close collapses peak daemons;
# session-scoped holding would raise the external-death resource class).
_SEED_SDKS: list[TortoiseSDK] = []


def _close_seed_sdks() -> None:
    """Close held seed SDKs (per-test; runs in the fixture finally).

    # mirrors tests/test_dr_endpoints.py's session-scoped
    # `_close_seed_sdks` — keep in sync.
    """
    while _SEED_SDKS:
        try:  # noqa: SIM105  (mirrors that fixture's pop/close drain)
            _SEED_SDKS.pop().close()
        except Exception:
            pass


def _computed_db_path() -> str:
    """Replicate _make_sdk's env-path computation.

    Mirrors `_resolve_embedded_db_path` (tortoise/hosted_api.py) — keep in
    sync.
    """
    db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    except OSError:
        db_path = os.path.join(tempfile.gettempdir(), "tortoise.db")
    return db_path


def _paths_same(path_a: object, path_b: str) -> bool:
    """Mirror `_anchor_usable`'s path comparison (tortoise/hosted_api.py)."""
    return (str(path_a) == str(path_b)) or (
        str(path_a) != ":memory:"
        and os.path.abspath(str(path_a)) == os.path.abspath(path_b)
    )


class _DriftEvictionCounter(dict):
    """Counting-dict replacement for ha_mod._FALLBACK_KEEPALIVE.

    Counts evictions during the test body; restore-time pops are excluded by
    setting ``enabled = False`` first. Each eviction is classified against the
    two paths that matter:

    - ``expected_db_path`` — the temp DB ``patched_tortoise_sdk`` force-binds
      every in-test ``TortoiseSDK`` to, so the anchor was created by THIS test.
    - ``_computed_db_path()`` — what ``_resolve_embedded_db_path`` resolves
      from the env. The fixture pins ``TORTOISE_DB_PATH`` to
      ``expected_db_path``, so the two agree and the anchor is reused.

    Buckets:

    - own path, env matches → ``probe_failures`` (warn-only: a transient probe
      failure on a loaded runner must not red the gate);
    - own path, env drifted → ``drift_evictions`` (the GATE,
      ``== _EXPECTED_DRIFT_EVICTIONS``);
    - another test's path → ``leaked_evictions`` (warn-only: a background probe
      worker's in-flight ``_make_sdk`` from a prior test finishes after this
      test's counter is installed and lands under key ``''``; evicting it is
      the intended #1502/#1950 self-heal, not #2090 churn);
    - no bound path → ``unclassified`` (vacuity guard, warn-only).

    The gate cannot fire inside ``reg_client``: the fixture pins
    env == expected, so an eviction of this test's own anchor lands in
    ``probe_failures``. It fires only where the env is deliberately drifted —
    ``TestDriftCounterWiring.test_drift_counter_classifies_real_eviction``.
    """

    def __init__(self, expected_db_path: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.expected_db_path = expected_db_path
        self.drift_evictions = 0
        self.probe_failures = 0
        self.leaked_evictions = 0
        self.unclassified = 0
        self.enabled = True
        # Set to the real dict at teardown so a stale-reference late write from
        # a background worker lands in the dict patched_tortoise_sdk's
        # exit-close drains, instead of in the discarded counter. Lock-free on
        # purpose: teardown must not block on _KEEPALIVE_LOCK held by a slow
        # in-flight construction.
        self.forward_to = None

    def setdefault(self, key, default=None):
        if self.forward_to is not None:
            return self.forward_to.setdefault(key, default)
        return super().setdefault(key, default)

    def __setitem__(self, key, value):
        if self.forward_to is not None:
            self.forward_to[key] = value
            return
        super().__setitem__(key, value)

    def pop(self, key, default=None):
        if self.enabled:
            value = dict.get(self, key)
            if value is not None:
                bound = getattr(value, "_db_path", None)
                if bound is None:
                    self.unclassified += 1  # never silently ignore (vacuity guard)
                elif not _paths_same(bound, self.expected_db_path):
                    # Foreign/leaked anchor (another test's in-flight worker) —
                    # self-healing to evict it; never a #2090 drift signal.
                    self.leaked_evictions += 1
                elif _paths_same(bound, _computed_db_path()):
                    self.probe_failures += 1  # this test's anchor, probe-failure/benign
                else:
                    self.drift_evictions += 1  # this test's anchor path-drifted → churn
        return dict.pop(self, key, default)


def _install_drift_counter(expected_db_path: str) -> tuple[_DriftEvictionCounter, dict]:
    """Swap the module keepalive dict for a counting dict (fresh per test).

    ``expected_db_path`` is the fixture's temp DB — the path every in-test
    ``TortoiseSDK`` is force-bound to. See ``_DriftEvictionCounter``.
    """
    _orig_dict = ha_mod._FALLBACK_KEEPALIVE
    counter = _DriftEvictionCounter(expected_db_path, _orig_dict)
    ha_mod._FALLBACK_KEEPALIVE = counter
    return counter, _orig_dict


def _own_bound_leftovers(counter: _DriftEvictionCounter) -> list[TortoiseSDK]:
    """Anchors bound to THIS test's db_path still present in ``counter``."""
    return [
        anchor for anchor in counter.values()
        if _paths_same(getattr(anchor, "_db_path", None), counter.expected_db_path)
    ]


def _drain_drift_counter(counter: _DriftEvictionCounter) -> None:
    """Close the anchors ``counter`` holds and assert the drain completed.

    #2127: the fixture owns the deterministic close of anchors the counter
    tracked. A late write from a background worker holding a stale reference is
    routed to the real dict by ``forward_to`` and closed by
    ``patched_tortoise_sdk``'s exit-close, so a leftover bound to this test's
    db_path means an anchor THIS test created was never closed → RED. A
    leftover bound elsewhere is the warn-only cross-test leak bucket.
    """
    for _ in range(_DRAIN_ATTEMPTS):
        for namespace in list(counter):
            anchor = counter.pop(namespace, None)
            if anchor is not None:
                try:  # noqa: SIM105
                    anchor.close()
                except Exception:
                    pass
        if not counter:
            break
        time.sleep(0)  # yield so an in-flight insert can land before re-check

    own_leftover = _own_bound_leftovers(counter)
    assert not own_leftover, (
        f"#2127 drain-completeness guard: {len(own_leftover)} anchor(s) bound "
        f"to this test's db_path survived the drain: "
        f"{[getattr(a, '_db_path', None) for a in own_leftover]}"
    )
    if counter:
        warnings.warn(
            f"[#2090] keepalive drain left {len(counter)} non-own anchor(s) "
            f"after {_DRAIN_ATTEMPTS} attempts",
            UserWarning,
            stacklevel=2,
        )


@pytest.mark.embedded_only
class TestDriftCounterWiring:
    """#2090 wiring negative control — pins the counter's install + the
    drift classification against the REAL _make_sdk eviction path, so the
    0-guard provably stays wired (removing the counter install, or a
    production refactor away from .pop() eviction, would fail here).
    Embedded-only: under a URI the keepalive branch never engages, so the
    eviction path this test exercises does not exist on the docker lane.
    """

    def test_drift_counter_classifies_real_eviction(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "wiring.db")
            drifted = os.path.join(tmpdir, "drifted-env.db")
            # The #2090 shape: the anchor is bound to the fixture's force-bind
            # path (db_path), while the env resolves a DIFFERENT embedded path.
            # _make_sdk path-drifts and evicts the test's OWN anchor — that must
            # be counted as a gate-failing drift.
            monkeypatch.setenv("TORTOISE_DB_PATH", drifted)
            ha_mod._FALLBACK_KEEPALIVE.clear()
            counter, _orig_dict = _install_drift_counter(db_path)
            try:
                anchor = TortoiseSDK(db_path=db_path, namespace="registry")
                # Production-shaped: _make_sdk eagerly holds the anchored
                # connection, so the eviction below must exercise the
                # PATH-DRIFT branch of _anchor_usable — not the earlier
                # `proj is None` short-circuit.
                anchor._get_proj()
                ha_mod._FALLBACK_KEEPALIVE["registry"] = anchor
                ha_mod._make_sdk(namespace="registry")  # evict + close + pop
                assert counter.drift_evictions >= 1, (
                    f"drift eviction not counted (got {counter.drift_evictions}, "
                    f"leaked: {counter.leaked_evictions}, "
                    f"probe-failures: {counter.probe_failures})"
                )
                assert counter.leaked_evictions == 0
                assert counter.probe_failures == 0
            finally:
                counter.enabled = False
                # close any held anchors from the counter directly (uncounted)
                for _ns in list(counter):
                    _anchor = dict.pop(counter, _ns, None)
                    if _anchor is not None:
                        try:  # noqa: SIM105
                            _anchor.close()
                        except Exception:
                            pass
                ha_mod._FALLBACK_KEEPALIVE = _orig_dict

    def test_drift_counter_classifies_leaked_eviction(self, monkeypatch):
        """A pop of an anchor bound to ANOTHER test's path is a self-heal,
        not #2090 churn (a background health-probe worker's in-flight
        _make_sdk inserts a prior test's anchor into this test's counter).
        It must land in the warn-only leak bucket."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "wiring.db")
            previous = os.path.join(tmpdir, "previous-test.db")
            monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
            ha_mod._FALLBACK_KEEPALIVE.clear()
            counter, _orig_dict = _install_drift_counter(db_path)
            try:
                anchor = TortoiseSDK(db_path=previous, namespace="registry")
                # Same fidelity as the sibling test: production anchors hold
                # an eager projection, so the eviction must exercise the
                # path-mismatch branch, not the `proj is None` short-circuit.
                anchor._get_proj()
                ha_mod._FALLBACK_KEEPALIVE["registry"] = anchor
                ha_mod._make_sdk(namespace="registry")  # evict + close + pop
                assert counter.leaked_evictions == 1, (
                    f"leaked eviction not classified (got "
                    f"{counter.leaked_evictions})"
                )
                assert counter.drift_evictions == 0
            finally:
                counter.enabled = False
                for _ns in list(counter):
                    _anchor = dict.pop(counter, _ns, None)
                    if _anchor is not None:
                        try:  # noqa: SIM105
                            _anchor.close()
                        except Exception:
                            pass
                ha_mod._FALLBACK_KEEPALIVE = _orig_dict

    def test_drain_guard_reds_on_own_anchor_leftover(self, monkeypatch):
        """#2127 falsifiability control: the drain guard must RED on a leftover
        bound to THIS test's db_path, and stay warn-only for a foreign one."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "wiring.db")
            foreign = os.path.join(tmpdir, "previous-test.db")
            monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
            ha_mod._FALLBACK_KEEPALIVE.clear()

            class _UndrainableCounter(_DriftEvictionCounter):
                def pop(self, key, default=None):  # drain cannot remove
                    return dict.get(self, key)

            own_anchor = TortoiseSDK(db_path=db_path, namespace="registry")
            foreign_anchor = TortoiseSDK(db_path=foreign, namespace="registry")
            try:
                counter = _UndrainableCounter(db_path)
                dict.__setitem__(counter, "registry", own_anchor)
                with pytest.raises(AssertionError, match="drain-completeness"):
                    _drain_drift_counter(counter)

                counter = _UndrainableCounter(db_path)
                dict.__setitem__(counter, "registry", foreign_anchor)
                with pytest.warns(UserWarning, match="non-own anchor"):
                    _drain_drift_counter(counter)
            finally:
                for anchor in (own_anchor, foreign_anchor):
                    try:  # noqa: SIM105
                        anchor.close()
                    except Exception:
                        pass

    def test_drift_counter_ignores_same_path_pop(self, monkeypatch):
        """A pop of a healthy same-path anchor must NOT count as drift
        (the probe-failure/warn-only bucket)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "wiring.db")
            monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
            ha_mod._FALLBACK_KEEPALIVE.clear()
            counter, _orig_dict = _install_drift_counter(db_path)
            try:
                ha_mod._FALLBACK_KEEPALIVE["registry"] = TortoiseSDK(
                    db_path=db_path, namespace="registry"
                )
                ha_mod._FALLBACK_KEEPALIVE.pop("registry", None)
                assert counter.drift_evictions == 0
                assert counter.leaked_evictions == 0
                assert counter.probe_failures == 1  # path equal → probe bucket
            finally:
                counter.enabled = False
                # close the same-path anchor from the counter directly (uncounted)
                for _ns in list(counter):
                    _anchor = dict.pop(counter, _ns, None)
                    if _anchor is not None:
                        try:  # noqa: SIM105
                            _anchor.close()
                        except Exception:
                            pass
                ha_mod._FALLBACK_KEEPALIVE = _orig_dict

# #1719 (Task 3): org_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID literals 22P02 (HTTP 400) under
# FakeControlPlane's fidelity check. user-1 → _U1; registry owner
# "u-owner" → _U2; JWT overrides for non-members → _U3/_U4.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_U2 = "9f2c1a40-0000-4a00-8000-000000000002"
_U3 = "9f2c1a40-0000-4a00-8000-000000000003"
_U4 = "9f2c1a40-0000-4a00-8000-000000000004"
OWNER = _U1


def _enable_supabase(monkeypatch, cp) -> FakeControlPlane:
    """Turn Supabase mode on and inject the fake control plane."""
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return cp


# #2127: the local _patch_tortoise_sdk_init / _restore_sdk_init /
# _close_keepalive_anchors copies are superseded by the shared helper
# tests._http_fixtures.patched_tortoise_sdk (patch → temp DB, #1950
# TORTOISE_DB_PATH pin, close-then-clear at enter; pop-env → restore __init__
# → deterministic anchor close → clear overrides at exit). The file keeps its
# #2090 counter/seed-hold machinery (Task 1-3 additions) and composes it
# around the helper per the drain-linchpin trace in
# docs/scoping/2026-09-02-2127-b-waves-scoping.md.


# ═══════════════════════════════════════════════════════════════════════
# #3472/#3505 — background work armed by `TestClient(app)`, for BOTH fixtures.
# One shared mechanism, deliberately fixture-INDEPENDENT (never copy-pasted):
# `_lifespan` arms this work for EVERY `TestClient(app)` entry, so it is a
# property of opening the app, not of the control-plane mode.
# ═══════════════════════════════════════════════════════════════════════

# #3505/#3546: the embedded construction serialization this file needs lives
# ONCE for the whole session — `tests/_embedded.EMBEDDED_CONSTRUCTION_LOCK`,
# installed by `tests/conftest._serialize_embedded_construction`. It was a
# module-scoped copy here (#3511); that copy could not serialize against the
# one in `tests/test_import_endpoint.py` or cover any other file, which is the
# defect #3546 names. Do NOT re-add a per-file copy.
#
# LANE SCOPE — the serialization is INERT on the lane CI runs this file on.
# Under a supported `TORTOISE_DB_URI` (the docker lane, this file's default)
# every construction from this module REDIRECTS to that server
# (`tortoise/projection/__init__.py`, the #1647 D-1=A test redirect: `path` is
# nulled, so `_is_embedded` is False), no redislite daemon is started, and no
# double-start can occur. `test_export_delete` is NOT in
# `tests._embedded.TEST_NO_REDIRECT_STEMS`, which is what selects that branch.
# What protects THIS file on the docker lane is the BOOT-SWEEP quiesce
# (`_quiesce_testclient_background_work`), not the serialization. The lock is
# live only on the embedded tier-2 / carve-out lane (no URI), where
# constructions stay local-file and real daemons are spawned; it is kept for
# correctness there, not because the docker lane depends on it.


async def _quiet_boot_sweeps() -> None:
    """#3472: no-op stand-in for the lifespan's one-shot `_run_boot_sweeps`.

    Must be `async def`: `_lifespan` arms it with
    `create_task(_run_boot_sweeps())`, so a sync stub would hand
    `create_task` a `None` and raise inside the startup half — turning a
    test-isolation fix into a second, unrelated failure.
    """


def _quiesce_testclient_background_work(monkeypatch) -> None:
    """#3472/#3505: make `TestClient(app)` entry safe for this file's tests.

    `_lifespan` arms background work for EVERY `TestClient(app)` entry,
    regardless of control-plane mode, and all of it touches the same store
    the test body is driving:

    1. `_run_boot_sweeps()` (armed by `_lifespan` as
       `app.state._boot_sweep_task`, one-shot) and `_event_retention_loop()`
       (armed by `_lifespan` as `app.state._event_retention_task`,
       re-armed every `event_retention_interval()`) BOTH call
       `_purge_deleted_teams`. A
       background purge landing between a test's seeding and its own
       `ha_mod._purge_deleted_teams()` call makes both read the row before
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

       #3036 added a THIRD caller to both sites — `_sweep_oauth_retention`
       (a `_run_boot_sweeps` member AND an `_event_retention_loop` call). It
       is benign for THIS file (it touches only the fake control plane), but
       the enumeration above is the guard that makes the next lifespan-armed
       caller visible, so keep it complete: a new sweep reachable from either
       entry point belongs in this list.

       The CALLEE is deliberately not stubbed: this file's tests call
       `ha_mod._purge_deleted_teams()` directly and resolve it off the
       module at call time, so a callee stub would silence the very call
       under test. `_run_boot_sweeps` is stubbed with an `async def` because
       `_lifespan` arms it with `create_task(...)` (see
       `_quiet_boot_sweeps`). The retention interval is pinned beyond any
       test's lifetime: `_event_retention_loop` invokes BOTH `_sweep_events`
       and `_purge_deleted_teams` on the same target, so pinning the
       interval quiesces the loop while leaving the directly-called
       `_purge_deleted_teams` under test.

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
       `tests/conftest._serialize_embedded_construction`) — this file no
       longer installs its own.

       LANE SCOPE — the serialization is INERT on the lane CI runs this file
       on. Under a supported `TORTOISE_DB_URI` (the docker lane, this file's
       default) every construction from this module redirects to that server
       (`tortoise/projection/__init__.py`, the #1647 D-1=A test redirect:
       `path` is nulled, so `_is_embedded` is False), so no redislite daemon
       exists and no double-start is possible. `test_export_delete` is NOT in
       `tests._embedded.TEST_NO_REDIRECT_STEMS`, which is what selects that
       branch. On that lane the protection this file actually gets is item 1
       — the BOOT-SWEEP quiesce, which removes the second caller of
       `_purge_deleted_teams` — and NOT the serialization. The lock is live
       only on the embedded tier-2 / carve-out lane (no URI), where
       constructions stay local-file and real daemons are spawned; it is
       kept for correctness there, not because the docker lane depends on
       it.
    """
    # (1) quiesce both background callers of the purge sweep (the caller, not
    # the callee — `_purge_deleted_teams` itself stays under test).
    monkeypatch.setattr(ha_mod, "_run_boot_sweeps", _quiet_boot_sweeps)
    monkeypatch.setattr(ha_mod, "event_retention_interval",
                        lambda *args, **kwargs: 86400.0)


@pytest.fixture
def sb_client(monkeypatch):
    """Supabase-mode TestClient with a fake control plane + temp DB.

    #3472/#3505: `_quiesce_testclient_background_work` is applied BEFORE the
    app is entered — the same lifespan-armed purge callers and health-probe
    constructor run for this fixture too (the arming is mode-independent),
    and on Supabase mode a boot sweep in the seeding window would run the
    REAL strict drop behind the test's late-installed fault injection.

    #2090: no drift counter here (reg_client only) — supabase-mode authz is
    control-plane-only (no SDK/anchor op before the authz short-circuit in
    the 401/403 tests), so a >=1 RED assert would spuriously red them. The
    pin + close-at-restore still apply (anchors created mid-test via
    _export_graph_snapshot are reused, not evicted).
    """
    _quiesce_testclient_background_work(monkeypatch)
    fake = FakeControlPlane({"organizations": [], "api_keys": [],
                             "org_memberships": [], "invitations": []})
    _enable_supabase(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "export.db")
        # #2127: shared helper — patch __init__ → temp DB, #1950 pin,
        # close-then-clear at enter; pop-env → restore → close → clear
        # overrides at exit. The #2090 counter is reg_client-only; the pin +
        # close-at-restore still apply here.
        with patched_tortoise_sdk(db_path):
            try:
                with TestClient(app) as tc:
                    yield tc, fake, db_path
            finally:
                # sb tests never append to _SEED_SDKS (graph seeds are
                # local-held) — keep for uniform per-test close discipline.
                # Ordering note: seeds close here BEFORE the helper's exit-
                # anchor-close (the helper closes last → still a deterministic
                # SHUTDOWN SAVE; outcome-equivalent to the pre-#2127 order).
                _close_seed_sdks()


@pytest.fixture
def reg_client(monkeypatch):
    """Registry-mode TestClient (TORTOISE_CONTROL_PLANE=registry) + temp DB.

    #3472/#3505: `_quiesce_testclient_background_work` is applied BEFORE the
    tempdir opens — it neutralizes the lifespan-armed background work that
    would otherwise race this fixture's own seeding: a background
    `_purge_deleted_teams` landing between the seed and the test's direct
    call (two `_drop_team_graph` calls, two `team_delete_purged` rows,
    surfacing as `assert ['reg-old', 'reg-old'] == ['reg-old']`), plus the
    health probe's projection construction on the same db file. Neither is
    registry-specific — see the helper — but this is the fixture whose
    exact-count assertions make the race an outright failure.
    """
    _quiesce_testclient_background_work(monkeypatch)
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "export.db")
        # #2127: shared helper (see sb_client) — the anchor is created pinned
        # at TestClient enter (lifespan purge) and REUSED, not evicted.
        with patched_tortoise_sdk(db_path):
            counter, _orig_dict = _install_drift_counter(db_path)
            try:
                # Coupling invariant: expected_db_path must be the path
                # patched_tortoise_sdk force-binds (it patches __init__ to this
                # db_path AND pins TORTOISE_DB_PATH to it, so the pinned env is
                # the observable witness). If the two diverged, this test's own
                # anchor could be mislabelled `leaked` and the gate go green.
                assert _paths_same(counter.expected_db_path, _computed_db_path()), (
                    "drift-counter expected path diverged from the fixture's "
                    f"pinned db_path: {counter.expected_db_path!r} != "
                    f"{_computed_db_path()!r}"
                )
                with TestClient(app) as tc:
                    yield tc, db_path
            finally:
                # ══ #2090 teardown (pinned — runs on body-failure paths too) ══
                try:
                    # G3: zero mid-test drift evictions — the anchor is reused
                    # (path pinned), never evicted. ⚠️ This assert runs with the
                    # counter ENABLED — moving enabled=False ahead of it would
                    # silently vacate the #2090 proof.
                    assert counter.drift_evictions == _EXPECTED_DRIFT_EVICTIONS, (
                        f"expected {_EXPECTED_DRIFT_EVICTIONS} drift evictions, "
                        f"got {counter.drift_evictions} "
                        f"(probe-failures: {counter.probe_failures}, "
                        f"leaked: {counter.leaked_evictions}, "
                        f"unclassified: {counter.unclassified})"
                    )
                    # #2090: the enter-pin probe-failure churn rate must be
                    # OBSERVABLE (warn-only — never fails the gate; a healthy
                    # pinned run should read 0, but a transient probe failure
                    # on a loaded runner must not red it). Surfaces in the
                    # pytest warnings summary.
                    if (counter.probe_failures or counter.leaked_evictions
                            or counter.unclassified):
                        warnings.warn(
                            f"[#2090] keepalive probe-failure pops: "
                            f"{counter.probe_failures}, leaked (other-test "
                            f"anchors): {counter.leaked_evictions}, "
                            f"unclassified: {counter.unclassified} (drift: "
                            f"{counter.drift_evictions})",
                            UserWarning,
                            stacklevel=2,
                        )
                finally:
                    counter.enabled = False  # restore-time pops must never count
                    # A background worker may hold a stale reference to this
                    # counter; forward_to routes its late write into the real
                    # dict, which patched_tortoise_sdk's exit-close drains.
                    # Lock-free on purpose: teardown must not block on
                    # _KEEPALIVE_LOCK held by a slow in-flight construction.
                    counter.forward_to = _orig_dict
                    ha_mod._FALLBACK_KEEPALIVE = _orig_dict
                    try:
                        # #2127: the fixture owns the deterministic close of the
                        # anchors the counter tracked; the helper REDs on a
                        # leftover bound to THIS test's db_path and warns on a
                        # cross-test one.
                        _drain_drift_counter(counter)
                    finally:
                        _close_seed_sdks()  # after anchor close (last-client SAVE)


@pytest.fixture
def as_user():
    """Override get_current_user per test (JWT session user)."""

    def _set(user_id: str = OWNER):
        app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}

    yield _set
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def capture_audit(monkeypatch):
    """Capture _audit_logger.append calls (no Postgres/JSONL in tests).

    AuditLogger.append is called with positional org_id/actor/operation by
    the purge sweep and with kwargs by _async_audit — normalize both into
    the kwargs dict."""
    captured: list[dict] = []
    _POSITIONAL = ("org_id", "actor_user_id", "operation",
                   "resource_type", "resource_id", "ip_address", "user_agent")

    def _capture(*args, **kwargs):
        for i, a in enumerate(args):
            if i < len(_POSITIONAL):
                kwargs[_POSITIONAL[i]] = a
        captured.append(kwargs)

    monkeypatch.setattr(ha_mod._audit_logger, "append", _capture)
    return captured


# ── Seeding helpers ─────────────────────────────────────────────────────────


def _seed_supabase_team(fake, *, role: str = "owner", deleted_at: str | None = None,
                        with_key: bool = True, with_invite: bool = False):
    team = dict(FREE_TEAM)
    if deleted_at:
        team["deleted_at"] = deleted_at
    fake.seed("organizations", [team])
    fake.seed("org_memberships", [_membership_row(role=role)])
    if with_key:
        fake.seed("api_keys", [_key_row()])
    if with_invite:
        fake.seed("invitations", [{
            "id": "inv-1", "org_id": ORG_ID, "email": "bob@example.com",
            "role": "member", "status": "pending", "expires_at": None,
        }])


def _seed_graph(db_path: str, org_id: str = ORG_ID, *,
                n_points: int = 2, n_events: int = 1) -> TortoiseSDK:
    """Seed the team's FalkorDB graph: points + a Tag + a TAGGED edge + events.

    Returns the SDK — the caller MUST keep the returned reference alive until
    the export/read that follows: with TORTOISE_FAST_ATEXIT=1 (tests/conftest)
    and the #1475 close-on-GC finalizer, the seed SDK going out of scope fires
    SHUTDOWN NOSAVE on the server, so a later read on the same path either
    reconnects to a dead socket or a fresh empty DB (redis.socket
    ConnectionError / 0 nodes — the test-isolation flake class).
    """
    sdk = TortoiseSDK(db_path, namespace=org_id)
    g = sdk._get_proj().g
    for i in range(n_points):
        g.query(
            "CREATE (p:Point {id:$id, content:$c, pointKind:'claim', confidence:0.8})",
            params={"id": f"pt-{i}", "c": f"content {i}"},
        )
    g.query("CREATE (t:Tag {id:'tag-1', name:'alpha'})")
    g.query(
        "MATCH (p:Point {id:'pt-0'}), (t:Tag {id:'tag-1'}) CREATE (p)-[:TAGGED]->(t)"
    )
    for i in range(n_events):
        g.query(
            "CREATE (e:GraphEvent {seq:$s, ts:$ts, type:'point_create', "
            "event_id:$eid, payload:$p})",
            params={"s": i + 1, "ts": "2026-08-01T00:00:00Z",
                    "eid": f"ev-{i}", "p": '{"id":"pt-0"}'},
        )
    return sdk  # caller keeps this alive until the export reads the graph


def _seed_registry(db_path: str, org_id: str = "reg-team-1", *,
                   deleted_at: str | None = None) -> None:
    """Seed registry Team + owner Membership + APIKey (+ optional deleted_at).

    #2090: the SDK is appended to _SEED_SDKS (suspension_parity precedent) so
    the #1475 close-on-GC finalizer can never SHUTDOWN NOSAVE the shared
    embedded server when this helper returns (dropped-SDK data loss → empty
    respawn → flaky registry-mode 403s).
    """
    sdk = TortoiseSDK(db_path, namespace="registry")
    _SEED_SDKS.append(sdk)
    reg = sdk._get_registry()
    reg.query("CREATE (t:Team {id:$id, name:$name, tier:'free'})",
              params={"id": org_id, "name": org_id})
    reg.query(
        # user_id mirrors _U2 (9f2c1a40-...-0002) — registry Membership
        # user_id is the same uuid column as org_memberships (#1719 T3).
        "CREATE (m:Membership {id:'m-1', user_id:'9f2c1a40-0000-4a00-8000-000000000002', org_id:$tid, "
        "role:'owner', status:'active', joined_at:'2026-08-01T00:00:00Z'})",
        params={"tid": org_id},
    )
    reg.query(
        "CREATE (k:APIKey {id:'k-1', org_id:$tid, key_hash:'h', "
        "key_prefix:'reg-team', revoked_at:null})",
        params={"tid": org_id},
    )
    if deleted_at:
        reg.query("MATCH (t:Team {id:$id}) SET t.deleted_at=$d",
                  params={"id": org_id, "d": deleted_at})


def _registry_count(db_path: str, label: str, org_id: str) -> int:
    """Count registry nodes of `label` scoped to a team. Team nodes key on
    `id`; Membership/APIKey/Invitation key on `org_id`.

    #2090: hold the read SDK in _SEED_SDKS (same dropped-SDK class as
    _seed_registry) — closed deterministically by the fixture teardown.
    """
    prop = "id" if label == "Team" else "org_id"
    sdk = TortoiseSDK(db_path, namespace="registry")
    _SEED_SDKS.append(sdk)
    rows = sdk._get_registry().query(
        f"MATCH (n:{label} {{{prop}:$tid}}) RETURN count(n)",
        params={"tid": org_id},
    ).result_set
    return int(rows[0][0]) if rows else 0


# ═══════════════════════════════════════════════════════════════════════════
# GET /v1/organizations/{org_id}/export — Supabase mode
# ═══════════════════════════════════════════════════════════════════════════


class TestExportSupabase:
    def test_export_requires_session_auth(self, sb_client):
        tc, _, _ = sb_client
        r = tc.get(f"/v1/organizations/{ORG_ID}/export")
        assert r.status_code == 401

    def test_export_requires_owner(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_supabase_team(fake, role="member")
        as_user()
        r = tc.get(f"/v1/organizations/{ORG_ID}/export")
        assert r.status_code == 403
        assert "owner" in r.json()["detail"]

    def test_export_admin_denied(self, sb_client, as_user):
        """Strict owner — admin can manage members but cannot export."""
        tc, fake, _ = sb_client
        _seed_supabase_team(fake, role="admin")
        as_user()
        assert tc.get(f"/v1/organizations/{ORG_ID}/export").status_code == 403

    def test_export_unknown_team_403(self, sb_client, as_user):
        """AuthZ-first: a non-member gets 403 for an unknown team (no
        existence oracle — security review, PR #873)."""
        tc, _, _ = sb_client
        as_user()
        r = tc.get("/v1/organizations/nope/export")
        assert r.status_code == 403

    def test_export_deleted_team_410(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_supabase_team(
            fake, deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        as_user()
        r = tc.get(f"/v1/organizations/{ORG_ID}/export")
        assert r.status_code == 410

    def test_export_deleted_team_non_owner_403(self, sb_client, as_user):
        """Non-owner probing a deleted team gets 403, not the 410/deletion
        schedule (no info disclosure — security review, PR #873)."""
        tc, fake, _ = sb_client
        _seed_supabase_team(
            fake, role="member", deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        as_user()
        r = tc.get(f"/v1/organizations/{ORG_ID}/export")
        assert r.status_code == 403

    def test_export_events_truncated(self, sb_client, as_user, monkeypatch):
        """Event cap: newest-by-seq window kept + truncation flags."""
        monkeypatch.setattr(ha_mod, "_EXPORT_MAX_EVENTS", 3)
        tc, fake, db_path = sb_client
        _seed_supabase_team(fake)
        seed_sdk = _seed_graph(db_path, n_events=5)  # noqa: F841
        as_user()
        r = tc.get(f"/v1/organizations/{ORG_ID}/export")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["events"] == 3
        assert body["summary"]["events_total"] == 5
        assert body["summary"]["events_truncated"] is True
        # newest-by-seq kept (traversal order is unspecified — sorted)
        seqs = sorted(e["seq"] for e in body["events"])
        assert seqs == [3, 4, 5]

    def test_export_happy_path(self, sb_client, as_user):
        tc, fake, db_path = sb_client
        _seed_supabase_team(fake)
        seed_sdk = _seed_graph(db_path)  # noqa: F841
        as_user()
        r = tc.get(f"/v1/organizations/{ORG_ID}/export")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["schema_version"] == 1
        assert body["org_id"] == ORG_ID
        assert body["exported_at"]
        # summary: 2 points + 1 Tag + 1 GraphEvent, 1 TAGGED edge
        assert body["summary"]["points"] == 2
        assert body["summary"]["entities"] == 1
        assert body["summary"]["edges"] == 1
        assert body["summary"]["events"] == 1
        assert body["summary"]["nodes"] == 4
        # full point data incl. confidence scores + kind mapping
        pts = {p["id"]: p for p in body["points"]}
        assert pts["pt-0"]["content"] == "content 0"
        assert pts["pt-0"]["kind"] == "claim"
        assert pts["pt-0"]["confidence"] == 0.8
        assert "pointKind" not in pts["pt-0"]
        # entity node
        assert any("Tag" in e["labels"] and e["name"] == "alpha"
                   for e in body["entities"])
        # edge with source/target/type
        edge = body["edges"][0]
        assert edge["source"] == "pt-0" and edge["target"] == "tag-1"
        assert edge["type"] == "TAGGED"
        # event payload decoded to a dict
        assert body["events"][0]["payload"] == {"id": "pt-0"}
        # control-plane metadata: team row, members, plan
        assert body["team"]["id"] == ORG_ID
        assert body["team"]["tier"] == "free"
        assert body["members"][0]["role"] == "owner"
        assert body["members"][0]["user_id"] == OWNER
        assert body["plan"]["tier"] == "free"
        assert "limits" in body["plan"]

    def test_export_audited(self, sb_client, as_user, capture_audit):
        tc, fake, db_path = sb_client
        _seed_supabase_team(fake)
        seed_sdk = _seed_graph(db_path)  # noqa: F841
        as_user()
        r = tc.get(f"/v1/organizations/{ORG_ID}/export")
        assert r.status_code == 200
        ops = [e["operation"] for e in capture_audit]
        assert "team_export" in ops
        event = next(e for e in capture_audit if e["operation"] == "team_export")
        assert event["actor_user_id"] == OWNER
        assert event["org_id"] == ORG_ID
        assert event["resource_type"] == "team"


# ═══════════════════════════════════════════════════════════════════════════
# DELETE /v1/organizations/{org_id} — Supabase mode
# ═══════════════════════════════════════════════════════════════════════════


class TestDeleteSupabase:
    def test_delete_requires_session_auth(self, sb_client):
        tc, _, _ = sb_client
        r = tc.delete(f"/v1/organizations/{ORG_ID}")
        assert r.status_code == 401

    def test_delete_requires_owner(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_supabase_team(fake, role="admin")  # admin ≠ owner
        as_user()
        r = tc.delete(f"/v1/organizations/{ORG_ID}")
        assert r.status_code == 403

    def test_delete_unknown_team_403(self, sb_client, as_user):
        """AuthZ-first: unknown team → 403 for non-members (no oracle)."""
        tc, _, _ = sb_client
        as_user()
        assert tc.delete("/v1/organizations/nope").status_code == 403

    def test_delete_cascade(self, sb_client, as_user, capture_audit):
        """Soft delete: deleted_at stamp + keys revoked + memberships
        removed + invitations revoked, audited with the acting user."""
        tc, fake, _ = sb_client
        _seed_supabase_team(fake, with_invite=True)
        as_user()
        r = tc.delete(f"/v1/organizations/{ORG_ID}")
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "delete_scheduled"
        assert body["org_id"] == ORG_ID
        assert body["grace_hours"] == RESTORE_WINDOW_HOURS
        assert body["deleted_at"]
        assert body["hard_delete_after"] > body["deleted_at"]

        by_id = {row["id"]: row for row in fake.tables["organizations"]}
        assert by_id[ORG_ID]["deleted_at"] == body["deleted_at"]
        assert by_id[ORG_ID]["grace_hours"] == RESTORE_WINDOW_HOURS  # persisted promise
        assert fake.tables["api_keys"][0]["revoked_at"] == body["deleted_at"]
        assert fake.tables["org_memberships"][0]["status"] == "removed"
        assert fake.tables["invitations"][0]["status"] == "revoked"

        ops = [e["operation"] for e in capture_audit]
        assert "team_delete_requested" in ops
        event = next(e for e in capture_audit
                     if e["operation"] == "team_delete_requested")
        assert event["actor_user_id"] == OWNER
        assert event["org_id"] == ORG_ID

    def test_delete_revokes_key_auth_fail_closed(self, sb_client, as_user):
        """After delete, the team's tt_ keys stop authenticating (401)."""
        tc, fake, _ = sb_client
        _seed_supabase_team(fake)
        as_user()
        assert tc.delete(f"/v1/organizations/{ORG_ID}").status_code == 202
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 401

    def test_delete_replay_non_owner_403(self, sb_client, as_user):
        """Idempotent replay is owner-gated too — a non-owner probing a
        delete-pending team gets 403, never the deletion schedule."""
        tc, fake, _ = sb_client
        _seed_supabase_team(fake, deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        as_user()
        # owner replay still works (removed-owner state accepted)
        r = tc.delete(f"/v1/organizations/{ORG_ID}")
        assert r.status_code == 200
        assert r.json()["already"] is True
        # non-owner → 403
        app.dependency_overrides[get_current_user] = lambda: {"user_id": _U4}
        r2 = tc.delete(f"/v1/organizations/{ORG_ID}")
        assert r2.status_code == 403

    def test_delete_idempotent(self, sb_client, as_user, capture_audit):
        tc, fake, _ = sb_client
        _seed_supabase_team(fake)
        as_user()
        first = tc.delete(f"/v1/organizations/{ORG_ID}")
        assert first.status_code == 202
        second = tc.delete(f"/v1/organizations/{ORG_ID}")
        assert second.status_code == 200
        body = second.json()
        assert body["already"] is True
        assert body["status"] == "delete_pending"
        assert body["deleted_at"] == first.json()["deleted_at"]
        # no duplicate delete_requested audit event
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_requested") == 1


# ═══════════════════════════════════════════════════════════════════════════
# #1903 — dashboard-created teams (POST /v1/organizations): stored graph_name must
# equal the data-plane namespace (org_{org_id}) so export/delete resolve
# the REAL graph. The old mint (org_{name}) made export empty and delete
# orphan the real graph.
# ═══════════════════════════════════════════════════════════════════════════


class TestDashboardCreatedTeamRoundTrip:
    def test_dashboard_created_team_export_returns_points(self, sb_client, as_user):
        """#1903 Indicator 1+2: POST /v1/organizations mints graph_name=org_{org_id}
        and a dashboard-created team's export returns its points (the stored
        name resolves the real data graph)."""
        tc, fake, db_path = sb_client
        as_user()
        r = tc.post("/v1/organizations", json={"name": "acme"})
        assert r.status_code == 200, r.text
        body = r.json()
        org_id = body["org_id"]
        assert body["graph_name"] == f"org_{org_id}"  # Indicator 1
        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_graph_name"] == f"org_{org_id}"
        # data-plane write (the real write path: namespace=org_id)
        seed_sdk = _seed_graph(db_path, org_id=org_id, n_points=1, n_events=0)  # noqa: F841
        r2 = tc.get(f"/v1/organizations/{org_id}/export")
        assert r2.status_code == 200, r2.text
        assert r2.json()["summary"]["points"] == 1  # Indicator 2

    def test_dashboard_created_team_delete_drops_org_id_graph(self, sb_client, as_user, monkeypatch, capture_audit):
        """#1903 Indicator 3: delete of a dashboard-created team targets the
        org_{org_id} graph (the old org_{name} stored name orphaned it).
        The _drop_team_graph_strict spy is the mechanism proof — the
        assertion is on the CORRECT TARGET passed to the drop."""
        tc, fake, _ = sb_client
        as_user()
        # env must be 0 BEFORE delete — soft_delete stamps the STORED
        # grace_hours and the purge honors stored grace over env
        # (_past_grace): a 7-day stamp would skip the just-deleted team.
        monkeypatch.setenv("TORTOISE_TEAM_DELETE_GRACE_HOURS", "0")
        r = tc.post("/v1/organizations", json={"name": "acme"})
        assert r.status_code == 200, r.text
        org_id = r.json()["org_id"]
        assert r.json()["graph_name"] == f"org_{org_id}"
        dropped = []
        monkeypatch.setattr(ha_mod, '_drop_org_graph_strict',
                            lambda tid, gn=None: dropped.append((tid, gn)))
        r = tc.delete(f"/v1/organizations/{org_id}")
        assert r.status_code == 202, r.text
        assert r.json()["grace_hours"] == 0  # env->stored promise pinned
        ha_mod._purge_deleted_orgs()
        # exactly one drop, exactly the org_{org_id} target (suite precedent:
        # TestPurge asserts strict equality on the captured drop list)
        assert dropped == [(org_id, f"org_{org_id}")]  # Indicator 3
        assert not any(t["id"] == org_id for t in fake.tables["organizations"])
        ops = [e["operation"] for e in capture_audit]
        assert "team_delete_purged" in ops


# ═══════════════════════════════════════════════════════════════════════════
# Same surface — registry mode (selfhost control plane)
# ═══════════════════════════════════════════════════════════════════════════


class TestExportDeleteRegistry:
    def test_export_happy_path_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        seed_sdk = _seed_graph(db_path, org_id="reg-team-1")  # noqa: F841
        as_user(user_id=_U2)
        r = tc.get("/v1/organizations/reg-team-1/export")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["points"] == 2
        assert body["summary"]["edges"] == 1
        assert body["members"][0]["role"] == "owner"
        assert body["members"][0]["joined_at"] == "2026-08-01T00:00:00Z"

    def test_export_requires_owner_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        # #2090: pin the seed — this test asserts 403 for a non-member and
        # would pass VACUOUSLY on an empty registry (the seed silently lost
        # to a daemon respawn). Fail loud if the seed didn't land (#1950
        # self-verify pattern).
        assert _registry_count(db_path, "Team", "reg-team-1") == 1
        as_user(user_id=_U3)  # no membership at all
        assert tc.get("/v1/organizations/reg-team-1/export").status_code == 403

    def test_export_uses_stored_graph_name(self, reg_client, as_user):
        """Teams created via sdk.team_create store graph_name=org_{name} —
        export must read THAT graph, not org_{id} (code-review P1, #873)."""
        tc, db_path = reg_client
        sdk = TortoiseSDK(db_path, namespace="registry")
        reg = sdk._get_registry()
        reg.query(
            "CREATE (t:Team {id:'reg-named', name:'Acme', tier:'free', "
            "graph_name:'org_Acme'})"
        )
        reg.query(
            "CREATE (m:Membership {id:'m-2', user_id:'9f2c1a40-0000-4a00-8000-000000000002', "
            "org_id:'reg-named', role:'owner', status:'active'})"
        )
        seed_sdk = _seed_graph(db_path, org_id="Acme", n_points=1, n_events=0)  # noqa: F841
        as_user(user_id=_U2)
        r = tc.get("/v1/organizations/reg-named/export")
        assert r.status_code == 200, r.text
        assert r.json()["summary"]["points"] == 1

    def test_export_deleted_team_410_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path, deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        as_user(user_id=_U2)
        r = tc.get("/v1/organizations/reg-team-1/export")
        assert r.status_code == 410

    def test_delete_cascade_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        # pending invitation must be revoked too (registry branch)
        sdk = TortoiseSDK(db_path, namespace="registry")
        sdk._get_registry().query(
            "CREATE (i:Invitation {id:'inv-r', org_id:'reg-team-1', "
            "email:'bob@example.com', role:'member', status:'pending'})"
        )
        as_user(user_id=_U2)
        r = tc.delete("/v1/organizations/reg-team-1")
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "delete_scheduled"

        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (t:Team {id:'reg-team-1'}) RETURN t.deleted_at, t.grace_hours"
        ).result_set
        assert rows and rows[0][0]  # deleted_at stamped
        assert rows[0][1] == RESTORE_WINDOW_HOURS  # persisted grace promise
        assert _registry_count(db_path, "APIKey", "reg-team-1") == 1
        rev = reg.query(
            "MATCH (k:APIKey {org_id:'reg-team-1'}) RETURN k.revoked_at"
        ).result_set
        assert rev and rev[0][0] is not None
        mem = reg.query(
            "MATCH (m:Membership {org_id:'reg-team-1'}) RETURN m.status"
        ).result_set
        assert mem and mem[0][0] == "removed"
        inv = reg.query(
            "MATCH (i:Invitation {org_id:'reg-team-1'}) RETURN i.status"
        ).result_set
        assert inv and inv[0][0] == "revoked"

    def test_delete_idempotent_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        as_user(user_id=_U2)
        assert tc.delete("/v1/organizations/reg-team-1").status_code == 202
        second = tc.delete("/v1/organizations/reg-team-1")
        assert second.status_code == 200
        assert second.json()["already"] is True

    def test_delete_revokes_key_auth_registry(self, reg_client, as_user):
        """Registry-mode key auth fails closed after delete (401)."""
        tc, db_path = reg_client
        # Registry mode resolves tt_ keys via registry APIKey nodes — seed a
        # key whose hash verifies against TOKEN, then delete the team.
        sdk = TortoiseSDK(db_path, namespace="registry")
        from tortoise.auth import hash_api_key
        reg = sdk._get_registry()
        reg.query("CREATE (t:Team {id:'reg-team-1', name:'reg-team-1'})")
        reg.query(
            "CREATE (k:APIKey {id:'k-1', org_id:'reg-team-1', "
            "key_prefix:$pfx, key_hash:$hash, revoked_at:null})",
            params={"pfx": TOKEN[:10], "hash": hash_api_key(TOKEN)},
        )
        reg.query(
            "CREATE (m:Membership {id:'m-1', user_id:'9f2c1a40-0000-4a00-8000-000000000002', "
            "org_id:'reg-team-1', role:'owner', status:'active'})"
        )
        as_user(user_id=_U2)
        assert tc.delete("/v1/organizations/reg-team-1").status_code == 202
        r = tc.get("/v1/team", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Post-grace purge (hard delete)
# ═══════════════════════════════════════════════════════════════════════════


class TestPurge:
    def test_purge_hard_deletes_past_grace_registry(self, reg_client,
                                                    capture_audit, monkeypatch):
        tc, db_path = reg_client  # noqa: RUF059
        past = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()  # noqa: UP017
        _seed_registry(db_path, org_id="reg-old", deleted_at=past)
        _seed_registry(db_path, org_id="reg-recent",
                       deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        # wiring check: the graph drop is invoked for the purged team only
        dropped: list[str] = []
        monkeypatch.setattr(ha_mod, '_drop_org_graph',
                            lambda org_id, graph_name=None: dropped.append(org_id))

        ha_mod._purge_deleted_orgs()

        assert _registry_count(db_path, "Team", "reg-old") == 0
        assert _registry_count(db_path, "Membership", "reg-old") == 0
        assert _registry_count(db_path, "APIKey", "reg-old") == 0
        # within grace → untouched
        assert _registry_count(db_path, "Team", "reg-recent") == 1
        assert dropped == ["reg-old"]  # never the within-grace team
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_purged") == 1
        assert capture_audit[-1]["org_id"] == "reg-old"

    def test_purge_honors_stored_grace(self, reg_client, capture_audit,
                                       monkeypatch):
        """A config change mid-grace must not hard-delete before the
        promised hard_delete_after (code-review P1, PR #873): env shrinks
        to 1h but the team was promised 24h 10h ago → NOT purged."""
        monkeypatch.setenv("TORTOISE_TEAM_DELETE_GRACE_HOURS", "1")
        tc, db_path = reg_client  # noqa: RUF059
        ten_hours = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()  # noqa: UP017
        _seed_registry(db_path, org_id="reg-promised", deleted_at=ten_hours)
        sdk = TortoiseSDK(db_path, namespace="registry")
        sdk._get_registry().query(
            "MATCH (t:Team {id:'reg-promised'}) SET t.grace_hours=24"
        )
        _seed_registry(db_path, org_id="reg-env-old",
                       deleted_at=ten_hours)  # no stored grace → env 1h

        ha_mod._purge_deleted_orgs()

        assert _registry_count(db_path, "Team", "reg-promised") == 1  # kept
        assert _registry_count(db_path, "Team", "reg-env-old") == 0  # purged

    def test_purge_does_not_defer_org_past_stored_grace(
            self, reg_client, capture_audit, monkeypatch):
        """#4179 P1 — grow-direction twin of ``test_purge_honors_stored_grace``.

        A legacy in-flight org deleted under the old 24h default (stored
        ``grace_hours=24``) 30h ago is past its OWN disclosed
        ``hard_delete_after``. Raising the env default to 168h must NOT hold
        it until 168h: the env cutoff is a fetch superset, never a pre-filter
        of the stored promise."""
        monkeypatch.setenv("TORTOISE_TEAM_DELETE_GRACE_HOURS", "168")
        tc, db_path = reg_client  # noqa: RUF059
        thirty_hours = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()  # noqa: UP017
        _seed_registry(db_path, org_id="reg-legacy", deleted_at=thirty_hours)
        sdk = TortoiseSDK(db_path, namespace="registry")
        sdk._get_registry().query(
            "MATCH (t:Team {id:'reg-legacy'}) SET t.grace_hours=24"
        )
        # control: no stored grace → the env fallback (168h) still applies.
        _seed_registry(db_path, org_id="reg-env-recent",
                       deleted_at=thirty_hours)

        ha_mod._purge_deleted_orgs()

        assert _registry_count(db_path, "Team", "reg-legacy") == 0
        assert _registry_count(db_path, "Team", "reg-env-recent") == 1

    def test_purge_deletes_rows_past_grace_supabase(self, sb_client,
                                                    capture_audit):
        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()  # noqa: UP017
        recent = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        fake.seed("organizations", [
            dict(FREE_TEAM, deleted_at=past),
            dict(FREE_TEAM, id="team-recent", deleted_at=recent),
        ])
        fake.seed("org_memberships", [
            _membership_row(),                       # team-free-001 (old)
            _membership_row(org_id="team-recent"),  # within grace
        ])
        fake.seed("api_keys", [
            _key_row(),                              # team-free-001 (old)
            _key_row(org_id="team-recent"),         # within grace
        ])
        fake.seed("invitations", [{
            "id": "inv-1", "org_id": ORG_ID, "email": "bob@example.com",
            "role": "member", "status": "pending", "expires_at": None,
        }])

        ha_mod._purge_deleted_orgs()

        # team-free-001 control-plane rows hard-deleted (all tables)
        assert all(r["id"] != ORG_ID for r in fake.tables["organizations"])
        assert all(r["org_id"] != ORG_ID for r in fake.tables["api_keys"])
        assert all(r["org_id"] != ORG_ID
                   for r in fake.tables["org_memberships"])
        assert fake.tables["invitations"] == []
        # within-grace team survives
        assert any(r["id"] == "team-recent" for r in fake.tables["organizations"])
        ops = [e["operation"] for e in capture_audit]
        assert "team_delete_purged" in ops

    def test_purge_keeps_row_anchor_on_graph_drop_failure_supabase(
            self, sb_client, capture_audit, monkeypatch):
        """#926: a silent graph-drop failure must not orphan the FalkorDB
        graph. The Supabase-mode drop is strict — on failure the sweep
        skips the team, the teams row survives as the retry anchor
        (control-plane rows untouched, no purge audit event), and the
        next sweep retries the drop to completion."""
        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()  # noqa: UP017
        fake.seed("organizations", [dict(FREE_TEAM, deleted_at=past),
                             dict(FREE_TEAM, id="team-other",
                                  deleted_at=past)])
        fake.seed("org_memberships", [_membership_row(),
                                        _membership_row(org_id="team-other")])
        fake.seed("api_keys", [_key_row(),
                                _key_row(org_id="team-other")])

        def _flaky(org_id, graph_name=None):
            if org_id == ORG_ID:
                raise RuntimeError("graph drop failed (fault injection, #926)")
            return None

        monkeypatch.setattr(ha_mod, '_drop_org_graph_strict', _flaky)

        ha_mod._purge_deleted_orgs()

        # retry anchor survives: teams row + child rows NOT purged
        assert any(r["id"] == ORG_ID for r in fake.tables["organizations"])
        assert any(r["org_id"] == ORG_ID for r in fake.tables["api_keys"])
        assert any(r["org_id"] == ORG_ID
                   for r in fake.tables["org_memberships"])
        # ...and a failed drop never blocks OTHER past-grace teams
        assert all(r["id"] != "team-other" for r in fake.tables["organizations"])
        assert all(r["org_id"] != "team-other"
                   for r in fake.tables["api_keys"])
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_purged") == 1
        assert capture_audit[-1]["org_id"] == "team-other"

        # next sweep (drop healed, real strict impl) → row purged
        monkeypatch.setattr(ha_mod, '_drop_org_graph_strict',
                            ha_mod._drop_org_graph_impl)
        ha_mod._purge_deleted_orgs()
        assert all(r["id"] != ORG_ID for r in fake.tables["organizations"])
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_purged") == 2

    def test_purge_unparseable_stamp_is_purged(self, sb_client,
                                               capture_audit):
        """#4029 FIX 4.1 (org path): a corrupt (unparseable) ``deleted_at``
        must be PURGED, not skipped — a corrupt row must never pin its graph
        and control-plane rows forever.

        RED condition: ``_stored_grace_elapsed``'s defensive except returns
        False, so the unparseable org is skipped and never purged. MUTATION
        CONFIRMED RED: change ``return True`` to ``return False`` in
        ``_stored_grace_elapsed``'s ``except Exception`` — the org row and its
        graph survive the sweep and no ``team_delete_purged`` is emitted.

        The stamp is ``"!!!"`` deliberately: the sweep's ``deleted_at lte
        now`` prefilter is a lexicographic string compare, and ``'!' < '2'``
        makes the row reach ``_stored_grace_elapsed`` (an ISO-shaped-but-bad
        stamp would be filtered out before the defensive branch runs).
        """
        tc, fake, _ = sb_client  # noqa: RUF059
        fake.seed("organizations", [dict(FREE_TEAM, deleted_at="!!!",
                                     grace_hours=1)])
        fake.seed("org_memberships", [_membership_row()])
        fake.seed("api_keys", [_key_row()])
        fake.seed("invitations", [{
            "id": "inv-1", "org_id": ORG_ID, "email": "bob@example.com",
            "role": "member", "status": "pending", "expires_at": None,
        }])

        ha_mod._purge_deleted_orgs()

        assert all(r["id"] != ORG_ID for r in fake.tables["organizations"])
        assert all(r["org_id"] != ORG_ID for r in fake.tables["api_keys"])
        assert fake.tables["invitations"] == []
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_purged") == 1


class TestDropTeamGraphImplCloudShape:
    """#2163 regression: _drop_team_graph_impl must issue GRAPH.DELETE on a
    cloud-shaped client (falkordb.FalkorDB — has select_graph, NO
    delete_graph attr). The old hasattr(delete_graph) probe was false on
    FalkorDB Cloud, so the purge sweep silently skipped every drop and
    orphaned the graph after the teams row was deleted (no retry — the
    #926 retry-anchor design broke)."""

    def test_impl_drops_via_select_graph_when_delete_graph_absent(self, monkeypatch):
        dropped = []

        class FakeGraph:
            def __init__(self, name):
                self._name = name

            def delete(self):
                dropped.append(self._name)  # GRAPH.DELETE fires

        class FakeDB:
            """Cloud-shaped: select_graph present, delete_graph ABSENT."""

            def select_graph(self, name):
                return FakeGraph(name)

        db = FakeDB()
        proj = type("FakeProj", (), {"db": db})()
        fake_sdk = type("FakeSDK", (), {"_get_proj": lambda self: proj})()
        monkeypatch.setattr(ha_mod, "_make_sdk", lambda namespace: fake_sdk)

        # graph_name wins; the default org_{org_id} fallback also drops
        ha_mod._drop_org_graph_impl("team-abc", "team_abc000000000000000000000")
        ha_mod._drop_org_graph_impl("team-xyz")

        # the pre-#2163 code called NOTHING on this client (hasattr probe
        # false) — the regression pin is that both drops actually fired
        assert not hasattr(db, "delete_graph"), \
            "fixture must mirror the pip falkordb client (no delete_graph)"
        assert dropped == [
            "team_abc000000000000000000000", "org_team-xyz"]

    def test_strict_drop_raises_when_graph_delete_fails(self, monkeypatch):
        """#926 retry-anchor contract: _drop_team_graph_strict propagates a
        GENUINE GRAPH.DELETE failure (auth/connection) so the purge sweep
        keeps the teams row — but treats an absent-graph raise as success
        (#2163 re-review P0) so the anchor converges."""
        class BoomDB:
            def select_graph(self, name):
                raise RuntimeError("GRAPH.DELETE failed (connection)")

        proj = type("FakeProj", (), {"db": BoomDB()})()
        fake_sdk = type("FakeSDK", (), {"_get_proj": lambda self: proj})()
        monkeypatch.setattr(ha_mod, "_make_sdk", lambda namespace: fake_sdk)

        with pytest.raises(RuntimeError, match=r"GRAPH\.DELETE failed"):
            ha_mod._drop_org_graph_strict("team-abc", "team_abc000000000000000000000")
        # best-effort variant swallows the same failure
        ha_mod._drop_org_graph("team-abc", "team_abc000000000000000000000")

    def test_strict_drop_converges_on_absent_graph(self, monkeypatch):
        """#2163 re-review P0: GRAPH.DELETE on an already-dropped graph
        raises 'Invalid graph operation on empty key' (v4.16.7) — the strict
        drop must treat that as SUCCESS so the sweep's retry anchor does not
        keep a team row poisoned forever after the graph is already gone."""
        from redis.exceptions import ResponseError

        class AbsentDB:
            def select_graph(self, name):
                g = type("G", (), {})()

                def _delete():
                    raise ResponseError("Invalid graph operation on empty key")

                g.delete = _delete
                return g

        proj = type("FakeProj", (), {"db": AbsentDB()})()
        fake_sdk = type("FakeSDK", (), {"_get_proj": lambda self: proj})()
        monkeypatch.setattr(ha_mod, "_make_sdk", lambda namespace: fake_sdk)

        # strict drop must NOT raise on the absent-graph family
        ha_mod._drop_org_graph_strict("team-abc", "team_abc000000000000000000000")
        ha_mod._drop_org_graph("team-abc", "team_abc000000000000000000000")


# ═══════════════════════════════════════════════════════════════════════════
# Sensitive-op rate limits
# ═══════════════════════════════════════════════════════════════════════════


class TestSensitiveRateLimit:
    def test_team_delete_rate_limited(self, sb_client, monkeypatch, as_user):
        """Per-IP hourly budget (5/h): 6th delete request → 429."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        ha_mod._SENSITIVE_BUCKETS.clear()
        tc, _, _ = sb_client
        as_user()
        for _ in range(5):
            r = tc.delete("/v1/organizations/nope")  # unknown team → 403, not 429
            assert r.status_code == 403
        r = tc.delete("/v1/organizations/nope")
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        ha_mod._SENSITIVE_BUCKETS.clear()

    def test_export_rate_limited_independently(self, sb_client, monkeypatch,
                                               as_user):
        """Export has its own budget; delete calls don't consume it
        (per-op keying)."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setitem(ha_mod._SENSITIVE_OP_LIMITS, "export", 2)
        ha_mod._SENSITIVE_BUCKETS.clear()
        tc, _, _ = sb_client
        as_user()
        # burn the delete budget first — export must be unaffected
        for _ in range(5):
            assert tc.delete("/v1/organizations/nope").status_code == 403
        assert tc.get("/v1/organizations/nope/export").status_code == 403  # budget 1
        assert tc.get("/v1/organizations/nope/export").status_code == 403  # budget 2
        assert tc.get("/v1/organizations/nope/export").status_code == 429  # exhausted
        ha_mod._SENSITIVE_BUCKETS.clear()


# ═══════════════════════════════════════════════════════════════════════════
# #4029 — user-account deletion (delete my account → grace → erasure)
#
# Owner ruling (2026-09-30): decision 1B — the account's SOLELY-owned teams are
# deleted with it, reusing the team cascade; decision 2B — backups age out (no
# active backup deletion on the account path). The confirm popup is the
# safeguard for 1B; the backend contract is what these tests pin.
#
# Class-B mutation evidence: each test's docstring names the mutation that was
# run to confirm it goes RED (applied, observed, reverted).
# ═══════════════════════════════════════════════════════════════════════════


def _account_org(org_id: str, **overrides) -> dict:
    return dict(FREE_TEAM, id=org_id, **overrides)


class TestAccountDeletion:
    def test_cascades_only_solely_owned_teams(self, sb_client, as_user,
                                              capture_audit):
        """DELETE /v1/user/account cascades the solely-owned team and ONLY it.

        RED condition: the org cascade runs for an org where a second owner
        remains (the shared team's keys/memberships would be revoked and its
        ``deleted_at`` stamped). MUTATION CONFIRMED RED: in
        ``sole_owned_org_ids`` replace the ``all(... owner == user_id)`` check
        with ``True`` — ``teams_deleted`` becomes both orgs and the shared-team
        assertions fail.
        """
        tc, fake, _ = sb_client
        fake.seed("organizations", [_account_org(ORG_ID),
                                    _account_org("team-shared")])
        fake.seed("org_memberships", [
            _membership_row(user_id=OWNER),                       # sole owner
            _membership_row(org_id="team-shared", user_id=OWNER),
            _membership_row(org_id="team-shared", user_id=_U2),   # 2nd owner
        ])
        fake.seed("api_keys", [_key_row(),
                               _key_row(id="key-002", org_id="team-shared")])
        fake.seed("invitations", [{
            "id": "inv-1", "org_id": ORG_ID, "email": "bob@example.com",
            "role": "member", "status": "pending", "expires_at": None,
        }])
        as_user()
        r = tc.delete("/v1/user/account")
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["teams_deleted"] == [ORG_ID]
        assert body["grace_hours"] == RESTORE_WINDOW_HOURS
        assert body["status"] == "delete_scheduled"

        # the solely-owned org: access-kill first, stamp last
        assert all(k["revoked_at"] for k in fake.tables["api_keys"]
                   if k["org_id"] == ORG_ID)
        assert all(m["status"] == "removed"
                   for m in fake.tables["org_memberships"]
                   if m["org_id"] == ORG_ID)
        assert all(i["status"] == "revoked"
                   for i in fake.tables["invitations"])
        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org["deleted_at"]
        assert org["grace_hours"] == RESTORE_WINDOW_HOURS

        # the shared org is UNTOUCHED
        assert all(k["revoked_at"] is None for k in fake.tables["api_keys"]
                   if k["org_id"] == "team-shared")
        assert all(m["status"] == "active"
                   for m in fake.tables["org_memberships"]
                   if m["org_id"] == "team-shared")
        shared = next(o for o in fake.tables["organizations"]
                      if o["id"] == "team-shared")
        assert shared.get("deleted_at") is None

        # the account ledger row (the account-delete promise)
        assert [d["user_id"] for d in fake.tables["account_deletions"]] == [OWNER]
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("account_delete_requested") == 1

    def test_anon_second_owner_blocks_sole_ownership(self, sb_client, as_user):
        """A second owner whose ``user_id`` is NULL (the anon agent anchor)
        still makes the org NOT solely owned.

        RED condition: the owner comparison is done with a ``user_id neq``
        filter (SQL ``<>`` excludes NULL), so the NULL owner is invisible and
        the team is wrongly deleted. MUTATION CONFIRMED RED: replace the Python
        ``all(r.get("user_id") == user_id ...)`` owners check with an owners
        query filtered ``("user_id", "neq", user_id)`` plus an ``if not rows``
        test — the team is cascaded and ``teams_deleted`` is non-empty.
        """
        tc, fake, _ = sb_client
        fake.seed("organizations", [_account_org(ORG_ID)])
        fake.seed("org_memberships", [
            _membership_row(user_id=OWNER),
            _membership_row(user_id=None, identity="anon-agent-1"),
        ])
        fake.seed("api_keys", [_key_row()])
        as_user()
        r = tc.delete("/v1/user/account")
        assert r.status_code == 202, r.text
        assert r.json()["teams_deleted"] == []
        assert fake.tables["api_keys"][0]["revoked_at"] is None
        assert fake.tables["organizations"][0].get("deleted_at") is None
        assert all(m["status"] == "active"
                   for m in fake.tables["org_memberships"])

    def test_placeholder_membership_is_never_an_org(self, sb_client, as_user):
        """The signup placeholder (``org_id=''``) is not a deletable team.

        RED condition: ``sole_owned_org_ids`` keeps the placeholder row, so the
        account cascade tries to delete a non-existent org (``teams_deleted``
        would include ``''``). MUTATION CONFIRMED RED: delete the
        ``if not org_id: continue`` guard.
        """
        tc, fake, _ = sb_client
        fake.seed("org_memberships", [
            _membership_row(user_id=OWNER, org_id="", status="active"),
        ])
        as_user()
        r = tc.delete("/v1/user/account")
        assert r.status_code == 202, r.text
        assert r.json()["teams_deleted"] == []

    def test_replay_honors_stored_grace(self, sb_client, as_user, monkeypatch):
        """A repeat request answers 200-already from the STORED promise, never
        a fresh env read.

        RED condition: the replay returns the current env grace instead of the
        row's stored ``grace_hours``. MUTATION CONFIRMED RED: set
        ``replay_grace = grace_hours`` (the env value 168) in the replay branch
        — the assertion of 24 fails.
        """
        monkeypatch.setenv("TORTOISE_USER_ACCOUNT_DELETE_GRACE_HOURS", "168")
        tc, fake, _ = sb_client
        one_hour_ago = (
            datetime.now(timezone.utc) - timedelta(hours=1)  # noqa: UP017
        ).isoformat()
        fake.seed("account_deletions", [{
            "user_id": OWNER, "deleted_at": one_hour_ago, "grace_hours": 24,
        }])
        as_user()
        r = tc.delete("/v1/user/account")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["already"] is True
        assert body["status"] == "delete_pending"
        assert body["grace_hours"] == 24  # stored, not the env 168
        assert body["teams_deleted"] == []
        # no re-cascade on the replay
        assert fake.tables.get("organizations", []) == []

    def test_idempotent_second_call_replays(self, sb_client, as_user):
        """Two sequential calls: 202 then 200-already (no duplicate ledger
        row, no duplicate audit event).

        RED condition: the second call re-cascades and re-INSERTs instead of
        replaying, so it answers 202 (or a 409 escapes) instead of 200-already
        and the ledger grows a second row. MUTATION CONFIRMED RED: delete the
        ``if existing and existing.get("deleted_at"):`` early-return block in
        ``delete_user_account``.
        """
        tc, fake, _ = sb_client
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])
        as_user()
        first = tc.delete("/v1/user/account")
        assert first.status_code == 202, first.text
        second = tc.delete("/v1/user/account")
        assert second.status_code == 200, second.text
        assert second.json()["already"] is True
        assert len(fake.tables["account_deletions"]) == 1
        assert second.json()["deleted_at"] == first.json()["deleted_at"]

    def test_partial_cascade_retry_reaches_org_and_purge(
            self, sb_client, as_user, monkeypatch, capture_audit):
        """#4029 FIX 1: a fault AFTER membership removal but BEFORE the org
        stamp must not strand the org. The persisted anchor (`org_ids`) lets
        the retry cascade and stamp it, so the post-grace purge actually finds
        it.

        RED condition: the retry rediscovers solely-owned orgs from the
        ``status='active'`` owner rows, which the partial cascade already set
        to ``removed`` — so it answers 202 with ``teams_deleted: []``,
        ``org.deleted_at`` stays None, and ``_purge_deleted_orgs`` (which
        selects on ``deleted_at``) never sees the org; its graph and pending
        invitations survive. MUTATION CONFIRMED RED: make ``_merge_org_ids``
        ignore its ``stored`` argument (return only the fresh discovery) — the
        retry's ``teams_deleted`` is ``[]`` and the org is never stamped.
        """
        import tortoise.supabase_control as sc
        tc, fake, _ = sb_client
        fake.seed("organizations", [_account_org(ORG_ID)])
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])
        fake.seed("api_keys", [_key_row()])
        fake.seed("invitations", [{
            "id": "inv-1", "org_id": ORG_ID, "email": "bob@example.com",
            "role": "member", "status": "pending", "expires_at": None,
        }])

        real_revoke = sc.revoke_org_invitations
        calls = {"n": 0}

        def _flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient control-plane fault")
            return real_revoke(*args, **kwargs)

        monkeypatch.setattr(sc, "revoke_org_invitations", _flaky)
        as_user()

        with pytest.raises(RuntimeError, match="transient"):
            tc.delete("/v1/user/account")

        # partial failure: memberships removed, org NOT stamped, account NOT
        # stamped — but the intended org set is durably anchored.
        org = next(o for o in fake.tables["organizations"]
                   if o["id"] == ORG_ID)
        assert org.get("deleted_at") is None
        assert all(m["status"] == "removed"
                   for m in fake.tables["org_memberships"])
        anchor = fake.tables["account_deletions"][0]
        assert anchor["deleted_at"] is None
        assert list(anchor["org_ids"]) == [ORG_ID]

        # retry: re-cascades FROM THE ANCHOR and stamps the org
        r = tc.delete("/v1/user/account")
        assert r.status_code == 202, r.text
        assert r.json()["teams_deleted"] == [ORG_ID]
        org = next(o for o in fake.tables["organizations"]
                   if o["id"] == ORG_ID)
        assert org["deleted_at"]
        assert org["grace_hours"] == RESTORE_WINDOW_HOURS
        assert all(i["status"] == "revoked"
                   for i in fake.tables["invitations"])

        # the stamped org is now discoverable by the purge once its grace
        # elapses — the whole point: it is not orphaned.
        org["deleted_at"] = (
            datetime.now(timezone.utc) - timedelta(days=30)  # noqa: UP017
        ).isoformat()
        ha_mod._purge_deleted_orgs()
        assert all(o["id"] != ORG_ID for o in fake.tables["organizations"])
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_purged") == 1

    def test_scheduled_hard_delete_after_derives_from_stored_stamp(
            self, sb_client, as_user):
        """#4029 FIX 3: the advertised ``hard_delete_after`` is the STORED
        ``deleted_at`` + the promised grace — never a second ``now()`` (which
        would advertise a deadline later than the one the purge enforces).

        RED condition: the 202 body computes ``hard_delete_after`` from a fresh
        ``datetime.now(UTC)``, so it is later than ``deleted_at +
        grace_hours``. MUTATION CONFIRMED RED: revert the return to
        ``(datetime.now(UTC) + timedelta(hours=grace_hours)).isoformat()`` —
        equality fails (time elapses across the cascade's thread hops and the
        ledger round-trip).
        """
        tc, fake, _ = sb_client
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])
        as_user()
        r = tc.delete("/v1/user/account")
        assert r.status_code == 202, r.text
        body = r.json()
        expected = (
            datetime.fromisoformat(body["deleted_at"])
            + timedelta(hours=body["grace_hours"])
        ).isoformat()
        assert body["hard_delete_after"] == expected

    def test_concurrent_schedule_409_stored_promise_wins(
            self, sb_client, as_user, monkeypatch, capture_audit):
        """#4029 FIX 4.2: a concurrent schedule that loses the INSERT race
        answers from the STORED promise (200 already), never its own fresh
        grace, and emits no second ``account_delete_requested``.

        RED condition: the 409 is swallowed and the handler returns 202 with
        its OWN grace/``hard_delete_after`` and a second audit event.
        MUTATION CONFIRMED RED: in the ``begin_account_deletion`` except,
        replace the winner re-read with ``pass`` — the response is 202 with the
        env grace (168) and the audit count is 1.
        """
        import tortoise.supabase_control as sc
        monkeypatch.setenv("TORTOISE_USER_ACCOUNT_DELETE_GRACE_HOURS", "168")
        tc, fake, _ = sb_client
        one_hour_ago = (
            datetime.now(timezone.utc) - timedelta(hours=1)  # noqa: UP017
        ).isoformat()
        fake.seed("account_deletions", [{
            "user_id": OWNER, "deleted_at": one_hour_ago, "grace_hours": 24,
        }])
        real_row = sc.account_deletion_row
        calls = {"n": 0}

        def _race(cp, user_id):
            # Simulate the read-before-INSERT window: the first read sees no
            # row, then the concurrent writer's row exists for the INSERT
            # conflict (the fake raises 409 on the duplicate PK) and for the
            # post-conflict re-read.
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return real_row(cp, user_id)

        monkeypatch.setattr(sc, "account_deletion_row", _race)
        as_user()
        r = tc.delete("/v1/user/account")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["already"] is True
        assert body["grace_hours"] == 24        # stored, not the env 168
        assert body["deleted_at"] == one_hour_ago
        expected = (
            datetime.fromisoformat(one_hour_ago) + timedelta(hours=24)
        ).isoformat()
        assert body["hard_delete_after"] == expected
        assert len(fake.tables["account_deletions"]) == 1
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("account_delete_requested") == 0

    def test_requires_auth(self, sb_client):
        """No session → 401 (the endpoint acts on the authenticated account).

        RED condition: the route is reachable without a session, so the
        request schedules a deletion with no authenticated actor. MUTATION
        CONFIRMED RED: drop ``user: dict = Depends(get_current_user)`` from the
        ``delete_user_account`` signature.
        """
        tc, _, _ = sb_client
        assert tc.delete("/v1/user/account").status_code == 401

    def test_registry_mode_unsupported(self, reg_client, as_user):
        """Selfhost/registry has no hosted auth accounts → the claimed
        ``unsupported`` shape (mirrors GET /v1/user/identity).

        RED condition: the endpoint proceeds past the mode guard and answers a
        scheduled-deletion body in registry mode. MUTATION CONFIRMED RED: delete
        the ``if not is_supabase_enabled(): return {"unsupported": True}``
        early return.
        """
        tc, _ = reg_client
        as_user(user_id=_U2)
        r = tc.delete("/v1/user/account")
        assert r.status_code == 200, r.text
        assert r.json() == {"unsupported": True}

    def test_account_delete_rate_limited(self, sb_client, monkeypatch, as_user):
        """The per-IP sensitive-op budget applies (5/h → 6th is 429).

        RED condition: the budget is not charged, so the 6th call is accepted
        instead of 429. MUTATION CONFIRMED RED: remove the
        ``@_deferred_sensitive_op("account_delete")`` decorator from
        ``delete_user_account`` (the op stays in ``_SENSITIVE_OP_LIMITS``).
        """
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        ha_mod._SENSITIVE_BUCKETS.clear()
        tc, _, _ = sb_client
        as_user()
        for _ in range(5):
            assert tc.delete("/v1/user/account").status_code in (200, 202)
        r = tc.delete("/v1/user/account")
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        ha_mod._SENSITIVE_BUCKETS.clear()


class TestAccountPurge:
    """The erasure leg: `_purge_deleted_accounts` (boot + hourly)."""

    def test_purge_erases_past_grace_and_keeps_within(self, sb_client,
                                                      capture_audit, monkeypatch):
        """Past-grace account → auth user erased + ledger row removed; a
        within-grace account is untouched.

        RED condition: the stored-grace decision is skipped and the
        within-grace account is erased too. MUTATION CONFIRMED RED: delete the
        ``if not _stored_grace_elapsed(...): continue`` guard — ``deleted``
        gains the within-grace user and the ledger keeps only one row.
        """
        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc)  # noqa: UP017
                - timedelta(days=8)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        fake.seed("account_deletions", [
            {"user_id": OWNER, "deleted_at": past,
             "grace_hours": RESTORE_WINDOW_HOURS},
            {"user_id": _U3, "deleted_at": recent,
             "grace_hours": RESTORE_WINDOW_HOURS},
        ])
        deleted: list[str] = []
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: (deleted.append(uid), 204)[1])

        ha_mod._purge_deleted_accounts()

        assert deleted == [OWNER]
        assert [d["user_id"] for d in fake.tables["account_deletions"]] == [_U3]
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("account_delete_purged") == 1

    def test_purge_completes_a_stale_unstamped_anchor_without_erasing(
            self, sb_client, capture_audit, monkeypatch):
        """P1-4 (#4029 review): a STALE UNSTAMPED anchor is COMPLETED, not
        erased.

        Between ``begin_account_deletion`` and ``stamp_account_deletion`` the
        row is un-stamped; if the process dies there, ``deleted_at lte now``
        (which SQL excludes NULL from) never selects it again — the account is
        never erased, the ledger row is never cleaned, and nothing notices. The
        sweep must select it and COMPLETE the interrupted schedule (re-derive,
        claim, cascade, then stamp) — never erase early, since the promised
        window starts at the stamp.

        RED condition: the un-stamped stale row is invisible to the sweep, so
        its org is never cascaded and the row is never stamped. MUTATION
        CONFIRMED RED: drop the second (un-stamped stale) query from
        ``_purge_deleted_accounts`` — the org stays un-stamped and the row's
        ``deleted_at`` stays None.
        """
        tc, fake, _ = sb_client  # noqa: RUF059
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [], "claimed_org_ids": [],
            "deleted_at": None, "grace_hours": None,
            "created_at": (
                datetime.now(timezone.utc)  # noqa: UP017
                - timedelta(days=8)).isoformat(),
        }])
        fake.seed("organizations", [_account_org(ORG_ID)])
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])
        fake.seed("api_keys", [_key_row()])
        deleted: list[str] = []
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: (deleted.append(uid), 204)[1])

        ha_mod._purge_deleted_accounts()

        assert deleted == [], "an un-stamped anchor must not be erased early"
        row = fake.tables["account_deletions"][0]
        assert row["deleted_at"] is not None, "the schedule must be stamped"
        assert row["grace_hours"] == ha_mod.USER_ACCOUNT_DELETE_GRACE_HOURS
        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org["deleted_at"] is not None, "the org cascade must complete"
        assert all(k["revoked_at"] for k in fake.tables["api_keys"])
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("account_delete_purged") == 0

    def test_purge_leaves_a_fresh_unstamped_anchor_alone(
            self, sb_client, monkeypatch):
        """P1-4 (#4029 review): an UNSTAMPED anchor still inside the in-flight
        bound may belong to a cascade that is RUNNING — the sweep must touch it
        neither way. Pins the staleness bound on the selection itself."""
        tc, fake, _ = sb_client  # noqa: RUF059
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [], "claimed_org_ids": [],
            "deleted_at": None, "grace_hours": None,
            "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        }])
        deleted: list[str] = []
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: (deleted.append(uid), 204)[1])

        ha_mod._purge_deleted_accounts()

        assert deleted == []
        row = fake.tables["account_deletions"][0]
        assert row["deleted_at"] is None, "a fresh anchor must not be completed"
        assert row["grace_hours"] is None

    def test_purge_honors_stored_grace_over_env(self, sb_client, monkeypatch):
        """A stored promise (168h) holds even when the env default shrinks to
        1h; a row with NO stored grace falls back to the env.

        RED condition: the env grace is applied to every row. MUTATION
        CONFIRMED RED: pass ``env_grace`` as ``row_grace_hours`` into
        ``_stored_grace_elapsed`` — the stored-promise account is then erased
        and ``deleted`` contains both users.
        """
        monkeypatch.setenv("TORTOISE_USER_ACCOUNT_DELETE_GRACE_HOURS", "1")
        tc, fake, _ = sb_client  # noqa: RUF059
        ten_hours_ago = (
            datetime.now(timezone.utc) - timedelta(hours=10)  # noqa: UP017
        ).isoformat()
        fake.seed("account_deletions", [
            {"user_id": OWNER, "deleted_at": ten_hours_ago, "grace_hours": 168},
            {"user_id": _U3, "deleted_at": ten_hours_ago, "grace_hours": None},
        ])
        deleted: list[str] = []
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: (deleted.append(uid), 204)[1])

        ha_mod._purge_deleted_accounts()

        assert deleted == [_U3]  # stored promise held; env fallback purged
        assert [d["user_id"] for d in fake.tables["account_deletions"]] == [OWNER]

    def test_purge_keeps_ledger_on_auth_failure(self, sb_client, capture_audit,
                                                monkeypatch):
        """A failed auth-user delete leaves the ledger row as the retry anchor
        (no audit purge event); the next sweep completes it.

        RED condition: the ledger row is removed before the auth delete
        succeeds. MUTATION CONFIRMED RED: move ``purge_account_deletion``
        BEFORE the ``_supabase_admin_delete_user`` call — the first
        ledger-survival assertion fails.
        """
        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc)  # noqa: UP017
                - timedelta(days=8)).isoformat()
        fake.seed("account_deletions", [
            {"user_id": OWNER, "deleted_at": past,
             "grace_hours": RESTORE_WINDOW_HOURS},
        ])

        def _boom(uid):
            raise RuntimeError("auth-service transport failure")

        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user", _boom)
        ha_mod._purge_deleted_accounts()

        assert [d["user_id"] for d in fake.tables["account_deletions"]] == [OWNER]
        assert "account_delete_purged" not in [
            e["operation"] for e in capture_audit]

        # healed → the next sweep purges
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: 204)
        ha_mod._purge_deleted_accounts()
        assert fake.tables["account_deletions"] == []

    def test_purge_treats_404_as_already_erased(self, sb_client, monkeypatch):
        """GoTrue 404 means the account is already gone — the ledger row is
        removed (idempotent), not retried forever.

        RED condition: any status >= 400 is treated as failure. MUTATION
        CONFIRMED RED: drop the ``and status != 404`` conjunct — the ledger row
        survives the sweep.
        """
        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc)  # noqa: UP017
                - timedelta(days=8)).isoformat()
        fake.seed("account_deletions", [
            {"user_id": OWNER, "deleted_at": past,
             "grace_hours": RESTORE_WINDOW_HOURS},
        ])
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: 404)

        ha_mod._purge_deleted_accounts()

        assert fake.tables["account_deletions"] == []

    def test_purge_is_a_noop_on_registry(self, reg_client, monkeypatch):
        """Selfhost/registry has no auth accounts — the sweep returns without
        ever resolving the Supabase control plane.

        RED condition: the sweep proceeds past the ``is_supabase_enabled()``
        guard and addresses Supabase. MUTATION CONFIRMED RED: delete that
        guard — ``get_control_plane`` is reached and the recorded call list is
        non-empty (the outer except swallows the raise, so the call must be
        OBSERVED, not merely raised).
        """
        import tortoise.supabase_control as sc
        tc, _ = reg_client  # noqa: RUF059
        reached: list[int] = []

        def _cp():
            reached.append(1)
            raise RuntimeError("registry mode must not build a Supabase client")

        monkeypatch.setattr(sc, "get_control_plane", _cp)
        ha_mod._purge_deleted_accounts()
        assert reached == []

    def test_purge_keeps_ledger_on_3xx(self, sb_client, capture_audit,
                                       monkeypatch):
        """#4029 FIX 2: a 3xx is NOT a successful erasure — ``httpx.delete``
        does not follow redirects, so the auth user is never deleted and the
        ledger row is the only retry anchor.

        RED condition: any sub-400 status counts as erased, so
        ``purge_account_deletion`` removes the anchor while the auth user
        survives permanently. MUTATION CONFIRMED RED: revert the predicate to
        ``status >= 400 and status != 404`` — the ledger row is removed and
        ``account_delete_purged`` is emitted.
        """
        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc)  # noqa: UP017
                - timedelta(days=8)).isoformat()
        fake.seed("account_deletions", [
            {"user_id": OWNER, "deleted_at": past,
             "grace_hours": RESTORE_WINDOW_HOURS},
        ])
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: 302)

        ha_mod._purge_deleted_accounts()

        assert [d["user_id"] for d in fake.tables["account_deletions"]] == [OWNER]
        assert "account_delete_purged" not in [
            e["operation"] for e in capture_audit]

    def test_purge_unparseable_stamp_is_purged(self, sb_client, capture_audit,
                                               monkeypatch):
        """#4029 FIX 4.1 (account path): a corrupt (unparseable) ``deleted_at``
        must be PURGED, not skipped — a corrupt row must not retain the auth
        account forever.

        RED condition: ``_stored_grace_elapsed``'s defensive except returns
        False, so the corrupt row survives and the auth user is never erased.
        MUTATION CONFIRMED RED: change ``return True`` to ``return False`` in
        ``_stored_grace_elapsed``'s ``except Exception`` — ``deleted`` stays
        empty and the ledger row survives.

        The stamp is ``"!!!"`` deliberately: the sweep's ``deleted_at lte
        now`` prefilter is a lexicographic string compare, and ``'!' < '2'``
        makes the row reach ``_stored_grace_elapsed`` (an ISO-shaped bad stamp
        would be filtered out before the defensive branch runs).
        """
        tc, fake, _ = sb_client  # noqa: RUF059
        fake.seed("account_deletions", [
            {"user_id": OWNER, "deleted_at": "!!!", "grace_hours": 1},
        ])
        deleted: list[str] = []
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: (deleted.append(uid), 204)[1])

        ha_mod._purge_deleted_accounts()

        assert deleted == [OWNER]
        assert fake.tables["account_deletions"] == []
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("account_delete_purged") == 1


# ═══════════════════════════════════════════════════════════════════════════
# #4029 cycle-3 — intent vs durable claim, purge-time re-derivation,
# idempotent re-cascade, the 409 winner-unstamped ordering, and the
# cross-user anchor dispute (properties A-F of the cycle-3 verification).
# ═══════════════════════════════════════════════════════════════════════════

# The other user's team in property F; deliberately NOT owned by OWNER.
_FOREIGN_ORG = "team-owned-by-someone-else"


class TestAccountDeletionCycle3:
    """The cycle-3 fix's six required properties (A-F), one test each.

    Each test names the condition that fails it and carries the mutation that
    was observed to make it RED before it was GREEN (test doctrine, Class-B).
    """

    def test_replay_drops_intent_org_the_caller_no_longer_solely_owns(
            self, sb_client, as_user):
        """A — a replay must never cascade a team the caller does not solely
        own.

        The anchor carries TWO sets: ``org_ids`` is the INTENT record (a cache
        of ownership, a fact that changes) and ``claimed_org_ids`` is the
        durable CLAIM. Cycle-2 reproduced this exact hole: anchor ``[X]`` → a
        second ACTIVE owner is added to ``X`` → the retry cascaded and stamped
        ``X``. The replay must therefore re-validate ownership, so a stale
        intent id that is neither live-discovered nor claimed is DROPPED.

        RED condition: the replay trusts the intent anchor (reads ``org_ids``
        instead of ``claimed_org_ids``), so ``X`` is cascaded despite the
        second owner and ``teams_deleted`` names a team the caller shares.
        MUTATION CONFIRMED RED: in ``delete_user_account`` change
        ``_account_org_ids_from_row(existing, "claimed_org_ids")`` to
        ``_account_org_ids_from_row(existing)`` (read the intent column) —
        ``teams_deleted`` becomes ``[ORG_ID]`` and the org is stamped.
        """
        tc, fake, _ = sb_client
        fake.seed("organizations", [_account_org(ORG_ID)])
        fake.seed("org_memberships", [
            _membership_row(user_id=OWNER),
            _membership_row(user_id=_U2),          # a SECOND active owner
        ])
        fake.seed("api_keys", [_key_row()])
        # A prior attempt's anchor: X is in the INTENT set but was never
        # claimed (no cascade ever began), and the account is un-stamped.
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [ORG_ID], "claimed_org_ids": [],
            "deleted_at": None, "grace_hours": None,
        }])
        as_user()

        r = tc.delete("/v1/user/account")
        assert r.status_code == 202, r.text
        assert r.json()["teams_deleted"] == []

        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org.get("deleted_at") is None
        assert all(k["revoked_at"] is None for k in fake.tables["api_keys"])
        assert all(m["status"] == "active"
                   for m in fake.tables["org_memberships"])

    def test_claim_is_durable_before_the_access_kill(
            self, sb_client, as_user, monkeypatch):
        """B(i) — the claim is written BEFORE the org's access-kill, so a fault
        after membership removal is still replayable.

        The cascade destroys the active-owner rows ``sole_owned_org_ids``
        discovers by; if the claim were written only after the cascade (or not
        at all) a fault at ``revoke_org_invitations`` would leave the org
        un-stamped AND undiscoverable — the replay's fresh discovery reads no
        rows. The durable claim must still reach it, stamp it, and let the
        post-grace purge see it.

        RED condition: the durable claim is missing (or written after the
        stamp), so the retry's cascade set is empty and the org is orphaned
        with ``deleted_at IS NULL``.
        MUTATION CONFIRMED RED: delete the
        ``await asyncio.to_thread(claim_account_deletion_org, cp, user_id, org_id)``
        call before ``_cascade_soft_delete_org`` — ``claimed_org_ids`` stays
        ``[]``, the retry answers 202 with ``teams_deleted: []``, and the org
        is never stamped or purged.
        """
        import tortoise.supabase_control as sc
        tc, fake, _ = sb_client
        fake.seed("organizations", [_account_org(ORG_ID)])
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])
        fake.seed("api_keys", [_key_row()])
        fake.seed("invitations", [{
            "id": "inv-1", "org_id": ORG_ID, "email": "bob@example.com",
            "role": "member", "status": "pending", "expires_at": None,
        }])
        real_revoke = sc.revoke_org_invitations
        calls = {"n": 0}

        def _flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient control-plane fault")
            return real_revoke(*args, **kwargs)

        monkeypatch.setattr(sc, "revoke_org_invitations", _flaky)
        as_user()

        with pytest.raises(RuntimeError, match="transient"):
            tc.delete("/v1/user/account")

        # partial failure: memberships removed, org NOT stamped, account NOT
        # stamped — but the org is durably CLAIMED.
        anchor = fake.tables["account_deletions"][0]
        assert anchor["deleted_at"] is None
        assert [str(x) for x in anchor["claimed_org_ids"]] == [ORG_ID]
        assert all(m["status"] == "removed"
                   for m in fake.tables["org_memberships"])
        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org.get("deleted_at") is None
        # The very discovery a replay would re-run is now EMPTY — the cascade
        # removed exactly the rows it reads, so only the claim can reach X.
        assert sc.sole_owned_org_ids(fake, OWNER) == []

        r = tc.delete("/v1/user/account")
        assert r.status_code == 202, r.text
        assert r.json()["teams_deleted"] == [ORG_ID]

        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org["deleted_at"]
        assert org["grace_hours"] == RESTORE_WINDOW_HOURS
        # …and it is punchable by the purge once its own window elapses.
        org["deleted_at"] = (
            datetime.now(timezone.utc) - timedelta(days=30)  # noqa: UP017
        ).isoformat()
        ha_mod._purge_deleted_orgs()
        assert all(o["id"] != ORG_ID for o in fake.tables["organizations"])

    def test_claim_is_per_org_atomic_append_not_a_full_column_rewrite(
            self, sb_client, as_user, monkeypatch):
        """B(ii) — a concurrent narrower writer must not drop an id another
        writer already claimed.

        Cycle-2 P2: the widen was a BLIND FULL-COLUMN PATCH computed from an
        earlier read, so an overlapping narrower writer overwrote the column
        and dropped an org the other request had already access-killed. The
        fix is one atomic DB-side append per org
        (``account_deletion_claim_org``). This test interleaves a second
        writer's claim between the first writer's read and its write — the
        exact reproduced loss — and requires every id to survive.

        RED condition: the claim is a read-then-whole-column PATCH, so the
        interleaved writer's id is erased by the stale write.
        MUTATION CONFIRMED RED: reimplement ``claim_account_deletion_org`` as
        ``rows = cp.query("account_deletions", select=["claimed_org_ids"], ...);
        cp.query("account_deletions", method="PATCH", json_body={
        "claimed_org_ids": rows[0]["claimed_org_ids"] + [org_id]})`` — the
        interleaved ``org-w`` is dropped and the assertion fails.
        """
        import tortoise.supabase_control as sc
        tc, fake, _ = sb_client
        # Two solely-owned orgs the endpoint will claim; org-x is also already
        # in the anchor's claimed set.
        fake.seed("organizations", [_account_org("org-x"),
                                    _account_org("org-z")])
        fake.seed("org_memberships", [
            _membership_row(user_id=OWNER, org_id="org-x"),
            _membership_row(user_id=OWNER, org_id="org-z"),
        ])
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": ["org-x"],
            "claimed_org_ids": ["org-x"],
            "deleted_at": None, "grace_hours": None,
        }])

        real_claim = sc.claim_account_deletion_org
        real_rpc = fake.rpc
        real_query = fake.query
        injected = {"n": 0}

        def _inject_writer_b_once():
            """Writer B claims org-w at the point where, under a read-then-
            write, its write lands between A's read and A's write."""
            if not injected["n"]:
                injected["n"] += 1
                real_claim(fake, OWNER, "org-w")

        def _rpc(fn, body=None, **kwargs):
            if (fn == "account_deletion_claim_org"
                    and (body or {}).get("p_org_id") == "org-z"):
                _inject_writer_b_once()
            return real_rpc(fn, body, **kwargs)

        def _query(table, *args, **kwargs):
            body = kwargs.get("json_body") or {}
            if (table == "account_deletions"
                    and kwargs.get("method") == "PATCH"
                    and "org-z" in (body.get("claimed_org_ids") or [])):
                _inject_writer_b_once()
            return real_query(table, *args, **kwargs)

        monkeypatch.setattr(fake, "rpc", _rpc)
        monkeypatch.setattr(fake, "query", _query)
        as_user()

        r = tc.delete("/v1/user/account")
        assert r.status_code == 202, r.text
        assert injected["n"] == 1, "the concurrent-writer injection never fired"

        claimed = [str(x) for x in
                   fake.tables["account_deletions"][0]["claimed_org_ids"]]
        assert set(claimed) == {"org-x", "org-w", "org-z"}

    def test_purge_cascades_org_acquired_inside_the_grace_window(
            self, sb_client, monkeypatch, capture_audit):
        """C — no org may outlive the account's erasure.

        Erasing the auth user cascades the caller's ``org_memberships`` rows —
        the rows sole-ownership discovery reads. A team the caller became the
        sole owner of INSIDE the grace window (the one-free-org gate permits
        the create once the schedule cascade removed the old rows) is in
        neither the frozen anchor nor any endpoint cascade, so the purge must
        re-derive ownership BEFORE it erases.

        RED condition: the purge erases the auth user without re-deriving, so
        the in-window org is orphaned with ``deleted_at IS NULL`` while no
        owner remains to reach it.
        MUTATION CONFIRMED RED: delete the
        ``for org_id in sole_owned_org_ids(cp, user_id):
        _cascade_soft_delete_org_sync(...)`` block from
        ``_purge_deleted_accounts`` — the org is never stamped and the
        ``org["deleted_at"] is not None`` assertion fails.
        """
        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc)  # noqa: UP017
                - timedelta(days=8)).isoformat()
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [], "claimed_org_ids": [],
            "deleted_at": past, "grace_hours": 24,
        }])
        # A team acquired INSIDE the window: solely owned at erasure time, in
        # NEITHER the frozen intent set NOR the claim set.
        fake.seed("organizations", [_account_org(ORG_ID)])
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])
        fake.seed("api_keys", [_key_row()])
        deleted: list[str] = []
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: (deleted.append(uid), 204)[1])

        ha_mod._purge_deleted_accounts()

        assert deleted == [OWNER]                      # account erased
        assert fake.tables["account_deletions"] == []  # ledger row gone
        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org["deleted_at"] is not None, "org outlived the erased account"
        assert org["grace_hours"] == 24                # the account's promise
        assert all(k["revoked_at"] for k in fake.tables["api_keys"])
        assert all(m["status"] == "removed"
                   for m in fake.tables["org_memberships"])
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("account_delete_purged") == 1

    def test_purge_claims_in_window_org_before_its_access_kill(
            self, sb_client, monkeypatch):
        """G — the ERASURE sweep must durably CLAIM an in-window org before it
        kills that org's access, exactly as the endpoint does.

        Sole-ownership discovery reads ACTIVE owner rows, so a fault AFTER
        ``remove_org_memberships`` makes an org unreachable by discovery
        forever — and this sweep is the LAST chance to reach it, because the
        endpoint early-returns once the account is stamped. Without a durable
        claim, sweep 2 re-derives nothing, **erases the account anyway**, and
        leaves the org un-stamped, ownerless and invisible to
        ``_purge_deleted_orgs`` (and, when the fault lands on
        ``revoke_org_invitations``, with a still-redeemable pending
        invitation, since ``invitation_accept``'s kill-switch is
        ``org.deleted_at``). This is the cycle-1 orphan shape re-entering
        through the purge.

        RED condition: the sweep cascades without claiming, so sweep 1's fault
        leaves the org unreachable in sweep 2, which erases the account and
        orphans it.
        MUTATION CONFIRMED RED: delete the
        ``claim_account_deletion_org(cp, user_id, org_id)`` call from
        ``_purge_deleted_accounts``'s cascade loop — after sweep 2,
        ``org["deleted_at"] is None`` while ``deleted == [OWNER]``.
        """
        import tortoise.supabase_control as sc

        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc)  # noqa: UP017
                - timedelta(days=8)).isoformat()
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [], "claimed_org_ids": [],
            "deleted_at": past, "grace_hours": 24,
        }])
        # A team acquired inside the window: solely owned at erasure time, in
        # NEITHER the frozen intent set NOR (yet) the claim set.
        fake.seed("organizations", [_account_org(ORG_ID)])
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])
        fake.seed("api_keys", [_key_row()])
        deleted: list[str] = []
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: (deleted.append(uid), 204)[1])

        # The access-kill faults ONCE, AFTER membership removal — the exact
        # point that destroys discoverability.
        real_revoke = sc.revoke_org_invitations
        calls = {"n": 0}

        def flaky(cp, org_id, now):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient control-plane fault")
            return real_revoke(cp, org_id, now)

        monkeypatch.setattr(sc, "revoke_org_invitations", flaky)

        # ---- sweep 1: faults after the memberships are gone.
        ha_mod._purge_deleted_accounts()
        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org.get("deleted_at") is None, "precondition: sweep 1 did not stamp"
        assert all(m["status"] == "removed"
                   for m in fake.tables["org_memberships"]), "precondition"
        assert deleted == [], (
            "the account must NOT be erased while an org it owns is unstamped")
        assert ORG_ID in fake.tables["account_deletions"][0]["claimed_org_ids"], (
            "the sweep must durably claim the org BEFORE its access-kill")

        # ---- sweep 2: discovery is blind now, so only the CLAIM reaches it.
        ha_mod._purge_deleted_accounts()
        assert deleted == [OWNER], "sweep 2 erased the account"
        assert fake.tables["account_deletions"] == [], "ledger row gone"
        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org["deleted_at"] is not None, "org outlived the erased account"
        assert org["grace_hours"] == 24, "the account's stored promise"
        assert all(k["revoked_at"] for k in fake.tables["api_keys"])

    def test_recascade_does_not_move_an_already_stamped_org_window(
            self, sb_client, as_user, monkeypatch):
        """D — a re-cascade must never move an already-stamped org's window.

        The account stamp is LAST, so a fault at the account stamp leaves the
        row un-stamped and the retry re-runs the whole cascade. The org is
        already stamped by then; re-stamping it with the retry's ``now`` would
        silently extend retention past the promise (N retries → N × the
        window). ``soft_delete_org`` is guarded on ``deleted_at IS NULL``. The
        access-kill must still complete on the retry.

        RED condition: the re-cascade re-stamps the org, moving its
        ``deleted_at`` forward.
        MUTATION CONFIRMED RED: drop the ``("deleted_at", "is", None)``
        conjunct from ``soft_delete_org``'s PATCH filters — the retry stamps a
        new ``now`` and the unchanged-stamp assertion fails.
        """
        import time as _time

        import tortoise.supabase_control as sc
        tc, fake, _ = sb_client
        fake.seed("organizations", [_account_org(ORG_ID)])
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])
        fake.seed("api_keys", [_key_row()])
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [ORG_ID], "claimed_org_ids": [ORG_ID],
            "deleted_at": None, "grace_hours": None,
        }])
        real_stamp = sc.stamp_account_deletion
        calls = {"n": 0}

        def _flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient account-stamp fault")
            return real_stamp(*args, **kwargs)

        monkeypatch.setattr(sc, "stamp_account_deletion", _flaky)
        as_user()

        with pytest.raises(RuntimeError, match="transient"):
            tc.delete("/v1/user/account")

        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        first_stamp = org["deleted_at"]
        assert first_stamp
        _time.sleep(0.005)  # a re-stamp's now must be distinguishable

        r = tc.delete("/v1/user/account")
        assert r.status_code == 202, r.text

        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org["deleted_at"] == first_stamp          # window unmoved
        assert org["grace_hours"] == RESTORE_WINDOW_HOURS
        assert all(k["revoked_at"] for k in fake.tables["api_keys"])
        assert all(m["status"] == "removed"
                   for m in fake.tables["org_memberships"])

    def test_concurrent_schedule_409_winner_unstamped_no_recascade(
            self, sb_client, as_user, monkeypatch, capture_audit):
        """E — the 409 loser must not double-cascade or double-audit, even when
        the winner holds the anchor UNSTAMPED.

        The winner acquires the row at INSERT, BEFORE its cascade and its
        stamp, so the realistic race finds ``deleted_at IS NULL``. The existing
        ``test_concurrent_schedule_409_stored_promise_wins`` pre-seeds
        ``deleted_at``, so it only covers the non-racing ordering. Here the
        loser must answer 200-already from the winner's row and must NOT
        re-cascade the caller's team nor emit a second
        ``account_delete_requested``.

        RED condition: the 409 branch falls through to the cascade, so the
        loser re-cascades the team, returns 202 with
        ``teams_deleted: [ORG_ID]``, and emits a second audit event.
        MUTATION CONFIRMED RED: in the ``begin_account_deletion`` except
        branch, replace the ``return JSONResponse(...)`` with ``pass`` —
        ``teams_deleted`` becomes ``[ORG_ID]``, the org is stamped, and the
        audit count is 1.
        """
        import tortoise.supabase_control as sc
        tc, fake, _ = sb_client
        # The winner's UNSTAMPED anchor: it holds the row but has not stamped.
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [ORG_ID], "claimed_org_ids": [],
            "deleted_at": None, "grace_hours": None,
        }])
        # The caller solely owns a team: a fallen-through loser would cascade it.
        fake.seed("organizations", [_account_org(ORG_ID)])
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])
        fake.seed("api_keys", [_key_row()])
        real_row = sc.account_deletion_row
        calls = {"n": 0}

        def _race(cp, user_id):
            # The read-before-INSERT window: the loser's first read sees no
            # row, then the winner's row exists for the 409 and the re-read.
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return real_row(cp, user_id)

        monkeypatch.setattr(sc, "account_deletion_row", _race)
        as_user()

        r = tc.delete("/v1/user/account")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["already"] is True
        assert body["deleted_at"] is None      # the winner has not stamped yet
        assert body["teams_deleted"] == []

        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org.get("deleted_at") is None   # the loser did not re-cascade
        assert all(k["revoked_at"] is None for k in fake.tables["api_keys"])
        assert len(fake.tables["account_deletions"]) == 1
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("account_delete_requested") == 0

    def test_replay_never_cascades_another_users_org_from_the_anchor(
            self, sb_client, as_user):
        """F — settling the cross-user claim: an anchor row naming another
        user's org must not cause that org to be cascaded.

        Cycle-2 had one reviewer report a cross-user shape and another unable
        to reproduce it, reasoning that every ledger read/write is
        ``user_id``-scoped. That reasoning covers WHICH ROW is read, not which
        ORGS the row NAMES: the claimed set was trusted as raw ids. This test
        seeds the disputed state — an anchor for OWNER naming a team solely
        owned by a DIFFERENT user, in both the intent and the claimed sets —
        and pins that the stranger's team is untouched.

        RED condition: the claimed set is honoured without checking that the
        caller ever held a membership in the org, so a foreign id in the anchor
        access-kills and stamps another user's team.
        MUTATION CONFIRMED RED: in ``delete_user_account`` drop the
        caller-membership filter on ``claimed_ids`` — the foreign org is
        cascaded, its key revoked and its ``deleted_at`` stamped.
        """
        tc, fake, _ = sb_client
        fake.seed("organizations", [_account_org(_FOREIGN_ORG)])
        fake.seed("org_memberships", [
            _membership_row(user_id=_U2, org_id=_FOREIGN_ORG),  # NOT the caller
        ])
        fake.seed("api_keys", [_key_row(id="key-foreign", org_id=_FOREIGN_ORG)])
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [_FOREIGN_ORG],
            "claimed_org_ids": [_FOREIGN_ORG],
            "deleted_at": None, "grace_hours": None,
        }])
        as_user()

        r = tc.delete("/v1/user/account")
        assert r.status_code == 202, r.text
        assert r.json()["teams_deleted"] == []

        org = next(o for o in fake.tables["organizations"]
                   if o["id"] == _FOREIGN_ORG)
        assert org.get("deleted_at") is None
        assert all(k["revoked_at"] is None
                   for k in fake.tables["api_keys"]
                   if k["org_id"] == _FOREIGN_ORG)
        assert all(m["status"] == "active"
                   for m in fake.tables["org_memberships"])

    def test_purge_never_cascades_another_users_org_from_the_anchor(
            self, sb_client, capture_audit, monkeypatch):
        """P2-1 (#4029 review): the ERASURE sweep applies the SAME claim
        ownership gate the endpoint does.

        The endpoint filters a claimed id through the caller's memberships
        before honouring it; the sweep merged the RAW claimed set, so a stale
        or malformed anchor naming a stranger's org was cascaded and stamped
        at purge time even though the endpoint would have refused it. Here
        OWNER's past-grace ledger row names a team owned by _U2 only; the purge
        must erase OWNER and leave the stranger's team untouched.

        RED condition: the purge honours the raw claimed set. MUTATION
        CONFIRMED RED: drop the caller-membership filter on ``claimed_ids`` in
        ``_purge_deleted_accounts`` — the foreign org is access-killed and
        stamped.
        """
        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc)  # noqa: UP017
                - timedelta(days=8)).isoformat()
        fake.seed("organizations", [_account_org(_FOREIGN_ORG)])
        fake.seed("org_memberships", [
            _membership_row(user_id=_U2, org_id=_FOREIGN_ORG),  # NOT the caller
        ])
        fake.seed("api_keys", [_key_row(id="key-foreign", org_id=_FOREIGN_ORG)])
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [_FOREIGN_ORG],
            "claimed_org_ids": [_FOREIGN_ORG],
            "deleted_at": past, "grace_hours": RESTORE_WINDOW_HOURS,
        }])
        deleted: list[str] = []
        monkeypatch.setattr(ha_mod, "_supabase_admin_delete_user",
                            lambda uid: (deleted.append(uid), 204)[1])

        ha_mod._purge_deleted_accounts()

        assert deleted == [OWNER]
        org = next(o for o in fake.tables["organizations"]
                   if o["id"] == _FOREIGN_ORG)
        assert org.get("deleted_at") is None
        assert all(k["revoked_at"] is None
                   for k in fake.tables["api_keys"]
                   if k["org_id"] == _FOREIGN_ORG)
        assert all(m["status"] == "active"
                   for m in fake.tables["org_memberships"])
        # The caller's own account IS erased, so exactly one purge audit fires.
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("account_delete_purged") == 1

    def test_second_delete_mid_cascade_defers_to_the_in_flight_anchor(
            self, sb_client, as_user, monkeypatch, capture_audit):
        """P2-3 (#4029 review): a second DELETE arriving while the winner is
        STILL cascading defers to the un-stamped anchor.

        The winner holds the ledger row un-stamped from its INSERT until the
        final stamp, so a second request reading it sees ``deleted_at = None``.
        Both of the endpoint's guards miss that state — the replay check keys
        on ``deleted_at`` and the anchor INSERT is attempted only when
        ``existing is None`` — so without an in-flight bound the second request
        re-runs the whole cascade and emits a second
        ``account_delete_requested``, the opposite of the docstring's promise.
        Here the anchor is un-stamped but FRESH, so it must read as in flight:
        200-already, no cascade, no audit.

        RED condition: the un-stamped fresh anchor falls through to the retry
        path. MUTATION CONFIRMED RED: delete the
        ``_account_deletion_in_flight(...)`` guard from ``delete_user_account``
        — the response is 202 with ``teams_deleted == [ORG_ID]`` and the audit
        count is 1.
        """
        tc, fake, _ = sb_client
        fake.seed("organizations", [_account_org(ORG_ID)])
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])
        fake.seed("api_keys", [_key_row()])
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [ORG_ID], "claimed_org_ids": [],
            "deleted_at": None, "grace_hours": None,
            "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        }])
        as_user()

        r = tc.delete("/v1/user/account")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["already"] is True
        assert body["deleted_at"] is None      # the winner has not stamped yet
        assert body["teams_deleted"] == []

        org = next(o for o in fake.tables["organizations"] if o["id"] == ORG_ID)
        assert org.get("deleted_at") is None   # not re-cascaded
        assert all(k["revoked_at"] is None for k in fake.tables["api_keys"])
        assert len(fake.tables["account_deletions"]) == 1
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("account_delete_requested") == 0

    def test_deferred_terminal_charging_three_cases(self, sb_client, as_user,
                                                    monkeypatch):
        """#2051: the sensitive-op budget is charged EXACTLY ONCE, at the
        TERMINAL outcome.

        success → 1 charge; a mid-flight 5xx → 0 charges (an outage must not
        burn the hourly budget, nor mask itself with a stale 429 that
        outlives recovery); a refusal (429) → no charge. RED before the
        #1719 migration (check-time charging consumed the budget on 5xx).
        """
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setitem(ha_mod._SENSITIVE_OP_LIMITS, "export", 3)
        ha_mod._SENSITIVE_BUCKETS.clear()
        tc, fake, db_path = sb_client
        _seed_supabase_team(fake)
        seed_sdk = _seed_graph(db_path)  # noqa: F841
        as_user()

        def _entries():
            return sum(len(v) for v in ha_mod._SENSITIVE_BUCKETS.values())

        # (a) SUCCESS → charged exactly once, at the terminal point.
        assert tc.get(f"/v1/organizations/{ORG_ID}/export").status_code == 200
        assert _entries() == 1

        # (b) MID-FLIGHT 5xx → charges NOTHING.
        real_snapshot = ha_mod._export_graph_snapshot
        monkeypatch.setattr(
            ha_mod, "_export_graph_snapshot",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("graph down")))
        for _ in range(2):
            r = tc.get(f"/v1/organizations/{ORG_ID}/export")
            assert r.status_code == 500, r.text
        assert _entries() == 1, "a 5xx must not consume the sensitive-op budget"

        # Recovery must not yield a spurious 429 (pre-#2051 the two 5xx had
        # consumed the budget and this read 429).
        monkeypatch.setattr(ha_mod, "_export_graph_snapshot", real_snapshot)
        assert tc.get(f"/v1/organizations/{ORG_ID}/export").status_code == 200
        assert _entries() == 2

        # (c) REFUSAL (429) → nothing charged; the bucket stays at the limit.
        assert tc.get(f"/v1/organizations/{ORG_ID}/export").status_code == 200
        assert _entries() == 3  # at limit
        r = tc.get(f"/v1/organizations/{ORG_ID}/export")
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert _entries() == 3, "a refusal must not charge"
        ha_mod._SENSITIVE_BUCKETS.clear()

    def test_client_error_still_charges(self, sb_client, as_user, monkeypatch):
        """#2051 boundary: a 4xx client error IS terminal — it charges once.
        Abusive callers keep the unchanged 429 boundary; only 5xx passes
        through uncharged."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        ha_mod._SENSITIVE_BUCKETS.clear()
        tc, _, _ = sb_client
        as_user()
        # unknown team → 403 from the owner gate (a terminal client error)
        assert tc.get("/v1/organizations/nope/export").status_code == 403
        assert sum(len(v) for v in ha_mod._SENSITIVE_BUCKETS.values()) == 1
        ha_mod._SENSITIVE_BUCKETS.clear()


class TestDeferredSensitiveOpCharging:
    """#2051: `_deferred_sensitive_op` is the shared terminal-charge
    mechanism for the whole family — lock its outcome table directly:
    success and 4xx charge once; 5xx and a raw exception (rendered as 5xx by
    the global handler) charge nothing."""

    @staticmethod
    def _request():
        from starlette.requests import Request as _Request
        return _Request({"type": "http", "method": "GET", "path": "/x",
                         "headers": [], "query_string": b"",
                         "client": ("203.0.113.5", 1234)})

    def test_charge_outcome_table(self, monkeypatch):
        import asyncio
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setitem(ha_mod._SENSITIVE_OP_LIMITS, "export", 10)
        ha_mod._SENSITIVE_BUCKETS.clear()

        def _entries():
            return sum(len(v) for v in ha_mod._SENSITIVE_BUCKETS.values())

        @ha_mod._deferred_sensitive_op("export")
        async def endpoint(request, mode):
            if mode == "ok":
                return {"ok": True}
            if mode == "client":
                raise ha_mod.HTTPException(status_code=403, detail="nope")
            if mode == "server":
                raise ha_mod.HTTPException(status_code=503, detail="down")
            raise RuntimeError("boom")

        req = self._request()

        async def _call(mode):
            try:
                return await endpoint(request=req, mode=mode)
            except Exception as exc:
                return exc

        async def _scenario():
            # success → exactly one terminal charge
            assert await _call("ok") == {"ok": True}
            assert _entries() == 1
            # 4xx client error → terminal → one more charge
            assert isinstance(await _call("client"), ha_mod.HTTPException)
            assert _entries() == 2
            # 5xx server fault → UNCHARGED
            assert isinstance(await _call("server"), ha_mod.HTTPException)
            assert _entries() == 2
            # raw exception (rendered 5xx by the global handler) → UNCHARGED
            assert isinstance(await _call("raw"), RuntimeError)
            assert _entries() == 2

        # ONE loop for the whole table: a single asyncio.run keeps the
        # module-global _SENSITIVE_LOCK bound to one loop (repeated
        # asyncio.run calls would only pass while the lock stays
        # uncontended — the acquire fast path skips _get_loop).
        try:
            asyncio.run(_scenario())
        finally:
            ha_mod._SENSITIVE_BUCKETS.clear()

    def test_cancelled_mid_flight_charges(self, monkeypatch):
        """#2051 review P2: a request cancelled mid-flight (client
        disconnect / server shutdown) raises ``asyncio.CancelledError`` — a
        ``BaseException``, so it never reached the ``HTTPException`` branch
        and charged 0. That is an evasion path: a client can disconnect
        before every response and run the heavy ``import``/``export``
        uncharged without bound, and the pre-migration check-time behaviour
        charged this class. It MUST charge exactly once (delta 0 or 1 — no
        double-charge), and the cancellation must still propagate."""
        import asyncio
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setitem(ha_mod._SENSITIVE_OP_LIMITS, "export", 10)
        ha_mod._SENSITIVE_BUCKETS.clear()

        def _entries():
            return sum(len(v) for v in ha_mod._SENSITIVE_BUCKETS.values())

        @ha_mod._deferred_sensitive_op("export")
        async def endpoint(request, mode):
            if mode == "cancel_raise":
                # the cancellation boundary a disconnect/shutdown delivers
                raise asyncio.CancelledError()
            # a genuinely in-flight body, cancelled at its own await
            await asyncio.Event().wait()
            return {"ok": True}

        req = self._request()

        async def _scenario():
            # (a) CancelledError raised from the endpoint body → 1 charge.
            try:
                await endpoint(request=req, mode="cancel_raise")
            except asyncio.CancelledError:
                pass
            else:  # pragma: no cover
                raise AssertionError("cancellation must propagate")
            assert _entries() == 1, (
                "a mid-flight cancellation must charge exactly once")

            # (b) a REAL task.cancel() while the endpoint hangs at its
            # await → still exactly one charge, delivered despite the
            # cancellation (shielded), and the task stays cancelled.
            task = asyncio.ensure_future(endpoint(request=req, mode="hang"))
            await asyncio.sleep(0)  # let it reach the hang await
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:  # pragma: no cover
                raise AssertionError("the task must end cancelled")
            assert _entries() == 2, (
                "a cancelled task must charge exactly once")

        try:
            asyncio.run(_scenario())
        finally:
            ha_mod._SENSITIVE_BUCKETS.clear()

    def test_cancel_during_terminal_charge_is_not_dropped(self, monkeypatch):
        """#2051 review P2: a cancellation delivered WHILE the terminal
        charge waits on the contended ``_SENSITIVE_LOCK`` must not drop the
        charge on ANY exit branch.

        ``_SENSITIVE_LOCK`` is ONE global lock shared by all four sensitive
        ops and all IPs, so an attacker can manufacture the contention the
        way this test does: the endpoint body takes the (task-agnostic)
        lock and holds it, so the terminal charge blocks on it; the request
        task is then cancelled. Un-shielded, the ``await`` on the charge
        raises ``CancelledError`` out of the wrapper and the append never
        happens — for an operation that ALREADY EXECUTED (success and 4xx
        branches). ``_terminal_charge`` shields it, so the charge lands once
        the lock is released, while the cancellation still propagates.

        MUTATION: restore the un-shielded
        ``await _charge_sensitive_op_rate_limit(key, op)`` in any branch
        (drop the ``_terminal_charge`` helper) and the success / 4xx
        assertions fail with ``entries_after_lock_released == 0``.
        """
        import asyncio
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setitem(ha_mod._SENSITIVE_OP_LIMITS, "export", 10)
        ha_mod._SENSITIVE_BUCKETS.clear()

        def _entries():
            return sum(len(v) for v in ha_mod._SENSITIVE_BUCKETS.values())

        async def _attack(mode, *, double_cancel=False):
            acquired = asyncio.Event()

            @ha_mod._deferred_sensitive_op("export")
            async def ep(request):
                # Hold the shared lock across the terminal charge: the body
                # acquires it (asyncio.Lock is not owner-bound), signals,
                # then unwinds into the charge, which blocks on the lock
                # this same task holds.
                await ha_mod._SENSITIVE_LOCK.acquire()
                acquired.set()
                if mode == "ok":
                    return {"ok": True}
                if mode == "client":
                    raise ha_mod.HTTPException(status_code=403, detail="nope")
                raise asyncio.CancelledError()

            task = asyncio.ensure_future(ep(request=self._request()))
            await acquired.wait()
            # let the body unwind into the terminal charge, which must now
            # be blocked on the held lock
            for _ in range(5):
                await asyncio.sleep(0)
            await asyncio.sleep(0.02)
            assert not task.done(), "the terminal charge must be blocked"
            assert _entries() == 0, "nothing may charge while the lock is held"
            task.cancel()
            if double_cancel:
                await asyncio.sleep(0)
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, ha_mod.HTTPException):
                pass
            else:  # pragma: no cover
                raise AssertionError("the cancellation must propagate")
            assert _entries() == 0, "the charge is still waiting on the lock"
            ha_mod._SENSITIVE_LOCK.release()
            await asyncio.sleep(0.05)
            assert _entries() == 1, (
                f"a cancellation during the terminal charge DROPPED the "
                f"charge for mode={mode!r} (double_cancel={double_cancel})")
            # drain the shieldee before the next branch reuses the loop
            await asyncio.sleep(0)

        async def _scenario():
            for mode in ("ok", "client", "cancel"):
                ha_mod._SENSITIVE_BUCKETS.clear()
                await _attack(mode)
                ha_mod._SENSITIVE_BUCKETS.clear()
                await _attack(mode, double_cancel=True)
                ha_mod._SENSITIVE_BUCKETS.clear()

        try:
            asyncio.run(_scenario())
        finally:
            ha_mod._SENSITIVE_BUCKETS.clear()


# ═══════════════════════════════════════════════════════════════════════════
# #4029 review follow-ups: the P2-6 create-org TOCTOU guard, the P2-5 deferred
# charge for the account-delete op, and the P2-2 uuid-by-value comparison.
# ═══════════════════════════════════════════════════════════════════════════


class TestOrgCreateBlockedByPendingAccountDeletion:
    """P2-6 (#4029 review): an org created inside the grace window would be
    orphaned by the account erase.

    A delete-pending user is still authenticated during the window, so an org
    created AFTER the sweep's discovery read and BEFORE the auth erase is in
    neither the claim set nor the discovery set; the auth erase then cascades
    the user's ``org_memberships`` rows (FK ON DELETE CASCADE), leaving an org
    with ``deleted_at IS NULL`` and NO owner — invisible to
    ``_purge_deleted_orgs``. ``create_org`` must refuse while a deletion row
    exists for the caller."""

    def test_create_org_refused_while_deletion_pending(self, sb_client, as_user):
        tc, fake, _ = sb_client
        fake.seed("account_deletions", [{
            "user_id": OWNER, "org_ids": [], "claimed_org_ids": [],
            "deleted_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "grace_hours": RESTORE_WINDOW_HOURS,
        }])
        as_user()
        r = tc.post("/v1/organizations", json={"name": "acme"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error_code"] == "account_deletion_pending"
        assert fake.tables["organizations"] == []   # nothing was provisioned

    def test_create_org_allowed_without_pending_deletion(self, sb_client, as_user):
        tc, fake, _ = sb_client
        as_user()
        r = tc.post("/v1/organizations", json={"name": "acme"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "acme"
        assert len(fake.tables["organizations"]) == 1


class TestAccountDeleteDeferredCharge:
    """P2-5 (#4029 review): ``DELETE /v1/user/account`` rides the SAME
    deferred-charge seam as the rest of the sensitive-op family, so a 5xx
    control-plane fault passes through UNCHARGED (#2051)."""

    def test_account_delete_5xx_does_not_burn_the_budget(
            self, sb_client, as_user, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        ha_mod._SENSITIVE_BUCKETS.clear()
        tc, _, _ = sb_client
        as_user()
        import tortoise.supabase_control as sc

        def _boom(*args, **kwargs):
            raise ha_mod.HTTPException(status_code=500,
                                       detail="control plane down")

        monkeypatch.setattr(sc, "account_deletion_row", _boom)

        def _entries():
            return sum(len(v) for v in ha_mod._SENSITIVE_BUCKETS.values())

        for _ in range(2):
            r = tc.delete("/v1/user/account")
            assert r.status_code == 500, r.text
        assert _entries() == 0, "a 5xx must not consume the sensitive-op budget"
        ha_mod._SENSITIVE_BUCKETS.clear()


class TestUuidComparisonTolerance:
    """P2-2 (#4029 review): uuid columns are compared BY VALUE, not as strings.

    ``_is_uuid`` admits every form Postgres's uuid input accepts, while a
    ``uuid`` column comes back from PostgREST in canonical lower-case
    hyphenated form — so a non-canonical ``sub`` must still read as the stored
    owner, or a solely-owned org is judged shared and orphaned on erasure."""

    def test_same_uuid_normalises_accepted_forms(self):
        import tortoise.supabase_control as sc
        canonical = "9f2c1a40-0000-4a00-8000-000000000001"
        assert sc._same_uuid(canonical, canonical)
        assert sc._same_uuid(canonical, canonical.upper())
        assert sc._same_uuid(canonical, canonical.replace("-", ""))
        assert sc._same_uuid(canonical, "{" + canonical + "}")
        assert sc._same_uuid(None, None)
        assert not sc._same_uuid(canonical, None)   # anon owner is a 2nd owner
        assert not sc._same_uuid(canonical, _U2)
        assert not sc._same_uuid(canonical, "junk")
        assert not sc._same_uuid("junk", None)

    def test_sole_owned_org_ids_matches_a_non_canonical_sub(
            self, sb_client, monkeypatch):
        """A caller whose ``sub`` is an accepted non-canonical form of the
        stored owner must still read as the SOLE owner.

        RED condition: the comparison is a raw string compare, so the canonical
        stored id never equals the caller's upper-case sub and the org is judged
        shared. MUTATION CONFIRMED RED: revert ``_same_uuid(...)`` to ``==`` in
        ``sole_owned_org_ids`` — the result is ``[]`` instead of ``[ORG_ID]``.
        """
        import tortoise.supabase_control as sc
        tc, fake, _ = sb_client  # noqa: RUF059
        fake.seed("org_memberships", [_membership_row(user_id=OWNER)])  # canonical
        real_query = fake.query

        def _pg_uuid_equality(table, *args, **kwargs):
            # Postgres compares a uuid column BY VALUE; the fake's ``eq`` is a
            # raw string compare, which is exactly the production divergence
            # this test must not inherit.
            filters = kwargs.get("filters")
            if table == "org_memberships" and filters:
                kwargs = dict(kwargs, filters=[
                    (c, op, str(v).lower())
                    if c == "user_id" and op in ("eq", "neq")
                    and isinstance(v, str) else (c, op, v)
                    for c, op, v in filters])
            return real_query(table, *args, **kwargs)

        monkeypatch.setattr(fake, "query", _pg_uuid_equality)
        assert sc.sole_owned_org_ids(fake, OWNER.upper()) == [ORG_ID]
