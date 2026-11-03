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
import socket

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


def test_the_record_block_states_when_it_was_read(monkeypatch):
    """`fetched_at` — the age of the arming claim, as a fact of the artefact.

    #10017's purge guard refuses to delete a turn the record cannot serve back,
    and one of its preconditions is "the record is armed" (`/health` reports
    it). A captured body with NO read instant satisfies that precondition for
    ever; before this field the guard demanded `org_id`/`graph_id` instead —
    which this endpoint cannot state (a multi-tenant process serves several),
    so every real body was refused and hand-authoring was the only passing
    input (#10483 review round 4, P1). The endpoint CAN state this one.
    """
    import datetime as _dt

    record = _health(monkeypatch)["record"]

    stamp = _dt.datetime.fromisoformat(record["fetched_at"])
    assert stamp.tzinfo is not None, "the stamp must be timezone-aware (UTC)"
    age = (_dt.datetime.now(_dt.UTC) - stamp).total_seconds()
    assert 0 <= age < 60, age


def test_the_fingerprint_convention_has_one_definition(monkeypatch):
    """`dsn_fingerprint` is the seam's own; the health block must not re-derive it.

    Two copies of a secret-handling convention drift, and the copy that drifts is
    the one that prints a DSN (`record_dsn`'s malformed-value error is the other
    caller).

    The first half pins the ERROR path (a wrong fingerprint in that message is a
    disclosure). It cannot detect a re-derivation, which is why the second half
    exists: the block is fed a SENTINEL through `dsn_fingerprint` (#9548 review
    P3 — an inline `sha256(...)[:8]` in the block left the old version green).
    """
    malformed = "postgresq://typo-in-the-scheme"
    with pytest.raises(RecordWriteError) as excinfo:
        record_writer.record_dsn(malformed)
    assert dsn_fingerprint(malformed) in str(excinfo.value)
    assert malformed not in str(excinfo.value)

    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)
    monkeypatch.setattr(record_writer, "dsn_fingerprint", lambda _dsn: "SENTINEL")

    assert _health(monkeypatch)["record"]["dsn"] == "SENTINEL", (
        "the block did not CALL record_writer.dsn_fingerprint — it re-derived "
        "the slice, so the two copies can now drift and the block's copy is the "
        "one that runs on an unauthenticated endpoint"
    )


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
    # ⛔ The REFUSED path is the one an operator actually reaches with a mangled
    # paste, and it is the path with no disclosure guard until this line (#9548
    # review P2). It has to name the secret's own material directly: the previous
    # `json.dumps(...)[len(error):]` slice was offset from the FRONT of a JSON
    # object while `error` is its LAST key, so it constrained nothing, and
    # `bad not in payload` cannot see a PREFIX or a password-only disclosure.
    payload = json.dumps(body)
    assert _PASSWORD not in payload, "the health payload disclosed the password"
    assert "pw@" not in payload, (
        "the health payload disclosed the password's tail — the shape a prefix "
        "or a partial value leaks in"
    )


# ── (d) the block collects NO I/O ────────────────────────────────────────────


