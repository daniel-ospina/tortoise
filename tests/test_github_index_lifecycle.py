"""#1725 Slice 0 — GitHub index lifecycle tests (Tasks 2/3/4/5/7).

Hosted-API level (TestClient + real SDK on a temp store): quota honest-fail,
per-team single-flight (reuse + TTL eviction), ONE-repo bounded first run,
auto-index-after-connect, re-poll endpoint, cursor + legacy-backfill marker
persistence, state-key registration, and the historical observation-dedup
script (dry-run + opt-in merge).
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY",
                      "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")

import sys
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests._github_mock import MockGitHubTransport, gh_issue
from tests._http_fixtures import patched_tortoise_sdk
from tests._verdict import require_answered, require_within, transport_wait_bound_s
from tortoise.hosted_api import (
    _ALLOWED_STATE_KEYS,
    _INDEX_JOBS,
    _ONBOARDING_DEFAULT_STATE,
    _start_index_job,
    app,
)
from tortoise.indexer.github_indexer import GitHubFetchError, GitHubIndexer
from tortoise.sdk import TortoiseSDK

_GRAPH_SCRIPTS = str(Path(__file__).resolve().parent.parent / "graph-scripts")
if _GRAPH_SCRIPTS not in sys.path:
    sys.path.insert(0, _GRAPH_SCRIPTS)

ORG_ID = "test-team-1"


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    """Deterministic, fast embeddings (optional everywhere — None is fine)."""
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        lambda *a, **k: None)


def _provision(db_path: str, *, org_id: str = ORG_ID,
               max_points: int | None = 10000) -> None:
    """Provision a Team node (mirrors hosted provision_tenant shape) with an
    encrypted GitHub token + org — on the SAME temp store the TestClient
    fixture redirects all SDK construction to (the per-path registry graph)."""
    reg_sdk = TortoiseSDK(db_path=db_path, namespace="registry")
    from tortoise.crypto import encrypt_token
    reg_sdk._get_registry().query(
        "CREATE (t:Team {id:$id, name:$name, tier:'free', "
        "max_users:1, max_graphs:1, max_api_keys:2, "
        "max_points:$mp})",
        params={"id": org_id, "name": org_id, "mp": max_points},
    )
    reg_sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) "
        "SET t.github_token_enc=$tok, t.github_org=$org",
        params={"id": org_id,
                "tok": encrypt_token("fake-token"), "org": "acme"},
    )
    reg_sdk.close()


@pytest.fixture
def client(tmp_path):
    """TestClient with all SDK construction redirected to a temp store.

    The org_id is UNIQUE PER TEST: _make_sdk(namespace=org_id) mints a
    TEST-PREFIXED graph name (test_<id>_tortoise) that the URI redirect
    honors VERBATIM — a fixed id would share ONE server graph across the
    whole session and pollute every later test's walk."""
    import uuid
    db_path = str(tmp_path / "lifecycle.db")
    org_id = f"test-team-{uuid.uuid4().hex[:10]}"

    from types import SimpleNamespace
    ctx = SimpleNamespace(tc=None, org_id=org_id, db_path=db_path)
    # #2127 wave 2: shared helper — patch __init__ → temp DB, #1950
    # TORTOISE_DB_PATH pin, close-then-clear at enter; pop-env → restore
    # __init__ → deterministic anchor close → clear overrides at exit.
    # Supersedes the inline _patched/clear/restore trio: the helper's pin
    # makes the embedded-lane _make_sdk fallback pass THIS fixture's store
    # (the old comment's "/data/tortoise.db positional split" hazard) and
    # the forced drop keeps every construction on it.
    with patched_tortoise_sdk(db_path):
        from tortoise.hosted_api import get_current_org
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": org_id, "tier": "free", "key_id": "k1",
            "legacy_full_access": True,
            "max_users": 1, "max_graphs": 1, "max_teams": 1,
            "max_points": 10000,
            "max_sessions": None,
        }
        _INDEX_JOBS.clear()
        with TestClient(app) as tc:
            ctx.tc = tc
            yield ctx
            # Drain background index jobs spawned during THIS test's requests
            # while __init__ is still bound to THIS fixture's store — a stale
            # task surviving portal teardown would otherwise rebind to the
            # next test's patched __init__ and pollute its graph
            # (single-flight + module-global _INDEX_JOBS make the tasks hard
            # to cancel by id).
            _drain_jobs(tc)


@pytest.fixture
def provisioned(client, tmp_path):
    """A provisioned team (Team node + encrypted GitHub token + org)."""
    _provision(client.db_path, org_id=client.org_id)
    return client


@pytest.fixture
def mock_github(monkeypatch):
    """Route the real GitHubIndexer's fetch layer through the mock REST."""
    transport = MockGitHubTransport(
        issues=[gh_issue(1), gh_issue(2)],
        repos=["acme/repo1", "acme/repo2"])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=transport)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    return transport


