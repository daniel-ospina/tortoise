"""A write the RECORD accepted must reach the CACHE — and when it cannot, say so (#12943).

THE MEASURED DEFECT. #12943 reported 8 objects / 39 events / 5 operators in
Postgres and a cache holding one object — the single one written through REST —
and read it as a missing cache leg on the SDK path.
Measuring the write path says otherwise: ``create_object`` → ``create_entity``
writes the GRAPH first (the apply-first declared lane, §17.1) and then appends
the record, so the cache leg is present and precedes the record. The
asymmetry is real, and its cause is one level up from the code:

    the two stores are selected by two INDEPENDENT variables —
    ``TORTOISE_DB_URI`` (cache) and ``TORTOISE_RECORD_DSN`` (record) —
    and nothing compares them.

With the record armed and no URI, ``hosted_api._make_sdk`` takes its embedded
fallback (#7722) and the cache becomes a process-local redislite file while the
truth goes to a shared Postgres. Reproduced, one throwaway tenant, both stores
counted (``docs/evidence/12943-cache-leg/measure.py``):

    TORTOISE_DB_URI set    record 2 objects / 3 points / 6 journal events
                           cache  2 objects / 3 points   ← agrees
    TORTOISE_DB_URI empty  record 2 objects / 3 points / 6 journal events
                           cache  0 objects / 0 points   ← #12943's shape

WHAT THIS FILE PINS, and in which order:

1. **The counter exists and fires** — the observable the issue asks for
   ("make the failure observable before making it rare"). Every one of these
   is RED on the tree before this change: there was no cache-leg counter at
   all, so ``metrics()`` read perfectly healthy on a deployment whose derived
   store held none of its writes (#4034's class).
2. **All THREE doors**, because the fix lives in the ONE constructor they all
   pass (``TortoiseSDK.__init__``): SDK direct, the hosted REST lane
   (``hosted_api._make_sdk``), the MCP lane (``mcp_auth``).
3. **The FOUR coherent shapes stay silent** — shared cache, unarmed record,
   declared single tenant, test-lane redirect — because two of them are recorded
   decisions this must not reverse: an unarmed record (#7722's embedded
   fallback is deliberate) and a declared single-tenant deployment (both stores
   local to one box — ``record_writer.single_tenant_declared``, #9192). The
   other two are the ordinary shared-cache deployment and the test lane's own
   redirect onto an ambient server. ⚠️ There is deliberately NO "graph unbound"
   member: the predicate has no graph-binding condition (a construction that
   binds no graph is counted like any other), so naming one would send a reader
   looking for a test that does not exist.
4. **The convergence itself**, per door, against a REAL record and a REAL
   shared cache — the acceptance sentence ("after a write through each door,
   the cache's counts match the record's for that scope"). Those need both
   stores, so they SKIP rather than pass when either is absent: a convergence
   test that cannot reach one side must never report agreement (§17.2's own
   rule for the reconciler, applied to its test).

⛔ What is NOT here, named so the absence is visible: the read-side fallback is
#12941's, and ``record-reconcile`` diverging on an operator Point's
``operator``/``pointKind`` is a DIFFERENT root (projection fidelity, measured
in ``docs/evidence/12943-cache-leg/``) — pinned there, not folded in here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tortoise import cache_leg, monitoring  # noqa: E402
from tortoise.record_writer import (  # noqa: E402
    RECORD_DSN_ENV,
    RECORD_GRAPH_ENV,
    RECORD_ORG_ENV,
    RECORD_SINGLE_TENANT_ENV,
)

#: The record's throwaway Postgres (`TEST_RECORD_DB_URI`, the repo convention —
#: `tests/test_9078_record_written_by_production_path.py` is the sibling user).
RECORD_DB_ENV = "TEST_RECORD_DB_URI"


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    """No ambient record arming, and no latch carried between tests.

    Both halves matter. The arming is process-global, so a test that forgot to
    clear it would decide its neighbour's verdict; and ``cache_leg._WARNED`` is
    the once-per-process log latch, which would make the *warning* assertion
    order-dependent (it would pass alone and fail in a full run).
    """
    for name in (RECORD_DSN_ENV, RECORD_ORG_ENV, RECORD_GRAPH_ENV,
                 RECORD_SINGLE_TENANT_ENV, "TORTOISE_DB_PATH",
                 "TORTOISE_TEST_NO_REDIRECT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cache_leg, "_WARNED", set())
    yield


def _counts() -> dict[str, int]:
    return monitoring.cache_leg_failure_counts()


def _delta(before: dict[str, int]) -> dict[str, int]:
    after = _counts()
    return {k: after.get(k, 0) - before.get(k, 0)
            for k in after if after.get(k, 0) != before.get(k, 0)}


def _delta_from(before: dict[str, int],
                after: dict[str, int]) -> dict[str, int]:
    """The same delta between two already-taken snapshots."""
    return {k: after.get(k, 0) - before.get(k, 0)
            for k in after if after.get(k, 0) != before.get(k, 0)}


def _arm_record(monkeypatch, dsn: str = "postgresql://u:p@127.0.0.1:1/db"):
    """Arm the record WITHOUT connecting — arming is presence-based.

    ``record_writer``'s decision 2: "Unset or blank ⇒ NOT_ARMED", and nothing
    about arming opens a socket (the seam connects per write, and this file
    never writes through an armed record except in the gated DB legs). So a
    non-routable DSN is the honest fixture here: it proves the check is
    presence-based and cannot accidentally be an availability test.
    """
    monkeypatch.setenv(RECORD_DSN_ENV, dsn)


def _arm_for_real(monkeypatch) -> None:
    """Arm the record against the REAL throwaway Postgres.

    The autouse ``hermetic`` fixture clears the ambient DSN so no test decides
    its neighbour's verdict; the convergence legs therefore re-arm from
    ``TEST_RECORD_DB_URI`` rather than depending on an ambient value.
    """
    monkeypatch.setenv(RECORD_DSN_ENV, os.environ[RECORD_DB_ENV])


# ── 1. the observable ────────────────────────────────────────────────────────


def test_a_record_armed_process_with_a_local_cache_is_counted(
        monkeypatch, tmp_path):
    """THE FIX'S CORE ASSERTION — RED before this change.

    Armed record + ``_db_uri is None`` + multi-tenant ⇒ the cache is a local
    store and the record is not. Measured on the real hosted seam, that is
    2 objects / 3 points in Postgres and 0 / 0 in FalkorDB, with every call
    returning success.
    """
    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    before = _counts()
    sdk = _embedded_sdk(tmp_path)
    try:
        assert _delta(before) == {cache_leg.CACHE_NOT_SHARED: 1}
    finally:
        sdk.close()


def test_the_counter_is_on_the_metrics_surface(monkeypatch, tmp_path):
    """A counter nobody can read is not observability.

    ``monitoring.metrics()`` is what the MCP ``tortoise_health`` tool and a
    Prometheus scrape (``serve_health``) read. The HOSTED app publishes its own
    curated block and has no ``/metrics`` route, so the cache leg is carried
    onto BOTH: this test pins ``metrics()``, the sibling below pins
    ``hosted_api._record_health``.
    """
    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    sdk = _embedded_sdk(tmp_path)
    try:
        payload = monitoring.metrics()
    finally:
        sdk.close()
    assert "cache_leg_failures" in payload
    assert payload["cache_leg_failures"][cache_leg.CACHE_NOT_SHARED] >= 1
    # The record's own counters stay CLEAN — that is the whole reason this is a
    # sibling key and not a new `record_write_failures` reason: on this
    # deployment the record lost nothing, and reporting it as a record failure
    # would send an operator to the wrong store.
    assert payload["record_write_failures"] == {}


def test_the_health_block_carries_it_too(monkeypatch, tmp_path):
    """The surface an operator actually reads, not only the scrape (#12943).

    The hosted app answers ``/health`` from ``hosted_api._record_health`` and has
    no ``/metrics`` route, so a counter that only reached ``metrics()`` would
    make the split visible to Prometheus and invisible to the operator
    dashboard — the dichotomy this fix exists to close.
    """
    from tortoise import hosted_api

    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "health.db"))
    # A DELTA, not an absolute. The counter is process-cumulative, so `>= 1`
    # would pass off an earlier test's increment and stay green with this test's
    # construction unhooked (review cycle 2).
    before = _counts()
    _embedded_sdk(tmp_path).close()
    assert _delta(before) == {cache_leg.CACHE_NOT_SHARED: 1}
    block = hosted_api._record_health()
    assert "cache_leg_failures" in block, sorted(block)
    assert block["cache_leg_failures"][cache_leg.CACHE_NOT_SHARED] == \
        before.get(cache_leg.CACHE_NOT_SHARED, 0) + 1


def test_the_health_block_carries_the_key_when_the_SNAPSHOT_fails(monkeypatch):
    """The arm where the key used to vanish (#12943 review cycle 2).

    ``_record_health`` seeds a block and publishes it on the ``counters_error``
    arm. A key assigned only in the ``else`` arm is ABSENT precisely when the
    snapshot raised — and an absent key reads as "the cache is fine", which is
    the illusion this whole change exists to end. Every published counter key
    must survive the failure arm.
    """
    from tortoise import hosted_api

    def _boom(*a, **k):
        raise RuntimeError("counter snapshot failed")

    # Patched on `tortoise.monitoring`, not on `hosted_api`: `_record_health`
    # imports its counters INSIDE the function body, so a local import re-reads
    # this attribute at call time while `hosted_api` has no such attribute to set.
    monkeypatch.setattr("tortoise.monitoring.cache_leg_failure_counts", _boom)
    block = hosted_api._record_health()
    # The degraded shape, not a healthy one.
    assert block["ok"] is False
    assert "counter snapshot failed" in block["counters_error"], block
    # …and the key is still THERE, null, alongside every sibling.
    for key in ("record_write_failures", "record_non_journal_skips",
                "record_position_unproven", "record_writes_applied",
                "record_last_write_at", "cache_leg_failures",
                "cache_write_targets"):
        assert key in block, f"{key} vanished on the failure arm: {sorted(block)}"
        assert block[key] is None, f"{key} = {block[key]!r} on the failure arm"


def test_the_warning_names_both_stores_and_the_remedy(monkeypatch, tmp_path,
                                                      caplog):
    """A bare increment is a number; the operator needs the SHAPE.

    The measured failure is silent *by construction* — both stores accept the
    write — so the message has to carry what an operator cannot see: which
    variable chose each store, and the two legal fixes (one per deployment
    shape).
    """
    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    before = _counts()
    with caplog.at_level("WARNING", logger=cache_leg.__name__):
        sdk = _embedded_sdk(tmp_path)
        sdk.close()
    assert _delta(before) == {cache_leg.CACHE_NOT_SHARED: 1}
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "TORTOISE_RECORD_DSN" in text
    assert "TORTOISE_DB_URI" in text
    assert RECORD_SINGLE_TENANT_ENV in text, (
        "the single-tenant remedy must be named: without it the only advice is "
        "wrong for a self-host where both stores are meant to be local")
    assert "cache_leg_failures" in text


def test_the_warning_is_emitted_once_per_process(monkeypatch, tmp_path, caplog):
    """The hosted lane builds one SDK PER REQUEST.

    An unlatched warning would write a log line per request into the very
    deployment an operator is trying to read, which is how a signal becomes
    noise. The COUNTER still increments every time — the two have different
    jobs, and only the log is latched.
    """
    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    before = _counts()
    with caplog.at_level("WARNING", logger=cache_leg.__name__):
        for _ in range(3):
            _embedded_sdk(tmp_path).close()
    assert _delta(before) == {cache_leg.CACHE_NOT_SHARED: 3}
    assert len([r for r in caplog.records if "CACHE leg" in r.getMessage()]) == 1


# ── 2. the four coherent shapes stay silent (recorded decisions) ────────────


def test_a_shared_cache_is_not_counted(monkeypatch, tmp_path):
    """The coherent deployment: one shared cache, one shared record.

    ⚠️ PREDICATE-level, and deliberately so: a CONSTRUCTOR-level assertion for this
    shape needs a REACHABLE shared cache, which is
    `test_no_cache_leg_failure_is_counted_when_the_cache_is_shared` below — it SKIPS
    when `TORTOISE_DB_URI` names nothing. This arm is the half that runs everywhere,
    so it proves the predicate only; `_delta(before) == {}` is sound but weak on its
    own (no construction happens between the two reads), which is why the skip-gated
    test below is the one that pins the CONSTRUCTION.
    """
    _arm_record(monkeypatch)
    before = _counts()
    assert cache_leg.cache_leg_reason(cache_uri="docker://:pw@h:6379/g") is None
    assert _delta(before) == {}


def test_an_unarmed_record_with_a_local_cache_is_not_counted(monkeypatch,
                                                             tmp_path):
    """⛔ #7722's embedded fallback is a DECISION, not the defect.

    A process with no record and no URI is the local/dev/onboarding lane, where
    the embedded graph is the only store and therefore is not a cache of
    anything. Reversing that would break onboarding to no purpose.

    ⛔ Asserted THROUGH THE CONSTRUCTOR, not through the predicate alone:
    `cache_leg_reason` is pure and returns None here, so a `== {}` delta beside
    it is a constant — it would stay green if the hook were duplicated or the
    condition widened.
    """
    _drop_uri(monkeypatch)
    before = _counts()
    _embedded_sdk(tmp_path).close()
    assert _delta(before) == {}


def test_a_declared_single_tenant_deployment_may_run_a_local_cache(
        monkeypatch, tmp_path):
    """⛔ The other recorded decision: a self-host where BOTH stores are local.

    ``record_writer.single_tenant_declared`` (#9192) is the operator's
    statement that this process serves exactly one tenant — the shape where an
    embedded graph and a same-box Postgres are a coherent pair, so the cache
    really is the record's cache. Asserted through the constructor for the same
    reason as the test above.
    """
    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    monkeypatch.setenv(RECORD_SINGLE_TENANT_ENV, "1")
    monkeypatch.setenv(RECORD_ORG_ENV, "org1")
    monkeypatch.setenv(RECORD_GRAPH_ENV, "g1")
    before = _counts()
    _embedded_sdk(tmp_path).close()
    assert _delta(before) == {}


def test_the_test_lane_redirect_is_not_a_split(monkeypatch, tmp_path):
    """Epic #1647's docker-lane redirect rewrites an embedded construction onto
    the ambient server, so the effective store IS shared and counting it would
    report a split that does not exist.

    ⚠️ WHAT THIS PINS, precisely: `cache_leg._redirected_to_a_server` — the
    MIRROR of the real gate, not the gate. Nothing here opens the projection
    (`__init__` leaves `self._proj` None, so the rewrite runs lazily), so if
    `projection.FalkorProjection`'s condition changed — redirect dropped, or
    narrowed — this test stays green while the mirror silently exempts a
    genuinely embedded construction (the under-count residual the mirror's own
    docstring names). Asserting the real redirect means opening the projection
    and reading where it resolved, which needs a live ambient server; that is
    `test_each_door_reaches_the_same_shared_stores` below, which skips without
    one. Said plainly here because the previous docstring claimed "through the
    REAL redirect", which this assertion does not do (review cycle 2)."""
    _arm_record(monkeypatch)
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:6379/g")
    before = _counts()
    _embedded_sdk(tmp_path).close()
    assert _delta(before) == {}


# ── 3. per door: ONE constructor, three writers ──────────────────────────────


def test_the_sdk_door_counts_it(monkeypatch, tmp_path):
    """Door 1 — a direct SDK caller (the self-host / script lane)."""
    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    before = _counts()
    _embedded_sdk(tmp_path).close()
    assert _delta(before) == {cache_leg.CACHE_NOT_SHARED: 1}


def test_the_rest_door_counts_it(monkeypatch, tmp_path):
    """Door 2 — the hosted REST lane's SDK opener (``_data_sdk`` → ``_make_sdk``).

    This is the door the issue's own writer used, and the one whose objects
    reached Postgres and not the cache.
    """
    from tortoise import hosted_api

    _arm_record(monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "rest.db"))
    _drop_uri(monkeypatch)
    before = _counts()
    sdk = hosted_api._make_sdk(graph_name="org_org1_g1")
    try:
        # TWO constructions, not one: the embedded fallback path builds the
        # keepalive ANCHOR and then the per-request SDK on top of it
        # (`hosted_api._make_sdk`). The counter is per CONSTRUCTION — it is
        # cumulative, so the exact multiple is a property of the door, not of
        # the defect. What the assertion is about is that THIS door does not
        # escape the check.
        assert _delta(before) == {cache_leg.CACHE_NOT_SHARED: 2}
    finally:
        sdk.close()


def test_the_mcp_door_counts_it(monkeypatch, tmp_path):
    """Door 3 — the MCP lane's SDK opener, which agents actually write through.

    ⛔ ``mcp_server._get_sdk`` CACHES into a module global and returns it
    unconditionally afterwards, with no staleness check — unlike
    ``hosted_api._make_sdk``'s anchor, which grew ``_anchor_usable`` for exactly
    the #1502 class. Without pinning it to ``None`` here, this test (a) leaves
    every later test in the worker holding a CLOSED SDK bound to a tmp dir this
    fixture then deletes, and (b) makes its own assertion a property of process
    HISTORY: anything that had cached ``_sdk`` earlier would satisfy it without
    the door being built at all. Both halves are why it is set on BOTH sides of
    the call, through ``monkeypatch`` so the module is restored after.
    """
    from tortoise import mcp_auth, mcp_server

    _arm_record(monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "mcp.db"))
    _drop_uri(monkeypatch)
    monkeypatch.setattr(mcp_server, "_sdk", None, raising=False)
    monkeypatch.setattr(mcp_server, "sdk", None, raising=False)
    before = _counts()
    sdk = mcp_auth._get_base_sdk()
    try:
        assert _delta(before) == {cache_leg.CACHE_NOT_SHARED: 1}
    finally:
        sdk.close()
        monkeypatch.setattr(mcp_server, "_sdk", None, raising=False)
        monkeypatch.setattr(mcp_server, "sdk", None, raising=False)


# ── 4. convergence: both stores, per door ────────────────────────────────────


def _both_stores():
    """Skip unless a REAL record AND a REAL shared cache are reachable."""
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the cache leg")
    pytest.importorskip("psycopg2", reason="psycopg2-binary is the record's driver")
    if not (os.environ.get("TORTOISE_DB_URI") or "").strip():
        pytest.skip("TORTOISE_DB_URI not set — there is no shared cache to compare")
    return dsn


def _tenant(suffix: str) -> tuple[str, str, str]:
    import uuid

    run = uuid.uuid4().hex[:10]
    org = f"org12943{suffix}{run}"
    graph_id = f"g_12943{suffix}{run}"
    return org, graph_id, f"org_{org}_{graph_id}"


def _seed(owner_dsn: str, org: str, graph_id: str) -> None:
    import psycopg2

    conn = psycopg2.connect(owner_dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING", (org, org))
        cur.execute(
            "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING", (graph_id, org, graph_id))
    conn.close()


def _count_record(owner_dsn: str, org: str, graph_id: str) -> dict[str, int]:
    import psycopg2

    conn = psycopg2.connect(owner_dsn)
    conn.autocommit = True
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in ("object", "point"):
            cur.execute(
                f"SELECT count(*) FROM public.{table} "
                "WHERE org_id = %s AND graph_id = %s", (org, graph_id))
            out[table] = cur.fetchone()[0]
    conn.close()
    return out


def _cache_client():
    """A client for the lane's shared cache, or SKIP on a shape we cannot parse.

    ⛔ ``str.rsplit`` with ``maxsplit=1`` returns ONE element when the URI has no
    userinfo, so a credential-less ``redis://host:6379/0`` (or ``rediss://``,
    or a bare path) raised ``ValueError: not enough values to unpack`` — turning
    an intended skip into a collection ERROR. Parsed with ``urlparse`` so the
    scheme, host, port and password come from the URI's own grammar rather than
    from a split.
    """
    from urllib.parse import unquote, urlparse

    from falkordb import FalkorDB

    uri = (os.environ.get("TORTOISE_DB_URI") or "").strip()
    if not uri:
        pytest.skip("TORTOISE_DB_URI not set — there is no shared cache to compare")
    parsed = urlparse(uri)
    if not parsed.hostname:
        pytest.skip(f"cannot parse a FalkorDB host out of {uri.split('://')[0]}://…")
    return FalkorDB(host=parsed.hostname, port=parsed.port or 6379,
                    password=unquote(parsed.password) if parsed.password else None)


def _cache_counts(cache_graph: str) -> dict[str, int]:
    """Count the cache graph's node labels without the SDK's own seams."""
    db = _cache_client()
    out: dict[str, int] = {}
    graph = db.select_graph(cache_graph)
    for label in ("Object", "Point"):
        res = graph.query(f"MATCH (n:{label}) RETURN count(n) AS c")
        out[label] = int(res.result_set[0][0]) if res.result_set else 0
    return out


def _owner_dsn() -> str:
    """The BYPASSRLS role, for seeding + counting only.

    The write path itself must use the record's APPLICATION role — the records
    it files are the thing under test — but seeding ``public.organizations`` and
    reading back across tenants is an operator act.
    """
    dsn = os.environ.get("OWNER_RECORD_DSN") or os.environ.get(RECORD_DB_ENV)
    return dsn

@pytest.mark.parametrize("door", ["sdk"])
def test_the_sdk_door_writes_both_stores(door, monkeypatch, tmp_path):
    """The acceptance sentence, door 1: write → the cache has it too."""
    _both_stores()
    _arm_for_real(monkeypatch)
    # The record's only durable source is the JSONL journal — `write_live_record`
    # refuses `no_journal` without one, and production arms it
    # (`TORTOISE_EVENT_LOG_BASE_DIR=/data/events`). Without this the leg would
    # measure the refusal, not the cache.
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path / "events"))
    from tortoise import hosted_api

    owner = _owner_dsn()
    org, graph_id, cache_graph = _tenant("sdk")
    _seed(owner, org, graph_id)
    sdk = hosted_api._make_sdk(graph_name=cache_graph)
    try:
        sdk._bind_record_tenant(org, graph_id)
        sdk.create_object("alpha", objectKind="goal")
        sdk.create_object("beta", objectKind="goal")
    finally:
        sdk.close()
    record = _count_record(owner, org, graph_id)
    cache = _cache_counts(cache_graph)
    assert record == {"object": 2, "point": 0}, record
    assert cache == {"Object": 2, "Point": 0}, cache


