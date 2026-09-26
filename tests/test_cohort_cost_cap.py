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

import logging
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import _as_dt
from tortoise import cohort_cost as _cc
from tortoise import hosted_api as _ha
from tortoise import operator_alert as oa
from tortoise.alert_store import AlertStore
from tortoise.hosted_api import app, get_current_org
from tortoise.hosted_backup import MemoryStorage

CAP_USD = 5.0
#: #3825: the metering WINDOW the seam-level tests below supply explicitly. The
#: cohort fixture's orgs carry no ``subscription_id``, so the real resolver
#: returns the calendar month in UTC (D13) — the behavioural tests further down
#: use that real path (``metering._current_period``); these stub-plane tests
#: pass the window in, because what they exercise is the SQL contract.
WINDOW = ("2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00")
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
    # #4010 (merged after this branch): the resolved-limits shape now ALWAYS
    # carries `max_sessions` — the key is present and explicitly None
    # (unlimited). A MISSING key is fail-closed (500), so a hand-built limits
    # dict that omits it no longer models the real resolver
    # (tortoise/quota.py `resolve_org_limits`).
    return {"org_id": org_id, "tier": "free", "key_id": "k-3665",
            "legacy_full_access": True, "max_points": 100000,
            "max_sessions": None}


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
    """Point the alert channel at a REAL AlertStore over fake transport.

    ONE patch covers both ways a cap incident reaches the channel: the cap
    FIRING (``file_cohort_cost_incident`` -> ``_cc._alert_store`` -> here) and
    the UNENFORCEABLE alert (``report_unenforceable_cap`` -> ``alert_operator``
    -> here). ``_cc._alert_store`` now DELEGATES to ``operator_alert.alert_store``
    (#3981), so patching the seam it delegates to is what keeps this fixture
    honest; patching ``_cc._alert_store`` alone would leave the unenforceable
    alert un-injected and silently unfiled.
    """
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
    monkeypatch.setattr(oa, "alert_store", lambda: store)
    return channels


def _op_channels_for(monkeypatch):
    """A recording operator-alert store, injected at the seam ``incidents`` uses.

    Returns ``(store, channels)``; ``store`` is an ``AlertStore`` over the fake
    transport so the assertion can read the FILED title, and its calls are
    recorded through the channels. Distinct from ``incidents`` only in not being
    a fixture, so a test that must avoid the HTTP endpoint can inject it too.
    """
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

    class _Recording:
        def __init__(self, inner):
            self.inner = inner
            self.calls: list[tuple] = []

        def open_incident_state(self, kind, org_id="", detail=None):
            self.calls.append((kind, org_id, dict(detail or {})))
            return self.inner.open_incident_state(kind, org_id, detail)

    recording = _Recording(store)
    monkeypatch.setattr(oa, "alert_store", lambda: recording)
    return recording, channels


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
    # #4614: the cohort refusal is a structured detail with its OWN code. Both
    # halves matter: the `message` is what a human reads, and the code is what
    # a caller branches on — a spend cap read as `quota_exceeded` would send
    # the user to buy a plan that cannot lift it.
    assert isinstance(detail, dict), (
        f"the spend-cap refusal is still prose — indistinguishable from a "
        f"plan-limit refusal by any caller: {detail!r}")
    assert detail["code"] == "cohort_cost_cap", detail
    assert detail["code"] != "quota_exceeded", detail
    message = detail["message"]
    assert "Cohort LLM spend cap reached" in message, detail
    # The TENANT-VISIBLE body must NOT carry the cohort-wide aggregate: it is
    # the SUM across every org in the cohort, so publishing it would disclose
    # the other tenants' COGS (and, against a tenant's own observable
    # run-rate, theirs by subtraction). The figure belongs to the internal
    # sinks — the AlertStore incident and the server-side log. REDs on:
    # interpolating ``spent``/``len(ids)`` back into the message.
    assert f"{CAP_USD + 1.0:.4f}" not in message, detail
    assert "$" not in message, detail
    assert capture_env.extraction_calls == [], (
        "the cap must refuse BEFORE any extraction is dispatched")
    assert _session_count(COHORT_ORG) == 0, "nothing may be written"


