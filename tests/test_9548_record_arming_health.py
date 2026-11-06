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
        is reported, so "left unset" is checkable rather than assumed;
  * (i) the POSITIVE channel — `record_writes_applied` / `record_last_write_at`,
        counted after the COMMIT, `None` (never `0`) when unread, and pinned
        against `metrics()`'s own key names so a rename cannot silently make this
        block's claim false.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import sys

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


# ── (i) the POSITIVE channel: did the record ever actually WRITE? ────────────
#
# Indicator (a) of #9548 is "a captured session writes `journal_event` + `point`
# rows", and indicator (b) is "`record_write_failures` stays at 0". Every
# record counter on the deployment was a FAILURE channel, so `{}` was equally
# consistent with "writes are landing" and with "the write path has never been
# reached" — the ambiguity that let this P0 be closed and reopened. These pin
# the two fields that make the POSITIVE claim checkable from one curl:
# `record_writes_applied` (a monotone count) and `record_last_write_at` (the
# instant, which a counter cannot express), and the writer-side increments that
# feed them.

class _CommittedConnection:
    """The minimum a live write touches: commit, then close."""

    def __init__(self) -> None:
        self.commits = 0
        self.closed = 0

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed += 1


def _applied(connection, *, first_seq: int = 1) -> record_writer.RecordWrite:
    return record_writer.RecordWrite(
        outcome=record_writer.RecordWriteOutcome.APPLIED,
        events=1, statements=4,
        first_seq=first_seq, last_seq=first_seq,
        connection=connection,
    )


def test_a_committed_live_write_increments_the_applied_counter(monkeypatch):
    """The positive signal is produced by the REAL seam, not by a test stub.

    A counter nothing increments is the "signal with no channel" class this
    whole file exists to end (#3677/#3995): the payload would carry
    `record_writes_applied: 0` forever and read as "the record never works".
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)
    conn = _CommittedConnection()
    monkeypatch.setattr(record_writer, "write_records",
                        lambda records, **kw: _applied(conn, first_seq=kw.get("first_seq", 1)))

    before = monitoring.record_write_applied_count()
    with record_writer.use_record_tenant("org-9548", "graph-9548"):
        result = record_writer.write_live_record({"type": "Point", "id": "p1"},
                                                 durable=True, seq=7)

    assert result is not None and result.applied is True, result
    assert conn.commits == 1, "the fixture must have committed — that is the claim"
    assert monitoring.record_write_applied_count() == before + 1, (
        "an APPLIED, COMMITTED write produced no positive signal — the deployment "
        "still cannot distinguish 'writing' from 'never reached'"
    )
    stamp = monitoring.record_last_write_at()
    assert stamp is not None, "the instant of the last write was never recorded"
    import datetime as _dt
    parsed = _dt.datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None, "the stamp must be timezone-aware (UTC)"


def test_an_uncommitted_or_refused_write_does_not_increment_the_counter(monkeypatch):
    """Only a COMMIT counts. `APPLIED` names "statements ran", not "it persisted"."""
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)

    def _explode(records, **kw):
        raise record_writer.RecordConnectError("the record is unreachable")

    monkeypatch.setattr(record_writer, "write_records", _explode)

    before = monitoring.record_write_applied_count()
    with record_writer.use_record_tenant("org-9548", "graph-9548"):
        result = record_writer.write_live_record({"type": "Point", "id": "p2"},
                                                 durable=True, seq=8)

    assert result is not None and result.applied is False, result
    assert monitoring.record_write_applied_count() == before, (
        "a FAILED write was counted as an applied one — the positive channel "
        "would then read 'the record works' from a record that never answered"
    )


def test_an_armed_deployment_reports_the_writes_it_applied(monkeypatch):
    """The block carries the positive channel, under `metrics()`'s own names."""
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)
    monkeypatch.setattr(monitoring, "record_write_applied_count", lambda: 7)
    monkeypatch.setattr(monitoring, "record_last_write_at",
                        lambda: "2026-11-05T21:07:11+00:00")

    record = _health(monkeypatch)["record"]

    assert record["record_writes_applied"] == 7
    assert record["record_last_write_at"] == "2026-11-05T21:07:11+00:00"
    assert record["ok"] is True


