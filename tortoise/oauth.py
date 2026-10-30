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
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import NoReturn
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit, urlunsplit  # noqa: F401

logger = logging.getLogger("tortoise.oauth")

# ── Protocol constants ──────────────────────────────────────────────────────

# SCOPES_SUPPORTED is the *client-facing default* / PRM document scope set;
# SCOPES_ACCEPTED is the superset the DCR gate accepts and the AS metadata
# advertises (#2866). `offline_access` is accepted (Claude's connector
# requests it, and the AS does mint refresh tokens unconditionally) without
# becoming a default fallback or a PRM-advertised scope. The superset
# relation is structural. `validate_scope` reads this list on the
# authorize/consent/mint paths and `register_client` shares its membership
# test (#3128), so SCOPES_ACCEPTED is the whole scope policy — never mint a
# scope that is not in it.
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
#
# #3025 removed the `claimed → unclaimed` transition: the claim now lives inside
# the `redeem_oauth_code` transaction, so a failure before its commit leaves the
# code UNCLAIMED outright and a failure after it is a real outcome the reconciler
# must resolve. There is nothing left to "re-arm" in process, and an in-process
# re-arm was always the wrong tool against a claim whose owner might be alive.
# A `claimed` row can still arise — the commit succeeded and the process died
# before the delivery gate — which is why the state, the reconciler and
# `REDEMPTION_CLAIMED` all stay.
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
# The CLAIM sets `used_at` and `redemption_state` in ONE statement; a settle only
# transitions `redemption_state` on a row that is already `claimed`. So the
# biconditional holds for everything we write, and the DB enforces the direction
# that matters (no settled row without a claim timestamp).
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


class OAuthTemporarilyUnavailable(OAuthError):
    """503 `temporarily_unavailable` — raised ONLY when retrying cannot double-issue:

      * constructively, where no write was attempted at all (a pre-mint read
        failed, or a pre-claim validation read failed);
      * by observation, where a write may have landed — the grant is confirmed
        untouched (`_prev_refresh_unclaimed` / `_consume_state`);
      * where a zero-row claim was OBSERVED and the failure is a later
        classification READ (#3027 `_observe_code` returning 'unobservable') —
        nothing was written by this request, and the retry re-runs the same claim
        CAS, which is what decides.

    Never on an unobserved WRITE state. #3025 makes this sharper rather than
    looser: the mint RPC is one transaction, so a RAISE rolled everything back and
    the probe confirms it — but a failure whose response was LOST after the commit
    is indistinguishable from that, which is why the probe (not the exception) is
    what decides whether the retryable signal is emitted.

    (This distinction is #2863's, not a new one; #3025 removed the in-process
    compensation that used to answer it, not the rule.)
    """

    def __init__(self, error_description: str = "Temporary control-plane failure — retry."):
        super().__init__(503, "temporarily_unavailable", error_description)