def test_the_health_path_opens_no_connection_to_the_record(monkeypatch):
    """/health must answer from memory alone (#2850): no connect, no hand-off.

    An armed-but-unreachable record (a dead host, a firewall dropping SYNs) must
    not be able to delay or fail a liveness probe — `CONNECT_TIMEOUT_SECONDS`
    bounds a WRITE, and a probe that paid 10 s per call would take the machine
    out of service over a monitoring nicety.

    The net RECORDS and only then raises (#9548 review P1): `_record_health` is
    deliberately per-step `try/except` — a health path must never 5xx — so an
    `_explode` that only raises is swallowed by the code under test and the test
    passes over a real connect. The assertion is on the RECORD, which no
    `except` inside the handler can erase.

    ⚠️ The boundary this net can actually see, stated rather than implied: it
    covers every **Python-level** connect on this path — the seam's own
    `_default_connect` hook (what `RecordWriter.write` uses) and the `socket`
    module entry points. It cannot see a C-level connect (`psycopg2.connect`
    reaches libpq, which does not call back into Python's `socket`), nor a
    `to_thread` hand-off. What rules those out is structural, not a net: the
    block's only imports are `record_writer` and `monitoring`, and the body a few
    lines below contains no driver call at all — so a future one would be visible
    in review as a new call, not as a regression this test would silently bless.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)

    attempts: list[tuple] = []

    def _note(name):
        def _hook(*a, **_kw):  # pragma: no cover - the assertion IS the record
            attempts.append((name, a, _kw))
            raise AssertionError(f"/health called {name}")

        return _hook

    monkeypatch.setattr(record_writer, "_default_connect", _note("_default_connect"))
    monkeypatch.setattr(socket.socket, "connect", _note("socket.socket.connect"))
    monkeypatch.setattr(socket, "create_connection", _note("socket.create_connection"))

    body = _health(monkeypatch)

    assert attempts == [], (
        "/health opened a connection to the record — an unreachable record can "
        f"then delay or fail a liveness probe (#2850). Observed: {attempts}"
    )
    assert body["status"] == "ok"
    assert body["record"]["ok"] is True
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


def test_an_unread_tenancy_resolution_does_not_publish_the_safe_value(monkeypatch):
    """#9548 review P2: `False` here means "multi-tenant", i.e. the SAFE answer.

    A swallowed read therefore renders a broken tenancy safety check as a PASS —
    `status: ok`, `single_tenant_declared: false`, no marker anywhere in the
    payload — which is the same fail-open shape the counters one screen up were
    just fixed for. ``ok`` must be false and the reason must be published in its
    own key, so `error` keeps meaning *a value was SET and refused*.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)

    def _explode():
        raise RuntimeError("tenancy resolution exploded")

    monkeypatch.setattr(record_writer, "single_tenant_declared", _explode)

    body = _health(monkeypatch)

    assert body["status"] == "degraded", (
        "an unreadable tenancy check reported a healthy deployment"
    )
    record = body["record"]
    assert record["ok"] is False
    assert record["error"] is None, (
        "a tenancy failure was published as a DSN problem"
    )
    assert "tenancy resolution exploded" in record["tenancy_error"]
    assert record["armed"] is True, (
        "the arming fact was correct and must survive a tenancy failure"
    )


# ── (g) a value no connection can USE must not read `armed` ──────────────────
#
# #9548 review P1. `armed` is this PR's product, so it must be unable to answer
# `true` for a value the record cannot use. The scheme check alone could not do
# that: a clipped `gh secret set` paste (`postgresql://user:pw@`) passed it, and
# libpq — which does NOT fail a hostless URI, it fills the host from `PGHOST` or
# a unix socket — would then connect somewhere nobody named. `/health` reported
# `armed: true, ok: true, status: ok` while the record stayed empty: the same
# false green this block exists to end, one predicate in.

#: Every shape here can never reach the record, and each is a paste an operator
#: plausibly produces. Two facts decide them, both from the string alone.
UNUSABLE_DSN_SHAPES = [
    ("postgresql://", "no host, no database"),
    ("postgres://", "no host, no database"),
    ("postgresql:///postgres", "empty authority ⇒ local socket"),
    ("postgresql://tortoise_record_app:pw@", "the clipped-paste shape"),
    ("postgresql://[::1:5432/postgres", "not a readable URI (invalid IPv6)"),
    (
        "postgresql:// tortoise_record_app:pw@pooler.supabase.com:5432/postgres",
        "whitespace-mangled paste — not a URI at all",
    ),
]