def test_the_mcp_door_writes_both_stores(monkeypatch, tmp_path):
    """The acceptance sentence, door 3: an MCP tool call reaches both stores.

    Driven through the tool body an agent actually calls
    (``mcp_server.tortoise_create_object``), with the three ContextVars its
    middleware would set — so the SDK under test is the one ``mcp_auth``
    builds, not a hand-made substitute. The RECORD TENANT is installed the way
    that lane installs it (``record_writer.use_record_tenant``, which is what
    ``mcp_auth._record_tenant_scope`` wraps around ``call_next``).
    """
    _both_stores()
    _arm_for_real(monkeypatch)
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path / "events"))
    from tortoise import mcp_auth, mcp_server
    from tortoise.record_writer import use_record_tenant

    owner = _owner_dsn()
    org, graph_id, cache_graph = _tenant("mcp")
    _seed(owner, org, graph_id)
    # The MCP lane resolves its quota from the REGISTRY graph, so an org that
    # exists only in Postgres is refused `Team '<id>' not found in registry`
    # before any SDK is built — the tool would never reach the store this leg
    # measures. Provision the same `:Team` node the production path creates
    # (`hosted_api._make_sdk(namespace="registry")._get_registry()`), as the
    # onboarding-parity test does for its own lanes.
    from tortoise import hosted_api
    registry = hosted_api._make_sdk(namespace="registry")
    try:
        registry._get_registry().query(
            "CREATE (t:Team {id:$id, onboarding_state:$st})",
            params={"id": org, "st": "{}"})
    finally:
        registry.close()

    # `_transport_mode` is the fail-closed auth gate the tool bodies read: it is
    # None until a transport initialises, and `None` refuses EVERY operation
    # with "MCP transport mode not initialized". Stdio is what an in-process
    # caller is (the other tests that drive these bodies set the same value).
    tokens = [
        mcp_auth._transport_mode.set("stdio"),
        mcp_auth._current_org_id.set(org),
        mcp_auth._current_graph_id.set(graph_id),
        mcp_auth._current_graph_namespace.set(cache_graph),
    ]
    try:
        with use_record_tenant(org, graph_id):
            mcp_server.tortoise_create_object("alpha", "goal")
            mcp_server.tortoise_create_object("beta", "goal")
    finally:
        for var, token in reversed(list(zip(
                (mcp_auth._transport_mode, mcp_auth._current_org_id,
                 mcp_auth._current_graph_id,
                 mcp_auth._current_graph_namespace),
                tokens, strict=True))):
            var.reset(token)

    record = _count_record(owner, org, graph_id)
    cache = _cache_counts(cache_graph)
    assert record == {"object": 2, "point": 0}, record
    assert cache == {"Object": 2, "Point": 0}, cache