def test_the_positive_pair_is_named_by_metrics_too(monkeypatch):
    """The block's claim is *"under the SAME names metrics() publishes"* — pin it.

    Without this, renaming the two `metrics()` keys leaves every leg above green
    (they pin the /health literals only) while the docstring and the runbook both
    become false — the paper-only claim this codebase treats as a defect. The
    sibling counter has exactly this pin in
    `tests/test_9078_record_written_by_production_path.py`; this is its other
    half. Asserted against the real `metrics()` dict, key-set form, so the test
    cannot pass vacuously on a substring of some other key.

    ⛔ It also pins the docstring that makes the claim, AT the claim: the pair's own
    bullet (``record_writes_applied`` / ``record_last_write_at`` — the POSITIVE
    channel) and the "under the SAME names ``metrics()`` publishes" sentence — a
    bare mention test was measured GREEN with both broken (#9548 review P2). The
    runbook's field row is pinned next, in `tests/test_deploy_workflow.py`, and for
    a stated reason: it runs on a docs-only diff and this file does not.
    """
    published = monitoring.metrics()

    assert {"record_writes_applied", "record_last_write_at"} <= set(published), (
        "the /health block documents these as `metrics()`'s own names, and "
        f"metrics() publishes {sorted(published)}"
    )
    # …and the claim must be pinned WHERE IT IS MADE, not merely mentioned: the
    # docstring says the record counters are published "under the SAME names
    # ``metrics()`` publishes". A bare `name in docstring` was measured GREEN on
    # both halves being broken — a renamed bullet head (`applied_writes` /
    # `last_write_at`) still mentions the real names later in the same bullet, and
    # "SAME names" inverted to "DIFFERENT names" matched nothing. So the pair's
    # bullet and the SAME-names sentence are both pinned, on a whitespace-normalised
    # copy so a reflow cannot red them (#9548 review P2).
    # ⚠️ The SAME-names pin runs to the END of its sentence — the full stop after
    # `is claimed about.` — and each earlier stop point was MEASURED open, not
    # assumed: stopping at `publishes` stayed green on a narrowing appended there,
    # and stopping at `(#9188/#9231/#9307)` stayed green on one appended after the
    # citation. So a narrowing placed anywhere INSIDE this sentence reds the pin.
    # ⛔ What it does NOT bind, stated so no reader over-trusts it: both halves below
    # are substring memberships, so a narrowing introduced as a SEPARATE NEW SENTENCE
    # after `is claimed about.` (e.g. "Separately, the positive pair is documented
    # elsewhere") still passes. Closing that needs sentence-level parsing of the
    # docstring — deliberately out of scope for a prose pin; treat this as
    # "the claim's own sentence is intact", never as "the whole docstring agrees with
    # it" (#11128 review P3 at 786baf3e7).
    # ⛔ The RUNBOOK half of this claim is pinned in `tests/test_deploy_workflow.py::
    # test_the_runbook_field_row_documents_the_positive_pair`, deliberately and not
    # here: a runbook-only diff selects no surface and runs `tier1` only, and this
    # file is on `api`/`core` — so a row pin in THIS file is asleep on the one diff
    # that can re-break it (measured via `tools/ci_selection.py`).
    doc = " ".join((hosted_api._record_health.__doc__ or "").split())
    assert (
        "``record_writes_applied`` / ``record_last_write_at`` — the POSITIVE channel"
        in doc
    ), (
        "`_record_health`'s docstring no longer documents the positive pair as the "
        "pair the payload publishes — the field is published but undocumented, "
        "which is the paper-only claim inverted"
    )
    assert (
        "the record counters, under the SAME names ``metrics()`` publishes "
        "(#9188/#9231/#9307) — the hosted app has no ``/metrics`` route, so without "
        "them indicator (b) of #9548 (``record_write_failures`` = 0) has no reader at "
        "all on the deployment it is claimed about." in doc
    ), (
        "`_record_health`'s docstring no longer claims (INSIDE that sentence) that the "
        "record counters are published under `metrics()`'s own names, or has narrowed "
        "the claim there so it excludes the positive pair — that claim is what ties the "
        "/health keys to the ones the rest of the process reads, so losing it makes a "
        "rename silent. ⚠️ Scope: this is a substring pin on the sentence itself, so a "
        "narrowing added as a separate later sentence is outside it by construction."
    )


