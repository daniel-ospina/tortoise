"""#8544 — the self-host off-box backup DRIVER.

Pins the execution half of the #8543 owner decision (durability parity with
hosted): a self-host deployment that declares a COMPLETE off-box target must
actually produce an archive, and a run that cannot must fail loudly rather than
leave a "declared" deployment with no copy.

The driver reuses ``hosted_backup.create_backup`` (the per-graph primitive the
hosted sweep calls) so the artifact is the same shape a hosted restore reads.
These tests run it against the embedded engine and a ``MemoryStorage`` seam —
no network, no R2.
"""
from __future__ import annotations

import threading
import time

import pytest

from tortoise.selfhost_backup import (
    DEFAULT_INTERVAL_SECONDS,
    INTERVAL_ENV,
    MIN_INTERVAL_SECONDS,
    OffboxBackupDriver,
    offbox_backup_interval_seconds,
    selfhost_graph_name,
)


class TestInterval:
    def test_default_and_override(self):
        assert offbox_backup_interval_seconds({}) == DEFAULT_INTERVAL_SECONDS
        assert offbox_backup_interval_seconds({INTERVAL_ENV: "120"}) == 120

    @pytest.mark.parametrize("raw,expected", [
        ("0", MIN_INTERVAL_SECONDS),
        ("-5", MIN_INTERVAL_SECONDS),
        ("1", MIN_INTERVAL_SECONDS),
        ("", DEFAULT_INTERVAL_SECONDS),
        ("soon", DEFAULT_INTERVAL_SECONDS),
        (None, DEFAULT_INTERVAL_SECONDS),
    ])
    def test_clamped_and_never_raises(self, raw, expected):
        """A cadence knob must never be able to wedge a boot — a typo'd "0"
        becomes the floor, not a busy loop, and garbage falls back to the
        default."""
        assert offbox_backup_interval_seconds({INTERVAL_ENV: raw}) == expected


class TestGraphName:
    def test_matches_the_sdk_derivation(self):
        """The archive must be keyed to the graph the daemon actually opens.

        ``selfhost_graph_name`` derives through ``sdk._derive_graph_name`` (the
        ONE derivation, #4240 F2); this asserts the value it yields for the
        self-host namespace is the org graph the REST/MCP doors open.
        """
        from tortoise.mcp_auth import SELFHOST_ORG_ID
        from tortoise.sdk import _derive_graph_name

        assert selfhost_graph_name() == f"org_{SELFHOST_ORG_ID}" == "org_selfhost"
        assert selfhost_graph_name() == _derive_graph_name(
            namespace=SELFHOST_ORG_ID, graph_name=None, uri_graph=None)


@pytest.fixture
def embedded_sdk(tmp_path):
    """An embedded self-host SDK holding one node — the driver's dump source."""
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(db_path=str(tmp_path / "tortoise.db"), namespace="selfhost")
    proj = sdk._get_proj()
    proj.g.query("CREATE (n:Point {id:'p1', content:'hello'})")
    try:
        yield sdk
    finally:
        sdk.close()


