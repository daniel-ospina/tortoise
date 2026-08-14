"""E2E-13-D — claim path: anonymous team attaches a verified identity (#1082).

Boots the REAL deployment artifact (`uvicorn tortoise.hosted_api:app`) in
SUPABASE control-plane mode with a local mock implementing the Supabase
surface (JWKS + PostgREST over the FakeControlPlane row store + GoTrue
/auth/v1/user). Drives the full journey over real HTTP:

  1. POST /v1/agent/signup  → tt_ key + anon team (identity-anchored owner)
  2. Pre-claim: GET /v1/team with the key → 200 (anon team, key auths)
  3. Claim: POST /v1/claim with a fresh provider-verified session JWT + the
     pasted key → 200, same team_id
  4. Post-claim session plane: GET /v1/teams (JWT) lists the claimed team,
     GET /v1/teams/{team_id}/members shows the linked owner — the claimed
     user sees graphs+members (indicator 2). Same key still auths (indicator
     1) and reads the same graph (indicator 3).
  5. First-claim-wins: a second user's claim → 409 (indicator 5).
  6. Double-provision guard: GET /v1/claim/status (key + JWT) reports the
     team claimable BEFORE the claim and claimed-by-me AFTER — welcome's
     Phase-2 mint never runs (the welcome page routes to the dashboard
     claim card; here we assert the server-side probe that backs it).

Gate: RUN_HOSTED_E2E=1 (local hermetic server, per the #303 convention).
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

try:
    from tests.e2e.hosted.conftest import skip_unless_hosted_e2e  # type: ignore
except Exception:  # pragma: no cover — import order fallback
    from .conftest import skip_unless_hosted_e2e

skip_unless_hosted_e2e()

from tests.fake_control_plane import FakeControlPlane  # noqa: E402

SUITE_TAG = "claim-e2e"
SERVICE_KEY = "svc_claim_e2e_1082"
SECRET_PEPPER = "e2e-claim-pepper-1082"
INTERNAL_KEY = "e2e-claim-internal-1082"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class _JWKS:
    """RSA keypair + JWKS doc (cryptography — RS256, matching session_auth)."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.kid = "claim-e2e-jwk"
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nums = self.private_key.public_key().public_numbers()
        self.jwks = {"keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid,
            "n": _b64url(nums.n.to_bytes((nums.n.bit_length() + 7) // 8, "big")),
            "e": _b64url(nums.e.to_bytes((nums.e.bit_length() + 7) // 8, "big")),
        }]}

    def mint(self, supabase_url: str, user_id: str, email: str,
             providers: list[str]) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT", "kid": self.kid}
        payload = {
            "iss": f"{supabase_url.rstrip('/')}/auth/v1",
            "aud": "authenticated", "sub": user_id, "email": email,
            "app_metadata": {"providers": providers},
            "iat": now, "exp": now + 3600,
        }
        signing_input = (
            f"{_b64url(json.dumps(header).encode())}."
            f"{_b64url(json.dumps(payload).encode())}"
        ).encode()
        sig = self.private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return signing_input.decode() + "." + _b64url(sig)


class _SupabaseMockHandler(BaseHTTPRequestHandler):
    """JWKS + PostgREST (FakeControlPlane dialect) + GoTrue /auth/v1/user."""

    jwks_doc: bytes = b"{}"
    cp: FakeControlPlane = FakeControlPlane()
    email_confirmed: bool = True

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/auth/v1/.well-known/jwks.json":
            self._send(200, self.jwks_doc)
            return
        if url.path == "/auth/v1/user":
            body = json.dumps(
                {"email_confirmed_at": "2026-08-13T00:00:00Z"}
                if self.email_confirmed else {}
            ).encode()
            self._send(200, body)
            return
        if url.path.startswith("/rest/v1/"):
            self._handle_rest_get(url)
            return
        self._send(404, b"{}")

    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path.startswith("/rest/v1/rpc/"):
            self._handle_rpc(url)
            return
        if url.path.startswith("/rest/v1/"):
            self._handle_rest_post(url)
            return
        self._send(404, b"{}")

    def _handle_rpc(self, url):
        fn = url.path.rsplit("/", 1)[-1]
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            result = self.cp.rpc(fn, body)
            self._send(200, b"{}" if result is None else json.dumps(result).encode())
        except RuntimeError as e:
            # PostgREST wraps RPC errors as 400 with the message embedded —
            # supabase_control.claim_membership maps the code from it.
            self._send(400, json.dumps({"message": str(e), "code": "P0001"}).encode())

    def _handle_rest_get(self, url):
        table = url.path[len("/rest/v1/"):].split("?")[0]
        qs = parse_qs(url.query)
        select = qs.get("select", [""])[0].split(",") if qs.get("select") else None
        filters = []
        for key, vals in qs.items():
            if key == "select":
                continue
            val = vals[0]
            if val.startswith("eq."):
                filters.append((key, "eq", val[3:]))
            elif val.startswith("neq."):
                filters.append((key, "neq", val[3:]))
            elif val.startswith("is.null"):
                filters.append((key, "is", None))
            elif val.startswith("gt."):
                filters.append((key, "gt", val[3:]))
            elif val.startswith("lt."):
                filters.append((key, "lt", val[3:]))
            elif val.startswith("lte."):
                filters.append((key, "lte", val[4:]))
            else:
                filters.append((key, "eq", val))
        try:
            rows = self.cp.query(table, select=select, filters=filters)
            self._send(200, json.dumps(rows).encode())
        except Exception as e:  # noqa: BLE001
            self._send(500, json.dumps({"message": str(e)}).encode())

    def _handle_rest_post(self, url):
        table = url.path[len("/rest/v1/"):].split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.cp.query(table, method="POST", json_body=body)
        self._send(201, json.dumps([body]).encode())

    def _send(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep CI logs clean
        pass


@pytest.fixture(scope="module")
def claim_server(tmp_path_factory):
    keys = _JWKS()
    cp = FakeControlPlane()
    handler = type("_BoundMock", (_SupabaseMockHandler,),
                   {"jwks_doc": json.dumps(keys.jwks).encode(), "cp": cp})
    mock_srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=mock_srv.serve_forever, daemon=True).start()
    mock_url = f"http://127.0.0.1:{mock_srv.server_address[1]}"

    db_path = str(tmp_path_factory.mktemp("claim-e2e-db") / "shared.db")
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "TORTOISE_DB_PATH": db_path,
        "TORTOISE_CONTROL_PLANE": "supabase",
        "SUPABASE_URL": mock_url,
        "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
        "SUPABASE_SERVICE_KEY": SERVICE_KEY,
        "SUPABASE_ANON_KEY": "anon-claim-e2e",
        "TORTOISE_SECRET_PEPPER": SECRET_PEPPER,
        "FASTAPI_INTERNAL_KEY": INTERNAL_KEY,
        "RATE_LIMIT_DISABLED": "1",
        "TORTOISE_SESSION_LLM_MOCK": "1",
        "TORTOISE_BACKUP_KEY": base64.b64encode(os.urandom(32)).decode(),
        "TORTOISE_BACKUP_STORAGE": "memory",
        "BACKUP_WATCHER_DISABLED": "1",
        "TORTOISE_PRICING_PATH": str(Path(__file__).resolve().parent / "fixtures" / "pricing-e2e.json"),
        "TORTOISE_AUDIT_DSN": "",
    }
    for var in ("TORTOISE_DB_URI", "FALKORDB_CLOUD_URI", "STRIPE_SECRET_KEY",
                "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_IDS", "GITHUB_CLIENT_ID"):
        env.setdefault(var, "")

    log_path = Path(db_path).parent / "claim-uvicorn.log"
    fh = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "tortoise.hosted_api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env, cwd=str(REPO_ROOT), stdout=fh, stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    def _ready():
        import urllib.request
        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"claim server died (rc={proc.returncode}):\n"
                    f"{Path(log_path).read_text(errors='replace')[-2000:]}")
            try:
                with urllib.request.urlopen(f"{base_url}/health/ready",
                                            timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.25)
        raise RuntimeError("claim server not ready")

    _ready()

    yield {
        "base_url": base_url,
        "mock_url": mock_url,
        "cp": cp,
        "keys": keys,
    }

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            proc.kill()
    mock_srv.shutdown()
    fh.close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _post(base, path, *, headers=None, body=None):
    import urllib.request
    req = urllib.request.Request(
        base + path, data=json.dumps(body or {}).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:  # noqa: F821
        return e.code, json.loads(e.read() or b"{}")


def _get(base, path, *, headers=None):
    import urllib.request
    req = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:  # noqa: F821
        return e.code, json.loads(e.read() or b"{}")


class TestClaimE2E:
    def test_claim_journey_claimed_user_sees_teams_and_members(self, claim_server):
        base = claim_server["base_url"]
        cp = claim_server["cp"]
        keys = claim_server["keys"]

        # 1. mint an anon team (key-possession anchor)
        status, signup = _post(base, "/v1/agent/signup", body={})
        assert status == 200, signup
        key = signup["key"]
        team_id = signup["team_id"]
        assert key.startswith("tt_")

        # 2. pre-claim: the key auths against the anon team
        status, team = _get(base, "/v1/team",
                            headers={"Authorization": f"Bearer {key}"})
        assert status == 200, team
        assert team["team_id"] == team_id

        # 3. welcome guard probe: claimable BEFORE the claim
        jwt_a = keys.mint(claim_server["mock_url"], "user-claim-a",
                          "claim-a@e2e.premise-labs.dev", ["github"])
        status, probe = _get(
            base, "/v1/claim/status",
            headers={"Authorization": f"Bearer {jwt_a}",
                     "X-Claim-Key": key})
        assert status == 200, probe
        assert probe["claimable"] is True
        assert probe["team_id"] == team_id

        # 4. claim: session JWT + pasted key → 200, same team
        status, claim = _post(
            base, "/v1/claim",
            headers={"Authorization": f"Bearer {jwt_a}"},
            body={"api_key": key})
        assert status == 200, claim
        assert claim["team_id"] == team_id

        # 5. post-claim: /v1/teams (JWT) lists the claimed team — the claimed
        #    user sees the team in the session plane (indicator 2)
        status, teams = _get(base, "/v1/teams",
                             headers={"Authorization": f"Bearer {jwt_a}"})
        assert status == 200, teams
        assert any(t["team_id"] == team_id for t in teams), teams

        # 6. members listing shows the linked owner (indicator 2)
        status, members = _get(
            base, f"/v1/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {jwt_a}"})
        assert status == 200, members
        assert any(m["user_id"] == "user-claim-a" and m["role"] == "owner"
                   for m in members), members

        # 7. same key still auths + reads the same graph (indicators 1 + 3)
        status, team2 = _get(base, "/v1/team",
                             headers={"Authorization": f"Bearer {key}"})
        assert status == 200, team2
        assert team2["team_id"] == team_id
        assert team2["anon"] is False

        # 8. welcome guard probe: claimed-by-me AFTER (no stray mint would be
        #    attempted; the probe reports the team is no longer claimable)
        status, probe2 = _get(
            base, "/v1/claim/status",
            headers={"Authorization": f"Bearer {jwt_a}",
                     "X-Claim-Key": key})
        assert status == 200, probe2
        assert probe2["claimable"] is False
        assert probe2["claimed"] is True

        # 9. first-claim-wins (indicator 5): a second user cannot claim
        jwt_b = keys.mint(claim_server["mock_url"], "user-claim-b",
                          "claim-b@e2e.premise-labs.dev", ["google"])
        status, second = _post(
            base, "/v1/claim",
            headers={"Authorization": f"Bearer {jwt_b}"},
            body={"api_key": key})
        assert status == 409, second
        assert "already" in str(second.get("detail", "")).lower()

        # 10. membership rows: exactly one owner, linked, identity cleared
        mems = [m for m in cp.tables["team_memberships"] if m["team_id"] == team_id]
        assert len(mems) == 1, mems
        assert mems[0]["user_id"] == "user-claim-a"
        assert mems[0]["identity"] is None
        team_row = next(t for t in cp.tables["teams"] if t["id"] == team_id)
        assert team_row["email"] == "claim-a@e2e.premise-labs.dev"

    def test_claim_requires_session_jwt_and_key(self, claim_server):
        base = claim_server["base_url"]
        # no JWT → 401
        status, body = _post(base, "/v1/claim", body={"api_key": "tt_x"})
        assert status == 401, body
        # password-only provider → 403 (provider-invariant fail-closed)
        jwt = claim_server["keys"].mint(claim_server["mock_url"], "user-pass",
                                        "pass@e2e.premise-labs.dev", ["email"])
        status, body = _post(base, "/v1/claim",
                             headers={"Authorization": f"Bearer {jwt}"},
                             body={"api_key": "tt_x"})
        assert status == 403, body
        # email_confirmed_at conjunct fail-closed
        claim_server["cp"]  # noqa: B018
        _SupabaseMockHandler.email_confirmed = False
        try:
            jwt2 = claim_server["keys"].mint(claim_server["mock_url"], "user-pass2",
                                             "pass2@e2e.premise-labs.dev", ["github"])
            status, body = _post(base, "/v1/claim",
                                 headers={"Authorization": f"Bearer {jwt2}"},
                                 body={"api_key": "tt_x"})
            assert status == 403, body
        finally:
            _SupabaseMockHandler.email_confirmed = True
