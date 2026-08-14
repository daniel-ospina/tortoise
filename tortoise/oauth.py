"""OAuth 2.1 server for remote MCP auth — auth-code + PKCE, DCR, RFC 8707 (#524).

The hosted MCP endpoint (``/mcp``) accepts two Bearer credentials:
  * ``tt_<key>``     — the pre-existing tenant API key (D3: permanent,
                       documented fallback; MCP-only additive, never breaking)
  * ``oat_<token>``  — an OAuth 2.1 access token minted by this module

Flow (locked scoping decisions 2026-08-15, docs/scoping/2026-08-15-524-oauth-mcp-scoping.md):
  * P1 — discovery: RFC 9728 Protected Resource Metadata at
    ``/.well-known/oauth-protected-resource[/mcp]`` + RFC 8414 Authorization
    Server Metadata at ``/.well-known/oauth-authorization-server[/mcp]``
    (served by hosted_api).
  * P2 — authorization code + PKCE (RFC 7636, S256 only) against the Supabase
    control plane. The browser session JWT is verified server-side with the
    existing JWKS/RS256 path (session_auth.verify_session_jwt — D2: "reuse
    JWKS verify"). Branded consent = ONE custom HTML page (D2).
  * P3 — Dynamic Client Registration (RFC 7591) at ``POST /register`` (D1).
  * P4 — token→team mapping via RFC 8707 resource indicator, client-declared
    (D4, no picker UI): the resource is ``{origin}/mcp`` (single-membership
    users resolve to their sole active team) or ``{origin}/mcp/teams/{team_id}``
    (explicit team). Rotating refresh tokens per (user, team), revoked on team
    suspension (D5).
  * D6 — OAuth tokens are self-sufficient at the MCP boundary: the middleware
    introspects the access token row (no tt_ key minting); the session→key
    bridge (POST /v1/session/key) stays for dashboard flows.

Storage: control-plane tables ``oauth_clients`` / ``oauth_codes`` /
``oauth_access_tokens`` / ``oauth_refresh_tokens`` (migration 0016) via the
PostgREST seam (functions take ``cp`` explicitly — the FakeControlPlane
interface used by the test suite implements the same query() dialect).
Secrets are stored as SHA-256 hashes only (mirrors api_keys.lookup_hash).
Selfhost/registry mode: OAuth is hosted-only (D3) — the functional endpoints
fail closed with 503 via hosted_api; ``tt_`` keys keep working unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse

# ── Protocol constants ──────────────────────────────────────────────────────

SCOPES_SUPPORTED = ["mcp"]
ACCESS_TOKEN_TTL_S = int(os.environ.get("TORTOISE_OAUTH_ACCESS_TTL", "3600"))
REFRESH_TOKEN_TTL_S = int(os.environ.get("TORTOISE_OAUTH_REFRESH_TTL",
                                          str(30 * 24 * 3600)))
AUTH_CODE_TTL_S = int(os.environ.get("TORTOISE_OAUTH_CODE_TTL", "600"))
# Distinct prefixes so the MCP auth middleware can route Bearer tokens without
# a table scan (tt_ = tenant key, oat_ = OAuth access token). Refresh tokens
# are never presented to /mcp — the prefix is a debugging aid.
ACCESS_TOKEN_PREFIX = "oat_"
REFRESH_TOKEN_PREFIX = "ort_"
GRANT_AUTHORIZATION_CODE = "authorization_code"
GRANT_REFRESH_TOKEN = "refresh_token"
SUPPORTED_GRANTS = {GRANT_AUTHORIZATION_CODE, GRANT_REFRESH_TOKEN}
SUPPORTED_RESPONSE_TYPES = {"code"}
SUPPORTED_AUTH_METHODS = {"none", "client_secret_post"}
_PKCE_CHARSET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


class OAuthError(Exception):
    """OAuth protocol error → RFC 6749 §5.2 JSON error response.

    ``status`` is the HTTP status (400/401/403); ``error`` is the RFC error
    code (invalid_request / invalid_grant / unauthorized_client / ...);
    ``error_description`` is a human-readable, client-safe explanation.
    """

    def __init__(self, status: int, error: str, error_description: str):
        super().__init__(error_description)
        self.status = status
        self.error = error
        self.error_description = error_description

    def body(self) -> dict:
        return {"error": self.error, "error_description": self.error_description}


# ── Small helpers ───────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _sha256(value: str) -> str:
    """Hex digest — the stored form for codes/tokens/secrets (never plaintext)."""
    return hashlib.sha256(value.encode()).hexdigest()


def _new_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def _expires_iso(ttl_s: int) -> str:
    return (_now() + timedelta(seconds=ttl_s)).isoformat()


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _valid_pkce(value: str) -> bool:
    """RFC 7636 §4.1/§4.2: 43-128 chars from the unreserved alphabet."""
    return (isinstance(value, str) and 43 <= len(value) <= 128
            and all(c in _PKCE_CHARSET for c in value))


def _verify_pkce(code_verifier: str, code_challenge: str,
                 method: str = "S256") -> bool:
    """S256 only (MCP spec / RFC 9700 hardening — 'plain' is rejected)."""
    if not _valid_pkce(code_verifier) or not _valid_pkce(code_challenge):
        return False
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(computed, code_challenge)


def _is_loopback(hostname: str) -> bool:
    """RFC 8252 §7.3: native-app loopback redirect URIs (localhost or a
    loopback address). Anything else must be https."""
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _valid_redirect_uri(uri: str) -> bool:
    """A registration-acceptable redirect URI: https, or http only when the
    host is loopback (RFC 8252 native-app pattern used by MCP clients)."""
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if parsed.scheme == "https" and parsed.hostname:
        return True
    if parsed.scheme == "http" and parsed.hostname and _is_loopback(parsed.hostname):
        return True
    return False


def mcp_resource_url(base: str) -> str:
    """Canonical RFC 8707 resource URI for the MCP surface at ``base``."""
    return base.rstrip("/") + "/mcp"


def team_resource_url(base: str, team_id: str) -> str:
    """Team-scoped resource indicator (D4 — the client-declared team selector)."""
    return mcp_resource_url(base) + "/teams/" + team_id


# ── RFC 8707 resource → team mapping (P4, D4) ──────────────────────────────

def parse_resource(base: str, resource: str | None) -> tuple[str | None, str | None]:
    """Split a client-declared resource indicator into (canonical, team_id).

    Returns (mcp_resource, None) for the bare MCP resource (team resolved
    from the user's memberships), (mcp_resource, team_id) for a team-scoped
    resource, or raises OAuthError for anything outside the MCP resource
    tree (RFC 8707 §2 — the AS must reject unknown resource values so a
    token can never be minted for a resource the client does not declare).
    """
    base_mcp = mcp_resource_url(base)
    if not resource:
        return base_mcp, None
    resource = resource.strip().rstrip("/")
    if resource == base_mcp:
        return base_mcp, None
    team_prefix = base_mcp + "/teams/"
    if resource.startswith(team_prefix) and "/" not in resource[len(team_prefix):]:
        team_id = resource[len(team_prefix):]
        if team_id:
            return resource, team_id
    raise OAuthError(400, "invalid_resource",
                     "Unknown resource indicator. Expected the MCP endpoint "
                     f"({base_mcp}) or a team-scoped resource under it.")


def _default_team(cp, user_id: str) -> str:
    """The user's sole active team (D4: no picker UI). 0 teams → error;
    >1 teams → error telling the client to declare a team-scoped resource."""
    from tortoise.supabase_control import user_memberships
    memberships = user_memberships(cp, user_id)
    if len(memberships) == 1:
        return memberships[0]["team_id"]
    if not memberships:
        raise OAuthError(403, "invalid_grant",
                         "This account has no team. Create a team before "
                         "connecting an MCP client.")
    raise OAuthError(400, "invalid_resource",
                     "This account belongs to multiple teams — the MCP client "
                     "must declare a team-scoped resource indicator "
                     f"({team_resource_url('<base>', '<team_id>')} form).")


def _resolve_team(cp, user_id: str, base: str, resource: str | None) -> str:
    """RFC 8707 mapping (D4): client-declared resource → team_id, verified
    against the user's active memberships."""
    _, team_id = parse_resource(base, resource)
    if team_id is not None:
        from tortoise.supabase_control import membership_for_user_team
        if membership_for_user_team(cp, user_id, team_id) is None:
            raise OAuthError(403, "invalid_resource",
                             "Not a member of the requested team.")
        return team_id
    return _default_team(cp, user_id)


def _team_name(cp, team_id: str) -> str | None:
    rows = cp.query("teams", select=["name"], filters=[("id", "eq", team_id)])
    return rows[0].get("name") if rows else None


# ── Client registry (P3 — DCR, RFC 7591, D1) ───────────────────────────────

def _client_row(cp, client_id: str) -> dict | None:
    rows = cp.query("oauth_clients", select=[
        "id", "client_secret_hash", "client_name", "redirect_uris",
        "grant_types", "response_types", "token_endpoint_auth_method",
        "scope", "created_at", "revoked_at",
    ], filters=[("id", "eq", client_id)])
    return rows[0] if rows else None


def get_client(cp, client_id: str) -> dict | None:
    """Public registration lookup (no secret material)."""
    row = _client_row(cp, client_id)
    if row is None:
        return None
    if row.get("revoked_at") is not None:
        return None
    return row


def register_client(cp, body: dict) -> dict:
    """RFC 7591 DCR — validate metadata, mint client_id (+ secret for
    confidential clients), persist, return the full registration response."""
    if not isinstance(body, dict):
        raise OAuthError(400, "invalid_client_metadata", "Request body must be a JSON object.")

    client_name = body.get("client_name")
    if not isinstance(client_name, str) or not client_name.strip():
        raise OAuthError(400, "invalid_client_metadata",
                         "client_name is required (non-empty string).")
    if len(client_name) > 100:
        raise OAuthError(400, "invalid_client_metadata", "client_name is too long (max 100).")

    redirect_uris = body.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise OAuthError(400, "invalid_client_metadata",
                         "redirect_uris is required (non-empty array of absolute URIs).")
    invalid = [u for u in redirect_uris if not isinstance(u, str) or not _valid_redirect_uri(u)]
    if invalid:
        raise OAuthError(400, "invalid_client_metadata",
                         "Each redirect_uri must be https (or http loopback), "
                         "absolute, and may not contain a fragment.")

    grant_types = body.get("grant_types", ["authorization_code"])
    if not isinstance(grant_types, list) or not grant_types:
        raise OAuthError(400, "invalid_client_metadata", "grant_types must be a non-empty array.")
    unknown = [g for g in grant_types if g not in SUPPORTED_GRANTS]
    if unknown:
        raise OAuthError(400, "invalid_client_metadata",
                         f"Unsupported grant_types: {unknown}")

    response_types = body.get("response_types", ["code"])
    if not isinstance(response_types, list) or not response_types:
        raise OAuthError(400, "invalid_client_metadata", "response_types must be a non-empty array.")
    if not set(response_types).issubset(SUPPORTED_RESPONSE_TYPES):
        raise OAuthError(400, "invalid_client_metadata",
                         "Only response_type 'code' is supported.")

    auth_method = body.get("token_endpoint_auth_method", "none")
    if auth_method not in SUPPORTED_AUTH_METHODS:
        raise OAuthError(400, "invalid_client_metadata",
                         f"token_endpoint_auth_method must be one of "
                         f"{sorted(SUPPORTED_AUTH_METHODS)}.")

    scope = body.get("scope")
    if scope is None:
        scope = " ".join(SCOPES_SUPPORTED)
    if not isinstance(scope, str):
        raise OAuthError(400, "invalid_client_metadata", "scope must be a string.")
    requested = scope.split()
    if any(s not in SCOPES_SUPPORTED for s in requested):
        raise OAuthError(400, "invalid_client_metadata",
                         f"Unsupported scope. Supported: {SCOPES_SUPPORTED}")

    client_id = _new_token("ct_")
    client_secret = _new_token("cs_") if auth_method == "client_secret_post" else None
    now = _now_iso()
    cp.query("oauth_clients", method="POST", json_body={
        "id": client_id,
        "client_secret_hash": _sha256(client_secret) if client_secret else None,
        "client_name": client_name.strip(),
        "redirect_uris": redirect_uris,
        "grant_types": grant_types,
        "response_types": response_types,
        "token_endpoint_auth_method": auth_method,
        "scope": scope,
        "created_at": now,
        "revoked_at": None,
    })
    resp = {
        "client_id": client_id,
        "client_id_issued_at": int(_now().timestamp()),
        "client_name": client_name.strip(),
        "redirect_uris": redirect_uris,
        "grant_types": grant_types,
        "response_types": response_types,
        "token_endpoint_auth_method": auth_method,
        "scope": scope,
        "client_secret_expires_at": 0,
    }
    if client_secret:
        resp["client_secret"] = client_secret
    return resp


def _verify_client_auth(cp, client_id: str, body: dict) -> dict:
    """Token-endpoint client authentication (public-client default).

    Public clients (token_endpoint_auth_method="none") authenticate by
    presenting client_id in the body (PKCE binds the code exchange).
    Confidential clients (client_secret_post) must additionally present the
    issued client_secret. Raises unauthorized_client / invalid_client.
    """
    if not client_id:
        raise OAuthError(401, "invalid_client", "client_id is required.")
    row = _client_row(cp, client_id)
    if row is None or row.get("revoked_at") is not None:
        raise OAuthError(401, "invalid_client", "Unknown client_id.")
    method = row.get("token_endpoint_auth_method") or "none"
    if method == "client_secret_post":
        secret = body.get("client_secret")
        stored = row.get("client_secret_hash")
        if not secret or not stored or not secrets.compare_digest(
                _sha256(secret), stored):
            raise OAuthError(401, "invalid_client", "Invalid client credentials.")
    elif method != "none":
        raise OAuthError(401, "unauthorized_client",
                         f"Unsupported client auth method: {method}")
    return row


# ── Authorization code + consent (P2) ──────────────────────────────────────

def validate_authorize_params(cp, *, client_id: str, redirect_uri: str | None,
                              response_type: str | None,
                              code_challenge: str | None,
                              code_challenge_method: str | None) -> dict:
    """Validate the /oauth/authorize request. Returns the client row."""
    client = get_client(cp, client_id)
    if client is None:
        raise OAuthError(400, "invalid_request", "Unknown or revoked client_id.")
    if response_type != "code":
        raise OAuthError(400, "invalid_request",
                         "Only response_type=code is supported.")
    if redirect_uri not in (client.get("redirect_uris") or []):
        raise OAuthError(400, "invalid_request",
                         "redirect_uri is not registered for this client.")
    if not code_challenge or not _valid_pkce(code_challenge):
        raise OAuthError(400, "invalid_request",
                         "code_challenge (PKCE, 43-128 chars) is required.")
    if code_challenge_method != "S256":
        raise OAuthError(400, "invalid_request",
                         "Only code_challenge_method=S256 is supported.")
    return client


def consent_preview(cp, user_id: str, base: str, resource: str | None) -> dict:
    """Consent-page team preview (D4): resolve the team the grant would bind."""
    team_id = _resolve_team(cp, user_id, base, resource)
    return {
        "team_id": team_id,
        "team_name": _team_name(cp, team_id),
        "resource": team_resource_url(base, team_id) if resource else mcp_resource_url(base),
    }


def issue_auth_code(cp, *, client_id: str, user_id: str, base: str,
                    redirect_uri: str, code_challenge: str, state: str | None,
                    scope: str | None, resource: str | None) -> tuple[str, str]:
    """Bind a (user, team) grant to a single-use PKCE code (P2 + P4).

    Resolves the team from the client-declared resource indicator (RFC 8707)
    at consent time so the code carries the exact team the token will bind.
    Returns (code, team_id).
    """
    team_id = _resolve_team(cp, user_id, base, resource)
    code = secrets.token_urlsafe(32)
    cp.query("oauth_codes", method="POST", json_body={
        "code_hash": _sha256(code),
        "client_id": client_id,
        "user_id": user_id,
        "team_id": team_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": scope or " ".join(SCOPES_SUPPORTED),
        "resource": resource,
        "expires_at": _expires_iso(AUTH_CODE_TTL_S),
        "used_at": None,
        "created_at": _now_iso(),
    })
    return code, team_id


def _consume_code(cp, code: str) -> dict:
    """Single-use auth-code redemption (RFC 6749 §4.1.2)."""
    rows = cp.query("oauth_codes", select=[
        "code_hash", "client_id", "user_id", "team_id", "redirect_uri",
        "code_challenge", "code_challenge_method", "scope", "resource",
        "expires_at", "used_at",
    ], filters=[("code_hash", "eq", _sha256(code))])
    if not rows:
        raise OAuthError(400, "invalid_grant", "Invalid authorization code.")
    row = rows[0]
    if row.get("used_at") is not None:
        raise OAuthError(400, "invalid_grant",
                         "Authorization code already used.")
    if _parse_ts(row.get("expires_at")) is None or _parse_ts(row["expires_at"]) < _now():
        raise OAuthError(400, "invalid_grant", "Authorization code expired.")
    cp.query("oauth_codes", method="PATCH",
             filters=[("code_hash", "eq", _sha256(code))],
             json_body={"used_at": _now_iso()})
    return row


def _assert_team_usable(cp, team_id: str) -> None:
    """D5: a suspended team cannot mint/refresh tokens. The durable
    suspended_at check is the single rejection authority (mirrors the tt_
    path's #308 semantics)."""
    rows = cp.query("teams", select=["suspended_at", "tier"],
                    filters=[("id", "eq", team_id)])
    if not rows:
        raise OAuthError(403, "invalid_grant", "Team not found.")
    if rows[0].get("suspended_at") is not None:
        raise OAuthError(403, "invalid_grant",
                         "Team is suspended — OAuth tokens revoked. "
                         "Contact support to appeal.")


# ── Token issuance / exchange (P2 + P4 + D5) ────────────────────────────────

def _quota_fields(team_row: dict) -> dict:
    """Quota shape shared with resolve_api_key so REST/MCP limits match
    (#329): preserve None (unlimited, Team tier), fall back to pricing."""
    from tortoise.pricing import tier_limits
    from tortoise.quota import DEFAULT_MAX_SESSIONS
    from tortoise.quota import derived_tier
    tier = derived_tier({**team_row, "id": team_row.get("id")})
    lim = tier_limits(tier)
    return {
        "team_id": team_row.get("id"),
        "tier": tier,
        "max_users": (team_row.get("max_users")
                      if team_row.get("max_users") is not None
                      else lim["max_users_per_team"]),
        "max_graphs": (team_row.get("max_graphs")
                       if team_row.get("max_graphs") is not None
                       else lim["max_graphs_per_team"]),
        "max_points": (int(team_row["graph_size_cap"])
                       if team_row.get("graph_size_cap") is not None
                       else int(lim["max_graph_nodes"])),
        "max_api_keys": lim["max_api_keys"],
        "max_sessions": DEFAULT_MAX_SESSIONS,
        "suspended_at": team_row.get("suspended_at"),
        "flagged_at": team_row.get("flagged_at"),
        "email": team_row.get("email"),
    }


def _team_row(cp, team_id: str) -> dict | None:
    rows = cp.query("teams", select=[
        "id", "tier", "max_users", "max_graphs", "graph_size_cap",
        "suspended_at", "flagged_at", "email",
    ], filters=[("id", "eq", team_id)])
    return rows[0] if rows else None


def _issue_tokens(cp, *, client_id: str, user_id: str, team_id: str,
                  scope: str, resource: str | None,
                  prev_refresh: dict | None = None,
                  prev_access_id: str | None = None) -> dict:
    """Mint an access+refresh pair; rotate (revoke) the previous pair when
    called from the refresh path (D5 rotation)."""
    if prev_refresh is not None:
        cp.query("oauth_refresh_tokens", method="PATCH",
                 filters=[("id", "eq", prev_refresh["id"])],
                 json_body={"revoked_at": _now_iso()})
    if prev_access_id:
        cp.query("oauth_access_tokens", method="PATCH",
                 filters=[("id", "eq", prev_access_id)],
                 json_body={"revoked_at": _now_iso()})
    access = _new_token(ACCESS_TOKEN_PREFIX)
    refresh = _new_token(REFRESH_TOKEN_PREFIX)
    refresh_id = secrets.token_urlsafe(16)
    access_id = secrets.token_urlsafe(16)
    now = _now_iso()
    cp.query("oauth_refresh_tokens", method="POST", json_body={
        "id": refresh_id,
        "token_hash": _sha256(refresh),
        "client_id": client_id,
        "user_id": user_id,
        "team_id": team_id,
        "scope": scope,
        "expires_at": _expires_iso(REFRESH_TOKEN_TTL_S),
        "revoked_at": None,
        "rotated_from": prev_refresh["id"] if prev_refresh is not None else None,
        "created_at": now,
    })
    cp.query("oauth_access_tokens", method="POST", json_body={
        "id": access_id,
        "token_hash": _sha256(access),
        "client_id": client_id,
        "user_id": user_id,
        "team_id": team_id,
        "scope": scope,
        "expires_at": _expires_iso(ACCESS_TOKEN_TTL_S),
        "revoked_at": None,
        "refresh_token_id": refresh_id,
        "created_at": now,
    })
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_S,
        "refresh_token": refresh,
        "scope": scope,
        "_refresh_id": refresh_id,
        "_access_id": access_id,
    }


