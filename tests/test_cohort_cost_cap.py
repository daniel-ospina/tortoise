"""#3665 (lane B7) — the COHORT COST CAP, executed against the real admission handler.

THE FLEET BAR FOR THIS FILE
---------------------------
Every test here drives the **real** hosted admission path
(``hosted_api._capture_session_impl`` — via FastAPI ``POST /v1/sessions`` and
via the MCP ``tortoise_session_capture`` tool) over stubbed transports, against
a **real over-cap cohort fixture**: real registry org rows carrying a real
``created_at``, and real ``:MeteringRecord`` ledger rows written through the
production writer (``metering.record_capture_usage``). Nothing here reads a
config value and calls it enforcement — a cap that is *configured* but not
*enforced* is the false PASS this lane exists to prevent.

The refusal is asserted three ways, so a config-only regression cannot pass:
the resolved 402 body, **zero extraction calls**, and **zero Session writes**.
"""
from __future__ import annotations

import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tortoise import cohort_cost as _cc
from tortoise import hosted_api as _ha
from tortoise.alert_store import AlertStore
from tortoise.hosted_api import app, get_current_org
from tortoise.hosted_backup import MemoryStorage

CAP_USD = 5.0
# A ONE-SECOND cohort window. The suite may share a FalkorDB server across
# runs, so the cohort must be resolvable to exactly the orgs this test
# provisions: a wide window would pull in every other org that carries a
# `created_at`, and their ledger rows would leak into the aggregate. Window
# + per-run unique ids together make the fixture deterministic.
COHORT_SINCE = "2026-08-01T00:00:00+00:00"
IN_COHORT_CREATED_AT = "2026-08-01T00:00:01+00:00"
PRE_COHORT_CREATED_AT = "2019-01-01T00:00:00+00:00"

_RUN = uuid.uuid4().hex[:8]
COHORT_ORG = f"org-3665-cohort-{_RUN}"
PRE_COHORT_ORG = f"org-3665-precohort-{_RUN}"

_PROVIDER = "openrouter"
_MODEL = "deepseek/deepseek-v4-flash"

_CONV = [{"role": "user",
          "content": "we decided to ship the cohort cost cap first"},
         {"role": "assistant",
          "content": "agreed — enforce it at admission"},
         {"role": "user", "content": "ok"}]

_S2 = ('{"entities": [], "events": [], "operators": [], '
       '"points": [{"content": "capped capture point", '
       '"pointKind": "statement"}]}')


def _org(org_id: str) -> dict:
    return {"org_id": org_id, "tier": "free", "key_id": "k-3665",
            "legacy_full_access": True, "max_points": 100000}


def _provision(org_id: str, created_at: str) -> None:
    """Create (idempotently — the registry may be a shared server) the org
    row with a REAL ``created_at``: the column the cohort is derived from
    (``organizations.created_at``; ``:Team`` is the embedded lane's label)."""
    _ha._make_sdk(namespace="registry")._get_registry().query(
        "MERGE (t:Team {id:$id}) "
        "SET t.onboarding_state=$st, t.created_at=$ca",
        params={"id": org_id, "st": "{}", "ca": created_at},
    )


def _spend(org_id: str, usd: float) -> None:
    """Write a REAL ledger row through the production writer — the fixture the
    cap reads. Not a monkeypatched reader: the real reader queries this row."""
    from tortoise.metering import record_capture_usage
    record_capture_usage(org_id, cost_usd=usd)


def _session_count(org_id: str) -> int:
    rows = _ha._make_sdk(namespace=org_id)._get_proj().g.query(
        "MATCH (s:Session) RETURN count(s)").result_set
    return int(rows[0][0])


