"""#2006 (W11) — onboarding funnel telemetry: seed_complete + decide_complete.

The two events ride the EXISTING PostHog seam (``tortoise/analytics.py``) and
are gated on **structural dedup**: ``onboarding.state.write_completed_step``
returns ``created=True`` only for the write that NEWLY creates the
``COMPLETED_STEP`` edge, and every writer emits only when it observed that
transition. The edge is the domain fact, so the events are exact-once per
EDGE CREATION by construction — restart-safe and multi-worker-safe, with no
threshold, no second dedup store and no in-process set. (``first-points-filed``
is write-once, so for it that is once per org forever; ``decide-completed`` is
the one W11 edge with a sanctioned removal path — the #3912 false-completion
repair — after which a genuine re-completion creates the edge again and
re-emits. See ``analytics.onboarding_decide_complete``. The residual
funnel-accuracy question is filed as #4458.)

The two exact-once tests (each would go RED if emission were ungated):

* ``test_repeat_write_emits_exactly_once`` — the core: the second write of the
  same step reports ``created=False`` and emits NOTHING.
* ``test_two_writers_race_on_the_same_step_emit_one_event`` — the actual
  property the issue asks for ("not the multi-worker-racy in-process set"):
  the agent checkpoint and the MCP auto-complete both write the SAME edge and
  exactly one event is emitted in total, in either order.

``test_capture_failure_never_breaks_the_write_path`` is the separate R19
guard: a raising PostHog client must not turn a committed write into an error.

DB-free: the edge store is a miniature ``COMPLETED_STEP`` store with the real
once-only MERGE semantics, and the graph/projection/gate legs are stubbed, so
the assertions target exactly the write-path dedup this issue owns.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault(
    "TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

import pytest
from fastapi.testclient import TestClient

import tortoise.analytics as analytics
from tests._http_fixtures import patched_tortoise_sdk
from tortoise import hosted_api
from tortoise import mcp_server as mcp
from tortoise.hosted_api import app, get_current_org
from tortoise.mcp_auth import SELFHOST_ORG_ID, _current_org_id
from tortoise.onboarding import state as onboarding_state

ORG = "org-2006-telemetry"
# A UUID, not a placeholder: `_onboarding_distinct_id` passes only UUID-shaped
# candidates (the same gate the #2600 `actor_user_id` alias uses), so the
# funnel identity assertions below exercise the real contract.
USER = "3f1a7c2e-9b84-4d51-a0c6-71e2ab5f8d30"
# #3671: a step checkpoint now requires an AGENT credential — a session JWT is
# refused 403 — so this org is the AGENT lane (`key_id`, no `session_user_id`).
# `actor_user_id` is the #2600 alias of the key's `created_by` and keeps the
# UUID funnel identity the W11 events join on.
TEAM = {
    "org_id": ORG, "tier": "free", "key_id": "k1",
    "legacy_full_access": True, "max_users": 1, "max_graphs": 1,
    "max_teams": 1, "max_points": 10000, "max_sessions": None,
    "actor_user_id": USER,
}


class _StepEdgeStore:
    """A miniature COMPLETED_STEP edge store — the REAL once-only semantics.

    ``write_completed_step`` MERGEs the edge and reports ``created`` True only
    for the call that created it; every replay sees the edge and reports
    False. Two different writers share one store, which is how the
    multi-writer test gets the cross-writer (not per-process) dedup.
    """

    def __init__(self):
        self.edges: set[tuple[str, str]] = set()
        self.writes: list[str] = []

    def write(self, proj, org_id, step_id, *, status_from_mirror=None):
        self.writes.append(step_id)
        key = (org_id, step_id)
        created = key not in self.edges
        self.edges.add(key)
        return {"created": created, "step_id": step_id}


@pytest.fixture
def emitted(monkeypatch):
    """Capture PostHog calls (module state is env-derived at import)."""
    calls: list[dict] = []
    monkeypatch.setattr(analytics.posthog, "disabled", False)
    monkeypatch.setattr(analytics.posthog, "capture",
                        lambda **kw: calls.append(kw))
    return calls


def _events(calls, name):
    return [c for c in calls if c["event"] == name]


@pytest.fixture
def edges(monkeypatch):
    """Install the edge store + stub the graph legs it does not need."""
    store = _StepEdgeStore()
    monkeypatch.setattr(onboarding_state, "write_completed_step", store.write)
    monkeypatch.setattr(hosted_api, "_graph_available", lambda org_id: True)
    monkeypatch.setattr(hosted_api, "_org_proj", lambda org_id: object())
    monkeypatch.setattr(hosted_api, "_maybe_apply_completion",
                        lambda org_id: False)
    monkeypatch.setattr(hosted_api, "_get_onboarding_state",
                        lambda org_id: {})
    monkeypatch.setattr(hosted_api, "_get_onboarding_projection",
                        lambda org_id: {"onboarding_complete": False,
                                        "completed_steps": []})
    return store


@pytest.fixture
def client(tmp_path, edges, emitted):
    """TestClient with auth overridden to ORG (embedded temp DB, no network)."""
    with patched_tortoise_sdk(str(tmp_path / "w11.db")):
        app.dependency_overrides[get_current_org] = lambda: dict(TEAM)
        with TestClient(app) as tc:
            yield tc
        app.dependency_overrides.clear()


@pytest.fixture
def org_ctx(monkeypatch):
    """The MCP plane's request-scoped org (hosted, not selfhost)."""
    token = _current_org_id.set(ORG)
    mcp._onboarding_state_cache.pop(ORG, None)
    try:
        yield ORG
    finally:
        _current_org_id.reset(token)
        mcp._onboarding_state_cache.pop(ORG, None)


