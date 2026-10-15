"""#2007 (W12) — the SELF-HOSTED onboarding path.

DE2E-10's assertions, all without a Supabase control plane:

- the OnboardingState node is inited at the selfhost SDK/API init seam
  (``selfhost_api._sdk`` for REST, ``mcp_auth._get_org_sdk`` for the selfhost
  MCP transport) — ONE owned write point, idempotent no-op when present;
- it carries ``fork='self'`` (so the fork card can never render on
  self-hosted — self-hosted is a deployment mode, R2-2);
- the two prompts (name, organization) file two Subjects linked ``memberOf``,
  idempotently;
- ONE decide completes the fork-aware gate;
- the self-hosted path never reaches the Supabase control plane (behavioural
  tripwire + a structural check over the module's imports);
- the REST + MCP surfaces reuse the registry's OWN paths/names, so an agent
  pointed at a self-hosted daemon needs no new client contract.

Runs URI-less (embedded lane, the self-hosted no-Supabase deployment mode);
the embedded graph stubs the FTS-only leg via ``force_sparse_tfidf`` so no
test depends on the ambient model cache (#3223).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault(
    "TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

from pathlib import Path

import pytest

from tortoise.mcp_auth import SELFHOST_ORG_ID, _current_org_id
from tortoise.onboarding import selfhost as sh
from tortoise.sdk import TortoiseSDK

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _sparse_embedder(force_sparse_tfidf):
    """Deterministic, network-free embedding leg for every test in this file
    (the seed files Subjects, which embed)."""
    return force_sparse_tfidf


def _close_anchors() -> None:
    """Close + drop every selfhost keepalive anchor.

    The anchor pins an embedded redislite daemon for the life of the process;
    clearing the dict WITHOUT closing leaks one daemon per test (the #2180 /
    #2090 leak these fixtures exist to avoid — a leaked daemon is what makes a
    later test's fresh embedded server fail to come up)."""
    from tortoise import selfhost_api as _sha
    for path in list(_sha._SELFHOST_KEEPALIVE):
        anchor = _sha._SELFHOST_KEEPALIVE.pop(path, None)
        if anchor is not None:
            try:  # noqa: SIM105
                anchor.close()
            except Exception:
                pass


@pytest.fixture
def db_env(monkeypatch, tmp_path):
    """Point the selfhost lane at a throwaway embedded graph and reset the
    process-level init memo (a second target in the same process must be able
    to observe the init firing again)."""
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "selfhost.db"))
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    _close_anchors()
    sh.reset_selfhost_onboarding_init_memo()
    yield str(tmp_path / "selfhost.db")
    sh.reset_selfhost_onboarding_init_memo()
    _close_anchors()


def _sdk() -> TortoiseSDK:
    return TortoiseSDK(namespace="selfhost")


class _selfhost_transport_context:
    """Set the ContextVars the selfhost HTTP transport middleware
    (`mcp_auth.TransportModeMiddleware`) sets, so MCP tool bodies exercise the
    self-hosted lane instead of failing `_safe`'s fail-closed transport gate."""

    def __enter__(self):
        from tortoise.mcp_auth import (
            _current_graph_id,
            _current_graph_namespace,
            _current_legacy_full_access,
            _current_org_limits,
            _current_scopes,
            _transport_mode,
        )
        from tortoise.sdk import _current_actor_user_id
        self._tokens = [
            (_current_org_id, _current_org_id.set(SELFHOST_ORG_ID)),
            (_transport_mode, _transport_mode.set("http")),
            (_current_graph_id, _current_graph_id.set(None)),
            (_current_graph_namespace, _current_graph_namespace.set(None)),
            (_current_scopes, _current_scopes.set(None)),
            (_current_legacy_full_access,
             _current_legacy_full_access.set(None)),
            (_current_org_limits, _current_org_limits.set(None)),
            (_current_actor_user_id, _current_actor_user_id.set(None)),
        ]
        return self

    def __exit__(self, *_exc):
        for var, token in reversed(self._tokens):
            var.reset(token)
        return False


def _subjects(sdk) -> dict[str, dict]:
    rows = sdk._get_proj().query(
        "MATCH (s:Subject) RETURN properties(s)").result_set
    return {r[0]["name"]: r[0] for r in rows}


def _member_of_count(sdk) -> int:
    rows = sdk._get_proj().query(
        "MATCH ()-[r:memberOf]->() RETURN count(r)").result_set
    return int(rows[0][0])