class _CostModel:
    """Cost-reporting extractor stub (the #3359 ``_V2SessionMock`` seam does
    not report a charge, so a capture through it would prove the ledger write
    only at $0.00). Every call reports a real ``last_cost_usd``."""

    provider = _PROVIDER
    id = _MODEL
    last_finish_reason = "stop"

    def complete(self, *, system: str, user: str, max_tokens=None) -> str:
        if "STORY SUMMARIZER" in system:
            self.last_prompt_tokens, self.last_completion_tokens = 100, 10
            self.last_cost_usd = 0.001
            return "A narrative."
        self.last_prompt_tokens, self.last_completion_tokens = 200, 20
        self.last_cost_usd = 0.002
        return _S2


class _Channels:
    """The AlertStore transport double — a REAL ``AlertStore`` over this, so
    the dedup state machine under test is the production one."""

    def __init__(self) -> None:
        self.issues: dict[int, str] = {}
        self.bodies: dict[int, str] = {}
        self.telegram: list[str] = []
        self.closed: list[int] = []
        self._next = 1

    def file_issue(self, title, body):
        n = self._next
        self._next += 1
        self.issues[n] = title
        self.bodies[n] = body
        return n

    def close_issue(self, number, comment=None):
        self.closed.append(number)

    def search_open(self, kind, org_id=""):
        return [n for n, t in self.issues.items()
                if f"[DR] {kind}" in t
                and (org_id == "" or t.endswith(f" — {org_id}"))
                and n not in self.closed]

    def push_telegram(self, text):
        self.telegram.append(text)


@pytest.fixture()
def incidents(monkeypatch):
    """Point the cap's alert sink at a REAL AlertStore over fake transport."""
    channels = _Channels()
    store = AlertStore(
        MemoryStorage(),
        file_issue=channels.file_issue,
        close_issue=channels.close_issue,
        search_open=channels.search_open,
        push_telegram=channels.push_telegram,
        repo="daniel-ospina/tortoise",
        assignee="daniel-ospina",
    )
    monkeypatch.setattr(_cc, "_alert_store", lambda: store)
    return channels


