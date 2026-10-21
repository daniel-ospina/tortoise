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
#:    it may reach through `:`, `#L` or `L` and any whitespace; `_stray_anchors` FOLDS the body
#:    first (whitespace runs to one space, the fullwidth colon to ASCII), so a wrapped or padded
#:    anchor is one shape again and the offset still points at a real line;
#:  * `_BARE_ANCHOR_RE` + `_NEAREST_PY_RE` catch the continuation idiom line by line, attributing
#:    the number to the BASENAME of the nearest `something.py` to its LEFT rather than to "any
#:    file named on the line" (see `_NEAREST_PY_RE` for why the looser rule over-reported) — the
#:    basename, not the whole token, because the repository-relative spelling
#:    `tortoise/hosted_api.py` is a spelling THIS document already uses (§1's `Canonical code:`
#:    line), and comparing the token let `` `tortoise/hosted_api.py`, see `:39286` `` through
#:    green (measured).
_HOSTED_ANCHOR_RE = re.compile(r"hosted_api\.py\s*(?:#?L|:)\s*(\d+)")
_BARE_ANCHOR_RE = re.compile(r":\s*(\d+)")
#: The nearest `something.py` to the LEFT of a bare `:<n>`, which is what the continuation
#: belongs to. It is matched WHOLE (it may carry a directory) and compared by BASENAME, so
#: `tortoise/hosted_api.py` attributes its continuation exactly like `hosted_api.py` does. A
#: file-name scan rather than "the line mentions hosted_api.py somewhere": §10's
#: `Redemption` row names `hosted_api.py` AND carries `session_auth.py:588`, so the loose rule
#: reported the row's legitimate anchor to a DIFFERENT file (measured).
_NEAREST_PY_RE = re.compile(r"[\w./-]+\.py")


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
    folded, origin = _fold("\n".join(line for _, line in outside))
    stray: list[str] = []
    explicit: set[int] = set()
    for match in _HOSTED_ANCHOR_RE.finditer(folded):
        lineno = outside[origin[match.start()] - 1][0] + 1
        explicit.add(lineno)
        stray.append(f"line {lineno}: {match.group(0)!r}")
    for index, line in outside:
        if index + 1 in explicit:
            # Already reported at this line by the explicit scan; the bare `:<n>` inside the same
            # anchor is the same citation, and a message that names one line twice reads as two.
            continue
        folded_line = _collapse(line)
        for match in _BARE_ANCHOR_RE.finditer(folded_line):
            owners = _NEAREST_PY_RE.findall(folded_line[:match.start()])
            # BASENAME, not the matched token: `tortoise/hosted_api.py` is the repository-relative
            # spelling this very document uses for the same file, so a continuation after it
            # belongs to `hosted_api.py` too (exact equality let it through — measured).
            if owners and owners[-1].rsplit("/", 1)[-1] == "hosted_api.py":
                stray.append(
                    f"line {index + 1}: bare continuation {match.group(0)!r} belongs to "
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
      forms this tree writes: `:n`, `#Ln`, a space-separated `Ln`, a space or a WRAPPED line after
      the separator, the fullwidth colon, and the bare `:n` continuation after a file named in
      ANY spelling of `hosted_api.py` (basename comparison, so `tortoise/hosted_api.py` counts).
      Each of those was green under an earlier version of this guard (measured), because the
      scan read one line at a time and knew one separator — and the token comparison let the
      repository-relative spelling through after the bare continuation was added (measured);
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

    quoted = set(_HOSTED_ANCHOR_RE.findall(_collapse(note_raw)))
    assert quoted <= {"23409", "23284"}, (
        f"the provenance note quotes the issue-body anchors {sorted(quoted)}; the only anchors it "
        "may quote are `hosted_api.py:23409` and `hosted_api.py:23284-23292` (#3524's stale "
        "body). A NEW anchor here — including one wrapped across two `>` lines — is a citation "
        "this doc owns, and belongs in the rows below instead. Pruning a stale quotation is "
        "fine (a subset passes), because retiring a record that no longer needs quoting must not "
        "be blocked by the guard that pins it."
    )

    # Every OTHER line: no explicit anchor in any spelling — the whole body is FOLDED before it is
    # scanned, so an anchor split across a line break, padded with a space or written with the
    # fullwidth colon is the same shape as the plain one — and no bare `:<n>` continuation whose
    # nearest `something.py` is hosted_api.py (§10 writes ``session_auth.py:588`` AND names
    # hosted_api.py in the same row, so "the line mentions the file" would over-report).
    stray = _stray_anchors(lines, note_start, note_end)
    assert not stray, (
        f"the spec cites hosted_api.py by a LINE NUMBER again: {sorted(stray)}. A line number is "
        "valid only at the commit it was written against — cite the symbol or the route string "
        "(the live ones are named in `_HOSTED_ANCHORS`), so the citation cannot rot between "
        "edits. This covers `:n`, `#L n`, an anchor wrapped across a line break or padded with "
        "space, the fullwidth colon, and the bare `:n` continuation; the provenance note may "
        "quote only the two anchors it owns."
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
