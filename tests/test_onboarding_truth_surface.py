"""#3670 / #3671 / #3681 — the onboarding TRUTH SURFACE (assertion ≠ observation).

One semantic, three sites: the onboarding "Connected" state and the
present-tense capture sentence must be made true by something the SERVER
observed, never by a client that observed nothing.

  1. #3671 — ``POST /v1/onboarding/state/checkpoint`` let ANY authenticated
     caller (including a session JWT) ASSERT a step. A step write now requires
     an AGENT credential (``tt_``/``tk_`` key, MCP/OAuth); a session JWT is
     refused (403).
  2. #3681 — the onboarding PATCH accepted ``session_capture_receipt_*`` (the
     receipt the capture sentence's tense depends on), and the capture derived
     the receipt key from the CLIENT-supplied ``body.harness``. The receipt
     keys are now server-owned, and the harness is resolved from the server's
     own record.
  3. #3670 — a REST-first / build-fork org could never file
     ``harness-connected`` (the signal was MCP-tool-only). An
     AGENT-credentialed REST write now files it; a session-JWT write does not.

EVIDENCE STANDARD (B3 cycle 9): a scan reports on a SPELLING, never a
BEHAVIOUR. These tests therefore EXTRACT the real route handlers from ``app``
and EXECUTE them over stubs. Every assertion is on

  (a) the request's ROUTER-RESOLVED path — ``_resolved_path`` resolves the
      path from the registered ``APIRoute`` (never a source-grepped string),
      and the handler identity is asserted; and
  (b) the value the state setter receives — the ``_os.write_completed_step``
      / ``_update_onboarding_state`` call payload, deep-equal to the expected
      claim.

Each part names the mutation that REDs it and the legitimate form that stays
GREEN in the test docstring.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault(
    "TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tortoise import hosted_api as ha
from tortoise.hosted_api import (
    app,
    get_current_org,
    get_current_org_session_ungated,
)


def _resolved_path(handler_name: str) -> str:
    """The router-resolved path for a handler.

    Spelling-independent: the path comes from the registered ``APIRoute``, so
    renaming the pytest URL string can never make the test exercise a route
    that does not exist — and if the handler is unregistered this fails loudly.
    """
    matches = [
        r for r in app.routes
        if isinstance(r, APIRoute)
        and getattr(r.endpoint, "__name__", "") == handler_name
    ]
    assert matches, f"no registered route for handler {handler_name!r}"
    # one canonical route per handler name on this surface
    assert len(matches) == 1, (
        f"handler {handler_name!r} registered {len(matches)} times: "
        f"{[r.path for r in matches]}"
    )
    assert matches[0].endpoint is getattr(ha, handler_name), (
        f"{matches[0].path} is not wired to hosted_api.{handler_name}"
    )
    return matches[0].path


# ── credential faces ─────────────────────────────────────────
# AGENT: a tt_ key (key_id, no session_user_id) — the credential the user's
#        agent runs with.
# SESSION: a session JWT (session_user_id + auth_lane marker) — the
#        dashboard / browser. The seam is the documented #2297/#2380
#        discriminator, which is what the server predicates on.
_AGENT = {
    "org_id": "org-truth", "tier": "free", "key_id": "k-truth",
    "legacy_full_access": True, "max_points": 100000,
}
_SESSION = {
    "org_id": "org-truth", "tier": "free",
    "session_user_id": "11111111-1111-1111-1111-111111111111",
    "auth_lane": "session",
}
# GRAPH-BOUND agent key: a machine credential scoped to ONE graph (C5 #2114).
_AGENT_GRAPH_SCOPED = {
    "org_id": "org-truth", "tier": "free", "key_id": "k-truth-graph",
    "graph_id": "g-truth",
    "legacy_full_access": True, "max_points": 100000,
}


def _set_dependency(credential: dict) -> None:
    app.dependency_overrides[get_current_org_session_ungated] = (
        lambda: dict(credential))


# ═══════════════════════════════════════════════════════════════════
# Part 1 — #3671: a checkpoint STEP write requires an agent credential
# ═══════════════════════════════════════════════════════════════════

class TestCheckpointStepRequiresAgentCredential:
    """RED mutation: allow a session-JWT call through to the step writer (drop
    the ``_credential_is_agent`` gate) → the session case gets 200 and the
    step setter is called → both assertions fail.
    GREEN mutation: use an agent credential (the legitimate form) → 200 and
    the setter receives exactly ``harness-connected``."""

    def _post_step(self, monkeypatch, credential, step="harness-connected"):
        steps: list[str] = []
        monkeypatch.setattr(ha, "_graph_available", lambda oid: True)
        monkeypatch.setattr(ha, "_org_proj", lambda oid: object())
        monkeypatch.setattr(ha, "_get_onboarding_state", lambda oid: {})
        monkeypatch.setattr(ha, "_get_onboarding_projection", lambda oid: {})
        monkeypatch.setattr(ha, "_maybe_apply_completion", lambda oid: False)

        def _writer(proj, oid, step, **kw):
            steps.append(step)
            return {"created": True}

        monkeypatch.setattr(ha._os, "write_completed_step", _writer)
        _set_dependency(credential)
        try:
            with TestClient(app) as tc:
                r = tc.post(_resolved_path("onboarding_checkpoint"),
                            json={"step": step})
        finally:
            app.dependency_overrides.clear()
        return r, steps

    def test_session_jwt_cannot_assert_a_step(self, monkeypatch):
        r, steps = self._post_step(monkeypatch, _SESSION)
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["message"] == "agent_credential_required"
        # the step setter received NOTHING — no partial write either
        assert steps == []

    def test_agent_credential_still_files_the_step(self, monkeypatch):
        r, steps = self._post_step(monkeypatch, _AGENT)
        assert r.status_code == 200, r.text
        assert steps == ["harness-connected"]

    def test_session_jwt_may_still_write_the_dashboard_catalog_step(
            self, monkeypatch):
        """``catalog-presented`` is exempt from the agent gate for ONE reason,
        and it is a LEGACY one: the production deploy is frozen at
        ``558c022c6`` (pre-#3913, held by #4471), and that deployed bundle
        still POSTs ``{"step": "catalog-presented"}`` with a session
        credential — so gating the step now would 403 the live dashboard.

        It is NOT a live dashboard surface: since #3913 (owner ruling
        2026-09-20) no dashboard path writes it (the B3 tripwire from #3704
        asserts exactly ONE checkpoint call site, the fork write, and no
        module serializes this step), and ``_GATE_BUILD`` no longer requires
        it. The exemption MUST be removed — emptying the allowlist — once the
        deploy carrying #3913 reaches production.

        RED mutation: gate ``body.step is not None`` (drop the
        ``_DASHBOARD_WRITABLE_STEPS`` exemption) → this session write returns
        403 and the step setter is never called → both assertions fail; the
        frozen live dashboard would 403 while the deploy freeze holds.
        GREEN: the frozen bundle's step stays session-writable."""
        r, steps = self._post_step(
            monkeypatch, _SESSION, step="catalog-presented")
        assert r.status_code == 200, r.text
        assert steps == ["catalog-presented"]

    def test_non_step_flow_op_keeps_its_session_lane(self, monkeypatch):
        """The dashboard's human answer (fork) is NOT a step observation — it
        must stay session-writable (scope pin: only step writes are gated)."""
        writes: list = []
        monkeypatch.setattr(ha, "_graph_available", lambda oid: True)
        monkeypatch.setattr(ha, "_org_proj", lambda oid: object())
        monkeypatch.setattr(ha, "_get_onboarding_state", lambda oid: {})
        monkeypatch.setattr(ha, "_get_onboarding_projection", lambda oid: {})
        monkeypatch.setattr(ha, "_maybe_apply_completion", lambda oid: False)
        monkeypatch.setattr(
            ha._os, "write_fork",
            lambda proj, oid, fork, **kw: (writes.append(fork), "ok")[1])
        monkeypatch.setattr(
            ha._os, "clear_fork_unsure_at", lambda proj, oid: None)
        _set_dependency(_SESSION)
        try:
            with TestClient(app) as tc:
                r = tc.post(_resolved_path("onboarding_checkpoint"),
                            json={"fork": "self"})
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200, r.text
        assert writes == ["self"]