@pytest.fixture()
def capture_env(tmp_path, monkeypatch):
    """The REAL hosted capture app over an embedded DB, with the cap ARMED and
    an extraction spy. The org the request resolves as is swappable
    (``env.org["org_id"]``) so the cohort boundary is testable."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv(_cc.CAP_ENV, str(CAP_USD))
    monkeypatch.setenv(_cc.SINCE_ENV, COHORT_SINCE)

    calls: list[str] = []
    orig_run = _ha._run_capture_bounded

    async def _spy(slot, fn, /, *args, **kwargs):
        calls.append(getattr(fn, "__name__", repr(fn)))
        return await orig_run(slot, fn, *args, **kwargs)

    monkeypatch.setattr(_ha, "_run_capture_bounded", _spy)
    org = {"org_id": COHORT_ORG}
    with patched_tortoise_sdk(str(tmp_path / "cap.db")):
        app.dependency_overrides[get_current_org] = lambda: dict(_org(org["org_id"]))
        _provision(COHORT_ORG, IN_COHORT_CREATED_AT)
        _provision(PRE_COHORT_ORG, PRE_COHORT_CREATED_AT)
        with TestClient(app) as tc:
            yield SimpleNamespace(client=tc, extraction_calls=calls, org=org)


# ── 1. the refusal, behaviourally ───────────────────────────────────────────


def test_over_cap_cohort_capture_refused_402_no_extraction_no_write(
        capture_env, incidents):
    """A capture from an org in a cohort whose MEASURED ledger spend is over
    the cap is refused at admission — 402, no extraction call, no Session.

    REDs on: deleting/neutralising the ``enforce_cohort_cost_cap(org)`` call in
    the admission block (the request would 200 and
    ``extraction_calls == ["_extract_session_v2"]``);
    inverting the comparison at the gate; and reading the cap without reading
    the ledger (spend=0 → below cap → 200).

    GREEN legitimate form: cap armed, real ledger row at ``CAP_USD + 1``."""
    _spend(COHORT_ORG, CAP_USD + 1.0)

    r = capture_env.client.post(
        "/v1/sessions", json={"conversation": _CONV, "harness": "claude"})

    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    assert "Cohort LLM spend cap reached" in detail, detail
    assert f"{CAP_USD + 1.0:.4f}" in detail, detail  # the measured figure
    assert capture_env.extraction_calls == [], (
        "the cap must refuse BEFORE any extraction is dispatched")
    assert _session_count(COHORT_ORG) == 0, "nothing may be written"


def test_over_cap_refusal_is_observable_as_an_alert_incident(
        capture_env, incidents):
    """The firing raises an AlertStore incident (deduped, GitHub + Telegram) —
    the acceptance evidence is the incident, not a log line.

    REDs on: dropping the ``file_cohort_cost_incident`` call (no issue is
    filed); dropping the ``asyncio.to_thread`` dispatch alone REDs too, because
    a blocking call inside ``to_thread`` is never reached... (it is reached —
    the mutation that REDs is removing the call, or passing no detail).

    GREEN legitimate form: incident filed once, titled for the cohort kind and
    the tripping org, carrying the measured spend."""
    _spend(COHORT_ORG, CAP_USD + 2.0)

    r = capture_env.client.post(
        "/v1/sessions", json={"conversation": _CONV, "harness": "claude"})
    assert r.status_code == 402, r.text

    assert list(incidents.issues.values()) == [
        f"[DR] {_cc.INCIDENT_KIND} — {COHORT_ORG}"], incidents.issues
    body = next(iter(incidents.bodies.values()))
    assert str(CAP_USD + 2.0) in body, body
    assert incidents.telegram, "the incident must also push, not only file"


# ── 2. the paired negative control ──────────────────────────────────────────


def test_below_cap_cohort_capture_proceeds_and_extracts(capture_env, incidents):
    """The ceiling is a ceiling, not a throttle: the SAME cohort, under the
    cap, captures normally and the extraction really runs.

    REDs on: making the gate fire unconditionally (a bad comparison, a missing
    ``spent >= cap`` guard) — the request would 402; and on a gate that never
    reads the ledger *and* never lets work through.

    GREEN legitimate form: real ledger row at ``CAP_USD - 1``."""
    _spend(COHORT_ORG, CAP_USD - 1.0)

    r = capture_env.client.post(
        "/v1/sessions", json={"conversation": _CONV, "harness": "claude"})

    assert r.status_code == 200, r.text
    assert capture_env.extraction_calls == ["_extract_session_v2"], (
        capture_env.extraction_calls)
    assert _session_count(COHORT_ORG) == 1
    assert not incidents.issues, "a below-cap capture must not raise an alert"


def test_org_outside_the_cohort_is_not_capped(capture_env, incidents):
    """The cap is COHORT-scoped: an org created before the cohort start is not
    caught by it even while the cohort it does not belong to is over the cap.

    REDs on: dropping the ``str(org_id) not in ids`` membership test — a
    cohort-wide spend ceiling would then become a global one and refuse this
    org (402 instead of 200).

    GREEN legitimate form: the pre-cohort org's own ``created_at`` predates
    ``COHORT_SINCE``."""
    _spend(COHORT_ORG, CAP_USD + 3.0)  # the cohort IS over — this org is not in it
    capture_env.org["org_id"] = PRE_COHORT_ORG

    r = capture_env.client.post(
        "/v1/sessions", json={"conversation": _CONV, "harness": "claude"})

    assert r.status_code == 200, r.text
    assert capture_env.extraction_calls == ["_extract_session_v2"]
    assert not incidents.issues


def test_replay_of_a_captured_session_is_never_capped(capture_env):
    """No data loss, and no spurious 402 on an idempotent re-POST: a replay
    writes nothing and extracts nothing, so it must never be refused — even
    after the same cohort is pushed over the cap.

    REDs on: moving the cohort check OUT of the ``not session_existed or
    retry_failed_capture`` guard (an unconditional pre-admission gate would 402
    the replay).

    GREEN legitimate form: the session was captured while under the cap; the
    trip is armed only afterwards."""
    first = capture_env.client.post(
        "/v1/sessions",
        json={"conversation": _CONV, "harness": "claude",
              "session_id": "sess-3665-replay"})
    assert first.status_code == 200, first.text
    assert capture_env.extraction_calls == ["_extract_session_v2"]

    _spend(COHORT_ORG, CAP_USD + 1.0)  # arm the trip AFTER the capture landed

    replay = capture_env.client.post(
        "/v1/sessions",
        json={"conversation": _CONV, "harness": "claude",
              "session_id": "sess-3665-replay"})
    assert replay.status_code == 200, replay.text
    assert replay.json()["extracted"] == 0
    assert capture_env.extraction_calls == ["_extract_session_v2"], (
        "a replay must not re-extract")
    assert _session_count(COHORT_ORG) == 1


# ── 3. the MCP surface reports the same class ───────────────────────────────


def test_mcp_capture_reports_err_quota_when_cohort_is_over_cap(
        tmp_path, monkeypatch, incidents):
    """The MCP tool shares the impl, so it must report the SAME refusal class:
    ``ERR_QUOTA`` (``-32006``) with status 402.

    REDs on: dropping the ``if status == 402: out["code"] = ERR_QUOTA``
    mapping in the MCP capture handler (the dict would carry only
    ``status: 402`` and ``result["code"]`` would KeyError).

    GREEN legitimate form: cap armed, real ledger row over it, MCP-hosted
    org ContextVars."""
    from tortoise.mcp_auth import _current_org_id, _current_org_limits
    from tortoise.mcp_server import ERR_QUOTA, tortoise_session_capture

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv(_cc.CAP_ENV, str(CAP_USD))
    monkeypatch.setenv(_cc.SINCE_ENV, COHORT_SINCE)

    with patched_tortoise_sdk(str(tmp_path / "mcp.db")):
        _provision(COHORT_ORG, IN_COHORT_CREATED_AT)
        _spend(COHORT_ORG, CAP_USD + 1.0)
        tok_t = _current_org_id.set(COHORT_ORG)
        tok_l = _current_org_limits.set(
            {"org_id": COHORT_ORG, "tier": "free", "max_points": 100000})
        try:
            result = tortoise_session_capture(
                conversation=_CONV, harness="claude", session_id="s-3665-mcp")
        finally:
            _current_org_id.reset(tok_t)
            _current_org_limits.reset(tok_l)

    assert result["status"] == 402, result
    assert result["code"] == ERR_QUOTA, result
    assert "Cohort LLM spend cap reached" in result["error"], result
    assert _session_count(COHORT_ORG) == 0


# ── 4. the chain: captured spend reaches the ledger the cap reads ───────────


def test_capture_ledger_write_is_what_the_cohort_spend_reader_reads(
        capture_env, monkeypatch):
    """The capture lane's MEASURED cost lands on the durable ledger and the
    cap's reader sees it — the chain that makes the cap bound capture spend at
    all, not just ask spend.

    REDs on: deleting the ``record_capture_usage`` call at the emission site
    (the reader would still return 0.0 after a real 200 capture); and on any
    reader that stops summing ``capture_cost_usd``.

    GREEN legitimate form: a real 200 capture through the cost-reporting
    extractor stub, then the real reader over the real ledger row."""
    from tortoise import sdk as sdk_mod
    from tortoise.metering import get_cohort_spend_usd

    monkeypatch.setattr(sdk_mod, "_V2SessionMock", _CostModel)

    before = get_cohort_spend_usd([COHORT_ORG])
    r = capture_env.client.post(
        "/v1/sessions",
        json={"conversation": _CONV, "harness": "claude",
              "session_id": "sess-3665-ledger"})
    assert r.status_code == 200, r.text
    after = get_cohort_spend_usd([COHORT_ORG])

    assert after > before, (
        f"a real capture must grow the cohort's ledger spend ({before} → {after})")
    assert before == 0.0
    assert after >= 0.005 - 1e-9, (
        "the measured provider charges (s1 0.001 + s2/s4 0.002 each, at least "
        f"one call per stage) must ride the ledger verbatim, got {after}")


# ── 5. the CONTROL-PLANE (Supabase) legs of the same reader/writer ─────────
#
# The tests above run the registry lane (embedded). The hosted control plane is
# where the cap will actually be armed, so its two seams get behavioural tests
# too — with a stub plane, no network. This closes the "only the embedded leg is
# covered" gap, and it is the leg where a silent PostgREST row cap would turn a
# spend ceiling into a fail-open.


class _StubPlane:
    """A control-plane double that HONOURS the filter ops and limit it is
    given — so a test failing to request a filter cannot pass by accident, and
    the sum the reader computes is the sum the plane would really return."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries: list[dict] = []
        self.rpcs: list[tuple[str, dict]] = []

    def query(self, table, *, select=None, filters=None, limit=None):
        self.queries.append({"table": table, "select": select,
                             "filters": filters, "limit": limit})
        rows = [dict(r) for r in self.rows]
        for col, op, value in filters or []:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == value]
            elif op == "gt":
                rows = [r for r in rows
                        if r.get(col) is not None and r.get(col) > value]
            elif op == "in":
                vals = list(value) if isinstance(value, (list, tuple, set)) \
                    else [value]
                rows = [r for r in rows if r.get(col) in vals]
            else:  # pragma: no cover - the reader uses only eq/gt/in
                raise AssertionError(f"_StubPlane got an unhandled op {op!r}")
        return rows[:limit] if limit is not None else rows

    def rpc(self, fn, body=None):
        self.rpcs.append((fn, dict(body or {})))
        return None


