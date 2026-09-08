"""#2600 Attribution Phase 1 — Task 1 unit tests: resolver actor threading.

Covers the pure units (``_is_uuid_shape`` / ``_alias_actor_user_id``), the
resolver RAW-actor returns (oauth ``user_id``/``client_id``; registry
``apikey_verify`` ``created_by``), and the UUID-shape matrix that decides
whether a ``created_by``/``user_id`` becomes a canonical ``actor_user_id``.
The MCP middleware / REST DI ContextVar behavior is exercised by the
auth-mode suites (test_mcp_server_auth_modes.py) + the docker-lane E2E tests
(Task 6); this module pins the pure logic + resolver return contracts.

Run: embedded lane (URI-less) or docker lane — no graph needed for the pure
units; apikey_verify uses an embedded registry graph (TORTOISE_DB_PATH).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

from tortoise.sdk import _alias_actor_user_id, _is_uuid_shape

UUID_A = "550e8400-e29b-41d4-a716-446655440000"


class TestIsUuidShape:
    """UUID gate matrix — must match supabase_control._is_uuid acceptance
    (#1738 class): brace-strip, reject urn:/uuid: prefixes, accept
    hyphenated / 32-hex-no-hyphen / braced. Non-UUID shapes (the "api",
    'st_'||hash, EMAIL production creators) must all FAIL so they can never
    fabricate an actor."""

    @pytest.mark.parametrize("value", [
        "550e8400-e29b-41d4-a716-446655440000",          # hyphenated
        "550e8400e29b41d4a716446655440000",              # 32-hex no hyphen
        "{550e8400-e29b-41d4-a716-446655440000}",        # braced
        "550E8400-E29B-41D4-A716-446655440000",          # uppercase
    ])
    def test_accepts_uuid_shapes(self, value):
        assert _is_uuid_shape(value) is True

    @pytest.mark.parametrize("value", [
        "urn:uuid:550e8400-e29b-41d4-a716-446655440000",
        "uuid:550e8400-e29b-41d4-a716-446655440000",
        "{urn:uuid:550e8400-e29b-41d4-a716-446655440000}",
        "api",           # API-minted creator (hosted_api.py:5382)
        "st_abc123",     # recovery-mint creator (supabase_control.py:2027)
        "member@example.com",  # registry self-signup EMAIL creator
        "", "not-a-uuid", "550e8400-e29b-41d4", "   ",
    ])
    def test_rejects_non_uuid_shapes(self, value):
        assert _is_uuid_shape(value) is False

    def test_rejects_none_and_non_str(self):
        assert _is_uuid_shape(None) is False
        assert _is_uuid_shape(42) is False
        assert _is_uuid_shape(["550e8400-e29b-41d4-a716-446655440000"]) is False


class TestAliasActorUserId:
    """Canonical actor_user_id key — UUID-gated, additive, never fabricated."""

    def test_aliases_user_id(self):
        team = {"team_id": "t1", "user_id": UUID_A, "client_id": "c1"}
        out = _alias_actor_user_id(team)
        assert out["actor_user_id"] == UUID_A
        # additive — pre-existing keys untouched
        assert out["team_id"] == "t1" and out["client_id"] == "c1"

    def test_aliases_created_by(self):
        team = {"team_id": "t1", "created_by": UUID_A}
        assert _alias_actor_user_id(team)["actor_user_id"] == UUID_A

    def test_aliases_session_user_id(self):
        # Session-lane dict shape: user["user_id"] attached as session_user_id
        team = {"team_id": "t1", "session_user_id": UUID_A, "auth_lane": "session"}
        assert _alias_actor_user_id(team)["actor_user_id"] == UUID_A

    def test_never_aliases_non_uuid_shapes(self):
        for raw in ("api", "st_abc123", "member@example.com",
                    "urn:uuid:" + UUID_A):
            team = {"created_by": raw}
            out = _alias_actor_user_id(team)
            assert "actor_user_id" not in out

    def test_no_raw_field_leaves_no_actor(self):
        team = {"team_id": "t1"}
        out = _alias_actor_user_id(team)
        assert "actor_user_id" not in out
        assert out["team_id"] == "t1"

    def test_never_removes_existing_actor(self):
        team = {"team_id": "t1", "actor_user_id": "kept",
                "created_by": "api"}
        out = _alias_actor_user_id(team)
        assert out["actor_user_id"] == "kept"


class TestOauthResolverRawActor:
    """resolve_oauth_access_token returns the RAW user_id/client_id (additive
    — indicator 1: the OAuth lane stops dropping the human)."""

    def test_resolved_dict_carries_raw_user_id(self, monkeypatch):
        from datetime import UTC, datetime, timedelta
        import uuid as _uuid

        from tests.fake_control_plane import FakeControlPlane

        user_uuid = str(_uuid.uuid4())
        cp = FakeControlPlane({
            "teams": [{"id": "team-oat", "name": "oat-team", "tier": "free"}],
            "oauth_access_tokens": [{
                "token_hash": "x" * 64,
                "client_id": "client-1",
                "user_id": user_uuid,
                "team_id": "team-oat",
                "scope": "read",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1))
                .isoformat(),
                "revoked_at": None,
            }],
        })
        # token_hash is SHA-256(plaintext) — seed the row above with the hash
        # of the token we present.
        from tortoise.oauth import _sha256, resolve_oauth_access_token
        token = "oat_testtoken123"
        cp.tables["oauth_access_tokens"][0]["token_hash"] = _sha256(token)
        team = resolve_oauth_access_token(cp, token)
        assert team is not None
        assert team["user_id"] == user_uuid
        assert team["client_id"] == "client-1"
        # additive — the quota/team shape is still present
        assert team["team_id"] == "team-oat"
        assert "tier" in team

    def test_revoked_token_returns_none(self, monkeypatch):
        from datetime import UTC, datetime, timedelta
        from tests.fake_control_plane import FakeControlPlane
        from tortoise.oauth import _sha256, resolve_oauth_access_token

        cp = FakeControlPlane({
            "teams": [{"id": "team-oat", "name": "oat-team", "tier": "free"}],
            "oauth_access_tokens": [{
                "token_hash": _sha256("oat_revokedtoken"),
                "client_id": "client-1",
                "user_id": "550e8400-e29b-41d4-a716-446655440000",
                "team_id": "team-oat",
                "scope": "read",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1))
                .isoformat(),
                "revoked_at": datetime.now(UTC).isoformat(),
            }],
        })
        assert resolve_oauth_access_token(cp, "oat_revokedtoken") is None


class TestRegistryApikeyVerifyRawCreatedBy:
    """sdk.apikey_verify returns the RAW created_by from the APIKey node —
    the registry MCP lane was the actor-less gap (indicator 1)."""

    def test_apikey_verify_returns_raw_created_by(self, tmp_path, monkeypatch):
        import uuid as _uuid

        from tortoise.sdk import TortoiseSDK

        db_path = str(tmp_path / "apikey-attribution.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        sdk = TortoiseSDK(db_path=db_path, namespace="registry")
        team = sdk.team_create("attribution-team")
        creator = str(_uuid.uuid4())
        mint = sdk.apikey_create(team["id"], created_by=creator)
        key = mint["api_key"]
        resolved = sdk.apikey_verify(key)
        assert resolved is not None
        assert resolved["created_by"] == creator
        # additive — pre-existing return keys untouched
        assert resolved["team_id"] == team["id"]
        assert "delegation_depth" in resolved
        assert "scopes" in resolved

    def test_apikey_verify_legacy_key_created_by_none(self, tmp_path, monkeypatch):
        # A mint with no creator → raw created_by absent → None (the seam's
        # UUID gate then leaves actor_user_id absent → unattributed).
        from tortoise.sdk import TortoiseSDK

        db_path = str(tmp_path / "apikey-legacy.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        sdk = TortoiseSDK(db_path=db_path, namespace="registry")
        team = sdk.team_create("attribution-team-legacy")
        # created_via="provisioned", no creator uuid rides the node when the
        # caller does not pass created_by (the "api" class).
        key = sdk.apikey_create(team["id"], "api")["api_key"]
        resolved = sdk.apikey_verify(key)
        assert resolved is not None
        assert resolved.get("created_by") in (None, "api")


# ═══════════════════════════════════════════════════════════════════════════
# #2600 Phase 1 Task 3 — _emit_event journaled-event actor backstop + the
# EventAPI._emit optional actor. The :GraphEvent store node payload (JSON
# string on e.payload) is the backstop record for who did a write; this
# merges the ContextVar actor at EMISSION (copy-first — a caller-reused
# payload dict never gains the key).
UUID_B = "660e8400-e29b-41d4-a716-446655440001"


class TestEmitEventActorBackstop:
    """_emit_event graph-event payload actor merge (ContextVar) + copy-first
    no-mutation pin + JSONL envelope parity."""

    def _graph_payloads(self, sdk, type_: str):
        rows = sdk._get_proj().g.query(
            "MATCH (e:GraphEvent {type:$t}) RETURN e.payload ORDER BY e.seq",
            params={"t": type_}).result_set
        import json
        return [json.loads(r[0]) for r in rows]

    def test_graph_event_payload_carries_actor_when_set(self, tmp_path):
        """ContextVar set → the :GraphEvent node payload gains actor_user_id
        (additive merge at emission)."""
        from tortoise.sdk import TortoiseSDK, _current_actor_user_id
        sdk = TortoiseSDK(db_path=str(tmp_path / "emit.db"))
        try:
            tok = _current_actor_user_id.set(UUID_B)
            try:
                sdk._emit_event("PointAdded", {"id": "pt-1", "content": "x"})
            finally:
                _current_actor_user_id.reset(tok)
            payloads = self._graph_payloads(sdk, "PointAdded")
            assert payloads and payloads[0]["actor_user_id"] == UUID_B, payloads
            assert payloads[0]["id"] == "pt-1", payloads
        finally:
            sdk.close()

    def test_graph_event_no_actor_when_var_unset(self, tmp_path):
        """ContextVar unset (embedded/self-host lane) → payload has NO
        actor_user_id key (byte-identical legacy shape — assert key
        absence, not None-value)."""
        from tortoise.sdk import TortoiseSDK, _current_actor_user_id
        sdk = TortoiseSDK(db_path=str(tmp_path / "emit-none.db"))
        try:
            _current_actor_user_id.set(None)  # noqa: F841 — explicit reset below
            sdk._emit_event("OperatorAdded", {"id": "op-1"})
            payloads = self._graph_payloads(sdk, "OperatorAdded")
            assert payloads and "actor_user_id" not in payloads[0], payloads
        finally:
            sdk.close()

    def test_caller_reused_payload_never_mutated(self, tmp_path):
        """Copy-first pin: the same payload dict passed to two emissions
        NEVER gains actor_user_id on the caller's object (the merge writes
        the COPY)."""
        from tortoise.sdk import TortoiseSDK, _current_actor_user_id
        sdk = TortoiseSDK(db_path=str(tmp_path / "emit-copy.db"))
        try:
            shared = {"id": "pt-shared"}
            tok = _current_actor_user_id.set(UUID_B)
            try:
                sdk._emit_event("PointAdded", shared)
                sdk._emit_event("PointAdded", shared)
            finally:
                _current_actor_user_id.reset(tok)
            assert "actor_user_id" not in shared, \
                "caller payload dict must never gain the actor key"
            payloads = self._graph_payloads(sdk, "PointAdded")
            assert len(payloads) == 2
            assert all(p.get("actor_user_id") == UUID_B for p in payloads)
        finally:
            sdk.close()

    def test_jsonl_envelope_carries_actor(self, tmp_path):
        """SDK with event_log_path + ContextVar set → the JSONL event line
        carries actor_user_id (additive); the underlying point snapshot is
        untouched."""
        import json
        from tortoise.log import EventLog
        from tortoise.sdk import TortoiseSDK, _current_actor_user_id
        events = tmp_path / "ev"
        events.mkdir()
        log_path = events / "emit.jsonl"
        sdk = TortoiseSDK(db_path=str(tmp_path / "emit-log.db"),
                          event_log_path=str(log_path))
        try:
            tok = _current_actor_user_id.set(UUID_B)
            try:
                pt = sdk.create_point("statement", "emit actor journal line",
                                      id="pt-emit-log", is_episodic=False)
            finally:
                _current_actor_user_id.reset(tok)
            assert pt.get("id") == "pt-emit-log"
            lines = EventLog(log_path).read_all()
            # the create_point journals a PointAdded snapshot event
            adds = [e for e in lines if e.get("type") == "PointAdded"]
            assert adds, "PointAdded must journal to the JSONL log"
            env = adds[0]
            assert env.get("actor_user_id") == UUID_B, env
            # the point snapshot itself keeps only point fields (actor rides
            # the envelope, never the stored point)
            assert "actor_user_id" not in env["point"], env
        finally:
            sdk.close()


class TestEventApiEmitOptionalActor:
    """EventAPI._emit actor keyword — default None byte-identical; explicit
    actor present on the emitted event dict."""

    def test_default_none_no_actor_key(self):
        from tortoise.api import EventAPI
        from tortoise.log import EventLog
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(Path(d) / "api.jsonl")
            api = EventAPI(log, initiated_by="extractor")
            ev = api._emit("PointAdded", id="p1")
            assert ev.get("actor_user_id") is None
            assert "actor_user_id" not in ev, ev

    def test_explicit_actor_present(self):
        from tortoise.api import EventAPI
        from tortoise.log import EventLog
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(Path(d) / "api.jsonl")
            api = EventAPI(log, initiated_by="extractor")
            ev = api._emit("PointAdded", id="p2", actor=UUID_B)
            assert ev["actor_user_id"] == UUID_B, ev
            # journaled copy carries it too
            readback = log.read_all()
            assert readback and readback[-1].get("actor_user_id") == UUID_B


# ═══════════════════════════════════════════════════════════════════════════
# #2600 Phase 1 Task 4 — forged-claim STRIP-AND-IGNORE (never reject). The
# server owns the actor: a client-supplied actor claim is popped with a
# warning (SDK _sanitize_props backstop) and popped at the MCP boundary
# (_reject_server_managed_props choke point) — never stored, never a 4xx.
# authoredBy is NOT stripped (pre-existing client author-label residual).

class TestStripAndIgnoreActorClaims:
    """_sanitize_props reserved-actor-key strip (E2E-3 negative, SDK leg)."""

    def test_sanitize_pops_forged_actor_keys(self, caplog):
        from tortoise.sdk import _sanitize_props
        for reserved in ("actor_user_id", "owner", "initiated_by", "agent_id"):
            props = {"content": "x", reserved: "attacker-claim",
                     "search_keys": "k"}
            out = _sanitize_props(props)
            assert reserved not in out, \
                f"{reserved} must be stripped from tenant props"
            assert out["content"] == "x", out
            # caller's dict is untouched (dict(props) copy-first)
            assert reserved in props, "caller dict must never be mutated"
        assert props["content"] == "x"

    def test_sanitize_other_rejects_still_raise(self):
        """The strip is additive — the existing server-managed rejects still
        raise (never masked by the pop)."""
        from tortoise.sdk import _sanitize_props
        try:
            _sanitize_props({"actor_user_id": "x", "is_episodic": True})
            raise AssertionError("is_episodic reject must still fire")
        except ValueError as ex:
            assert "is_episodic" in str(ex), ex
        try:
            _sanitize_props({"actor_user_id": "x", "sourcePath": "/etc/passwd"})
            raise AssertionError("sourcePath reject must still fire")
        except ValueError as ex:
            assert "sourcePath" in str(ex), ex

    def test_sanitize_authoredBy_not_stripped(self):
        """authoredBy is the pre-existing client author-label — deliberately
        NOT in the reserved set (documented residual)."""
        from tortoise.sdk import _sanitize_props
        out = _sanitize_props({"authoredBy": "agent-claude", "content": "y"})
        assert out["authoredBy"] == "agent-claude", out

    def test_sanitize_strip_emits_warning(self, caplog):
        import logging
        from tortoise.sdk import _sanitize_props
        with caplog.at_level(logging.WARNING, logger="tortoise.api"):
            _sanitize_props({"actor_user_id": "forged", "content": "z"})
        hits = [r.getMessage() for r in caplog.records
                if "ignoring client-supplied" in r.getMessage()]
        assert hits, "the strip must emit an ignoring warning"
        assert "actor_user_id" in hits[0], hits

    def test_create_point_strip_backstop_node_clean(self, tmp_path):
        """End-to-end SDK backstop: a forged actor claim via props on
        create_point is stripped — the node never carries it and no error
        is raised (strip-and-ignore, never 4xx)."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK(db_path=str(tmp_path / "strip.db"))
        try:
            pt = sdk.create_point("statement", "strip forged claim",
                                  actor_user_id="attacker-1",
                                  is_episodic=False)
            # props bypass: create_point binds unknown kwargs into props —
            # the forged key is stripped before the node write.
            rows = sdk._get_proj().g.query(
                "MATCH (p:Point {id:$id}) RETURN p.actor_user_id",
                params={"id": pt["id"]}).result_set
            assert rows and rows[0][0] is None, \
                "forged actor_user_id must never reach the node"
        finally:
            sdk.close()


