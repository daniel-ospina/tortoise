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

import ast
import re
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
OAUTH = REPO / "tortoise" / "oauth.py"
HOSTED = REPO / "tortoise" / "hosted_api.py"
TOKEN_TS = REPO / "website/apps/dashboard/functions/_shared/auth/token.ts"
CONSENT_FLOW_TS = (
    REPO / "website/apps/dashboard/functions/_shared/auth/consent-flow.ts"
)
SPEC = REPO / "docs/engineering/auth/consent-identity-bridge.md"
ALLOWLIST_RECORD = REPO / "docs/ops/auth-email-flows.md"
#: Property 4's route (the bridge's §9 step 3). Its PRESENCE is the artifact that
#: decides whether the spec's "un-routed" status is honest — never a prose claim.
CONSENT_ROUTE_DIR = REPO / "website/apps/dashboard/functions/api/consent"


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
    # Assert on the RETURNED EXPRESSION, not a fixed window of source lines.
    # A window lands inside the function's docstring — which literally reads
    # "Origin for RFC 8707 resource URIs (request.base_url in tests resolves..."
    # — so `return "https://api.premiselabs.co"` still satisfied a substring
    # check and the guard PASSED on the very regression it names (#8219 review).
    # `ast` reads behaviour; prose cannot satisfy it.
    _tree = ast.parse(HOSTED.read_text())
    _fn = next((n for n in ast.walk(_tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_oauth_base"), None)
    assert _fn is not None, (
        "_oauth_base is gone from hosted_api.py — the RFC 8414 origin derivation the spec's §5 "
        "VERIFIED section rests on no longer exists"
    )
    _returns = [n for n in ast.walk(_fn) if isinstance(n, ast.Return)]
    assert _returns, (
        "_oauth_base has no return — it no longer derives an origin at all, so the metadata's "
        "authorization_endpoint source changed and the RFC 8414 claim must be re-measured"
    )
    body = ast.unparse(_returns[-1].value)
    assert "request.base_url" in body, (
        "_oauth_base no longer derives the origin from request.base_url (returned "
        f"{body!r}) — the metadata's origin source changed, so the RFC 8414 stability claim "
        "in the spec must be re-measured"
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

#: The same three properties, already re-implemented server-side by the BFF (#3522/#4054), plus
#: property 4's new decision. Each entry is (module, the token that carries the guarantee).
#: The bridge's job for 1-3 is to ROUTE the page onto this path, not to write them again.
_BFF_PROPERTIES = {
    # CAS on the OLD refresh token: the loser re-reads the winner's token instead of clobbering it.
    "single-flight": (TOKEN_TS, "AND refresh_token = ?5"),
    # Do not refresh until just before expiry, and bound how often a rejection may force one.
    "refresh-once": (TOKEN_TS, "const REFRESH_SKEW_MS = 60_000;"),
    # `no_session` (401) and `unavailable` (503) stay DISTINCT — collapsing them is #3485.
    "never-sign-out": (TOKEN_TS, '| { ok: false; reason: "unavailable" }'),
    # Property 4's decision — the event listener's server-side replacement (#3524 task 3).
    "auto-advance": (CONSENT_FLOW_TS, "export function decideConsentAdvance("),
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

    for prop, (path, token) in _BFF_PROPERTIES.items():
        # At-least-once here: the cooldown stamp legitimately appears in both the DDL and the SQL.
        assert any(token in line for line in _lines(path)), (
            f"{path.name} no longer carries the {prop!r} property ({token!r} is absent) — the "
            "spec's claim that it is server-side is now false"
        )


def test_auto_advance_status_matches_the_route_artifact() -> None:
    """`auto-advance`'s server-side status is pinned to the ARTIFACT, not to prose.

    Properties 1–3 already had server-side equivalents. Property 4's decision
    (`consent-flow.ts::decideConsentAdvance`) is NEW here and its route is the
    bridge's §9 step 3. The status must not rest on a sentence that can only
    re-stale: a claim about our own process has no artifact to check it against,
    and the honest update — deleting the sentence when the route ships — would fail
    a test that pinned the sentence, so the cheapest way to stay green would be to
    leave a false statement. This guard instead reads REALITY (does the route
    directory exist?) and asserts the spec AGREES with it, either way. When the
    route lands and the spec's "NOT built" line is deleted, it retires itself.
    """
    spec = SPEC.read_text()
    assert "auto-advance" in spec
    assert "decideConsentAdvance" in spec, (
        "the spec no longer names property 4's server-side decision — the implementation and "
        "the contract have drifted apart"
    )
    routed = CONSENT_ROUTE_DIR.is_dir() and any(CONSENT_ROUTE_DIR.rglob("*.ts"))
    claims_unbuilt = "is NOT built" in spec
    assert routed or claims_unbuilt, (
        "no `api/consent/` route exists and the spec no longer records the un-routed gap — "
        "a partial delivery has become a false claim"
    )
    assert not (routed and claims_unbuilt), (
        "the `api/consent/` route now exists but the spec still says it is NOT built — "
        "update the spec's status to match the artifact"
    )


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

    assert "api.premiselabs.co" in table, (
        "the consent page's sign-in fallback origin (https://api.premiselabs.co/oauth/authorize) "
        "is absent from the redirect-allowlist record. Add the entry, or record that the bridge "
        "removed the dependency — do not leave the origin unaddressed (#3524 task 5)."
    )


# ── §7 — the deletion set's anchors, recomputed not trusted ──────────────────

#: §7's deletion set: (row marker, the token that IS the item) for each anchor the table cites, so
#: the row's `oauth.py:<n>` can be RECOMPUTED from the source instead of trusted. The marker names
#: the ROW the citation must appear in: a number that happens to be cited elsewhere in §7 (the
#: prose above the table cites `oauth.py:2268-2497`, for instance) must not be able to satisfy a
#: row it does not belong to. Each token must also occur on exactly one line (see `_only_line`), so
#: a duplicated or renamed site reds here rather than silently re-pointing.
_DELETION_SET = {
    ("adapter + the aux-store probe chain", "const auxStores = () => {"),
    ("`COOKIE_NAME`/`COOKIE_DOMAIN`",
     'const COOKIE_NAME = "sb-tortoise-auth-token";'),
    ("`COOKIE_NAME`/`COOKIE_DOMAIN`", 'const COOKIE_PATH = "/";'),
    ("`COOKIE_NAME`/`COOKIE_DOMAIN`", 'const COOKIE_DOMAIN = ".premiselabs.co";'),
    ("`COOKIE_NAME`/`COOKIE_DOMAIN`", "const SIZE_GUARD = 3800;"),
    ("`COOKIE_NAME`/`COOKIE_DOMAIN`", "const SIZE_CAP = COOKIE_LIMIT"),
    ("`COOKIE_NAME`/`COOKIE_DOMAIN`", 'document.cookie = key + "=" + encoded'),
    ("`COOKIE_NAME`/`COOKIE_DOMAIN`", 'document.cookie = key + "=;"'),
    ("supabase-js CDN", "window.supabase.createClient("),
    ("`pkceIncapable`/`CAPABILITY`", "function pkceIncapable() {"),
    ("`onAuthStateChange` (property 4", "supabaseClient.auth.onAuthStateChange("),
}


def test_the_deletion_set_rows_cite_live_sites() -> None:
    """§7's deletion-set table must cite the line each item ACTUALLY lives on at this head.

    This table is the part of the spec that was hand-remapped and got it wrong twice: round 1
    advanced a block by +171 (`oauth.py` had moved +190), and round 2 rewrote the §7 rows with the
    PR BASE's numbers, so `:2037` pointed at `_valid_redirect_uri`'s neighbour instead of
    `COOKIE_NAME`. `test_the_four_properties_have_live_implementation_sites` recomputes only the
    four property anchors, so nothing caught it: the anchors were wrong and CI was green.

    The guard therefore DERIVES the number from `tortoise/oauth.py` and requires the row that names
    the item to cite it — an anchor is a claim about a live site, and a claim nobody recomputes is
    exactly how this table came to point at unrelated code.
    """
    spec = SPEC.read_text()
    section = spec.split("## 7. Deletion set", 1)
    assert len(section) == 2, (
        "the spec no longer has a '## 7. Deletion set' section — the deletion set and its ordering "
        "guard moved, and this guard is now scanning nothing"
    )
    section_text = section[1].split("## 8.", 1)[0]
    rows = [line for line in section_text.splitlines() if line.startswith("|")]
    assert rows, "§7 has no table rows — the guard would pass vacuously"

    stale: list[str] = []
    for row_marker, token in _DELETION_SET:
        matching = [row for row in rows if row_marker in row]
        assert len(matching) == 1, (
            f"§7 should have exactly one row naming {row_marker!r}, found {len(matching)} — the "
            "table's shape changed and this guard can no longer tell which row it is checking"
        )
        # Both citation forms the spec uses: `oauth.py:<n>` and the bare in-context `:<n>` (§7
        # only cites `oauth.py`, but the bare form is what the §4 table already uses). The item's
        # line must be the START of a cited span: a range names the block it opens, and the
        # singleton anchors name the individual sites INSIDE such a range. Containment is
        # deliberately not accepted — the round-2 defect was exactly the singletons going stale
        # while the row's outer range still covered them, so a containment test would have passed
        # on `(:2037, :2052, :2053, :2054, :2060)`.
        starts = {int(m.group(1)) for m in re.finditer(r":(\d+)", matching[0])}
        line = _only_line(OAUTH, token)
        if line not in starts:
            stale.append(f"{token!r} is at oauth.py:{line}, but its row starts citing {sorted(starts)}")
    assert not stale, (
        "§7's deletion set cites line numbers that do not name the item: " + "; ".join(stale) +
        " — re-derive them from tortoise/oauth.py AFTER the last source edit (a remap done before "
        "a later comment edit shifts them again, which is how this table went stale twice)."
    )


# ── The spec's `hosted_api.py` anchors, recomputed the same way ──────────────

#: `hosted_api.py` anchors the spec cites as EVIDENCE. The provenance note's QUOTATIONS of #3524's
#: stale issue body (`hosted_api.py:23409`, `:23284-23292`) are deliberately not here: a quotation
#: of the issue cannot be remapped when the source moves. These can, and did — the §5/§8 §10 rows
#: were off by ~3,300 lines (`:34756` naming backup-restore code) while `_oauth_base` alone had been
#: corrected, so each table mixed one live anchor with a dead one and read as authoritative. An
#: anchor is satisfied when the item's line STARTS a citation, for the same reason as §7's: a
#: broad range that merely contains the site is what let the round-2 singletons go stale.
_HOSTED_ANCHORS = {
    "the well-known authorization-server routes":
        '@app.get("/.well-known/oauth-authorization-server")',
    "the authorize handler": '@app.get("/oauth/authorize")',
    "the consent handler": '@app.post("/oauth/consent")',
    "`_oauth_base`": "def _oauth_base(request: Request) -> str:",
}


def test_the_spec_cites_live_hosted_api_sites() -> None:
    """EVERY `hosted_api.py:<n>` anchor in the spec must name one of the live sites.

    Checked in the inverse direction on purpose: a dead anchor is dead even when the SAME line is
    also cited correctly somewhere else, and the §5/§8 §10 rows did exactly that (one corrected
    `_oauth_base` beside three anchors ~3,300 lines off). So the rule is "no citation may point
    anywhere but a derived site", not "each site must be cited somewhere".
    """
    spec = SPEC.read_text()
    # The provenance note QUOTES #3524's stale issue-body anchors; a quotation cannot be remapped
    # when the source moves, so it is excluded rather than "fixed".
    body = "\n".join(line for line in spec.splitlines() if not line.startswith(">"))
    live = {_only_line(HOSTED, token) for token in _HOSTED_ANCHORS.values()}
    cited = {int(m.group(1)) for m in re.finditer(r"hosted_api\.py:(\d+)", body)}
    assert cited, "the spec cites no `hosted_api.py` line anchors at all"
    dead = sorted(cited - live)
    assert not dead, (
        f"the spec cites hosted_api.py line(s) {dead} that are not one of the live sites "
        f"{sorted(live)} ({sorted(_HOSTED_ANCHORS)}) — re-derive them from tortoise/hosted_api.py "
        "after the last source edit, or cite the route string instead of a line number."
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


def test_the_spec_names_the_behavioural_guard_for_all_four_properties() -> None:
    """The spec must point at the file that PROVES the four properties, not only at anchors.

    `test_oauth_consent_bridge.py` pins WHERE they live; `tests/test_oauth_session_flow_properties.py`
    pins WHAT they do (and each of its tests goes red when its property is deleted). A spec that
    names only the anchors lets "the four properties" be satisfied by prose again.
    """
    assert "tests/test_oauth_session_flow_properties.py" in SPEC.read_text(), (
        "the spec no longer names the behavioural guard for the four properties — the contract "
        "would be anchor-only again, which is the state #3524 task 3 exists to end"
    )
