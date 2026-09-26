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
    existing JWKS path (session_auth.verify_session_jwt — D2: "reuse
    JWKS verify"). Branded consent = ONE custom HTML page (D2).
  * P3 — Dynamic Client Registration (RFC 7591) at ``POST /register`` (D1).
  * P4 — token→org mapping via RFC 8707 resource indicator, client-declared
    (D4): the resource is ``{origin}/mcp`` (single-membership users resolve
    to their sole active org) or ``{origin}/mcp/organizations/{org_id}`` (explicit
    org). Rotating refresh tokens per (user, org), revoked on org
    suspension (D5). #1701 R1 — resource-less OAuth clients (ChatGPT cannot
    declare RFC 8707 resources): ``consent_preview`` returns the account's
    selectable (non-suspended) orgs and the consent page offers an org
    chooser; suspended orgs never bind (preview, mint AND exchange); the AS
    origin root is accepted as the bare MCP resource echo.
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
import contextlib
import hashlib
import ipaddress
import json
import logging
import os
import re  # noqa: F401
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse  # noqa: F401

logger = logging.getLogger("tortoise.oauth")

# ── Protocol constants ──────────────────────────────────────────────────────

# SCOPES_SUPPORTED is the *client-facing default* / PRM document scope set;
# SCOPES_ACCEPTED is the superset the DCR gate accepts and the AS metadata
# advertises (#2866). `offline_access` is accepted (Claude's connector
# requests it, and the AS does mint refresh tokens unconditionally) without
# becoming a default fallback or a PRM-advertised scope. The superset
# relation is structural. NOTE: scope enforcement is DCR-only today —
# `validate_authorize_params` takes no `scope` parameter, so the
# authorize/consent path forwards an unvalidated scope into the minted token
# (pre-existing, filed as #3128). Do not read this constant as an authorize
# gate.
SCOPES_SUPPORTED = ["mcp"]
SCOPES_ACCEPTED = [*SCOPES_SUPPORTED, "offline_access"]
ACCESS_TOKEN_TTL_S = int(os.environ.get("TORTOISE_OAUTH_ACCESS_TTL", "3600"))
REFRESH_TOKEN_TTL_S = int(os.environ.get("TORTOISE_OAUTH_REFRESH_TTL",
                                          str(30 * 24 * 3600)))
AUTH_CODE_TTL_S = int(os.environ.get("TORTOISE_OAUTH_CODE_TTL", "600"))
# ── Retention / GC windows (issue #3036) ────────────────────────────────────
# Credential hygiene, a DIFFERENT AXIS from the user-content deletion promise:
# these rows are service-role-only hashed secrets (0016 RLS), never user
# content. Canonical promise doc: docs/retention-and-deletion.md (which records
# the same carve-out for the operational event store).
#
# Each value is the DEFAULT grace kept AFTER the row's own expires_at, so a row
# that is revoked but not yet expired lives out its natural TTL first. That is
# what makes a #2863 soft-revoke an accounting residue rather than a leak. The
# env override is read and VALIDATED per sweep (see _retention_seconds), never
# parsed blindly: a negative override would move the cutoff into the future and
# delete LIVE credentials.
OAUTH_CODE_RETENTION_S = 86400
OAUTH_ACCESS_RETENTION_S = 86400
OAUTH_REFRESH_RETENTION_S = 86400

# Upper bound on any retention window (10 years). A larger override is
# indistinguishable from "retention off" AND overflows the cutoff arithmetic
# (``timedelta`` raises OverflowError), which would skip that table forever.
_MAX_RETENTION_S = 10 * 365 * 86400
_MAX_RETENTION_STR = str(_MAX_RETENTION_S)


def _retention_seconds(env_name: str, default: int) -> int:
    """Resolve a retention window from the environment, fail-safe (#3036).

    Mirrors ``monitoring.event_retention_interval``: a value that is not a
    positive whole number of seconds falls back to ``default`` with a warning.
    This matters because the window is SUBTRACTED from ``now`` to form a
    DELETE cutoff — a negative or malformed value would otherwise delete live
    rows (or raise at import, silently disabling retention).

    The parse is deliberately STRICT — ASCII ``str.isdigit`` — because bare
    ``int()`` also accepts a sign (``+5``), underscore separators (``1_0``)
    and non-ASCII digit forms (``٣`` = 3). None of those is a window a human
    meant, and the last two resolve to a far shorter window than intended.

    Out-of-range handling is DIRECTIONAL on purpose: a non-positive or
    malformed value falls back to ``default``, but a value ABOVE the ceiling
    is CLAMPED to it. Falling back to the 1-day default for an operator who
    asked for a longer window would delete EARLIER than requested — the wrong
    direction for a retention knob.
    """
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    if not (raw.isascii() and raw.isdigit()):
        logger.warning("oauth: %s=%r is not a positive integer — using %ss",
                       env_name, raw, default)
        return default
    # Width check BEFORE int(): CPython refuses a string longer than
    # ``sys.get_int_max_str_digits()`` (4300) with an uncaught ValueError, and
    # no digits-only value this wide can be below the ceiling. Compare
    # SIGNIFICANT digits, not ``len(raw)``: leading zeros inflate the string
    # without inflating the value, so ``00000086400`` must resolve to 86400
    # (not clamp) and ``0000000000`` must hit the non-positive branch.
    significant = raw.lstrip("0") or "0"
    if len(significant) > len(_MAX_RETENTION_STR):
        logger.warning("oauth: %s is wider than %d digits — clamping to %ds",
                       env_name, len(_MAX_RETENTION_STR), _MAX_RETENTION_S)
        return _MAX_RETENTION_S
    value = int(significant)
    if value <= 0:
        logger.warning("oauth: %s=%r must be positive — using %ss",
                       env_name, raw, default)
        return default
    if value > _MAX_RETENTION_S:
        logger.warning("oauth: %s=%r exceeds %ds — clamping",
                       env_name, raw, _MAX_RETENTION_S)
        return _MAX_RETENTION_S
    return value


# ── Redemption state (issue #3027) ───────────────────────────────────────────
# `used_at` records that a request CLAIMED a code; it says nothing about what
# the claim did. These states make the OUTCOME durable, so a failed or lost
# redemption is distinguishable from a replay, and "did this code already mint
# a pair?" is answerable from the grants themselves (the `code_id` link).
# Design: docs/scoping/2026-09-25-3027-oauth-redemption-state.md.
#
#   unclaimed → claimed → minted    the pair was handed to the response
#                       → burned    terminal; the code never mints again
#   claimed   → unclaimed           a VERIFIED-CLEAN failure re-armed it
#                                   (#2863's re-arm, now durably recorded)
#
# `burned` is written where the claim's residue is terminal: a pre-mint signal, or
# a reconcile past the grace that ATTEMPTED to revoke a live orphan family
# (`_rollback_minted` is best-effort — a failed revoke is captured, and the row
# survives inert under a now-`burned` code until the retention sweep reaches its
# TTL) or found none AT PROBE TIME (the probe and the settle are not one
# transaction, so a family minted between them escapes). An outcome the process
# could not settle stays `claimed` so the reconciler can resolve it — see
# `_reconcile_claimed_redemption`.
#
# Schema invariant (migration 20260925000002) — DIRECTIONAL, deliberately not the
# biconditional (a biconditional rejects the pre-#3027 writer, which sets
# `used_at` alone, during the rolling deploy):
#   used_at IS NULL  ⇒  redemption_state = 'unclaimed'
# The CLAIM and the RE-ARM set `used_at` and `redemption_state` in ONE statement;
# a settle only transitions `redemption_state` on a row that is already `claimed`.
# So the biconditional holds for everything we write, and the DB enforces the
# direction that matters (no settled row without a claim timestamp).
REDEMPTION_UNCLAIMED = "unclaimed"
REDEMPTION_CLAIMED = "claimed"
REDEMPTION_MINTED = "minted"
REDEMPTION_BURNED = "burned"

# An outcome-unknown claim is reconciled only once it is older than this. The
# window does NOT prove the owner is dead — the mutating grant is awaited with no
# wall-clock bound above it, so a live sibling can outlive any window
# (`_reconcile_claimed_redemption` spells this out). What the window bounds is
# WHEN a later request starts taking the claim over; a live sibling that outlives
# it simply loses the settle CAS and compensates its pair. Set generously so an
# ordinary request is not aborted for nothing.
REDEMPTION_CLAIM_GRACE_S = 60


