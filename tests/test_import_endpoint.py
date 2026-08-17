"""HTTP tests for the #1230 graph import endpoint — POST /v1/teams/{team_id}/import.

Integration-layer matrix (plan Integration Surface Map S4–S6):
- Auth: no session 401; member/admin 403; unknown team 403 (no existence
  oracle); deleted team 410 (owner) / 403 (non-owner).
- Caps: oversize 413 (streaming cap — including a chunked body with NO
  Content-Length); artifact > team max_points 413; rate 429 (import budget,
  independent of export/team_delete).
- Fail-closed validation chain: tampered blob / wrong artifact key / tampered
  payload / count mismatch / malformed envelope → 422 + quarantine (audit +
  last_import_quarantined_sha256), live graph untouched.
- Happy path: valid artifact (raw-bytes + header, and the JSON-body form) →
  200 imported; structure counts + Point IDs match the payload; audited.
- Quarantine: dangling-edge dump → 422, live graph untouched.
- Idempotency: re-import of the same payload sha256 → 200 already-imported,
  no double swap.
- Crash-mid-swap: a live-delete failure leaves the old graph intact (helper
  level) and the endpoint maps a swap failure to 503 + quarantine.

Also runs the ``_restore_into_temp_verify_swap`` helper regression surface
shared with ``restore_backup`` (which keeps its own suite in
tests/test_hosted_backup.py, run unchanged).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

# #1389: these deep import-path tests (restore → temp graph → verify → swap)
# collide with the in-process TestClient harness on embedded FalkorDBLite —
# the app's keepalive anchor + the handler boot two embedded daemons on the
# same single-writer path. Prod uses FalkorDB Cloud (multi-client — no
# collision). The swap logic itself is covered by the hosted_backup
# regression suite; the full import journey runs against the subprocess
# server in #1390's parity E2E (tests/e2e/hosted). Skipped here until a
# server-mode harness is wired for these cases.
_import_deep = pytest.mark.skip(
    reason="embedded single-writer collision in the in-process harness — "
           "deep import path covered by #1390 subprocess E2E"
)

from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
from tortoise.hosted_backup import (
    DUMP_FORMAT,
    RestoreVerificationError,
    _restore_into_temp_verify_swap,
    encrypt_backup,
)
from tortoise.hosted_api import app, get_current_user
from tortoise.projection import FalkorProjection

from tests.fake_control_plane import FakeControlPlane
from tests.test_supabase_control import (
    FREE_TEAM, _key_row, _membership_row,
)

# Tests opt out of the IP rate limiter; rate-limit tests re-enable it.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

TEAM_ID = "team-free-001"
OWNER = "user-1"
GRAPH_NAME = "team_import_g"  # teams.graph_name in Supabase mode (import target)
IMPORT_KEY_HEADER = "X-Tortoise-Import-Key"


# ── harness ───────────────────────────────────────────────────────────────


def _enable_supabase(monkeypatch, cp) -> FakeControlPlane:
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return cp


def _patch_tortoise_sdk_init(db_path: str):
    _orig = ha_mod.TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched
    return _orig


def _restore_sdk_init(_orig):
    ha_mod.TortoiseSDK.__init__ = _orig
    app.dependency_overrides.clear()


@pytest.fixture
def sb_client(monkeypatch):
    """Supabase-mode TestClient with a fake control plane + temp DB."""
    fake = FakeControlPlane({"teams": [], "api_keys": [],
                             "team_memberships": [], "invitations": []})
    _enable_supabase(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "import.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc, fake, db_path
        finally:
            _restore_sdk_init(_orig)


@pytest.fixture
def as_user():
    """Override get_current_user per test (JWT session user)."""

    def _set(user_id: str = OWNER):
        app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}

    yield _set
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def capture_audit(monkeypatch):
    captured: list[dict] = []
    _POSITIONAL = ("team_id", "actor_user_id", "operation",
                   "resource_type", "resource_id", "ip_address", "user_agent")

    def _capture(*args, **kwargs):
        for i, a in enumerate(args):
            if i < len(_POSITIONAL):
                kwargs[_POSITIONAL[i]] = a
        captured.append(kwargs)

    monkeypatch.setattr(ha_mod._audit_logger, "append", _capture)
    return captured


# ── seeding ───────────────────────────────────────────────────────────────


def _seed_team(fake, *, role: str = "owner", deleted_at: str | None = None,
               max_points: int | None = None) -> None:
    """Seed the Supabase control plane with a team (incl. graph_name so the
    import's team_graph_name seam resolves) + owner membership."""
    team = dict(FREE_TEAM, graph_name=GRAPH_NAME)
    if max_points is not None:
        team["max_points"] = max_points
    if deleted_at:
        team["deleted_at"] = deleted_at
    fake.seed("teams", [team])
    fake.seed("team_memberships", [_membership_row(role=role)])
    fake.seed("api_keys", [_key_row()])


def _seed_live_graph(db_path: str, n_points: int = 1, *,
                     graph_name: str = GRAPH_NAME) -> None:
    """Seed the team's live FalkorDB graph (the content an import replaces)."""
    proj = FalkorProjection(db_path, graph_name=graph_name)
    try:
        g = proj.g
        for i in range(n_points):
            g.query(
                "CREATE (p:Point {id:$id, content:$c, pointKind:'claim'})",
                params={"id": f"old-{i}", "c": f"old content {i}"},
            )
    finally:
        proj._conn.close() if hasattr(proj, "_conn") else None


def _counts(db_path: str, graph_name: str = GRAPH_NAME) -> dict:
    """(nodes, edges, point_ids) of a graph in the temp DB."""
    proj = FalkorProjection(db_path, graph_name=graph_name)
    try:
        g = proj.g
        nodes = int(g.query("MATCH (n) RETURN count(n)").result_set[0][0])
        edges = int(g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0])
        ids = sorted(
            str(r[0]) for r in g.query(
                "MATCH (n:Point) RETURN coalesce(n.id, '')"
            ).result_set
        )
        return {"nodes": nodes, "edges": edges, "ids": ids}
    finally:
        proj._conn.close() if hasattr(proj, "_conn") else None