def _checkpoint(client, step):
    return client.post("/v1/onboarding/state/checkpoint", json={"step": step})


# ── the core: exact-once per edge creation ────────────────────────────

class TestExactOnce:
    def test_repeat_write_emits_exactly_once(self, client, edges, emitted):
        """The CORE test: a duplicate write of the same step emits exactly
        one event — the second call's created=False emits NOTHING.

        Mutating the emission to ignore the gate (e.g. passing
        ``created_steps + noop_steps``) turns this RED with 2 events."""
        first = _checkpoint(client, "first-points-filed")
        assert first.status_code == 200, first.text
        assert first.json()["created_steps"] == ["first-points-filed"]

        second = _checkpoint(client, "first-points-filed")
        assert second.status_code == 200, second.text
        assert second.json()["created_steps"] == []
        assert second.json()["noop_steps"] == ["first-points-filed"]

        evs = _events(emitted, "onboarding_seed_complete")
        assert len(evs) == 1, (
            f"expected exactly one onboarding_seed_complete, got {evs}")
        assert evs[0]["properties"] == {"org_id": ORG, "source": "checkpoint"}
        assert evs[0]["distinct_id"] == USER  # actor UUID (funnel join)
        # no decide event ever fired from a seed step
        assert _events(emitted, "onboarding_decide_complete") == []

    def test_decide_step_emits_decide_complete_once(self, client, edges,
                                                    emitted):
        assert _checkpoint(client, "decide-completed").status_code == 200
        assert _checkpoint(client, "decide-completed").status_code == 200
        evs = _events(emitted, "onboarding_decide_complete")
        assert len(evs) == 1, evs
        assert evs[0]["properties"] == {"org_id": ORG, "source": "checkpoint"}
        assert _events(emitted, "onboarding_seed_complete") == []

    @pytest.mark.parametrize("step", [
        "harness-connected", "catalog-presented", "capture-disclosed"])
    def test_uninstrumented_steps_emit_nothing(self, client, edges, emitted,
                                               step):
        """harness-connected / catalog-presented / capture-disclosed carry no
        W11 event (only seed + decide do)."""
        r = _checkpoint(client, step)
        assert r.status_code == 200, r.text
        assert r.json()["created_steps"] == [step]
        assert _events(emitted, "onboarding_seed_complete") == []
        assert _events(emitted, "onboarding_decide_complete") == []

    def test_mcp_replay_emits_nothing(self, client, edges, emitted, org_ctx):
        """The MCP auto-complete's step set is idempotent: the second pass
        creates no edge and therefore emits no event."""
        mcp._maybe_onboarding_auto_complete(decision_observed=True)
        first = len(emitted)
        mcp._maybe_onboarding_auto_complete(decision_observed=True)
        assert len(emitted) == first, "a replay emitted a funnel event"


# ── the property the issue names: cross-writer dedup ──────────────────