class TestMcpBoundaryStripAndIgnore:
    """E2E-3 negative (MCP boundary leg): _reject_server_managed_props pops
    the reserved actor keys BEFORE the server-managed reject — a tool call
    succeeds, and the forged claim never lands on the node."""

    def test_reject_helper_strips_reserved(self):
        from tortoise.mcp_server import _reject_server_managed_props
        for reserved in ("actor_user_id", "owner", "initiated_by", "agent_id"):
            props = {reserved: "forged", "content": "x"}
            # None → no server-managed violation remains after the pop
            assert _reject_server_managed_props(props) is None, props
            assert reserved not in props, props
            assert props["content"] == "x", props

    def test_reject_helper_still_rejects_server_managed(self):
        from tortoise.mcp_server import _reject_server_managed_props
        err = _reject_server_managed_props(
            {"actor_user_id": "forged", "is_episodic": True})
        assert err and "is_episodic" in err, err
        # actor key gone even when a real violation follows
        err = _reject_server_managed_props(
            {"actor_user_id": "forged", "sourcePath": "/x"})
        assert err and "sourcePath" in err, err

    def test_tool_create_point_forged_actor_ignored(self, tmp_path):
        """tortoise_create_point (direct call — MCP tenant-mode team context
        required for the SDK open) with a forged actor claim in props →
        success (no 4xx) and the node carries no forged key. Run with a
        minimal hosted team context."""
        from tortoise.mcp_auth import (  # noqa: I001
            _current_team_id, _current_team_limits, _transport_mode)
        from tortoise.mcp_server import tortoise_create_point
        import os
        os.environ.setdefault("TORTOISE_SESSION_LLM_MOCK", "1")
        from tests._http_fixtures import patched_tortoise_sdk
        with patched_tortoise_sdk(str(tmp_path / "mcp-strip.db")):
            import tortoise.hosted_api as _ha
            _ha._make_sdk(namespace="registry")._get_registry().query(
                "CREATE (t:Team {id:$id})",
                params={"id": "team-strip-2600"})
            tok_t = _current_team_id.set("team-strip-2600")
            tok_l = _current_team_limits.set(
                {"team_id": "team-strip-2600", "tier": "free",
                 "max_points": 100000})
            tok_m = _transport_mode.set("http")
            try:
                res = tortoise_create_point(
                    kind="statement", content="strip forged mcp claim",
                    props={"actor_user_id": "forged", "owner": "evil",
                           "agent_id": "spoof"})
                assert "error" not in res, res
                rows = _ha._make_sdk(namespace="team-strip-2600")._get_proj(
                ).g.query(
                    "MATCH (p:Point) WHERE p.content CONTAINS 'strip forged mcp' "
                    "RETURN p.actor_user_id, p.owner, p.agent_id").result_set
                assert rows, "the forged-claim point must be created"
                assert list(rows[0]) == [None, None, None], \
                    f"forged claims must never reach the node: {rows}"
            finally:
                _current_team_id.reset(tok_t)
                _current_team_limits.reset(tok_l)
                _transport_mode.reset(tok_m)