def exchange_auth_code(cp, body: dict, base: str) -> dict:
    """POST /oauth/token grant_type=authorization_code (P2 + P4).

    Validates the PKCE verifier, redirect_uri, client auth, and the RFC 8707
    resource (must map to the SAME team the code was bound to), then issues
    the access+refresh pair.
    """
    client = _verify_client_auth(cp, body.get("client_id"), body)
    code_row = _consume_code(cp, body.get("code", ""))
    if code_row["client_id"] != client["id"]:
        raise OAuthError(400, "invalid_grant",
                         "Authorization code was issued to a different client.")
    if body.get("redirect_uri") != code_row["redirect_uri"]:
        raise OAuthError(400, "invalid_grant", "redirect_uri mismatch.")
    if not _verify_pkce(body.get("code_verifier", ""),
                        code_row["code_challenge"],
                        code_row.get("code_challenge_method") or "S256"):
        raise OAuthError(400, "invalid_grant", "PKCE verification failed.")
    # RFC 8707: the resource at the token endpoint must resolve to the same
    # team the authorization code was bound to (lenient when omitted — the
    # mcp SDK always sends it, but a bare authorize→token pair is legal).
    resource = body.get("resource")
    if resource:
        _, requested_team = parse_resource(base, resource)
        if requested_team is not None and requested_team != code_row["team_id"]:
            raise OAuthError(400, "invalid_grant",
                             "Resource indicator does not match the authorized team.")
    _assert_team_usable(cp, code_row["team_id"])
    scope = code_row.get("scope") or " ".join(SCOPES_SUPPORTED)
    out = _issue_tokens(cp, client_id=client["id"], user_id=code_row["user_id"],
                        team_id=code_row["team_id"], scope=scope,
                        resource=code_row.get("resource"))
    return {k: v for k, v in out.items() if not k.startswith("_")}