def test_supabase_cohort_reader_filters_to_the_cohort_and_sums_both_lanes():
    """The control-plane reader sums ``ask_cost_usd + capture_cost_usd`` over
    the COHORT only, in one bounded read.

    REDs on: dropping the ``org_id in (...)`` filter (a non-cohort org's spend
    would be added — 0.25 becomes 1.0); summing only one lane (0.10 or 0.15);
    and dropping the ``limit`` bound (no ``limit`` key in the recorded query).

    GREEN legitimate form: the stub returns one cohort row and one outsider."""
    from tortoise.supabase_control import metering_cohort_spend

    plane = _StubPlane([
        {"org_id": "cohort-a", "period": "2026-09", "ask_cost_usd": 0.10,
         "capture_cost_usd": 0.15},
        {"org_id": "not-in-cohort", "period": "2026-09",
         "ask_cost_usd": 0.50, "capture_cost_usd": 0.50},
    ])

    total = metering_cohort_spend(plane, ["cohort-a"], "2026-09")

    assert total == pytest.approx(0.25), total
    q = plane.queries[-1]
    assert q["table"] == "metering_records"
    assert ("org_id", "in", ["cohort-a"]) in q["filters"], q["filters"]
    assert ("period", "eq", "2026-09") in q["filters"], q["filters"]
    assert q["limit"] == 2, q["limit"]


