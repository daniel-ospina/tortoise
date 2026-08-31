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

from fastapi.testclient import TestClient  # noqa: E402, I001

import tortoise.hosted_api as ha_mod  # noqa: E402
from tortoise.hosted_backup import (  # noqa: E402
    DUMP_FORMAT,
    RestoreVerificationError,  # noqa: F401
    _restore_into_temp_verify_swap,
    encrypt_backup,
)
from tortoise.hosted_api import app, get_current_user  # noqa: E402
from tortoise.projection import FalkorProjection  # noqa: E402

from tests.fake_control_plane import FakeControlPlane  # noqa: E402
from tests.test_supabase_control import (  # noqa: E402
    FREE_TEAM, _key_row, _membership_row,
)

# Tests opt out of the IP rate limiter; rate-limit tests re-enable it.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")


@pytest.fixture(scope="module", autouse=True)
def _embedded_local_file_lane():
    """Epic #1647 P4 (Task 10): this file's harness is a LOCAL-FILE server by
    design — the sb_client fixture patches TortoiseSDK.__init__ to force every
    SDK construction onto one temp DB file (the hosted app's store), and the
    seed/count helpers construct FalkorProjection on that same file. Under a
    docker session the URI-aware redirect would flip the seed/count
    constructions to derived SERVER graphs while the patched app keeps
    reading the local file — the import/swap assertions then compare two
    different stores (and the GRAPH.COPY swap fails on the server's
    non-test-prefixed names). Popping the URI for this module keeps the whole
    harness on one local file on BOTH lanes. Documented divergence from the
    plan's Task 10 "13 migrate out" list: this is an embedded-file-contract
    file, not docker-migratable — it stays in RAW_EMBEDDED_ALLOWLIST."""
    mp = pytest.MonkeyPatch()
    mp.delenv("TORTOISE_DB_URI", raising=False)
    yield
    mp.undo()


TEAM_ID = "team-free-001"

# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID literals 22P02 (HTTP 400) under
# FakeControlPlane's fidelity check. user-1 → _U1 (mirrors the constant
# in test_supabase_control, which _membership_row already seeds).
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
OWNER = _U1
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
    # #1497: break the _make_sdk embedded fallback anchor — module-level
    # _FALLBACK_KEEPALIVE survives tests, so an anchored SDK bound to a prior
    # test's temp DB leaks state / dies socket. Re-bind to THIS temp DB.
    ha_mod._FALLBACK_KEEPALIVE.clear()
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


# #1612: hold seeded projections alive — _seed_live_graph creates a
# FalkorProjection whose server is GC'd with close-on-GC + SHUTDOWN NOSAVE
# (#1475) when it goes out of scope, losing the seeded writes before a later
# _counts read re-opens the DB (assert [] == ['old-0'] flake). Same pattern
# as _REG_SDKS / _SEED_SDKS elsewhere. Closed at session end.
_SEED_PROJS: list = []


@pytest.fixture(scope="session", autouse=True)
def _close_seed_projs():
    """Close held seeded projections at session end (see _SEED_PROJS)."""
    yield
    while _SEED_PROJS:
        try:  # noqa: SIM105
            _SEED_PROJS.pop().close()
        except Exception:
            pass


def _seed_live_graph(db_path: str, n_points: int = 1, *,
                     graph_name: str = GRAPH_NAME) -> None:
    """Seed the team's live FalkorDB graph (the content an import replaces)."""
    proj = FalkorProjection(db_path, graph_name=graph_name)
    _SEED_PROJS.append(proj)  # #1612: hold so the server (and writes) survive
    try:
        g = proj.g
        for i in range(n_points):
            g.query(
                "CREATE (p:Point {id:$id, content:$c, pointKind:'claim'})",
                params={"id": f"old-{i}", "c": f"old content {i}"},
            )
    finally:
        # keep the projection + its server alive until session end (#1612)
        pass