class TestMcpToolSweepStripActor:
    """Plan Step 3 (P2-5): parametrized sweep across representative
    props-accepting MCP tools — each succeeds with a forged actor key in
    props, and the stored node carries no forged value (the shared
    _reject_server_managed_props choke point covers ALL 11 call sites; the
    sweep pins the end-to-end behavior on a per-surface sample)."""

    def _ctx(self, tmp_path):
        import contextlib
        from tortoise.mcp_auth import (_current_team_id, _current_team_limits,
                                       _transport_mode)
        from tests._http_fixtures import patched_tortoise_sdk
        import os
        os.environ.setdefault("TORTOISE_SESSION_LLM_MOCK", "1")

        @contextlib.contextmanager
        def _mgr():
            with patched_tortoise_sdk(str(tmp_path / "sweep.db")):
                import tortoise.hosted_api as _ha
                _ha._make_sdk(namespace="registry")._get_registry().query(
                    "CREATE (t:Team {id:$id})",
                    params={"id": "team-sweep-2600"})
                tok_t = _current_team_id.set("team-sweep-2600")
                tok_l = _current_team_limits.set(
                    {"team_id": "team-sweep-2600", "tier": "free",
                     "max_points": 100000})
                tok_m = _transport_mode.set("http")
                try:
                    yield
                finally:
                    _current_team_id.reset(tok_t)
                    _current_team_limits.reset(tok_l)
                    _transport_mode.reset(tok_m)
        return _mgr()

    def test_update_point_forged_actor_stripped(self, tmp_path):
        """update_point with forged actor props → success; the node keeps
        its real props and never gains the forged key."""
        from tortoise.mcp_server import (tortoise_create_point,
                                         tortoise_update_point)
        with self._ctx(tmp_path):
            created = tortoise_create_point(kind="statement",
                                            content="sweep update target")
            assert "error" not in created, created
            pid = created["id"]
            res = tortoise_update_point(pid, {"actor_user_id": "evil",
                                              "owner": "evil2",
                                              "note": "kept"})
            assert "error" not in res, res
            import tortoise.hosted_api as _ha
            rows = _ha._make_sdk(namespace="team-sweep-2600")._get_proj(
            ).g.query(
                "MATCH (p:Point {id:$id}) RETURN p.actor_user_id, p.owner, "
                "p.note", params={"id": pid}).result_set
            assert rows and list(rows[0]) == [None, None, "kept"], rows

    def test_create_subject_object_forged_actor_stripped(self, tmp_path):
        """create_subject / create_object with forged actor props → created
        nodes carry no forged value (entity surfaces)."""
        from tortoise.mcp_server import (tortoise_create_object,
                                         tortoise_create_subject)
        with self._ctx(tmp_path):
            s = tortoise_create_subject("sweep-subj", "core:strategy",
                                        props={"actor_user_id": "evil",
                                               "initiated_by": "spoof"})
            assert "error" not in s, s
            o = tortoise_create_object("sweep-obj", "core:metric",
                                       props={"actor_user_id": "evil",
                                              "agent_id": "spoof"})
            assert "error" not in o, o
            import tortoise.hosted_api as _ha
            sdk = _ha._make_sdk(namespace="team-sweep-2600")
            rows = sdk._get_proj().g.query(
                "MATCH (n) WHERE n.name IN ['sweep-subj','sweep-obj'] "
                "RETURN n.actor_user_id, n.initiated_by, n.agent_id").result_set
            assert rows, "both entities must be created"
            for r in rows:
                assert list(r) == [None, None, None], rows