def test_supabase_cohort_reader_fails_closed_on_a_truncated_read():
    """A read returning more rows than the cohort has orgs is a garbage read —
    it must RAISE, never be summed. Summing it would price the cohort from an
    untrustworthy aggregate; a spend ceiling must fail closed instead.

    REDs on: deleting the ``len(rows) > len(wanted)`` guard (the call would
    return a garbage total instead of raising).

    GREEN legitimate form: the stub returns two rows for a one-org cohort."""
    from tortoise.supabase_control import metering_cohort_spend

    plane = _StubPlane([
        {"org_id": "cohort-a", "period": "2026-09", "ask_cost_usd": 1.0,
         "capture_cost_usd": 0.0},
        {"org_id": "cohort-a", "period": "2026-09", "ask_cost_usd": 1.0,
         "capture_cost_usd": 0.0},
    ])

    with pytest.raises(RuntimeError, match="more rows than the cohort"):
        metering_cohort_spend(plane, ["cohort-a"], "2026-09")


def test_supabase_capture_increment_calls_the_atomic_rpc_with_the_measurement():
    """The capture lane reaches the durable ledger through the ATOMIC RPC (not
    a read-modify-write), carrying the measured charge.

    REDs on: swapping the RPC for a plain table write (``plane.rpcs`` would be
    empty) or dropping the charge from the body (``p_cost_usd`` → 0.0).

    GREEN legitimate form: the measured charge, verbatim."""
    from tortoise.supabase_control import metering_increment_capture_cost

    plane = _StubPlane()
    metering_increment_capture_cost(plane, "cohort-a", "2026-09",
                                    calls=1, cost_usd=0.004321)

    assert plane.rpcs == [("metering_increment_capture_cost", {
        "p_org_id": "cohort-a", "p_period": "2026-09", "p_calls": 1,
        "p_cost_usd": 0.004321})], plane.rpcs
    assert plane.queries == [], (
        "the ledger write must be the atomic RPC, never a read-modify-write")