# ═══════════════════════════════════════════════════════════════════
# Part 2a — #3681: the capture receipt is SERVER-OWNED on the PATCH surface
# ═══════════════════════════════════════════════════════════════════

class TestPatchRefusesFabricatedReceipt:
    """RED mutation: remove the capture keys from ``_PATCH_SERVER_OWNED_KEYS``
    → the PATCH is accepted (200) and the state setter receives the fabricated
    receipt → both assertions fail.
    GREEN: an operational key (the legitimate form) still writes."""

    @pytest.mark.parametrize("credential", [_AGENT, _SESSION],
                             ids=["agent", "session"])
    @pytest.mark.parametrize(
        ("field", "state_key"),
        [
            ("session_capture_receipt", "session_capture_receipt"),
            ("session_capture_receipt_claude", "session_capture_receipt_claude"),
            ("session_capture_receipt_cursor", "session_capture_receipt_cursor"),
            # underscore PATCH field → hyphenated STATE key (translated by the
            # handler before the ownership check) — pins the translation path
            ("session_capture_receipt_claude_desktop",
             "session_capture_receipt_claude-desktop"),
            ("session_capture_last_error_pi", "session_capture_last_error_pi"),
            ("install_probe_claude", "install_probe_claude"),
        ],
    )
    def test_server_owned_capture_keys_are_refused(
            self, monkeypatch, credential, field, state_key):
        """Ownership is lane-INDEPENDENT: both an agent key and a session JWT
        are refused (a session caller must not fabricate a receipt either —
        the session lane's own capture writes the BARE receipt, never a
        per-harness one). Parametrized over both lanes (#3671 review)."""
        seen: list[dict] = []
        monkeypatch.setattr(
            ha, "_update_onboarding_state",
            lambda oid, **kw: (seen.append(kw), {})[1])
        monkeypatch.setattr(ha, "_get_onboarding_projection", lambda oid: {})
        monkeypatch.setattr(ha, "_org_email", lambda oid: None)
        _set_dependency(credential)
        try:
            with TestClient(app) as tc:
                r = tc.patch(_resolved_path("patch_onboarding_state"),
                             json={field: "2026-01-01T00:00:00Z"})
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == {
            "message": "server_owned_key", "keys": [state_key]}
        assert seen == []  # the state setter received nothing

    def test_operational_key_still_writes(self, monkeypatch):
        seen: list[dict] = []
        monkeypatch.setattr(
            ha, "_update_onboarding_state",
            lambda oid, **kw: (seen.append(kw), {})[1])
        monkeypatch.setattr(ha, "_get_onboarding_projection", lambda oid: {})
        monkeypatch.setattr(ha, "_org_email", lambda oid: None)
        _set_dependency(_AGENT)
        try:
            with TestClient(app) as tc:
                r = tc.patch(_resolved_path("patch_onboarding_state"),
                             json={"prompt_pasted": True})
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200, r.text
        assert seen == [{"prompt_pasted": True}]


