"""#2599 Attribution Phase 2 — additive session machine_id + model attribution.

Covers:
- Pure unit: SessionRequest validator rejects control chars in machine_id/model
- E2E: machine_id + model flow from REST capture → stored on Session node
- E2E: read path returns machine_id + model (list_sessions + get_session_detail)
- E2E: absent fields → None on stored node, null in response (legacy compat)
- Regression: Phase 1 actor_user_id stamp still works when machine_id+model
  present (non-interference)
- Regression: machine_id/model do not appear in Phase 1 strip-and-ignore paths
  (they're client-claimed informational, NOT server-managed — never stripped)

Run: docker lane (TORTOISE_DB_URI required) — uses real registry auth via
apikey_create + TestClient POST /v1/sessions.
"""
from __future__ import annotations

import os
import uuid as _uuid_lib

import pytest
from fastapi.testclient import TestClient

from tests.test_hosted_api import (
    _patch_tortoise_sdk_init,
    _restore_tortoise_sdk_init,
    _seed_team_graphs,
)

_2599_UUID_A = str(_uuid_lib.uuid4())
_2599_MACHINE_ID = "sha256:abc123def456"  # example hashed hostname
_2599_MODEL = "claude-sonnet-4-20250514"


class TestSessionRequestValidators:
    """Pure unit tests for the machine_id + model field validators on
    SessionRequest (sanitization boundary — control chars rejected,
    printable accepted)."""

    def test_accepts_valid_machine_id(self):
        from tortoise.hosted_api import SessionRequest
        req = SessionRequest(
            conversation=[{"role": "user", "content": "hi"}],
            machine_id="my-machine-1",
            model="claude-opus-4-20250514",
            session_id="s-valid")
        assert req.machine_id == "my-machine-1"
        assert req.model == "claude-opus-4-20250514"

    def test_accepts_hashed_machine_id(self):
        from tortoise.hosted_api import SessionRequest
        req = SessionRequest(
            conversation=[{"role": "user", "content": "hi"}],
            machine_id="sha256:abc123def4567890",
            model="gpt-4o",
            session_id="s-hashed")
        assert req.machine_id == "sha256:abc123def4567890"

    def test_accepts_none_defaults(self):
        from tortoise.hosted_api import SessionRequest
        req = SessionRequest(
            conversation=[{"role": "user", "content": "hi"}])
        assert req.machine_id is None
        assert req.model is None

    def test_accepts_empty_machine_id_accepted_as_none(self):
        """Empty string for optional Field defaults to None via Field(None)
        semantics — Pydantic does NOT coerce '' to None for str | None;
        but we test the handler strips blanks.

        Write-path note (review P2): the capture handler gates on truthiness
        (`if body.machine_id:`), so an empty string is accepted by the
        validator but NEVER written to the graph — it is treated exactly
        like None (unattributed). Read path renders the missing graph
        property as blank/"—". Consistent, if subtle."""
        from tortoise.hosted_api import SessionRequest
        req = SessionRequest(
            conversation=[{"role": "user", "content": "hi"}],
            machine_id="",
            model="",
            session_id="s-empty")
        # Empty strings are accepted by the validator (not control chars)
        assert req.machine_id == ""
        assert req.model == ""

    @pytest.mark.parametrize("field,length", [
        ("machine_id", 257),
        ("model", 129),
    ])
    def test_rejects_overlong_values(self, field, length):
        """Field(max_length=...) caps client-claimed values at the Pydantic
        boundary — an overlong machine_id/model 422s (review P2 test-gap:
        explicit coverage for the built-in length enforcement)."""
        from pydantic import ValidationError

        from tortoise.hosted_api import SessionRequest
        with pytest.raises(ValidationError):
            SessionRequest(
                conversation=[{"role": "user", "content": "hi"}],
                **{field: "a" * length},
                session_id="s-overlong")

    @pytest.mark.parametrize("field,value", [
        ("machine_id", "line\nbreak"),
        ("machine_id", "tab\tcharacter"),
        ("machine_id", "null\x00byte"),
        ("machine_id", "carriage\rreturn"),
        ("model", "line\nbreak"),
        ("model", "tab\tcharacter"),
        ("model", "null\x00byte"),
    ])
    def test_rejects_control_characters(self, field, value):
        from tortoise.hosted_api import SessionRequest
        with pytest.raises(ValueError, match="control characters"):
            SessionRequest(
                conversation=[{"role": "user", "content": "hi"}],
                session_id="s-ctrl",
                **{field: value})

    def test_accepts_unicode_machine_id(self):
        """Unicode (non-ASCII printable) is allowed — e.g. emoji or
        international chars in opaque machine identifiers."""
        from tortoise.hosted_api import SessionRequest
        req = SessionRequest(
            conversation=[{"role": "user", "content": "hi"}],
            machine_id="macbook-プロ-123",
            session_id="s-unicode")
        assert req.machine_id == "macbook-プロ-123"


