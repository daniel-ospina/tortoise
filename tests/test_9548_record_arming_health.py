"""#9548 (R1): is the record ARMED on the hosted deployment? — ``/health`` must say.

The record seam arms on PRESENCE (`record_writer` decision 2): unset
``TORTOISE_RECORD_DSN`` ⇒ ``NOT_ARMED`` ⇒ zero statements, no connection
attempt, no exception. That default is right, and it made the deployment's
arming state **unobservable from outside the process** — so `#9548` sat as a P0
with the variable set nowhere, the record inert, and nothing red. Measured
`docs/infra-runbook.md` (§"Arming the Postgres record") before this change: the
hosted app exposes no ``/metrics`` route, ``GET /health`` carried none of the
record counters, and the only reads that existed were `fly logs` greps.

These tests pin the read that closes that gap — ``/health``'s additive
``record`` block — and are deliberately DB-free and app-boot-free (the
`test_backup_watcher_health.py` pattern): ``tortoise.hosted_api`` is imported
with the pepper env set and the DB probe stubbed, so nothing here touches
FalkorDB, Postgres or the network.

Each test names the thing that would be FALSE without the block, so a revert to
the old payload fails here rather than in production:

  * (a) unarmed reads as a SUPPORTED state, not a fault — and says so;
  * (b) armed reports the fact and a fingerprint, NEVER the DSN value;
  * (c) set-but-unusable degrades `status` (the silent-arming defect itself);
  * (d) the block collects no I/O — an unreachable record cannot fail /health;
  * (e) the three record counters ride along under `metrics()`'s own names, so
        indicator (b) of the issue (`record_write_failures` = 0) has a reader on
        the deployment it is claimed about;
  * (f) `TORTOISE_RECORD_SINGLE_TENANT` — which the issue requires stay UNSET —
        is reported, so "left unset" is checkable rather than assumed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os

import pytest

# Auth module import requires the pepper; set it before importing the app.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import tortoise.hosted_api as hosted_api
from tortoise import monitoring, record_writer
from tortoise.record_writer import RecordWriteError, dsn_fingerprint

#: A realistic pooler DSN: the shape `docs/infra-runbook.md` §"Arming the Postgres
#: record" mandates (R6's app role, URL-encoded password). Its PASSWORD is the one
#: string that must never appear in a health payload.
_PASSWORD = "s3cr3t-p%40ss"
RECORD_DSN = (
    "postgresql://tortoise_record_app.ybetwichurajbfswfeqa:"
    f"{_PASSWORD}@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    "?sslmode=require&connect_timeout=5"
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """No ambient record env may decide these assertions (or leak out of them)."""
    for name in (
        record_writer.RECORD_DSN_ENV,
        record_writer.RECORD_SINGLE_TENANT_ENV,
        record_writer.RECORD_ORG_ENV,
        record_writer.RECORD_GRAPH_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def _stub_health_dependencies(monkeypatch) -> None:
    """Neutralise everything /health reads that is NOT the record block.

    The DB verdict is a snapshot from the background refresher (#2850), so a cold
    probe would report ``{"ok": False, "error": "probe has not produced a
    result"}`` and fail an ``ok``-status assertion for the wrong reason. The
    backup watcher is disabled (its legitimate, ``ok`` state) for the same reason.
    """
    verdict = {"ok": True, "latency_ms": 0.1, "error": None}
    monkeypatch.setattr(hosted_api, "_probe_db", lambda: dict(verdict))
    health_probe = getattr(hosted_api, "_HEALTH_PROBE", None)
    if health_probe is not None:
        monkeypatch.setattr(health_probe, "snapshot", lambda: dict(verdict))
    monkeypatch.setattr(hosted_api, "_WATCHER", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_START_ERROR", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_EXPECTED", False)


def _health(monkeypatch) -> dict:
    _stub_health_dependencies(monkeypatch)
    return asyncio.run(hosted_api.health())


# ── (a) unarmed is a SUPPORTED state and is distinguishable from armed ───────


def test_unarmed_deployment_reports_the_state_and_stays_ok(monkeypatch):
    """The P0's actual state on the hosted app: no DSN ⇒ record inert.

    Before this block that state was indistinguishable from a healthy armed
    deployment from outside the process — which is why nothing went red for
    days. Unarmed must read as the documented default-off, NOT as a fault.
    """
    body = _health(monkeypatch)

    assert body["status"] == "ok", "off-by-policy must not degrade liveness"
    record = body["record"]
    assert record["armed"] is False
    assert record["dsn"] is None
    assert record["error"] is None, (
        "absent is not an error — `error` is reserved for a value that was SET "
        "and cannot be used, so an operator can tell the two apart"
    )
    assert record["ok"] is True


def test_the_health_payload_keeps_its_existing_contract(monkeypatch):
    """The record block is ADDITIVE — the documented shape must not move."""
    body = _health(monkeypatch)

    for key in ("status", "db", "probe", "loop_stale_ms", "backup_watcher", "change_feed"):
        assert key in body, f"/health lost its `{key}` field — the block is additive only"
    assert body["db"]["ok"] is True


# ── (b) armed reports the fact, and NEVER the value ──────────────────────────


def test_armed_deployment_reports_armed(monkeypatch):
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)

    record = _health(monkeypatch)["record"]

    assert record["armed"] is True
    assert record["ok"] is True
    assert record["error"] is None


def test_the_dsn_value_is_never_published_only_its_fingerprint(monkeypatch):
    """A DSN is secret material: /health is unauthenticated and world-readable.

    The fingerprint is what lets an operator tell the deployed value from a stale
    one without either of them being disclosed.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)

    body = _health(monkeypatch)
    payload = json.dumps(body)

    assert body["record"]["dsn"] == hashlib.sha256(RECORD_DSN.encode()).hexdigest()[:8]
    assert _PASSWORD not in payload
    assert "pooler.supabase.com" not in payload
    assert RECORD_DSN not in payload


def test_the_fingerprint_convention_has_one_definition(monkeypatch):
    """`dsn_fingerprint` is the seam's own; the health block must not re-derive it.

    Two copies of a secret-handling convention drift, and the copy that drifts is
    the one that prints a DSN (`record_dsn`'s malformed-value error is the other
    caller).
    """
    malformed = "postgresq://typo-in-the-scheme"
    with pytest.raises(RecordWriteError) as excinfo:
        record_writer.record_dsn(malformed)
    assert dsn_fingerprint(malformed) in str(excinfo.value)
    assert malformed not in str(excinfo.value)


# ── (c) set-but-unusable DEGRADES — the silent-arming defect itself ──────────


def test_a_set_but_unusable_dsn_degrades_health(monkeypatch):
    """A typo'd DSN is not "unarmed": it is wanted-but-broken.

    `record_dsn()` refuses it (so every capture counts `malformed_dsn` and the
    record stays empty). Leaving `/health` `ok` in that state is exactly the
    "nothing goes red" class #9548 exists to end, so this one case folds into
    `status` — the `backup_watcher` rule: off-by-policy stays ok, wanted-but-
    broken degrades.
    """
    bad = "postgresq://tortoise_record_app:pw@pooler.example.com:5432/postgres"
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, bad)

    body = _health(monkeypatch)

    assert body["status"] == "degraded"
    record = body["record"]
    assert record["armed"] is False
    assert record["ok"] is False
    assert record["error"], "a set-but-unusable value must say why"
    assert dsn_fingerprint(bad) in record["error"], (
        "the error names the value by fingerprint, never by content"
    )
    assert bad not in json.dumps(body)
    assert "pw" not in json.dumps(body["record"])[len(record["error"]):]