# ═══════════════════════════════════════════════════════════════════
# Part 2b — #3681: the receipt harness is SERVER-resolved
# ═══════════════════════════════════════════════════════════════════

_CAPTURE_TEAM = {
    "org_id": "team-truth", "tier": "free", "key_id": "k-capture",
    "legacy_full_access": True, "max_points": 100000,
    # #4010: the resolved-limits contract carries EVERY resource — sessions is
    # unlimited (explicit None), and a MISSING key is fail-closed.
    "max_sessions": None,
}
# A GRAPH-BOUND variant of the same agent key (C5 #2114): the Session it
# captures lives in its OWN graph (`graph_namespace`), never the org DEFAULT
# graph. Presents the P1 shape — a session that exists ONLY in the bound graph.
_CAPTURE_TEAM_GRAPH_SCOPED = {
    **_CAPTURE_TEAM, "key_id": "k-capture-bound",
    "graph_id": "g-truth", "graph_namespace": "org_truth_gtruth",
}
_CONV = [
    {"role": "user", "content": "We decided to ship serve --http first."},
    {"role": "assistant", "content": "Agreed, the config was the root cause."},
]


class TestCaptureReceiptHarnessIsServerResolved:
    """RED mutation: keep ``_capture_receipt_key(body.harness)`` → capture #2
    (a forged ``body.harness='cursor'`` replay of a claude session) writes
    ``session_capture_receipt_cursor`` → the 'no cursor receipt' assertion
    fails. Also: revert ``_observed_capture_harness`` to return ``claimed``
    for a session credential → the bare-receipt assertion fails.
    GREEN: the legitimate forms — a fresh agent capture names its own harness,
    and a re-capture keeps the server's recorded harness."""

    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        holder = {"org": dict(_CAPTURE_TEAM)}
        seen: list[dict] = []

        def _spy(oid, **kw):
            seen.append(dict(kw))
            return _real(oid, **kw)

        with patched_tortoise_sdk(str(tmp_path / "truth.db")):
            _real = ha._update_onboarding_state
            ha._make_sdk(namespace="registry")._get_registry().query(
                "CREATE (t:Team {id:$id, onboarding_state:$st})",
                params={"id": _CAPTURE_TEAM["org_id"], "st": "{}"})
            app.dependency_overrides[get_current_org] = (
                lambda: dict(holder["org"]))
            monkeypatch.setattr(ha, "_update_onboarding_state", _spy)
            try:
                with TestClient(app) as tc:
                    yield tc, holder, seen
            finally:
                # never leak the auth override into the next test (the
                # ordering-flake source the helpers above also close)
                app.dependency_overrides.clear()

    def _capture(self, tc, *, session_id, harness):
        return tc.post(_resolved_path("capture_session"),
                       json={"session_id": session_id, "harness": harness,
                             "conversation": _CONV})

    @staticmethod
    def _receipt_keys(seen: list[dict]) -> list[dict]:
        return [
            {k: v for k, v in c.items() if k.startswith("session_capture_receipt")}
            for c in seen
            if any(k.startswith("session_capture_receipt") for k in c)
        ]

    def test_forged_harness_cannot_relabel_an_existing_session(self, env):
        tc, _holder, seen = env
        r1 = self._capture(tc, session_id="S1", harness="claude")
        assert r1.status_code == 200, r1.text
        # forged replay: same session_id, a DIFFERENT harness
        r2 = self._capture(tc, session_id="S1", harness="cursor")
        assert r2.status_code == 200, r2.text

        # the pin cannot go vacuous: the forged replay DID write a second
        # receipt (so the key assertions below are exercised on both).
        receipts = self._receipt_keys(seen)
        assert len(receipts) == 2, (
            f"the forged replay wrote no second receipt: {receipts}")
        assert all(
            k == "session_capture_receipt_claude" for r in receipts for k in r
        ), f"a forged body.harness reached the receipt key: {receipts}"
        # the server's own stored harness is what the receipt names
        assert receipts[0] == {"session_capture_receipt_claude": receipts[0][
            "session_capture_receipt_claude"]}
        assert isinstance(receipts[0]["session_capture_receipt_claude"], str)
        assert receipts[0]["session_capture_receipt_claude"]
        # …and the Session node ITSELF still carries the original harness. The
        # relabel under test is the Session MERGE (`s.harness=$harness`), so
        # read it back directly: mutating only that line to the client value
        # keeps the receipt assertions green, and this is what REDs.
        stored = ha._make_sdk(
            namespace=_CAPTURE_TEAM["org_id"])._get_proj().g.query(
            "MATCH (s:Session {id:$sid}) RETURN s.harness AS harness",
            params={"sid": "S1"}).result_set
        assert stored and stored[0][0] == "claude", (
            f"the forged replay relabeled the Session's stored harness: {stored}")

    def test_fresh_agent_capture_names_its_own_harness(self, env):
        """The legitimate form: a FRESH session's agent credential declares its
        harness, and the receipt names it (the agent is the observation)."""
        tc, _holder, seen = env
        r = self._capture(tc, session_id="S-fresh", harness="cursor")
        assert r.status_code == 200, r.text
        assert self._receipt_keys(seen) == [
            {"session_capture_receipt_cursor": self._receipt_keys(seen)[0][
                "session_capture_receipt_cursor"]}]

    def test_session_credential_writes_only_the_bare_receipt(self, env):
        tc, holder, seen = env
        holder["org"] = dict(_CAPTURE_TEAM)
        holder["org"]["session_user_id"] = (
            "11111111-1111-1111-1111-111111111111")
        holder["org"]["auth_lane"] = "session"
        r = self._capture(tc, session_id="S-session", harness="cursor")
        assert r.status_code == 200, r.text
        receipts = self._receipt_keys(seen)
        assert receipts == [{"session_capture_receipt": receipts[0][
            "session_capture_receipt"]}], (
            f"a session (browser) capture named a harness: {receipts}")

    def test_error_path_reads_the_stored_harness_from_the_bound_graph(
            self, env):
        """#3681 review P1: the capture ERROR path must resolve the stored
        harness from the SAME graph the capture WRITES the Session to.

        A graph-bound key's Session lives in its OWN graph (C5 #2114), so a
        lookup against the org-DEFAULT graph misses it, the helper fails open,
        and the caller's ``body.harness`` wins — planting a caller-named
        ``session_capture_last_error_{claim}`` and re-opening the relabel hole
        #3681 closes (the existing new test only covers the points auto-file,
        not this error path).

        RED mutation: resolve via ``_org_proj(org["org_id"])`` (the org-DEFAULT
        graph) → the bound-graph Session is invisible → the last-error key is
        ``session_capture_last_error_cursor`` (the client's claim) → both
        assertions fail. GREEN: resolving via ``_data_sdk(org)`` (the same
        resolver the capture writes with) sees the bound graph's stored
        ``claude`` harness."""
        tc, holder, seen = env
        ns = "org_truth_gtruth"
        holder["org"] = dict(_CAPTURE_TEAM_GRAPH_SCOPED)
        # the key's graph is registered (the ownership pre-check reads it)
        ha._make_sdk(namespace="registry")._get_registry().query(
            "CREATE (g:Graph {id:'g-truth', org_id:'team-truth', "
            "namespace:$ns})", params={"ns": ns})
        # a Session that exists ONLY in the key's bound graph
        ha._make_sdk(graph_name=ns)._get_proj().g.query(
            "CREATE (s:Session {id:'S-bound', harness:'claude'})")
        # an empty conversation raises at the pre-write 422 gate — the ERROR
        # path, where the harness is resolved for the last-error key
        r = tc.post(_resolved_path("capture_session"),
                    json={"session_id": "S-bound", "harness": "cursor",
                          "conversation": []})
        assert r.status_code == 422, r.text
        error_keys = {k for c in seen for k in c
                      if k.startswith("session_capture_last_error_")}
        assert error_keys == {"session_capture_last_error_claude"}, (
            f"the bound graph's stored harness did not name the last-error "
            f"key: {sorted(error_keys)}")
        assert "session_capture_last_error_cursor" not in error_keys, (
            "the client's body.harness claimed the last-error key")