def _revoke_team_family(cp, user_id: str, team_id: str) -> None:
    """D5: revoke the user's ENTIRE refresh-token family for a team (called
    on team suspension). Mirrors durable revocation semantics of api_keys."""
    cp.query("oauth_refresh_tokens", method="PATCH",
             filters=[("user_id", "eq", user_id), ("team_id", "eq", team_id),
                      ("revoked_at", "is", None)],
             json_body={"revoked_at": _now_iso()})


def refresh_grant(cp, body: dict, base: str) -> dict:
    """POST /oauth/token grant_type=refresh_token (D5).

    Rotating per (user, team): each use revokes the presented token and mints
    a fresh pair. Team suspension revokes the whole (user, team) family;
    a lapsed membership revokes the presented token.
    """
    client = _verify_client_auth(cp, body.get("client_id"), body)
    refresh_token = body.get("refresh_token", "")
    rows = cp.query("oauth_refresh_tokens", select=[
        "id", "token_hash", "client_id", "user_id", "team_id", "scope",
        "expires_at", "revoked_at",
    ], filters=[("token_hash", "eq", _sha256(refresh_token))])
    if not rows:
        raise OAuthError(400, "invalid_grant", "Invalid refresh token.")
    row = rows[0]
    if row.get("revoked_at") is not None:
        raise OAuthError(400, "invalid_grant",
                         "Refresh token already revoked (rotated or invalidated).")
    if row["client_id"] != client["id"]:
        raise OAuthError(401, "unauthorized_client",
                         "Refresh token was issued to a different client.")
    if _parse_ts(row.get("expires_at")) is None or _parse_ts(row["expires_at"]) < _now():
        raise OAuthError(400, "invalid_grant", "Refresh token expired.")
    resource = body.get("resource")
    if resource:
        _, requested_team = parse_resource(base, resource)
        if requested_team is not None and requested_team != row["team_id"]:
            raise OAuthError(400, "invalid_grant",
                             "Resource indicator does not match the token's team.")
    # D5: suspension → revoke the whole (user, team) family, then reject.
    try:
        _assert_team_usable(cp, row["team_id"])
    except OAuthError:
        _revoke_team_family(cp, row["user_id"], row["team_id"])
        raise
    # Lapsed membership → revoke this token (the grant dies with the seat).
    from tortoise.supabase_control import membership_for_user_team
    if membership_for_user_team(cp, row["user_id"], row["team_id"]) is None:
        cp.query("oauth_refresh_tokens", method="PATCH",
                 filters=[("id", "eq", row["id"])],
                 json_body={"revoked_at": _now_iso()})
        raise OAuthError(403, "invalid_grant",
                         "Membership in the team has ended — the grant was revoked.")
    prev_access = cp.query("oauth_access_tokens",
                           select=["id"],
                           filters=[("refresh_token_id", "eq", row["id"]),
                                    ("revoked_at", "is", None)])
    out = _issue_tokens(cp, client_id=row["client_id"], user_id=row["user_id"],
                        team_id=row["team_id"], scope=row.get("scope")
                        or " ".join(SCOPES_SUPPORTED), resource=resource,
                        prev_refresh=row,
                        prev_access_id=prev_access[0]["id"] if prev_access else None)
    return {k: v for k, v in out.items() if not k.startswith("_")}