def _log_and_capture(exc: BaseException, *, where: str) -> None:
    """One WARNING + at most one Sentry capture for a conversion path. Must never raise.

    OWNER TABLE (I4 — never capture twice for one request):
      #3027 delivery-gate loss (capture=True) → the single capture for that path
                                               (the mint committed, so nothing has
                                               captured; the handler only logs)
      `_reconcile_claimed_redemption` revoke → the single capture for that path
      the three correction-#8 revokes  → each the single capture for its terminal path
                                        (family revoke / membership revoke /
                                        poisoned-scope revoke)
      `exchange_auth_code` pre-mint `except Exception`
                                       → this call IS the single capture for that path
      `refresh_grant` MINT failure (`where="refresh_grant mint"`)
                                       → this call IS the single capture for that path
                                        (it replaces the capture `_issue_tokens` lane 2
                                        owned before #3025; the pre-mint read handler
                                        above is a DIFFERENT path and captures the
                                        pre-mint failure instead — neither re-captures
                                        the other)
      `oauth_token` boundary           → this call IS the single capture for that path

    LOG-ONLY paths (deliberately NO capture, listed so the absence is not read as
    an omission): the refused-code burn failure — a control-plane failure while
    RECORDING an already-decided refusal must not page, and the coherent OAuth
    error is still raised; the pre-#3025 table made the same choice for its lane 3.

    #2863's `_issue_tokens` lanes are gone (#3025): the rollback/observe protocol
    they captured for no longer exists, so neither does its capture accounting.
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


# ── Scope policy (the single allow-list gate, #3128) ────────────────────────

def _unsupported_scope(scope: str) -> list[str]:
    """Scope tokens this AS does not accept (empty ⇒ every token is accepted).

    The one membership test shared by the DCR gate (``register_client``) and
    the authorize/consent/mint gate (``validate_scope``), so those two can
    never drift from the advertised ``SCOPES_ACCEPTED`` set (#3128). The CIMD
    document validator (``tortoise/cimd.py``) applies the same test through
    its own ``supported_scopes`` argument, which is wired to this same
    constant.
    """
    return [s for s in scope.split() if s not in SCOPES_ACCEPTED]


def validate_scope(scope) -> str:
    """The authorize/consent/mint scope gate (#3128).

    Returns the canonical (space-delimited, single-spaced) scope string, or
    raises ``invalid_scope`` when any requested token is outside
    ``SCOPES_ACCEPTED``. Per RFC 6749 §3.3 + §4.1.2.1 an unsupported scope is
    a client error (``invalid_scope``: "The requested scope is invalid,
    unknown, or malformed") — it is REJECTED, never silently minted onto an
    authorization code or a token claim. §3.3's alternative ("MAY fully or
    partially ignore the scope requested") is deliberately not used to
    intersect with the client's registered scope: #2866 admits
    ``offline_access`` at the AS level for a client that requests it, and a
    default ``mcp`` registration would otherwise be silently narrowed — a
    user-visible revocation of a scope the AS advertises.

    A blank/absent scope falls back to ``SCOPES_SUPPORTED`` (RFC 6749 §3.3
    pre-defined default). A non-string scope is malformed ⇒ ``invalid_scope``.
    """
    blank = scope is None or (isinstance(scope, str) and not scope.split())
    resolved = " ".join(SCOPES_SUPPORTED) if blank else scope
    if not isinstance(resolved, str):
        raise OAuthError(400, "invalid_scope", "scope must be a string.")
    if _unsupported_scope(resolved):
        # Deliberately does NOT echo the requested tokens: the value is
        # attacker-controlled and lands in a redirect query string.
        raise OAuthError(400, "invalid_scope",
                         f"Unsupported scope. Supported: {SCOPES_ACCEPTED}")
    return " ".join(resolved.split())


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
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    # IPv4-MAPPED IPv6 (``::ffff:127.0.0.1``) is loopback iff its embedded IPv4 address is, and
    # CPython only reports that on 3.11.13+/3.12.7+ (gh-117566) — while ``requires-python`` here is
    # ``>=3.12``. Normalising FIRST makes the verdict identical on every supported interpreter,
    # exactly as ``_parse_bind_ip`` does for ``--bind`` (#719). Without it this predicate flips
    # with the interpreter's PATCH level: on 3.12.0–3.12.6 it refuses a loopback redirect that the
    # AS accepts. The guard is test_the_loopback_verdict_does_not_depend_on_the_interpreter_patch_level
    # — the cross-half table only bites on a buggy stdlib.
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


# Bytes we refuse to reason about in a redirect URI, checked BEFORE any
# comparison or echo. The authorization code reaches the client by NAVIGATING
# THE BROWSER, and the thing navigated is now a URL REBUILT from the registered
# URI (`_canonical_redirect_target`, #3443) rather than the request string — so
# this gate is no longer the boundary it was when `redirectBack` navigated the
# raw `redirect_uri`. It is kept, and it is still load-bearing, in two roles:
#
#   * ON THE REGISTERED SIDE — and this is the one that cannot be retired. The
#     rebuild TRUSTS the registered URI (it re-emits the scheme, authority, path
#     and query from it verbatim, taking only the port from the request), so a
#     differential string sitting in a client row would be navigated with the
#     server's own blessing. `_valid_redirect_uri` refuses these bytes at
#     REGISTRATION for exactly that reason, and `_redirect_uri_matches` refuses
#     them on both sides so no pair carrying one can ever reach the rebuild.
#   * ON THE REQUEST SIDE, as defence in depth: a request that carries a byte we
#     refuse to reason about is refused outright rather than canonicalized. That
#     is what makes "the two parsers cannot disagree" a property of the COMPOSED
#     path (rebuild + gate) and not only of the rebuild.
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
#   * C1 controls (U+0080-U+009F) and the other Unicode `Cc` characters are NOT
#     refused, and that is a RECORDED boundary rather than an unexamined gap:
#     this predicate is "C0 + DEL", never "all control characters". Review
#     established empirically that C1 does not move the authority boundary —
#     0 host divergences across 7,623 single-insertion and 57,212 fuzz
#     candidates without a backslash — so it is not a differential today.
#     Recorded HERE because #3443 makes the registered URI the trust anchor of
#     the navigation (`_canonical_redirect_target` re-emits its authority
#     verbatim): the property that has to hold is therefore "our parse of the
#     REGISTERED uri agrees with the browser's", and C1 is the byte class where
#     that rests on measurement rather than on a refusal. If it is ever
#     challenged, widening this predicate is a one-line change.
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
# delivers the code by navigating to the target rebuilt from the REGISTERED uri
# (`window.location.href = <rebuilt target> + "?code=…"`, #3443), whose scheme
# is therefore the registered one — so a registrable scheme the browser
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
        # while it still made the consent page's navigation (the target rebuilt
        # from this URI, fragment included — #3443) land the code inside the
        # fragment (caught in review — both reviewers, independently). `#` can
        # only ever begin the fragment (RFC 3986 pchar excludes it), so presence
        # is the correct predicate. Still load-bearing after #3443: the rebuild
        # re-emits the registered URI's fragment, so a `#` in a client row would
        # put the code where the browser never sends it.
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


# #3443 — the redirect target the BROWSER IS SENT TO.
#
# `_redirect_uri_matches` answers WHETHER a presented URI may be redirected to;
# this answers WHAT is navigated. Keeping those two questions apart is the whole
# point: the browser's parse — never ours — is what decides where the code
# actually goes, so while the navigated string IS the request's string, the two
# parsers have to agree on every byte, and the byte gate is the boundary (the
# arrangement #2846 left behind, and the one three review rounds kept revisiting
# — see the comment above `_unsafe_redirect_uri_bytes`).
#
# Rebuilding the target from the REGISTERED URI's components with only the
# presented PORT applied (RFC 8252 §7.3 — the one component permitted to vary)
# removes the requirement instead of enumerating its cases: nothing the request
# sends can reach the browser EXCEPT the port's digits, so a differential cannot
# move the host whatever the two parsers disagree about. The authority comes from
# the REGISTERED side verbatim: rebuilding it from the PRESENTED components would
# re-emit them (`urlsplit` reads the host after the last `@`, so
# `http://evil.example\@localhost/cb` parses to host `localhost` with the whole
# `evil.example\@localhost` still in its netloc).

#: The largest port a browser will dial — also the top of `urlsplit().port`'s
#: accepted range. RFC 3986's ``port = *DIGIT`` carries no bound of its own, so
#: the limit comes from the transport rather than the grammar.
_PORT_TEXT_MAX = 65535


def _authority_parts(netloc: str) -> tuple[str, str]:
    """Split an authority into ``(authority-without-port, port-text)``.

    Textual on purpose: ``urlsplit(...).port`` raises on a port that is not
    ASCII digits or is out of range, and every caller here must stay total on
    input `_redirect_uri_matches` accepts — it deliberately does NOT validate
    port sanity (pinned by ``test_port_values_are_not_validated``), because a
    browser refuses to navigate such a URI at all.

    RFC 3986 §3.2: the port is the last ``:``-delimited component of the
    authority. USERINFO IS STRIPPED FIRST — a ``]`` in userinfo is not the IPv6
    terminator, and reading it as one drops the presented port for a
    registrable, matching pair — then a host that OPENS with ``[`` ends at its
    ``]``. A ``]`` that does not belong to a bracketed host is NOT a terminator:
    honouring it started the port scan AFTER it, so the separator in front of it
    was never found and an authority that HAS a port was reported as having
    none — which drops a registered port, or appends a presented port to an
    authority that already carries one (`host:8787]:3118`). `urlsplit` refuses
    those shapes outright (`_check_bracketed_netloc`: brackets are legal only in
    a host that opens with ``[``), so this is defence in depth for a hand-rolled
    parser inside the trust boundary rather than a live path — the caller's
    ``urlsplit`` guard is what answers with the trusted side there.

    ⚠️ "No ``:`` at all" and "a ``:`` with an EMPTY port text" deliberately
    return the same ``""``: to the caller both mean "the request names the
    scheme's default port", which is ONE branch. A port text that is PRESENT but
    unusable is a different state, because it must keep the registered authority.
    """
    # Strip userinfo FIRST: a `]` in it is not the IPv6 terminator (reading it
    # as one dropped the presented port for a registrable, matching pair), and
    # `@` cannot follow the host, so everything after the LAST `@` is the host.
    host_at = netloc.rfind("@") + 1
    # …and only a host that OPENS with `[` is bracketed: a stray `]` after the
    # port separator must not hide that separator (see the docstring).
    bracketed = netloc.startswith("[", host_at)
    end = netloc.find("]", host_at) if bracketed else -1
    port_at = netloc.find(":", end if end >= 0 else host_at)
    if port_at < 0:
        return netloc, ""
    return netloc[:port_at], netloc[port_at + 1:]


def _navigable_port(port_text: str) -> bool:
    """RFC 3986 ``port = *DIGIT``, inside the range a browser will dial.

    ``isascii`` first: ``str.isdigit()`` is true for Unicode digits such as
    ``\u0663`` and ``int()`` accepts them, but the WHATWG port parser does not —
    an ASCII-only grammar is what keeps this a rebuild of the registered URI
    rather than a re-introduction of the request's bytes.

    ⚠️ The LENGTH bound is load-bearing, not tidiness: ``int()`` on a digit
    string longer than ``sys.get_int_max_str_digits()`` (4300) RAISES
    ``ValueError``, so converting first would make a 4301-digit port text an
    unhandled error on the unauthenticated ``/oauth/authorize`` path — the
    caller's last-resort handler then answers 500 for plain client input, where
    the contract is a 400. ``lstrip("0")`` first, so a port spelled with
    leading zeros (`:000008080`) still compares as the number it denotes.
    """
    if not (port_text.isascii() and port_text.isdigit()):
        return False
    significant = port_text.lstrip("0") or "0"
    return (len(significant) <= len(str(_PORT_TEXT_MAX))
            and int(significant) <= _PORT_TEXT_MAX)


def _canonical_redirect_target(registered: str, presented: str) -> str:
    """#3443 — the URL to NAVIGATE the browser to, for a matched pair.

    The registered URI's scheme, authority, path, query and fragment, with the
    presented port applied. Only the port comes from the request: that is the
    component RFC 8252 §7.3 requires the AS to ignore, so it is the only one
    that may legitimately differ. ⚠️ The port IS still a component of the
    authority (RFC 3986 §3.2), so its digits are the ONE thing in the emitted
    string the request controls — and they are copied as TEXT, never re-rendered
    from a parsed int (which would substitute digits the request did not send),
    and admitted only when they are ASCII `*DIGIT` inside 0–65535. Everything
    else — scheme, host, userinfo, path, query, fragment — is the registered
    side's.

    For every pair the current matcher accepts, the result is byte-identical to
    the PRESENTED string except (a) where only the case of the scheme or host
    differs (`_redirect_uri_matches` compares those case-insensitively, and the
    authority comes from the registered side), (b) a presented port no URI may
    carry, (c) an EMPTY presented port (`http://host:/cb`), which names the
    scheme default and is emitted without its colon, (d) a registered URI with
    NO authority, returned verbatim (no port to vary, and `urlunsplit` would
    promote a `//`-leading path into a netloc), and (e) a trailing empty `?`.

    Total (never raises), and idempotent for a target the NAVIGABLE-port branch
    produced; a target ending in an EMPTY port re-canonicalizes on a second pass.

    Called ONLY after `_redirect_uri_matches` accepted the pair; the `presented`
    port it copies is the one component the matcher never inspects.
    """
    try:
        reg = urlsplit(registered)
        pre = urlsplit(presented)
    except ValueError:
        # An unbalanced IPv6 bracket is the one input `urlsplit` rejects
        # outright. `_redirect_uri_matches` refuses such a pair before this is
        # reached (it catches the same ValueError), so this branch is a
        # totality guard, not a live path: the REGISTERED string is the trusted,
        # validated side, and returning it is what keeps the function total.
        return registered
    if not reg.netloc:
        return registered
    reg_authority, _ = _authority_parts(reg.netloc)
    _, presented_port = _authority_parts(pre.netloc)
    if presented_port and _navigable_port(presented_port):
        netloc = reg_authority + ":" + presented_port
    elif presented_port:
        # A port no URI may carry (`:abc`, `:99999`) — there is nothing to
        # apply, so the REGISTERED authority stands as it is. Reachable only
        # because the matcher does not validate port sanity; the presented URI
        # is one no browser navigates at all, so this loses no working
        # destination and keeps the code on the endpoint that was registered.
        netloc = reg.netloc
    else:
        # No port presented: the request names the scheme's default port, which
        # is the registered port DROPPED when the registration carried one.
        # Identical to what `redirectBack` navigated before this change for
        # every accepted pair, which is the fidelity the CLI depends on.
        netloc = reg_authority
    return urlunsplit((reg.scheme, netloc, reg.path, reg.query, reg.fragment))


def canonical_redirect_target(client: dict | None,
                              presented: str | None) -> str | None:
    """#3443 — the navigation target for ``client`` + ``presented``, or
    ``None`` when no registered URI matches (NEVER the raw request string).

    The single source both the success navigation and the pre-round-trip error
    redirect take their destination from, so neither can fall back to
    `redirect_uri` as presented. A non-list ``redirect_uris`` (legacy or
    hand-corrupted row) is normalized exactly as `validate_authorize_params`
    normalizes it, so the two can never disagree about which entries exist.
    """
    if not client or not isinstance(presented, str) or not presented:
        return None
    registered_uris = client.get("redirect_uris") or []
    if not isinstance(registered_uris, (list, tuple)):
        registered_uris = [registered_uris]
    for uri in registered_uris:
        if _redirect_uri_matches(uri, presented):
            return _canonical_redirect_target(uri, presented)
    return None


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
    if _unsupported_scope(scope):
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
                              code_challenge_method: str | None,
                              scope: str | None = None) -> dict:
    """Validate the /oauth/authorize request. Returns the client row.

    #3128: ``scope`` is the requested scope and is validated HERE — on the one
    path shared by GET /oauth/authorize (page render) and POST /oauth/consent
    (code mint) — so an out-of-allow-list scope is refused before a code is
    stored. ``scope or client.get("scope")`` mirrors the mint fallback
    exactly.
    """
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
        # #3128: the requested scope is checked against SCOPES_ACCEPTED (the
        # same set DCR enforces) BEFORE the consent page renders or a code is
        # minted. A scope the AS does not advertise is a client error.
        validate_scope(scope or client.get("scope"))
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


def _rollback_minted(cp, minted: list[tuple[str, str]], now: str, *, capture: bool) -> None:
    """Idempotent soft-revoke by id of every row this request may have written.

    A PATCH filtered by `id` is a VERIFIED no-op on a missing row in both seams
    (real: `Prefer: return=minimal` → `[]`; fake: `select is None` → `[]`), so zero
    affected rows is the EXPECTED SUCCESS for a write that never committed — this
    function must never raise on an empty result. Each row is attempted in its own
    try/except. At most ONE capture is emitted per call: the pair may fail on both
    rows (refresh + access), and I4 permits exactly one Sentry event for that
    request — the subsequent row failures are logged only.

    #3025: this is now reached from exactly TWO places, both #3027's — the
    lost-delivery settle compensation in `exchange_auth_code`, and
    `_reconcile_claimed_redemption` revoking an orphan family. #2863's use of it
    (unwinding a half-minted pair inside `_issue_tokens`) is gone with the window
    it compensated for.
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


def _prev_refresh_unclaimed(cp, prev_refresh: dict) -> bool:
    """READ-ONLY: is the presented refresh token still unrevoked?

    #3025: this is now the RETRYABILITY PROBE for a failed `rotate_oauth_refresh`,
    not a compensation observation. The rotation is one transaction, so an
    unrevoked row means the whole call rolled back and a retry is safe; a revoked
    row means it committed and the response was lost (only a re-authorization
    recovers). Fails CLOSED: any raise → False, because an unreadable observation
    is not evidence that the grant is intact.
    """
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
            "id", "code_hash", "used_at", "expires_at", "redemption_state",
            "redemption_id", "client_id", "user_id", "org_id", "redirect_uri",
            "code_challenge", "code_challenge_method", "scope", "resource",
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
    BURNED — fail safe; the client re-runs authorization. (#3025 removed the
    in-process re-arm too: a failure before the `redeem_oauth_code` commit leaves
    the code unclaimed outright, so there is no longer a verified-clean residue
    inside the request to re-arm.)

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


def _refuse_observed_code(cp, observed: dict) -> NoReturn:
    """Raise the terminal (or retryable) error for a code that cannot be claimed.

    #3025: this is the CLASSIFICATION half of what `_consume_code` did. The claim
    itself moved into `redeem_oauth_code`, where it shares one transaction with the
    mint; so a code that could not be claimed is now classified from a READ-ONLY
    observation, with the same verdicts as before.

    Every verdict EXCEPT `unobservable` is TERMINAL (`invalid_grant`): a minted
    code is a replay, and a CLAIMED code is consumed by an attempt that may still
    be running — a different request must never report it retryable (the retry can
    terminate, which #2863 records as the untruthful signal it removed) and must
    never take the claim over in process (that is the reconciler's job, and it is
    CAS-fenced). `unobservable` — the classification READ failed — is a retryable
    503 instead, because nothing this request did can have written: `_observe_code`
    is reached only from the pre-claim read, or after a claim the RPC OBSERVED as
    zero rows.

    A `claimed` observation also triggers the lazy reconcile, which settles the
    residue for good.
    """
    state = observed.get("state")
    if state == REDEMPTION_CLAIMED:
        verdict = _reconcile_claimed_redemption(cp, observed)
        logger.info("oauth: code already claimed (%s)", verdict)
        raise OAuthError(400, "invalid_grant", "Invalid authorization code.")
    if state == "unobservable":
        raise OAuthTemporarilyUnavailable(
            "Could not determine the authorization code's state — retry.")
    if state == "expired":
        raise OAuthError(400, "invalid_grant", "Authorization code expired.")
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


def _new_pair_material() -> dict:
    """Fresh token material for one mint. The plaintext never leaves this
    process; only the SHA-256 digests cross the control-plane seam."""
    return {
        "access": _new_token(ACCESS_TOKEN_PREFIX),
        "refresh": _new_token(REFRESH_TOKEN_PREFIX),
        "refresh_id": secrets.token_urlsafe(16),
        "access_id": secrets.token_urlsafe(16),
    }


def _token_response(material: dict, scope: str) -> dict:
    """The RFC 6749 §5.1 body, plus the underscore-prefixed row ids the caller
    needs and strips before returning."""
    return {
        "access_token": material["access"],
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_S,
        "refresh_token": material["refresh"],
        "scope": scope,
        "_refresh_id": material["refresh_id"],
        "_access_id": material["access_id"],
    }


def _redeem_code_rpc(cp, *, observed: dict, code: str, scope: str,
                     burn: bool = False) -> dict:
    """Claim the authorization code and mint its pair, in ONE transaction (#3025).

    `redeem_oauth_code` performs the single-use CAS — the same predicate
    `_consume_code` used (PR #1264 review P2) — AND the two token INSERTs inside
    one Postgres function, so a failure anywhere leaves ZERO new rows and an
    unset `used_at`. #2863's in-process rollback/observe protocol compensated for
    exactly the window the transaction removes, which is why it is gone.

    `burn` selects the REFUSAL path: the caller has validated the code row and
    refused it, so the function claims the code and records the terminal
    `claimed → burned` outcome (#3027) in the same transaction, inserting no token
    row. That preserves the pre-#3025 behaviour — a validation failure after the
    claim burned the code — without the claim and the burn being two independent
    writes.

    Returns the RPC's status dict augmented with `_status`. A `not_claimable`
    status wrote NOTHING; the caller re-reads the row READ-ONLY to classify why.

    ⛔ Reached through :meth:`rpc_value`, NOT :meth:`rpc`. `rpc` sends
    `Prefer: return=minimal`, and PostgREST does not echo a VOLATILE SECURITY
    DEFINER function's body under it — the repo's own measured precedent
    (`supabase_control.py::resolve_signup_token`: "in production the echo is
    ALWAYS empty … the token path 422'd in prod while passing against the
    fake's bare-string rpc"). A `None` here therefore means NOTHING about the
    transaction, and must never be read as `not_claimable`.
    """
    material = _new_pair_material()
    redemption_id = secrets.token_urlsafe(16)
    res = cp.rpc_value("redeem_oauth_code", {
        "p_code_id": observed["id"],
        "p_code_hash": _sha256(code),
        "p_client_id": observed["client_id"],
        "p_user_id": observed["user_id"],
        "p_org_id": observed["org_id"],
        "p_scope": scope,
        "p_redemption_id": redemption_id,
        "p_refresh_id": material["refresh_id"],
        "p_refresh_hash": _sha256(material["refresh"]),
        "p_access_id": material["access_id"],
        "p_access_hash": _sha256(material["access"]),
        "p_now": _now_iso(),
        "p_refresh_expires_at": _expires_iso(REFRESH_TOKEN_TTL_S),
        "p_access_expires_at": _expires_iso(ACCESS_TOKEN_TTL_S),
        "p_burn": burn,
    })
    # A non-dict envelope (None, a bare scalar, a truncated body) is classified
    # as outcome-UNKNOWN below — never as `not_claimable`, which would positively
    # assert that the function wrote nothing.
    status = res.get("status") if isinstance(res, dict) else None
    if status == "minted":
        return {**_token_response(material, scope),
                "_status": "minted", "_redemption_id": redemption_id}
    if status in ("burned", "not_claimable"):
        return {"_status": status, "_redemption_id": redemption_id}
    # An unreadable envelope, or a status this build does not know, is NOT
    # evidence that nothing was written — the transaction may have COMMITTED
    # (a lost response, a contract drift). Raise, so the caller's READ-ONLY
    # probe decides retryable vs terminal. `_issue_tokens`'s rollback protocol
    # existed to compensate this window; the transaction removes the window,
    # so what replaces it is this: never assert success OR failure from an
    # absent observation.
    raise RuntimeError(
        "redeem_oauth_code returned an unreadable envelope "
        f"({res!r}) — the transaction may have committed")


def _rotate_refresh_rpc(cp, *, prev: dict) -> dict | None:
    """Rotate the presented refresh token, in ONE transaction (#3025).

    `rotate_oauth_refresh` claims the presented row (`WHERE revoked_at IS NULL`),
    inserts the new pair, and revokes the previous family's live access rows — all
    inside one Postgres function. A zero-row claim means a concurrent rotator won;
    it wrote NOTHING, so the loser no longer leaves an orphan pair to unwind,
    which is what `_issue_tokens`'s lane-1 rollback compensated for.

    The new rows inherit client_id / user_id / org_id / scope / code_id from the
    claimed row, so the family's identity and its #3027 provenance link cannot
    drift between a rotation and its parent. Returns None when the claim lost.

    ⛔ Reached through :meth:`rpc_value` for the same transport reason as
    :func:`_redeem_code_rpc`: under `Prefer: return=minimal` the body is not
    echoed, so a `None` must be treated as outcome-UNKNOWN (raise) rather than
    as "the claim lost" — the loser's `None` and a lost response are different
    facts, and only the first is safe to answer terminally.
    """
    material = _new_pair_material()
    res = cp.rpc_value("rotate_oauth_refresh", {
        "p_prev_refresh_id": prev["id"],
        "p_refresh_id": material["refresh_id"],
        "p_refresh_hash": _sha256(material["refresh"]),
        "p_access_id": material["access_id"],
        "p_access_hash": _sha256(material["access"]),
        "p_now": _now_iso(),
        "p_refresh_expires_at": _expires_iso(REFRESH_TOKEN_TTL_S),
        "p_access_expires_at": _expires_iso(ACCESS_TOKEN_TTL_S),
    })
    if not isinstance(res, dict) or res.get("status") != "rotated":
        if isinstance(res, dict) and res.get("status") == "not_claimable":
            return None          # the CAS matched zero rows: it wrote NOTHING
        raise RuntimeError(
            "rotate_oauth_refresh returned an unreadable envelope "
            f"({res!r}) — the rotation may have committed")
    scope = res.get("scope") or prev.get("scope") or " ".join(SCOPES_SUPPORTED)
    return _token_response(material, scope)


def exchange_auth_code(cp, body: dict, base: str) -> dict:
    """POST /oauth/token grant_type=authorization_code (P2 + P4).

    Validates the PKCE verifier, redirect_uri, client auth, and the RFC 8707
    resource (must map to the SAME org the code was bound to), then issues
    the access+refresh pair.

    #3025: the multi-table write is ONE transaction. The code row is read and
    validated FIRST — its identity fields (client_id, redirect_uri,
    code_challenge, scope) are written once by `issue_auth_code` and never
    updated, so this read-then-act carries no TOCTOU — and then a single
    `redeem_oauth_code` RPC claims the code AND inserts both token rows. A failure
    anywhere in that function leaves ZERO new rows and an unset `used_at`.

    #2863's in-process rollback/re-arm protocol is deleted with the window it
    compensated for. What remains is READ-ONLY classification plus #3027's
    DELIVERY GATE, which is the one write that must still follow the commit
    (a committed mint whose response is lost has to remain distinguishable from a
    replay — that is what the durable state is for, and a transaction cannot fix
    it).
    """
    code = body.get("code", "")
    attempted = False
    out: dict | None = None
    try:
        client = _verify_client_auth(cp, body.get("client_id"), body)   # pure read
        observed = _observe_code(cp, code)
        if observed.get("state") != REDEMPTION_UNCLAIMED:
            _refuse_observed_code(cp, observed)

        # ── validation of the code row's IMMUTABLE bound fields ──────────────
        # Collected rather than raised one at a time so that a REFUSAL is
        # recorded durably in the same transaction as the claim (the
        # `redemption_note='terminal'` path) rather than being one of several
        # independent raises.
        #
        # ORDER, and why it is not the pre-#3025 order: `validate_scope` used to
        # be the LAST check, inside the now-deleted `_issue_tokens` — i.e. after
        # client / redirect_uri / PKCE / resource / org. It is now FIRST, because
        # the claim must be gated before any mint can be attempted and the scope
        # is what the minted row would carry. That IS an observable precedence
        # change on a multiply-invalid request (an unsupported stored scope now
        # answers 400 `invalid_scope` where it used to answer the org/PKCE error
        # that happened to be checked first), and it is accepted: both are
        # terminal 400/403 invalid-credential answers to a request that cannot
        # succeed, and the scope answer is the more actionable one. Pinned by
        # `tests/test_oauth_mcp.py` TestScopeAllowList.
        refusal: tuple[str, OAuthError] | None = None
        try:
            scope = validate_scope(observed.get("scope") or " ".join(SCOPES_SUPPORTED))
        except OAuthError as exc:                      # #3128 unsupported scope
            refusal = ("unsupported-scope", exc)
            scope = ""
        if refusal is None and observed["client_id"] != client["id"]:
            refusal = ("client-mismatch", OAuthError(
                400, "invalid_grant",
                "Authorization code was issued to a different client."))
        if refusal is None and body.get("redirect_uri") != observed["redirect_uri"]:
            refusal = ("redirect-uri-mismatch",
                       OAuthError(400, "invalid_grant", "redirect_uri mismatch."))
        if refusal is None and not _verify_pkce(
                body.get("code_verifier", ""),
                observed["code_challenge"],
                observed.get("code_challenge_method") or "S256"):
            refusal = ("pkce", OAuthError(400, "invalid_grant",
                                           "PKCE verification failed."))
        # RFC 8707: the resource at the token endpoint must resolve to the same
        # org the authorization code was bound to (lenient when omitted — the
        # mcp SDK always sends it, but a bare authorize→token pair is legal).
        # `parse_resource`'s own 400 is COLLECTED like the rest: it is raised
        # after the claim, so leaving it outside would make the burn depend on
        # which helper raised — the pre-#3025 code burned every terminal signal
        # after the claim, and the `p_burn` path must not silently carve one out.
        if refusal is None:
            resource = body.get("resource")
            if resource:
                try:
                    _, requested_org = parse_resource(base, resource)
                except OAuthError as exc:              # 400 invalid_resource
                    refusal = ("invalid-resource", exc)
                else:
                    if (requested_org is not None
                            and requested_org != observed["org_id"]):
                        refusal = ("resource-mismatch", OAuthError(
                            400, "invalid_grant",
                            "Resource indicator does not match the authorized team."))
        if refusal is None:
            try:
                _assert_org_usable(cp, observed["org_id"])
            except OAuthError as exc:                  # 403 team missing/suspended
                refusal = ("org-unusable", exc)

        if refusal is not None:
            # #3027: the terminal `claimed → burned` outcome is recorded in the
            # SAME transaction as the claim, so the burn cannot outlive a claim
            # that never committed (nor the reverse). Best-effort by CONTRACT, as
            # the pre-#3025 `_settle_redemption` burn was: a control-plane failure
            # while RECORDING a refusal must not turn a coherent OAuth error into
            # a 500 — the code simply stays unclaimed and the client may retry.
            #
            # A RETRYABLE signal must NEVER burn (the pre-#3025 rule was
            # `if consumed and not isinstance(exc, OAuthTemporarilyUnavailable)`).
            # Nothing reachable inside the collection above is retryable today —
            # `_assert_org_usable` raises only 403 and the rest are 400s — so this
            # guard is structural rather than live: it keeps the rule true by
            # construction instead of by inspecting every callee.
            logger.info("oauth: code refused (%s)", refusal[0])
            if not isinstance(refusal[1], OAuthTemporarilyUnavailable):
                try:
                    _redeem_code_rpc(cp, observed=observed, code=code, scope="",
                                     burn=True)
                except Exception as exc:
                    logger.warning("oauth: refused-code burn failed: %s", exc)
            raise refusal[1]

        attempted = True
        for attempt in (1, 2):
            outcome = _redeem_code_rpc(cp, observed=observed, code=code, scope=scope)
            if outcome.get("_status") == "minted":
                out = outcome
                break
            fresh = _observe_code(cp, code)
            if fresh.get("state") == REDEMPTION_UNCLAIMED and attempt == 1:
                observed = fresh                # lost the claim race — retry once
                continue
            _refuse_observed_code(cp, fresh)
        if out is None:            # unreachable: `_refuse_observed_code` always raises
            _refuse_observed_code(cp, _observe_code(cp, code))

        # #3027: record delivery BEFORE returning the pair — and DELIVERY IS
        # GATED ON THIS CAS. The reconciler takes ownership of a stale claim with
        # the same CAS before it revokes anything, so exactly one of us can
        # settle: if we lose, a family we just minted must not be delivered
        # (it would be revoked out from under the client) and we revoke it and
        # report a terminal failure instead.
        settle_row = {"id": observed["id"],
                      "redemption_state": REDEMPTION_CLAIMED,
                      "redemption_id": out["_redemption_id"]}
        if not _settle_redemption(cp, settle_row, REDEMPTION_MINTED):
            minted = [("oauth_refresh_tokens", out["_refresh_id"]),
                      ("oauth_access_tokens", out["_access_id"])]
            # capture=True is this path's SINGLE capture (I4): the mint committed,
            # so nothing has captured yet, and a failed compensation leaves a
            # live, never-delivered row that must not vanish silently. The outcome
            # is TERMINAL either way — the reconciler owns the claim, so no retry
            # of this request can be safe.
            _rollback_minted(cp, minted, _now_iso(), capture=True)
            raise OAuthError(400, "invalid_grant",
                             "The authorization code could not be redeemed — re-run "
                             "authorization.")
    except OAuthError:
        raise
    except Exception as exc:
        _log_and_capture(exc, where="exchange_auth_code")
        if not attempted:
            raise OAuthTemporarilyUnavailable() from None        # constructive-clean
        # The RPC may have COMMITTED before its response was lost. A retryable
        # signal is therefore only ever emitted on an OBSERVED-clean code: an
        # unobserved write stays TERMINAL (its residue is `claimed`, which the
        # reconciler resolves against the durable `code_id` link).
        if _consume_state(cp, code) == "unconsumed":
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
    a lapsed membership revokes the presented token. A stored scope no longer
    in SCOPES_ACCEPTED (#3128) revokes the presented token and terminates the
    grant with `invalid_grant` — no mint.
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
    except OAuthError:
        raise
    except Exception as exc:
        _log_and_capture(exc, where="refresh_grant pre-mint")
        raise OAuthTemporarilyUnavailable(
            "Temporary control-plane failure before token rotation — retry.") from None
    # #3128: a refresh row minted before the scope gate (or made stale by a
    # future narrowing of SCOPES_ACCEPTED) carries a scope the AS no longer
    # accepts. The mint refuses it — but left as-is the presented token would
    # be refused forever with no recovery signal. Revoke the poisoned
    # credential and report terminal invalid_grant so the client re-authorizes
    # (mirrors the lapsed-membership branch above).
    try:
        validate_scope(row.get("scope") or " ".join(SCOPES_SUPPORTED))
    except OAuthError:
        try:
            cp.query("oauth_refresh_tokens", method="PATCH",
                     filters=[("id", "eq", row["id"])],
                     json_body={"revoked_at": _now_iso()})
        except Exception as exc:  # correction #8: the single capture for this path
            _log_and_capture(exc, where="poisoned-scope revoke")
        raise OAuthError(400, "invalid_grant",
                         "The refresh token's scope is no longer supported — "
                         "re-run authorization.") from None
    try:
        out = _rotate_refresh_rpc(cp, prev=row)
    except Exception as exc:
        # #3025: the rotation is ONE transaction, so an error either rolled the
        # whole call back (every write undone — the presented token is still
        # unrevoked, so a retry simply succeeds) or committed before its response
        # was lost (the presented token IS revoked and a live pair exists that the
        # client never saw — terminal; only a re-authorization recovers). The probe
        # is READ-ONLY and gates the retryable signal, so a retryable 503 is never
        # emitted on an unobserved write.
        _log_and_capture(exc, where="refresh_grant mint")
        if _prev_refresh_unclaimed(cp, row):
            raise OAuthTemporarilyUnavailable() from None
        raise OAuthError(400, "invalid_grant",
                         "The refresh token could not be rotated — re-run "
                         "authorization.") from None
    if out is None:
        # A concurrent rotator won the CAS. Nothing was written, so there is no
        # orphan pair to unwind — the `not_claimable` result IS the whole signal.
        raise OAuthError(400, "invalid_grant",
                         "Refresh token already revoked (rotated or invalidated).")
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
    # #10046: the RECORD tenant for an OAuth credential. This resolver is the
    # OAuth half of the MCP auth boundary and — like a SESSION/JWT credential —
    # it never goes through `resolve_api_key`, so nothing put the org's
    # default-graph row id on the dict and `record_tenant_for` (the one
    # declaration both transports call) refused every armed write `no_tenant`
    # for the whole `oat_` class. The row is resolved through the SAME home the
    # API-key lane uses (`default_graph_id_for_credential`), so the three
    # credential classes cannot name different defaults; a `None` here stays a
    # NAMED refusal counted `no_tenant`, never a guess, and the lookup/mint is
    # contract-bound never to fail this caller.
    from tortoise.supabase_control import default_graph_id_for_credential

    org["default_graph_id"] = default_graph_id_for_credential(
        cp, row["org_id"], context="oauth auth self-heal")
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
    --red: #ef4444; --border: #1e293b;
    --mono: ui-monospace,'SFMono-Regular','SF Mono',Menlo,'Cascadia Code','Fira Code','JetBrains Mono',monospace;
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
  button, a.action { font-family: var(--mono); font-size: 14px; font-weight: 600;
           padding: .6rem 1rem; border-radius: 6px; border: 1px solid var(--border);
           cursor: pointer; flex: 1; text-align: center; text-decoration: none;
           background: transparent; color: var(--text); }
  .btn-auth { background: var(--accent); color: #04121a; }
  .btn-auth:hover { background: var(--accent-hover); }
  .btn-auth:disabled { opacity: .45; cursor: not-allowed; }
  .btn-deny { background: transparent; color: var(--text-dim); }
  .error { display: none; color: var(--red); margin-top: 1rem; font-size: 13px; }
  .error.visible { display: block; }
  #btn-retry, #btn-signin { display: none; width: 100%; margin-top: .75rem; }
  #btn-retry.on, #btn-signin.on { display: block; }
  #btn-signin { border-color: var(--accent); color: var(--accent); }
  select { display: none; background: var(--bg); color: var(--text);
           border: 1px solid var(--border); border-radius: 6px;
           font-family: var(--mono); font-size: 13px; padding: 4px 6px;
           max-width: 60%; text-align: left; }
  select.on { display: block; }
  .spinner { color: var(--text-dim); font-size: 13px; margin-top: 1rem; }
</style>
</head>
<body>
<div class="card" id="card">
  <div class="logo">Tortoise<span>.</span></div>
  <h1>Connect an MCP client</h1>
  <p class="muted" id="client-line"></p>
  <div class="row"><span class="k">Requested scopes</span><span class="v" id="scope-line"></span></div>
  <div class="row"><span class="k">Resource</span><span class="v" id="resource-line"></span></div>
  <div class="row"><span class="k">Org</span>
    <span class="v" id="org-line">resolving…</span>
    <select id="org-select" aria-label="Org for this connection"></select>
  </div>
  <div class="actions">
    <button class="btn-deny" id="btn-deny">Deny</button>
    <button class="btn-auth" id="btn-auth" disabled>Authorize</button>
  </div>
  <div class="error" id="error" role="alert"></div>
  <!-- #5734: outside .actions and always in the DOM; a refusal's remedy must be
       a control the visitor can actually see, and the two remedies are
       DIFFERENT (sign in again vs. retry the same check). -->
  <button id="btn-retry">Try again</button>
  <a class="action" id="btn-signin" role="button">Sign in</a>
  <div class="spinner" id="spinner">Checking your session…</div>
</div>
<script nonce="__NONCE__">
  // ── wiring from the server (JSON-embedded; origin-safe) ──────────────────
  // PARAMS is the authorize request VERBATIM — `state` and `redirect_uri` are
  // opaque to the client and compared byte-for-byte by it, so this page never
  // trims, re-cases or re-encodes them. `redirect_target` is the destination
  // the SERVER rebuilt from the registered URI (#3443): that is what the page
  // navigates, never the raw request string.
  const PARAMS = __PARAMS__;
  // #3524 §9 step 4: this page holds NO credential. It cannot read the app's
  // `__Host-session` (host-only, HttpOnly), so it does not sign anyone in — it
  // asks the ONE origin that holds the session for a bound assertion
  // (`POST {APP}/api/consent/token`), and hands that assertion to
  // `POST /oauth/consent` in exchange for the code.
  const MINT_URL = __MINT_URL__;
  const CONSENT_URL = __CONSENT_URL__;
  // #1701 R1's account chooser — KEPT by §9 step 4, with its data source changed
  // along with the page's identity: the preview used to take a session JWT and
  // this page has none, so it takes the BOUND CONSENT TOKEN in a HEADER (never a
  // query string — it is a bearer-equivalent assertion, and a query string
  // reaches access logs, proxy logs and `Referer`). The four authorize params
  // ride the query because they are what the token is bound to and the AS
  // compares them (§2.2) exactly as it does at redemption.
  const PREVIEW_URL = __PREVIEW_URL__;
  const CONSENT_TOKEN_HEADER = __CONSENT_TOKEN_HEADER__;
  // The app-owned sign-in leg, pre-built by the server: the same bounce an
  // unauthenticated authorize request takes, with this flow as its return
  // target. It is a LINK, never an automatic redirect — the page must not bounce
  // itself (see `mint` below), or a session that cannot mint would loop
  // between two origins.
  const SIGNIN_URL = __SIGNIN_URL__;
  // The mint's per-attempt key (#10208): the app writes it onto the authorize URL
  // (`ATTEMPT_KEY_PARAM` in functions/_shared/auth/consent-bridge.ts) because
  // this page is the mint's ONLY caller, and the mint answers
  // `403 no_authorize_attempt` without it. Read from THIS PAGE'S OWN URL — the
  // server does not re-embed it, so there is exactly one carrier.
  const ATTEMPT_KEY_PARAM = __ATTEMPT_KEY_PARAM__;

  const byId = (id) => document.getElementById(id);
  const authBtn = () => byId("btn-auth");
  let consentToken = null;
  // #1701 R1: orgResource is set ONLY by the picker's change handler — an
  // untouched picker can never authorize (no silent wrong-org bind).
  let orgResource = null;
  // The other half of "may this click approve": a team was RESOLVED for this
  // assertion (sole-org auto-bind, or a pick on the chooser branch). Authorize is
  // armed from exactly `consentToken && orgReady` — ONE expression, evaluated by
  // ONE function — so no later handler can re-arm a button while either half is
  // missing. (Found in review: the picker's onchange re-armed the button after a
  // 401 had deliberately disabled it, because it only looked at the pick.)
  let orgReady = false;
  function refreshArm() {
    authBtn().disabled = !(consentToken && orgReady);
  }

  function showError(msg) {
    const el = byId("error");
    el.textContent = msg;
    el.classList.add("visible");
  }
  function hideError() {
    const el = byId("error");
    el.textContent = "";
    el.classList.remove("visible");
  }
  function spinner(on) { byId("spinner").style.display = on ? "block" : "none"; }
  function offerRetry(on) { byId("btn-retry").classList[on ? "add" : "remove"]("on"); }
  function offerSignin(on) {
    const el = byId("btn-signin");
    el.classList[on ? "add" : "remove"]("on");
    el.setAttribute("href", SIGNIN_URL);
  }

  // ── the attempt key, from this page's own URL ────────────────────────────
  function attemptKey() {
    return new URLSearchParams(window.location.search).get(ATTEMPT_KEY_PARAM) || "";
  }

  // ── #3443: navigate the SERVER-rebuilt target, with `state` echoed ────────
  // `dest` is the value returned alongside the code by POST /oauth/consent, or
  // the one the server embedded at authorize time (the deny path has no server
  // round trip). Both are rebuilt from the registered URI; neither is
  // `PARAMS.redirect_uri`. Falling back to the request string is the class this
  // work exists to remove, so an absent target is a refusal, not a navigation.
  function redirectBack(params, target) {
    const dest = target || PARAMS.redirect_target;
    if (!dest) {
      spinner(false);
      showError("Return address unavailable — start the connection again from your client.");
      return;
    }
    const sep = dest.indexOf("?") === -1 ? "?" : "&";
    window.location.href = dest + sep + new URLSearchParams(params).toString();
  }

  // ── the org preview: which team this grant binds (D4, #1701 R1) ──────────
  // Rendered from the AS's own resolution of the ASSERTED user — never from
  // anything the page could name. Fail-closed in every direction: Authorize is
  // enabled only by a resolved org (sole-org branch) or by a pick (chooser
  // branch), so an unresolved preview cannot approve.
  async function loadOrg(assertion) {
    const orgLine = byId("org-line");
    const orgSelect = byId("org-select");
    // #1701 R1: the picker is REBUILT here, so any previous pick is gone with
    // it. This is the only place the selection is cleared — a failed approval
    // must keep the pick its retry will re-post (see the consent handler).
    orgResource = null;
    orgReady = false;
    refreshArm();
    orgLine.style.display = "";
    orgSelect.classList.remove("on");
    while (orgSelect.firstChild) orgSelect.removeChild(orgSelect.firstChild);
    const q = new URLSearchParams({
      client_id: PARAMS.client_id || "",
      redirect_uri: PARAMS.redirect_uri || "",
      code_challenge: PARAMS.code_challenge || "",
      state: PARAMS.state || "",
      resource: PARAMS.resource || "",
    });
    let res;
    try {
      const headers = {};
      headers[CONSENT_TOKEN_HEADER] = assertion;
      res = await fetch(PREVIEW_URL + "?" + q.toString(), { headers: headers });
    } catch (e) {
      showError("Could not reach Tortoise to resolve your team — check your connection, then try again.");
      offerRetry(true);
      return;
    }
    const preview = await res.json().catch(() => null);
    if (!res.ok) {
      const why = (preview && (preview.error_description || preview.error))
          || ("HTTP " + res.status);
      showError(preview && preview.error === "invalid_token"
          ? "Your approval expired before it was issued. Try again to re-confirm your session."
          : why);
      offerRetry(true);
      return;
    }
    const memberships = preview && preview.memberships;
    if (memberships && memberships.length > 1) {
      // Account chooser — options are REBUILT from scratch every run so a
      // sequential re-run can never duplicate rows. Authorize stays disabled
      // until the user explicitly picks an org (change handler below).
      orgLine.style.display = "none";
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
      orgSelect.classList.add("on");
      byId("resource-line").textContent =
          "Tortoise MCP — choose the org this connection will use";
      return;
    }
    if (preview && preview.org_id) {
      // sole active org — auto-bind, exactly as before R1: the resource line
      // shows the client-declared value (or the pre-R1 default), never the
      // resolved org URL.
      byId("resource-line").textContent = PARAMS.resource || "default (sole org)";
      orgLine.textContent = (preview.org_name || preview.org_id) +
          " (" + preview.org_id + ")";
      orgReady = true;          // sole active team — auto-bound, nothing to pick
      refreshArm();
      return;
    }
    showError("No usable org for this account.");
  }

  byId("org-select").onchange = (ev) => {
    orgResource = ev.target.value;
    orgReady = !!orgResource;
    refreshArm();               // never more than the two halves allow
  };

  // ── step 1: ask the session origin for the bound assertion ────────────────
  // Fail-closed everywhere: Authorize starts disabled and is enabled ONLY by a
  // 200 carrying a token, every refusal is typed by status (401 = no session,
  // 403 = not this browser's attempt, anything else = transient), and no path
  // navigates on its own.
  async function mint() {
    hideError(); offerRetry(false); offerSignin(false); spinner(true);
    authBtn().disabled = true;
    consentToken = null;
    const key = attemptKey();
    if (!key) {
      spinner(false);
      showError("This page was opened without an approval attempt — start the connection again from your client.");
      return;
    }
    let res;
    try {
      res = await fetch(MINT_URL, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: PARAMS.state, attempt_key: key }),
      });
    } catch (e) {
      spinner(false);
      showError("Could not reach Tortoise to confirm your session — check your connection, then try again.");
      offerRetry(true);
      return;
    }
    const payload = await res.json().catch(() => null);
    spinner(false);
    if (res.ok && payload && typeof payload.token === "string" && payload.token) {
      consentToken = payload.token;
      // Authorize stays DISABLED until the org resolves: the resource the code
      // binds is decided there, so approving before it would send an unbound
      // (or wrongly bound) request.
      await loadOrg(consentToken);
      return;
    }
    if (res.status === 401) {
      showError("You are not signed in on this browser. Sign in, then approve the connection.");
      offerSignin(true);
      return;
    }
    if (res.status === 403) {
      showError("This approval link is not yours, or it has expired. Start the connection again from your client.");
      return;
    }
    const why = (payload && (payload.reason || payload.error)) || ("HTTP " + res.status);
    showError("Could not confirm your session (" + why + ").");
    // ⛔ A 4xx that is not 401/403 is the REQUEST's fault, not a blip: the mint
    // refuses a missing `state` with `400 invalid_request`, and `state` is not
    // validated at authorize time, so this page can legitimately be reached
    // without one. Offering "Try again" there would name a control that cannot
    // ever help — the same dead end the render-time 503 exists to avoid. Only a
    // 5xx or a transport failure is retryable.
    offerRetry(res.status >= 500);
  }

  // ── step 2: hand the assertion to the AS, and go where IT says ────────────
  authBtn().onclick = async () => {
    if (!consentToken) return;
    hideError(); offerRetry(false); offerSignin(false); spinner(true);
    authBtn().disabled = true;
    let res;
    try {
      res = await fetch(CONSENT_URL, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          consent_token: consentToken,
          client_id: PARAMS.client_id,
          redirect_uri: PARAMS.redirect_uri,
          response_type: PARAMS.response_type || "code",
          code_challenge: PARAMS.code_challenge,
          code_challenge_method: PARAMS.code_challenge_method || "S256",
          state: PARAMS.state,
          scope: PARAMS.scope,
          // the picker's selection wins over the client-declared indicator, and
          // an untouched chooser cannot get here (Authorize is disabled)
          resource: orgResource || PARAMS.resource || null,
        }),
      });
    } catch (e) {
      spinner(false);
      refreshArm();             // the token and the pick both survive a transport blip
      showError("Could not reach Tortoise to approve — check your connection, then try again.");
      offerRetry(true);
      return;
    }
    const payload = await res.json().catch(() => null);
    spinner(false);
    if (res.ok && payload && typeof payload.code === "string" && payload.code) {
      // `state` is echoed from the AS's OWN response (it is the BOUND value),
      // falling back to the request's byte-identical copy — never a normalised
      // or re-derived one.
      const q = { code: payload.code };
      if (typeof payload.state === "string") q.state = payload.state;
      else if (PARAMS.state) q.state = PARAMS.state;
      redirectBack(q, payload.redirect_target);
      return;
    }
    if (res.status === 401) {
      // The jti is spent before the code is inserted (a post-spend refusal costs
      // a RE-MINT, not a re-login), so the remedy is a fresh assertion. The token
      // is DROPPED — not merely the button disabled — because the chooser is still
      // on screen and its onchange re-derives the button state: keeping the token
      // would let one pick re-arm a button holding a jti the AS already recorded.
      consentToken = null;
      refreshArm();
      showError("Your approval expired before it was issued. Try again to re-confirm your session.");
      offerRetry(true);
      return;
    }
    // ⛔ The pick SURVIVES a failed approval. It used to be nulled here, which
    // left the chooser DISPLAYING the user's team while the next click posted
    // `PARAMS.resource` (or nothing) instead — a retry that is guaranteed to be
    // refused by `_default_org` for a multi-team account, with a message that
    // blames the client. The pick is reset where it is genuinely rebuilt: in
    // `loadOrg()`, on the re-mint this handler offers.
    refreshArm();
    const why = (payload && (payload.error_description || payload.error)) || ("HTTP " + res.status);
    showError("Approval failed (" + why + ").");
    if (res.status >= 500) offerRetry(true);
  };

  byId("btn-retry").onclick = () => { mint(); };

  byId("btn-deny").onclick = () =>
      redirectBack({ error: "access_denied", state: PARAMS.state });

  // ── render, then run ─────────────────────────────────────────────────────
  byId("client-line").textContent =
      (PARAMS.client_name || PARAMS.client_id || "This client") +
      " wants to access your Tortoise MCP surface.";
  byId("scope-line").textContent = PARAMS.scope || "mcp";
  byId("resource-line").textContent = PARAMS.resource || "(the MCP server, no team declared)";

  mint();
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


