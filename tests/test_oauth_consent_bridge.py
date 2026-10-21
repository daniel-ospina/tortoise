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

import pytest

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
        # only cites `oauth.py`, but the bare form is what the §1 table already uses). The item's
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


# ── The spec's `hosted_api.py` citations: SYMBOLS, never line numbers ───────

#: Every form this tree writes a `hosted_api.py` LINE citation in, and the one the guard must
#: catch for each: `:39152`, `#L39152`, a space-separated `L39152`, a space or a newline after the
#: separator (CommonMark renders a wrapped code span as one space), the fullwidth colon, and the
#: bare `:<n>` CONTINUATION the §1 table uses for `session_auth.py:588` → `` (`:600`) ``. Each was
#: let through by an earlier version of this guard (measured), because the check read one line at
#: a time and knew one separator. So:
#:
#:  * `_HOSTED_ANCHOR_RE` matches the explicit forms — the number adjacent to the filename, which
#:    it may reach through `:`, `#L`, `L` or the prose word `line`, plus any whitespace, and it
#:    also tolerates an `L` AFTER the colon (`hosted_api.py:L39432`, the sibling spelling this
#:    doc's house style of separate code spans invites); `_stray_anchors` FOLDS the body first
#:    (whitespace runs to one space, the fullwidth colon to ASCII), so a wrapped or padded anchor
#:    is one shape again and the offset still points at a real line;
#:  * `_BARE_ANCHOR_RE` + `_NEAREST_FILE_RE` catch the continuation idiom per BLOCK (`_blocks`),
#:    attributing the number to the nearest FILE TOKEN of ANY extension to its LEFT rather than
#:    to "any file named on the line" (see `_NEAREST_FILE_RE` for why the looser rule
#:    over-reported) — compared through `_file_basename`, which folds the case and strips a regex
#:    escape, so the repository-relative spelling `tortoise/hosted_api.py` (§1's
#:    `Canonical code:` line), `HOSTED_API.PY` and `hosted_api\\.py` all attribute exactly like
#:    the plain name. Each of those was green under an earlier version (measured).
#:
#:    ⛔ The owner token is ANY extension, NOT `*.py`: a `:<n>` belonging to a `.ts`/`.yml`/`.md`
#:    cited in the same table row (or paragraph) as a `hosted_api.py` mention was reported as a
#:    `hosted_api.py` anchor, i.e. an honest citation redded with a message naming a file the
#:    editor did not touch (§10's `Bindings` row after the `hosted_api.py` rows — measured).
_HOSTED_ANCHOR_RE = re.compile(
    r"hosted_api\\?\.py\s*(?::\s*(?:#?L)?|#?L|line\s+)\s*(\d+)", re.IGNORECASE)
#: The NUMBER of a line anchor, in every spelling this doc family writes it: the bare `:<n>`
#: continuation, `#L<n>` / `L<n>` in their own code span (`` `hosted_api.py`, `L39432` `` — the
#: natural way to re-add one here, and green until this arm existed), and the prose `line <n>`.
#:
#: ⚠️ The `L` arms require TWO digits: a one-digit `L1`/`L2` in prose is the guard LAYER's name
#: (`L1` = the per-call opt-in, `L2` = the non-test-graph check), not a line number, and this
#: module is full of both. No real citation is lost — `hosted_api.py` starts at line 1, so a
#: `L10` and up still reds — and the lookbehind keeps a letter-prefixed token (`FAIL12`) out.
_BARE_ANCHOR_RE = re.compile(
    r":\s*(\d+)|(?<![\w#])#?L\s*(\d{2,})|(?<![\w])line\s+(\d+)", re.IGNORECASE)