# ═══════════════════════════════════════════════════════════════════════════
# #2600 Phase 1 Task 6 — deterministic LLM-free two-actor dedup (E2E-5,
# SDK lane). The load-bearing dedup-actor contract: writer A's PointAdded
# GraphEvent keeps A; writer B's identical content dedup-hits the canonical
# and emits NOTHING (no second PointAdded with B). This survives even if a
# hosted capture event leg is dropped (the plan's escape-hatch precision).

class TestTwoActorDedupSdkLane:
    """Two writers, one shared claim — first writer's event keeps the actor;
    the second write returns the canonical and emits no B event."""

    def _graph_events(self, sdk, type_: str):
        import json
        rows = sdk._get_proj().g.query(
            "MATCH (e:GraphEvent {type:$t}) RETURN e.payload ORDER BY e.seq",
            params={"t": type_}).result_set
        return [json.loads(r[0]) for r in rows]

    def test_first_writer_event_keeps_actor_second_emits_nothing(self, tmp_path):
        from tortoise.sdk import TortoiseSDK, _current_actor_user_id
        sdk = TortoiseSDK(db_path=str(tmp_path / "dedup2.db"))
        try:
            content = "shared dedup claim (two writers, one memory)"
            tok = _current_actor_user_id.set(UUID_A)
            try:
                p1 = sdk.create_or_update_point("statement", content)
            finally:
                _current_actor_user_id.reset(tok)
            # B's identical write → canonical returned, NO new PointAdded
            tok = _current_actor_user_id.set(UUID_B)
            try:
                p2 = sdk.create_or_update_point("statement", content)
            finally:
                _current_actor_user_id.reset(tok)
            assert p1["id"] == p2["id"], \
                "identical content must resolve the SAME canonical point"
            adds = self._graph_events(sdk, "PointAdded")
            # exactly ONE PointAdded — the first writer's (B emitted nothing)
            assert len(adds) == 1, adds
            assert adds[0]["id"] == p1["id"], adds
            assert adds[0]["actor_user_id"] == UUID_A, \
                f"first writer's PointAdded must keep A: {adds}"
        finally:
            sdk.close()

    def test_distinct_claims_each_carry_their_writer(self, tmp_path):
        """Control: two DISTINCT claims (no dedup) → each PointAdded carries
        its own writer (proves the dedup leg above is what suppressed B)."""
        from tortoise.sdk import TortoiseSDK, _current_actor_user_id
        sdk = TortoiseSDK(db_path=str(tmp_path / "dedup2b.db"))
        try:
            tok = _current_actor_user_id.set(UUID_A)
            try:
                sdk.create_or_update_point("statement", "claim by writer A")
            finally:
                _current_actor_user_id.reset(tok)
            tok = _current_actor_user_id.set(UUID_B)
            try:
                sdk.create_or_update_point("statement", "claim by writer B")
            finally:
                _current_actor_user_id.reset(tok)
            adds = self._graph_events(sdk, "PointAdded")
            assert len(adds) == 2, adds
            by_id = {a["content_hash"]: a.get("actor_user_id") for a in adds}
            assert len(by_id) == 2, by_id
            assert UUID_A in by_id.values() and UUID_B in by_id.values(), by_id
        finally:
            sdk.close()