def revoke_token(cp, body: dict) -> None:
    """RFC 7009 token revocation. Idempotent: always succeeds (200) even for
    unknown/already-revoked tokens (the client treats revocation as done)."""
    token = body.get("token", "")
    if not token:
        raise OAuthError(400, "invalid_request", "token is required.")
    hint = body.get("token_type_hint")
    hashed = _sha256(token)
    if hint in (None, "refresh_token"):
        cp.query("oauth_refresh_tokens", method="PATCH",
                 filters=[("token_hash", "eq", hashed), ("revoked_at", "is", None)],
                 json_body={"revoked_at": _now_iso()})
    if hint in (None, "access_token"):
        cp.query("oauth_access_tokens", method="PATCH",
                 filters=[("token_hash", "eq", hashed), ("revoked_at", "is", None)],
                 json_body={"revoked_at": _now_iso()})


# ── MCP-boundary introspection (D6) ─────────────────────────────────────────

def resolve_oauth_access_token(cp, token: str) -> dict | None:
    """Introspect an ``oat_`` access token → team dict (same shape as
    resolve_api_key) or None. This is the OAuth half of the MCP auth
    boundary — no tt_ key is minted (D6).

    Checks, in order: prefix, token row (revoked_at authoritative, expiry),
    team existence + durable suspension (the suspended_at check rides the
    returned dict so TeamResolutionMiddleware's existing #308 gate applies
    identically to OAuth and tt_ credentials).
    """
    if not isinstance(token, str) or not token.startswith(ACCESS_TOKEN_PREFIX):
        return None
    rows = cp.query("oauth_access_tokens", select=[
        "token_hash", "client_id", "user_id", "team_id", "scope",
        "expires_at", "revoked_at",
    ], filters=[("token_hash", "eq", _sha256(token))])
    if not rows:
        return None
    row = rows[0]
    if row.get("revoked_at") is not None:
        return None
    exp = _parse_ts(row.get("expires_at"))
    if exp is None or exp < _now():
        return None
    team = _team_row(cp, row["team_id"])
    if team is None:
        return None
    return _quota_fields(team)