@pytest.mark.asyncio
async def test_stored_session_harness_owns_and_closes_its_data_sdk(
        monkeypatch):
    """#3681 review P2: the error-path lookup must (a) resolve through the
    data-plane tenancy resolver (``_data_sdk`` — the same graph the capture
    writes the Session to), never ``_org_proj``, and (b) own and CLOSE its SDK
    handle — ``_org_proj`` opened a fresh SDK per call and leaked the
    connection, and this runs on the async request path.

    RED mutation 1: resolve via ``_org_proj`` (monkeypatched here to answer
    from the org-DEFAULT graph) → the harness comes back ``"wrong-graph"`` →
    the harness assertion fails.
    RED mutation 2: drop the ``sdk.close()`` → ``closed`` stays empty → the
    leak assertion fails.
    GREEN: ``_data_sdk`` answers ``"claude"`` and the handle is closed."""
    from types import SimpleNamespace

    closed: list[bool] = []
    seen: list[tuple] = []

    def _proj(harness):
        def _query(cypher, params=None):
            seen.append((harness, params["sid"]))
            return SimpleNamespace(result_set=[[harness]])
        return SimpleNamespace(g=SimpleNamespace(query=_query))

    class _Sdk:
        def _get_proj(self):
            return _proj("claude")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(ha, "_data_sdk", lambda org: _Sdk())
    monkeypatch.setattr(ha, "_org_proj", lambda oid: _proj("wrong-graph"))
    assert await ha._stored_session_harness(
        {"org_id": "o"}, "S-bound") == "claude"
    assert seen == [("claude", "S-bound")]
    assert closed == [True], "the SDK handle was not closed (connection leak)"
    # no session id → no lookup, no SDK opened, no query run
    closed_before, seen_before = list(closed), list(seen)
    assert await ha._stored_session_harness({"org_id": "o"}, None) is None
    assert closed == closed_before, (
        "the no-session-id case opened (and closed) an SDK")
    assert seen == seen_before, "the no-session-id case ran a query"