# ── 1. node init at the selfhost SDK/API init seams ──────────

class TestNodeInit:
    def test_api_sdk_init_creates_node_with_fork_self(self, db_env):
        """The selfhost API's SDK factory (`selfhost_api._sdk`) is the REST
        init seam: the node exists with fork='self' from the FIRST request."""
        from tortoise import selfhost_api as _sha
        sdk = _sha._sdk()
        try:
            state = sh.read_selfhost_state(sdk)
        finally:
            sdk.close()
        assert state["node"] is True
        assert state["fork"] == "self"
        assert state["status"] == "active"
        assert state["compact"] is False
        assert state["onboarding_complete"] is False
        # `team-named` is NOT claimed at init — self-hosted names the org at
        # the seed prompt, not before it.
        assert state["completed_steps"] == []

    def test_mcp_sdk_init_creates_node(self, db_env, monkeypatch):
        """The selfhost MCP transport reaches the graph only through
        `mcp_auth._get_org_sdk` — the node must exist from there too (the
        self-hosted daemon's /mcp lane and `serve --http --auth static`)."""
        from tortoise.mcp_auth import _get_org_sdk
        token = _current_org_id.set(SELFHOST_ORG_ID)
        try:
            sdk = _get_org_sdk()
            state = sh.read_selfhost_state(sdk)
        finally:
            _current_org_id.reset(token)
        assert state["node"] is True
        assert state["fork"] == "self"

    def test_init_is_idempotent_and_never_clobbers(self, db_env):
        """ONE owned write point, idempotent: a second init (restart, second
        request, second lane) is a no-op — it can neither duplicate the node
        nor regress FLOW state."""
        sdk = _sdk()
        try:
            first = sh.ensure_selfhost_onboarding(sdk)
            # a completed node must survive a re-init untouched
            sh.seed_selfhost_anchors(sdk, org_name="Acme", person_name="Ada")
            sh.observe_selfhost_write(decision_observed=True, sdk=sdk)
            before = sh.read_selfhost_state(sdk)
            assert before["onboarding_complete"] is True

            sh.ensure_selfhost_onboarding(sdk)
            sh.ensure_selfhost_onboarding_once(sdk, target="explicit")
            after = sh.read_selfhost_state(sdk)
            nodes = sdk._get_proj().query(
                "MATCH (n:OnboardingState {org_id: $org_id}) RETURN count(n)",
                org_id=SELFHOST_ORG_ID).result_set
        finally:
            sdk.close()
        assert nodes[0][0] == 1
        assert first["fork"] == "self"
        assert after == before

    def test_init_once_retries_after_a_failure(self, db_env, monkeypatch):
        """A failed init is NOT memoized — the next SDK init retries, and the
        wrapper never raises into the request that triggered it."""
        calls: list[int] = []

        def _boom(handle):
            calls.append(1)
            raise RuntimeError("graph down")

        monkeypatch.setattr(sh, "ensure_selfhost_onboarding", _boom)
        sdk = _sdk()
        try:
            sh.ensure_selfhost_onboarding_once(sdk, target="t1")  # no raise
            sh.ensure_selfhost_onboarding_once(sdk, target="t1")
        finally:
            sdk.close()
        assert len(calls) == 2  # failure not memoized → retried

    def test_graph_down_read_serves_unavailable_markers(self, db_env):
        """DE2E-10 negative case: a graph-down read reports 'unavailable' for
        every FLOW key (200, never fabricated defaults, never a false 'N of
        M complete')."""

        class _DeadProj:
            def query(self, *_a, **_k):
                raise RuntimeError("graph down")

        class _DeadSdk:
            def _get_proj(self):
                return _DeadProj()

        state = sh.read_selfhost_state(_DeadSdk())
        assert state["fork"] == "unavailable"
        assert state["status"] == "unavailable"
        assert state["completed_steps"] == "unavailable"
        assert state["onboarding_complete"] == "unavailable"
        assert state["next"] == "unavailable"
        assert state["node"] == "unavailable"

    def test_absent_node_read_is_read_only(self, db_env):
        """Pin 4: the READ path never writes. Before the init seam runs, the
        projection is the FLOW default set and the node is still absent — so a
        reader can never manufacture the node (the init seam is the ONE owned
        writer)."""
        sdk = _sdk()
        try:
            state = sh.read_selfhost_state(sdk)
            assert state["node"] is False
            assert sh.ensure_selfhost_onboarding(sdk) is not None
        finally:
            sdk.close()
        sdk = _sdk()
        try:
            assert sh.read_selfhost_state(sdk)["node"] is True
        finally:
            sdk.close()


