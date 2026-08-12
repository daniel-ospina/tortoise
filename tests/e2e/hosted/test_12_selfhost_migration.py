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
from __future__ import annotations

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

from conftest import REPO_ROOT, SECRET_PEPPER, is_remote_mode, skip_unless_hosted_e2e

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
        stdout=open(log_path, "wb"), stderr=subprocess.STDOUT,
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
            except Exception:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
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