def _load_dedup_script():
    """Load graph-scripts/1714_dedup_observation.py (hyphen-free, dotted
    path cannot import a leading-digit module name)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dedup_observation",
        os.path.join(_GRAPH_SCRIPTS, "1714_dedup_observation.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wait_for(
    predicate,
    timeout_s: float = 8.0,
    what: str = "the background index run was invoked",
):
    """Wait for a fire-and-forget side effect (background _run_indexing task
    on the TestClient's portal loop).

    #5049 rule 1: a deadline expiry here is a machine-load event, so it is
    INCONCLUSIVE naming the budget — never the `AssertionError` this used to
    raise (the same wall-clock-FAIL class as `_poll_until`, #8579 review P2-2).

    #8579 review P2 (fail-open): pass the **event** ("the run was invoked"),
    not an exact-equality predicate over the captured payload. An exact
    predicate is false forever on a wrong forward too, so its expiry would
    report a regression as a machine-load SKIP. The event is the only part
    that legitimately depends on load; callers assert the captured payload
    deterministically AFTER it returns.

    #8590 review P3: the INCONCLUSIVE reason must name THAT event. It used to
    say "a background index job reached its terminal state" — a property this
    predicate does not observe (the five callers wait on `bool(seen)`, the run
    being *invoked*) — which pointed the reader at the job-status/heartbeat
    path instead of the invocation path, and contradicted
    `tests/_verdict.py::inconclusive`'s own contract ("``what`` names the
    property that was not observed"). `what` stays overridable for a future
    caller with a different predicate.
    """
    require_within(predicate, timeout_s=timeout_s, what=what)


def _pump_until_walk_entered(client, job_id: str, beats: list,
                            *, timeout_s: float = 10.0) -> None:
    """Pump the poll GET until the walk has been entered (an `on_progress`
    beat is captured).

    #5049 rule 1 + #8579 review P2: the loop's OWN wall-clock budget is a
    machine-load clock, so its expiry is INCONCLUSIVE naming that budget —
    never the rule-1 `assert beats` FAIL this used to raise. The request is
    converted through `require_answered`, so a bound refusal is a non-answer;
    an ANSWERED error status stays a FAIL (asserted here, #8590 review P2).
    """

    def _entered() -> bool:
        if beats:
            return True
        # #5049: a bound refusal on the pump GET is a non-answer, not a FAIL.
        rp = require_answered(
            client.get(f"/v1/index/github/{job_id}"),
            what=f"GET /v1/index/github/{job_id} answered (pump)",
        )
        # #8590 review P2 (fail-open): `require_answered` returns every
        # non-refusal unchanged, so an ANSWERED error (404/403) must be
        # asserted here — mirroring `_poll_until._reached` — or it misses the
        # window forever and the budget exits as an INCONCLUSIVE SKIP instead
        # of the FAIL the pre-fix loop produced.
        assert rp.status_code == 200, rp.text
        return bool(beats)

    require_within(
        _entered, timeout_s=timeout_s,
        what="the index walk was entered with an on_progress beat",
    )


def _pump_until_midwalk(client, job_id: str, mid: dict,
                       *, timeout_s: float = 10.0) -> None:
    """Pump `GET /v1/index/github/<id>` until the walk's mid-progress window
    is observed, recording `repos_processed`/`progress`/`repos_total` into
    `mid`.

    #5049 rule 1 + #8579 review P2: the pump-and-observe loop carries its own
    wall-clock budget in addition to the request's transport bound. Its expiry
    is a machine-load event — INCONCLUSIVE naming the budget — never the
    rule-1 `assert mid["seen"]` FAIL this used to raise. The request itself is
    converted through `require_answered`, and an ANSWERED error status is
    asserted, so a genuine 404/403 stays a FAIL (#8590 review P2).
    """

    def _observe() -> bool:
        rp = require_answered(
            client.get(f"/v1/index/github/{job_id}"),
            what=f"GET /v1/index/github/{job_id} answered",
        )
        # #8590 review P2: an ANSWERED error (404/403) is not a non-answer —
        # assert it here (as `_poll_until._reached` does) so it stays a FAIL
        # instead of a load-event SKIP.
        assert rp.status_code == 200, rp.text
        body = rp.json()
        if (body.get("repos_processed", 0) >= 1
                and 0 < body.get("progress", 0) < 100):
            mid["seen"] = True
            mid["repos_processed"] = body["repos_processed"]
            mid["progress"] = body["progress"]
            mid["repos_total"] = body.get("repos_total", 0)
            return True
        return False

    require_within(
        _observe, timeout_s=timeout_s,
        what="mid-walk live progress on GET /v1/index/github/<id>",
        diagnosis="_job() must write repos_processed/progress after each repo",
    )


def _drain_jobs(client, timeout_s: float = 5.0):
    """Wait for this fixture's background index jobs to reach a terminal
    state (or time out) while its SDK-construction patch is still live.

    TestClient's portal only runs background tasks while it is servicing a
    request — the drain pumps the loop with cheap requests so spawned
    _run_indexing tasks settle on THIS fixture's store instead of leaking
    into the next test's patched __init__."""
    deadline = time.time() + timeout_s
    while _INDEX_JOBS and time.time() < deadline:
        if all(j.get("status") in ("completed", "failed")
               for j in _INDEX_JOBS.values()):
            return
        with suppress(Exception):
            client.get("/v1/onboarding/state")  # pump the portal loop
        time.sleep(0.02)


#: The index job's terminal statuses: `hosted_api` mints "started" and writes
#: exactly "completed"/"failed" (`_job(status=...)`). A body in this set either
#: MATCHES the expected status or is a determinate contradiction — never the
#: indeterminate "still working" state a load-driven deadline is allowed to
#: skip on.
_TERMINAL_JOB_STATUSES = ("completed", "failed")


def _poll_until(client, job_id: str, status: str, timeout_s: float | None = None):
    """Poll the index job; INCONCLUSIVE (never FAIL) on either deadline.

    TWO clocks meet here, and they are deliberately reconciled (#8579 P2-2):

    * the PRODUCT's transport wait bound (10 s) bounds ONE request — a request
      that outlives it is answered with the legible refusal, which
      `require_answered` turns INCONCLUSIVE. The pre-fix 8 s default was
      SHORTER than that bound, so under load this poll's own budget fired
      before the refusal could even be read;
    * this poll's deadline bounds the WHOLE poll loop, so it must outlast a
      single request's bound or it decides the verdict first. The default is the
      product bound plus a 2 s margin, and a caller may still pass an explicit
      budget for a deterministic test.
    """
    budget = transport_wait_bound_s() + 2.0 if timeout_s is None else timeout_s
    observed: dict = {}

    def _reached() -> bool:
        r = require_answered(
            client.get(f"/v1/index/github/{job_id}"),
            what=f"GET /v1/index/github/{job_id} answered",
        )
        assert r.status_code == 200, r.text
        observed["body"] = r.json()
        observed_status = observed["body"]["status"]
        if observed_status != status and observed_status in _TERMINAL_JOB_STATUSES:
            # #8590 review P2 (fail-open): an ANSWERED terminal status that
            # CONTRADICTS the expected one is a determinate product
            # observation — the run already ended in a different terminal
            # state — not a machine-load event. Raising INSIDE the predicate
            # keeps the FAIL observation-triggered, never deadline-triggered;
            # a non-terminal body ("started") at the budget still expires
            # through `require_within` as INCONCLUSIVE.
            raise AssertionError(
                f"job {job_id} reached {observed_status!r}, expected "
                f"{status!r}: {observed['body']}"
            )
        return observed_status == status

    # #5049 rule 1: the poll's own deadline is a wall-clock budget, so its
    # expiry is INCONCLUSIVE and names the budget — never the AssertionError
    # (`job <id> not <status>`) this used to raise.
    require_within(
        _reached, timeout_s=budget,
        what=f"job {job_id} reached status {status!r}",
    )
    return observed["body"]


def _post_index(client, path: str = "/v1/index/github", **kwargs):
    """POST an index route through the #5049 verdict seam.

    `POST /v1/index/github` and its `/re-poll` sibling are not exempt from the
    transport wait bound, so a request that outlives it is answered with the
    legible refusal — which carries no `job_id`, so dereferencing it is the
    `KeyError: 'job_id'` #8122 measured as a red (four times). Routing EVERY
    post in this file through one seam — not the two leaves an earlier pass
    converted — leaves no unwrapped poster to reintroduce that error
    (#8579 review P3-2/P3-3).
    """
    return require_answered(
        client.post(path, **kwargs),
        what=f"POST {path} answered",
    )


def test_poll_until_is_inconclusive_on_a_bound_refusal():
    """#5049: the poll's own 504 refusal is a non-answer — never a FAIL.

    Deterministic (no load, no server): the client answers with the product's
    own refusal. Removing `require_answered` from `_poll_until` makes this go
    RED with `assert 504 == 200`, which is the must-fail leg.
    """

    class _Refusing:
        status_code = 504

        def __init__(self) -> None:
            from tortoise import mcp_auth

            self._message = mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE
            self.text = json.dumps({"detail": self._message})

        def get(self, *_args, **_kwargs):
            return self

        def json(self):
            return {"detail": self._message}

    with pytest.raises(pytest.skip.Exception) as excinfo:
        _poll_until(_Refusing(), "job-1", "completed")
    assert "INCONCLUSIVE [#5049]" in str(excinfo.value)


def test_poll_until_deadline_is_inconclusive_never_an_assertion():
    """#8579 P2-2: the poll's OWN deadline must not produce FAIL.

    Deterministic: a 200 whose body never reaches the target status, with the
    budget spent. Pre-fix this raised
    `AssertionError: job job-1 not completed: {'status': 'running'}` from the
    same helper that already converted the transport refusal; the rule-1
    budget expiry must SKIP and name the budget it spent.
    """

    class _Stuck:
        status_code = 200

        def __init__(self) -> None:
            self.text = '{"status": "running"}'

        def get(self, *_args, **_kwargs):
            return self

        def json(self):
            return {"status": "running"}

    with pytest.raises(pytest.skip.Exception) as excinfo:
        _poll_until(_Stuck(), "job-1", "completed", timeout_s=0.0)
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "0s" in message, message
    assert "job job-1 reached status 'completed'" in message, message


def test_poll_until_terminal_mismatch_is_a_fail_not_a_skip():
    """#8590 review P2 must-fail leg: a CONTRADICTORY terminal status is a
    determinate observation, never a machine-load SKIP.

    Deterministic: an answered 200 whose body has already reached the OTHER
    terminal status, with the budget spent. Pre-fix the predicate was false
    forever and the budget exited as `INCONCLUSIVE [#5049]`, dropping the run's
    own failure text; an answered terminal contradiction must FAIL.
    """

    class _Failed:
        status_code = 200
        text = '{"status": "failed", "error": "boom"}'

        def get(self, *_args, **_kwargs):
            return self

        def json(self):
            return {"status": "failed", "error": "boom"}

    try:
        _poll_until(_Failed(), "job-1", "completed", timeout_s=0.0)
    except AssertionError as exc:
        message = str(exc)
    except pytest.skip.Exception as exc:
        raise AssertionError(
            "a contradictory terminal status ('failed' while awaiting "
            f"'completed') was swallowed as an INCONCLUSIVE skip: {exc}"
        ) from exc
    else:
        raise AssertionError(
            "the poll raised no verdict for a contradictory terminal status")
    assert "reached 'failed', expected 'completed'" in message, message
    assert "boom" in message, message


def test_wait_for_names_the_event_it_waits_for():
    """#8590 review P3: the INCONCLUSIVE reason names the event the predicate
    observes — the run was INVOKED — not its terminal state.

    Pre-fix the reason said "a background index job reached its terminal
    state", a property `lambda: bool(seen)` never observes, so this fails on
    the pre-fix string.
    """
    with pytest.raises(pytest.skip.Exception) as excinfo:
        _wait_for(lambda: False, timeout_s=0.0)
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message, message
    assert "the background index run was invoked" in message, message
    assert "terminal state" not in message, message


def test_post_index_is_inconclusive_on_a_bound_refusal():
    """#8579 P3-2/P3-3: the file's ONE post seam converts the refusal.

    Every `/v1/index/github` POST in this file goes through `_post_index`, so
    pinning the seam pins all of them: a bound refusal is a non-answer — never
    the `KeyError: 'job_id'` a bare `.json()["job_id"]` would raise.
    """

    class _Refusing:
        status_code = 504

        def __init__(self) -> None:
            from tortoise import mcp_auth

            self._message = mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE
            self.text = json.dumps({"detail": self._message})

        def post(self, *_args, **_kwargs):
            return self

        def json(self):
            return {"detail": self._message}

    with pytest.raises(pytest.skip.Exception) as excinfo:
        _post_index(_Refusing(), json={"org": "acme"})
    assert "INCONCLUSIVE [#5049]" in str(excinfo.value)


# ── State-key registration (cycle-3 P1-2) ─────────────────────────

def test_state_keys_registered():
    """github_index_cursor + github_legacy_backfill_done must be registered
    in BOTH live default-state dicts + _ALLOWED_STATE_KEYS + the PATCH model
    — an unregistered key is silently dropped by _update_onboarding_state."""
    import tortoise.hosted_api as ha
    for key in ("github_index_cursor", "github_legacy_backfill_done",
                "github_indexed_at", "github_docs_indexed_at"):
        assert key in _ONBOARDING_DEFAULT_STATE
        assert key in _ALLOWED_STATE_KEYS
        assert key in ha.DEFAULT_ONBOARDING_STATE
    # PATCH model fields (the live OnboardingStatePatchRequest)
    assert "github_index_cursor" in ha.OnboardingStatePatchRequest.model_fields
    assert "github_legacy_backfill_done" in ha.OnboardingStatePatchRequest.model_fields
    assert "github_indexed_at" in ha.OnboardingStatePatchRequest.model_fields
    assert "github_docs_indexed_at" in ha.OnboardingStatePatchRequest.model_fields


def test_server_owned_state_keys_patch_refused_with_reason(client, provisioned):
    """The cursor is SERVER-OWNED — #3552 moved it onto the PATCH refusal
    list, so a client PATCH is refused 403 with a reason rather than being
    silently dropped by the allowlist filter.

    This test was written to catch a key that the allowlist filter drops in
    silence; for a SERVER-OWNED key the equivalent failure is a silent drop of
    the refusal, which the ``detail`` assertion below pins. The client-writable
    round-trip for REGISTERED keys is
    ``test_onboarding_endpoints.test_state_keys_registered_parametrized``, and
    the persisted-value half (the refusal must not mutate the row) is
    ``test_onboarding_endpoints.test_operational_keys_not_client_writable``.
    """
    r = client.tc.patch("/v1/onboarding/state",
                        json={"github_index_cursor": {"acme/repo1": {"updated_at": "x", "number": 1}}})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {
        "message": "server_owned_key", "keys": ["github_index_cursor"]}, r.text


# ── Single-flight (T2-P2 + cycle-3 P1-3) ──────────────────────────

def test_in_flight_single_flight_reuses(provisioned, mock_github, monkeypatch):
    """A `started` job for the team is REUSED — its job_id is returned
    (guard-check FIRST), and the reused POST spawns NO second run — two
    concurrent POSTs produce EXACTLY ONE _run_indexing execution (P1-1:
    single-flight dedupes the RUN, not just the entry)."""
    _org_id = provisioned.org_id
    calls = []
    import asyncio as _asyncio

    import tortoise.hosted_api as ha
    orig_run = ha._run_indexing

    async def _counting(*args, **kw):
        calls.append(args)
        # keep the run IN-FLIGHT across both POSTs (the mock walk is
        # millisecond-fast — without the hold the portal settles it before
        # the second POST and the entry turns terminal)
        await _asyncio.sleep(0.2)
        await orig_run(*args, **kw)

    monkeypatch.setattr(ha, "_run_indexing", _counting)
    r1 = _post_index(provisioned.tc, "/v1/index/github", json={"org": "acme"})
    assert r1.status_code == 200
    job_id = r1.json()["job_id"]
    assert _INDEX_JOBS[job_id]["status"] == "started"
    r2 = _post_index(provisioned.tc, "/v1/index/github", json={"org": "acme"})
    assert r2.status_code == 200
    assert r2.json()["job_id"] == job_id, "in-flight job must be reused"
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["points_created"] == 0  # object-only (#1844)
    assert body["events_minted"] == 2   # 2 issues × 1 created event
    assert len(calls) == 1, "concurrent POSTs must spawn exactly ONE run"


def test_stuck_started_evicted(provisioned, mock_github):
    """NEGATIVE CONTROL (#3539 §D): a `started` entry whose HEARTBEAT has
    gone stale is presumed-dead and evicted — a stopped heartbeat MUST expire
    the claim, so a hung/cancelled run never bricks the team.

    The test is on the HEARTBEAT, never on entry age (see
    ``test_long_live_run_not_duplicated_by_ttl_expiry`` for the other half of
    the rule). Both stamps are backdated because a dead run stops beating."""
    from tortoise.hosted_api import _INDEX_JOB_STALE_S
    org_id = provisioned.org_id
    stale, _ = _start_index_job(org_id)
    dead_at = time.time() - _INDEX_JOB_STALE_S - 10
    _INDEX_JOBS[stale]["started_at"] = dead_at
    _INDEX_JOBS[stale]["heartbeat_at"] = dead_at
    r = _post_index(provisioned.tc, "/v1/index/github", json={"org": "acme"})
    assert r.status_code == 200
    new_job = r.json()["job_id"]
    assert new_job != stale
    assert stale not in _INDEX_JOBS, (
        "a stale-heartbeat entry must be evicted — a stopped heartbeat MUST "
        "expire the claim")
    _poll_until(provisioned.tc, new_job, "completed")  # settle the spawned task


def test_long_live_run_not_duplicated_by_ttl_expiry(provisioned, mock_github,
                                                    monkeypatch):
    """#3539 §D: eviction is a LIVENESS check, NOT entry age.

    A cursor-correct walk of a 1,700-issue org can outlive any fixed TTL. A
    run that is merely LONG keeps beating, so a re-poll must REUSE its claim —
    the age-based rule evicted it and spawned a SECOND concurrent walk
    (duplicate statement ids, version inflation, job-status races)."""
    import asyncio as _asyncio

    import tortoise.hosted_api as ha
    from tortoise.hosted_api import _INDEX_JOB_STALE_S
    calls = []
    orig_run = ha._run_indexing

    async def _counting(*args, **kw):
        calls.append(args)
        # keep the run IN-FLIGHT across both POSTs (the mock walk is
        # millisecond-fast — without the hold the portal settles it before
        # the second POST and the entry turns terminal)
        await _asyncio.sleep(0.2)
        await orig_run(*args, **kw)

    monkeypatch.setattr(ha, "_run_indexing", _counting)
    r1 = _post_index(provisioned.tc, "/v1/index/github", json={"org": "acme"})
    assert r1.status_code == 200
    job_id = r1.json()["job_id"]
    assert _INDEX_JOBS[job_id]["status"] == "started"
    # the run has been going for FOUR stale-windows — far past the old 30-min
    # TTL — and it is STILL BEATING (the heartbeat is kept fresh by progress).
    _INDEX_JOBS[job_id]["started_at"] = time.time() - _INDEX_JOB_STALE_S * 4
    _INDEX_JOBS[job_id]["heartbeat_at"] = time.time()
    r2 = _post_index(provisioned.tc, "/v1/index/github", json={"org": "acme"})
    assert r2.status_code == 200
    assert r2.json()["job_id"] == job_id, (
        "a LONG but LIVE run must keep its claim — entry age is not a "
        "liveness test")
    assert job_id in _INDEX_JOBS, "the live claim must not be evicted"
    _poll_until(provisioned.tc, job_id, "completed")  # settle the run
    assert len(calls) == 1, (
        "a long LIVE run must not be duplicated by its claim expiring")


def test_progress_beat_stamps_heartbeat(provisioned, mock_github, monkeypatch):
    """POSITIVE CONTROL (#3539 §D): the run must ACTUALLY beat while it walks.

    Without this, heartbeat-only eviction would evict a genuinely live long
    run — the exact failure mode it exists to prevent: a mint-stamp-only
    heartbeat goes stale while the run is still working. Asserts both halves
    of the wiring: `_run_indexing` HANDS the walk its beat, and the beat
    stamps the job's heartbeat while the run is still `started`."""
    import asyncio as _asyncio

    from tortoise.indexer.github_indexer import GitHubIndexer
    beats: list = []
    orig_index_repo = GitHubIndexer.index_repo

    async def _held_index_repo(self, sdk, repo, **kw):
        cb = kw.get("on_progress")
        assert cb is not None, (
            "_run_indexing must hand the walk its progress beat")
        beats.append(cb)
        # loop-friendly hold: the portal advances background tasks only while
        # servicing a request, so the run stays `started` across the poll GETs
        # below (a threading barrier would deadlock the portal loop).
        await _asyncio.sleep(0.5)
        return await orig_index_repo(self, sdk, repo, **kw)

    monkeypatch.setattr(GitHubIndexer, "index_repo", _held_index_repo)
    r = _post_index(provisioned.tc, "/v1/index/github", json={"org": "acme"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    # pump the portal (the poll GET both advances and observes the run) until
    # the walk has been entered; the loop's own deadline is INCONCLUSIVE
    # (never the rule-1 `assert beats` FAIL) — #8579 review P2.
    _pump_until_walk_entered(provisioned.tc, job_id, beats)
    assert _INDEX_JOBS[job_id]["status"] == "started", (
        "the hold must keep the run in flight for a mid-walk assertion")

    # the mint stamp is NOT liveness — zero it and prove the run's beat raises
    # it again, mid-walk
    _INDEX_JOBS[job_id]["heartbeat_at"] = 0.0
    beats[0]()
    assert _INDEX_JOBS[job_id]["heartbeat_at"] > 0.0, (
        "the run's progress beat must stamp the liveness heartbeat")

    _poll_until(provisioned.tc, job_id, "completed")  # settle the run


def test_terminal_jobs_evicted_on_enqueue(provisioned, mock_github):
    """Terminal (completed) entries for the team are cleared on the next
    enqueue (T1-P14)."""
    org_id = provisioned.org_id
    job_id, _ = _start_index_job(org_id)
    _INDEX_JOBS[job_id]["status"] = "completed"
    r = _post_index(provisioned.tc, "/v1/index/github", json={"org": "acme"})
    new_job = r.json()["job_id"]
    assert new_job != job_id
    assert job_id not in _INDEX_JOBS
    _poll_until(provisioned.tc, new_job, "completed")  # settle the spawned task


# ── ONE-repo bounded first run (P2-4) ─────────────────────────────

def test_first_run_single_repo(provisioned, mock_github):
    """First-run scope = ONE repo regardless of org size; job status reports
    repos_total vs repos_processed (the honest 'index more' affordance)."""
    r = _post_index(provisioned.tc, "/v1/index/github", json={"org": "acme"})
    job_id = r.json()["job_id"]
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["repos_total"] == 1
    assert body["repos_processed"] == 1
    # only the first resolved repo was walked (acme/repo1)
    assert body["points_created"] == 0  # object-only (#1844): no statement points
    assert body["events_minted"] == 2   # 2 issues × 1 created event


def test_second_run_full_org_with_cursors(provisioned, mock_github):
    """After the first run (github_indexed=True), a re-poll walks ALL repos
    with their persisted cursors."""
    r = _post_index(provisioned.tc, "/v1/index/github", json={"org": "acme"})
    _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    r = _post_index(provisioned.tc, "/v1/index/github/re-poll")
    job_id = r.json()["job_id"]
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["repos_total"] == 2
    assert body["repos_processed"] == 2
    # the diff re-run produced 0 new points (cursor-stopped walk)
    assert body["points_created"] == 0
    assert body["events_minted"] == 0


def test_live_progress_written_during_walk(provisioned, mock_github, monkeypatch):
    """#1894: the job poll surfaces LIVE per-repo progress mid-walk (not
    just 0 → 100 at terminal) — the dashboard's ETA derives from REAL
    signal. Pump-aware: the portal runs background tasks only while
    servicing a request, so the walk is advanced by interleaved GETs and
    the hold is a loop-friendly `asyncio.sleep` (never a threading barrier,
    which would block the portal loop and deadlock)."""
    import asyncio as _asyncio

    # Defeat the first-run ONE-repo bound (mock resolves BOTH repos) so the
    # org-wide re-poll walks both.
    r0 = provisioned.tc.patch("/v1/onboarding/state",
                              json={"github_indexed": True})
    assert r0.status_code == 200, r0.text

    orig_index_repo = GitHubIndexer.index_repo

    async def _slow_repo2(self, sdk, repo, **kw):
        # _run_indexing calls index_repo(team_sdk, repo_name, cursor=...,
        # on_progress=...) — pass through, do NOT rebuild the kwargs (an
        # explicit cap/quota_check=None would break the `len(items) >= cap`
        # default); delegate to inherit the contract.
        if repo.endswith("/repo2"):
            await _asyncio.sleep(1.0)  # loop-friendly hold on the 2nd repo
        return await orig_index_repo(self, sdk, repo, **kw)

    monkeypatch.setattr(GitHubIndexer, "index_repo", _slow_repo2)
    r = _post_index(provisioned.tc, "/v1/index/github/re-poll")
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    # Interleave request pumps with polls of the LIVE JOB DICT VIA THE PUBLIC
    # POLL ENDPOINT (the same GET the dashboard's bounded poll consumes —
    # `{job_id, **job}`) — the portal advances _run_indexing WHILE servicing
    # a request, so the poll GET both pumps and observes (mirrors _drain_jobs).
    mid = {"seen": False, "repos_processed": 0, "progress": 0,
           "repos_total": 0}
    # #5049: this pump-and-observe loop is a separate poll path from
    # `_poll_until`; its GET is converted through `require_answered` AND its
    # own wall-clock deadline through `require_within` (#8579 review P2), so
    # neither a bound refusal nor a load event exits into a rule-1 FAIL.
    _pump_until_midwalk(provisioned.tc, job_id, mid)

    assert mid["repos_processed"] == 1
    assert mid["repos_total"] == 2
    assert 0 < mid["progress"] < 100  # 1 of 2 repos → 50
    assert mid["progress"] == 50

    # the run still settles to a normal terminal state with full totals
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["repos_processed"] == 2
    assert body["repos_total"] == 2


def test_midwalk_fetch_error_stamps_indexed_at(provisioned, mock_github,
                                               monkeypatch):
    """#1894 parity: a MID-WALK GitHubFetchError (repo2 fails AFTER repo1
    processed) is "made progress" — the finally flips github_indexed AND
    stamps github_indexed_at (the guard is repos_processed > 0, not "no
    errors"). #1844 made github ingest object-only (no points quota), so the
    mid-walk fetch error is now the ONLY github partial-progress path — the
    docs side pins its quota-partial equivalent in test_index_docs_api."""
    from tortoise.indexer.github_indexer import GitHubFetchError

    # Defeat the first-run ONE-repo bound (mock resolves BOTH repos) so the
    # org-wide re-poll walks both.
    r0 = provisioned.tc.patch("/v1/onboarding/state",
                              json={"github_indexed": True})
    assert r0.status_code == 200, r0.text

    orig_index_repo = GitHubIndexer.index_repo

    async def _fail_repo2(self, sdk, repo, **kw):
        if repo.endswith("/repo2"):
            raise GitHubFetchError("injected 429 on repo2 (test)")
        return await orig_index_repo(self, sdk, repo, **kw)

    monkeypatch.setattr(GitHubIndexer, "index_repo", _fail_repo2)
    r = _post_index(provisioned.tc, "/v1/index/github/re-poll")
    assert r.status_code == 200, r.text
    body = _poll_until(provisioned.tc, r.json()["job_id"], "failed")
    assert body["status"] == "failed"
    assert "injected 429" in body["error"]
    assert body["repos_processed"] == 1  # repo1 walked, repo2 failed

    state = provisioned.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_indexed"] is True, \
        ">=1 repo processed = made progress — the bool flips on partial runs"
    # #1894 parity: the timestamp stamps in the SAME branch as the bool — a
    # resumable partial run shows a truthful "Last indexed".
    assert state["github_indexed_at"], \
        "mid-walk-error partial runs stamp github_indexed_at (parity with " \
        "the bool — resumable-cursor semantics)"
    datetime.fromisoformat(state["github_indexed_at"])  # ISO, strict parse


# ── Auto-index after connect (Task 5) ─────────────────────────────

def test_auto_index_after_connect(provisioned, mock_github, monkeypatch):
    """Connect fires the first index (quota-gated, ONE repo). The Team node
    must pre-exist for the callback's token MATCH (provisioned fixture)."""
    from tortoise.hosted_api import _GITHUB_STATES
    _GITHUB_STATES["test-state"] = {"org_id": provisioned.org_id, "org": "acme",
                                    "created_at": time.time()}
    async def _fake_exchange(code):
        return "oauth-token"
    monkeypatch.setattr("tortoise.hosted_api._exchange_github_token", _fake_exchange)
    r = provisioned.tc.get("/v1/onboarding/github/callback?code=abc&state=test-state",
                           follow_redirects=False)
    assert r.status_code == 302
    # an index job was enqueued for the team
    jobs = [j for j in _INDEX_JOBS.values() if j.get("org_id") == provisioned.org_id]
    assert jobs, "auto-index job must be enqueued after connect"
    job_id = next(jid for jid, j in _INDEX_JOBS.items()
                  if j.get("org_id") == provisioned.org_id)
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["repos_total"] == 1


def test_re_poll_requires_connection(client):
    r = _post_index(client.tc, "/v1/index/github/re-poll")
    assert r.status_code == 400
    assert "GitHub not connected" in r.json()["detail"]


def test_re_poll_scoped_repo(provisioned, monkeypatch):
    """#1845: re-poll accepts an optional {repos} scope and forwards it to
    the run — org is still read server-side from the stored credentials (the
    client never supplies it). The legacy single {repo} field is equivalent
    to a one-item list."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, org_id, org, repos):
        seen.append((org, repos))
        ha._INDEX_JOBS[job_id]["status"] = "completed"  # settle the drain

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    r = _post_index(provisioned.tc, "/v1/index/github/re-poll", json={"repo": "repo2"})
    assert r.status_code == 200
    assert r.json()["status"] == "started"
    # #8579 review P2: wait for the EVENT (the run was invoked), then assert
    # the forwarded scope deterministically — an exact-equality predicate makes
    # a wrong scope a machine-load SKIP instead of the FAIL it is.
    _wait_for(lambda: bool(seen))
    assert seen == [("acme", ["repo2"])]


def test_re_poll_multi_repo_scope(provisioned, monkeypatch):
    """#1845: re-poll with a {repos} LIST forwards exactly those repos (org
    still read server-side)."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, org_id, org, repos):
        seen.append((org, repos))
        ha._INDEX_JOBS[job_id]["status"] = "completed"

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    r = _post_index(provisioned.tc, "/v1/index/github/re-poll",
                            json={"repos": ["repo1", "repo2"]})
    assert r.status_code == 200
    _wait_for(lambda: bool(seen))
    assert seen == [("acme", ["repo1", "repo2"])]


def test_re_poll_empty_repos_is_full_org(provisioned, monkeypatch):
    """#1845: an EMPTY repos list is the full-org diff (repos=None), not a
    scoped walk of nothing."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, org_id, org, repos):
        seen.append((org, repos))
        ha._INDEX_JOBS[job_id]["status"] = "completed"

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    r = _post_index(provisioned.tc, "/v1/index/github/re-poll", json={"repos": []})
    assert r.status_code == 200
    _wait_for(lambda: bool(seen))
    assert seen == [("acme", None)]


def test_re_poll_invalid_repo_400(provisioned, monkeypatch):
    """#1845 (review P1): a malicious repo scope must be rejected (400) —
    the org boundary is server-side and the repo must stay a SHORT name.
    Dot-segment traversal ("../victimorg/x") and query/whitespace junk are
    not valid short repo names and must never reach the GitHub URL path."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, org_id, org, repos):
        seen.append((org, repos))

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    for bad in ("../victimorg/secret", "a/b?q=1", "repo name", "../.."):
        r = _post_index(provisioned.tc, "/v1/index/github/re-poll", json={"repo": bad})
        assert r.status_code == 400, f"repo={bad!r} should 400"
        assert "Invalid repo name" in r.json()["detail"]
        r2 = _post_index(provisioned.tc, "/v1/index/github/re-poll",
                                 json={"repos": [bad]})
        assert r2.status_code == 400, f"repos=[{bad!r}] should 400"
    assert seen == [], "no job must be spawned for an invalid repo"


def test_re_poll_whitespace_repo_is_full_org(provisioned, monkeypatch):
    """#1845 (review P2): a whitespace-only repo scope collapses to None
    (full-org diff), never a scoped walk of a junk name."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, org_id, org, repos):
        seen.append((org, repos))
        ha._INDEX_JOBS[job_id]["status"] = "completed"

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    r = _post_index(provisioned.tc, "/v1/index/github/re-poll", json={"repo": "   "})
    assert r.status_code == 200
    _wait_for(lambda: bool(seen))
    assert seen == [("acme", None)]


def test_re_poll_no_repo_is_full_org(provisioned, monkeypatch):
    """#1845: re-poll WITHOUT a body keeps the full-org diff (repos=None) —
    backward compatible with the pre-selector flow."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, org_id, org, repos):
        seen.append((org, repos))
        ha._INDEX_JOBS[job_id]["status"] = "completed"

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    r = _post_index(provisioned.tc, "/v1/index/github/re-poll")
    assert r.status_code == 200
    _wait_for(lambda: bool(seen))
    assert seen == [("acme", None)]


def test_pump_until_midwalk_deadline_is_inconclusive_never_an_assertion():
    """#8579 review P2: the pump-and-observe loop's OWN budget must SKIP, not
    FAIL. Deterministic: a 200 whose body never reaches the mid-walk window,
    with the budget already spent. Pre-fix the loop exited its deadline into
    `assert mid["seen"]` — this pins the conversion through `require_within`.
    """

    class _AnsweredButNeverMidwalk:
        status_code = 200
        text = '{"repos_processed": 0, "progress": 0}'

        def get(self, *_args, **_kwargs):
            return self

        def json(self):
            return {"repos_processed": 0, "progress": 0}

    mid: dict = {"seen": False}
    with pytest.raises(pytest.skip.Exception) as excinfo:
        _pump_until_midwalk(_AnsweredButNeverMidwalk(), "job-1", mid,
                            timeout_s=0.0)
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "0s" in message, message
    assert "mid-walk live progress" in message, message


def test_pump_until_midwalk_answered_error_is_a_fail_not_a_skip():
    """#8590 review P2 must-fail leg: a genuine ANSWERED error on the pump GET
    reaches the caller's own assertion — never a machine-load SKIP.

    Pre-fix (no status assertion after `require_answered`) the 404 missed the
    mid-walk window on every iteration and the budget exited as
    `INCONCLUSIVE [#5049]`; a real regression was thus reported as a skip. The
    conversion must leave it a FAIL.
    """

    class _NotFound:
        status_code = 404
        text = '{"detail": "job not found"}'

        def get(self, *_args, **_kwargs):
            return self

        def json(self):
            return {"detail": "job not found"}

    mid: dict = {"seen": False}
    try:
        _pump_until_midwalk(_NotFound(), "job-1", mid, timeout_s=0.0)
    except AssertionError:
        return  # expected: the answered 404 reaches the caller's assertion
    except pytest.skip.Exception as exc:
        raise AssertionError(
            "an answered 404 was swallowed as an INCONCLUSIVE skip instead of "
            f"failing: {exc}"
        ) from exc
    raise AssertionError(
        "the pump helper returned without asserting the answered 404 status")


def test_pump_until_walk_entered_answered_error_is_a_fail_not_a_skip():
    """#8590 review P2 must-fail leg for `_pump_until_walk_entered`: an
    answered 404 on the pump GET is a FAIL, not a load-event SKIP."""

    class _NotFound:
        status_code = 404
        text = '{"detail": "job not found"}'

        def get(self, *_args, **_kwargs):
            return self

        def json(self):
            return {"detail": "job not found"}

    beats: list = []
    try:
        _pump_until_walk_entered(_NotFound(), "job-1", beats, timeout_s=0.0)
    except AssertionError:
        return  # expected: the answered 404 reaches the caller's assertion
    except pytest.skip.Exception as exc:
        raise AssertionError(
            "an answered 404 was swallowed as an INCONCLUSIVE skip instead of "
            f"failing: {exc}"
        ) from exc
    raise AssertionError(
        "the pump helper returned without asserting the answered 404 status")


# ── Cursor + backfill marker persistence (Tasks 2/3) ──────────────

def test_cursor_and_backfill_marker_persisted(provisioned, mock_github):
    r = _post_index(provisioned.tc, "/v1/index/github", json={"org": "acme"})
    _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    state = provisioned.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_indexed"] is True
    assert state["github_legacy_backfill_done"] is True
    cursor = state["github_index_cursor"]
    assert cursor and "acme/repo1" in cursor
    assert cursor["acme/repo1"]["updated_at"]
    assert "number" in cursor["acme/repo1"]
    # #1894: completion stamps the last-indexed timestamp (parity with the
    # bool — a progress-y run flips BOTH). Strict ISO parse (fromisoformat
    # rejects malformed strings, not just epoch floats).
    assert state["github_indexed_at"], "github_indexed_at must be stamped on completion"
    datetime.fromisoformat(state["github_indexed_at"])  # ISO, not an epoch float


def test_repoll_drains_and_clears_truncated_persisted(client, tmp_path,
                                                      monkeypatch):
    """#1895: the hosted_api persistence round-trip — an exact cap-multiple
    backlog (600 items @ one second, page_size 100) truncates at 500 on the
    first POST; the next re-poll (DRAIN) drains the remaining 100 and
    persists a CLEAN cursor (truncated gone); the following re-poll is DIFF
    (its first issues request carries `since`) and stays exact-once.
    Pre-fix: the 0-processed re-poll left `last is None` → the persisted
    cursor kept truncated forever (the production freeze)."""
    _provision(client.db_path, org_id=client.org_id)
    S = "2026-08-18T02:49:35Z"
    t = MockGitHubTransport(
        issues=[gh_issue(n, updated_at=S) for n in range(1, 601)],
        repos=["acme/repo1"], page_size=100)

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    # run 1: cap 500 → truncated cursor persisted
    r = _post_index(client.tc, "/v1/index/github", json={"org": "acme"})
    body = _poll_until(client.tc, r.json()["job_id"], "completed")
    assert body["repos_processed"] == 1
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_index_cursor"]["acme/repo1"] == {
        "updated_at": S, "number": 500, "truncated": True}
    # re-poll 1 (DRAIN): drains the remaining 100 → CLEAN persisted cursor
    r2 = _post_index(client.tc, "/v1/index/github/re-poll")
    body2 = _poll_until(client.tc, r2.json()["job_id"], "completed")
    assert body2["repos_processed"] == 1
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_index_cursor"]["acme/repo1"] == {
        "updated_at": S, "number": 600}, \
        "the drained re-poll must persist a CLEAN cursor (truncated gone)"
    # re-poll 2 (DIFF): first issues request carries `since` — no DRAIN,
    # and the run stays exact-once (0 new mints)
    n_queries_before = len(t.issue_query_params())
    r3 = _post_index(client.tc, "/v1/index/github/re-poll")
    body3 = _poll_until(client.tc, r3.json()["job_id"], "completed")
    assert body3["events_minted"] == 0, "exact-once — 0 new mints"
    new_queries = t.issue_query_params()[n_queries_before:]
    assert any("since" in p for p in new_queries), \
        "a clean persisted cursor must exit DRAIN (the next run uses since)"


# ── P2 (PR #1792): marker / 404 / quota-break honesty ────────────────

def test_backfill_marker_set_only_on_success(client, tmp_path, monkeypatch):
    """P2: github_legacy_backfill_done is set ONLY when the backfill
    SUCCEEDS — a transient failure must not skip the one-time migration
    forever (the job still completes; the backfill re-runs next time)."""
    _provision(client.db_path, org_id=client.org_id)
    t = MockGitHubTransport(issues=[gh_issue(1)])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    fail_backfill = {"fail": True}

    def _flaky_backfill(self, proj, *, sdk=None):
        if fail_backfill["fail"]:
            raise RuntimeError("backfill boom")
        return 0

    monkeypatch.setattr(GitHubIndexer, "backfill_legacy_closed",
                        _flaky_backfill)
    r = _post_index(client.tc, "/v1/index/github", json={"org": "acme"})
    _poll_until(client.tc, r.json()["job_id"], "completed")
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_legacy_backfill_done"] is False, \
        "marker must stay UNSET when the backfill raises"
    # heal the backfill → the next run re-runs it and sets the marker
    fail_backfill["fail"] = False
    r2 = _post_index(client.tc, "/v1/index/github/re-poll")
    _poll_until(client.tc, r2.json()["job_id"], "completed")
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_legacy_backfill_done"] is True


def test_resolve_repos_404_fails_job(client, tmp_path, monkeypatch):
    """P2: an org that 404s on BOTH orgs/ and users/ must FAIL the job
    with a readable error — never silently complete with 0 points +
    github_indexed=True."""
    _provision(client.db_path, org_id=client.org_id)
    t = MockGitHubTransport(issues=[], resolve_repos_404=True)

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    r = _post_index(client.tc, "/v1/index/github", json={"org": "acme"})
    job_id = r.json()["job_id"]
    body = _poll_until(client.tc, job_id, "failed")
    assert "not found" in body["error"] or "no access" in body["error"]
    assert body["status"] == "failed"
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert not state["github_indexed"], \
        "a 0-repo 404 failure must leave github_indexed UNSET — the next " \
        "run keeps the ONE-repo bounded first-run pacing (P2, PR #1792)"
    # #1894 parity: a 0-progress failure must NOT stamp the timestamp either
    # (it lives in the same repos_processed>0 branch as the bool) — a
    # regression moving the stamp outside the guard would show a fabricated
    # "Last indexed" on the dashboard.
    assert not state.get("github_indexed_at"), \
        "a 0-repo 404 failure must leave github_indexed_at UNSET (parity " \
        "with the bool — no stamp on zero progress)"


def test_issue_ingest_no_longer_consumes_points_quota(client, tmp_path,
                                                      monkeypatch):
    """#1844: issue ingest is OBJECT-ONLY — no statement Points are written,
    so the points quota gate never fires on a github index run (a 0-point-cap
    team still ingests issues fine; the old points-quota preflight would have
    FAILED this job). Pre-change: each issue consumed one statement point, so
    max_points=1 let exactly one issue through and stamped the cursor
    truncated on the quota break."""
    _provision(client.db_path, org_id=client.org_id, max_points=0)
    t = MockGitHubTransport(issues=[gh_issue(1), gh_issue(2)])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    r = _post_index(client.tc, "/v1/index/github", json={"org": "acme"})
    job_id = r.json()["job_id"]
    body = _poll_until(client.tc, job_id, "completed")
    assert body["quota_hit"] is False, \
        "object-only ingest must not trip the points quota"
    assert body["points_created"] == 0
    assert body["events_minted"] == 2, "both issues' lifecycle events minted"
    assert body["repos_processed"] == 1
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    cursor = state["github_index_cursor"]["acme/repo1"]
    assert "truncated" not in cursor, \
        "a quota-free run ends with a clean window (no DRAIN flag)"


def test_resolve_repos_failure_preserves_persisted_cursors(client, tmp_path,
                                                            monkeypatch):
    """P2: a PRE-WALK failure (resolve_repos raising) must not WIPE
    previously persisted cursors — the finally persists the loaded state back
    (a `cursors={}` blind write would silently drop the resume point).
    (Formerly the points-quota preflight failure; that gate is removed in
    #1844 — the object-only job writes zero points — so the pre-walk failure
    surface is now resolve_repos.)"""
    _provision(client.db_path, org_id=client.org_id, max_points=10000)
    t = MockGitHubTransport(issues=[gh_issue(1)])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    # seed persisted cursors + indexed state through the SERVER path: since
    # #3552 `github_index_cursor` is server-owned, so the PATCH this used to
    # seed with is refused 403 and would leave the test asserting against
    # nothing (the pre-walk-failure guarantee would go unexercised).
    import tortoise.hosted_api as ha
    ha._update_onboarding_state(
        client.org_id, _echo=False,
        github_index_cursor={
            "acme/repo1": {"updated_at": "2026-07-19T12:00:00Z",
                            "number": 7}},
        github_indexed=True)
    # break the org resolution → the pre-walk resolve_repos fails
    async def _boom(self, org, **kw):
        raise GitHubFetchError("org not found")

    monkeypatch.setattr(GitHubIndexer, "resolve_repos", _boom)
    r = _post_index(client.tc, "/v1/index/github", json={"org": "acme"})
    body = _poll_until(client.tc, r.json()["job_id"], "failed")
    assert "org not found" in body["error"]
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_index_cursor"] == {
        "acme/repo1": {"updated_at": "2026-07-19T12:00:00Z",
                        "number": 7}}, \
        "pre-walk failure must not wipe persisted cursors"
    assert state["github_indexed"] is True, \
        "an already-indexed team must NOT be downgraded to first-run by a " \
        "0-repo pre-walk failure (github_indexed only flips on progress)"


def test_owner_token_eviction_aborts_stale_run(client, tmp_path, monkeypatch):
    """P2: a stale run whose entry was TTL-evicted / replaced (owner token
    gone) must settle SILENTLY — no KeyError on status writes, no
    resurrection of the entry (pre-fix: `_INDEX_JOBS[job_id].update` raised
    KeyError on the missing entry)."""
    _provision(client.db_path, org_id=client.org_id)
    t = MockGitHubTransport(issues=[gh_issue(1)])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    from tortoise.hosted_api import _INDEX_JOB_OWNERS, _run_indexing
    stale_job, _ = _start_index_job(client.org_id)
    # entry + owner gone — as after a TTL eviction + replacement by a newer
    # run (the stale coroutine still holds its old job_id)
    _INDEX_JOBS.pop(stale_job, None)
    _INDEX_JOB_OWNERS.pop(stale_job, None)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            _run_indexing(stale_job, client.org_id, "acme", None))
    finally:
        loop.close()
    # the stale run settled without resurrecting its entry (ownership-gated
    # status writes were no-ops) and without raising
    assert stale_job not in _INDEX_JOBS
    assert stale_job not in _INDEX_JOB_OWNERS


# ── Historical observation-dedup script (Task 7) ──────────────────

@pytest.fixture
def dedup_sdk(tmp_path):
    store = TortoiseSDK(str(tmp_path / "dedup.db"))
    yield store
    store.close()


def test_dedup_script_dry_run(dedup_sdk):
    """Dry-run reports the duplicate observation→statement pairing with NO
    writes (deliver-or-defer default)."""
    script = _load_dedup_script()

    proj = dedup_sdk._get_proj()
    sdk = dedup_sdk
    sdk.create_point("observation", "old unkeyed claim",
                     github_url="https://github.com/acme/repo1/issues/1",
                     github_repo="acme/repo1", github_number=1,
                     github_state="open", source="github")
    sdk.create_point("statement", "[acme/repo1] Issue 1\n\nbody",
                     externalId="github:issue:acme/repo1#1",
                     github_url="https://github.com/acme/repo1/issues/1",
                     github_repo="acme/repo1", github_number=1,
                     extractedFrom="https://github.com/acme/repo1/issues/1",
                     source="github")
    report = script.dry_run_report(proj)
    assert report["observations_to_supersede"] == 1
    assert report["duplicate_urls"] == 1
    url = "https://github.com/acme/repo1/issues/1"
    assert url in report["pairs"]
    # dry-run performed NO writes
    assert proj.g.query("MATCH ()-[r:CORRECTS]->() RETURN count(r)").result_set[0][0] == 0


def test_dedup_script_opt_in_merge(dedup_sdk):
    """Opt-in merge supersedes the duplicate observation into its statement
    twin (CORRECTS + terminal observation, statement stays current)."""
    script = _load_dedup_script()

    proj = dedup_sdk._get_proj()
    sdk = dedup_sdk
    obs = sdk.create_point("observation", "old unkeyed claim",
                           github_url="https://github.com/acme/repo1/issues/1",
                           github_repo="acme/repo1", github_number=1,
                           github_state="open", source="github")
    stmt = sdk.create_point("statement", "[acme/repo1] Issue 1\n\nbody",
                            externalId="github:issue:acme/repo1#1",
                            github_url="https://github.com/acme/repo1/issues/1",
                            github_repo="acme/repo1", github_number=1,
                            extractedFrom="https://github.com/acme/repo1/issues/1",
                            source="github")
    report = script.merge_duplicates(sdk, proj, dry_run=False)
    assert report["merged"] == 1
    rows = proj.g.query(
        "MATCH (a:Point {id:$new})-[:CORRECTS]->(b:Point {id:$old}) "
        "RETURN b.status, b.outdated",
        params={"new": stmt["id"], "old": obs["id"]},
    ).result_set
    assert rows and rows[0][0] == "superseded" and rows[0][1] is True


def test_dedup_script_leave_as_is_by_default(dedup_sdk):
    """The script's DEFAULT mode is dry-run — no writes even when duplicates
    exist (the recorded deliver-or-defer decision)."""
    script = _load_dedup_script()

    proj = dedup_sdk._get_proj()
    sdk = dedup_sdk
    sdk.create_point("observation", "old unkeyed claim",
                     github_url="https://github.com/acme/repo1/issues/1",
                     github_repo="acme/repo1", github_number=1,
                     github_state="open", source="github")
    sdk.create_point("statement", "[acme/repo1] Issue 1\n\nbody",
                     externalId="github:issue:acme/repo1#1",
                     github_url="https://github.com/acme/repo1/issues/1",
                     github_repo="acme/repo1", github_number=1,
                     extractedFrom="https://github.com/acme/repo1/issues/1",
                     source="github")
    report = script.merge_duplicates(sdk, proj, dry_run=True)
    assert report["dry_run"] is True
    assert report["merged"] == 0
    assert proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 2


def test_dedup_script_resolve_uri_honors_explicit_uri(monkeypatch):
    """P2 (PR #1792): an EXPLICIT --uri equal to the default constant is
    honored verbatim — the old constant-comparison silently rerouted it to
    the embedded DB (the operator's explicit target was ignored)."""
    script = _load_dedup_script()
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    # no URI anywhere → embedded default path (not a URI)
    embedded = script._resolve_uri("")
    assert embedded and not embedded.startswith("docker://")
    # explicit URI equal to the constant → honored verbatim
    assert script._resolve_uri(script.DEFAULT_URI) == script.DEFAULT_URI
    # explicit --uri wins over env
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:x@localhost:9999/other")
    assert script._resolve_uri(script.DEFAULT_URI) == script.DEFAULT_URI
    # env-only → env wins
    assert script._resolve_uri("") == "docker://:x@localhost:9999/other"