def test_the_rest_door_shares_both_seams_with_the_measured_door(monkeypatch):
    """Door 2's convergence is STRUCTURAL, and the residual is stated.

    ``POST /v1/objects`` does not write anything itself: its handler is
    ``sdk.create_object`` on the SDK ``_data_sdk_offloaded`` opened, and
    `_data_sdk` opens it through ``_make_sdk(graph_name=<the resolved
    namespace>)`` and binds the record with ``_bind_record_tenant`` — the exact
    pair the SDK leg above measures end to end (and the pair the evidence
    harness drives through the graph-bound branch). So this pin asserts the
    SHARING rather than re-proving it: a REST write cannot converge differently
    from an SDK write while both seams are the same objects, and if that ever
    stops being true this reds instead of the divergence hiding behind a door.

    ⛔ Residual, named not implied: an END-TO-END REST convergence leg needs a
    control plane (``_bind_record_tenant`` resolves the tenant through
    ``record_writer.record_tenant_for``, which refuses without one), and this
    repo's test tree has none. The counter legs above prove the REST door
    PASSES the cache-leg guard; the graph branch is measured in
    ``docs/evidence/12943-cache-leg/``.
    """
    # The handler's own text, not a re-statement: the write must be an SDK call.
    import inspect

    from tortoise import hosted_api

    handler = inspect.getsource(hosted_api.create_object)
    assert "sdk.create_object" in handler
    assert "_data_sdk_offloaded" in handler
    # `_data_sdk` is the one opener both REST branches use, and it is the object
    # the SDK leg measures.
    data_sdk = inspect.getsource(hosted_api._data_sdk)
    assert "_make_sdk(" in data_sdk
    assert "_bind_record_tenant(sdk, org)" in data_sdk


