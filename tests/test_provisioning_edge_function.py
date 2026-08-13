"""E2E-1 source-level guard (#770 — plan Task 2): the tenant-provision Edge
Function must write the master list into Supabase ONLY.

The live E2E-1 registry assertion (registry_control_plane node count == 0
after signup) runs in the pre-deploy gate (plan Task 10). This test guards
the same contract at the source level so a regression can never ship: the
Edge Function must call provision_team (the atomic Supabase RPC), must NOT
call /internal/provision (the old registry writer) or update_user_team (the
old membership writer), must keep the data-plane demo seed, and must compute
lookup_hash via the shared TS mirror (parity with tortoise/auth.py).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
EDGE_FN = _REPO_ROOT / "supabase" / "functions" / "tenant-provision" / "index.ts"
SHARED_LOOKUP = _REPO_ROOT / "supabase" / "functions" / "_shared" / "lookup.ts"


def test_edge_function_parses():
    """The Edge Function must at least PARSE as TypeScript — a syntax error
    would make every signup 500 at deploy time. Regression guard for the
    duplicate-`const pepper` P0 caught in review (PR #847): string-assertion
    tests above cannot see redeclarations, and CI has no deno/tsc step, so
    node's type-stripping parser is the cheapest gate (node >= 22.18).
    Skips when node is absent."""
    node = shutil.which("node")
    if node is None:
        import pytest

        pytest.skip("node not available — edge-function parse check skipped")
    result = subprocess.run(
        [node, "--experimental-strip-types", "--check", str(EDGE_FN)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"tenant-provision/index.ts does not parse (exit {result.returncode}):\n"
        f"{result.stderr}"
    )


def test_edge_function_writes_supabase_only():
    """E2E-1: zero registry writes from the signup path (source contract)."""
    src = EDGE_FN.read_text()
    assert 'rpc("provision_team"' in src, (
        "Edge Function must write Supabase via the provision_team RPC"
    )
    # The string may appear in explanatory comments; the CALL must not.
    assert "fetch(`${fastApiUrl}/internal/provision`" not in src, (
        "Edge Function must not call the registry-writing /internal/provision"
    )
    assert 'rpc("update_user_team"' not in src, (
        "Edge Function must not call the removed update_user_team RPC"
    )
    # Data plane stays: the demo seed creates the team's knowledge-graph
    # namespace (FalkorDB) — that is NOT a registry write.
    assert "/internal/demo" in src, "Edge Function must keep the demo seed"
    # lookup_hash must be computed via the shared TS mirror (P1-1 parity).
    assert "lookupHash" in src, "Edge Function must compute lookup_hash"


def test_edge_function_uses_shared_lookup_mirror():
    """The TS mirror import must exist and stay pure (runs in Deno AND node —
    the parity test imports it; Deno-only APIs would break the parity run)."""
    assert SHARED_LOOKUP.exists(), "supabase/functions/_shared/lookup.ts missing"
    assert EDGE_FN.read_text().count("_shared/lookup.ts") == 1
    src = SHARED_LOOKUP.read_text()
    assert "Deno.env" not in src and "Deno.serve" not in src, (
        "lookup.ts must be pure (no Deno API calls) — the node parity test imports it"
    )
    assert "crypto.subtle.digest" in src


def test_shared_lookup_implements_plan_construction():
    """The construction must be the plan's exact scheme: SHA-256(pepper + key)
    — pepper FIRST (plan P1-1). The Python side is locked to the same order
    by tests/test_lookup_hash.py + the parity test."""
    src = SHARED_LOOKUP.read_text()
    assert "pepper + key" in src
    # the digest input must be pepper concatenated BEFORE the key
    assert "pepper + key" in src.replace(" ", "") or "encode(pepper + key)" in src


# ── CORS regression guards (production incident 2026-08-13) ──────────────────
# The welcome page calls tenant-provision DIRECTLY from the browser (JWT path,
# #527/#802). A `Content-Type: application/json` + `Authorization: Bearer`
# POST forces a CORS preflight; the function previously returned 405 with NO
# CORS headers, so every browser signup was blocked with "No
# 'Access-Control-Allow-Origin' header". These guards pin the fix: preflight
# handled before the method gate, CORS headers on EVERY response path, and an
# origin allowlist covering the welcome page's hosts (mirrors
# waitlist-subscribe's proven pattern).

def test_edge_function_answers_cors_preflight():
    """OPTIONS (preflight) must be answered 204 BEFORE the method gate, with
    methods/headers the welcome page actually sends."""
    src = EDGE_FN.read_text()
    assert 'if (req.method === "OPTIONS")' in src, (
        "OPTIONS preflight must be handled before the POST method gate"
    )
    assert 'if (req.method !== "POST")' in src
    # Preflight must come first (file order = runtime order).
    assert (
        src.index('req.method === "OPTIONS"')
        < src.index('req.method !== "POST"')
    ), "preflight must precede the method gate"
    assert "status: 204" in src, "preflight must return 204"
    assert '"Access-Control-Allow-Methods": "POST, OPTIONS"' in src
    # welcome.html sends Authorization: Bearer + Content-Type: json
    assert '"Access-Control-Allow-Headers": "authorization, content-type"' in src


def test_edge_function_sends_cors_headers_on_every_response():
    """Every response path must carry Access-Control-Allow-Origin + Vary so the
    browser can read success AND error bodies. Guarded via the single json()
    helper: no bare Response may remain outside it / the preflight."""
    src = EDGE_FN.read_text()
    assert "function json(" in src, "responses must go through the CORS json() helper"
    assert 'headers["Access-Control-Allow-Origin"] = corsOrigin ?? ALLOWED_ORIGINS[0];' in src
    assert 'headers["Vary"] = "Origin";' in src
    # The exact pre-incident regression: bare 405 with no CORS headers.
    assert 'new Response("Method not allowed", { status: 405 })' not in src, (
        "method gate must answer through json() (CORS headers), not a bare 405"
    )
    # Every remaining new Response must be ONE OF: the json() helper itself
    # (`{ status, headers }` — headers carries ACAO+Vary) or the preflight
    # (inline `"Access-Control-Allow-Origin"` map). A Response whose options
    # only name `corsOrigin` in a THIRD constructor position would be a
    # silent no-op — the Response constructor takes (body, init) only — so
    # any other pattern fails this check.
    import re

    for match in re.finditer(r"new Response\s*\(", src):
        chunk = src[match.start():match.start() + 300]
        assert (
            "{ status, headers }" in chunk
            or '"Access-Control-Allow-Origin"' in chunk
        ), (
            f"Response at offset {match.start()} lacks CORS headers "
            f"(Response(body, init) only — a third corsOrigin arg is ignored):\n{chunk}"
        )


def test_edge_function_allowlists_welcome_page_origins():
    """The allowlist must cover every host the welcome page is served on:
    the tortoise product host (the reported failure origin), the company host,
    Cloudflare previews, and local wrangler dev."""
    src = EDGE_FN.read_text()
    assert "https://tortoise.premiselabs.co" in src
    assert "https://premiselabs.co" in src
    assert ".premise-labs.pages.dev" in src
    assert "http://127.0.0.1:8788" in src and "http://localhost:8788" in src


def test_edge_function_rejects_unknown_origins():
    """Non-allowlisted browser origins must get 403 with CORS headers — never
    an echoed ACAO (would grant arbitrary sites access to team minting)."""
    src = EDGE_FN.read_text()
    assert 'if (requestOrigin && !originAllowed(requestOrigin))' in src, (
        "browser requests must pass the origin allowlist gate"
    )
    assert 'json({ error: "Origin not allowed" }, 403, corsOrigin)' in src
    # No wildcard CORS anywhere — team minting must stay origin-scoped.
    assert '"Access-Control-Allow-Origin": "*"' not in src