class TestMultiWriter:
    def test_two_writers_race_on_the_same_step_emit_one_event(
            self, client, edges, emitted, org_ctx):
        """Two DIFFERENT writers write the same seed edge; exactly ONE event.

        Writer 1 = the agent checkpoint (POST /v1/onboarding/state/checkpoint);
        writer 2 = the MCP auto-complete (tortoise/mcp_server.py). They share
        no process state: the dedup is the edge itself, which is why this is
        not the multi-worker-racy in-process set."""
        assert _checkpoint(client, "first-points-filed").status_code == 200
        mcp._maybe_onboarding_auto_complete()

        assert edges.writes.count("first-points-filed") == 2, edges.writes
        evs = _events(emitted, "onboarding_seed_complete")
        assert len(evs) == 1, (
            f"two writers produced {len(evs)} seed events: {evs}")
        assert evs[0]["properties"]["source"] == "checkpoint"

    def test_writer_order_does_not_change_the_count(self, client, edges,
                                                    emitted, org_ctx):
        """Same property with the MCP writer first — the event attributes to
        whichever writer created the edge, and the total is still one."""
        mcp._maybe_onboarding_auto_complete()
        assert _checkpoint(client, "first-points-filed").status_code == 200

        assert edges.writes.count("first-points-filed") == 2, edges.writes
        evs = _events(emitted, "onboarding_seed_complete")
        assert len(evs) == 1, evs
        assert evs[0]["properties"]["source"] == "mcp_auto"

    def test_mcp_decision_observation_emits_both_events_once(
            self, client, edges, emitted, org_ctx):
        mcp._maybe_onboarding_auto_complete(decision_observed=True)
        seed = _events(emitted, "onboarding_seed_complete")
        decide = _events(emitted, "onboarding_decide_complete")
        assert len(seed) == 1 and len(decide) == 1, (seed, decide)
        assert seed[0]["properties"]["source"] == "mcp_auto"
        assert decide[0]["properties"] == {"org_id": ORG,
                                          "source": "mcp_auto"}

    def test_mcp_without_a_decision_emits_no_decide_event(
            self, client, edges, emitted, org_ctx):
        """A plain write observes no decision (#3784) — and therefore must not
        claim the decide funnel event either."""
        mcp._maybe_onboarding_auto_complete(decision_observed=False)
        assert _events(emitted, "onboarding_decide_complete") == []
        assert len(_events(emitted, "onboarding_seed_complete")) == 1


# ── the seam's fail-safe promise ──────────────────────────────────────

class TestFailSafe:
    def test_capture_failure_never_breaks_the_write_path(
            self, client, edges, monkeypatch):
        """analytics.capture() never raises (R19): a PostHog client blowing
        up must not turn a committed checkpoint into an error."""
        def _boom(**kw):
            raise RuntimeError("posthog down")

        monkeypatch.setattr(analytics.posthog, "disabled", False)
        monkeypatch.setattr(analytics.posthog, "capture", _boom)

        r = _checkpoint(client, "first-points-filed")
        assert r.status_code == 200, r.text
        assert r.json()["created_steps"] == ["first-points-filed"]
        assert (ORG, "first-points-filed") in edges.edges  # edge committed

    def test_emitter_failure_never_breaks_the_write_path(
            self, client, edges, monkeypatch):
        """Stronger than the capture() contract: even if the emitter itself
        raised (a bug in the helper, a bad mapping), the write still lands."""
        def _boom(*a, **k):
            raise RuntimeError("emitter bug")

        monkeypatch.setattr(hosted_api, "_emit_onboarding_step_events", _boom)
        r = _checkpoint(client, "first-points-filed")
        assert r.status_code == 200, r.text
        assert r.json()["created_steps"] == ["first-points-filed"]


# ── the other writers of first-points-filed / decide-completed ─────────

class _FakeSeedSDK:
    def _get_proj(self):
        return object()

    def close(self):
        pass


_ANCHOR_REPORT = {
    "org_subject": {"id": "s-org", "name": "Acme"},
    "user_subject": {"id": "s-user", "name": "Alex"},
    "org_created": True,
    "person_created": True,
    "org_kind_normalized": False,
    "person_kind_normalized": False,
    "member_of": True,
}


@pytest.fixture
def seed_env(monkeypatch, edges):
    """The seed runners' external legs, stubbed (no graph)."""
    from tortoise.onboarding import seed as seed_mod
    monkeypatch.setattr(hosted_api, "_make_sdk",
                        lambda **kw: _FakeSeedSDK())
    monkeypatch.setattr(hosted_api, "_org_name", lambda org_id: "Acme")
    monkeypatch.setattr(seed_mod, "seed_onboarding_anchors",
                        lambda *a, **k: dict(_ANCHOR_REPORT))
    monkeypatch.setattr(onboarding_state, "read_onboarding_node",
                        lambda proj, org_id: None)
    monkeypatch.setattr(onboarding_state, "write_onboards_edge",
                        lambda proj, org_id, sid: {"created": True})
    monkeypatch.setattr(hosted_api, "_next_onboarding_step",
                        lambda org_id, proj: "done")
    return edges


