"""E2E-12-D — self-hoster migration to cloud (parity journey).

Reconstructed case (#303; no E2E-12-D marker survives — the user-journeys
epic's J-5 defines migration as "migrate to cloud anytime": fresh hosted team
+ connect; the repo has NO graph-import endpoint, so the testable contract is
the documented path: the selfhost daemon serves the local graph, the customer
registers a hosted team, replays the knowledge, and the hosted surface
answers with parity). Boots a REAL second server: uvicorn tortoise.selfhost:app
on its own embedded DB with static-key auth.

Negatives: wrong static key on selfhost → 401; the hosted tt_ key is rejected
by the selfhost daemon → 401 (keys are not portable across surfaces); the
migration destination rejects duplicate emails → 409. Skips in remote mode
(local selfhost server only).
"""
from __future__ import annotations  # noqa: I001

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

from conftest import (
    INTERNAL_KEY,
    REPO_ROOT,
    SECRET_PEPPER,
    is_remote_mode,
    skip_unless_hosted_e2e,
)

skip_unless_hosted_e2e()

pytestmark = pytest.mark.skipif(
    is_remote_mode(),
    reason="E2E-12-D boots a local selfhost daemon",
)

SELFHOST_KEY = "e2e-selfhost-static-key-303"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _boot_selfhost(tmpdir: str):
    port = _free_port()
    env = {**os.environ}
    # #303 (review r2): blank-not-pop — mcp_server._load_dotenv refills only
    # ABSENT keys from the repo .env in the fresh child, so a popped URI
    # would be re-populated and silently beat TORTOISE_DB_PATH.
    for var in ("TORTOISE_DB_URI", "FALKORDB_CLOUD_URI"):
        env[var] = ""
    env.update({
        "TORTOISE_DB_PATH": os.path.join(tmpdir, "selfhost.db"),
        "TORTOISE_API_KEY": SELFHOST_KEY,
        "TORTOISE_SECRET_PEPPER": SECRET_PEPPER,
    })
    log_path = Path(tmpdir) / f"selfhost-{port}.log"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "tortoise.selfhost:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env, cwd=str(REPO_ROOT),
        stdout=open(log_path, "wb"), stderr=subprocess.STDOUT,  # noqa: SIM115
        start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                tail = log_path.read_text(errors="replace")[-2000:]
                raise RuntimeError(f"selfhost died during boot:\n{tail}")
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=2) as r:
                    if r.status == 200:
                        return base, proc
            except Exception:  # noqa: BLE001, RUF100
                time.sleep(0.25)
        raise RuntimeError("selfhost not ready in 60s")
    except Exception:
        if proc.poll() is None:
            proc.kill()
        raise


@pytest.fixture(scope="module")
def selfhost_server():
    """Second REAL server: the selfhost daemon on its own DB (redislite
    file-lock: NEVER share the hosted server's path). Boots with retry —
    the FalkorDBLite handshake is timing-sensitive under CPU contention."""
    base = proc = None
    last_exc = None
    tmpdirs = []
    for _attempt in range(3):
        # fresh DB dir per attempt — a partial redislite init must not
        # poison the retry
        tmpdir = tempfile.mkdtemp(prefix="tortoise_e2e_selfhost_")
        tmpdirs.append(tmpdir)
        try:
            base, proc = _boot_selfhost(tmpdir)
            break
        except Exception as e:  # noqa: BLE001, RUF100
            last_exc = e  # _boot_selfhost kills its own proc on failure
    if base is None:
        raise RuntimeError(f"selfhost failed to boot after 3 attempts: {last_exc}")
    try:
        yield base
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        for d in tmpdirs:
            shutil.rmtree(d, ignore_errors=True)


def test_migration_journey_selfhost_to_hosted(api, selfhost_server, tenant_factory):
    """Positive chain: knowledge lives on the selfhost daemon → customer
    registers a hosted team → replays the knowledge → hosted parity."""
    sh = {"Authorization": f"Bearer {SELFHOST_KEY}"}
    knowledge = [f"selfhost memory {i} ({uuid.uuid4().hex[:4]})" for i in range(3)]
    for content in knowledge:
        r = api.post(f"{selfhost_server}/v1/points", headers=sh,
                     data={"content": content, "kind": "statement"})
        assert r.status == 200, f"selfhost write: {r.status} {r.text()}"

    r = api.get(f"{selfhost_server}/v1/points", headers=sh)
    assert r.status == 200, r.text()
    on_selfhost = {p["content"] for p in r.json()}
    assert set(knowledge) <= on_selfhost

    # The migration: fresh hosted team, knowledge replayed.
    t = tenant_factory("migration")
    hh = {"Authorization": f"Bearer {t['api_key']}"}
    for content in knowledge:
        r = api.post("/v1/points", headers=hh,
                     data={"content": content, "kind": "statement"})
        assert r.status == 200, r.text()

    hosted = {p["content"]
              for p in api.get("/v1/points", headers=hh).json()["points"]}
    assert set(knowledge) <= hosted, "hosted graph must reach selfhost parity"