def test_cohort_larger_than_the_priced_bound_fails_closed(monkeypatch):
    """A cohort bigger than the bound this cap can price from ONE bounded read
    is refused (fail-closed), never partially summed — a truncated org set
    would both understate spend AND drop orgs out of the membership test.

    REDs on: deleting the ``len(ids) > _MAX_COHORT_ORGS`` guard (the resolution
    would return a truncated-looking org list instead of raising).

    GREEN legitimate form: a control plane whose organizations read returns one
    org over the bound."""
    import tortoise.supabase_control as sc
    from tortoise.quota import QuotaCheckError

    over = _cc._MAX_COHORT_ORGS + 1
    plane = _StubPlane([{"id": f"o{i}", "created_at": IN_COHORT_CREATED_AT}
                        for i in range(over)])
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: plane)

    with pytest.raises(QuotaCheckError, match="larger than the"):
        _cc.cohort_org_ids(COHORT_SINCE)

    # and the bound is exactly where it says it is: one fewer org resolves
    plane.rows = plane.rows[:_cc._MAX_COHORT_ORGS]
    assert len(_cc.cohort_org_ids(COHORT_SINCE)) == _cc._MAX_COHORT_ORGS


# ── 6. the cap's OWN configuration contract (fail-open is the whole risk) ───


def test_present_but_degenerate_cap_fails_closed_never_disarms(monkeypatch):
    """Only an ABSENT ``TORTOISE_COHORT_COST_CAP_USD`` disables the cap. Any
    PRESENT value that cannot bound anything raises — a ceiling someone
    believes is armed (or emergency-stopped) while actually disarmed is the
    "configured but not enforced" false PASS this lane exists to prevent.

    REDs on: treating ``0``/``-1``/``""`` as "off" (the earlier bug — the
    resolved cap would be ``None`` and the ceiling silently gone), and on a
    sign-only check that lets ``nan``/``inf`` through (``float()`` accepts
    both and neither is ``<= 0``, so ``spent >= cap`` is permanently False —
    the same trap hosted_api.py records for TORTOISE_HEALTH_PROBE_INTERVAL).

    GREEN legitimate form: the variable absent → ``None`` (off)."""
    from tortoise.quota import QuotaCheckError

    assert _cc.resolve_cohort_cost_cap({}) is None, "absent must mean off"
    assert _cc.resolve_cohort_cost_cap(
        {_cc.CAP_ENV: "5.0", _cc.SINCE_ENV: COHORT_SINCE}
    ) == _cc.CohortCostCap(cap_usd=5.0, since=COHORT_SINCE)

    # "nan"/"inf"/"1e999" are the ones a sign check alone would let through:
    # float() parses all three, and neither nan<=0 nor inf<=0 is True.
    for degenerate in ("0", "-1", "", "   ", "not-a-number",
                       "nan", "NaN", "inf", "Infinity", "-inf", "1e999"):
        with pytest.raises(QuotaCheckError, match=re.escape(_cc.CAP_ENV)):
            _cc.resolve_cohort_cost_cap(
                {_cc.CAP_ENV: degenerate, _cc.SINCE_ENV: COHORT_SINCE})

    # a missing SINCE is likewise fail-closed (an unscoped cap would hit all)
    with pytest.raises(QuotaCheckError, match=re.escape(_cc.SINCE_ENV)):
        _cc.resolve_cohort_cost_cap({_cc.CAP_ENV: "5.0"})