def _counts(db_path: str, graph_name: str = GRAPH_NAME) -> dict:
    """(nodes, edges, point_ids) of a graph in the temp DB."""
    proj = FalkorProjection(db_path, graph_name=graph_name)
    try:
        g = proj.g
        # Exclude the R2/R3 Meta bookkeeping marker (point_fts_v2 / event_fts_v2)
        # — it's internal state, not imported content.
        nodes = int(g.query(
            "MATCH (n) WHERE NOT (n:Meta) AND NOT (n:GraphEventMeta) "
            "AND NOT (n:EpMeta) RETURN count(n)"
        ).result_set[0][0])
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
        _seed_team(fake, deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        as_user()
        r = _post_import(tc, b"x", os.urandom(32))
        assert r.status_code == 410

    def test_import_deleted_team_non_owner_403(self, sb_client, as_user):
        """Non-owner probing a deleted team gets 403, not the deletion schedule."""
        tc, fake, _ = sb_client
        _seed_team(fake, role="member",
                   deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
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
# #2028 — pre-v1.1 foreign-kind guard (loud mismatch, never silent drop)
# ═══════════════════════════════════════════════════════════════════════════


# Shared manifest constant (drift-hazard: one source of truth — the same
# ontology is validated by the guard tests in test_export_pack_config.py).
from tests.test_export_pack_config import CUSTOM_MANIFEST as _CUSTOM_MANIFEST  # noqa: E402


class TestImportForeignKindsGuard:
    def test_import_pre_v1_1_foreign_kind_422(self, sb_client, as_user,
                                              capture_audit):
        """Pre-v1.1 artifact (no pack_config) with a namespaced custom-pack
        kind → guard fires PRE-restore → 422 + quarantine; nothing lands in
        the live graph, last_import_sha256 unstamped (retries converge).

        No _seed_live_graph — no additional long-held holder on the file
        (the anchor+SDK pair is empirically safe: same pattern as the unskipped
        json-body-form / fresh-team tests)."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=1, n_edges=0)
        payload["nodes"][0]["props"]["pointKind"] = "tenant-ops:contract"
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "predates pack-config" in r.text  # the guard is the rejection source
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == []  # nothing landed (pre-restore)
        # ledger NOT stamped → re-import of the same artifact re-validates
        # (quarantine stamps a separate key; last_import_sha256 is untouched)
        assert not fake.tables["teams"][0].get("last_import_sha256")

    def test_import_v1_1_empty_packs_foreign_kind_422(self, sb_client, as_user,
                                                      capture_audit):
        """v1.1 artifact whose pack_config establishes NO vocabulary
        (collect_pack_config emits {schema_version:1, packs:[]} for a graph
        whose custom kinds have no PackInstall records) → the guard still
        fires → 422 + quarantine, nothing lands (#2028 — same silent-drop
        class as pre-v1.1: packs:[] upserts nothing, so the custom vocab
        would be dropped)."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=1, n_edges=0)
        payload["nodes"][0]["props"]["pointKind"] = "tenant-ops:contract"
        payload["pack_config"] = {"schema_version": 1, "packs": []}
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "declares no packs" in r.text  # accurate reason for the v1.1 empty-packs path
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == []  # nothing landed (pre-restore)

    def test_import_partial_packs_foreign_kind_422(self, sb_client, as_user,
                                                   capture_audit):
        """v1.1 artifact whose declared packs do NOT cover every namespaced
        kind in the dump → guard fires → 422 + quarantine, nothing lands.
        (Reachable on orphaned/partial pack state: a kind whose namespace is
        in no source of truth — catalog, starters, dump manifests, declared
        packs — would silently drop, the exact #2028 failure class.)"""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=1, n_edges=0)
        payload["nodes"][0]["props"]["objectKind"] = "ghost:poltergeist"
        payload["pack_config"] = {"schema_version": 1, "packs": [
            {"namespace": "tenant-ops", "version": "0.1.0", "activated": True,
             "yaml": _CUSTOM_MANIFEST}]}
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "does not cover" in r.text
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == []  # nothing landed (pre-restore)

    def test_import_post_v1_1_custom_pack_passthrough_200(self, sb_client,
                                                          as_user):
        """Post-v1.1 artifact (pack_config declaring a covering pack) with a
        custom-pack kind → the guard RUNS and passes because `tenant-ops` is
        absorbed from the declared pack_config (locks the absorption, not a
        presence gate); restore + manifest upsert succeed → 200. Contrast:
        WITHOUT a covering pack the same kinds 422 (see the two 422 tests).

        No _seed_live_graph — no additional long-held holder (same pattern as
        the unskipped json-body-form / fresh-team tests)."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=1, n_edges=0)
        payload["nodes"][0]["props"]["objectKind"] = "tenant-ops:contract"
        payload["pack_config"] = {"schema_version": 1, "packs": [
            {"namespace": "tenant-ops", "version": "0.1.0", "activated": True,
             "yaml": _CUSTOM_MANIFEST}]}
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 200, r.text
        assert r.json()["imported"] is True
        assert _counts(db_path)["ids"] == ["pt-0"]  # restore+swap landed


# ═══════════════════════════════════════════════════════════════════════════
# #2040 — malformed pack_config shape → 422 PRE-RESTORE (never 500)
# ═══════════════════════════════════════════════════════════════════════════


class TestImportPackConfigShape422:
    """#2040 Indicator 1: a malformed ``pack_config`` shape must 422
    fail-closed PRE-RESTORE — live graph untouched (``ids == []``),
    ``last_import_sha256`` unstamped, ``last_import_quarantined_sha256``
    stamped, ``quarantined_import`` audit recorded. Never a post-swap 500
    (AttributeError) or a silent 200 with the config dropped."""

    @pytest.mark.parametrize("pc", [
        {"packs": [42]},          # entry not dict → AttributeError 500 pre-fix
        {"packs": 42},            # non-list packs
        "x",                      # non-dict pack_config
        {"schema_version": 2, "packs": []},  # unsupported schema_version
    ], ids=["entry-not-dict", "packs-not-list", "pc-not-dict",
           "schema-version-2"])
    def test_malformed_pack_config_422_pre_restore(self, sb_client, as_user,
                                                   capture_audit, pc):
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=1, n_edges=0)
        payload["pack_config"] = pc
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "pack_config" in r.json()["detail"]
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == []  # nothing landed (pre-restore)
        # ledger NOT stamped; quarantine prop IS stamped
        row = fake.tables["teams"][0]
        assert not row.get("last_import_sha256")
        assert row.get("last_import_quarantined_sha256")

    def test_malformed_pack_config_cli_form_422(self, sb_client, as_user,
                                                capture_audit):
        """The CLI artifact form (single canonical JSON with blob_b64)
        funnels through the same ``_validate_import_envelope`` gate — a
        malformed pack_config must 422 there too (shared path lock)."""
        from tortoise.export import artifact_bytes, build_artifact

        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=1, n_edges=0)
        payload["pack_config"] = {"packs": [42]}
        artifact = artifact_bytes(build_artifact(payload, key=key))
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "pack_config" in r.json()["detail"]
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == []
        assert not fake.tables["teams"][0].get("last_import_sha256")

    def test_dual_fault_shape_fires_before_foreign_kind_guard(
            self, sb_client, as_user, capture_audit):
        """DUAL-FAULT payload: malformed pc ({"packs": 42}) AND a foreign
        kind (tenant-ops:contract). The envelope shape gate fires BEFORE
        ``_check_foreign_kinds`` — the 422 detail + quarantine reason must
        carry the SHAPE error (locks the envelope-before-guard precedence)."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=1, n_edges=0)
        payload["nodes"][0]["props"]["pointKind"] = "tenant-ops:contract"
        payload["pack_config"] = {"packs": 42}
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        # SHAPE reason in detail (gate fires before the guard) — pinned check
        # order: {"packs": 42} lacks schema_version, so the shape error is
        # "schema_version must be 1". The lock is: pack_config shape reason,
        # NOT the foreign-kind guard reason ("predates pack-config").
        assert "pack_config" in r.json()["detail"]
        assert "predates pack-config" not in r.json()["detail"]
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == []
        assert not fake.tables["teams"][0].get("last_import_sha256")


# ═══════════════════════════════════════════════════════════════════════════
# #2040 Indicator 2 — pack-application failures must not stamp the ledger
# (failures CLEAR it → same-artifact retry and rollback both converge)
# ═══════════════════════════════════════════════════════════════════════════


class TestImportPackConfigApplyFailures:
    """#2040 Indicator 2: a pack-application failure 422s with the swap
    landed but ``last_import_sha256`` NOT stamped (CLEARED) — the same
    artifact re-imports with the REAL reason (never ``{"already": true}``
    wedge); a fixed artifact converges; rollback to a prior artifact
    re-swaps. Success stamps the ledger + clears the quarantine prop.

    Success/failure fixtures MUST reuse ``CUSTOM_MANIFEST`` (declared ns
    ``tenant-ops`` == yaml ``namespace: tenant-ops``) so Task 3's ns↔yaml
    guard does not trip the fixture for the wrong reason."""

    def _payload(self, manifest_yaml, *, ns: str = "tenant-ops",
                 n_points: int = 1) -> dict:
        payload = _build_payload(n_points=n_points, n_edges=0)
        payload["pack_config"] = {"schema_version": 1, "packs": [
            {"namespace": ns, "version": "0.1.0", "activated": True,
             "yaml": manifest_yaml}]}
        return payload

    def test_invalid_manifest_422_ledger_clear_retryable(self, sb_client,
                                                         as_user, capture_audit):
        """Invalid custom-manifest yaml → 422 "invalid YAML" AFTER the swap
        (the pack upsert fails post-restore) with the ledger CLEARED: re-
        import of the same artifact 422s again with the real reason (never
        ``already``)."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = self._payload("namespace: [broken")
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "invalid YAML" in r.json()["detail"]
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        # swap LANDED (pack failure is post-swap) but ledger NOT stamped
        assert _counts(db_path)["ids"] == ["pt-0"]
        row = fake.tables["teams"][0]
        assert not row.get("last_import_sha256")  # cleared / never stamped
        expected = hashlib.sha256(_canonical(payload)).hexdigest()
        assert row.get("last_import_quarantined_sha256") == expected
        # same-artifact retry → 422 again with the real reason (never already)
        r2 = _post_import(tc, artifact, key)
        assert r2.status_code == 422, r2.text
        assert "invalid YAML" in r2.json()["detail"]
        assert r2.json() == r.json() or "already" not in r2.text

    def test_unknown_starter_422_ledger_clear_retryable(self, sb_client,
                                                        as_user):
        """Unknown starter reference (yaml null, ns not in starters) → 422 +
        ledger clear; the same artifact re-422s (never already)."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = self._payload(None, ns="not-a-real-pack")
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "unknown starter pack" in r.json()["detail"]
        assert _counts(db_path)["ids"] == ["pt-0"]
        assert not fake.tables["teams"][0].get("last_import_sha256")
        r2 = _post_import(tc, artifact, key)
        assert r2.status_code == 422, r2.text
        assert "unknown starter pack" in r2.json()["detail"]

    def test_deeply_nested_manifest_yaml_422_not_500(self, sb_client,
                                                     as_user):
        """Deeply-nested payload-controlled yaml → 422 (clean validation
        failure via the Task 4 RecursionError catch — REQUIRES Task 4), NOT a
        500; ledger clear."""
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = self._payload("{" * 1500 + "}" * 1500)
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "nesting too deep" in r.json()["detail"]
        assert not fake.tables["teams"][0].get("last_import_sha256")

    def test_rollback_prior_artifact_after_pack_failure(self, sb_client,
                                                        as_user):
        """Import A (200, stamped A) → import B broken (422, ledger CLEARED
        immediately after the 422) → re-import A → 200 ``imported: true``
        (RE-SWAP, not ``already``) — the rollback-to-prior-artifact path
        converges."""
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload_a = self._payload(_CUSTOM_MANIFEST, n_points=1)
        artifact_a = _build_artifact(payload_a, key)
        r_a = _post_import(tc, artifact_a, key)
        assert r_a.status_code == 200, r_a.text
        assert r_a.json()["imported"] is True
        sha_a = hashlib.sha256(_canonical(payload_a)).hexdigest()
        assert fake.tables["teams"][0].get("last_import_sha256") == sha_a
        # B: broken pack config (invalid manifest) — same key, new sha
        payload_b = self._payload("namespace: [broken", n_points=2)
        artifact_b = _build_artifact(payload_b, key)
        r_b = _post_import(tc, artifact_b, key)
        assert r_b.status_code == 422, r_b.text
        assert "invalid YAML" in r_b.json()["detail"]
        # ledger CLEARED (distinguishes clear-from-reorder-only: B's sha is
        # NOT what sits in last_import_sha256 — it must be falsy)
        row = fake.tables["teams"][0]
        assert not row.get("last_import_sha256")  # cleared, not A and not B
        # re-import A → RE-SWAP (imported true), not already
        r_a2 = _post_import(tc, artifact_a, key)
        assert r_a2.status_code == 200, r_a2.text
        assert r_a2.json()["imported"] is True
        assert r_a2.json()["already"] is False
        assert fake.tables["teams"][0].get("last_import_sha256") == sha_a

    def test_clear_path_stamp_failure_still_422(self, sb_client, as_user,
                                                monkeypatch, caplog):
        """The pack-failure ledger CLEAR is best-effort (try/except →
        warning): if the clear stamp raises (control-plane blip), the import
        still 422s (not 500) and the retry still 422s (quarantine
        consultation keeps the already-fast-path off)."""
        import logging

        real_stamp = ha_mod._stamp_import_prop

        def _boom(source, team_id, prop, value):
            if prop == "last_import_sha256" and value == "":
                raise RuntimeError("simulated clear failure")
            return real_stamp(source, team_id, prop, value)

        monkeypatch.setattr(ha_mod, "_stamp_import_prop", _boom)
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = self._payload("namespace: [broken")
        artifact = _build_artifact(payload, key)
        with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
            r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert "invalid YAML" in r.json()["detail"]
        assert any("ledger" in rec.message.lower() for rec in caplog.records)
        # retry still 422s (quarantine consultation) — never already
        r2 = _post_import(tc, artifact, key)
        assert r2.status_code == 422, r2.text

    def test_same_sha_fail_then_success_returns_already(self, sb_client,
                                                        as_user, monkeypatch):
        """Sticky-quarantine regression: fail-then-succeed on the SAME sha
        (transient apply failure then fixed env) → the success clears the
        quarantine prop, so a THIRD import returns ``already: true`` (not a
        perpetual re-swap loop)."""
        real_apply = ha_mod._apply_import_pack_config
        state = {"calls": 0}

        def _flaky(sdk, payload):
            state["calls"] += 1
            if state["calls"] == 1:
                raise ValueError("transient pack env failure")
            return real_apply(sdk, payload)

        monkeypatch.setattr(ha_mod, "_apply_import_pack_config", _flaky)
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = self._payload(_CUSTOM_MANIFEST)
        artifact = _build_artifact(payload, key)
        sha = hashlib.sha256(_canonical(payload)).hexdigest()
        # 1: transient apply failure → 422, quarantined
        r1 = _post_import(tc, artifact, key)
        assert r1.status_code == 422, r1.text
        assert "transient pack env failure" in r1.json()["detail"]
        assert fake.tables["teams"][0].get("last_import_quarantined_sha256") == sha
        # 2: same sha, env fixed → 200 imported; quarantine cleared
        r2 = _post_import(tc, artifact, key)
        assert r2.status_code == 200, r2.text
        assert r2.json()["imported"] is True
        row = fake.tables["teams"][0]
        assert row.get("last_import_sha256") == sha
        assert not row.get("last_import_quarantined_sha256")
        # 3: same sha → already (quarantine consultation passes)
        r3 = _post_import(tc, artifact, key)
        assert r3.status_code == 200, r3.text
        assert r3.json()["already"] is True

    def test_success_quarantine_clear_failure_reimport_still_already(self, sb_client,
                                                                     as_user, monkeypatch):
        """Success-path QUARANTINE-clear failure (control-plane write blip)
        leaves Q==sha while L==sha. The already-fast-path consults the
        POST-SWAP PACK-FAILURE MARKER (not the audit-only quarantine prop) —
        the marker cleared on success, so re-import returns ``already``
        (L==sha proves full application; the stamp runs only after pack
        application succeeds). Locks the marker-vs-Q consultation split
        (#2040 review round 2).

        Scenario: fail-then-succeed on the same sha with the SUCCESS-path
        quarantine clear broken — first import quarantines (Q=sha), second
        import succeeds (L=sha) but the quarantine clear fails → Q stays sha;
        the pack-failure marker STILL clears → already fires."""
        real_stamp = ha_mod._stamp_import_prop

        def _clear_boom(source, team_id, prop, value):
            if prop == "last_import_quarantined_sha256" and value == "":
                raise RuntimeError("simulated persistent clear failure")
            return real_stamp(source, team_id, prop, value)

        monkeypatch.setattr(ha_mod, "_stamp_import_prop", _clear_boom)
        # Transient apply failure on the FIRST import (fail-then-succeed).
        real_apply = ha_mod._apply_import_pack_config
        state = {"calls": 0}

        def _flaky(sdk, payload):
            state["calls"] += 1
            if state["calls"] == 1:
                raise ValueError("transient pack env failure")
            return real_apply(sdk, payload)

        monkeypatch.setattr(ha_mod, "_apply_import_pack_config", _flaky)
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = self._payload(_CUSTOM_MANIFEST)
        artifact = _build_artifact(payload, key)
        sha = hashlib.sha256(_canonical(payload)).hexdigest()
        # 1: transient apply failure → 422, quarantine stamped (Q=sha)
        r1 = _post_import(tc, artifact, key)
        assert r1.status_code == 422, r1.text
        assert fake.tables["teams"][0].get("last_import_quarantined_sha256") == sha
        # 2: same sha, env fixed → 200 imported; L=sha, Q-clear FAILS → Q stays sha;
        # the pack-failure marker still clears (independent best-effort writes)
        r2 = _post_import(tc, artifact, key)
        assert r2.status_code == 200, r2.text
        assert r2.json()["imported"] is True
        row = fake.tables["teams"][0]
        assert row.get("last_import_sha256") == sha
        assert row.get("last_import_quarantined_sha256") == sha  # Q clear failed
        assert not row.get("last_import_pack_failed_sha256")  # marker cleared
        # 3: re-import → already (marker != sha proves no post-swap failure)
        r3 = _post_import(tc, artifact, key)
        assert r3.status_code == 200, r3.text
        assert r3.json()["already"] is True

    def test_pre_restore_quarantine_other_sha_still_already(self, sb_client,
                                                            as_user):
        """#2040 review round 2 (P1 regression lock): a PRE-RESTORE
        rejection of a DIFFERENT artifact (foreign kind — graph untouched,
        only Q stamped, no post-swap marker) must NOT block ``already`` for
        the fully-applied sha. The marker-based consultation restores the
        documented idempotent re-import contract ("Re-import of the same
        payload sha256 → already") that the round-1 Q-consultation broke."""
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        # A: valid artifact → 200, L=A, marker cleared
        payload_a = self._payload(_CUSTOM_MANIFEST, n_points=1)
        artifact_a = _build_artifact(payload_a, key)
        sha_a = hashlib.sha256(_canonical(payload_a)).hexdigest()
        r_a = _post_import(tc, artifact_a, key)
        assert r_a.status_code == 200, r_a.text
        assert fake.tables["teams"][0].get("last_import_sha256") == sha_a
        # B: pre-restore rejection (foreign kind, no pack_config) — Q=B stamped,
        # graph untouched, NO pack-failure marker
        payload_b = _build_payload(n_points=1, n_edges=0)
        payload_b["nodes"][0]["props"]["pointKind"] = "tenant-ops:contract"
        artifact_b = _build_artifact(payload_b, key)
        r_b = _post_import(tc, artifact_b, key)
        assert r_b.status_code == 422, r_b.text
        row = fake.tables["teams"][0]
        assert row.get("last_import_quarantined_sha256") != sha_a
        assert not row.get("last_import_pack_failed_sha256")
        assert row.get("last_import_sha256") == sha_a  # A untouched
        # re-import A → already (marker != sha_a; no post-swap failure)
        r_a2 = _post_import(tc, artifact_a, key)
        assert r_a2.status_code == 200, r_a2.text
        assert r_a2.json()["already"] is True
        assert r_a2.json()["imported"] is False

    def test_pack_failure_marker_blocks_already_after_mirror_history(
            self, sb_client, as_user, monkeypatch):
        """#2040 review round 2 (P2 mirror-history lock): success→
        different-sha-quarantine→same-sha-failure must NOT return ``already``.
        A succeeded (L=A), B failed post-swap with the L-clear ALSO blipping
        (L stays stale=A, marker=B), then a re-import of A fails post-swap
        again (marker=A). The marker==sha blocks ``already`` — the last
        attempt at this sha failed post-swap, so the vocabulary may not be
        live despite L==sha."""
        real_stamp = ha_mod._stamp_import_prop

        def _clear_boom(source, team_id, prop, value):
            if prop == "last_import_sha256" and value == "":
                raise RuntimeError("simulated ledger-clear failure")
            return real_stamp(source, team_id, prop, value)

        monkeypatch.setattr(ha_mod, "_stamp_import_prop", _clear_boom)
        real_apply = ha_mod._apply_import_pack_config
        # Fail ONLY the SECOND application of artifact A (B's failed apply
        # also funnels through this seam — key on the CUSTOM_MANIFEST yaml).
        a_calls = {"n": 0}

        def _fail_apply_second(sdk, payload):
            pc = payload.get("pack_config") or {}
            packs = pc.get("packs") or []
            if any(p.get("yaml") == _CUSTOM_MANIFEST for p in packs):
                a_calls["n"] += 1
                if a_calls["n"] == 2:
                    raise ValueError("pack env failure on A re-import")
            return real_apply(sdk, payload)

        monkeypatch.setattr(ha_mod, "_apply_import_pack_config", _fail_apply_second)
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        # A: valid → 200, L=A
        payload_a = self._payload(_CUSTOM_MANIFEST, n_points=1)
        artifact_a = _build_artifact(payload_a, key)
        sha_a = hashlib.sha256(_canonical(payload_a)).hexdigest()
        r_a = _post_import(tc, artifact_a, key)
        assert r_a.status_code == 200, r_a.text
        assert fake.tables["teams"][0].get("last_import_sha256") == sha_a
        # B: broken manifest → 422 post-swap; L-clear blips → L stays A (stale),
        # marker=B
        payload_b = self._payload("namespace: [broken", n_points=2)
        artifact_b = _build_artifact(payload_b, key)
        r_b = _post_import(tc, artifact_b, key)
        assert r_b.status_code == 422, r_b.text
        row = fake.tables["teams"][0]
        assert row.get("last_import_sha256") == sha_a  # stale (clear blipped)
        assert row.get("last_import_pack_failed_sha256") != sha_a
        # re-import A: swap lands, pack apply FAILS (2nd call) → 422; marker=A
        r_a2 = _post_import(tc, artifact_a, key)
        assert r_a2.status_code == 422, r_a2.text
        assert "pack env failure on A re-import" in r_a2.json()["detail"]
        row = fake.tables["teams"][0]
        assert row.get("last_import_pack_failed_sha256") == sha_a  # marker=A
        # re-import A again → NOT already (marker==sha_a blocks the lie) → re-swap
        a_calls["n"] = 0  # reset so the third import applies cleanly
        r_a3 = _post_import(tc, artifact_a, key)
        assert r_a3.status_code == 200, r_a3.text
        assert r_a3.json()["imported"] is True
        assert r_a3.json()["already"] is False

    def test_rollback_after_pack_failure_clear_blip_200(self, sb_client,
                                                        as_user, monkeypatch):
        """Rollback wedge (reviewer finding): import A succeeds (L=A), then
        import B fails post-swap with the pack-failure ledger-CLEAR ALSO
        failing (control-plane blip) → L stays STALE=A while the live graph
        holds B's dump. Re-importing A must RE-SWAP (200 imported), not
        short-circuit to ``already`` — the consultation refuses ``already``
        for a non-empty quarantine of a DIFFERENT sha."""
        real_stamp = ha_mod._stamp_import_prop

        def _clear_boom(source, team_id, prop, value):
            if prop == "last_import_sha256" and value == "":
                raise RuntimeError("simulated ledger-clear failure")
            return real_stamp(source, team_id, prop, value)

        monkeypatch.setattr(ha_mod, "_stamp_import_prop", _clear_boom)
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        # A: valid artifact → 200, L=A, Q cleared
        payload_a = self._payload(_CUSTOM_MANIFEST, n_points=1)
        artifact_a = _build_artifact(payload_a, key)
        sha_a = hashlib.sha256(_canonical(payload_a)).hexdigest()
        r_a = _post_import(tc, artifact_a, key)
        assert r_a.status_code == 200, r_a.text
        assert fake.tables["teams"][0].get("last_import_sha256") == sha_a
        # B: broken manifest → 422 post-swap; ledger-clear FAILS → L stays A
        payload_b = self._payload("namespace: [broken", n_points=2)
        artifact_b = _build_artifact(payload_b, key)
        r_b = _post_import(tc, artifact_b, key)
        assert r_b.status_code == 422, r_b.text
        assert "invalid YAML" in r_b.json()["detail"]
        row = fake.tables["teams"][0]
        assert row.get("last_import_sha256") == sha_a  # STALE (clear failed)
        assert row.get("last_import_quarantined_sha256") != sha_a
        # re-import A → RE-SWAP (200 imported), NOT already (Q != A non-empty)
        r_a2 = _post_import(tc, artifact_a, key)
        assert r_a2.status_code == 200, r_a2.text
        assert r_a2.json()["imported"] is True
        assert r_a2.json()["already"] is False

    def test_registry_mode_clear_stamps_empty(self, sb_client):
        """Registry-mode ``_stamp_import_prop`` clear: the SET path must store
        the "" sentinel verbatim on the Team node (the clear is
        falsy-safe for every ledger consumer)."""
        from tortoise.hosted_api import _stamp_import_prop
        from tortoise.sdk import TortoiseSDK
        # Use the sb_client fixture: its SDK-init patch forces every SDK onto
        # the shared per-test import.db server — a fresh db_path here would
        # spawn a second embedded server that survives the conftest end-sweep
        # (redislite orphan gate, #1005/E2E-7).
        sdk = TortoiseSDK(namespace="registry")
        try:
            g = sdk._get_registry()
            g.query("CREATE (t:Team {id:$id})", params={"id": TEAM_ID})
            _stamp_import_prop(g, TEAM_ID, "last_import_sha256", "")
            rows = g.query(
                "MATCH (t:Team {id:$id}) RETURN t.last_import_sha256",
                params={"id": TEAM_ID},
            )
            assert rows.result_set[0][0] == ""
        finally:
            sdk.close()

    def test_import_with_pack_config_success_ledger_stamped(self, sb_client,
                                                            as_user):
        """Well-formed pack_config + valid manifest → 200; ledger stamped;
        quarantine prop cleared; re-import ``already: true``."""
        tc, fake, _ = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = self._payload(_CUSTOM_MANIFEST)
        artifact = _build_artifact(payload, key)
        sha = hashlib.sha256(_canonical(payload)).hexdigest()
        r = _post_import(tc, artifact, key)
        assert r.status_code == 200, r.text
        assert r.json()["imported"] is True
        row = fake.tables["teams"][0]
        assert row.get("last_import_sha256") == sha
        assert not row.get("last_import_quarantined_sha256")
        r2 = _post_import(tc, artifact, key)
        assert r2.status_code == 200, r2.text
        assert r2.json()["already"] is True


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
# CLI artifact form (#1390 — the `tortoise export` → import journey)
# ═══════════════════════════════════════════════════════════════════════════


class TestImportCliArtifactForm:
    """The ``tortoise export`` CLI (#1388) writes a single canonical-JSON
    artifact (encrypted blob inline as ``blob_b64``) — NOT the wire form
    (header line + raw blob). The endpoint must ingest the CLI output
    unchanged; the E2E-12 parity case (#1390) proves the full journey."""

    def test_import_cli_artifact_form_happy_path(self, sb_client, as_user):
        from tortoise.export import artifact_bytes, build_artifact

        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=3, n_edges=2)
        # Exactly what `tortoise export --output f` writes to disk.
        artifact = artifact_bytes(build_artifact(payload, key=key))
        assert b"\n" not in artifact  # single canonical JSON, blob_b64 inline
        r = _post_import(tc, artifact, key)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["imported"] is True
        assert body["restored"] == {"nodes": 3, "edges": 2}
        counts = _counts(db_path)
        assert counts["nodes"] == 3
        assert counts["edges"] == 2
        assert counts["ids"] == ["pt-0", "pt-1", "pt-2"]

    def test_import_cli_artifact_form_wrong_key_422(self, sb_client, as_user):
        """Wrong artifact key on the CLI form fails closed (fingerprint check)
        — same quarantine semantics as the wire form."""
        from tortoise.export import artifact_bytes, build_artifact

        tc, fake, db_path = sb_client  # noqa: RUF059
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        artifact = artifact_bytes(build_artifact(_build_payload(), key=key))
        r = _post_import(tc, artifact, os.urandom(32))
        assert r.status_code == 422, r.text
        assert "fingerprint" in r.json()["detail"]


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
        tc, fake, db_path = sb_client  # noqa: RUF059
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
                    orig = g.delete  # noqa: F841

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