# ── 2. the two-prompt seed ──────────────────────────────────

class TestSeed:
    def test_two_prompts_two_subjects_linked_member_of(self, db_env):
        """DE2E-10: two prompts → two Subjects (Organization/organization +
        User/naturalPerson) linked memberOf, plus the node↔anchor link."""
        sdk = _sdk()
        try:
            r = sh.seed_selfhost_anchors(sdk, org_name="Acme Ltd",
                                         person_name="Ada Lovelace")
            state = sh.read_selfhost_state(sdk)
            subjects = _subjects(sdk)
            edges = _member_of_count(sdk)
        finally:
            sdk.close()
        assert r["status"] == "seeded"
        assert r["org_created"] is True and r["person_created"] is True
        assert subjects["Acme Ltd"]["subjectKind"] == "organization"
        assert subjects["Acme Ltd"]["org_id"] == SELFHOST_ORG_ID
        assert subjects["Ada Lovelace"]["subjectKind"] == "naturalPerson"
        assert subjects["Ada Lovelace"]["user_id"] == sh.selfhost_local_user_ref()
        assert edges == 1
        assert r["onboards"]["subject_id"] == r["org_subject"]["id"]
        assert set(state["completed_steps"]) == {
            "team-named", "harness-connected", "first-points-filed"}
        assert state["next"] == "decide-completed"

    def test_resume_is_idempotent_never_restarts(self, db_env):
        """Re-running the seed (resume, never restart) reuses the anchors —
        no duplicate Subjects, no duplicate memberOf edge."""
        sdk = _sdk()
        try:
            first = sh.seed_selfhost_anchors(sdk, org_name="Acme",
                                             person_name="Ada")
            second = sh.seed_selfhost_anchors(sdk, org_name="Acme",
                                              person_name="Ada")
            subjects = _subjects(sdk)
            edges = _member_of_count(sdk)
            state = sh.read_selfhost_state(sdk)
        finally:
            sdk.close()
        assert second["status"] == "seeded"
        assert second["org_created"] is False
        assert second["person_created"] is False
        assert sorted(subjects) == ["Acme", "Ada"]
        assert edges == 1
        assert first["org_subject"]["id"] == second["org_subject"]["id"]
        assert state["completed_steps"].count("first-points-filed") == 1

    def test_both_prompts_are_required_never_invented(self, db_env):
        """DM-3 on self-hosted: there is no JWT / teams.name / email to derive
        from, so a missing answer is refused — never a placeholder."""
        sdk = _sdk()
        try:
            for kwargs in ({"org_name": None, "person_name": "Ada"},
                           {"org_name": "Acme", "person_name": "  "},
                           {"org_name": "", "person_name": "Ada"}):
                with pytest.raises(ValueError):
                    sh.seed_selfhost_anchors(sdk, **kwargs)
            assert _subjects(sdk) == {}  # zero writes on a refusal
        finally:
            sdk.close()

    def test_collision_refuses_with_zero_writes(self, db_env):
        """A same-name Subject that is NOT this deployment's anchor (ref-less,
        so identity-unprovable) is a collision — surfaced, never merged."""
        sdk = _sdk()
        try:
            sdk.create_subject("Ada", subjectKind="naturalPerson")
            r = sh.seed_selfhost_anchors(sdk, org_name="Acme",
                                         person_name="Ada")
            subjects = _subjects(sdk)
            edges = _member_of_count(sdk)
        finally:
            sdk.close()
        assert r["status"] == "collision"
        assert r["collisions"][0]["name"] == "Ada"
        assert sorted(subjects) == ["Ada"]      # the org anchor was NOT filed
        assert edges == 0

    def test_legacy_person_kind_normalized(self, db_env):
        """The W3 seed's person→naturalPerson normalization applies on the
        self-hosted lane too (it consumes the same core)."""
        sdk = _sdk()
        try:
            sdk.create_subject("Ada", subjectKind="person",
                               user_id=sh.selfhost_local_user_ref())
            r = sh.seed_selfhost_anchors(sdk, org_name="Acme",
                                         person_name="Ada")
            subjects = _subjects(sdk)
        finally:
            sdk.close()
        assert r["status"] == "seeded"
        assert r["person_kind_normalized"] is True
        assert subjects["Ada"]["subjectKind"] == "naturalPerson"