# ── Metadata (P1 — RFC 9728 PRM + RFC 8414 AS metadata) ────────────────────

def protected_resource_metadata(base: str) -> dict:
    """RFC 9728 Protected Resource Metadata for the MCP endpoint."""
    return {
        "resource": mcp_resource_url(base),
        "authorization_servers": [base.rstrip("/")],
        "scopes_supported": SCOPES_SUPPORTED,
        "bearer_methods_supported": ["header"],
    }


def authorization_server_metadata(base: str) -> dict:
    """RFC 8414 Authorization Server Metadata (OAuth 2.1 profile)."""
    base = base.rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/register",
        "revocation_endpoint": base + "/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": SCOPES_SUPPORTED,
    }


# ── Branded consent page (D2 — one custom HTML page, signup/signin pattern) ─

_CONSENT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Authorize MCP client — Tortoise</title>
<meta name="theme-color" content="#060b14">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg: #060b14; --surface: #0d1a2d; --text: #cbd5e1; --text-dim: #94a3b8;
    --accent: #06b6d4; --accent-hover: #0891b2; --green: #4ade80;
    --red: #ef4444; --gold: #f59e0b; --border: #1e293b;
    --mono: 'SF Mono','Cascadia Code','Fira Code','JetBrains Mono',monospace;
    --serif: 'Georgia','Times New Roman',serif;
  }}
  body {{ background: var(--bg); color: var(--text); font-family: var(--mono);
         font-size: 14px; line-height: 1.6; -webkit-font-smoothing: antialiased;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; padding: 24px; }}
  .card {{ width: 100%; max-width: 460px; background: var(--surface);
          border: 1px solid var(--border); border-radius: 10px; padding: 2rem; }}
  .logo {{ font-family: var(--serif); font-size: 1.4rem; margin-bottom: 1.5rem; }}
  .logo span {{ color: var(--accent); }}
  h1 {{ font-family: var(--serif); font-size: 1.3rem; font-weight: 400;
       margin-bottom: .5rem; }}
  .muted {{ color: var(--text-dim); font-size: 13px; margin-bottom: 1.25rem; }}
  .row {{ display: flex; justify-content: space-between; padding: .5rem 0;
         border-bottom: 1px solid var(--border); font-size: 13px; }}
  .row .k {{ color: var(--text-dim); }}
  .row .v {{ color: var(--text); word-break: break-all; text-align: right;
            max-width: 60%; }}
  .actions {{ display: flex; gap: .75rem; margin-top: 1.5rem; }}
  button {{ font-family: var(--mono); font-size: 14px; font-weight: 600;
           padding: .6rem 1rem; border-radius: 6px; border: 1px solid var(--border);
           cursor: pointer; flex: 1; }}
  .btn-auth {{ background: var(--accent); color: #04121a; }}
  .btn-auth:hover {{ background: var(--accent-hover); }}
  .btn-deny {{ background: transparent; color: var(--text-dim); }}
  .error {{ display: none; color: var(--red); margin-top: 1rem; font-size: 13px; }}
  .error.visible {{ display: block; }}
  input {{ width: 100%; background: var(--bg); border: 1px solid var(--border);
          color: var(--text); border-radius: 6px; padding: .6rem .75rem;
          font-family: var(--mono); font-size: 14px; margin-bottom: .75rem; }}
  .providers {{ display: flex; gap: .75rem; margin-bottom: .75rem; }}
  .btn-provider {{ background: var(--bg); color: var(--text); }}
  .btn-provider:hover {{ border-color: var(--accent); }}
  .spinner {{ color: var(--text-dim); font-size: 13px; margin-top: 1rem; }}
</style>
</head>
<body>
<div class="card" id="card">
  <div class="logo">Tortoise<span>.</span></div>
  <div id="view-consent">
    <h1>Connect an MCP client</h1>
    <p class="muted" id="client-line"></p>
    <div class="row"><span class="k">Requested scopes</span><span class="v" id="scope-line"></span></div>
    <div class="row"><span class="k">Resource</span><span class="v" id="resource-line"></span></div>
    <div class="row"><span class="k">Team</span><span class="v" id="team-line">resolving…</span></div>
    <div class="actions">
      <button class="btn-deny" id="btn-deny">Deny</button>
      <button class="btn-auth" id="btn-auth">Authorize</button>
    </div>
  </div>
  <div id="view-signin" style="display:none">
    <h1>Sign in to Tortoise</h1>
    <p class="muted">Sign in to approve this connection.</p>
    <div class="providers">
      <button class="btn-provider" id="btn-github">GitHub</button>
      <button class="btn-provider" id="btn-google">Google</button>
    </div>
    <input type="email" id="email" placeholder="Email" autocomplete="email">
    <input type="password" id="password" placeholder="Password" autocomplete="current-password">
    <button class="btn-auth" id="btn-email">Sign in with email</button>
  </div>
  <div class="error" id="error"></div>
  <div class="spinner" id="spinner" style="display:none">Verifying session…</div>
</div>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.min.js"
        onerror="showError('Auth script blocked — please retry.')"></script>
<script>
  // ── authorize request params (JSON-embedded by the server; origin-safe) ──
  const PARAMS = __PARAMS__;
  const SUPABASE_URL = __SUPABASE_URL__;
  const SUPABASE_ANON_KEY = __SUPABASE_ANON_KEY__;
  const AUTHORIZE_PATH = "/oauth/authorize";
  let supabaseClient = null;
  try {{
    if (typeof window.supabase !== "undefined") {{
      supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
    }} else {{
      showError("Auth is temporarily unavailable (script blocked).");
    }}
  }} catch (e) {{ showError("Auth init failed: " + e.message); }}

  function showError(msg) {{
    const el = document.getElementById("error");
    el.textContent = msg; el.classList.add("visible");
  }}
  function hideError() {{ document.getElementById("error").classList.remove("visible"); }}
  function spinner(on) {{ document.getElementById("spinner").style.display = on ? "block" : "none"; }}
  function redirectBack(params) {{
    const sep = PARAMS.redirect_uri.includes("?") ? "&" : "?";
    window.location.href = PARAMS.redirect_uri + sep + new URLSearchParams(params).toString();
  }}

  async function fetchPreview(accessToken) {{
    const res = await fetch("/oauth/consent/preview?resource=" +
        encodeURIComponent(PARAMS.resource || ""), {{
      headers: {{ "Authorization": "Bearer " + accessToken }},
    }});
    if (res.status === 401) return null;
    if (!res.ok) throw new Error("Could not resolve team: " + res.status);
    return res.json();
  }}

  async function showConsent() {{
    const { data } = await supabaseClient.auth.getSession();
    if (!data.session) {{ showSignin(); return; }}
    document.getElementById("view-consent").style.display = "block";
    document.getElementById("view-signin").style.display = "none";
    document.getElementById("client-line").textContent =
        PARAMS.client_name + " wants to access your Tortoise MCP surface.";
    document.getElementById("scope-line").textContent = PARAMS.scope || "mcp";
    document.getElementById("resource-line").textContent =
        PARAMS.resource || "default (sole team)";
    try {{
      const preview = await fetchPreview(data.session.access_token);
      if (!preview) {{ showSignin(); return; }}
      document.getElementById("team-line").textContent =
          (preview.team_name || preview.team_id) + " (" + preview.team_id + ")";
    }} catch (e) {{ showError(e.message); }}
  }}

  function showSignin() {{
    document.getElementById("view-consent").style.display = "none";
    document.getElementById("view-signin").style.display = "block";
  }}

  async function signInWithProvider(provider) {{
    const { error } = await supabaseClient.auth.signInWithOAuth({{
      provider: provider,
      options: {{ redirectTo: window.location.origin + AUTHORIZE_PATH + window.location.search }},
    }});
    if (error) showError(error.message);
  }}

  document.getElementById("btn-github").onclick = () => signInWithProvider("github");
  document.getElementById("btn-google").onclick = () => signInWithProvider("google");
  document.getElementById("btn-email").onclick = async () => {{
    hideError();
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;
    if (!email || !password) {{ showError("Enter email and password."); return; }}
    const { error } = await supabaseClient.auth.signInWithPassword({{ email, password }});
    if (error) {{ showError(error.message); return; }}
    showConsent();
  }};

  document.getElementById("btn-auth").onclick = async () => {{
    hideError(); spinner(true);
    const { data } = await supabaseClient.auth.getSession();
    if (!data.session) {{ spinner(false); showSignin(); return; }}
    try {{
      const res = await fetch("/oauth/consent", {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          "Authorization": "Bearer " + data.session.access_token,
        }},
        body: JSON.stringify({{
          client_id: PARAMS.client_id,
          redirect_uri: PARAMS.redirect_uri,
          response_type: PARAMS.response_type,
          code_challenge: PARAMS.code_challenge,
          code_challenge_method: PARAMS.code_challenge_method,
          state: PARAMS.state,
          scope: PARAMS.scope,
          resource: PARAMS.resource || null,
        }}),
      }});
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.error_description || payload.error || "Consent failed");
      const q = {{ code: payload.code }};
      if (payload.state) q.state = payload.state;
      redirectBack(q);
    }} catch (e) {{ spinner(false); showError(e.message); }}
  }};

  document.getElementById("btn-deny").onclick = () =>
      redirectBack({{ error: "access_denied", state: PARAMS.state }});

  if (supabaseClient) showConsent(); else spinner(false);
</script>
</body>
</html>
"""


def consent_page_html(*, client_name: str, scope: str | None,
                      params: dict, supabase_url: str,
                      supabase_anon_key: str) -> str:
    """Render the branded consent page (D2). ``params`` are the raw authorize
    query params — JSON-encoded into the page so the JS echoes them back."""
    safe_params = {
        k: (v if isinstance(v, str) else "")
        for k, v in params.items()
    }
    safe_params["client_name"] = client_name
    return _CONSENT_HTML.replace("__PARAMS__", json.dumps(safe_params)) \
        .replace("__SUPABASE_URL__", json.dumps(supabase_url.rstrip("/"))) \
        .replace("__SUPABASE_ANON_KEY__", json.dumps(supabase_anon_key)) \
        .replace("__CLIENT_NAME__", client_name)