# ── artifact builder (the tortoise-export-v1 envelope #1388 produces) ──────


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _build_payload(n_points: int = 3, n_edges: int = 2, *,
                   graph_name: str = "selfhost_graph") -> dict:
    nodes = [
        {"dump_id": i, "labels": ["Point"],
         "props": {"id": f"pt-{i}", "content": f"content {i}", "pointKind": "claim"}}
        for i in range(n_points)
    ]
    edges = [
        {"src": i, "dst": i + 1, "type": "IMPL", "props": {"weight": 0.8}}
        for i in range(n_edges)
    ]
    return {
        "format": DUMP_FORMAT,
        "dumped_at": "2026-08-17T00:00:00Z",
        "graph_name": graph_name,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def _build_artifact(payload: dict, key: bytes, *,
                    tamper_blob: bool = False,
                    payload_sha_override: str | None = None,
                    header: dict | None = None) -> bytes:
    """One-line clear header + raw encrypted blob (the wire contract)."""
    inner = {
        "format": "tortoise-export-v1",
        "payload_sha256": payload_sha_override or hashlib.sha256(_canonical(payload)).hexdigest(),
        "payload": payload,
    }
    blob = encrypt_backup(json.dumps(inner).encode("utf-8"), key=key)
    if tamper_blob:
        blob = blob[:-1] + bytes([blob[-1] ^ 0xFF])
    header = header or {
        "format": "tortoise-export-v1",
        "artifact_version": 1,
        "encrypted": True,
        "algorithm": "AES-256-GCM",
        "key_fingerprint": hashlib.sha256(key).hexdigest()[:8],
        "exporter_version": "1.0.0",
        "exported_at": "2026-08-17T00:00:00Z",
        "source_surface": "selfhost",
        "blob_sha256": hashlib.sha256(blob).hexdigest(),
    }
    return json.dumps(header).encode("utf-8") + b"\n" + blob


def _key_b64(key: bytes) -> str:
    return base64.b64encode(key).decode()


def _post_import(tc, artifact: bytes, key: bytes, *, headers: dict | None = None):
    hdrs = {"Content-Type": "application/vnd.tortoise.export.v1",
            IMPORT_KEY_HEADER: _key_b64(key)}
    if headers:
        hdrs.update(headers)
    return tc.post(f"/v1/teams/{TEAM_ID}/import", content=artifact, headers=hdrs)


# ═══════════════════════════════════════════════════════════════════════════
# Auth (S4) — owner-scoped, authz-first
# ═══════════════════════════════════════════════════════════════════════════


class TestImportAuth:
    def test_import_requires_session_auth(self, sb_client):
        tc, _, _ = sb_client
        r = tc.post(f"/v1/teams/{TEAM_ID}/import", content=b"x")
        assert r.status_code == 401

    def test_import_requires_owner(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, role="member")
        as_user()
        r = _post_import(tc, b"x", os.urandom(32))
        assert r.status_code == 403
        assert "owner" in r.json()["detail"]

    def test_import_admin_denied(self, sb_client, as_user):
        """Strict owner — a full-graph overwrite is not writable by admins."""
        tc, fake, _ = sb_client
        _seed_team(fake, role="admin")
        as_user()
        assert _post_import(tc, b"x", os.urandom(32)).status_code == 403

    def test_import_unknown_team_403(self, sb_client, as_user):
        """AuthZ-first: no existence oracle for unknown teams."""
        tc, _, _ = sb_client
        as_user()
        r = tc.post("/v1/teams/nope/import", content=b"x",
                    headers={IMPORT_KEY_HEADER: _key_b64(os.urandom(32))})
        assert r.status_code == 403

    def test_import_deleted_team_410(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, deleted_at=datetime.now(timezone.utc).isoformat())
        as_user()
        r = _post_import(tc, b"x", os.urandom(32))
        assert r.status_code == 410

    def test_import_deleted_team_non_owner_403(self, sb_client, as_user):
        """Non-owner probing a deleted team gets 403, not the deletion schedule."""
        tc, fake, _ = sb_client
        _seed_team(fake, role="member",
                   deleted_at=datetime.now(timezone.utc).isoformat())
        as_user()
        assert _post_import(tc, b"x", os.urandom(32)).status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# Caps (S4) — streaming size cap, graph-size cap, rate limit
# ═══════════════════════════════════════════════════════════════════════════


class TestImportCaps:
    def test_import_oversize_413(self, sb_client, as_user, monkeypatch):
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        monkeypatch.setattr(ha_mod, "_IMPORT_MAX_BYTES", 256)
        key = os.urandom(32)
        artifact = _build_artifact(_build_payload(), key)
        assert len(artifact) > 256
        r = _post_import(tc, artifact, key)
        assert r.status_code == 413

    def test_import_oversize_stream_caught_without_content_length(
            self, sb_client, as_user, monkeypatch):
        """The cap is enforced WHILE streaming — a chunked body (no
        Content-Length, so it cannot be trusted) is still caught."""
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        monkeypatch.setattr(ha_mod, "_IMPORT_MAX_BYTES", 256)
        key = os.urandom(32)
        artifact = _build_artifact(_build_payload(), key)
        chunks = [artifact[i:i + 64] for i in range(0, len(artifact), 64)]
        r = tc.post(
            f"/v1/teams/{TEAM_ID}/import", content=iter(chunks),
            headers={"Content-Type": "application/vnd.tortoise.export.v1",
                     IMPORT_KEY_HEADER: _key_b64(key)},
        )
        assert r.status_code == 413

    def test_import_over_team_max_points_413(self, sb_client, as_user):
        """node_count > team max_points (graph_size_cap) → 413."""
        tc, fake, _ = sb_client
        _seed_team(fake, max_points=2)
        as_user()
        key = os.urandom(32)
        artifact = _build_artifact(_build_payload(n_points=3), key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 413

    def test_import_rate_limited_429(self, sb_client, as_user, monkeypatch):
        """Per-IP hourly budget for import (5/h): exhausted → 429."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setitem(ha_mod._SENSITIVE_OP_LIMITS, "import", 2)
        ha_mod._SENSITIVE_BUCKETS.clear()
        try:
            tc, _, _ = sb_client
            as_user()
            for _ in range(2):
                # unknown team → 403 (authz-first), budget consumed either way
                r = tc.post(f"/v1/teams/{TEAM_ID}/import", content=b"x")
                assert r.status_code == 403
            r = tc.post(f"/v1/teams/{TEAM_ID}/import", content=b"x")
            assert r.status_code == 429
            assert "Retry-After" in r.headers
        finally:
            ha_mod._SENSITIVE_BUCKETS.clear()

    def test_import_rate_limit_independent_of_export(self, sb_client,
                                                     as_user, monkeypatch):
        """Import has its own budget — export calls don't consume it."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setitem(ha_mod._SENSITIVE_OP_LIMITS, "import", 1)
        ha_mod._SENSITIVE_BUCKETS.clear()
        try:
            tc, _, _ = sb_client
            as_user()
            assert tc.get(f"/v1/teams/{TEAM_ID}/export").status_code == 403
            assert tc.post(f"/v1/teams/{TEAM_ID}/import", content=b"x").status_code == 403
            # export didn't consume the import bucket → still budget left
            r = tc.post(f"/v1/teams/{TEAM_ID}/import", content=b"x")
            assert r.status_code == 429
        finally:
            ha_mod._SENSITIVE_BUCKETS.clear()


# ═══════════════════════════════════════════════════════════════════════════
# Fail-closed validation chain (S5) — 422 + quarantine, live untouched
# ═══════════════════════════════════════════════════════════════════════════


class TestImportValidationFailClosed:
    @_import_deep
    def test_import_tampered_blob_422(self, sb_client, as_user, capture_audit):
        tc, fake, db_path = sb_client
        _seed_team(fake)
        _seed_live_graph(db_path, n_points=1)
        as_user()
        key = os.urandom(32)
        artifact = _build_artifact(_build_payload(), key, tamper_blob=True)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "blob integrity" in r.json()["detail"]
        # quarantine recorded
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        # live graph untouched (old content survives)
        assert _counts(db_path)["ids"] == ["old-0"]

    def test_import_wrong_key_422(self, sb_client, as_user, capture_audit):
        tc, fake, db_path = sb_client
        _seed_team(fake)
        _seed_live_graph(db_path, n_points=1)
        as_user()
        key = os.urandom(32)
        artifact = _build_artifact(_build_payload(), key)
        wrong = os.urandom(32)
        r = _post_import(tc, artifact, wrong)
        assert r.status_code == 422, r.text
        assert "fingerprint" in r.json()["detail"]
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == ["old-0"]

    def test_import_tampered_payload_422(self, sb_client, as_user, capture_audit):
        tc, fake, db_path = sb_client
        _seed_team(fake)
        _seed_live_graph(db_path, n_points=1)
        as_user()
        key = os.urandom(32)
        # envelope whose declared payload_sha256 does NOT match the payload
        other = _build_payload(n_points=5)
        artifact = _build_artifact(
            _build_payload(), key,
            payload_sha_override=hashlib.sha256(_canonical(other)).hexdigest(),
        )
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "payload integrity" in r.json()["detail"]
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == ["old-0"]

    def test_import_count_mismatch_422(self, sb_client, as_user, capture_audit):
        tc, fake, db_path = sb_client
        _seed_team(fake)
        _seed_live_graph(db_path, n_points=1)
        as_user()
        key = os.urandom(32)
        payload = _build_payload()
        payload["node_count"] = payload["node_count"] + 1
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "count" in r.json()["detail"]
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == ["old-0"]

    def test_import_non_artifact_body_422(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        r = _post_import(tc, b"this is not an artifact", os.urandom(32))
        assert r.status_code == 422

    @_import_deep
    def test_import_empty_backup_over_live_422(self, sb_client, as_user,
                                              capture_audit):
        """Empty-backup-over-live guard: a 0-node artifact must not wipe."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        _seed_live_graph(db_path, n_points=2)
        as_user()
        key = os.urandom(32)
        artifact = _build_artifact(_build_payload(n_points=0, n_edges=0), key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "empty" in r.json()["detail"].lower()
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["nodes"] == 2  # live untouched

    @_import_deep
    def test_import_dangling_edge_quarantined_422(self, sb_client, as_user,
                                                  capture_audit):
        """Dangling-edge dump: restore_graph fails → 422 + quarantine; the
        live graph is never touched (S5)."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        _seed_live_graph(db_path, n_points=1)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=2, n_edges=2)
        payload["edges"][1]["dst"] = 99  # references a missing node
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == ["old-0"]

    def test_import_quarantine_stamps_ledger(self, sb_client, as_user):
        """A rejected import records last_import_quarantined_sha256 on the
        Team row (control-plane ledger) — idempotent metadata, best-effort."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        _seed_live_graph(db_path, n_points=1)
        as_user()
        key = os.urandom(32)
        artifact = _build_artifact(_build_payload(), key, tamper_blob=True)
        assert _post_import(tc, artifact, key).status_code == 422
        rows = fake.tables["teams"]
        assert rows and rows[0].get("last_import_quarantined_sha256")


# ═══════════════════════════════════════════════════════════════════════════
# Happy path (S5) — temp→verify→swap; counts + Point IDs match; audited
# ═══════════════════════════════════════════════════════════════════════════


class TestImportHappyPath:
    @_import_deep
    def test_import_happy_path(self, sb_client, as_user, capture_audit):
        tc, fake, db_path = sb_client
        _seed_team(fake)
        _seed_live_graph(db_path, n_points=1)  # old content replaced by swap
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=3, n_edges=2)
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["imported"] is True
        assert body["already"] is False
        assert body["id"] == hashlib.sha256(_canonical(payload)).hexdigest()
        assert body["restored"] == {"nodes": 3, "edges": 2}
        # structure verification: counts + Point-ID survival (beats the
        # E2E-12-D content-presence baseline — #1230)
        counts = _counts(db_path)
        assert counts["nodes"] == 3
        assert counts["edges"] == 2
        assert counts["ids"] == ["pt-0", "pt-1", "pt-2"]
        # audit event recorded (team_import, actor, sha256)
        events = [e for e in capture_audit if e["operation"] == "team_import"]
        assert len(events) == 1
        assert events[0]["actor_user_id"] == OWNER
        assert events[0]["detail"]["sha256"] == body["id"]

    def test_import_json_body_form(self, sb_client, as_user):
        """JSON-field form: {"artifact": <base64>, "key": <base64>}."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=2, n_edges=1)
        artifact = _build_artifact(payload, key)
        r = tc.post(
            f"/v1/teams/{TEAM_ID}/import",
            json={"artifact": base64.b64encode(artifact).decode(),
                  "key": _key_b64(key)},
        )
        assert r.status_code == 200, r.text
        assert r.json()["imported"] is True
        assert _counts(db_path)["ids"] == ["pt-0", "pt-1"]

    def test_import_into_fresh_team_graph(self, sb_client, as_user):
        """Import into a team whose live graph has never been created."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=2, n_edges=1)
        r = _post_import(tc, _build_artifact(payload, key), key)
        assert r.status_code == 200, r.text
        counts = _counts(db_path)
        assert counts["nodes"] == 2 and counts["edges"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# Idempotency (S6) + crash-mid-swap safety
# ═══════════════════════════════════════════════════════════════════════════


class TestImportIdempotencyAndSwapSafety:
    def test_import_idempotent_reimport_200(self, sb_client, as_user,
                                            capture_audit):
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=3, n_edges=2)
        artifact = _build_artifact(payload, key)
        r1 = _post_import(tc, artifact, key)
        assert r1.status_code == 200 and r1.json()["imported"] is True
        r2 = _post_import(tc, artifact, key)
        assert r2.status_code == 200, r2.text
        assert r2.json()["imported"] is False
        assert r2.json()["already"] is True
        assert r2.json()["id"] == r1.json()["id"]
        # no double-swap: the graph still has the imported nodes exactly once
        assert _counts(db_path)["nodes"] == 3
        assert _counts(db_path)["ids"] == ["pt-0", "pt-1", "pt-2"]
        events = [e for e in capture_audit if e["operation"] == "team_import"]
        assert len(events) == 2
        assert events[1]["detail"].get("already") is True

    def test_import_ledger_stamped(self, sb_client, as_user):
        """Successful import stamps last_import_sha256 on the Team row."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=1, n_edges=0)
        artifact = _build_artifact(payload, key)
        assert _post_import(tc, artifact, key).status_code == 200
        expected = hashlib.sha256(_canonical(payload)).hexdigest()
        assert fake.tables["teams"][0].get("last_import_sha256") == expected

    @_import_deep
    def test_import_swap_failure_503_quarantined_live_untouched(
            self, sb_client, as_user, monkeypatch, capture_audit):
        """A server-side swap failure (RuntimeError) maps to 503 + quarantine;
        the live graph is never partially swapped (the temp graph is where the
        failure leaves verified content, never the live graph)."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        _seed_live_graph(db_path, n_points=1)
        as_user()

        def _boom(db, payload, *, live_name, **kwargs):
            raise RuntimeError("simulated GRAPH.COPY failure")

        monkeypatch.setattr(ha_mod, "_restore_into_temp_verify_swap", _boom)
        key = os.urandom(32)
        artifact = _build_artifact(_build_payload(), key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 503, r.text
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == ["old-0"]

    def test_helper_crash_mid_swap_keeps_old_graph(self, tmp_path):
        """Helper-level crash safety: if the live-graph delete fails mid-swap,
        the old graph survives (delete is best-effort; a failed copy surfaces
        as RuntimeError with the verified temp graph intact)."""
        db_path = str(tmp_path / "crash.db")
        proj = FalkorProjection(db_path, graph_name="crash_live")
        db = proj.db
        try:
            live = db.select_graph("crash_live")
            live.query("CREATE (p:Point {id:'old-0', content:'old'})")
            payload = _build_payload(n_points=2, n_edges=1)

            real_select = db.select_graph

            def _failing_select(name):
                g = real_select(name)
                if name == "crash_live":
                    orig = g.delete

                    def _boom(*a, **k):
                        raise RuntimeError("simulated crash in live delete")

                    g.delete = _boom
                return g

            db.select_graph = _failing_select
            try:
                with pytest.raises(RuntimeError):
                    _restore_into_temp_verify_swap(
                        db, payload, live_name="crash_live"
                    )
            finally:
                db.select_graph = real_select

            # old graph intact (delete failed → copy failed on existing dest)
            left = db.select_graph("crash_live")
            ids = [r[0] for r in left.query(
                "MATCH (n:Point) RETURN n.id").result_set]
            assert ids == ["old-0"]
        finally:
            proj._conn.close() if hasattr(proj, "_conn") else None