def _redemption_grace_s() -> int:
    """Resolve the reconcile grace window, fail-safe (#3027).

    Same strict parse and DIRECTIONAL fallback as `_retention_seconds`: a
    malformed or non-positive override falls back to the default (never to a zero
    window — that would take a claim over the instant it is written, aborting
    every concurrent sibling for no gain), and an over-long one is clamped. Read
    per use so the env stays a reversible lever.
    """
    return _retention_seconds("TORTOISE_OAUTH_REDEMPTION_GRACE_S",
                              REDEMPTION_CLAIM_GRACE_S)


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

    #2863: ``status`` may also be 500 (the /oauth/token last-resort boundary)
    or 503 (``OAuthTemporarilyUnavailable``) — both still render through the
    same RFC 6749 §5.2 body producer, ``_oauth_error_response``.
    """

    def __init__(self, status: int, error: str, error_description: str):
        super().__init__(error_description)
        self.status = status
        self.error = error
        self.error_description = error_description

    def body(self) -> dict:
        return {"error": self.error, "error_description": self.error_description}


# Transient-failure contract (#2863). /oauth/token's consumer (mcp 1.29.0) parses the
# RFC 6749 §5.2 body, so it must NOT reuse `hosted_api._control_plane_unavailable()`'s
# FastAPI {"detail": {"error_code": ...}} shape. Same STATUS (503), different driver:
# that is deliberate, and the endpoint test pins the status parity. §8.5 permits the
# extra error code; §5.2's charset admits "_".


class OAuthMintAborted(Exception):
    """Internal (#2863): `_issue_tokens` failed after possibly writing.
    `recovered` is True iff an observation confirmed the mint left no live minted row
    (and, on the rotation path, left the previous refresh token unclaimed). Never
    escapes `oauth.py` — callers map it to a typed `OAuthError`."""

    def __init__(self, recovered: bool, cause: str = ""):
        super().__init__(cause or "token mint aborted")
        self.recovered = recovered


class OAuthTemporarilyUnavailable(OAuthError):
    """503 `temporarily_unavailable` — raised ONLY when retrying cannot double-issue:

      * constructively, where no write was attempted at all (a pre-claim read
        failed, or the refresh path failed before minting);
      * by observation, where a write may have landed — the grant is confirmed
        still-usable (see `_mint_observably_clean` / `_prev_refresh_unclaimed`);
      * where a conditional claim was observed to match ZERO rows and the
        failure is a later classification READ (#3027 `_observe_code` returning
        'unobservable') — nothing was written by this request, and the retry
        re-runs the same claim CAS, which is what decides.

    Never on an unobserved WRITE state: a claim PATCH that RAISED may have
    committed (`_consume_state` → 'unknown'), and that stays terminal
    `invalid_grant`. This distinction is #2863's, not a new one.
    """

    def __init__(self, error_description: str = "Temporary control-plane failure — retry."):
        super().__init__(503, "temporarily_unavailable", error_description)


def _log_and_capture(exc: BaseException, *, where: str) -> None:
    """One WARNING + at most one Sentry capture for a conversion path. Must never raise.

    OWNER TABLE (I4 — never capture twice for one request):
      lane 2 of `_issue_tokens`        → the single capture of the TRIGGERING exception
      lane 1 loser rollback (capture=True)  → the single capture for the loser path
                                               (nothing else captures there)
      #3027 delivery-gate loss (capture=True) → the single capture for that path
                                               (the mint succeeded, so nothing has
                                               captured; the handler only logs)
      lane 2 rollback/observation (capture=False) → log only (lane 2 captured the trigger)
      lane 3 prev-access revoke        → log only (non-decision-bearing hygiene)
      the two correction-#8 revokes    → each the single capture for its terminal path
      `exchange_auth_code` / `refresh_grant` pre-consume/pre-mint `except Exception`
                                       → this call IS the single capture for that path
      `oauth_token` boundary           → this call IS the single capture for that path
    """
    with contextlib.suppress(Exception):  # logging never breaks the response
        logger.warning("oauth: %s failed: %s", where, exc, exc_info=True)
    try:
        from tortoise.sentry import capture_exception as _capture
        _capture(exc, tags={"component": "oauth", "where": where})
    except Exception:
        pass


# ── Small helpers ───────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)  # noqa: UP017


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
        parsed = parsed.replace(tzinfo=timezone.utc)  # noqa: UP017
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


# Bytes we refuse to reason about in a redirect URI, checked BEFORE any
# comparison or echo. The authorization code is delivered by NAVIGATING THE
# BROWSER to the raw `redirect_uri` (see `redirectBack` in the consent page), so
# the browser's parse — never ours — is what decides where the code actually goes.
#
#   * `\` is a GENUINE parser differential and the reason this gate exists.
#     WHATWG ends the authority at a backslash for special schemes (http/https);
#     `urlsplit` does not. So `http://evil.example\@localhost/cb` has host
#     `localhost` to us and `evil.example` to the browser: validated as loopback,
#     then navigated off-device carrying the code. Found in review; reproduced in
#     Chromium with the attacker's listener receiving `?code=...`. PKCE does not
#     help — the attacker authors the authorize request and holds the verifier.
#
#   * C0 controls (0x00-0x1F) and DEL are NOT a differential, and this comment
#     claimed they were until review falsified it. `urlsplit` strips \t \r \n
#     (`urllib.parse._UNSAFE_URL_BYTES_TO_REMOVE`) exactly as a browser does, and
#     for the remaining bytes no browser moves the authority boundary either.
#     The precise treatment is position-dependent (stripped / refused /
#     percent-encoded — `docs/oauth-mcp.md` carries the per-position detail),
#     which is why this comment deliberately does NOT try to characterise it:
#     three review rounds each caught an over-specific claim here. The only
#     load-bearing point is that the boundary does not move. They are refused
#     anyway, as defence in depth: no legitimate redirect URI contains a control
#     character, so the conservative direction costs nothing real. It does mean a
#     URI registered before this gate existed stops matching — deliberate, and
#     pinned by `test_differential_uris_are_refused_even_on_exact_match`.
#
# Refusing the bytes outright is preferred to modelling WHATWG: a whitelist of
# "URIs both parsers agree on" cannot be kept correct, and fail-closed is the
# only safe direction on the input that decides where a credential is sent.
def _unsafe_redirect_uri_bytes(uri: str) -> bool:
    """True when a redirect URI holds bytes we refuse to reason about.

    Conservative by design: a raw backslash is a real parser differential
    between ``urlsplit`` and the browser, the control characters are
    belt-and-suspenders. See the comment above — this is a fail-closed byte
    filter, NOT a precise differential detector, which is what earlier wording
    in this PR wrongly claimed.
    """
    return any(ch == "\\" or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in uri)


# Private-use URI schemes (RFC 8252 §7.1) that a harness may register as a
# redirect target. Cursor IDE's MCP OAuth DCR still sends its custom-scheme
# callback (cursor://anysphere.cursor-mcp/oauth/callback) on the exthost path —
# the Cursor 3.13.25 report on their forum, and their own staff answer, confirm
# it persists alongside the documented loopback/https pair. Registration is
# ALL-OR-NOTHING (`register_client` rejects the whole request if ANY entry is
# invalid), so a single custom-scheme entry costs the client its client_id
# entirely: Cursor never reaches /oauth/authorize and the tester cannot sign in.
#
# Deliberately an ALLOWLIST, not "any private-use scheme". The consent page
# delivers the code by navigating to the raw redirect_uri
# (`window.location.href = redirect_uri + "?code=…"`), so a scheme the browser
# executes rather than navigates — javascript:, data:, vbscript: — would run in
# the consent page's own origin, and a scheme with an app handler the user has
# is a code-delivery target we have not reasoned about. Failing closed on every
# scheme that is not listed costs nothing today: Cursor is the only harness in
# the beta using one. Extending it is a reviewed one-line change with a test.
_NATIVE_REDIRECT_SCHEMES = frozenset({"cursor"})


def _valid_redirect_uri(uri: str) -> bool:
    """A registration-acceptable redirect URI: https; http only when the host
    is loopback (RFC 8252 §7.3 native-app pattern used by MCP clients); or a
    private-use scheme listed in `_NATIVE_REDIRECT_SCHEMES` (RFC 8252 §7.1) —
    see that constant for why it is an allowlist and not "any scheme".

    URIs holding bytes we refuse to reason about are rejected here too, so a
    string the browser might read differently can never enter a client row.
    """
    if not isinstance(uri, str) or _unsafe_redirect_uri_bytes(uri):
        return False
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if "#" in uri:
        # RFC 6749 §3.1.2 — a fragment is never a valid redirect component, and
        # this function's error message already promises rejection. Tested as
        # the literal delimiter, NOT `parsed.fragment`: a bare trailing `#`
        # parses to an EMPTY fragment, so `if parsed.fragment:` let it through
        # while it still made the consent page's `redirect_uri + "?code=…"`
        # navigation land the code inside the fragment (caught in review — both
        # reviewers, independently). `#` can only ever begin the fragment
        # (RFC 3986 pchar excludes it), so presence is the correct predicate.
        return False
    if parsed.scheme == "https" and parsed.hostname:
        return True
    if parsed.scheme == "http" and parsed.hostname and _is_loopback(parsed.hostname):
        return True
    if parsed.scheme in _NATIVE_REDIRECT_SCHEMES:
        # Not a network location, so "https or loopback" does not describe it.
        # The invariant that matters is that it names something to hand the code
        # to — an authority (cursor://anysphere.cursor-mcp/oauth/callback) or at
        # least a path. Exact-match at authorize time is unchanged
        # (`_redirect_uri_matches` relaxes only for two LOOPBACK hosts).
        return bool(parsed.netloc or parsed.path)
    return False


def _redirect_uri_matches(registered: str | None,
                          presented: str | None) -> bool:
    """#2846 — does ``presented`` match a registered ``redirect_uri``?

    RFC 8252 §7.3: for loopback redirect URIs the authorization server MUST
    ignore the port, because a native app binds an ephemeral port at request
    time and cannot know it at registration. Anthropic's connector docs state
    the same requirement, and the Claude Code CLI depends on it.

    The relaxation is deliberately narrow:

    * BOTH values must be loopback hosts (``_is_loopback`` — the same predicate
      ``_valid_redirect_uri`` uses) carrying the SAME scheme. Nothing requires
      ``http``: the relaxation keys on loopback, so an ``https``-on-loopback
      pair relaxes as well.
    * scheme, host, path, params, query and fragment must still match exactly
      (host per RFC 3986 §3.2.2 and scheme per §3.1, case-insensitively).
    * the userinfo component must match exactly, so ``http://evil@localhost/cb``
      never satisfies a registration for ``http://localhost/cb``.
    * anything else — including every non-loopback URI — keeps the original
      exact-string rule, so the hosted security posture is unchanged.

    Host is NOT relaxed: ``localhost`` and ``127.0.0.1`` are distinct hosts,
    even though both are loopback. Only the port varies.

    Inputs holding bytes we refuse to reason about are refused outright (see
    ``_unsafe_redirect_uri_bytes``) — this function's own parse is never the one
    that decides where the code actually goes.
    """
    if not isinstance(registered, str) or not isinstance(presented, str):
        return False
    if _unsafe_redirect_uri_bytes(registered) or _unsafe_redirect_uri_bytes(presented):
        return False
    if registered == presented:
        return True
    try:
        reg = urlparse(registered)
        pre = urlparse(presented)
    except ValueError:
        return False
    if not reg.hostname or not pre.hostname:
        return False
    if not (_is_loopback(reg.hostname) and _is_loopback(pre.hostname)):
        return False
    return (
        reg.scheme.lower() == pre.scheme.lower()
        and reg.hostname.lower() == pre.hostname.lower()
        and reg.username == pre.username
        and reg.password == pre.password
        and reg.path == pre.path
        and reg.params == pre.params
        and reg.query == pre.query
        # A `#` can no longer be REGISTERED (see `_valid_redirect_uri`), so a
        # value carrying one here is a legacy row or an attack: the port
        # relaxation is refused for it outright rather than comparing the
        # (possibly empty) fragments — a bare trailing `#` parses to an EMPTY
        # fragment, compared equal to "no fragment", and matched a registration
        # without one (review round 1). Identical strings still match via the
        # exact-equality shortcut above, preserving legacy rows.
        and "#" not in registered and "#" not in presented
    )


def mcp_resource_url(base: str) -> str:
    """Canonical RFC 8707 resource URI for the MCP surface at ``base``."""
    return base.rstrip("/") + "/mcp"


def org_resource_url(base: str, org_id: str) -> str:
    """Org-scoped resource indicator (D4 — the client-declared org selector)."""
    return mcp_resource_url(base) + "/organizations/" + org_id


# ── RFC 8707 resource → org mapping (P4, D4) ──────────────────────────────

def parse_resource(base: str, resource: str | None) -> tuple[str | None, str | None]:
    """Split a client-declared resource indicator into (canonical, org_id).

    Returns (mcp_resource, None) for the bare MCP resource (org resolved
    from the user's memberships), (mcp_resource, org_id) for an org-scoped
    resource, or raises OAuthError for anything outside the MCP resource
    tree (RFC 8707 §2 — the AS must reject unknown resource values so a
    token can never be minted for a resource the client does not declare).

    #1701 R1: the AS's own origin root (``{base}`` / ``{base}/``) is accepted
    as the bare MCP resource — some OAuth clients (OpenAI/ChatGPT's runtime)
    echo ``resource={origin}`` instead of the PRM value. Exact equality only
    (never a prefix rule); tokens stay (user, org)-bound and MCP-only.
    """
    base_mcp = mcp_resource_url(base)
    if not resource:
        return base_mcp, None
    resource = resource.strip().rstrip("/")
    if resource == base_mcp:
        return base_mcp, None
    base_root = base.rstrip("/")
    if resource == base_root:
        return base_mcp, None
    org_prefix = base_mcp + "/organizations/"
    if resource.startswith(org_prefix) and "/" not in resource[len(org_prefix):]:
        org_id = resource[len(org_prefix):]
        if org_id:
            return resource, org_id
    raise OAuthError(400, "invalid_resource",
                     "Unknown resource indicator. Expected the MCP endpoint "
                     f"({base_mcp}) or a team-scoped resource under it.")


def _selectable_orgs(cp, user_id: str) -> list[dict]:
    """The user's ACTIVE memberships whose orgs are not durably suspended —
    the single source for default-org resolution AND the consent chooser
    (#1701 R1). Sorted deterministically by org_id (user_memberships has no
    ORDER BY; the chooser needs a stable order)."""
    from tortoise.supabase_control import user_memberships
    out = []
    for m in user_memberships(cp, user_id):
        rows = cp.query("organizations", select=["name", "suspended_at"],
                        filters=[("id", "eq", m["org_id"])])
        if not rows or rows[0].get("suspended_at") is not None:
            continue
        out.append({"org_id": m["org_id"],
                    "org_name": rows[0].get("name") or m["org_id"]})
    return sorted(out, key=lambda t: t["org_id"])


def _default_org(cp, user_id: str) -> str:
    """The user's sole ACTIVE (non-suspended) org (D4 + #1701 R1).

    0 usable orgs → error; >1 usable orgs → error telling the client to
    declare an org-scoped resource. Suspended memberships never count toward
    the default, so a 1-active + 1-suspended account binds the active org
    and never dead-ends on the multi-org 400."""
    active = _selectable_orgs(cp, user_id)
    if len(active) == 1:
        return active[0]["org_id"]
    if not active:
        raise OAuthError(403, "invalid_grant",
                         "This account has no active team. Create a team "
                         "before connecting an MCP client.")
    raise OAuthError(400, "invalid_resource",
                     "This account belongs to multiple teams — the MCP client "
                     "must declare a team-scoped resource indicator "
                     f"({org_resource_url('<base>', '<org_id>')} form).")


def _resolve_org(cp, user_id: str, base: str, resource: str | None) -> str:
    """RFC 8707 mapping (D4): client-declared resource → org_id, verified
    against the user's active memberships."""
    _, org_id = parse_resource(base, resource)
    if org_id is not None:
        from tortoise.supabase_control import membership_for_user_org
        if membership_for_user_org(cp, user_id, org_id) is None:
            raise OAuthError(403, "invalid_resource",
                             "Not a member of the requested team.")
        return org_id
    return _default_org(cp, user_id)


def _org_name(cp, org_id: str) -> str | None:
    rows = cp.query("organizations", select=["name"], filters=[("id", "eq", org_id)])
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


def resolve_client(cp, client_id: str) -> dict | None:
    """Client lookup for the authorize/token paths: registry first (DCR or
    operator-issued), then **CIMD** (#2847).

    CIMD is what lets a Claude connector obtain a client identity WITHOUT the
    ``POST /register`` round-trip, so the DCR limiter stops being load-bearing
    at directory scale. The metadata document is fetched under the full SSRF
    control set in ``tortoise.cimd``; this function owns only the control-plane
    side — persisting ONE ``oauth_clients`` row per distinct client_id URL,
    which `oauth_codes`/`oauth_access_tokens`/`oauth_refresh_tokens` require by
    foreign key.

    Failure policy: a CIMD problem returns ``None`` (→ the caller's existing
    "Unknown or revoked client_id"), never a 5xx — the fetch is attacker-
    reachable, so its failures must not become an availability signal. That
    includes a control-plane write failure on the provisioning insert, which is
    why the persist sits inside the guard below.
    """
    row = get_client(cp, client_id)
    if row is not None:
        return row
    from tortoise import cimd
    if not cimd.is_cimd_client_id(client_id) or not cimd.cimd_enabled():
        return None
    try:
        record = cimd.resolve_client_metadata(
            client_id,
            supported_scopes=set(SCOPES_ACCEPTED),
            supported_grants=set(SUPPORTED_GRANTS),
            default_scope=" ".join(SCOPES_SUPPORTED))
        _persist_cimd_client(cp, record)
    except Exception:
        # Deliberately broad: a refused/blocked fetch — or a control-plane
        # failure while provisioning — must land as an unknown client, not as
        # a distinguishable error (no SSRF oracle, no 5xx).
        return None
    # #2847 review P1 — revocation fail-open. `_persist_cimd_client`'s duplicate
    # re-read goes through the RAW `_client_row`, so a REVOKED CIMD client could
    # come back non-None here while the registry path returns None: the consent
    # page rendered and an authorization code was minted for a revoked client
    # (the token endpoint still rejected, so no token was issued). Re-reading
    # through the REVOKED-FILTERED accessor here — on the single resolver both
    # /oauth/authorize and /oauth/token share — closes it whatever the insert
    # did, and keeps this path's answer identical to the registry path's.
    # Fail-closed when the row is absent: the FK on oauth_codes requires it.
    return get_client(cp, client_id)


def _persist_cimd_client(cp, record: dict) -> None:
    """Insert the ``oauth_clients`` row a CIMD client needs for the FK, once.

    Growth bound: one row per distinct client_id URL — ``O(client
    implementations)`` (a handful), not ``O(connections)`` as DCR is. Claude's
    URL is stable, so this is exactly one row, ever.

    Concurrency: two simultaneous first-time authorizations of the same URL
    race on the primary key; the loser's insert raises while the row is
    present, which is not an error. A re-read that still finds nothing
    re-raises, so a genuine control-plane failure is never silently absorbed.
    The caller re-reads through the revoked-filtered accessor, so this function
    deliberately returns nothing — its return value is not a trusted view.
    """
    row = {
        "id": record["client_id"],
        "client_secret_hash": None,
        # The HOST, never the document's self-asserted client_name (Anthropic's
        # consent-screen rule — a client must not name itself on our page).
        "client_name": record["client_name"],
        "redirect_uris": record["redirect_uris"],
        "grant_types": record["grant_types"],
        "response_types": record["response_types"],
        "token_endpoint_auth_method": record["token_endpoint_auth_method"],
        "scope": record["scope"],
        "created_at": _now_iso(),
        "revoked_at": None,
    }
    try:
        cp.query("oauth_clients", method="POST", json_body=row)
    except Exception:
        if _client_row(cp, record["client_id"]) is None:
            raise


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
                         "Each redirect_uri must be https, http loopback, or a "
                         "supported native-app scheme, absolute, and may not "
                         "contain a fragment.")

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
    if any(s not in SCOPES_ACCEPTED for s in requested):
        raise OAuthError(400, "invalid_client_metadata",
                         f"Unsupported scope. Supported: {SCOPES_ACCEPTED}")

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
    row = resolve_client(cp, client_id)
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
    client = resolve_client(cp, client_id)
    try:
        if client is None:
            raise OAuthError(400, "invalid_request", "Unknown or revoked client_id.")
        if response_type != "code":
            raise OAuthError(400, "invalid_request",
                             "Only response_type=code is supported.")
        # #2846: loopback ports are ignored (RFC 8252 §7.3); every other redirect
        # keeps the exact-string rule. See `_redirect_uri_matches`.
        #
        # A non-list `redirect_uris` can only come from a legacy/hand-corrupted row
        # (`register_client` requires a list and the column is jsonb). Normalize it
        # to a single-element list so a bare string stays ONE uri: iterating the
        # string directly would compare character by character and silently stop
        # matching it at all.
        registered_uris = client.get("redirect_uris") or []
        if not isinstance(registered_uris, (list, tuple)):
            registered_uris = [registered_uris]
        if not any(_redirect_uri_matches(u, redirect_uri)
                   for u in registered_uris):
            raise OAuthError(400, "invalid_request",
                             "redirect_uri is not registered for this client.")
        if not code_challenge or not _valid_pkce(code_challenge):
            raise OAuthError(400, "invalid_request",
                             "code_challenge (PKCE, 43-128 chars) is required.")
        if code_challenge_method != "S256":
            raise OAuthError(400, "invalid_request",
                             "Only code_challenge_method=S256 is supported.")
        return client
    except OAuthError as exc:
        # #3669 finding 2: stamp the client resolved ABOVE onto the error
        # (None when the client itself was unresolvable) so the
        # /oauth/authorize handler can choose the redirect-vs-JSON shape
        # WITHOUT resolving a second time. On the FAILURE path that second
        # resolve cost a second CIMD fetch and a second rate-limit charge
        # (the success path paid nothing — the fetch cache absorbed it),
        # halving the effective failure budget.
        exc.client = client
        raise


def consent_preview(cp, user_id: str, base: str, resource: str | None) -> dict:
    """Consent-page org preview (D4 + #1701 R1 account-chooser).

    A client-declared org-scoped resource resolves to that org (membership
    AND suspension checked — a suspended org 403s here, never at exchange).
    A bare/omitted/origin-root-echoed resource resolves to the sole ACTIVE
    org or, for several, returns the selectable list for the page's chooser.
    Zero active orgs keeps the 403 so an account with no usable org cannot
    mint a code.
    """
    _, org_id = parse_resource(base, resource)
    if org_id is not None:
        from tortoise.supabase_control import membership_for_user_org
        if membership_for_user_org(cp, user_id, org_id) is None:
            raise OAuthError(403, "invalid_resource",
                             "Not a member of the requested team.")
        _assert_org_usable(cp, org_id)  # suspended → 403 invalid_grant
        return {
            "org_id": org_id,
            "org_name": _org_name(cp, org_id),
            "resource": (org_resource_url(base, org_id) if resource
                         else mcp_resource_url(base)),
        }
    orgs = _selectable_orgs(cp, user_id)
    if len(orgs) == 1:
        return {
            "org_id": orgs[0]["org_id"],
            "org_name": orgs[0]["org_name"],
            # byte-identical with today: a truthy declared resource (bare MCP
            # or origin echo) keeps the org-scoped resource field.
            "resource": (org_resource_url(base, orgs[0]["org_id"])
                         if resource else mcp_resource_url(base)),
        }
    if len(orgs) > 1:
        return {
            "org_id": None,
            "org_name": None,
            "resource": mcp_resource_url(base),
            "memberships": [
                {**t, "resource": org_resource_url(base, t["org_id"])}
                for t in orgs
            ],
        }
    raise OAuthError(403, "invalid_grant",
                     "This account has no active team. Create a team "
                     "before connecting an MCP client.")


def issue_auth_code(cp, *, client_id: str, user_id: str, base: str,
                    redirect_uri: str, code_challenge: str, state: str | None,
                    scope: str | None, resource: str | None) -> tuple[str, str]:
    """Bind a (user, org) grant to a single-use PKCE code (P2 + P4).

    Resolves the org from the client-declared resource indicator (RFC 8707)
    at consent time so the code carries the exact org the token will bind.
    #1701 R1: the org must be USABLE (not suspended) — a suspended org can
    never mint a code (suspension surfaces at consent, not at a later
    exchange). Returns (code, org_id).
    """
    org_id = _resolve_org(cp, user_id, base, resource)
    _assert_org_usable(cp, org_id)
    code = secrets.token_urlsafe(32)
    cp.query("oauth_codes", method="POST", json_body={
        "code_hash": _sha256(code),
        "client_id": client_id,
        "user_id": user_id,
        "org_id": org_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": scope or " ".join(SCOPES_SUPPORTED),
        "resource": resource,
        "expires_at": _expires_iso(AUTH_CODE_TTL_S),
        "used_at": None,
        # #3027: state every new code explicitly. The column has a DB default,
        # but sending it keeps the PostgREST seam and the in-memory fake in
        # lockstep (the fake has no column defaults).
        "redemption_state": REDEMPTION_UNCLAIMED,
        "redemption_id": None,
        "redemption_settled_at": None,
        "redemption_note": None,
        "created_at": _now_iso(),
    })
    return code, org_id


def _consume_state(cp, code: str) -> str:
    """READ-ONLY observation of a code's redemption state (#2863).

    "unconsumed" iff the row exists, is unclaimed and unexpired (a retry provably
    works); "consumed" for any other observed state (non-NULL `used_at`, no row,
    expired); "unknown" on any failure of the read OR its predicate.

    Performs NO write — this is what separates it from the withdrawn v4/v5 re-arm
    helpers, which cleared `used_at` and could clobber a concurrent claim.
    """
    try:
        rows = cp.query("oauth_codes", select=["used_at", "expires_at"],
                        filters=[("code_hash", "eq", _sha256(code))])
        if not rows or rows[0].get("used_at") is not None:
            return "consumed"
        expires = _parse_ts(rows[0].get("expires_at"))
        if expires is None or expires < _now():
            return "consumed"
        return "unconsumed"
    except Exception as exc:
        logger.warning("oauth: consume-state observation failed: %s", exc)
        return "unknown"


def _restore_code(cp, code: str, expected) -> bool:
    """CAS re-arm of the claim THIS request owns (#2863, #3027). Clears `used_at`
    ONLY if it still holds the value this request wrote AND the claim is still
    ours (`redemption_state='claimed'`), and only while the code is redeemable.

    True iff the re-arm is confirmed observable. Any raise / empty result / None
    expectation ⇒ False (terminal) — never a retryable signal on unobserved state.
    The expiry filter mirrors `_consume_state`: the failure path can spend ~20 s
    before the re-arm, so a near-TTL code must not be re-armed into a 503 whose retry
    then returns expired `invalid_grant`.

    The `redemption_state` condition is #3027's FENCE: a reconciler that took the
    claim over (and burned it) cannot be undone by this request's late re-arm.
    """
    if expected is None:
        return False
    try:
        rows = cp.query("oauth_codes", method="PATCH",
                        select=["used_at", "expires_at"],
                        filters=[("code_hash", "eq", _sha256(code)),
                                 ("used_at", "eq", expected),
                                 ("redemption_state", "eq", REDEMPTION_CLAIMED),
                                 ("expires_at", "gt", _now_iso())],
                        json_body={"used_at": None,
                                   # #3027: clear the redemption state in the SAME
                                   # CAS, so the durable state can never disagree
                                   # with `used_at` (the schema constrains them to
                                   # agree). A re-arm means "unclaimed" again.
                                   "redemption_state": REDEMPTION_UNCLAIMED,
                                   "redemption_id": None,
                                   "redemption_settled_at": None,
                                   "redemption_note": None})
        return bool(rows)
    except Exception as exc:
        logger.warning("oauth: code re-arm failed: %s", exc)
        return False


def _rollback_minted(cp, minted: list[tuple[str, str]], now: str, *, capture: bool) -> None:
    """Idempotent soft-revoke by id of every row this request may have written.

    A PATCH filtered by `id` is a VERIFIED no-op on a missing row in both seams
    (real: `Prefer: return=minimal` → `[]`; fake: `select is None` → `[]`), so zero
    affected rows is the EXPECTED SUCCESS for a write that never committed — this
    function must never raise on an empty result. Each row is attempted in its own
    try/except. `capture=True` is for lane 1 (nothing else captures on that path);
    lane 2 passes False because it already captured the trigger (I4). At most ONE
    capture is emitted per call: the loser path may fail on both rows (refresh +
    access), and I4 permits exactly one Sentry event for that request — the
    subsequent row failures are logged only.
    """
    captured = False
    for table, row_id in minted:
        try:
            cp.query(table, method="PATCH", filters=[("id", "eq", row_id)],
                     json_body={"revoked_at": now})
        except Exception as exc:
            if capture and not captured:
                captured = True
                _log_and_capture(exc, where=f"loser rollback {table}")
            else:
                logger.warning("oauth: mint rollback failed for %s/%s: %s", table, row_id, exc)


def _mint_observably_clean(cp, minted: list[tuple[str, str]]) -> bool:
    """True iff no minted row is live. Any raise → False (terminal, never fail-open)."""
    try:
        for table, row_id in minted:
            rows = cp.query(table, select=["id"],
                            filters=[("id", "eq", row_id), ("revoked_at", "is", None)])
            if rows:
                return False
        return True
    except Exception as exc:
        logger.warning("oauth: mint observation failed: %s", exc)
        return False


def _prev_refresh_unclaimed(cp, prev_refresh: dict) -> bool:
    """True iff the presented refresh token is still unrevoked. Any raise → False."""
    try:
        rows = cp.query("oauth_refresh_tokens", select=["revoked_at"],
                        filters=[("id", "eq", prev_refresh["id"])])
        return bool(rows) and rows[0].get("revoked_at") is None
    except Exception as exc:
        logger.warning("oauth: prev-refresh observation failed: %s", exc)
        return False


def _settle_redemption(cp, code_row: dict | None, state: str,
                       *, note: str | None = None) -> bool:
    """CAS-settle the claim this request OWNS (#3027). True iff the CAS WON.

    The write is fenced on the claim identity — `id` AND
    `redemption_state='claimed'` AND (when present) `redemption_id` — so exactly
    ONE of {the owning request, a reconciler that took the claim over} can record
    the outcome, and the loser is told so by the return value:

      * `exchange_auth_code` uses this as its DELIVERY GATE — a pair is only
        returned if the `minted` settle won; on a loss it compensates the pair it
        minted and reports the failure, so a reconciler can never revoke a family
        that is about to be delivered, and a late attempt can never resurrect a
        claim the reconciler already resolved.
      * `_reconcile_claimed_redemption` takes ownership with the same CAS BEFORE
        it revokes anything, so it can never revoke a family whose owner then
        delivers it.

    Best-effort by CONTRACT — never raises (a state-recording failure must not
    turn a coherent OAuth error into a 500), and the return value is the fence.
    `used_at` is left untouched: the claim wrote it, and `minted`/`burned` are
    terminal states that keep it.
    """
    if not code_row or code_row.get("id") is None:
        return False
    filters = [("id", "eq", code_row["id"]),
               ("redemption_state", "eq", REDEMPTION_CLAIMED)]
    # A legacy/pre-state row carries no redemption_id; the id+state CAS still
    # fences it (PostgREST `eq` does not match NULL, so an unconditional filter
    # would make such a row un-settleable in production while the fake matched it).
    # (`eq` with a NULL is NOT "match NULL": `_encode` renders it `eq.None`, i.e.
    # the LITERAL string — a 400 on a `bigint` column and a literal compare on a
    # `text` column. Either way it matches no real row, so the clause must be
    # omitted, not passed as NULL.)
    if code_row.get("redemption_id") is not None:
        filters.append(("redemption_id", "eq", code_row["redemption_id"]))
    try:
        rows = cp.query("oauth_codes", method="PATCH", select=["id"],
                        filters=filters,
                        json_body={"redemption_state": state,
                                   "redemption_settled_at": _now_iso(),
                                   "redemption_note": note})
        return bool(rows)
    except Exception as exc:
        logger.warning("oauth: redemption settle (%s) failed: %s", state, exc)
        return False


def _observe_code(cp, code: str) -> dict:
    """READ-ONLY classification of a code after a zero-row claim (#3027).

    Returns the row (so the caller can settle or reconcile it) with an added
    ``state``: one of 'unclaimed' | 'claimed' | 'minted' | 'burned' | 'expired'
    | 'missing' | 'unobservable'.

    Never writes, and never raises: the claim PATCH has already been OBSERVED as
    a zero-row result, so nothing was written on this path, and a failed read is
    'unobservable' (a retryable signal is then safe — unlike an unobserved
    WRITE, which #2863 keeps terminal).

    The zero-row observation is load-bearing for that safety, and it rests on the
    control-plane seam: a select-bearing PATCH is sent with
    `Prefer: return=representation`, whose genuine zero-match result is a
    content-bearing `[]`, and a transport failure RAISES rather than returning
    empty (`supabase_control.query`). So on the normal seam a COMMITTED claim does
    not arrive here as zero rows.

    Residual, stated rather than hidden: `query` ALSO reads a 2xx with an EMPTY
    body as `[]`, so an intermediary that stripped a committed PATCH's body would
    make this look like a zero-row claim. The consequence is bounded and is NOT a
    double-issue — the retry re-runs the same claim CAS, which is what actually
    decides — but the signal is then retryable for a code that is in fact
    consumed, i.e. #2863's "untruthful retry" would be reinstated by the seam.
    Pinned by `test_empty_body_patch_reads_as_zero_rows` in the fault suite; do
    not widen the 503 basis further without re-reading it.
    """
    try:
        rows = cp.query("oauth_codes", select=[
            "id", "used_at", "expires_at", "redemption_state",
            "redemption_id", "client_id", "org_id",
        ], filters=[("code_hash", "eq", _sha256(code))])
    except Exception as exc:
        logger.warning("oauth: code state observation failed: %s", exc)
        return {"state": "unobservable"}
    if not rows:
        return {"state": "missing"}
    row = dict(rows[0])
    if row.get("used_at") is not None:
        state = row.get("redemption_state")
        # The schema invariant makes a non-'unclaimed' state the only reachable
        # value when `used_at` is set. A row observed through a pre-migration
        # seam carries no state column at all; treat it as 'claimed' — the
        # reconcilable state — never as terminal.
        return {**row, "state": state if state in (
            REDEMPTION_CLAIMED, REDEMPTION_MINTED, REDEMPTION_BURNED)
            else REDEMPTION_CLAIMED}
    expires = _parse_ts(row.get("expires_at"))
    if expires is None or expires < _now():
        return {**row, "state": "expired"}
    return {**row, "state": REDEMPTION_UNCLAIMED}


def _live_family_for_code(cp, code_row: dict) -> list[tuple[str, str]]:
    """Every LIVE token row linked to this code (#3027). Raises on read failure.

    A code row with NO id is refused rather than probed: there is no `code_id`
    value to filter on, and passing NULL would render `code_id=eq.None` — a 400 on
    the `bigint` column, and on a `text` column a compare against the literal
    "None". Probing is therefore impossible, NOT "matches every unlinked row".
    """
    code_id = code_row.get("id")
    if code_id is None:
        raise ValueError("code row carries no id — cannot resolve its family")
    out: list[tuple[str, str]] = []
    for table in ("oauth_refresh_tokens", "oauth_access_tokens"):
        rows = cp.query(table, select=["id"],
                        filters=[("code_id", "eq", code_id),
                                 ("revoked_at", "is", None)])
        out.extend((table, r["id"]) for r in rows if r.get("id") is not None)
    return out


def _reconcile_claimed_redemption(cp, code_row: dict) -> str:
    """Settle an outcome-unknown claim (#3027). Returns 'burned-orphan' |
    'burned-clean' | 'inflight' | 'lost-race' | 'unobservable'.

    The durable `code_id` link is the evidence an in-process read cannot supply
    across requests: it asks the GRANTS whether this code minted, so a later
    request can resolve a claim whose compensation failed.

    ⛔ There is deliberately NO cross-request re-arm. "Older than the grace"
    does not prove the owner is dead — the mutating grant is awaited with no
    wall-clock bound above it, and a control-plane stall (or an operator lowering
    the grace) can hold an attempt between its claim and its settle for an
    arbitrarily long time. Re-arming on that guess is exactly how TWO live
    families get minted for one single-use code: the late owner wakes, mints, and
    records `minted` over the re-armed row. A residue with no family is therefore
    BURNED — fail safe; the client re-runs authorization. The common
    verified-clean failure still re-arms, IN PROCESS, via `_restore_code`.

    Ordering is load-bearing: ownership is taken with a CAS on the claim identity
    BEFORE anything is revoked. If the owner settles first, this CAS loses
    ('lost-race') and NOTHING is touched, so a family that is about to be
    delivered is never revoked. If this CAS wins, the owner's own settle loses and
    `exchange_auth_code` compensates its pair instead of delivering it.
    """
    claimed_at = _parse_ts(code_row.get("used_at"))
    if claimed_at is None or (_now() - claimed_at).total_seconds() < _redemption_grace_s():
        return "inflight"
    try:
        family = _live_family_for_code(cp, code_row)
    except Exception as exc:
        logger.warning("oauth: redemption reconcile read failed: %s", exc)
        return "unobservable"
    if not _settle_redemption(cp, code_row, REDEMPTION_BURNED,
                              note="orphan-revoked" if family else "unresolved"):
        return "lost-race"
    if family:
        # The claim is now ours to decide, so no delivery can follow: these rows
        # belong to a mint whose response was lost and whose compensation failed.
        _rollback_minted(cp, family, _now_iso(), capture=True)
        return "burned-orphan"
    return "burned-clean"


def _consume_code(cp, code: str) -> dict:
    """Single-use auth-code redemption (RFC 6749 §4.1.2) with the durable
    redemption state machine (#3027).

    Atomic claim: one conditional UPDATE (``WHERE used_at IS NULL AND
    redemption_state='unclaimed' AND expires_at > now()``) with
    return=representation — a concurrent worker reusing the same code sees zero
    affected rows and cannot double-issue (no SELECT-then-PATCH race, PR #1264
    review P2). The claim records the timestamp, the state AND a fresh
    `redemption_id` in the SAME statement, so the durable state can never be
    half-written relative to the CAS.

    On zero rows the row is re-read READ-ONLY to classify WHY. Every verdict
    EXCEPT `unobservable` is TERMINAL (`invalid_grant`): a minted code is a
    replay, and a CLAIMED code is consumed by an attempt that may still be
    running — a different request must never re-arm it (two live families) and
    must never report it retryable (the retry can terminate, which #2863 records
    as the untruthful signal it removed). A `claimed` observation also triggers
    the lazy reconcile, which settles the residue for good. `unobservable` — the
    classification READ failed — is a retryable 503 instead, because the claim
    PATCH was OBSERVED to match zero rows (so this request wrote nothing, and the
    retry re-runs that same CAS); see `_observe_code`.
    """
    for attempt in (1, 2):
        rows = cp.query("oauth_codes", select=[
            "id", "code_hash", "client_id", "user_id", "org_id", "redirect_uri",
            "code_challenge", "code_challenge_method", "scope", "resource",
            "expires_at", "used_at", "redemption_state", "redemption_id",
        ], method="PATCH",
            filters=[("code_hash", "eq", _sha256(code)),
                     ("used_at", "is", None),
                     ("redemption_state", "eq", REDEMPTION_UNCLAIMED),
                     ("expires_at", "gt", _now_iso())],
            json_body={"used_at": _now_iso(),
                       "redemption_state": REDEMPTION_CLAIMED,
                       "redemption_id": secrets.token_urlsafe(16),
                       "redemption_settled_at": None,
                       "redemption_note": None})
        if rows:
            row = rows[0]
            # Defence in depth: the claim filter already excludes an expired code.
            if _parse_ts(row.get("expires_at")) is None or _parse_ts(row["expires_at"]) < _now():
                _settle_redemption(cp, row, REDEMPTION_BURNED, note="expired")
                raise OAuthError(400, "invalid_grant", "Authorization code expired.")
            return row
        observed = _observe_code(cp, code)
        state = observed.get("state")
        if state == REDEMPTION_CLAIMED:
            verdict = _reconcile_claimed_redemption(cp, observed)
            logger.info("oauth: code already claimed (%s)", verdict)
            raise OAuthError(400, "invalid_grant", "Invalid authorization code.")
        if state == "unclaimed" and attempt == 1:
            continue                        # lost a claim race — retry exactly once
        if state == "unobservable":
            raise OAuthTemporarilyUnavailable(
                "Could not determine the authorization code's state — retry.")
        if state == "expired":
            raise OAuthError(400, "invalid_grant", "Authorization code expired.")
        raise OAuthError(400, "invalid_grant", "Invalid authorization code.")
    # Unreachable: the loop either returns a claimed row or raises.
    raise OAuthError(400, "invalid_grant", "Invalid authorization code.")


def _assert_org_usable(cp, org_id: str) -> None:
    """D5: a suspended org cannot mint/refresh tokens. The durable
    suspended_at check is the single rejection authority (mirrors the tt_
    path's #308 semantics)."""
    rows = cp.query("organizations", select=["suspended_at", "tier"],
                    filters=[("id", "eq", org_id)])
    if not rows:
        raise OAuthError(403, "invalid_grant", "Team not found.")
    if rows[0].get("suspended_at") is not None:
        raise OAuthError(403, "invalid_grant",
                         "Team is suspended — OAuth tokens revoked. "
                         "Contact support to appeal.")


# ── Token issuance / exchange (P2 + P4 + D5) ────────────────────────────────

def _quota_fields(cp, org_row: dict) -> dict:
    """Quota shape shared with resolve_api_key so REST/MCP limits match
    (#329): preserve None (unlimited, Team tier), fall back to pricing.
    #1859 P3-2: max_points column (points-cap override) takes precedence
    over graph_size_cap, then pricing — mirrors resolve_api_key."""
    from tortoise.pricing import tier_limits
    from tortoise.quota import derived_tier
    tier = derived_tier({**org_row, "id": org_row.get("id")})
    lim = tier_limits(tier)
    mp = org_row.get("max_points")
    if mp is None:
        mp = org_row.get("graph_size_cap")
    return {
        "org_id": org_row.get("id"),
        "tier": tier,
        "max_users": (org_row.get("max_users")
                      if org_row.get("max_users") is not None
                      else lim["max_users_per_team"]),
        "max_graphs": (org_row.get("max_graphs")
                       if org_row.get("max_graphs") is not None
                       else lim["max_graphs_per_team"]),
        "max_points": (int(mp)
                       if mp is not None
                       else int(lim["max_graph_nodes"])),
        "max_api_keys": lim["max_api_keys"],
        # #4010: sessions are unlimited for every tier — no cap of any kind
        # (the pre-#4010 DEFAULT_MAX_SESSIONS fallback is deleted).
        "max_sessions": None,
        "suspended_at": org_row.get("suspended_at"),
        "flagged_at": org_row.get("flagged_at"),
        # #1765: prefer the owner's USER email (demotion — teams.email is a
        # stale-prone contact field), fall back to the contact value.
        "email": _owner_email_or(cp, org_row.get("id"), org_row.get("email")),
    }


def _owner_email_or(cp, org_id: str, fallback) -> str | None:
    from tortoise.supabase_control import owner_email
    try:
        return owner_email(cp, org_id) or fallback
    except Exception:
        return fallback


def _org_row(cp, org_id: str) -> dict | None:
    rows = cp.query("organizations", select=[
        "id", "tier", "max_users", "max_graphs", "graph_size_cap",
        "max_points", "suspended_at", "flagged_at", "email",
    ], filters=[("id", "eq", org_id)])
    return rows[0] if rows else None


def _issue_tokens(cp, *, client_id: str, user_id: str, org_id: str,
                  scope: str, resource: str | None,
                  prev_refresh: dict | None = None,
                  prev_access_id: str | None = None,
                  code_id: int | None = None) -> dict:
    """Mint an access+refresh pair; rotate (revoke) the previous pair when
    called from the refresh path (D5 rotation).

    Mint FIRST, revoke after (PR #1264 review P3): a DB failure mid-rotation
    leaves the new pair live instead of a dead token. The old refresh token
    is revoked with an atomic conditional UPDATE (``WHERE revoked_at IS
    NULL``); if a concurrent worker already claimed it, the freshly minted
    orphan pair is rolled back so exactly one rotation wins (PR #1264 review
    P2 — no double rotation under concurrent workers).
    """
    access = _new_token(ACCESS_TOKEN_PREFIX)
    refresh = _new_token(REFRESH_TOKEN_PREFIX)
    refresh_id = secrets.token_urlsafe(16)
    access_id = secrets.token_urlsafe(16)
    now = _now_iso()
    # #2863: appended BEFORE the POST, so a commit-then-lost POST still gets its
    # rollback (the row exists even though the response never arrived).
    minted: list[tuple[str, str]] = []
    # #3027: the provenance link back to the authorizing code. Omitted (rather
    # than sent as NULL) for a family with no origin code — a refresh rotation of
    # one minted before this migration. The column and the claim's
    # `redemption_state` filter are read unconditionally, so the migration MUST be
    # applied before this code (the deploy's migration-drift gate is fail-closed
    # on that ordering).
    code_link = {"code_id": code_id} if code_id is not None else {}
    try:
        minted.append(("oauth_refresh_tokens", refresh_id))
        cp.query("oauth_refresh_tokens", method="POST", json_body={
            "id": refresh_id,
            "token_hash": _sha256(refresh),
            "client_id": client_id,
            "user_id": user_id,
            "org_id": org_id,
            "scope": scope,
            "expires_at": _expires_iso(REFRESH_TOKEN_TTL_S),
            "revoked_at": None,
            "rotated_from": prev_refresh["id"] if prev_refresh is not None else None,
            "created_at": now,
            **code_link,
        })
        minted.append(("oauth_access_tokens", access_id))
        cp.query("oauth_access_tokens", method="POST", json_body={
            "id": access_id,
            "token_hash": _sha256(access),
            "client_id": client_id,
            "user_id": user_id,
            "org_id": org_id,
            "scope": scope,
            "expires_at": _expires_iso(ACCESS_TOKEN_TTL_S),
            "revoked_at": None,
            "refresh_token_id": refresh_id,
            "created_at": now,
            **code_link,
        })
        if prev_refresh is not None:
            claimed = cp.query("oauth_refresh_tokens", method="PATCH",
                               select=["id"],
                               filters=[("id", "eq", prev_refresh["id"]),
                                        ("revoked_at", "is", None)],
                               json_body={"revoked_at": _now_iso()})
            if not claimed:
                # A concurrent worker already rotated this grant — roll back the
                # orphan pair so only the winner's tokens survive (lane 1: the
                # INTENTIONAL signal). `_rollback_minted` is contractually
                # non-raising (per-row try/except + a raise-proof `_log_and_capture`),
                # so this cannot spill into lane 2 and convert the pinned
                # `invalid_grant` into an `OAuthMintAborted`.
                _rollback_minted(cp, minted, now, capture=True)
                raise OAuthError(400, "invalid_grant",
                                 "Refresh token already revoked (rotated or invalidated).")
    except OAuthError:
        raise                                              # lane 1 — intentional signal
    except Exception as exc:
        # lane 2 — the SINGLE capture of the triggering exception (I4).
        _log_and_capture(exc, where="_issue_tokens")
        try:
            _rollback_minted(cp, minted, now, capture=False)   # lane 2 already captured
            recovered = _mint_observably_clean(cp, minted)
            if recovered and prev_refresh is not None:
                recovered = _prev_refresh_unclaimed(cp, prev_refresh)
        except Exception as inner:                          # structural no-leak guarantee
            logger.warning("oauth: mint compensation raised: %s", inner)
            recovered = False
        raise OAuthMintAborted(recovered) from exc
    if prev_access_id:                                      # lane 3 — outside the handler
        try:
            cp.query("oauth_access_tokens", method="PATCH",
                     filters=[("id", "eq", prev_access_id)],
                     json_body={"revoked_at": now})
        except Exception as exc:
            logger.warning("oauth: prev-access revoke failed: %s", exc)
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
    resource (must map to the SAME org the code was bound to), then issues
    the access+refresh pair.
    """
    # #2863: the redemption is atomic-feel — a failure after the atomic claim
    # either CAS-restores the code (so a retry provably works) or reports a
    # terminal invalid_grant. Every signal is derived from an OBSERVED state;
    # an unobservable state is never advertised as retryable.
    consumed = False
    attempted_consume = False
    code_row: dict | None = None
    try:
        client = _verify_client_auth(cp, body.get("client_id"), body)   # pure read
        attempted_consume = True
        code_row = _consume_code(cp, body.get("code", ""))              # THE atomic gate
        consumed = True
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
        # org the authorization code was bound to (lenient when omitted — the
        # mcp SDK always sends it, but a bare authorize→token pair is legal).
        resource = body.get("resource")
        if resource:
            _, requested_org = parse_resource(base, resource)
            if requested_org is not None and requested_org != code_row["org_id"]:
                raise OAuthError(400, "invalid_grant",
                                 "Resource indicator does not match the authorized team.")
        _assert_org_usable(cp, code_row["org_id"])
        scope = code_row.get("scope") or " ".join(SCOPES_SUPPORTED)
        out = _issue_tokens(cp, client_id=client["id"], user_id=code_row["user_id"],
                            org_id=code_row["org_id"], scope=scope,
                            resource=code_row.get("resource"),
                            code_id=code_row.get("id"))
        # #3027: record delivery BEFORE returning the pair — and DELIVERY IS
        # GATED ON THIS CAS. The reconciler takes ownership of a stale claim with
        # the same CAS before it revokes anything, so exactly one of us can
        # settle: if we lose, a family we just minted must not be delivered
        # (it would be revoked out from under the client) and we compensate it
        # and report the failure instead.
        if not _settle_redemption(cp, code_row, REDEMPTION_MINTED):
            minted = [("oauth_refresh_tokens", out["_refresh_id"]),
                      ("oauth_access_tokens", out["_access_id"])]
            # capture=True is this path's SINGLE capture (I4): nothing has captured
            # yet — `_issue_tokens` succeeded — and the handler below only logs. A
            # failed compensation here leaves a live, never-delivered row, which
            # must not vanish silently.
            _rollback_minted(cp, minted, _now_iso(), capture=True)
            raise OAuthMintAborted(_mint_observably_clean(cp, minted))
    except OAuthError as exc:
        # #3027: an intentional terminal signal AFTER the claim burns the code
        # durably (CAS-fenced on the claim identity, so it cannot overwrite a
        # reconciler's decision). Without this the row would stay 'claimed' and
        # the reconciler would later burn a live residue the client already
        # knows failed. A retryable signal is not a terminal outcome, so
        # `temporarily_unavailable` never burns.
        if consumed and not isinstance(exc, OAuthTemporarilyUnavailable):
            _settle_redemption(cp, code_row, REDEMPTION_BURNED, note="terminal")
        raise
    except OAuthMintAborted as exc:
        logger.warning("oauth: auth-code mint aborted (recovered=%s)", exc.recovered)
        if exc.recovered and _restore_code(cp, body.get("code", ""), code_row["used_at"]):
            raise OAuthTemporarilyUnavailable() from None
        # `recovered=False` deliberately does NOT record a terminal state: the
        # outcome is UNKNOWN, which is precisely what #3027 exists to represent.
        # The code stays 'claimed' for `_reconcile_claimed_redemption` to resolve
        # against the durable `code_id` link; burning it here would hide the
        # orphan from the one mechanism that can revoke it.
        raise OAuthError(400, "invalid_grant",
                         "The authorization code could not be redeemed — re-run "
                         "authorization.") from None
    except Exception as exc:
        _log_and_capture(exc, where="exchange_auth_code")
        if consumed:
            if _restore_code(cp, body.get("code", ""), code_row["used_at"]):
                raise OAuthTemporarilyUnavailable() from None
            raise OAuthError(400, "invalid_grant",
                             "The authorization code could not be redeemed — re-run "
                             "authorization.") from None
        if not attempted_consume:
            raise OAuthTemporarilyUnavailable() from None        # constructive-clean
        if _consume_state(cp, body.get("code", "")) == "unconsumed":
            raise OAuthTemporarilyUnavailable() from None
        raise OAuthError(400, "invalid_grant",
                         "The authorization code could not be redeemed — re-run "
                         "authorization.") from None
    return {k: v for k, v in out.items() if not k.startswith("_")}


def _revoke_org_family(cp, user_id: str, org_id: str) -> None:
    """D5: revoke the user's ENTIRE refresh-token family for an org (called
    on org suspension). Mirrors durable revocation semantics of api_keys."""
    cp.query("oauth_refresh_tokens", method="PATCH",
             filters=[("user_id", "eq", user_id), ("org_id", "eq", org_id),
                      ("revoked_at", "is", None)],
             json_body={"revoked_at": _now_iso()})


def refresh_grant(cp, body: dict, base: str) -> dict:
    """POST /oauth/token grant_type=refresh_token (D5).

    Rotating per (user, org): each use revokes the presented token and mints
    a fresh pair. Org suspension revokes the whole (user, org) family;
    a lapsed membership revokes the presented token.
    """
    # #2863: wrap every pre-mint read (the FIRST one is `_verify_client_auth` →
    # `oauth_clients`; a wrap starting at the refresh-token SELECT leaves it
    # leaking), and un-mask the two revokes that used to swallow a terminal
    # OAuthError into a bare 500.
    try:
        client = _verify_client_auth(cp, body.get("client_id"), body)
        refresh_token = body.get("refresh_token", "")
        rows = cp.query("oauth_refresh_tokens", select=[
            "id", "token_hash", "client_id", "user_id", "org_id", "scope",
            "expires_at", "revoked_at", "code_id",
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
            _, requested_org = parse_resource(base, resource)
            if requested_org is not None and requested_org != row["org_id"]:
                raise OAuthError(400, "invalid_grant",
                                 "Resource indicator does not match the token's team.")
        # D5: suspension → revoke the whole (user, org) family, then reject.
        try:
            _assert_org_usable(cp, row["org_id"])
        except OAuthTemporarilyUnavailable:
            raise        # a transient signal must NEVER trigger family revocation
        except OAuthError:
            try:
                _revoke_org_family(cp, row["user_id"], row["org_id"])
            except Exception as exc:  # correction #8: the single capture for this path
                _log_and_capture(exc, where="family revoke")
            raise
        # Lapsed membership → revoke this token (the grant dies with the seat).
        from tortoise.supabase_control import membership_for_user_org
        if membership_for_user_org(cp, row["user_id"], row["org_id"]) is None:
            try:
                cp.query("oauth_refresh_tokens", method="PATCH",
                         filters=[("id", "eq", row["id"])],
                         json_body={"revoked_at": _now_iso()})
            except Exception as exc:  # correction #8: the single capture for this path
                _log_and_capture(exc, where="membership revoke")
            raise OAuthError(403, "invalid_grant",
                             "Membership in the team has ended — the grant was revoked.")
        prev_access = cp.query("oauth_access_tokens",
                               select=["id"],
                               filters=[("refresh_token_id", "eq", row["id"]),
                                        ("revoked_at", "is", None)])
    except OAuthError:
        raise
    except Exception as exc:
        _log_and_capture(exc, where="refresh_grant pre-mint")
        raise OAuthTemporarilyUnavailable(
            "Temporary control-plane failure before token rotation — retry.") from None
    try:
        out = _issue_tokens(cp, client_id=row["client_id"], user_id=row["user_id"],
                            org_id=row["org_id"], scope=row.get("scope")
                            or " ".join(SCOPES_SUPPORTED), resource=resource,
                            prev_refresh=row,
                            prev_access_id=prev_access[0]["id"] if prev_access else None,
                            # #3027: rotation INHERITS the family's origin code, so
                            # a live rotated descendant is still discoverable from
                            # the code the reconciler is resolving. A link that died
                            # at the first rotation would make the reconciler blind
                            # to the live family and re-arm a live grant.
                            code_id=row.get("code_id"))
    except OAuthMintAborted as exc:
        logger.warning("oauth: refresh mint aborted (recovered=%s)", exc.recovered)   # I4: log-only
        if exc.recovered:
            raise OAuthTemporarilyUnavailable() from None
        raise OAuthError(400, "invalid_grant",
                         "The refresh token could not be rotated — re-run "
                         "authorization.") from None
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
    """Introspect an ``oat_`` access token → org dict (same shape as
    resolve_api_key) or None. This is the OAuth half of the MCP auth
    boundary — no tt_ key is minted (D6).

    Checks, in order: prefix, token row (revoked_at authoritative, expiry),
    org existence + durable suspension (the suspended_at check rides the
    returned dict so OrgResolutionMiddleware's existing #308 gate applies
    identically to OAuth and tt_ credentials).
    """
    if not isinstance(token, str) or not token.startswith(ACCESS_TOKEN_PREFIX):
        return None
    rows = cp.query("oauth_access_tokens", select=[
        "token_hash", "client_id", "user_id", "org_id", "scope",
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
    org = _org_row(cp, row["org_id"])
    if org is None:
        return None
    org = _quota_fields(cp, org)
    # #2600: the resolved dict carries the RAW actor fields (the token row's
    # user_id/client_id) so consuming seams (mcp_auth middleware / REST DI)
    # can alias the canonical `actor_user_id` — additive, transport-agnostic
    # (the resolver never aliases; the seam gates UUID shape).
    org["user_id"] = row.get("user_id")
    org["client_id"] = row.get("client_id")
    return org


# ── Metadata (P1 — RFC 9728 PRM + RFC 8414 AS metadata) ────────────────────

def protected_resource_metadata(base: str) -> dict:
    """RFC 9728 Protected Resource Metadata for the MCP endpoint."""
    return {
        "resource": mcp_resource_url(base),
        "authorization_servers": [base.rstrip("/")],
        "scopes_supported": SCOPES_SUPPORTED,
        "bearer_methods_supported": ["header"],
    }


def _cimd_advertised() -> bool:
    """#2847 — is the CIMD client-identity path advertised?

    Reads the flag at CALL time (no cached metadata): the env is the reversible
    lever, and a cached copy would make flipping it require a restart.
    """
    from tortoise import cimd
    return bool(cimd.client_id_metadata_document_supported())


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
        # #2847: advertise the registration-free client-identity path. Claude
        # selects CIMD only when this flag AND "none" above are both present
        # (the CIMD client authenticates as a public client), otherwise it
        # falls back to DCR and re-registers on every fresh connection.
        "client_id_metadata_document_supported": _cimd_advertised(),
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": SCOPES_ACCEPTED,
    }


# ── Branded consent page (D2 — one custom HTML page, signup/signin pattern) ─

_CONSENT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Authorize MCP client — Tortoise</title>
<meta name="theme-color" content="#060b14">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #060b14; --surface: #0d1a2d; --text: #cbd5e1; --text-dim: #94a3b8;
    --accent: #06b6d4; --accent-hover: #0891b2; --green: #4ade80;
    --red: #ef4444; --gold: #f59e0b; --border: #1e293b;
    --mono: 'SF Mono','Cascadia Code','Fira Code','JetBrains Mono',monospace;
    --serif: 'Georgia','Times New Roman',serif;
  }
  body { background: var(--bg); color: var(--text); font-family: var(--mono);
         font-size: 14px; line-height: 1.6; -webkit-font-smoothing: antialiased;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; padding: 24px; }
  .card { width: 100%; max-width: 460px; background: var(--surface);
          border: 1px solid var(--border); border-radius: 10px; padding: 2rem; }
  .logo { font-family: var(--serif); font-size: 1.4rem; margin-bottom: 1.5rem; }
  .logo span { color: var(--accent); }
  h1 { font-family: var(--serif); font-size: 1.3rem; font-weight: 400;
       margin-bottom: .5rem; }
  .muted { color: var(--text-dim); font-size: 13px; margin-bottom: 1.25rem; }
  .row { display: flex; justify-content: space-between; padding: .5rem 0;
         border-bottom: 1px solid var(--border); font-size: 13px; }
  .row .k { color: var(--text-dim); }
  .row .v { color: var(--text); word-break: break-all; text-align: right;
            max-width: 60%; }
  .actions { display: flex; gap: .75rem; margin-top: 1.5rem; }
  button { font-family: var(--mono); font-size: 14px; font-weight: 600;
           padding: .6rem 1rem; border-radius: 6px; border: 1px solid var(--border);
           cursor: pointer; flex: 1; }
  .btn-auth { background: var(--accent); color: #04121a; }
  .btn-auth:hover { background: var(--accent-hover); }
  .btn-deny { background: transparent; color: var(--text-dim); }
  .error { display: none; color: var(--red); margin-top: 1rem; font-size: 13px; }
  .error.visible { display: block; }
  input { width: 100%; background: var(--bg); border: 1px solid var(--border);
          color: var(--text); border-radius: 6px; padding: .6rem .75rem;
          font-family: var(--mono); font-size: 14px; margin-bottom: .75rem; }
  .providers { display: flex; gap: .75rem; margin-bottom: .75rem; }
  .btn-provider { background: var(--bg); color: var(--text); }
  .btn-provider:hover { border-color: var(--accent); }
  .spinner { color: var(--text-dim); font-size: 13px; margin-top: 1rem; }
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
    <div class="row"><span class="k">Org</span>
      <span class="v" id="org-line">resolving…</span>
      <select id="org-select" style="display:none;background:var(--bg,#0d1a2d);color:var(--text,#e2e8f0);border:1px solid var(--border,#1e293b);border-radius:6px;font-family:var(--mono);font-size:13px;padding:4px 6px;max-width:60%;text-align:left;" aria-label="Org for this connection"></select>
    </div>
    <div class="actions">
      <button class="btn-deny" id="btn-deny">Deny</button>
      <button class="btn-deny" id="btn-retry-preview" style="display:none">Retry</button>
      <button class="btn-auth" id="btn-auth" disabled>Authorize</button>
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
        nonce="__NONCE__"
        onerror="showError('Auth script blocked — please retry.')"></script>
<script nonce="__NONCE__">
  // ── authorize request params (JSON-embedded by the server; origin-safe) ──
  const PARAMS = __PARAMS__;
  const SUPABASE_URL = __SUPABASE_URL__;
  const SUPABASE_ANON_KEY = __SUPABASE_ANON_KEY__;
  const AUTHORIZE_PATH = "/oauth/authorize";
  // #1704: reuse the dashboard's parent-domain session cookie
  // (sb-tortoise-auth-token on .premiselabs.co — main.jsx supabaseStorage).
  // The user is already signed in on app.premiselabs.co; this page must not
  // ask for a SECOND login. persistSession stays TRUE (gotrue DISCARDS a
  // custom storage when persistSession is false, review P0) and
  // setItem/removeItem are REAL writes — getSession() always re-reads
  // storage, so an ingested OAuth/email session must persist to the cookie
  // or the sign-in fallback loops. detectSessionInUrl stays true so the
  // provider redirect back with #access_token is ingested.
  // #3503: this page is the ONE place it stays true — it does NOT load
  // website/assets/supabase-session.js (that file's factory sets it false,
  // because its load-time IIFE is the fragment consumer there), so this
  // inline client is the sole consumer of the hash and must ingest it.
  const COOKIE_NAME = "sb-tortoise-auth-token";
  // #1704: parent-domain cookie storage — a faithful port of the
  // dashboard's supabaseStorage (website/assets/supabase-session.js):
  // getItem reads an existing dashboard session (no second login),
  // setItem/removeItem persist sign-ins here (the OAuth/email fallback
  // needs a REAL write — getSession() always re-reads storage).
  // Method shorthand so `this` binds to the object (arrow functions
  // would bind window). Size guard + localhost-aware domain/secure
  // attributes mirror the canonical adapter.
  const COOKIE_PATH = "/";
  const COOKIE_DOMAIN = ".premiselabs.co";
  const SIZE_GUARD = 3800;
  const isLocal = () => {
    const h = window.location.hostname;
    if (h === "localhost" || h === "127.0.0.1" || h === "::1" || h === "[::1]") return true;
    if (h.startsWith("10.") || h.startsWith("192.168.")) return true;
    return /^172\.(1[6-9]|2\d|3[01])\./.test(h);
  };
  const isPremiselabsHost = () => {
    const h = window.location.hostname;
    return h === "premiselabs.co" || h.endsWith(".premiselabs.co");
  };
  const domainAttr = () => (isPremiselabsHost() && !isLocal() ? "; Domain=" + COOKIE_DOMAIN : "");
  const secureAttr = () => (isLocal() ? "" : "; Secure");
  const cookieStorage = {
    getItem(key) {
      try {
        const parts = document.cookie.split("; ");
        for (const p of parts) {
          const eq = p.indexOf("=");
          if (eq > 0 && p.slice(0, eq) === key) return decodeURIComponent(p.slice(eq + 1));
        }
        return null;
      } catch (e) { return null; }
    },
    setItem(key, value) {
      if (!value) { this.removeItem(key); return; }
      let encoded = encodeURIComponent(value);
      // Size guard (#1225): a GitHub OAuth session (user_metadata +
      // identities + provider_token) can exceed the 4096-byte cookie
      // cap. provider tokens are only needed by the initiating flow.
      if (encoded.length > SIZE_GUARD) {
        try {
          const obj = JSON.parse(value);
          delete obj.provider_token;
          delete obj.provider_refresh_token;
          encoded = encodeURIComponent(JSON.stringify(obj));
        } catch (e) { /* not JSON — leave as-is */ }
        if (encoded.length > SIZE_GUARD + 100) {
          console.warn('sb-tortoise-auth-token session exceeds cookie size cap (' + encoded.length + ' bytes) — session may not bridge subdomains');
        }
      }
      const expires = new Date(Date.now() + 7 * 24 * 3600 * 1000).toUTCString();
      document.cookie = key + "=" + encoded + domainAttr() + "; Path=" + COOKIE_PATH +
        "; SameSite=Lax" + secureAttr() + "; Expires=" + expires;
    },
    removeItem(key) {
      document.cookie = key + "=;" + domainAttr() + "; Path=" + COOKIE_PATH +
        "; SameSite=Lax" + secureAttr() + "; Max-Age=0";
    },
  };
  let supabaseClient = null;
  try {
    if (typeof window.supabase !== "undefined") {
      supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
        auth: {
          storage: cookieStorage,
          storageKey: COOKIE_NAME,
          persistSession: true,   // required for the custom storage to be used
          autoRefreshToken: false,
          detectSessionInUrl: true,  // OAuth fallback ingests the hash
        },
      });
    } else {
      showError("Auth is temporarily unavailable (script blocked).");
    }
  } catch (e) { showError("Auth init failed: " + e.message); }

  function showError(msg) {
    const el = document.getElementById("error");
    el.textContent = msg; el.classList.add("visible");
  }
  function hideError() { document.getElementById("error").classList.remove("visible"); }
  function spinner(on) { document.getElementById("spinner").style.display = on ? "block" : "none"; }
  function redirectBack(params) {
    const sep = PARAMS.redirect_uri.includes("?") ? "&" : "?";
    window.location.href = PARAMS.redirect_uri + sep + new URLSearchParams(params).toString();
  }

  async function fetchPreview(accessToken) {
    let res;
    try {
      res = await fetch("/oauth/consent/preview?resource=" +
          encodeURIComponent(PARAMS.resource || ""), {
        headers: { "Authorization": "Bearer " + accessToken },
      });
    } catch {
      // network throw (offline blip, DNS, server restart) — transient:
      // the Retry affordance must appear (never a dead-end reload).
      const err = new Error("Could not reach Tortoise — check your connection and retry.");
      err.transient = true;
      throw err;
    }
    if (res.status === 401) return null;   // stale/rejected session
    if (!res.ok) {
      // Terminal 4xx (suspended org, no usable org) carries an actionable
      // error_description — surface it verbatim; only 5xx is retryable.
      const payload = await res.json().catch(() => null);
      const err = new Error((payload && (payload.error_description || payload.error)) ||
          ("Could not resolve org: " + res.status));
      if (res.status >= 500) err.transient = true;
      throw err;
    }
    return res.json();
  }

  // #1701 R1: account-chooser state. orgResource is set ONLY by the picker's
  // change handler — an untouched picker can never authorize (no silent
  // wrong-org bind). previewInFlight guards concurrent showConsent runs.
  let previewInFlight = false;
  let orgResource = null;
  let staleRefreshes = 0;   // at most ONE refresh per stale cycle
  const authBtn = () => document.getElementById("btn-auth");
  function disableAuthorize() { authBtn().disabled = true; }
  function enableAuthorize() { authBtn().disabled = false; }
  function showRetry() { document.getElementById("btn-retry-preview").style.display = "inline-block"; }
  function hideRetry() { document.getElementById("btn-retry-preview").style.display = "none"; }
  function showExpiredSignin() {
    // NEVER call the auth logout API here — the session cookie is shared
    // with the dashboard (parent domain) and a global logout would revoke
    // it server-side on every device. A fresh sign-in replaces the cookie.
    showSignin();
    showError("Your session expired — sign in again.");
  }

  async function showConsentOnce() {
    const { data } = await supabaseClient.auth.getSession();
    if (!data.session) { showSignin(); return "nosession"; }
    document.getElementById("view-consent").style.display = "block";
    document.getElementById("view-signin").style.display = "none";
    hideError();
    document.getElementById("client-line").textContent =
        PARAMS.client_name + " wants to access your Tortoise MCP surface.";
    document.getElementById("scope-line").textContent = PARAMS.scope || "mcp";
    document.getElementById("org-line").style.display = "";
    const orgSelect = document.getElementById("org-select");
    orgSelect.style.display = "none";
    hideRetry();
    try {
      const preview = await fetchPreview(data.session.access_token);
      if (!preview) return "stale";   // session rejected/expired
      const memberships = preview.memberships;
      if (memberships && memberships.length > 1) {
        // Account chooser — options are REBUILT from scratch every run so a
        // sequential re-run can never duplicate rows. Authorize stays disabled
        // until the user explicitly picks an org (change event below).
        document.getElementById("org-line").style.display = "none";
        while (orgSelect.firstChild) orgSelect.removeChild(orgSelect.firstChild);
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.disabled = true;
        placeholder.selected = true;
        placeholder.textContent = "Choose an org…";
        orgSelect.appendChild(placeholder);
        memberships.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.resource;
          opt.textContent = (m.org_name || m.org_id) + " (" + m.org_id + ")";
          orgSelect.appendChild(opt);
        });
        orgSelect.style.display = "block";
        document.getElementById("resource-line").textContent =
            "Tortoise MCP — choose the org this connection will use";
        disableAuthorize();
      } else if (preview.org_id) {
        // single / sole-active-org auto-bind — the page renders EXACTLY as
        // before R1 (byte-identical single-org contract): the resource line
        // shows the client-declared value (or the pre-R1 default), never the
        // resolved org URL
        document.getElementById("resource-line").textContent =
            PARAMS.resource || "default (sole org)";
        document.getElementById("org-line").textContent =
            (preview.org_name || preview.org_id) + " (" + preview.org_id + ")";
        enableAuthorize();
      } else {
        disableAuthorize();
        showError("No usable org for this account.");
      }
      return "ok";
    } catch (e) {
      disableAuthorize();
      showError(e.message);
      if (e.transient) showRetry();   // terminal 4xx/network-side copy needs no retry affordance
      return "error";
    }
  }

  async function runConsentFlow() {
    if (previewInFlight) return;   // concurrent guard (spans the refresh too)
    previewInFlight = true;
    orgResource = null;           // never carry a stale selection between runs
    staleRefreshes = 0;            // one-shot cap per cycle — never sticky across runs
    disableAuthorize();
    let result;
    try {
      result = await showConsentOnce();
      if (result === "stale" && staleRefreshes < 1) {
        // refresh-first recovery (NEVER sign-out): at most ONE refresh per
        // stale cycle, and the in-flight guard stays held across it so an
        // onAuthStateChange (INITIAL_SESSION/SIGNED_IN) racing the refresh
        // cannot double-run and reuse the rotating refresh token.
        staleRefreshes += 1;
        const { error } = await supabaseClient.auth.refreshSession();
        if (!error) result = await showConsentOnce();
      }
    } finally {
      previewInFlight = false;
    }
    if (result === "stale") showExpiredSignin();
  }

  function showSignin() {
    document.getElementById("view-consent").style.display = "none";
    document.getElementById("view-signin").style.display = "block";
  }

  async function signInWithProvider(provider) {
    const { error } = await supabaseClient.auth.signInWithOAuth({
      provider: provider,
      options: { redirectTo: window.location.origin + AUTHORIZE_PATH + window.location.search },
    });
    if (error) showError(error.message);
  }

  document.getElementById("btn-github").onclick = () => signInWithProvider("github");
  document.getElementById("btn-google").onclick = () => signInWithProvider("google");
  document.getElementById("btn-email").onclick = async () => {
    hideError();
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;
    if (!email || !password) { showError("Enter email and password."); return; }
    const { error } = await supabaseClient.auth.signInWithPassword({ email, password });
    if (error) { showError(error.message); return; }
    runConsentFlow();
  };

  const orgSelectEl = document.getElementById("org-select");
  orgSelectEl.onchange = function () {
    orgResource = orgSelectEl.value;
    if (orgResource) enableAuthorize(); else disableAuthorize();
  };

  document.getElementById("btn-auth").onclick = async () => {
    hideError(); spinner(true);
    if (authBtn().disabled) { spinner(false); showError("Resolving your org… retry in a moment."); return; }
    const { data } = await supabaseClient.auth.getSession();
    if (!data.session) { spinner(false); showSignin(); return; }
    const doPost = async (accessToken) => fetch("/oauth/consent", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + accessToken,
      },
      body: JSON.stringify({
        client_id: PARAMS.client_id,
        redirect_uri: PARAMS.redirect_uri,
        response_type: PARAMS.response_type,
        code_challenge: PARAMS.code_challenge,
        code_challenge_method: PARAMS.code_challenge_method,
        state: PARAMS.state,
        scope: PARAMS.scope,
        resource: orgResource || PARAMS.resource || null,
      }),
    });
    try {
      let res = await doPost(data.session.access_token);
      if (res.status === 401) {
        // one refresh, one re-POST with the FRESH token (never logout)
        const { error } = await supabaseClient.auth.refreshSession();
        if (!error) {
          const { data: d2 } = await supabaseClient.auth.getSession();
          if (d2 && d2.session) res = await doPost(d2.session.access_token);
        }
      }
      const payload = await res.json();
      if (!res.ok) {
        if (res.status === 401) { spinner(false); showExpiredSignin(); return; }
        throw new Error(payload.error_description || payload.error || "Consent failed");
      }
      const q = { code: payload.code };
      if (payload.state) q.state = payload.state;
      redirectBack(q);
    } catch (e) { spinner(false); showError(e.message); }
  };

  document.getElementById("btn-retry-preview").onclick = () => {
    hideError(); hideRetry();
    runConsentFlow();
  };

  document.getElementById("btn-deny").onclick = () =>
      redirectBack({ error: "access_denied", state: PARAMS.state });

  // #1701 R1: auto-advance when a session lands after an initial null
  // (provider redirect hash ingestion / cookie session). runConsentFlow is
  // in-flight guarded, so a double fire never runs two overlapping previews.
  if (supabaseClient) {
    supabaseClient.auth.onAuthStateChange((event) => {
      if (event === "INITIAL_SESSION" || event === "SIGNED_IN") runConsentFlow();
    });
    runConsentFlow();
  } else spinner(false);
</script>
</body>
</html>
"""


def _json_for_script(obj) -> str:
    """JSON-encode a value for embedding inside an HTML <script> block.

    json.dumps does NOT escape ``<``, ``>``, ``&`` or the U+2028/U+2029 line
    separators — an attacker-controlled string containing ``</script>`` would
    close the script element and inject markup/script. Escaping those
    characters as \\u sequences (OWASP XSS cheat sheet) keeps the JSON
    valid while making script-element breakout impossible.
    """
    return (json.dumps(obj)
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def consent_page_html(*, client_name: str, scope: str | None,
                      params: dict, supabase_url: str,
                      supabase_anon_key: str) -> tuple[str, str]:
    """Render the branded consent page (D2). ``params`` are the raw authorize
    query params — JSON-encoded into the page so the JS echoes them back.

    Returns ``(html, csp_nonce)``. The nonce is embedded in both <script>
    tags so the caller can serve a script-src CSP that still permits the
    inline consent logic. Attacker-controlled values (state / scope /
    resource / client_name) are JSON-escaped for the <script> context, so a
    ``</script>`` payload cannot break out of the block (PR #1264 review P1).
    """
    safe_params = {
        k: (v if isinstance(v, str) else "")
        for k, v in params.items()
    }
    safe_params["client_name"] = client_name
    nonce = secrets.token_urlsafe(16)
    html = _CONSENT_HTML.replace("__PARAMS__", _json_for_script(safe_params)) \
        .replace("__SUPABASE_URL__", _json_for_script(supabase_url.rstrip("/"))) \
        .replace("__SUPABASE_ANON_KEY__", _json_for_script(supabase_anon_key)) \
        .replace("__NONCE__", nonce)
    return html, nonce


# ── Retention / GC (issue #3036) ────────────────────────────────────────────

def sweep_oauth_retention(cp, *, now: datetime | None = None) -> dict[str, int]:
    """Hard-delete dead OAuth rows past their retention grace (issue #3036).

    GC for the three token tables 0016 introduced with no TTL sweep. A row is
    dead once its own ``expires_at`` is in the past (a redeemed or unredeemed
    code, an expired or revoked access/refresh token); it is then kept for a
    short forensic grace (``OAUTH_*_RETENTION_S``) before this sweep removes
    it. Those windows are credential hygiene — a different axis from the
    user-content deletion promise (``docs/retention-and-deletion.md``).

    Delete order: access rows first, then refresh rows, then codes. That order
    matters because ``refresh_grant`` DOES dereference the relationship (it
    looks up the live access row by ``refresh_token_id`` to revoke it): an
    access row is reaped before any refresh row it points at, so the
    ``ON DELETE SET NULL`` action added by migration 20260925000001 is a safety
    net for an out-of-band / manual delete, not this path.

    The invariant that makes the order sufficient is ``ACCESS_TOKEN_TTL_S +
    access window <= REFRESH_TOKEN_TTL_S + refresh window``. It HOLDS for the
    shipped defaults; it is NOT enforced, so an operator override that inverts
    the two TTLs relative to the two windows can leave a LIVE access row
    pointing at a reap-eligible refresh row, and this sweep then NULLs that
    back-link via the FK. That is a PROVENANCE loss, not a revocation gap: no
    read path treats a NULL pointer as a live grant (``refresh_grant``
    resolves the refresh row by hash first and only then dereferences; the
    rotation path cannot run once the parent row is gone), and the access
    token still carries its own ``expires_at``/``revoked_at`` check.

    Each table is swept INDEPENDENTLY: a failure on one table is recorded and
    the other two are still attempted, so a persistent query fault cannot
    starve GC for the healthy tables. If any table failed, a RuntimeError is
    raised AFTER the loop (fail-closed); every table already swept committed,
    and the un-swept rows keep their past ``expires_at`` so the next cycle
    retries them.

    Returns, per table, the number of rows OBSERVED as eligible at sweep time.
    It is a best-effort count, not an exact delete count: the eligibility read
    is a separate PostgREST request (capped by the project's max-rows) and the
    DELETE is a second request, so a full read page makes the number a lower
    bound. Idempotent: a re-run finds nothing and deletes nothing.
    """
    now_dt = now or _now()

    def _cutoff(seconds: int) -> str:
        # Defensive floor: a cutoff of `now` only matches rows already expired.
        return (now_dt - timedelta(seconds=max(0, int(seconds)))).isoformat()

    plan = (
        ("oauth_access_tokens", "TORTOISE_OAUTH_ACCESS_RETENTION_S",
         OAUTH_ACCESS_RETENTION_S),
        ("oauth_refresh_tokens", "TORTOISE_OAUTH_REFRESH_RETENTION_S",
         OAUTH_REFRESH_RETENTION_S),
        ("oauth_codes", "TORTOISE_OAUTH_CODE_RETENTION_S",
         OAUTH_CODE_RETENTION_S),
    )
    observed: dict[str, int] = {}
    failures: dict[str, str] = {}
    for table, env_name, default in plan:
        observed[table] = 0
        try:
            cutoff = _cutoff(_retention_seconds(env_name, default))
            doomed = cp.query(table, select=["id"],
                              filters=[("expires_at", "lt", cutoff)])
            if not doomed:
                continue
            cp.query(table, method="DELETE",
                     filters=[("expires_at", "lt", cutoff)])
            observed[table] = len(doomed)
        except Exception as exc:  # per-table isolation — sweep the rest
            failures[table] = str(exc)
            logger.warning("oauth: retention sweep failed for %s: %s",
                           table, exc)
    if failures:
        raise RuntimeError(
            "oauth retention sweep failed for "
            + ", ".join(f"{t}: {failures[t][:200]}" for t in sorted(failures)))
    return observed