def test_no_cache_leg_failure_is_counted_when_the_cache_is_shared(monkeypatch):
    """The control the whole counter rests on: a shared cache never trips it.

    ⛔ It must NOT require the record. This test never touches Postgres — it
    asserts only that a NON-EMBEDDED construction is not counted — so gating it
    on `TEST_RECORD_DB_URI` slept the one assertion that keeps the counter
    trustworthy on every lane without a Postgres. Arming is presence-based, so
    a fake DSN is the honest fixture; what is genuinely required is a shared
    cache URI, and that is the one skip.
    """
    if not (os.environ.get("TORTOISE_DB_URI") or "").strip():
        pytest.skip("TORTOISE_DB_URI not set — there is no shared cache to build")
    _arm_record(monkeypatch)
    before = _counts()
    from tortoise import hosted_api
    from tortoise import sdk as sdk_module

    # ⛔ "THE HOOK RAN AND DECLINED", not just "nothing was counted" (review
    # cycle 2; review pass 6 found the first cut did NOT establish it). A
    # delta-only assertion cannot tell the predicate's `if cache_uri: return
    # None` arm from a hook that was never reached, so it stays green if the
    # call is deleted or pushed back inside the constructor's `_db_uri is None`
    # branch — the exact drift `tortoise/sdk.py`'s unconditional call exists to
    # prevent (and that `sdk.py` names THIS test as the pin for). The SPY is
    # what observes the call; it delegates to the real hook, so the delta below
    # is still the real counter's.
    calls: list[dict[str, object]] = []
    real_note = sdk_module._note_local_cache
    assert real_note is not None, "the hook must be importable, or this control is vacuous"

    def _spy(**kwargs):
        calls.append(kwargs)
        return real_note(**kwargs)

    monkeypatch.setattr(sdk_module, "_note_local_cache", _spy)
    sdk = hosted_api._make_sdk(graph_name="org_org1_g1")
    assert sdk._db_uri, "the shared-cache construction must carry a URI"
    assert len(calls) == 1, f"the constructor must call the hook exactly once, got {calls!r}"
    assert calls[0]["cache_uri"] == sdk._db_uri, "the hook must be handed the SHARED uri"
    assert cache_leg.cache_leg_reason(cache_uri=sdk._db_uri) is None
    sdk.close()
    assert _delta(before) == {}


