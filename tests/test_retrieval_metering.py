"""#4489 — the RETRIEVAL dimension of the per-org metering ledger.

Reads are free by decision, so the engine cost of answering a query had no meter
at all. This file pins the measurement that closes it: the tally primitive, the
work-owning boundaries (the hosted ASGI middleware and the MCP dispatch bind),
the read funnel that notes the query, the leg helpers that note their OWN
executions off-thread, the durable ledger writer/reader, and the person-facing
``/v1/team`` render.

The registry (embedded) lane is used for the ledger tests — no docker, no
network. The funnel test stubs ``EmbeddingModel.get`` so no HF model load leaks
into the suite.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import tempfile
import threading
from pathlib import Path

import pytest

import tortoise.metering as metering
import tortoise.retrieval_metering as rm

ORG = "team-retrieval-1"

#: The leg names the ledger carries a COLUMN for. Anything else is folded into
#: the totals (see ``rm._KNOWN_LEGS``).
KNOWN = ("fts", "vector", "structural")


@pytest.fixture
def reg_org(monkeypatch, tmp_path):
    """A registry (embedded) SDK with one org — the lane the writer and reader
    both run on when Supabase mode is off."""
    from tortoise.sdk import TortoiseSDK

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    # Pin the PATH too: ``metering._reg_sdk()`` resolves the registry from
    # TORTOISE_DB_PATH (tempfile fallback), so without the pin the writer and
    # the fixture's sdk would address DIFFERENT databases.
    db = str(tmp_path / "retrieval.db")
    monkeypatch.setenv("TORTOISE_DB_PATH", db)
    sdk = TortoiseSDK(db, namespace="registry")
    org = sdk.org_create(name="retrieval-test")
    yield sdk, org["id"]
    sdk.close()


@pytest.fixture(autouse=True)
def _clean_context():
    """Every test starts and ends with NO active tally (no cross-test bleed)."""
    token = rm._ACTIVE.set(None)
    yield
    rm._ACTIVE.reset(token)


def _capture_writer(monkeypatch) -> list[dict]:
    """Replace the ledger writer with a recorder; return the calls list."""
    calls: list[dict] = []

    def _fake(org_id, **kw):
        calls.append({"org_id": org_id, **kw})
        return {"retrieval_queries": kw.get("queries", 0)}

    monkeypatch.setattr(metering, "record_retrieval_usage", _fake)
    return calls


def _capture_alert(monkeypatch) -> list[dict]:
    alerts: list[dict] = []
    monkeypatch.setattr(metering, "report_unmetered_increment",
                        lambda **kw: alerts.append(kw))
    return alerts


def _no_embedder(monkeypatch) -> None:
    """No HF load: the vector leg does not run, the others still do."""
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(
        EmbeddingModel, "get",
        classmethod(lambda cls, load_timeout=None: None))
    EmbeddingModel._reset()


# ════════════════════════════════════════════════════════════════════════════
# The tally primitive
# ════════════════════════════════════════════════════════════════════════════

class TestTallyPrimitive:
    def test_arm_is_idempotent_and_notes_accumulate(self):
        # Mutation: arm() returning a fresh tally every call → the identity
        # assert fails (the figure would split across two objects).
        t1 = rm.arm(ORG)
        t2 = rm.arm()
        assert t1 is t2
        rm.note_query(wall_ms=10.0)
        rm.note_query(wall_ms=2.5)
        rm.note_leg("fts", ran=True, degraded=False)
        assert (t1.queries, t1.wall_ms, t1.leg_runs) == (2, 12.5, 1)
        assert t1.org_id == ORG

    def test_unarmed_notes_are_noops(self):
        # Mutation: note_* dereferencing _ACTIVE.get() without a None check →
        # AttributeError here.
        rm.note_query(wall_ms=1.0)
        rm.note_leg("fts", ran=True, degraded=False)
        assert rm.current_tally() is None

    def test_take_and_reset_clears_and_is_single_shot(self):
        # Mutation: take_and_reset() not clearing _ACTIVE → the second take
        # returns a tally instead of None and a later request inherits it.
        rm.arm(ORG)
        rm.note_query(wall_ms=1.0)
        t = rm.take_and_reset()
        assert t is not None and t.queries == 1
        assert rm.take_and_reset() is None
        assert rm.current_tally() is None

    def test_unknown_leg_advances_the_totals_only(self):
        # Mutation: counting the per-leg column by name without the KNOWN set →
        # an unknown name would be recorded into a per-leg count and the row
        # shape would grow with the vocabulary.
        t = rm.arm(ORG)
        rm.note_leg("fallback", ran=True, degraded=False)
        rm.note_leg("structural", ran=True, degraded=False)
        assert t.leg_runs == 2
        assert t.legs == {"structural": 1}

    def test_a_leg_that_did_not_run_is_not_a_run(self):
        # The breaker-open branch records ran=False, degraded=True: no leg ran,
        # and the read IS degraded. Counting it as a run would overstate work.
        t = rm.arm(ORG)
        rm.note_leg("fts", ran=False, degraded=True)
        assert t.leg_runs == 0
        assert t.legs == {}
        assert t.leg_degraded == 1

    def test_negative_wall_ms_is_clamped(self):
        # Mutation: summing the raw value → a negative duration, i.e. a
        # monotonic figure that can go DOWN.
        t = rm.arm(ORG)
        rm.note_query(wall_ms=-5.0)
        rm.note_query(wall_ms=3.0)
        assert t.wall_ms == 3.0

    def test_note_query_derives_legs_from_a_trace(self):
        # The trace path is still supported (and is what a caller holding a
        # leg_trace gets): entries are read as recorded, unknown names folded.
        t = rm.arm(ORG)
        rm.note_query(wall_ms=1.0, legs=[
            {"leg": "fts", "ran": True, "degraded": False},
            {"leg": "vector", "ran": True, "degraded": True},
            {"leg": "fallback", "ran": False, "degraded": False},
            "not-a-dict",
        ])
        assert t.leg_runs == 2
        assert t.legs == {"fts": 1, "vector": 1}
        assert t.leg_degraded == 1

    def test_is_empty_ignores_a_tally_that_recorded_nothing(self):
        tally = rm.RetrievalTally()
        assert tally.is_empty()
        tally.note_leg("fts", ran=False, degraded=True)
        assert not tally.is_empty()


# ════════════════════════════════════════════════════════════════════════════
# flush — the one write, and the drops it can detect
# ════════════════════════════════════════════════════════════════════════════

class TestFlush:
    def test_flush_writes_the_legs_through_the_ledger_writer(self, monkeypatch):
        # Mutation: mapping a leg to the wrong column (or dropping the mapping
        # by passing only the totals) → the per-leg asserts go RED.
        calls = _capture_writer(monkeypatch)
        rm.arm(ORG)
        rm.note_query(wall_ms=7.5)
        rm.note_leg("fts", ran=True, degraded=False)
        rm.note_leg("vector", ran=True, degraded=True)
        rm.note_leg("vector", ran=True, degraded=False)
        out = rm.flush()
        assert out == {"retrieval_queries": 1}
        assert len(calls) == 1
        kw = calls[0]
        assert kw["org_id"] == ORG
        assert kw["queries"] == 1
        assert kw["wall_ms"] == pytest.approx(7.5)
        assert (kw["leg_runs"], kw["leg_degraded"]) == (3, 1)
        assert (kw["leg_fts"], kw["leg_vector"], kw["leg_structural"]) == (
            1, 2, 0)

    def test_empty_tally_writes_nothing(self, monkeypatch):
        # Mutation: writing an all-zero row for every armed request → the
        # ledger would hold a row per request that retrieved nothing.
        calls = _capture_writer(monkeypatch)
        rm.arm(ORG)
        assert rm.flush() is None
        assert calls == []

    def test_no_resolvable_org_alerts_and_writes_nothing(self, monkeypatch):
        # A non-empty tally with no org is a BOOKKEEPING fault of ours: the
        # reads happened and cannot be attributed. Silent is not an option.
        calls = _capture_writer(monkeypatch)
        alerts = _capture_alert(monkeypatch)
        rm.arm()
        rm.note_query(wall_ms=1.0)
        assert rm.flush() is None
        assert calls == []
        assert [a["lane"] for a in alerts] == ["retrieval"]

    def test_the_org_argument_overrides_the_bound_one(self, monkeypatch):
        calls = _capture_writer(monkeypatch)
        rm.arm("stale-org")
        rm.note_query(wall_ms=1.0)
        rm.flush("scope-org")
        assert calls[0]["org_id"] == "scope-org"

    def test_a_consumed_tally_is_never_written_twice(self, monkeypatch):
        # Two boundaries can reach one tally (the middleware and a caller):
        # without the consumed transition the same work is counted twice.
        calls = _capture_writer(monkeypatch)
        tally = rm.arm(ORG)
        rm.note_query(wall_ms=1.0)
        assert rm.flush_tally(tally, ORG) is not None
        assert rm.flush_tally(tally, ORG) is None
        assert len(calls) == 1

    def test_concurrent_flush_writes_exactly_once(self, monkeypatch):
        calls = _capture_writer(monkeypatch)
        tally = rm.arm(ORG)
        rm.note_query(wall_ms=1.0)
        barrier = threading.Barrier(4)
        results: list = []

        def _flush():
            barrier.wait()
            results.append(rm.flush_tally(tally, ORG))

        threads = [threading.Thread(target=_flush) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1
        assert sum(1 for r in results if r is not None) == 1

    def test_a_writer_failure_is_alerted_and_never_raised(self, monkeypatch):
        alerts = _capture_alert(monkeypatch)

        def _boom(org_id, **kw):
            raise RuntimeError("ledger down")

        monkeypatch.setattr(metering, "record_retrieval_usage", _boom)
        rm.arm(ORG)
        rm.note_query(wall_ms=1.0)
        assert rm.flush() is None          # total: the read is never failed
        assert [a["lane"] for a in alerts] == ["retrieval"]


# ════════════════════════════════════════════════════════════════════════════
# The worker-context binder — why the leg hooks can see the tally at all
# ════════════════════════════════════════════════════════════════════════════

class TestWorkerContextBinder:
    def test_unarmed_returns_the_function_unchanged(self):
        # The overwhelmingly common path (no product tally) must add ZERO work
        # — not a wrapper that is called and does nothing.
        def _fn():
            return 1

        assert rm.bind_worker_context(_fn) is _fn

    def test_a_new_thread_notes_into_the_callers_tally(self):
        # The pinned mechanism: ``degradation_chain`` runs the legs in a
        # ThreadPoolExecutor, and ContextVars do NOT propagate to a new thread
        # (CPython), so without this binder the leg half of the figure is
        # permanently, silently zero.
        tally = rm.arm(ORG)

        def _leg_work():
            rm.note_leg("fts", ran=True, degraded=False)

        bound = rm.bind_worker_context(_leg_work)
        thread = threading.Thread(target=bound)
        thread.start()
        thread.join()
        assert tally.leg_runs == 1
        assert tally.legs == {"fts": 1}


# ════════════════════════════════════════════════════════════════════════════
# The hosted boundary — the ASGI middleware
# ════════════════════════════════════════════════════════════════════════════

class TestMiddleware:
    @staticmethod
    def _run(scope, note=True, raise_exc=False):
        seen: list[bool] = []

        async def _app(scope, receive, send):
            if note:
                rm.note_query(wall_ms=2.0)
                rm.note_leg("vector", ran=True, degraded=False)
            seen.append(True)
            if raise_exc:
                raise RuntimeError("handler blew up")

        mw = rm.RetrievalMeteringMiddleware(_app)

        async def _receive():
            return {"type": "http.request"}

        async def _send(msg):
            return None

        with pytest.raises(RuntimeError) if raise_exc else _noop_ctx():
            asyncio.run(mw(scope, _receive, _send))
        return seen

    def test_the_middleware_is_actually_installed_on_the_app(self):
        """A guard that cannot fail is not a guard.

        ``app.add_middleware(_RetrievalMeteringMiddleware)`` is the ONLY thing
        that puts the hosted read lane on the ledger. Every other test in this
        class builds its OWN app, so deleting that one line would leave the
        whole suite green while the product recorded nothing. (The sibling embed
        boundary learned this the hard way — PR #5345.)
        """
        import tortoise.hosted_api as ha_mod
        import tortoise.retrieval_metering as rm_mod

        classes = [m.cls for m in ha_mod.app.user_middleware]
        assert rm_mod.RetrievalMeteringMiddleware in classes, (
            "the retrieval metering middleware is not installed — the hosted "
            f"read lane would record nothing. Installed: {classes}")
        # ...and a neighbour, so this cannot pass by the stack being replaced
        # wholesale with something that happens to contain the class.
        assert ha_mod.InFlightMiddleware in classes

    def test_get_arms_and_flushes_binding_the_scope_org(self, monkeypatch):
        # ⛔ The method gate is the OPPOSITE of the embed boundary's: a read is
        # a GET (``GET /v1/search``) as well as a POST, so method-gating here
        # would measure the wrong half of the surface. Mutation: copying the
        # embed middleware's GET skip → no writer call → RED.
        calls = _capture_writer(monkeypatch)
        self._run({"type": "http", "method": "GET",
                   "state": {"org_id": ORG}})
        assert len(calls) == 1
        assert calls[0]["org_id"] == ORG
        assert calls[0]["queries"] == 1

    def test_an_empty_tally_pays_no_thread_hop(self, monkeypatch):
        # Mutation: dropping the is_empty() guard → the to_thread hop runs (and
        # is spent) before flush_tally discovers there is nothing to write.
        calls = _capture_writer(monkeypatch)
        hops: list = []
        real = asyncio.to_thread

        async def _spy(fn, *a, **kw):
            hops.append(fn)
            return await real(fn, *a, **kw)

        monkeypatch.setattr(asyncio, "to_thread", _spy)
        self._run({"type": "http", "method": "POST",
                   "state": {"org_id": ORG}}, note=False)
        assert calls == []
        assert hops == []

    def test_an_app_exception_still_flushes_and_is_not_swallowed(self, monkeypatch):
        """The ``finally``-return trap, pinned.

        A ``return`` inside the middleware's ``finally`` would DISCARD the
        handler's exception — turning every failing request into a silent
        success — while still flushing. Both halves are asserted, because either
        alone passes for the wrong implementation.
        """
        calls = _capture_writer(monkeypatch)
        self._run({"type": "http", "method": "GET",
                   "state": {"org_id": ORG}}, raise_exc=True)
        assert len(calls) == 1             # the work is still recorded
        assert calls[0]["org_id"] == ORG

    def test_non_http_scope_passes_through(self, monkeypatch):
        # lifespan/websocket scopes are not requests; arming one would leave a
        # tally that no flush ever consumes.
        calls = _capture_writer(monkeypatch)
        seen = self._run({"type": "lifespan"})
        assert seen == [True]
        assert calls == []
        assert rm.current_tally() is None


class _noop_ctx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


# ════════════════════════════════════════════════════════════════════════════
# The leg hooks in search_engine — trace-INDEPENDENT by design
# ════════════════════════════════════════════════════════════════════════════

class _BoomGraph:
    """A graph whose every query fails — the leg's ``query_failed`` branch."""

    def query(self, *a, **kw):
        raise RuntimeError("graph down")


class TestLegHooks:
    def setup_method(self):
        import tortoise.search_engine as se
        se.reset_circuit_breakers()

    def teardown_method(self):
        import tortoise.search_engine as se
        se.reset_circuit_breakers()

    def test_the_fts_leg_notes_its_execution_with_no_trace_at_all(self):
        """The whole reason the hooks live in the leg helpers.

        A measurement that required the caller's opt-in ``leg_trace`` would
        record no legs on the default product path (``GET /v1/search`` passes
        none) — and minting a private trace instead would switch ON the
        #4199/#5287 scope probe inside the vector leg, making every read pay for
        a declaration it never asked for. Mutation: moving the note behind the
        ``leg_trace is not None`` gate → leg_runs == 0 → RED.
        """
        import tortoise.search_engine as se

        tally = rm.arm(ORG)
        se.run_fts_query(_BoomGraph(), "tortoise", entity_type="point",
                         leg_trace=None)
        assert tally.leg_runs == 1
        assert tally.legs == {"fts": 1}
        assert tally.leg_degraded == 1     # query_failed is a degraded run

    def test_the_breaker_open_branch_notes_a_degraded_non_run(self):
        # ran=False is a fact about the read (no work happened) and the degraded
        # flag is a fact about it too — both are recorded, neither is invented.
        import tortoise.search_engine as se

        tally = rm.arm(ORG)
        se._breaker("fts").record_failure()
        se._breaker("fts").record_failure()
        se._breaker("fts").record_failure()
        se.run_fts_query(_BoomGraph(), "tortoise", entity_type="point",
                         leg_trace=None)
        assert tally.leg_runs == 0
        assert tally.leg_degraded == 1


# ════════════════════════════════════════════════════════════════════════════
# The durable ledger — registry lane writer + reader
# ════════════════════════════════════════════════════════════════════════════

class TestLedger:
    def test_record_then_read_round_trips_and_accumulates(self, reg_org):
        _sdk, org = reg_org
        metering.record_retrieval_usage(
            org, queries=2, leg_runs=3, leg_degraded=1, wall_ms=12.5,
            leg_fts=1, leg_vector=1, leg_structural=1)
        got = metering.get_retrieval_usage(org)
        assert got["retrieval_queries"] == 2
        assert got["retrieval_leg_runs"] == 3
        assert got["retrieval_leg_degraded"] == 1
        assert got["retrieval_wall_ms"] == pytest.approx(12.5)
        assert (got["retrieval_leg_fts"], got["retrieval_leg_vector"],
                got["retrieval_leg_structural"]) == (1, 1, 1)

        # ADDITIVE on the same (org, period) row — a second batch must not
        # replace the first (the embed lane's own pinned property).
        metering.record_retrieval_usage(org, queries=1, leg_runs=1, wall_ms=0.5,
                                        leg_fts=1)
        got2 = metering.get_retrieval_usage(org)
        assert got2["retrieval_queries"] == 3
        assert got2["retrieval_leg_runs"] == 4
        assert got2["retrieval_wall_ms"] == pytest.approx(13.0)
        assert got2["retrieval_leg_fts"] == 2

    def test_a_fresh_org_reads_a_zero_view_not_an_error(self, reg_org):
        _sdk, org = reg_org
        got = metering.get_retrieval_usage(org)
        assert got["retrieval_queries"] == 0
        assert got["retrieval_wall_ms"] == 0.0
        # An org id that has no row at all is the same zero view.
        assert metering.get_retrieval_usage("no-such-org")["retrieval_queries"] == 0
        # ...and so is the empty id (stdio/None).
        assert metering.get_retrieval_usage("")["retrieval_queries"] == 0

    def test_no_org_and_selfhost_are_exempt(self, reg_org, monkeypatch):
        _sdk, org = reg_org
        assert metering.record_retrieval_usage(None, queries=1) is None
        assert metering.record_retrieval_usage("", queries=1) is None
        # BOTH selfhost forms: the explicit flag and the transport ContextVar
        # (the value "selfhost" is never the exemption key).
        assert metering.record_retrieval_usage(
            org, queries=1, _selfhost_transport=True) is None
        monkeypatch.setattr(metering, "_selfhost_transport_active",
                            lambda: True)
        assert metering.record_retrieval_usage(org, queries=1) is None
        assert metering.get_retrieval_usage(org)["retrieval_queries"] == 0

    def test_non_finite_wall_ms_is_dropped_to_zero(self, reg_org, caplog):
        _sdk, org = reg_org
        metering.record_retrieval_usage(org, queries=1, wall_ms=float("nan"))
        got = metering.get_retrieval_usage(org)
        # The COUNT survives; the poisoned duration does not.
        assert got["retrieval_queries"] == 1
        assert got["retrieval_wall_ms"] == 0.0
        assert math.isfinite(got["retrieval_wall_ms"])

    def test_the_read_write_split_is_readable_from_the_same_org(self, reg_org):
        """#4489 indicator 3: distinguish a read-heavy org from a write-heavy one.

        The retrieval figure and the write-ops figure must be readable for the
        SAME org and window — that is what makes the comparison possible without
        a second system or a dashboard.
        """
        _sdk, org = reg_org
        metering.record_write_ops(org, tier="free", n=4)
        metering.record_retrieval_usage(org, queries=9, leg_runs=11, wall_ms=90.0,
                                        leg_fts=9, leg_vector=1, leg_structural=1)
        writes = metering.get_current_usage(org)["write_ops_used"]
        reads = metering.get_retrieval_usage(org)
        assert writes == 4
        assert reads["retrieval_queries"] == 9
        assert reads["period"] == metering.get_current_usage(org)["period"]


# ════════════════════════════════════════════════════════════════════════════
# The read funnel — one note per served read, legs included
# ════════════════════════════════════════════════════════════════════════════

class TestReadFunnel:
    def test_the_funnel_notes_the_query_and_the_legs_that_ran(
            self, monkeypatch, tmp_path):
        """``tortoise_fts_query`` is the ONE funnel every product read surface
        goes through (``GET /v1/search``, the MCP recall tools, the ask lane).
        Its exit closure notes the query; the legs note themselves, off-thread.
        """
        from tortoise.sdk import TortoiseSDK

        _no_embedder(monkeypatch)
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        db = str(tmp_path / "funnel.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db)
        sdk = TortoiseSDK(db, namespace="registry")
        try:
            tally = rm.arm(ORG)
            sdk.tortoise_fts_query("what is a tortoise", limit=5)
            assert tally.queries == 1
            assert tally.wall_ms > 0.0
            # At least one leg ran: the FTS leg with no index in an embedded
            # store is a DEGRADED RUN — still a run, and still recorded, so a
            # silent keyword-only read is visible as such.
            assert tally.leg_runs >= 1
            assert set(tally.legs) <= set(KNOWN)
            assert tally.legs  # not empty → the legs really were noted
        finally:
            sdk.close()


# ════════════════════════════════════════════════════════════════════════════
# The person-facing read path — /v1/team renders the figure
# ════════════════════════════════════════════════════════════════════════════

TEAM_ROUTE = "team-retrieval-route"
TOKEN_ROUTE = "tt_retrieval_route_0001"


class TestTeamRoute:
    """#4489 indicator 2: a person reads the figure from an existing query.

    Drives the REAL route, the REAL reader and the REAL writer (via the fake
    control plane's RPC emulation) — not a monkeypatched get_retrieval_usage,
    which would only prove the field is spelled correctly.
    """

    @pytest.fixture
    def env(self, monkeypatch):
        os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
        import tortoise.supabase_control as sc
        from tests._http_fixtures import patched_tortoise_sdk
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane()
        fake.seed("organizations", [{
            "id": TEAM_ROUTE, "name": "retrieval-route", "tier": "free",
            "email": "owner@retrieval.test", "graph_name": f"org_{TEAM_ROUTE}",
            "max_users": 1, "max_graphs": 1, "ops_allowance": 10000,
            "graph_size_cap": 100000, "suspended_at": None, "flagged_at": None,
        }])
        from tortoise.auth import lookup_hash
        fake.seed("api_keys", [{
            "id": "key-retrieval", "org_id": TEAM_ROUTE,
            "lookup_hash": lookup_hash(TOKEN_ROUTE),
            "key_prefix": TOKEN_ROUTE[:10], "created_via": "provisioned",
            "created_by": "user-1",
            "created_at": "2026-08-01T00:00:00+00:00",
            "expires_at": None, "revoked_at": None,
        }])
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        import tortoise.hosted_api as ha
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patched_tortoise_sdk(os.path.join(tmpdir, "retrieval.db")),
        ):
            yield {"fake": fake, "app": ha.app}

    @staticmethod
    def _auth():
        return {"Authorization": f"Bearer {TOKEN_ROUTE}"}

    def test_renders_zeros_then_the_recorded_figure(self, env):
        # Mutation: dropping the additive fields from OrgInfoResponse / the
        # render → the keys are absent or stuck at the model defaults, i.e. the
        # "no verifiable deliverable" P1 the scope review caught on #4488.
        from fastapi.testclient import TestClient
        tc = TestClient(env["app"])
        fresh = tc.get("/v1/team", headers=self._auth())
        assert fresh.status_code == 200, fresh.text
        body = fresh.json()
        # A fresh org renders ZEROS, not an error and not a missing key (P2-14).
        assert body["retrieval_queries"] == 0
        assert body["retrieval_leg_runs"] == 0
        assert body["retrieval_wall_ms"] == 0.0
        assert body["retrieval_leg_structural"] == 0

        # The REAL writer, through the REAL seam, onto the fake ledger row.
        metering.record_retrieval_usage(
            TEAM_ROUTE, queries=4, leg_runs=6, leg_degraded=1, wall_ms=33.5,
            leg_fts=4, leg_vector=1, leg_structural=1)

        got = tc.get("/v1/team", headers=self._auth())
        assert got.status_code == 200, got.text
        b = got.json()
        assert b["retrieval_queries"] == 4
        assert b["retrieval_leg_runs"] == 6
        assert b["retrieval_leg_degraded"] == 1
        assert b["retrieval_wall_ms"] == pytest.approx(33.5)
        assert b["retrieval_leg_fts"] == 4
        assert b["retrieval_leg_vector"] == 1
        assert b["retrieval_leg_structural"] == 1
        # The figure rides ALONGSIDE the existing fields — no pre-existing key
        # was displaced, and the write-ops comparison is one response away.
        assert "write_ops_used" in b and "ask_calls" in b and "embed_calls" in b

    def test_the_read_figure_does_not_move_the_spend_ceiling(self, env):
        # #4489 is explicitly NOT a billing change: the cohort spend reads only
        # ask_cost_usd + capture_cost_usd. A retrieval figure must not appear in
        # it — the ceiling is untouched by CONSTRUCTION, and this pins it.
        from fastapi.testclient import TestClient
        tc = TestClient(env["app"])
        before = tc.get("/v1/team", headers=self._auth()).json()
        metering.record_retrieval_usage(
            TEAM_ROUTE, queries=99, leg_runs=99, wall_ms=999.0,
            leg_fts=99)
        after = tc.get("/v1/team", headers=self._auth()).json()
        assert after.get("ask_cost_usd") == before.get("ask_cost_usd")
        assert after.get("overage_cost_usd") == before.get("overage_cost_usd")


# ════════════════════════════════════════════════════════════════════════════
# Migration ↔ Python contract (the SQL never runs in CI)
# ════════════════════════════════════════════════════════════════════════════

MIGRATION = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
             / "20261013000001_metering_retrieval_columns.sql")