#: The nearest FILE TOKEN to the LEFT of a bare anchor, which is what the continuation
#: belongs to. It is matched WHOLE (it may carry a directory) and compared by BASENAME, so
#: `tortoise/hosted_api.py` attributes its continuation exactly like `hosted_api.py` does. A
#: file-name scan rather than "the line mentions hosted_api.py somewhere": §10's
#: `Redemption` row names `hosted_api.py` AND carries `session_auth.py:588`, so the loose rule
#: reported the row's legitimate anchor to a DIFFERENT file (measured).
#:
#: ANY extension, not `*.py` (the same defect one level down): `_blocks` merges a whole table
#: into one block, so a `:<n>` belonging to a `.ts`/`.yml` cited at-or-after a `hosted_api.py`
#: row was attributed to `hosted_api.py` — a false red on an honest edit, naming a file the
#: editor never opened (measured: §10's `Bindings` row `config/required-bindings.yml` `:12`).
#: The nearest file-like token is the owner whatever its extension; only a `hosted_api.py`
#: ownership reds, so widening the token set can only REMOVE reports, never add one.
#:
#: The scan is per BLOCK (`_blocks`), not per line: a continuation whose governing
#: `hosted_api.py` mention sits on an earlier line of the same paragraph or table IS attributed,
#: and one separated by a blank line is not. Same-LINE-only was a hole — `` …`hosted_api.py` ``
#: then `` `:39205` `` reads clean, yet CommonMark joins the pair into one sentence (measured).
#: And every regex here is case-insensitive and tolerates an escaped dot, because `HOSTED_API.PY:…`
#: and `hosted_api\\.py:…` are the same citation to a reader (both were green — measured).
_NEAREST_FILE_RE = re.compile(
    r"[\w./-]+\\?\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|yml|yaml|json|jsonc|md|mdx|sh|bash|sql|toml"
    r"|cfg|ini|conf|html|css|txt|lock)",
    re.IGNORECASE)


def _collapse(text: str) -> str:
    """Fold whitespace runs and the fullwidth colon, so one anchor is one shape."""
    return re.sub(r"\s+", " ", text.replace("\uff1a", ":"))


def _fold(raw: str) -> tuple[str, list[int]]:
    """`_collapse`, plus the raw line each folded character came from.

    Scanning line by line was the hole the review found: a code span wrapped across two lines is
    invisible to a per-line regex, yet CommonMark renders the newline as a space, so the document
    still reads ``hosted_api.py: 39286``. Folding the WHOLE body first puts the anchor back in one
    shape; the parallel line list keeps the failure message pointing at a real line.
    """
    out: list[str] = []
    origin: list[int] = []
    line, gap = 1, False
    for char in raw:
        if char == "\n":
            line, gap = line + 1, True
            continue
        if char in " \t":
            gap = True
            continue
        if gap and out:
            out.append(" ")
            origin.append(line)
        gap = False
        out.append(":" if char == "\uff1a" else char)
        origin.append(line)
    return "".join(out), origin


def _file_basename(token: str) -> str:
    """`token`'s file name, case-folded, with any regex escape removed.

    The escape is the whole point: `_NEAREST_FILE_RE` tolerates `hosted_api\\.py`, and comparing
    the RAW token left the backslash in it, so `hosted_api\\.py` matched and then failed
    equality — the `\\?` was inert for the continuation arm while the explicit arm closed
    (measured). One normaliser, used by both arms, so they cannot disagree again.
    """
    return token.replace("\\", "").rsplit("/", 1)[-1].lower()