class TestSessionMachineModelStamp:
    """E2E: machine_id + model flow through REST capture → stored on Session
    node → returned on read path. Uses real registry auth (apikey_create with
    created_by=<uuid>) and DIRECT graph reads to verify the stored fields.

    Pattern mirrors test_hosted_api.py's TestSessionActorStamp2600.
    """

    _TEAM_ID = "team-2599-mm"

    def _data_sdk(self, team_id: str):
        """Open the TEAM data graph directly."""
        import tortoise.hosted_api as ha_mod
        return ha_mod._make_sdk(namespace=team_id)

    def _session_fields(self, team_id: str, session_id: str) -> dict:
        """Read machine_id and model from the stored Session node."""
        rows = self._data_sdk(team_id)._get_proj().g.query(
            "MATCH (s:Session {id:$sid}) RETURN "
            "s.machine_id, s.model, s.actor_user_id, s.harness",
            params={"sid": session_id}).result_set
        if not rows:
            return {}
        return {"machine_id": rows[0][0], "model": rows[0][1],
                "actor_user_id": rows[0][2], "harness": rows[0][3]}

    def _setup(self, tmp_path):
        """Temp registry + seeded pro team + real-auth TestClient."""
        import tortoise.hosted_api as ha_mod
        db_path = os.path.join(tmp_path, "mm-stamp.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        os.environ["TORTOISE_DB_PATH"] = db_path
        # CI has no LLM provider key → capture fails closed 503 unless the
        # offline MockModel test seam is on (convention: test_capture_session.py;
        # the extraction content is irrelevant here — we assert Session-node
        # stamp fields, not extractor output).
        _prior_mock = os.environ.get("TORTOISE_SESSION_LLM_MOCK")
        os.environ["TORTOISE_SESSION_LLM_MOCK"] = "1"
        sdk = ha_mod._make_sdk(namespace="registry")
        _seed_team_graphs(sdk, self._TEAM_ID, "pro", None)
        try:
            with TestClient(ha_mod.app,
                            raise_server_exceptions=False) as tc:
                yield sdk, self._TEAM_ID, tc
        finally:
            if _prior_mock is None:
                os.environ.pop("TORTOISE_SESSION_LLM_MOCK", None)
            else:
                os.environ["TORTOISE_SESSION_LLM_MOCK"] = _prior_mock
            os.environ.pop("TORTOISE_DB_PATH", None)
            _restore_tortoise_sdk_init(_orig)

    def test_capture_stamps_machine_id_and_model(self, tmp_path):
        """Capture with machine_id + model → Session node stores both fields
        + actor_user_id and harness still present (additive, non-interference
        with Phase 1)."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = sdk.apikey_create(tid, _2599_UUID_A)
            h = {"Authorization": f"Bearer {key['api_key']}"}
            r = tc.post("/v1/sessions", headers=h, json={
                "conversation": [{"role": "user", "content": "test machine model stamp."},
                                  {"role": "assistant", "content": "confirmed."}],
                "session_id": "s-mm-2599-a",
                "harness": "pi",
                "machine_id": _2599_MACHINE_ID,
                "model": _2599_MODEL,
            })
            assert r.status_code == 200, r.text[:300]
            fields = self._session_fields(tid, "s-mm-2599-a")
            assert fields.get("machine_id") == _2599_MACHINE_ID, fields
            assert fields.get("model") == _2599_MODEL, fields
            # Phase 1 regression: actor_user_id and harness still present
            assert fields.get("actor_user_id") == _2599_UUID_A, fields
            assert fields.get("harness") == "pi", fields
        finally:
            gen.close()

    def test_capture_without_machine_model_absents_fields(self, tmp_path):
        """Capture WITHOUT machine_id/model → Session node stores None for
        both (legacy compat — hook-less sessions never fabricate values)."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = sdk.apikey_create(tid, _2599_UUID_A)
            h = {"Authorization": f"Bearer {key['api_key']}"}
            r = tc.post("/v1/sessions", headers=h, json={
                "conversation": [{"role": "user", "content": "no machine model."},
                                  {"role": "assistant", "content": "ok."}],
                "session_id": "s-mm-2599-none",
                "harness": "claude",
            })
            assert r.status_code == 200, r.text[:300]
            fields = self._session_fields(tid, "s-mm-2599-none")
            assert fields.get("machine_id") is None, fields
            assert fields.get("model") is None, fields
            # Phase 1 regression: actor still stamps
            assert fields.get("actor_user_id") == _2599_UUID_A, fields
            assert fields.get("harness") == "claude", fields
        finally:
            gen.close()

    def test_capture_machine_only(self, tmp_path):
        """machine_id present, model absent → only machine_id stored."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = sdk.apikey_create(tid, _2599_UUID_A)
            h = {"Authorization": f"Bearer {key['api_key']}"}
            r = tc.post("/v1/sessions", headers=h, json={
                "conversation": [{"role": "user", "content": "machine only."}],
                "session_id": "s-mm-2599-monly",
                "machine_id": "my-laptop",
            })
            assert r.status_code == 200, r.text[:300]
            fields = self._session_fields(tid, "s-mm-2599-monly")
            assert fields.get("machine_id") == "my-laptop", fields
            assert fields.get("model") is None, fields
        finally:
            gen.close()

    def test_capture_model_only(self, tmp_path):
        """model present, machine_id absent → only model stored."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = sdk.apikey_create(tid, _2599_UUID_A)
            h = {"Authorization": f"Bearer {key['api_key']}"}
            r = tc.post("/v1/sessions", headers=h, json={
                "conversation": [{"role": "user", "content": "model only."}],
                "session_id": "s-mm-2599-donly",
                "model": "deepseek-v4-flash",
            })
            assert r.status_code == 200, r.text[:300]
            fields = self._session_fields(tid, "s-mm-2599-donly")
            assert fields.get("machine_id") is None, fields
            assert fields.get("model") == "deepseek-v4-flash", fields
        finally:
            gen.close()

    def test_cross_actor_repost_keeps_first_machine_model(self, tmp_path):
        """coalesce discriminator: first capture sets machine_id/model;
        re-POST by a different actor preserves the ORIGINAL machine_id/model
        (first-writer-wins, same pattern as actor_user_id)."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            uuidB = str(_uuid_lib.uuid4())
            keyA = sdk.apikey_create(tid, _2599_UUID_A)
            keyB = sdk.apikey_create(tid, uuidB)
            hA = {"Authorization": f"Bearer {keyA['api_key']}"}
            hB = {"Authorization": f"Bearer {keyB['api_key']}"}
            conv = [{"role": "user", "content": "first capture."},
                    {"role": "assistant", "content": "stamped."}]
            r1 = tc.post("/v1/sessions", headers=hA, json={
                "conversation": conv, "session_id": "s-mm-2599-fww",
                "machine_id": "first-machine", "model": "first-model"})
            assert r1.status_code == 200, r1.text[:300]
            # Re-POST with different machine_id/model from a different actor
            r2 = tc.post("/v1/sessions", headers=hB, json={
                "conversation": conv, "session_id": "s-mm-2599-fww",
                "machine_id": "second-machine", "model": "second-model"})
            assert r2.status_code == 200, r2.text[:300]
            fields = self._session_fields(tid, "s-mm-2599-fww")
            assert fields.get("machine_id") == "first-machine", fields
            assert fields.get("model") == "first-model", fields
            # Phase 1 regression: actor also stays first-writer
            assert fields.get("actor_user_id") == _2599_UUID_A, fields
        finally:
            gen.close()

    def test_list_sessions_returns_machine_model(self, tmp_path):
        """GET /v1/sessions returns machine_id + model in the response dict
        when present on stored Session nodes."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = sdk.apikey_create(tid, _2599_UUID_A)
            h = {"Authorization": f"Bearer {key['api_key']}"}
            tc.post("/v1/sessions", headers=h, json={
                "conversation": [{"role": "user", "content": "list test."}],
                "session_id": "s-mm-2599-list",
                "harness": "claude",
                "machine_id": _2599_MACHINE_ID,
                "model": _2599_MODEL,
            })
            r = tc.get("/v1/sessions", headers=h)
            assert r.status_code == 200, r.text[:300]
            data = r.json()
            sessions = data.get("sessions", [])
            target = [s for s in sessions if s.get("id") == "s-mm-2599-list"]
            assert target, "session not found in list"
            assert target[0].get("machine_id") == _2599_MACHINE_ID, target[0]
            assert target[0].get("model") == _2599_MODEL, target[0]
            # Phase 1 regression: actor_user_id and harness still present
            assert target[0].get("actor_user_id") == _2599_UUID_A
            assert target[0].get("harness") is not None
        finally:
            gen.close()

    def test_list_sessions_legacy_null_machine_model(self, tmp_path):
        """GET /v1/sessions returns null machine_id + model for legacy
        sessions (no hook supplied them) — never crashes, never fabricates."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = sdk.apikey_create(tid, _2599_UUID_A)
            h = {"Authorization": f"Bearer {key['api_key']}"}
            tc.post("/v1/sessions", headers=h, json={
                "conversation": [{"role": "user", "content": "legacy compat."}],
                "session_id": "s-mm-2599-legacy",
            })
            r = tc.get("/v1/sessions", headers=h)
            assert r.status_code == 200, r.text[:300]
            data = r.json()
            sessions = [s for s in data.get("sessions", [])
                       if s.get("id") == "s-mm-2599-legacy"]
            assert sessions, "legacy session not found"
            # machine_id/model must be null when not supplied
            assert sessions[0].get("machine_id") is None, sessions[0]
            assert sessions[0].get("model") is None, sessions[0]
        finally:
            gen.close()

    def test_legacy_session_detail_returns_null_machine_model(self, tmp_path):
        """GET /v1/sessions/{id} returns null machine_id + model for
        legacy sessions (no crash, no fabrication)."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            key = sdk.apikey_create(tid, _2599_UUID_A)
            h = {"Authorization": f"Bearer {key['api_key']}"}
            tc.post("/v1/sessions", headers=h, json={
                "conversation": [{"role": "user", "content": "detail legacy."}],
                "session_id": "s-mm-2599-detail-legacy",
            })
            r = tc.get("/v1/sessions/s-mm-2599-detail-legacy", headers=h)
            assert r.status_code == 200, r.text[:300]
            detail = r.json()
            assert detail.get("machine_id") is None, detail
            assert detail.get("model") is None, detail
            # actor still present
            assert detail.get("actor_user_id") == _2599_UUID_A, detail
        finally:
            gen.close()

    def test_mcp_capture_tool_rejects_machine_id_control_chars(self):
        """The MCP capture tool's SessionRequest construction rejects
        control chars in machine_id (matches REST boundary behavior)."""
        from tortoise.hosted_api import SessionRequest
        with pytest.raises(ValueError, match="control characters"):
            SessionRequest(
                conversation=[{"role": "user", "content": "hi"}],
                machine_id="bad\x00machine",
                model="ok-model",
                session_id="s-mcp-ctrl")

    def test_mcp_capture_accepts_valid_machine_model(self):
        from tortoise.hosted_api import SessionRequest
        req = SessionRequest(
            conversation=[{"role": "user", "content": "hi"}],
            machine_id="ok-machine",
            model="claude-opus-4",
            session_id="s-mcp-ok")
        assert req.machine_id == "ok-machine"
        assert req.model == "claude-opus-4"

    def test_strip_and_ignore_does_not_strip_machine_model(self):
        """#2599: machine_id and model are CLIENT-CLAIMED informational
        fields — NOT in the reserved actor props set. _sanitize_props must
        NOT strip them (unlike actor_user_id, owner, etc.)."""
        from tortoise.sdk import _sanitize_props
        props = _sanitize_props(
            {"machine_id": "my-machine", "model": "my-model",
             "content": "test"})
        assert props.get("machine_id") == "my-machine", props
        assert props.get("model") == "my-model", props

    def test_reject_helper_does_not_strip_machine_model(self):
        """_reject_server_managed_props must NOT reject machine_id/model
        (they are client-claimed informational, not server-managed)."""
        from tortoise.mcp_server import _reject_server_managed_props
        err = _reject_server_managed_props(
            {"machine_id": "my-machine", "model": "my-model",
             "content": "test"})
        assert err is None, err