class TestSeedWriters:
    def test_interactive_seed_emits_once_and_replay_is_silent(
            self, seed_env, emitted):
        """W3 seed runner (shared by POST /v1/onboarding/seed AND the MCP
        seed tool) — the self-hosted/MCP write path."""
        res = hosted_api._run_onboarding_seed(
            ORG, org_name="Acme", person_name="Alex",
            person_user_id=USER)
        assert res["status"] == "seeded", res
        res2 = hosted_api._run_onboarding_seed(
            ORG, org_name="Acme", person_name="Alex",
            person_user_id=USER)
        assert res2["status"] == "seeded", res2

        evs = _events(emitted, "onboarding_seed_complete")
        assert len(evs) == 1, evs
        assert evs[0]["properties"] == {"org_id": ORG, "source": "seed"}
        assert evs[0]["distinct_id"] == USER

    def test_starter_seed_emits_once_and_replay_is_silent(
            self, seed_env, emitted):
        """The provisioning-time seed runner (#2360) — the SECOND writer of
        first-points-filed that the design round missed. It must emit on its
        OWN created result, and the shared edge still yields one event."""
        res = hosted_api._run_starter_seed(
            ORG, org_name="Acme", person_name="Alex",
            person_user_id=USER)
        assert res["status"] == "seeded", res
        res2 = hosted_api._run_starter_seed(
            ORG, org_name="Acme", person_name="Alex",
            person_user_id=USER)
        assert res2["status"] == "seeded", res2

        evs = _events(emitted, "onboarding_seed_complete")
        assert len(evs) == 1, evs
        assert evs[0]["properties"] == {"org_id": ORG,
                                       "source": "starter_seed"}
        assert evs[0]["distinct_id"] == USER

    def test_starter_seed_without_person_files_no_step_and_no_event(
            self, seed_env, emitted):
        """No person anchor → no first-points-filed edge → no funnel event."""
        res = hosted_api._run_starter_seed(ORG, org_name="Acme")
        assert res["status"] == "seeded", res
        assert _events(emitted, "onboarding_seed_complete") == []

    def test_seed_then_checkpoint_still_emits_one(
            self, client, seed_env, emitted):
        """Cross-surface: the seed runner creates the edge, a later agent
        checkpoint replay is silent — one event across both surfaces."""
        hosted_api._run_onboarding_seed(
            ORG, org_name="Acme", person_name="Alex", person_user_id=USER)
        assert _checkpoint(client, "first-points-filed").status_code == 200
        assert len(_events(emitted, "onboarding_seed_complete")) == 1


# ── identity + noise guards ───────────────────────────────────────────

def test_non_uuid_identity_never_becomes_distinct_id(monkeypatch):
    """A registry-lane key's `created_by` can be an EMAIL (or the literal
    "api"/an st_ recovery id). Those must NEVER reach PostHog as the
    `distinct_id` — that would push PII and collapse unrelated orgs onto one
    pseudo-person, breaking the web-funnel join. The helper drops any
    non-UUID candidate and falls back to the org id."""
    from tortoise.sdk import _current_actor_user_id
    token = _current_actor_user_id.set(None)
    try:
        assert hosted_api._onboarding_distinct_id(
            ORG, {"created_by": "alex@example.com"}) == ORG
        assert hosted_api._onboarding_distinct_id(
            ORG, {"created_by": "api"}) == ORG
        assert hosted_api._onboarding_distinct_id(
            ORG, {"created_by": "st_deadbeef"}) == ORG
        # …while a real UUID passes through, and the #2600 alias wins first.
        assert hosted_api._onboarding_distinct_id(
            ORG, {"created_by": USER}) == USER
        assert hosted_api._onboarding_distinct_id(
            ORG, {"actor_user_id": USER,
                  "created_by": "alex@example.com"}) == USER
    finally:
        _current_actor_user_id.reset(token)


def test_selfhost_and_stdio_orgs_emit_nothing(edges, emitted, monkeypatch):
    """The MCP auto-complete no-ops for stdio/selfhost (no hosted onboarding
    state) — so no funnel event can fire there."""
    token = _current_org_id.set(SELFHOST_ORG_ID)
    mcp._onboarding_state_cache.pop(SELFHOST_ORG_ID, None)
    try:
        mcp._maybe_onboarding_auto_complete(decision_observed=True)
    finally:
        _current_org_id.reset(token)
    assert _events(emitted, "onboarding_seed_complete") == []
    assert _events(emitted, "onboarding_decide_complete") == []
