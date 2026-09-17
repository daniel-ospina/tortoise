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
        """``catalog-presented`` is the ONE dashboard-owned step (W1/W8): the
        browser rendered the catalog, no agent can observe that, the B3
        tripwire (#3428/#2937) pins the dashboard's checkpoint body to exactly
        it, and ``_GATE_BUILD`` requires it.

        RED mutation: gate ``body.step is not None`` (drop the
        ``_DASHBOARD_WRITABLE_STEPS`` exemption) → this session write returns
        403 and the step setter is never called → both assertions fail; the
        build-fork completion path becomes unreachable, re-creating #3670.
        GREEN: the dashboard's own step stays session-writable."""
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
            self, monkeypatch, field, state_key):
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
            with TestClient(app) as tc:
                yield tc, holder, seen

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

        receipts = self._receipt_keys(seen)
        assert receipts, "no receipt was written at all"
        assert all(
            k == "session_capture_receipt_claude" for r in receipts for k in r
        ), f"a forged body.harness reached the receipt key: {receipts}"
        # the server's own stored harness is what the receipt names
        assert receipts[0] == {"session_capture_receipt_claude": receipts[0][
            "session_capture_receipt_claude"]}
        assert isinstance(receipts[0]["session_capture_receipt_claude"], str)
        assert receipts[0]["session_capture_receipt_claude"]

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
        GREEN: the org-wide agent key still files it (test above)."""
        r, steps = self._post_point(monkeypatch, _AGENT_GRAPH_SCOPED)
        assert r.status_code == 200, r.text
        assert steps == []


def test_install_probe_server_owned_keys_are_derived_from_the_registry():
    """#3681 review P2: the install-probe members of
    ``_CAPTURE_SERVER_OWNED_KEYS`` must be DERIVED from the registration
    table (``_ALLOWED_STATE_KEYS`` ← ``_ONBOARDING_DEFAULT_STATE``), not
    hand-listed.

    RED mutation: replace the derivation with the literal pair
    ``{"install_probe_claude", "install_probe_pi"}`` and register a third
    probe → the new probe is client-PATCHable evidence (the surface re-opens)
    → the bidirectional assertion fails.
    GREEN: every registered probe is server-owned, and no unregistered probe
    is claimed as server-owned."""
    probes = {k for k in ha._ALLOWED_STATE_KEYS
              if k.startswith("install_probe_")}
    assert probes, "no install probes registered — the derivation is vacuous"
    owned = {k for k in ha._CAPTURE_SERVER_OWNED_KEYS
             if k.startswith("install_probe_")}
    assert owned == probes, (
        "install-probe server-owned set drifted from the registration table: "
        f"registered={sorted(probes)} owned={sorted(owned)}")
    assert probes <= ha._PATCH_SERVER_OWNED_KEYS