@pytest.fixture(autouse=True)
def _archive_key(monkeypatch):
    """A valid archive key for every test in this module.

    ``encrypt_backup`` fail-closes without ``TORTOISE_BACKUP_KEY`` (#8544 makes
    that key part of the declared target for exactly this reason) — a run test
    is meaningless without one, and an ephemeral key would produce an archive
    nobody could restore.
    """
    import base64
    import secrets

    monkeypatch.setenv(
        "TORTOISE_BACKUP_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )


class TestRunOnce:
    def test_uploads_a_read_back_verified_archive(self, embedded_sdk):
        """The archive lands under this deployment's pool and carries the
        read-back coverage proof (#5062) — not just a self-reported counter."""
        from tortoise.hosted_backup import MemoryStorage
        from tortoise.selfhost_backup import run_selfhost_offbox_backup

        store = MemoryStorage()
        result = run_selfhost_offbox_backup(storage=store, sdk=embedded_sdk)

        assert result["status"] == "backed_up"
        assert result["org_id"] == "selfhost"
        assert result["graph_name"] == "org_selfhost"
        assert result["node_count"] >= 1
        assert result["backup_id"].startswith("selfhost/")

        keys = store.list("backups/selfhost/")
        assert any(k.endswith("/dump.enc") for k in keys), keys
        assert any(k.endswith("/manifest.json") for k in keys), keys
        assert any(k.endswith("/ledger.json") for k in keys), keys

    def test_the_archive_restores_back_into_a_graph(self, embedded_sdk):
        """The driver's output must be a RECOVERY, not only a write.

        A drill-mode restore into a scratch graph is the strongest check
        available without a running deployment: it proves the uploaded artifact
        decrypts, passes the manifest + payload isolation guards, and rebuilds
        the content the deployment held. The self-hosted DR *drill* on a live
        deployment is still owed (docs/durability-posture.md says so) — this
        pins the recovery path, not the operational exercise.
        """
        from tortoise.hosted_backup import MemoryStorage, restore_backup
        from tortoise.selfhost_backup import (
            _NoControlPlane,
            run_selfhost_offbox_backup,
        )

        store = MemoryStorage()
        res = run_selfhost_offbox_backup(storage=store, sdk=embedded_sdk)
        out = restore_backup(
            embedded_sdk._get_proj().db, _NoControlPlane(), store,
            f"backups/{res['backup_id']}/dump.enc",
            org_id="selfhost", graph_name="org_selfhost",
            target_graph="_drill_8544", drill=True,
        )
        assert out["restored"]["nodes"] >= 1, out
        rows = embedded_sdk._get_proj().db.select_graph("_drill_8544").query(
            "MATCH (p:Point {id:'p1'}) RETURN count(p)").result_set
        assert int(rows[0][0]) == 1, "the restored graph lost the seeded Point"

    def test_prunes_the_pool_with_the_shared_retention_triple(
            self, embedded_sdk, monkeypatch):
        """An unbounded bucket is a cost defect: the driver prunes its OWN pool
        using the reviewed triple (``docs/retention-and-deletion.md``), not a
        second hardcoded copy of it."""
        from tortoise import selfhost_backup
        from tortoise.backup_config import BackupConfig
        from tortoise.hosted_backup import MemoryStorage

        seen: dict = {}

        def _fake_prune(storage, org_id, **kw):
            seen["org_id"] = org_id
            seen.update(kw)
            return []

        monkeypatch.setattr("tortoise.hosted_backup.prune_backups", _fake_prune)
        store = MemoryStorage()
        selfhost_backup.run_selfhost_offbox_backup(storage=store, sdk=embedded_sdk)

        assert seen["org_id"] == "selfhost"
        assert seen["keep_hourly"] == BackupConfig.retention_hourly
        assert seen["keep_daily"] == BackupConfig.retention_daily
        assert seen["keep_weekly"] == BackupConfig.retention_weekly

    def test_a_prune_failure_does_not_fail_a_durable_archive(
            self, embedded_sdk, monkeypatch):
        """The archive is already uploaded + verified when the prune runs; a
        prune error must be visible (logged) but must not discard the backup."""
        from tortoise import selfhost_backup
        from tortoise.hosted_backup import MemoryStorage

        def _boom(*_a, **_kw):
            raise RuntimeError("bucket busy")

        monkeypatch.setattr("tortoise.hosted_backup.prune_backups", _boom)
        result = selfhost_backup.run_selfhost_offbox_backup(
            storage=MemoryStorage(), sdk=embedded_sdk)
        assert result["status"] == "backed_up"


class TestDriverLoop:
    def test_first_run_is_after_one_interval_not_at_boot(self):
        """A restart burst would hammer the store, and arming the loop in a
        short-lived process (every in-process TestClient boot) must not reach
        the network. The cadence is one run per interval."""
        calls: list[int] = []
        slept: list[float] = []
        release = threading.Event()

        def _run():
            calls.append(1)
            return {"status": "backed_up"}

        def _sleep(seconds):
            slept.append(seconds)
            if len(slept) == 1:
                return          # first interval elapses
            release.wait(5)     # hold the second wait so the thread stays put

        driver = OffboxBackupDriver(interval_seconds=42, run=_run, sleep=_sleep)
        driver.start()
        for _ in range(200):
            if calls:
                break
            time.sleep(0.01)
        driver.stop(timeout=1)
        release.set()

        assert calls == [1], "exactly one run per elapsed interval"
        assert slept and slept[0] == 42, slept

    def test_a_failing_run_is_counted_and_logged_never_raised(self, caplog):
        def _boom():
            raise RuntimeError("R2 unreachable")

        driver = OffboxBackupDriver(interval_seconds=60, run=_boom)
        with caplog.at_level("ERROR"):
            assert driver.run_once() is None
        assert driver.failures == 1
        assert driver.runs == 0
        assert "R2 unreachable" in (driver.last_error or "")
        assert any(r.levelname == "ERROR" for r in caplog.records)

        # Recovery clears the error state on the next success.
        driver._run = lambda: {"status": "backed_up"}
        assert driver.run_once() is not None
        assert driver.runs == 1 and driver.last_error is None

    def test_start_is_idempotent_and_stop_joins(self):
        blocker = threading.Event()
        driver = OffboxBackupDriver(
            interval_seconds=60,
            run=lambda: {"status": "backed_up"},
            sleep=blocker.wait,
        )
        assert driver.start() is True
        assert driver.start() is False, "a second start must not spawn a thread"
        driver.stop(timeout=2)
        assert driver._thread is None
        blocker.set()


class TestLifespanWiring:
    """The daemon arms the driver from the SAME state the boot signal reports.

    A background thread that talks to a remote store is only safe to arm when
    the deployment actually declared a complete target — the two surfaces must
    not be able to disagree about whether this deployment claims an off-box
    copy. No run happens inside the test: the first run is one interval out.
    """

    def _boot(self, monkeypatch, tmp_path, **env):
        import importlib

        from starlette.testclient import TestClient

        from tortoise import selfhost

        monkeypatch.setenv("TORTOISE_DB_URI", "")  # embedded mode
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "selfhost.db"))
        for var in selfhost._OFF_BOX_TARGET_ENVS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(selfhost._OFF_BOX_DECLINE_ENV, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        importlib.reload(selfhost)
        return selfhost, TestClient(selfhost.app)

    def test_complete_target_arms_the_driver_and_shutdown_stops_it(
            self, monkeypatch, tmp_path):
        from tortoise import selfhost, selfhost_backup

        state, tc = self._boot(
            monkeypatch, tmp_path,
            **{n: "x" for n in selfhost._OFF_BOX_TARGET_ENVS},
        )
        assert state.off_box_backup_state() == state.OFF_BOX_CONFIGURED
        with tc:
            assert selfhost_backup._DRIVER is not None, "driver not armed"
            assert selfhost_backup._DRIVER._thread is not None
        assert selfhost_backup._DRIVER is None, "shutdown left the driver armed"

    def test_partial_target_arms_nothing(self, monkeypatch, tmp_path):
        """A driver with no complete target is an inert enforcer — exactly the
        category-A polarity #8544 fixes. The lifespan must not arm one."""
        from tortoise import selfhost_backup

        state, tc = self._boot(monkeypatch, tmp_path, R2_BUCKET="b")
        assert state.off_box_backup_state() == state.OFF_BOX_ABSENT
        with tc:
            assert selfhost_backup._DRIVER is None
