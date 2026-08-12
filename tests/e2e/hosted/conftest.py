"""Shared substrate for the hosted-platform E2E suite (#303 — 12 detailed cases).

Boots the REAL deployment artifact — `uvicorn tortoise.hosted_api:app` as a
subprocess on embedded FalkorDBLite (registry control plane) — and drives the
full customer journey over real HTTP with pytest-playwright APIRequestContext.

Run modes (RUN_LEGAL_E2E precedent, tests/e2e/test_signup_form_safety_e2e.py):

  RUN_HOSTED_E2E=1 python -m pytest tests/e2e/hosted/ -q -rs
      Local hermetic mode (CI default): the fixture boots the server.

  E2E_BASE_URL=https://staging.example.co ALLOW_PROD=1 RUN_HOSTED_E2E=1 ...
      Remote mode: no server boot; cases whose local-only seams don't apply
      (backup memory storage, selfhost daemon) skip per-test. https targets
      require ALLOW_PROD=1 (signup-safety precedent).

Every test module MUST call `skip_unless_hosted_e2e()` at import time so an
unconfigured environment skips gracefully with a clear message.

Env contract for the local server (see plan docs/plans/2026-08-12-303-hosted-e2e-suite.md):
  TORTOISE_DB_PATH (absolute), TORTOISE_CONTROL_PLANE=registry,
  SUPABASE_URL=<local JWKS mock>, TORTOISE_SECRET_PEPPER, FASTAPI_INTERNAL_KEY,
  RATE_LIMIT_DISABLED=1, TORTOISE_SESSION_EXTRACTION=regex,
  TORTOISE_BACKUP_KEY (base64 32B), TORTOISE_BACKUP_STORAGE=memory (#303 seam),
  BACKUP_WATCHER_DISABLED=1, TORTOISE_PRICING_PATH=<fixture>,
  STRIPE_WEBHOOK_SECRET + STRIPE_PRICE_IDS (local catalog — zero Stripe network),
  GITHUB_CLIENT_ID (no secret → token exchange stays gated).

The pytest process NEVER opens the server's DB file (redislite single-writer
hazard, tests/conftest.py) — every assertion goes over HTTP.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
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

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PRICING_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pricing-e2e.json"

WEBHOOK_SECRET = "whsec_e2e_local_303"
INTERNAL_KEY = "e2e-internal-key-303"
SECRET_PEPPER = "e2e-static-pepper-303"
PRICE_IDS = {
    "pro": {"monthly": "price_e2e_pro_monthly", "annual": "price_e2e_pro_annual"},
    "team": {"monthly": "price_e2e_team_monthly", "annual": "price_e2e_team_annual"},
    "e2e_small": {"monthly": "price_e2e_small_monthly", "annual": "price_e2e_small_annual"},
}

SUITE_ID = uuid.uuid4().hex[:8]


# ── Gating ───────────────────────────────────────────────────────────────────

def _gate_reason() -> str | None:
    """None = run; str = skip reason (clear message per #303 requirement).

    RUN_HOSTED_E2E=1 is ALWAYS required (RUN_LEGAL_E2E opt-in discipline);
    E2E_BASE_URL selects the target (default: fixture-booted local server)."""
    if not os.environ.get("RUN_HOSTED_E2E"):
        return ("hosted E2E suite: opt-in via RUN_HOSTED_E2E=1 (local hermetic "
                "server, or with E2E_BASE_URL=<url> for remote mode; "
                "non-loopback targets also need ALLOW_PROD=1)")
    url = os.environ.get("E2E_BASE_URL", "").strip()
    if url:
        # #303 (review r2): require ALLOW_PROD for ANY non-loopback target
        # regardless of scheme/case — an http:// (or HTTPS://) live
        # deployment or tunnel fronting prod must not slip past the gate.
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        if host not in ("localhost", "127.0.0.1", "::1") \
                and os.environ.get("ALLOW_PROD") != "1":
            return ("hosted E2E: E2E_BASE_URL is non-loopback — set "
                    "ALLOW_PROD=1 to run against a live/staging deployment")
    return None


GATE_REASON = _gate_reason()


def skip_unless_hosted_e2e() -> None:
    """Module-level gate — call at import time in every test module."""
    if GATE_REASON:
        # Visible even in -q collection (requirement: clear skip message).
        print(f"\n[hosted-e2e] {GATE_REASON}", file=sys.stderr)
        pytest.skip(GATE_REASON, allow_module_level=True)


def is_remote_mode() -> bool:
    return bool(os.environ.get("E2E_BASE_URL", "").strip())


# ── JWKS mock + session JWT mint (session-auth endpoints, E2E-6/8-D) ─────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class _JWKSKeys:
    """RSA keypair + JWKS document (cryptography — RS256, PKCS1v15+SHA256,
    matching session_auth._verify_rs256)."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.kid = "e2e-jwk-303"
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nums = self.private_key.public_key().public_numbers()
        self.jwks = {"keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid,
            "n": _b64url(nums.n.to_bytes((nums.n.bit_length() + 7) // 8, "big")),
            "e": _b64url(nums.e.to_bytes((nums.e.bit_length() + 7) // 8, "big")),
        }]}

    def mint(self, supabase_url: str, user_id: str, email: str | None = None,
             ttl_s: int = 3600) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT", "kid": self.kid}
        payload = {
            "iss": f"{supabase_url.rstrip('/')}/auth/v1",
            "aud": "authenticated",
            "sub": user_id,
            "email": email or f"{user_id[:12]}@e2e.premise-labs.dev",
            "iat": now,
            "exp": now + ttl_s,
        }
        signing_input = (
            f"{_b64url(json.dumps(header).encode())}."
            f"{_b64url(json.dumps(payload).encode())}"
        ).encode()
        sig = self.private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return signing_input.decode() + "." + _b64url(sig)


class _JWKSHandler(BaseHTTPRequestHandler):
    jwks_doc: bytes = b"{}"

    def do_GET(self):  # noqa: N802
        if self.path == "/auth/v1/.well-known/jwks.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(self.jwks_doc)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # keep CI logs clean
        pass


def _start_jwks_server(keys: _JWKSKeys) -> tuple[HTTPServer, str]:
    handler = type("_BoundJWKSHandler", (_JWKSHandler,),
                   {"jwks_doc": json.dumps(keys.jwks).encode()})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ── Server boot ──────────────────────────────────────────────────────────────

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_server_env(db_path: str, jwks_url: str, *, bare: bool) -> dict:
    """Explicit env contract — inherits the parent env, then applies the
    contract and SCRUBS vars that would silently flip server behavior
    (TORTOISE_DB_URI beats TORTOISE_DB_PATH in _make_sdk → a stale exported
    URI hangs the readiness poll; test_bridge_mcp documents the hazard)."""
    env = {**os.environ}
    # Blank (never pop) the vars that flip server mode/durability, AND the
    # bare-server unconfigured-contract vars: tortoise/mcp_server
    # ._load_dotenv runs in the fresh child interpreter (hosted_api imports
    # it at module scope) and refills only ABSENT keys from the repo .env —
    # a POPPED var is silently re-populated from a dev .env (TORTOISE_DB_URI
    # beats TORTOISE_DB_PATH in _make_sdk → the "hermetic" server targets
    # the dev DB), while an explicit "" blocks the refill (#303 review r2).
    # Non-bare servers re-set the Stripe/GitHub vars below; the bare server
    # keeps them blank for its unconfigured negatives (checkout/github 503).
    for var in ("TORTOISE_DB_URI", "FALKORDB_CLOUD_URI",
                "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY",
                "SUPABASE_ANON_KEY", "GITHUB_CLIENT_SECRET",
                "STRIPE_SECRET_KEY", "TORTOISE_BACKUP_STORAGE",
                "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_IDS",
                "GITHUB_CLIENT_ID"):
        env[var] = ""
    for var in ("TORTOISE_AUDIT_DSN", "RESEND_API_KEY", "BILLING_NOTIFY_TO",
                "POSTHOG_API_KEY", "POSTHOG_HOST", "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID", "DR_ISSUES_PAT", "R2_ACCOUNT_ID",
                "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET",
                # #303 (review r2): denylist scrub — parent-env secrets not
                # part of the server contract must not reach the child (it
                # runs the full production app code; inherited LLM keys flip
                # provider detection on). A full allowlist would be more
                # hermetic but risks dropping boot-required vars; blanking
                # the known secret families covers the realistic leak classes.
                "GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
                "OPENROUTER_API_KEY", "EXA_API_KEY", "PERPLEXITY_API_KEY",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                "TURNSTILE_SECRET_KEY", "REGISTRY_STREAM_KEY",
                "CLOUDFLARE_API_TOKEN"):
        env[var] = "" 
    env.update({
        "TORTOISE_DB_PATH": db_path,
        "TORTOISE_CONTROL_PLANE": "registry",
        "SUPABASE_URL": jwks_url,
        "TORTOISE_SECRET_PEPPER": SECRET_PEPPER,
        "FASTAPI_INTERNAL_KEY": INTERNAL_KEY,
        "RATE_LIMIT_DISABLED": "1",
        "TORTOISE_SESSION_EXTRACTION": "regex",
        "TORTOISE_BACKUP_KEY": base64.b64encode(os.urandom(32)).decode(),
        "TORTOISE_BACKUP_STORAGE": "memory",
        "BACKUP_WATCHER_DISABLED": "1",
        "TORTOISE_PRICING_PATH": str(PRICING_FIXTURE),
    })
    if not bare:
        env.update({
            "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET,
            # Dummy key so StripeClient() CONSTRUCTS for webhook signature
            # verification (it reads the key at __init__). Checkout is never
            # called with a valid price on this server (price validation 400s
            # before any Stripe network call), so the dummy can't leak to the
            # real Stripe API.
            "STRIPE_SECRET_KEY": "sk_test_e2e_dummy_303",
            "STRIPE_PRICE_IDS": json.dumps(PRICE_IDS),
            "GITHUB_CLIENT_ID": "e2e_client_id_303",
        })
    return env


class _ServerProc:
    def __init__(self, name: str, app: str, db_path: str, jwks_url: str,
                 *, bare: bool = False):
        self.name = name
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._log_path = Path(db_path).parent / f"{name}-uvicorn.log"
        env = _build_server_env(db_path, jwks_url, bare=bare)
        self._log_fh = open(self._log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", app,
             "--host", "127.0.0.1", "--port", str(self.port),
             "--log-level", "warning"],
            env=env, cwd=str(REPO_ROOT),
            stdout=self._log_fh, stderr=subprocess.STDOUT,
            # Own process group: the embedded redislite child dies with the
            # uvicorn parent on group kill (#176 orphan class).
            start_new_session=True,
        )

    def wait_ready(self, timeout_s: float = 60.0) -> None:
        import urllib.request

        deadline = time.time() + timeout_s
        last_err = "no attempt"
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"{self.name} died during boot (rc={self.proc.returncode}):\n"
                    f"{self.stderr_tail()}")
            try:
                with urllib.request.urlopen(f"{self.base_url}/health/ready",
                                            timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
            time.sleep(0.25)
        raise RuntimeError(
            f"{self.name} not ready after {timeout_s}s ({last_err}):\n"
            f"{self.stderr_tail()}")

    def stderr_tail(self, n: int = 40) -> str:
        try:
            lines = self._log_path.read_text(errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except Exception:  # noqa: BLE001
            return "(no log)"

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    self.proc.kill()
                self.proc.wait(timeout=5)
        try:
            self._log_fh.close()
        except Exception:  # noqa: BLE001
            pass


def _boot_with_retry(factory, attempts: int = 2):
    """Boot a server via factory() -> _ServerProc; retry on boot failure.

    Embedded FalkorDBLite version handshake is timing-sensitive under CPU
    contention (shared CI/dev machines) — a dead/slow boot is retried
    rather than failing the whole suite. Returns the live server."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        server = factory()
        try:
            server.wait_ready()
            return server
        except Exception as e:  # noqa: BLE001
            last_exc = e
            server.stop()
            if attempt == attempts:
                break
    raise RuntimeError(f"server failed to boot after {attempts} attempts: {last_exc}")


# ── Session fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def hosted_env():
    """The hosted platform under test: {base_url, remote, server?}.

    Remote mode (E2E_BASE_URL): no boot, no seams — cases skip per-test where
    local-only seams are required. Local mode: JWKS mock + uvicorn subprocess.
    """
    if GATE_REASON:
        pytest.skip(GATE_REASON)

    if is_remote_mode():
        yield {"base_url": os.environ["E2E_BASE_URL"].rstrip("/"), "remote": True,
               "server": None, "jwks": None, "supabase_url": None}
        return

    tmpdirs: list[str] = []
    keys = _JWKSKeys()
    jwks_srv, jwks_url = _start_jwks_server(keys)

    def _mk_hosted() -> "_ServerProc":
        # Fresh DB dir PER ATTEMPT — a partial redislite init must not
        # poison the retry (test_12 selfhost precedent, #176).
        d = tempfile.mkdtemp(prefix="tortoise_hosted_e2e_")
        tmpdirs.append(d)
        return _ServerProc("hosted", "tortoise.hosted_api:app",
                           os.path.join(d, "hosted.db"), jwks_url)

    try:
        server = _boot_with_retry(_mk_hosted)
    except Exception:
        jwks_srv.shutdown()
        raise
    yield {"base_url": server.base_url, "remote": False, "server": server,
           "jwks": keys, "supabase_url": jwks_url}
    server.stop()
    jwks_srv.shutdown()
    for d in tmpdirs:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session")
def bare_hosted_server(hosted_env):
    """Minimal-env second server (no Stripe/GitHub env) for unconfigured
    negatives: checkout → 503, webhook → 500, github connect → 503.
    Remote mode: skip (can't boot a bare prod variant)."""
    if hosted_env["remote"]:
        pytest.skip("bare server is local-only (hermetic negatives)")
    tmpdirs: list[str] = []
    keys = _JWKSKeys()
    jwks_srv, jwks_url = _start_jwks_server(keys)

    def _mk_bare() -> "_ServerProc":
        d = tempfile.mkdtemp(prefix="tortoise_hosted_e2e_bare_")
        tmpdirs.append(d)
        return _ServerProc("bare", "tortoise.hosted_api:app",
                           os.path.join(d, "bare.db"), jwks_url, bare=True)

    try:
        server = _boot_with_retry(_mk_bare)
    except Exception:
        jwks_srv.shutdown()
        raise
    yield server
    server.stop()
    jwks_srv.shutdown()
    for d in tmpdirs:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session")
def api(playwright, hosted_env):
    """Playwright APIRequestContext bound to the hosted base URL."""
    # 30s default per request: fail fast when the server degrades instead
    # of hanging the suite (local machine load / cold CI runner headroom).
    ctx = playwright.request.new_context(base_url=hosted_env["base_url"],
                                         timeout=30_000)
    yield ctx
    ctx.dispose()


@pytest.fixture(scope="session")
def session_jwt(hosted_env):
    """Mint Supabase-shaped session JWTs verified by the server's JWKS path."""
    if hosted_env["remote"]:
        pytest.skip("session JWT mint requires the local JWKS mock")

    def _mint(user_id: str | None = None, email: str | None = None,
              ttl_s: int = 3600) -> tuple[str, str]:
        uid = user_id or str(uuid.uuid4())
        return uid, hosted_env["jwks"].mint(hosted_env["supabase_url"], uid,
                                            email=email, ttl_s=ttl_s)

    return _mint


@pytest.fixture(scope="session")
def tenant_factory(api, hosted_env):
    """Disposable tenants via the public /v1/register surface.

    Local mode: fresh tenant per call (register limiter disabled). Remote
    mode: the server-side limit is 3/hr/IP — share a small pool instead.
    """
    created: list[dict] = []
    pool: list[dict] = []
    pool_idx = {"i": 0}

    def _register(label: str) -> dict:
        # Remote mode: server-side register limit is 3/hr/IP — rotate a pool
        # of 3 shared tenants (pairwise-isolation tests skip in remote mode).
        if hosted_env["remote"] and len(pool) >= 3:
            t = pool[pool_idx["i"] % 3]
            pool_idx["i"] += 1
            return t
        email = f"e2e-{SUITE_ID}-{label}-{uuid.uuid4().hex[:6]}@e2e.premise-labs.dev"
        r = api.post("/v1/register", data={"email": email, "password": "E2ePass-303-x"})
        assert r.status == 200, f"register failed: {r.status} {r.text()}"
        body = r.json()
        tenant = {"email": email, "api_key": body["api_key"],
                  "team_id": body["team_id"], "graph_name": body["graph_name"]}
        created.append(tenant)
        if hosted_env["remote"]:
            pool.append(tenant)
        return tenant

    yield _register


@pytest.fixture(autouse=True)
def _server_alive(hosted_env, request):
    """Crash detection: fail fast with the server log tail if the subprocess
    died mid-suite (instead of ~N raw connection-refused errors)."""
    server = hosted_env.get("server")
    if server is not None and server.proc.poll() is not None:
        pytest.fail(f"hosted server died before {request.node.name}:\n"
                    f"{server.stderr_tail()}")
    yield


# ── Billing helpers (hermetic tier bumps — zero Stripe network) ──────────────

def sign_stripe_event(event: dict, secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
    """(raw_body, Stripe-Signature header) — HMAC over the RAW bytes
    (billing.verify_webhook_signature contract, test_billing._sign precedent)."""
    body = json.dumps(event).encode()
    ts = int(time.time())
    sig = hmac_mod.new(secret.encode(), f"{ts}.".encode() + body,
                       hashlib.sha256).hexdigest()
    return body, f"t={ts},v1={sig}"


def bump_team_tier(api, team_id: str, tier: str, *,
                   customer: str | None = None) -> str:
    """Drive the real webhook path to a tier bump (E2E-3-D semantics):
    checkout.session.completed binds team↔customer via client_reference_id
    (no `subscription` field → zero Stripe API calls), then
    customer.subscription.updated resolves the price via the LOCAL catalog.
    Returns the stripe customer id used."""
    cust = customer or f"cus_e2e_{uuid.uuid4().hex[:10]}"
    price_key = {"monthly": PRICE_IDS[tier]["monthly"]}
    checkout = {
        "id": f"evt_e2e_co_{uuid.uuid4().hex[:8]}",
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": team_id,
            "customer": cust,
            "customer_details": {"email": f"e2e-{team_id[:8]}@e2e.premise-labs.dev"},
        }},
    }
    body, sig = sign_stripe_event(checkout)
    r = api.post("/webhooks/stripe", data=body,
                 headers={"Stripe-Signature": sig})
    assert r.status == 200, f"checkout webhook: {r.status} {r.text()}"

    sub_updated = {
        "id": f"evt_e2e_su_{uuid.uuid4().hex[:8]}",
        "type": "customer.subscription.updated",
        "data": {"object": {
            "customer": cust,
            "status": "active",
            "items": [{"price": {"id": price_key["monthly"]}}],
        }},
    }
    body, sig = sign_stripe_event(sub_updated)
    r = api.post("/webhooks/stripe", data=body,
                 headers={"Stripe-Signature": sig})
    assert r.status == 200, f"subscription webhook: {r.status} {r.text()}"
    return cust