def _blocks(outside: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """`outside` split into blank-line-separated blocks: a paragraph or one table.

    The continuation rule used to read ONE LINE, and a code span wrapped across two lines left it
    green — `` …`hosted_api.py` `` / `` `:39205` `` — even though CommonMark renders the pair as
    one sentence naming the file and then a line number (measured). A block is the smallest unit
    that keeps that attribution possible while still refusing to reach across a paragraph break,
    where the file name really can belong to a different claim.
    """
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for index, line in outside:
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append((index, line))
    if current:
        blocks.append(current)
    return blocks


def _bare_continuations(raw: str) -> list[str]:
    """Every bare `:<n>` continuation in `raw` that belongs to `hosted_api.py`.

    The note half of the guard. `_stray_anchors` scans the body OUTSIDE the provenance note by
    design (the note is allowed to quote the stale pair), which left the ONE region that already
    holds anchors exempt from the CONTINUATION rule too: `_HOSTED_ANCHOR_RE` sees only the
    explicit forms, so `` > Verified against `hosted_api.py`, `:39286`. `` inside the note left
    `quoted == {'23284','23409'}` and `stray == []` — green, anchor added (measured). Applying
    the continuation rule to the note's own text closes it: the note's legitimate quotation is
    the EXPLICIT `hosted_api.py:23409` / `:23284-23292` pair, so no honest note line carries a
    continuation.

    An `:<n>` sitting INSIDE an explicit anchor (`hosted_api.py:23409`) is that anchor, not a
    continuation — the explicit scan already owns it, and flagging it would make the note's own
    quotation illegal.
    """
    folded = _collapse(raw)
    explicit = [match.span() for match in _HOSTED_ANCHOR_RE.finditer(folded)]
    found: list[str] = []
    for match in _BARE_ANCHOR_RE.finditer(folded):
        if any(start <= match.start() < end for start, end in explicit):
            continue
        owners = _NEAREST_FILE_RE.findall(folded[:match.start()])
        if owners and _file_basename(owners[-1]) == "hosted_api.py":
            found.append(match.group(0))
    return found


#: The ONLY anchors the `#3524` provenance note may quote — the stale issue body it quotes.
#: Compared as a SUBSET, never by equality: pruning a quotation that no longer needs quoting has
#: to stay legal (the same contract `test_auto_advance_status_matches_the_route_artifact` states —
#: a guard must not make the honest edit the expensive one), while ADDING one is a new citation.
_NOTE_QUOTED_ANCHORS = frozenset({"23409", "23284"})


def _note_anchor_violations(note_raw: str) -> tuple[list[str], list[str]]:
    """What the provenance note does NOT have licence for: `(new_anchors, continuations)`.

    ONE rule in ONE place, because both halves of the guard call it: the primary guard accepted a
    pruned quotation (subset) while `test_the_provenance_note_cannot_smuggle_a_bare_continuation`
    pinned set EQUALITY, so the honest cleanup the guard's own message invites redded its
    neighbour with a bare two-set diff and no instruction (measured). Sharing the helper makes
    that disagreement unrepresentable.

    `new_anchors` is a subset violation, not an equality: the note may quote `23409` and `23284`,
    may prune either, and may not gain anything else. `continuations` is the note half of
    `_bare_continuations`: the note is exempt from the body scan, so the `:<n>` continuation form
    has to be checked inside it too (an anchor added there in that spelling was green before).
    """
    quoted = set(_HOSTED_ANCHOR_RE.findall(_collapse(note_raw)))
    return sorted(quoted - _NOTE_QUOTED_ANCHORS), _bare_continuations(note_raw)


def _provenance_note_bounds(lines: list[str]) -> tuple[int, int]:
    """The `[start, end)` span of the ONE blockquote this doc may quote an anchor in.

    An ASSERTION, not `next(...)` with no default: a reworded or retired `#3524` note raised a
    bare `StopIteration` from inside the test body — fail-closed, but with no message to act on,
    which contradicts this module's contract that "each one asserts it found its subject".
    """
    note_start = next(
        (i for i, line in enumerate(lines)
         if line.startswith(">") and "#3524" in line and "Provenance" in line), None)
    assert note_start is not None, (
        "the `#3524` provenance note is gone — the ONE blockquote allowed to quote a "
        "`hosted_api.py` anchor can no longer be identified, so this guard cannot tell a "
        "quotation from a citation. Restore the note, or move its anchors into the evidence rows."
    )
    note_end = note_start
    while note_end < len(lines) and lines[note_end].startswith(">"):
        note_end += 1
    return note_start, note_end


def _stray_anchors(lines: list[str], note_start: int, note_end: int) -> list[str]:
    """Every `hosted_api.py` line anchor outside the note, each with the line it is REALLY on.

    The body is scanned with the note REMOVED, so an index into that shortened list is not a line
    number of the file: reporting one as such named a line `note_end - note_start` early (7 in this
    spec) — an unrelated table row — for essentially every anchor a future editor could add, which
    is the one message this guard ever produces.
    """
    outside = [
        (index, re.sub(r"^\s*(?:>\s?)+", "", line))
        for index, line in enumerate(lines)
        if not note_start <= index < note_end]
    stray: list[str] = []
    for block in _blocks(outside):
        raw = "\n".join(text for _, text in block)
        folded, origin = _fold(raw)
        explicit: list[tuple[int, int]] = []
        for match in _HOSTED_ANCHOR_RE.finditer(folded):
            lineno = block[origin[match.start()] - 1][0] + 1
            explicit.append(match.span())
            stray.append(f"line {lineno}: {match.group(0)!r}")
        for match in _BARE_ANCHOR_RE.finditer(folded):
            if any(start <= match.start() < end for start, end in explicit):
                # The `:<n>` INSIDE an explicit anchor is that anchor, not a continuation: the
                # explicit scan already owns it, and flagging it would double-report one citation.
                continue
            owners = _NEAREST_FILE_RE.findall(folded[:match.start()])
            # BASENAME, not the matched token: `tortoise/hosted_api.py` is the repository-relative
            # spelling this very document uses for the same file, so a continuation after it
            # belongs to `hosted_api.py` too (exact equality let it through — measured), and the
            # comparison is case-folded for the same reason (`HOSTED_API.PY:…` was green).
            if owners and _file_basename(owners[-1]) == "hosted_api.py":
                lineno = block[origin[match.start()] - 1][0] + 1
                stray.append(
                    f"line {lineno}: bare continuation {match.group(0)!r} belongs to "
                    "`hosted_api.py`")
    return stray


#: `(section heading prefix, row prefix, citations that row must carry)` — the sites the old line
#: anchors pointed at. Pinned PER ROW, and the row must be unique IN ITS SECTION: a document-wide
#: presence check is satisfied by an unrelated occurrence of the same route string (the route also
#: occurs in a §9 plan step), and a document-wide `startswith` is satisfied by a decoy row with the
#: same prefix anywhere else in the file. Both were measured green before this shape.
_SPEC_ROW_CITATIONS = (
    ("## 1.", "| `/oauth/authorize` carries",
     ('`@app.get("/oauth/authorize")`', "`hosted_api.py`")),
    ("## 5.", "- **Not inert:**", ('`@app.get("/oauth/authorize")`',)),
    ("## 5.", '`@app.get("/.well-known/oauth-authorization-server")`',
     ('`@app.get("/.well-known/oauth-authorization-server")`', "`/mcp` twin")),
    ("## 10.", "| **Redemption** |",
     ('`@app.post("/oauth/consent")`', "`hosted_api.py`")),
    ("## 10.", "| **Endpoint stability** |",
     ("`_oauth_base(request)`", "`hosted_api.py`")),
)

#: The four `hosted_api.py` SYMBOLS/ROUTES the spec names as evidence, and the tokens that must
#: still resolve to exactly ONE line each.
#:
#: ⛔ These used to be LINE anchors, and the spec used to cite them by number. Both were retired:
#: the numbers drifted ~50 lines behind the source and the §1/§5/§10 tables then pointed at the
#: wrong functions while reading as authoritative. The spec now cites the route string or the
#: symbol, and `test_the_spec_cites_no_hosted_api_line_numbers` pins that it does so IN THE ROW
#: THAT MAKES THE CLAIM, while forbidding any line anchor outside the `#3524` provenance note.
#: What survives of the old rule is `_only_line`: a symbol citation is only checkable while the
#: symbol it names still exists exactly once.
_HOSTED_ANCHORS = {
    "the well-known authorization-server routes":
        '@app.get("/.well-known/oauth-authorization-server")',
    "the authorize handler": '@app.get("/oauth/authorize")',
    "the consent handler": '@app.post("/oauth/consent")',
    "`_oauth_base`": "def _oauth_base(request: Request) -> str:",
}


#: `(document fragment, expected stray lines)` for the forms an earlier line-by-line, one-
#: separator, case-sensitive scan let through. Pinned as a table rather than as prose in a commit
#: message: each row is a shape a future editor can reintroduce, and `_stray_anchors` is the only
#: thing standing between it and a documented number that has already rotted.
_EVASION_SHAPES = (
    # wrapped under backticks: the continuation's owner is on the previous line of the block
    ("The builder (`hosted_api.py`\n`:39205`).", [2]),
    # the same, wrapped without the second code span
    ("See `hosted_api.py`\n`:39205`.", [2]),
    # wrapped inside a blockquote, where the marker is stripped before inline parsing
    ("> The builder is `hosted_api.py`\n> `:39205`.", [2]),
    # case variants of the explicit form
    ("See `HOSTED_API.PY:39286`.", [1]),
    ("See `Hosted_Api.py:39286`.", [1]),
    # an escaped dot, explicit and as the continuation's owner
    ("See `hosted_api\\.py:39286`.", [1]),
    ("See `hosted_api\\.py`, then `:39205`.", [1]),
    # the repository-relative spelling is the same file
    ("See `tortoise/hosted_api.py`, then `:39205`.", [1]),
    # NEGATIVE: a file name in the PREVIOUS block is a different claim, not this continuation
    ("The base is in `hosted_api.py`.\n\nElsewhere `session_auth.py:588` → `:600`.", []),
    # the `L` family in the spelling THIS doc's house style invites — one file token, then the
    # anchor in its own code span, so neither `:` adjacency nor the continuation's colon reaches it
    ("See `hosted_api.py`, `L39432`.", [1]),
    ("See `hosted_api.py` `#L39432`.", [1]),
    # …and the explicit form with the `L` AFTER the colon
    ("See `hosted_api.py:L39432`.", [1]),
    # the prose spelling of a line citation
    ("See `hosted_api.py`, line 39432.", [1]),
    # NEGATIVE (I1): the continuation's owner is the nearest FILE TOKEN of any extension, not the
    # nearest `*.py` — an honest `.ts`/`.yml` anchor after a `hosted_api.py` mention is not ours
    ("See `hosted_api.py`, and the BFF `token.ts`, `:152`.", []),
    ("| a | `hosted_api.py` |\n| b | `config/required-bindings.yml`, `:12` |", []),
)


@pytest.mark.parametrize("fragment,expected", _EVASION_SHAPES)
def test_the_anchor_scan_catches_every_shape_at_any_offset(fragment: str, expected: list[int]) -> None:
    """The evasion table: each row was GREEN under an earlier version of this guard.

    `test_the_anchor_scan_catches_every_form_at_the_line_it_is_on` covers the shapes that were
    already caught; this covers the ones that were not, and the negatives that keep the block
    boundary and the OWNER rule honest.

    What each row can and cannot prove, measured rather than assumed. Reverting `_blocks` reds the
    three wrapped rows, and reverting `_file_basename`'s escape strip reds the escaped-continuation
    row — those are single-mechanism rows. The `IGNORECASE` flags and the case FOLD are NOT
    individually load-bearing, because the two arms overlap: `HOSTED_API.PY:39286` is caught by
    the explicit arm even if the continuation arm's regex is case-sensitive, and vice versa. So
    reverting either flag alone stays green — that redundancy is deliberate (each arm covers the
    other's blind spot), and it means no row here should be read as pinning one flag.

    The `L`/`line` rows and the two `.ts`/`.yml` negatives ARE single-mechanism rows, and each was
    green before its fix (measured): the four anchor rows need the widened arms (`L` after the
    colon, `#?L`/`line` as a continuation), and the two negatives need the owner scan to accept a
    non-`.py` file token — under the `*.py`-only scan both negatives reported the `hosted_api.py`
    mention to their left, naming a file the editor never touched.
    """
    lines = fragment.splitlines()
    reported = _stray_anchors(lines, 0, 0)
    got = sorted({int(entry.split()[1].rstrip(":")) for entry in reported})
    assert got == expected, (
        f"{fragment!r} should report stray anchors on lines {expected}, got {got} "
        f"({reported}) — a shape that reads as a line citation must be caught, and a file name "
        "in the PREVIOUS block must not be credited with this continuation")


def test_the_spec_cites_no_hosted_api_line_numbers() -> None:
    """The spec must cite hosted_api.py by SYMBOL or ROUTE, never by line number.

    ⛔ THIS GUARD USED TO REQUIRE THE OPPOSITE, and that is why it was replaced rather than
    repaired. It demanded that every `hosted_api.py:<n>` anchor match a live site — a check that
    has to be re-derived after EVERY source edit and silently becomes the defect between edits.
    It did: on `main` at `fa7b27b66` the spec cited `39152/39225/39233/39379` while the sites had
    moved to `39205/39278/39286/39432`, so the whole §1/§5/§10 evidence table named the wrong
    functions and only a red shard noticed.

    A line number is valid only at the commit it was written against; a symbol or a route is
    valid while the code exists. So the PROHIBITION is pinned, and it is scoped by IDENTITY, not
    by syntax:

    * the ONE blockquote this doc owns — the `#3524` provenance note — is identified by its
      content, and the anchors inside it may only be the quoted issue-body pair (pruning the
      quotation passes; ADDING one does not).
      A blanket "skip every line starting with `>`" is what an earlier version did, and it let a
      stale anchor ride in any NEW blockquote (measured: `> Verified against \
      `hosted_api.py:39286`.` passed);
    * every other line — including other blockquotes — may carry no anchor at all, in ANY of the
      forms this tree writes: `:n`, `#Ln`, a space-separated `Ln`, an `Ln` after the colon
      (`hosted_api.py:L39432`), the prose `line <n>`, a space or a WRAPPED line after the
      separator, the fullwidth colon, and the bare `:n` / `Ln` / `#Ln` / `line <n>` continuation
      after a file named in ANY spelling of `hosted_api.py` (basename comparison, so
      `tortoise/hosted_api.py` counts). Each of those was green under an earlier version of this
      guard (measured), because the scan read one line at a time and knew one separator — and the
      token comparison let the repository-relative spelling through after the bare continuation
      was added (measured), and the `L`/`line` spellings through until the owner scan knew them
      (measured). The same continuation rule is applied INSIDE the provenance note, which the body
      scan skips by design: without it the note was the one place a `hosted_api.py` continuation
      could be added green (measured);
    * ⛔ the continuation's OWNER is the nearest file token of ANY extension, not the nearest
      `*.py`. A `:n` belonging to a `.ts`/`.yml` cited at-or-after a `hosted_api.py` row is that
      other file's anchor — reading it as ours red a genuine citation with a message naming a
      file the editor never touched (measured: §10's `Bindings` row,
      `config/required-bindings.yml` `:12`, after the `hosted_api.py` rows);
    * and the replacement citations are pinned PER SECTION, as exactly one row: a document-wide
      `in body` check passes after a row is deleted or gutted (the route string also occurs in a
      §9 plan step), and a document-wide `startswith` passes when a decoy row with the same prefix
      exists anywhere else in the file (measured).
    """
    raw = SPEC.read_text()
    lines = raw.splitlines()
    note_start, note_end = _provenance_note_bounds(lines)
    note_raw = "\n".join(lines[note_start:note_end])
    rest_raw = "\n".join(lines[:note_start] + lines[note_end:])

    note_new_anchors, note_bare = _note_anchor_violations(note_raw)
    assert not note_new_anchors, (
        f"the provenance note quotes the issue-body anchors {note_new_anchors} that it has no "
        "licence for; the only anchors it may quote are `hosted_api.py:23409` and "
        "`hosted_api.py:23284-23292` (#3524's stale body). A NEW anchor here — including one "
        "wrapped across two `>` lines — is a citation this doc owns, and belongs in the rows "
        "below instead. Pruning a stale quotation is fine (a subset passes), because retiring a "
        "record that no longer needs quoting must not be blocked by the guard that pins it."
    )
    # …and the exemption must not become a hole for the OTHER form: the note is the one region
    # `_stray_anchors` skips, so a bare `:<n>` continuation added inside it was covered by neither
    # half of the guard (measured: `quoted` stayed `{'23284','23409'}` and `stray` stayed `[]`).
    assert not note_bare, (
        f"the #3524 provenance note carries a bare continuation {sorted(note_bare)}; this note may "
        "quote ONLY the explicit `hosted_api.py:23409` / `hosted_api.py:23284-23292` pair it owns "
        "(#3524's stale body). A continuation (`hosted_api.py`, then `:39286`) is a NEW citation "
        "in the one region the body scan exempts — cite the symbol or the route in the rows below "
        "instead."
    )

    # Every OTHER line: no explicit anchor in any spelling — the whole body is FOLDED before it is
    # scanned, so an anchor split across a line break, padded with a space or written with the
    # fullwidth colon is the same shape as the plain one — and no bare `:n`/`Ln`/`#Ln`/`line <n>`
    # continuation whose nearest file TOKEN (any extension, not just `*.py`) is hosted_api.py
    # (§10 writes ``session_auth.py:588`` AND names hosted_api.py in the same row, so "the line
    # mentions the file" would over-report).
    stray = _stray_anchors(lines, note_start, note_end)
    assert not stray, (
        f"the spec cites hosted_api.py by a LINE NUMBER again: {sorted(stray)}. A line number is "
        "valid only at the commit it was written against — cite the symbol or the route string "
        "(the live ones are named in `_HOSTED_ANCHORS`), so the citation cannot rot between "
        "edits. This covers `:n`, `#L n`, `L n`, `:L n`, the prose `line n`, an anchor wrapped "
        "across a line break or padded with space, the fullwidth colon, and the bare `:n`/`L n`/"
        "`line n` continuation; the provenance note may quote only the two anchors it owns."
    )

    # The replacement is not merely "fewer numbers": each row must still NAME what it used to
    # point at — pinned in ITS OWN section, as exactly one row, so deleting or gutting an evidence
    # row reds here and a decoy row elsewhere cannot stand in for it.
    for section, row_prefix, citations in _SPEC_ROW_CITATIONS:
        assert section in rest_raw, f"the spec no longer has the {section!r} section"
        body = rest_raw.split(section, 1)[1]
        body = body.split("\n## ", 1)[0]
        rows = [line for line in body.splitlines() if line.startswith(row_prefix)]
        assert len(rows) == 1, (
            f"{section!r} must hold exactly ONE row starting {row_prefix!r}; found {len(rows)} "
            "(0 = the evidence was deleted — or the row was REFLOWED so it no longer STARTS with "
            "the prefix, which reads as a deletion here; >1 = a duplicate that could mask a "
            "gutted original)")
        for citation in citations:
            assert citation in rows[0], (
                f"the {row_prefix.strip()!r} row in {section!r} no longer cites {citation!r}; it "
                "is the symbol/route that replaced a rotted line anchor, so its absence means "
                "the citation was dropped rather than converted")
    for token in _HOSTED_ANCHORS.values():
        _only_line(HOSTED, token)


def test_the_anchor_scan_catches_every_form_at_the_line_it_is_on() -> None:
    """The ban must cover each citation form AND name the line a reader can actually open.

    Proven on a synthetic body rather than only on the live spec, because the live spec returns
    `[]` — a scan that caught nothing at all would pass there too. Line 5-6 is one anchor
    WRAPPED across a line break; 8 is the bare `:<n>` continuation; 9-10 are the fullwidth colon
    and a space-padded anchor; 11 is that same continuation after the REPOSITORY-RELATIVE
    spelling `tortoise/hosted_api.py` (the token comparison missed it — measured); 12 is the
    space-separated `L<n>` form. Line 7 cites a DIFFERENT file in the same idiom and must stay
    legal, and the note's own quotation (lines 3-4) must not be reported at all — nor may one
    line be reported twice (an explicit anchor also carries a bare `:<n>`; the message names the
    line once, as one citation).

    The expected numbers are the REAL ones: the note is removed before the scan, so an index into
    the shortened body used to be reported as a file line (2 early, here) — the one message this
    guard produces sent the reader to an unrelated row.
    """
    lines = [
        "| row | `hosted_api.py:39152` |",
        "",
        "> **Provenance (#3524)** — the stale issue body quoted: `hosted_api.py:23409` of",
        "> `hosted_api.py:23284-23292` are unrelated code today.",
        "wrapped `hosted_api.py:",
        "39286`",
        "the other file (`session_auth.py:588`), and `:600`",
        "a row naming `hosted_api.py`, then `:39205`",
        "fullwidth `hosted_api.py\uff1a39432`",
        "padded `hosted_api.py : 39278`",
        "repo-relative `tortoise/hosted_api.py`, then `:39286`",
        "space-led `hosted_api.py L39205`",
    ]
    stray = _stray_anchors(lines, note_start=2, note_end=4)
    reported = sorted(int(m.group(1)) for m in re.finditer(r"line (\d+):", " | ".join(stray)))
    assert reported == [1, 5, 8, 9, 10, 11, 12], (
        "expected the seven hosted_api.py anchors on source lines 1, 5, 8, 9, 10, 11 and 12 — the "
        "wrapped one reported where it STARTS, the other file's on line 7 and the note's own "
        "quotation on lines 3-4 excluded; got "
        f"{reported} from {stray}"
    )


def test_the_provenance_note_cannot_smuggle_a_bare_continuation() -> None:
    """The note's exemption covers the QUOTED pair only — the continuation form must still fail.

    The note is the one region `_stray_anchors` skips, so applying the continuation rule only to
    the body left the guard's own contract ("the anchors inside it may only be the quoted
    issue-body pair … ADDING one does not") false for the spelling §1 already uses for
    `session_auth.py:588` → `` (`:600`) `` — the most likely form a future editor copies in.
    Measured before the fix: `` > Verified against `hosted_api.py`, `:39286`. `` inside the note
    left `quoted == {'23409','23284'}` and `stray == []`, i.e. green with an anchor added.

    Non-vacuous in both directions: the escaping line is shown to be INVISIBLE to the explicit
    scan (so the note half is what catches it), and the note's own legitimate quotation is shown
    to stay legal (so the rule is not simply "no colons in the note").

    ⛔ And the rule is a SUBSET, not an equality — this test used to assert the note's quoted set
    `== {"23409", "23284"}` while the guard beside it asserted `<=`, so an editor who followed
    the guard's own instruction ("pruning a stale quotation is fine") was redded here by a bare
    two-set diff with nothing to act on. Both halves now call `_note_anchor_violations`, and the
    pruning case is pinned below: restoring an equality rule reds it again (measured).
    """
    # The measured escape: no explicit anchor, but a continuation that belongs to hosted_api.py.
    escaping = "> Verified against `hosted_api.py`, `:39286`."
    assert not _HOSTED_ANCHOR_RE.findall(_collapse(escaping)), (
        "this case is only interesting while the EXPLICIT scan cannot see it — the note's own "
        "quoted set would otherwise catch it and the continuation scan would be vacuous here"
    )
    assert _bare_continuations(escaping) == [":39286"], (
        "the note's continuation form must be caught: `hosted_api.py` followed by a bare `:39286` "
        f"is a new citation, got {_bare_continuations(escaping)}"
    )

    # PRUNING the quotation is legal (the subset rule), and ADDING an explicit anchor is not —
    # the two directions the equality used to conflate into one false red.
    pruned = ("> **Provenance (#3524)** — the stale issue body quoted: "
              "(its line anchors are unrelated code today), and")
    assert _note_anchor_violations(pruned) == ([], []), (
        "retiring a quotation that no longer needs quoting must stay legal — the guard's own "
        "message says so, so a rule that reds it is the false red this helper replaced; got "
        f"{_note_anchor_violations(pruned)}"
    )
    assert _note_anchor_violations(pruned + " `hosted_api.py:39286`") == (["39286"], []), (
        "the subset rule must still catch a NEW anchor in the note, or 'pruning is legal' would "
        "have bought an exemption for everything; got "
        f"{_note_anchor_violations(pruned + ' `hosted_api.py:39286`')}"
    )

    # The live spec's note quotes only the explicit pair, so this rule does not red it — and the
    # explicit pair must NOT be re-reported as a continuation of itself.
    lines = SPEC.read_text().splitlines()
    note_start, note_end = _provenance_note_bounds(lines)
    note_raw = "\n".join(lines[note_start:note_end])
    assert _note_anchor_violations(note_raw) == ([], []), (
        "the live note may quote at most `hosted_api.py:23409` / `:23284-23292` and carry no "
        f"continuation; got {_note_anchor_violations(note_raw)}"
    )
    assert _bare_continuations(note_raw) == [], (
        "the note's own quoted pair `hosted_api.py:23409` / `hosted_api.py:23284-23292` is an "
        "EXPLICIT anchor, so the continuation rule must leave it alone; it flagged "
        f"{_bare_continuations(note_raw)}"
    )


def test_the_guard_asserts_its_subject_when_the_provenance_note_is_gone() -> None:
    """A retired or reworded note must fail closed WITH a message, not with `StopIteration`.

    The note is the only place a `hosted_api.py` anchor is legal, so the guard has to identify it;
    locating it with `next(...)` and no default raised a bare `StopIteration` from inside the test
    body — unreadable in a shard log, and a contradiction of this module's own contract ("each
    one asserts it found its subject"). The exact wording matters too: it must say what to do.
    """
    with pytest.raises(AssertionError, match="provenance note is gone"):
        _provenance_note_bounds(["# 1. a heading, no blockquote here", "| a row |"])

    # And the live spec does carry one, so the assertion above is not describing an unreachable
    # branch: the found span starts at a `>` line that names both `#3524` and `Provenance`.
    spec_lines = SPEC.read_text().splitlines()
    note_start, note_end = _provenance_note_bounds(spec_lines)
    assert spec_lines[note_start].startswith(">") and note_end > note_start


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