# ── 3. one decide completes ─────────────────────────────────

class TestDecide:
    def test_decide_completes_the_fork_aware_gate(self, db_env):
        """DE2E-10: seed → one decide → gate satisfied, status 'complete'."""
        sdk = _sdk()
        try:
            sh.seed_selfhost_anchors(sdk, org_name="Acme", person_name="Ada")
            assert sh.read_selfhost_state(sdk)["onboarding_complete"] is False
            sh.observe_selfhost_write(decision_observed=True, sdk=sdk)
            state = sh.read_selfhost_state(sdk)
        finally:
            sdk.close()
        assert state["onboarding_complete"] is True
        assert state["status"] == "complete"
        assert "decide-completed" in state["completed_steps"]
        assert state["next"] is None
        assert state["fork"] == "self"

    def test_non_decision_write_never_claims_a_decision(self, db_env):
        """#3784 fail-closed: a write that did not observe a decision must not
        file `decide-completed` — the org stays incomplete."""
        sdk = _sdk()
        try:
            sh.seed_selfhost_anchors(sdk, org_name="Acme", person_name="Ada")
            sh.observe_selfhost_write(decision_observed=False, sdk=sdk)
            state = sh.read_selfhost_state(sdk)
        finally:
            sdk.close()
        assert "decide-completed" not in state["completed_steps"]
        assert state["onboarding_complete"] is False
        assert state["next"] == "decide-completed"

    def test_decide_before_the_prompts_still_files_the_canonical_gate(
            self, db_env):
        """The selfhost gate IS the canonical self-fork gate, and #3784's
        "no separate ceremony" semantics hold on the self-hosted lane: any
        FLOW write satisfies the org-named edge through the state machine's
        create-on-write seam (``team-named`` is in ``_NON_AGENT_STEPS`` for
        exactly this reason), so a decision-shaped write alone closes it.
        W12 does NOT invent a selfhost-only gate; the two prompts are the
        JOURNEY (J7) and the seed files the two Subjects."""
        sdk = _sdk()
        try:
            sh.observe_selfhost_write(decision_observed=True, sdk=sdk)
            state = sh.read_selfhost_state(sdk)
        finally:
            sdk.close()
        assert state["onboarding_complete"] is True
        assert set(state["completed_steps"]) == {
            "team-named", "harness-connected", "first-points-filed",
            "decide-completed"}
        # #2007: this is the node-ABSENT path — no init and no seed ran, so the
        # step writes are what create the node. It must still be born
        # fork='self' (the create-on-write seam would otherwise leave `fork`
        # unset, permanently, since `fork` is set-once).
        assert state["fork"] == "self"


# ── 4. fork='self' / no fork card ───────────────────────────

class TestForkSelf:
    def test_fork_is_pinned_self_and_never_asks(self, db_env):
        """R2-2 / J7: the fork defaults to 'self' at the init seam — the
        read-time fork is never null once the init has run, so the fork card
        can never render, and no deferral marker (`fork_unsure_at`) is ever
        written."""
        sdk = _sdk()
        try:
            sh.ensure_selfhost_onboarding(sdk)
            state = sh.read_selfhost_state(sdk)
        finally:
            sdk.close()
        assert state["fork"] == "self"
        assert state["fork_unsure_at"] is None
        assert state["compact"] is False


# ── 5. no Supabase on the self-hosted path ──────────────────

class TestNoSupabase:
    def test_onboarding_path_never_touches_the_control_plane(
            self, db_env, monkeypatch):
        """Parity violation guard (test-design surface 15): with the control
        plane turned into a tripwire, the whole self-hosted onboarding path
        still works — init, seed, decide, read."""
        import tortoise.hosted_api as _ha
        import tortoise.supabase_control as _sc

        def _tripwire(*_a, **_k):
            raise AssertionError(
                "the self-hosted onboarding path reached the Supabase "
                "control plane (W12 parity violation)")

        for mod, name in ((_sc, "is_supabase_enabled"),
                          (_sc, "get_control_plane"),
                          (_sc, "org_onboarding_state"),
                          (_ha, "_get_onboarding_projection"),
                          (_ha, "_maybe_apply_completion"),
                          (_ha, "_org_proj")):
            monkeypatch.setattr(mod, name, _tripwire)
        sdk = _sdk()
        try:
            sh.ensure_selfhost_onboarding(sdk)
            assert sh.seed_selfhost_anchors(
                sdk, org_name="Acme", person_name="Ada")["status"] == "seeded"
            sh.observe_selfhost_write(decision_observed=True, sdk=sdk)
            state = sh.read_selfhost_state(sdk)
        finally:
            sdk.close()
        assert state["onboarding_complete"] is True

    def test_module_imports_no_control_plane(self):
        """Structural half of the parity guard: the module this lane is built
        on imports neither the Supabase control plane nor hosted_api."""
        src = (_REPO_ROOT / "tortoise" / "onboarding" / "selfhost.py"
               ).read_text()
        imports = [ln.strip() for ln in src.splitlines()
                   if ln.strip().startswith(("import ", "from "))]
        offenders = [ln for ln in imports
                     if "supabase" in ln or "hosted_api" in ln]
        assert offenders == [], offenders