# ═══════════════════════════════════════════════════════════════════
# Part 3 — #3670: an agent-credentialed REST write files harness-connected
# ═══════════════════════════════════════════════════════════════════

class TestAgentRestWriteFilesHarnessConnected:
    """RED mutation: drop the ``_credential_is_agent`` gate on the
    ``create_point`` auto-file → the session case files the step → the 'no
    step for a session write' assertion fails.
    GREEN: the agent-credentialed REST write (the legitimate form) files the
    step."""

    def _post_point(self, monkeypatch, credential):
        steps: list[str] = []
        monkeypatch.setattr(ha, "_check_org_limit", lambda org, res: None)
        monkeypatch.setattr(ha, "_graph_available", lambda oid: True)
        monkeypatch.setattr(ha, "_get_onboarding_state", lambda oid: {})
        monkeypatch.setattr(ha, "_org_proj", lambda oid: object())
        monkeypatch.setattr(ha, "_maybe_apply_completion", lambda oid: False)
        monkeypatch.setattr(ha, "_enqueue_dream", lambda *a, **k: None)
        monkeypatch.setattr(ha, "_record_write_op", lambda org: None)

        class _SdkHandle:
            """The auto-file now OWNS its SDK handle (review P2: `_org_proj`
            leaked a connection per write) instead of routing through the
            monkeypatched `_org_proj` — so the seam is `_make_sdk`."""

            def _get_proj(self):
                return object()

            def close(self):
                return None

        monkeypatch.setattr(ha, "_make_sdk", lambda **kw: _SdkHandle())

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(ha, "_async_audit", _noop)
        monkeypatch.setattr(ha, "_abuse_record_points", _noop)

        def _writer(proj, oid, step, **kw):
            steps.append(step)
            return {"created": True}

        monkeypatch.setattr(ha._os, "write_completed_step", _writer)

        class _Proj:
            def create_about_edge(self, *a, **k):
                return None

        class _Sdk:
            _dirty_roots: tuple = ()

            def create_point(self, **kw):
                return {"id": "p-truth", "content": kw["content"]}

            def _get_proj(self):
                return _Proj()

        monkeypatch.setattr(ha, "_data_sdk", lambda org: _Sdk())
        _set_dependency(credential)
        try:
            with TestClient(app) as tc:
                r = tc.post(_resolved_path("create_point"),
                            json={"content": "first memory", "kind": "statement"})
        finally:
            app.dependency_overrides.clear()
        return r, steps

    def test_agent_credentialed_rest_write_files_the_step(self, monkeypatch):
        r, steps = self._post_point(monkeypatch, _AGENT)
        assert r.status_code == 200, r.text
        assert steps == ["harness-connected"]

    def test_session_credentialed_rest_write_files_nothing(self, monkeypatch):
        """The dashboard's own first-party write must NOT manufacture a
        connection the user never made (#3670 design constraint)."""
        r, steps = self._post_point(monkeypatch, _SESSION)
        assert r.status_code == 200, r.text
        assert steps == []

    def test_graph_bound_agent_write_files_no_org_level_step(
            self, monkeypatch):
        """C5 #2114: a per-graph key must not write ORG-DEFAULT graph state.

        The auto-file goes through the org projection (`_org_proj` → the
        default graph), so a graph-bound credential firing it would be a
        cross-graph write — the class every sibling org-level surface rejects
        via `_reject_graph_bound_org_surface`. The point itself still lands.

        RED mutation: drop `and not org.get("graph_id")` → the graph-bound
        write files the org-level step → the `steps == []` assertion fails.

        This test is SELF-CONTAINED: `steps == []` alone is satisfied by ANY
        skip reason (a dead auto-file, a mis-wired credential), so the test
        (a) asserts the credential really is AGENT-lane — an `_credential_is_
        agent` skip cannot be what empties `steps` — and (b) carries its own
        positive control: the SAME credential minus `graph_id` DOES file the
        step. GREEN: the org-wide agent key still files it."""
        scoped = _AGENT_GRAPH_SCOPED
        # (a) the guard under test is the GRAPH-BOUND clause, not the agent
        # gate — this credential is agent-lane and carries a graph_id.
        assert ha._credential_is_agent(scoped) is True
        assert scoped["graph_id"] == "g-truth"
        # (b) positive control IN THIS TEST: the same agent-lane credential
        # WITHOUT graph_id files the step, so an empty `steps` below can only
        # be the graph-bound skip.
        org_wide = {k: v for k, v in scoped.items() if k != "graph_id"}
        assert "graph_id" not in org_wide
        r_wide, steps_wide = self._post_point(monkeypatch, org_wide)
        assert r_wide.status_code == 200, r_wide.text
        assert steps_wide == ["harness-connected"]
        # the graph-bound write files NO org-level step, but the point lands
        r, steps = self._post_point(monkeypatch, scoped)
        assert r.status_code == 200, r.text
        assert r.json()["id"] == "p-truth"
        assert steps == []