def test_selfhost_rejects_wrong_and_foreign_keys(api, selfhost_server, tenant_factory):
    r = api.get(f"{selfhost_server}/v1/points",
                headers={"Authorization": "Bearer wrong-static-key"})
    assert r.status == 401, f"wrong static key must 401, got {r.status}"

    t = tenant_factory("migration-foreign")
    r = api.get(f"{selfhost_server}/v1/points",
                headers={"Authorization": f"Bearer {t['api_key']}"})
    assert r.status == 401, f"hosted tt_ key must not work on selfhost, got {r.status}"


def test_migration_destination_dup_email_409(api):
    email = f"e2e-mig-{uuid.uuid4().hex[:8]}@e2e.premise-labs.dev"
    r = api.post("/v1/register", data={"email": email, "password": "E2ePass-303-x"})
    assert r.status == 200, r.text()
    r = api.post("/v1/register", data={"email": email, "password": "E2ePass-303-x"})
    assert r.status == 409


def _provision_owner_tenant(api, user_id: str) -> dict:
    """Team + APIKey + owner Membership via /internal/provision (the E2E-6-D
    pattern): register-created teams have no Membership node, but the import
    endpoint is owner-scoped session auth — the parity journey needs a real
    owner tenant to POST /v1/teams/{team_id}/import and read the export."""
    team_id = f"e2e12p-{uuid.uuid4().hex[:10]}"
    r = api.post("/internal/provision",
                 headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
                 data={"team_id": team_id, "team_name": f"E2E12P {team_id[-6:]}",
                       "api_key_hash": "e2e:unused-placeholder-hash",
                       "created_by": user_id})
    assert r.status == 200, f"internal provision: {r.status} {r.text()}"
    return r.json()


def _seed_parity_source_graph(db_path: str) -> dict:
    """Build the selfhost source graph (embedded FalkorDBLite — the surface
    `tortoise export` reads) with pinned Point IDs, an operator node, and
    edges. Returns the source reference structure so the hosted graph can be
    asserted at parity: node/edge counts (the `MATCH` counts behind
    `tortoise_check_structure`), Point IDs, and the edge set (topology)."""
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection(db_path)
    try:
        g = proj.g
        ts = "2026-08-17T00:00:00Z"
        # 3 Points with pinned IDs (round-trip survival is assertable).
        for i in range(3):
            g.query(
                "CREATE (p:Point {id:$id, content:$c, pointKind:$k, "
                "status:'live', createdAt:$ts})",
                params={"id": f"parity-pt-{i}", "c": f"parity content {i}",
                        "k": "claim", "ts": ts},
            )
        # Operator node (Point with is_operator=true, pinned ID) + its edge
        # to the source point — the topology that must survive the round-trip.
        g.query(
            "CREATE (o:Point {id:$oid, is_operator:true, op_type:'IMPL', "
            "direction:'bidirectional', status:'live', createdAt:$ts})",
            params={"oid": "parity-op-0", "ts": ts},
        )
        g.query(
            "MATCH (o:Point {id:$oid}), (s:Point {id:$sid}) "
            "CREATE (o)-[:IMPL {idx:0}]->(s)",
            params={"oid": "parity-op-0", "sid": "parity-pt-0"},
        )
        # A direct (non-operator) edge between two Points.
        g.query(
            "MATCH (a:Point {id:$a}), (b:Point {id:$b}) "
            "CREATE (a)-[:IMPL {weight:0.8}]->(b)",
            params={"a": "parity-pt-1", "b": "parity-pt-2"},
        )
        # Epic #1647 (PR #1684 CI-fix): the exporter's node_count excludes
        # internal bookkeeping (the R2/R3 Meta FTS marker — #1625/#1641
        # class); the raw MATCH count includes it → 5 vs 4. Count non-skip
        # nodes with the SAME predicate the exporter uses so parity holds.
        from tortoise.hosted_api import _is_export_skip_node
        _rows = g.query("MATCH (n) RETURN labels(n), properties(n)").result_set
        nodes = sum(
            1 for row in _rows
            if not _is_export_skip_node(
                [str(l) for l in (row[0] or [])], dict(row[1] or {})))
        edges = int(g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0])
        ids = sorted(str(r[0]) for r in g.query(
            "MATCH (n:Point) RETURN coalesce(n.id, '')").result_set)
        edge_set = {
            (str(s), str(t), str(ty))
            for s, t, ty in g.query(
                "MATCH (a)-[r]->(b) RETURN coalesce(a.id, ''), "
                "coalesce(b.id, ''), type(r)").result_set
        }
        return {"nodes": nodes, "edges": edges, "ids": ids, "edge_set": edge_set}
    finally:
        proj.close()