def test_an_unread_applied_counter_publishes_none_never_a_zero(monkeypatch):
    """The #9548 review P2 rule, one field over: an unread counter is not a zero.

    A `0` here is the WORST value to invent on this field: it is exactly what a
    never-writing deployment reports, so a broken counter read would look like
    the finding the field exists to make.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)

    def _explode():
        raise RuntimeError("applied counter unreadable")

    monkeypatch.setattr(monitoring, "record_write_applied_count", _explode)

    body = _health(monkeypatch)
    record = body["record"]

    assert record["ok"] is False, "a broken counter read reported healthy"
    assert record["record_writes_applied"] is None, (
        "an unread counter was published as 0 — indistinguishable from a "
        "deployment that has never written"
    )
    assert "applied counter unreadable" in record["counters_error"]
    # ALL-OR-NOTHING (#9548 review cycle 3): the three failure counters are read
    # BEFORE the pair, so a half-published block would hand a consumer a stale
    # `record_write_failures` beside `counters_error` and no way to tell it was
    # not a complete snapshot. Every counter key must be `None` together.
    for key in (
        "record_write_failures",
        "record_non_journal_skips",
        "record_position_unproven",
        "record_last_write_at",
    ):
        assert record[key] is None, (
            f"`{key}` published a value from a snapshot that FAILED half-way — "
            "a consumer reads a partial block as a complete one"
        )


def test_a_commit_that_RAISES_does_not_count_as_applied(monkeypatch):
    """The ordering claim, pinned: counted AFTER the commit, not at APPLIED.

    `RecordWriteOutcome.APPLIED` means "the plan materialized and every statement
    ran on the caller's connection" — the seam has NOT committed at that point.
    Counting there would report a write the rollback then discards, so a
    `commit()` that raises must leave the counter untouched. Without this test
    the placement is an unpinned claim: moving `_count_write_applied()` one line
    up leaves every other test in this file green.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)

    class _CommitFails(_CommittedConnection):
        def commit(self) -> None:
            raise RuntimeError("the commit was rejected by the server")

    conn = _CommitFails()
    monkeypatch.setattr(record_writer, "write_records",
                        lambda records, **kw: _applied(conn, first_seq=kw.get("first_seq", 1)))

    before = monitoring.record_write_applied_count()
    with record_writer.use_record_tenant("org-9548", "graph-9548"):
        result = record_writer.write_live_record({"type": "Point", "id": "p3"},
                                                 durable=True, seq=9)

    assert result is not None and result.applied is False, result
    assert result.refusal == "write_failed", (
        "a failed commit must be reported as a failure, not silently applied"
    )
    assert monitoring.record_write_applied_count() == before, (
        "a write whose COMMIT raised was counted as applied — the positive "
        "channel would report rows a rollback discarded"
    )


def test_the_unimportable_seam_shape_still_carries_the_positive_pair(monkeypatch):
    """The block's OTHER shape: the seam itself could not be imported.

    `_record_health` has two surfaces — the early return (nothing could be
    evaluated) and the normal block — and a key present in one but not the other
    is read as "no writes" by a `.get(...)`. The early return is only reachable
    when the import fails, which no other test exercises; a later edit could drop
    the pair (or seed `0`) and stay green.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, RECORD_DSN)
    # `from tortoise.monitoring import …` raises when the module is None in
    # sys.modules, which is the arm whose early return this pins.
    monkeypatch.setitem(sys.modules, "tortoise.monitoring", None)

    record = hosted_api._record_health()

    assert record["ok"] is False, "an unimportable seam reported healthy"
    for key in ("record_writes_applied", "record_last_write_at"):
        assert key in record, f"`{key}` is ABSENT — a .get() reads it as 'no writes'"
        assert record[key] is None, f"`{key}` published a value for an unread snapshot"
