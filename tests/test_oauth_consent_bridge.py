"""The consent identity bridge — the token contract's guards, and the four properties' anchors.

WHY THIS FILE EXISTS (#3524)
----------------------------
#3524's task 1 says the consent token "is a **new security surface** and must be specified, not
assumed". The specification lives in `docs/engineering/auth/consent-identity-bridge.md`; this file
is what stops it from becoming fiction:

  * **Task 2** — RFC 8414 `authorization_endpoint` stability: the endpoint must stay derived from
    the serving origin, stay on `/oauth/authorize`, be served from BOTH well-known routes, and
    still point at a live handler. The `base` value is `request.base_url` today; a future change
    that crosses the origins would silently break every discovered client, so it is pinned.
  * **Task 3** — the four session-flow properties (`SCOPE.md` §4 W3). Their line anchors were
    LOST in the #3501 consolidation and the scope explicitly asked for them to be *confirmed*, not
    re-anchored. Confirming them once is a claim; pinning the token that carries each property is
    the guard. Each property must have a live implementation site — on the consent page, or in the
    BFF for the three that already moved server-side.
  * **Task 5** — the sign-in fallback origin. The consent page's provider sign-in returns to
    `https://api.premiselabs.co/oauth/authorize`; `docs/ops/auth-email-flows.md` §3 presents a
    "Required entries" table for the Supabase redirect allowlist that omits that origin. The record
    must ADDRESS the origin — either as an allowlist entry, or as a recorded non-requirement once
    the bridge moves provider sign-in to the app origin. Silence is the defect.

These are STATIC guards on purpose. They assert where behaviour lives, not that it behaves: the
page's behaviour is EXECUTED by `tests/test_oauth_consent_pkce.py` (node:vm against the vendored
supabase bundle), and the BFF's by the Pages gates. A guard that cannot fail is not a guard, so
each one asserts it found its subject.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
OAUTH = REPO / "tortoise" / "oauth.py"
HOSTED = REPO / "tortoise" / "hosted_api.py"
TOKEN_TS = REPO / "website/apps/dashboard/functions/_shared/auth/token.ts"
SPEC = REPO / "docs/engineering/auth/consent-identity-bridge.md"
ALLOWLIST_RECORD = REPO / "docs/ops/auth-email-flows.md"


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def _only_line(path: Path, needle: str) -> int:
    """The 1-based line number of the single line containing `needle`.

    Uniqueness is the point: a second occurrence means the guard is now pointing at something
    else, and an anchor that matches nothing is a claim with no subject.
    """
    hits = [i + 1 for i, line in enumerate(_lines(path)) if needle in line]
    assert len(hits) == 1, (
        f"expected exactly one line containing {needle!r} in {path.name}, found {hits} — "
        "the anchor this guard cites has drifted or been duplicated"
    )
    return hits[0]


# ── Task 2 — RFC 8414 authorization_endpoint stability ──────────────────────

def test_rfc8414_authorization_endpoint_stays_same_origin() -> None:
    """The endpoint is built from the serving origin, so it cannot become cross-origin.

    `authorization_server_metadata(base)` derives every URL from the same `base`, and
    `_oauth_base(request)` derives it from `request.base_url`. Both facts are pinned here,
    because either one changing is what would make the advertised endpoint foreign to the issuer
    (every discovered MCP client then sends the authorization request somewhere else).
    """
    from tortoise.oauth import authorization_server_metadata

    md = authorization_server_metadata("https://api.premiselabs.co")
    assert md["authorization_endpoint"] == "https://api.premiselabs.co/oauth/authorize"
    assert md["issuer"] == "https://api.premiselabs.co"
    # Same origin AND a path under the issuer — the two independent ways "cross-origin" shows up.
    assert (urlparse(md["authorization_endpoint"]).netloc
            == urlparse(md["issuer"]).netloc)
    assert md["authorization_endpoint"].startswith(md["issuer"] + "/")

    # Trailing-slash normalisation must not produce a double slash (a 404 for a discovered client).
    assert (authorization_server_metadata("https://api.premiselabs.co/")["authorization_endpoint"]
            == "https://api.premiselabs.co/oauth/authorize")

    # The base is the REQUEST's origin, not a literal: that is what keeps it correct on previews
    # and on a self-hosted deployment, and it is the reason a `Host`-header change moves the
    # advertised origin with it.
    base_src = _lines(HOSTED)
    base_line = next(i for i, line in enumerate(base_src)
                     if line.startswith("def _oauth_base(request: Request)"))
    body = "\n".join(base_src[base_line:base_line + 6])
    assert "request.base_url" in body, (
        "_oauth_base no longer derives the origin from request.base_url — the metadata's origin "
        "source changed, so the RFC 8414 stability claim in the spec must be re-measured"
    )

    # Served from BOTH well-known routes (the MCP SDK tries the path-based form first), and the
    # endpoint it advertises must still be a live handler — "inert" is the other half of task 2.
    hosted = HOSTED.read_text()
    for route in (
        '@app.get("/.well-known/oauth-authorization-server")',
        '@app.get("/.well-known/oauth-authorization-server/mcp")',
        '@app.get("/oauth/authorize")',
    ):
        assert route in hosted, f"{route} is gone — task 2's stability claim no longer holds"


# ── Task 3 — the four properties, anchored ──────────────────────────────────

#: property → (file, the token that carries it). Each must have exactly ONE implementation site.
_PAGE_PROPERTIES = {
    "single-flight": (OAUTH, "if (previewInFlight) return previewInFlight;"),
    "refresh-once": (OAUTH, "let staleRefreshes = 0;"),
    "never-sign-out": (OAUTH, "NEVER call the auth logout API here"),
    "auto-advance": (OAUTH, "supabaseClient.auth.onAuthStateChange("),
}

#: The same three properties, already re-implemented server-side by the BFF (#3522/#4054). The
#: bridge's job for them is to ROUTE the page onto this path, not to write them again.
_BFF_PROPERTIES = {
    # CAS on the OLD refresh token: the loser re-reads the winner's token instead of clobbering it.
    "single-flight": "AND refresh_token = ?5",
    # Do not refresh until just before expiry, and bound how often a rejection may force one.
    "refresh-once": "const REFRESH_SKEW_MS = 60_000;",
    # `no_session` (401) and `unavailable` (503) stay DISTINCT — collapsing them is #3485.
    "never-sign-out": '| { ok: false; reason: "unavailable" }',
}


def test_the_four_properties_have_live_implementation_sites() -> None:
    """Each of SCOPE §4 W3's four properties must exist somewhere that runs.

    The properties were the only statement of the working pattern, and the consolidation dropped
    their citation block. Pinning the carrier token means the anchor can never point at nothing —
    which is exactly how "the four properties" became an unverifiable claim in the first place.
    """
    for prop, (path, token) in _PAGE_PROPERTIES.items():
        line = _only_line(path, token)
        assert f"oauth.py:{line}" in SPEC.read_text(), (
            f"the spec's {prop!r} row no longer cites oauth.py:{line} — the confirmed anchor and "
            "the guard have drifted apart"
        )

    for prop, token in _BFF_PROPERTIES.items():
        # At-least-once here: the cooldown stamp legitimately appears in both the DDL and the SQL.
        assert any(token in line for line in _lines(TOKEN_TS)), (
            f"the BFF no longer carries the {prop!r} property ({token!r} is absent from "
            "token.ts) — the spec's claim that it is already server-side is now false"
        )


def test_auto_advance_is_the_property_with_no_server_side_equivalent_yet() -> None:
    """`auto-advance` is the gap the bridge closes — the claim is falsifiable, so pin it.

    Properties 1–3 already have server-side equivalents. If a 4th ever acquires one, the spec's
    §3 table ("Gap: none — this is the property the bridge changes") becomes wrong and this test
    reds for the right reason.
    """
    spec = SPEC.read_text()
    assert "auto-advance" in spec
    assert "**none — this is the property the bridge changes**" in spec


def test_the_consent_page_still_does_not_call_any_logout_api() -> None:
    """Property 3's sharpest edge, asserted directly rather than by prose.

    A `signOut()`/`logout` call on this page is the #3485 mechanism: the session cookie is shared
    with the dashboard's *legacy* jar, so a page-initiated logout revokes it server-side for every
    device. The page's own comment says "NEVER call the auth logout API here" — this checks that
    the code obeys it.
    """
    page = OAUTH.read_text().split("_CONSENT_HTML = r\"\"\"", 1)[1]
    for forbidden in ("signOut(", "auth.logout", "removeSession", "/logout"):
        assert forbidden not in page, (
            f"the consent page calls {forbidden!r} — that is the never-sign-out property broken "
            "(the #3485 class), not a feature"
        )


# ── Task 5 — the sign-in fallback origin must be ACCOUNTED FOR ──────────────

def test_the_redirect_allowlist_record_covers_the_consent_page() -> None:
    """The consent page signs in with `redirectTo = window.location.origin + /oauth/authorize`.

    GoTrue refuses a `redirect_to` it does not recognise, and the repo's record of the required
    allowlist entries (`docs/ops/auth-email-flows.md` §3) does not contain the consent page's
    origin. Either the allowlist is missing it (provider sign-in on the only working auth flow is
    refused) or the record is incomplete. Both are defects; silence is the one that is
    unfalsifiable, so the record must mention the origin — as an entry, or as a recorded
    non-requirement once the bridge moves provider sign-in to the app origin.
    """
    record = ALLOWLIST_RECORD.read_text()
    section = record.split("## 3. Redirect allowlist", 1)
    assert len(section) == 2, (
        "docs/ops/auth-email-flows.md no longer has a '## 3. Redirect allowlist' section — the "
        "allowlist record moved, and this guard is now scanning nothing"
    )
    table = section[1].split("## 4.", 1)[0]
    assert "| Entry | Why |" in table, "the required-entries table is gone from §3"

    assert "api.premiselabs.co" in record, (
        "the consent page's sign-in fallback origin (https://api.premiselabs.co/oauth/authorize) "
        "is absent from the redirect-allowlist record. Add the entry, or record that the bridge "
        "removed the dependency — do not leave the origin unaddressed (#3524 task 5)."
    )


# ── The spec's own load-bearing clauses ─────────────────────────────────────

def test_the_spec_binds_every_authorize_param_single_use_and_a_ttl() -> None:
    """The token contract is only a contract if all three halves are stated.

    `SCOPE.md` §9 class 4(ii) requires the binding, single-use AND a TTL together; the class is
    OPEN and its stated failure mode was exactly "consolidation had dropped [the binding], leaving
    only the half that does not work". A spec that states two of the three re-creates that.
    """
    spec = SPEC.read_text()
    for clause in (
        '"client_id", "redirect_uri", "code_challenge", "state"',  # the bind set, verbatim
        '"jti"',                                                  # single-use nonce
        "CONSENT_TOKEN_TTL_S",                                    # the TTL, named
        "Consent fixation",                                       # the attack it ADDS, named
        "class 4(i)",                                             # the half that is still open
    ):
        assert clause in spec, f"the token contract no longer states {clause!r}"