def test_selfhost_transport_exemption_survives_the_to_thread_dispatch(
        monkeypatch):
    """The gate runs off the event loop (``asyncio.to_thread``), and the
    selfhost-transport exemption is a ContextVar — so the copy must carry it.
    Mirrors ``hosted_api``'s exact dispatch shape.

    The cohort and the spend are stubbed so the only thing that can prevent
    the trip is the exemption: without it the org IS in the cohort and the
    spend IS over the cap, so the call would raise.

    REDs on: dropping the contextvar read from the gate
    (``_selfhost_transport_active()``) — the stubbed cohort+spend would then
    trip and the call would raise instead of returning None; and on a dispatch
    that does not copy contextvars to the worker thread.

    GREEN legitimate form: the transport ContextVar set in the caller."""
    import asyncio

    import tortoise.metering as _metering
    from tortoise.cohort_cost import enforce_cohort_cost_cap
    from tortoise.transport import _selfhost_transport

    monkeypatch.setattr(_cc, "cohort_org_ids", lambda since: ["in-cohort"])
    monkeypatch.setattr(_metering, "get_cohort_spend_usd",
                        lambda ids, period: 999.0)

    async def _run():
        tok = _selfhost_transport.set(True)
        try:
            # an armed cap, a cohort that IS over it — only the exemption
            # can prevent the trip
            return await asyncio.to_thread(
                enforce_cohort_cost_cap, {"org_id": "in-cohort"},
                cap=_cc.CohortCostCap(cap_usd=0.0001, since=COHORT_SINCE))
        finally:
            _selfhost_transport.reset(tok)

    assert asyncio.run(_run()) is None  # no raise == exemption reached the thread

    # negative control: WITHOUT the exemption the very same call trips
    async def _run_unbudgeted():
        return await asyncio.to_thread(
            enforce_cohort_cost_cap, {"org_id": "in-cohort"},
            cap=_cc.CohortCostCap(cap_usd=0.0001, since=COHORT_SINCE))

    with pytest.raises(_cc.CohortCostCapExceeded):
        asyncio.run(_run_unbudgeted())


def test_non_finite_capture_cost_never_poisons_the_cohort_sum(
        tmp_path, monkeypatch):
    """A ``nan``/``inf`` charge is dropped to 0.0 before it can reach the
    ledger. This is the cap's own substrate: a non-finite row would poison the
    cohort ``SUM``, making ``spent >= cap`` permanently False — a silently
    disarmed ceiling.

    REDs on: removing the ``math.isfinite`` guard in ``record_capture_usage``
    (the non-finite charge would be written through to the ledger row).

    GREEN legitimate form: a finite charge is recorded verbatim."""
    from tortoise.metering import get_cohort_spend_usd, record_capture_usage

    with patched_tortoise_sdk(str(tmp_path / "nonfinite.db")):
        assert record_capture_usage("org-3665-finite", cost_usd=float("nan"))
        assert record_capture_usage("org-3665-finite", cost_usd=float("inf"))
        assert record_capture_usage("org-3665-finite", cost_usd=2.5)

        assert get_cohort_spend_usd(["org-3665-finite"]) == pytest.approx(2.5)