def test_over_cap_refusal_is_observable_as_an_alert_incident(
        capture_env, incidents):
    """The firing raises an AlertStore incident (deduped, GitHub + Telegram) —
    the acceptance evidence is the incident, not a log line.

    REDs on: dropping the ``file_cohort_cost_incident`` call (no issue is
    filed), or dropping the ``detail`` argument (the incident body would lose
    the measured spend). Removing the ``asyncio.to_thread`` dispatch alone is
    NOT caught here — the call is still reached synchronously.

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


def test_cap_firing_files_with_the_sweep_disabled(
        monkeypatch, real_operator_alert_store):
    """D5a: a cap that FIRES files on a backups-disabled deployment.

    Pins the REAL builder, NOT the injected ``incidents`` seam: routing this
    through the seam makes it GREEN even if ``_cc._alert_store`` is reverted to
    the sweep-gated body, which would void the regression guard. The autouse
    ``_operator_alert_isolation`` would substitute ``lambda: None`` — hence the
    ``real_operator_alert_store`` opt-out.

    The object store is the MEMORY seam (``TORTOISE_BACKUP_STORAGE=memory``)
    rather than a patched ``ha._backup_storage``: the light leg builds its own
    store from env, so patching the hosted module's storage would leave the
    ``R2_*`` vars to decide the outcome and the test would measure the wrong
    builder.
    """
    from tortoise import github_issue as gi
    from tortoise import telegram_push as tp

    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    monkeypatch.setenv("TORTOISE_BACKUP_STORAGE", "memory")
    monkeypatch.setenv("DR_ISSUES_PAT", "pat-test-only")
    monkeypatch.setenv("GH_REPO", "daniel-ospina/tortoise")
    monkeypatch.setenv("BACKUP_ALERT_ASSIGNEE", "daniel-ospina")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "tg-chat")
    filed: list[str] = []
    monkeypatch.setattr(
        gi, "create_issue",
        lambda repo, pat, title, body, assignee=None: (
            filed.append(title) or 4242))
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    assert _ha._backup_config_safe() is None, (
        "precondition: the sweep is disabled, which is the gate under test")
    assert _cc.file_cohort_cost_incident(COHORT_ORG, {"spent_usd": 99.0}) is True
    assert filed == [f"[DR] {_cc.INCIDENT_KIND} — {COHORT_ORG}"], filed


def test_unenforceable_cap_files_its_own_kind_directly(monkeypatch):
    """The unenforceable-cap reporter dispatches its OWN kind — asserted WITHOUT
    the HTTP endpoint, so it does not depend on the transport wait budget (which
    a cold embedder load breaches on a loaded box).

    REDs on: dropping the ``alert_operator`` call in ``report_unenforceable_cap``
    (no incident), or reusing ``INCIDENT_KIND`` (the kind would read as a cap
    FIRING — the conflation that makes 'cannot say' look like 'over budget').
    """
    from tortoise.quota import QuotaCheckError

    store, ch = _op_channels_for(monkeypatch)
    _cc.report_unenforceable_cap(COHORT_ORG, QuotaCheckError("window"))
    assert oa.join_operator_alerts() == 0, "the dispatch is asynchronous"

    assert list(ch.issues.values()) == [
        f"[DR] {_cc.UNENFORCEABLE_INCIDENT_KIND} — {COHORT_ORG}"], ch.issues
    assert ch.telegram, "the incident must also push, not only file"
    assert store.calls == [(_cc.UNENFORCEABLE_INCIDENT_KIND, COHORT_ORG,
                             {"error_type": "QuotaCheckError"})]


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


def test_unresolvable_window_is_served_and_alerted_never_500(
        capture_env, incidents, caplog):
    """#3981 P0: a PAYING org whose metering window is UNRESOLVABLE gets a
    NORMAL response — 200, extraction runs — plus an OPERATOR alert. Never a
    500.

    This is the exact shape ``checkout.session.completed`` leaves behind: a
    ``subscription_id`` with no period, so ``metering._current_period`` raises
    ``QuotaCheckError``. Pre-fix the admission gate let that escape and
    ``hosted_api`` turned it into a 500 — a NEW unconditional user-facing
    refusal on the capture path, BEFORE any spend, which the owner's #3981
    ruling forbids (the gate was pure calendar arithmetic before #3825 and
    could not raise). RED on the pre-fix tree (500 instead of 200).

    Mutations caught: removing the ``except QuotaCheckError`` absorb in
    ``enforce_cohort_cost_cap`` (the 500 returns); absorbing WITHOUT alerting
    (no ``UNENFORCEABLE COHORT COST CAP`` record — the silent, unenforceable
    cap); and substituting a calendar month (the cohort sum would read as 0,
    so a capture that SHOULD be under an armed cap at seed-0 spend would still
    200 — see ``test_over_cap_cohort_capture_refused_402_no_extraction_no_write``
    for the paired enforcement path, which stays red if the guard is simply
    deleted).

    GREEN legitimate form: a subscription org with NULL period columns in an
    armed cohort with no spend — the unenforceable case, served and alerted.

    TWO kinds are expected on the incident channel, and they are DISTINCT:
    ``COHORT_CAP_UNENFORCEABLE`` (the cap could not be evaluated — this lane)
    and ``UNMETERED_INCREMENT`` (the capture's own ledger write dropped for the
    same unresolvable window). Neither is ``COHORT_COST_CAP`` — an unenforceable
    cap is not a cap firing, and it is the KIND that says so, not the absence of
    an incident.
    """
    reg = _ha._make_sdk(namespace="registry")._get_registry()
    reg.query(
        "MATCH (t:Team {id: $tid}) SET t.subscription_id = 'sub-3981', "
        "t.current_period_start = null, t.current_period_end = null",
        params={"tid": COHORT_ORG},
    )

    with caplog.at_level(logging.ERROR, logger="tortoise.cohort_cost"):
        r = capture_env.client.post(
            "/v1/sessions", json={"conversation": _CONV, "harness": "claude"})

    assert r.status_code == 200, r.text
    assert capture_env.extraction_calls == ["_extract_session_v2"], (
        "an unenforceable cap must not block the spend it cannot measure")
    assert any("UNENFORCEABLE COHORT COST CAP" in rec.getMessage()
               for rec in caplog.records), [
        rec.getMessage() for rec in caplog.records]

    assert oa.join_operator_alerts() == 0, "the dispatch is asynchronous"
    titles = list(incidents.issues.values())
    # MEMBERSHIP, not equality: on this path the capture's ledger write ALSO
    # raises (the same unresolvable window), so the capture_ledger lane files a
    # second incident. The two KINDS distinguish them now — the assertion that
    # used to live here ("an unenforceable cap is NOT a cap firing — it must not
    # raise a cap incident") is now the ABSENCE of COHORT_COST_CAP, not the
    # absence of any incident.
    assert not any(f"[DR] {_cc.INCIDENT_KIND}" in t for t in titles), titles
    assert any(f"[DR] {_cc.UNENFORCEABLE_INCIDENT_KIND}" in t for t in titles), (
        f"the unenforceable cap must file its OWN kind: {titles}")
    assert any(f"[DR] {oa.UNMETERED_INCREMENT_KIND}" in t for t in titles), (
        "the capture ledger's dropped increment must file too: "
        f"{titles}")
    assert incidents.telegram, "the incidents must push, not only file"


def test_checkout_written_window_makes_the_cap_enforceable(
        capture_env, incidents, monkeypatch):
    """#4216 end-to-end: the CHECKOUT path now writes the org's window, so the
    cohort cap IS enforced for it (402) — instead of being absorbed as
    unenforceable and served (the pre-#4216 behaviour, and the paired negative
    directly above).

    REDs on the pre-#4216 tree: ``checkout.session.completed`` wrote
    ``subscription_id`` and NO period, so ``metering._current_period`` raised,
    ``enforce_cohort_cost_cap`` absorbed it (#3981) and this over-cap capture
    returned 200 with the extraction running — the cap silently unenforceable
    for a paying org. (With the checkout fix reverted, the anchor assertion
    below fires first; with THAT removed, the capture 200s — the 402 vs 200
    contrast is the same mutation either way.)

    Mutations caught: reverting the checkout window write (the 402 becomes a
    200); and the gate substituting a calendar month for a subscription org
    (the cohort read would target a different window and miss the ledger row).
    """
    import json
    from datetime import datetime

    from tortoise import billing as bl

    org_id = COHORT_ORG
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    start = int(datetime.fromisoformat(
        "2026-08-01T00:00:00+00:00").timestamp())
    end = int(datetime.fromisoformat(
        "2026-09-01T00:00:00+00:00").timestamp())
    monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature",
                        lambda self, payload, sig: {
                            "id": "evt_4216_e2e",
                            "type": "checkout.session.completed",
                            "data": {"object": {
                                "client_reference_id": org_id,
                                "customer": "cus_4216_e2e",
                                "subscription": "sub_4216_e2e"}}})
    monkeypatch.setattr(bl.StripeClient, "get_subscription",
                        lambda self, sid: {
                            "id": "sub_4216_e2e", "status": "active",
                            "current_period_start": start,
                            "current_period_end": end,
                            "items": {"data": []}})

    r = capture_env.client.post(
        "/webhooks/stripe", content=json.dumps({}),
        headers={"stripe-signature": "t=1,v1=x"})
    assert r.status_code == 200, r.text

    anchor = _ha._make_sdk(namespace="registry")._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN t.current_period_start, "
        "t.current_period_end", params={"id": org_id}).result_set[0]
    assert anchor[0] is not None and anchor[1] is not None, (
        f"the checkout webhook must persist a COMPLETE window: {anchor}")

    _spend(org_id, CAP_USD + 1.0)

    r = capture_env.client.post(
        "/v1/sessions", json={"conversation": _CONV, "harness": "claude"})
    assert r.status_code == 402, (
        "a metered paying org's cohort cap must be ENFORCED, not absorbed")
    assert capture_env.extraction_calls == []


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
            {"org_id": COHORT_ORG, "tier": "free", "max_points": 100000,
             "max_sessions": None})
        try:
            result = tortoise_session_capture(
                conversation=_CONV, harness="claude", session_id="s-3665-mcp")
        finally:
            _current_org_id.reset(tok_t)
            _current_org_limits.reset(tok_l)

    assert result["status"] == 402, result
    assert result["code"] == ERR_QUOTA, result
    # #4614: the MCP twin must surface the refusal's MESSAGE, never the Python
    # repr of the structured detail — stringifying the dict first bypassed
    # `_record_capture_last_error`'s flattening and painted a repr on the
    # dashboard sub-line and in the MCP tool result.
    assert result["error"].startswith("Cohort LLM spend cap reached"), result
    assert "{" not in result["error"] and "'code'" not in result["error"], (
        f"the MCP error text is a repr of the structured detail: "
        f"{result['error']!r}")
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
    from tortoise.metering import _current_period, get_cohort_spend_usd

    monkeypatch.setattr(sdk_mod, "_V2SessionMock", _CostModel)

    # #3825: the window is the org's own (no subscription → D13 calendar month
    # in UTC). Resolved once and reused, so the before/after pair reads the
    # same window the capture lane writes to.
    window = _current_period(COHORT_ORG)
    before = get_cohort_spend_usd([COHORT_ORG], window)
    r = capture_env.client.post(
        "/v1/sessions",
        json={"conversation": _CONV, "harness": "claude",
              "session_id": "sess-3665-ledger"})
    assert r.status_code == 200, r.text
    after = get_cohort_spend_usd([COHORT_ORG], window)

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
    """A control-plane double with NO network, mirroring the two SQL
    aggregates the cap uses (20260917000001).

    It is deliberately a SCALAR double: ``metering_cohort_spend`` and
    ``cohort_org_ids_since`` are reached through ``rpc_value``, so a reader
    that falls back to a filtered ROW read shows up as a recorded ``query`` —
    which is exactly the ``db-max-rows`` truncation fail-open the RPCs exist
    to make impossible. The reader tests therefore assert ``queries == []``.
    """

    def __init__(self, ledger=None, orgs=None):
        self.ledger = ledger or []
        self.orgs = orgs or []
        self.queries: list[dict] = []
        self.rpcs: list[tuple[str, dict]] = []

    def query(self, table, *, select=None, filters=None, limit=None):
        self.queries.append({"table": table, "select": select,
                             "filters": filters, "limit": limit})
        rows = [dict(r) for r in self.ledger]
        for col, op, value in filters or []:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == value]
            elif op == "gt":
                rows = [r for r in rows
                        if r.get(col) is not None and r.get(col) > value]
            else:  # pragma: no cover - the readers use no other op
                raise AssertionError(f"_StubPlane got an unhandled op {op!r}")
        return rows[:limit] if limit is not None else rows

    def rpc(self, fn, body=None, *, representation=False):
        self.rpcs.append((fn, dict(body or {})))
        return None

    def rpc_value(self, fn, body=None):
        self.rpcs.append((fn, dict(body or {})))
        p = body or {}
        if fn == "metering_cohort_spend":
            # #3825: the SQL is a HALF-OPEN OVERLAP test over the window, not a
            # ``period = p_period`` month equality. Mirror it exactly, or a
            # boundary regression would pass against this double.
            wanted = {str(i) for i in (p.get("p_org_ids") or [])}
            start = _as_dt(p.get("p_period_start"))
            end = _as_dt(p.get("p_period_end"))
            total = 0.0
            for r in self.ledger:
                if str(r.get("org_id")) not in wanted:
                    continue
                r_start = _as_dt(r.get("period_start"))
                r_end = _as_dt(r.get("period_end"))
                if r_start is None or r_end is None:
                    continue
                if r_start < end and r_end > start:
                    total += float(r.get("ask_cost_usd") or 0.0)
                    total += float(r.get("capture_cost_usd") or 0.0)
            return total
        if fn == "cohort_org_ids_since":
            since = str(p.get("p_since") or "")
            limit = int(p.get("p_limit") or 0)
            ids = [str(o["id"]) for o in self.orgs
                   if o.get("id") and str(o.get("created_at") or "") > since]
            ids.sort()
            return ids[:limit + 1]
        raise AssertionError(f"_StubPlane got an unhandled rpc {fn!r}")


def test_supabase_cohort_reader_sums_both_lanes_over_the_cohort_via_rpc():
    """The control-plane reader aggregates the COHORT's two cost lanes
    server-side, in ONE scalar RPC call.

    REDs on: passing the wrong org set (a non-cohort org's spend would be
    added — 0.25 becomes 1.0); summing only one lane (0.10 or 0.15); and on
    reverting to a filtered ROW read, which a PostgREST ``db-max-rows`` cap
    can silently truncate into an UNDERSTATED spend (``plane.queries`` would
    be non-empty) — the fail-open this RPC designs out.

    GREEN legitimate form: one cohort row and one outsider on the ledger."""
    from tortoise.supabase_control import metering_cohort_spend

    plane = _StubPlane(ledger=[
        {"org_id": "cohort-a", "period_start": WINDOW[0],
         "period_end": WINDOW[1], "ask_cost_usd": 0.10,
         "capture_cost_usd": 0.15},
        {"org_id": "not-in-cohort", "period_start": WINDOW[0],
         "period_end": WINDOW[1], "ask_cost_usd": 0.50,
         "capture_cost_usd": 0.50},
    ])

    total = metering_cohort_spend(plane, ["cohort-a"], *WINDOW)

    assert total == pytest.approx(0.25), total
    assert plane.rpcs == [("metering_cohort_spend", {
        "p_org_ids": ["cohort-a"], "p_period_start": WINDOW[0],
        "p_period_end": WINDOW[1]})], plane.rpcs
    assert plane.queries == [], (
        "a filtered row read can be silently truncated by db-max-rows and "
        "read as a cheaper cohort — the total must come from the aggregate")


def test_supabase_cohort_reader_fails_closed_when_the_aggregate_is_unreachable():
    """A spend-ceiling read that FAILS must raise, never read as a cheap
    cohort — a zero view is the fail-open the cap could never recover from
    (#686's discipline; the earlier revision's row-count guard was dead code
    that validated the opposite).

    REDs on: swallowing the control-plane failure and returning 0.0 (the call
    would return instead of raising).

    GREEN legitimate form: an RPC that raises (unreachable plane)."""
    from tortoise.supabase_control import metering_cohort_spend

    class _Broken(_StubPlane):
        def rpc_value(self, fn, body=None):
            raise RuntimeError("Supabase unreachable (simulated)")

    with pytest.raises(RuntimeError, match="unreachable"):
        metering_cohort_spend(_Broken(), ["cohort-a"], *WINDOW)


def test_supabase_cohort_reader_rejects_a_non_finite_aggregate():
    """A non-finite aggregate is refused: ``spent >= cap`` against ``nan`` is
    permanently False — a silently disarmed ceiling.

    REDs on: dropping the ``math.isfinite`` guard in the reader (the call
    would return ``inf``/``nan`` instead of raising).

    GREEN legitimate form: the aggregate comes back as ``inf``."""
    from tortoise.supabase_control import metering_cohort_spend

    class _Poisoned(_StubPlane):
        def rpc_value(self, fn, body=None):
            return float("inf")

    with pytest.raises(RuntimeError, match="not finite"):
        metering_cohort_spend(_Poisoned(), ["cohort-a"], *WINDOW)


def test_supabase_capture_increment_calls_the_atomic_rpc_with_the_measurement():
    """The capture lane reaches the durable ledger through the ATOMIC RPC (not
    a read-modify-write), carrying the measured charge.

    REDs on: swapping the RPC for a plain table write (``plane.rpcs`` would be
    empty) or dropping the charge from the body (``p_cost_usd`` → 0.0).

    GREEN legitimate form: the measured charge, verbatim."""
    from tortoise.supabase_control import metering_increment_capture_cost

    plane = _StubPlane()
    metering_increment_capture_cost(plane, "cohort-a", *WINDOW,
                                    calls=1, cost_usd=0.004321)

    assert plane.rpcs == [("metering_increment_capture_cost", {
        "p_org_id": "cohort-a", "p_period_start": WINDOW[0],
        "p_period_end": WINDOW[1], "p_calls": 1,
        "p_cost_usd": 0.004321})], plane.rpcs
    assert plane.queries == [], (
        "the ledger write must be the atomic RPC, never a read-modify-write")


def test_cohort_larger_than_the_priced_bound_fails_closed(monkeypatch):
    """A cohort bigger than the bound this cap will gate is refused
    (fail-closed), never partially gated — a dropped org reads as "outside the
    cohort" and disarms the cap for exactly that org.

    REDs on: deleting the ``len(ids) > _MAX_COHORT_ORGS`` guard (the resolution
    would return a truncated org list instead of raising).

    GREEN legitimate form: a cohort RPC returning one org over the bound."""
    import tortoise.supabase_control as sc
    from tortoise.quota import QuotaCheckError

    over = _cc._MAX_COHORT_ORGS + 1
    plane = _StubPlane(orgs=[{"id": f"o{i:05d}",
                              "created_at": IN_COHORT_CREATED_AT}
                             for i in range(over)])
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: plane)

    with pytest.raises(QuotaCheckError, match="larger than the"):
        _cc.cohort_org_ids(COHORT_SINCE)

    # and the bound is exactly where it says it is: one fewer org resolves
    plane.orgs = plane.orgs[:_cc._MAX_COHORT_ORGS]
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

    # ...and so is a SINCE that is not an unambiguous ISO-8601 instant. In the
    # registry lane ``created_at > since`` is a STRING comparison, so an
    # unparseable value orders lexicographically, selects an EMPTY cohort, and
    # disarms the cap with no error — the same "configured but not enforced"
    # false PASS the cap check above exists to prevent. "2026-08-01" and the
    # offset-less form parse as NAIVE datetimes and are rejected too (an
    # ambiguous instant must not silently mean UTC).
    for bad_since in ("yesterday", "2026-08-01", "2026-08-01T00:00:00",
                      "2026-13-45T00:00:00+00:00", "not-a-date"):
        with pytest.raises(QuotaCheckError, match=re.escape(_cc.SINCE_ENV)):
            _cc.resolve_cohort_cost_cap(
                {_cc.CAP_ENV: "5.0", _cc.SINCE_ENV: bad_since})

    # a non-UTC offset is legitimate and normalises to UTC
    normalised = _cc.resolve_cohort_cost_cap(
        {_cc.CAP_ENV: "5.0", _cc.SINCE_ENV: "2026-08-01T05:00:00+05:00"})
    assert normalised == _cc.CohortCostCap(
        cap_usd=5.0, since="2026-08-01T00:00:00+00:00")


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
    from datetime import datetime

    import tortoise.metering as _metering
    from tortoise.cohort_cost import enforce_cohort_cost_cap
    from tortoise.transport import _selfhost_transport

    monkeypatch.setattr(_cc, "cohort_org_ids", lambda since: ["in-cohort"])
    monkeypatch.setattr(_metering, "get_cohort_spend_usd",
                        lambda ids, period: 999.0)
    # #3825: the gate resolves the REQUESTING org's metering window before it
    # reads the ledger (a registry read in the embedded lane). Stub it — this
    # test is about the ContextVar surviving the ``to_thread`` dispatch, not
    # about anchor resolution.
    monkeypatch.setattr(
        _metering, "_current_period",
        lambda org_id: _metering.MeteringPeriod(
            start=datetime.fromisoformat("2026-09-01T00:00:00+00:00"),
            end=datetime.fromisoformat("2026-10-01T00:00:00+00:00")))

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
    from tortoise.metering import _current_period, get_cohort_spend_usd, record_capture_usage

    with patched_tortoise_sdk(str(tmp_path / "nonfinite.db")):
        assert record_capture_usage("org-3665-finite", cost_usd=float("nan"))
        assert record_capture_usage("org-3665-finite", cost_usd=float("inf"))
        assert record_capture_usage("org-3665-finite", cost_usd=2.5)

        # #3825: the reader takes a WINDOW. The org has no subscription (an
        # unknown registry org → D13 calendar month in UTC).
        window = _current_period("org-3665-finite")
        assert get_cohort_spend_usd(["org-3665-finite"], window) == \
            pytest.approx(2.5)