# ── the page's wiring to the app-owned bridge (#3524 §9 step 4) ────────────
# Every path/param name the PAGE needs, declared once on this side so the
# template carries no literal of its own. The three that mirror the TypeScript
# half are cross-checked by `tests/test_oauth_consent_page_step4.py`:
# `CONSENT_ATTEMPT_KEY_PARAM` against `ATTEMPT_KEY_PARAM`
# (functions/_shared/auth/consent-bridge.ts, the authority), and the two paths
# against the route files that serve them. A name that drifts here does not
# red a build — it makes every mint answer `403 no_authorize_attempt` AFTER the
# visitor has approved, which is the exact class step 4 exists to remove.
CONSENT_ATTEMPT_KEY_PARAM = "attempt_key"
CONSENT_MINT_PATH = "/api/consent/token"
CONSENT_START_PATH = "/api/consent/start"
CONSENT_SIGNIN_PATH = "/auth"
CONSENT_PREVIEW_PATH = "/oauth/consent/preview"
#: The header the page presents its consent token in when it asks for the org
#: preview. A HEADER, not a query parameter: the token is a bearer-equivalent
#: assertion, and a query string lands in access logs, proxy logs and the
#: `Referer` of anything the page loads next.
CONSENT_TOKEN_HEADER = "X-Consent-Token"