class TestMigrationContract:
    def test_every_counter_is_not_null_default_zero(self):
        """Load-bearing, not tidiness.

        A metering row is created by whichever lane writes FIRST, and the other
        lanes' RPCs do not mention these columns. ``NULL + n`` is NULL, so
        without the default the figures would be permanently NULL and the reader
        would render 0 with no error anywhere — a dead figure that reads as
        "this org never read".
        """
        sql = MIGRATION.read_text()
        for column in ("retrieval_queries", "retrieval_leg_runs",
                       "retrieval_leg_degraded", "retrieval_wall_ms",
                       "retrieval_leg_fts", "retrieval_leg_vector",
                       "retrieval_leg_structural"):
            match = re.search(
                rf"ADD COLUMN IF NOT EXISTS {column}\s+([^,\n]+)[,;]", sql)
            assert match, f"{column} is not declared by the migration"
            spec = match.group(1)
            assert "NOT NULL" in spec, f"{column} is nullable: {spec!r}"
            assert "DEFAULT 0" in spec, f"{column} has no default: {spec!r}"

    def test_the_rpc_takes_exactly_the_parameters_python_sends(self):
        """A dropped/recreated RPC is the one place a rename goes silent.

        ``metering_increment_retrieval`` is DROPPED and re-CREATEd (not replaced
        in place, #3825), so a parameter renamed on one side only would leave the
        call failing at runtime with nothing in the repo to catch it. The SQL is
        parsed here because CI never executes it.
        """
        sql = MIGRATION.read_text()
        declared = set(re.findall(r"^\s+(p_[a-z_]+)\s+(?:text|timestamptz|integer"
                                  r"|double precision)", sql, re.M))
        assert declared, "no CREATE FUNCTION parameters found in the migration"

        sent: list[dict] = []

        class _Cp:
            def rpc(self, fn, params):
                assert fn == "metering_increment_retrieval"
                sent.append(params)

        from tortoise.supabase_control import metering_increment_retrieval
        metering_increment_retrieval(_Cp(), "org-1", "2026-10-01T00:00:00+00:00",
                                     "2026-11-01T00:00:00+00:00",
                                     queries=1, leg_runs=2, leg_degraded=0,
                                     wall_ms=3.0, leg_fts=1, leg_vector=1,
                                     leg_structural=0)
        assert len(sent) == 1
        # ``metering_increment_retrieval`` sends p_-prefixed keys (PostgREST RPC
        # argument names), so the comparison is against the raw key set.
        assert set(sent[0]) == declared