# ── (d) the block collects NO I/O ────────────────────────────────────────────


def test_the_health_path_opens_no_connection_to_the_record(monkeypatch):
    """/health must answer from memory alone (#2850): no connect, no hand-off.

    An armed-but-unreachable record (a dead host, a firewall dropping SYNs) must
    not be able to delay or fail a liveness probe — `CONNECT_TIMEOUT_SECONDS`
    bounds a WRITE, and a probe that paid 10 s per call would take the machine
    out of service over a monitoring nicety.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)

    def _explode(*_a, **_kw):  # pragma: no cover - the assertion IS the raise
        raise AssertionError("/health opened a connection to the record")

    monkeypatch.setattr(record_writer, "_default_connect", _explode)

    body = _health(monkeypatch)

    assert body["status"] == "ok"
    assert body["record"]["armed"] is True


# ── (e) indicator (b): the counters have a reader on the deployment ──────────


def test_the_record_counters_ride_along_under_the_metrics_names(monkeypatch):
    """`record_write_failures` = 0 is #9548 indicator (b) — it needs a reader.

    The hosted app serves no `/metrics` route, so `monitoring.metrics()`'s record
    keys were unreachable on the very deployment they are claimed about. The
    block carries the same three keys, with the same names, so the two surfaces
    cannot drift: a `no_durable_journal` decline (#9231) and a healed
    `position_unproven` append (#9307) stay OUT of the failure counter.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)
    before = monitoring.record_write_failure_counts().get("write_failed", 0)
    before_skips = monitoring.record_non_journal_skip_counts().get("no_durable_journal", 0)
    before_unproven = monitoring.record_position_unproven_count()

    monitoring.record_record_write_failure("write_failed")
    record_writer._count_non_journal_skip("no_durable_journal")
    monitoring.record_position_unproven()

    record = _health(monkeypatch)["record"]

    assert record["record_write_failures"]["write_failed"] == before + 1
    assert record["record_non_journal_skips"]["no_durable_journal"] == before_skips + 1
    assert record["record_position_unproven"] == before_unproven + 1


def test_a_healthy_armed_deployment_reports_the_counters_faithfully(monkeypatch):
    """The block MIRRORS the seam's counters — it never invents or filters them.

    A health surface that reported its own idea of "healthy" would be a second
    source of truth beside the counter the write path increments, and the two
    would disagree exactly when it matters. (This asserts equality with the live
    snapshot rather than an empty dict, because the counter is process-global and
    an earlier test in this session may legitimately have incremented it.)
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)

    record = _health(monkeypatch)["record"]

    assert record["record_write_failures"] == monitoring.record_write_failure_counts()
    assert record["record_non_journal_skips"] == monitoring.record_non_journal_skip_counts()
    assert record["record_position_unproven"] == monitoring.record_position_unproven_count()
    # No invented reasons, and no reason it was never told about.
    assert "escalated" not in record["record_write_failures"]
    assert all(isinstance(v, int) for v in record["record_write_failures"].values())


# ── (f) the issue's "leave SINGLE_TENANT unset" is checkable ─────────────────


def test_multi_tenancy_is_the_reported_default(monkeypatch):
    """#9548 requires `TORTOISE_RECORD_SINGLE_TENANT` stay UNSET on hosted.

    Unset ⇒ multi-tenant ⇒ the process-global org/graph pair is REFUSED rather
    than silently used (#9192). Asserting "unset" was previously impossible from
    outside the process; the block reports the resolution, not the raw env.
    """
    record = _health(monkeypatch)["record"]

    assert record["single_tenant_declared"] is False


def test_a_declared_single_tenant_deployment_is_reported(monkeypatch):
    """The other half — without it the field above could be a hardcoded False."""
    monkeypatch.setenv(record_writer.RECORD_SINGLE_TENANT_ENV, "1")

    assert _health(monkeypatch)["record"]["single_tenant_declared"] is True