def consent_app_origin() -> str | None:
    """The session origin — where the mint lives, and where a cold browser is
    sent to sign in. ``None`` when the binding is not an absolute http(s)
    origin.

    ⛔ ONE KNOB, ONE FALLBACK: this is `consent_token_issuer()`, not a second
    setting. The mint stamps that value as the token's ``iss`` and the AS
    verifies it byte-exactly, so the page MUST call the mint at the same origin
    the issuer names — a separate ``APP_ORIGIN``-style knob could point the page
    at one deployment while the verifier expects another, with every redemption
    answering `401 invalid_token` and no other symptom (the rule
    `token.ts::consentTokenIssuer` records on the minting side).

    ``None`` is not "defensive padding": `new URL()`-equivalent parsing is what
    separates "this deployment cannot mint" (a typed 503 the caller can report)
    from a redirect to an unparseable string, which reaches the browser as a
    navigation error with nothing naming the binding.
    """
    from urllib.parse import urlsplit

    from tortoise.consent_token import consent_token_issuer
    parts = urlsplit(consent_token_issuer())
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return None


def consent_bounce_url(app_origin: str, raw_query: str) -> str:
    """The app-owned sign-in leg for an authorize request this server will not
    render: ``{app}/auth?next=/api/consent/start?{raw}``.

    WHY THIS EXACT SHAPE
    --------------------
    * The AS holds NO credential it could read (the app's ``__Host-session`` is
      host-only AND HttpOnly), so it cannot tell a signed-in visitor from a
      cold one. Rather than guess, it hands the COMPLETE authorize request to
      the one origin that can answer — and the visitor with a live session is
      forwarded straight back by the sign-in page's own session probe, with no
      interaction.
    * ``/auth`` is `signup.html`, whose return-to is ALLOW-LIST ONLY
      (``APP_RETURN_ROUTES``): a ``next`` naming anything else is silently
      dropped and the visitor lands on the app root, losing the flow. The inner
      target is therefore `/api/consent/start`, the ONE API path that list
      admits — step 3 added it for exactly this return leg, and
      `tests/test_oauth_consent_page_step4.py` runs the real allowlist over the
      URL this function emits rather than asserting a string.
    * ``raw_query`` is the request's RAW query string, re-encoded exactly once
      here. Re-serialising parsed params would re-encode opaque values: `state`
      and `redirect_uri` are compared byte-for-byte by the client, and a
      `+`/`%2B`/`%20` normalisation at this hop is the silent
      CSRF-mismatch-after-consent class the bridge refuses everywhere else.
    """
    inner = CONSENT_START_PATH + (("?" + raw_query) if raw_query else "")
    return (app_origin.rstrip("/") + CONSENT_SIGNIN_PATH + "?"
            + urlencode({"next": inner}))