@pytest.mark.parametrize("shape,why", UNUSABLE_DSN_SHAPES)
def test_the_seam_refuses_a_value_no_connection_can_use(shape, why):
    """The refusal is in `record_dsn()`, not in the reader — one definition.

    This is the WRITE path's rule: a reader that re-checked would be a second
    definition of "usable", and the two would disagree exactly when it matters.
    It also stops the write path handing libpq a hostless DSN, the shape
    `record_backup.libpq_env` already refuses for this same variable.
    """
    with pytest.raises(RecordWriteError) as excinfo:
        record_writer.record_dsn(shape)

    message = str(excinfo.value)
    assert "misconfiguration, not an unarmed record" in message, (
        f"a hostless value must be a MISCONFIGURATION, not an unarmed record "
        f"({why}) — reason: {message}"
    )
    assert dsn_fingerprint(shape) in message, (
        "the refusal must name the value by fingerprint"
    )
    assert shape not in message, (
        "the refusal must never carry the value itself — it may hold a password"
    )


@pytest.mark.parametrize("shape,why", UNUSABLE_DSN_SHAPES)
def test_an_unusable_dsn_degrades_health_instead_of_reporting_armed(
    monkeypatch, shape, why
):
    """`/health` must not be able to answer `armed: true` for such a value.

    `status` is the field an operator (and the runbook's pass bar) reads, so
    leaving it `ok` and adding a field to be remembered is still the false
    green — the reading has to degrade.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, shape)

    body = _health(monkeypatch)

    assert body["status"] == "degraded", f"green for an unusable value: {why}"
    record = body["record"]
    assert record["armed"] is False, f"armed for an unusable value: {why}"
    assert record["ok"] is False
    assert record["error"], "a refused value must say why"
    assert dsn_fingerprint(shape) in record["error"]
    assert shape not in json.dumps(body), "the health payload disclosed the value"


@pytest.mark.parametrize(
    "shape",
    [
        "postgresql://tortoise_record_app:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
        "postgresql://record/db",
        # A password that merely CONTAINS `@` must still parse as one DSN.
        "postgresql://svc:p@ss@host.example.com:5432/postgres",
    ],
)
def test_a_usable_value_is_still_accepted(monkeypatch, shape):
    """The opposite direction: the host rule must not refuse what WORKS.

    A guard that only ever refuses is indistinguishable from a hardcoded
    refusal, so the accepted shapes are pinned beside the refused ones.
    """
    assert record_writer.record_dsn(shape) == shape

    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, shape)

    record = _health(monkeypatch)["record"]
    assert record["armed"] is True, f"a usable value was refused: {shape}"
    assert record["ok"] is True
    assert record["error"] is None


# ── (h) a counter snapshot that cannot be READ must not report ok ────────────


def test_a_failing_counter_snapshot_does_not_fail_open(monkeypatch):
    """#9548 review P2: a monitoring surface that cannot read itself is not ok.

    The pre-fix block recycled `error` for a counter failure and left `ok: true`,
    so a broken counter snapshot produced `status: ok` with `error` non-null —
    which the docstring and the runbook both define as "the DSN was SET and is
    unusable", sending an operator to rotate a secret that was fine. And
    `**counters` was spread AFTER `"error": None`, so the counter's own `error`
    key overwrote the record-level one by dict-literal ordering.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)

    def _explode():
        raise RuntimeError("counter snapshot exploded")

    monkeypatch.setattr(monitoring, "record_write_failure_counts", _explode)

    body = _health(monkeypatch)

    assert body["status"] == "degraded", (
        "a health surface that cannot read its own counters reported healthy"
    )
    record = body["record"]
    assert record["ok"] is False
    # The documented pairing holds: `error` is reserved for a refused VALUE.
    assert record["error"] is None, (
        "a counter failure was published as a DSN problem — the operator is "
        "sent to rotate a secret that is fine"
    )
    assert "counter snapshot exploded" in record["counters_error"]
    # The three keys are PRESENT and null: an unread counter is not a zero, and
    # `.get("write_failed", 0)` must not read a passing zero from a surface that
    # never answered.
    for key in (
        "record_write_failures",
        "record_non_journal_skips",
        "record_position_unproven",
    ):
        assert key in record, f"`{key}` vanished, so a `.get(..., 0)` reads zero"
        assert record[key] is None, f"`{key}` is not null on an unread snapshot"
    assert record["armed"] is True, (
        "the arming fact was correct and must survive a counter failure"
    )