def test_parity_export_import(api, session_jwt, tmp_path):
    """Epic #1230 Task 3 (#1390) — export→import parity journey.

    Beats the E2E-12-D baseline (test_migration_journey_selfhost_to_hosted
    asserts content-presence only): selfhost graph (points + operator + edges)
    → `tortoise export` subprocess (real CLI, encrypt-by-default) → fresh
    hosted team → POST /v1/teams/{team_id}/import → structure counts, Point
    IDs, and edge topology all match the source.

    Pinned name — referenced by the `-k parity` CI selector.
    """
    import base64 as _b64
    import json as _json

    # 1. Source graph (embedded) → export via the REAL CLI subprocess.
    db_path = str(tmp_path / "parity-source.db")
    ref = _seed_parity_source_graph(db_path)
    assert ref["nodes"] >= 4 and ref["edges"] >= 2  # 3 points + operator, 2 edges

    key = os.urandom(32)
    env = dict(os.environ, TORTOISE_BACKUP_KEY=_b64.b64encode(key).decode())
    out = str(tmp_path / "parity.tortoise")
    r = subprocess.run(
        [sys.executable, "-m", "tortoise", "export",
         "--db", db_path, "--output", out],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"tortoise export failed:\n{r.stderr}"
    summary = _json.loads(r.stdout.strip().splitlines()[-1])
    assert summary["status"] == "ok"
    assert summary["encrypted"] is True
    # The exporter's own structure counts agree with the source graph.
    assert summary["node_count"] == ref["nodes"]
    assert summary["edge_count"] == ref["edges"]

    # 2. Fresh hosted team (owner provisioned — import is owner-scoped
    #    session auth, mirroring the E2E-6-D export surface).
    user_id, tok = session_jwt()
    h = {"Authorization": f"Bearer {tok}"}
    team_id = _provision_owner_tenant(api, user_id)["team_id"]

    # 3. Import the artifact into the fresh team graph.
    r = api.post(
        f"/v1/teams/{team_id}/import",
        data=Path(out).read_bytes(),
        headers={**h,
                 "Content-Type": "application/vnd.tortoise.export.v1",
                 "X-Tortoise-Import-Key": _b64.b64encode(key).decode()},
    )
    assert r.status == 200, f"import: {r.status} {r.text()}"
    body = r.json()
    assert body["imported"] is True
    assert body["restored"] == {"nodes": ref["nodes"], "edges": ref["edges"]}

    # 4. Parity — structure, not content presence (strictly stronger than
    #    the E2E-12-D baseline). The owner export snapshot surfaces the same
    #    `MATCH (n) RETURN count(n)` / `MATCH ()-[r]->() RETURN count(r)`
    #    counts that back `tortoise_check_structure`.
    snapshot = api.get(f"/v1/teams/{team_id}/export", headers=h)
    assert snapshot.status == 200, snapshot.text()
    exp = snapshot.json()
    assert exp["summary"]["nodes"] == ref["nodes"]
    assert exp["summary"]["edges"] == ref["edges"]

    hosted_ids = {p.get("id") for p in exp["points"]}
    assert set(ref["ids"]) <= hosted_ids, "every source Point ID must survive"

    hosted_edges = {(e["source"], e["target"], e["type"]) for e in exp["edges"]}
    assert hosted_edges == ref["edge_set"], "edge topology must survive"

    # Operator node survives with its Point ID + operator props (topology).
    op = next(p for p in exp["points"] if p.get("id") == "parity-op-0")
    assert op.get("is_operator") is True
    assert op.get("op_type") == "IMPL"
    assert op.get("direction") == "bidirectional"