# ── helpers ─────────────────────────────────────────────────────────────────


def _drop_uri(monkeypatch) -> None:
    """Force the EMBEDDED branch — the whole point of the door tests."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)


def _embedded_sdk(tmp_path):
    """An SDK whose cache is a local file.

    An explicit ``db_path`` wins over ``TORTOISE_DB_URI`` in the constructor's
    own branch order (``if db_uri and db_path is None``), so the CONSTRUCTOR
    already treats this as embedded. The ambient URI is dropped as well, and
    that half is not cosmetic: under ``TORTOISE_TEST_MODE=1`` the docker-lane
    redirect rewrites an explicit-path construction onto the ambient server, and
    ``cache_leg`` deliberately exempts that shape (see
    ``test_the_test_lane_redirect_is_not_a_split``). So the split shape under
    test is "no URI anywhere", which is exactly the deployment shape #12943
    measured. Declared in ``tests/test_uri_env_mutations_declared.py``.
    """
    from tortoise.sdk import TortoiseSDK

    return TortoiseSDK(db_path=str(tmp_path / "cache.db"))


# ── 5. the CLASS: the tenant and the cache graph can be two answers (#12943) ─
#
# The reopening's evidence was that #12950 covered a DOOR, not the class: the
# rest of this file grades ONE configuration split (record armed + embedded
# cache, decided at construction). Three other shapes put a write in the record
# and not in the cache, and NONE of them is a construction property:
#
#   * a cache URI naming a DIFFERENT SERVER (a stale/other FalkorDB);
#   * a credential class that opens a different GRAPH — MEASURED on a coherent
#     deployment: an org-wide credential writes ``org_<org>`` (the default
#     graph's namespace) while the record files the same write under
#     ``org_<org>_<gid>`` (`docs/evidence/12943-cache-leg/door_matrix.py`, the
#     ``data_sdk_orgwide`` row: record 1 object / cache 1 object in the OTHER
#     graph / the tenant's own graph never created);
#   * a lane that binds a tenant it did not resolve, onto an SDK opened for
#     another graph.
#
# Two instruments close them, and both are graded here rather than narrated:
# the TENANT↔GRAPH check (decidable, below) and the per-process live-write
# TARGET registry (cause-agnostic, below it).


def test_a_tenant_bound_to_another_graphs_cache_is_COUNTED(monkeypatch,
                                                          tmp_path):
    """THE FIX'S CORE ASSERTION for the reopening — RED before this change.

    An SDK opened for ``org_<org>_g_A`` whose writer binds the tenant
    ``(org, g_B)`` files the record row under ``g_B`` and the cache node under
    ``g_A``. Both calls return success; `record-reconcile --cache-graph
    org_<org>_g_B` then reports the whole tenant ``missing_in_cache`` while
    every counter on ``metrics()`` reads healthy.

    Constructed THROUGH ``_make_sdk`` (the hosted opener) so the assertion is
    about the shipped door, not about the pure predicate.
    """
    from tortoise import hosted_api

    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "cache.db"))
    sdk = hosted_api._make_sdk(graph_name="org_orgA_g_A")
    try:
        # Baseline AFTER the construction: an embedded construction is its own
        # counted event (#12950's `cache_not_shared`), and folding that into
        # this assertion would let the binding check be dead and still pass.
        before = _counts()
        sdk._bind_record_tenant("orgA", "g_A")   # coherent: NOT counted
        mid = _counts()
        sdk._bind_record_tenant("orgA", "g_B")   # misrouted: COUNTED
        after = _counts()
    finally:
        sdk.close()
    assert _delta_from(before, mid) == {}, (
        "the coherent binding must stay silent — a counter that fires on the "
        "ordinary case is noise, not observability")
    assert _delta_from(mid, after) == {cache_leg.TENANT_GRAPH_MISMATCH: 1}


def test_the_tenant_graph_counter_is_on_the_metrics_surface(monkeypatch,
                                                           tmp_path):
    """``monitoring.metrics()`` is the MCP health tool's and the scrape's read."""
    from tortoise import hosted_api

    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "cache.db"))
    sdk = hosted_api._make_sdk(graph_name="org_orgA_g_A")
    try:
        sdk._bind_record_tenant("orgA", "g_B")
    finally:
        sdk.close()
    payload = monitoring.metrics()
    assert payload["cache_leg_failures"][cache_leg.TENANT_GRAPH_MISMATCH] >= 1
    # …and the RECORD's own counters stay clean: nothing was lost, the cache
    # copy is in a graph the reconciler will not read. Reporting it as a record
    # failure would send an operator to the wrong store (#4034's misattribution).
    assert payload["record_write_failures"] == {}


def test_the_warning_names_both_graphs_and_the_remedy(monkeypatch, tmp_path,
                                                     caplog):
    """Silence is the defect; a number alone does not say WHICH graph."""
    from tortoise import hosted_api

    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "cache.db"))
    with caplog.at_level("WARNING", logger=cache_leg.__name__):
        sdk = hosted_api._make_sdk(graph_name="org_orgA_g_A")
        try:
            sdk._bind_record_tenant("orgA", "g_B")
        finally:
            sdk.close()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "org_orgA_g_A" in text and "g_B" in text
    assert "record-reconcile" in text
    assert "cache_leg_failures" in text


def test_an_org_level_graph_name_is_SILENT_and_that_is_DELIBERATE(monkeypatch,
                                                                 tmp_path):
    """The residual, pinned so a widening has to change a test.

    ``org_<org>`` is the DEFAULT graph's namespace, and on a coherent hosted
    deployment an org-wide credential binds exactly this pair (the default
    graph's id) to exactly this graph. A constructor cannot tell it from a lane
    that bound a CUSTOM graph's id onto an org-level SDK — that needs the
    control plane — so grading it would count every ordinary org-wide request
    as a fault. The shape is left ungraded ON PURPOSE, and the target registry
    below is what makes it visible instead of silently trusted.
    """
    from tortoise import hosted_api

    _arm_record(monkeypatch)
    _drop_uri(monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "cache.db"))
    sdk = hosted_api._make_sdk(namespace="orgA")
    try:
        before = _counts()   # after construction — see the sibling test
        assert sdk._cache_graph_name() == "org_orgA"
        sdk._bind_record_tenant("orgA", "g_custom")
    finally:
        sdk.close()
    assert _delta(before) == {}, "the org-level shape must not be counted here"


def test_a_live_fold_records_the_target_it_landed_in():
    """THE CAUSE-AGNOSTIC INSTRUMENT — `{store|graph: writes}` per process.

    Needs a real shared cache (``TORTOISE_DB_URI``): the target is read off the
    REAL projection's own endpoint, which is the point — a fingerprint derived
    from the environment would agree with itself while the write went elsewhere.
    """
    if not (os.environ.get("TORTOISE_DB_URI") or "").strip():
        pytest.skip("TORTOISE_DB_URI not set — no shared cache to fold into")
    from tortoise import hosted_api
    from tortoise.cache_leg import cache_write_targets, reset_cache_write_targets

    reset_cache_write_targets()
    import uuid

    cache_graph = f"org_orgA_g_{uuid.uuid4().hex[:8]}"
    sdk = hosted_api._make_sdk(graph_name=cache_graph)
    try:
        sdk.create_object("target-alpha", objectKind="goal")
    finally:
        sdk.close()
    targets = cache_write_targets()
    matching = [k for k in targets if k.endswith(f"|{cache_graph}")]
    assert len(matching) == 1, targets
    assert matching[0].startswith("server://"), (
        f"the target must name the STORE, not only the graph: {matching[0]!r}")
    assert targets[matching[0]] == 1, targets
    # ⛔ THE PUBLICATION IS ASSERTED TOO (review cycle 1, finding 1). A registry
    # nobody can read is not observability, and both surfaces are the ones an
    # operator uses: `metrics()` for a scrape, `_record_health()` for the hosted
    # app (which has no `/metrics` route). Without these two lines either
    # publication can be deleted with the suite fully green — the same gap the
    # sibling `cache_leg_failures` key closes at `test_the_counter_is_on_the_
    # metrics_surface` / `test_the_health_block_carries_it_too`.
    from tortoise import hosted_api

    published = monitoring.metrics()["cache_write_targets"]
    assert published.get(matching[0]) == 1, published
    block = hosted_api._record_health()
    assert block["cache_write_targets"] == published


def test_a_replay_is_NOT_recorded_as_a_live_write(monkeypatch, tmp_path):
    """A rebuild writes the same target on purpose.

    Counting it would let a repair make the live picture look healthier than the
    traffic it repaired — the inverse of what the instrument is for. Driven
    through the SHIPPED replay engine (`rebuild_all`) over a real journal, not a
    hand-opened scope.
    """
    _drop_uri(monkeypatch)
    from tortoise.cache_leg import cache_write_targets, reset_cache_write_targets
    from tortoise.log import EventLog
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events.jsonl"
    log = EventLog(str(events))
    log.append({"event_id": "ev-replay-1", "type": "ObjectRegistered",
                "ts": "2026-01-01T00:00:00+00:00",
                "payload": {"id": "obj-replay-1", "name": "replayed",
                            "objectKind": "goal"}})
    reset_cache_write_targets()
    sdk = TortoiseSDK(db_path=str(tmp_path / "cache.db"))
    try:
        proj = sdk._get_proj()
        proj.rebuild(log, confirm_destructive=True)
    finally:
        sdk.close()
    assert cache_write_targets() == {}, (
        "a replay must not be counted as a live write — got "
        f"{cache_write_targets()!r}")


def test_an_UNREADABLE_target_registry_is_NULL_and_never_an_empty_map(monkeypatch):
    """The failure arm must not impersonate the success arm (#12943 review P1).

    ``metrics()['cache_write_targets']`` is ``{target: writes}``, so ``{}`` is
    the SUCCESS reading — "this process has taken no live cache write".
    Publishing an empty map from the ``except`` arm makes an agent that cannot
    READ the registry report byte-identically to the deployment shape this key
    was added to expose (measured: ``{}`` while its in-process writes went
    elsewhere), which is the illusion the whole change exists to end.
    ``/health`` already seeds ``None`` on that arm; both surfaces read the same
    registry, so both must spell "unreadable" the same way.
    """
    def _boom(*a, **k):
        raise RuntimeError("target registry unreadable")

    # Patched on `tortoise.cache_leg`, not through `monitoring`:
    # `_cache_write_targets` imports the accessor INSIDE the function body, so
    # the local import re-reads this attribute at call time.
    monkeypatch.setattr("tortoise.cache_leg.cache_write_targets", _boom)
    payload = monitoring.metrics()
    assert "cache_write_targets" in payload, sorted(payload)
    assert payload["cache_write_targets"] is None, (
        "an unreadable registry must be null, never {} — {} means 'this "
        f"process took no live write' — got {payload['cache_write_targets']!r}")


def test_a_SKIPPED_fold_is_not_recorded_as_a_landed_write(monkeypatch, tmp_path):
    """The registry counts folds that RETURNED, not folds that were attempted.

    A malformed event is logged and skipped (`FalkorProjection.apply`'s own type
    guard) — nothing is written — so counting it would tell an operator the cache
    holds a write it never made, which is the wrong direction for a number read
    beside `record_writes_applied` (#12943 review cycle 2, finding 1: the hook
    used to sit ABOVE that guard).
    """
    _drop_uri(monkeypatch)
    from tortoise.cache_leg import cache_write_targets, reset_cache_write_targets

    reset_cache_write_targets()
    sdk = _embedded_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        proj.apply({"event_id": "ev-skip", "type": 123})     # skipped, no write
        assert cache_write_targets() == {}, (
            "a skipped fold must not be counted as a landed write — got "
            f"{cache_write_targets()!r}")
        sdk.create_object("landed-after-skip", objectKind="goal")
        assert sum(cache_write_targets().values()) == 1, cache_write_targets()
    finally:
        sdk.close()


def test_the_structural_edge_lane_records_its_target_too(monkeypatch, tmp_path):
    """`create_edge` raw-MERGEs without passing `apply` — so it needs the hook.

    Without it a process whose writes are edges registers NO target, and
    `metrics()['cache_write_targets']` reads `{}` — byte-identical to "this
    process took no live cache write", the illusion the key exists to end
    (#12943 review cycle 2, finding 2).
    """
    _drop_uri(monkeypatch)
    from tortoise.cache_leg import cache_write_targets, reset_cache_write_targets

    reset_cache_write_targets()
    sdk = _embedded_sdk(tmp_path)
    try:
        a = sdk.create_object("edge-source", objectKind="goal")
        b = sdk.create_object("edge-target", objectKind="goal")
        reset_cache_write_targets()      # only the EDGE is under test
        created = sdk._get_proj().create_edge(a["id"], b["id"], "related")
        assert created is True
        assert sum(cache_write_targets().values()) == 1, cache_write_targets()
    finally:
        sdk.close()


def test_a_malformed_uri_does_not_turn_binding_a_tenant_into_a_failure(
        monkeypatch, tmp_path):
    """#5148 protects the CALL, not its argument (#12943 review cycle 2, finding 4).

    `_cache_graph_name` parses the URI, and `urlparse('docker://[::1/db')` raises
    `ValueError: Invalid IPv6 URL`. Evaluated outside the guard, that turned a
    tenant bind on the hosted per-request path into a 500 for a write that could
    have been served — the exact class `cache_leg`'s "observability must never
    deny a caller a graph it can use" rule forbids.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://[::1/db")
    _arm_record(monkeypatch)
    from tortoise.sdk import TortoiseSDK

    before = dict(monitoring.cache_leg_failure_counts())
    sdk = TortoiseSDK()
    try:
        sdk._bind_record_tenant("orgA", "g_B")     # must not raise
        # The derivation itself is still as strict as it always was — the point
        # is only that BINDING never depends on it succeeding.
        with pytest.raises(ValueError):
            sdk._cache_graph_name()
        assert dict(monitoring.cache_leg_failure_counts()) == before, (
            "binding a tenant must not add a cache-leg failure of any kind")
    finally:
        sdk.close()


def test_apply_still_RETURNS_the_fold_result(monkeypatch, tmp_path):
    """`apply`'s return value is consumed — a wrapper that drops it loses records.

    `create_source`'s `_return_apply_result` path feeds
    `apply_result.result_set[0][0]` into the #5024 (T6) `SourceVersioned`
    decision. The registry wrapper's first draft returned `None` instead, and the
    record stopped carrying source version transitions **permanently** — the
    journal is append-only. Measured then: `test_source_rematerialisation_5024`
    12 failed, `test_source_version_window_3644` 5 failed. This is the cheap pin
    that makes the same silent loss impossible to re-introduce.
    """
    _drop_uri(monkeypatch)
    sdk = _embedded_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        out = proj.apply({"event_id": "ev-src", "type": "SourceCreated",
                          "url": "https://example.test/a", "sourceId": "src-1"})
        assert out is not None, (
            "apply() must return the fold's own result — `create_source` reads "
            "it to decide whether a SourceVersioned record is owed")
    finally:
        sdk.close()


def test_apply_keeps_the_signature_the_replay_engines_introspect(
        monkeypatch, tmp_path):
    """The journal-context keywords are a CONTRACT, not cosmetics.

    `consistency.recover_from_log` and `backup.restore` read
    `inspect.signature(proj.apply).parameters` to decide whether this engine can
    be handed the journal's own context. A `**kwargs` wrapper erases all four
    names, so both engines silently stop threading it — measured on that draft:
    `test_rebuild_parity_fail_loud_3585` 8 failed (the whole journal-context
    class: forward references, absent-target supersedes, restore refusals).
    """
    import inspect

    _drop_uri(monkeypatch)
    sdk = _embedded_sdk(tmp_path)
    try:
        params = inspect.signature(sdk._get_proj().apply).parameters
        for name in ("journal_object_surviving", "journal_object_deleted",
                     "journal_first_materialized", "journal_seq",
                     "journal_existence", "journal_object_established",
                     "about_links_deferred"):
            assert name in params, (
                f"apply() lost the {name!r} parameter the replay engines "
                "introspect — they will stop threading the journal context")
        assert not any(p.kind is inspect.Parameter.VAR_KEYWORD
                       for p in params.values()), (
            "apply() must name its keywords explicitly; **kwargs erases them")
    finally:
        sdk.close()


@pytest.mark.parametrize("event", [
    {"type": "PointsMerged"},                            # no merge_ids
    {"type": "ObjectSuperseded", "id": "no-such-object"},  # matched nothing
    {"type": "PointPromoted", "point": {"id": "\x00bad"}},  # unwritable id
    # No `point` payload AT ALL: no statement is issued, so nothing is written.
    # `_skip()`'s own list already claimed "an unwritable or ABSENT id on the
    # point/operator/promote arms" was marked; this is the `apply()` arm that
    # makes that claim true (#12943 review cycle 4 follow-up).
    {"type": "PointPromoted"},
    # ── review cycle 4, P2: the DELEGATED folds that report their own
    # 0-row count straight through `_apply_event`, none of which called
    # `_skip()`. Measured on the head before this cycle: every one of these
    # left `{'embedded:…|tortoise': 1}` in the registry while its fold logged
    # (or silently counted) zero rows.
    {"type": "OperatorAnnotated", "id": "no-such-operator", "bias": 0.4},
    {"type": "PointRetracted", "id": "no-such-retract"},
    {"type": "ConfidenceChanged", "id": "no-such-cc", "confidence": 0.5},
    {"type": "EdgeMessageChanged", "op": "set", "src": "a", "tgt": "b",
     "rel": "IMPL", "msg_alpha": 0.5},
    {"type": "EdgeMessageChanged", "op": "clear", "scope": "all"},
    {"type": "EntityLinked", "source_id": "s-missing",
     "source_label": "Session", "target_id": "o-missing",
     "target_label": "Object", "edge_type": "aboutObject"},
    {"type": "SessionRecorded"},                        # malformed: no id
])
def test_the_OTHER_writing_nothing_exits_are_not_counted(
        monkeypatch, tmp_path, event):
    """`_skip()` must cover every exit that issues no write — measured, not assumed.

    Review cycle 3 measured the draft counting all three of these (registry
    `{'embedded:…|tortoise': 1}`) while the fold logged that the record was
    dropped, and counted `ObjectSuperseded` on the LIVE path because its `_skip()`
    was nested inside the journal-context gate — the one path this instrument
    exists to serve.

    Review cycle 4 found the same defect one layer down: the folds that RETURN
    their matched-row count (annotator, retract, belief, edge-message) and the
    two delegated arms (`EntityLinked`, `SessionRecorded`) hand `_apply_event` a
    0 that nothing translated into `_skip()`. Six more rows came from that.
    """
    _drop_uri(monkeypatch)
    from tortoise.cache_leg import cache_write_targets, reset_cache_write_targets

    sdk = _embedded_sdk(tmp_path)
    try:
        reset_cache_write_targets()
        sdk._get_proj().apply(dict(event, event_id=f"ev-{event['type']}"))
        assert cache_write_targets() == {}, (
            f"{event['type']} wrote nothing but was counted as a landed cache "
            f"write — got {cache_write_targets()!r}")
    finally:
        sdk.close()


def test_the_0_row_capable_arms_STILL_count_a_fold_that_LANDED(
        monkeypatch, tmp_path):
    """The `_skip()` on those arms must not swallow a fold that DID write.

    A blanket "never count these event types" mutant would pass the sibling
    test above while inverting the instrument's polarity (under-reporting
    instead of over-reporting), so each arm is driven twice: once against a
    target that does not exist, once against one that does.
    """
    _drop_uri(monkeypatch)
    from tortoise.cache_leg import cache_write_targets, reset_cache_write_targets

    sdk = _embedded_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        point = sdk.create_point("fact", "a landed fold")
        # Every arm below MATCHes an existing target, so each must count 1.
        for event in (
            {"type": "OperatorAnnotated", "id": point["id"], "bias": 0.4},
            {"type": "ConfidenceChanged", "id": point["id"],
             "confidence": 0.6},
            {"type": "PointRetracted", "id": point["id"]},
            {"type": "SessionRecorded", "id": "sess-landed-1"},
        ):
            reset_cache_write_targets()
            proj.apply(dict(event, event_id=f"ev-landed-{event['type']}"))
            assert sum(cache_write_targets().values()) == 1, (
                f"{event['type']} folded a real target and must count exactly "
                f"one landed write — got {cache_write_targets()!r}")
    finally:
        sdk.close()


def test_a_declared_gateway_lane_records_its_target(monkeypatch, tmp_path):
    """`why_snapshot` / `webhook_delivery` / onboarding write WITHOUT folding.

    They issue their own statements through the projection's handle under a
    declared lane, so a process whose live traffic is those lanes registered no
    target at all — the same "{} means healthy" illusion, one lane over
    (#12943 review cycle 3, finding 4).
    """
    _drop_uri(monkeypatch)
    from tortoise import why_snapshot
    from tortoise.cache_leg import cache_write_targets, reset_cache_write_targets

    sdk = _embedded_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        reset_cache_write_targets()
        why_snapshot._run(proj, "CREATE (:WhyProbe {id: $id})", {"id": "w-1"})
        assert sum(cache_write_targets().values()) == 1, cache_write_targets()
        # A READ through the same gateway is not a cache write.
        why_snapshot._run(proj, "MATCH (n:WhyProbe) RETURN count(n)")
        assert sum(cache_write_targets().values()) == 1, cache_write_targets()
    finally:
        sdk.close()
