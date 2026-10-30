"""Signed, bound, single-use consent tokens — the AS half of the consent identity bridge.

WHY THIS MODULE EXISTS (#3524, `docs/engineering/auth/consent-identity-bridge.md` §9 step 2)
----------------------------------------------------------------------------------------------
The consent page lives on `api.*` and, under the target topology, holds no credential it can
present as a bearer: the dashboard's session is `__Host-session` on `app.*` — host-only, so it is
never sent to `api.*`, and HttpOnly, so no script anywhere can read it (§1). Identity must
therefore arrive as an **assertion about the visitor, minted on the session origin**. §2.1
specifies that assertion; this module is the verifying half of it. Minting is §9 step 3 (the BFF,
TypeScript) and is NOT here.

The contract, and the failure each field prevents (§2.2 — quoted so this file can be checked
against the spec rather than against its own prose):

| Field                | Rule enforced here                                              |
|----------------------|-----------------------------------------------------------------|
| `aud`                | must be exactly ``tortoise-consent`` — never a Supabase audience |
| `iss`                | must equal the configured session origin                        |
| `exp` / `iat`        | ``exp = iat + CONSENT_TOKEN_TTL_S``, verified with leeway        |
| `jti`                | recorded on first redemption; a second is refused (§2.3)         |
| `bind.client_id`     | must equal the `client_id` presented at redemption (§2.2)        |
| `bind.redirect_uri`  | must match, under the AS's own `_redirect_uri_matches` (§2.2)     |
| `bind.code_challenge`| must equal the `code_challenge` presented at redemption (§2.2)    |
| `bind.state`         | must equal the `state` presented at redemption (§2.2)             |
| `sub`                | the identity — the ONLY thing the token may establish (§2.2)      |
| `email`              | display only; **never** an authorization input (§2.1)             |

Why the binding is load-bearing and the TTL is not a substitute — §2.4, verbatim: *"Single-use +
short TTL bounds replay, not fixation. An attacker's own live link still fixes the victim. Only the
binding addresses that."* So the bind comparison is the security-critical half of this module, and
the `jti` register is what stops ONE TOKEN from yielding N codes.

⚠️ That is a narrower bound than "one consent yields one code", and the difference is deliberate: the
BFF mint does not consume its attempt, so a page that mints again after a spent/refused redemption (§2.3's
re-mint recovery, `hosted_api.py`) gets a fresh `jti` and hence a second code for the SAME approval. `jti`
bounds the token; nothing in steps 1+3 bounds the approval. Closing that needs a single-use mark on the
BFF's `consent_attempts` row — a step-2/step-3 contract change — and the re-mint is pinned by
`tests/test_oauth_consent_bff_routes.py::test_the_attempt_can_be_MINTED_AGAIN_for_step_4s_recovery`, so it
cannot be narrowed silently either.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
* **No mint route, and no route may call `mint_consent_token`.** The minting authority is the
  session origin (§2.3). The function here is the reference INVERSE of the verifier — it exists so
  the AS has a checkable counterpart to the contract and so tests can mint exactly what §2.1
  specifies. Shipping it as a route would put identity assertion on the wrong origin.
* **No page change.** Reading a token on the page is §9 step 4; a page that half-reads a token and
  half-reads the old cookie is the "strictly worse than not starting" state §9 records for #3559.

The `jti` register IS shared (§2.3, DELIVERED)
---------------------------------------------
* `ControlPlaneJtiStore` redeems the nonce against the control plane the AS already holds at the
  call site, and `hosted_api._lifespan` installs it at startup, so single-use holds across Fly
  machines rather than per process. The row store is `oauth_consent_tickets`
  (`supabase/migrations/20261030000001_oauth_consent_tickets.sql`); the RPC decides the winner
  with one `INSERT … ON CONFLICT DO NOTHING RETURNING`, because a read-then-write is a TOCTOU two
  machines can both win.
* `ProcessLocalJtiStore` is therefore the seam's default only when nothing is installed (tests, a
  bare embed). It is NOT the deployed posture: reading it as the production guarantee understates
  replay safety and invites a duplicate re-implementation of the shared store.
* The store stamps `exp + CONSENT_TOKEN_LEEWAY_S`, not `exp`: the verifier accepts until the
  leeway runs out, so the prune must not forget the nonce while its token still verifies —
  otherwise the replay deletes its own fence and then re-inserts, for the whole window.

REFUSALS (all typed, all fail-closed)
-------------------------------------
``OAuthError`` so the endpoint renders one RFC 6749 §5.2 body:
  * ``401 invalid_token`` — the assertion was not accepted (structure, `alg`, signature, `aud`,
    `iss`, `exp`/`iat`, `sub`, `jti`, `bind` shape, `bind` mismatch, or a replayed `jti`).
  * ``503 temporarily_unavailable`` — the arm cannot be used at all: `CONSENT_TOKEN_SECRET` is
    unset, or the `jti` register is at capacity. Never a 500, and never a silent fall-through to
    the bearer path (which would answer "missing session token" to a request that never had one).

OPERATOR REQUIREMENT (§8) — the token arm is LIVE the moment these are set on the hosted API:
  * ``CONSENT_TOKEN_SECRET`` — a dedicated secret, HMAC-SHA256 signing key, **at least 32 UTF-8
    bytes** (≈32 characters for an ASCII key; a shorter value is treated as unprovisioned: a
    guessable key is the same forgery
    oracle as a reused one — the floor is a length heuristic, not a strength guarantee, so pick a
    generated value). Must NOT be the service-role key (key reuse across purposes) or the anon key
    (§2.3: the anon key is public by construct — it ships in every browser that loads the app,
    independently of whether any one page embeds it). Unset ⇒ the arm is unavailable and the
    bearer path is unaffected.
  * ``CONSENT_TOKEN_ISSUER`` — optional, defaults to ``https://app.premiselabs.co`` (the session
    origin). A token whose `iss` differs is refused, so this must match the minting origin.
  * the shared `jti` store — installed from `hosted_api._lifespan` (above). If nothing is
    installed the seam falls back to `ProcessLocalJtiStore`, i.e. single-use PER PROCESS.
  * ``config/required-bindings.yml`` declares `CONSENT_TOKEN_SECRET` (and, as `recommended`,
    ``CONSENT_AS_ORIGIN`` / ``CONSENT_TOKEN_ISSUER``, since both have correct defaults) so the
    deploy preflight fails closed instead of shipping an unprovisioned mint.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import inspect
import json
import logging
import math
import os
import re
import secrets as _secrets
import threading
import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from tortoise.oauth import OAuthError, _redirect_uri_matches

logger = logging.getLogger(__name__)

#: §2.1 — ``aud``. Deliberately NOT ``authenticated``: §2.2 requires that a Supabase session token
#: (or any other JWT sharing a key) can never be read as a consent assertion.
CONSENT_TOKEN_AUDIENCE = "tortoise-consent"
#: §2.2 — ``exp = iat + CONSENT_TOKEN_TTL_S``. The TTL bounds EXPOSURE; it is not a substitute for
#: the single-use ``jti`` (which bounds REPLAY) and neither substitutes for ``bind``.
CONSENT_TOKEN_TTL_S = 120
#: §2.2 — "verified with ≤30 s leeway". Applied to both `exp` and `iat`.
CONSENT_TOKEN_LEEWAY_S = 30
#: §2.1 — the session origin, i.e. the minting authority. Overridable so a preview deployment or a
#: self-hosted install verifies its OWN mint rather than refusing every token (§2.1 `iss`).
DEFAULT_CONSENT_TOKEN_ISSUER = "https://app.premiselabs.co"
#: §2.1 — the bind set, in the spec's order. ``issue_auth_code`` is called with these values (§9
#: step 2), and the set is EXACT: an unknown bind key is refused, because a verifier that ignored
#: them would accept a future contract it does not implement.
BIND_FIELDS = ("client_id", "redirect_uri", "code_challenge", "state")
#: §2.1 — the header this verifier emits/accepts. ``kid`` is checked for presence only: §2.3 makes
#: rotation ``kid``-scoped ("two `kid`s are accepted during a rotation window") and the accepted-kid
#: list belongs with the secret provisioning in §9 step 1. Pinning one literal here would make a
#: rotation impossible to perform without editing code, which is the opposite of what `kid` is for.
CONSENT_TOKEN_ALG = "HS256"
CONSENT_TOKEN_TYP = "JWT"

_SECRET_ENV = "CONSENT_TOKEN_SECRET"
_ISSUER_ENV = "CONSENT_TOKEN_ISSUER"

#: A consent token is ~1 KB. The cap is defence-in-depth against a hostile header/body value, and
#: it is checked before any parse so a huge token cannot cost a base64 decode.
_MAX_TOKEN_BYTES = 8192
_MAX_JTI_CHARS = 256
#: The in-process register's bound. Pruning runs first; a FULL register refuses (503) rather than
#: evicting a live `jti`, because evicting one re-enables the replay this store exists to stop.
#: It is filled by REDEMPTION, not by minting — every accepted assertion spends one entry, and that
#: needs the secret. So it is not anonymously reachable; a caller who can drive the mint route could
#: still spend the cap and answer every other caller 503 on this instance for up to TTL+leeway.
#: Bounded and fail-closed, and the pressure disappears with §2.3's shared store (step 1).
_MAX_STORE_ENTRIES = 4096

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


# ── configuration (§2.3 / §8) ───────────────────────────────────────────────

#: Keys that must never ALSO be the consent HMAC key — §2.3: "It must **not** be the service-role
#: key (key reuse across purposes) and must **not** be the anon key (it is public by construct, so
#: anyone can hold it)". BOTH halves are checked here: `SUPABASE_ANON_KEY` is in this process's
#: environment, so a collision is observable — an earlier version of this tuple claimed otherwise
#: and would have left the
#: WORSE half unguarded (that key is public by construction: everyone who opens `/oauth/authorize`
#: receives it). §8's human checklist still carries the cross-deployment case, where the Pages
#: project's env differs from the API's.
_REUSED_KEY_ENVS = ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY")

#: The shortest secret this arm will use. A FLOOR, not an entropy measurement: it catches the
#: copy-paste-a-word class (measured with `CONSENT_TOKEN_SECRET=a`: a token HMAC'd with `"a"` was
#: accepted and minted a code). HMAC-SHA256 with a guessable key is the same forgery oracle the
#: reuse check above exists to prevent, reached by a different operator mistake — and this function
#: already owns "an operator set this to the wrong value", so it owns this one too.
#: ⚠️ It is a LENGTH heuristic, NOT a strength bound, and the number is not a bit-count: 32
#: characters is 128 bits if the value is hex and ~190 if base64url, while the digest itself is 32
#: BYTES (43–64 characters encoded). So `"passwordpasswordpasswordpassword"` clears this floor and
#: is still ~2^19 guesses. It refuses words and short strings, which is the mistake worth catching;
#: it cannot tell you the key is strong — that is the operator's job (§8).
#: Minimum length in UTF-8 BYTES (a 256-bit key is the usual floor for an HMAC-SHA256 key). Measured
#: in bytes, not characters: 20 multi-byte characters is a >32-byte key and must not be refused.
_MIN_SECRET_BYTES = 32


def consent_token_secret_problem() -> str | None:
    """Why the arm is unavailable, or ``None`` when it can be used (§2.3).

    A REUSED key is treated as *not provisioned* rather than as a working arm. §2.3 makes a
    dedicated secret a ``must``, and the failure it guards is silent and total: a
    ``CONSENT_TOKEN_SECRET`` copied from the anon key turns this verifier into a forgery oracle —
    that key is public by construction (it ships to every browser that loads the app), so anyone
    holding it could assert any ``sub`` with any ``bind``, and ``sub`` is the only identity input
    on the token
    arm. A reused service-role key is the same class from the other direction: one leak of the
    wider credential silently becomes an assertion signing key. Refusing here costs one
    typed 503 and one restart with the right value; accepting it costs every account.
    """
    raw = os.environ.get(_SECRET_ENV, "")
    if not raw:
        return f"{_SECRET_ENV} is not set"
    # ⛔ BYTE-EXACT, never normalised. Step 3's minter is the BFF (TypeScript), and it reads
    # `process.env.CONSENT_TOKEN_SECRET` verbatim — it does not strip. A padded value is the
    # normal result of `fly secrets set CONSENT_TOKEN_SECRET < file`, of a pasted secret, or of a
    # value round-tripped through a file, and silently stripping it here would make the two sides
    # HMAC DIFFERENT keys: every redemption then answers `401 invalid_token: signature does not
    # verify`, i.e. the whole step-4 flow is dead and the diagnosis blames the token rather than
    # the key. Treating a padded value as unprovisioned costs one typed 503 at the first request
    # and names the fix; changing the operator's value silently costs the feature.
    if raw != raw.strip():
        return (f"{_SECRET_ENV} has leading or trailing whitespace — the minting side (the BFF, "
                "§9 step 3) reads the value verbatim, so a padded value is a DIFFERENT HMAC key "
                "and every redemption would answer `invalid_token`. Set it byte-exact")
    secret = raw
    for name in _REUSED_KEY_ENVS:
        other = os.environ.get(name, "")
        # Compared against BOTH spellings of the reused credential: an operator who padded the
        # service-role key must not slip past the dedicated-secret rule by that accident.
        if other and secret in (other, other.strip()):
            return (f"{_SECRET_ENV} is also {name} — §2.3 requires a DEDICATED secret, so the "
                    "arm is treated as unprovisioned rather than trusting a reused key")
    try:
        length = len(secret.encode("utf-8"))
    except UnicodeEncodeError:      # a lone surrogate in the env value: not a key we can sign with
        return f"{_SECRET_ENV} is not encodable as UTF-8, so it cannot be an HMAC key"
    if length < _MIN_SECRET_BYTES:
        return (f"{_SECRET_ENV} is {length} bytes — a key shorter than {_MIN_SECRET_BYTES} bytes "
                "is guessable, so the arm is treated as unprovisioned")
    return None


def consent_token_secret() -> str | None:
    """The HMAC key, or ``None`` when the arm is not provisioned (§2.3).

    Read per call, not cached at import: provisioning is an operator action on a running platform,
    and a value cached at import would make a rollback to unset invisible until the next deploy.

    Returned BYTE-EXACT as the operator set it (never stripped) — see the whitespace arm of
    `consent_token_secret_problem`, which is what makes that safe.
    """
    return None if consent_token_secret_problem() else os.environ[_SECRET_ENV]


def consent_token_issuer() -> str:
    """The configured session origin (§2.1 `iss`)."""
    configured = os.environ.get(_ISSUER_ENV, "").strip()
    return configured or DEFAULT_CONSENT_TOKEN_ISSUER


# ── refusals ───────────────────────────────────────────────────────────────

def _refuse(description: str) -> OAuthError:
    """401 ``invalid_token`` — the assertion was not accepted.

    ``invalid_token``/401 is the RFC 6750 §3.1 pairing for "the presented credential is expired,
    revoked, malformed, or invalid for other reasons", which is exactly this case. The
    description names WHICH check failed and carries no secret material: it is read by the page's
    logs and by an operator, and both need to tell a wrong `iss` from a spent `jti`.
    """
    return OAuthError(401, "invalid_token", f"Invalid consent token: {description}.")


def _unavailable(description: str) -> OAuthError:
    """503 ``temporarily_unavailable`` — the ARM cannot be used, not the token (§9 step 2)."""
    return OAuthError(503, "temporarily_unavailable", f"Consent token support unavailable: {description}.")


# ── base64url (§2.1) ───────────────────────────────────────────────────────

def _b64url_decode(segment: str, what: str) -> bytes:
    if not segment or not _B64URL_RE.match(segment):
        raise _refuse(f"{what} is not base64url")
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError) as exc:
        raise _refuse(f"{what} is not base64url") from exc


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _json_segment(segment: str, what: str) -> dict:
    decoded = _b64url_decode(segment, what)
    try:
        value = json.loads(decoded)
    except Exception as exc:            # any parse failure is the same refusal
        raise _refuse(f"{what} is not JSON") from exc
    if not isinstance(value, dict):
        # A non-dict JSON segment ([1,2], 123, "x") is a valid JSON body and an invalid JWS
        # segment; it must refuse here rather than raise AttributeError one line later.
        raise _refuse(f"{what} is not a JSON object")
    return value


# ── bind comparison (§2.2) ─────────────────────────────────────────────────

def _bind_mismatch(bound: dict, presented: dict) -> str | None:
    """The name of the first bind field that does not match, or ``None`` when they all do.

    ``redirect_uri`` is compared with ``_redirect_uri_matches`` — §2.2: "after the same
    `_redirect_uri_matches` normalisation the AS already applies" — so the RFC 8252 §7.3 loopback
    port relaxation is honoured here exactly as it is at `/oauth/authorize`. Every other field is
    exact string equality; `state` may be ``None`` on both sides, and ``None != "x"`` on purpose
    (an absent state is a value, not a wildcard).
    """
    if bound["client_id"] != presented.get("client_id"):
        return "client_id"
    if not _redirect_uri_matches(bound["redirect_uri"], presented.get("redirect_uri")):
        return "redirect_uri"
    if bound["code_challenge"] != presented.get("code_challenge"):
        return "code_challenge"
    if bound["state"] != presented.get("state"):
        return "state"
    return None


#: The handler's name for the SAME refusal shape `verify_consent_token` raises, so a refusal the
#: handler adds (a bind this client cannot be delivered to) is indistinguishable to the client from
#: one the verifier raises — one error contract, one place it is defined.
_consent_refusal = _refuse


# ── verification (§2.1, §2.2, §9 step 2) ───────────────────────────────────

def _number(claims: dict, name: str) -> float:
    value = claims.get(name)
    # `bool` is an `int` in Python; `True` as a timestamp is a type confusion, not a time.
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise _refuse(f"{name} is missing or not a number")
    # ⛔ NaN must be refused HERE, not left to the comparisons below: every one of them is False
    # for NaN, so `iat = exp = NaN` would pass the expiry check, pass the TTL check, and be stored
    # in the `jti` register with an expiry that never arrives (the entry is never pruned). That
    # defeats exactly the bound §2.2 requires. ±Infinity is already refused by the TTL check, but
    # finiteness is the property, so it is stated as the property.
    #
    # ⛔ THE CONVERSION IS GUARDED TOO: an integer too large to be a float (`10**400`, which
    # `json.loads` produces from a JSON integer literal with no float involved) makes BOTH
    # `math.isfinite` and `float()` raise `OverflowError` — which escaped as a 500 `server_error`
    # and put an exception in this module's "every refusal is a typed OAuthError" claim. Measured
    # end-to-end by the convergence review: a correctly signed token with `exp = 10**400` answered
    # 500. A caller who can sign one can also just mint a valid token, so this was never a
    # privilege path — it was a claim that was not true of its own code.
    try:
        if not math.isfinite(value):
            raise _refuse(f"{name} is not a finite number")
        return float(value)
    except (OverflowError, ValueError) as exc:
        raise _refuse(f"{name} is not a finite number") from exc


def _text(claims: dict, name: str, *, max_chars: int | None = None) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value.strip():
        raise _refuse(f"{name} is missing or empty")
    if max_chars is not None and len(value) > max_chars:
        raise _refuse(f"{name} is too long")
    return value


def _checked_bind(claims: dict) -> dict:
    """The `bind` object, shape-checked to EXACTLY §2.1's four params.

    An unknown key is refused rather than ignored: `bind` is the field that decides which flow the
    token authorises, so a token carrying a binding this verifier does not understand is a token
    whose authority is not understood.
    """
    bind = claims.get("bind")
    if not isinstance(bind, dict):
        raise _refuse("bind is missing or not an object")
    if set(bind) != set(BIND_FIELDS):
        unexpected = sorted(set(bind) - set(BIND_FIELDS))
        missing = sorted(set(BIND_FIELDS) - set(bind))
        raise _refuse(
            f"bind must be exactly {', '.join(BIND_FIELDS)} "
            f"(missing={missing or 'none'}, unexpected={unexpected or 'none'})")
    for field in ("client_id", "redirect_uri", "code_challenge"):
        if not isinstance(bind[field], str) or not bind[field].strip():
            raise _refuse(f"bind.{field} is missing or empty")
    if bind["state"] is not None and not isinstance(bind["state"], str):
        raise _refuse("bind.state must be a string or null")
    return {field: bind[field] for field in BIND_FIELDS}


def verify_consent_token(token: str, *, presented: dict) -> dict:
    """Verify a consent token and return ``{sub, email, bind, jti, exp}`` (§2.1/§2.2/§9 step 2).

    PURE: it performs no write, so the caller decides when the `jti` is spent. That ordering is
    load-bearing and is the caller's — see `consume_consent_jti`.

    ``presented`` carries the four authorize params from the redemption REQUEST. The token's
    `bind` must match every one of them (§2.2); the returned ``bind`` is the token's, which is what
    the caller mints with (§9 step 2).

    Order matters and is the contract's, not an implementation detail:
      1. size, then structure — before any decode;
      2. header `alg`/`typ`/`kid`, then the SIGNATURE — no claim is read before the signature
         verifies, so a forged payload cannot influence a single branch;
      3. claims `aud`, `iss`, `iat`/`exp`;
      4. `sub`, `jti`, `bind` shape;
      5. `bind` against the request.

    Raises ``OAuthError``: 401 ``invalid_token`` for every refused assertion, 503
    ``temporarily_unavailable`` when the arm itself is not provisioned (§2.3).
    """
    if not isinstance(token, str) or not token.strip():
        raise _refuse("it is missing or not a string")
    # `len(token)`, not `len(token.encode())`: a lone surrogate (sendable as a JSON escape, which
    # `json.loads` accepts) raises `UnicodeEncodeError` out of `.encode()`, which escaped as a 500
    # with a captured Sentry event per request — the exact "bare 500" class #3026 exists to close.
    if len(token) > _MAX_TOKEN_BYTES:
        raise _refuse("it is too large")

    problem = consent_token_secret_problem()
    if problem is not None:
        # §2.3: unprovisioned (or provisioned with a key that must not be reused). Fail CLOSED and
        # TYPED — never fall through to the bearer path, which would answer "missing session
        # token" for a request that has no business holding one, and would make a missing secret
        # look like a broken browser. The description names WHICH operator action is needed.
        raise _unavailable(problem)
    secret = consent_token_secret()

    parts = token.split(".")
    if len(parts) != 3:
        raise _refuse("it is not a compact-JWS token (header.payload.signature)")
    if not all(_B64URL_RE.match(part) for part in parts):
        # Charset FIRST, and for every segment — including `parts[1]`, which is folded into the
        # signature's input before its own decode: a non-ASCII character there would reach
        # `.encode()` and raise, so it must be refused as a shape error, not as an exception.
        raise _refuse("its segments are not base64url")

    header = _json_segment(parts[0], "header")
    algorithm = header.get("alg")
    if algorithm != CONSENT_TOKEN_ALG:
        # §2.1 pins HS256. This single comparison is what refuses the RFC 7519 §6 "unsecured"
        # token (`alg: none`) and any asymmetric/algorithm-confusion attempt: the key is symmetric,
        # so there is no second algorithm this verifier could legitimately accept.
        raise _refuse(f"alg must be {CONSENT_TOKEN_ALG} (got {algorithm!r})")
    if header.get("typ") != CONSENT_TOKEN_TYP:
        raise _refuse(f"typ must be {CONSENT_TOKEN_TYP}")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid.strip():
        raise _refuse("kid is missing")

    signing_input = f"{parts[0]}.{parts[1]}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url_decode(parts[2], "signature")):
        raise _refuse("signature does not verify")

    claims = _json_segment(parts[1], "payload")

    # §2.2 `aud` — string-exact; a list form is refused rather than searched, because searching
    # accepts any token that happens to carry our audience among others.
    audience = claims.get("aud")
    if not isinstance(audience, str) or audience != CONSENT_TOKEN_AUDIENCE:
        raise _refuse(f"aud must be {CONSENT_TOKEN_AUDIENCE!r}")

    # §2.2 `iss` — string-exact against the configured session origin.
    issuer = claims.get("iss")
    if issuer != consent_token_issuer():
        raise _refuse("iss is not the configured session origin")

    now = time.time()
    issued_at = _number(claims, "iat")
    expires_at = _number(claims, "exp")
    if expires_at + CONSENT_TOKEN_LEEWAY_S < now:
        # §2.2 `exp` — the TTL is what bounds exposure for a leaked token.
        raise _refuse("it is expired")
    if issued_at > now + CONSENT_TOKEN_LEEWAY_S:
        raise _refuse("iat is in the future")
    if expires_at - issued_at > CONSENT_TOKEN_TTL_S:
        # §2.2: `exp = iat + CONSENT_TOKEN_TTL_S`. Enforced from the TOKEN, not from the mint,
        # because the mint is the other side of the trust boundary (§2.3) — a verifier that
        # trusted an arbitrary `exp` would let a mis-minted (or rogue-minted) token live for a
        # year while every other check still passed.
        raise _refuse(f"ttl exceeds {CONSENT_TOKEN_TTL_S}s")

    # §2.2 `sub` / `jti`; §2.1 `email` is display-only and never an authorisation input.
    subject = _text(claims, "sub")
    jti = _text(claims, "jti", max_chars=_MAX_JTI_CHARS)
    email = claims.get("email")
    if email is not None and not isinstance(email, str):
        raise _refuse("email must be a string or null")

    bound = _checked_bind(claims)
    mismatch = _bind_mismatch(bound, presented)
    if mismatch is not None:
        # §2.2/§2.4 — the binding bounds REPLAY and CROSS-FLOW SUBSTITUTION. §2.4 is explicit
        # that it does NOT bound fixation: "Single-use + short TTL bounds replay, not fixation.
        # An attacker's own live link still fixes the victim. Only the binding addresses that" —
        # and that sentence is about the binding's limit, not its reach: a victim driven to the
        # attacker's authorize URL FRESH is still fixed, and that half is an attested client
        # identity (§9 class 4(i), #8218), which is not built. Do not upgrade this line.
        raise _refuse(f"bind.{mismatch} does not match the request")

    return {"sub": subject, "email": email, "bind": bound, "jti": jti, "exp": expires_at}


# ── the single-use `jti` register (§2.2, §2.3) ─────────────────────────────

class JtiStoreUnavailable(RuntimeError):
    """The register cannot answer, so single-use cannot be guaranteed — refuse (§2.3)."""


@runtime_checkable
class ConsentJtiStore(Protocol):
    """The seam `configure_consent_jti_store` installs (§2.3) — the contract, as a TYPE.

    Two implementations satisfy it: `ProcessLocalJtiStore` (the per-process fallback) and
    `ControlPlaneJtiStore` (the SHARED one the deployed AS installs at startup). Annotating the
    module global against this protocol rather than against the fallback is what keeps the
    declared type honest on the only path that matters — a caller that type-checked against the
    fallback would reach for `_seen`/`_max_entries`, which the deployed store does not have.

    `consume` MUST be genuinely `async` (its redemption is a network call) and MUST raise
    `JtiStoreUnavailable` when it cannot answer, rather than degrading to a permissive answer:
    a register that cannot answer cannot guarantee single-use, so the caller refuses.
    """

    async def consume(self, jti: str, expires_at: float) -> bool:
        """``True`` on FIRST use; ``False`` on replay; raises when it cannot answer."""
        ...


class ProcessLocalJtiStore:
    """A process-local single-use register — the seam's fallback, NOT the deployed posture.

    §2.3 requires shared state because the AS is multi-instance: this store enforces single-use
    **per process**, which is the guarantee one instance needs and not the one a fleet needs. In
    the deployed API it is superseded by `ControlPlaneJtiStore` (installed at startup from
    `hosted_api._lifespan`); it remains here for a bare embed and for tests that want the property
    without a database. It is kept rather than deleted because the alternative — no register until
    the shared one — would ship a verifier whose `jti` field does nothing, i.e. the "specified,
    not implemented" state #3524 was reopened for.

    `consume` is `async` so the shared store is a drop-in: its redemption is a network call.
    """

    def __init__(self, *, max_entries: int = _MAX_STORE_ENTRIES,
                 clock=time.time) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries
        # Injectable so the BOUNDARY (an entry kept at the token's last valid instant) can be
        # measured rather than raced: a test that reads the wall clock twice cannot land on an
        # exact expiry, which is precisely the instant the prune predicate is about.
        self._clock = clock

    async def consume(self, jti: str, expires_at: float) -> bool:
        """``True`` on FIRST use; ``False`` on replay; raises when it cannot answer."""
        now = self._clock()
        # An entry is dropped only once the TOKEN it stands for can no longer pass `exp`, so the
        # register never forgets a `jti` that could still be replayed. Strict `<` on purpose: the
        # verifier refuses only when `exp + LEEWAY < now`, so at EXACT equality the token is still
        # acceptable and its entry must still be there.
        forget_before = now
        with self._lock:
            for key in [k for k, expiry in self._seen.items() if expiry < forget_before]:
                del self._seen[key]
            if jti in self._seen:
                return False
            if len(self._seen) >= self._max_entries:
                raise JtiStoreUnavailable(
                    f"the jti register is full ({self._max_entries} live tokens)")
            self._seen[jti] = expires_at + CONSENT_TOKEN_LEEWAY_S
            return True


#: The ONE description a shared register may publish to the 503 body.
#:
#: ⛔ THIS IS A CONTRACT, NOT A STYLE CHOICE. `consume_consent_jti` passes a
#: `JtiStoreUnavailable`'s message into the `error_description` of an UNAUTHENTICATED
#: response, and PR #10096's P2-1 deliberately kept that passthrough because the message is
#: the operator's remedy channel ("the register is full"). The condition that makes the
#: passthrough safe is that the message carries NO driver internals — no DSN, URL, query
#: string, request id, or schema detail. The control plane's errors can carry all of those,
#: so the DETAIL IS LOGGED and this literal is what is raised. `main` rejected the variant
#: that silenced the channel entirely (commit `d57b5d64e8`, kept as
#: `archive/consent-token-round2-strict-p2-1-not-adopted`); silencing it is NOT the fix and
#: would reverse a recorded decision — honouring the contract from the store side is.
_SHARED_STORE_UNAVAILABLE = "the shared consent-ticket register could not answer"


class ControlPlaneJtiStore:
    """The shared single-use register — §2.3 option (b), on the seam the AS already holds.

    WHY THIS CLASS EXISTS (§2.2/§2.3, §9 step 1)
    `ProcessLocalJtiStore` enforces single-use PER PROCESS, and the hosted API runs several
    Fly machines, so a token replayed against a different machine yields a second
    authorization code — the replay `jti` exists to bound, un-bounded in production. A
    register only bounds replay if every machine consults the SAME row store.

    WHY THE CONTROL PLANE AND NOT A THIRD MECHANISM
    §2.3 compares (a) a D1 table on the session origin redeemed over a server-to-server call
    against (b) "a row in the control plane consumed by the AS directly", and records (b) as
    the DECIDED mechanism — this class is why it is no longer "the one open mechanism choice"
    it read as while the choice was open. (b) was taken for one reason: the AS **already holds this seam at the exact call
    site** (`hosted_api.py` passes its `cp` into `issue_auth_code`, and the whole
    `oauth_clients`/`oauth_codes`/`oauth_*_tokens` family lives there). (a) would add a new
    route on the session origin, a new server-to-server authentication for it, and a new
    failure domain on the consent path — strictly more new boundary for the same guarantee.

    CONSEQUENCE OF (b) THAT IS WORTH STATING: only the AS writes. The minter records
    nothing, because single-use is bounded at REDEMPTION — which is where a replay
    arrives. So the minting side needs no service-role credential, no new secret, and no
    write access (§2.3's "no new secret is needed for the redemption leg").

    ATOMICITY. `consume` is ONE `rpc_value` call to `consume_oauth_consent_ticket`, whose
    body is a single `INSERT … ON CONFLICT DO NOTHING RETURNING` (migration
    `20261030000001_oauth_consent_tickets.sql`). A read-then-write would be a TOCTOU that
    two machines can both win; with the constraint deciding, exactly one caller gets `true`.
    """

    def __init__(self, control_plane=None) -> None:
        # Injected for tests; `None` resolves `get_control_plane()` per call, so a store
        # constructed before the control plane is configured is not silently dead forever.
        #
        # NO clock parameter, deliberately: the prune happens in POSTGRES (`now()`), so an
        # injected clock here could not influence it — offering one would let a test pin a
        # boundary the code does not actually use, which is the false-confidence seam the
        # injected clocks elsewhere in this module exist to prevent.
        self._cp = control_plane

    def _client(self):
        if self._cp is not None:
            return self._cp
        from tortoise.supabase_control import get_control_plane

        return get_control_plane()

    async def consume(self, jti: str, expires_at: float) -> bool:
        """``True`` on FIRST use; ``False`` on replay; raises when it cannot answer.

        The blocking PostgREST client is run in a worker thread rather than called inline:
        `consume` is awaited from an async request handler, and a synchronous HTTP call here
        would block the event loop for every other request on the worker.
        """
        try:
            answer = await asyncio.to_thread(self._consume_blocking, jti, expires_at)
        except JtiStoreUnavailable:
            raise
        except Exception as exc:
            # ⛔ NO `str(exc)` IN THE RAISED MESSAGE — see `_SHARED_STORE_UNAVAILABLE`.
            # A control-plane error can carry the upstream URL, a query string or a schema
            # detail, and this message reaches an unauthenticated 503 body.
            logger.warning(
                "the shared consent-ticket register could not answer (%s): %s",
                type(exc).__name__, exc, exc_info=True)
            raise JtiStoreUnavailable(_SHARED_STORE_UNAVAILABLE) from exc
        # ⛔ A NON-BOOLEAN ANSWER IS "CANNOT ANSWER", never a replay and never a pass.
        # PostgREST returns the function's scalar; a missing row, an RPC that does not
        # exist, or a `null` are all indistinguishable from `false` once coerced, and
        # coercing `None` to `False` would answer every FIRST redemption with "already
        # redeemed" — a fail-closed that reads as a replay and blames the user's token.
        if not isinstance(answer, bool):
            logger.warning("the consent-ticket register answered %r, not a boolean", answer)
            raise JtiStoreUnavailable(_SHARED_STORE_UNAVAILABLE)
        return answer

    def _consume_blocking(self, jti: str, expires_at: float) -> object:
        # ⛔ STAMP THE LAST VALID INSTANT, NOT THE RAW `exp`. The verifier refuses only when
        # `exp + LEEWAY < now`, so a token stays redemable until `exp + LEEWAY`; the RPC prunes
        # `expires_at < now()` BEFORE it inserts. Stamping the raw `exp` therefore lets a replay
        # inside the leeway window DELETE the row that fences its own nonce and then insert it
        # again — a repeatable replay for 30 s, i.e. one consent yielding N codes (measured;
        # `ProcessLocalJtiStore` adds the leeway for the same reason).
        stamp = datetime.fromtimestamp(
            expires_at + CONSENT_TOKEN_LEEWAY_S, tz=UTC).isoformat()
        return self._client().rpc_value(
            "consume_oauth_consent_ticket", {"p_jti": jti, "p_expires_at": stamp})


_jti_store_lock = threading.Lock()
_jti_store: ConsentJtiStore | None = None


def consent_jti_store() -> ConsentJtiStore:
    """The active register (§2.3). Lazily built so importing this module has no side effect.

    Returns the seam's type, not the fallback's: on the deployed AS this is a
    `ControlPlaneJtiStore` (installed from `hosted_api._lifespan`), so annotating the fallback
    here would be false on the only path that runs in production.
    """
    global _jti_store
    with _jti_store_lock:
        if _jti_store is None:
            _jti_store = ProcessLocalJtiStore()
        return _jti_store


def configure_consent_jti_store(store: ConsentJtiStore | None) -> None:
    """Install the register §9 step 1 provides (or ``None`` to rebuild the default).

    The seam exists so step 1 can land WITHOUT touching the endpoint or this verifier: whatever
    object is installed must satisfy `ConsentJtiStore` — ``async consume(jti, expires_at) -> bool``
    — and raise ``JtiStoreUnavailable`` when it cannot answer.
    """
    global _jti_store
    if store is not None:
        consume = getattr(store, "consume", None)
        # BOTH: callable, and genuinely `async`. A blocking `consume` (the natural shape for a
        # D1/HTTP store — a plain `def` returning the row) would run its body, RECORD the jti, and
        # then fail at the `await` — a 500 thrown by a request that has already spent the nonce.
        if not callable(consume) or not inspect.iscoroutinefunction(consume):
            raise TypeError(
                "a consent jti store must expose an async consume(jti, expires_at)")
    with _jti_store_lock:
        _jti_store = store


def reset_consent_jti_store() -> None:
    """Drop the register (tests, and a deliberate re-provision)."""
    configure_consent_jti_store(None)


def install_shared_consent_jti_store(control_plane=None) -> None:
    """Install the SHARED register — §2.3 cross-machine single-use (§9 step 1).

    Called once from the hosted app's startup (`hosted_api._lifespan`) rather than at import:
    the module's own contract is that importing it has no side effect, and a store resolved at
    import time would capture whatever control plane existed then.

    Installed UNCONDITIONALLY, including on a deployment with no control plane: the failure
    then surfaces as a typed `503 temporarily_unavailable` on the consent-token arm (§2.3's
    fail-closed posture) instead of silently degrading to a PER-PROCESS register — which is
    the one state this class exists to leave behind. The bearer path is untouched either way.
    """
    configure_consent_jti_store(ControlPlaneJtiStore(control_plane))


async def consume_consent_jti(jti: str, expires_at: float) -> None:
    """Spend `jti`, refusing a replay (§2.2/§2.3).

    CALL THIS LAST — after every refusal the caller can evaluate, and immediately before the code
    is minted:
      * spending it on a request that could never mint is a denial-of-service on the real flow
        (one malformed consent POST invalidates the token the page is about to present);
      * minting BEFORE spending it would allow one consent to yield two codes, which is the replay
        `jti` exists to bound.

    ⚠️ THE RESIDUAL, named rather than implied: the CALLEE that mints can still refuse AFTER this
    returns — `_resolve_org` (403 no active team / not a member), a suspended org (403), or a
    control-plane transport failure (503). Those consume the nonce, and a retry of the same token
    answers 401 `invalid_token` "already redeemed". That is deliberate and fail-closed: spending
    after the insert would admit two codes for one consent in the window between them, and the
    cost of the residual is a RE-MINT (the 120 s TTL was nearly spent anyway), never a re-login.
    The refusals that are PURE are evaluated by the caller before this call — a malformed
    `resource`, an invalid assertion, a bind that names another flow — so a caller typo does not
    burn the token.
    """
    try:
        first_use = await consent_jti_store().consume(jti, expires_at)
    except OAuthError:
        raise
    except JtiStoreUnavailable as exc:
        # The DOCUMENTED exception a step-1 store raises to say WHY it cannot answer ("the register
        # is full", "the ticket table is unreachable"). Its message is operator-facing by contract
        # and carries no driver internals, so it is passed through — losing it would leave the
        # 503 with no remedy.
        raise _unavailable(str(exc)) from exc
    except Exception as exc:
        # ANY store failure is "cannot answer", not an internal error: whatever step 1 installs
        # (D1, HTTP) raises its own exception types, and none of them may surface as a 500 after
        # the nonce has been spent. `JtiStoreUnavailable` is the documented one; this is the
        # fail-closed catch-all around it, so the module's "never a 500" claim is true by
        # construction rather than by enumeration.
        #
        # ⛔ THE DETAIL GOES TO THE LOG, NEVER INTO THE BODY. `configure_consent_jti_store()` accepts
        # any object (a D1/HTTP client per §2.3), so `str(exc)` can carry an upstream URL, a query
        # string, a request id or a schema detail — and this 503 is triggerable by one unauthenticated
        # POST. That is the no-detail convention `_oauth_unconverted_failure` states for this endpoint
        # family (#3026); an earlier version echoed the text here.
        logger.warning("consent jti store could not answer (%s): %s",
                       type(exc).__name__, exc, exc_info=True)
        raise _unavailable("the consent jti store is unavailable") from exc
    if not first_use:
        raise _refuse("it has already been redeemed (single-use, §2.2)")


# ── the reference inverse (§2.1) — NOT a route ────────────────────────────

def mint_consent_token(*, user_id: str, email: str | None, bind: dict,
                       secret: str | None = None, issuer: str | None = None,
                       ttl_s: int = CONSENT_TOKEN_TTL_S, kid: str = "consent-1",
                       jti: str | None = None, now: float | None = None) -> str:
    """Build the §2.1 token. REFERENCE INVERSE — the minting authority is the session origin.

    ⛔ No route in this repo may call this: §2.3 puts the mint on `app.*`, because that origin is
    the one that holds the session. Its purpose here is to give the verifier a checkable
    counterpart (a contract with only one side implemented is how a wire format drifts), and to
    let the tests mint exactly what §2.1 specifies. `secret` defaults to the configured
    ``CONSENT_TOKEN_SECRET``; passing one explicitly is how a test builds the
    "minted elsewhere, this AS has no key" state §8's checklist warns about.
    """
    key = secret if secret is not None else consent_token_secret()
    if not key:
        raise ValueError(f"{_SECRET_ENV} is not set, so no consent token can be signed")
    if set(bind) != set(BIND_FIELDS):
        raise ValueError(f"bind must be exactly {BIND_FIELDS}, got {sorted(bind)}")
    issued_at = time.time() if now is None else float(now)

    header = {"alg": CONSENT_TOKEN_ALG, "typ": CONSENT_TOKEN_TYP, "kid": kid}
    payload = {
        "iss": issuer or consent_token_issuer(),
        "aud": CONSENT_TOKEN_AUDIENCE,
        "iat": issued_at,
        "exp": issued_at + ttl_s,
        "jti": jti or _secrets.token_hex(32),
        "sub": user_id,
        "email": email,
        "bind": {field: bind[field] for field in BIND_FIELDS},
    }
    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url_encode(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    mac = hmac.new(key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(mac)}"