def test_install_probe_server_owned_keys_are_derived_from_the_registry():
    """#3681 review P2: the install-probe members of
    ``_CAPTURE_SERVER_OWNED_KEYS`` must be DERIVED from the registration
    table (``_ALLOWED_STATE_KEYS`` ← ``_ONBOARDING_DEFAULT_STATE``), not
    hand-listed.

    RED mutation: replace the derivation (``ha._capture_server_owned_keys``'s
    install-probe comprehension) with the literal pair
    ``{"install_probe_claude", "install_probe_pi"}`` → a THIRD registered probe
    does not become owned → the synthetic-probe assertion fails.

    Why the mutation NEEDS to be run through the derivation: comparing two
    filters of the SAME frozen registry (`owned` vs `probes`) is satisfied by
    the literal pair whenever the table happens to hold exactly those two
    probes — it cannot tell derivation from coincidence. Re-running the
    production derivation over an EXTENDED table is what makes it RED.

    GREEN: every registered probe is server-owned, and the live set is
    exactly the derivation over the live table."""
    probes = {k for k in ha._ALLOWED_STATE_KEYS
              if k.startswith("install_probe_")}
    assert probes, "no install probes registered — the derivation is vacuous"
    # the LIVE constant is the derivation's output over the live table
    assert ha._capture_server_owned_keys() == ha._CAPTURE_SERVER_OWNED_KEYS, (
        "_CAPTURE_SERVER_OWNED_KEYS is not the derivation over the live "
        "registration table")
    owned = {k for k in ha._CAPTURE_SERVER_OWNED_KEYS
             if k.startswith("install_probe_")}
    assert owned == probes, (
        "install-probe server-owned set drifted from the registration table: "
        f"registered={sorted(probes)} owned={sorted(owned)}")
    # DERIVATION, not coincidence: a third REGISTERED probe must become owned.
    synthetic = "install_probe_third"
    ha._ALLOWED_STATE_KEYS.add(synthetic)
    try:
        extended = ha._capture_server_owned_keys()
    finally:
        ha._ALLOWED_STATE_KEYS.discard(synthetic)
    assert synthetic in extended, (
        "a newly registered install probe did not become server-owned — the "
        "capture server-owned set is hand-listed, not derived")
    assert extended - ha._CAPTURE_SERVER_OWNED_KEYS == {synthetic}, (
        "the derivation changed more than the newly registered probe: "
        f"{sorted(extended - ha._CAPTURE_SERVER_OWNED_KEYS)}")
    assert probes <= ha._PATCH_SERVER_OWNED_KEYS