def consent_page_html(*, client_name: str, params: dict, raw_query: str,
                      app_origin: str) -> tuple[str, str]:
    """Render the branded consent page (D2, rewritten by #3524 §9 step 4).

    The page holds NO credential and signs NOBODY in. ``params`` are the raw
    authorize query params — JSON-encoded into the page so the JS presents them
    verbatim — plus ``client_name`` and the server-built ``redirect_target``
    (#3443), which is the ONLY value the page navigates (see `redirectBack`);
    the raw ``redirect_uri`` stays in the page only to be posted back to
    /oauth/consent for validation. ``raw_query`` builds the page's sign-in link
    (``consent_bounce_url``) and ``app_origin`` the mint it calls.

    Returns ``(html, csp_nonce)``. The nonce gates the single inline <script>,
    so the caller can serve a script-src CSP that still permits the consent
    logic. Attacker-controlled values (state / scope / resource / client_name)
    are JSON-escaped for the <script> context, so a ``</script>`` payload cannot
    break out of the block (PR #1264 review P1). Placeholder substitution is a
    SINGLE pass, so a param value that *contains* a placeholder token is
    inserted verbatim rather than re-expanded (#6001 — see the comment at the
    substitution site for the failure it prevents).
    """
    safe_params = {
        k: (v if isinstance(v, str) else "")
        for k, v in params.items()
    }
    safe_params["client_name"] = client_name
    nonce = secrets.token_urlsafe(16)
    # Substitution is a SINGLE pass (#6001). A chain of ``str.replace()`` calls
    # rescans what it has already inserted: injecting ``_json_for_script(params)``
    # while later passes still ran meant a param VALUE carrying a literal
    # placeholder token was itself substituted — inside the JSON string literal
    # meant to hold it. The injected text is raw JSON, so its own quotes closed
    # that literal early and the whole inline block became a SyntaxError (no
    # PARAMS, no storage adapter, no handlers, an inert Authorize button). Both
    # vectors are reachable without a crafted link: ``client_name`` is DCR/CIMD
    # client-controlled and ``state`` is never shape-validated. ``re.sub`` with a
    # FUNCTION replacement visits the template exactly once and never rescans
    # what it produced; the function form also keeps re's backslash processing
    # off the replacement text (an escaped value's literal ``\\u003c`` would
    # otherwise be read as an escape sequence). The pattern is derived from the
    # mapping's own keys, so a placeholder added to ``_CONSENT_HTML`` has exactly
    # one place to be declared.
    mapping = {
        "__PARAMS__": _json_for_script(safe_params),
        "__MINT_URL__": _json_for_script(app_origin.rstrip("/") + CONSENT_MINT_PATH),
        "__CONSENT_URL__": _json_for_script("/oauth/consent"),
        "__PREVIEW_URL__": _json_for_script(CONSENT_PREVIEW_PATH),
        "__CONSENT_TOKEN_HEADER__": _json_for_script(CONSENT_TOKEN_HEADER),
        "__SIGNIN_URL__": _json_for_script(consent_bounce_url(app_origin, raw_query)),
        "__ATTEMPT_KEY_PARAM__": _json_for_script(CONSENT_ATTEMPT_KEY_PARAM),
        "__NONCE__": nonce,
    }
    html = re.sub(
        "|".join(re.escape(tok) for tok in mapping),
        lambda m: mapping[m.group(0)],
        _CONSENT_HTML,
    )
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