# ── 6. the surfaces an agent already calls ──────────────────

def _client(monkeypatch, tmp_path):
    import importlib

    from starlette.testclient import TestClient

    from tortoise import selfhost
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "daemon.db"))
    _close_anchors()
    sh.reset_selfhost_onboarding_init_memo()
    importlib.reload(selfhost)
    return TestClient(selfhost.app)


class TestRestSurface:
    def test_registry_paths_are_served_by_the_selfhost_router(self):
        """The self-hosted routes ARE the registry's RestSpec paths for the
        two onboarding tools — no new client contract for a self-hosted
        deployment."""
        from tortoise.selfhost_api import router
        from tortoise.tool_registry import TOOL_REGISTRY
        spec = {t.name: (t.rest_spec.method, t.rest_spec.path)
                for t in TOOL_REGISTRY if t.rest_spec}
        routes = {(sorted(r.methods)[0], r.path)
                  for r in router.routes if getattr(r, "path", None)}
        assert spec["tortoise_onboarding_state"] == ("GET",
                                                     "/v1/onboarding/state")
        assert spec["tortoise_onboarding_seed"] == ("POST",
                                                    "/v1/onboarding/seed")
        for key in ("tortoise_onboarding_state", "tortoise_onboarding_seed"):
            assert spec[key] in routes, (spec[key], sorted(routes))

    def test_rest_state_read_inits_then_reports_fork_self(
            self, monkeypatch, tmp_path):
        tc = _client(monkeypatch, tmp_path)
        with tc:
            r = tc.get("/v1/onboarding/state")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["node"] is True
            assert body["fork"] == "self"
            assert body["completed_steps"] == []

    def test_rest_seed_files_the_two_prompts(self, monkeypatch, tmp_path):
        tc = _client(monkeypatch, tmp_path)
        with tc:
            r = tc.post("/v1/onboarding/seed",
                        json={"org_name": "Acme", "person_name": "Ada"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "seeded"
            assert body["next"] == "decide-completed"
            state = tc.get("/v1/onboarding/state").json()
            assert set(state["completed_steps"]) == {
                "harness-connected", "first-points-filed", "team-named"}
            assert state["onboarding_complete"] is False

            # a missing prompt is a 422, never an invented identity
            bad = tc.post("/v1/onboarding/seed", json={"org_name": "Acme"})
            assert bad.status_code == 422

    def test_rest_unknown_field_is_rejected(self, monkeypatch, tmp_path):
        tc = _client(monkeypatch, tmp_path)
        with tc:
            r = tc.post("/v1/onboarding/seed",
                        json={"org_name": "Acme", "person_name": "Ada",
                              "email": "ada@example.com"})
            assert r.status_code == 422


class TestMcpTools:
    def test_state_tool_reads_the_flow_projection(self, db_env):
        from tortoise.mcp_server import tortoise_onboarding_state
        with _selfhost_transport_context():
            sh.seed_selfhost_anchors(_sdk(), org_name="Acme",
                                     person_name="Ada").get("status")
            state = tortoise_onboarding_state()
        assert state["fork"] == "self"
        assert "first-points-filed" in state["completed_steps"]
        assert "onboarding_complete" in state

    def test_seed_tool_asks_for_both_answers(self, db_env):
        from tortoise.mcp_server import tortoise_onboarding_seed
        with _selfhost_transport_context():
            gaps = tortoise_onboarding_seed()
            assert gaps["status"] == "needs_confirmation"
            assert sorted(gaps["gaps"]) == ["org_name", "person_name"]
            seeded = tortoise_onboarding_seed(org_name="Acme",
                                              person_name="Ada")
        assert seeded["status"] == "seeded"
        assert seeded["org_subject"]["name"] == "Acme"

    def test_create_point_decide_completes_without_ceremony(self, db_env):
        """The agent's documented decide protocol is an MCP write — the
        selfhost observation lane must complete onboarding from it, with no
        separate checkpoint call (#3784)."""
        from tortoise.mcp_server import (
            tortoise_create_point,
            tortoise_onboarding_seed,
            tortoise_onboarding_state,
        )
        with _selfhost_transport_context():
            assert tortoise_onboarding_seed(
                org_name="Acme", person_name="Ada")["status"] == "seeded"
            # a plain statement observes connection + a filed point only
            tortoise_create_point(kind="statement", content="setup prompt")
            assert tortoise_onboarding_state()["onboarding_complete"] is False
            # the decision-shaped write is the decide — the gate closes
            tortoise_create_point(kind="decision", content="We pick option A")
            state = tortoise_onboarding_state()
        assert state["onboarding_complete"] is True
        assert "decide-completed" in state["completed_steps"]


# ── 4. the capture controls are OBSERVABLE, not merely persisted ──

class TestOperationalObservability:
    """#8686: a setting the user cannot see is not a setting.

    The defect came in two halves, and BOTH had to be closed. The write was a
    bare ``MATCH ... SET`` on a node a self-hosted install never has — 0 rows
    matched, *without error*, over an echo that re-added the value it had just
    been asked to set. Once that landed, the state read STILL could not show
    the setting, because this projection was FLOW-only.

    These tests drive the real MCP-tool entry point and read the value back
    through the real reader, so neither half can silently re-open.
    """

    def test_the_recording_toggle_is_observable_through_the_state_read(self, db_env):
        from tortoise.hosted_api import _update_onboarding_state
        sdk = _sdk()
        try:
            sh.ensure_selfhost_onboarding(sdk)
        finally:
            sdk.close()

        # EXACTLY what mcp_server.tortoise_onboarding_session_recording calls.
        _update_onboarding_state(SELFHOST_ORG_ID, session_recording=False,
                                 capture_revised=True)

        sdk = _sdk()
        try:
            state = sh.read_selfhost_state(sdk)
        finally:
            sdk.close()
        assert state["operational"]["session_recording"] is False, (
            "the self-hosted state read cannot show the setting it just stored "
            "— that is the #8686 false ACK with a fresh coat of paint")

    def test_the_toggle_survives_an_unrelated_write(self, db_env):
        """The second half of #8686: the value must not revert. The CAS read
        leg had no selfhost branch, so it handed back the DEFAULT set
        (session_recording=True) and the next write merged it straight back
        over the stored False."""
        from tortoise.hosted_api import _update_onboarding_state
        sdk = _sdk()
        try:
            sh.ensure_selfhost_onboarding(sdk)
        finally:
            sdk.close()

        _update_onboarding_state(SELFHOST_ORG_ID, session_recording=False,
                                 capture_revised=True)
        _update_onboarding_state(SELFHOST_ORG_ID, demo_created=True)

        sdk = _sdk()
        try:
            state = sh.read_selfhost_state(sdk)
        finally:
            sdk.close()
        assert state["operational"]["session_recording"] is False, (
            "an unrelated write reverted the switch — the user is told OFF and "
            "recording resumed")
        assert state["operational"]["demo_created"] is True

    def test_an_untouched_deployment_reports_no_operational_keys(self, db_env):
        """An absent key means the documented DEFAULT applies. This layer must
        not invent one — the disclosure renderer owns the wording for 'unset',
        and a fabricated value here is how a reader starts lying."""
        sdk = _sdk()
        try:
            sh.ensure_selfhost_onboarding(sdk)
            state = sh.read_selfhost_state(sdk)
        finally:
            sdk.close()
        assert state["operational"] == {}

    def test_graph_down_is_unavailable_never_an_empty_dict(self):
        """`{}` reads as 'nothing is set'; the graph being DOWN is a different
        fact. Fabricating an empty dict would turn an outage into a silent
        'your settings are gone'."""

        class _DeadProj:
            def query(self, *_a, **_k):
                raise RuntimeError("graph down")

        class _DeadSdk:
            def _get_proj(self):
                return _DeadProj()

        state = sh.read_selfhost_state(_DeadSdk())
        assert state["operational"] == "unavailable"
